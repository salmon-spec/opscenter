<template>
  <div class="view k8s">
    <div class="view-head">
      <div>
        <h1 class="view-title">任务与事件</h1>
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
        <div class="big">⚠ 任务与事件加载失败</div>
        <div class="muted">{{ fatal }}</div>
        <div><button class="btn btn-sm" :disabled="loading" @click="load">重试</button></div>
      </div>
      <div v-else-if="loading && !hasData" class="loading"><span class="spinner"></span>正在读取任务与事件…</div>
      <template v-else>
        <div v-if="partialText" class="partial">
          <span>⚠ 部分数据源异常：</span><span class="partial-item">{{ partialText }}</span>
        </div>

        <div class="filters">
          <select v-model="filters.kind" class="select">
            <option value="all">资源：全部</option>
            <option value="jobs">Jobs</option>
            <option value="cronjobs">CronJobs</option>
            <option value="events">Events</option>
          </select>
          <select v-model="filters.namespace" class="select">
            <option value="">命名空间：全部</option>
            <option v-for="ns in namespaceOptions" :key="ns" :value="ns">{{ ns }}</option>
          </select>
          <input v-model="filters.keyword" class="input kw" placeholder="搜索名称、命名空间、状态、对象、原因或消息" />
          <span class="muted tiny">Job {{ filteredJobs.length }} · CronJob {{ filteredCronjobs.length }} · Event {{ filteredEvents.length }}</span>
        </div>

        <section v-if="showKind('jobs')" class="card">
          <div class="card-head">
            <h3>Jobs（{{ filteredJobs.length }} / {{ jobs.length }}）</h3>
            <select v-model="filters.jobStatus" class="select select-sm">
              <option value="">状态：全部</option>
              <option value="running">运行中</option>
              <option value="succeeded">成功</option>
              <option value="failed">失败</option>
              <option value="pending">等待中</option>
            </select>
          </div>
          <div class="table-scroll" v-if="jobs.length">
            <table class="table">
              <thead><tr><th>名称</th><th>命名空间</th><th>状态</th><th>完成度</th><th>耗时</th><th>Age</th></tr></thead>
              <tbody>
                <tr v-for="(j, i) in filteredJobs" :key="`${j.namespace || ''}/${j.name || i}`">
                  <td><b>{{ j.name || '—' }}</b></td>
                  <td>{{ j.namespace || '—' }}</td>
                  <td><span class="tag" :class="jobClass(j)">{{ jobLabel(j) }}</span></td>
                  <td class="mono">{{ completionsText(j) }}</td>
                  <td>{{ j.duration ? fmtDuration(j.duration) : '—' }}</td>
                  <td class="nowrap">{{ fmtAge(j.age) }}</td>
                </tr>
                <tr v-if="!filteredJobs.length"><td colspan="6" class="empty-inline">当前条件下没有 Job</td></tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-inline">暂无 Job</div>
        </section>

        <section v-if="showKind('cronjobs')" class="card">
          <div class="card-head">
            <h3>CronJobs（{{ filteredCronjobs.length }} / {{ cronjobs.length }}）</h3>
            <select v-model="filters.cronStatus" class="select select-sm">
              <option value="">状态：全部</option>
              <option value="active">启用中</option>
              <option value="suspended">已停用</option>
            </select>
          </div>
          <div class="table-scroll" v-if="cronjobs.length">
            <table class="table">
              <thead><tr><th>名称</th><th>命名空间</th><th>调度</th><th>状态</th><th>上次调度</th><th>活跃</th></tr></thead>
              <tbody>
                <tr v-for="(cj, i) in filteredCronjobs" :key="`${cj.namespace || ''}/${cj.name || i}`" :class="{ dim: isSuspended(cj) }">
                  <td><b>{{ cj.name || '—' }}</b></td>
                  <td>{{ cj.namespace || '—' }}</td>
                  <td class="mono">{{ cj.schedule || '—' }}</td>
                  <td><span v-if="isSuspended(cj)" class="tag tag-slate">已停用</span><span v-else class="tag tag-ok">启用中</span></td>
                  <td class="tiny nowrap">{{ cj.last_schedule ? fmtTime(cj.last_schedule) : '从未' }}</td>
                  <td>{{ Number(cj.active ?? 0) }}</td>
                </tr>
                <tr v-if="!filteredCronjobs.length"><td colspan="6" class="empty-inline">当前条件下没有 CronJob</td></tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-inline">暂无 CronJob</div>
        </section>

        <section v-if="showKind('events')" class="card">
          <div class="card-head">
            <h3>Events（{{ filteredEvents.length }} / {{ events.length }}）</h3>
            <div class="seg">
              <button class="btn btn-sm" :class="{ 'seg-on': eventFilter === 'all' }" @click="setEventFilter('all')">全部</button>
              <button class="btn btn-sm" :class="{ 'seg-on': eventFilter === 'Warning' }" @click="setEventFilter('Warning')">Warning</button>
              <button class="btn btn-sm" :class="{ 'seg-on': eventFilter === 'Normal' }" @click="setEventFilter('Normal')">Normal</button>
            </div>
          </div>
          <div v-if="eventsLoading" class="loading" style="padding:18px"><span class="spinner"></span>正在读取事件…</div>
          <div v-else-if="events.length" class="table-scroll tall">
            <table class="table">
              <thead><tr><th>时间</th><th>类型</th><th>命名空间</th><th>对象</th><th>原因</th><th>消息</th></tr></thead>
              <tbody>
                <tr v-for="(ev, i) in filteredEvents" :key="i">
                  <td class="mono tiny nowrap">{{ evTime(ev) }}</td>
                  <td><span class="tag" :class="String(evType(ev)).toLowerCase() === 'warning' ? 'tag-warn' : 'tag-slate'">{{ evType(ev) }}</span></td>
                  <td>{{ evNs(ev) }}</td>
                  <td class="tiny">{{ evObj(ev) }}</td>
                  <td class="tiny">{{ ev.reason || '—' }}</td>
                  <td class="tiny ev-msg">{{ evMsg(ev) }}</td>
                </tr>
                <tr v-if="!filteredEvents.length"><td colspan="6" class="empty-inline">当前条件下没有事件</td></tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-inline">暂无事件</div>
        </section>
      </template>
    </template>
  </div>
