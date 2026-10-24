import test from 'node:test';
import assert from 'node:assert/strict';
import { workflowStages, workflowStage, objectScope, isReportRebuild, runTitle, runStages } from '../src/run-presentation.js';

test('the backend Chinese stages and legacy stages use the same seven-step track', () => {
  assert.equal(workflowStages.length, 7);
  assert.equal(workflowStage('模型评审'), '模型评审');
  assert.equal(workflowStage('judge'), '模型评审');
  assert.equal(workflowStage('context_build'), '模型评审');
  assert.equal(workflowStage('completed'), '完成');
  assert.equal(workflowStage('custom-stage'), 'custom-stage');
});

test('the displayed scope belongs to the stored run, including single-object and unrestricted runs', () => {
  const historicalRun = { mode: 'review', objects: ['R1'], completed: 7, total: 7 };
  assert.equal(objectScope(historicalRun), 'R1');
  assert.equal(objectScope({ mode: 'review', objects: ['R10', 'R11'] }), 'R10、R11');
  assert.equal(objectScope({ mode: 'review', objects: [] }), '全部适用对象');
  assert.equal(objectScope({ mode: 'program', objects: null }), '全部适用对象');
  assert.equal(objectScope({ mode: 'materials' }), '材料检查，不限定评审对象');
});

test('report-only rebuilds retain their actual operation and three-stage lifecycle', () => {
  for (const run of [{ operation: 'report_rebuild', mode: 'review' }, { mode: 'review', summary: { operation: 'report_rebuild', model_request_count: 0 } }]) {
    assert.equal(isReportRebuild(run), true);
    assert.equal(runTitle(run), '报告重建');
    assert.deepEqual(runStages(run), ['验证并复制原评审', '重新生成报告', '交付校验']);
  }
  assert.equal(runTitle({ mode: 'review' }), 'LLM 深度评审');
  assert.equal(isReportRebuild({ mode: 'review', summary: { model_request_count: 0 } }), false);
  assert.deepEqual(runStages({ mode: 'review' }), workflowStages);
});
