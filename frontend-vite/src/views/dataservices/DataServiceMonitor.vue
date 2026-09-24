<template>
  <section class="monitor-panel">
    <div class="panel-head">
      <div>
        <h3>监控指标</h3>
        <span class="muted small">实时指标与多维历史趋势</span>
      </div>
      <div class="controls">
        <select v-model="range" class="select" @change="loadTimeseries">
          <option value="15m">15 分钟</option>
          <option value="1h">1 小时</option>
          <option value="6h">6 小时</option>
          <option value="24h">24 小时</option>
          <option value="7d">7 天</option>
        </select>
        <select v-model="metricKey" class="select metric-select" @change="loadTimeseries">
          <option v-for="item in metricOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
        </select>
        <select v-model="groupBy" class="select" @change="loadTimeseries">
          <option value="">不分组</option>
          <option v-for="item in dimensionOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
        </select>
        <button class="btn btn-sm" :disabled="loading" @click="reload">{{ loading ? '刷新中…' : '刷新' }}</button>
      </div>
    </div>

    <div v-if="error" class="monitor-error">{{ error }}</div>
    <div v-if="monitor?.metrics_truncated" class="monitor-notice">
      共采集 {{ monitor.metrics_total }} 项指标，已按异常与汇总优先级保留显示。
    </div>
    <div v-if="loading && !monitor" class="loading"><span class="spinner"></span>正在读取监控指标…</div>

    <template v-else-if="monitor">
      <div class="metric-grid">
        <div v-for="metric in visibleMetrics" :key="`${metric.key}:${metric.dimension_key}`" class="metric-card" :class="metric.status">
          <div class="metric-label" :title="metric.key">{{ metric.label }}</div>
          <b>{{ formatValue(metric) }}</b>
          <small v-if="dimensionText(metric)">{{ dimensionText(metric) }}</small>
          <span class="metric-source">{{ metric.source || 'live' }}</span>
        </div>
      </div>

      <div class="trend-block">
        <div class="trend-head">
          <div>
            <b>{{ selectedMetricLabel }}</b>
            <span class="muted small">{{ rangeText }} · {{ resolutionText }}</span>
          </div>
          <span class="muted small">{{ seriesCount }} 条曲线</span>
        </div>
        <div ref="chartEl" class="trend-chart"></div>
        <div v-if="!seriesCount && !trendLoading" class="empty-trend">
          暂无历史采样，后台采样开启后会逐步形成趋势。
        </div>
      </div>

      <div class="diagnostic-block">
        <div class="diagnostic-head">
          <b>异常诊断</b>
          <span class="muted small">{{ diagnostics.length }} 项</span>
        </div>
        <div v-if="diagnostics.length" class="diagnostic-list">
          <div v-for="(item, index) in diagnostics" :key="`${item.metric_key || item.title}:${index}`"
               class="diagnostic-item" :class="item.level">
            <span class="diagnostic-dot"></span>
            <div><b>{{ item.title }}</b><small>{{ item.message }}</small></div>
          </div>
        </div>
        <div v-else class="muted small">暂无异常</div>
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import echarts from '../../utils/echarts'
import { dsApi, unwrap, apiErrorText } from '../../api/dataservices'
import { fmtBytes } from '../../api'

const props = defineProps({
  kind: { type: String, required: true },
  instance: { type: String, default: 'primary' },
})

const monitor = ref(null)
const diagnostics = ref([])
const error = ref('')
const loading = ref(false)
const trendLoading = ref(false)
const range = ref('1h')
const metricKey = ref('')
const groupBy = ref('')
const series = ref([])
const chartEl = ref(null)
let chart = null
let monitorController = null
let trendController = null
let monitorSeq = 0
let trendSeq = 0

const metricOptions = computed(() => {
  const seen = new Set()
  return (monitor.value?.metrics || [])
    .filter((item) => item.numeric && item.key)
    .filter((item) => {
      if (seen.has(item.key)) return false
      seen.add(item.key)
      return true
    })
    .map((item) => ({ key: item.key, label: item.label }))
    .slice(0, 80)
})

const dimensionOptions = computed(() => (monitor.value?.dimensions || []).filter((item) => item.key))

