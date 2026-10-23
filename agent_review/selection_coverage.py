"""Published batch coverage is execution metadata, never an electronic verdict."""
from collections import Counter
import datetime,html
from pathlib import Path
from kernel.util import read_json,write_json

def refresh(out):
    out=Path(out);p=out/'delivery_coverage.json'
    if not p.is_file():return None
    data=read_json(p);totals=Counter();requests=[]
    from agent_review.enumerate import rule_meta_map
    titles=rule_meta_map()
    for row in data['rules']:
        row['title']=(titles.get(row['rule_id']) or {}).get('name') or row['rule_id']
        counts=Counter();statuses=[]
        for obj in row['selected_objects']:
            f=out/'delivery_progress'/row['rule_id']/(obj+'.json')
            progress=read_json(f) if f.is_file() else {}
            valid=progress.get('run_key')==data['run_key'] and progress.get('status')=='validated'
            status='validated' if valid else 'execution_pending'
            counts[status]+=1
            if valid:counts[progress['verdict']]+=1
            if valid and progress.get('verdict') in {'待确认','INSUFFICIENT'}:
                research=out/'agent_jobs'/row['rule_id']/(obj+'.research.json')
                if research.is_file():
                    for request in read_json(research).get('requests',[]):
                        requests.append({'rule_id':row['rule_id'],'object':obj,'run_id':progress.get('run_id'),'request':request})
            statuses.append({'object':obj,'status':status,'verdict':progress.get('verdict') if valid else None,'run_id':progress.get('run_id'),
                             'execution_errors':progress.get('errors',[]) if not valid else []})
        row.update(counts=dict(counts),objects=statuses);totals.update(counts)
    data.update(counts=dict(totals),unresolved_requests=requests,updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    write_json(out/'delivery_material_requests.json',{'run_key':data['run_key'],'business_requests':[r for r in requests if r['request']['category']=='business_fact'],'internal_unresolved':[r for r in requests if r['request']['category']!='business_fact']})
    write_json(p,data);return data

def render(out,rid=None):
    p=Path(out)/'delivery_coverage.json'
    if not p.is_file():return ''
    d=read_json(p);esc=lambda v:html.escape(str(v))
    if rid:
        row=next((r for r in d['rules'] if r['rule_id']==rid),None)
        if not row:return '<p class="hint">既有批次结果 · 本项未在本轮选型评审中重审。</p>'
        if row['no_object']:return '<p class="hint">本轮已核查器件分类，未识别到适用对象；不代表存在器件且已通过。</p>'
        return f'<p class="hint">本轮代表性评审：候选 {row["candidate_count"]}，选中 {row["selected_count"]}，已校验 {row.get("counts",{}).get("validated",0)}。未选对象不计入本轮已审范围。</p>'
    rows=''
    for r in d['rules']:
        counts=r.get('counts',{})
        distribution=' / '.join(f'{name} {counts.get(key,0)}' for key,name in [('是','通过'),('否','不通过'),('待确认','待确认'),('不适用','不适用')]) if not r['no_object'] else '已核查无适用对象'
        remaining='；'.join(x['object']+('：执行未完成' if x['status']!='validated' else '：待确认') for x in r.get('objects',[]) if x['status']!='validated' or x.get('verdict') in {'待确认','INSUFFICIENT'}) or '—'
        rows+=f'<tr><td><a href="#item/{esc(r["rule_id"])}">{esc(r["rule_id"])}</a><br>{esc(r.get("title", ""))}</td><td>{r["candidate_count"]}</td><td>{r["selected_count"]}</td><td>{counts.get("validated",0)}</td><td>{esc(distribution)}</td><td>{esc("无适用对象" if r["no_object"] else "、".join(r["selected_objects"]))}</td><td>{esc(remaining)}</td></tr>'
    c=d.get('counts',{})
    return f'''<section class="panel" style="padding:16px 20px;margin:12px 0"><h3 style="margin:0 0 8px">器件选型 · 本轮代表性完整评审</h3><p class="hint">覆盖 {len(d['rules'])} 项；选中 {d['selected_pairs']} 组，已校验 {c.get('validated',0)} 组，执行未完成 {c.get('execution_pending',0)} 组。通过 {c.get('是',0)}，不通过 {c.get('否',0)}，电子待确认 {c.get('待确认',0)}，对象不适用 {c.get('不适用',0)}。</p><p class="hint">本轮为代表性抽样，不代表全板全器件已审完；其他检查项保留既有批次结果。执行未完成与电子待确认分别统计。</p><details style="margin-top:10px"><summary>查看16项覆盖、结论与对象清单</summary><div style="overflow-x:auto"><table><thead><tr><th>检查项</th><th>候选</th><th>选中</th><th>已校验</th><th>结论分布</th><th>对象 / 适用状态</th><th>剩余对象（原因见详情）</th></tr></thead><tbody>{rows}</tbody></table></div></details></section>'''
