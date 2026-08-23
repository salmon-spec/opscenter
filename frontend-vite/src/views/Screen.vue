<template>
  <div class="screen-root" ref="screenRoot">
    <header class="screen-head">
      <div>
        <h1 class="screen-title">OpsCenter 监控大屏</h1>
        <div class="screen-sub">最后刷新 {{ lastRefresh }}</div>
      </div>
      <div class="screen-tools">
        <router-link v-if="standalone" to="/" class="btn btn-ghost screen-btn">← 返回工作台</router-link>
        <button class="btn screen-btn" @click="toggleFullscreen">{{ isFullscreen ? '退出全屏' : '全屏' }}</button>
      </div>
    </header>

    <div class="screen-grid">
      <!-- 主机水位 -->
      <section class="panel">
        <div class="panel-title">主机资源水位</div>
        <div v-if="!hosts.length" class="screen-empty">暂无主机数据</div>
        <div v-for="h in hosts" :key="h.id" class="host-line">
          <div class="host-line-head">
            <span>{{ h.name }}</span>
            <span class="screen-muted">{{ h.host }}</span>
          </div>
          <div class="host-line-bars">
            <div class="hlb"><span>CPU</span><div class="hlb-track"><div class="hlb-fill" :style="{ width: (monitors[h.id]?.cpu || 0) + '%', background: levelColor(monitors[h.id]?.cpu) }"></div></div></div>
            <div class="hlb"><span>MEM</span><div class="hlb-track"><div class="hlb-fill" :style="{ width: (monitors[h.id]?.memory || 0) + '%', background: levelColor(monitors[h.id]?.memory) }"></div></div></div>
            <div class="hlb"><span>DISK</span><div class="hlb-track"><div class="hlb-fill" :style="{ width: (monitors[h.id]?.disk || 0) + '%', background: levelColor(monitors[h.id]?.disk) }"></div></div></div>
          </div>
        </div>
      </section>

      <!-- 服务健康矩阵 -->
      <section class="panel">
        <div class="panel-title">服务健康矩阵
          <span class="screen-muted" style="font-size:12px">up {{ healthSummary.up }} / down {{ healthSummary.down }} / total {{ healthSummary.total }}</span>
        </div>
        <div v-if="!healthServices.length" class="screen-empty">暂无服务数据</div>
        <div v-else class="health-grid">
          <div
            v-for="s in healthServices" :key="s.id"
            class="health-tile" :class="'st-' + s.status"
            :title="s.name + (s.url ? ' · ' + s.url : '')"
            @click="selected = s; drawerVisible = true"
          >
            <span class="tile-name">{{ s.name }}</span>
          </div>
        </div>
      </section>

      <!-- 活跃告警 -->
      <section class="panel">
        <div class="panel-title">活跃告警 <span class="screen-muted" style="font-size:12px">最近 24h</span></div>
        <div v-if="!events.length" class="screen-empty">当前无告警 🎉</div>
        <div v-else class="alert-list">
          <div v-for="e in events" :key="e.id" class="alert-item">
            <span class="alert-dot" :class="e.status === 'resolved' ? 'ok' : e.status === 'acked' ? 'warn' : 'err'"></span>
            <div class="alert-main">
              <div class="alert-name">{{ e.rule_name || '未知规则' }}</div>
              <div class="alert-meta">{{ e.server_name || '' }} · {{ fmtTime(e.fired_at) }}</div>
            </div>
            <span class="tag" :class="e.status === 'resolved' ? 'tag-green' : e.status === 'acked' ? 'tag-amber' : 'tag-red'">{{ e.status }}</span>
          </div>
        </div>
      </section>

      <!-- 资源趋势 -->
      <section class="panel panel-wide">
        <div class="panel-title">资源趋势（CPU 6h）</div>
        <div ref="cpuChartEl" class="chart"></div>
      </section>

      <!-- 网络趋势 -->
      <section class="panel panel-wide">
        <div class="panel-title">网络流量趋势（6h）
          <select v-model="selectedNetHostId" class="screen-select" @change="loadNetworkTrend">
            <option v-for="h in hosts" :key="h.id" :value="h.id">{{ h.name }}</option>
          </select>
        </div>
        <div ref="netChartEl" class="chart"></div>
      </section>
    </div>

    <ServiceDetailDrawer :visible="drawerVisible" :service="selected" @close="drawerVisible = false" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import * as echarts from 'echarts'
import { api, fmtTime } from '../api'
import ServiceDetailDrawer from '../components/ServiceDetailDrawer.vue'

