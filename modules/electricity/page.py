"""Self-contained dashboard page (v0.7.0) — merged single page with top tabs.

One page, two tabs (电费监测 / 通知设置) that switch in place, styled to match
the OpsCenter workbench palette (--bg #f4f6fa, --brand #2563eb, cards on white,
10px radius). The day/month/year statistics render as compact blocks instead of
a table: day defaults to 30, month to 12, year lists everything; no totals
block inside the grid (a summary strip above carries the aggregates) and no
"账单" chip — only estimates are flagged. Recipients render as compact list
rows with an aligned enable switch on the right.

Served at both /ui and /ui/notify (the latter opens with the notify tab active).
"""
from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>电费监测</title>
<style>
/* Design tokens mirror the OpsCenter workbench (assets/index-v4-*.css :root) */
:root{--bg:#f4f6fa;--brand:#2563eb;--brand-soft:#eff6ff;--card:#ffffff;--text:#1f2937;
--muted:#6b7280;--border:#e5e9f0;--ok:#16a34a;--ok-bg:#f0fdf4;--warn:#d97706;
--warn-bg:#fff7ed;--err:#dc2626;--err-bg:#fef2f2;--radius:10px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14.5px/1.55 system-ui,-apple-system,"Microsoft YaHei",sans-serif}
.wrap{max-width:1020px;margin:0 auto;padding:0 18px 52px}

/* ---- 顶栏：品牌 + Tab（适配工作台风格） ---- */
.topbar{position:sticky;top:0;z-index:20;background:rgba(244,246,250,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--border)}
.topbar-in{max-width:1020px;margin:0 auto;padding:10px 18px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:8px;font-weight:700;font-size:15.5px;color:var(--text)}
.brand .dot{width:10px;height:10px;border-radius:3px;background:var(--brand)}
.tabs{display:inline-flex;background:#e8ecf3;border-radius:9px;padding:3px;gap:3px}
.tabs button{border:0;background:transparent;color:var(--muted);font:600 13.5px/1 system-ui;padding:8px 18px;border-radius:7px;cursor:pointer;transition:all .15s}
.tabs button.active{background:var(--brand);color:#fff;box-shadow:0 1px 6px #2563eb55}
.btn{font:600 13px/1 system-ui;background:var(--brand);color:#fff;border:0;border-radius:8px;padding:9px 15px;cursor:pointer}
.btn[disabled]{opacity:.55;cursor:default}
.btn.ghost{background:#fff;color:var(--brand);border:1px solid var(--border)}

/* ---- 主视图 ---- */
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:12px;margin:16px 0 4px}
.hcard{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;box-shadow:0 1px 4px #0f1b2d0a;transition:transform .15s}
.hcard:hover{transform:translateY(-1px)}
.hcard .label{font-size:12.5px;color:var(--muted);display:flex;justify-content:space-between;align-items:center;gap:6px}
.hcard .value{font-size:31px;font-weight:800;letter-spacing:-.4px;margin:6px 0 2px;font-variant-numeric:tabular-nums}
.hcard .value small{font-size:13.5px;font-weight:600;margin-left:2px;color:var(--muted)}
.hcard .note{font-size:12px;color:var(--muted)}
.hcard.primary{background:linear-gradient(135deg,#2563eb,#3b82f6);border:0;color:#fff}
.hcard.primary .label,.hcard.primary .value small,.hcard.primary .note{color:#dbeafe}
.aline{display:flex;align-items:center;gap:8px;border-radius:9px;padding:9px 14px;font-size:12.5px;margin:12px 0 2px}
.aline.ok{background:var(--ok-bg);border:1px solid #bbf7d0;color:#166534}
.aline.warn{background:var(--warn-bg);border:1px solid #fed7aa;color:#9a3412}
.aline.crit{background:var(--err-bg);border:1px solid #fecaca;color:var(--err)}
.aline .pulse{width:7px;height:7px;border-radius:50%;background:currentColor;opacity:.75;flex:none}
.aline b{font-weight:700}

section{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin-top:14px}
h2{margin:0;font-size:15px}
.sec-head{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.hint{margin:0;font-size:12px;color:var(--muted)}
.sumline{display:flex;gap:16px;flex-wrap:wrap;background:#f8fafc;border:1px solid var(--border);border-radius:9px;padding:8px 14px;font-size:12px;color:var(--muted);margin-bottom:10px}
.sumline b{color:var(--text);font-size:13.5px;font-variant-numeric:tabular-nums}
.seg{display:inline-flex;background:#e8ecf3;border-radius:8px;padding:3px;gap:3px}
.seg button{border:0;background:transparent;color:var(--muted);font:600 12.5px/1 system-ui;padding:7px 13px;border-radius:6px;cursor:pointer}
.seg button.active{background:#fff;color:var(--brand);box-shadow:0 1px 4px #0f1b2d1f}

/* ---- 统计小块 ---- */
.blocks{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:7px}
.blk{background:#fff;border:1px solid var(--border);border-radius:10px;padding:8px 10px;display:flex;flex-direction:column;gap:1px;transition:all .15s;animation:pop .22s ease both}
.blk:hover{transform:translateY(-2px);box-shadow:0 4px 12px #0f1b2d1a;border-color:#c7d2e3}
.blk.now{border:1.5px dashed var(--brand);background:var(--brand-soft)}
@keyframes pop{from{opacity:0;transform:scale(.96)}to{opacity:1;transform:scale(1)}}
.blk .d{font-size:10.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.blk .c{font-size:16.5px;font-weight:800;letter-spacing:-.3px;font-variant-numeric:tabular-nums}
.blk .c small{font-size:10.5px;font-weight:600;color:var(--muted);margin-left:1px}
.blk .k{font-size:10.5px;color:var(--muted)}
.blk .foot{display:flex;gap:4px;margin-top:3px;flex-wrap:wrap;min-height:15px}
.chip{font-size:9.5px;font-weight:700;padding:1px 6px;border-radius:10px;background:#eef2f7;color:#52606f}
.chip.est{background:var(--warn-bg);color:#b45309}
.chip.up{background:var(--err-bg);color:var(--err)}
.chip.down{background:var(--ok-bg);color:var(--ok)}
.chip.flat{background:#eef2f7;color:#6b7280}

/* ---- 通知记录 ---- */
.feed{display:grid;gap:6px}
.feed .item{border:1px solid var(--border);border-radius:9px;padding:8px 12px;display:flex;gap:10px;align-items:center;font-size:12.5px;background:#fbfcfe}
.feed .t{color:var(--muted);font-size:11.5px;min-width:112px;font-variant-numeric:tabular-nums;flex:none}
.feed .tag{font-size:10.5px;font-weight:700;padding:1px 8px;border-radius:10px;background:var(--brand-soft);color:var(--brand);flex:none}
.feed .tag.test{background:#eef2f7;color:var(--muted)}
.feed .tag.ok{background:var(--ok-bg);color:var(--ok)}
.feed .what{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.feed .ch{color:var(--muted);font-size:11.5px;flex:none}
.feed .ch.fail{color:var(--err)}

/* ---- 通知设置：通知人列表 ---- */
.persons{border:1px solid var(--border);border-radius:10px;overflow:hidden}
.row{display:flex;gap:12px;align-items:center;padding:8px 12px;border-bottom:1px solid #eef1f6;transition:background .12s;font-size:13px}
.row:last-child{border-bottom:0}
.row:hover{background:#f8fafc}
.row.off .name,.row.off .meta{opacity:.5}
.avatar{width:26px;height:26px;border-radius:7px;background:var(--brand-soft);color:var(--brand);display:grid;place-items:center;font-weight:700;font-size:12px;flex:none}
.row .name{font-weight:700;font-size:13px;display:flex;gap:6px;align-items:center;flex:none;white-space:nowrap}
.row .meta{flex:1;min-width:0;font-size:11.5px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.row .ops{display:flex;gap:4px;flex:none}
.row .ops button{font:600 11px/1 system-ui;border-radius:7px;padding:4px 0;width:38px;text-align:center;border:1px solid var(--border);background:#fff;color:var(--muted);cursor:pointer}
.row .ops button:hover{border-color:#c7d2e3;color:var(--text)}
.row .ops button.pri{background:var(--brand-soft);color:var(--brand);border-color:transparent}
.row .ops button.del{color:var(--err)}
.switch{position:relative;width:32px;height:18px;flex:none;align-self:center;margin-left:2px;display:inline-block}
.switch input{opacity:0;width:0;height:0;position:absolute}
.switch i{position:absolute;inset:0;background:#cbd5e1;border-radius:20px;transition:.2s;cursor:pointer}
.switch i:before{content:"";position:absolute;width:12px;height:12px;border-radius:50%;background:#fff;top:3px;left:3px;transition:.2s;box-shadow:0 1px 3px #0003}
.switch input:checked+i{background:var(--brand)}
.switch input:checked+i:before{transform:translateX(14px)}

/* ---- 表单卡 ---- */
.form-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 18px;margin-top:12px}
.form-card h2{margin-bottom:8px}
form{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));align-items:end}
label{font-size:12px;color:var(--muted);display:grid;gap:4px}
input,select{font:inherit;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:#fbfcfe;color:var(--text)}
.note{font-size:12px;color:var(--muted);margin:8px 0 0}
code{background:var(--brand-soft);border-radius:5px;padding:1px 5px;font-size:11.5px}
.state{margin-top:14px;font-size:12.5px;color:var(--muted);min-height:18px}
.state.err{color:var(--err)}
@media(max-width:680px){.row .meta{display:none}.hcard .value{font-size:26px}}
</style>
</head>
<body>
<div class="topbar"><div class="topbar-in">
  <div class="brand"><span class="dot"></span>电费监测</div>
  <div class="tabs" id="tabs">
    <button data-view="main" class="active">⚡ 电费监测</button>
    <button data-view="notify">🔔 通知设置</button>
  </div>
  <div><button class="btn" id="refresh">立即刷新</button></div>
</div></div>

<div class="wrap">

<!-- ======================= Tab 1：电费监测 ======================= -->
<div id="view-main">
  <div class="hero">
    <div class="hcard primary" id="card-balance"><div class="label">当前电费余额</div><div class="value">—</div><div class="note">正在加载…</div></div>
    <div class="hcard" id="card-month"><div class="label">本月花费</div><div class="value">—</div><div class="note">—</div></div>
    <div class="hcard" id="card-days"><div class="label">预计可用</div><div class="value">—</div><div class="note">—</div></div>
  </div>

  <div class="aline ok" id="alertline"><span class="pulse"></span>正在加载告警状态…</div>

  <section>
    <div class="sec-head">
      <h2>电费统计</h2>
      <span class="hint" id="stats-hint">花费按真实均价折算；仅估算值会标注。</span>
    </div>
    <div class="sumline" id="sumline"></div>
    <div class="sec-head" style="margin-bottom:10px">
      <span class="seg" id="seg-gran">
        <button data-gran="day" class="active">按天</button>
        <button data-gran="month">按月</button>
        <button data-gran="year">按年</button>
      </span>
      <span class="seg" id="seg-range"></span>
    </div>
    <div class="blocks" id="blocks"></div>
  </section>

  <section>
    <div class="sec-head"><h2>通知记录</h2><span class="hint">最近 10 次推送与测试</span></div>
    <div class="feed" id="history"></div>
  </section>

  <p class="state" id="state"></p>
</div>

<!-- ======================= Tab 2：通知设置 ======================= -->
<div id="view-notify" hidden>
  <section>
    <div class="sec-head"><h2>通知人</h2><span class="hint" id="targets-hint">加载中…</span></div>
    <div class="persons" id="targets"></div>
  </section>

  <div class="form-card">
    <h2 id="form-title">添加通知人</h2>
    <p class="note" style="margin:4px 0 12px">地址按方式填写：飞书 / 企微 / 钉钉 / Webhook 填机器人地址；Server酱 填 SendKey；PushPlus 填 token；邮箱填收件地址（需先在下方填 SMTP）；短信填手机号（走独立 module-sms 网关）；Bark / ntfy 填推送地址。</p>
    <form onsubmit="return false">
      <label>名称<input id="f-name" placeholder="如：秋水的微信"></label>
      <label>通知方式<select id="f-type"></select></label>
      <label>地址 / Key<input id="f-target" placeholder="按上方说明填写"></label>
      <label>接收级别<select id="f-sev">
        <option value="warning" selected>警告及以上（推荐）</option>
        <option value="critical">仅严重</option>
        <option value="info">全部（含提示）</option>
      </select></label>
      <div style="display:flex;gap:8px">
        <button class="btn" id="f-save">保存</button>
        <button class="btn ghost" id="f-reset" type="button">清空</button>
      </div>
    </form>
  </div>

  <div class="form-card">
    <h2>通道基础设施（可选）</h2>
    <p class="note" style="margin:4px 0 12px">邮箱通道需要 SMTP；短信通道对接独立的 module-sms 网关（能接收 <code>{"phone","message"}</code> JSON）。</p>
    <form onsubmit="return false">
      <label>SMTP 服务器<input id="s-smtp" placeholder="smtp.example.com"></label>
      <label>SMTP 端口<input id="s-port" placeholder="465"></label>
      <label>SMTP 用户名<input id="s-user" placeholder="user@example.com"></label>
      <label>SMTP 密码<input id="s-pass" type="password" placeholder="已保存则留空"></label>
      <label>发件人<input id="s-from" placeholder="电费监测 &lt;user@example.com&gt;"></label>
      <label>短信网关地址<input id="s-sms" placeholder="https://your-gateway/sms/send"></label>
      <label>短信签名<input id="s-sign" placeholder="选填"></label>
      <label>短信模板<input id="s-tpl" placeholder="选填"></label>
      <div><button class="btn" id="s-save">保存设置</button></div>
    </form>
  </div>
</div>

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
const RANGES = { day: [7, 14, 30], month: [3, 6, 12] };
const DEF = { day: 30, month: 12, year: 50 };   // 按天默认 30 天、按月默认 12 月、按年全部
const UNIT = { day: '日均', month: '月均', year: '年均' };
const ACTION = { firing: '首次告警', 're-fire': '重复提醒', resolved: '已恢复', test: '测试推送' };
const esc = (v) => String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(d);

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

/* ---- Tab 切换 ---- */
function selectView(view) {
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $('view-main').hidden = view !== 'main';
  $('view-notify').hidden = view !== 'notify';
  if (view === 'notify') loadNotify();
}
document.querySelectorAll('#tabs button').forEach((b) => b.onclick = () => selectView(b.dataset.view));
if (location.pathname.endsWith('/notify') || location.hash === '#notify') selectView('notify');

/* ---- 电费监测 ---- */
function renderOverview(data) {
  const item = (data.households || [])[0] || {};
  const bal = item.balance;
  const low = bal !== null && bal !== undefined && Number(bal) < 20;
  const days = data.remaining_days;
  const monthNote = data.monthly_budget
    ? `预算 ${num(data.monthly_budget)} 元 · 预计月末 ${num(data.month_cost_projection)} 元`
    : (data.month_cost_projection ? `按当前节奏预计月末 ${num(data.month_cost_projection)} 元` : '暂无可折算数据');
  const price = data.price_per_kwh ? ` · 单价 ${num(data.price_per_kwh, 4)} 元/度` : '';

  $('card-balance').className = 'hcard primary';
  $('card-balance').innerHTML =
    `<div class="label">当前电费余额 <span style="font-size:11px;background:#ffffff2e;border-radius:7px;padding:1px 8px">户号 …${esc(String(item.id || '').slice(-4)) || '----'}</span></div>` +
    `<div class="value">${num(bal)}<small> 元</small></div>` +
    `<div class="note">${item.due > 0 ? `应缴 ${num(item.due)} 元 · ` : ''}更新 ${esc((data.updated_at || '').replace('T', ' ').slice(5, 16))}${price}</div>`;
  $('card-month').innerHTML =
    `<div class="label">本月花费 ${data.month_cost_estimated ? '<span class="chip est">估算</span>' : ''}</div>` +
    `<div class="value">${num(data.month_cost)}<small> 元</small></div>` +
    `<div class="note">${esc(monthNote)}</div>`;
  const warnDays = days !== null && days !== undefined && Number(days) < 5;
  $('card-days').className = 'hcard' + (warnDays ? '' : '');
  $('card-days').innerHTML =
    `<div class="label">预计可用</div>` +
    `<div class="value">${num(days, 1)}<small> 天</small></div>` +
    `<div class="note">日均约 ${num(data.estimated_daily_cost)} 元 · 近 7 日均 ${num(data.average_7d_kwh, 1)} 度</div>`;
  setState(`采集：${(data.updated_at || '尚未采集').replace('T', ' ')}` +
    ((data.errors || []).length ? ` · 待重试：${(data.errors || []).join('；')}` : ''), (data.errors || []).length > 0);
}

function renderAlertLine(data) {
  const total = (data.counts && data.counts.total) || 0;
  const el = $('alertline');
  if (total > 0) {
    const items = data.alerts || [];
    const crit = (data.counts.critical || 0) > 0;
    el.className = 'aline ' + (crit ? 'crit' : 'warn');
    el.innerHTML = `<span class="pulse"></span><b>${total} 条告警生效</b>` +
      (items.length ? ` · ${esc(items.slice(0, 2).map((a) => a.title).join('；'))}${items.length > 2 ? ' 等' : ''}` : '');
  } else {
    el.className = 'aline ok';
    el.innerHTML = '<span class="pulse"></span>当前无告警 · 余额与采集状态正常';
  }
}

function renderStats(data) {
  const rows = (data.rows || []).slice().sort((a, b) => String(b.label).localeCompare(String(a.label)));
  const t = data.totals || {};
  $('stats-hint').textContent = `单价 ${num(data.price_per_kwh, 4)} 元/度（${data.price_source === 'year-average' ? '年度账单实算' : '配置值'}）；仅估算值标注。`;
  $('sumline').innerHTML =
    `<span>合计花费 <b>${num(t.cost)}</b> 元</span><span>合计用电 <b>${num(t.kwh, 1)}</b> 度</span>` +
    `<span>${UNIT[data.granularity] || '均'} <b>${num(t.avg)}</b> 元</span><span>共 <b>${rows.length}</b> 期</span>`;
  $('blocks').innerHTML = rows.length ? rows.map((row) => {
    const d = row.delta_pct;
    const dChip = (d === null || d === undefined) ? '' :
      `<span class="chip ${d > 5 ? 'up' : d < -5 ? 'down' : 'flat'}">${d > 0 ? '↑' : '↓'} ${Math.abs(d)}%</span>`;
    const est = row.estimated ? '<span class="chip est">估算</span>' : '';
    const now = row.source && String(row.source).includes('进行中') ? ' now' : '';
    return `<div class="blk${now}"><div class="d">${esc(row.label)}</div>` +
      `<div class="c">${num(row.cost)}<small>元</small></div>` +
      `<div class="k">${num(row.kwh, 1)} 度</div>` +
      `<div class="foot">${dChip}${est}</div></div>`;
  }).join('') : '<p class="hint">暂无数据</p>';
}

function renderHistory(rows) {
  $('history').innerHTML = rows.length ? rows.map((h) => {
    const failed = h.delivered === false;
    const tag = h.action === 'resolved' ? 'ok' : (h.action === 'test' ? 'test' : '');
    return `<div class="item"><span class="t">${esc((h.at || '').replace('T', ' '))}</span>` +
      `<span class="tag ${tag}">${esc(ACTION[h.action] || h.action || '')}</span>` +
      `<span class="what">${esc(h.title || h.alert_id || '')}</span>` +
      `<span class="ch ${failed ? 'fail' : ''}">${esc((h.channels || []).join(', ') || '—')}${failed ? ' · 失败' : ''}</span></div>`;
  }).join('') : '<p class="hint">暂无推送记录；到「通知设置」添加通知人后即可接收。</p>';
}

function renderRangeButtons() {
  const seg = $('seg-range');
  if (gran === 'year') { seg.style.display = 'none'; return; }
  seg.style.display = '';
  seg.innerHTML = RANGES[gran].map((value) =>
    `<button data-range="${value}" class="${value === range ? 'active' : ''}">近 ${value} ${gran === 'day' ? '天' : '月'}</button>`).join('');
}

async function loadStats() {
  try {
    renderStats(await request(`${API}/stats`, 'GET', undefined, { granularity: gran, limit: range }));
  } catch (e) { $('stats-hint').textContent = `统计接口不可用（${e.message}）`; }
}

function setState(text, error) {
  $('state').textContent = text;
  $('state').className = error ? 'state err' : 'state';
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
  try {
    const alerts = await request(`${API}/alerts`, 'GET', undefined, { history: 10 });
    renderHistory(alerts.history || []);
    renderAlertLine(alerts);
  } catch (e) { $('history').innerHTML = '<p class="hint">通知记录不可用</p>'; }
}

/* ---- 通知设置 ---- */
let editing = null;
const TYPE_LABEL = {};

function renderTargets(data) {
  const targets = data.targets || [];
  $('targets-hint').textContent = targets.length
    ? `共 ${targets.length} 位通知人；发送时会按「接收级别」过滤。`
    : '还没有通知人：在下方添加第一位即可开始接收告警。';
  $('targets').innerHTML = targets.length ? targets.map((t) => `
    <div class="row${t.enabled ? '' : ' off'}">
      <span class="avatar">${esc(String(t.name || '?').slice(0, 1))}</span>
      <span class="name">${esc(t.name)} <span class="chip">${esc(TYPE_LABEL[t.type] || t.type)}</span></span>
      <span class="meta">${esc(t.target_masked)} · ${esc(t.min_severity)}</span>
      <span class="ops">
        <button class="pri" data-test="${esc(t.id)}">测试</button>
        <button data-edit="${esc(t.id)}">编辑</button>
        <button class="del" data-del="${esc(t.id)}">删除</button>
      </span>
      <label class="switch" title="${t.enabled ? '点击停用' : '点击启用'}">
        <input type="checkbox" data-toggle="${esc(t.id)}" ${t.enabled ? 'checked' : ''}><i></i>
      </label>
    </div>`).join('') : '<p class="hint" style="padding:12px">暂无通知人</p>';
}

async function loadNotify() {
  if (!moduleReady) return;
  try {
    const data = await request(`${API}/notify/targets`);
    Object.assign(TYPE_LABEL, data.types || {});
    const select = $('f-type');
    if (select.options.length !== Object.keys(data.types || {}).length) {
      select.innerHTML = Object.entries(data.types || {}).map(([value, label]) =>
        `<option value="${esc(value)}">${esc(label)}</option>`).join('');
    }
    renderTargets(data);
    $('s-smtp').value = data.settings?.smtp_host || '';
    $('s-port').value = data.settings?.smtp_port || '';
    $('s-user').value = data.settings?.username || '';
    $('s-pass').value = data.settings?.password || '';
    $('s-from').value = data.settings?.mail_from || '';
    $('s-sms').value = data.settings?.sms_webhook || '';
    $('s-sign').value = data.settings?.sms_sign || '';
    $('s-tpl').value = data.settings?.sms_template || '';
  } catch (e) { setState(`通知设置加载失败：${e.message}`, true); }
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
    await loadNotify();
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
    await loadNotify();
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
    } else if (btn.dataset.del) {
      await request(`${API}/notify/targets/delete`, 'POST', { id: btn.dataset.del });
      setState('已删除');
    } else if (btn.dataset.edit) {
      const data = await request(`${API}/notify/targets`);
      const item = (data.targets || []).find((t) => t.id === btn.dataset.edit);
      if (!item) throw new Error('未找到该通知人');
      editing = item.id;
      $('f-name').value = item.name || '';
      $('f-type').value = item.type;
      $('f-target').value = item.target || '';
      $('f-sev').value = item.min_severity || 'warning';
      $('form-title').textContent = `编辑通知人：${item.name}`;
      selectView('notify');
    }
    await loadNotify();
  } catch (e) { setState(`操作失败：${e.message}`, true); btn.disabled = false; }
});

$('targets').addEventListener('change', async (event) => {
  const input = event.target.closest('input[data-toggle]');
  if (!input) return;
  input.disabled = true;
  try {
    await request(`${API}/notify/targets/toggle`, 'POST', { id: input.dataset.toggle });
    await loadNotify();
  } catch (e) { setState(`操作失败：${e.message}`, true); input.disabled = false; }
});

/* ---- 刷新与握手 ---- */
$('refresh').onclick = async () => {
  const btn = $('refresh'); btn.disabled = true; btn.textContent = '采集中…';
  try {
    const result = await request(`${API}/refresh`, 'POST');
    setState(result.triggered ? '已触发采集，稍后自动刷新…' : (result.message || '已有采集任务在进行'));
  } catch (e) { setState(`触发采集失败：${e.message}`, true); }
  setTimeout(async () => { btn.disabled = false; btn.textContent = '立即刷新'; await load(); }, 8000);
};

window.addEventListener('message', (event) => {
  if (event.source !== window.parent) return;
  const data = event.data || {};
  if (data.type === 'ops-module-info') {
    moduleReady = true;
    renderRangeButtons();
    load();
    loadNotify();
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
  loadNotify();
}
setInterval(load, 60000);
</script>
</body>
</html>
"""


def render_page() -> str:
    return PAGE
