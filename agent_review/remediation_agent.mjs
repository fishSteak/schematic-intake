import {Agent} from "./codex_agent.mjs";
import {readFile,writeFile} from "node:fs/promises";
const [board,output,promptPath,resultPath]=process.argv.slice(2);
const prompt=await readFile(promptPath,"utf8");
await using agent=await Agent.create({model:{id:process.env.AGENT_JUDGE_MODEL||"default"},local:{cwd:board},outputs:[output]});
const run=await agent.send(`${prompt}\n工作台：${output.replace(/\.json$/,'.context.json')}\n契约：${output.replace(/\.json$/,'.schema.json')}\n输出：${output}`);
let timer;
try {
 const result=await Promise.race([run.wait(),new Promise((_,reject)=>{timer=setTimeout(async()=>{await run.cancel();reject(new Error('remediation model timeout'));},300000);})]);
 await writeFile(resultPath,JSON.stringify(result,null,2));
 if(result.status!=='completed')throw new Error(result.error||result.status);
 console.log(`REMEDIATION_MODEL\t${result.id}\tcompleted`);
} finally {clearTimeout(timer);}
