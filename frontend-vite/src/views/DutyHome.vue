<template>
  <div class="view duty-home">
    <div class="view-head">
      <div>
        <h1 class="view-title">值班首页</h1>
        <p class="view-sub">按模块汇总资产、监控、日志、K3s、PVE 备份与告警状态；单个模块不可用不会阻塞其他卡片。</p>
      </div>
      <div class="home-actions"><button class="btn" :disabled="refreshing" @click="refreshAll">{{ refreshing ? '刷新中…' : '刷新摘要' }}</button></div>
    </div>

    <div class="module-grid">
      <section v-for="item in modules" :key="item.key" class="card summary-card">
        <header class="summary-head">
          <div><h2>{{ item.title }}</h2><p>{{ item.description }}</p></div>
          <span class="state" :class="stateClass(item)"><i></i>{{ stateLabel(item) }}</span>
        </header>

        <div v-if="item.loading && !item.summary" class="summary-empty">正在读取模块摘要…</div>
        <div v-else-if="!item.summary && !item.error" class="summary-empty">尚未读取</div>
        <div v-else-if="!item.summary" class="summary-empty">{{ item.error || '模块摘要暂不可用' }}</div>
        <template v-else>
          <div v-if="entries(item).length" class="metric-grid">
            <div v-for="[key, value] in entries(item)" :key="key" class="metric">
              <span>{{ metricLabel(key) }}</span><b>{{ metricValue(value) }}</b>
            </div>
          </div>
          <div v-else class="summary-empty">模块已响应，暂未提供可展示的摘要指标。</div>
          <p v-if="item.summary.error || item.error" class="summary-error">{{ item.error || item.summary.error }}</p>
          <section v-if="item.findings?.length" class="findings">
            <div class="findings-head">待关注 <b>{{ item.findings.length }}</b></div>
            <div v-for="finding in item.findings.slice(0, 3)" :key="finding.id" class="finding-row">
              <span class="severity" :class="finding.severity">{{ finding.severity === 'critical' ? '紧急' : '关注' }}</span>
              <span class="finding-title">{{ finding.title }}</span>
              <small>{{ finding.summary }}</small>
            </div>
          </section>
          <p v-else-if="item.findingsError" class="findings-error">告警项暂不可读取：{{ item.findingsError }}</p>
          <p v-else-if="item.findings && !item.findingsError" class="findings-clear">暂无待关注项</p>
        </template>

        <footer class="summary-foot">
          <span>最近采集：{{ item.summary?.collected_at ? fmtTime(item.summary.collected_at) : '暂无成功采集' }}</span>
          <router-link v-if="item.path" :to="item.path" class="link-btn">打开模块 →</router-link>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup>
import { onActivated, onMounted, ref } from 'vue'
import { fmtTime, getAccessToken } from '../api'

const modules = ref([
  { key: 'service-catalog', title: '服务广场', description: '服务目录与健康探活摘要', path: '/services', summary: null, error: '', findings: null, findingsError: '', loading: false },
  { key: 'host-inventory', title: '主机管理', description: '主机台账、Agent 状态与管理地址', path: '/hosts', summary: null, error: '', findings: null, findingsError: '', loading: false },
  { key: 'host-monitor', title: '系统应用', description: '主机监控指标与系统运维工具', path: '/system/monitor', summary: null, error: '', findings: null, findingsError: '', loading: false },
  { key: 'logs', title: '日志中心', description: 'Loki 与日志查询模块状态', path: '/logs', summary: null, error: '', findings: null, findingsError: '', loading: false },
  { key: 'k3s', title: 'K3s 集群', description: '节点、工作负载、存储与事件', path: '/kubernetes/overview', summary: null, error: '', findings: null, findingsError: '', loading: false },
  { key: 'pve-backup', title: 'PVE 与备份', description: '虚拟机、存储容量与备份覆盖', path: '/pve-backup', summary: null, error: '', findings: null, findingsError: '', loading: false },
  { key: 'alerts', title: '告警', description: '未处理事件、等级与最近变化', path: '/alerts', summary: null, error: '', findings: null, findingsError: '', loading: false },
])
const refreshing = ref(false)

function entries(item) {
  const data = item.summary?.data || {}
  const values = data.metrics && typeof data.metrics === 'object' ? data.metrics : data
  return Object.entries(values).filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value)).slice(0, 4)
}

function metricLabel(value) {
  return String(value).replaceAll('_', ' ')
}

function metricValue(value) {
  return typeof value === 'boolean' ? (value ? '是' : '否') : String(value)
}

function isStale(item) {
  const collectedAt = Date.parse(item.summary?.collected_at || '')
  const limit = Number(item.summary?.stale_after_seconds || 120)
  return Number.isFinite(collectedAt) && Date.now() - collectedAt > limit * 1000
}