</template>

<script>
export default { name: 'KubernetesOperations' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { fmtTime } from '../../api'
import { useTabActive, setTabState, getTabState } from '../../workbench/tabs'
import workbench from '../../workbench/tabs'
import { useClusterContext } from '../../workbench/clusters'
import { k8sApi, unwrap, apiErrorText } from '../../api/kubernetes'

const { selectedClusterId, currentCluster, refreshClusters } = useClusterContext()
const { isActive, tabKey } = useTabActive('KubernetesOperations')

const POLL_MS = 15000
const jobs = ref([])
const cronjobs = ref([])
const events = ref([])
const eventFilter = ref('all')
const filters = reactive({ kind: 'all', namespace: '', keyword: '', jobStatus: '', cronStatus: '' })
const eventsLoading = ref(false)
const meta = reactive({ ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
const fatal = ref('')
const loading = ref(false)
const clusterErr = ref('')
const nowTick = ref(Date.now())

let controller = null
let eventsCtl = null
let pollTimer = null
let inFlight = false
let tabAlive = false
let loadedOnce = false

const cid = () => selectedClusterId.value
const clusterMissing = computed(() => !cid())
const hasData = computed(() => jobs.value.length > 0 || cronjobs.value.length > 0 || events.value.length > 0)
const partialText = computed(() => meta.partialErrors.join('；'))
const stampText = computed(() => {
  if (!meta.ts) return ''
  const t = new Date(meta.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  return meta.cached ? `${t} · 缓存 ${meta.cacheAge.toFixed(0)}s` : t
})
const stale = computed(() => meta.ts > 0 && nowTick.value / 1000 - meta.ts > 60)
const subText = computed(() => {
  const c = currentCluster.value
  if (!c) return 'Job、CronJob 与 Kubernetes 事件'
  return `${c.name}${c.version ? ' · ' + c.version : ''} · Job / CronJob / Events`
})

const namespaceOptions = computed(() => [...new Set([
  ...jobs.value.map((item) => item.namespace),
  ...cronjobs.value.map((item) => item.namespace),
  ...events.value.map((item) => evNs(item) === '—' ? '' : evNs(item)),
].filter(Boolean))].sort())

const filteredJobs = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return jobs.value.filter((item) => {
    if (filters.namespace && item.namespace !== filters.namespace) return false
    if (filters.jobStatus && jobState(item) !== filters.jobStatus) return false
    return !keyword || [item.name, item.namespace, jobLabel(item), completionsText(item), fmtDuration(item.duration)]
      .filter(Boolean).join(' ').toLowerCase().includes(keyword)
  })
})

const filteredCronjobs = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return cronjobs.value.filter((item) => {
    if (filters.namespace && item.namespace !== filters.namespace) return false
    if (filters.cronStatus && cronState(item) !== filters.cronStatus) return false
    return !keyword || [item.name, item.namespace, item.schedule, isSuspended(item) ? '已停用' : '启用中', item.active]
      .filter((v) => v !== null && v !== undefined).join(' ').toLowerCase().includes(keyword)
  })
})

