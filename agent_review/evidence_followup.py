"""Close evidence requests before the ordinary pipeline publishes a report."""
from pathlib import Path
from kernel.util import out_dir,read_json
from agent_review.evidence_loop import run_research,business_requests,digest,pending_pairs
from agent_review.rule_modes import filter_enumeration_for_agent


def changed_pairs(previous,current,pairs):
    before={f['id']:f for f in previous['facts']};after={f['id']:f for f in current['facts']}
    semantic=lambda f:digest({k:f.get(k) for k in ('subject','predicate','value','unit','conditions')})
    changed=[f for key,f in after.items() if key not in before or semantic(f)!=semantic(before[key])]
    changed += [f for key,f in before.items() if key not in after]
    impacted=set()
    for fact in changed:
        affected={(p['rule_id'],p['object']) for p in fact.get('affected_pairs',[])}
        impacted |= affected & pairs if affected else pairs
    return impacted


def run(board,kg,enumeration,*,concurrency=2,max_audits=3):
    from agent_review.stages import judge
    from agent_review.repair_cards import repair
    board=Path(board);o=out_dir(board);rounds=[]
    pairs={(rid,obj) for rid,objects in enumeration['by_rule'].items() for obj in objects}
    def pending():
        return pending_pairs(board,[{'rule_id':rid,'object':obj} for rid,obj in pairs])
    for wave in range(max_audits):
        previous=read_json(o/'evidence_store/current.json')
        run_research(board,enumeration,followup=True,force=True)
        current=read_json(o/'evidence_store/current.json');waiting=pending()
        changed=changed_pairs(previous,current,pairs);targets=set(changed)
        for request in current['requests']:
            if request['category']!='business_fact':
                targets|={(p['rule_id'],p['object']) for p in request['affected_pairs']} & waiting
        if not targets:break
        if wave==max_audits-1:
            if changed:raise RuntimeError('New evidence requires another targeted review before publication: '+str(sorted(changed)))
            break
        selected={rid:[obj for obj in objects if (rid,obj) in targets] for rid,objects in enumeration['by_rule'].items()}
        selected={rid:objects for rid,objects in selected.items() if objects}
        scoped=filter_enumeration_for_agent({'rule_ids':list(selected),'by_rule':selected},kg)
        print('EVIDENCE_TARGETED_REVIEW',wave+1,'pairs',len(targets),flush=True)
        result=judge.run(board,kg,scoped,concurrency=concurrency,resume=False,research_prepared=True)
        fixes=repair(board,kg,scoped,concurrency=concurrency) if result['failed'] else []
        rounds.append({'scope':selected,'judge':result,'repairs':fixes})
    requests=business_requests(board,pending())
    return {'rounds':rounds,'business_request_count':len(requests['business_requests']),
            'internal_unresolved_count':len(requests['internal_unresolved'])}
