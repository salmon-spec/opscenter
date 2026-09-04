<template>
  <div class="screen-root" ref="screenRoot">
    <header class="screen-head">
      <div>
        <h1 class="screen-title">OpsCenter 健康大屏</h1>
        <div class="screen-sub">核心数据每 10 秒刷新 · 最后刷新 {{ lastRefresh }}<span v-if="pageHidden"> · 页面已隐藏，暂停轮询</span></div>
      </div>
      <div class="screen-tools">
        <router-link v-if="standalone" to="/" class="btn btn-ghost screen-btn">← 返回工作台</router-link>
        <button class="btn screen-btn" @click="toggleFullscreen">{{ isFullscreen ? '退出全屏' : '全屏' }}</button>
      </div>
    </header>

    <div v-if="partialErrors.length" class="partial-errors">
      <span class="perr-icon">⚠</span>
      <div class="perr-body">
        <b>部分数据异常（时间 {{ lastRefresh }}）</b>
        <p v-for="(e, i) in partialErrors" :key="i" class="perr-item">{{ e }}</p>
      </div>
    </div>

    <!-- 资源总览卡 -->
    <div class="res-grid">
      <router-link to="/system/monitor" class="res-card">
        <div class="res-head"><span class="res-label">主机</span><b class="res-num">{{ fmtHosts() }}</b></div>
        <p class="res-meta">在线 {{ hostsSummary.online || 0 }} / {{ hostsSummary.total || 0 }}<template v-if="hostsSummary.stale"> · 数据陈旧 {{ hostsSummary.stale }}</template></p>
        <div class="res-risk">{{ riskTop('cpu', 'CPU') }}</div>
      </router-link>
      <router-link to="/container" class="res-card">
        <div class="res-head"><span class="res-label">容器</span><b class="res-num">{{ containersSummary.running ?? '--' }}</b></div>
        <p class="res-meta">运行中 · 已停止 {{ containersSummary.stopped ?? '--' }}<template v-if="containersSummary.unknown_hosts"> · 未知主机 {{ containersSummary.unknown_hosts }}</template></p>
      </router-link>
      <router-link to="/database" class="res-card">
        <div class="res-head"><span class="res-label">数据库</span><b class="res-num">{{ databasesSummary.total ?? '--' }}</b></div>
        <p class="res-meta">{{ databasesSummary.total === 0 ? '暂无实例' : `已连接 ${databasesSummary.connected || 0} · 待接入 ${databasesSummary.pending || 0} · 异常 ${databasesSummary.error || 0}` }}</p>
      </router-link>
      <router-link to="/service-health" class="res-card">
        <div class="res-head"><span class="res-label">服务</span><b class="res-num">{{ servicesSummary.up ?? '--' }} / {{ servicesSummary.total ?? '--' }}</b></div>
        <p class="res-meta">在线/总数 · 离线 {{ servicesSummary.down ?? '--' }}<template v-if="servicesSummary.incidents"> · 事件 {{ servicesSummary.incidents }}</template></p>
      </router-link>
      <router-link to="/logs" class="res-card">
        <div class="res-head"><span class="res-label">日志</span><b class="res-num">{{ logsSummary.running ?? '--' }}/{{ logsSummary.total ?? '--' }}</b></div>
        <p class="res-meta">采集器运行/总数 · 摄入状态在日志中心按需检查 · 异常 {{ logsSummary.abnormal ?? '--' }}</p>
      </router-link>
      <router-link :to="{ path: '/topology', query: { scenario: 'wireguard' } }" class="res-card">
        <div class="res-head"><span class="res-label">WireGuard</span><b class="res-num">{{ wgSummary.managed ?? '--' }}</b></div>
        <p class="res-meta">已纳管 · 健康 {{ wgSummary.healthy ?? '--' }} · 警告 {{ wgSummary.warning ?? '--' }} · 离线 {{ wgSummary.offline ?? '--' }} · 未纳管 {{ wgSummary.unmanaged ?? '--' }}</p>
      </router-link>
      <router-link to="/alerts" class="res-card">
        <div class="res-head"><span class="res-label">告警</span><b class="res-num" :class="alertsSummary.firing ? 'res-err' : ''">{{ alertsSummary.firing ?? '--' }}</b></div>
        <p class="res-meta">触发中 · 已确认 {{ alertsSummary.acknowledged ?? '--' }}</p>
      </router-link>
    </div>

    <div class="screen-grid">
      <!-- 主机水位 -->
      <section class="panel">
        <div class="panel-title">主机资源水位 <span class="screen-muted" style="font-size:12px">异常优先</span></div>
        <div v-if="!servers.length" class="screen-empty">暂无主机数据</div>
        <div v-for="h in sortedServers" :key="h.id" class="host-line">
          <div class="host-line-head">
            <span>{{ h.name }}<em v-if="h.stale" class="stale-tag">陈旧</em></span>
            <span class="screen-muted">{{ h.host }} · {{ h.last_seen ? fmtTime(h.last_seen) : '无数据' }}</span>
          </div>
          <div class="host-line-bars">
            <div class="hlb"><span>CPU</span><div class="hlb-track"><div class="hlb-fill" :style="fillStyle(h.cpu)"></div></div><b class="hlb-val">{{ fmtPct(h.cpu) }}</b></div>
            <div class="hlb"><span>MEM</span><div class="hlb-track"><div class="hlb-fill" :style="fillStyle(h.memory)"></div></div><b class="hlb-val">{{ fmtPct(h.memory) }}</b></div>
            <div class="hlb"><span>DISK</span><div class="hlb-track"><div class="hlb-fill" :style="fillStyle(h.disk)"></div></div><b class="hlb-val">{{ fmtPct(h.disk) }}</b></div>
          </div>
        </div>
      </section>

      <!-- 服务健康矩阵 -->
      <section class="panel">
        <div class="panel-title">服务健康矩阵
          <span class="screen-muted" style="font-size:12px">up {{ healthSummary.up }} / down {{ healthSummary.down }} / total {{ healthSummary.total }}</span>
        </div>
        <div v-if="!services.length" class="screen-empty">暂无服务数据</div>
        <div v-else class="health-grid">
          <div v-for="s in services" :key="s.id" class="health-tile" :class="'st-' + (s.status === 'unknown' ? 'unknown' : s.status)" :title="s.name" @click="go('/service-health')">
            <span class="tile-name">{{ s.name }}</span>
          </div>
        </div>
      </section>

      <!-- 活跃告警 -->
      <section class="panel">
        <div class="panel-title">活跃告警 <span class="screen-muted" style="font-size:12px">触发中</span></div>
        <div v-if="!alerts.length" class="screen-empty">当前无告警 🎉</div>
        <div v-else class="alert-list">
          <div v-for="e in alerts" :key="e.id" class="alert-item" @click="go('/alerts')">
            <span class="alert-dot err"></span>
            <div class="alert-main">
              <div class="alert-name">{{ e.rule_name || '未知规则' }}</div>
              <div class="alert-meta">{{ e.server_name || '' }} · {{ fmtTime(e.fired_at || e.created_at) }} · {{ e.current_value || '' }}</div>
            </div>
            <span class="tag tag-red">{{ e.status }}</span>
          </div>
        </div>
      </section>

      <!-- 资源趋势 -->
      <section class="panel panel-wide">
        <div class="panel-title">资源趋势
          <select v-model="trendHost" class="screen-select" @change="queueTrend()">
            <option value="__top3__">风险 Top 3 主机</option>
            <option v-for="h in servers" :key="h.id" :value="h.id">{{ h.name }}</option>
          </select>
          <select v-model="trendMetric" class="screen-select" @change="queueTrend()">
            <option value="cpu">CPU</option>
            <option value="memory">内存</option>
            <option value="disk">磁盘</option>
            <option value="net">网络</option>
          </select>
          <select v-model="trendRange" class="screen-select" @change="queueTrend()">
            <option value="1">近 1 小时</option>
            <option value="6">近 6 小时</option>
            <option value="24">近 24 小时</option>
          </select>
        </div>
        <div ref="trendChartEl" class="chart"></div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { api, fmtTime } from '../api'

