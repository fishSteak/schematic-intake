"""Execute reviews against a frozen material workspace and publish only after gates."""
from __future__ import annotations
import hashlib
import json
import os
import shutil
from pathlib import Path
from kernel.util import ROOT, read_json, write_json, out_dir
from agent_review.llm_tasks import configure, check_cancelled

def profile_signature(path):
    """Compare execution settings without requiring or exposing credentials."""
    from dataclasses import fields
    from agent_review.llm import Profile, LLMError
    if not path:
        raise ValueError('原任务没有冻结模型配置，请创建新任务')
    try:
        data=read_json(Path(path))
        known={field.name for field in fields(Profile)}
        values={key:value for key,value in data.items() if key in known}
        values.update(api_key='',api_key_env='')
        return Profile(**values).signature()
    except (OSError, TypeError, ValueError, LLMError) as exc:
        raise ValueError('模型配置快照无效，请创建新任务') from exc

def validate_snapshot(board):
    """Reject reused material snapshots whose original bytes were changed."""
    from .materials import REQUIRED
    marker=board/'material_snapshot.json'
    if not marker.is_file():
        raise ValueError('缺少原材料快照校验信息，请创建新任务')
    snapshot=read_json(marker);config=read_json(board/'materials.json')
    hashes=snapshot.get('sha256') or {}
    if any(key not in hashes for key in REQUIRED):
        raise ValueError('原材料快照不完整，请创建新任务')
    for key,digest in hashes.items():
        if not isinstance(config.get(key),str):
            raise ValueError('原材料快照配置已变化，请创建新任务')
        path=(board/config[key]).resolve()
        if not path.is_relative_to(board) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('原材料快照已变化，请创建新任务')
    if snapshot.get('config_sha256') and hashlib.sha256((board/'materials.json').read_bytes()).hexdigest()!=snapshot['config_sha256']:
        raise ValueError('原材料快照配置已变化，请创建新任务')
    for relative,digest in (snapshot.get('source_files') or {}).items():
        path=(board/relative).resolve()
        if not path.is_relative_to(board) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('缓存材料或评审设置快照已变化，请创建新任务')

def snapshot_board(source, workspace):
    source=Path(source).resolve();board=Path(workspace)/'board'
    board.mkdir(parents=True,exist_ok=True)
    config=read_json(source/'materials.json')
    config.setdefault('rules',str(ROOT/'rules/product_center_15.json'))
    from .materials import EXTENSIONS
    hashes={};source_files={}
    for key in (*EXTENSIONS,'rules'):
        if not isinstance(config.get(key),str):continue
        path=(source/config[key]).resolve()
        if not path.is_file():continue
        target=board/'material_inputs'/key/path.name
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
        config[key]=str(target.relative_to(board));hashes[key]=hashlib.sha256(target.read_bytes()).hexdigest()
    write_json(board/'materials.json',config)
    # Source PDFs are reusable inputs; regenerate all derived board facts.
    for root in ('cache','review_out/datasheets'):
        for path in (source/root).rglob('*.pdf'):
            target=board/path.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
            source_files[str(target.relative_to(board))]=hashlib.sha256(target.read_bytes()).hexdigest()
    for filename in ('main_analysis.json','selection_delivery.json','review_config.json','detail_presentation.json'):
        if (source/filename).is_file():
            shutil.copy2(source/filename,board/filename)
            source_files[filename]=hashlib.sha256((board/filename).read_bytes()).hexdigest()
    write_json(board/'material_snapshot.json',{'source':str(source),'sha256':hashes,
        'config_sha256':hashlib.sha256((board/'materials.json').read_bytes()).hexdigest(),'source_files':source_files})
    return board

def prepare_local(board):
    from kernel.steps import s0_materials,s1_graph,s2_datasheet,s3_extract,s4_pdf_index,s5_board_kg
    materials=s0_materials.run(board);check_cancelled()
    graph=s1_graph.run(board,materials);check_cancelled()
    queue,_=s2_datasheet._queue_from_graph(board,graph)
    hit=[q['mpn'] for q in queue if (Path(q['cache'])/'datasheet.pdf').is_file()]
    miss=[q['mpn'] for q in queue if q['mpn'] not in hit]
    ds={'queue':queue,'hit':hit,'miss':miss,'note':'Local inputs only; missing datasheets remain unknown'}
    write_json(out_dir(board)/'datasheet_miss.json',ds)
    extract=s3_extract.run(board,graph,ds);check_cancelled()
    sch=s4_pdf_index.run(board,materials,graph);check_cancelled()
    kg=s5_board_kg.run(board,materials,graph,ds,extract,sch,allow_agent=False)
    return {'materials':materials,'kg':kg,'graph':graph,'sch':sch}

