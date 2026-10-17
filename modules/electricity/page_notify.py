"""Notification settings page (通知设置) — served at /ui/notify.

Lets the operator manage recipients (people / groups) and the delivery
channels: SMS (via a self-hosted gateway), e-mail, Feishu, WeCom,
WeChat push (ServerChan / PushPlus), DingTalk, Bark, ntfy and custom webhooks.
Each recipient can be tested individually; every delivery is audited.
"""
from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>通知设置 · 电费监测</title>
<style>
:root{--ink:#16302a;--muted:#5f756d;--line:#dfeae4;--brand:#0b7c6b;--warn:#b24c18;--crit:#b02a2a}
*{box-sizing:border-box}
body{margin:0;padding:22px 18px 56px;background:#f4f8f6;color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Microsoft YaHei",sans-serif}
.wrap{max-width:980px;margin:0 auto}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:12px;margin-bottom:18px}
h1{margin:0;font-size:23px}
.sub{margin:5px 0 0;color:var(--muted);font-size:13.5px}
button{font:inherit;background:var(--brand);color:#fff;border:0;border-radius:9px;padding:8px 14px;cursor:pointer}
button[disabled]{opacity:.55;cursor:default}
button.ghost{background:#e6f1ed;color:#125b4d}
button.tiny{padding:4px 10px;font-size:12px;border-radius:7px}
button.danger{background:#fdecec;color:var(--crit)}
section{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:14px}
h2{margin:0 0 6px;font-size:16.5px}
.hint{margin:0 0 12px;font-size:12.5px;color:var(--muted)}
table{border-collapse:collapse;width:100%}
th,td{padding:9px 10px;border-bottom:1px solid #edf3ef;text-align:left;font-size:13.5px}
th{color:var(--muted);font-weight:600;font-size:12.5px}
.chip{font-size:11.5px;font-weight:700;padding:2px 8px;border-radius:20px;background:#e6ece9;color:#3e544c}
.chip.on{background:#0b7c6b;color:#fff}
.chip.off{background:#dfe4e2;color:#6b7a74}
form{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));align-items:end}
label{font-size:12.5px;color:var(--muted);display:grid;gap:4px}
input,select{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:#fbfdfc;color:var(--ink)}
.state{margin-top:12px;font-size:13px;color:var(--muted);min-height:18px}
.state.err{color:var(--crit)}
.grid2{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.note{font-size:12.5px;color:var(--muted);margin:8px 0 0}
code{background:#eef5f2;border-radius:5px;padding:1px 5px;font-size:12px}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>通知设置</h1>
      <p class="sub">添加通知人并选择通知方式；告警触发时按级别推送给对应的人或群。</p>
    </div>
    <div><a href="ui" style="color:var(--brand);font-size:13.5px">← 返回电费监测</a></div>
  </header>

  <section>
    <h2>通知人</h2>
    <p class="hint" id="targets-hint">加载中…</p>
    <div class="table-wrap"><table><thead>
      <tr><th>名称</th><th>方式</th><th>地址</th><th>接收级别</th><th>状态</th><th>操作</th></tr>
    </thead><tbody id="targets"></tbody></table></div>
  </section>

  <section>
    <h2 id="form-title">添加通知人</h2>
    <p class="hint">地址按方式填写：飞书/企微/钉钉/Webhook 填机器人地址；Server酱 填 SendKey；PushPlus 填 token；邮箱填收件地址（需先在下方填写 SMTP）；短信填手机号（需先配置短信网关）；Bark/ntfy 填推送地址。</p>
    <form id="target-form" onsubmit="return false">
      <label>名称<input id="f-name" placeholder="如：秋水的微信" /></label>
      <label>通知方式<select id="f-type"></select></label>
      <label>地址 / Key<input id="f-target" placeholder="按上方说明填写" /></label>
      <label>接收级别<select id="f-sev">
        <option value="warning" selected>警告及以上（推荐）</option>
        <option value="critical">仅严重</option>
        <option value="info">全部（含提示）</option>
      </select></label>
      <button id="f-save">保存</button>
      <button class="ghost" id="f-reset" type="button">清空</button>
    </form>
  </section>

  <section>
    <h2>通道基础设施（可选）</h2>
    <p class="hint">邮箱通道需要 SMTP；短信通道需要一个能接收 <code>{"phone","message"}</code> JSON 的自建网关（对接阿里云/腾讯云短信均可）。</p>
    <form id="settings-form" onsubmit="return false">
      <label>SMTP 服务器<input id="s-smtp" placeholder="smtp.example.com" /></label>
      <label>SMTP 端口<input id="s-port" placeholder="465" /></label>
      <label>SMTP 用户名<input id="s-user" placeholder="user@example.com" /></label>
      <label>SMTP 密码<input id="s-pass" type="password" placeholder="已保存则留空" /></label>
      <label>发件人<input id="s-from" placeholder="电费监测 &lt;user@example.com&gt;" /></label>
      <label>短信网关地址<input id="s-sms" placeholder="https://your-gateway/sms/send" /></label>
      <label>短信签名<input id="s-sign" placeholder="选填" /></label>
      <label>短信模板<input id="s-tpl" placeholder="选填" /></label>
      <button id="s-save">保存设置</button>
    </form>
  </section>

  <section>
    <h2>通知记录</h2>
    <p class="hint">最近 20 次推送与测试。</p>
    <div class="table-wrap"><table><thead><tr><th>时间</th><th>事件</th><th>内容</th><th>通道</th></tr></thead><tbody id="history"></tbody></table></div>
  </section>

  <p class="state" id="state"></p>
</div>
<script>
const API = '/modules/electricity/api/v1';
const $ = (id) => document.getElementById(id);
const framed = window.parent !== window;
const pending = new Map();
let requestId = 0;
let moduleReady = !framed;
let editing = null;
const TYPE_LABEL = {};
const ACTION = { firing: '首次告警', 're-fire': '重复提醒', resolved: '已恢复', test: '测试推送' };
const esc = (v) => String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function request(path, method = 'GET', body) {
  if (!framed) {
    const token = localStorage.getItem('ops-access-token') || '';
    return fetch(path, {
      method,
      headers: Object.assign({}, token ? { Authorization: `Bearer ${token}` } : {}, body ? { 'Content-Type': 'application/json' } : {}),
      body: body ? JSON.stringify(body) : undefined,
    }).then(async (r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
  }
  const id = `notify-${Date.now()}-${++requestId}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('请求超时')); }, 30000);
    pending.set(id, { resolve, reject, timer });
    window.parent.postMessage({ type: 'ops-module-api-request', id, path, method, body, timeoutMs: 30000 }, '*');
  });
}

function renderTargets(data) {
  const targets = data.targets || [];
  $('targets-hint').textContent = targets.length
    ? `共 ${targets.length} 位通知人；发送时会按「接收级别」过滤。`
    : '还没有通知人：在下方添加第一位即可开始接收告警。';
  $('targets').innerHTML = targets.length ? targets.map((t) => `
    <tr><td>${esc(t.name)}</td><td>${esc(TYPE_LABEL[t.type] || t.type)}</td>
    <td>${esc(t.target_masked)}</td><td>${esc(t.min_severity)}</td>
    <td><span class="chip ${t.enabled ? 'on' : 'off'}">${t.enabled ? '启用' : '停用'}</span></td>
    <td>
      <button class="ghost tiny" data-test="${esc(t.id)}">测试</button>
      <button class="ghost tiny" data-toggle="${esc(t.id)}">${t.enabled ? '停用' : '启用'}</button>
      <button class="ghost tiny" data-edit="${esc(t.id)}">编辑</button>
      <button class="danger tiny" data-del="${esc(t.id)}">删除</button>
    </td></tr>`).join('') : '<tr><td colspan="6" class="hint">暂无</td></tr>';
}

function renderHistory(data) {
  const rows = data.history || [];
  $('history').innerHTML = rows.length ? rows.map((h) => {
    const failed = h.delivered === false;
    return `<tr><td>${esc((h.at || '').replace('T', ' '))}</td><td>${esc(ACTION[h.action] || h.action || '')}</td>` +
      `<td>${esc(h.title || h.alert_id || '')}</td>` +
      `<td class="${failed ? 'state err' : ''}">${esc((h.channels || []).join(', ') || '—')}${failed ? ' · 失败' : ''}</td></tr>`;
  }).join('') : '<tr><td colspan="4" class="hint">暂无记录</td></tr>';
}

async function load() {
  if (!moduleReady) return;
  try {
    const data = await request(`${API}/notify/targets`);
    TYPE_LABEL.clear ? null : null;
    Object.assign(TYPE_LABEL, data.types || {});
    const select = $('f-type');
    select.innerHTML = Object.entries(data.types || {}).map(([value, label]) =>
      `<option value="${esc(value)}">${esc(label)}</option>`).join('');
    renderTargets(data);
    $('s-smtp').value = data.settings?.smtp_host || '';
    $('s-port').value = data.settings?.smtp_port || '';
    $('s-user').value = data.settings?.username || '';
    $('s-pass').value = data.settings?.password || '';
    $('s-from').value = data.settings?.mail_from || '';
    $('s-sms').value = data.settings?.sms_webhook || '';
    $('s-sign').value = data.settings?.sms_sign || '';
    $('s-tpl').value = data.settings?.sms_template || '';
    renderHistory(await request(`${API}/alerts?history=20`));
  } catch (e) { setState(`加载失败：${e.message}`, true); }
}

function setState(text, error) {
  $('state').textContent = text;
  $('state').className = error ? 'state err' : 'state';
}

$('f-save').onclick = async () => {
  const payload = {
    id: editing || '',
    name: $('f-name').value.trim(),
    type: $('f-type').value,
    target: $('f-target').value.trim(),
    min_severity: $('f-sev').value,
  };
  if (!payload.target) return setState('地址不能为空', true);
  try {
    await request(`${API}/notify/targets`, 'POST', payload);
    setState(editing ? '通知人已更新' : '通知人已添加');
    editing = null; $('f-name').value = ''; $('f-target').value = '';
    $('form-title').textContent = '添加通知人';
    await load();
  } catch (e) { setState(`保存失败：${e.message}`, true); }
};

$('f-reset').onclick = () => {
  editing = null; $('f-name').value = ''; $('f-target').value = '';
  $('form-title').textContent = '添加通知人';
};

$('s-save').onclick = async () => {
  try {
    await request(`${API}/notify/settings`, 'POST', {
      smtp_host: $('s-smtp').value.trim(), smtp_port: $('s-port').value.trim(),
      username: $('s-user').value.trim(), password: $('s-pass').value,
      mail_from: $('s-from').value.trim(),
      sms_webhook: $('s-sms').value.trim(), sms_sign: $('s-sign').value.trim(),
      sms_template: $('s-tpl').value.trim(),
    });
    setState('通道设置已保存');
    await load();
  } catch (e) { setState(`保存失败：${e.message}`, true); }
};

$('targets').addEventListener('click', async (event) => {
  const btn = event.target.closest('button');
  if (!btn) return;
  btn.disabled = true;
  try {
    if (btn.dataset.test) {
      const result = await request(`${API}/notify/test`, 'POST', { target_id: btn.dataset.test });
      const failed = (result.results || []).filter((r) => !r.ok).length;
      setState(`测试已发送 ${result.sent} 条` + (failed ? `，其中 ${failed} 条失败` : '，请查收'));
    } else if (btn.dataset.toggle) {
      await request(`${API}/notify/targets/toggle`, 'POST', { id: btn.dataset.toggle });
    } else if (btn.dataset.del) {
      await request(`${API}/notify/targets/delete`, 'POST', { id: btn.dataset.del });
      setState('已删除');
    } else if (btn.dataset.edit) {
      const data = await request(`${API}/notify/targets`);
      const item = (data.targets || []).find((t) => t.id === btn.dataset.edit);
      if (!item) throw new Error('未找到');
      editing = item.id;
      $('f-name').value = item.name || '';
      $('f-type').value = item.type;
      $('f-target').value = item.target || '';
      $('f-sev').value = item.min_severity || 'warning';
      $('form-title').textContent = `编辑通知人：${item.name}`;
    }
    await load();
  } catch (e) { setState(`操作失败：${e.message}`, true); btn.disabled = false; }
});

window.addEventListener('message', (event) => {
  if (event.source !== window.parent) return;
  const data = event.data || {};
  if (data.type === 'ops-module-info') { moduleReady = true; load(); }
  else if (data.type === 'ops-module-api-response') {
    const task = pending.get(data.id);
    if (!task) return;
    pending.delete(data.id); clearTimeout(task.timer);
    if (data.ok) task.resolve(data.data); else task.reject(new Error(data.data?.message || '请求失败'));
  }
});

if (framed) {
  const announce = () => window.parent.postMessage({ type: 'ops-module-ready' }, '*');
  announce();
  const timer = setInterval(() => { if (moduleReady) clearInterval(timer); else announce(); }, 400);
  setTimeout(() => clearInterval(timer), 15000);
} else { load(); }
</script>
</body>
</html>
"""


def render_notify_page() -> str:
    return PAGE
