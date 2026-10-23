export function readPageFields(docs){
 return docs.filter(d=>d.head.docType==='SCH_PAGE').map(page=>{
  const title=page.records.find(r=>r.header.type==='META')?.body.title??page.head.uuid;
  const frames=[];
  for(const c of page.records.filter(r=>r.header.type==='COMPONENT')){
   const attrs=page.records.filter(r=>r.header.type==='ATTR'&&r.body.parentId===c.header.id);
   // Native frame identity, not arbitrary text containing "A4" or "Reviewed".
   if(!attrs.some(r=>r.body.key==='Title Block')||!attrs.some(r=>r.body.key==='Size'))continue;
   const fields={},attributeIds={};let conflict=false;
   for(const r of attrs){if(Object.hasOwn(fields,r.body.key))conflict=true;fields[r.body.key]=r.body.value;attributeIds[r.body.key]=r.header.id;}
   frames.push({fields,attributeIds,conflict,location:{pageId:page.head.uuid,pageName:title,componentId:c.header.id,x:c.body.x,y:c.body.y}});
  }
  return {id:page.head.uuid,title,frames,opaqueContent:page.records.filter(r=>['TABLE','BLOB','OBJ','ELE_PLACEHOLDER'].includes(r.header.type)).map(r=>({id:r.header.id,type:r.header.type}))};
 });
}