def scope_objects(enumeration, objects):
    if objects is None:return enumeration
    requested=set(objects);available={obj for rows in enumeration['by_rule'].values() for obj in rows}
    unknown=requested-available
    if unknown:raise ValueError('所选对象不在检查范围内：'+', '.join(sorted(unknown)))
    data={**enumeration,'by_rule':{rid:[o for o in rows if o in requested] for rid,rows in enumeration['by_rule'].items()}}
    data['stats']={**data.get('stats',{}),'judge_pairs':sum(map(len,data['by_rule'].values()))}
    return data


def case_preparation_signature(board, kg, enumeration):
    """Bind auxiliary model work to frozen sources, settings and implementation."""
    from agent_review.evidence_loop import source_revision
    from agent_review.execution_policy import digest
    from agent_review.llm import profile_fingerprint
    board=Path(board)
    names={'web_workbench/execution.py','agent_review/evidence_loop.py',
           'agent_review/execution_policy.py','agent_review/p11_pipeline.py',
           'agent_review/program_bridge.py','agent_review/batching.py',
           'agent_review/llm_auxiliary.py','agent_review/llm_tasks.py','agent_review/llm_tools.py',
           'agent_review/stages/cases_stage.py','agent_review/stages/calc_stage.py',
           'agent_review/prompts/path_draft.md'}
    for directory in ('agent_review/board_paths','agent_review/llm','kernel','solve_prog'):
        names.update(str(path.relative_to(ROOT)) for path in (ROOT/directory).rglob('*.py'))
    names.update(str(path.relative_to(ROOT)) for path in (ROOT/'rules').glob('*.json'))
    implementation={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sorted(names)}
    inputs={}
    for name in ('review_out/materials.json','review_out/design_graph.json','review_out/sch_index.json',
                 'review_out/operating_conditions.json','review_out/design_task.json','main_analysis.json',
                 'selection_delivery.json','review_config.json','detail_presentation.json'):
        path=board/name
        inputs[name]=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return digest({'source_revision':source_revision(board),'kg':kg,'inputs':inputs,
                   'scope':enumeration.get('by_rule',{}),'implementation':implementation,
                   'model':profile_fingerprint(task='path_draft'),
                   'skip_agent':os.environ.get('BOARD_PATHS_SKIP_AGENT','').strip()})


def case_preparation_outputs(board):
    """Validate complete stage outputs and return their exact byte identities."""
    from agent_review.batching import safe_object_name
    board=Path(board);o=out_dir(board)
    required=['board_paths/index.json','board_paths/path_draft_jobs.json',
              'board_paths/projection_stats.json','board_paths/assist.json',
              'operating_cases/_bundle.json','program_stress/_bundle.json',
              'p11_cases_result.json','p11_stress_result.json']
    if any(not (o/name).is_file() for name in required):
        raise ValueError('工况或计算输出尚未完整生成')
    # The bundles and object files must describe the same completed calculation.
    for folder in ('operating_cases','program_stress'):
        bundle=read_json(o/folder/'_bundle.json')
        if not isinstance(bundle,dict):raise ValueError('工况或计算汇总格式无效')
        for rid,objects in bundle.items():
            if not isinstance(objects,dict):raise ValueError('工况或计算对象格式无效')
            for ref,document in objects.items():
                path=o/folder/safe_object_name(rid)/(safe_object_name(ref)+'.json')
                if not isinstance(document,dict):raise ValueError('工况或计算对象格式无效')
                expected={key:value for key,value in document.items() if not str(key).startswith('_')} if folder=='operating_cases' else document
                if read_json(path)!=expected:
                    raise ValueError('工况或计算对象文件与汇总不一致')
    jobs=read_json(o/'board_paths/path_draft_jobs.json').get('jobs') or []
    if jobs and os.environ.get('BOARD_PATHS_SKIP_AGENT','').strip() not in {'1','true','yes'}:
        results=read_json(o/'board_paths/sdk_path_draft_result.json').get('results') or []
        by_id={row.get('job_id'):row for row in results}
        for job in jobs:
            row=by_id.get(job.get('job_id')) or {}
            if not row.get('ok') or not row.get('wrote'):
                raise ValueError('模型工况任务尚未成功完成')
            for key,record in (('out_path',job),('metadata_path',row)):
                path=(board/record[key]).resolve()
                if not path.is_relative_to(o.resolve()) or not path.is_file():
                    raise ValueError('模型工况任务缺少输出或来源记录')
                if key=='metadata_path' and read_json(path).get('status')!='completed':
                    raise ValueError('模型工况任务来源记录未完成')
    paths={o/name for name in required}
    for folder in ('board_paths','operating_cases','program_stress','llm_runs/path_draft','research_assets/llm'):
        paths.update(path for path in (o/folder).rglob('*') if path.is_file())
    hashes={}
    for path in sorted(paths):
        if path.is_symlink() or not path.resolve().is_relative_to(o.resolve()):
            raise ValueError('工况输出路径无效')
        raw=path.read_bytes()
        if path.suffix=='.json':json.loads(raw)
        elif path.suffix=='.jsonl':
            for line in raw.splitlines():
                if line.strip():json.loads(line)
        hashes[str(path.relative_to(board))]=hashlib.sha256(raw).hexdigest()
    return hashes


