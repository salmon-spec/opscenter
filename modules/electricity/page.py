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
.wrap{max-width:1440px;margin:0 auto;padding:0 24px 44px}

/* ---- 顶栏：仿工作台 ModuleTabs（k8s 集群页同款切换） ---- */
.topbar{position:sticky;top:0;z-index:20;background:#fff;border-bottom:1px solid var(--border)}
.topbar-in{max-width:1440px;margin:0 auto;padding:6px 24px;display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.module-tabs{display:flex;align-items:center;gap:4px;overflow-x:auto}
.module-tab{height:31px;padding:0 14px;display:flex;align-items:center;border-radius:7px;color:var(--muted);font-size:13px;font-weight:600;background:transparent;border:0;cursor:pointer;white-space:nowrap;transition:color .14s,background .14s}
.module-tab:hover,.module-tab.active{color:var(--brand);background:#eff6ff}
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

/* ---- 按日统计：自然月日历 ---- */
.calendar-nav{display:flex;align-items:center;gap:10px;color:var(--text)}
.calendar-nav strong{min-width:88px;text-align:center;font-size:13px;font-variant-numeric:tabular-nums}
.calendar-nav button{width:29px;height:29px;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--brand);font-size:19px;line-height:1;cursor:pointer}
.calendar-nav button:disabled{color:#cbd5e1;cursor:default}
.calendar{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:7px}
.calendar-nav[hidden],.calendar[hidden],.blocks[hidden]{display:none}
.weekday{display:flex;justify-content:center;align-items:center;gap:5px;padding:5px 0 7px;color:var(--muted);font-size:11px;font-weight:700}
.week-icon{display:grid;place-items:center;width:22px;height:22px;border-radius:7px;background:var(--brand-soft);color:var(--brand);font-size:11px}
.weekday.weekend .week-icon{background:#fff7ed;color:#c2410c}
.day-cell{min-height:85px;border:1px solid var(--border);border-radius:9px;padding:7px 8px;background:#fff;font-variant-numeric:tabular-nums}
.day-cell.empty{border-color:transparent;background:transparent}
.day-cell.today{border-color:var(--brand);background:#f8fbff}
.day-cell.future{background:#f8fafc;color:#9ca3af}
.day-cell .date{font-size:12px;font-weight:800}
.day-cell .usage{margin-top:5px;font-size:15px;font-weight:800;color:var(--text)}
.day-cell .usage small{font-size:10px;font-weight:500;color:var(--muted)}
.day-cell .cost{font-size:10px;color:var(--muted)}
.day-cell .missing{margin-top:8px;font-size:10px;color:#9ca3af}

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
.grid2{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
label{font-size:12px;color:var(--muted);display:grid;gap:4px}
input,select,textarea{font:inherit;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:#fbfcfe;color:var(--text)}
.note{font-size:12px;color:var(--muted);margin:8px 0 0}
code{background:var(--brand-soft);border-radius:5px;padding:1px 5px;font-size:11.5px}
.state{margin-top:14px;font-size:12.5px;color:var(--muted);min-height:18px}
.state.err{color:var(--err)}
#alert-settings-form{display:block;margin:0}
#view-alerts{padding-top:16px}
.alert-layout{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.alert-card{min-width:0;background:#fff;border:1px solid var(--border);border-radius:12px;padding:18px}
.alert-card h3{font-size:14px;margin:0 0 4px}
.alert-fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:16px}
.alert-fields label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600;min-width:0}
.alert-fields .hint{font-weight:400;font-size:11px;line-height:1.5}
.alert-advanced{margin-top:14px}
.alert-advanced summary{cursor:pointer;font-weight:600;font-size:13px;color:var(--brand)}
#alert-advanced-fields{grid-template-columns:repeat(3,minmax(0,1fr))}
.alert-savebar{position:sticky;bottom:0;background:#fff;border:1px solid var(--border);border-radius:10px;margin:14px 0;padding:12px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px;z-index:10;box-shadow:0 -3px 12px #0f172a06}
.alert-savebar .state{margin:0}
.module-version{font-size:10px;color:var(--muted);margin-left:8px;white-space:nowrap}
@media(max-width:900px){.alert-layout{grid-template-columns:1fr}#alert-advanced-fields{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:520px){.alert-fields,#alert-advanced-fields{grid-template-columns:1fr}.alert-savebar{align-items:flex-start;flex-direction:column}.wrap{padding-left:12px;padding-right:12px}}
#view-alerts p>label{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
#view-alerts #alert-quiet{width:320px;max-width:100%}
#alert-settings-fields label{display:flex;flex-direction:column;gap:6px;font-size:13px;font-weight:600}
#view-alerts input:not([type=checkbox]){border:1px solid var(--border);border-radius:7px;padding:9px;font:inherit;width:100%}
#alert-settings-fields .hint{font-weight:400}
#alert-settings-active .row{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap}
@media(max-width:680px){.row .meta{display:none}.hcard .value{font-size:26px}.calendar{gap:3px}.day-cell{min-height:68px;padding:4px}.day-cell .usage{font-size:12px}.day-cell .cost{font-size:9px}.weekday{gap:2px;font-size:9px}.week-icon{width:17px;height:17px}}
</style>
</head>
<body>
<div class="topbar"><div class="topbar-in">
  <nav class="module-tabs" id="tabs" aria-label="模块导航">
    <button class="module-tab active" data-view="main">电费监测</button>
    <button class="module-tab" data-view="notify">通知设置</button>
    <button class="module-tab" data-view="alerts">告警阈值</button><span class="module-version">电量模块 v0.10.0</span>
  </nav>
  <div><button class="btn" id="refresh">立即刷新</button></div>
</div></div>

<div class="wrap" id="dashboard-wrap">

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
      <span class="hint" id="stats-hint">花费按真实均价折算。</span>
    </div>
    <div class="sumline" id="sumline"></div>
    <div class="sec-head" style="margin-bottom:10px">
      <span class="seg" id="seg-gran">
        <button data-gran="day" class="active">按天</button>
        <button data-gran="month">按月</button>
        <button data-gran="year">按年</button>
      </span>
      <span class="calendar-nav" id="calendar-nav">
        <button id="cal-prev" aria-label="上个月">‹</button><strong id="cal-title"></strong><button id="cal-next" aria-label="下个月">›</button>
        <input type="month" id="calendar-jump" aria-label="选择历史月份" style="max-width:155px;border:1px solid var(--border);border-radius:7px;padding:5px;color:var(--text)">
      </span>
    </div>
    <div class="calendar" id="calendar"></div>
    <div class="blocks" id="blocks"></div>
    <p class="hint" id="history-coverage" style="margin-top:12px"></p>
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
    <div class="sec-head"><h2>通知人</h2><button class="btn ghost" id="test-all">测试所有启用目标</button><span class="hint" id="targets-hint">加载中…</span></div>
    <div class="persons" id="targets"></div>
  </section>

  <div class="form-card">
    <h2 id="form-title">添加通知人</h2>
    <p class="note" style="margin:4px 0 12px">Apprise 短信填 <code>twilio://SID:TOKEN@发送号码/接收号码</code> 或 <code>httpsms://KEY@发送号码/接收号码</code>；微信填 <code>wecombot://机器人Key</code>、Server酱的 <code>schan://SendKey</code> 或 PushPlus / WxPusher 地址；邮件填 <code>mailtos://...</code>。地址含凭据，保存后只显示遮罩；原有飞书、邮箱等通道继续可用。</p>
    <form onsubmit="return false">
      <label>名称<input id="f-name" placeholder="如：秋水的微信"></label>
      <label>通知方式<select id="f-type"></select></label>
      <label>地址 / Key<textarea id="f-target" rows="2" placeholder="可粘贴多个收件人，每行一个；也可用逗号或分号分隔"></textarea></label>
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
    <div id="pushplus-fields" hidden style="margin-top:14px">
      <h2>PushPlus 发送选项</h2>
      <p class="note" style="margin:4px 0 12px">默认微信公众号渠道。好友消息需先添加好友并填写好友令牌，多个令牌用英文逗号分隔。Webhook、企业微信应用等需填写 PushPlus 后台渠道编码；好友令牌与群组编码不能同时填写。</p>
      <div class="grid2">
        <label>发送渠道<select id="f-push-channel">
          <option value="wechat">微信公众号</option><option value="app">PushPlus App</option><option value="extension">浏览器/桌面插件</option>
          <option value="webhook">Webhook</option><option value="clawbot">微信 ClawBot</option><option value="cmcc">新消息 ClawBot</option>
          <option value="qq">QQ 机器人</option><option value="cp">企业微信应用</option><option value="mail">PushPlus 邮箱</option>
          <option value="sms">PushPlus 短信</option><option value="voice">语音</option>
        </select></label>
        <label>渠道编码 / option<input id="f-push-option" placeholder="按 PushPlus 后台渠道配置填写"></label>
        <label>好友令牌 / to<input id="f-push-to" placeholder="多个令牌用英文逗号分隔"></label>
        <label>群组编码 / topic<input id="f-push-topic" placeholder="一对多群组编码"></label>
        <label>PushPlus 消息模板<select id="f-push-template"><option value="html">HTML</option><option value="markdown">Markdown</option><option value="txt">纯文本</option><option value="json">JSON</option></select></label>
      </div>
    </div>
    <p class="note">多个地址会分别保存为通知人，告警会发送给每个已启用且级别匹配的通知人；可单独测试，也可测试全部启用目标。</p>
  </div>

  <div class="form-card">
    <h2>消息格式</h2>
    <p class="hint">可用状态变量：{severity_label} 中文级别、{balance} 余额、{remaining_days} 可用天数、{policy} 提醒策略。时间统一为北京时间。</p>
    <p class="note" style="margin:4px 0 12px">可用变量：<code>{title}</code> 标题、<code>{summary}</code> 摘要、<code>{severity}</code> 级别、<code>{observed_at}</code> 时间、<code>{alert_id}</code> 编号、<code>{url}</code> 详情链接、<code>{action_label}</code> 动作、<code>{icon}</code> 图标。</p>
    <form onsubmit="return false">
      <label>消息标题<input id="s-title-template" placeholder="{icon} 电费监测｜{title}"></label>
      <label style="grid-column:1/-1">消息正文<textarea id="s-body-template" rows="5" placeholder="{title}\n{summary}\n触发时间：{observed_at}\n告警 ID：{alert_id}\n查看详情：{url}\n来源：OpsCenter 电费监测模块"></textarea></label>
      <div><button class="btn" id="template-save">保存消息格式</button></div>
    </form>
  </div>

  <div class="form-card">
    <h2>通道基础设施（可选）</h2>
    <p class="note" style="margin:4px 0 12px">以下设置只供原有邮箱 / 短信网关通道使用。Apprise 渠道在通知人地址中配置供应商；短信需要供应商账号或已配对的 Android 网关，微信需要机器人 Key / 推送 Token，邮件需要 SMTP 账号。</p>
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
<div id="view-alerts" class="wrap" hidden>
  <div class="sec-head"><div><h2>告警阈值</h2><p class="hint">按实际用电情况设置提醒，保存后持续生效</p></div></div>
  <form id="alert-settings-form">
    <div class="alert-layout">
      <div class="alert-card"><h3>余额与预算</h3><p class="hint">优先设置日常最关心的提醒条件</p><div class="alert-fields" id="alert-basic-fields"></div></div>
      <div class="alert-card"><h3>通知策略</h3><p class="hint">控制持续告警的提醒频率和次数</p><div class="alert-fields" id="alert-delivery-fields"></div>
      <p><label>免打扰时段（北京时间） <input id="alert-quiet" placeholder="23:00-07:00，留空关闭"></label></p>
      <p class="hint">严重告警仍会通知；达到次数上限后保留告警展示。</p>
      <p><label><input type="checkbox" id="alert-recovery"> 告警恢复时通知一次</label></p>
      </div>
    </div>
    <details class="alert-card alert-advanced"><summary>更多阈值 · 用电异常、采集与电价</summary><div class="alert-fields" id="alert-advanced-fields"></div></details>
    <div class="alert-savebar"><div><p class="state" id="alert-settings-state" role="status">修改后请保存</p><p class="hint" id="alert-schedule-hint"></p></div>
      <div class="actions"><button class="btn" id="alert-save" type="button" disabled>保存设置</button> <button class="btn ghost" id="alert-defaults" type="button" disabled>填入默认值</button></div>
    </div>
  </form>
  <section><div class="sec-head"><h2>当前告警与通知次数</h2><button class="btn ghost" id="alert-reload" type="button">刷新状态</button></div>
    <p class="hint">次数按每条持续告警的发送轮次累计，包含首次发送与失败尝试；每轮向符合级别的已启用收件人分发。恢复后再次触发重新计数。</p>
    <div id="alert-settings-active" style="margin-top:12px"></div>
  </section>
</div>
<script>
const API = '/modules/electricity/api/v1';
const $ = (id) => document.getElementById(id);
const framed = window.parent !== window;
const pending = new Map();
let requestId = 0;
let moduleReady = !framed;
let gran = 'day';
let calendarMonth = '';
let latestMonth = '';
let earliestMonth = '';
let statsRequestId = 0;
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
    }).then(async (r) => { const data = await r.json(); if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`); return data; });
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
  $('view-alerts').hidden = view !== 'alerts';
  $('dashboard-wrap').hidden = view === 'alerts';
  if (view === 'notify') loadNotify();
  if (view === 'alerts') loadAlertSettings();
}
document.querySelectorAll('#tabs button').forEach((b) => b.onclick = () => selectView(b.dataset.view));
if (location.pathname.endsWith('/notify') || location.hash === '#notify') selectView('notify');
if (location.hash === '#alerts') selectView('alerts');

let alertDocument;
const alertBasic = ['low_balance', 'critical_balance', 'depletion_days', 'monthly_budget'];
const alertDelivery = ['max_notifications', 'repeat_interval_seconds', 'pending_observations'];
const alertUnits = {stale_after_seconds: [3600, '数据过期时间（小时）'], repeat_interval_seconds: [60, '重复通知间隔（分钟）'], tou_tip_ratio: [0.01, '尖峰用电占比（%）']};
function alertFactor(key) { return alertUnits[key]?.[0] || 1; }
function alertField(f) {
  const factor = alertFactor(f.key), label = alertUnits[f.key]?.[1] || f.label;
  let help = f.help;
  if (f.key === 'tou_tip_ratio') help = '超过此比例提醒，例如 50 表示 50%';
  if (f.key === 'max_notifications') help = '包含首次，0 为不限；失败也计次，恢复后重新计数';
  return `<label>${esc(label)}<input id="alert-field-${f.key}" type="number" min="${f.min/factor}" max="${f.max/factor}" step="${f.step === 1 && factor === 1 ? 1 : 'any'}" required><span class="hint">${esc(help)}</span></label>`;
}
function fillAlertSettings(settings) {
  for (const f of alertDocument.fields) $('alert-field-' + f.key).value = Number((settings[f.key] / alertFactor(f.key)).toFixed(6));
  $('alert-quiet').value = settings.quiet_hours || '';
  $('alert-recovery').checked = settings.notify_recovery;
}
async function loadAlertSettings() {
  const state = $('alert-settings-state');
  try {
    alertDocument = await request(`${API}/alerts/settings`);
    $('alert-basic-fields').innerHTML = alertDocument.fields.filter(f => alertBasic.includes(f.key)).map(alertField).join('');
    $('alert-delivery-fields').innerHTML = alertDocument.fields.filter(f => alertDelivery.includes(f.key)).sort((a,b)=>alertDelivery.indexOf(a.key)-alertDelivery.indexOf(b.key)).map(alertField).join('');
    $('alert-advanced-fields').innerHTML = alertDocument.fields.filter(f => !alertBasic.includes(f.key) && !alertDelivery.includes(f.key)).map(alertField).join('');
    fillAlertSettings(alertDocument.settings);
    $('alert-save').disabled = false;
    $('alert-defaults').disabled = false;
    $('alert-schedule-hint').textContent = `每 ${alertDocument.evaluation_interval_minutes} 分钟及采集完成后评估一次。通知达到上限后仍保留告警展示。默认值填入后需保存。`;
    await loadAlertStatus();
  } catch (e) { state.textContent = `加载失败：${e.message}`; state.className = 'state err'; }
}
async function loadAlertStatus() {
  try {
    const data = await request(`${API}/alerts`);
    const limit = alertDocument?.settings.max_notifications || 0;
    $('alert-settings-active').innerHTML = data.items.length ? data.items.map(a => `<div class="row"><div><b>${esc(a.title)}</b><div class="hint">${esc(a.summary)}</div></div><span>${a.notify_count} / ${limit || '不限'} 次 · ${esc(a.status)}${limit && a.notify_count >= limit ? ' · 已达上限' : ''}</span></div>`).join('') : '<p class="hint">当前没有告警</p>';
  } catch (e) { $('alert-settings-active').textContent = `状态加载失败：${e.message}`; }
}
$('alert-defaults').onclick = () => { fillAlertSettings(alertDocument.defaults); $('alert-settings-state').textContent = '已填入默认值，保存后生效'; };
$('alert-reload').onclick = loadAlertStatus;
$('alert-save').onclick = async (event) => {
  event.preventDefault();
  if (!$('alert-settings-form').reportValidity()) return;
  const button = $('alert-save'), state = $('alert-settings-state');
  button.disabled = true;
  try {
    const payload = Object.fromEntries(alertDocument.fields.map(f => {const value = Number($('alert-field-' + f.key).value) * alertFactor(f.key); return [f.key, f.step === 1 ? Math.round(value) : Number(value.toFixed(8))];}));
    payload.quiet_hours = $('alert-quiet').value.trim();
    payload.notify_recovery = $('alert-recovery').checked;
    alertDocument = await request(`${API}/alerts/settings`, 'POST', payload);
    fillAlertSettings(alertDocument.settings);
    state.className = 'state'; state.textContent = '设置已保存，下一次评估生效';
    await loadAlertStatus();
  } catch (e) { state.className = 'state err'; state.textContent = `保存失败：${e.message}`; }
  finally { button.disabled = false; }
};

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
  if (!calendarMonth && /^[0-9]{6}$/.test(data.month_label || '')) {
    calendarMonth = `${data.month_label.slice(0, 4)}-${data.month_label.slice(4)}`;
    latestMonth = calendarMonth;
  }

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
    `<div class="note">${data.estimate_source === 'kwh-price' ? `按近 7 个用电日均 ${num(data.average_7d_kwh, 1)} 度 × ${num(data.price_per_kwh, 4)} 元/度` :
      data.estimate_source === 'tiered-kwh-price' ? '按近 7 个用电日与分档单价估算' :
      data.estimate_source === 'measured' ? '缺少用电量，按余额变动估算' : '暂无足够用电数据'} · 约 ${num(data.estimated_daily_cost)} 元/天</div>`;
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
  const range = data.history_range || {};
  $('history-coverage').textContent = rows.length ? `全部已保存记录：${range.from} 至 ${range.to}，共 ${rows.length} ${data.granularity === 'day' ? '天' : data.granularity === 'month' ? '个月' : '年'}。已采集日/月数据长期保留；更早未取得的数据不补零，估算不等于正式账单。` : '尚无已保存历史，后续采集会自动累计归档。';
  if (data.granularity === 'day') earliestMonth = range.from?.slice(0,7) || '';
  $('stats-hint').textContent = `单价 ${num(data.price_per_kwh, 4)} 元/度（${data.price_source === 'year-average' ? '年度账单实算' : '配置值'}）。`;
  $('calendar').hidden = data.granularity !== 'day';
  $('calendar-nav').hidden = data.granularity !== 'day';
  $('blocks').hidden = data.granularity === 'day';
  if (data.granularity === 'day') {
    renderCalendar(rows);
    return;
  }
  const t = data.totals || {};
  $('sumline').innerHTML =
    `<span>合计花费 <b>${num(t.cost)}</b> 元</span><span>合计用电 <b>${num(t.kwh, 1)}</b> 度</span>` +
    `<span>${UNIT[data.granularity] || '均'} <b>${num(t.avg)}</b> 元</span><span>共 <b>${rows.length}</b> 期</span>`;
  $('blocks').innerHTML = rows.length ? rows.map((row) => {
    const d = row.delta_pct;
    const dChip = (d === null || d === undefined) ? '' :
      `<span class="chip ${d > 5 ? 'up' : d < -5 ? 'down' : 'flat'}">${d > 0 ? '↑' : '↓'} ${Math.abs(d)}%</span>`;
    const now = row.source && String(row.source).includes('进行中') ? ' now' : '';
    return `<div class="blk${now}"><div class="d">${esc(row.label)}</div>` +
      `<div class="c">${num(row.kwh, 1)}<small>度</small></div>` +
      `<div class="k">${num(row.cost)} 元</div>` +
      `<div class="foot">${row.estimated ? '<span class="chip est">估算</span>' : ''}${now ? '<span class="chip">进行中</span>' : ''}${dChip}</div></div>`;
  }).join('') : '<p class="hint">暂无数据</p>';
}

function renderCalendar(rows) {
  if (!calendarMonth) calendarMonth = new Date().toISOString().slice(0, 7);
  if (!latestMonth) latestMonth = calendarMonth;
  const [year, month] = calendarMonth.split('-').map(Number);
  const firstWeekday = (new Date(Date.UTC(year, month - 1, 1)).getUTCDay() + 6) % 7;
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const byDay = new Map(rows.map((row) => [row.label, row]));
  const currentRows = rows.filter((row) => row.label.startsWith(calendarMonth));
  const kwh = currentRows.reduce((sum, row) => sum + (Number(row.kwh) || 0), 0);
  const cost = currentRows.reduce((sum, row) => sum + (Number(row.cost) || 0), 0);
  $('sumline').innerHTML = `<span>已公布 <b>${currentRows.length}</b> 天</span><span>用电 <b>${num(kwh, 1)}</b> 度</span>` +
    `<span>折算花费 <b>${num(cost)}</b> 元</span><span>日均 <b>${currentRows.length ? num(cost / currentRows.length) : '—'}</b> 元</span>`;
  $('cal-title').textContent = `${year} 年 ${month} 月`;
  $('calendar-jump').value = calendarMonth;
  $('calendar-jump').min = earliestMonth;
  $('calendar-jump').max = latestMonth;
  $('cal-prev').disabled = Boolean(earliestMonth && calendarMonth <= earliestMonth);
  $('cal-next').disabled = calendarMonth >= latestMonth;
  const weekdays = ['一', '二', '三', '四', '五', '六', '日'];
  const header = weekdays.map((label, index) => `<div class="weekday${index > 4 ? ' weekend' : ''}"><span class="week-icon">${label}</span>周${label}</div>`).join('');
  const blanks = '<div class="day-cell empty" aria-hidden="true"></div>'.repeat(firstWeekday);
  const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Shanghai' });
  const cells = Array.from({ length: days }, (_, index) => {
    const day = `${calendarMonth}-${String(index + 1).padStart(2, '0')}`;
    const row = byDay.get(day);
    const future = day > today;
    return `<div class="day-cell${day === today ? ' today' : ''}${future ? ' future' : ''}">` +
      `<div class="date">${index + 1}</div>` +
      (row ? `<div class="usage">${num(row.kwh, 1)}<small> 度</small></div><div class="cost">约 ${num(row.cost)} 元</div>` :
        `<div class="missing">${future ? '待公布' : '暂无数据'}</div>`) + '</div>';
  }).join('');
  $('calendar').innerHTML = header + blanks + cells;
}

function renderHistory(rows) {
  $('history').innerHTML = rows.length ? rows.map((h) => {
    const failed = h.delivered === false;
    const tag = h.action === 'resolved' ? 'ok' : (h.action === 'test' ? 'test' : '');
    const when = h.at ? new Date(h.at).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai', hour12:false}) : '—';
    return `<div class="item"><span class="t">${esc(when)}</span>` +
      `<span class="tag ${tag}">${esc(ACTION[h.action] || h.action || '')}</span>` +
      `<span class="what">${esc(h.title || h.alert_id || '')}</span>` +
      `<span class="ch ${failed ? 'fail' : ''}">${esc((h.channels || []).join(', ') || '—')}${failed ? ' · 失败' : ''}</span></div>`;
  }).join('') : '<p class="hint">暂无推送记录；到「通知设置」添加通知人后即可接收。</p>';
}

document.querySelectorAll('#seg-gran button').forEach((button) => button.addEventListener('click', () => {
  gran = button.dataset.gran;
  document.querySelectorAll('#seg-gran button').forEach((item) => item.classList.toggle('active', item === button));
  loadStats();
}));
$('cal-prev').onclick = () => {
  if (earliestMonth && calendarMonth <= earliestMonth) return;
  const [year, month] = calendarMonth.split('-').map(Number);
  calendarMonth = new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 7);
  loadStats();
};
$('cal-next').onclick = () => {
  if (calendarMonth >= latestMonth) return;
  const [year, month] = calendarMonth.split('-').map(Number);
  calendarMonth = new Date(Date.UTC(year, month, 1)).toISOString().slice(0, 7);
  loadStats();
};

async function loadStats() {
  const current = ++statsRequestId;
  try {
    const data = await request(`${API}/stats`, 'GET', undefined,
      { granularity: gran, limit: 0 });
    if (current === statsRequestId) renderStats(data);
  } catch (e) { if (current === statsRequestId) $('stats-hint').textContent = `统计接口不可用（${e.message}）`; }
}
$('calendar-jump').onchange = (event) => {
  const value = event.target.value;
  if (!value || (earliestMonth && value < earliestMonth) || value > latestMonth) return;
  calendarMonth = value;
  loadStats();
};

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
const TARGET_HINT = {
  apprise_sms: 'twilio://SID:TOKEN@发送号码/接收号码',
  apprise_wechat: 'wecombot://机器人Key 或 schan://SendKey',
  apprise_email: 'mailtos://邮件服务地址?user=...&pass=...&to=...',
};

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
    if (!editing) $('f-target').placeholder = TARGET_HINT[select.value] || '按上方说明填写';
    renderTargets(data);
    $('s-smtp').value = data.settings?.smtp_host || '';
    $('s-port').value = data.settings?.smtp_port || '';
    $('s-user').value = data.settings?.username || '';
    $('s-pass').value = data.settings?.password || '';
    $('s-from').value = data.settings?.mail_from || '';
    $('s-title-template').value = data.settings?.title_template || data.message_defaults?.title_template || '';
    $('s-body-template').value = data.settings?.body_template || data.message_defaults?.body_template || '';
    $('s-sms').value = data.settings?.sms_webhook || '';
    $('s-sign').value = data.settings?.sms_sign || '';
    $('s-tpl').value = data.settings?.sms_template || '';
  } catch (e) { setState(`通知设置加载失败：${e.message}`, true); }
}

$('f-save').onclick = async () => {
  const kind = $('f-type').value;
  const raw = $('f-target').value.trim();
  const addresses = editing || kind.startsWith('apprise_') ? [raw] : raw.split(/[\\n,;，；]+/).map(value => value.trim()).filter(Boolean);
  const payload = {
    id: editing || '',
    name: $('f-name').value.trim(),
    type: kind,
    min_severity: $('f-sev').value,
    extra: kind === 'wechat_pushplus' ? {
      pushplus_channel: $('f-push-channel').value,
      pushplus_option: $('f-push-option').value.trim(),
      pushplus_to: $('f-push-to').value.trim(),
      pushplus_topic: $('f-push-topic').value.trim(),
      pushplus_template: $('f-push-template').value,
    } : {},
  };
  if (!addresses.length) return setState('地址不能为空', true);
  let savedCount = 0;
  try {
    for (const [index, address] of addresses.entries()) {
      await request(`${API}/notify/targets`, 'POST', {
        ...payload, id: editing || '', target: address,
        name: addresses.length > 1 ? `${payload.name || '通知人'} ${index + 1}` : payload.name,
      });
      savedCount++;
    }
    setState(editing ? '通知人已更新' : `已保存 ${addresses.length} 个通知人`);
    editing = null; $('f-name').value = ''; $('f-target').value = '';
    $('f-target').placeholder = TARGET_HINT[$('f-type').value] || '按上方说明填写';
    $('form-title').textContent = '添加通知人';
    $('pushplus-fields').hidden = true;
    await loadNotify();
  } catch (e) { setState(`${savedCount ? `已保存 ${savedCount} 个；` : ''}保存失败：${e.message}`, true); }
};

$('f-reset').onclick = () => {
  editing = null; $('f-name').value = ''; $('f-target').value = '';
  $('f-target').placeholder = TARGET_HINT[$('f-type').value] || '按上方说明填写';
  $('form-title').textContent = '添加通知人';
  $('pushplus-fields').hidden = true;
  $('f-push-option').value = ''; $('f-push-to').value = ''; $('f-push-topic').value = '';
  $('f-push-channel').value = 'wechat'; $('f-push-template').value = 'html';
};

$('f-type').onchange = () => {
  $('pushplus-fields').hidden = $('f-type').value !== 'wechat_pushplus';
  $('f-target').placeholder = editing ? '切换方式时需填写新地址' :
    (TARGET_HINT[$('f-type').value] || '按上方说明填写');
};

$('template-save').onclick = async () => {
  try {
    await request(`${API}/notify/settings`, 'POST', {
      title_template: $('s-title-template').value,
      body_template: $('s-body-template').value,
    });
    setState('消息格式已保存');
  } catch (e) { setState(`保存失败：${e.message}`, true); }
};

$('test-all').onclick = async (event) => {
  const button = event.currentTarget; button.disabled = true;
  try {
    const result = await request(`${API}/notify/test`, 'POST', {});
    const failed = (result.results || []).filter((item) => !item.ok).length;
    setState(`已测试 ${result.sent} 个目标` + (failed ? `，${failed} 个失败` : '，请查收'));
  } catch (e) { setState(`测试失败：${e.message}`, true); }
  finally { button.disabled = false; }
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
      $('f-target').value = '';
      $('f-target').placeholder = `留空保留原地址（${item.target_masked || '已保存'}）`;
      $('f-sev').value = item.min_severity || 'warning';
      $('pushplus-fields').hidden = item.type !== 'wechat_pushplus';
      $('f-push-channel').value = item.extra?.pushplus_channel || 'wechat';
      $('f-push-option').value = item.extra?.pushplus_option || '';
      $('f-push-to').value = item.extra?.pushplus_to || '';
      $('f-push-topic').value = item.extra?.pushplus_topic || '';
      $('f-push-template').value = item.extra?.pushplus_template || 'html';
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
    load();
    loadNotify();
    if (!$('view-alerts').hidden) loadAlertSettings();
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
  loadNotify();
}
setInterval(load, 60000);
</script>
</body>
</html>
"""


def render_page() -> str:
    return PAGE
