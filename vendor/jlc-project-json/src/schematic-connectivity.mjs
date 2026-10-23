// Read-only JLC geometry projection. Does not consume TEL while building the
// graph and never calls itself native ERC. Unsupported hierarchy/bus/geometry
// is explicit instead of disappearing from a successful comparison.
const records=(d,t)=>d.records.filter(r=>r.header.type===t);
const attrs=(d,id)=>Object.fromEntries(records(d,'ATTR').filter(r=>r.body?.parentId===id).map(r=>[r.body.key,r.body.value]));
const close=(a,b)=>Math.abs(a-b)<1e-6;
const same=(a,b)=>close(a[0],b[0])&&close(a[1],b[1]);
const ends=s=>[[s.startX,s.startY],[s.endX,s.endY]];
const on=(p,s)=>Math.abs((p[0]-s.startX)*(s.endY-s.startY)-(p[1]-s.startY)*(s.endX-s.startX))<1e-5&&p[0]>=Math.min(s.startX,s.endX)-1e-6&&p[0]<=Math.max(s.startX,s.endX)+1e-6&&p[1]>=Math.min(s.startY,s.endY)-1e-6&&p[1]<=Math.max(s.startY,s.endY)+1e-6;
function point(place,p){
 if(![place.x,place.y,place.rotation,p.x,p.y].every(Number.isFinite)||typeof place.isMirror!=='boolean'||![0,90,180,270].includes(place.rotation))throw Error('尚不支持或无效的符号变换');
 let x=place.isMirror?-p.x:p.x,y=p.y,a=place.rotation*Math.PI/180;
 return [place.x+x*Math.cos(a)+y*Math.sin(a),place.y-x*Math.sin(a)+y*Math.cos(a)].map(v=>Math.round(v*1e8)/1e8);
}
export function readSchematicConnectivity(docs,components){
 const groups=[],pins=[],ports=[],issues=[],byId=new Map(docs.map(d=>[d.head.uuid,d]));
 if(docs.filter(d=>d.head.docType==='SCH').length!==1)issues.push('当前连接解析仅验收单原理图作用域；多层级工程尚不能给出完整连接结论');
 const parent=[];const make=()=>{let i=parent.length;parent.push(i);return i;};
 const root=i=>parent[i]===i?i:(parent[i]=root(parent[i]));const join=(a,b)=>{parent[root(a)]=root(b);};
 for(const page of docs.filter(d=>d.head.docType==='SCH_PAGE')){
  const pageName=records(page,'META')[0]?.body.title??page.head.uuid;
  const local=[];
  for(const w of records(page,'WIRE')){
   const segments=records(page,'LINE').filter(l=>l.body?.lineGroup===w.header.id).map(l=>l.body),at=attrs(page,w.header.id);
   if(!segments.length||segments.some(s=>!ends(s).flat().every(Number.isFinite)||same(...ends(s)))){issues.push(`${pageName}/${w.header.id}导线几何无效`);continue;}
   if(at.Bus||at['Bus Name'])issues.push(`${pageName}包含尚未验收的总线`);
   // A WIRE container may not silently join disjoint drawn segments.
   const reached=new Set([0]);let again=true;
   while(again){again=false;for(let i=0;i<segments.length;i++)if(!reached.has(i)&&[...reached].some(j=>ends(segments[i]).some(p=>on(p,segments[j]))||ends(segments[j]).some(p=>on(p,segments[i])))){reached.add(i);again=true;}}
   if(reached.size!==segments.length)issues.push(`${pageName}/${w.header.id}同导线组含断开的线段`);
   const g={node:make(),segments,names:at.NET?[String(at.NET)]:[],location:{pageId:page.head.uuid,pageName,wireId:w.header.id}};local.push(g);groups.push(g);
  }
  // Endpoint/T junctions connect; an unmarked crossing of segment interiors
  // does not. The native WIRE group supplies explicit same-group junctions.
  for(let i=0;i<local.length;i++)for(let j=0;j<i;j++)if(local[i].segments.some(a=>local[j].segments.some(b=>ends(a).some(p=>on(p,b))||ends(b).some(p=>on(p,a)))))join(local[i].node,local[j].node);
  const pagePins=[];
  for(const raw of records(page,'COMPONENT')){
   const at=attrs(page,raw.header.id),device=byId.get(at.Device),meta=device&&records(device,'META')[0]?.body;
   const effective={...meta?.attributes};for(const[k,v]of Object.entries(at))if(v!=null)effective[k]=v;
   const c=components.find(c=>c.location.pageId===page.head.uuid&&c.location.componentId===raw.header.id);
   const symbol=byId.get(effective.Symbol),symbolPins=symbol?records(symbol,'PIN').filter(p=>p.body.partId===raw.body.partId):[];
   const isPort=meta?.tags?.includes('网络端口'),isFlag=meta?.tags?.includes('网络标识');
   if(!c&&!isPort&&!isFlag){if(symbolPins.length)issues.push(`${pageName}/${raw.header.id}未识别电气对象`);continue;}
   if(!symbolPins.length){issues.push(`${pageName}/${c?.ref??raw.header.id}未解析符号引脚`);continue;}
   for(const p of symbolPins){
    try{
     const atPin=attrs(symbol,p.header.id),pos=point(raw.body,p.body),node=make();
     const location={pageId:page.head.uuid,pageName,componentId:raw.header.id,pinId:p.header.id,x:pos[0],y:pos[1]};
     for(const g of local)if(g.segments.some(s=>on(pos,s)))join(node,g.node);
     for(const previous of pagePins)if(same(pos,previous.pos))join(node,previous.node);
     pagePins.push({node,pos});
     if(c){
      const number=String(atPin['Pin Number']??'');if(!number)issues.push(`${c.ref}引脚号为空`);
      else pins.push({node,object:`${c.ref}.${number}`,location,pinName:atPin['Pin Name']??'',pinType:atPin['Pin Type']??'',excludedPcb:c.excludedPcb});
     }else{
      const name=String(isFlag?effective['Global Net Name']??'':effective.Name??'');
      if(!name)issues.push(`${pageName}/${raw.header.id}网络标识为空`);
      const g={node,names:name?[name]:[],segments:[],location};groups.push(g);
      if(isPort)ports.push({node,name,location,attributes:effective});
     }
    }catch(e){issues.push(`${pageName}/${c?.ref??raw.header.id}：${e.message}`);}
   }
  }
 }
 // Freeze physical page connectivity BEFORE globally merging equal labels.
 // Otherwise a floating port appears connected through its remote namesake.
 for(const p of ports)p.localPins=[...new Set(pins.filter(pin=>root(pin.node)===root(p.node)&&!pin.excludedPcb).map(pin=>pin.object))].sort();
 const named=new Map();for(const g of groups)for(const name of g.names){if(named.has(name))join(g.node,named.get(name));else named.set(name,g.node);}
 const nets=new Map();const net=i=>{let key=root(i);if(!nets.has(key))nets.set(key,{names:[],pins:[],locations:[]});return nets.get(key);};
 for(const g of groups){net(g.node).names.push(...g.names);net(g.node).locations.push(g.location);}
 for(const p of pins)if(!p.excludedPcb)net(p.node).pins.push(p.object);
 const pinToNet={},pinLocations={},pinRecords=[];
 for(const p of pins){pinLocations[p.object]=p.location;pinRecords.push({...p,node:undefined});}
 const networks=[...nets.values()].filter(n=>n.pins.length).map(n=>({...n,names:[...new Set(n.names)].sort(),pins:[...new Set(n.pins)].sort()}));
 for(const n of networks){if(n.names.length>1)issues.push(`同一连接分量具有冲突网络名：${n.names.join('、')}`);for(const p of n.pins){if(Object.hasOwn(pinToNet,p))issues.push(`${p}在多个原生连接分量出现`);pinToNet[p]=n.names.length===1?n.names[0]:null;}}
 return {version:'jlc-schematic-geometry-1',networks,pinToNet,pinLocations,pinRecords,ports:ports.map(p=>({...p,node:undefined})),issues};
}