const visibleMetrics = computed(() => {
  const priority = { critical: 0, warning: 1, ok: 2, unknown: 3 }
  return [...(monitor.value?.metrics || [])]
    .sort((a, b) => (priority[a.status] ?? 9) - (priority[b.status] ?? 9))
    .slice(0, 18)
})

const selectedMetricLabel = computed(() => metricOptions.value.find((item) => item.key === metricKey.value)?.label || metricKey.value || '趋势')
const seriesCount = computed(() => series.value.length)
const rangeText = computed(() => ({ '15m': '15 分钟', '1h': '1 小时', '6h': '6 小时', '24h': '24 小时', '7d': '7 天' }[range.value] || range.value))
const resolutionText = computed(() => (range.value === '15m' ? '原始采样' : '分钟级聚合'))

function dimensionText(metric) {
  const entries = Object.entries(metric.dimensions || {})
  if (!entries.length) return ''
  return entries.slice(0, 2).map(([key, value]) => `${key}=${value}`).join(' · ')
}

function formatValue(metric) {
  if (!metric.numeric) return String(metric.value ?? '—')
  const value = Number(metric.value)
  if (!Number.isFinite(value)) return '—'
  if (metric.unit === 'B') return fmtBytes(value)
  if (metric.unit === '%') return `${value.toFixed(1)}%`
  if (metric.unit === 'bool') return value ? '是' : '否'
  if (metric.unit === 'ms' || metric.unit === 'us') return `${value.toFixed(1)} ${metric.unit}`
  if (metric.unit === 's') return `${value.toFixed(1)} s`
  if (metric.unit === 'ratio') return value.toFixed(2)
  return `${Number.isInteger(value) ? value : value.toFixed(2)} ${metric.unit === 'count' ? '' : metric.unit}`.trim()
}

async function loadMonitor(force = false) {
  loading.value = true
  error.value = ''
  const seq = ++monitorSeq
  monitorController?.abort()
  monitorController = new AbortController()
  trendSeq += 1
  trendController?.abort()
  const { signal } = monitorController
  try {
    const [mr, dr] = await Promise.all([
      dsApi.monitor(props.kind, { force, instance: props.instance }, { signal, timeoutMs: 15000 }),
      dsApi.diagnostics(props.kind, { force, instance: props.instance }, { signal, timeoutMs: 15000 }),
    ])
    if (seq !== monitorSeq) return
    monitor.value = unwrap(mr).data
    diagnostics.value = unwrap(dr).data?.items || []
    if (!metricKey.value || !metricOptions.value.some((item) => item.key === metricKey.value)) {
      metricKey.value = metricOptions.value[0]?.key || ''
    }
    await nextTick()
    renderChart()
    if (metricKey.value) await loadTimeseries()
  } catch (e) {
    if (seq === monitorSeq && e.name !== 'AbortError') error.value = apiErrorText(e)
  } finally {
    if (seq === monitorSeq) loading.value = false
  }
}

async function loadTimeseries() {
  if (!metricKey.value) {
    trendSeq += 1
    trendController?.abort()
    series.value = []
    renderChart()
    return
  }
  const seq = ++trendSeq
  trendController?.abort()
  trendController = new AbortController()
  trendLoading.value = true
  try {
    const result = unwrap(await dsApi.timeseries(props.kind, {
      metrics: metricKey.value,
      range: range.value,
      resolution: range.value === '15m' ? 'raw' : range.value === '7d' ? '1h' : '5m',
      group_by: groupBy.value || undefined,
      instance: props.instance || undefined,
    }, { signal: trendController.signal, timeoutMs: 15000 }))
    if (seq !== trendSeq) return
    series.value = result.data?.series || []
    renderChart()
  } catch (e) {
    if (seq === trendSeq && e.name !== 'AbortError') {
      series.value = []
      renderChart()
    }
  } finally {
    if (seq === trendSeq) trendLoading.value = false
  }
}

