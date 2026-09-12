<template>
  <Teleport to="body">
    <div v-if="visible" class="drawer-mask" @click.self="$emit('close')">
      <aside class="detail-drawer">
        <header>
          <div>
            <h2>{{ server?.name || '主机详情' }}</h2>
            <p>{{ server?.host || '' }}<span v-if="server?.node_role"> · {{ server.node_role }}</span></p>
          </div>
          <button class="btn btn-ghost" @click="$emit('close')">✕</button>
        </header>

        <nav class="section-tabs">
          <button v-for="s in visibleSections" :key="s.id" class="s-tab" :class="{ active: section === s.id }" @click="activateSection(s.id)">{{ s.label }}</button>
        </nav>

        <div class="detail-body">
          <div v-if="ovLoading && !overview" class="loading"><span class="spinner"></span>加载中…</div>

          <section v-show="section === 'basic'">
            <div v-if="server" class="info-grid">
              <div class="info-item"><span class="muted">名称</span><b>{{ server.name }}</b></div>
              <div class="info-item"><span class="muted">状态</span><span class="tag" :class="server.status === 'online' ? 'tag-green' : 'tag-red'">{{ server.status === 'online' ? '在线' : '离线' }}</span></div>
              <div class="info-item"><span class="muted">主地址</span><span class="mono">{{ server.host }}:{{ server.ssh_port }}</span></div>
              <div class="info-item"><span class="muted">LAN IP</span><span class="mono">{{ server.lan_ip || '—' }}</span></div>
              <div class="info-item"><span class="muted">WG IP</span><span class="mono">{{ server.wireguard_ip || '—' }}</span></div>
              <div class="info-item"><span class="muted">监控地址</span><span>{{ monitoringChannelLabel(server) }} · <span class="mono">{{ monitoringAddress(server) }}</span></span></div>
              <div class="info-item"><span class="muted">角色 / 节点</span><span>{{ server.node_role || '—' }}<template v-if="server.kubernetes_node_name"> · {{ server.kubernetes_node_name }}</template></span></div>
              <div class="info-item"><span class="muted">运行时</span><span>{{ runtimeTypeLabel(server.runtime_type) }}</span></div>
              <div class="info-item"><span class="muted">最后在线</span><span>{{ fmtTime(server.last_seen || server.last_online_at) }}</span></div>
              <div class="info-item"><span class="muted">标签</span>
                <span>
                  <template v-if="(server.tags || []).length">
                    <span v-for="tag in server.tags" :key="tag" class="tag tag-slate tag-mini">{{ tag }}</span>
                  </template>
                  <template v-else>—</template>
                </span>
              </div>
              <div class="info-item wide"><span class="muted">备注</span><span>{{ server.remark || '—' }}</span></div>
              <div v-if="server.last_error" class="info-item wide"><span class="muted">最近错误</span><span class="err-text">{{ server.last_error }}</span></div>
            </div>
            <p v-if="ovNotes.length" class="notes muted">{{ ovNotes.join('；') }}</p>
          </section>

          <section v-show="section === 'resource'">
            <div class="metric-cards">
              <div v-for="card in resourceCards" :key="card.label" class="metric-card">
                <span class="muted">{{ card.label }}</span>
                <b>{{ card.value }}</b>
              </div>
            </div>
            <p class="notes muted" :class="{ 'freshness-stale': overview?.stale }">数据时间：{{ freshnessTime }}<template v-if="overview?.cached"> · 缓存 {{ Number(overview.cache_age_seconds || 0).toFixed(1) }} 秒</template><template v-if="metricSourceText"> · {{ metricSourceText }}</template><strong v-if="overview?.stale"> · 数据已过期</strong><template v-if="ovNotes.length"> · {{ ovNotes.join('；') }}</template></p>
          </section>

          <section v-show="section === 'trends'">
            <div class="range-row">
              <button v-for="r in ranges" :key="r" class="btn btn-sm" :class="trendRange === r ? 'btn-primary' : ''" @click="setRange(r)">{{ r }}</button>
            </div>
            <div v-if="trendLoading" class="loading"><span class="spinner"></span>加载趋势…</div>
            <div ref="chartEl" class="trend-chart"></div>
            <p v-if="trendNotes.length" class="notes muted">{{ trendNotes.join('；') }}</p>
          </section>

          <section v-show="section === 'services'">
            <div v-if="svcLoading" class="loading"><span class="spinner"></span>加载服务…</div>
            <div v-else-if="!services.length" class="empty-line muted">该主机暂无登记服务</div>
            <table v-else class="table mini-table">
              <thead><tr><th>服务</th><th>端口</th><th>状态</th><th>延迟</th><th>最近检查</th></tr></thead>
              <tbody>
                <tr v-for="svc in services" :key="svc.id">
                  <td>{{ svc.name }}</td>
                  <td class="mono">{{ svc.port || '—' }}</td>
                  <td><span class="tag" :class="svcTagClass(svc.health?.status)">{{ svcStatusLabel(svc.health?.status) }}</span></td>
                  <td class="mono">{{ svc.health?.latency_ms != null ? svc.health.latency_ms + ' ms' : '—' }}</td>
                  <td class="muted">{{ fmtTime(svc.health?.last_checked_at) }}</td>
                </tr>
              </tbody>
            </table>
            <p v-if="svcNotes.length" class="notes muted">{{ svcNotes.join('；') }}</p>
          </section>

          <section v-show="section === 'k3s'">
            <template v-if="k8sNode">
              <div class="info-grid">
                <div class="info-item"><span class="muted">Ready</span>
                  <span class="tag" :class="k8sReady === true ? 'tag-green' : k8sReady === false ? 'tag-red' : 'tag-slate'">{{ k8sReady === true ? 'Ready' : k8sReady === false ? 'NotReady' : '未知' }}</span>
                </div>
                <div class="info-item"><span class="muted">Pod 数</span><b>{{ k8sPodCount ?? '—' }}</b></div>
                <div class="info-item wide"><span class="muted">Taint（{{ k8sTaints.length }} 条）</span>
                  <span v-if="k8sTaints.length" class="taint-list mono">{{ taintSummary }}</span>
                  <span v-else>无</span>
                </div>
                <div class="info-item wide"><span class="muted">Condition</span>
                  <span v-if="k8sConditions.length">
                    <span v-for="cond in k8sConditions" :key="condKey(cond)" class="tag tag-mini" :class="String(condStatus(cond)).toLowerCase() === 'true' ? 'tag-green' : 'tag-amber'">{{ condName(cond) }}={{ condStatus(cond) }}</span>
                  </span>
                  <span v-else>—</span>
                </div>
                <div class="info-item"><span class="muted">内存压力</span><span class="tag" :class="pressureTag(k8sPressure.memory)">{{ pressureText(k8sPressure.memory) }}</span></div>
                <div class="info-item"><span class="muted">磁盘压力</span><span class="tag" :class="pressureTag(k8sPressure.disk)">{{ pressureText(k8sPressure.disk) }}</span></div>
                <div class="info-item"><span class="muted">PID 压力</span><span class="tag" :class="pressureTag(k8sPressure.pid)">{{ pressureText(k8sPressure.pid) }}</span></div>
              </div>
            </template>
            <div v-else class="empty-line muted">K8s 节点数据不可用</div>
            <p v-if="ovNotes.length" class="notes muted">{{ ovNotes.join('；') }}</p>
          </section>

          <section v-show="section === 'connectivity'">
            <div class="conn-rows">
              <div v-for="ch in ['lan', 'wireguard']" :key="ch" class="conn-row">
                <div class="conn-head">
                  <b>{{ ch === 'lan' ? '局域网 LAN' : 'WG / 外网' }}</b>
                  <span v-if="connectivity?.preferred === ch" class="tag tag-slate">首选</span>
                  <span v-if="connRow(ch)" class="tag" :class="connRow(ch).ok ? 'tag-green' : 'tag-red'">{{ connRow(ch).ok ? '正常' : '异常' }}</span>
                  <span v-else class="tag tag-slate">未探测</span>
                </div>
                <div class="conn-body">
                  <span class="mono">{{ connRow(ch)?.target || '—' }}</span>
                  <span class="muted">{{ connRow(ch)?.latency_ms != null ? connRow(ch).latency_ms + ' ms' : '—' }}</span>
                  <span v-if="connRow(ch)?.error" class="conn-err">{{ connRow(ch).error }}</span>
                  <span class="muted">{{ fmtTime(connRow(ch)?.checked_at) }}</span>
                </div>
              </div>
            </div>
            <div class="conn-eff">
              <div class="info-item"><span class="muted">生效目标（§7.2 分层）</span><b class="mono">{{ connectivity?.effective_target || '—' }}</b></div>
              <div class="info-item"><span class="muted">Agent</span><span>{{ agentStatusLabel(connectivity?.agent?.status) }}<template v-if="connectivity?.agent?.version"> · v{{ connectivity.agent.version }}</template> · 端口 {{ connectivity?.agent?.port || '—' }}</span></div>
            </div>
            <p v-if="connNotes.length" class="notes muted">{{ connNotes.join('；') }}</p>
            <button class="btn btn-sm" :disabled="connLoading" @click="loadConnectivity(true)">{{ connLoading ? '探测中…' : '↻ 重新探测' }}</button>
          </section>

          <section v-show="section === 'agent'">
            <div class="info-grid">
              <div class="info-item"><span class="muted">状态</span><span class="tag" :class="agentTagClass(server?.agent_status)">{{ agentStatusLabel(server?.agent_status) }}</span></div>
              <div class="info-item"><span class="muted">当前版本</span><b>{{ server?.agent_version ? 'v' + server.agent_version : '—' }}</b></div>
              <div class="info-item"><span class="muted">目标版本</span><b>{{ targetText }}</b></div>
              <div class="info-item"><span class="muted">待升级</span><span class="tag" :class="checkResult ? (checkResult.outdated ? 'tag-amber' : 'tag-green') : 'tag-slate'">{{ checkResult ? (checkResult.outdated ? '是' : '否') : '未检查' }}</span></div>
              <div class="info-item"><span class="muted">在线验证</span><span>{{ reachableText }}</span></div>
              <div class="info-item"><span class="muted">端口</span><span class="mono">{{ server?.agent_port || '—' }}</span></div>
            </div>
            <div class="agent-actions">
              <button class="btn btn-sm" :disabled="checking || !server" @click="checkAgent">{{ checking ? '检查中…' : '检查更新' }}</button>
            </div>
            <p v-if="checkNotes.length" class="notes muted">{{ checkNotes.join('；') }}</p>
            <AgentUpgradePanel v-if="server" :server="server" :visible="visible" @upgraded="onUpgraded" />
          </section>

          <section v-show="section === 'alerts'">
            <div v-if="alertLoading" class="loading"><span class="spinner"></span>加载告警…</div>
            <div v-else-if="!alerts.length" class="empty-line muted">暂无主机维度告警数据</div>
            <table v-else class="table mini-table">
              <thead><tr><th>规则</th><th>状态</th><th>当前值</th><th>触发时间</th><th>恢复时间</th></tr></thead>
              <tbody>
                <tr v-for="event in alerts" :key="event.id">
                  <td>{{ event.rule_name || '—' }}</td>
                  <td><span class="tag" :class="alertTagClass(event.status)">{{ alertLabel(event.status) }}</span></td>
                  <td class="mono">{{ event.current_value ?? '—' }}</td>
                  <td class="muted">{{ fmtTime(event.fired_at) }}</td>
                  <td class="muted">{{ fmtTime(event.recovered_at) }}</td>
                </tr>
              </tbody>
            </table>
          </section>
        </div>
      </aside>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import echarts from '../../utils/echarts'
