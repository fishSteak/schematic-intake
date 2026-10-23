// Version-isolated read-only inventory codec, derived from frozen v1.
// No writer exports. Preserve opaque graphics; never assert native acceptance.
// ZIP/JSON validation is deliberately isolated: historical observation proofs
// pin the exact v1 module bytes. Do not relax that frozen verification policy.
import AdmZip from 'adm-zip';
import { createHash } from 'node:crypto';
import { crc32 } from 'node:zlib';

export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const recordTypes = new Set('ACTIVE_LAYER ATTR BLOB CANVAS CIRCLE COMPONENT DOCHEAD ELE_PLACEHOLDER ELLIPSE FILL LAYER LAYER_PHYS LINE META NET OBJ PAD PAD_NET PANELIZE PART PIN POLY PREFERENCE PRIMITIVE RECT RULE RULE_SELECTOR RULE_TEMPLATE SILK_OPTS TABLE TEXT UNIVERSAL WIRE'.split(' '));
const docTypes = new Set('FOOTPRINT SYMBOL DEVICE BOARD SCH SCH_PAGE PCB CONFIG PANEL BLOB'.split(' '));
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
function requireThat(ok, code) { if (!ok) throw new Error(code); }

function parseStrictJson(text, maxDepth=64) {
  let at=0;
  const whitespace=()=>{ while(/[\t\n\r ]/.test(text[at]??'')) at++; };
  const string=()=>{
    requireThat(text[at]==='"','JSON_SYNTAX'); const start=at++;
    while(at<text.length) {
      const code=text.charCodeAt(at),char=text[at++];
      requireThat(code>=0x20,'JSON_SYNTAX');
      if(char==='"') {
        try { return JSON.parse(text.slice(start,at)); } catch { throw new Error('JSON_SYNTAX'); }
      }
      if(char==='\\') {
        const escape=text[at++]; requireThat('"\\/bfnrtu'.includes(escape??''),'JSON_SYNTAX');
        if(escape==='u') { requireThat(/^[0-9a-fA-F]{4}$/.test(text.slice(at,at+4)),'JSON_SYNTAX'); at+=4; }
      }
    }
    throw new Error('JSON_SYNTAX');
  };
  const value=depth=>{
    whitespace(); const char=text[at];
    if(char==='{') {
      const nextDepth=depth+1; requireThat(nextDepth<=maxDepth,'JSON_DEPTH'); at++; whitespace();
      const result={}; const keys=new Set();
      if(text[at]==='}') { at++; return result; }
      while(true) {
        whitespace(); const key=string(); requireThat(!keys.has(key),'JSON_DUPLICATE_KEY'); keys.add(key);
        whitespace(); requireThat(text[at++]===':','JSON_SYNTAX');
        Object.defineProperty(result,key,{value:value(nextDepth),enumerable:true,writable:true,configurable:true});
        whitespace(); const separator=text[at++];
        if(separator==='}') return result;
        requireThat(separator===',','JSON_SYNTAX');
      }
    }
    if(char==='[') {
      const nextDepth=depth+1; requireThat(nextDepth<=maxDepth,'JSON_DEPTH'); at++; whitespace();
      const result=[]; if(text[at]===']') { at++; return result; }
      while(true) {
        result.push(value(nextDepth)); whitespace(); const separator=text[at++];
        if(separator===']') return result;
        requireThat(separator===',','JSON_SYNTAX');
      }
    }
    if(char==='"') return string();
    for(const [literal,result] of [['true',true],['false',false],['null',null]]) {
      if(text.startsWith(literal,at)) { at+=literal.length; return result; }
    }
    const match=text.slice(at).match(/^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/);
    requireThat(match,'JSON_SYNTAX'); at+=match[0].length;
    const result=Number(match[0]); requireThat(Number.isFinite(result),'JSON_NON_FINITE'); return result;
  };
  requireThat(typeof text==='string','JSON_SYNTAX');
  const result=value(0); whitespace(); requireThat(at===text.length,'JSON_SYNTAX'); return result;
}