const standalone = ref(!!(window.location.hash.includes('screen-standalone')))
const screenRoot = ref(null)
const cpuChartEl = ref(null)
const netChartEl = ref(null)
const isFullscreen = ref(false)
const lastRefresh = ref('-')

const hosts = ref([])
const monitors = ref({})
const healthServices = ref([])
const events = ref([])
const drawerVisible = ref(false)
const selected = ref(null)
const selectedNetHostId = ref('')

let cpuChart = null
let netChart = null
let timer = null
let trendsRefreshedAt = 0

const healthSummary = computed(() => {
  const up = healthServices.value.filter((s) => s.status === 'up' || s.status === 'online').length
  const down = healthServices.value.filter((s) => s.status === 'down' || s.status === 'offline').length
  return { up, down, total: healthServices.value.length }
})
function levelColor(v) {
  if (v >= 90) return '#ef4444'
  if (v >= 70) return '#f59e0b'
  return '#22c55e'
}

function normStatus(s) {
  if (s === 'online' || s === 'up' || s === 'running') return 'up'
  if (s === 'offline' || s === 'down') return 'down'
  if (s === 'degraded') return 'warn'
  return 'unknown'
}

async function loadAll() {
  lastRefresh.value = fmtTime(new Date().toISOString())
  // 主机 + 指标
  const sv = await api.get('/servers').catch(() => [])
  hosts.value = sv
  if (!sv.some((h) => h.id === selectedNetHostId.value)) {
    selectedNetHostId.value = sv[0]?.id || ''
  }
  const mon = {}
  await Promise.allSettled(sv.map(async (h) => {
    try {
      const d = await api.get(`/servers/${h.id}/monitor`)
      mon[h.id] = d.metrics || {}
    } catch { mon[h.id] = null }
  }))
  monitors.value = mon

  // 服务健康
  const health = await api.get('/services/health').catch(() => null)
  if (health?.services) {
    healthServices.value = health.services.map((s) => ({ ...s, status: normStatus(s.status) }))
  } else {
    const list = await api.get('/services-with-status').catch(() => [])
    healthServices.value = list.map((s) => ({ id: s.id, name: s.name, url: s.url, status: normStatus(s.status) }))
  }

  // 告警
  events.value = await api.get('/alert-events', { days: 1 }).catch(() => [])

  // 趋势
  const now = Date.now()
  if (now - trendsRefreshedAt >= 30000) {
    trendsRefreshedAt = now
    await loadTrends()
  }
}

async function loadTrends() {
  const series = []
  await Promise.allSettled(hosts.value.map(async (h) => {
    const d = await api.get(`/monitor/${h.id}/history`, { metric: 'cpu', hours: 6 }).catch(() => null)
    if (d?.values?.length) {
      series.push({
        name: h.name,
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: d.values.map(([t, v]) => [t * 1000, Number(v).toFixed(1)]),
      })
    }
  }))
  if (cpuChart) {
    cpuChart.setOption({
      series,
      xAxis: { type: 'time' },
      yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
    })
  }

  await loadNetworkTrend()
}

async function loadNetworkTrend() {
  const h = hosts.value.find((item) => item.id === selectedNetHostId.value)
  if (h) {
    const [rx, tx] = await Promise.allSettled([
      api.get(`/monitor/${h.id}/history`, { metric: 'net_rx', hours: 6 }),
      api.get(`/monitor/${h.id}/history`, { metric: 'net_tx', hours: 6 }),
    ])
    const toKbps = (v) => Number((v / 1024).toFixed(2))
    if (netChart) {
      netChart.setOption({
        series: [
          { name: '下行', type: 'line', smooth: true, showSymbol: false, areaStyle: { opacity: .2 }, data: rx.status === 'fulfilled' ? rx.value.values.map(([t, v]) => [t * 1000, toKbps(v)]) : [] },
          { name: '上行', type: 'line', smooth: true, showSymbol: false, areaStyle: { opacity: .2 }, data: tx.status === 'fulfilled' ? tx.value.values.map(([t, v]) => [t * 1000, toKbps(v)]) : [] },
        ],
        xAxis: { type: 'time' },
        yAxis: { type: 'value', name: 'KB/s' },
      })
    }
  } else if (netChart) {
    netChart.setOption({ series: [] })
  }
}

