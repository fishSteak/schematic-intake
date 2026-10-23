import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { LocalWorkspace, prepareMaterials, disposeMaterials } from "../agent_review/local_workspace.mjs";

test("publish only declared artifacts; facts remain unchanged", async () => {
  const source = await mkdtemp(path.join(os.tmpdir(), "schematic-stage-test-"));
  let workspace;
  try {
    const fact = path.join(source, "review_out/context_store/objects/R4.json");
    await mkdir(path.dirname(fact), { recursive: true });
    await writeFile(fact, '{"sealed":true}');
    const output = path.join(source, "review_out/agent_jobs/P11-A01/R4.json");
    await mkdir(path.dirname(output), {recursive:true});
    const schema = output.replace(/\.json$/, ".schema.json");
    await writeFile(schema, '{"title":"contract without example answers"}');
    await mkdir(path.join(source,'docs/samples'), {recursive:true});
    await writeFile(path.join(source,'docs/samples/answer.json'), '{"expected":"must not reach agent"}');
    await writeFile(output, '{"previous_verdict":"must not reach agent"}');
    workspace = await LocalWorkspace.create(source, [output]);
    assert.equal(JSON.parse(await readFile(path.join(workspace.root,workspace.relative(schema)),'utf8')).title, 'contract without example answers');
    await assert.rejects(readFile(path.join(workspace.root,'docs/samples/answer.json')));
    await assert.rejects(readFile(path.join(workspace.root,workspace.relative(output))));
    await writeFile(path.join(workspace.root, "review_out/context_store/objects/R4.json"), '{"sealed":false}');
    const local = path.join(workspace.root, workspace.relative(output));
    await writeFile(local, JSON.stringify({ file: path.join(workspace.root, "review_out/sch/schematic.pdf") }));
    await workspace.collect();
    assert.equal(JSON.parse(await readFile(fact, "utf8")).sealed, true);
    assert.equal(JSON.parse(await readFile(output, "utf8")).file, path.join(source, "review_out/sch/schematic.pdf"));
    await writeFile(local, "{broken");
    await assert.rejects(workspace.collect());
    const analysis = output.replace(/\.json$/, ".analysis.md");
    const context = output.replace(/\.json$/, ".context.json");
    await writeFile(context, '{"version":1}');
    await writeFile(path.join(workspace.root, workspace.relative(context)), '{"version":1}');
    await writeFile(path.join(workspace.root, workspace.relative(analysis)), '# Analysis\n\n## Compare\nmodel output\n');
    workspace.outputs.push(analysis);
    const checkpoint = await workspace.saveAnalysisCheckpoint();
    assert.equal(await readFile(path.join(checkpoint, workspace.relative(analysis)), "utf8"), '# Analysis\n\n## Compare\nmodel output\n');
    const recovered = await LocalWorkspace.create(source, [output, analysis], checkpoint);
    try {
      assert.equal(await readFile(path.join(recovered.root, recovered.relative(analysis)), "utf8"), '# Analysis\n\n## Compare\nmodel output\n');
      await writeFile(context, '{"version":2}');
      await assert.rejects(LocalWorkspace.create(source, [analysis], checkpoint), /context differs/);
    } finally { await recovered.dispose(); }
    assert.throws(() => workspace.relative(path.join(source, "../outside.json")));
  } finally {
    if (workspace) await workspace.dispose();
    if (path.dirname(source) !== os.tmpdir() || !path.basename(source).startsWith("schematic-stage-test-")) throw new Error("Unexpected temp path");
    await rm(source, { recursive: true, force: true });
  }
});

