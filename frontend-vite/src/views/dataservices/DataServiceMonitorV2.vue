<template>
  <section class="monitor-shell" :class="`kind-${kind}`" :style="accentStyle">
    <header class="monitor-hero">
      <div class="hero-identity">
        <div class="kind-mark" aria-hidden="true">
          <span>{{ kindMeta.short }}</span>
          <i class="kind-pulse"></i>
        </div>
        <div class="hero-copy">
          <div class="hero-eyebrow">
            <span class="live-dot" :class="health.key"></span>
            <span>{{ kindMeta.group }}</span>
            <span class="eyebrow-sep">/</span>
            <span>{{ instanceName }}</span>
          </div>
          <div class="hero-title">
            <h3>{{ kindMeta.name }} 监控</h3>
            <span class="health-pill" :class="health.key">{{ health.label }}</span>
          </div>
          <div class="hero-meta">
            <span v-if="monitor?.endpoint" class="meta-item mono">
              <b>端点</b>{{ monitor.endpoint }}
            </span>
            <span v-if="monitor?.version" class="meta-item">
              <b>版本</b>{{ monitor.version }}
            </span>
            <span class="meta-item">
              <b>采集</b>{{ collectedText }}
            </span>
            <span class="meta-item">
              <b>质量</b>{{ qualityText }}
            </span>
          </div>
        </div>
      </div>
      <div class="hero-actions">
        <span v-if="monitor?.cached" class="cache-note">缓存 {{ cacheAgeText }}</span>
        <button class="icon-btn" :disabled="loading" title="刷新监控数据" aria-label="刷新监控数据" @click="reload">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M20 11a8 8 0 1 0-2.34 5.66M20 4v7h-7" />
          </svg>
        </button>
      </div>
    </header>

    <div class="summary-grid">
      <article class="summary-card">
        <span class="summary-icon">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19V9m5 10V5m5 14v-7m5 7V3" /></svg>
        </span>
        <div><small>指标项</small><b>{{ monitor?.metrics_total ?? '—' }}</b><em>当前采集范围</em></div>
      </article>
      <article class="summary-card">
        <span class="summary-icon">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18" /><circle cx="12" cy="12" r="8" /></svg>
        </span>
        <div><small>唯一指标</small><b>{{ uniqueMetrics.length }}</b><em>{{ dimensionCount }} 个维度</em></div>
      </article>
      <article class="summary-card" :class="{ alert: warningCount > 0 || criticalCount > 0 }">
        <span class="summary-icon">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2.5 20h19L12 3Z" /><path d="M12 9v5m0 3h.01" /></svg>
        </span>
        <div><small>关注指标</small><b>{{ warningCount + criticalCount }}</b><em>{{ criticalCount }} 严重 · {{ warningCount }} 警告</em></div>
      </article>
      <article class="summary-card">
        <span class="summary-icon">
          <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8" /><path d="M12 7v5l3 2" /></svg>
        </span>
        <div><small>探测延迟</small><b>{{ latencyValue }}</b><em>{{ latencyValue === '—' ? '暂无采样' : '状态接口往返' }}</em></div>
      </article>
    </div>

    <div v-if="error" class="monitor-alert error">
      <span>!</span>
      <div><b>监控数据读取失败</b><small>{{ error }}</small></div>
    </div>
    <div v-if="monitor?.metrics_truncated" class="monitor-alert notice">
      <span>i</span>
      <div><b>指标已按优先级保留</b><small>共采集 {{ monitor.metrics_total }} 项，当前展示监控契约允许的最大集合。</small></div>
    </div>

    <div v-if="loading && !monitor" class="monitor-loading">
      <span class="spinner"></span>
      <b>正在读取 {{ kindMeta.name }} 监控指标</b>
      <small>连接状态、运行指标与维度会同步加载</small>
    </div>

    <template v-else-if="monitor">
      <div class="monitor-toolbar">
        <div class="view-tabs" role="tablist" aria-label="监控指标视图">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            class="view-tab"
            :class="{ active: activeTab === tab.key }"
            role="tab"
            :aria-selected="activeTab === tab.key"
            @click="activeTab = tab.key"
          >
            {{ tab.label }}
            <span v-if="tab.count !== null">{{ tab.count }}</span>
          </button>
        </div>
        <div class="toolbar-filters">
          <label class="search-box">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></svg>
            <input v-model.trim="query" placeholder="搜索指标、Key 或来源" aria-label="搜索指标" />
          </label>
          <select v-model="range" class="control" aria-label="趋势时间范围" @change="loadTimeseries">
            <option v-for="item in rangeOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </div>
      </div>

      <div v-if="activeTab === 'issues'" class="issues-view">
        <div class="section-heading">
          <div><h4>需要关注的指标</h4><p>按严重程度排序，支持直接切换到趋势分析</p></div>
          <span class="section-count">{{ issueMetrics.length }} 项</span>
        </div>
        <div v-if="issueMetrics.length" class="metric-grid">
          <button
            v-for="metric in paginatedIssues"
            :key="`${metric.key}:${metric.dimension_key}`"
            class="metric-card issue-card"
            :class="[metric.status, { selected: isSelected(metric.key) }]"
            @click="selectMetric(metric.key)"
          >
            <span class="metric-topline">
              <span class="metric-category">{{ categoryLabel(metric) }}</span>
              <span class="status-text" :class="metric.status">{{ statusLabel(metric.status) }}</span>
            </span>
            <span class="metric-name">{{ metric.label }}</span>
            <strong>{{ formatValue(metric) }}</strong>
            <span class="metric-detail">{{ dimensionText(metric) || metric.key }}</span>
            <span class="metric-foot"><i></i>{{ metric.source || 'live' }}</span>
          </button>
        </div>
        <div v-else class="empty-state compact">
          <span class="empty-icon">✓</span>
          <b>当前没有异常指标</b>
          <small>连接状态和已采集指标均处于正常范围。</small>
        </div>
        <div v-if="issueMetrics.length > pageSize" class="issue-pagination">
          <button class="control" :disabled="issuePage <= 1" @click="issuePage -= 1">上一页</button>
          <span>{{ issuePage }} / {{ issuePages }}</span>
          <button class="control" :disabled="issuePage >= issuePages" @click="issuePage += 1">下一页</button>
        </div>
      </div>

      <template v-else>
        <div class="section-heading">
          <div>
            <h4>{{ activeTab === 'key' ? '关键指标' : '全部指标' }}</h4>
            <p>{{ activeTab === 'key' ? '按健康、连接、流量、延迟、容量和运行时自动归组' : '覆盖当前采集契约中的全部指标与维度' }}</p>
          </div>
          <span class="section-count">{{ visibleMetrics.length }} / {{ uniqueMetrics.length }}</span>
        </div>

        <div class="category-legend">
          <span v-for="item in visibleCategories" :key="item.key">
            <i :style="{ background: item.color }"></i>{{ item.label }}
          </span>
        </div>

        <div v-if="visibleMetrics.length" class="metric-grid" :class="{ dense: activeTab === 'all' }">
          <button
            v-for="metric in visibleMetrics"
            :key="metric.key"
            class="metric-card"
            :class="[metric.status, { selected: isSelected(metric.key), 'compact-card': activeTab === 'all' }]"
            :title="`查看 ${metric.label} 的趋势`"
            @click="selectMetric(metric.key)"
          >
            <span class="metric-topline">
              <span class="metric-category" :style="{ '--category-color': categoryInfo(metric).color }">
                <i></i>{{ categoryLabel(metric) }}
              </span>
              <span class="status-text" :class="metric.status">{{ statusLabel(metric.status) }}</span>
            </span>
            <span class="metric-name">{{ metric.label }}</span>
            <strong>{{ formatValue(metric) }}</strong>
            <span v-if="activeTab === 'all'" class="metric-detail">{{ dimensionText(metric) || '无维度' }}</span>
            <span class="metric-foot">
              <span>{{ metric.source || 'live' }}</span>
              <i>{{ metric.variants > 1 ? `${metric.variants} 个维度值` : '单维度' }}</i>
            </span>
          </button>
        </div>
        <div v-else class="empty-state compact">
          <span class="empty-icon">⌕</span>
          <b>没有匹配的指标</b>
          <small>尝试清空搜索词，或切换到“全部指标”。</small>
        </div>

        <section class="trend-panel">
          <header class="trend-header">
            <div class="trend-title">
              <span class="trend-mark">
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 17 9 11l4 4 8-9" /></svg>
              </span>
              <div>
                <h4>{{ selectedMetricLabel }}</h4>
                <p>{{ rangeLabel }} · {{ resolutionLabel }} · {{ seriesCount }} 条曲线</p>
              </div>
              <div class="latest-value">
                <small>最新值</small>
                <b>{{ latestTrendValue }}</b>
              </div>
            </div>
            <div class="trend-controls">
              <select v-model="metricKey" class="control metric-control" aria-label="趋势指标" @change="loadTimeseries">
                <option v-for="item in metricOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
              </select>
              <select v-model="groupBy" class="control" aria-label="趋势分组维度" @change="loadTimeseries">
                <option value="">不分组</option>
                <option v-for="item in dimensionOptions" :key="item.key" :value="item.key">{{ item.label }}</option>
              </select>
            </div>
          </header>

          <div class="quick-metrics">
            <span>快速切换</span>
            <button
              v-for="item in quickMetrics"
              :key="item.key"
              :class="{ active: item.key === metricKey }"
              @click="selectMetric(item.key)"
            >
              {{ item.label }}
            </button>
          </div>

          <div class="chart-wrap">
            <div ref="chartEl" class="trend-chart"></div>
            <div v-if="trendLoading" class="chart-overlay">
              <span class="spinner"></span>正在读取趋势
            </div>
            <div v-else-if="!seriesCount" class="chart-overlay empty">
              <span class="empty-icon">∿</span>
              <b>暂无历史采样</b>
              <small>采样开启后，指标会按时间范围逐步形成趋势。</small>
            </div>
          </div>
        </section>
      </template>

      <section class="diagnostic-panel">
        <header class="section-heading">
          <div><h4>异常诊断</h4><p>按阈值规则和采集错误生成的即时结论</p></div>
          <span class="section-count" :class="{ warn: diagnostics.length > 0 }">{{ diagnostics.length }} 项</span>
        </header>
        <div v-if="diagnostics.length" class="diagnostic-grid">
          <article
            v-for="(item, index) in diagnostics"
            :key="`${item.metric_key || item.title}:${index}`"
            class="diagnostic-card"
            :class="item.level"
          >
            <span class="diagnostic-icon">{{ diagnosticIcon(item.level) }}</span>
            <div>
              <b>{{ item.title }}</b>
              <p>{{ item.message }}</p>
              <small v-if="item.metric_key" class="mono">{{ item.metric_key }}</small>
            </div>
          </article>
        </div>
        <div v-else class="empty-state compact inline">
          <span class="empty-icon">✓</span>
          <div><b>暂无诊断信息</b><small>采集任务尚未返回诊断结果。</small></div>
        </div>
      </section>
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

