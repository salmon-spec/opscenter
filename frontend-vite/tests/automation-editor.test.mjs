import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function workspace() {
  const element = { addEventListener() {} };
  const root = { querySelector: () => element, addEventListener() {} };
  const window = {};
  const context = vm.createContext({ window, parent: window, document: { getElementById: () => root },
    sessionStorage: { getItem: () => null }, setInterval: () => 0 });
  vm.runInContext(readFileSync(new URL('../../modules/automation/static/app.js', import.meta.url), 'utf8'), context);
  return context;
}

test('run variables keep task identifiers and descriptions in separate form controls', () => {
  const context = workspace();
  for (const name of ['template_id', 'message', 'dry_run']) {
    context.parameter = { name, label: name, type: 'text', default: 'value' };
    const html = vm.runInContext('runParameter(parameter, [])', context);
    assert.ok(html.includes(`name="parameter-${name}"`));
    assert.ok(!html.includes(`name="${name}"`));
  }
});

test('custom option labels and values remain text in generated parameter controls', () => {
  const context = workspace();
  context.parameter = { name: 'mode', label: '<unsafe>', type: 'choice', default: '', options: ['"><img src=x>'] };
  const html = vm.runInContext('runParameter(parameter, [])', context);
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;unsafe&gt;'));
  assert.ok(html.includes('&quot;&gt;&lt;img'));
});