const router = useRouter()
const standalone = ref(!!(window.location.hash.includes('screen-standalone')))
const screenRoot = ref(null)
const trendChartEl = ref(null)
const isFullscreen = ref(false)
const lastRefresh = ref('-')
const pageHidden = ref(false)

const summary = ref(null)
const partialErrors = ref([])
const trendHost = ref('__top3__')
const trendMetric = ref('cpu')
const trendRange = ref('6')

let trendChart = null
let coreTimer = null
let trendTimer = null
let controller = null
let requestInFlight = false

const hostsSummary = computed(() => summary.value?.hosts_summary || {})
const containersSummary = computed(() => summary.value?.containers_summary || {})
const databasesSummary = computed(() => summary.value?.databases_summary || {})
const servicesSummary = computed(() => summary.value?.services_summary || {})
const logsSummary = computed(() => summary.value?.logs_summary || {})
const wgSummary = computed(() => summary.value?.wireguard_summary || {})
const alertsSummary = computed(() => summary.value?.alerts_summary || {})
const servers = computed(() => summary.value?.servers || [])
const services = computed(() => summary.value?.services || [])
const alerts = computed(() => summary.value?.active_alerts || [])

const healthSummary = computed(() => {
  const up = services.value.filter((s) => s.status === 'up' || s.status === 'online').length
  const down = services.value.filter((s) => s.status === 'down' || s.status === 'offline').length
  return { up, down, total: services.value.length }
})

