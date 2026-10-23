/** Run on a local disk; publish only explicitly declared artifact paths. */
import { cp, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";

const MATERIAL_DIRS = ["cache", "review_out/cache", "review_out/datasheets", "review_out/sch", "review_out/context_store","material_inputs","review_out/evidence_store","review_out/independent_reviews","review_out/page_evidence"];
const MATERIAL_FILES = ["materials.json", "review_out/materials.json", "review_out/board_kg.json", "review_out/design_graph.json", "review_out/sch_index.json","review_out/jlc_project.json","review_out/pintable_index.json","review_out/operating_conditions.json","review_out/design_task.json"];
const RAW_INPUTS=['netlist','bom','schematic_pdf','eprj2','operating_conditions','design_task_book','design_task_book_docx','drc','rules'];
const WORKSPACE_TOKEN='__SCHEMATIC_EXECUTION_WORKSPACE__';
const relocate=(body,from,to)=>body.split(from).join(to).split(from.replaceAll('\\','/')).join(to.replaceAll('\\','/'));
async function copyRawInputs(source,dest) {
  const configPath=path.join(source,'materials.json');
  if(!await exists(configPath))return;
  const config=JSON.parse(await readFile(configPath,'utf8'));
  for(const key of RAW_INPUTS) {
    if(typeof config[key]!=='string' || !config[key])continue;
    const src=path.resolve(source,config[key]);
    if(!await exists(src))throw new Error(`Configured material missing: ${key}`);
    const rel=`material_inputs/${key}${path.extname(src)}`;
    await mkdir(path.dirname(path.join(dest,rel)),{recursive:true});
    await cp(src,path.join(dest,rel));config[key]=rel;
  }
  await writeFile(path.join(dest,'materials.json'),JSON.stringify(config,null,2));
}
async function publishMaterialAliases(snapshot,source) {
  const file=path.join(snapshot,'materials.json');
  if(!await exists(file))return;
  const config=JSON.parse(await readFile(file,'utf8'));
  for(const key of RAW_INPUTS) {
    const rel=config[key];
    if(typeof rel!=='string' || !/^material_inputs\/[a-z0-9_]+\.[a-z0-9]+$/i.test(rel))continue;
    // Controller publishes identical source bytes, never model-modified files.
    const dest=path.join(source,rel);await mkdir(path.dirname(dest),{recursive:true});
    await cp(path.join(snapshot,rel),dest);
  }
}
async function copyResearchSources(source,dest){
  const current=path.join(source,'review_out/evidence_store/current.json');
  if(!await exists(current))return;
  const data=JSON.parse(await readFile(current,'utf8'));
  for(const [rel,expected] of Object.entries(data._provenance?.evidence_hashes||{})){
    const src=path.resolve(source,rel),sub=path.relative(source,src);
    if(path.isAbsolute(rel)||sub.startsWith('..')||path.isAbsolute(sub))throw Error('Evidence source outside board');
    if(createHash('sha256').update(await readFile(src)).digest('hex')!==expected)throw Error('Evidence source changed: '+rel);
    const target=path.join(dest,sub);await mkdir(path.dirname(target),{recursive:true});await cp(src,target);
  }
}
export async function prepareMaterials(source) {
  const root = await mkdtemp(path.join(os.tmpdir(), "schematic-material-"));
  try {
    for (const name of [...MATERIAL_DIRS,...MATERIAL_FILES]) {
      await mkdir(path.dirname(path.join(root,name)),{recursive:true});
      try { await cp(path.join(source,name),path.join(root,name),{recursive:true,
        filter:src=>!["quarantine","tombstones","history"].includes(path.basename(src))}); }
      catch(e) { if(e.code!=="ENOENT")throw e; }
    }
    await copyRawInputs(source,root);
    await copyResearchSources(source,root);
    await publishMaterialAliases(root,source);
    return root;
  } catch(e) { await disposeMaterials(root);throw e; }
}
export async function disposeMaterials(root) {
  if(path.dirname(root)!==os.tmpdir() || !path.basename(root).startsWith('schematic-material-'))throw new Error('Unexpected material snapshot');
  await rm(root,{recursive:true,force:true});
}

async function exists(p) { try { return (await stat(p)).isFile(); } catch { return false; } }
async function copyOwnCheckpointAssets(from,to,analysisRelative) {
  const assetRelative=path.join('review_out/research_assets',path.basename(path.dirname(analysisRelative)),path.basename(analysisRelative,'.analysis.md'));
  const source=path.join(from,assetRelative), dest=path.join(to,assetRelative);
  try {
    if(!(await stat(source)).isDirectory())throw Error('Checkpoint asset path is not a directory');
    await mkdir(path.dirname(dest),{recursive:true});
    await cp(source,dest,{recursive:true});
  } catch(error) {if(error.code!=='ENOENT')throw error;}
}
export class LocalWorkspace {
  static async create(source, outputs, checkpointRoot = null, allowPriorAnalysis = false, inputs = []) {
    const self = new LocalWorkspace(source, outputs);
    self.root = await mkdtemp(path.join(os.tmpdir(), "schematic-agent-"));
    try {
    const materialSource = process.env.SCHEMATIC_MATERIAL_SNAPSHOT || source;
    for (const name of MATERIAL_DIRS) {
      try {
        await cp(path.join(materialSource, name), path.join(self.root, name), { recursive: true,
          filter: src => !["quarantine", "tombstones", "history"].includes(path.basename(src)),
        });
      } catch (e) { if (e.code !== "ENOENT") throw e; }
    }
    for (const rel of MATERIAL_FILES) if (await exists(path.join(materialSource, rel))) {
      const dest = path.join(self.root, rel); await mkdir(path.dirname(dest), { recursive: true });
      await cp(path.join(materialSource, rel), dest);
    }
    await copyResearchSources(materialSource,self.root);
    for(const target of inputs){
      const rel=self.relative(target),dest=path.join(self.root,rel);
      if(!(await exists(target)))throw Error('Declared research input missing: '+rel);
      await mkdir(path.dirname(dest),{recursive:true});await cp(target,dest);
    }
    if(materialSource===source) {
      await copyRawInputs(source,self.root);
      await publishMaterialAliases(self.root,source);
    }
    // The current object workbench and prior analysis are inputs, other verdicts are not.
    for (const target of outputs) {
      const rel = self.relative(target);
      await mkdir(path.dirname(path.join(self.root, rel)), { recursive: true });
      if (await exists(target)) {
        self.before.set(rel, await readFile(target, "utf8"));
        if (allowPriorAnalysis && target.endsWith(".analysis.md")) await cp(target, path.join(self.root, rel));
      }
      const context = target.replace(/(?:\.analysis\.md|\.json)$/, ".context.json");
      if (await exists(context)) await cp(context, path.join(self.root, self.relative(context)));
      const schema = context.replace(/\.context\.json$/, ".schema.json");
      if (await exists(schema)) await cp(schema, path.join(self.root, self.relative(schema)));
      const mainSchema=context.replace(/\.context\.json$/, ".main.schema.json");
      if(await exists(mainSchema))await cp(mainSchema,path.join(self.root,self.relative(mainSchema)));
      if (checkpointRoot && target.endsWith(".analysis.md")) {
        const checkpoint = path.join(checkpointRoot, rel);
        const checkpointContext = path.join(checkpointRoot, self.relative(context));
        if (!(await exists(checkpoint)) || !(await exists(checkpointContext))) throw new Error("Checkpoint missing analysis or context");
        const expected = JSON.parse(await readFile(context, "utf8"));
        const previous = JSON.parse(await readFile(checkpointContext, "utf8"));
        if (JSON.stringify(expected) !== JSON.stringify(previous)) throw new Error("Checkpoint context differs from current workbench");
        const body = await readFile(checkpoint, "utf8");
        if (!body.trim() || !body.includes("\n## ")) throw new Error("Checkpoint analysis is empty or incomplete");
        await writeFile(path.join(self.root, rel), relocate(body,checkpointRoot,self.root).split(WORKSPACE_TOKEN).join(self.root));
        await copyOwnCheckpointAssets(checkpointRoot,self.root,rel);
      }
    }
    return self;
    } catch (error) {
      await self.dispose();
      throw error;
    }
  }
  constructor(source, outputs) { this.source = source; this.outputs = outputs; this.before = new Map(); }
  relative(target) {
    const rel = path.relative(this.source, target);
    if (rel.startsWith("..") || path.isAbsolute(rel)) throw new Error("Artifact outside board workspace");
    return rel;
  }
  prompt(text) {
    const runtime = `\nAvailable Node executable: ${process.execPath}.`
      + (process.env.SCHEMATIC_PYTHON ? ` Available Python executable: ${process.env.SCHEMATIC_PYTHON}.` : "");
    return text.split(this.source).join(this.root) + runtime
      + "\nExecution workspace is a local snapshot. Do not read historical reports or archived reviews. Write only the requested artifacts in this workspace."
      + "\nFor long JSON/Markdown artifacts, write the file directly with apply_patch. Avoid long Python -c strings and PowerShell stdin pipelines; Windows quoting can corrupt them. Investigate decisive source gaps before writing the requested outputs. Reuse completed investigations unless new evidence challenges them. Categorize truly unavailable project facts separately from unfinished public research, inference, or execution problems, then end the turn.\n";
  }
  async collect() {
    for (const target of this.outputs) {
      const rel = this.relative(target); const local = path.join(this.root, rel);
      if (!(await exists(local))) continue;
      let body = await readFile(local, "utf8");
      if (target.endsWith(".json")) {
        const data = JSON.parse(body);
        if(target.endsWith('.research.json')){
          const cardDir=path.dirname(rel),rid=path.basename(cardDir),obj=path.basename(target,'.research.json');
          const assetRoot=path.resolve(this.root,'review_out/research_assets',rid,obj);
          for(const fact of data.facts||[])for(const ev of fact.evidence||[]){
            if(typeof ev.path!=='string')continue;
            const src=path.resolve(this.root,ev.path),sub=path.relative(assetRoot,src);
            if(!sub.startsWith('..')&&!path.isAbsolute(sub)&&src!==assetRoot){
              const info=await stat(src);if(!info.isFile()||info.size>50*1024*1024)throw Error('Invalid object research asset');
              const dest=path.join(this.source,path.relative(this.root,src));await mkdir(path.dirname(dest),{recursive:true});await cp(src,dest);
            }
          }
        }
        const restore = value => typeof value === "string" ? relocate(value,this.root,this.source)
          : Array.isArray(value) ? value.map(restore)
          : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).map(([k,v]) => [k,restore(v)])) : value;
        body = JSON.stringify(restore(data), null, 2) + "\n";
      } else body = relocate(body,this.root,this.source);
      await mkdir(path.dirname(target), { recursive: true });
      await writeFile(target, body, "utf8");
    }
  }
  async saveAnalysisCheckpoint() {
    const analyses = [];
    for (const target of this.outputs) {
      if (target.endsWith(".analysis.md") && await exists(path.join(this.root, this.relative(target)))) analyses.push(target);
    }
    if (!analyses.length) return null;
    const dest = path.join(this.source, "history", "review-checkpoints", `${Date.now()}-${path.basename(this.root)}`);
    for (const target of analyses) {
      await copyOwnCheckpointAssets(this.root,dest,this.relative(target));
      for (const rel of [this.relative(target), this.relative(target.replace(/\.analysis\.md$/, ".context.json"))]) {
        const local = path.join(this.root, rel);
        if (!await exists(local)) continue;
        await mkdir(path.dirname(path.join(dest, rel)), { recursive: true });
        if(rel.endsWith('.analysis.md'))await writeFile(path.join(dest,rel),relocate(await readFile(local,'utf8'),this.root,WORKSPACE_TOKEN));
        else await cp(local, path.join(dest, rel));
      }
    }
    return dest;
  }
  async saveStageCheckpoint(options, stage) {
    for (const target of this.outputs.filter(p=>p.endsWith('.analysis.md'))) {
      const rel=this.relative(target), local=path.join(this.root,rel);
      const body=Buffer.from(relocate(await readFile(local,'utf8'),this.root,WORKSPACE_TOKEN));
      if(!body.toString().trim())throw new Error('Empty completed analysis');
      const obj=path.basename(target,'.analysis.md');
      const dest=path.join(this.source,'history/execution-checkpoints',options.ruleId,obj,options.executionHash);
      for(const file of [rel,rel.replace(/\.analysis\.md$/,'.context.json')]) {
        await mkdir(path.dirname(path.join(dest,file)),{recursive:true});
        if(file===rel)await writeFile(path.join(dest,file),body);
        else await cp(path.join(this.root,file),path.join(dest,file));
      }
      await copyOwnCheckpointAssets(this.root,dest,rel);
      const meta={stage,execution_hash:options.executionHash,analysis_hash:createHash('sha256').update(body).digest('hex')};
      await writeFile(path.join(dest,'stage.json'),JSON.stringify(meta));
    }
  }
  async dispose() {
    if (path.dirname(this.root) !== os.tmpdir() || !path.basename(this.root).startsWith("schematic-agent-")) throw new Error("Unexpected staging directory");
    await rm(this.root, { recursive: true, force: true });
  }
}