// Opt-in reader only; no writing capability exists in this module. Empty LINE
// records remain opaque records, not copper, not reconstructed or deleted here.
export const READ_ONLY_GRAPHICS_PROFILE = 'READ_ONLY_GRAPHICS_V2';
export const READ_ONLY_GRAPHICS_PROFILE_V3 = 'READ_ONLY_GRAPHICS_V3';
export function parseEpru(text, {strictJson=false, sourceProfile}={}) {
  requireThat(sourceProfile === undefined || [READ_ONLY_GRAPHICS_PROFILE, READ_ONLY_GRAPHICS_PROFILE_V3].includes(sourceProfile), 'EPRU_SOURCE_PROFILE_UNSUPPORTED');
  const readOnlyGraphics = sourceProfile !== undefined;
  requireThat(typeof text === 'string' && text.length > 0 && text.length <= 64*1024*1024, 'EPRU_LIMIT');
  const parse=strictJson?parseStrictJson:JSON.parse;
  const docs = []; const ids = new Set(); let doc; let keys;
  docs.trailingNewline = text.endsWith('\n');
  const lines = text.split('\n'); if (docs.trailingNewline) lines.pop();
  requireThat(lines.length <= 100000, 'EPRU_LIMIT');
  for (const line of lines) {
    const split = line.indexOf('||'); requireThat(split > 0, 'EPRU_DELIMITER');
    const header = parse(line.slice(0,split));
    requireThat(object(header) && (recordTypes.has(header.type)
      || (readOnlyGraphics && ['STRING','ARC','VIA'].includes(header.type))), 'EPRU_UNKNOWN_RECORD');
    const afterDelimiter = line.slice(split+2);
    const trailingPipe = afterDelimiter.endsWith('|');
    const raw = trailingPipe ? afterDelimiter.slice(0,-1) : afterDelimiter;
    const body = raw === '' ? null : parse(raw);
    const emptyGraphic = readOnlyGraphics && raw === '' && header.type === 'LINE' && doc?.head.docType === 'SCH_PAGE';
    requireThat(raw === '' ? ['NET','RULE_SELECTOR'].includes(header.type) || emptyGraphic : object(body), 'EPRU_BODY');
    if (emptyGraphic && sourceProfile === READ_ONLY_GRAPHICS_PROFILE) requireThat(Object.keys(header).length === 4
      && typeof header.id === 'string' && header.id.length > 0
      && Number.isSafeInteger(header.firstTicket) && header.firstTicket > 0
      && Number.isSafeInteger(header.ticket) && header.ticket >= header.firstTicket, 'EPRU_EMPTY_GRAPHIC_HEADER');
    // Editor 3.2.149 exports some empty SCH_PAGE LINE records without firstTicket.
    // Preserve them opaquely. This does not admit empty electrical WIRE records.
    if (emptyGraphic && sourceProfile === READ_ONLY_GRAPHICS_PROFILE_V3) requireThat(
      Object.keys(header).every(key => ['type','id','ticket','firstTicket'].includes(key))
      && typeof header.id === 'string' && header.id.length > 0
      && Number.isSafeInteger(header.ticket) && header.ticket > 0
      && (!Object.hasOwn(header,'firstTicket') || (Number.isSafeInteger(header.firstTicket)
        && header.firstTicket > 0 && header.firstTicket <= header.ticket)), 'EPRU_EMPTY_GRAPHIC_HEADER');
    if (readOnlyGraphics && ['STRING','ARC','VIA'].includes(header.type)) {
      requireThat((header.type === 'STRING' && doc?.head.docType === 'FOOTPRINT')
        || (header.type === 'VIA' && doc?.head.docType === 'FOOTPRINT')
        || (header.type === 'ARC' && doc?.head.docType === 'SYMBOL'), 'EPRU_LIBRARY_GRAPHIC_LOCATION');
      requireThat(typeof header.id === 'string' && header.id.length > 0 && body !== null, 'EPRU_LIBRARY_GRAPHIC_STRUCTURE');
    }
    if (header.type === 'DOCHEAD') {
      requireThat(body && docTypes.has(body.docType) && typeof body.uuid === 'string' && body.uuid.length > 0, 'EPRU_DOCUMENT');
      requireThat(!ids.has(body.uuid), 'EPRU_DUPLICATE_DOCUMENT'); ids.add(body.uuid);
      doc = {head:body, records:[]}; docs.push(doc); keys = new Set();
    }
    requireThat(doc, 'EPRU_DOCUMENT_REQUIRED');
    const key = JSON.stringify([header.type,header.id]);
    requireThat(!keys.has(key), 'EPRU_DUPLICATE_RECORD'); keys.add(key);
    doc.records.push({header,body,trailingPipe});
  }
  return docs;
}