import { api, fmtTime, toast } from '../../api'
import { useHostContext } from '../../hostContext'
import AgentUpgradePanel from './AgentUpgradePanel.vue'
import {
  agentStatusLabel, agentTagClass, monitoringAddress, monitoringChannelLabel, runtimeTypeLabel,
} from './hostAdmin'

const props = defineProps({
  visible: Boolean,
  hostId: { type: String, default: '' },
})
const emit = defineEmits(['close', 'upgraded'])

const { hosts } = useHostContext()

const overview = ref(null)
const ovLoading = ref(false)
const connectivity = ref(null)
const connLoading = ref(false)
const section = ref('basic')

const trendRange = ref('6h')
const trendData = ref(null)
const trendNotes = ref([])
const trendLoading = ref(false)
const trendLoaded = ref(false)
const chartEl = ref(null)
let chart = null

const services = ref([])
const svcNotes = ref([])
const svcLoading = ref(false)
const svcLoaded = ref(false)

const alerts = ref([])
const alertLoading = ref(false)
const alertLoaded = ref(false)

const checkResult = ref(null)
const checkNotes = ref([])
const checking = ref(false)

let detailController = null
let requestedId = ''

const sections = [
  { id: 'basic', label: '基本信息' },
  { id: 'resource', label: '资源概览' },
  { id: 'trends', label: '趋势图' },
  { id: 'services', label: '服务情况' },
  { id: 'k3s', label: 'K3s 情况' },
  { id: 'connectivity', label: '连接诊断' },
  { id: 'agent', label: 'Agent' },
  { id: 'alerts', label: '最近告警' },
]
const visibleSections = computed(() => {
  const hasNodeName = !!(overview.value?.server?.kubernetes_node_name || contextServer.value?.kubernetes_node_name)
  return sections.filter((s) => s.id !== 'k3s' || hasNodeName)
})

