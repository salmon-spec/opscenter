<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">拓扑架构</h1>
        <p class="view-sub">{{ scenario === 'wireguard' ? 'WireGuard 内网拓扑 · 仅只读展示，不泄露密钥' : '服务间流程流转关系 · 点击节点查看详情' }}</p>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button
          v-for="sc in scenarios" :key="sc.id"
          class="btn" :class="scenario === sc.id ? 'btn-primary' : ''"
          @click="switchScenario(sc.id)"
        >{{ sc.label }}</button>
      </div>
    </div>

    <div v-if="scenario === 'wireguard'" class="wg-summary card">
      <div class="wg-stat"><b>{{ wgSummary.peer_total ?? 0 }}</b><span>Peer 总数</span></div>
      <div class="wg-stat"><b>{{ wgSummary.managed ?? 0 }}</b><span>已纳管</span></div>
      <div class="wg-stat ok"><b>{{ wgSummary.healthy ?? 0 }}</b><span>健康</span></div>
      <div class="wg-stat warn"><b>{{ wgSummary.warning ?? 0 }}</b><span>警告</span></div>
      <div class="wg-stat err"><b>{{ wgSummary.offline ?? 0 }}</b><span>离线</span></div>
      <div class="wg-stat"><b>{{ wgSummary.unmanaged ?? 0 }}</b><span>未纳管</span></div>
      <div class="wg-stat"><b>{{ fmtBytes((wgSummary.wg_rx_bytes||0)+(wgSummary.wg_tx_bytes||0)) }}</b><span>WG 累计流量</span></div>
      <div class="wg-summary-meta muted">生成于 {{ fmtTime(wgGeneratedAt) }}<template v-if="wgPartialErrors.length"> · 部分数据异常：{{ wgPartialErrors.join('；') }}</template></div>
    </div>

    <div v-if="scenario === 'wireguard'" class="wg-filters">
      <button v-for="f in wgFilterOptions" :key="f.id" class="btn btn-sm" :class="wgFilter === f.id ? 'btn-primary' : ''" @click="wgFilter = f.id">{{ f.label }}</button>
      <span class="muted wg-hint">累计值在接口重启后可能归零；握手时间来自 Hub 采集快照（30 秒缓存）</span>
    </div>

    <div class="card topo-card">
      <div v-if="loading" class="loading"><span class="spinner"></span>正在加载拓扑…</div>
      <EmptyState v-else-if="!visibleNodes.length" :icon="scenario==='wireguard' ? '🔒' : '🔗'" :text="scenario==='wireguard' ? '暂无 WireGuard 数据，请确认已升级 Agent 至 v2.6+' : '暂无拓扑数据（请先录入服务关系）'" />
      <div v-else ref="chartEl" class="topo-chart"></div>
      <div v-if="notice" class="topo-notice muted">{{ notice }}</div>
      <div v-if="scenario==='wireguard' && legacyAgentHint" class="topo-notice warn">{{ legacyAgentHint }}</div>
    </div>

    <ServiceDetailDrawer :visible="drawerVisible && selected?.kind!=='wg'" :service="selected" @close="drawerVisible = false" />
    <div v-if="drawerVisible && selected?.kind==='wg'" class="wg-drawer-mask" @click.self="drawerVisible = false">
      <aside class="wg-drawer">
        <header><div><h2>{{ selected.name }}</h2><p class="muted">{{ nodeTypeLabel(selected.type) }}<template v-if="selected.wg_ip"> · {{ selected.wg_ip }}</template></p></div><button class="btn btn-ghost" @click="drawerVisible = false">✕</button></header>
        <dl>
          <dt>健康状态</dt><dd><span class="health-pill" :class="selected.health">{{ wgHealthLabel(selected.health) }}</span></dd>
          <dt>WG IP</dt><dd>{{ selected.wg_ip || '—' }}</dd>
          <dt>资产主机</dt><dd>{{ selected.host || '未纳管' }}<template v-if="selected.host">（{{ selected.name }}）</template></dd>
          <dt>端点</dt><dd>{{ selected.endpoint || '—' }}</dd>
          <dt>最后握手</dt><dd>{{ selected.latest_handshake_at ? fmtTime(selected.latest_handshake_at, true) : '从未握手' }}</dd>
          <dt>距今</dt><dd>{{ selected.latest_handshake_age_seconds == null ? '—' : fmtAge(selected.latest_handshake_age_seconds) }}</dd>
          <dt>Allowed IPs</dt><dd>{{ (selected.allowed_ips || []).join(', ') || '—' }}</dd>
          <dt>累计 RX / TX</dt><dd>{{ fmtBytes(selected.rx_bytes||0) }} / {{ fmtBytes(selected.tx_bytes||0) }}</dd>
          <dt>数据时间</dt><dd>{{ selected.data_source === 'live' ? '实时缓存（≤30s）' : (selected.data_source || '未知') }}</dd>
        </dl>
        <p class="muted wg-drawer-note">完整公钥、私钥与预共享密钥均不展示；WireGuard 拓扑为只读视图。</p>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import ServiceDetailDrawer from '../components/ServiceDetailDrawer.vue'