export function readNativeArchive(bytes, {sha256:expectedHash, maxEntries=256, maxUncompressedBytes=64*1024*1024, strictJson=false, sourceProfile}={}) {
  requireThat(Buffer.isBuffer(bytes) && bytes.length <= 32*1024*1024, 'ZIP_LIMIT');
  const digest = sha256(bytes);
  requireThat(/^[0-9a-f]{64}$/.test(expectedHash ?? '') && digest === expectedHash, 'SOURCE_HASH_MISMATCH');
  // Bound entry allocation before letting the ZIP library read its central directory.
  let end = -1;
  for(let i=bytes.length-22;i>=Math.max(0,bytes.length-65557);i--) {
    if(bytes.readUInt32LE(i)===0x06054b50 && i+22+bytes.readUInt16LE(i+20)===bytes.length){end=i;break;}
  }
  requireThat(end>=0, 'ZIP_END');
  const count = bytes.readUInt16LE(end+10);
  requireThat(count>0 && count<=maxEntries && count<65535, 'ZIP_LIMIT');
  requireThat(bytes.readUInt16LE(end+4)===0 && bytes.readUInt16LE(end+6)===0 && bytes.readUInt16LE(end+8)===count, 'ZIP_MULTIDISK');
  requireThat(bytes.readUInt32LE(end+16)+bytes.readUInt32LE(end+12)===end,'ZIP_DIRECTORY');
  const zip = new AdmZip(bytes); const entries = zip.getEntries();
  requireThat(entries.length===count,'ZIP_ENTRY_COUNT');
  const names=new Set(); let total=0;
  for(const entry of entries) {
    const name=entry.entryName;
    requireThat(!name.includes('\\') && !name.includes('\0') && !name.startsWith('/') && !name.includes(':') && name.split('/').every((p,i,a)=>p!=='.'&&p!=='..'&&(p!==''||i===a.length-1)), 'ZIP_PATH');
    requireThat(!names.has(name),'ZIP_DUPLICATE'); names.add(name);
    total+=entry.header.size;
    requireThat(total<=maxUncompressedBytes && entry.header.size<=maxUncompressedBytes,'ZIP_LIMIT');
    requireThat((entry.header.flags & 1)===0 && [0,8].includes(entry.header.method),'ZIP_UNSUPPORTED');
    requireThat(entry.isDirectory || name==='project2.json' || /^[^/]+\.epru$/.test(name) || /^IMAGE\/[a-f0-9]{16}\.webp$/.test(name),'ZIP_UNKNOWN_ENTRY');
  }
  const content = new Map();
  for(const entry of entries) {
    const data = entry.getData(); // adm-zip pins maxOutputLength to declared size on Node >=15.
    requireThat(data.length===entry.header.size,'ZIP_SIZE');
    requireThat(crc32(data)===entry.header.crc,'ZIP_CRC');
    content.set(entry.entryName,data);
  }
  const epruNames=[...content.keys()].filter(n=>n.endsWith('.epru'));
  requireThat(epruNames.length===1 && content.has('project2.json'),'ZIP_PROJECT_SHAPE');
  const decode = data => {
    if(!strictJson)return new TextDecoder('utf-8',{fatal:true}).decode(data);
    try { return new TextDecoder('utf-8',{fatal:true}).decode(data); } catch { throw new Error('UTF8_INVALID'); }
  };
  const project = (strictJson?parseStrictJson:JSON.parse)(decode(content.get('project2.json')));
  requireThat(project.editorVersion==='3.2.149.88089769','UNSUPPORTED_EDITOR_VERSION');
  const images=Object.freeze([...content.entries()].filter(([name])=>name.startsWith('IMAGE/')&&name.endsWith('.webp')).map(([name,data])=>Object.freeze({name,sha256:sha256(data)})).sort((a,b)=>a.name.localeCompare(b.name)));
  return {project,documents:parseEpru(decode(content.get(epruNames[0])),{strictJson,sourceProfile}),sha256:digest,size:bytes.length,epruName:epruNames[0],images};
}

// New captures may use V3; callers replaying stored versions select them explicitly.
export function readCurrentNativeArchive(bytes, options) {
  try { return readNativeArchive(bytes, {...options, sourceProfile: READ_ONLY_GRAPHICS_PROFILE}); }
  catch (error) {
    if (error.message !== 'EPRU_EMPTY_GRAPHIC_HEADER') throw error;
    return readNativeArchive(bytes, {...options, sourceProfile: READ_ONLY_GRAPHICS_PROFILE_V3});
  }
}