const KIND_META = {
  redis: { name: 'Redis', short: 'R', group: '缓存与分布式锁', color: '#dc2626', soft: '#fff1f2', rgb: '220, 38, 38' },
  rabbitmq: { name: 'RabbitMQ', short: 'Q', group: '消息队列', color: '#ea580c', soft: '#fff7ed', rgb: '234, 88, 12' },
  kafka: { name: 'Kafka', short: 'K', group: '事件流平台', color: '#4f46e5', soft: '#eef2ff', rgb: '79, 70, 229' },
  zookeeper: { name: 'ZooKeeper', short: 'Z', group: '协调服务', color: '#0891b2', soft: '#ecfeff', rgb: '8, 145, 178' },
  nacos: { name: 'Nacos', short: 'N', group: '注册与配置中心', color: '#2563eb', soft: '#eff6ff', rgb: '37, 99, 235' },
  minio: { name: 'MinIO', short: 'M', group: '对象存储', color: '#c2410c', soft: '#fff7ed', rgb: '194, 65, 12' },
  mongodb: { name: 'MongoDB', short: 'D', group: '文档数据库', color: '#15803d', soft: '#f0fdf4', rgb: '21, 128, 61' },
}
const FALLBACK_META = { name: props.kind || '数据服务', short: 'S', group: '中间件监控', color: '#475569', soft: '#f8fafc', rgb: '71, 85, 105' }

