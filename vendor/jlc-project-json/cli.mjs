import {writeFileSync} from 'node:fs';
import {convertProject} from './src/index.mjs';
const [input,output,...extra]=process.argv.slice(2);
if(!input||!output||extra.length){console.error('用法：node cli.mjs <工程.eprj2|.epro2> <输出.json>');process.exitCode=1;}
else try{const result=convertProject(input);writeFileSync(output,JSON.stringify(result,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify({output,...result.summary,complete:result.complete}));if(!result.complete){console.error('解析存在缺口，请查看coverage、issues、connectionIssues。');process.exitCode=2;}}catch(e){console.error(e.code==='EEXIST'?'输出文件已存在，请换一个文件名。':e.message);process.exitCode=1;}
