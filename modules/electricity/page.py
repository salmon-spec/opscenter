"""Self-contained dashboard page (v0.6.0).

Focus per the operator's requirement: current balance, this month's spending and
the estimated days left — plus an adjustable day/month/year spending table and a
link to the standalone notification settings page (/ui/notify).

Removed in this revision: the prepaid-power banner, the per-kWh price banner,
the tiered / time-of-use sections, the 7-day forecast chart and the collection
diagnostics drawer (backend diagnostics APIs remain available for operators).
"""
from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>电费监测</title>
<style>
:root{--ink:#16302a;--muted:#5f756d;--line:#dfeae4;--brand:#0b7c6b;--warn:#b24c18;--warn-bg:#fff5ed;--warn-line:#f1c39d;--crit:#b02a2a}
*{box-sizing:border-box}
body{margin:0;padding:22px 18px 56px;background:#f4f8f6;color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Microsoft YaHei",sans-serif}
.wrap{max-width:980px;margin:0 auto}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:12px;margin-bottom:18px}
h1{margin:0;font-size:24px}
.sub{margin:5px 0 0;color:var(--muted);font-size:13.5px}
.actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
a.link{font-size:13.5px;color:var(--brand);text-decoration:none;background:#e6f1ed;border-radius:9px;padding:8px 14px}
button{font:inherit;background:var(--brand);color:#fff;border:0;border-radius:9px;padding:8px 14px;cursor:pointer}
button[disabled]{opacity:.55;cursor:default}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin-bottom:16px}
.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 3px 14px #14332c08}
.card .label{font-size:13px;color:var(--muted)}
.card .value{font-size:34px;font-weight:700;line-height:1.25;margin:8px 0 4px}
.card .value small{font-size:15px;font-weight:500}
.card .note{font-size:12.5px;color:#6d8078}
.card.warn{background:var(--warn-bg);border-color:var(--warn-line)}
.card.warn .value{color:var(--warn)}
section{background:#fff;border:1px solid var(--line);border-radius:14px;padding:18px;margin-top:14px}
h2{margin:0 0 4px;font-size:16.5px}
.hint{margin:0 0 12px;font-size:12.5px;color:var(--muted)}
.toolbar{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px}
.toolbar .seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;background:#fff}
.toolbar button{border-radius:0;padding:6px 14px;background:#fff;color:var(--muted);font-size:13px}
.toolbar button.active{background:var(--brand);color:#fff}
.table-wrap{overflow:auto}
table{border-collapse:collapse;width:100%}
th,td{padding:10px 12px;border-bottom:1px solid #edf3ef;text-align:left;font-size:13.5px;font-variant-numeric:tabular-nums}
th{color:var(--muted);font-weight:600;font-size:12.5px;white-space:nowrap}
td.num{text-align:right}
.chip{font-size:11px;font-weight:600;padding:2px 7px;border-radius:14px;background:#eef5f2;color:#4d6459}
.chip.est{background:#fff5ed;color:#a05a22}
.delta-up{color:var(--crit);font-weight:600}
.delta-down{color:#0b7c6b;font-weight:600}
.state{margin-top:14px;font-size:13.5px;color:var(--muted)}
.state.err{color:var(--warn)}
@media(max-width:720px){.card .value{font-size:28px}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>电费监测</h1>
      <p class="sub" id="identity">正在加载…</p>
    </div>
    <div class="actions">
      <a class="link" href="ui/notify">通知设置</a>
      <button id="refresh">立即刷新</button>
    </div>
  </header>

  <div class="cards">
    <div class="card" id="card-balance"><div class="label">当前电费余额</div><div class="value">—</div><div class="note">—</div></div>
    <div class="card" id="card-month"><div class="label">本月花费</div><div class="value">—</div><div class="note">—</div></div>
    <div class="card" id="card-days"><div class="label">预计可用</div><div class="value">—</div><div class="note">—</div></div>
  </div>

  <section>
    <h2>电费统计</h2>
    <p class="hint" id="stats-hint">花费按真实均价折算；月/年含账单数据时用账单值。</p>
    <div class="toolbar">
      <span class="seg" id="seg-gran">
        <button data-gran="day" class="active">按天</button>
        <button data-gran="month">按月</button>
        <button data-gran="year">按年</button>
      </span>
      <span class="seg" id="seg-range"></span>
    </div>
    <div class="table-wrap">
      <table><thead><tr>
        <th id="th-label">日期</th><th class="num">用电量（度）</th><th class="num">花费（元）</th>
        <th class="num">环比</th><th>口径</th>
      </tr></thead><tbody id="stats-rows"></tbody>
      <tfoot id="stats-total"></tfoot></table>
    </div>
  </section>

  <section>
    <h2>通知记录</h2>
    <p class="hint">最近 10 次告警推送与测试；新增通知人请到 <a href="ui/notify" style="color:var(--brand)">通知设置</a>。</p>
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
let gran = 'day';
let range = 30;
const RANGES = { day: [7, 14, 30, 90], month: [6, 12, 24], year: [2, 3, 5] };
const ACTION = { firing: '首次告警', 're-fire': '重复提醒', resolved: '已恢复', test: '测试推送' };
const esc = (v) => String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(d);
const LABEL = { day: '日期', month: '月份', year: '年份' };
const RANGE_LABEL = { day: '天', month: '个月', year: '年' };

function request(path, method = 'GET', body, query) {
  // IMPORTANT: inside the workbench iframe the API bridge rejects paths that
  // carry a query string, so parameters must travel in `data.query`.
  if (!framed) {
    const qs = query ? '?' + new URLSearchParams(query).toString() : '';
    const token = localStorage.getItem('ops-access-token') || '';
    return fetch(path + qs, {
      method,
      headers: Object.assign({}, token ? { Authorization: `Bearer ${token}` } : {}, body ? { 'Content-Type': 'application/json' } : {}),
      body: body ? JSON.stringify(body) : undefined,
    }).then(async (r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
  }
  const id = `electricity-${Date.now()}-${++requestId}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('模块接口请求超时')); }, 30000);
    pending.set(id, { resolve, reject, timer });
    window.parent.postMessage({ type: 'ops-module-api-request', id, path, method, body, query, timeoutMs: 30000 }, '*');
  });
}

function renderOverview(data) {
  const item = (data.households || [])[0] || {};
  const bal = item.balance;
  const low = bal !== null && bal !== undefined && Number(bal) < 20;
  const days = data.remaining_days;
  const monthNote = data.monthly_budget
    ? `预算 ${num(data.monthly_budget)} 元 · 预计月末 ${num(data.month_cost_projection)} 元`
    : (data.month_cost_projection ? `按当前节奏预计月末 ${num(data.month_cost_projection)} 元` : '暂无可折算数据');
  const price = data.price_per_kwh ? ` · 单价 ${num(data.price_per_kwh, 4)} 元/度` : '';

  $('identity').textContent = `${item.name || '未找到户号'} · 户号尾号 ${String(item.id || '').slice(-4) || '----'}`;
  $('card-balance').className = 'card' + (low ? ' warn' : '');
  $('card-balance').innerHTML =
    `<div class="label">当前电费余额</div><div class="value">${num(bal)}<small> 元</small></div>` +
    `<div class="note">${item.due > 0 ? `应缴 ${num(item.due)} 元 · ` : ''}更新于 ${esc((data.updated_at || '').replace('T', ' ').slice(0, 16))}${price}</div>`;
  $('card-month').innerHTML =
    `<div class="label">本月花费</div><div class="value">${num(data.month_cost)}<small> 元</small></div>` +
    `<div class="note">${data.month_cost_estimated ? '估算值 · ' : ''}${esc(monthNote)}</div>`;
  const warnDays = days !== null && days !== undefined && Number(days) < 5;
  $('card-days').className = 'card' + (warnDays ? ' warn' : '');
  $('card-days').innerHTML =
    `<div class="label">预计可用</div><div class="value">${num(days, 1)}<small> 天</small></div>` +
    `<div class="note">日均约 ${num(data.estimated_daily_cost)} 元 · 近 7 日均 ${num(data.average_7d_kwh, 1)} 度</div>`;
  $('state').textContent = `采集：${(data.updated_at || '尚未采集').replace('T', ' ')}` +
    ((data.errors || []).length ? ` · 待重试：${(data.errors || []).join('；')}` : '');
  $('state').className = (data.errors || []).length ? 'state err' : 'state';
}

function renderStats(data) {
  const rows = data.rows || [];
  $('th-label').textContent = LABEL[data.granularity] || '日期';
  $('stats-hint').textContent = `单价 ${num(data.price_per_kwh, 4)} 元/度（${data.price_source === 'year-average' ? '年度账单实算' : '配置值'}）；` +
    `共 ${rows.length} 行，合计 ${num(data.totals?.cost)} 元`;
  $('stats-rows').innerHTML = rows.length ? rows.slice().reverse().map((row) => `
    <tr><td>${esc(row.label)}</td>
    <td class="num">${num(row.kwh, 1)}</td>
    <td class="num">${num(row.cost)}</td>
    <td class="num">${row.delta_pct === null || row.delta_pct === undefined ? '—'
      : `<span class="${row.delta_pct > 0 ? 'delta-up' : (row.delta_pct < 0 ? 'delta-down' : '')}">${row.delta_pct > 0 ? '+' : ''}${row.delta_pct}%</span>`}</td>
    <td><span class="chip ${row.estimated ? 'est' : ''}">${esc(row.source)}</span></td></tr>`).join('')
    : '<tr><td colspan="5" class="hint">暂无数据</td></tr>';
  $('stats-total').innerHTML = rows.length
    ? `<tr><th>合计（${rows.length} 行）</th><th class="num">${num(data.totals?.kwh, 1)}</th><th class="num">${num(data.totals?.cost)}</th><th class="num">平均 ${num(data.totals?.avg)}</th><th></th></tr>`
    : '';
}

function renderHistory(rows) {
  $('history').innerHTML = rows.length ? rows.map((h) => {
    const failed = h.delivered === false;
    return `<tr><td>${esc((h.at || '').replace('T', ' '))}</td><td>${esc(ACTION[h.action] || h.action || '')}</td>` +
      `<td>${esc(h.title || h.alert_id || '')}</td>` +
      `<td class="${failed ? 'state err' : ''}">${esc((h.channels || []).join(', ') || '—')}${failed ? ' · 失败' : ''}</td></tr>`;
  }).join('') : '<tr><td colspan="4" class="hint">暂无推送记录；到 通知设置 添加通知人后即可接收。</td></tr>';
}

async function loadStats() {
  try {
    renderStats(await request(`${API}/stats`, 'GET', undefined, { granularity: gran, limit: range }));
  } catch (e) { $('stats-hint').textContent = `统计接口不可用（${e.message}）`; }
}

function renderRangeButtons() {
  $('seg-range').innerHTML = RANGES[gran].map((value) =>
    `<button data-range="${value}" class="${value === range ? 'active' : ''}">近 ${value} ${RANGE_LABEL[gran]}</button>`).join('');
}

async function load() {
  if (!moduleReady) return;
  let overview;
  try {
    overview = await request(`${API}/overview`);
    renderOverview(overview);
  } catch (e) {
    $('card-balance').innerHTML = `<div class="label">当前电费余额</div><div class="value">—</div><div class="note">模块暂时不可用（${esc(e.message)}）</div>`;
    return;
  }
  await loadStats();
  try { renderHistory((await request(`${API}/alerts`, 'GET', undefined, { history: 10 })).history || []); }
  catch (e) { $('history').innerHTML = '<tr><td colspan="4" class="hint">通知记录不可用</td></tr>'; }
}

$('seg-gran').addEventListener('click', async (event) => {
  const btn = event.target.closest('button');
  if (!btn || btn.dataset.gran === gran) return;
  gran = btn.dataset.gran;
  range = RANGES[gran][Math.min(2, RANGES[gran].length - 1)];
  document.querySelectorAll('#seg-gran button').forEach((el) => el.classList.toggle('active', el.dataset.gran === gran));
  renderRangeButtons();
  await loadStats();
});

$('seg-range').addEventListener('click', async (event) => {
  const btn = event.target.closest('button');
  if (!btn || Number(btn.dataset.range) === range) return;
  range = Number(btn.dataset.range);
  renderRangeButtons();
  await loadStats();
});

$('refresh').onclick = async () => {
  const btn = $('refresh'); btn.disabled = true; btn.textContent = '采集中…';
  try {
    const result = await request(`${API}/refresh`, 'POST');
    $('state').textContent = result.triggered ? '已触发采集，稍后自动刷新…' : (result.message || '已有采集任务在进行');
  } catch (e) { $('state').textContent = `触发采集失败：${e.message}`; }
  setTimeout(async () => { btn.disabled = false; btn.textContent = '立即刷新'; await load(); }, 8000);
};

window.addEventListener('message', (event) => {
  if (event.source !== window.parent) return;
  const data = event.data || {};
  if (data.type === 'ops-module-info') {
    moduleReady = true;
    $('identity').textContent = `${data.module?.name || '电费监测'} · v${data.module?.version || ''}`;
    renderRangeButtons();
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
  renderRangeButtons();
  load();
}
setInterval(load, 60000);
</script>
</body>
</html>
"""


def render_page() -> str:
    return PAGE
