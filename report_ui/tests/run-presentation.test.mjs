import test from 'node:test';
import assert from 'node:assert/strict';
import { workflowStages, workflowStage, objectScope } from '../src/run-presentation.js';

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