def prepare_cases(board, kg, enumeration, *, resume, emit):
    """Reuse only fully completed, unchanged cases/calc segments on a resume."""
    from agent_review.stages import cases_stage,calc_stage
    from agent_review.llm_tasks import atomic_json
    marker=Path(board)/'history/preparation-checkpoints/cases-calc.json'
    signature=case_preparation_signature(board,kg,enumeration)
    if resume:
        try:
            saved=read_json(marker)
            if saved.get('signature')==signature and saved.get('outputs')==case_preparation_outputs(board):
                check_cancelled()
                emit('preparation_reused',{'stage':'cases_calc','signature':signature,
                    'detail':'工况分析与程序计算的输入、策略和全部产物校验通过'})
                return
        except (OSError,ValueError,KeyError,TypeError):pass
    marker.unlink(missing_ok=True)
    cases_stage.run(board,kg,skip_agent=False);check_cancelled()
    calc_stage.run(board,kg);check_cancelled()
    if case_preparation_signature(board,kg,enumeration)!=signature:
        raise ValueError('工况分析期间材料或执行策略发生变化，请重新运行')
    try:outputs=case_preparation_outputs(board)
    except (OSError,ValueError,KeyError,TypeError):
        # Existing fallback policy can still deliver a visibly incomplete case;
        # it must never become a reusable successful model-preparation segment.
        return
    atomic_json(marker,{'signature':signature,'outputs':outputs})


