import {createHash} from 'node:crypto';
import {readCurrentNativeArchive} from './native-readonly-archive.mjs';
import {readSchematicConnectivity} from './schematic-connectivity.mjs';
import {readPageFields} from './page-fields.mjs';
const hash=b=>createHash('sha256').update(b).digest('hex');
const clean=v=>typeof v==='string'?v.trim():'';
const find=(object,actual,requirement,location,suggestion)=>({object,actual,requirement,location,suggestion});
export function readSchematicModel(bytes){
 const archive=readCurrentNativeArchive(bytes,{sha256:hash(bytes),strictJson:true});
 const docs=archive.documents, components=[], issues=[];
 for(const page of docs.filter(d=>d.head.docType==='SCH_PAGE')){
  const title=page.records.find(r=>r.header.type==='META')?.body.title??page.head.uuid;
  for(const c of page.records.filter(r=>r.header.type==='COMPONENT')){
   const attrs={}, attributeIds={};
   for(const a of page.records.filter(r=>r.header.type==='ATTR'&&r.body.parentId===c.header.id)){
    if(Object.hasOwn(attrs,a.body.key))issues.push(find(c.header.id,a.body.key,'属性键不得重复',{pageId:page.head.uuid,pageName:title,componentId:c.header.id},'删除重复属性并复查。'));
    attrs[a.body.key]=a.body.value;attributeIds[a.body.key]=a.header.id;
   }
   const device=docs.find(d=>d.head.docType==='DEVICE'&&d.head.uuid===attrs.Device)?.records.find(r=>r.header.type==='META')?.body;
   // JLC instance null means inherit the archived DEVICE value; an explicit
   // empty string remains empty. Preserve provenance instead of inventing data.
   const merged={...device?.attributes},fieldSources={};
   for(const key of Object.keys(merged))fieldSources[key]={kind:'DEVICE',documentId:attrs.Device,field:key};
   for(const [key,value] of Object.entries(attrs))if(value!==null&&value!==undefined){merged[key]=value;fieldSources[key]={kind:'INSTANCE',attributeId:attributeIds[key],field:key};}
   const tags=device?.tags??[],rawRef=clean(attrs.Designator);
   if(!rawRef&&tags.includes('特殊器件'))continue;
   if(!rawRef&&merged['Convert to PCB']!=='yes'&&merged['Add into BOM']!=='yes')continue;
   const symbol=docs.find(d=>d.head.docType==='SYMBOL'&&d.head.uuid===merged.Symbol);
   const partIds=symbol?.records.filter(r=>r.header.type==='PART').map(r=>r.header.id)??[];
   const suffix=rawRef.match(/^(.+)\.(\d+)$/);
   const multiUnit=Boolean(partIds.length>1&&partIds.includes(c.body.partId));
   const ref=multiUnit&&suffix?suffix[1]:rawRef;
   const symbolTitle=symbol?.records.find(r=>r.header.type==='META')?.body.title;
   const kind=tags.includes('电阻')||symbolTitle==='RES'?'RESISTANCE':tags.includes('电容')||symbolTitle==='CAP'?'CAPACITANCE':null;
   const fp=docs.find(d=>d.head.docType==='FOOTPRINT'&&d.head.uuid===merged.Footprint);
   let fpNames=fp?.records.filter(r=>r.header.type==='ATTR'&&r.body?.key==='Footprint'&&!r.body.parentId)??[];
   if(!fpNames.length)fpNames=fp?.records.filter(r=>r.header.type==='ATTR'&&r.body?.key==='Name'&&!r.body.parentId)??[];
   const footprintName=fpNames.length===1?clean(fpNames[0].body.value):'';
   components.push({ref,rawRef,multiUnit,unit:multiUnit?c.body.partId:null,partId:c.body.partId,deviceId:attrs.Device,
    kind,tags,attributes:merged,rawAttributes:attrs,attributeIds,fieldSources,footprintName,
    footprintLocation:fp?{documentId:fp.head.uuid,attributeId:fpNames[0]?.header.id,field:fpNames[0]?.body.key}:null,
    value:clean(merged.Value),mpn:clean(merged['Manufacturer Part']),footprint:clean(merged.Footprint),
    location:{pageId:page.head.uuid,pageName:title,componentId:c.header.id,x:c.body.x,y:c.body.y},
    excludedPcb:merged['Convert to PCB']==='no',includedBom:merged['Add into BOM']==='yes'});
  }
 }
 const annotations=docs.filter(d=>d.head.docType==='SCH_PAGE').flatMap(d=>d.records.filter(r=>r.header.type==='TEXT'||r.header.type==='ATTR'&&r.body?.valueVisible===true).map(r=>({value:String(r.body?.value??''),location:{pageId:d.head.uuid,pageName:d.records.find(r=>r.header.type==='META')?.body.title,textId:r.header.id,x:r.body.x,y:r.body.y},key:r.body.key})));
 return {sourceSha256:hash(bytes),pageFields:readPageFields(docs),annotations,pages:docs.filter(d=>d.head.docType==='SCH_PAGE').map(d=>({id:d.head.uuid,title:d.records.find(r=>r.header.type==='META')?.body.title})),components,issues,connectivity:readSchematicConnectivity(docs,components)};
}