const sortedServers = computed(() => {
  const score = (h) => {
    const v = [h.cpu, h.memory, h.disk]
    return Math.max(...v.map((x) => (x == null ? -1 : x)))
  }
  return [...servers.value].sort((a, b) => score(b) - score(a))
})

function fmtPct(v) { return v == null ? '--' : `${Number(v).toFixed(1)}%` }
function fmtHosts() {
  const s = hostsSummary.value
  return s.total == null ? '--' : `${s.online || 0}/${s.total}`
}
function fillStyle(v) {
  if (v == null) return { width: '0%' }
  return { width: Math.min(100, Number(v)) + '%', background: levelColor(v) }
}
function levelColor(v) {
  if (v >= 90) return '#ef4444'
  if (v >= 70) return '#f59e0b'
  return '#22c55e'
}
function riskTop(metricKey, label) {
  const top = [...servers.value]
    .filter((h) => h[metricKey] != null)
    .sort((a, b) => Number(b[metricKey]) - Number(a[metricKey]))
    .slice(0, 3)
  if (!top.length) return ''
  return `${label} 前三：${top.map((h) => `${h.name} ${fmtPct(h[metricKey])}`).join('，')}`
}
function go(path) { router.push(path) }

// ---------------- 核心聚合 ----------------
async function loadSummary() {
  if (requestInFlight) return
  if (document.visibilityState !== 'visible') return
  requestInFlight = true
  if (controller) controller.abort()
  controller = new AbortController()
  try {
    const data = await api.get('/screen/summary', null, { signal: controller.signal })
    summary.value = data
    partialErrors.value = data.partial_errors || []
    lastRefresh.value = fmtTime(new Date().toISOString())
  } catch (err) {
    if (err.name !== 'AbortError') {
      // 保留上一份可用数据；只更新时间戳
      partialErrors.value = [...partialErrors.value, `核心聚合失败：${err.message}`].slice(-6)
    }
  } finally {
    requestInFlight = false
  }
}

async function queueTrend() {
  if (document.visibilityState === 'hidden') return
  // 趋势独立于核心刷新，60 秒节流
  const now = Date.now()
  if (now - (lastTrendAt || 0) < 15000) return
  lastTrendAt = now
  await loadTrend()
}
let lastTrendAt = 0

