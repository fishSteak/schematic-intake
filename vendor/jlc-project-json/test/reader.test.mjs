import test from 'node:test';import assert from 'node:assert/strict';
import {parseEpru} from '../src/native-readonly-archive.mjs';
import {convertProject} from '../src/index.mjs';
test('损坏原生JSON必须拒绝',()=>assert.throws(()=>parseEpru('not-json',{strictJson:true})));
test('用户提供的工程可转为可序列化JSON',{skip:!process.env.JLC_TEST_PROJECT},()=>{const r=convertProject(process.env.JLC_TEST_PROJECT);assert(r.summary.pageCount>0);assert(r.summary.symbolCount>=r.summary.physicalCount);assert.doesNotThrow(()=>JSON.stringify(r));assert.equal(Object.hasOwn(r,'adapterBytes'),false);assert.throws(()=>convertProject(process.env.JLC_TEST_PROJECT,{expectedSha256:'0'.repeat(64)}),/SOURCE_HASH_MISMATCH/);});
