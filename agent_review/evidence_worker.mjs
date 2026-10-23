/** Isolated research transport; only declared proposals/downloads are published. */
import {readFile,writeFile,appendFile,cp,mkdir,stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Agent} from './codex_agent.mjs';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const job=JSON.parse(await readFile(process.argv[2],'utf8'));
const folder=path.dirname(job.output),model=process.env.AGENT_JUDGE_MODEL||'auto';
const task=JSON.parse(await readFile(job.output.replace(/\.json$/,'.context.json'),'utf8'));
const prompt=await readFile(path.join(root,'agent_review/prompts/evidence_research.md'),'utf8');
const followup=job.followup?await readFile(path.join(root,'agent_review/prompts/evidence_followup.md'),'utf8'):'';
let feedback='';
try{feedback=await readFile(path.join(folder,'validation.json'),'utf8');}catch{}
let result;
try{
 await using agent=await Agent.create({model:{id:model},local:{cwd:job.board},outputs:[job.output],inputs:[...(job.inputs||[]),...(feedback?[job.output]:[])],autonomousResearch:true});
 const text=`${prompt}\n${followup}\n任务文件：${job.output.replace(/\.json$/,'.context.json')}\n输出契约：${job.output.replace(/\.json$/,'.schema.json')}\n输出：${job.output}\n下载资料仅保存在 ${task.assets_root}，引用 path 为板目录相对路径。quote 必须逐字摘录，PDF 引用填写实际 pdf_page；表格可用准确原文短引句配合完整 locator，不把总结改写当原文。\n${feedback?'上次取证交付的实际校验错误，修复并重新查证：'+feedback:''}\n研究完写 JSON 并结束，不写正式评审卡。`;
 const run=await agent.send(text);let timer;
 const pump=(async()=>{for await(const event of run.stream())await appendFile(path.join(folder,'events.jsonl'),JSON.stringify({ts:new Date().toISOString(),event})+'\n');})();
 try{
  result=await Promise.race([run.wait(),new Promise((_,reject)=>{timer=setTimeout(async()=>{await run.cancel();reject(Error('research time budget exceeded'));},1200000);})]);
  await pump;
  if(result.status==='completed'){
   const draft=JSON.parse(await readFile(job.output,'utf8'));
   const assetsRoot=path.resolve(agent.workspace.root,task.assets_root);
   for(const fact of draft.facts||[])for(const ev of fact.evidence||[]){
    if(typeof ev.path!=='string')continue;
    const src=path.resolve(agent.workspace.root,ev.path);
    const rel=path.relative(assetsRoot,src);
    if(!rel.startsWith('..')&&!path.isAbsolute(rel)&&src!==assetsRoot){
     const info=await stat(src);
     if(!info.isFile()||info.size>50*1024*1024)throw Error('Invalid research download');
     const dest=path.resolve(job.board,ev.path),inside=path.relative(path.resolve(job.board,task.assets_root),dest);
     if(inside.startsWith('..')||path.isAbsolute(inside))throw Error('Download target escaped proposal');
     await mkdir(path.dirname(dest),{recursive:true});await cp(src,dest);
    }
   }
  }
 }finally{clearTimeout(timer);}
}catch(e){result={status:'error',error:String(e.message||e)};}
await writeFile(path.join(folder,'run.json'),JSON.stringify(result,null,2));
await appendFile(path.join(folder,'run_history.jsonl'),JSON.stringify({ts:new Date().toISOString(),...result})+'\n');
console.log('RESEARCH_MODEL',result.id||'',result.status,result.error||'');
process.exitCode=result.status==='completed'?0:2;
