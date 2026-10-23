// Read only the selected project's design history. Never read user/account tables.
// Current qualified profile: a single authenticated full history on main, editor 3.2.149.
import {DatabaseSync} from 'node:sqlite';
import {readFileSync,statSync} from 'node:fs';
import {createHash,createDecipheriv} from 'node:crypto';
import {gunzipSync} from 'node:zlib';
import AdmZip from 'adm-zip';
import {readSchematicModel} from './schematic-model.mjs';
import {parseEpru,READ_ONLY_GRAPHICS_PROFILE_V3} from './native-readonly-archive.mjs';
export const EPRJ_PARSER_VERSION='eprj-main-full-history-1';
const hash=b=>createHash('sha256').update(b).digest('hex');
const requireThat=(yes,code)=>{if(!yes)throw Error(code)};
export function readEprjProject(path,expectedHash){
 requireThat(statSync(path).size<=64*1024*1024,'EPRJ_SIZE');const original=readFileSync(path);requireThat(hash(original)===expectedHash,'SOURCE_HASH_MISMATCH');
 const db=new DatabaseSync(path,{readOnly:true});let text,structure,head,project;
 try{
  const branches=db.prepare("SELECT uuid,history_uuid FROM branches WHERE name='main' AND delete_status=0").all();requireThat(branches.length===1,'EPRJ_BRANCH_AMBIGUOUS');const branch=branches[0];requireThat(/^[a-f0-9]{32}$/.test(branch.uuid),'EPRJ_BRANCH_ID');
  const rows=db.prepare(`SELECT uuid,parent,snapshot,key,num FROM project_history_${branch.uuid}`).all();requireThat(rows.length===1&&rows[0].uuid===branch.history_uuid&&!rows[0].parent&&!rows[0].snapshot,'EPRJ_HISTORY_CHAIN_UNSUPPORTED');head=rows[0];
  requireThat(/^[a-f0-9]{32}$/.test(head.key)&&/^[a-f0-9]{32}$/.test(head.uuid),'EPRJ_HISTORY_SHAPE');
  const histories=db.prepare('SELECT dataStr FROM history_data WHERE history_uuid=?').all(head.uuid);requireThat(histories.length===1,'EPRJ_HISTORY_MISSING');
  const payload=Buffer.from(histories[0].dataStr,'base64');requireThat(payload.length>16&&payload.toString('base64')===histories[0].dataStr,'EPRJ_PAYLOAD_INVALID');
  let compressed;try{const cipher=createDecipheriv('aes-128-gcm',Buffer.from(head.key,'hex'),Buffer.from(head.uuid,'hex'));cipher.setAuthTag(payload.subarray(-16));compressed=Buffer.concat([cipher.update(payload.subarray(0,-16)),cipher.final()]);}catch{throw Error('EPRJ_HISTORY_AUTHENTICATION');}
  requireThat(compressed[0]===31&&compressed[1]===139,'EPRJ_COMPRESSION_UNSUPPORTED');text=new TextDecoder('utf-8',{fatal:true}).decode(gunzipSync(compressed,{maxOutputLength:64*1024*1024}));
  const structures=db.prepare('SELECT structure FROM project_structures WHERE branch_uuid=?').all(branch.uuid);requireThat(structures.length===1,'EPRJ_STRUCTURE_AMBIGUOUS');structure=JSON.parse(structures[0].structure);
  const projects=db.prepare('SELECT uuid,name FROM projects').all();requireThat(projects.length===1,'EPRJ_PROJECT_AMBIGUOUS');project=projects[0];
 }finally{db.close()}
 requireThat(hash(readFileSync(path))===expectedHash,'EPRJ_SOURCE_CHANGED');
 const lines=text.split('\n');requireThat(JSON.parse(lines[0].split('||')[0]).type==='EDIT_HEAD','EPRJ_EDIT_HEAD');const epru=lines.slice(1).join('\n');
 const docs=parseEpru(epru,{strictJson:true,sourceProfile:READ_ONLY_GRAPHICS_PROFILE_V3});
 const pages=docs.filter(d=>d.head.docType==='SCH_PAGE'),expectedPages=Object.keys(structure.sheets??{}).sort();requireThat(JSON.stringify(pages.map(p=>p.head.uuid).sort())===JSON.stringify(expectedPages),'EPRJ_PAGE_COVERAGE');
 const versions=new Set(docs.map(d=>d.head.editVersion).filter(Boolean));requireThat(versions.size===1&&versions.has('3.2.149.88089769'),'EPRJ_EDITOR_VERSION_UNSUPPORTED');
 // In-memory codec adapter only: never publish this synthetic ZIP as an editor export.
 const zip=new AdmZip();zip.addFile('project2.json',Buffer.from(JSON.stringify({title:project.name,editorVersion:[...versions][0]})));zip.addFile('decoded.epru',Buffer.from(epru));const adapterBytes=zip.toBuffer();const model=readSchematicModel(adapterBytes);model.sourceSha256=expectedHash;
 const keyed=new Set(model.components.map(c=>c.location.pageId+'/'+c.location.componentId));const byId=new Map(docs.map(d=>[d.head.uuid,d]));const classified=[],excluded=[],unclassified=[];
 for(const page of pages){
  const meta=page.records.find(r=>r.header.type==='META')?.body;requireThat(meta?.title===structure.sheets[page.head.uuid].title,'EPRJ_PAGE_TITLE_CONFLICT');
  for(const raw of page.records.filter(r=>r.header.type==='COMPONENT')){
   const key=page.head.uuid+'/'+raw.header.id;const location={pageId:page.head.uuid,pageName:meta.title,componentId:raw.header.id,x:raw.body.x,y:raw.body.y};
   if(keyed.has(key)){classified.push({key,type:'COMPONENT',location});continue;}
   const attrs=Object.fromEntries(page.records.filter(r=>r.header.type==='ATTR'&&r.body?.parentId===raw.header.id).map(r=>[r.body.key,r.body.value]));const device=byId.get(attrs.Device)?.records.find(r=>r.header.type==='META')?.body;const tags=device?.tags??[];const merged={...device?.attributes};for(const [k,v] of Object.entries(attrs))if(v!==null)merged[k]=v;
   const symbol=byId.get(merged.Symbol);const pins=symbol?.records.filter(r=>r.header.type==='PIN'&&r.body.partId===raw.body.partId);
   if(tags.includes('网络端口')||tags.includes('网络标识'))classified.push({key,type:'NET_LABEL',location});
   else if(symbol&&pins.length===0&&!merged.Designator&&merged['Add into BOM']!=='yes'&&merged['Convert to PCB']!=='yes')excluded.push({key,reason:'无电气引脚且明确非BOM/PCB对象的图框或图形',location});
   else unclassified.push({key,tags,reason:'未归类的原生对象',location});
  }
 }
 return {parserVersion:EPRJ_PARSER_VERSION,source:{sha256:expectedHash,projectId:project.uuid,historyId:head.uuid,editorVersion:[...versions][0]},model,docs,adapterBytes,coverage:{expectedPages,parsedPages:pages.length,classified,excluded,unclassified,complete:unclassified.length===0&&model.issues.length===0},structure};
}