const CATEGORIES = [
  { key: 'health', label: '健康状态', color: '#059669', keys: ['available', 'configured', 'alive', 'health', 'uptime', 'status'] },
  { key: 'connection', label: '连接会话', color: '#2563eb', keys: ['connection', 'connected', 'client', 'session', 'member', 'broker', 'node', 'peer', 'endpoint'] },
  { key: 'traffic', label: '吞吐流量', color: '#0891b2', keys: ['ops', 'command', 'request', 'message', 'record', 'throughput', 'network', 'input', 'output', 'publish', 'consume', 'produce'] },
  { key: 'latency', label: '延迟耗时', color: '#7c3aed', keys: ['latency', 'duration', 'elapsed', 'age', 'lag', 'delay', 'wait'] },
  { key: 'capacity', label: '容量资源', color: '#d97706', keys: ['memory', 'disk', 'storage', 'size', 'bytes', 'usage', 'used', 'free', 'object', 'key', 'topic', 'partition', 'queue', 'database', 'collection', 'bucket', 'znode'] },
  { key: 'runtime', label: '运行状态', color: '#4f46e5', keys: ['cpu', 'thread', 'process', 'gc', 'load', 'replica', 'version', 'session', 'fd', 'cache'] },
  { key: 'risk', label: '异常风险', color: '#dc2626', keys: ['error', 'fail', 'reject', 'evict', 'expire', 'offline', 'unhealthy', 'alarm', 'under_replicated', 'partition', 'lost', 'timeout'] },
]
const CATEGORY_MAP = Object.fromEntries(CATEGORIES.map((item) => [item.key, item]))
const CATEGORY_FALLBACK = { key: 'other', label: '其他指标', color: '#64748b' }

const monitor = ref(null)
const diagnostics = ref([])
const error = ref('')
const loading = ref(false)
const trendLoading = ref(false)
const activeTab = ref('key')
const query = ref('')
const range = ref('1h')
const metricKey = ref('')
const groupBy = ref('')
const series = ref([])
const chartEl = ref(null)
const issuePage = ref(1)
const pageSize = 12
let chart = null
let monitorController = null
let trendController = null
let monitorSeq = 0
let trendSeq = 0
let resizeObserver = null

const kindMeta = computed(() => KIND_META[props.kind] || { ...FALLBACK_META, name: props.kind })
const accentStyle = computed(() => ({
  '--kind-accent': kindMeta.value.color,
  '--kind-soft': kindMeta.value.soft,
  '--kind-rgb': kindMeta.value.rgb,
}))
const rangeOptions = [
  { value: '15m', label: '15 分钟' },
  { value: '1h', label: '1 小时' },
  { value: '6h', label: '6 小时' },
  { value: '24h', label: '24 小时' },
  { value: '7d', label: '7 天' },
]
const statusOrder = { critical: 0, warning: 1, unknown: 2, ok: 3 }

function metricCategory(metric) {
  const key = String(metric?.key || '').toLowerCase()
  if (!key) return CATEGORY_FALLBACK
  if (key.startsWith('data_service_')) return CATEGORY_MAP.health
  const category = CATEGORIES.find((item) => item.keys.some((token) => key.includes(token)))
  return category || CATEGORY_FALLBACK
}
function categoryInfo(metric) {
  return metricCategory(metric)
}
function categoryLabel(metric) {
  return metricCategory(metric).label
}
function metricRank(metric) {
  const key = String(metric?.key || '')
  const category = metricCategory(metric)
  const thresholded = /(available|usage|percent|lag|alarm|partition|age|latency|unhealthy|offline|rejected|evicted|fragmentation)/.test(key)
  const systemRank = key.startsWith('data_service_') ? 0 : thresholded ? 1 : key.includes('_total') ? 3 : 2
  return [(statusOrder[metric?.status] ?? 9), systemRank, category.key === 'other' ? 1 : 0, key]
}
function compareMetrics(a, b) {
  const ar = metricRank(a)
  const br = metricRank(b)
  for (let index = 0; index < ar.length; index += 1) {
    if (ar[index] < br[index]) return -1
    if (ar[index] > br[index]) return 1
  }
  return 0
}

