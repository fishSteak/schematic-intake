"""Source-bound autonomous research. No circuit knowledge or verdict rules here."""
from __future__ import annotations
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import zipfile
import xml.etree.ElementTree as ET
from jsonschema import Draft202012Validator
from kernel.util import ROOT,read_json,write_json,out_dir
from kernel.runtime import node_executable

SCHEMA=ROOT/'agent_review/schemas/evidence.v1.schema.json'
PROMPT=ROOT/'agent_review/prompts/evidence_research.md'
FOLLOWUP=ROOT/'agent_review/prompts/evidence_followup.md'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def digest(data):return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def policy_hash():
    return digest({str(p.relative_to(ROOT)):sha(p) for p in (Path(__file__),SCHEMA,PROMPT,FOLLOWUP,ROOT/'agent_review/evidence_worker.mjs',ROOT/'agent_review/prompts/autonomous_judge.md')})

def source_revision(board):
    board=Path(board);files={}
    for rel in ('materials.json','review_out/board_kg.json'):
        p=board/rel
        if p.is_file():files[rel]=sha(p)
    for key,v in read_json(board/'materials.json').items():
        if isinstance(v,str):
            p=Path(v) if Path(v).is_absolute() else board/v
            if p.is_file():files['input:'+key]=sha(p)
    for folder in ('cache','review_out/datasheets'):
        for p in sorted((board/folder).rglob('*.pdf')):files[str(p.relative_to(board))]=sha(p)
    return digest(files)

def scoped_pairs(enumeration):
    return [{'rule_id':rid,'object':obj} for rid,objects in enumeration['by_rule'].items() for obj in objects]

def pending_pairs(board,pairs):
    result=set()
    for p in pairs:
        f=out_dir(board)/'agent_jobs'/p['rule_id']/(p['object']+'.json')
        if f.exists() and read_json(f).get('verdict_zh')=='待确认':result.add((p['rule_id'],p['object']))
    return result

def validate_followup_coverage(data,board,pairs,previous=None):
    covered={(p['rule_id'],p['object']) for r in data['requests'] for p in r['affected_pairs']}
    if previous is not None:
        before={f['id']:f for f in previous['facts']};after={f['id']:f for f in data['facts']}
        semantic=lambda f:digest({k:f.get(k) for k in ('subject','predicate','value','unit','conditions')})
        changed=[f for k,f in after.items() if k not in before or semantic(f)!=semantic(before[k])]
        changed += [f for k,f in before.items() if k not in after]
        covered|={(p['rule_id'],p['object']) for f in changed for p in f['affected_pairs']}
    missing=pending_pairs(board,pairs)-covered
    return [f'Pending pair omitted from audit: {rid}/{obj}; record a categorized request, including reasoning when existing evidence is sufficient but the card still needs reconsideration' for rid,obj in sorted(missing)]

def _inside(path,root):
    try:path.resolve().relative_to(root.resolve());return True
    except ValueError:return False

def _text(path,page=None):
    suffix=path.suffix.lower()
    if suffix=='.pdf':
        import pymupdf
        with pymupdf.open(path) as doc:
            if page is not None:
                if not 1<=page<=len(doc):raise ValueError('PDF page out of range')
                return doc[page-1].get_text()
            return '\n'.join(p.get_text() for p in doc)
    if suffix=='.docx':
        with zipfile.ZipFile(path) as z:
            root=ET.fromstring(z.read('word/document.xml'))
            return ' '.join(x.text or '' for x in root.iter() if x.tag.endswith('}t'))
    raw=path.read_bytes()
    for encoding in ('utf-8-sig','utf-16','gb18030'):
        try:return raw.decode(encoding)
        except (UnicodeError,LookupError):pass
    return None