async function loadTrend() {
  if (!trendChartEl.value) return
  const hours = Number(trendRange.value)
  const end = new Date()
  const start = new Date(end.getTime() - hours * 3600 * 1000)
  let ids = []
  if (trendHost.value === '__top3__') {
    ids = sortedServers.value.slice(0, 3).map((h) => h.id)
  } else {
    ids = [trendHost.value]
  }
  const isNet = trendMetric.value === 'net'
  const metrics = isNet ? 'net_rx,net_tx' : trendMetric.value
  const series = []
  const color = { cpu: '#3b82f6', memory: '#10b981', disk: '#f59e0b', net_rx: '#38bdf8', net_tx: '#fb7185' }
  const unit = isNet ? 'KB/s' : '%'
  await Promise.allSettled(ids.map(async (id) => {
    const h = servers.value.find((x) => x.id === id)
    if (!h) return
    const d = await api.get(`/servers/${id}/metrics/timeseries`, {
      metrics, start: start.toISOString(), end: end.toISOString(), resolution: 'auto',
    }).catch(() => null)
    if (!d?.series) return
    for (const [name, points] of Object.entries(d.series)) {
      if (points?.length) {
        const isNetP = name === 'net_rx' || name === 'net_tx'
        series.push({
          name: isNetP ? `${h.name} ${name === 'net_rx' ? '下行' : '上行'}` : `${h.name}`,
          type: 'line', smooth: true, showSymbol: false,
          itemStyle: { color: color[name] || color[trendMetric.value] },
          areaStyle: isNetP ? { opacity: .15 } : undefined,
          data: points.map((pt) => [pt[0] * 1000, isNetP ? Number((pt[1] / 1024).toFixed(2)) : Number(pt[1].toFixed(1))]),
        })
      }
    }
  }))
  if (trendChart) {
    trendChart.setOption({
      series,
      xAxis: { type: 'time' },
      yAxis: { type: 'value', max: isNet ? undefined : 100, axisLabel: { color: '#7f95b5', formatter: isNet ? '{value}' : '{value}%' }, name: unit },
    })
  }
}

function initChart() {
  trendChart = echarts.init(trendChartEl.value)
  trendChart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#7f95b5' },
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#7f95b5' }, top: 0, type: 'scroll' },
    grid: { left: 48, right: 16, top: 34, bottom: 24 },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: '#2a4060' } }, axisLabel: { color: '#7f95b5' } },
    yAxis: { type: 'value', axisLabel: { color: '#7f95b5' }, splitLine: { lineStyle: { color: 'rgba(90,130,190,.12)' } } },
    series: [],
  })
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    const p = screenRoot.value.requestFullscreen()
    if (p && p.catch) p.catch(() => window.open(location.href.split('#')[0] + '#/screen-standalone', '_blank'))
  } else {
    document.exitFullscreen()
  }
}

function onVisibility() {
  const hidden = document.visibilityState !== 'visible'
  pageHidden.value = hidden
  if (!hidden) {
    loadSummary()
    queueTrend()
  }
}

onMounted(() => {
  initChart()
  loadSummary()
  queueTrend()
  coreTimer = setInterval(loadSummary, 10000)
  trendTimer = setInterval(queueTrend, 60000)
  document.addEventListener('visibilitychange', onVisibility)
  document.addEventListener('fullscreenchange', onFsChange)
  window.addEventListener('resize', resizeChart)
})
onUnmounted(() => {
  clearInterval(coreTimer)
  clearInterval(trendTimer)
  document.removeEventListener('visibilitychange', onVisibility)
  document.removeEventListener('fullscreenchange', onFsChange)
  window.removeEventListener('resize', resizeChart)
  if (controller) controller.abort()
  if (trendChart) trendChart.dispose()
})

function onFsChange() { isFullscreen.value = !!document.fullscreenElement }
function resizeChart() { trendChart?.resize() }
</script>