def execute_run(run, board_dir, workspace, emit, cancelled):
    from agent_review.stages import enumerate_stage,program_stage,context_build,judge,aggregate_stage,gate,cases_stage,calc_stage
    from agent_review.rule_modes import filter_enumeration_for_agent,split_rule_ids
    from .materials import inventory
    configure(emit=emit,cancelled=cancelled)
    workspace=Path(workspace).resolve()
    steps=['材料快照','材料解析','检查范围','程序检查','模型评审','报告生成','交付校验']
    def stage(index, detail=None):
        check_cancelled();emit('stage',{'stage':steps[index],'completed':index,'total':len(steps),'detail':detail})
    stage(0)
    current=inventory({'path':str(board_dir)})
    if current['version']!=run['material_version']:raise ValueError('材料已变化，请重新创建任务')
    board=None
    if run.get('resume') and run.get('parent_id'):
        from .store import Store
        parent=Store().run(run['parent_id'])
        if parent.get('board_id')!=run.get('board_id') or parent.get('status') not in {'stopped','interrupted','failed'}:
            raise ValueError('该任务状态不能续跑')
        if parent.get('material_version')!=run['material_version'] or parent.get('mode')!=run['mode'] or set(parent.get('rule_ids') or [])!=set(run['rule_ids']) or (
            None if parent.get('objects') is None else set(parent['objects'])
        )!=(None if run.get('objects') is None else set(run['objects'])):
            raise ValueError('续跑的材料或检查范围与原任务不一致')
        if parent.get('profile_id')!=run.get('profile_id') or (run['mode']=='review' and profile_signature(parent.get('profile_path'))!=profile_signature(run.get('profile_path'))):
            raise ValueError('续跑的模型策略与原任务不一致')
        marker=Path(parent['workspace'])/'execution.json'
        if marker.is_file() and parent.get('material_version')==run['material_version']:
            prior=read_json(marker);candidate=Path(prior['board']).resolve()
            if prior.get('material_version')!=run['material_version'] or not candidate.is_relative_to(Store().home/'runs') or not (candidate/'materials.json').is_file():
                raise ValueError('原任务执行快照无效，请创建新任务')
            validate_snapshot(candidate)
            board=candidate;emit('resume',{'source_run':parent['id'],'detail':'复用材料快照；模型检查点按输入和策略指纹校验'})
    if board is None:board=snapshot_board(board_dir,workspace)
    write_json(workspace/'execution.json',{'board':str(board),'material_version':run['material_version']})
    stage(1)
    if run.get('resume') and (out_dir(board)/'board_kg.json').is_file():
        context={'materials':read_json(out_dir(board)/'materials.json'),'kg':read_json(out_dir(board)/'board_kg.json')}
    else:context=prepare_local(board)
    if inventory({'path':str(board_dir)})['version']!=run['material_version']:raise ValueError('复制期间材料发生变化，请重试')
    kg=context['kg'];materials=context['materials']
    if run['mode']=='materials':return {'report_dir':None,'summary':{'components':len(kg['components']),'nets':len(kg['nets']),'material_version':run['material_version']}}
    stage(2)
    scope=list(run['rule_ids']);materials.update(rule_ids=scope,rule_ids_explicit=True)
    write_json(out_dir(board)/'materials.json',materials)
    enumeration=enumerate_stage.run(board,kg,rule_ids=scope)
    if run['mode']=='program' and split_rule_ids(scope)['agent']:raise ValueError('程序检查不能包含模型评审项')
    stage(3)
    program,enumeration=program_stage.run(board,kg,enumeration)
    enumeration=scope_objects(enumeration,run.get('objects'))
    write_json(out_dir(board)/'agent_enumeration.json',enumeration)
    agent_enum=filter_enumeration_for_agent(enumeration,kg)
    context_meta={'coverage':{'skipped':True}}
    stage(4)
    if run['mode']=='review':
        from agent_review.llm import load_profile
        load_profile(run.get('profile_path'))  # Fail before starting any model task when unconfigured.
        if agent_enum['rule_ids']:
            prepare_cases(board,kg,agent_enum,resume=bool(run.get('resume')),emit=emit)
            context_meta=context_build.run(board,kg,agent_enum,use_sdk=False,concurrency=run['concurrency'])
            checked=judge.run(board,kg,agent_enum,concurrency=run['concurrency'],resume=bool(run.get('resume')))
            if checked.get('failed'):
                from agent_review.repair_cards import repair
                repair(board,kg,agent_enum,concurrency=run['concurrency'])
            from agent_review.evidence_followup import run as followup
            followup(board,kg,agent_enum,concurrency=run['concurrency'])
    stage(5)
    summary=aggregate_stage.run(board,enumeration,materials=materials,context_meta=context_meta,
          figure_rule_ids=scope,figure_objects=run.get('objects'),generate_prose=run['mode']=='review')
    stage(6)
    checked=gate.run(board,enumeration,materials=materials,context_meta=context_meta,generate_prose=run['mode']=='review')
    if not checked.get('passed'):raise ValueError('报告未通过交付校验：'+json.dumps(checked,ensure_ascii=False)[:2000])
    check_cancelled()
    # A resumed run can execute in its original checkpoint workspace. Its final
    # publication is a fresh immutable bundle under this attempt's workspace.
    report=out_dir(board)
    if not report.is_relative_to(workspace):
        target=workspace/'report';shutil.copytree(report,target,dirs_exist_ok=True);report=target
    emit('progress',{'completed':len(steps),'total':len(steps),'stage':'完成'})
    return {'report_dir':str(report),'summary':{**summary['verdicts']['summary'],'mode':run['mode'],
            'model_analysis':run['mode']=='review','objects':enumeration['stats']['judge_pairs'],'gate_passed':True}}