const uniqueMetrics = computed(() => {
  const grouped = new Map()
  for (const metric of monitor.value?.metrics || []) {
    const key = metric.key
    if (!key) continue
    const current = grouped.get(key)
    if (!current) {
      grouped.set(key, { ...metric, variants: 1, dimensionKeys: new Set([metric.dimension_key || '']) })
      continue
    }
    current.variants += 1
    current.dimensionKeys.add(metric.dimension_key || '')
    if ((statusOrder[metric.status] ?? 9) < (statusOrder[current.status] ?? 9)) {
      Object.assign(current, {
        value: metric.value,
        display_value: metric.display_value,
        unit: metric.unit,
        numeric: metric.numeric,
        status: metric.status,
        dimensions: metric.dimensions,
        dimension_key: metric.dimension_key,
        source: metric.source,
      })
    }
  }
  return [...grouped.values()].map((item) => ({ ...item, dimensionKeys: undefined })).sort(compareMetrics)
})
const uniqueMetricsByCategory = computed(() => {
  const counts = new Map()
  for (const metric of uniqueMetrics.value) {
    const category = metricCategory(metric)
    counts.set(category.key, (counts.get(category.key) || 0) + 1)
  }
  return counts
})
const normalizedQuery = computed(() => query.value.toLowerCase())
const searchedMetrics = computed(() => uniqueMetrics.value.filter((metric) => {
  if (!normalizedQuery.value) return true
  return [metric.key, metric.label, metric.source, ...Object.values(metric.dimensions || {})]
    .some((value) => String(value || '').toLowerCase().includes(normalizedQuery.value))
}))
const keyMetrics = computed(() => {
  const result = []
  const perCategory = new Map()
  for (const metric of searchedMetrics.value) {
    const category = metricCategory(metric)
    const count = perCategory.get(category.key) || 0
    if (count >= 3) continue
    result.push(metric)
    perCategory.set(category.key, count + 1)
    if (result.length >= 18) break
  }
  return result
})
const issueMetrics = computed(() => searchedMetrics.value.filter((metric) => ['critical', 'warning', 'unknown'].includes(metric.status)))
const visibleMetrics = computed(() => {
  if (activeTab.value === 'all') return searchedMetrics.value.slice(0, 60)
  return keyMetrics.value
})
const issuePages = computed(() => Math.max(1, Math.ceil(issueMetrics.value.length / pageSize)))
const paginatedIssues = computed(() => {
  const start = (issuePage.value - 1) * pageSize
  return issueMetrics.value.slice(start, start + pageSize)
})
const visibleCategories = computed(() => {
  const keys = new Set(visibleMetrics.value.map((metric) => metricCategory(metric).key))
  return CATEGORIES.filter((item) => keys.has(item.key))
})
const metricOptions = computed(() => (monitor.value?.metrics || [])
  .filter((item) => item.numeric && item.key)
  .filter((item, index, items) => items.findIndex((candidate) => candidate.key === item.key) === index)
  .map((item) => ({ key: item.key, label: item.label || item.key }))
  .slice(0, 100))
const dimensionOptions = computed(() => (monitor.value?.dimensions || []).filter((item) => item.key && item.values?.length))
const selectedMetric = computed(() => monitor.value?.metrics?.find((item) => item.key === metricKey.value) || null)
const selectedMetricLabel = computed(() => metricOptions.value.find((item) => item.key === metricKey.value)?.label || metricKey.value || '历史趋势')
const quickMetrics = computed(() => {
  const selectedCategory = metricCategory(selectedMetric.value || {})
  const options = metricOptions.value
  const preferred = options.filter((item) => metricCategory(item).key === selectedCategory.key)
  const rest = options.filter((item) => metricCategory(item).key !== selectedCategory.key)
  return [...preferred, ...rest].slice(0, 6)
})
const warningCount = computed(() => (monitor.value?.metrics || []).filter((item) => item.status === 'warning').length)
const criticalCount = computed(() => (monitor.value?.metrics || []).filter((item) => item.status === 'critical').length)
const dimensionCount = computed(() => monitor.value?.dimensions?.length || 0)
const instanceName = computed(() => {
  if (props.instance === 'primary') return monitor.value?.instance_name || '主实例'
  return monitor.value?.instance_name || props.instance
})
const health = computed(() => {
  if (!monitor.value) return { key: 'unknown', label: '检测中' }
  if (!monitor.value.configured) return { key: 'warning', label: '未配置' }
  if (!monitor.value.available) return { key: 'critical', label: '不可用' }
  if (criticalCount.value) return { key: 'critical', label: '存在严重指标' }
  if (warningCount.value) return { key: 'warning', label: '需要关注' }
  return { key: 'ok', label: '运行正常' }
})
const latencyValue = computed(() => {
  const value = Number(monitor.value?.latency_ms)
  if (!Number.isFinite(value)) return '—'
  return value < 10 ? `${value.toFixed(1)} ms` : `${Math.round(value)} ms`
})
const collectedText = computed(() => {
  const stamp = Number(monitor.value?.collected_at)
  if (!Number.isFinite(stamp)) return '—'
  return new Date(stamp * 1000).toLocaleTimeString('zh-CN', { hour12: false })
})
const qualityText = computed(() => {
  if (monitor.value?.quality === 'ok') return '完整'
  if (monitor.value?.partial_errors?.length) return `${monitor.value.partial_errors.length} 项降级`
  return monitor.value?.quality === 'unknown' ? '未知' : '正常'
})
const cacheAgeText = computed(() => `${Math.max(0, Number(monitor.value?.cache_age_seconds || 0)).toFixed(0)}s`)
const seriesCount = computed(() => series.value.length)
const rangeLabel = computed(() => rangeOptions.find((item) => item.value === range.value)?.label || range.value)
const resolutionLabel = computed(() => (range.value === '15m' ? '原始采样' : range.value === '7d' ? '小时聚合' : '分钟聚合'))
const latestTrendValue = computed(() => {
  let latest = null
  for (const item of series.value) {
    const point = item.points?.[item.points.length - 1]
    if (!point) continue
    const stamp = new Date(point.timestamp).getTime()
    if (!latest || stamp > latest.stamp) latest = { stamp, value: point.value, unit: item.unit }
  }
  return latest ? formatUnit(latest.value, latest.unit) : '—'
})
function preferredMetricKey(options) {
  const keys = new Set(options.map((item) => item.key))
  const preferred = [
    'redis_memory_usage_percent',
    'kafka_consumer_lag_total',
    'rabbitmq_messages_ready',
    'nacos_unhealthy_instances',
    'mongodb_replication_lag_seconds',
    'minio_capacity_usage_percent',
    'zookeeper_outstanding_requests',
  ]
  const exact = preferred.find((key) => keys.has(key))
  if (exact) return exact
  return options.find((item) => /(_usage_percent|_lag_total|_lag_seconds|_latency_ms)$/.test(item.key))?.key
    || options.find((item) => !item.key.startsWith('data_service_') && item.key !== 'data_service_available')?.key
    || options[0]?.key
    || ''
}
const tabs = computed(() => [
  { key: 'key', label: '关键指标', count: keyMetrics.value.length },
  { key: 'all', label: '全部指标', count: uniqueMetrics.value.length },
  { key: 'issues', label: '关注', count: issueMetrics.value.length },
])

