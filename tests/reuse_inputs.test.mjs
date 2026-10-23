import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import os from 'node:os';import path from 'node:path';
import {priorReviewInputs} from '../agent_review/reuse_inputs.mjs';

test('draft reuse includes cited object assets and reports genuinely missing sources',async()=>{
 const board=await mkdtemp(path.join(os.tmpdir(),'schematic-reuse-test-'));
 try{
  const base=path.join(board,'O');await mkdir(path.join(board,'assets'));
  await writeFile(path.join(board,'assets/source.pdf'),'actual source bytes');
  await writeFile(base+'.analysis.md','existing analysis');
  await writeFile(base+'.research.json',JSON.stringify({facts:[{evidence:[{path:'assets/source.pdf'},{path:'assets/missing.pdf'}]}]}));
  await writeFile(path.join(board,'assets/card-only.pdf'),'card evidence');
  await writeFile(base+'.json',JSON.stringify({evidence_refs:[{path:'assets/card-only.pdf'}]}));
  const result=await priorReviewInputs(board,base);
  assert.ok(result.inputs.includes(path.join(board,'assets/card-only.pdf')));
  assert.ok(result.inputs.includes(path.join(board,'assets/source.pdf')));
  assert.equal(result.hashes[path.join('assets','source.pdf')].length,64);
  assert.deepEqual(result.missing,[path.join('assets','missing.pdf')]);
  await writeFile(base+'.research.json',JSON.stringify({facts:[{evidence:[{path:'../escape.pdf'}]}]}));
  await assert.rejects(()=>priorReviewInputs(board,base),/escaped board/);
 }finally{
  assert.equal(path.dirname(path.resolve(board)),path.resolve(os.tmpdir()));
  assert.ok(path.basename(board).startsWith('schematic-reuse-test-'));
  await rm(board,{recursive:true,force:true});
 }
});