function stateLabel(item) {
  if (item.notImplemented) return '尚未接入'
  if (item.loading && !item.summary) return '读取中'
  if (item.error && item.summary) return '刷新失败 · 保留上次数据'
  if (!item.summary) return item.error ? '不可用' : '未读取'
  if (isStale(item)) return '数据过期'
  if (item.summary.status === 'unavailable') return '模块不可用'
  if (item.summary.status === 'degraded') return '部分异常'
  return '正常'
}

function stateClass(item) {
  if (item.notImplemented) return 'muted-state'
  if (item.error || item.summary?.status === 'unavailable') return 'bad'
  if (isStale(item) || item.summary?.status === 'degraded') return 'warn'
  if (item.summary?.status === 'ok') return 'ok'
  return 'muted-state'
}

async function moduleRequest(item, endpoint, controller) {
  const headers = { Accept: 'application/json' }
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const response = await fetch(`/modules/${item.key}/api/v1/${endpoint}`, { headers, signal: controller.signal })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('模块网关尚未接入')
  return response.json()
}

async function loadSummary(item) {
  if (item.notImplemented) return
  item.loading = true
  item.error = ''
  item.findingsError = ''
  const summaryController = new AbortController()
  const findingsController = new AbortController()
  const summaryTimer = setTimeout(() => summaryController.abort(), 10000)
  const findingsTimer = setTimeout(() => findingsController.abort(), 10000)
  try {
    await Promise.all([
      moduleRequest(item, 'summary', summaryController).then(summary => {
        if (!['ok', 'degraded', 'unavailable'].includes(summary.status)) { item.error = '模块摘要格式不正确'; return }
        if (summary.status === 'unavailable' && item.summary && item.summary.status !== 'unavailable') item.error = summary.error || '模块不可用，保留上次数据'
        else item.summary = summary
      }).catch(error => { item.error = summaryController.signal.aborted ? '模块请求超时（10 秒）' : error.message || '模块摘要不可用' }),
      moduleRequest(item, 'findings', findingsController).then(findings => {
        if (!Array.isArray(findings.items) || findings.status === 'unavailable') { item.findingsError = findings.error || '告警源暂不可用'; return }
        item.findings = findings.items
        if (findings.status === 'degraded') item.findingsError = findings.error || '告警项数据部分不可用'
      }).catch(error => { item.findingsError = findingsController.signal.aborted ? '请求超时' : error.message || '不可用' }),
    ])
  } finally {
    clearTimeout(summaryTimer)
    clearTimeout(findingsTimer)
    item.loading = false
  }
}

async function refreshAll() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await Promise.all(modules.value.map(loadSummary))
  } finally {
    refreshing.value = false
  }
}

onMounted(refreshAll)
onActivated(refreshAll)
</script>

<style scoped>
.home-actions { display: flex; gap: 8px; }
.module-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.summary-card { min-width: 0; padding: 16px; }
.summary-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.summary-head h2 { margin: 0; font-size: 15px; }
.summary-head p { margin: 5px 0 0; color: var(--muted); font-size: 12px; }
.state { display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0; font-size: 11px; }
.state i { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.state.ok { color: var(--ok); }
.state.warn { color: var(--warn); }
.state.bad { color: var(--err); }
.state.muted-state { color: var(--muted); }
.metric-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 9px; margin-top: 18px; }
.metric { min-width: 0; padding: 10px 12px; border-radius: 8px; background: var(--surface-subtle, #f8fafc); }
.metric span, .metric b { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.metric span { color: var(--muted); font-size: 11px; text-transform: capitalize; }
.metric b { margin-top: 4px; font-size: 18px; }
.summary-empty { min-height: 74px; display: flex; align-items: center; color: var(--muted); font-size: 12px; }
.summary-error { margin: 12px 0 0; color: var(--err); font-size: 12px; }
.findings { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 10px; }
.findings-head { margin-bottom: 6px; color: var(--muted); font-size: 11px; }
.findings-head b { margin-left: 4px; color: var(--err); }
.finding-row { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 3px 7px; padding: 6px 0; font-size: 11px; }
.finding-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
.finding-row small { grid-column: 2; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.severity { grid-row: span 2; color: var(--warn); }
.severity.critical { color: var(--err); }
.findings-clear, .findings-error { margin: 10px 0 0; color: var(--muted); font-size: 11px; }
.findings-error { color: var(--warn); }
.summary-foot { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--border); color: var(--muted); font-size: 11px; }
@media (max-width: 760px) { .module-grid { grid-template-columns: 1fr; } }
</style>