function statusLabel(status) {
  return { critical: '严重', warning: '警告', unknown: '未知', ok: '正常' }[status] || '正常'
}
function diagnosticIcon(level) {
  return { critical: '!', warning: '!', ok: '✓' }[level] || 'i'
}
function isSelected(key) {
  return metricKey.value === key
}
function dimensionText(metric) {
  const entries = Object.entries(metric?.dimensions || {})
  if (!entries.length) return ''
  return entries.slice(0, 2).map(([key, value]) => `${key}=${value}`).join(' · ')
}
function formatUnit(value, unit) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  if (unit === 'B' || unit === 'bytes') return fmtBytes(number)
  if (unit === '%') return `${number.toFixed(number >= 100 ? 0 : 1)}%`
  if (unit === 'bool') return number ? '是' : '否'
  if (unit === 'ms') return `${number < 100 ? number.toFixed(1) : Math.round(number)} ms`
  if (unit === 'us') return `${number.toFixed(1)} µs`
  if (unit === 's') return number >= 3600 ? `${(number / 3600).toFixed(1)} h` : number >= 60 ? `${(number / 60).toFixed(1)} min` : `${number.toFixed(number < 10 ? 1 : 0)} s`
  if (unit === 'ops/s' || unit === 'B/s') return `${number.toFixed(number < 10 ? 1 : 0)} ${unit}`
  if (unit === 'ratio') return number.toFixed(2)
  if (unit === 'count' || !unit) return number.toLocaleString('zh-CN', { maximumFractionDigits: number < 100 ? 2 : 0 })
  return `${number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ${unit}`
}
function formatValue(metric) {
  if (metric?.display_value) return metric.display_value
  if (!metric?.numeric) return String(metric?.value ?? '—')
  return formatUnit(metric.value, metric.unit)
}
function axisLabel(value) {
  if (selectedMetric.value?.unit === 'bool') return Number(value) >= 0.5 ? '是' : '否'
  const unit = selectedMetric.value?.unit
  if (unit === 'B' || unit === 'bytes') return fmtBytes(Number(value))
  if (unit === '%') return `${value}%`
  if (unit === 'ms' && Number(value) >= 1000) return `${(Number(value) / 1000).toFixed(1)}s`
  if (unit === 's' && Number(value) >= 3600) return `${(Number(value) / 3600).toFixed(1)}h`
  return Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 1 })
}
function tooltipFormatter(params) {
  if (!params?.length) return ''
  const time = new Date(params[0].value[0]).toLocaleString('zh-CN', { hour12: false })
  const unit = selectedMetric.value?.unit
  const rows = params.map((item) => (
    `<div style="display:flex;justify-content:space-between;gap:18px;margin-top:5px">`
    + `<span>${item.marker}${item.seriesName}</span>`
    + `<b>${formatUnit(item.value[1], unit)}</b></div>`
  )).join('')
  return `<div style="min-width:170px"><div style="color:#64748b;font-size:11px">${time}</div>${rows}</div>`
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
    const [monitorResult, diagnosticResult] = await Promise.all([
      dsApi.monitor(props.kind, { force, instance: props.instance }, { signal, timeoutMs: 15000 }),
      dsApi.diagnostics(props.kind, { force, instance: props.instance }, { signal, timeoutMs: 15000 }),
    ])
    if (seq !== monitorSeq) return
    monitor.value = unwrap(monitorResult).data
    diagnostics.value = unwrap(diagnosticResult).data?.items || []
    const options = metricOptions.value
    if (!metricKey.value || !options.some((item) => item.key === metricKey.value)) {
      metricKey.value = preferredMetricKey(options)
    }
    issuePage.value = 1
    await nextTick()
    renderChart()
    if (metricKey.value) await loadTimeseries()
  } catch (caught) {
    if (seq === monitorSeq && caught.name !== 'AbortError') error.value = apiErrorText(caught)
  } finally {
    if (seq === monitorSeq) loading.value = false
  }
}

async function loadTimeseries() {
  issuePage.value = 1
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
  } catch (caught) {
    if (seq === trendSeq && caught.name !== 'AbortError') {
      series.value = []
      renderChart()
    }
  } finally {
    if (seq === trendSeq) trendLoading.value = false
  }
}

function selectMetric(key) {
  if (!key) return
  if (key === metricKey.value) {
    loadTimeseries()
    return
  }
  metricKey.value = key
  loadTimeseries()
}

