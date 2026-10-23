import test from 'node:test';
import assert from 'node:assert/strict';
import {decodeValidation, VALIDATION_PREFIX} from '../agent_review/validation_transport.mjs';

const output = (stdout, rest={}) => ({status:0, stdout, stderr:'', ...rest});
test('native PDF diagnostics do not turn a completed clean validation into a model repair', () => {
  assert.deepEqual(decodeValidation(output('MuPDF error: cmsOpenProfileFromMem failed\r\n'+VALIDATION_PREFIX+'[]\r\n')), []);
});
test('real validation errors survive surrounding native diagnostics', () => {
  assert.deepEqual(decodeValidation(output(VALIDATION_PREFIX+'["Wrong source identity"]\nMuPDF diagnostic\n')), ['Wrong source identity']);
});
test('missing, duplicate or malformed protocol and process errors remain failures', () => {
  for (const result of [output('[]'), output(VALIDATION_PREFIX+'[]\n'+VALIDATION_PREFIX+'[]'), output(VALIDATION_PREFIX+'{}'), output(VALIDATION_PREFIX+'[]', {status:1}), output(VALIDATION_PREFIX+'[]', {error:'process failed'})]) {
    assert.match(decodeValidation(result)[0], /^validation execution failed:/);
  }
});
