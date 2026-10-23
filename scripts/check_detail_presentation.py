"""Read-only full-report presentation validation; does not run electrical review."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from jsonschema import Draft202012Validator
from kernel.util import ROOT, read_json, resolve_board_path
from kernel.runtime import node_executable
from agent_review.detail_view import build, digest, native
from agent_review.detail_prose import load


def check(board,published=False):
    out=board/'review_out';verdicts=read_json(out/'verdicts.json');before=digest(verdicts)
    schema=Draft202012Validator(read_json(ROOT/'agent_review/schemas/review_detail.v2.schema.json'))
    views=[];statuses={};payload=read_json(out/'report_data.json') if published else {}
    for result in verdicts['results']:
        rid=result['rule_id'];pack=read_json(out/'evidence'/(rid+'.json'))
        byobj={c['object']:c for c in pack.get('object_cards',[])}
        for source in result.get('object_cards',[]):
            source=dict(source,rule_id=rid);card=byobj[source['object']]
            view=card['review_detail'] if published else build(source,out,presented=card)
            schema.validate(view)
            from agent_review.analysis_contract import assumptions_for, validate_links, required_assumptions
            assert view['key_assumptions']==assumptions_for(source)
            assert not validate_links(view['analysis_steps'],view['key_assumptions'],required_assumptions(source) if view['analysis_steps'] and view['analysis_origin']=='merged_model' else ())
            assert 'premises' not in view and 'reasoning_steps' not in view
            if view['analysis_origin']=='native_model':
                n=native(source) or source
                expected=[{'text':s['text'],'assumption_refs':s.get('premise_refs',s.get('assumption_refs',[]))} for s in n['analysis_steps']]
                assert view['analysis_steps']==expected
            else:
                prose=load(out,source)
                if prose is None:raise RuntimeError('Missing validated prose: '+rid+'/'+source['object'])
                expected=prose.get('analysis_steps',[{'text':s,'assumption_refs':[]} for s in prose.get('reasoning_steps',[])])
                if view['analysis_steps']!=expected:raise RuntimeError('Displayed analysis differs from model source')
                if not native(source) and prose.get('primary_ref') and view.get('primary') and view['primary']['id']==prose['primary_ref']:
                    assert view['primary']['route_summary']==prose['route_summary']
            if view['verdict']=='not_applicable':assert not view['primary'] and not view['comparisons'] and not view['assessment_rows']
            if published:
                assert card['verdict_zh']==source['verdict_zh']
                assert payload['review_details'][rid][source['object']]==view
            views.append(view);statuses[view['prose_status']]=statuses.get(view['prose_status'],0)+1
    source=(ROOT/'vendor/expert_html/build_expert_html.py').read_text(encoding='utf-8')
    functions=source[source.index('function renderContractPremises(v){{'):source.index('function renderPremises(d){{')].replace('{{','{').replace('}}','}')
    script="const hEsc=s=>String(s??'').replaceAll('<','&lt;');const stressViewHtml=s=>'<img data-route-cover=1>';\n"+functions
    script+='\nconst views='+json.dumps(views,ensure_ascii=False)+r''';
for(const v of views){
 const body=renderContractPremises(v)+renderContractReview(v);
 if((body.match(/<img/g)||[]).length>1)throw Error('Multiple main figures');
 if(/<ol class="rv-calcs"><\/ol>/.test(body))throw Error('Empty calculations');
 if(/<h[34][^>]*>(工况通路图|公式代入|全局极值|整改与补证)<\//.test(body))throw Error('Removed heading returned');
 if(body.includes('[object Object]'))throw Error('Unformatted structured data');
}
console.log('ALL_DETAIL_RENDERED '+views.length);
'''
    with tempfile.TemporaryDirectory(prefix='schematic-detail-check-') as tmp:
        js=Path(tmp)/'check.mjs';js.write_text(script,encoding='utf-8')
        subprocess.run([node_executable(),str(js)],check=True)
    assert digest(verdicts)==before
    print(json.dumps({'records':len(views),'prose_statuses':statuses,'published_data_parity':published},ensure_ascii=False))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--board',required=True);ap.add_argument('--published',action='store_true')
    args=ap.parse_args();check(resolve_board_path(args.board),args.published)