<style scoped>
.screen-root {
  height: 100%; overflow: auto; background: var(--screen-bg); color: var(--screen-text);
  padding: 18px 22px;
}
.screen-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 14px; }
.screen-title { margin: 0; font-size: 22px; }
.screen-sub { color: var(--screen-muted); font-size: 12px; margin-top: 4px; }
.screen-tools { display: flex; gap: 8px; }
.screen-btn { background: rgba(20,35,58,.8); border-color: var(--screen-border); color: var(--screen-text); }
.screen-select { margin-left: auto; background: #14233a; color: var(--screen-text); border: 1px solid var(--screen-border); border-radius: 6px; padding: 4px 8px; }
.partial-errors { display: flex; gap: 10px; align-items: flex-start; background: rgba(180,83,9,.14); border: 1px solid rgba(245,158,11,.4); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; }
.perr-icon { font-size: 16px; }
.perr-body b { font-size: 13px; color: #fcd34d; }
.perr-item { margin: 2px 0 0; font-size: 12px; color: var(--screen-muted); }
.res-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: 12px; margin-bottom: 16px; }
.res-card {
  background: var(--screen-panel); border: 1px solid var(--screen-border); border-radius: 12px;
  padding: 12px 14px; text-decoration: none; color: var(--screen-text); display: block; transition: border-color .15s;
}
.res-card:hover { border-color: #3b82f6; }
.res-head { display: flex; align-items: center; justify-content: space-between; }
.res-label { font-size: 12px; color: var(--screen-muted); }
.res-num { font-size: 22px; }
.res-num.res-err { color: #f87171; }
.res-meta { margin: 6px 0 0; font-size: 12px; color: var(--screen-muted); }
.res-risk { margin-top: 6px; font-size: 11px; color: var(--screen-muted); }
.screen-grid { display: grid; grid-template-columns: 1fr 1.4fr 1fr; gap: 14px; }
.panel { background: var(--screen-panel); border: 1px solid var(--screen-border); border-radius: 12px; padding: 14px 16px; min-height: 240px; }
.panel-wide { grid-column: span 1; }
.panel-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.screen-muted { color: var(--screen-muted); }
.screen-empty { color: var(--screen-muted); font-size: 13px; text-align: center; padding: 30px 0; }
.host-line { margin-bottom: 12px; }
.host-line-head { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 5px; }
.host-line-head em { font-style: normal; color: #f59e0b; margin-left: 6px; border: 1px solid rgba(245,158,11,.5); border-radius: 4px; padding: 0 4px; font-size: 10px; }
.host-line-bars { display: flex; gap: 10px; }
.hlb { flex: 1; display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--screen-muted); }
.hlb-track { flex: 1; height: 6px; background: rgba(127,149,181,.18); border-radius: 3px; overflow: hidden; }
.hlb-fill { height: 100%; border-radius: 3px; transition: width .4s; }
.hlb-val { min-width: 44px; text-align: right; font-weight: 600; font-size: 11px; }
.health-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 8px; max-height: 300px; overflow: auto; }
.health-tile { border: 1px solid var(--screen-border); border-radius: 8px; padding: 8px 6px; text-align: center; font-size: 12px; cursor: pointer; background: rgba(20,35,58,.5); }
.health-tile:hover { border-color: #3b82f6; }
.st-up { border-color: rgba(34,197,94,.45); color: #86efac; }
.st-down { border-color: rgba(239,68,68,.55); color: #fca5a5; }
.st-warn { border-color: rgba(245,158,11,.5); color: #fcd34d; }
.st-unknown { color: var(--screen-muted); }
.tile-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }
.alert-list { max-height: 300px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
.alert-item { display: flex; align-items: flex-start; gap: 8px; border-bottom: 1px solid rgba(90,130,190,.14); padding-bottom: 8px; cursor: pointer; }
.alert-dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 5px; flex: none; }
.alert-dot.err { background: #ef4444; }
.alert-main { flex: 1; min-width: 0; }
.alert-name { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.alert-meta { font-size: 11px; color: var(--screen-muted); margin-top: 2px; }
.chart { height: 240px; }
@media (max-width: 1100px) {
  .screen-grid { grid-template-columns: 1fr; }
}
</style>
