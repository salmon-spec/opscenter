import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

function workspace() {
  const element = { addEventListener() {}, appendChild() {} };
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

test('log copying falls back when the secure clipboard API is unavailable in the frame', async () => {
  const context = workspace();
  let copied = false;
  context.window.isSecureContext = true;
  context.navigator = { clipboard: { writeText: async () => { throw new Error('frame policy'); } } };
  context.document.createElement = () => ({ select() {}, remove() {} });
  context.document.execCommand = (command) => { copied = command === 'copy'; return copied; };
  vm.runInContext('logLines = ["verified log"]', context);
  await vm.runInContext('copyLog()', context);
  assert.equal(copied, true);
  assert.equal(vm.runInContext("$('#log-feedback').textContent", context), '日志已复制');
});


test('native script templates expose types and direct execution without Ansible check mode', async () => {
  const context = workspace();
  vm.runInContext("state={repositories:[{id:1,name:'repo'}],environments:[{id:1,name:'env'}],inventories:[],templates:[{id:7,name:'Python',app:'python',playbook:'example.py'}]}; open=(title,subtitle,html)=>{globalThis.formHtml=html}; updateTemplateType=()=>{}; api=async()=>({parameters:[]}); templateForm();", context);
  assert.ok(context.formHtml.includes('value="python"'));
  assert.ok(context.formHtml.includes('value="bash"'));
  await vm.runInContext('runForm(7)', context);
  assert.ok(context.formHtml.includes('直接执行'));
  assert.ok(context.formHtml.includes('执行容器'));
  assert.ok(!context.formHtml.includes('checked>检查模式'));
  assert.ok(context.formHtml.includes('最后一个命令行参数'));
});