const filteredEvents = computed(() => {
  const keyword = filters.keyword.trim().toLowerCase()
  return events.value.filter((item) => {
    const namespace = evNs(item)
    if (filters.namespace && namespace !== filters.namespace) return false
    return !keyword || [evType(item), namespace, evObj(item), item.reason, evMsg(item)]
      .filter(Boolean).join(' ').toLowerCase().includes(keyword)
  })
})

function showKind(kind) { return filters.kind === 'all' || filters.kind === kind }

async function initClusters(force = false) {
  clusterErr.value = ''
  try { await refreshClusters(force) } catch (e) { clusterErr.value = e?.message || '加载失败' }
}

function normJobs(d) {
  if (Array.isArray(d)) return { jobs: d, cronjobs: [] }
  if (d && typeof d === 'object') {
    return { jobs: Array.isArray(d.jobs) ? d.jobs : [], cronjobs: Array.isArray(d.cronjobs) ? d.cronjobs : (Array.isArray(d.cron_jobs) ? d.cron_jobs : []) }
  }
  return { jobs: [], cronjobs: [] }
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
    const [jr, er] = await Promise.allSettled([
      k8sApi.jobs(cidNow, undefined, { signal, timeoutMs: 20000 }),
      k8sApi.events(cidNow, { type: eventFilter.value || undefined, limit: 200 }, { signal, timeoutMs: 20000 }),
    ])
    if (cid() !== cidNow) return
    const errs = []
    const metas = []
    if (jr.status === 'fulfilled') {
      const u = unwrap(jr.value)
      const n = normJobs(u.data)
      jobs.value = n.jobs
      cronjobs.value = n.cronjobs
      metas.push(u.meta)
    } else errs.push(`任务读取失败：${apiErrorText(jr.reason)}`)
    if (er.status === 'fulfilled') { const u = unwrap(er.value); events.value = Array.isArray(u.data) ? u.data : []; metas.push(u.meta) }
    else errs.push(`事件读取失败：${apiErrorText(er.reason)}`)
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
    fatal.value = errs.length === 2 ? errs.join('；') : ''
    loadedOnce = true
    nowTick.value = Date.now()
  } finally {
    inFlight = false
    if (cid() === cidNow) loading.value = false
  }
}

async function loadEvents() {
  const cidNow = cid()
  if (!cidNow || eventsLoading.value) return
  eventsLoading.value = true
  eventsCtl?.abort()
  eventsCtl = new AbortController()
  const { signal } = eventsCtl
  try {
    const res = await k8sApi.events(cidNow, { type: eventFilter.value || undefined, limit: 200 }, { signal, timeoutMs: 20000 })
    if (cid() !== cidNow) return
    const u = unwrap(res)
    events.value = Array.isArray(u.data) ? u.data : []
    if (u.meta.partialErrors.length) meta.partialErrors = [...u.meta.partialErrors]
  } catch (e) {
    if (e.name === 'AbortError' || cid() !== cidNow) return
    meta.partialErrors = [`事件读取失败：${apiErrorText(e)}`]
  } finally {
    eventsLoading.value = false
  }
}

