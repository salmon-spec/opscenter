<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">拓扑架构</h1>
        <p class="view-sub">服务间流程流转关系 · 点击节点查看详情</p>
      </div>
      <div style="display:flex;gap:8px">
        <button
          v-for="sc in scenarios" :key="sc.id"
          class="btn" :class="scenario === sc.id ? 'btn-primary' : ''"
          @click="switchScenario(sc.id)"
        >{{ sc.label }}</button>
      </div>
    </div>

    <div class="card topo-card">
      <div v-if="loading" class="loading"><span class="spinner"></span>正在加载拓扑…</div>
      <EmptyState v-else-if="!graphData.nodes.length" icon="🔗" text="暂无拓扑数据（请先录入服务关系）" />
      <div v-else ref="chartEl" class="topo-chart"></div>
      <div v-if="notice" class="topo-notice muted">{{ notice }}</div>
    </div>

    <ServiceDetailDrawer :visible="drawerVisible" :service="selected" @close="drawerVisible = false" />
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api'
import EmptyState from '../components/EmptyState.vue'
import ServiceDetailDrawer from '../components/ServiceDetailDrawer.vue'

const scenarios = [
  { id: 'cicd', label: 'CI/CD 链路' },
  { id: 'monitoring', label: '监控链路' },
  { id: 'gateway', label: '公网入口' },
]

const scenario = ref('cicd')
const loading = ref(true)
const notice = ref('')
const graphData = ref({ nodes: [], edges: [] })
const chartEl = ref(null)
const drawerVisible = ref(false)
const selected = ref(null)
let chart = null

const PALETTE = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#64748b']

async function load() {
  loading.value = true
  notice.value = ''
  try {
    graphData.value = await api.get('/topology', { scenario: scenario.value })
    if (!graphData.value.edges?.length) {
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
  render()
}

function switchScenario(id) {
  scenario.value = id
  load()
}

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

function render() {
  if (!chart) {
    chart = echarts.init(chartEl.value)
    chart.on('click', (params) => {
      if (params.dataType === 'node' && params.data) {
        selected.value = {
          id: params.data.id,
          name: params.data.name,
          category: params.data.category,
          url: params.data.url,
          server_name: params.data.server_name,
        }
        drawerVisible.value = true
      }
    })
  }
  const nodes = layout(graphData.value.nodes, graphData.value.edges)
  const edges = graphData.value.edges.map((e) => ({
    source: e.source,
    target: e.target,
    label: e.label ? { show: true, formatter: e.label, fontSize: 10, color: '#94a3b8' } : undefined,
  }))
  const categories = [...new Set(nodes.map((n) => n.category).filter(Boolean))]
  chart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      formatter: (p) => p.dataType === 'node'
        ? `${p.data.name}<br/>${p.data.category || ''}${p.data.server_name ? '<br/>主机: ' + p.data.server_name : ''}`
        : `${p.data.source} → ${p.data.target}`,
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
</style>
