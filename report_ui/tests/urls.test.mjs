import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeBasePath, withBasePath, assetUrl, loginRedirectUrl } from '../src/urls.js';

test('root and subdirectory API paths preserve queries without duplicate prefixes', () => {
  assert.equal(withBasePath('/api/runs?board_id=board-1', '/'), '/api/runs?board_id=board-1');
  assert.equal(withBasePath('/api/runs?board_id=board-1', '/schematic/'), '/schematic/api/runs?board_id=board-1');
  assert.equal(withBasePath('/schematic/api/runtime', '/schematic/'), '/schematic/api/runtime');
  assert.equal(withBasePath('/schematic-other/api/runtime', '/schematic/'), '/schematic/schematic-other/api/runtime');
});

test('deployment base accepts path forms and rejects URLs and traversal', () => {
  assert.equal(normalizeBasePath(), '/');
  assert.equal(normalizeBasePath('schematic'), '/schematic/');
  assert.equal(normalizeBasePath('/schematic/review'), '/schematic/review/');
  for (const path of ['https://example.com/app', '/app?token=x', '/app/../other', '/app\\other']) {
    assert.throws(() => normalizeBasePath(path));
  }
});

test('versioned report resources support old and prefixed API responses', () => {
  const path = 'review_out/sch/schematic.pdf';
  assert.equal(assetUrl('/reports/run-1', path, '/'), '/reports/run-1/sch/schematic.pdf');
  assert.equal(assetUrl('/reports/run-1', path, '/schematic/'), '/schematic/reports/run-1/sch/schematic.pdf');
  assert.equal(assetUrl('/schematic/reports/run-1/', path, '/schematic/'), '/schematic/reports/run-1/sch/schematic.pdf');
  assert.equal(assetUrl('/reports/run-1', '/schematic/reports/run-1/sch/schematic.pdf', '/schematic/'), '/schematic/reports/run-1/sch/schematic.pdf');
  assert.equal(assetUrl('/reports/run-1', 'sources/material_inputs/spec.pdf', '/schematic/'), '/schematic/reports/run-1/sources/material_inputs/spec.pdf');
});

test('PDF viewer and its file parameter stay under the same deployment prefix', () => {
  const viewer = assetUrl('/reports/run-1', 'pdfjs/web/viewer.html', '/schematic/');
  const file = assetUrl('/reports/run-1', 'sch/schematic.pdf', '/schematic/');
  const url = new URL(`${viewer}?file=${encodeURIComponent(file)}#page=9`, 'https://fishsteak.xyz');
  assert.equal(url.pathname, '/schematic/reports/run-1/pdfjs/web/viewer.html');
  assert.equal(url.searchParams.get('file'), '/schematic/reports/run-1/sch/schematic.pdf');
  assert.equal(url.hash, '#page=9');
});

test('external source URLs and document anchors do not receive application prefixes', () => {
  assert.equal(assetUrl('/reports/run-1', 'https://example.com/spec.pdf', '/schematic/'), 'https://example.com/spec.pdf');
  assert.equal(withBasePath('https://example.com/spec.pdf', '/schematic/'), 'https://example.com/spec.pdf');
  assert.equal(withBasePath('#main-content', '/schematic/'), '#main-content');
});

test('login recovery keeps the current report route and replaces remote redirect parameters', () => {
  const current = 'https://fishsteak.xyz/schematic/reports?board=b&run=r#object-R1';
  const redirect = loginRedirectUrl('/login?next=https%3A%2F%2Fevil.example&redirect=https%3A%2F%2Fevil.example', current, '/schematic/');
  const target = new URL(redirect, current);
  assert.equal(target.pathname, '/login');
  assert.equal(target.searchParams.get('next'), '/schematic/reports?board=b&run=r#object-R1');
  assert.deepEqual([...target.searchParams.keys()], ['next']);
  assert.equal(loginRedirectUrl('https://fishsteak.xyz/login', current, '/schematic/'), redirect);
});

test('login recovery rejects external, credentialed, non-login and missing URLs', () => {
  const current = 'https://fishsteak.xyz/schematic/reports?run=r';
  for (const url of [undefined, null, '', 'https://evil.example/login', '//evil.example/login', 'http://fishsteak.xyz/login', 'https://user:secret@fishsteak.xyz/login', 'javascript:alert(1)', '/api/logout', '/login/other']) {
    assert.equal(loginRedirectUrl(url, current, '/schematic/'), '', String(url));
  }
});

test('login return locations cannot escape the workbench or become protocol-relative', () => {
  for (const current of ['https://fishsteak.xyz/other?next=evil', 'https://fishsteak.xyz//evil.example/', 'https://fishsteak.xyz/%2f%2fevil.example/']) {
    const target = new URL(loginRedirectUrl('/login', current, '/schematic/'), current);
    assert.equal(target.searchParams.get('next'), '/schematic/');
  }
  const target = new URL(loginRedirectUrl('/login', 'https://fishsteak.xyz/%2f%2fevil.example/', '/'), 'https://fishsteak.xyz');
  assert.equal(target.searchParams.get('next'), '/');
});