function initCharts() {
  cpuChart = echarts.init(cpuChartEl.value)
  netChart = echarts.init(netChartEl.value)
  const base = {
    backgroundColor: 'transparent',
    textStyle: { color: '#7f95b5' },
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#7f95b5' }, top: 0 },
    grid: { left: 44, right: 16, top: 32, bottom: 24 },
  }
  cpuChart.setOption({
    ...base,
    xAxis: { type: 'time', axisLine: { lineStyle: { color: '#2a4060' } }, axisLabel: { color: '#7f95b5' } },
    yAxis: { type: 'value', max: 100, axisLabel: { color: '#7f95b5', formatter: '{value}%' }, splitLine: { lineStyle: { color: 'rgba(90,130,190,.12)' } } },
    series: [],
  })
  netChart.setOption({
    ...base,
    xAxis: { type: 'time', axisLine: { lineStyle: { color: '#2a4060' } }, axisLabel: { color: '#7f95b5' } },
    yAxis: { type: 'value', name: 'KB/s', axisLabel: { color: '#7f95b5' }, splitLine: { lineStyle: { color: 'rgba(90,130,190,.12)' } } },
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

onMounted(() => {
  initCharts()
  loadAll()
  timer = setInterval(loadAll, 5000)
  document.addEventListener('fullscreenchange', onFsChange)
  window.addEventListener('resize', resizeCharts)
})
onUnmounted(() => {
  clearInterval(timer)
  document.removeEventListener('fullscreenchange', onFsChange)
  window.removeEventListener('resize', resizeCharts)
  if (cpuChart) cpuChart.dispose()
  if (netChart) netChart.dispose()
})

function onFsChange() { isFullscreen.value = !!document.fullscreenElement }
function resizeCharts() { cpuChart?.resize(); netChart?.resize() }
</script>

<style scoped>
.screen-root {
  height: 100%; overflow: auto; background: var(--screen-bg); color: var(--screen-text);
  padding: 18px 22px;
}
.screen-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 16px; }
.screen-title { margin: 0; font-size: 22px; }
.screen-sub { color: var(--screen-muted); font-size: 12px; margin-top: 4px; }
.screen-tools { display: flex; gap: 8px; }
.screen-btn { background: rgba(20,35,58,.8); border-color: var(--screen-border); color: var(--screen-text); }
.screen-select { margin-left: auto; background: #14233a; color: var(--screen-text); border: 1px solid var(--screen-border); border-radius: 6px; padding: 4px 8px; }
.screen-grid { display: grid; grid-template-columns: 1fr 1.4fr 1fr; gap: 14px; }
.panel {
  background: var(--screen-panel); border: 1px solid var(--screen-border); border-radius: 12px;
  padding: 14px 16px; min-height: 240px;
}
.panel-wide { grid-column: span 1; }
.panel-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }
.screen-muted { color: var(--screen-muted); }
.screen-empty { color: var(--screen-muted); font-size: 13px; text-align: center; padding: 30px 0; }
.host-line { margin-bottom: 12px; }
.host-line-head { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 5px; }
.host-line-bars { display: flex; gap: 10px; }
.hlb { flex: 1; display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--screen-muted); }
.hlb-track { flex: 1; height: 6px; background: rgba(127,149,181,.18); border-radius: 3px; overflow: hidden; }
.hlb-fill { height: 100%; border-radius: 3px; transition: width .4s; }
.health-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(96px, 1fr)); gap: 8px; max-height: 300px; overflow: auto; }
.health-tile {
  border: 1px solid var(--screen-border); border-radius: 8px; padding: 8px 6px;
  text-align: center; font-size: 12px; cursor: pointer; background: rgba(20,35,58,.5);
}
.health-tile:hover { border-color: #3b82f6; }
.st-up { border-color: rgba(34,197,94,.45); color: #86efac; }
.st-down { border-color: rgba(239,68,68,.55); color: #fca5a5; }
.st-warn { border-color: rgba(245,158,11,.5); color: #fcd34d; }
.st-unknown { color: var(--screen-muted); }
.tile-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }
.alert-list { max-height: 300px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
.alert-item { display: flex; align-items: flex-start; gap: 8px; border-bottom: 1px solid rgba(90,130,190,.14); padding-bottom: 8px; }
.alert-dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 5px; }
.alert-dot.ok { background: #22c55e; }
.alert-dot.warn { background: #f59e0b; }
.alert-dot.err { background: #ef4444; }
.alert-main { flex: 1; min-width: 0; }
.alert-name { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.alert-meta { font-size: 11px; color: var(--screen-muted); margin-top: 2px; }
.chart { height: 220px; }
@media (max-width: 1100px) {
  .screen-grid { grid-template-columns: 1fr; }
}
</style>