const contextServer = computed(() => hosts.value.find((h) => h.id === props.hostId) || null)
const server = computed(() => overview.value?.server || contextServer.value)
const metrics = computed(() => overview.value?.metrics || null)
const ovNotes = computed(() => overview.value?.notes || [])
const connNotes = computed(() => connectivity.value?.notes || [])
const k8sNode = computed(() => overview.value?.k8s_node || null)
const freshnessTime = computed(() => {
  const value = overview.value?.data_timestamp || metrics.value?.ts
  const numeric = typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value || ''))
  return fmtTime(numeric ? Number(value) * 1000 : value)
})
const metricSourceText = computed(() => ({
  ok: '指标历史', fallback_summary_cache: '摘要缓存', empty: '无监控数据',
})[overview.value?.source_status?.metrics] || '')

const targetText = computed(() => {
  const version = checkResult.value?.target_version
  if (version) return `v${version}`
  if (checkResult.value) return '—'
  return '点击「检查更新」获取'
})
const reachableText = computed(() => {
  if (!checkResult.value || checkResult.value.reachable === null || checkResult.value.reachable === undefined) return '未验证'
  return checkResult.value.reachable ? '通过' : '失败'
})

const ranges = ['1h', '6h', '24h', '7d', '30d']

const resourceCards = computed(() => {
  const m = metrics.value || {}
  const pct = (value) => (value == null ? '—' : `${Math.round(value)}%`)
  return [
    { label: 'CPU', value: pct(m.cpu) },
    { label: '内存', value: pct(m.memory) },
    { label: '磁盘', value: pct(m.disk) },
    { label: '负载', value: m.load == null ? '—' : Number(m.load).toFixed(2) },
    { label: '网络入', value: fmtNet(m.net_in) },
    { label: '网络出', value: fmtNet(m.net_out) },
  ]
})

