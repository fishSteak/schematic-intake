import {readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {prepareMaterials,disposeMaterials} from '../agent_review/local_workspace.mjs';
const board=path.resolve(process.argv[2]);const snapshot=await prepareMaterials(board);
try {
  const config=JSON.parse(await readFile(path.join(snapshot,'materials.json'),'utf8'));
  const checks={};
  for(const [key,rel] of Object.entries(config)) {
    if(typeof rel!=='string'||!rel.startsWith('material_inputs/'))continue;
    const hash=async p=>createHash('sha256').update(await readFile(p)).digest('hex');
    const local=await hash(path.join(snapshot,rel)),published=await hash(path.join(board,rel));
    checks[key]={identical:local===published,sha256:local};
  }
  const result={passed:Object.values(checks).every(c=>c.identical),checks};
  await writeFile(path.join(board,'review_out/material_snapshot_verification.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({passed:result.passed,configured_materials:Object.keys(checks)}));
} finally {await disposeMaterials(snapshot);}
