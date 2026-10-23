"""Audit a scoped delivery against real runs, source identity and published media."""
import argparse,hashlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kernel.util import read_json,write_json,resolve_board_path
from agent_review.delivery import validate as validate_delivery, policy as delivery_policy
from agent_review.validate_job import validate_judge_card
from agent_review.evidence_loop import validate_object_research
from agent_review.review_contract import is_v1

def audit(board,published=False):
    o=board/'review_out';config=read_json(board/'selection_delivery.json');errors=[];records=[]
    for rel,h in config['raw_source_hashes'].items():
        p=board/rel
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=h:errors.append('Raw source changed: '+rel)
    packs={rid:read_json(o/'evidence'/(rid+'.json')) for rid in config['by_rule']} if published else {}
    for rid,objects in config['by_rule'].items():
        for obj in objects:
            key=rid+'::'+obj;p=o/'agent_jobs'/rid/(obj+'.json');local=[]
            for rel in config.get('independent_documents',{}).get(key,[]):
                if not (board/rel).is_file():local.append('Declared independent evidence file is missing: '+rel)
            if not p.is_file():local.append('No model card')
            else:
                c=read_json(p);wb=read_json(p.with_suffix('.context.json'))
                for suffix in ['.failed.json','.invalid.json']:
                    if p.with_suffix(suffix).exists():local.append('Unresolved execution marker: '+suffix)
                local+=validate_judge_card(c,rule_id=rid,obj=obj,board=board)+validate_delivery(c,wb)+validate_object_research(board,rid,obj,c)
                if wb.get('delivery_policy')!=delivery_policy(board,rid,obj):local.append('Independent evidence policy changed; targeted reconciliation required')
                research=p.with_suffix('.research.json')
                if research.is_file():
                    for fact in read_json(research).get('facts',[]):
                        for ev in fact.get('evidence',[]):
                            path=str(ev.get('path','')).replace('\\','/')
                            if '/independent_reviews/' in path and path.endswith('.md'):
                                local.append('Derived independent review used as primary fact evidence: '+path)
                progress=o/'delivery_progress'/rid/(obj+'.json');status=read_json(progress) if progress.is_file() else {}
                if status.get('run_key')!=config['run_key'] or status.get('status')!='validated':local.append('No current validated run')
                execution=(c.get('_meta') or {}).get('execution') if is_v1(c) else c.get('_execution')
                if not execution or not execution.get('run_id'):local.append('No model run provenance')
                elif execution.get('run_id')!=status.get('run_id'):local.append('Progress receipt does not match the card model run')
                if published:
                    shown=next((x for x in packs[rid].get('object_cards',[]) if x.get('object')==obj),None)
                    if not shown:local.append('Object absent from published report')
                    else:
                        detail=shown.get('review_detail') or {};primary=detail.get('primary')
                        if is_v1(c):
                            source_diagram=bool((c.get('extreme') or {}).get('primary_scenario_ref'))
                        else:
                            source_diagram=bool((c.get('main_analysis') or {}).get('figure') or any(s.get('figure') for s in c.get('states') or []))
                        if c.get('verdict_zh')!='不适用' and source_diagram and not primary:
                            local.append('Source operating analysis was hidden from the published detail')
                        if primary and primary.get('route_expected'):
                            if not primary.get('figure'):local.append('Operating analysis has no published diagram')
                            elif not (o/primary['figure']).is_file():local.append('Published diagram file missing')
                            for page in primary.get('figure_pages') or []:
                                if page.get('src') and not (o/page['src']).is_file():local.append('Published diagram page file missing')
                        ds=shown.get('datasheet') or {}
                        if ds.get('local_path'):
                            pdf=next((p for p in [o/ds['local_path'],board/ds['local_path']] if p.is_file()),None)
                            if pdf is None:local.append('Datasheet link not local/readable')
                            else:
                                import fitz
                                try:
                                    with fitz.open(pdf) as doc:
                                        if not len(doc):local.append('Datasheet link resolves to a zero-page PDF')
                                except Exception as error:local.append('Datasheet PDF cannot be opened: '+str(error))
                verdict=c.get('verdict_zh')
                if is_v1(c):
                    from agent_review.review_contract import ZH
                    verdict=ZH.get(c['decision']['verdict'])
                if published and shown and shown.get('verdict_zh')!=verdict:
                    local.append('Published verdict differs from the current model card')
                records.append({'rule_id':rid,'object':obj,'verdict':verdict,'errors':local})
            errors.extend(key+': '+str(e) for e in local)
    result={'run_key':config['run_key'],'published':published,'selected_pairs':sum(map(len,config['by_rule'].values())),'records':records,'errors':errors}
    write_json(o/('delivery_published_audit.json' if published else 'delivery_source_audit.json'),result)
    print('DELIVERY_AUDIT',len(records),'records',len(errors),'errors',flush=True)
    for e in errors[:20]:print(e)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--board',required=True);p.add_argument('--published',action='store_true');a=p.parse_args()
    sys.exit(bool(audit(resolve_board_path(a.board),a.published)['errors']))