function fmtNet(value) {
  if (value == null) return '—'
  const kb = Number(value) / 1024
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB/s`
  return `${kb.toFixed(1)} KB/s`
}

function connRow(channel) {
  return connectivity.value?.[channel] || null
}
function svcStatusLabel(status) {
  return { up: '正常', healthy: '正常', ok: '正常', down: '异常', failing: '异常', error: '异常', unknown: '未知' }[status] || status || '未知'
}
function svcTagClass(status) {
  return { up: 'tag-green', healthy: 'tag-green', ok: 'tag-green', down: 'tag-red', failing: 'tag-red', error: 'tag-red', unknown: 'tag-slate' }[status] || 'tag-slate'
}
function alertLabel(status) {
  return { resolved: '已恢复', acked: '已确认', firing: '触发中', pending: '待处理' }[status] || status || '—'
}
function alertTagClass(status) {
  return { resolved: 'tag-green', acked: 'tag-amber', firing: 'tag-red', pending: 'tag-red' }[status] || 'tag-slate'
}

function k8sReadyValue(node) {
  const raw = node.ready ?? node.is_ready ?? node.Ready
    ?? (Array.isArray(node.conditions)
      ? node.conditions.find((c) => String(c.type || c.name || '').toLowerCase() === 'ready')?.status
      : undefined)
  if (raw === true || raw === 'True' || raw === 'true') return true
  if (raw === false || raw === 'False' || raw === 'false') return false
  return null
}
const k8sReady = computed(() => (k8sNode.value ? k8sReadyValue(k8sNode.value) : null))
const k8sTaints = computed(() => (Array.isArray(k8sNode.value?.taints) ? k8sNode.value.taints : []))
const taintSummary = computed(() => k8sTaints.value
  .slice(0, 3)
  .map((t) => `${t.key || '?'}${t.value ? '=' + t.value : ''}:${t.effect || ''}`)
  .join('  '))
const k8sConditions = computed(() => (Array.isArray(k8sNode.value?.conditions) ? k8sNode.value.conditions.slice(0, 6) : []))
const condName = (cond) => cond.type || cond.name || '?'
const condStatus = (cond) => String(cond.status ?? cond.value ?? '?')
const condKey = (cond) => `${condName(cond)}-${condStatus(cond)}`
const k8sPodCount = computed(() => {
  const value = k8sNode.value?.pod_count ?? k8sNode.value?.pods_count ?? k8sNode.value?.pods
  return (typeof value === 'number' || (typeof value === 'string' && value !== '')) ? value : null
})
function pressureOf(node, keys) {
  for (const key of keys) {
    if (node[key] !== undefined && node[key] !== null) return node[key]
  }
  return null
}
const k8sPressure = computed(() => {
  const node = k8sNode.value || {}
  return {
    memory: pressureOf(node, ['memory_pressure', 'memoryPressure']),
    disk: pressureOf(node, ['disk_pressure', 'diskPressure']),
    pid: pressureOf(node, ['pid_pressure', 'pidPressure']),
  }
})
const pressureText = (value) => (value == null ? '—' : (value === true || value === 'True' || value === 'true' ? '有压力' : '正常'))
const pressureTag = (value) => (value == null ? 'tag-slate' : (value === true || value === 'True' || value === 'true' ? 'tag-red' : 'tag-green'))

function openLoad() {
  detailController?.abort()
  detailController = new AbortController()
  requestedId = props.hostId
  overview.value = null
  connectivity.value = null
  services.value = []
  alerts.value = []
  svcNotes.value = []
  trendData.value = null
  trendNotes.value = []
  trendLoaded.value = false
  svcLoaded.value = false
  alertLoaded.value = false
  checkResult.value = null
  checkNotes.value = []
  section.value = 'basic'
  loadOverview()
  loadConnectivity(false)
}

async function loadOverview() {
  if (!props.hostId) return
  ovLoading.value = true
  const id = props.hostId
  try {
    const data = await api.get(`/servers/${id}/overview`, undefined, { signal: detailController.signal, timeoutMs: 10000 })
    if (id === requestedId) overview.value = data
  } catch (error) {
    if (error.name !== 'AbortError') toast(`主机详情加载失败：${error.message}`, 'err')
  } finally {
    if (id === requestedId) ovLoading.value = false
  }
}

async function loadConnectivity(force = false) {
  if (!props.hostId) return
  if (connLoading.value && !force) return
  connLoading.value = true
  const id = props.hostId
  try {
    const data = await api.get(`/servers/${id}/connectivity`, undefined, { signal: detailController.signal, timeoutMs: 15000 })
    if (id === requestedId) connectivity.value = data
  } catch (error) {
    if (error.name !== 'AbortError' && id === requestedId) connectivity.value = { notes: [`连接诊断加载失败：${error.message}`] }
  } finally {
    if (id === requestedId) connLoading.value = false
  }
}

async function loadServices() {
  if (!props.hostId || svcLoading.value) return
  svcLoading.value = true
  const id = props.hostId
  try {
    const data = await api.get(`/servers/${id}/services`, undefined, { signal: detailController.signal, timeoutMs: 10000 })
    if (id === requestedId) {
      services.value = data.services || []
      svcNotes.value = data.notes || []
      svcLoaded.value = true
    }
  } catch (error) {
    if (error.name !== 'AbortError' && id === requestedId) svcNotes.value = [`服务加载失败：${error.message}`]
  } finally {
    if (id === requestedId) svcLoading.value = false
  }
}

async function loadAlerts() {
  if (!props.hostId || alertLoading.value) return
  alertLoading.value = true
  const id = props.hostId
  try {
    const data = await api.get('/alert-events', { server_id: id, days: 7 }, { signal: detailController.signal, timeoutMs: 10000 })
    if (id === requestedId) {
      alerts.value = Array.isArray(data) ? data : []
      alertLoaded.value = true
    }
  } catch (error) {
    if (error.name !== 'AbortError' && id === requestedId) alertLoaded.value = true
  } finally {
    if (id === requestedId) alertLoading.value = false
  }
}

let trendSeq = 0
async function loadTrends() {
  if (!props.hostId) return
  const id = props.hostId
  const seq = ++trendSeq
  trendLoading.value = true
  try {
    const data = await api.get(
      `/servers/${id}/metrics/trends`,
      { range: trendRange.value, metrics: 'cpu,memory,disk,network' },
      { signal: detailController.signal, timeoutMs: 20000 },
    )
    if (seq === trendSeq && id === requestedId) {
      trendData.value = data
      trendNotes.value = data.notes || []
      trendLoaded.value = true
      await ensureChart()
      renderTrend()
    }
  } catch (error) {
    if (error.name !== 'AbortError' && seq === trendSeq && id === requestedId) {
      trendNotes.value = [`趋势加载失败：${error.message}`]
    }
  } finally {
    if (seq === trendSeq) trendLoading.value = false
  }
}

function setRange(range) {
  if (trendRange.value === range) return
  trendRange.value = range
  loadTrends()
}

function activateSection(id) {
  section.value = id
  if (id === 'trends') {
    if (!trendLoaded.value) loadTrends()
    else nextTick(() => { ensureChart(); renderTrend() })
  }
  if (id === 'services' && !svcLoaded.value) loadServices()
  if (id === 'alerts' && !alertLoaded.value) loadAlerts()
}

async function ensureChart() {
  await nextTick()
  if (!chartEl.value) return
  if (!chart) {
    chart = echarts.init(chartEl.value)
    chart.setOption({
      backgroundColor: 'transparent',
      textStyle: { color: '#64748b' },
      tooltip: { trigger: 'axis' },
      legend: { textStyle: { color: '#64748b' }, top: 0, type: 'scroll' },
      grid: { left: 48, right: 16, top: 34, bottom: 24 },
      xAxis: { type: 'time', axisLine: { lineStyle: { color: '#cbd5e1' } }, axisLabel: { color: '#64748b' } },
      yAxis: { type: 'value', axisLabel: { color: '#64748b' }, splitLine: { lineStyle: { color: 'rgba(100, 116, 139, .14)' } } },
      series: [],
    })
  }
  chart.resize()
}

function disposeChart() {
  if (chart) {
    chart.dispose()
    chart = null
  }
}

function renderTrend() {
  const data = trendData.value
  if (!chart || !data) return
  const colors = { cpu: '#2563eb', memory: '#16a34a', disk: '#d97706' }
  const series = []
  for (const key of ['cpu', 'memory', 'disk']) {
    const points = data.series?.[key] || []
    if (points.length) {
      series.push({
        name: key.toUpperCase(), type: 'line', smooth: true, showSymbol: false,
        itemStyle: { color: colors[key] },
        data: points.map((p) => [p.t * 1000, Number(Number(p.v).toFixed(2))]),
      })
    }
  }
  const netIn = data.series?.network?.in || []
  const netOut = data.series?.network?.out || []
  if (netIn.length) {
    series.push({
      name: '网络入 KB/s', type: 'line', smooth: true, showSymbol: false,
      itemStyle: { color: '#0ea5e9' }, areaStyle: { opacity: .12 },
      data: netIn.map((p) => [p.t * 1000, Number((p.v / 1024).toFixed(2))]),
    })
  }
  if (netOut.length) {
    series.push({
      name: '网络出 KB/s', type: 'line', smooth: true, showSymbol: false,
      itemStyle: { color: '#8b5cf6' },
      data: netOut.map((p) => [p.t * 1000, Number((p.v / 1024).toFixed(2))]),
    })
  }
  chart.setOption({ series }, { replaceMerge: ['series'] })
}

async function checkAgent() {
  if (!props.hostId || checking.value) return
  checking.value = true
  try {
    const data = await api.post(`/servers/${props.hostId}/agent/check`)
    checkResult.value = data
    checkNotes.value = Array.isArray(data.notes) ? data.notes : []
  } catch (error) {
    toast(`检查更新失败：${error.message}`, 'err')
  } finally {
    checking.value = false
  }
}

async function onUpgraded() {
  checkResult.value = null
  checkNotes.value = []
  await loadOverview()
  emit('upgraded')
}

function onResize() {
  chart?.resize()
}
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  disposeChart()
  detailController?.abort()
})

watch(() => [props.visible, props.hostId], ([visible]) => {
  if (visible && props.hostId) openLoad()
  if (!visible) disposeChart()
})
</script>

<style scoped>
.drawer-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, .48); z-index: 2400; display: flex; justify-content: flex-end; }
.detail-drawer { width: min(720px, 96vw); height: 100%; background: var(--bg); box-shadow: -12px 0 40px rgba(15, 23, 42, .2); padding: 20px; display: flex; flex-direction: column; }
.detail-drawer header { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid var(--border); padding-bottom: 14px; }
.detail-drawer h2 { margin: 0; }
.detail-drawer header p { margin: 5px 0 0; color: var(--muted); font-size: 13px; }
.section-tabs { display: flex; gap: 6px; flex-wrap: wrap; margin: 12px 0; }
.s-tab { border: 1px solid var(--border); background: var(--card); color: var(--muted); font-size: 12px; padding: 5px 10px; border-radius: 999px; cursor: pointer; }
.s-tab:hover { color: var(--brand); border-color: var(--brand); }
.s-tab.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.detail-body { flex: 1; overflow: auto; padding: 2px 2px 12px; }
.info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 18px; font-size: 13px; }
.info-item { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.info-item .muted { font-size: 11px; }
.info-item.wide { grid-column: 1 / -1; }
.err-text { color: var(--err); font-size: 12px; word-break: break-all; }
.notes { font-size: 12px; line-height: 1.6; margin: 12px 0 0; }
.freshness-stale, .freshness-stale strong { color: var(--err); }
.metric-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.metric-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 4px; }
.metric-card b { font-size: 18px; }
.range-row { display: flex; gap: 6px; margin-bottom: 10px; }
.trend-chart { width: 100%; height: 260px; }
.empty-line { padding: 24px 0; text-align: center; font-size: 13px; }
.mini-table { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
.tag-mini { margin-right: 4px; }
.taint-list { font-size: 12px; word-break: break-all; }
.conn-rows { display: flex; flex-direction: column; gap: 8px; }
.conn-row { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; }
.conn-head { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.conn-body { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 6px; font-size: 12px; }
.conn-err { color: var(--err); word-break: break-all; }
.conn-eff { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 18px; margin: 12px 0; font-size: 13px; }
.agent-actions { display: flex; gap: 8px; margin-top: 12px; }
@media (max-width: 640px) { .info-grid, .conn-eff { grid-template-columns: 1fr; } .metric-cards { grid-template-columns: 1fr 1fr; } }
</style>
