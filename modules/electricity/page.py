"""Self-contained dashboard page served by the electricity module.

Sections: balance/forecast cards, prepaid-power warning, alert panel with
silence and test actions, tiered-tariff progress, time-of-use split, 90-day
trend + 7-day forecast, bills, notification history, and a diagnostics drawer
(raw payload capture + extra-API probe) used to verify upstream field shapes.
"""
from __future__ import annotations

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>电费监测</title>
<style>
:root{--ink:#16302a;--muted:#5f756d;--line:#dfeae4;--brand:#0b7c6b;--warn:#b24c18;--warn-bg:#fff5ed;--warn-line:#f1c39d;--crit:#b02a2a;--crit-bg:#fdf0f0;--crit-line:#eec4c4;--info:#2b5f9e;--info-bg:#f0f6fd;--info-line:#c6dcf3}
*{box-sizing:border-box}
body{margin:0;padding:22px 18px 56px;background:#f4f8f6;color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Microsoft YaHei",sans-serif}
.wrap{max-width:1040px;margin:0 auto}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:14px;margin-bottom:20px}
h1{margin:0;font-size:24px;letter-spacing:.01em}
.sub{margin:5px 0 0;color:var(--muted);font-size:13.5px}
.actions{display:flex;flex-wrap:wrap;gap:8px}
button{font:inherit;background:var(--brand);color:#fff;border:0;border-radius:9px;padding:9px 15px;cursor:pointer}
button[disabled]{opacity:.55;cursor:default}
button.ghost{background:#e6f1ed;color:#125b4d}
button.tiny{padding:4px 10px;font-size:12px;border-radius:7px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:13px}
.card,section{background:#fff;border:1px solid var(--line);border-radius:14px;box-shadow:0 3px 14px #14332c08}
.card{padding:17px}
.card .label{font-size:13px;color:var(--muted)}
.card .value{font-size:26px;font-weight:700;line-height:1.3;margin:7px 0 2px}
.card .value small{font-size:14px;font-weight:500}
.card .note{font-size:12.5px;color:#6d8078}
.card.warn{background:var(--warn-bg);border-color:var(--warn-line)}
.card.warn .value{color:var(--warn)}
section{padding:20px;margin-top:16px}
h2{margin:0 0 4px;font-size:17px}
.hint{margin:0 0 14px;font-size:12.5px;color:var(--muted)}
.banner{border-radius:12px;padding:12px 14px;margin-bottom:16px;font-size:13.5px;border:1px solid var(--warn-line);background:var(--warn-bg)}
.banner.crit{border-color:var(--crit-line);background:var(--crit-bg);color:#7d1f1f}
.days{display:grid;gap:6px}
.day{display:grid;grid-template-columns:100px 1fr 68px;align-items:center;gap:11px;font-size:13px}
.day strong{text-align:right;font-variant-numeric:tabular-nums}
.track{height:11px;background:#eaf3ef;border-radius:20px;overflow:hidden}
.fill{display:block;height:100%;border-radius:20px;background:linear-gradient(90deg,#66c3ac,#0b7c6b)}
.fill.warn{background:linear-gradient(90deg,#f0b27a,#b24c18)}
.fill.crit{background:linear-gradient(90deg,#e88b8b,#b02a2a)}
.table-wrap{overflow:auto}
table{border-collapse:collapse;width:100%}
th,td{padding:10px 12px;border-bottom:1px solid #edf3ef;text-align:left;font-size:14px}
th{color:var(--muted);font-weight:600;font-size:13px}
.state{margin-top:14px;font-size:13.5px;color:var(--muted)}
.state.err{color:var(--warn)}
.empty{margin:0;color:var(--muted);font-size:14px}
.alerts{display:grid;gap:10px}
.alert{border:1px solid var(--line);border-left-width:5px;border-radius:11px;padding:12px 14px;background:#fff}
.alert.critical{border-left-color:var(--crit);background:var(--crit-bg);border-color:var(--crit-line)}
.alert.warning{border-left-color:var(--warn);background:var(--warn-bg);border-color:var(--warn-line)}
.alert.info{border-left-color:var(--info);background:var(--info-bg);border-color:var(--info-line)}
.alert-head{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.alert-head b{font-size:14.5px}
.chip{font-size:11.5px;font-weight:700;padding:2px 8px;border-radius:20px;background:#e6ece9;color:#3e544c}
.chip.critical{background:var(--crit);color:#fff}
.chip.warning{background:var(--warn);color:#fff}
.chip.info{background:var(--info);color:#fff}
.chip.mute{background:#dfe4e2;color:#4a5a54}
.alert p{margin:6px 0 0;font-size:13.5px}
.alert-meta{margin-top:8px;font-size:12px;color:#6d8078;display:flex;flex-wrap:wrap;gap:12px;align-items:center}
.steps{display:grid;gap:12px}
.step-row{display:grid;grid-template-columns:112px 1fr 120px;align-items:center;gap:12px;font-size:13px}
.step-row b{font-weight:600}
.step-row .num{text-align:right;color:#4d6459;font-variant-numeric:tabular-nums}
.tou{display:grid;gap:8px}
.tou-row{display:grid;grid-template-columns:70px 1fr 130px;align-items:center;gap:10px;font-size:13px}
.split{display:flex;height:14px;border-radius:20px;overflow:hidden;background:#eaf3ef}
.split span{display:block;height:100%}
svg.trend{width:100%;height:170px;display:block}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--muted);margin-top:6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:middle;font-style:normal}
details.diag{margin-top:16px;font-size:13.5px}
details.diag summary{cursor:pointer;color:var(--brand);font-weight:600}
details.diag .body{margin-top:10px;display:grid;gap:10px}
textarea{width:100%;min-height:120px;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;border:1px solid var(--line);border-radius:9px;padding:10px;background:#fbfdfc;color:#26443b}
@media(max-width:820px){.card .value{font-size:23px}.step-row,.tou-row{grid-template-columns:90px 1fr 88px}}
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
      <button id="test">测试通知</button>
      <button class="ghost" id="silence-all">静默 12 小时</button>
      <button class="ghost" id="export">导出本月 CSV</button>
      <button id="refresh">立即刷新</button>
    </div>
  </header>

  <div id="prepaid"></div>

  <div class="cards" id="cards">
    <div class="card"><div class="label">当前余额</div><div class="value">—</div><div class="note">—</div></div>
    <div class="card"><div class="label">预计可用</div><div class="value">—</div><div class="note">—</div></div>
    <div class="card"><div class="label">最近已公布日电量</div><div class="value">—</div><div class="note">—</div></div>
    <div class="card"><div class="label">本月电费</div><div class="value">—</div><div class="note">—</div></div>
    <div class="card"><div class="label">近 7 个公布日日均</div><div class="value">—</div><div class="note">—</div></div>
  </div>

  <section>
    <h2>提醒告警</h2>
    <p class="hint" id="alert-hint">正在评估…</p>
    <div class="alerts" id="alerts"></div>
  </section>

  <section id="steps-section" hidden>
    <h2>阶梯电价</h2>
    <p class="hint" id="steps-hint">已用电量按阶梯累计，进入下一档后单价上升。</p>
    <div class="steps" id="steps"></div>
  </section>

  <section id="tou-section" hidden>
    <h2>分时用电</h2>
    <p class="hint" id="tou-hint">本月各时段用电量占比（尖/峰/平/谷）。</p>
    <div class="tou" id="tou"></div>
  </section>

  <section>
    <h2>用电趋势与预测</h2>
    <p class="hint" id="trend-hint">实线为近 90 日实际公布电量，虚线为未来 7 日预测。</p>
    <svg class="trend" id="trend" viewBox="0 0 900 170" preserveAspectRatio="none"></svg>
    <div class="legend">
      <span><i style="background:#0b7c6b"></i>实际日电量</span>
      <span><i style="background:#b24c18"></i>预测</span>
      <span id="trend-stats"></span>
    </div>
  </section>

  <section><h2>最近账单</h2><div class="table-wrap"><table><thead><tr><th>月份</th><th>用电量</th><th>电费</th></tr></thead><tbody id="bills"></tbody></table></div></section>

  <section><h2>通知记录</h2><p class="hint">最近 20 条告警推送与恢复记录。</p><div class="table-wrap"><table><thead><tr><th>时间</th><th>事件</th><th>告警</th><th>通道</th></tr></thead><tbody id="history"></tbody></table></div></section>

  <section>
    <h2>采集诊断</h2>
    <p class="hint">留档脱敏后的国网原始报文，用于核对字段是否变化；探针会调用模块平时不用的两个接口，判断阶梯/分时数据是否可取。</p>
    <details class="diag">
      <summary>展开诊断面板</summary>
      <div class="body">
        <div class="actions">
          <button class="ghost tiny" id="probe">运行接口探针</button>
          <button class="ghost tiny" id="reload-raw">刷新报文列表</button>
        </div>
        <div id="raw-list" class="hint">—</div>
        <textarea id="raw-view" readonly placeholder="点上方列表的文件名查看结构摘要…"></textarea>
      </div>
    </details>
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
const num = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '—' : Number(v).toFixed(d);
const monthLabel = (m) => (/^\\d{6}$/.test(String(m)) ? `${String(m).slice(0,4)}-${String(m).slice(4)}` : (m || '—'));
const esc = (v) => String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const SEV = { critical: '严重', warning: '警告', info: '提示' };
const ACTION = { firing: '首次告警', 're-fire': '重复提醒', resolved: '已恢复', test: '测试推送' };
const TOU_LABEL = { tip: '尖', peak: '峰', flat: '平', valley: '谷' };
const TOU_COLOR = { tip: '#b02a2a', peak: '#b24c18', flat: '#2b5f9e', valley: '#0b7c6b' };

function postMessageRequest(id, path, method, body, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('模块接口请求超时')); }, timeoutMs);
    pending.set(id, { resolve, reject, timer });
    window.parent.postMessage({ type: 'ops-module-api-request', id, path, method, body, timeoutMs }, '*');
  });
}

function request(path, method = 'GET', body) {
  if (!framed) {
    const token = localStorage.getItem('ops-access-token') || '';
    return fetch(path, {
      method,
      headers: Object.assign({}, token ? { Authorization: `Bearer ${token}` } : {}, body ? { 'Content-Type': 'application/json' } : {}),
      body: body ? JSON.stringify(body) : undefined,
    }).then(async (response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    });
  }
  return postMessageRequest(`electricity-${Date.now()}-${++requestId}`, path, method, body, 20000);
}

function download(path, filename) {
  if (!framed) {
    const token = localStorage.getItem('ops-access-token') || '';
    return fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((response) => response.blob())
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url; anchor.download = filename; anchor.click();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      });
  }
  const id = `electricity-dl-${Date.now()}-${++requestId}`;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('下载超时')); }, 60000);
    pending.set(id, { resolve, reject, timer });
    window.parent.postMessage({ type: 'ops-module-download-request', id, path, filename }, '*');
  });
}

function renderOverview(data) {
  const item = (data.households || [])[0] || {};
  const daily = item.daily || [];
  const last = daily[0] || {};
  const recent = daily.slice(0, 7);
  const avg = recent.length ? recent.reduce((s, r) => s + Number(r.kwh || 0), 0) / recent.length : null;
  const bal = item.balance;
  const low = bal !== null && bal !== undefined && Number(bal) < 20;
  const days = data.remaining_days;
  const forecast = data.forecast || {};

  $('identity').textContent = `${item.name || '未找到户号'} · 户号尾号 ${String(item.id || '').slice(-4) || '----'}`;

  const card = (label, value, unit, note, warn) =>
    `<div class="card${warn ? ' warn' : ''}"><div class="label">${label}</div>` +
    `<div class="value">${value}<small> ${unit}</small></div><div class="note">${note}</div></div>`;
  const budgetNote = data.monthly_budget
    ? `预算 ${num(data.monthly_budget)} 元 · 预计 ${num(data.month_cost_projection)} 元`
    : (data.month_cost_projection ? `预计月末 ${num(data.month_cost_projection)} 元` : '尚未产生当月账单');
  $('cards').innerHTML = [
    card('当前余额', num(bal), '元', item.due > 0 ? `应缴 ${num(item.due)} 元` : '以国网账单为准', low),
    card('预计可用', num(days, 1), '天',
      data.estimated_daily_cost ? `日均约 ${num(data.estimated_daily_cost)} 元` : '数据不足',
      days !== null && days !== undefined && Number(days) < 5),
    card('最近已公布日电量', num(last.kwh), '度', last.day || '暂无数据'),
    card('本月电费', num(data.month_cost), '元', budgetNote),
    card('近 7 个公布日日均', num(avg), '度',
      forecast.total_kwh ? `未来 7 日预计 ${num(forecast.total_kwh, 1)} 度` : `共 ${recent.length} 天`),
  ].join('');

  const arrears = Number(item.arrears || 0);
  $('prepaid').innerHTML = arrears > 0
    ? `<div class="banner crit"><b>⚠️ 存在待缴欠费 ${num(arrears)} 元。</b>若已签约智能交费，电费为预付费模式，欠费可能直接触发停电，请立即缴纳。</div>`
    : `<div class="banner">提示：若已签约国网「智能交费」，电费由后付费转为预付费，欠费即停电。本模块会在余额与欠费上主动告警。</div>`;

  $('bills').innerHTML = (item.bills || []).length ? item.bills.map((r) =>
    `<tr><td>${monthLabel(r.month)}</td><td>${num(r.kwh)} 度</td><td>${num(r.cost)} 元</td></tr>`).join('')
    : '<tr><td colspan="3">暂无账单</td></tr>';

  const errors = data.errors || [];
  $('state').className = errors.length ? 'state err' : 'state';
  $('state').textContent = `采集时间：${(data.updated_at || '尚未采集').replace('T', ' ')}` +
    (errors.length ? ` · 待重试：${errors.join('；')}` : ' · 自动采集与告警评估均已启用');

  renderTrend(data, { daily_history: daily });
}

function renderTrend(overview, usage) {
  const actual = (usage.daily_history || []).slice() || [];
  let history = (usage.daily_history || []).length ? usage.daily_history.slice() : [];
  if (!history.length) {
    history = (overview.households ? (overview.households[0] || {}).daily : []) || [];
  }
  const series = history.slice(0, 90).reverse().map((r) => ({ day: r.day, kwh: Number(r.kwh || 0), future: false }));
  const forecast = ((usage.forecast || overview.forecast || {}).daily_kwh || []).map((r) => ({
    day: r.day, kwh: Number(r.kwh || 0), future: true,
  }));
  const all = series.concat(forecast);
  const svg = $('trend');
  if (all.length < 2) {
    svg.innerHTML = '';
    $('trend-hint').textContent = '历史数据不足，暂无法绘制趋势（至少需要 2 天）。';
    return;
  }
  const width = 900, height = 170, padX = 8, padY = 14;
  const peak = Math.max(1, ...all.map((r) => r.kwh));
  const stepX = (width - padX * 2) / Math.max(all.length - 1, 1);
  const y = (value) => height - padY - (value / peak) * (height - padY * 2);
  const point = (index, value) => [padX + index * stepX, y(value)];
  const line = (rows, offset, color, dash) => {
    const points = rows.map((row, index) => point(index + offset, row.kwh).join(',')).join(' ');
    return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2"${dash ? ' stroke-dasharray="5 4"' : ''}/>`;
  };
  const gridLines = [0.25, 0.5, 0.75, 1].map((ratio) =>
    `<line x1="${padX}" y1="${y(peak * ratio)}" x2="${width - padX}" y2="${y(peak * ratio)}" stroke="#eaf3ef" stroke-width="1"/>`).join('');
  const boundary = series.length
    ? `<line x1="${point(series.length - 1, all[series.length - 1].kwh)[0]}" y1="${padY}" x2="${point(series.length - 1, all[series.length - 1].kwh)[0]}" y2="${height - padY}" stroke="#c9d8d2" stroke-width="1" stroke-dasharray="4 4"/>`
    : '';
  svg.innerHTML = gridLines + boundary +
    (series.length ? line(series, 0, '#0b7c6b', false) : '') +
    (forecast.length && series.length ? line(forecast, series.length - 1, '#b24c18', true) : '') +
    `<text x="${padX}" y="12" font-size="11" fill="#7d908a">峰值 ${peak.toFixed(1)} 度</text>`;

  const stats = usage.forecast || overview.forecast || {};
  $('trend-hint').textContent = `实线为近 ${series.length} 日实际公布电量，虚线为未来 ${(stats.daily_kwh || []).length} 日预测。`;
  $('trend-stats').textContent = stats.total_kwh
    ? `未来 7 日预计 ${num(stats.total_kwh, 1)} 度（方法：${stats.method}）`
    : '';
}

function renderUsage(usage) {
  const steps = usage.steps || [];
  if (steps.length) {
    $('steps-section').hidden = false;
    const current = usage.current_step || {};
    $('steps-hint').textContent = current.stage
      ? `当前处于第 ${current.stage} 阶梯${current.price ? `，单价 ${current.price} 元/度` : ''}；剩余额度为 0 即进入下一档。`
      : '已用电量按阶梯累计，进入下一档后单价上升。';
    $('steps').innerHTML = steps.map((step) => {
      const used = Number(step.used || 0);
      const remain = Number(step.remain || 0);
      const total = used + remain;
      const percent = total > 0 ? Math.min(100, Math.max(1, (used / total) * 100)) : (step.current ? 100 : 0);
      const tone = remain === 0 ? 'crit' : (remain < 100 ? 'warn' : '');
      return `<div class="step-row"><b>第 ${esc(step.stage)} 阶梯${step.current ? '（当前）' : ''}</b>` +
        `<span class="track"><span class="fill ${tone}" style="width:${percent.toFixed(1)}%"></span></span>` +
        `<span class="num">已用 ${num(used, 1)} / 剩余 ${num(remain, 1)} 度</span></div>`;
    }).join('');
  }
  const tou = usage.tou || {};
  const buckets = ['tip', 'peak', 'flat', 'valley'].filter((key) => typeof tou[key] === 'number');
  if (buckets.length >= 2) {
    $('tou-section').hidden = false;
    const total = buckets.reduce((sum, key) => sum + tou[key], 0);
    $('tou').innerHTML =
      `<div class="split">${buckets.map((key) =>
        `<span style="width:${(tou[key] / total * 100).toFixed(1)}%;background:${TOU_COLOR[key]}" title="${TOU_LABEL[key]} ${num(tou[key], 1)} 度"></span>`).join('')}</div>` +
      buckets.map((key) =>
        `<div class="tou-row"><span>${TOU_LABEL[key]}段</span><span class="track"><span class="fill" style="width:${(tou[key] / total * 100).toFixed(1)}%;background:${TOU_COLOR[key]}"></span></span>` +
        `<span class="num">${num(tou[key], 1)} 度 · ${(tou[key] / total * 100).toFixed(0)}%</span></div>`).join('');
    const tipPeak = (tou.tip || 0) + (tou.peak || 0);
    $('tou-hint').textContent = `本月统计 ${num(total, 1)} 度，尖+峰占比 ${(tipPeak / total * 100).toFixed(0)}%；` +
      '把可延后的电器放到 22:00 后（谷段）可降低电费。';
  }
}

function renderAlerts(payload) {
  const items = payload.items || [];
  const counts = payload.counts || {};
  const channelCount = (payload.channels || []).length;
  const silenceCount = Object.keys(payload.silences || {}).length;
  const brokenChannels = Object.entries(payload.channel_health || {})
    .filter(([, value]) => value && value.open_until).map(([key]) => key);
  $('alert-hint').textContent = items.length
    ? `共 ${counts.total || 0} 条（严重 ${counts.critical || 0} / 警告 ${counts.warning || 0} / 提示 ${counts.info || 0}）`
      + ` · 通知通道 ${channelCount} 个` + (silenceCount ? ` · 静默 ${silenceCount} 项` : '')
      + (brokenChannels.length ? ` · 熔断中：${brokenChannels.join(', ')}` : '')
    : (channelCount
      ? `当前没有告警 · 通知通道 ${channelCount} 个` + (silenceCount ? ` · 静默 ${silenceCount} 项` : '')
      : '当前没有告警 · 尚未配置通知通道（ELECTRICITY_NOTIFY_CHANNELS 为空，仅工作台内可见）');

  $('alerts').innerHTML = items.length ? items.map((a) => {
    const sev = a.severity || 'info';
    return `<div class="alert ${sev}">
      <div class="alert-head"><span class="chip ${sev}">${SEV[sev] || sev}</span><b>${esc(a.title)}</b>` +
      (a.silenced ? '<span class="chip mute">已静默</span>' : '') + `</div>
      <p>${esc(a.summary)}</p>
      <div class="alert-meta"><span>ID ${esc(a.id)}</span><span>首次触发 ${esc((a.first_seen || a.observed_at || '').replace('T',' '))}</span>` +
      (a.notify_count ? `<span>已推送 ${a.notify_count} 次</span>` : '<span>未推送</span>') +
      `<button class="ghost tiny" data-silence="${esc(a.id)}">静默 12h</button>` +
      (a.silenced ? `<button class="ghost tiny" data-unsilence="${esc(a.id)}">解除静默</button>` : '') +
      `</div></div>`;
  }).join('') : '<p class="empty">暂无告警，一切正常。</p>';

  const history = payload.history || [];
  $('history').innerHTML = history.length ? history.map((h) => {
    const failed = h.delivered === false;
    return `<tr><td>${esc((h.at || '').replace('T', ' '))}</td><td>${esc(ACTION[h.action] || h.action || '')}</td>` +
      `<td>${esc(h.title || h.alert_id || '')}</td>` +
      `<td class="${failed ? 'state err' : ''}">${esc((h.channels || []).join(', ') || '—')}` +
      (failed ? ' · 部分失败' : '') + `</td></tr>`;
  }).join('') : '<tr><td colspan="4">暂无推送记录</td></tr>';
}

function renderRaw(payload) {
  const files = payload.files || [];
  $('raw-list').textContent = payload.capture_enabled
    ? (files.length ? `已留档 ${files.length} 份报文（点击查看结构摘要）：` : '尚未留档任何报文，等待下一轮采集。')
    : '原始报文留档已关闭（设置 ELECTRICITY_RAW_CAPTURE=true 开启）。';
  if (!files.length) return;
  $('raw-list').innerHTML += files.map((file) =>
    `<button class="ghost tiny" data-raw="${esc(file.name)}">${esc(file.name)}</button>`).join(' ');
}

function notice(text) {
  $('cards').innerHTML = `<div class="card" style="grid-column:1/-1"><div class="label">${text}</div></div>`;
  $('alerts').innerHTML = ''; $('history').innerHTML = ''; $('bills').innerHTML = '';
  $('trend').innerHTML = ''; $('identity').textContent = '电费监测';
}

async function load() {
  if (!moduleReady) return;
  let overview;
  try {
    overview = await request(`${API}/overview`);
    renderOverview(overview);
  } catch (e) {
    notice(`模块暂时不可用（${e.message}）`);
    return;
  }
  try { renderAlerts(await request(`${API}/alerts`)); }
  catch (e) { $('alerts').innerHTML = `<p class="empty">告警接口不可用（${esc(e.message)}）</p>`; }
  try {
    const usage = await request(`${API}/usage`);
    renderUsage(usage);
    renderTrend(overview, usage);
  } catch (e) { $('trend-hint').textContent = `用量接口不可用（${e.message}）`; }
  try { renderRaw(await request(`${API}/diagnostics/raw`)); }
  catch (e) { $('raw-list').textContent = '诊断接口不可用'; }
}

$('refresh').onclick = async () => {
  if (!moduleReady) return;
  const btn = $('refresh'); btn.disabled = true; btn.textContent = '采集中…';
  try {
    const result = await request(`${API}/refresh`, 'POST');
    $('state').textContent = result.triggered ? '已触发采集，稍后自动刷新结果…' : (result.message || '已有采集任务在进行');
  } catch (e) { $('state').textContent = '触发采集失败'; }
  setTimeout(async () => { btn.disabled = false; btn.textContent = '立即刷新'; await load(); }, 8000);
};

$('test').onclick = async () => {
  if (!moduleReady) return;
  const btn = $('test'); btn.disabled = true; btn.textContent = '发送中…';
  try {
    const result = await request(`${API}/alerts/test`, 'POST');
    const failed = (result.results || []).filter((r) => !r.ok).length;
    $('alert-hint').textContent = `测试通知已发送 ${result.sent} 条` + (failed ? `，其中 ${failed} 条失败（见通知记录）` : '，请检查接收端');
  } catch (e) {
    $('alert-hint').textContent = `测试通知失败：${e.message}`;
  } finally {
    btn.disabled = false; btn.textContent = '测试通知';
    setTimeout(load, 1200);
  }
};

$('silence-all').onclick = async () => {
  if (!moduleReady) return;
  try {
    await request(`${API}/alerts/silence`, 'POST', { id: '*', hours: 12 });
    $('alert-hint').textContent = '已静默全部告警 12 小时（工作台内仍会显示）';
    setTimeout(load, 800);
  } catch (e) { $('alert-hint').textContent = `静默失败：${e.message}`; }
};

$('export').onclick = async () => {
  if (!moduleReady) return;
  const month = new Date().toISOString().slice(0, 7).replace('-', '');
  try { await download(`${API}/report.csv?month=${month}`, `electricity-${month}.csv`); }
  catch (e) { $('state').textContent = `导出失败：${e.message}`; }
};

$('probe').onclick = async () => {
  if (!moduleReady) return;
  const btn = $('probe'); btn.disabled = true; btn.textContent = '探测中…';
  try {
    const result = await request(`${API}/diagnostics/probe`, 'POST');
    $('raw-view').value = JSON.stringify(result, null, 2);
  } catch (e) {
    $('raw-view').value = `探针失败：${e.message}`;
  } finally {
    btn.disabled = false; btn.textContent = '运行接口探针';
    setTimeout(load, 500);
  }
};

$('reload-raw').onclick = async () => {
  try { renderRaw(await request(`${API}/diagnostics/raw`)); } catch (e) { /* ignore */ }
};

$('raw-list').addEventListener('click', async (event) => {
  const file = event.target.dataset && event.target.dataset.raw;
  if (!file) return;
  $('raw-view').value = '正在读取结构摘要…';
  try {
    const shape = await request(`${API}/diagnostics/raw/shape?name=${encodeURIComponent(file)}`);
    $('raw-view').value = JSON.stringify(shape.shape, null, 2);
  } catch (e) { $('raw-view').value = `读取失败：${e.message}`; }
});

$('alerts').addEventListener('click', async (event) => {
  const target = event.target.closest('button');
  if (!target) return;
  if (target.dataset.silence) {
    target.disabled = true;
    try { await request(`${API}/alerts/silence`, 'POST', { id: target.dataset.silence, hours: 12 }); }
    catch (e) { $('alert-hint').textContent = `静默失败：${e.message}`; }
    await load();
  } else if (target.dataset.unsilence) {
    target.disabled = true;
    try { await request(`${API}/alerts/unsilence`, 'POST', { id: target.dataset.unsilence }); }
    catch (e) { $('alert-hint').textContent = `解除失败：${e.message}`; }
    await load();
  }
});

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
