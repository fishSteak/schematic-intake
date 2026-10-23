import {readFileSync,statSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {readEprjProject} from './eprj-reader.mjs';
import {readSchematicModel} from './schematic-model.mjs';
export {readEprjProject};
// Analysis JSON only. Raw document records and the internal synthetic ZIP are
// deliberately excluded; this is not a lossless editor interchange format.
function packModel(model, meta){
 const groups=new Map();
 for(const symbol of model.components){const group=groups.get(symbol.ref)??[];group.push(symbol);groups.set(symbol.ref,group);}
 const instances=[...groups].map(([ref,units])=>({ref,symbolIds:units.map(u=>({pageId:u.location.pageId,componentId:u.location.componentId,partId:u.partId})),locations:units.map(u=>u.location),mpn:units[0].mpn,manufacturer:units[0].attributes.Manufacturer??'',value:units[0].value,package:units[0].footprintName,pins:model.connectivity.pinRecords.filter(p=>p.object.startsWith(ref+'.')).map(p=>({...p,net:model.connectivity.pinToNet[p.object]}))}));
 const connectionIssues=model.connectivity.issues;
 return {schemaVersion:1,parserVersion:meta.parserVersion,source:meta.source,summary:{pageCount:model.pages.length,symbolCount:model.components.length,physicalCount:instances.length},complete:Boolean(meta.coverageComplete)&&connectionIssues.length===0,coverage:meta.coverage,issues:model.issues,connectionIssues,pages:model.pages,pageFields:model.pageFields,symbols:model.components,instances,annotations:model.annotations,connectivity:model.connectivity};
}
export function convertProject(path,{expectedSha256}={}){
 if(statSync(path).size>64*1024*1024)throw Error('EPRJ_SIZE');
 const original=readFileSync(path);
 const fingerprint=expectedSha256??createHash('sha256').update(original).digest('hex');
 if(original[0]===0x50&&original[1]===0x4b){
  const model=readSchematicModel(original);
  return packModel(model,{
   parserVersion:'epro2-native-archive-1',
   source:{sha256:fingerprint,format:'epro2'},
   coverage:{complete:model.issues.length===0,parsedPages:model.pages.length,expectedPages:model.pages.map(p=>p.id),note:'native zip/epro2; not full-history eprj2'},
   coverageComplete:model.issues.length===0,
  });
 }
 const r=readEprjProject(path,fingerprint);
 return packModel(r.model,{parserVersion:r.parserVersion,source:r.source,coverage:r.coverage,coverageComplete:r.coverage.complete});
}