function setEventFilter(v) {
  if (eventFilter.value === v) return
  eventFilter.value = v
  saveUi()
  loadEvents()
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

function jobState(job) {
  const raw = typeof job === 'string' ? job : job?.status
  const s = String(raw || '').toLowerCase()
  if (s === 'succeeded' || s === 'complete' || s === 'completed') return 'succeeded'
  if (s === 'failed') return 'failed'
  if (s === 'running' || s === 'active') return 'running'
  if (Number(job?.active ?? 0) > 0) return 'running'
  if (Number(job?.failed ?? 0) > 0) return 'failed'
  if (Number(job?.succeeded ?? 0) > 0) return 'succeeded'
  return s || 'pending'
}
function jobLabel(job) {
  const s = jobState(job)
  if (s === 'succeeded' || s === 'complete' || s === 'completed') return '成功'
  if (s === 'failed') return '失败'
  if (s === 'running' || s === 'active') return '运行中'
  if (s === 'pending') return '等待中'
  return s || '—'
}
function jobClass(job) {
  const s = jobState(job)
  if (s === 'succeeded' || s === 'complete' || s === 'completed') return 'tag-ok'
  if (s === 'failed') return 'tag-err'
  if (s === 'running' || s === 'active') return 'tag-ok'
  return 'tag-slate'
}
function completionsText(j) {
  const c = j?.completions
  if (c !== null && c !== undefined) {
    if (typeof c === 'object') return `${c.succeeded ?? c.ready ?? 0}/${c.desired ?? '?'}`
    return String(c)
  }
  if (j?.succeeded !== undefined || j?.failed !== undefined || j?.active !== undefined) {
    return `${Number(j.succeeded ?? 0)} 成功 / ${Number(j.failed ?? 0)} 失败 / ${Number(j.active ?? 0)} 活跃`
  }
  return '—'
}
function isSuspended(cj) { return cj?.suspend === true || cj?.suspended === true }
function cronState(cj) { return isSuspended(cj) ? 'suspended' : 'active' }
function fmtDuration(sec) {
  const n = Number(sec)
  if (!n || n < 0) return '—'
  const d = Math.floor(n / 86400)
  const h = Math.floor((n % 86400) / 3600)
  const m = Math.floor((n % 3600) / 60)
  if (d > 0) return `${d}天${h}小时`
  if (h > 0) return `${h}小时${m}分`
  return `${m}分钟`
}
// Age accepts elapsed seconds or an ISO creation timestamp.
function fmtAge(v) {
  if (typeof v === 'string' && v.includes('T')) {
    const timestamp = Date.parse(v)
    if (Number.isFinite(timestamp)) v = (Date.now() - timestamp) / 1000
  }
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v !== 'number') return String(v)
  const sec = Math.max(0, Math.round(v))
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`
  return `${Math.floor(sec / 86400)}d`
}
function evTime(ev) {
  const v = ev?.last_at || ev?.first_at || ev?.last_timestamp || ev?.first_timestamp || ev?.time || ''
  if (!v) return '—'
  const dt = new Date(v)
  return Number.isNaN(dt.getTime()) ? String(v) : dt.toLocaleTimeString('zh-CN', { hour12: false })
}
function evNs(ev) { return ev?.namespace || ev?.involved_object?.namespace || '—' }
function evObj(ev) {
  const io = ev?.involved_object || {}
  return [io.kind, io.name].filter(Boolean).join('/') || ev?.object || '—'
}
function evMsg(ev) { return ev?.message || ev?.msg || ev?.note || ev?.reason || '—' }
function evType(ev) { return ev?.type || '—' }

function restoreUi() {
  const saved = getTabState(tabKey.value) || {}
  const f = saved.filters || {}
  if (typeof f.kind === 'string') filters.kind = f.kind
  if (typeof f.namespace === 'string') filters.namespace = f.namespace
  if (typeof f.keyword === 'string') filters.keyword = f.keyword
  if (typeof f.jobStatus === 'string') filters.jobStatus = f.jobStatus
  if (typeof f.cronStatus === 'string') filters.cronStatus = f.cronStatus
  if (saved.eventFilter === '' || saved.eventFilter === 'all') eventFilter.value = 'all'
  else if (saved.eventFilter === 'Warning' || saved.eventFilter === 'Normal') eventFilter.value = saved.eventFilter
  if (saved.scrollY !== undefined) {
    nextTick(() => { const el = document.querySelector('.content'); if (el) el.scrollTop = saved.scrollY })
  }
}
function saveUi() {
  const el = document.querySelector('.content')
  setTabState(tabKey.value, {
    filters: { kind: filters.kind, namespace: filters.namespace, keyword: filters.keyword, jobStatus: filters.jobStatus, cronStatus: filters.cronStatus },
    eventFilter: eventFilter.value,
    scrollY: el ? el.scrollTop : 0,
  })
}

watch(filters, () => {
  if (tabAlive) saveUi()
}, { deep: true })

watch(selectedClusterId, (id) => {
  if (!tabAlive) return
  jobs.value = []
  cronjobs.value = []
  events.value = []
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
  eventsCtl?.abort()
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
.filters .kw { min-width: 280px; }
.card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.card-head .select { width: auto; }
.card-head h3 { margin: 0; font-size: 14px; }
.seg { display: flex; gap: 0; }
.seg .btn { border-radius: 0; }
.seg .btn:first-child { border-radius: 6px 0 0 6px; }
.seg .btn:last-child { border-radius: 0 6px 6px 0; }
.seg-on { background: var(--brand); border-color: var(--brand); color: #fff; }
.seg-on:hover { color: #fff; }
.table-scroll { overflow: auto; }
.table-scroll.tall { max-height: 560px; }
.dim td { opacity: .55; }
.empty-inline { text-align: center; color: var(--muted); padding: 22px 10px; font-size: 13px; }
.nowrap { white-space: nowrap; }
.tiny { font-size: 12px; }
.ev-msg { word-break: break-all; }
.tag-ok { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #ecfdf5; color: #059669; }
.tag-warn { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; }
.tag-err { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fef2f2; color: #dc2626; }
</style>
