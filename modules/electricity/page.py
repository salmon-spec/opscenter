"""Self-contained dashboard page served by the electricity module."""
from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>电费监测</title>
<style>
:root{--ink:#16302a;--muted:#5f756d;--line:#dfeae4;--brand:#0b7c6b;--brand-l:#d9efe8;--warn:#b24c18;--warn-bg:#fff5ed;--warn-line:#f1c39d}
*{box-sizing:border-box}
body{margin:0;padding:22px 18px 56px;background:#f4f8f6;color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Microsoft YaHei",sans-serif}
.wrap{max-width:1040px;margin:0 auto}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:14px;margin-bottom:20px}
h1{margin:0;font-size:24px;letter-spacing:.01em}
.sub{margin:5px 0 0;color:var(--muted);font-size:13.5px}
button{font:inherit;background:var(--brand);color:#fff;border:0;border-radius:9px;padding:9px 15px;cursor:pointer}
button[disabled]{opacity:.55;cursor:default}
button.ghost{background:#e6f1ed;color:#125b4d}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:13px}
.card,section{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:0 3px 14px #14332c08}
.card{padding:17px}
.card .label{font-size:13px;color:var(--muted)}
.card .value{font-size:28px;font-weight:700;line-height:1.3;margin:7px 0 2px}
.card .value small{font-size:14px;font-weight:500}
.card .note{font-size:12.5px;color:#6d8078}
.card.warn{background:var(--warn-bg);border-color:var(--warn-line)}
.card.warn .value{color:var(--warn)}
section{padding:20px;margin-top:16px}
h2{margin:0 0 4px;font-size:17px}
.hint{margin:0 0 14px;font-size:12.5px;color:var(--muted)}
.days{display:grid;gap:6px}
.day{display:grid;grid-template-columns:100px 1fr 68px;align-items:center;gap:11px;font-size:13px}
.day strong{text-align:right;font-variant-numeric:tabular-nums}
.track{height:11px;background:#eaf3ef;border-radius:20px;overflow:hidden}
.fill{display:block;height:100%;border-radius:20px;background:linear-gradient(90deg,#66c3ac,#0b7c6b)}
.table-wrap{overflow:auto}
table{border-collapse:collapse;width:100%}
th,td{padding:11px 12px;border-bottom:1px solid #edf3ef;text-align:left;font-size:14px}
th{color:var(--muted);font-weight:600;font-size:13px}
.notice{background:var(--warn-bg);border:1px solid var(--warn-line);border-radius:10px;padding:11px 13px;font-size:13.5px;margin:14px 0 0}
.state{margin-top:14px;font-size:13.5px;color:var(--muted)}
.state.err{color:var(--warn)}
.empty{margin:0;color:var(--muted);font-size:14px}
@media(max-width:820px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.card .value{font-size:24px}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>电费监测</h1>
      <p class="sub" id="identity">正在加载…</p>
    </div>
    <div><button id="refresh">立即刷新</button></div>
  </header>
  <div class="cards" id="cards">
    <div class="card"><div class="label">当前余额</div><div class="value">—</div><div class="note">—</div></div>
    <div class="card"><div class="label">最近已公布日电量</div><div class="value">—</div><div class="note">—</div></div>
    <div class="card"><div class="label">最近账单电费</div><div class="value">—</div><div class="note">—</div></div>
    <div class="card"><div class="label">近 7 个公布日日均</div><div class="value">—</div><div class="note">—</div></div>
  </div>
  <section><h2>近 30 日用电</h2><p class="hint">单位：度。国家电网公布日期通常晚于当天。</p><div id="chart"></div></section>
  <section><h2>最近账单</h2><div class="table-wrap"><table><thead><tr><th>月份</th><th>用电量</th><th>电费</th></tr></thead><tbody id="bills"></tbody></table></div></section>
  <p class="state" id="state"></p>
</div>
<script>
const API = '/modules/electricity/api/v1';
const $ = (id) => document.getElementById(id);
const framed = window.parent !== window;
const pending = new Map();
let requestId = 0;
let moduleReady = !framed;
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(d);
const monthLabel = (m) => (/^\\d{6}$/.test(String(m)) ? `${String(m).slice(0,4)}-${String(m).slice(4)}` : (m || '—'));

function request(path, method = 'GET') {
  if (!framed) {
    const token = localStorage.getItem('ops-access-token') || '';
    return fetch(path, { method, headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      });
  }
  const id = `electricity-${Date.now()}-${++requestId}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('模块接口请求超时')); }, 20000);
    pending.set(id, { resolve, reject, timer });
    window.parent.postMessage({ type: 'ops-module-api-request', id, path, method, timeoutMs: 20000 }, '*');
  });
}

function render(data) {
  const item = (data.households || [])[0] || {};
  const daily = item.daily || [];
  const bills = item.bills || [];
  const last = daily[0] || {};
  const recent = daily.slice(0, 7);
  const avg = recent.length ? recent.reduce((s, r) => s + Number(r.kwh || 0), 0) / recent.length : null;
  const bal = item.balance;
  const low = bal !== null && bal !== undefined && Number(bal) < 20;

  $('identity').textContent = `${item.name || '未找到户号'} · 户号尾号 ${String(item.id || '').slice(-4) || '----'}`;

  const card = (label, value, unit, note, warn) =>
    `<div class="card${warn ? ' warn' : ''}"><div class="label">${label}</div>` +
    `<div class="value">${value}<small> ${unit}</small></div><div class="note">${note}</div></div>`;
  $('cards').innerHTML = [
    card('当前余额', num(bal), '元', item.due > 0 ? `应缴 ${num(item.due)} 元` : '以国网账单为准', low),
    card('最近已公布日电量', num(last.kwh), '度', last.day || '暂无数据'),
    card('最近账单电费', num((bills[0] || {}).cost), '元', monthLabel((bills[0] || {}).month)),
    card('近 7 个公布日日均', num(avg), '度', `共 ${recent.length} 天`),
  ].join('');

  const peak = Math.max(1, ...daily.map((r) => Number(r.kwh || 0)));
  $('chart').innerHTML = daily.length ? `<div class="days">` + daily.map((r) =>
    `<div class="day"><time>${r.day}</time><span class="track">` +
    `<span class="fill" style="width:${Math.min(100, Math.max(2, (r.kwh / peak) * 100)).toFixed(1)}%"></span></span>` +
    `<strong>${num(r.kwh)}</strong></div>`).join('') + `</div>` : '<p class="empty">暂无逐日用电数据</p>';

  $('bills').innerHTML = bills.length ? bills.map((r) =>
    `<tr><td>${monthLabel(r.month)}</td><td>${num(r.kwh)} 度</td><td>${num(r.cost)} 元</td></tr>`).join('')
    : '<tr><td colspan="3">暂无账单</td></tr>';

  const errors = data.errors || [];
  $('state').className = errors.length ? 'state err' : 'state';
  $('state').textContent = `采集时间：${(data.updated_at || '尚未采集').replace('T', ' ')}` +
    (errors.length ? ` · 待重试：${errors.join('；')}` : ' · 每 12 小时自动采集');
}

function notice(text) {
  $('cards').innerHTML = `<div class="card" style="grid-column:1/-1"><div class="label">${text}</div></div>`;
  $('chart').innerHTML = ''; $('bills').innerHTML = ''; $('identity').textContent = '电费监测';
}

async function load() {
  if (!moduleReady) return;
  try {
    render(await request(`${API}/overview`));
  } catch (e) {
    notice(`模块暂时不可用（${e.message}）`);
  }
}

$('refresh').onclick = async () => {
  if (!moduleReady) return;
  const btn = $('refresh'); btn.disabled = true; btn.textContent = '采集中…';
  try {
    const result = await request(`${API}/refresh`, 'POST');
    $('state').textContent = result.triggered ? '已触发采集，稍后自动刷新结果…' : (result.message || '已有采集任务在进行');
  } catch (e) { $('state').textContent = '触发采集失败'; }
  setTimeout(async () => { btn.disabled = false; btn.textContent = '立即刷新'; await load(); }, 6000);
};

window.addEventListener('message', (event) => {
  if (event.source !== window.parent) return;
  const data = event.data || {};
  if (data.type === 'ops-module-info') {
    moduleReady = true;
    $('identity').textContent = `${data.module?.name || '电费监测'} · v${data.module?.version || '0.1.0'}`;
    load();
  } else if (data.type === 'ops-module-api-response') {
    const task = pending.get(data.id);
    if (!task) return;
    pending.delete(data.id);
    clearTimeout(task.timer);
    if (data.ok) task.resolve(data.data);
    else task.reject(new Error(data.data?.message || '模块请求失败'));
  }
});

if (framed) {
  const announceReady = () => window.parent.postMessage({ type: 'ops-module-ready' }, '*');
  announceReady();
  const readyTimer = setInterval(() => { if (moduleReady) clearInterval(readyTimer); else announceReady(); }, 400);
  setTimeout(() => clearInterval(readyTimer), 15000);
} else {
  load();
}
setInterval(load, 60000);
</script>
</body>
</html>
"""


def render_page() -> str:
    return PAGE
