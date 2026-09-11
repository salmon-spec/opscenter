<template>
  <div class="view k8s">
    <div class="view-head">
      <div>
        <h1 class="view-title">工作负载</h1>
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
      <div v-if="fatal && !hasList" class="card k8s-error">
        <div class="big">⚠ 工作负载列表加载失败</div>
        <div class="muted">{{ fatal }}</div>
        <div><button class="btn btn-sm" :disabled="loading" @click="load">重试</button></div>
      </div>
      <div v-else-if="loading && !hasList" class="loading"><span class="spinner"></span>正在读取工作负载…</div>
      <template v-else>
        <div v-if="partialText" class="partial">
          <span>⚠ 部分数据源异常：</span><span class="partial-item">{{ partialText }}</span>
        </div>
        <section class="card">
          <div class="filters">
            <select v-model="filters.namespace" class="select">
              <option value="">命名空间：全部</option>
              <option v-for="ns in nsOptions" :key="ns" :value="ns">{{ ns }}</option>
            </select>
            <select v-model="filters.type" class="select">
              <option value="">类型：全部</option>
              <option v-for="t in typeOptions" :key="t" :value="t">{{ t }}</option>
            </select>
            <select v-model="filters.status" class="select">
              <option value="">状态：全部</option>
              <option value="ready">就绪</option>
              <option value="not_ready">未就绪</option>
            </select>
            <input v-model="filters.keyword" class="input kw" placeholder="搜索名称或镜像" />
            <span class="muted tiny">共 {{ rows.length }} 项</span>
          </div>
          <div class="table-scroll">
            <table class="table">
              <thead><tr><th>名称</th><th>类型</th><th>命名空间</th><th>副本</th><th>状态</th><th>镜像</th><th>重启</th><th>Age</th><th>创建时间</th></tr></thead>
              <tbody>
                <tr v-for="w in rows" :key="`${w.namespace || ''}/${w.type || ''}/${w.name}`" class="row-click" @click="openDrawer(w)">
                  <td><b>{{ w.name }}</b></td>
                  <td><span class="tag">{{ w.type || '—' }}</span></td>
                  <td>{{ w.namespace || '—' }}</td>
                  <td class="mono">{{ w.replicas?.ready ?? '—' }} / {{ w.replicas?.desired ?? '—' }}</td>
                  <td><span class="tag" :class="replClass(w)">{{ replLabel(w) }}</span></td>
                  <td class="tiny img-cell" :title="(w.images || []).join(' / ')">{{ (w.images || []).join(' / ') || '—' }}</td>
                  <td><span :class="Number(w.restarts) > 0 ? 'err-text' : ''">{{ w.restarts ?? 0 }}</span></td>
                  <td class="nowrap">{{ fmtAge(w.age) }}</td>
                  <td class="muted tiny nowrap">{{ w.created_at ? fmtTime(w.created_at) : '—' }}</td>
                </tr>
                <tr v-if="!rows.length"><td colspan="9" class="empty-inline">当前条件下没有工作负载</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </template>

    <div v-if="drawer.open" class="drawer-mask" @click="closeDrawer"></div>
    <aside v-if="drawer.open" class="drawer">
      <div class="drawer-head">
        <div><b>{{ drawer.row?.name }}</b><div class="muted tiny">{{ drawer.row?.type || '—' }} · {{ drawer.row?.namespace || '—' }}</div></div>
        <button class="btn btn-sm btn-ghost" @click="closeDrawer">✕ 关闭</button>
      </div>
      <div class="drawer-body">
        <div class="kv"><span>副本（就绪 / 期望）</span><b class="mono">{{ drawer.row?.replicas?.ready ?? '—' }} / {{ drawer.row?.replicas?.desired ?? '—' }}</b></div>
        <div class="kv"><span>状态</span><b><span class="tag" :class="replClass(drawer.row)">{{ replLabel(drawer.row) }}</span></b></div>
        <div class="kv"><span>重启次数</span><b :class="Number(drawer.row?.restarts) > 0 ? 'err-text' : ''">{{ drawer.row?.restarts ?? 0 }}</b></div>
        <div class="kv"><span>Age</span><b>{{ fmtAge(drawer.row?.age) }}</b></div>
        <div class="kv"><span>创建时间</span><b>{{ drawer.row?.created_at ? fmtTime(drawer.row.created_at) : '—' }}</b></div>
        <div class="kv"><span>镜像</span><b class="mono tiny">{{ (drawer.row?.images || []).join(' / ') || '—' }}</b></div>
        <div class="drawer-sub">
          <h4 class="sub-title">关联 Pods（{{ drawer.pods.length }}）</h4>
          <button class="btn btn-sm" :disabled="drawer.loading" @click="loadDrawerData">刷新</button>
        </div>
        <div v-if="drawer.loading && !drawer.pods.length" class="loading" style="padding:18px"><span class="spinner"></span>加载中…</div>
        <div v-else-if="drawer.err" class="drawer-err tiny">{{ drawer.err }} <button class="btn btn-sm" @click="loadDrawerData">重试</button></div>
        <div v-else-if="drawer.pods.length" class="table-scroll drawer-table">
          <table class="table">
            <thead><tr><th>Pod</th><th>Phase</th><th>Ready</th><th>重启</th><th>节点</th></tr></thead>
            <tbody>
              <tr v-for="p in drawer.pods" :key="`${p.namespace || ''}/${p.name}`">
                <td class="mono">{{ p.name }}</td>
                <td><span class="tag" :class="phaseTagClass(p.phase)">{{ phaseLabel(p.phase) }}</span></td>
                <td class="mono">{{ p.ready || '—' }}</td>
                <td><span :class="Number(p.restarts) > 0 ? 'err-text' : ''">{{ p.restarts ?? 0 }}</span></td>
                <td class="tiny">{{ p.node || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-inline">未找到关联 Pod</div>
        <h4 class="sub-title">近期事件（{{ drawer.events.length }}）</h4>
        <table v-if="drawer.events.length" class="table">
          <thead><tr><th>时间</th><th>类型</th><th>对象</th><th>消息</th></tr></thead>
          <tbody>
            <tr v-for="(ev, i) in drawer.events" :key="i">
              <td class="mono tiny nowrap">{{ evTime(ev) }}</td>
              <td><span class="tag" :class="String(evType(ev)).toLowerCase() === 'warning' ? 'tag-warn' : 'tag-slate'">{{ evType(ev) }}</span></td>
              <td class="tiny">{{ evObj(ev) }}</td>
              <td class="tiny ev-msg">{{ evMsg(ev) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty-inline">暂无事件</div>
      </div>
    </aside>
  </div>
</template>

<script>
export default { name: 'KubernetesWorkloads' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { fmtTime } from '../../api'
import { useTabActive, setTabState, getTabState } from '../../workbench/tabs'
import workbench from '../../workbench/tabs'
import { useClusterContext } from '../../workbench/clusters'
import { k8sApi, unwrap, apiErrorText } from '../../api/kubernetes'

const { selectedClusterId, currentCluster, refreshClusters } = useClusterContext()
const { isActive } = useTabActive('KubernetesWorkloads')

const POLL_MS = 10000
const rows = ref([])
const meta = reactive({ ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
const fatal = ref('')
const loading = ref(false)
const clusterErr = ref('')
const nowTick = ref(Date.now())
const filters = reactive({ namespace: '', type: '', status: '', keyword: '' })
const drawer = reactive({ open: false, row: null, pods: [], events: [], loading: false, err: '' })

let controller = null
let pollTimer = null
let kwTimer = null
let inFlight = false
let tabAlive = false
let loadedOnce = false

const cid = () => selectedClusterId.value
const clusterMissing = computed(() => !cid())
const hasList = computed(() => rows.value.length > 0)
const partialText = computed(() => meta.partialErrors.join('；'))
const nsOptions = computed(() => [...new Set(rows.value.map((r) => r.namespace).filter(Boolean))].sort())
const typeOptions = computed(() => [...new Set(rows.value.map((r) => r.type).filter(Boolean))].sort())
const stampText = computed(() => {
  if (!meta.ts) return ''
  const t = new Date(meta.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  return meta.cached ? `${t} · 缓存 ${meta.cacheAge.toFixed(0)}s` : t
})
const stale = computed(() => meta.ts > 0 && nowTick.value / 1000 - meta.ts > 60)
const subText = computed(() => {
  const c = currentCluster.value
  if (!c) return 'Deployment、StatefulSet 与 DaemonSet'
  return `${c.name}${c.version ? ' · ' + c.version : ''} · Deployment / StatefulSet / DaemonSet`
})

async function initClusters(force = false) {
  clusterErr.value = ''
  try { await refreshClusters(force) } catch (e) { clusterErr.value = e?.message || '加载失败' }
}

function listQuery() {
  return {
    namespace: filters.namespace || undefined,
    type: filters.type || undefined,
    status: filters.status || undefined,
    keyword: filters.keyword.trim() || undefined,
    limit: 200,
  }
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
    const res = await k8sApi.workloads(cidNow, listQuery(), { signal, timeoutMs: 20000 })
    if (cid() !== cidNow) return
    const u = unwrap(res)
    rows.value = Array.isArray(u.data) ? u.data : []
    meta.ts = u.meta.ts
    meta.cached = u.meta.cached
    meta.cacheAge = u.meta.cacheAge
    meta.partialErrors = [...u.meta.partialErrors]
    fatal.value = ''
    loadedOnce = true
    nowTick.value = Date.now()
  } catch (e) {
    if (e.name === 'AbortError' || cid() !== cidNow) return
    fatal.value = apiErrorText(e)
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

function openDrawer(w) {
  drawer.open = true
  drawer.row = w
  drawer.pods = []
  drawer.events = []
  drawer.err = ''
  loadDrawerData()
}
function closeDrawer() { drawer.open = false }

async function loadDrawerData() {
  const w = drawer.row
  const cidNow = cid()
  if (!w || !cidNow || drawer.loading) return
  drawer.loading = true
  drawer.err = ''
  try {
    const [pr, er] = await Promise.allSettled([
      k8sApi.pods(cidNow, { namespace: w.namespace, keyword: w.name, limit: 100 }, { timeoutMs: 20000 }),
      k8sApi.events(cidNow, { namespace: w.namespace, limit: 20 }, { timeoutMs: 20000 }),
    ])
    if (cid() !== cidNow || !drawer.open) return
    const errs = []
    if (pr.status === 'fulfilled') { const u = unwrap(pr.value); drawer.pods = Array.isArray(u.data) ? u.data : []; errs.push(...u.meta.partialErrors) }
    else errs.push(`关联 Pod 读取失败：${apiErrorText(pr.reason)}`)
    if (er.status === 'fulfilled') { const u = unwrap(er.value); drawer.events = Array.isArray(u.data) ? u.data : []; errs.push(...u.meta.partialErrors) }
    else errs.push(`事件读取失败：${apiErrorText(er.reason)}`)
    drawer.err = errs.join('；')
  } finally {
    drawer.loading = false
  }
}

function replLabel(w) {
  const d = Number(w?.replicas?.desired ?? 0)
  const r = Number(w?.replicas?.ready ?? 0)
  if (!d) return '无副本'
  return r >= d ? '就绪' : '未就绪'
}
function replClass(w) {
  const d = Number(w?.replicas?.desired ?? 0)
  const r = Number(w?.replicas?.ready ?? 0)
  if (!d) return 'tag-slate'
  return r >= d ? 'tag-ok' : 'tag-warn'
}
function phaseLabel(phase) {
  return ({ Running: '运行中', Pending: '等待中', Failed: '失败', Unknown: '未知', Succeeded: '已完成' })[phase] || phase || '—'
}
function phaseTagClass(phase) {
  return ({ Running: 'tag-ok', Pending: 'tag-warn', Failed: 'tag-err', Unknown: 'tag-err', Succeeded: 'tag-slate' })[phase] || 'tag-slate'
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
function evTime(ev) {
  const v = ev?.last_timestamp || ev?.first_timestamp || ev?.time || ''
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleTimeString('zh-CN', { hour12: false })
}
function evObj(ev) {
  const io = ev?.involved_object || {}
  return [io.kind, io.name].filter(Boolean).join('/') || ev?.object || '—'
}
function evMsg(ev) { return ev?.message || ev?.msg || ev?.note || ev?.reason || '—' }
function evType(ev) { return ev?.type || '—' }

function restoreUi() {
  const saved = getTabState(workbench.state.activeKey) || {}
  const f = saved.filters || {}
  if (typeof f.namespace === 'string') filters.namespace = f.namespace
  if (typeof f.type === 'string') filters.type = f.type
  if (typeof f.status === 'string') filters.status = f.status
  if (typeof f.keyword === 'string') filters.keyword = f.keyword
  if (saved.scrollY !== undefined) {
    nextTick(() => { const el = document.querySelector('.content'); if (el) el.scrollTop = saved.scrollY })
  }
}
function saveUi() {
  const el = document.querySelector('.content')
  setTabState(workbench.state.activeKey, {
    filters: { namespace: filters.namespace, type: filters.type, status: filters.status, keyword: filters.keyword },
    scrollY: el ? el.scrollTop : 0,
  })
}

watch(filters, () => {
  if (!tabAlive) return
  saveUi()
  if (!cid()) return
  clearTimeout(kwTimer)
  kwTimer = setTimeout(() => { if (isActive.value && tabAlive) load() }, 300)
}, { deep: true })

watch(selectedClusterId, (id) => {
  if (!tabAlive) return
  rows.value = []
  Object.assign(meta, { ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
  fatal.value = ''
  loadedOnce = false
  if (drawer.open) closeDrawer()
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
  clearTimeout(kwTimer)
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
.filters .kw { min-width: 200px; }
.table-scroll { overflow: auto; }
.row-click { cursor: pointer; }
.err-text { color: var(--err); font-weight: 600; }
.img-cell { max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty-inline { text-align: center; color: var(--muted); padding: 22px 10px; font-size: 13px; }
.nowrap { white-space: nowrap; }
.tiny { font-size: 12px; }
.ev-msg { word-break: break-all; }
.drawer-mask { position: fixed; inset: 0; background: rgba(15, 27, 45, .32); z-index: 220; }
.drawer { position: fixed; top: 0; right: 0; bottom: 0; width: 560px; max-width: 92vw; background: var(--card); border-left: 1px solid var(--border); box-shadow: -12px 0 32px rgba(15, 27, 45, .12); z-index: 221; display: flex; flex-direction: column; }
.drawer-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--border); }
.drawer-body { flex: 1; overflow: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 10px; }
.kv { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; padding: 5px 0; border-bottom: 1px dashed var(--border); }
.kv span { color: var(--muted); flex: none; }
.kv b { font-weight: 600; text-align: right; word-break: break-all; }
.sub-title { margin: 4px 0 2px; font-size: 13px; }
.drawer-sub { display: flex; align-items: center; justify-content: space-between; margin-top: 4px; }
.drawer-sub h4 { margin: 0; }
.drawer-table { max-height: 320px; }
.drawer-err { color: var(--warn); }
.tag-ok { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #ecfdf5; color: #059669; }
.tag-warn { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; }
.tag-err { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fef2f2; color: #dc2626; }
</style>
