/** Repair evidence categorization only; never rerun or change the electronic verdict. */
import {readFile,writeFile,appendFile} from 'node:fs/promises';
import path from 'node:path';
import {Agent} from '../agent_review/codex_agent.mjs';
const [board,rid,obj]=process.argv.slice(2);
const dir=path.join(board,'review_out/agent_jobs',rid);
const file=path.join(dir,obj+'.research.json');
const receipt=JSON.parse(await readFile(file,'utf8'));
const card=JSON.parse(await readFile(path.join(dir,obj+'.json'),'utf8'));
const schema=await readFile(path.join(dir,obj+'.research.schema.json'),'utf8');
await using agent=await Agent.create({model:{id:process.env.AGENT_JUDGE_MODEL},local:{cwd:board},outputs:[file],inputs:[file],allowPriorAnalysis:true});
const prompt=`Only repair this existing evidence.v1 receipt at review_out/agent_jobs/${rid}/${obj}.research.json. Do not analyze the circuit, search, modify the verdict, or create new facts. Existing calculations are derived claims, not primary source facts. Remove any facts supported only by .analysis.md/.research.json or shared summaries from the facts array; preserve the distinction in existing reasoning requests/search_log as appropriate. Keep valid original-source facts and literal quotes unchanged. Fix conflict references if a removed fact was referenced. Keep unresolved reasoning gaps and do not invent business requests. Write the complete receipt only. Schema: ${schema}\nExisting receipt: ${JSON.stringify(receipt)}\nExisting verdict context, read only: ${JSON.stringify({verdict:card.verdict_zh,decision_basis:card.decision_basis})}`;
const run=await agent.send(prompt);
const log=file.replace('.research.json','.receipt-repair.run.jsonl');
const pump=(async()=>{for await(const event of run.stream())await appendFile(log,JSON.stringify({ts:new Date().toISOString(),event})+'\n');})();
const result=await run.wait();await pump;
await writeFile(file.replace('.research.json','.receipt-repair.run.json'),JSON.stringify(result,null,2));
if(result.status!=='completed')throw Error(result.error||result.status);
console.log('RECEIPT_REPAIR_COMPLETED',result.id);