const route = useRoute()
const router = useRouter()

const scenarios = [
  { id: 'cicd', label: 'CI/CD 链路' },
  { id: 'monitoring', label: '监控链路' },
  { id: 'gateway', label: '公网入口' },
  { id: 'wireguard', label: 'WireGuard 内网' },
]
const wgFilterOptions = [
  { id: 'all', label: '全部' },
  { id: 'managed', label: '已纳管' },
  { id: 'abnormal', label: '异常' },
  { id: 'unmanaged', label: '未纳管' },
]

const scenario = ref(route.query.scenario === 'wireguard' ? 'wireguard' : 'cicd')
const loading = ref(true)
const notice = ref('')
const legacyAgentHint = ref('')
const graphData = ref({ nodes: [], edges: [], summary: {}, partial_errors: [] })
const chartEl = ref(null)
const drawerVisible = ref(false)
const selected = ref(null)
const wgFilter = ref('all')
let chart = null

const PALETTE = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#64748b']

const wgSummary = computed(() => graphData.value?.summary || {})
const wgGeneratedAt = computed(() => graphData.value?.generated_at || '')
const wgPartialErrors = computed(() => graphData.value?.partial_errors || [])

const visibleNodes = computed(() => {
  const nodes = graphData.value?.nodes || []
  if (scenario.value !== 'wireguard' || wgFilter.value === 'all') return nodes
  return nodes.filter((n) => {
    if (wgFilter.value === 'managed') return n.type === 'hub' || n.type === 'managed_host'
    if (wgFilter.value === 'unmanaged') return n.type === 'unregistered_peer'
    if (wgFilter.value === 'abnormal') return ['warning', 'offline', 'unknown'].includes(n.health)
    return true
  })
})

