#!/usr/bin/env python3
"""OpsCenter v2.4 → v2.5 运维工作台改版脚本
在服务器上执行: python3 /opt/opscenter/frontend/upgrade_v25.py
"""
import os, re, shutil, datetime

BASE = '/opt/opscenter/frontend'
INDEX = os.path.join(BASE, 'index.html')
APP_JS = os.path.join(BASE, 'assets/js/app.js')
CONFIG_JS = os.path.join(BASE, 'assets/js/config.js')
TOOLS_JS = os.path.join(BASE, 'assets/js/tools.js')

def backup(path):
    bak = path + '.bak.v2.4'
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
        print(f'  backed up → {bak}')

def read(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def write(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

def upgrade_index():
    """index.html: 所有模板+样式改动"""
    print('\n=== Upgrading index.html ===')
    backup(INDEX)
    html = read(INDEX)

    # --- P0-1: Fix CPU icon #fa-microphone → #fa-microchip ---
    old = '<svg class="icon w-3 text-[10px]" style="color:#22c55e"><use href="#fa-microphone"></use></svg>'
    new = '<svg class="icon w-3 text-[10px]" style="color:#22c55e"><use href="#fa-microchip"></use></svg>'
    if old in html:
        html = html.replace(old, new)
        print('  P0-1: Fixed CPU icon microphone→microchip')
    else:
        print('  P0-1: WARNING - microphone icon not found, trying broader match')
        html = html.replace('href="#fa-microphone"', 'href="#fa-microchip"')
        print('  P0-1: Applied broader replace')

    # --- Title update ---
    html = html.replace('<title>OpsCenter v2.4</title>', '<title>运维工作台 v2.5</title>')

    # --- Add new SVG symbols: fa-bell, fa-clipboard, fa-arrow-right, fa-exclamation-triangle ---
    new_symbols = '''
<symbol id="fa-bell" viewBox="0 0 448 512"><path d="M224 0c-17.7 0-32 14.3-32 32V49.9C119.5 63.4 64 124 64 198v44l-28 56a32 32 0 0 0 28.6 46H383.4a32 32 0 0 0 28.6-46L384 242v-44c0-74-55.5-134.6-128-148.1V32c0-17.7-14.3-32-32-32zm0 512a48 48 0 0 0 48-48H176a48 48 0 0 0 48 48z"/></symbol>
<symbol id="fa-clipboard" viewBox="0 0 384 512"><path d="M280 64h40c35.3 0 64 28.7 64 64V448c0 35.3-28.7 64-64 64H64c-35.3 0-64-28.7-64-64V128C0 92.7 28.7 64 64 64h40C104 28.7 132.7 0 168 0h48c35.3 0 64 28.7 64 64zM168 16c-13.3 0-24 10.7-24 24v16h96V40c0-13.3-10.7-24-24-24h-48zM64 80c-17.7 0-32 14.3-32 32V448c0 17.7 14.3 32 32 32H320c17.7 0 32-14.3 32-32V112c0-17.7-14.3-32-32-32H280v24c0 22.1-17.9 40-40 40H144c-22.1 0-40-17.9-40-40V80H64zm96 200v-48c0-8.8 7.2-16 16-16h32c8.8 0 16 7.2 16 16v48h48c8.8 0 16 7.2 16 16v32c0 8.8-7.2 16-16 16h-48v48c0 8.8-7.2 16-16 16h-32c-8.8 0-16-7.2-16-16v-48h-48c-8.8 0-16-7.2-16-16v-32c0-8.8 7.2-16 16-16h48z"/></symbol>
<symbol id="fa-arrow-right" viewBox="0 0 448 512"><path d="M438.6 278.6c12.5-12.5 12.5-32.8 0-45.3l-160-160c-12.5-12.5-32.8-12.5-45.3 0s-12.5 32.8 0 45.3L338.8 224 32 224c-17.7 0-32 14.3-32 32s14.3 32 32 32l306.7 0L233.4 393.4c-12.5 12.5-12.5 32.8 0 45.3s32.8 12.5 45.3 0l160-160z"/></symbol>
<symbol id="fa-triangle-exclamation" viewBox="0 0 512 512"><path d="M256 32L10.4 457.5C3.3 470 12.3 485 26.5 485h459c14.2 0 23.2-15 16.1-27.5L256 32zm0 56l190 361H66L256 88zm-16 120v112c0 8.8 7.2 16 16 16s16-7.2 16-16V208c0-8.8-7.2-16-16-16s-16 7.2-16 16zm16 176a24 24 0 1 0 0 48 24 24 0 1 0 0-48z"/></symbol>
<symbol id="fa-shield" viewBox="0 0 512 512"><path d="M256 0L38.6 108.5c-7.2 3.6-11.6 11-11.4 19.1 2.1 65.4 20.6 139.5 56.7 204.3C122.4 401.2 178 455.4 256 512c78-56.6 133.6-110.8 172.1-180.1 36.1-64.8 54.6-138.9 56.7-204.3.2-8.1-4.2-15.5-11.4-19.1L256 0zm0 33.5l193 96.5c-2.4 55.3-18.8 118.1-51.2 176.2C363.6 367.8 315.3 416.4 256 466.1c-59.3-49.7-107.6-98.3-141.8-159.9-32.4-58.1-48.8-120.9-51.2-176.2l193-96.5z"/></symbol>'''
    # Insert before closing </svg> of sprite
    sprite_end = '</svg>\n<div id="app">'
    html = html.replace(sprite_end, new_symbols + '\n' + sprite_end)
    print('  Added SVG symbols: fa-bell, fa-clipboard, fa-arrow-right, fa-triangle-exclamation, fa-shield')

    # --- CSS additions ---
    new_css = '''
/* v2.5: Alert bar, overview, drawer, severity colors */
.alert-bar { background: rgba(239,68,68,.12); border-left: 3px solid #ef4444; border-radius: 8px; padding: 10px 16px; margin-bottom: 16px; display: flex; align-items: center; gap: 10px; }
.alert-bar .alert-text { color: #ef4444; font-size: 13px; font-weight: 600; flex: 1; }
.alert-bar .alert-link { color: #fca5a5; font-size: 12px; cursor: pointer; white-space: nowrap; }
.alert-bar .alert-link:hover { color: #fff; }
.overview-card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 12px 16px; display: flex; align-items: center; gap: 12px; }
.overview-card .ov-icon { width: 36px; height: 36px; border-radius: 10px; display: flex; align-items: center; justify-content: center; font-size: 16px; flex-shrink: 0; }
.overview-card .ov-value { font-size: 20px; font-weight: 700; line-height: 1; }
.overview-card .ov-label { font-size: 11px; color: var(--text2); margin-top: 2px; }
.overview-card.alert { border-color: rgba(239,68,68,.3); }
.severity-warn { color: #f59e0b !important; }
.severity-crit { color: #ef4444 !important; }
.severity-warn-border { border-color: rgba(245,158,11,.5) !important; }
.severity-crit-border { border-color: rgba(239,68,68,.5) !important; }
/* Drawer */
.drawer-overlay { position: fixed; inset: 0; z-index: 60; background: rgba(0,0,0,.5); display: flex; justify-content: flex-end; animation: fadeIn .2s ease; }
.drawer-panel { width: 380px; max-width: 90vw; background: var(--card); border-left: 1px solid var(--border); padding: 24px; overflow-y: auto; animation: slideInRight .25s ease; }
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
/* Server card action buttons */
.server-actions { display: flex; gap: 6px; margin-top: 8px; }
.server-actions button { flex: 1; padding: 4px 8px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg); color: var(--text2); font-size: 11px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 4px; transition: all .15s; }
.server-actions button:hover { color: var(--accent); border-color: var(--accent); }
/* Monitor error state */
.monitor-error { text-align: center; padding: 40px 20px; color: var(--text2); }
.monitor-error svg { display: block; margin: 0 auto 12px; }
/* Metric severity pulse */
.pulse-warn { animation: pulseWarn 2s infinite; }
@keyframes pulseWarn { 0%,100% { opacity: 1; } 50% { opacity: .6; } }
/* Command templates */
.cmd-group { margin-bottom: 12px; }
.cmd-group-title { font-size: 11px; color: var(--text2); margin-bottom: 6px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; }
.cmd-list { display: flex; flex-wrap: wrap; gap: 6px; }
.cmd-btn { padding: 5px 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--bg); color: var(--text1); font-size: 12px; font-family: 'Cascadia Code','Fira Code',Consolas,monospace; cursor: pointer; transition: all .15s; }
.cmd-btn:hover { border-color: var(--accent); color: var(--accent); }
/* Alert bell badge */
.bell-badge { position: absolute; top: -4px; right: -4px; min-width: 16px; height: 16px; border-radius: 8px; background: #ef4444; color: #fff; font-size: 10px; font-weight: 700; display: flex; align-items: center; justify-content: center; padding: 0 4px; }
'''
    # Insert before </style>
    style_end = '</style>\n</head>'
    html = html.replace(style_end, new_css + style_end)
    print('  Added CSS: alert-bar, overview-card, drawer, severity, server-actions, cmd-templates, bell-badge')

    # --- P0-4 + P1-1: Insert alert bar + overview bar at top of nav page ---
    # Find the nav page content area and insert before "Recent Access"
    # The nav page starts with: <!-- Recent Access -->
    # We want to insert alert bar + overview bar before it

    alert_and_overview = '''
        <!-- v2.5: Alert Bar (P0-4) -->
        <div v-if="offlineServices.length" class="alert-bar mb-5">
          <svg class="icon" style="color:#ef4444"><use href="#fa-triangle-exclamation"></use></svg>
          <span class="alert-text">{{ offlineServices.length }} 个服务异常（{{ offlineServices.filter(s=>s.status==='offline').length }} 离线, {{ offlineServices.filter(s=>s.status==='unknown').length }} 未知）</span>
          <span class="alert-link" @click="showAlertList=!showAlertList">{{ showAlertList ? '收起' : '查看详情 →' }}</span>
        </div>
        <!-- Alert list expandable -->
        <div v-if="offlineServices.length && showAlertList" class="mb-5 rounded-xl border overflow-hidden" style="background:var(--card);border-color:rgba(239,68,68,.3)">
          <div v-for="svc in offlineServices" :key="svc.id" @click="openDrawer(svc)"
               class="px-4 py-2.5 flex items-center gap-3 cursor-pointer hover:bg-red-500/5 border-b" style="border-color:var(--border)">
            <svg class="icon text-sm" :style="{color: svc.icon_color||'var(--accent)'}"><use :href="iconHref(svc.icon||'fa-cube')"></use></svg>
            <span class="text-sm font-medium flex-1" style="color:var(--text1)">{{ svc.name }}</span>
            <span class="text-xs px-2 py-0.5 rounded-full" :style="{background: svc.status==='offline'?'rgba(239,68,68,.15)':'rgba(245,158,11,.15)', color: svc.status==='offline'?'#ef4444':'#f59e0b'}">{{ svc.status==='offline'?'离线':'未知' }}</span>
          </div>
        </div>

        <!-- v2.5: Overview Cards (P1-1) -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
          <div v-for="ov in overviewCards" :key="ov.label" class="overview-card" :class="{'alert': ov.alert}">
            <div class="ov-icon" :style="{background: (ov.color||'var(--accent)')+'18'}">
              <svg class="icon" :style="{color: ov.color||'var(--accent)'}"><use :href="iconHref(ov.icon)"></use></svg>
            </div>
            <div>
              <div class="ov-value" :style="{color: ov.alert?'#ef4444':'var(--text1)'}">{{ ov.value }}</div>
              <div class="ov-label">{{ ov.label }}</div>
            </div>
          </div>
        </div>
'''

    # Insert before "Recent Access" section
    recent_marker = '        <!-- Recent Access -->'
    if recent_marker in html:
        html = html.replace(recent_marker, alert_and_overview + recent_marker)
        print('  P0-4 + P1-1: Inserted alert bar + overview cards')
    else:
        print('  WARNING: Could not find Recent Access marker, trying alternative')
        # Try to find the v-if for nav page and insert after the div
        nav_marker = '<div v-if="currentPage===\'nav\'">'
        if nav_marker in html:
            html = html.replace(nav_marker, nav_marker + '\n' + alert_and_overview)
            print('  P0-4 + P1-1: Inserted alert bar + overview cards (alternative)')

    # --- P1-2: Server card action buttons ---
    # Find the server card template and add action buttons after the metrics
    # The server card ends with the container count line, we need to add buttons after it
    # Look for: {{ serverMetrics[s.id].container_count }} 容器
    server_card_end = '''                    {{ serverMetrics[s.id].container_count }} 容器
                  </span>
                </div>
              </div>
            </div>'''
    server_card_new = '''                    {{ serverMetrics[s.id].container_count }} 容器
                  </span>
                </div>
              </div>
              <!-- v2.5: Quick actions -->
              <div class="server-actions">
                <button @click.stop="currentPage='monitor';currentServerId=s.id;onServerChange();nextTick(()=>{loadMonitor();loadHistory()})"><svg class="icon text-xs"><use href="#fa-chart-line"></use></svg> 监控</button>
                <button @click.stop="currentPage='tools';currentServerId=s.id;onServerChange()"><svg class="icon text-xs"><use href="#fa-terminal"></use></svg> 终端</button>
              </div>
            </div>'''
    if server_card_end in html:
        html = html.replace(server_card_end, server_card_new)
        print('  P1-2: Added server card action buttons')
    else:
        print('  WARNING: Could not find server card end marker')

    # --- P1-3: Service card copy button + P1-4: click opens drawer ---
    # Category mode service card: add copy button next to pin button, change click to openDrawer
    # The category mode card is the main one (v-else section)
    cat_card_click = '''@click="openService(svc)"
                     :title="svc.url"
                     class="rounded-xl p-4 border card-hover cursor-pointer relative group"
                     style="background:var(--card);border-color:var(--border)">'''
    cat_card_new = '''@click="openDrawer(svc)"
                     :title="svc.url"
                     class="rounded-xl p-4 border card-hover cursor-pointer relative group"
                     style="background:var(--card);border-color:var(--border)">'''
    count = html.count(cat_card_click)
    html = html.replace(cat_card_click, cat_card_new)
    print(f'  P1-4: Changed {count} service card clicks to openDrawer')

    # Add copy button in category mode cards (after the pin button)
    # Find pin button in category cards and add copy next to it
    pin_in_cat = '''                  <button @click.stop="togglePin(svc)"
                          class="absolute top-2 right-2 w-6 h-6 rounded flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                          :style="{color: svc.pinned?'#f59e0b':'var(--text2)', background:'var(--bg)'}">
                    <svg class="icon" :style="{transform:svc.pinned?'none':'rotate(45deg)'}"><use href="#fa-thumbtack"></use></svg>
                  </button>'''
    # There are two instances (search mode + category mode), replace both
    pin_with_copy = '''                  <button @click.stop="togglePin(svc)"
                          class="absolute top-2 right-2 w-6 h-6 rounded flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                          :style="{color: svc.pinned?'#f59e0b':'var(--text2)', background:'var(--bg)'}">
                    <svg class="icon" :style="{transform:svc.pinned?'none':'rotate(45deg)'}"><use href="#fa-thumbtack"></use></svg>
                  </button>
                  <button @click.stop="copyServiceInfo(svc)"
                          class="absolute top-2 right-9 w-6 h-6 rounded flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                          style="color:var(--text2);background:var(--bg)" title="复制信息">
                    <svg class="icon"><use href="#fa-clipboard"></use></svg>
                  </button>'''
    count = html.count(pin_in_cat)
    html = html.replace(pin_in_cat, pin_with_copy)
    print(f'  P1-3: Added copy button to {count} service cards')

    # Also update search mode card click to openDrawer
    search_card_click = '''@click="openService(svc)"
               @mouseenter="searchSelectedIndex=idx"
               :title="svc.url"'''
    search_card_new = '''@click="openDrawer(svc)"
               @mouseenter="searchSelectedIndex=idx"
               :title="svc.url"'''
    html = html.replace(search_card_click, search_card_new)
    print('  P1-4: Changed search card click to openDrawer')

    # --- P1-4: Add drawer component before confirm modal ---
    drawer_html = '''
  <!-- v2.5: Service Detail Drawer (P1-4) -->
  <div v-if="serviceDrawer.show" class="drawer-overlay" @click="serviceDrawer.show=false">
    <div class="drawer-panel" @click.stop>
      <div class="flex items-center justify-between mb-5">
        <h3 class="text-base font-bold" style="color:var(--text1)">{{ serviceDrawer.service?.name }}</h3>
        <button @click="serviceDrawer.show=false" class="w-8 h-8 rounded-lg flex items-center justify-center" style="color:var(--text2);background:var(--bg)">
          <svg class="icon"><use href="#fa-circle-xmark"></use></svg>
        </button>
      </div>
      <div class="space-y-4" v-if="serviceDrawer.service">
        <!-- Status -->
        <div class="flex items-center gap-2">
          <span class="w-3 h-3 rounded-full" :class="statusClass(serviceDrawer.service.status)"></span>
          <span class="text-sm font-medium" :class="serviceDrawer.service.status==='online'?'text-emerald-500':serviceDrawer.service.status==='offline'?'text-red-500':'text-amber-500'">
            {{ serviceDrawer.service.status==='online'?'在线':serviceDrawer.service.status==='offline'?'离线':'未知' }}
          </span>
        </div>
        <!-- Category -->
        <div v-if="serviceDrawer.service.category" class="flex items-center gap-2">
          <span class="text-xs" style="color:var(--text2)">分类</span>
          <span class="text-xs px-2 py-0.5 rounded-full" :style="{background: (getCategoryColor(serviceDrawer.service.category)||'#10b981')+'18', color: getCategoryColor(serviceDrawer.service.category)||'#10b981'}">{{ serviceDrawer.service.category }}</span>
        </div>
        <!-- URL -->
        <div>
          <span class="text-xs block mb-1" style="color:var(--text2)">访问地址</span>
          <a :href="serviceDrawer.service.url" target="_blank" class="text-sm break-all hover:underline" style="color:var(--accent)">{{ serviceDrawer.service.url }}</a>
        </div>
        <!-- Description -->
        <div v-if="serviceDrawer.service.description">
          <span class="text-xs block mb-1" style="color:var(--text2)">描述</span>
          <p class="text-sm" style="color:var(--text1)">{{ serviceDrawer.service.description }}</p>
        </div>
        <!-- Actions -->
        <div class="pt-3 border-t space-y-2" style="border-color:var(--border)">
          <button @click="openService(serviceDrawer.service);serviceDrawer.show=false"
                  class="w-full px-4 py-2.5 rounded-lg text-sm font-medium bg-emerald-500 text-white hover:bg-emerald-600 transition flex items-center justify-center gap-2">
            <svg class="icon"><use href="#fa-arrow-right"></use></svg> 打开服务
          </button>
          <div class="flex gap-2">
            <button @click="copyServiceInfo(serviceDrawer.service)"
                    class="flex-1 px-4 py-2 rounded-lg text-xs font-medium border hover:bg-opacity-80 transition flex items-center justify-center gap-2"
                    style="background:var(--bg);color:var(--text1);border-color:var(--border)">
              <svg class="icon"><use href="#fa-clipboard"></use></svg> 复制信息
            </button>
            <button @click="togglePin(serviceDrawer.service)"
                    class="flex-1 px-4 py-2 rounded-lg text-xs font-medium border hover:bg-opacity-80 transition flex items-center justify-center gap-2"
                    :style="{background:'var(--bg)', color: serviceDrawer.service.pinned?'#f59e0b':'var(--text1)', borderColor:'var(--border)'}">
              <svg class="icon" :style="{transform:serviceDrawer.service.pinned?'none':'rotate(45deg)'}"><use href="#fa-thumbtack"></use></svg>
              {{ serviceDrawer.service.pinned?'取消置顶':'置顶' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>

'''
    confirm_marker = '  <!-- Confirm Modal -->'
    if confirm_marker in html:
        html = html.replace(confirm_marker, drawer_html + confirm_marker)
        print('  P1-4: Added service detail drawer')

    # --- P0-3: Monitor empty/error state ---
    # Replace the empty state div to show more helpful error message
    old_empty = '''          <div v-if="!monitor.connected && !loading.monitor" class="text-center py-16" style="color:var(--text2)">
            <svg class="icon text-4xl mb-3 block opacity-30 mx-auto"><use href="#fa-chart-line"></use></svg>
            <p class="text-sm mb-3">暂无监控数据，请检查服务器连接</p>
            <button @click="loadMonitor" class="px-4 py-2 rounded-lg text-xs font-medium bg-emerald-500 text-white hover:bg-emerald-600 transition">重新加载</button>
          </div>'''
    new_empty = '''          <div v-if="!monitor.connected && !loading.monitor" class="monitor-error">
            <svg class="icon text-4xl opacity-30" style="color:var(--text2)"><use href="#fa-triangle-exclamation"></use></svg>
            <p class="text-sm mb-1 font-semibold" style="color:var(--text1)">监控数据加载失败</p>
            <p class="text-xs mb-4" style="color:var(--text2)">请检查后端服务（端口 9091）是否正常运行</p>
            <button @click="loadMonitor" class="px-4 py-2 rounded-lg text-xs font-medium bg-emerald-500 text-white hover:bg-emerald-600 transition">重新加载</button>
          </div>'''
    if old_empty in html:
        html = html.replace(old_empty, new_empty)
        print('  P0-3: Improved monitor empty state')

    # --- P1-5: Metric cards severity styling ---
    # Replace metric card to support severity
    old_metric_card = '''              <div class="text-2xl font-bold" :style="{color:m.color}">{{ m.value }}{{ m.unit }}</div>
              <div class="mt-2 h-1.5 rounded-full overflow-hidden" style="background:var(--border)">
                <div class="h-full rounded-full transition-all duration-500" :style="{width: Math.min(m.value,100)+'%', background:m.color}"></div>
              </div>'''
    new_metric_card = '''              <div class="text-2xl font-bold" :class="getMetricClass(m.key, m.value)" :style="{color: getMetricColor(m.key, m.value) || m.color}">{{ m.value }}{{ m.unit }}</div>
              <div class="mt-2 h-1.5 rounded-full overflow-hidden" style="background:var(--border)">
                <div class="h-full rounded-full transition-all duration-500" :class="{'pulse-warn': isMetricCritical(m.key, m.value)}" :style="{width: Math.min(m.value,100)+'%', background: getMetricColor(m.key, m.value) || m.color}"></div>
              </div>'''
    if old_metric_card in html:
        html = html.replace(old_metric_card, new_metric_card)
        print('  P1-5: Added metric severity styling')

    # --- P1-6: Sidebar rename + alert bell ---
    # Change "OpsCenter" brand to "运维工作台"
    html = html.replace('>OpsCenter<', '>运维工作台<')
    print('  P1-6: Renamed brand to 运维工作台')

    # Add alert bell in header, before theme toggle
    theme_toggle_marker = '''      <!-- Theme Toggle -->'''
    bell_html = '''      <!-- v2.5: Alert Bell -->
      <div class="relative">
        <button @click="showAlertList=!showAlertList"
                class="w-9 h-9 rounded-lg flex items-center justify-center border"
                style="background:var(--card);border-color:var(--border);color:var(--text2)"
                title="异常服务">
          <svg class="icon"><use href="#fa-bell"></use></svg>
          <span v-if="offlineServices.length" class="bell-badge">{{ offlineServices.length }}</span>
        </button>
        <!-- Alert dropdown -->
        <div v-if="offlineServices.length && showAlertList" class="absolute right-0 top-11 w-72 rounded-xl border shadow-xl z-50 overflow-hidden" style="background:var(--card);border-color:var(--border)">
          <div class="px-4 py-2.5 border-b text-xs font-semibold" style="border-color:var(--border);color:var(--text2)">异常服务</div>
          <div v-for="svc in offlineServices" :key="svc.id" @click="openDrawer(svc);showAlertList=false"
               class="px-4 py-2.5 flex items-center gap-2 cursor-pointer hover:bg-emerald-500/5 border-b" style="border-color:var(--border)">
            <span class="w-2 h-2 rounded-full" :class="statusClass(svc.status)"></span>
            <span class="text-sm flex-1" style="color:var(--text1)">{{ svc.name }}</span>
            <span class="text-xs" :class="svc.status==='offline'?'text-red-400':'text-amber-400'">{{ svc.status==='offline'?'离线':'未知' }}</span>
          </div>
        </div>
      </div>

'''
    if theme_toggle_marker in html:
        html = html.replace(theme_toggle_marker, bell_html + theme_toggle_marker)
        print('  P1-6: Added alert bell in header')

    # --- Quick entry cards text update ---
    html = html.replace('>系统监控<', '>监控中心<')
    html = html.replace('>设置管理<', '>资源管理<')
    html = html.replace('>服务器/服务/主题<', '>服务器/服务/外观<')
    print('  Updated quick entry card labels')

    # --- Search placeholder update ---
    html = html.replace('搜索服务... (Ctrl+K, ↑↓导航)', '搜索服务... 支持 status:offline cat:分类 (Ctrl+K)')
    print('  Updated search placeholder')

    # --- P2-3: Settings page tab split ---
    # Replace settings page with tabbed layout
    old_settings_open = '''      <!-- ===================== 设置 ===================== -->
      <div v-if="currentPage==='settings'">
        <h2 class="text-lg font-semibold flex items-center gap-2 mb-5" style="color:var(--text1)">
          <svg class="icon" style="color:var(--accent)"><use href="#fa-cog"></use></svg> 设置
        </h2>'''
    new_settings_open = '''      <!-- ===================== 资源管理 ===================== -->
      <div v-if="currentPage==='settings'">
        <h2 class="text-lg font-semibold flex items-center gap-2 mb-4" style="color:var(--text1)">
          <svg class="icon" style="color:var(--accent)"><use href="#fa-cog"></use></svg> 资源管理
        </h2>
        <!-- Tabs -->
        <div class="flex gap-1 mb-5">
          <button v-for="tab in [{k:'servers',l:'服务器管理',i:'fa-server'},{k:'services',l:'服务注册',i:'fa-cube'},{k:'scan',l:'服务扫描',i:'fa-satellite-dish'},{k:'appearance',l:'外观设置',i:'fa-sun'}]" :key="tab.k"
                  @click="settingsTab=tab.k"
                  :class="['px-3 py-2 rounded-lg text-xs font-medium transition', settingsTab===tab.k?'bg-emerald-500 text-white':'']"
                  :style="settingsTab!==tab.k?'background:var(--card);color:var(--text2);border:1px solid var(--border)':''">
            <svg class="icon mr-1"><use :href="iconHref(tab.i)"></use></svg>{{ tab.l }}
          </button>
        </div>'''
    if old_settings_open in html:
        html = html.replace(old_settings_open, new_settings_open)
        print('  P2-3: Added settings tabs')

    # Wrap settings sections with v-if="settingsTab==='...'"
    # Server management section
    html = html.replace(
        '        <!-- Server Management -->\n        <div class="rounded-xl border mb-5"',
        '        <!-- Server Management -->\n        <div v-if="settingsTab===\'servers\'" class="rounded-xl border mb-5"'
    )
    # Manual service registration
    html = html.replace(
        '        <!-- Manual Service Registration -->\n        <div class="rounded-xl border mb-5"',
        '        <!-- Manual Service Registration -->\n        <div v-if="settingsTab===\'services\'" class="rounded-xl border mb-5"'
    )
    # Scan section
    html = html.replace(
        '        <!-- Scan -->\n        <div class="rounded-xl border mb-5"',
        '        <!-- Scan -->\n        <div v-if="settingsTab===\'scan\'" class="rounded-xl border mb-5"'
    )
    print('  P2-3: Wrapped settings sections with tab conditions')

    # Add appearance tab content before closing settings div
    appearance_tab = '''        <!-- Appearance Settings -->
        <div v-if="settingsTab==='appearance'" class="space-y-4">
          <div class="rounded-xl border p-5" style="background:var(--card);border-color:var(--border)">
            <h3 class="text-sm font-semibold mb-4" style="color:var(--text1)">主题</h3>
            <div class="flex gap-3">
              <button @click="isDark=true;applyTheme()" :class="['flex-1 p-4 rounded-xl border text-center', isDark?'border-emerald-500':'']" style="background:var(--bg);border-color:var(--border)">
                <svg class="icon text-2xl mb-2 block mx-auto" style="color:#f59e0b"><use href="#fa-moon"></use></svg>
                <span class="text-xs" style="color:var(--text1)">深色模式</span>
              </button>
              <button @click="isDark=false;applyTheme()" :class="['flex-1 p-4 rounded-xl border text-center', !isDark?'border-emerald-500':'']" style="background:var(--bg);border-color:var(--border)">
                <svg class="icon text-2xl mb-2 block mx-auto" style="color:#3b82f6"><use href="#fa-sun"></use></svg>
                <span class="text-xs" style="color:var(--text1)">浅色模式</span>
              </button>
            </div>
          </div>
          <div class="rounded-xl border p-5" style="background:var(--card);border-color:var(--border)">
            <h3 class="text-sm font-semibold mb-3" style="color:var(--text1)">侧边栏</h3>
            <label class="flex items-center gap-3 cursor-pointer" style="color:var(--text1)">
              <input type="checkbox" :checked="sidebarCollapsed" @change="sidebarCollapsed=!sidebarCollapsed" class="accent-emerald-500">
              <span class="text-sm">默认折叠侧边栏</span>
            </label>
          </div>
        </div>
'''
    # Insert before the closing </div> of settings page
    settings_close = '      </div>\n    </div>\n  </main>'
    # This is tricky because there are multiple </div> - we need to find the right one
    # The scan section ends right before the main closing tags
    # Find the scan section's last div and insert appearance tab after it
    scan_section_end = '            <span class="text-xs" style="color:var(--text2)">扫描将自动发现服务器上的运行服务</span>\n          </div>\n        </div>'
    if scan_section_end in html:
        html = html.replace(scan_section_end, scan_section_end + '\n' + appearance_tab)
        print('  P2-3: Added appearance tab content')

    # --- Stats cards only show on servers tab ---
    html = html.replace(
        "        <!-- Stats -->\n        <div class=\"grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6\">",
        "        <!-- Stats -->\n        <div v-if=\"settingsTab==='servers'\" class=\"grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6\">"
    )

    # --- P2: Command templates in tools page ---
    cmd_template_html = '''
        <!-- v2.5: Command Templates -->
        <div class="rounded-xl border p-5 mb-5" style="background:var(--card);border-color:var(--border)">
          <h3 class="text-sm font-semibold mb-4 flex items-center gap-2" style="color:var(--text1)">
            <svg class="icon" style="color:#10b981"><use href="#fa-terminal"></use></svg> 常用命令模板
            <span class="text-xs font-normal" style="color:var(--text2)">点击自动填入终端</span>
          </h3>
          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div v-for="grp in commandGroups" :key="grp.name" class="cmd-group">
              <div class="cmd-group-title">{{ grp.name }}</div>
              <div class="cmd-list">
                <button v-for="cmd in grp.commands" :key="cmd" @click="fillTerminalCommand(cmd)" class="cmd-btn">{{ cmd }}</button>
              </div>
            </div>
          </div>
        </div>
'''
    # Insert after terminal tool card (after the closing </details></div>)
    terminal_end = '          </details>\n        </div>\n        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">'
    if terminal_end in html:
        html = html.replace(terminal_end, '          </details>\n        </div>\n' + cmd_template_html + '        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">')
        print('  P2: Added command templates section')

    write(INDEX, html)
    print('  index.html written successfully')

def upgrade_app_js():
    """app.js: New state, computed, methods"""
    print('\n=== Upgrading app.js ===')
    backup(APP_JS)
    js = read(APP_JS)

    # Add new state after existing state declarations
    new_state = '''
    /* ========== v2.5 New State ========== */
    const offlineServices = computed(() => allServices.value.filter(s => s.status === 'offline' || s.status === 'unknown'));
    const serviceDrawer = reactive({ show: false, service: null });
    const showAlertList = ref(false);
    const settingsTab = ref('servers');

    const overviewCards = computed(() => {
      const onSrv = servers.value.filter(s => s.status === 'online').length;
      const onSvc = allServices.value.filter(s => s.status === 'online').length;
      return [
        { label: '服务器', value: `${onSrv}/${servers.value.length}`, icon: 'fa-server', color: '#3b82f6' },
        { label: '服务', value: `${onSvc}/${allServices.value.length}`, icon: 'fa-cube', color: '#10b981' },
        { label: '容器', value: monitor.containers.length || (stats.containers || 0), icon: 'fa-box', color: '#8b5cf6' },
        { label: '告警', value: offlineServices.value.length, icon: 'fa-circle-exclamation', color: '#ef4444', alert: offlineServices.value.length > 0 },
      ];
    });

    const commandGroups = [
      { name: 'Docker', commands: ['docker ps', 'docker ps -a', 'docker stats', 'docker compose ps', 'docker images', 'docker system df'] },
      { name: '系统', commands: ['df -h', 'free -h', 'top -bn1', 'uptime', 'ps aux', 'systemctl status'] },
      { name: '网络', commands: ['ss -tlnp', 'netstat -tlnp', 'curl -I localhost', 'ping -c 3', 'traceroute'] },
      { name: 'Nginx', commands: ['nginx -t', 'nginx -s reload', 'tail -f /var/log/nginx/access.log', 'tail -f /var/log/nginx/error.log'] },
    ];
'''

    # Insert after the tools reactive block
    tools_end = "    const tools = reactive({\n      timestamp: { unix: '', date: '', now: '', iso: '' },\n      base64: { input: '', encoded: '', error: '' },\n      json: { input: '', output: '', error: '' },\n      password: { length: 16, upper: true, lower: true, digits: true, symbols: true, result: '' },\n    });"
    if tools_end in js:
        js = js.replace(tools_end, tools_end + new_state)
        print('  Added v2.5 new state + computed')
    else:
        # Try alternative insertion point
        new_state_marker = "    const showAddServer = ref(false);"
        if new_state_marker in js:
            js = js.replace(new_state_marker, new_state + new_state_marker)
            print('  Added v2.5 new state (alternative)')

    # Add new methods before the return block
    new_methods = '''
    /* ========== v2.5 New Methods ========== */
    function openDrawer(svc) {
      serviceDrawer.show = true;
      serviceDrawer.service = svc;
    }

    function copyServiceInfo(svc) {
      const text = `${svc.name} | ${svc.url || ''} | 状态: ${svc.status==='online'?'在线':svc.status==='offline'?'离线':'未知'}` + (svc.category ? ` | 分类: ${svc.category}` : '');
      navigator.clipboard?.writeText(text);
      toast('已复制服务信息', 'success');
    }

    function fillTerminalCommand(cmd) {
      terminalInput.value = cmd;
      // Switch to terminal section and focus
      const details = document.querySelector('details');
      if (details && !details.open) details.open = true;
      nextTick(() => {
        const input = document.querySelector('.terminal-window input');
        if (input) input.focus();
      });
    }

    function getMetricColor(key, value) {
      const numVal = parseFloat(value);
      if (isNaN(numVal)) return null;
      if (key === 'network') return null; // Network doesn't have threshold
      if (numVal > 90) return '#ef4444';
      if (numVal > 80) return '#f59e0b';
      return null;
    }

    function getMetricClass(key, value) {
      const numVal = parseFloat(value);
      if (isNaN(numVal) || key === 'network') return '';
      if (numVal > 90) return 'severity-crit';
      if (numVal > 80) return 'severity-warn';
      return '';
    }

    function isMetricCritical(key, value) {
      const numVal = parseFloat(value);
      if (isNaN(numVal) || key === 'network') return false;
      return numVal > 90;
    }

'''

    # Insert before /* ========== Return ========== */
    return_marker = "    /* ========== Return ========== */"
    if return_marker in js:
        js = js.replace(return_marker, new_methods + return_marker)
        print('  Added v2.5 new methods')
    else:
        print('  WARNING: Could not find return marker')

    # Update return block to include new exports
    old_return = """    return {
      isDark, currentPage, currentServerId, sidebarCollapsed, searchQuery, searchSelectedIndex, mobileView, isMac,
      navItems, loading, servers, allServices, categories, categoryExpanded, pinnedIds, toasts,
      monitor, historyMetric, refreshCountdown, historyChart,
      terminalLines, terminalInput, terminalHistoryIdx,
      tools, showAddServer, newServer, newService, scanning, stats, confirmModal, showServerPwd, sshTestResult, serverMetrics,
      currentServer, filteredServices, pinnedServices, recentServices, categoriesWithServices,
      metricCards, statsCards, passwordStrengthLabel, passwordStrengthColor,
      toggleTheme, onServerChange, addServer, deleteServer, addService,
      recentIds, recentServices,
      togglePin, triggerScan, openService, toggleCategory, statusClass: OpsTools.statusClass, getCategoryColor,
      loadServers, loadServices, loadMonitor,
      executeTerminal, terminalHistoryUp, terminalHistoryDown,
      convertTimestamp, encodeBase64, decodeBase64,
      formatJson, compressJson, copyJson,
      generatePassword, copyPassword,
      iconHref, highlightText, searchUp, searchDown, searchEnter,
    };"""
    new_return = """    return {
      isDark, currentPage, currentServerId, sidebarCollapsed, searchQuery, searchSelectedIndex, mobileView, isMac,
      navItems, loading, servers, allServices, categories, categoryExpanded, pinnedIds, toasts,
      monitor, historyMetric, refreshCountdown, historyChart,
      terminalLines, terminalInput, terminalHistoryIdx,
      tools, showAddServer, newServer, newService, scanning, stats, confirmModal, showServerPwd, sshTestResult, serverMetrics,
      currentServer, filteredServices, pinnedServices, recentServices, categoriesWithServices,
      metricCards, statsCards, passwordStrengthLabel, passwordStrengthColor,
      toggleTheme, onServerChange, addServer, deleteServer, addService,
      recentIds, recentServices,
      togglePin, triggerScan, openService, toggleCategory, statusClass: OpsTools.statusClass, getCategoryColor,
      loadServers, loadServices, loadMonitor,
      executeTerminal, terminalHistoryUp, terminalHistoryDown,
      convertTimestamp, encodeBase64, decodeBase64,
      formatJson, compressJson, copyJson,
      generatePassword, copyPassword,
      iconHref, highlightText, searchUp, searchDown, searchEnter,
      // v2.5 new
      offlineServices, serviceDrawer, showAlertList, settingsTab, overviewCards, commandGroups,
      openDrawer, copyServiceInfo, fillTerminalCommand,
      getMetricColor, getMetricClass, isMetricCritical,
    };"""
    if old_return in js:
        js = js.replace(old_return, new_return)
        print('  Updated return block with v2.5 exports')
    else:
        print('  WARNING: Could not find exact return block, trying manual append')
        # Just add to the existing return - find the closing };
        old_ending = "      iconHref, highlightText, searchUp, searchDown, searchEnter,\n    };"
        new_ending = "      iconHref, highlightText, searchUp, searchDown, searchEnter,\n      // v2.5 new\n      offlineServices, serviceDrawer, showAlertList, settingsTab, overviewCards, commandGroups,\n      openDrawer, copyServiceInfo, fillTerminalCommand,\n      getMetricColor, getMetricClass, isMetricCritical,\n    };"
        if old_ending in js:
            js = js.replace(old_ending, new_ending)
            print('  Updated return block (alternative)')

    write(APP_JS, js)
    print('  app.js written successfully')

def upgrade_config_js():
    """config.js: Update nav labels and version"""
    print('\n=== Upgrading config.js ===')
    backup(CONFIG_JS)
    js = read(CONFIG_JS)
    js = js.replace("label: '服务导航'", "label: '工作台'")
    js = js.replace("label: '系统监控'", "label: '监控中心'")
    js = js.replace("label: '运维工具'", "label: '工具箱'")
    js = js.replace("label: '设置'", "label: '资源管理'")
    js = js.replace("version: 'v2.4'", "version: 'v2.5'")
    # Update nav icons
    js = js.replace("icon: 'fa-compass'", "icon: 'fa-shield'")
    write(CONFIG_JS, js)
    print('  Updated nav labels + version to v2.5')

def upgrade_tools_js():
    """tools.js: Add search parser"""
    print('\n=== Upgrading tools.js ===')
    backup(TOOLS_JS)
    js = read(TOOLS_JS)

    # Add multi-dim search parser
    search_parser = '''

  /* v2.5: Multi-dimensional search parser */
  parseSearchQuery(query) {
    const result = { text: '', status: null, category: null, server: null };
    if (!query) return result;
    const tokens = query.split(/\s+/);
    const textParts = [];
    for (const token of tokens) {
      if (token.startsWith('status:')) {
        result.status = token.slice(7);
      } else if (token.startsWith('cat:')) {
        result.category = token.slice(4).toLowerCase();
      } else if (token.startsWith('server:')) {
        result.server = token.slice(7).toLowerCase();
      } else {
        textParts.push(token);
      }
    }
    result.text = textParts.join(' ').toLowerCase();
    return result;
  },
'''

    # Insert before closing };
    js = js.rstrip()
    if js.endswith('};'):
        js = js[:-2] + search_parser + '};'
        print('  Added parseSearchQuery')

    write(TOOLS_JS, js)
    print('  tools.js written successfully')

def main():
    print('🚀 OpsCenter v2.4 → v2.5 Upgrade Script')
    print(f'Time: {datetime.datetime.now()}')
    upgrade_index()
    upgrade_app_js()
    upgrade_config_js()
    upgrade_tools_js()
    print('\n✅ All upgrades applied! Now run deploy:')
    print('  sudo docker cp /opt/opscenter/frontend/index.html nginx:/usr/share/nginx/html/ops/index.html')
    print('  sudo docker cp /opt/opscenter/frontend/assets/js/. nginx:/usr/share/nginx/html/ops/assets/js/')

if __name__ == '__main__':
    main()