function renderChart() {
  if (!chartEl.value) return
  chart ||= echarts.init(chartEl.value)
  const colors = ['#2563eb', '#059669', '#d97706', '#dc2626', '#7c3aed', '#0891b2', '#be185d', '#4d7c0f']
  const option = {
    tooltip: { trigger: 'axis' },
    legend: { type: 'scroll', top: 0, textStyle: { fontSize: 11 } },
    grid: { left: 52, right: 18, top: 38, bottom: 34 },
    dataZoom: [{ type: 'inside' }],
    xAxis: { type: 'time' },
    yAxis: { type: 'value', scale: true },
    series: series.value.slice(0, 12).map((item, index) => ({
      name: item.group ? `${item.metric_key} · ${item.group}` : item.metric_key,
      type: 'line',
      showSymbol: false,
      smooth: true,
      lineStyle: { color: colors[index % colors.length] },
      itemStyle: { color: colors[index % colors.length] },
      data: (item.points || []).map((point) => [new Date(point.timestamp), point.value]),
    })),
  }
  chart.setOption(option, true)
}

function reload() {
  loadMonitor(true)
}

function resize() {
  chart?.resize()
}

watch(() => props.kind, () => {
  monitor.value = null
  diagnostics.value = []
  series.value = []
  metricKey.value = ''
  groupBy.value = ''
  loadMonitor()
})

watch(() => props.instance, () => {
  monitor.value = null
  diagnostics.value = []
  series.value = []
  metricKey.value = ''
  loadMonitor()
})

onMounted(() => {
  loadMonitor()
  window.addEventListener('resize', resize)
})

onUnmounted(() => {
  monitorController?.abort()
  trendController?.abort()
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.monitor-panel { display: flex; flex-direction: column; gap: 12px; padding: 14px; border: 1px solid var(--border); border-radius: 8px; background: #fff; }
.panel-head, .trend-head, .diagnostic-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.panel-head h3 { margin: 0 0 2px; font-size: 14px; }
.controls { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.select { height: 30px; padding: 0 8px; border: 1px solid var(--border); border-radius: 6px; background: #fff; color: var(--text); }
.metric-select { min-width: 190px; }
.metric-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px; }
.metric-card { min-width: 0; padding: 10px; border: 1px solid var(--border); border-left: 3px solid var(--ok); border-radius: 7px; background: #fbfdff; }
.metric-card.warning { border-left-color: var(--warn); background: #fffbeb; }
.metric-card.critical { border-left-color: var(--err); background: #fef2f2; }
.metric-label { color: var(--muted); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.metric-card b { display: block; margin: 4px 0 2px; font-size: 17px; }
.metric-card small, .metric-source { display: block; color: var(--muted); font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.metric-source { margin-top: 4px; }
.trend-block { position: relative; padding-top: 4px; }
.trend-chart { height: 250px; }
.empty-trend { position: absolute; inset: 60px 0 0; display: grid; place-items: center; color: var(--muted); font-size: 12px; pointer-events: none; }
.diagnostic-block { border-top: 1px solid var(--border); padding-top: 10px; }
.diagnostic-list { display: grid; gap: 6px; margin-top: 8px; }
.diagnostic-item { display: flex; gap: 8px; align-items: flex-start; padding: 8px 10px; border-radius: 6px; background: #f8fafc; }
.diagnostic-item.warning { background: #fffbeb; }
.diagnostic-item.critical { background: #fef2f2; }
.diagnostic-dot { width: 7px; height: 7px; margin-top: 5px; flex: none; border-radius: 50%; background: var(--ok); }
.diagnostic-item.warning .diagnostic-dot { background: var(--warn); }
.diagnostic-item.critical .diagnostic-dot { background: var(--err); }
.diagnostic-item b, .diagnostic-item small { display: block; }
.diagnostic-item b { font-size: 12px; }
.diagnostic-item small { color: var(--muted); margin-top: 2px; }
.monitor-error { padding: 8px 10px; border: 1px solid #fecaca; border-radius: 7px; background: #fef2f2; color: var(--err); font-size: 12px; }
.monitor-notice { padding: 8px 10px; border: 1px solid #fde68a; border-radius: 7px; background: #fffbeb; color: #b45309; font-size: 12px; }
.small { font-size: 12px; }
@media (max-width: 700px) {
  .controls { width: 100%; }
  .controls .select, .controls .btn { flex: 1; min-width: 120px; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