def validate_research(data,board,*,verify_quotes=True):
    errors=[f"{'.'.join(map(str,e.absolute_path))}: {e.message}" for e in Draft202012Validator(read_json(SCHEMA)).iter_errors(data)]
    if errors:return errors
    ids=[f['id'] for f in data['facts']]
    if len(ids)!=len(set(ids)):errors.append('fact IDs must be unique')
    for conflict in data['conflicts']:
        if any(x not in ids for x in conflict['fact_refs']):errors.append('conflict refers to unknown fact')
        if conflict['status']=='resolved' and (not conflict['fact_refs'] or not conflict['resolution'].strip()):errors.append('resolved conflict requires source-bound facts and reasoning')
    normalize=lambda v:re.sub(r'\s+','',v)
    texts={}
    for fact in data['facts']:
        for ev in fact['evidence']:
            rel=Path(ev['path']);p=Path(board)/rel
            if rel.is_absolute() or not _inside(p,Path(board)) or not p.is_file():
                errors.append(f"{fact['id']}: evidence file missing or outside board: {ev['path']}");continue
            if p.name=='current.json' and 'evidence_store' in p.parts or p.name.endswith(('.analysis.md','.research.json')):
                errors.append(f"{fact['id']}: derived claims cannot be their own primary evidence");continue
            if not verify_quotes:continue
            try:
                key=(str(p),ev.get('pdf_page'))
                if key not in texts:texts[key]=_text(p,ev.get('pdf_page'))
                text=texts[key]
                if text is not None and normalize(ev['quote']) not in normalize(text):
                    errors.append(f"{fact['id']}: quote is not verbatim at cited source/page: {ev['path']}")
            except Exception as e:errors.append(f"{fact['id']}: unreadable evidence: {e}")
    return errors