function renderChart() {
  if (!chartEl.value) return
  chart ||= echarts.init(chartEl.value)
  const colors = [kindMeta.value.color, '#0891b2', '#d97706', '#7c3aed', '#059669', '#dc2626', '#2563eb', '#be185d']
  if (!resizeObserver && 'ResizeObserver' in window) {
    resizeObserver = new ResizeObserver(() => resize())
    resizeObserver.observe(chartEl.value)
  }
  const option = {
    animationDuration: 260,
    color: colors,
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,.98)',
      borderColor: '#e2e8f0',
      borderWidth: 1,
      padding: [9, 11],
      textStyle: { color: '#0f172a', fontSize: 12 },
      formatter: tooltipFormatter,
    },
    legend: seriesCount.value > 1 ? {
      type: 'scroll',
      top: 0,
      right: 4,
      itemWidth: 14,
      itemHeight: 3,
      textStyle: { color: '#64748b', fontSize: 11 },
    } : { show: false },
    grid: { left: 54, right: 18, top: seriesCount.value > 1 ? 34 : 18, bottom: 34 },
    dataZoom: [{ type: 'inside', throttle: 80 }],
    xAxis: {
      type: 'time',
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#e2e8f0' } },
      axisTick: { show: false },
      axisLabel: { color: '#94a3b8', fontSize: 10, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      ...(selectedMetric.value?.unit === 'bool' ? { min: 0, max: 1, interval: 1 } : {}),
      type: 'value',
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#94a3b8', fontSize: 10, formatter: axisLabel },
      splitLine: { lineStyle: { color: '#edf2f7', type: 'dashed' } },
    },
    series: series.value.slice(0, 12).map((item, index) => ({
      name: item.group ? String(item.group) : (selectedMetric.value?.label || item.metric_key),
      type: 'line',
      showSymbol: false,
      smooth: 0.18,
      lineStyle: { width: 2, color: colors[index % colors.length] },
      itemStyle: { color: colors[index % colors.length] },
      areaStyle: series.value.length === 1 ? {
        opacity: 0.08,
        color: colors[index % colors.length],
      } : undefined,
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
function handleVisibility() {
  resize()
}

watch(() => props.kind, () => {
  monitor.value = null
  diagnostics.value = []
  series.value = []
  metricKey.value = ''
  groupBy.value = ''
  query.value = ''
  activeTab.value = 'key'
  loadMonitor()
})
watch(() => props.instance, () => {
  monitor.value = null
  diagnostics.value = []
  series.value = []
  metricKey.value = ''
  groupBy.value = ''
  loadMonitor()
})
watch(issuePages, (pages) => {
  if (issuePage.value > pages) issuePage.value = pages
})

onMounted(() => {
  loadMonitor()
  window.addEventListener('resize', resize)
  document.addEventListener('visibilitychange', handleVisibility)
})
onUnmounted(() => {
  monitorController?.abort()
  trendController?.abort()
  window.removeEventListener('resize', resize)
  document.removeEventListener('visibilitychange', handleVisibility)
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.monitor-shell {
  --kind-accent: #475569;
  --kind-soft: #f8fafc;
  --kind-rgb: 71, 85, 105;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
  color: var(--text);
}
.monitor-hero {
  position: relative;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  min-width: 0;
  padding: 18px 20px;
  overflow: hidden;
  border: 1px solid rgba(var(--kind-rgb), .2);
  border-radius: 10px;
  background:
    radial-gradient(circle at 94% 8%, rgba(var(--kind-rgb), .12), transparent 32%),
    linear-gradient(120deg, var(--kind-soft), #fff 58%);
}
.monitor-hero::after {
  position: absolute;
  right: 22px;
  bottom: -42px;
  width: 150px;
  height: 90px;
  border: 1px solid rgba(var(--kind-rgb), .12);
  border-radius: 50%;
  content: "";
  transform: rotate(-12deg);
  pointer-events: none;
}
.hero-identity { display: flex; align-items: flex-start; gap: 14px; min-width: 0; }
.kind-mark {
  position: relative;
  display: grid;
  flex: 0 0 auto;
  width: 48px;
  height: 48px;
  place-items: center;
  border: 1px solid rgba(var(--kind-rgb), .2);
  border-radius: 10px;
  background: #fff;
  box-shadow: 0 8px 22px rgba(var(--kind-rgb), .12);
  color: var(--kind-accent);
  font-size: 20px;
  font-weight: 800;
}
.kind-pulse {
  position: absolute;
  right: -3px;
  bottom: -3px;
  width: 13px;
  height: 13px;
  border: 3px solid #fff;
  border-radius: 50%;
  background: var(--kind-accent);
}
.hero-copy { min-width: 0; }
.hero-eyebrow {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #64748b;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .03em;
  text-transform: uppercase;
}
.live-dot { width: 7px; height: 7px; border-radius: 50%; background: #94a3b8; }
.live-dot.ok { background: #10b981; box-shadow: 0 0 0 3px #d1fae5; }
.live-dot.warning { background: #f59e0b; box-shadow: 0 0 0 3px #fef3c7; }
.live-dot.critical { background: #ef4444; box-shadow: 0 0 0 3px #fee2e2; }
.eyebrow-sep { color: #cbd5e1; }
.hero-title { display: flex; align-items: center; gap: 10px; margin-top: 5px; flex-wrap: wrap; }
.hero-title h3 { margin: 0; font-size: 22px; line-height: 1.2; }
.health-pill {
  padding: 3px 9px;
  border: 1px solid #e2e8f0;
  border-radius: 999px;
  background: #f8fafc;
  color: #475569;
  font-size: 11px;
  font-weight: 700;
}
.health-pill.ok { border-color: #a7f3d0; background: #ecfdf5; color: #047857; }
.health-pill.warning { border-color: #fde68a; background: #fffbeb; color: #b45309; }
.health-pill.critical { border-color: #fecaca; background: #fef2f2; color: #b91c1c; }
.hero-meta { display: flex; align-items: center; gap: 7px 16px; flex-wrap: wrap; margin-top: 9px; color: #64748b; font-size: 11px; }
.meta-item { display: inline-flex; align-items: center; gap: 6px; min-width: 0; max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.meta-item b { color: #94a3b8; font-weight: 600; }
.hero-actions { position: relative; z-index: 1; display: flex; align-items: center; gap: 9px; flex: 0 0 auto; }
.cache-note { color: #94a3b8; font-size: 10px; }
.icon-btn {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  border: 1px solid rgba(var(--kind-rgb), .22);
  border-radius: 8px;
  background: #fff;
  color: var(--kind-accent);
  cursor: pointer;
}
.icon-btn:hover:not(:disabled) { background: var(--kind-soft); }
.icon-btn:disabled { cursor: wait; opacity: .55; }
.icon-btn svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.8; }
.summary-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.summary-card {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
  padding: 13px 14px;
  border: 1px solid #e2e8f0;
  border-radius: 9px;
  background: #fff;
}
.summary-card.alert { border-color: #fed7aa; background: #fffaf5; }
.summary-icon {
  display: grid;
  flex: 0 0 auto;
  width: 33px;
  height: 33px;
  place-items: center;
  border-radius: 8px;
  background: var(--kind-soft);
  color: var(--kind-accent);
}
.summary-card.alert .summary-icon { background: #ffedd5; color: #c2410c; }
.summary-icon svg { width: 17px; height: 17px; fill: none; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.7; }
.summary-card small, .summary-card em { display: block; color: #94a3b8; font-size: 10px; font-style: normal; }
.summary-card b { display: block; margin: 1px 0; font-size: 19px; line-height: 1.2; }
.summary-card em { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.monitor-alert {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid #fde68a;
  border-radius: 8px;
  background: #fffbeb;
}
.monitor-alert.error { border-color: #fecaca; background: #fef2f2; }
.monitor-alert > span { display: grid; flex: 0 0 auto; width: 19px; height: 19px; place-items: center; border-radius: 50%; background: #f59e0b; color: #fff; font-size: 11px; font-weight: 800; }
.monitor-alert.error > span { background: #ef4444; }
.monitor-alert b, .monitor-alert small { display: block; }
.monitor-alert b { font-size: 12px; }
.monitor-alert small { margin-top: 2px; color: #92400e; font-size: 11px; }
.monitor-alert.error small { color: #b91c1c; }
.monitor-loading { display: grid; min-height: 240px; place-items: center; align-content: center; gap: 8px; border: 1px solid #e2e8f0; border-radius: 10px; background: #fff; color: #64748b; }
.monitor-loading b { color: #334155; font-size: 13px; }
.monitor-loading small { color: #94a3b8; }
.monitor-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  border-bottom: 1px solid #e2e8f0;
}
.view-tabs { display: flex; align-items: center; gap: 2px; }
.view-tab {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  height: 37px;
  padding: 0 12px;
  border: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
}
.view-tab:hover { color: #0f172a; }
.view-tab.active { border-bottom-color: var(--kind-accent); color: #0f172a; }
.view-tab span { min-width: 20px; padding: 1px 6px; border-radius: 999px; background: #f1f5f9; color: #64748b; font-size: 10px; text-align: center; }
.view-tab.active span { background: var(--kind-soft); color: var(--kind-accent); }
.toolbar-filters { display: flex; align-items: center; gap: 8px; }
.search-box { position: relative; display: flex; align-items: center; }
.search-box svg { position: absolute; left: 9px; width: 14px; height: 14px; fill: none; stroke: #94a3b8; stroke-linecap: round; stroke-width: 1.8; }
.search-box input {
  width: 220px;
  height: 32px;
  padding: 0 10px 0 29px;
  border: 1px solid #dbe3ed;
  border-radius: 7px;
  outline: none;
  background: #fff;
  color: #0f172a;
  font-size: 12px;
}
.search-box input:focus { border-color: rgba(var(--kind-rgb), .55); box-shadow: 0 0 0 3px rgba(var(--kind-rgb), .08); }
.control {
  height: 32px;
  padding: 0 9px;
  border: 1px solid #dbe3ed;
  border-radius: 7px;
  background: #fff;
  color: #334155;
  font-size: 12px;
}
.section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; margin: 2px 0 10px; }
.section-heading h4 { margin: 0; font-size: 14px; }
.section-heading p { margin: 3px 0 0; color: #94a3b8; font-size: 11px; }
.section-count { padding: 3px 8px; border-radius: 999px; background: #f1f5f9; color: #64748b; font-size: 10px; white-space: nowrap; }
.section-count.warn { background: #fff7ed; color: #c2410c; }
.category-legend { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin: -2px 0 10px; color: #94a3b8; font-size: 10px; }
.category-legend span { display: inline-flex; align-items: center; gap: 5px; }
.category-legend i { width: 7px; height: 7px; border-radius: 50%; }
.metric-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }
.metric-grid.dense { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.metric-card {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 12px 13px 11px;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  border-radius: 9px;
  background: #fff;
  color: inherit;
  cursor: pointer;
  text-align: left;
  transition: border-color .16s ease, box-shadow .16s ease, transform .16s ease;
}
.metric-card::before {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  width: 3px;
  background: #10b981;
  content: "";
}
.metric-card.warning::before { background: #f59e0b; }
.metric-card.critical::before { background: #ef4444; }
.metric-card.unknown::before { background: #94a3b8; }
.metric-card:hover { border-color: rgba(var(--kind-rgb), .38); box-shadow: 0 8px 22px rgba(15, 23, 42, .07); transform: translateY(-1px); }
.metric-card.selected { border-color: var(--kind-accent); box-shadow: 0 0 0 3px rgba(var(--kind-rgb), .08); }
.metric-topline, .metric-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
.metric-category { display: inline-flex; align-items: center; gap: 5px; min-width: 0; color: #64748b; font-size: 10px; }
.metric-category i { width: 6px; height: 6px; flex: 0 0 auto; border-radius: 50%; background: var(--category-color, #64748b); }
.status-text { font-size: 10px; font-weight: 700; }
.status-text.ok { color: #059669; }
.status-text.warning { color: #d97706; }
.status-text.critical { color: #dc2626; }
.status-text.unknown { color: #64748b; }
.metric-name { display: block; margin-top: 9px; overflow: hidden; color: #64748b; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.metric-card strong { display: block; margin: 3px 0 8px; overflow: hidden; color: #0f172a; font-size: 20px; line-height: 1.15; text-overflow: ellipsis; white-space: nowrap; }
.compact-card strong { font-size: 18px; }
.metric-detail { display: block; min-height: 15px; margin: -3px 0 7px; overflow: hidden; color: #94a3b8; font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.metric-foot { padding-top: 7px; border-top: 1px solid #f1f5f9; color: #94a3b8; font-size: 9px; }
.metric-foot span, .metric-foot i { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.metric-foot i { font-style: normal; }
.issue-card { cursor: pointer; }
.issues-view .metric-grid { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.issue-pagination { display: flex; align-items: center; justify-content: center; gap: 10px; margin-top: 12px; color: #64748b; font-size: 11px; }
.issue-pagination button:disabled { cursor: not-allowed; opacity: .45; }
.empty-state { display: grid; min-height: 180px; place-items: center; align-content: center; gap: 6px; border: 1px dashed #cbd5e1; border-radius: 10px; background: #fbfdff; color: #94a3b8; text-align: center; }
.empty-state.compact { min-height: 150px; }
.empty-state.inline { display: flex; min-height: auto; justify-content: flex-start; padding: 14px; text-align: left; }
.empty-icon { display: grid; width: 30px; height: 30px; place-items: center; border-radius: 50%; background: #f1f5f9; color: #64748b; font-size: 15px; font-weight: 700; }
.empty-state b { color: #475569; font-size: 12px; }
.empty-state small { font-size: 10px; }
.trend-panel { margin-top: 16px; padding: 15px 16px 10px; border: 1px solid #e2e8f0; border-radius: 10px; background: #fff; }
.trend-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.trend-title { display: flex; align-items: center; gap: 10px; min-width: 0; }
.trend-mark { display: grid; flex: 0 0 auto; width: 34px; height: 34px; place-items: center; border-radius: 8px; background: var(--kind-soft); color: var(--kind-accent); }
.trend-mark svg { width: 18px; height: 18px; fill: none; stroke: currentColor; stroke-linecap: round; stroke-linejoin: round; stroke-width: 1.8; }
.trend-title h4 { margin: 0; font-size: 13px; }
.trend-title p { margin: 3px 0 0; color: #94a3b8; font-size: 10px; }
.latest-value { margin-left: 12px; padding-left: 13px; border-left: 1px solid #e2e8f0; }
.latest-value small { display: block; color: #94a3b8; font-size: 9px; }
.latest-value b { display: block; margin-top: 1px; color: var(--kind-accent); font-size: 14px; }
.trend-controls { display: flex; align-items: center; gap: 8px; }
.metric-control { width: 190px; }
.quick-metrics { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 13px; padding-top: 11px; border-top: 1px solid #f1f5f9; }
.quick-metrics > span { margin-right: 3px; color: #94a3b8; font-size: 10px; }
.quick-metrics button { padding: 4px 8px; border: 1px solid #e2e8f0; border-radius: 999px; background: #fff; color: #64748b; cursor: pointer; font-size: 10px; }
.quick-metrics button:hover { border-color: rgba(var(--kind-rgb), .35); color: var(--kind-accent); }
.quick-metrics button.active { border-color: rgba(var(--kind-rgb), .25); background: var(--kind-soft); color: var(--kind-accent); font-weight: 700; }
.chart-wrap { position: relative; min-height: 270px; }
.trend-chart { width: 100%; height: 270px; }
.chart-overlay { position: absolute; inset: 0; display: grid; place-items: center; align-content: center; gap: 7px; background: rgba(255, 255, 255, .86); color: #64748b; font-size: 11px; }
.chart-overlay.empty b { color: #475569; font-size: 12px; }
.chart-overlay.empty small { color: #94a3b8; font-size: 10px; }
.diagnostic-panel { margin-top: 2px; padding-top: 4px; }
.diagnostic-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.diagnostic-card { display: flex; align-items: flex-start; gap: 10px; padding: 11px 12px; border: 1px solid #e2e8f0; border-radius: 8px; background: #fff; }
.diagnostic-card.warning { border-color: #fde68a; background: #fffbeb; }
.diagnostic-card.critical { border-color: #fecaca; background: #fef2f2; }
.diagnostic-icon { display: grid; flex: 0 0 auto; width: 22px; height: 22px; place-items: center; border-radius: 50%; background: #dcfce7; color: #15803d; font-size: 11px; font-weight: 800; }
.diagnostic-card.warning .diagnostic-icon { background: #fef3c7; color: #b45309; }
.diagnostic-card.critical .diagnostic-icon { background: #fee2e2; color: #b91c1c; }
.diagnostic-card b { display: block; font-size: 12px; }
.diagnostic-card p { margin: 3px 0 0; color: #64748b; font-size: 10px; word-break: break-word; }
.diagnostic-card small { display: block; margin-top: 5px; color: #94a3b8; font-size: 9px; }
.mono { font-family: Consolas, 'Courier New', monospace; }
.spinner { width: 14px; height: 14px; border: 2px solid #e2e8f0; border-top-color: var(--kind-accent); border-radius: 50%; animation: monitor-spin .7s linear infinite; }
@keyframes monitor-spin { to { transform: rotate(360deg); } }
@media (max-width: 1180px) {
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-grid, .issues-view .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric-grid.dense { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 820px) {
  .monitor-hero { padding: 15px; }
  .hero-meta { display: grid; grid-template-columns: minmax(0, 1fr); gap: 5px; }
  .meta-item { max-width: 100%; }
  .monitor-toolbar { align-items: stretch; }
  .view-tabs { width: 100%; overflow-x: auto; }
  .view-tab { flex: 0 0 auto; }
  .toolbar-filters { width: 100%; }
  .search-box { flex: 1; min-width: 0; }
  .search-box input { width: 100%; }
  .trend-header { flex-direction: column; }
  .trend-controls { width: 100%; }
  .trend-controls .control, .metric-control { flex: 1; width: auto; min-width: 0; }
  .latest-value { margin-left: auto; }
  .diagnostic-grid { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 620px) {
  .hero-identity { gap: 10px; }
  .kind-mark { width: 42px; height: 42px; }
  .hero-title h3 { font-size: 18px; }
  .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .summary-card { align-items: flex-start; padding: 11px; }
  .summary-icon { width: 28px; height: 28px; }
  .summary-card b { font-size: 17px; }
  .summary-card em { white-space: normal; }
  .metric-grid, .metric-grid.dense, .issues-view .metric-grid { grid-template-columns: minmax(0, 1fr); }
  .metric-card strong { font-size: 19px; }
  .category-legend { gap: 8px; }
  .trend-panel { padding: 13px 11px 8px; }
  .trend-title { width: 100%; }
  .latest-value { margin-left: auto; }
  .trend-controls { flex-direction: column; }
  .trend-controls .control { width: 100%; }
  .chart-wrap, .trend-chart { min-height: 230px; height: 230px; }
}
</style>
