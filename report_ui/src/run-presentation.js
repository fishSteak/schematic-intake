export const workflowStages = ['材料快照', '材料解析', '检查范围', '程序检查', '模型评审', '报告生成', '交付校验'];

export function isReportRebuild(run) {
  return (run?.operation || run?.summary?.operation) === 'report_rebuild';
}

export function runTitle(run) {
  return isReportRebuild(run) ? '报告重建' : ({ materials: '材料检查', program: '程序检查', review: 'LLM 深度评审' })[run?.mode] || '评审任务';
}

export function runStages(run) {
  return isReportRebuild(run) ? ['验证并复制原评审', '重新生成报告', '交付校验'] : workflowStages;
}

export function workflowStage(stage) {
  return ({ starting: '材料快照', prep: '材料解析', materials: '材料解析', enumerate: '检查范围', program: '程序检查',
    cases: '模型评审', calc: '模型评审', context_build: '模型评审', judge: '模型评审', stress_figures: '报告生成',
    aggregate: '报告生成', gate: '交付校验', publish: '交付校验', completed: '完成', queued: '等待调度',
    stopping: '正在停止', stopped: '已停止', failed: '执行失败', interrupted: '已中断' })[stage] || stage || '';
}

export function objectScope(run) {
  if (Array.isArray(run?.objects) && run.objects.length) return run.objects.join('、');
  return run?.mode === 'materials' ? '材料检查，不限定评审对象' : '全部适用对象';
}
