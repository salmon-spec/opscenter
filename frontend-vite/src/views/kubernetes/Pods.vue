<template>
  <div class="view k8s">
    <div class="view-head">
      <div>
        <h1 class="view-title">Pod</h1>
        <p class="view-sub">{{ detailMode ? `Pod 详情 · ${detailPod}` : subText }}</p>
      </div>
      <div class="head-actions">
        <span v-if="!detailMode && stampText" class="muted stamp">数据 {{ stampText }}<span v-if="stale" class="stale">数据陈旧</span></span>
        <button v-if="detailMode" class="btn btn-sm" @click="backToList">← 返回列表</button>
        <button v-else class="btn btn-sm" :disabled="loading" @click="load">↻ 刷新</button>
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
      <template v-if="!detailMode">
        <div v-if="fatal && !hasList" class="card k8s-error">
          <div class="big">⚠ Pod 列表加载失败</div>
          <div class="muted">{{ fatal }}</div>
          <div><button class="btn btn-sm" :disabled="loading" @click="load">重试</button></div>
        </div>
        <div v-else-if="loading && !hasList" class="loading"><span class="spinner"></span>正在读取 Pod 列表…</div>
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
              <select v-model="filters.node" class="select">
                <option value="">节点：全部</option>
                <option v-for="nd in nodeOptions" :key="nd" :value="nd">{{ nd }}</option>
              </select>
              <select v-model="filters.phase" class="select">
                <option value="">Phase：全部</option>
                <option v-for="ph in phases" :key="ph" :value="ph">{{ ph }}</option>
              </select>
              <input v-model="filters.keyword" class="input kw" placeholder="搜索 Pod 名称" />
              <span class="muted tiny">共 {{ podsList.length }} 个</span>
            </div>
            <div class="table-scroll">
              <table class="table">
                <thead><tr><th>Pod</th><th>命名空间</th><th>Phase</th><th>Ready</th><th>重启</th><th>Pod IP</th><th>节点</th><th>QoS</th><th>Age</th></tr></thead>
                <tbody>
                  <tr v-for="p in podsList" :key="`${p.namespace || ''}/${p.name}`" class="row-click" @click="openPod(p)">
                    <td><b>{{ p.name }}</b></td>
                    <td>{{ p.namespace || '—' }}</td>
                    <td><span class="tag" :class="phaseTagClass(p.phase)">{{ phaseLabel(p.phase) }}</span></td>
                    <td><span class="mono" :class="readyWarnClass(p.ready)">{{ p.ready || '—' }}</span></td>
                    <td><span :class="Number(p.restarts) > 0 ? 'err-text' : ''">{{ p.restarts ?? 0 }}</span></td>
                    <td class="mono">{{ p.pod_ip || '—' }}</td>
                    <td class="tiny">{{ p.node || '—' }}</td>
                    <td>{{ p.qos || '—' }}</td>
                    <td class="nowrap">{{ fmtAge(p.age) }}</td>
                  </tr>
                  <tr v-if="!podsList.length"><td colspan="9" class="empty-inline">当前条件下没有 Pod</td></tr>
                </tbody>
              </table>
            </div>
          </section>
        </template>
      </template>

      <template v-else>
        <div v-if="podFatal && !podDetail" class="card k8s-error">
          <div class="big">⚠ Pod 详情加载失败</div>
          <div class="muted">{{ podFatal }}</div>
          <div><button class="btn btn-sm" :disabled="detailLoading" @click="loadDetail">重试</button></div>
        </div>
        <div v-else-if="detailLoading && !podDetail" class="loading"><span class="spinner"></span>正在读取 Pod 详情…</div>
        <template v-else-if="podDetail">
          <div v-if="podFatal" class="partial">
            <span>⚠ 详情刷新失败：</span><span class="partial-item">{{ podFatal }}</span>
          </div>
          <section class="card">
            <div class="pod-head">
              <div>
                <h3 class="pod-name mono">{{ detailPod }}</h3>
                <div class="muted tiny">{{ d.namespace || detailNs }}<template v-if="d.node"> · 节点 {{ d.node }}</template></div>
              </div>
              <div class="pod-tags">
                <span class="tag" :class="phaseTagClass(d.phase)">{{ phaseLabel(d.phase) }}</span>
                <span v-if="d.qos" class="tag tag-slate">QoS {{ d.qos }}</span>
              </div>
            </div>
            <div class="kvgrid">
              <div class="kv"><span>Ready</span><b class="mono" :class="readyWarnClass(d.ready)">{{ d.ready || '—' }}</b></div>
              <div class="kv"><span>重启次数</span><b :class="Number(d.restarts) > 0 ? 'err-text' : ''">{{ d.restarts ?? 0 }}</b></div>
              <div class="kv"><span>Pod IP</span><b class="mono">{{ d.pod_ip || '—' }}</b></div>
              <div class="kv"><span>节点</span><b>{{ d.node || '—' }}</b></div>
              <div class="kv"><span>创建时间</span><b>{{ d.created_at ? fmtTime(d.created_at) : '—' }}</b></div>
              <div class="kv"><span>Owner</span><b>{{ d.owner ? [d.owner.kind, d.owner.name].filter(Boolean).join('/') : '—' }}</b></div>
            </div>
          </section>

          <section class="card">
            <div class="card-head"><h3>容器（{{ containers.length }}）</h3></div>
            <table class="table">
              <thead><tr><th>名称</th><th>就绪</th><th>重启</th><th>状态</th><th>Probe</th></tr></thead>
              <tbody>
                <tr v-for="c in containers" :key="c.name">
                  <td class="mono">{{ c.name }}</td>
                  <td><span class="tag" :class="c.ready ? 'tag-ok' : 'tag-err'">{{ c.ready ? '是' : '否' }}</span></td>
                  <td><span :class="Number(c.restarts) > 0 ? 'err-text' : ''">{{ c.restarts ?? 0 }}</span></td>
                  <td>{{ c.state || '—' }}</td>
                  <td class="tiny">{{ probesText(c) }}</td>
                </tr>
                <tr v-if="!containers.length"><td colspan="5" class="empty-inline">暂无容器</td></tr>
              </tbody>
            </table>
          </section>

          <section v-if="initContainers.length" class="card">
            <div class="card-head"><h3>Init 容器（{{ initContainers.length }}）</h3></div>
            <table class="table">
              <thead><tr><th>名称</th><th>就绪</th><th>重启</th><th>状态</th><th>Probe</th></tr></thead>
              <tbody>
                <tr v-for="c in initContainers" :key="c.name">
                  <td class="mono">{{ c.name }}</td>
                  <td><span class="tag" :class="c.ready ? 'tag-ok' : 'tag-warn'">{{ c.ready ? '是' : '否' }}</span></td>
                  <td><span :class="Number(c.restarts) > 0 ? 'err-text' : ''">{{ c.restarts ?? 0 }}</span></td>
                  <td>{{ c.state || '—' }}</td>
                  <td class="tiny">{{ probesText(c) }}</td>
                </tr>
              </tbody>
            </table>
          </section>

          <section class="card">
            <div class="card-head"><h3>资源与用量</h3></div>
            <div class="kv"><span>Requests</span><b class="mono tiny">{{ kvText(res.requests) }}</b></div>
            <div class="kv"><span>Limits</span><b class="mono tiny">{{ kvText(res.limits) }}</b></div>
            <div class="kv"><span>当前用量</span><b><span v-if="usage" class="mono tiny">{{ kvText(usage) }}</span><span v-else class="muted">指标源不可用</span></b></div>
          </section>

          <section class="card">
            <div class="card-head"><h3>近期事件（{{ detailEvents.length }}）</h3></div>
            <table v-if="detailEvents.length" class="table">
              <thead><tr><th>时间</th><th>类型</th><th>对象</th><th>消息</th></tr></thead>
              <tbody>
                <tr v-for="(ev, i) in detailEvents" :key="i">
                  <td class="mono tiny nowrap">{{ evTime(ev) }}</td>
                  <td><span class="tag" :class="String(evType(ev)).toLowerCase() === 'warning' ? 'tag-warn' : 'tag-slate'">{{ evType(ev) }}</span></td>
                  <td class="tiny">{{ evObj(ev) }}</td>
                  <td class="tiny ev-msg">{{ evMsg(ev) }}</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="empty-inline">暂无事件</div>
          </section>

          <section class="card">
            <div class="card-head"><h3>日志查看器（只读）</h3><span v-if="logMeta?.truncated" class="tag tag-warn">日志已截断</span></div>
            <div class="log-bar">
              <select v-model="logForm.container" class="select" aria-label="选择容器">
                <option v-for="c in containerNames" :key="c" :value="c">{{ c }}</option>
              </select>
              <select v-model="logForm.tail" class="select" aria-label="日志行数">
                <option :value="300">最近 300 行</option>
                <option :value="500">最近 500 行</option>
                <option :value="1000">最近 1000 行</option>
              </select>
              <select v-model="logForm.since" class="select" aria-label="时间范围">
                <option value="">不限时间</option>
                <option value="600">最近 10 分钟</option>
                <option value="3600">最近 1 小时</option>
              </select>
              <button class="btn btn-sm" :disabled="logLoading || !logForm.container" @click="loadLogs">{{ logLoading ? '加载中…' : '查询日志' }}</button>
            </div>
            <div v-if="logErr" class="log-err tiny">日志读取失败：{{ logErr }} <button class="btn btn-sm" @click="loadLogs">重试</button></div>
            <pre v-else class="log-view">{{ logText }}</pre>
            <div class="muted tiny">日志为受限行数的只读快照，不提供任何写操作。</div>
          </section>
        </template>
      </template>
    </template>
  </div>
