"""Controller-owned execution signatures and validated stage checkpoints."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
from kernel.util import ROOT, read_json
from agent_review.batching import safe_object_name

POLICY=ROOT/'rules/review_execution.json'

def two_turn(rule_id):
    return rule_id in read_json(POLICY)['two_turn_rules']

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def execution_hash(rule_id,workbench,materials_hash):
    names=['agent_review/delivery.py','agent_review/delivery_worker.mjs','agent_review/prompts/delivery_review.md','agent_review/execution_policy.py','agent_review/orchestrator.mjs',
           'agent_review/local_workspace.mjs','agent_review/codex_agent.mjs',
           'agent_review/validate_job.py','agent_review/analysis_contract.py','agent_review/main_analysis.py','agent_review/schemas/main_analysis.v1.schema.json','agent_review/workbench.py','agent_review/stages/judge.py','rules/object_card_schema.json',
           'rules/review_execution.json','rules/product_center_15.json','rules/xlsx_detail_15.json']
    names += ['agent_review/evidence_loop.py','agent_review/evidence_worker.mjs','agent_review/schemas/evidence.v1.schema.json',
              'agent_review/prompts/evidence_research.md','agent_review/prompts/evidence_followup.md','agent_review/prompts/autonomous_judge.md']
    names += ['agent_review/criteria.py','agent_review/design_pin_labels.py','agent_review/page_evidence.py','agent_review/validation_transport.mjs','agent_review/reuse_inputs.mjs']
    names += ['agent_review/figure_contract.py','agent_review/repair_cards.py','scripts/repair_selection_cards.mjs']
    prompt_dir=ROOT/'agent_review/prompts/judge'
    native=workbench.get('contract_task',{}).get('schema_version')=='review.v1'
    if native:
        names += [f'agent_review/prompts/judge/{rule_id}.analysis.md']
        names += [str(p.relative_to(ROOT)) for p in prompt_dir.glob('review_v1*.md')]
        names += ['agent_review/review_contract.py','agent_review/schemas/review.v1.schema.json','rules/review_contract_rules.json']
    else:
        names += ['agent_review/prompts/_common_judge.md']
        names += [str(p.relative_to(ROOT)) for p in prompt_dir.glob(rule_id+'.*.md')]
        names += [f'agent_review/prompts/judge/{rule_id}.md']
        names += ['agent_review/prompts/judge/_deep_self_check.md']
        if rule_id in ('P11-A01','P11-A03'):
            names += ['agent_review/prompts/judge/_p11_self_check.md']
        if rule_id=='P11-A01':names += ['agent_review/prompts/judge/_power_decision.md','agent_review/prompts/judge/_p11_critique.md']
    h={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names if (ROOT/n).is_file()}
    config=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'config.toml'
    model={'requested':os.environ.get('AGENT_JUDGE_MODEL','auto'),
           'reuse_current_analysis':os.environ.get('AGENT_DELIVERY_REUSE_ANALYSIS')=='1',
           'effort':os.environ.get('SCHEMATIC_CODEX_REASONING_EFFORT'),
           'config_hash':hashlib.sha256(config.read_bytes()).hexdigest() if config.is_file() else None}
    if workbench.get('delivery_policy',{}):
        materials_hash=workbench['delivery_policy']['raw_material_hash']
    return digest({'files':h,'workbench':workbench,'materials':materials_hash,'model':model})

def material_hash(board):
    """Hash actual inputs once per dispatch, including raw sources and extracted facts."""
    board=Path(board);files={}
    for name in ('materials.json','review_out/materials.json','review_out/board_kg.json'):
        p=board/name
        if p.is_file():files[name]=hashlib.sha256(p.read_bytes()).hexdigest()
    config=read_json(board/'materials.json')
    for key,value in config.items():
        if not isinstance(value,str):continue
        p=Path(value)
        if not p.is_absolute():p=board/p
        if p.is_file():files['input:'+key]=hashlib.sha256(p.read_bytes()).hexdigest()
    evidence=board/'review_out/evidence_store/current.json'
    if evidence.is_file():
        files['review_out/evidence_store/current.json']=hashlib.sha256(evidence.read_bytes()).hexdigest()
        for rel,h in read_json(evidence).get('_provenance',{}).get('evidence_hashes',{}).items():
            p=board/rel
            files['research:'+rel]=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else 'missing'
    for folder in ('cache','review_out/cache','review_out/datasheets','review_out/sch','review_out/context_store'):
        for p in sorted((board/folder).rglob('*')):
            if p.is_file() and not set(p.parts)&{'history','quarantine','tombstones'}:
                files[str(p.relative_to(board))]=hashlib.sha256(p.read_bytes()).hexdigest()
    return digest(files)

def checkpoint(board,rid,obj,key):
    root=Path(board)/'history/execution-checkpoints'/safe_object_name(rid)/safe_object_name(obj)/key
    # Node handles long UNC paths, but Python needs the extended Windows form.
    if os.name=='nt':
        raw=os.path.abspath(root)
        if not raw.startswith('\\\\?\\'):
            root=Path('\\\\?\\UNC\\'+raw[2:] if raw.startswith('\\\\') else '\\\\?\\'+raw)
    meta=root/'stage.json'
    try:
        data=read_json(meta)
        rel=Path('review_out/agent_jobs')/rid/(safe_object_name(obj)+'.analysis.md')
        if data['execution_hash']!=key or data['stage'] not in ('analysis','self_check'):return None
        if hashlib.sha256((root/rel).read_bytes()).hexdigest()!=data['analysis_hash']:return None
        return {'root':str(root),'stage':data['stage']}
    except (OSError,ValueError,KeyError):return None