test('shared material snapshot still isolates writes and stage checkpoint resumes only completed analysis',async()=>{
  const source=await mkdtemp(path.join(os.tmpdir(),'schematic-stage-test-'));
  let snapshot,a,b;
  const previous=process.env.SCHEMATIC_MATERIAL_SNAPSHOT;
  try {
    const fact=path.join(source,'cache/mpn/part/pintable.json');
    await mkdir(path.dirname(fact),{recursive:true});await writeFile(fact,'{"rating":3}');
    const output=path.join(source,'review_out/agent_jobs/P11-A05/C1.json');
    await mkdir(path.dirname(output),{recursive:true});
    const analysis=output.replace(/\.json$/,'.analysis.md');
    await writeFile(output.replace(/\.json$/,'.context.json'),'{"material":"original"}');
    snapshot=await prepareMaterials(source);process.env.SCHEMATIC_MATERIAL_SNAPSHOT=snapshot;
    a=await LocalWorkspace.create(source,[output,analysis]);
    await writeFile(path.join(a.root,'cache/mpn/part/pintable.json'),'{"rating":999}');
    b=await LocalWorkspace.create(source,[output,analysis]);
    assert.equal(JSON.parse(await readFile(path.join(b.root,'cache/mpn/part/pintable.json'),'utf8')).rating,3);
    assert.equal(JSON.parse(await readFile(fact,'utf8')).rating,3);
    await writeFile(path.join(a.root,a.relative(analysis)),`# Analysis\n\n## Material\nExplicit source ${a.root} ${a.root.replaceAll('\\','/')}`);
    const asset='review_out/research_assets/P11-A05/C1/source.pdf';
    await mkdir(path.dirname(path.join(a.root,asset)),{recursive:true});
    await writeFile(path.join(a.root,asset),'new evidence acquired during analysis');
    await a.saveStageCheckpoint({ruleId:'P11-A05',executionHash:'current'},'analysis');
    const root=path.join(source,'history/execution-checkpoints/P11-A05/C1/current');
    const meta=JSON.parse(await readFile(path.join(root,'stage.json'),'utf8'));
    assert.equal(meta.stage,'analysis');assert.equal(meta.execution_hash,'current');
    const resumed=await LocalWorkspace.create(source,[output,analysis],root);
    try {
      const text=await readFile(path.join(resumed.root,resumed.relative(analysis)),'utf8');
      assert.equal(await readFile(path.join(resumed.root,asset),'utf8'),'new evidence acquired during analysis');
      assert.match(text,/Explicit source/);assert.ok(text.includes(resumed.root));assert.ok(!text.includes(a.root));
    }
    finally {await resumed.dispose();}
  } finally {
    if(a)await a.dispose();if(b)await b.dispose();if(snapshot)await disposeMaterials(snapshot);
    if(previous===undefined)delete process.env.SCHEMATIC_MATERIAL_SNAPSHOT;else process.env.SCHEMATIC_MATERIAL_SNAPSHOT=previous;
    await rm(source,{recursive:true,force:true});
  }
});

test('raw configured materials and task sidecars are available inside each isolated workspace',async()=>{
  const base=await mkdtemp(path.join(os.tmpdir(),'schematic-stage-test-'));
  const source=path.join(base,'board');let snapshot,workspace;
  const prior=process.env.SCHEMATIC_MATERIAL_SNAPSHOT;
  try {
    await mkdir(path.join(source,'review_out'),{recursive:true});
    await writeFile(path.join(base,'source.csv'),'Ref,Material\nC1,ceramic');
    await writeFile(path.join(base,'source.eprj2'),'raw-project-bytes');
    await writeFile(path.join(source,'materials.json'),JSON.stringify({bom:'../source.csv',eprj2:'../source.eprj2'}));
    await writeFile(path.join(source,'review_out/design_task.json'),'{"approved":false}');
    snapshot=await prepareMaterials(source);process.env.SCHEMATIC_MATERIAL_SNAPSHOT=snapshot;
    assert.equal(await readFile(path.join(source,'material_inputs/eprj2.eprj2'),'utf8'),'raw-project-bytes');
    workspace=await LocalWorkspace.create(source,[path.join(source,'review_out/agent_jobs/P11-A04/C1.json')]);
    const config=JSON.parse(await readFile(path.join(workspace.root,'materials.json'),'utf8'));
    assert.match(config.bom,/^material_inputs\//);
    assert.match(await readFile(path.join(workspace.root,config.bom),'utf8'),/C1,ceramic/);
    assert.equal(JSON.parse(await readFile(path.join(workspace.root,'review_out/design_task.json'),'utf8')).approved,false);
    await writeFile(path.join(workspace.root,config.bom),'modified');
    assert.match(await readFile(path.join(base,'source.csv'),'utf8'),/C1,ceramic/);
    assert.equal(JSON.parse(await readFile(path.join(source,'materials.json'),'utf8')).bom,'../source.csv');
  } finally {
    if(workspace)await workspace.dispose();if(snapshot)await disposeMaterials(snapshot);
    if(prior===undefined)delete process.env.SCHEMATIC_MATERIAL_SNAPSHOT;else process.env.SCHEMATIC_MATERIAL_SNAPSHOT=prior;
    await rm(base,{recursive:true,force:true});
  }
});