</template>

<script>
export default { name: 'KubernetesPods' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { fmtTime } from '../../api'
import { useTabActive, setTabState, getTabState, setTabContext } from '../../workbench/tabs'
import workbench from '../../workbench/tabs'
import { useClusterContext } from '../../workbench/clusters'
import { k8sApi, unwrap, apiErrorText } from '../../api/kubernetes'

const route = useRoute()
const router = useRouter()
const { selectedClusterId, currentCluster, refreshClusters } = useClusterContext()
const { isActive } = useTabActive('KubernetesPods')

const detailPod = computed(() => String(route.query.pod || ''))
const detailNs = computed(() => String(route.query.ns || ''))
const detailMode = computed(() => !!detailPod.value)

const podsList = ref([])
const meta = reactive({ ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
const fatal = ref('')
const loading = ref(false)
const clusterErr = ref('')
const nowTick = ref(Date.now())
const filters = reactive({ namespace: '', node: '', phase: '', keyword: '' })

const podDetail = ref(null)
const detailTs = ref(0)
const podFatal = ref('')
const detailLoading = ref(false)
const logs = ref('')
const logMeta = ref(null)
const logErr = ref('')
const logLoading = ref(false)
const logForm = reactive({ container: '', tail: 300, since: '' })

const phases = ['Running', 'Pending', 'Failed', 'Unknown', 'Succeeded']
let listCtl = null
let detailCtl = null
let logCtl = null
let pollTimer = null
let kwTimer = null
let listInFlight = false
let detailInFlight = false
let tabAlive = false
let listLoaded = false
let logInited = false

const POLL_MS = computed(() => (detailMode.value ? 15000 : 10000))
const cid = () => selectedClusterId.value
const clusterMissing = computed(() => !cid())
const hasList = computed(() => podsList.value.length > 0)
const partialText = computed(() => meta.partialErrors.join('；'))
const nsOptions = computed(() => [...new Set(podsList.value.map((p) => p.namespace).filter(Boolean))].sort())
const nodeOptions = computed(() => [...new Set(podsList.value.map((p) => p.node).filter(Boolean))].sort())
const stampText = computed(() => {
  if (!meta.ts) return ''
  const t = new Date(meta.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  return meta.cached ? `${t} · 缓存 ${meta.cacheAge.toFixed(0)}s` : t
})
const stale = computed(() => meta.ts > 0 && nowTick.value / 1000 - meta.ts > 60)
const subText = computed(() => {
  const c = currentCluster.value
  if (!c) return 'Pod 列表与只读详情'
  return `${c.name}${c.version ? ' · ' + c.version : ''} · Pod 列表与只读详情`
})

const d = computed(() => podDetail.value || {})
const containers = computed(() => (Array.isArray(d.value.containers) ? d.value.containers : []))
const initContainers = computed(() => (Array.isArray(d.value.init_containers) ? d.value.init_containers : []))
const containerNames = computed(() => containers.value.map((c) => c.name).filter(Boolean))
const res = computed(() => d.value.resources || {})
const usage = computed(() => d.value.usage ?? null)
const detailEvents = computed(() => (Array.isArray(d.value.events) ? d.value.events : []))
const logText = computed(() => {
  if (logLoading.value) return '正在读取日志…'
  return logs.value || '（暂无日志输出）'
})

async function initClusters(force = false) {
  clusterErr.value = ''
  try { await refreshClusters(force) } catch (e) { clusterErr.value = e?.message || '加载失败' }
}

function listQuery() {
  return {
    namespace: filters.namespace || undefined,
    node: filters.node || undefined,
    phase: filters.phase || undefined,
    keyword: filters.keyword.trim() || undefined,
    limit: 200,
  }
}

async function load() {
  if (detailMode.value) return loadDetail()
  return loadList()
}

async function loadList() {
  const cidNow = cid()
  if (!cidNow || listInFlight) return
  listInFlight = true
  loading.value = true
  listCtl?.abort()
  listCtl = new AbortController()
  const { signal } = listCtl
  try {
    const res = await k8sApi.pods(cidNow, listQuery(), { signal, timeoutMs: 20000 })
    if (cid() !== cidNow) return
    const u = unwrap(res)
    podsList.value = Array.isArray(u.data) ? u.data : []
    meta.ts = u.meta.ts
    meta.cached = u.meta.cached
    meta.cacheAge = u.meta.cacheAge
    meta.partialErrors = [...u.meta.partialErrors]
    fatal.value = ''
    listLoaded = true
    nowTick.value = Date.now()
  } catch (e) {
    if (e.name === 'AbortError' || cid() !== cidNow) return
    fatal.value = apiErrorText(e)
  } finally {
    listInFlight = false
    if (cid() === cidNow) loading.value = false
  }
}

async function loadDetail() {
  const cidNow = cid()
  const ns = detailNs.value
  const name = detailPod.value
  if (!cidNow || detailInFlight) return
  if (!ns || !name) { podFatal.value = '缺少 namespace 或 Pod 名称参数（URL 需含 ?ns=&pod=）'; return }
  detailInFlight = true
  detailLoading.value = true
  detailCtl?.abort()
  detailCtl = new AbortController()
  const { signal } = detailCtl
  try {
    const res = await k8sApi.pod(cidNow, ns, name, { signal, timeoutMs: 20000 })
    if (cid() !== cidNow) return
    const u = unwrap(res)
    podDetail.value = u.data || {}
    podFatal.value = u.meta.partialErrors.length ? u.meta.partialErrors.join('；') : ''
    detailTs.value = u.meta.ts
    nowTick.value = Date.now()
    if (!logInited) {
      logInited = true
      if (!logForm.container && containerNames.value.length) logForm.container = containerNames.value[0]
      loadLogs()
    }
  } catch (e) {
    if (e.name === 'AbortError' || cid() !== cidNow) return
    podFatal.value = apiErrorText(e)
  } finally {
    detailInFlight = false
    if (cid() === cidNow) detailLoading.value = false
  }
}

async function loadLogs() {
  const cidNow = cid()
  const ns = detailNs.value || d.value.namespace
  const name = detailPod.value
  if (!cidNow || !ns || !name || !logForm.container || logLoading.value) return
  logLoading.value = true
  logErr.value = ''
  logCtl?.abort()
  logCtl = new AbortController()
  const { signal } = logCtl
  try {
    const u = unwrap(await k8sApi.podLogs(cidNow, ns, name, {
      container: logForm.container,
      tail_lines: logForm.tail,
      since_seconds: logForm.since ? Number(logForm.since) : undefined,
    }, { signal, timeoutMs: 20000 }))
    if (cid() !== cidNow) return
    const payload = u.data || {}
    logs.value = typeof payload.data === 'string' ? payload.data : ''
    logMeta.value = payload
    logErr.value = u.meta.partialErrors.length ? u.meta.partialErrors.join('；') : ''
  } catch (e) {
    if (e.name === 'AbortError' || cid() !== cidNow) return
    logErr.value = apiErrorText(e)
    logs.value = ''
    logMeta.value = null
  } finally {
    logLoading.value = false
  }
}

function openPod(p) {
  if (!p?.name) return
  setTabContext({ namespace: p.namespace || '', resourceUid: p.name, titleSuffix: p.name })
  router.push({ path: '/kubernetes/pods', query: { ns: p.namespace || '', pod: p.name } })
}

function backToList() {
  router.push({ path: '/kubernetes/pods' })
}

function tick() {
  nowTick.value = Date.now()
  if (!isActive.value || !tabAlive || document.hidden || !cid()) return
  if (detailMode.value) { if (!detailInFlight) loadDetail() } else if (!listInFlight) load()
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(tick, POLL_MS.value)
}
function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null } }

function ensureFresh() {
  if (!cid()) return
  if (detailMode.value) {
    const fresh = !!podDetail.value && detailTs.value > 0 && nowTick.value / 1000 - detailTs.value <= 15
    if (!fresh) loadDetail()
    return
  }
  const fresh = listLoaded && meta.ts > 0 && nowTick.value / 1000 - meta.ts <= 15
  if (!fresh) loadList()
}

function phaseLabel(phase) {
  return ({ Running: '运行中', Pending: '等待中', Failed: '失败', Unknown: '未知', Succeeded: '已完成' })[phase] || phase || '—'
}
function phaseTagClass(phase) {
  return ({ Running: 'tag-ok', Pending: 'tag-warn', Failed: 'tag-err', Unknown: 'tag-err', Succeeded: 'tag-slate' })[phase] || 'tag-slate'
}
function readyWarnClass(v) {
  const m = String(v ?? '').match(/^(\d+)\/(\d+)$/)
  if (!m) return ''
  return Number(m[1]) < Number(m[2]) ? 'warn-text' : ''
}
function probesText(c) {
  const p = c?.probes
  if (!p) return '—'
  if (Array.isArray(p)) return p.map((x) => (typeof x === 'string' ? x : `${x.type || x.kind || ''}${x.status ? ':' + x.status : ''}`)).filter(Boolean).join(', ') || '—'
  if (typeof p === 'object') return Object.entries(p).map(([k, v]) => `${k}:${v}`).join(', ') || '—'
  return String(p)
}
function kvText(o) {
  return o && typeof o === 'object' ? Object.entries(o).map(([k, v]) => `${k} ${v}`).join(' · ') : '—'
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
  const dt = new Date(v)
  return Number.isNaN(dt.getTime()) ? String(v) : dt.toLocaleTimeString('zh-CN', { hour12: false })
}
function evObj(ev) {
  const io = ev?.involved_object || {}
  return [io.kind, io.name].filter(Boolean).join('/') || ev?.object || '—'
}
function evMsg(ev) { return ev?.message || ev?.msg || ev?.note || ev?.reason || '—' }
function evType(ev) { return ev?.type || '—' }

function restoreUi() {
  const saved = getTabState(workbench.state.activeKey) || {}
  if (!detailMode.value) {
    const f = saved.filters || {}
    if (typeof f.namespace === 'string') filters.namespace = f.namespace
    if (typeof f.node === 'string') filters.node = f.node
    if (typeof f.phase === 'string') filters.phase = f.phase
    if (typeof f.keyword === 'string') filters.keyword = f.keyword
  }
  if (saved.scrollY !== undefined) {
    nextTick(() => { const el = document.querySelector('.content'); if (el) el.scrollTop = saved.scrollY })
  }
}
function saveUi() {
  const el = document.querySelector('.content')
  const patch = { scrollY: el ? el.scrollTop : 0 }
  if (!detailMode.value) patch.filters = { namespace: filters.namespace, node: filters.node, phase: filters.phase, keyword: filters.keyword }
  setTabState(workbench.state.activeKey, patch)
}

watch(filters, () => {
  if (!tabAlive) return
  saveUi()
  if (!cid() || detailMode.value) return
  clearTimeout(kwTimer)
  kwTimer = setTimeout(() => { if (isActive.value && tabAlive) loadList() }, 300)
}, { deep: true })

watch(selectedClusterId, (id) => {
  if (!tabAlive) return
  podsList.value = []
  podDetail.value = null
  detailTs.value = 0
  Object.assign(meta, { ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
  fatal.value = ''
  podFatal.value = ''
  listLoaded = false
  logInited = false
  logs.value = ''
  logMeta.value = null
  if (id) load()
})

watch(detailMode, () => {
  if (!tabAlive) return
  if (isActive.value) { stopPolling(); startPolling() }
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
  listCtl?.abort()
  detailCtl?.abort()
  logCtl?.abort()
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
.warn-text { color: var(--warn); font-weight: 600; }
.empty-inline { text-align: center; color: var(--muted); padding: 22px 10px; font-size: 13px; }
.nowrap { white-space: nowrap; }
.tiny { font-size: 12px; }
.ev-msg { word-break: break-all; }
.pod-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.pod-name { margin: 0; font-size: 15px; word-break: break-all; }
.pod-tags { display: flex; gap: 6px; flex: none; }
.kvgrid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0 18px; }
.kv { display: flex; justify-content: space-between; gap: 12px; font-size: 13px; padding: 6px 0; border-bottom: 1px dashed var(--border); }
.kv span { color: var(--muted); flex: none; }
.kv b { font-weight: 600; text-align: right; word-break: break-all; }
.card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.card-head h3 { margin: 0; font-size: 14px; }
.log-bar { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.log-bar .select { width: auto; min-width: 150px; }
.log-err { color: var(--warn); margin-bottom: 8px; }
.log-view { margin: 0 0 8px; padding: 12px; background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; font-family: Consolas, 'Courier New', monospace; font-size: 12px; line-height: 1.55; overflow: auto; max-height: 480px; white-space: pre-wrap; word-break: break-all; }
.tag-ok { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #ecfdf5; color: #059669; }
.tag-warn { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; }
.tag-err { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fef2f2; color: #dc2626; }
@media (max-width: 900px) { .kvgrid { grid-template-columns: 1fr; } }
</style>