// WireGuard 辅助
function wgColor(h) { return ({ healthy: '#10b981', warning: '#f59e0b', offline: '#ef4444', unknown: '#94a3b8' })[h] || '#94a3b8' }
function wgHealthLabel(h) { return ({ healthy: '健康', warning: '警告', offline: '离线', unknown: '未知' })[h] || '未知' }
function nodeTypeLabel(t) { return ({ hub: '中心节点 (Hub)', managed_host: '已纳管主机', unregistered_peer: '未纳管 Peer' })[t] || t }
function fmtBytes(v) {
  if (v == null || v === 0) return '0 B'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0, n = v
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  return `${n.toFixed(n >= 100 ? 0 : 1)} ${u[i]}`
}
function fmtAge(s) {
  if (s == null) return '—'
  if (s < 60) return `${s} 秒前`
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`
  return `${Math.floor(s / 86400)} 天前`
}
function fmtTime(iso, full = false) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  const hhmmss = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  if (full) {
    const diff = Math.max(0, (Date.now() - d.getTime()) / 1000)
    return `${hhmmss}（${fmtAge(Math.round(diff))}）`
  }
  return hhmmss
}

async function load() {
  loading.value = true
  notice.value = ''
  legacyAgentHint.value = ''
  try {
    graphData.value = await api.get('/topology', { scenario: scenario.value })
    if (scenario.value === 'wireguard') {
      if (!graphData.value.nodes?.length) notice.value = '暂无 WireGuard 数据'
      if (graphData.value.partial_errors?.length) {
        legacyAgentHint.value = graphData.value.partial_errors.join('；')
      }
      if (graphData.value.summary && !graphData.value.summary.peer_total && !graphData.value.partial_errors?.length) {
        notice.value = 'Hub 当前没有可展示的 Peer'
      }
    } else if (!graphData.value.edges?.length) {
      notice.value = '当前场景暂无服务依赖关系，可先到资源管理录入服务并配置拓扑关系'
    }
  } catch {
    // 后端拓扑接口未就绪：退化为全部服务节点（无边）
    const list = await api.get('/services-with-status').catch(() => [])
    graphData.value = {
      nodes: list.map((s) => ({ id: s.id, name: s.name, category: s.category, status: s.status, server_name: s.server_name, url: s.url })),
      edges: [],
    }
    notice.value = '拓扑关系接口未就绪，当前仅展示服务节点'
  } finally {
    loading.value = false
  }
  await nextTick()
  render()
}

function switchScenario(id) {
  scenario.value = id
  wgFilter.value = 'all'
  router.replace({ query: id === 'wireguard' ? { scenario: id } : {} })
  load()
}

watch(wgFilter, () => { if (scenario.value === 'wireguard') render() })

// 分层布局：按入度 BFS 分层（流水线方向），同层纵向排布
function layout(nodes, edges) {
  const byId = {}
  nodes.forEach((n) => { byId[n.id] = n })
  const inDeg = {}
  nodes.forEach((n) => { inDeg[n.id] = 0 })
  edges.forEach((e) => { if (byId[e.source] && byId[e.target]) inDeg[e.target] = (inDeg[e.target] || 0) + 1 })
  const adj = {}
  edges.forEach((e) => { (adj[e.source] = adj[e.source] || []).push(e.target) })
  const layer = {}
  let queue = nodes.filter((n) => !inDeg[n.id]).map((n) => n.id)
  if (!queue.length && nodes.length) queue = [nodes[0].id]
  const seen = new Set()
  let depth = 0
  while (queue.length) {
    const next = []
    for (const id of queue) {
      if (seen.has(id)) continue
      seen.add(id)
      layer[id] = depth
      ;(adj[id] || []).forEach((t) => next.push(t))
    }
    queue = next
    depth++
  }
  nodes.forEach((n) => { if (layer[n.id] === undefined) layer[n.id] = 0 })
  const layerIndex = {}
  const catColor = {}
  let colorIdx = 0
  return nodes.map((n) => {
    const l = layer[n.id]
    const idx = layerIndex[l] = (layerIndex[l] || 0)
    layerIndex[l]++
    if (!(n.category in catColor)) catColor[n.category] = PALETTE[colorIdx++ % PALETTE.length]
    return {
      ...n,
      x: 90 + l * 300,
      y: 60 + idx * 100,
      symbolSize: 46,
      itemStyle: { color: catColor[n.category] },
    }
  })
}

// WireGuard 布局：Hub 居中，其余辐射分布；线宽按累计流量分级
function wgLayout(nodes) {
  const hub = nodes.find((n) => n.type === 'hub')
  const hubId = hub?.id
  const others = nodes.filter((n) => n.id !== hubId)
  const step = others.length ? (2 * Math.PI) / Math.max(others.length, 1) : 0
  const cx = 460, cy = 300, R = 210
  return nodes.map((n) => {
    if (n.id === hubId) return { ...n, x: cx, y: cy, symbolSize: 66, itemStyle: { color: '#3b82f6' } }
    const idx = others.findIndex((o) => o.id === n.id)
    const ang = (idx * step) - Math.PI / 2
    return { ...n, x: cx + R * Math.cos(ang), y: cy + R * Math.sin(ang), symbolSize: 42, itemStyle: { color: wgColor(n.health) } }
  })
}

function wgEdgeWidth(rx, tx) {
  const total = (rx || 0) + (tx || 0)
  if (total <= 0) return 1
  return Math.min(6, 1 + Math.log2(1 + total) / 3.2)
}

function render() {
  const nodesSource = Array.isArray(visibleNodes.value) ? visibleNodes.value : []
  const allEdges = Array.isArray(graphData.value?.edges) ? graphData.value.edges : []
  const isWg = scenario.value === 'wireguard'
  if (!chartEl.value || !nodesSource.length) {
    if (chart) { chart.dispose(); chart = null }
    return
  }
  if (chart && chart.getDom() !== chartEl.value) {
    chart.dispose()
    chart = null
  }
  if (!chart) {
    chart = echarts.init(chartEl.value)
    chart.on('click', (params) => {
      if (params.dataType === 'node' && params.data) {
        selected.value = { ...params.data, kind: isWg ? 'wg' : 'service' }
        drawerVisible.value = true
      }
    })
  }
  const edgesSource = allEdges.filter((e) => nodesSource.some((n) => n.id === e.source) && nodesSource.some((n) => n.id === e.target))
  const nodes = isWg ? wgLayout(nodesSource) : layout(nodesSource, edgesSource)
  const edges = edgesSource.map((e) => ({
    source: e.source,
    target: e.target,
    label: e.label ? { show: true, formatter: e.label, fontSize: 10, color: '#94a3b8' } : undefined,
    lineStyle: isWg ? { color: wgColor(e.health), width: wgEdgeWidth(e.rx_bytes, e.tx_bytes), curveness: 0.04 } : undefined,
  }))
  const categories = isWg
    ? ['中心节点', '已纳管主机', '未纳管 Peer']
    : [...new Set(nodes.map((n) => n.category).filter(Boolean))]
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (p) => {
        if (p.dataType !== 'node') return `${p.data.source} → ${p.data.target}`
        if (isWg) {
          const n = p.data
          return `${n.name}<br/>类型: ${nodeTypeLabel(n.type)}<br/>WG IP: ${n.wg_ip || '—'}<br/>状态: ${wgHealthLabel(n.health)}${n.latest_handshake_age_seconds != null ? `<br/>最后握手: ${fmtAge(n.latest_handshake_age_seconds)}` : ''}<br/>累计: ${fmtBytes(n.rx_bytes || 0)} / ${fmtBytes(n.tx_bytes || 0)}`
        }
        return `${p.data.name}<br/>${p.data.category || ''}${p.data.server_name ? '<br/>主机: ' + p.data.server_name : ''}`
      },
    },
    legend: {
      data: categories,
      textStyle: { color: '#64748b' },
      top: 8,
    },
    animationDuration: 500,
    series: [{
      type: 'graph',
      layout: 'none',
      roam: true,
      draggable: true,
      categories: categories.map((c) => ({ name: c })),
      data: nodes,
      links: edges,
      label: { show: true, position: 'bottom', fontSize: 12, color: '#334155', formatter: (p) => p.name },
      lineStyle: { color: '#94a3b8', width: 1.6, curveness: 0.08 },
      emphasis: { focus: 'adjacency', lineStyle: { width: 3 } },
    }],
  })
  chart.resize()
}

function onResize() { chart?.resize() }

onMounted(() => {
  load()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) { chart.dispose(); chart = null }
})
</script>

<style scoped>
.topo-card { padding: 10px; }
.topo-chart { height: 640px; }
.topo-notice { padding: 8px 12px; font-size: 12px; }
.topo-notice.warn { color: #b45309; background: #fffbeb; border-radius: 8px; margin-top: 6px; }
.wg-summary { display: flex; align-items: center; gap: 18px; flex-wrap: wrap; padding: 12px 16px; margin-bottom: 10px; }
.wg-stat { display: flex; flex-direction: column; min-width: 52px; }
.wg-stat b { font-size: 20px; }
.wg-stat span { font-size: 12px; color: var(--muted); }
.wg-stat.ok b { color: var(--ok, #16a34a); }
.wg-stat.warn b { color: #d97706; }
.wg-stat.err b { color: var(--err, #dc2626); }
.wg-summary-meta { flex-basis: 100%; font-size: 12px; }
.wg-filters { display: flex; align-items: center; gap: 6px; margin-bottom: 10px; flex-wrap: wrap; }
.wg-hint { font-size: 12px; margin-left: 6px; }
.wg-drawer-mask { position: fixed; inset: 0; background: rgba(15,23,42,.45); z-index: 2300; display: flex; justify-content: flex-end; }
.wg-drawer { width: min(430px, 94vw); height: 100%; background: var(--bg); box-shadow: -12px 0 40px rgba(15,23,42,.2); padding: 20px; overflow: auto; }
.wg-drawer header { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid var(--border); padding-bottom: 12px; margin-bottom: 10px; }
.wg-drawer h2 { margin: 0; font-size: 16px; }
.wg-drawer dl { margin: 0; }
.wg-drawer dt { font-size: 12px; color: var(--muted); margin-top: 10px; }
.wg-drawer dd { margin: 2px 0 0; font-size: 13px; word-break: break-all; }
.wg-drawer-note { font-size: 12px; margin-top: 16px; }
.health-pill { padding: 2px 10px; border-radius: 999px; font-size: 12px; color: #fff; }
.health-pill.healthy { background: #16a34a; }
.health-pill.warning { background: #d97706; }
.health-pill.offline { background: #dc2626; }
.health-pill.unknown { background: #64748b; }
</style>