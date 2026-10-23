/** Complete object analysis followed by a focused evidence check; no separate re-judging card turn. */
import {readFile,appendFile,access} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {Agent} from './codex_agent.mjs';
import {decodeValidation,VALIDATION_PREFIX} from './validation_transport.mjs';
import {priorReviewInputs} from './reuse_inputs.mjs';
const ROOT=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const read=p=>readFile(path.join(ROOT,p),'utf8');
const exists=async p=>{try{await access(p);return true;}catch{return false;}};
function validate(workspace,rid,obj,diagnostics){
 const code=`import sys,json\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom kernel.util import read_json\nfrom agent_review.validate_job import validate_judge_card\nfrom agent_review.decision_contract import validate_decision_audit\nfrom agent_review.evidence_loop import validate_object_research\nfrom agent_review.delivery import validate\nb=Path(sys.argv[2]);rid=sys.argv[3];obj=sys.argv[4];p=b/'review_out/agent_jobs'/rid/(obj+'.json')\ntry:\n c=read_json(p);e=validate_judge_card(c,rule_id=rid,obj=obj,board=b)+validate(c,read_json(p.with_suffix('.context.json')))\n if rid=='P11-A01' and c.get('schema_version')!='review.v1':e+=validate_decision_audit(c,required=True)\n e+=validate_object_research(b,rid,obj,c)\n print(${JSON.stringify(VALIDATION_PREFIX)}+json.dumps(e,ensure_ascii=False))\nexcept Exception as x:print(${JSON.stringify(VALIDATION_PREFIX)}+json.dumps([str(x)]))`;
 const r=spawnSync(process.env.SCHEMATIC_PYTHON||'python',['-c',code,ROOT,workspace,rid,obj],{encoding:'utf8',windowsHide:true,maxBuffer:1024*1024,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 const noise=String(r.stdout||'').split(/\r?\n/).filter(line=>line&&!line.startsWith(VALIDATION_PREFIX)).join('\n');
 if(noise||r.stderr)diagnostics.push({stdout:noise.slice(-16000),stderr:String(r.stderr||'').slice(-16000)});
 return decodeValidation(r);
}
export async function runDeliveryJob(board,job,model){
 const started=Date.now(),rid=job.rule_id,obj=job.objects[0],base=path.join(board,'review_out/agent_jobs',rid,obj);
 const wb=JSON.parse(await readFile(base+'.context.json','utf8'));const native=job.contract_version==='review.v1';
 const common=await read(native?'agent_review/prompts/judge/review_v1.common.md':'agent_review/prompts/_common_judge.md');
 const rule=await read(native?'agent_review/prompts/judge/review_v1.power.md':`agent_review/prompts/judge/${rid}.md`);
 const analysis=await read(`agent_review/prompts/judge/${rid}.analysis.md`);
 const delivery=await read('agent_review/prompts/delivery_review.md');
 const extra=rid==='P11-A01'&&!native?await read('agent_review/prompts/judge/_power_decision.md'):'';
 const selfPath=path.join(ROOT,'agent_review/prompts/judge',rid+'.self_check.md');
 const selfCheck=await readFile(await exists(selfPath)?selfPath:path.join(ROOT,'agent_review/prompts/judge',native?'review_v1.self_check.md':rid==='P11-A01'?'_p11_self_check.md':'_deep_self_check.md'),'utf8');
 const inputs=[];
 const reuse=process.env.AGENT_DELIVERY_REUSE_ANALYSIS==='1';
 const prior=reuse?await priorReviewInputs(board,base):{inputs:[],hashes:{},missing:[]};
 const priorInputs=prior.hashes;inputs.push(...prior.inputs);
 for(const rel of wb.delivery_policy?.independent_documents||[]){const p=path.resolve(board,rel);if(!p.startsWith(path.resolve(board)+path.sep))throw Error('Independent input escaped board');if(await exists(p))inputs.push(p);}
 const prompt=`${common}\n${analysis}\n${rule}\n${extra}\n${delivery}\n【本次delivery.v1完整草稿任务，优先于旧提示的“本轮只写Markdown/最后另轮落卡”说明】\n检查项${rid}，对象${obj}。先读review_out/agent_jobs/${rid}/${obj}.context.json及其Schema。\n必须一次写完整分析${base}.analysis.md、正式卡${base}.json和查证记录${base}.research.json（同目录.research.schema.json）。${native?'正式卡严格review.v1，先读.schema.json和contract_task。':'旧卡按检查项契约，统一analysis.v1；main_analysis启用时另读.main.schema.json。'}\n同目录文件和已完成查证是本对象的唯一输出，禁止跨对象、修改材料或伪造运行元数据。`;
 let runId=null,errors=[];const diagnostics=[];
 try{
  await using agent=await Agent.create({model:{id:model},ruleId:rid,local:{cwd:board},outputs:[base+'.analysis.md',base+'.json',base+'.research.json'],inputs,allowPriorAnalysis:reuse,autonomousResearch:true,executionHash:job.execution_hash});
  async function turn(text,stage,number){
   const run=await agent.send(text);let timer;
   const pump=(async()=>{for await(const event of run.stream())await appendFile(base+'.run.jsonl',JSON.stringify({ts:new Date().toISOString(),turn:number,stage,event})+'\n');})();
   try{const result=await Promise.race([run.wait(),new Promise((_,reject)=>{timer=setTimeout(async()=>{await run.cancel();reject(Error(stage+' timeout'));},Number(process.env.AGENT_DELIVERY_TURN_TIMEOUT_MS||600000));})]);await pump;runId=result.id;if(result.status!=='completed')throw Error(result.error||result.status);await appendFile(base+'.run.jsonl',JSON.stringify({ts:new Date().toISOString(),type:'phase_completed',phase:stage,run_id:runId,elapsed_ms:Date.now()-started})+'\n');}
   finally{clearTimeout(timer);}
  }
  if(reuse)await appendFile(base+'.run.jsonl',JSON.stringify({ts:new Date().toISOString(),type:'prior_artifacts',hashes:priorInputs})+'\n');
  await turn(prompt+(reuse?'\n【定向复核】现有本对象分析、卡和研究记录仅为待审草稿。重点核查新增独立证据、完整原条款与明确缺口；复用已成立推导，不为复述重做无关检索。实际回查决定性原始来源，说明采纳或拒绝新论证的理由，同步修订三个产物；不得把旧运行元数据当作本次记录。缺失的既有附件须重新取得或撤销对应失效事实，不保留悬空引用：'+JSON.stringify(prior.missing):''),'analysis_draft',1);
  errors=validate(agent.workspace.root,rid,obj,diagnostics);
  // Do not call an independent evidence check a formatting turn.
  if(job.self_check!==false||errors.length){
   await turn(`${delivery}\n${job.self_check!==false?selfCheck:'仅修复以下契约错误，不重新推导电路。'}\n【决定性证据核查】检查现有分析、正式卡和research，修正受影响段落并同步三个文件。不重复查证已成立资料，不扩展无关保证。已列校验问题：${JSON.stringify(errors)}。不得以改材料或猜测数值过门禁。`,job.self_check!==false?'decisive_check':'contract_repair',2);
   errors=validate(agent.workspace.root,rid,obj,diagnostics);
  }
  if(errors.length)await turn(`${delivery}\n只定向修复现有产物的实际字段/引用错误，不重做整份分析：${JSON.stringify(errors)}。需要改变电子结论时必须在分析说明原始依据。输出仍为原指定三个文件。`,'contract_repair',3);
  errors=validate(agent.workspace.root,rid,obj,diagnostics);
  return {job_id:job.job_id,ok:errors.length===0,error:errors.join('; '),validation_errors:errors,run:runId,mode:'delivery.v1',elapsed_ms:Date.now()-started,prior_artifacts:priorInputs,validator_diagnostics:diagnostics};
 }catch(e){return {job_id:job.job_id,ok:false,error:String(e.message||e),run:runId,validation_errors:errors,mode:'delivery.v1',elapsed_ms:Date.now()-started};}
}