def seal(board,data,run,revision,pairs):
    if run.get('status')!='completed':raise RuntimeError('No successful research run')
    errors=validate_research(data,board)
    if errors:raise RuntimeError('Evidence failed validation: '+str(errors[:12]))
    hashes={ev['path']:sha(Path(board)/ev['path']) for f in data['facts'] for ev in f['evidence']}
    sealed={**data,'_provenance':{'run_id':run['id'],'model':os.environ.get('AGENT_JUDGE_MODEL','auto'),
        'source_revision':revision,'policy_hash':policy_hash(),'pairs':pairs,'evidence_hashes':hashes,
        'validated_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'validation':'schema, file identity, literal source quote; factual interpretation remains model-reviewed'}}
    version='review_out/evidence_store/versions/'+digest(sealed)+'.json'
    sealed['_provenance']['version_path']=version
    write_json(Path(board)/version,sealed)
    p=out_dir(board)/'evidence_store/current.json'
    if p.exists():
        archive=Path(board)/'history/evidence-versions'/f'{uuid.uuid4().hex}.json';write_json(archive,read_json(p))
    write_json(p,sealed)
    return sealed

def run_research(board,enumeration,*,followup=False,force=False):
    board=Path(board);o=out_dir(board);pairs=scoped_pairs(enumeration);revision=source_revision(board)
    current=o/'evidence_store/current.json'
    if not force and not followup and current.exists():
        old=read_json(current);meta=old.get('_provenance',{})
        have={(p['rule_id'],p['object']) for p in meta.get('pairs',[])}
        want={(p['rule_id'],p['object']) for p in pairs}
        if meta.get('source_revision')==revision and meta.get('policy_hash')==policy_hash() and want<=have:
            if not validate_research(old,board) and all((board/rel).is_file() and sha(board/rel)==h for rel,h in meta.get('evidence_hashes',{}).items()):
                return {'reused':True,'facts':len(old['facts']),'path':str(current)}
    key=uuid.uuid4().hex;folder=o/'evidence_proposals'/key;folder.mkdir(parents=True)
    output=folder/'research.json'
    from agent_review.enumerate import rule_meta_map
    meta=rule_meta_map()
    task={'schema_version':'evidence_task.v1','mode':'followup' if followup else 'initial','pairs':pairs,
          'criteria':{rid:meta.get(rid,{}) for rid in enumeration['rule_ids']},
          'assets_root':str((folder/'assets').relative_to(board)).replace('\\','/'),
          'output':str(output.relative_to(board)).replace('\\','/'),'source_revision':revision}
    inputs=[]
    previous=read_json(current) if followup and current.exists() else None
    if followup:
        for p in pairs:
            for suffix in ('.json','.analysis.md','.research.json'):
                f=o/'agent_jobs'/p['rule_id']/(p['object']+suffix)
                if f.exists():inputs.append(str(f))
        task['current_review_artifacts']=[str(Path(p).relative_to(board)).replace('\\','/') for p in inputs]
        task['current_pending_pairs']=[{'rule_id':rid,'object':obj} for rid,obj in sorted(pending_pairs(board,pairs))]
    write_json(output.with_suffix('.context.json'),task);write_json(output.with_suffix('.schema.json'),read_json(SCHEMA))
    job=folder/'job.json';write_json(job,{'board':str(board),'output':str(output),'inputs':inputs,'followup':followup})
    env=os.environ.copy();env.setdefault('SCHEMATIC_PYTHON',sys.executable)
    for attempt in range(3):
        subprocess.run([node_executable(),str(ROOT/'agent_review/evidence_worker.mjs'),str(job)],cwd=ROOT,env=env,check=False)
        result_path=folder/'run.json'
        run=read_json(result_path) if result_path.exists() else {'status':'error','error':'No research run delivered'}
        if run.get('status')!='completed':
            print('RESEARCH_RETRY',attempt+1,run.get('error'),flush=True);continue
        try:
            data=read_json(output);errors=validate_research(data,board)
            if followup and not errors:errors+=validate_followup_coverage(data,board,pairs,previous)
        except Exception as e:errors=[str(e)]
        if not errors:
            if source_revision(board)!=revision:raise RuntimeError('Raw materials changed during research')
            accepted=seal(board,data,run,revision,pairs)
            print('RESEARCH_ACCEPTED',len(data['facts']),'facts',len(data['requests']),'requests',flush=True)
            return {'reused':False,'facts':len(accepted['facts']),'path':str(current),'run_id':run['id']}
        write_json(folder/'validation.json',{'errors':errors});print('RESEARCH_REPAIR',errors[:5],flush=True)
    raise RuntimeError('Autonomous research did not deliver source-valid evidence; prior report retained')

def validate_object_research(board,rid,obj,card):
    p=out_dir(board)/'agent_jobs'/rid/(obj+'.research.json')
    if not p.exists():return ['autonomous research receipt missing']
    try:
        data=read_json(p);errors=validate_research(data,board)
    except Exception as e:return [str(e)]
    if card.get('verdict_zh')=='待确认' and not data.get('requests'):
        errors.append('pending verdict requires categorized unresolved requests and actual investigation attempts')
    if card.get('verdict_zh')=='待确认' and not any(any(p['rule_id']==rid and p['object']==obj for p in r['affected_pairs']) for r in data.get('requests',[])):
        errors.append('unresolved requests must identify this rule/object pair')
    return errors

def business_requests(board,pending_pairs=None):
    current=read_json(out_dir(board)/'evidence_store/current.json')
    if pending_pairs is not None:
        covered={(p['rule_id'],p['object']) for r in current['requests'] for p in r['affected_pairs']}
        if pending_pairs-covered:raise RuntimeError('Final audit omitted pending pairs: '+str(sorted(pending_pairs-covered)))
    relevant=lambda r:pending_pairs is None or any((p['rule_id'],p['object']) in pending_pairs for p in r['affected_pairs'])
    data={'business_requests':[r for r in current['requests'] if r['category']=='business_fact' and relevant(r)],
          'internal_unresolved':[r for r in current['requests'] if r['category']!='business_fact' and relevant(r)],
          'source_run':current['_provenance']['run_id']}
    write_json(out_dir(board)/'business_material_requests.json',data)
    return data
