/** Resolve source-file dependencies of a previous model draft before reuse. */
import {readFile,access} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import path from 'node:path';

export async function priorReviewInputs(board,base){
 const inputs=new Set(),hashes={},missing=[];let research,card;
 async function add(file,required=false){
  const rel=path.relative(board,file);
  if(rel.startsWith('..')||path.isAbsolute(rel))throw Error('Prior source escaped board');
  try{await access(file);}catch{if(required)missing.push(rel);return;}
  const body=await readFile(file);inputs.add(file);hashes[rel]=createHash('sha256').update(body).digest('hex');
  return body;
 }
 for(const suffix of ['.analysis.md','.json','.research.json']){
  const body=await add(base+suffix);
  if(suffix==='.research.json'&&body)research=JSON.parse(body.toString('utf8'));
  if(suffix==='.json'&&body)card=JSON.parse(body.toString('utf8'));
 }
 const evidenceRefs=[...(research?.facts||[]).flatMap(f=>f.evidence||[]),...(card?.evidence_refs||[]),...(card?.evidence||[])];
 for(const evidence of evidenceRefs){
  if(evidence.kind==='rule'&&evidence.path?.replaceAll('\\','/').startsWith('rules/'))continue;
  if(typeof evidence.path!=='string'||path.isAbsolute(evidence.path))throw Error('Invalid prior evidence path');
  await add(path.resolve(board,evidence.path),true);
 }
 return {inputs:[...inputs],hashes,missing};
}
