<template>
  <div class="view k8s">
    <div class="view-head">
      <div>
        <h1 class="view-title">网络与入口</h1>
        <p class="view-sub">{{ subText }}</p>
      </div>
      <div class="head-actions">
        <span v-if="stampText" class="muted stamp">数据 {{ stampText }}<span v-if="stale" class="stale">数据陈旧</span></span>
        <button class="btn btn-sm" :disabled="loading" @click="load">↻ 刷新</button>
      </div>
    </div>

    <div v-if="clusterMissing" class="card k8s-guide">
      <div class="guide-icon">☸</div>
      <template v-if="clusterErr">
        <div>集群列表加载失败：{{ clusterErr }}</div>
        <button class="btn btn-sm" @click="initClusters(true)">重试</button>
      </template>
      <template v-else>
        <div>尚未选择集群</div>
        <div class="muted">请在顶栏选择要监控的 K3s 集群</div>
      </template>
    </div>

    <template v-else>
      <div v-if="fatal && !hasData" class="card k8s-error">
        <div class="big">⚠ 网络数据加载失败</div>
        <div class="muted">{{ fatal }}</div>
        <div><button class="btn btn-sm" :disabled="loading" @click="load">重试</button></div>
      </div>
      <div v-else-if="loading && !hasData" class="loading"><span class="spinner"></span>正在读取网络数据…</div>
      <template v-else>
        <div v-if="partialText" class="partial">
          <span>⚠ 部分数据源异常：</span><span class="partial-item">{{ partialText }}</span>
        </div>

        <div class="filters">
          <select v-model="filters.kind" class="select">
            <option value="all">资源：全部</option>
            <option value="services">Services</option>
            <option value="ingresses">Ingresses</option>
            <option value="netpol">NetworkPolicy</option>
          </select>
          <select v-model="filters.namespace" class="select">
            <option value="">命名空间：全部</option>
            <option v-for="ns in namespaceOptions" :key="ns" :value="ns">{{ ns }}</option>
          </select>
          <input v-model="filters.keyword" class="input kw" placeholder="搜索名称、类型、IP 或 Host" />
          <span class="muted tiny">Service {{ filteredServices.length }} · Ingress {{ filteredIngresses.length }} · NetPol {{ filteredNetpol.length }}</span>
        </div>

        <section v-if="showKind('services')" class="card">
          <div class="card-head"><h3>Services（{{ filteredServices.length }}）</h3></div>
          <div class="table-scroll">
            <table class="table">
              <thead><tr><th>名称</th><th>命名空间</th><th>类型</th><th>Cluster IP</th><th>外部 IP</th><th>端口</th><th>端点</th><th>Age</th></tr></thead>
              <tbody>
                <tr v-for="(sv, i) in filteredServices" :key="`${sv.namespace || ''}/${sv.name || i}`">
                  <td><b>{{ sv.name || '—' }}</b></td>
                  <td>{{ sv.namespace || '—' }}</td>
                  <td><span class="tag">{{ sv.type || '—' }}</span></td>
                  <td class="mono">{{ sv.cluster_ip || '—' }}</td>
                  <td class="mono tiny">{{ externalIps(sv) }}</td>
                  <td class="tiny">{{ portsText(sv) }}</td>
                  <td>
                    <span v-if="sv.exposed === true" class="tag tag-ok">有端点</span>
                    <span v-else-if="sv.exposed === false" class="tag tag-warn">无端点</span>
                    <span v-else class="muted tiny">—</span>
                  </td>
                  <td class="nowrap">{{ fmtAge(sv.age) }}</td>
                </tr>
                <tr v-if="!filteredServices.length"><td colspan="8" class="empty-inline">当前条件下没有 Service</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-if="showKind('ingresses')" class="card">
          <div class="card-head"><h3>Ingresses（{{ filteredIngresses.length }}）</h3></div>
          <div class="table-scroll">
            <table class="table">
              <thead><tr><th>名称</th><th>命名空间</th><th>Hosts</th><th>规则</th><th>TLS</th><th>Age</th></tr></thead>
              <tbody>
                <tr v-for="(ing, i) in filteredIngresses" :key="`${ing.namespace || ''}/${ing.name || i}`">
                  <td><b>{{ ing.name || '—' }}</b></td>
                  <td>{{ ing.namespace || '—' }}</td>
                  <td class="tiny">{{ hostsText(ing) }}</td>
                  <td class="tiny">{{ rulesText(ing) }}</td>
                  <td class="tiny">{{ tlsText(ing) }}</td>
                  <td class="nowrap">{{ fmtAge(ing.age) }}</td>
                </tr>
                <tr v-if="!filteredIngresses.length"><td colspan="6" class="empty-inline">当前条件下没有 Ingress</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section v-if="showKind('netpol')" class="card">
          <div class="card-head"><h3>NetworkPolicy（{{ filteredNetpol.length }}）</h3></div>
          <div class="table-scroll" v-if="filteredNetpol.length">
            <table class="table">
              <thead><tr><th>名称</th><th>命名空间</th><th>类型</th><th>Age</th></tr></thead>
              <tbody>
                <tr v-for="(p, i) in filteredNetpol" :key="`${p.namespace || ''}/${p.name || i}`">
                  <td><b>{{ p.name || '—' }}</b></td>
                  <td>{{ p.namespace || '—' }}</td>
                  <td>{{ npKind(p) }}</td>
                  <td class="nowrap">{{ fmtAge(p.age) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-inline">当前条件下没有 NetworkPolicy</div>
        </section>
      </template>
    </template>
  </div>
</template>

<script>
export default { name: 'KubernetesNetwork' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useTabActive, setTabState, getTabState } from '../../workbench/tabs'
import workbench from '../../workbench/tabs'
import { useClusterContext } from '../../workbench/clusters'
import { k8sApi, unwrap, apiErrorText } from '../../api/kubernetes'

const { selectedClusterId, currentCluster, refreshClusters } = useClusterContext()
const { isActive } = useTabActive('KubernetesNetwork')

const POLL_MS = 15000
const services = ref([])
const ingresses = ref([])
const netpolInfo = reactive({ total: null, items: [] })
const meta = reactive({ ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
const fatal = ref('')
const loading = ref(false)
const clusterErr = ref('')
const nowTick = ref(Date.now())
const filters = reactive({ kind: 'all', namespace: '', keyword: '' })

let controller = null
let pollTimer = null
let inFlight = false
let tabAlive = false
let loadedOnce = false

const cid = () => selectedClusterId.value
const clusterMissing = computed(() => !cid())
const hasData = computed(() => services.value.length > 0 || ingresses.value.length > 0 || netpolInfo.items.length > 0)
const partialText = computed(() => meta.partialErrors.join('；'))
const stampText = computed(() => {
  if (!meta.ts) return ''
  const t = new Date(meta.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  return meta.cached ? `${t} · 缓存 ${meta.cacheAge.toFixed(0)}s` : t
})
const stale = computed(() => meta.ts > 0 && nowTick.value / 1000 - meta.ts > 60)
const subText = computed(() => {
  const c = currentCluster.value
  if (!c) return 'Service、Ingress 与 NetworkPolicy'
  return `${c.name}${c.version ? ' · ' + c.version : ''} · Service / Ingress / NetworkPolicy`
})

const namespaceOptions = computed(() => [...new Set([
  ...services.value.map((item) => item.namespace),
  ...ingresses.value.map((item) => item.namespace),
  ...netpolInfo.items.map((item) => item.namespace),
].filter(Boolean))].sort())

function filterRows(rows, textOf) {
  const keyword = filters.keyword.trim().toLowerCase()
  return rows.filter((item) => {
    if (filters.namespace && item.namespace !== filters.namespace) return false
    return !keyword || textOf(item).toLowerCase().includes(keyword)
  })
}

const filteredServices = computed(() => filterRows(services.value, (item) => [item.name, item.namespace, item.type, item.cluster_ip, externalIps(item), portsText(item)].filter(Boolean).join(' ')))
const filteredIngresses = computed(() => filterRows(ingresses.value, (item) => [item.name, item.namespace, hostsText(item), rulesText(item), tlsText(item)].filter(Boolean).join(' ')))
const filteredNetpol = computed(() => filterRows(netpolInfo.items, (item) => [item.name, item.namespace, npKind(item)].filter(Boolean).join(' ')))
function showKind(kind) { return filters.kind === 'all' || filters.kind === kind }

async function initClusters(force = false) {
  clusterErr.value = ''
  try { await refreshClusters(force) } catch (e) { clusterErr.value = e?.message || '加载失败' }
}

function itemsOf(v) {
  if (Array.isArray(v)) return v
  if (Array.isArray(v?.items)) return v.items
  return []
}

async function load() {
  const cidNow = cid()
  if (!cidNow || inFlight) return
  inFlight = true
  loading.value = true
  controller?.abort()
  controller = new AbortController()
  const { signal } = controller
  try {
    const [sr, ir, nr] = await Promise.allSettled([
      k8sApi.services(cidNow, undefined, { signal, timeoutMs: 20000 }),
      k8sApi.ingresses(cidNow, undefined, { signal, timeoutMs: 20000 }),
      k8sApi.netpol(cidNow, undefined, { signal, timeoutMs: 20000 }),
    ])
    if (cid() !== cidNow) return
    const errs = []
    const metas = []
    if (sr.status === 'fulfilled') { const u = unwrap(sr.value); services.value = itemsOf(u.data); metas.push(u.meta) }
    else errs.push(`Services 读取失败：${apiErrorText(sr.reason)}`)
    if (ir.status === 'fulfilled') { const u = unwrap(ir.value); ingresses.value = itemsOf(u.data); metas.push(u.meta) }
    else errs.push(`Ingresses 读取失败：${apiErrorText(ir.reason)}`)
    if (nr.status === 'fulfilled') {
      const u = unwrap(nr.value)
      const items = itemsOf(u.data)
      netpolInfo.items = items
      const total = u.data && typeof u.data === 'object' && !Array.isArray(u.data) ? u.data.total ?? u.data.count : null
      netpolInfo.total = total === null || total === undefined ? items.length : Number(total)
      metas.push(u.meta)
    } else errs.push(`NetworkPolicy 读取失败：${apiErrorText(nr.reason)}`)
    let ts = 0, cached = false, cacheAge = 0
    const pe = []
    for (const m of metas) {
      ts = Math.max(ts, m.ts)
      cached = cached || m.cached
      cacheAge = Math.max(cacheAge, m.cacheAge)
      pe.push(...m.partialErrors)
    }
    meta.ts = ts; meta.cached = cached; meta.cacheAge = cacheAge
    meta.partialErrors = [...pe, ...errs]
    fatal.value = errs.length === 3 ? errs.join('；') : ''
    loadedOnce = true
    nowTick.value = Date.now()
  } finally {
    inFlight = false
    if (cid() === cidNow) loading.value = false
  }
}

function tick() {
  nowTick.value = Date.now()
  if (!isActive.value || !tabAlive || document.hidden || inFlight || !cid()) return
  load()
}
function startPolling() {
  stopPolling()
  pollTimer = setInterval(tick, POLL_MS)
}
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

function ensureFresh() {
  if (!cid()) return
  const fresh = loadedOnce && meta.ts > 0 && nowTick.value / 1000 - meta.ts <= 15
  if (!fresh) load()
}

function externalIps(sv) {
  const ips = sv?.external_ips || sv?.external_ip
  if (Array.isArray(ips)) return ips.join(', ') || '—'
  return ips || '—'
}
function portsText(sv) {
  const ports = Array.isArray(sv?.ports) ? sv.ports : []
  return ports.map((p) => `${p.port ?? p.port ?? ''}${p.node_port ? '/' + p.node_port : ''}${p.protocol && p.protocol !== 'TCP' ? '/' + p.protocol : ''}`).join(', ') || '—'
}
function hostsText(ing) {
  const hosts = Array.isArray(ing?.hosts) ? ing.hosts : (Array.isArray(ing?.rules) ? ing.rules.map((r) => r.host).filter(Boolean) : [])
  return hosts.join(', ') || '—'
}
function rulesText(ing) {
  const rules = Array.isArray(ing?.rules) ? ing.rules : []
  const paths = rules.reduce((a, r) => a + (Array.isArray(r.http?.paths) ? r.http.paths.length : 0), 0)
  if (!rules.length) return '—'
  return `${rules.length} host · ${paths} 路径`
}
function tlsText(ing) {
  const tls = Array.isArray(ing?.tls) ? ing.tls : []
  const hosts = tls.flatMap((t) => (Array.isArray(t?.hosts) ? t.hosts : []))
  return hosts.length ? hosts.join(', ') : '无'
}
function npKind(p) {
  const parts = []
  if (p?.ingress) parts.push('Ingress')
  if (p?.egress) parts.push('Egress')
  return parts.join(' + ') || '—'
}
function fmtAge(v) {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'number') {
    const sec = Math.max(0, Math.round(v))
    if (sec < 60) return `${sec}s`
    if (sec < 3600) return `${Math.floor(sec / 60)}m`
    if (sec < 86400) return `${Math.floor(sec / 3600)}h`
    return `${Math.floor(sec / 86400)}d`
  }
  return String(v)
}

function restoreUi() {
  const saved = getTabState(workbench.state.activeKey) || {}
  const f = saved.filters || {}
  if (typeof f.kind === 'string') filters.kind = f.kind
  if (typeof f.namespace === 'string') filters.namespace = f.namespace
  if (typeof f.keyword === 'string') filters.keyword = f.keyword
  if (saved.scrollY !== undefined) {
    nextTick(() => { const el = document.querySelector('.content'); if (el) el.scrollTop = saved.scrollY })
  }
}
function saveUi() {
  const el = document.querySelector('.content')
  setTabState(workbench.state.activeKey, { filters: { kind: filters.kind, namespace: filters.namespace, keyword: filters.keyword }, scrollY: el ? el.scrollTop : 0 })
}

watch(filters, () => {
  if (tabAlive) saveUi()
}, { deep: true })

watch(selectedClusterId, (id) => {
  if (!tabAlive) return
  services.value = []
  ingresses.value = []
  netpolInfo.items = []
  netpolInfo.total = null
  Object.assign(meta, { ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
  fatal.value = ''
  loadedOnce = false
  if (id) load()
})

watch(isActive, (v) => {
  if (!tabAlive) return
  if (v) { ensureFresh(); startPolling() } else stopPolling()
})

onMounted(async () => {
  tabAlive = true
  await initClusters(false)
  restoreUi()
  if (cid()) ensureFresh()
  if (isActive.value) startPolling()
})
onActivated(() => {
  tabAlive = true
  restoreUi()
  ensureFresh()
  if (isActive.value) startPolling()
})
onDeactivated(() => {
  tabAlive = false
  stopPolling()
  saveUi()
})
onUnmounted(() => {
  tabAlive = false
  stopPolling()
  controller?.abort()
})
</script>

<style scoped>
.k8s { display: flex; flex-direction: column; gap: 12px; }
.head-actions { display: flex; align-items: center; gap: 10px; }
.stamp { font-size: 12px; }
.stale { display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
.partial { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 8px 12px; border: 1px solid #fde68a; background: #fffbeb; color: #b45309; border-radius: 8px; font-size: 12px; }
.partial-item { word-break: break-all; }
.k8s-guide { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 48px 16px; text-align: center; }
.guide-icon { font-size: 40px; }
.k8s-error { display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.k8s-error .big { font-weight: 700; color: var(--err); }
.filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
.filters .select, .filters .input { width: auto; }
.filters .kw { min-width: 230px; }
.card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.card-head h3 { margin: 0; font-size: 14px; }
.table-scroll { overflow: auto; }
.empty-inline { text-align: center; color: var(--muted); padding: 22px 10px; font-size: 13px; }
.nowrap { white-space: nowrap; }
.tiny { font-size: 12px; }
.tag-ok { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #ecfdf5; color: #059669; }
.tag-warn { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; }
</style>
