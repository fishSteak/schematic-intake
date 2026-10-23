import {Agent} from './codex_agent.mjs';
import {readFile,writeFile,mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
const [board,output,promptPath,resultPath]=process.argv.slice(2);
// Prose reads only explicitly supplied context, never raw electrical materials.
const emptySnapshot=await mkdtemp(path.join(os.tmpdir(),'schematic-prose-'));
process.env.SCHEMATIC_MATERIAL_SNAPSHOT=emptySnapshot;
await using agent=await Agent.create({model:{id:'gpt-6.1-sol'},local:{cwd:board},outputs:[output]});
const prompt=await readFile(promptPath,'utf8');
const run=await agent.send(`${prompt}\n工作台：${output.replace(/\.json$/,'.context.json')}\n契约：${output.replace(/\.json$/,'.schema.json')}\n输出：${output}`);
let timer;
try {
 const result=await Promise.race([run.wait(),new Promise((_,reject)=>{timer=setTimeout(async()=>{await run.cancel();reject(new Error('detail prose timeout'));},600000);})]);
 await writeFile(resultPath,JSON.stringify(result,null,2));
 if(result.status!=='completed')throw new Error(result.error||result.status);
 console.log(`DETAIL_PROSE\t${result.id}\tcompleted`);
} finally {clearTimeout(timer);await rm(emptySnapshot,{recursive:true,force:true});}
