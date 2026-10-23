/** Model-only contract repair. Facts and checks come from current job artifacts. */
import {readFile,writeFile,appendFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {Agent} from '../agent_review/codex_agent.mjs';
import {prepareMaterials,disposeMaterials} from '../agent_review/local_workspace.mjs';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const board=process.argv[2];
const out=path.join(board,'review_out');
const doc=JSON.parse(await readFile(path.join(out,'judge_jobs.json'),'utf8'));
const model=process.env.AGENT_JUDGE_MODEL;
if(!model)throw Error('An explicit review model is required');
const snapshot=await prepareMaterials(board);
process.env.SCHEMATIC_MATERIAL_SNAPSHOT=snapshot;
let cursor=0;const results=[];
async function execute(job){
 const rid=job.rule_id,obj=job.objects[0];
 const card=path.join(out,'agent_jobs',rid,obj+'.json');
 const analysis=card.replace(/\.json$/,'.analysis.md');
 const log=card.replace(/\.json$/,'.repair.run.jsonl');
 const common=(await readFile(path.join(root,'agent_review/prompts/_common_judge.md'),'utf8'))+(job.delivery_version?'\n'+await readFile(path.join(root,'agent_review/prompts/delivery_review.md'),'utf8'):'');
 const native=job.contract_version==='review.v1';
 const rules=await readFile(path.join(root,'agent_review/prompts/judge',native?'review_v1.common.md':rid+'.md'),'utf8');
 const power=native?await readFile(path.join(root,'agent_review/prompts/judge/review_v1.power.md'),'utf8'):
   rid==='P11-A01'?await readFile(path.join(root,'agent_review/prompts/judge/_power_decision.md'),'utf8'):'';
 const validator=await readFile(path.join(root,'agent_review/validate_job.py'),'utf8');
 const contractCode=!native&&rid==='P11-A01'?validator.slice(validator.indexOf('def validate_power_decision'),validator.indexOf('_P11 =')):'';
 const calcStart=validator.indexOf('            calc = str(card.get("calc")');
 const calcCode=!native&&['P11-A01','P11-A03'].includes(rid)?validator.slice(calcStart,validator.indexOf('        if len(str(card.get("summary")',calcStart)):'';
 const context=`检查项 ${rid}，对象 ${obj}。工作台：review_out/agent_jobs/${rid}/${obj}.context.json；已有分析：review_out/agent_jobs/${rid}/${obj}.analysis.md。只处理该对象，禁止修改材料或 context_store。`;
 const prefix=`${common}\n${rules}\n${power}\n${context}\n这是当前模型输出的契约修复，不是标准答案。依据原始材料和已完成分析修复实际校验错误；不得为了通过校验改变或编造电路事实。执行失败或 provenance 错误由控制器在本次真实运行完成后处理，不要伪造 _meta、_execution 或运行 ID。研究记录的 conflicts.fact_refs 只能引用本文件 facts 中实际定义的 ID，不能直接引用共享证据库的 ID；需要记录已解决冲突时，将对应原始来源及事实纳入本文件 facts，并说明解决理由。共享事实可以直接复用为查证背景，但不能将共享摘要当作原始来源。\n实际校验器的相关字段契约（不含任何评审答案）：\n${contractCode}\n${calcCode}\n校验错误：${JSON.stringify(job.validation_errors)}\n本对象已有JSON文件会作为待修草稿提供；先读相关字段并定向修改，不重新抄写整张卡。仅当实际原始证据要求时才改变电子判定，并说明依据。`;
 const started=Date.now();let runId=null;
 try{
  const inputs=[];
  try{await readFile(card);inputs.push(card);}catch(e){if(e.code!=='ENOENT')throw e;}
  if(job.autonomous_evidence){
   const receipt=card.replace(/\.json$/,'.research.json');
   try{
    const prior=JSON.parse(await readFile(receipt,'utf8'));inputs.push(receipt);
    for(const fact of prior.facts||[])for(const ev of fact.evidence||[]){
     if(typeof ev.path!=='string'||path.isAbsolute(ev.path))continue;
     const file=path.resolve(board,ev.path),rel=path.relative(board,file);
     if(rel.startsWith('..')||path.isAbsolute(rel))throw Error('Research input escaped board');
     await readFile(file);inputs.push(file);
    }
   }catch(e){if(e.code!=='ENOENT'&&!(e instanceof SyntaxError))throw e;}
  }
  await using agent=await Agent.create({model:{id:model},ruleId:rid,executionHash:job.execution_hash,
    local:{cwd:board},outputs:[card,analysis,...(job.autonomous_evidence?[card.replace(/\.json$/,'.research.json')]:[])],
    inputs:[...new Set(inputs)],autonomousResearch:job.autonomous_evidence===true,allowPriorAnalysis:true,analysisCheckpointRoot:job.checkpoint_root||null});
  const prompts=[];
  if(!job.resume_stage){
   const p=await readFile(path.join(root,'agent_review/prompts/judge',rid+'.analysis.md'),'utf8');
   prompts.push({stage:'analysis',prompt:`${common}\n${p}\n${context}\n先从材料完成分析，仅写 ${analysis}，不写 JSON。`});
  }
  if(job.self_check!==false&&job.resume_stage!=='self_check'){
   const p=await readFile(path.join(root,'agent_review/prompts/judge',native?'review_v1.self_check.md':'_deep_self_check.md'),'utf8');
   prompts.push({stage:'self_check',prompt:`${common}\n${p}\n${context}\n核对并修订 ${analysis}，不写 JSON。`});
  }
  prompts.push({stage:'card',prompt:`${prefix}\n先读工作台与分析，按规则重写 ${card}。必要时同步修订 ${analysis}，不得只输出解释。${job.autonomous_evidence?`同时必须写独立文件 ${card.replace(/\.json$/,'.research.json')}，按 ${card.replace(/\.json$/,'.research.schema.json')} 交付 evidence.v1；不能只交正式判定卡。已核实的共享事实可以复用，不必重新列作新增 facts，但 search_log 必须记录实际读过的来源；待确认要分类记录决定性请求及已尝试的查证。`:''}修复后自行核对所有已列校验要求。`});
  for(let i=0;i<prompts.length;i++){
   const run=await agent.send(prompts[i].prompt);let timer;
   const pump=(async()=>{for await(const event of run.stream())await appendFile(log,JSON.stringify({ts:new Date().toISOString(),turn:i+1,event})+'\n');})();
   try{
    const result=await Promise.race([run.wait(),new Promise((_,reject)=>{timer=setTimeout(async()=>{await run.cancel();reject(Error('repair turn timeout'));},900000);})]);
    await pump;runId=result.id;if(result.status!=='completed')throw Error(result.error||result.status);
    if(prompts[i].stage!=='card')await agent.workspace.saveStageCheckpoint({ruleId:rid,executionHash:job.execution_hash},prompts[i].stage);
    await appendFile(log,JSON.stringify({ts:new Date().toISOString(),type:'phase_completed',phase:prompts[i].stage,run_id:runId})+'\n');
   }finally{clearTimeout(timer);}
  }
  const localCard=path.join(agent.workspace.root,path.relative(board,card));
  await readFile(localCard,'utf8');
  await agent.workspace.collect();
  return {job_id:job.job_id,ok:true,run:runId,mode:'contract_repair',elapsed_ms:Date.now()-started};
 }catch(e){return {job_id:job.job_id,ok:false,error:String(e.message||e),run:runId};}
}
try{
 await Promise.all(Array.from({length:Math.min(doc.concurrency,doc.jobs.length)},async()=>{
  while(cursor<doc.jobs.length){const job=doc.jobs[cursor++],r=await execute(job);results.push(r);console.log('REPAIR',results.length+'/'+doc.jobs.length,r.ok?'ok':'bad',r.job_id,r.error||'');}
 }));
 await writeFile(path.join(out,'sdk_judge_result.json'),JSON.stringify({results,mode:'contract_repair',attempt_id:doc.attempt_id},null,2));
}finally{delete process.env.SCHEMATIC_MATERIAL_SNAPSHOT;await disposeMaterials(snapshot);}
process.exitCode=results.some(r=>!r.ok)?2:0;
