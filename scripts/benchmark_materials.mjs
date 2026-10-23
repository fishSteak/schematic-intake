import path from 'node:path';
import {writeFile} from 'node:fs/promises';
import {LocalWorkspace,prepareMaterials,disposeMaterials} from '../agent_review/local_workspace.mjs';
const board=path.resolve(process.argv[2]), dest=path.resolve(process.argv[3]);
const results={};
for(const shared of [false,true]) {
  const start=performance.now();let root;
  try {
    if(shared) {root=await prepareMaterials(board);process.env.SCHEMATIC_MATERIAL_SNAPSHOT=root;}
    for(let i=0;i<3;i++) {
      const ws=await LocalWorkspace.create(board,[path.join(board,'review_out/benchmark-material/no-output.json')]);
      await ws.dispose();
    }
    results[shared?'one_NAS_snapshot_three_local_copies_ms':'three_NAS_copies_ms']=performance.now()-start;
  } finally {delete process.env.SCHEMATIC_MATERIAL_SNAPSHOT;if(root)await disposeMaterials(root);}
}
await writeFile(dest,JSON.stringify(results,null,2));console.log(JSON.stringify(results));
