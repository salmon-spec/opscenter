<template>
  <div class="view k8s">
    <div class="view-head">
      <div>
        <h1 class="view-title">集群概览</h1>
        <p class="view-sub">{{ subText }}</p>
      </div>
      <div class="head-actions">
        <span v-if="stampText" class="muted stamp">数据 {{ stampText }}<span v-if="stale" class="stale">数据陈旧</span></span>
        <button class="btn btn-sm" :disabled="loading" @click="reload">↻ 刷新</button>
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
        <div class="big">⚠ 集群数据加载失败</div>
        <div class="muted">{{ fatal }}</div>
        <div><button class="btn btn-sm" :disabled="loading" @click="reload">重试</button></div>
      </div>
      <div v-else-if="loading && !hasData" class="loading"><span class="spinner"></span>正在读取集群数据…</div>
      <template v-else>
        <div v-if="partialText" class="partial">
          <span>⚠ 部分数据源异常：</span><span class="partial-item">{{ partialText }}</span>
        </div>

        <div class="mgrid">
          <div class="mcard" :class="{ err: nodesTotal > 0 && nodesReady < nodesTotal }">
            <span>节点 Ready</span>
            <b>{{ nodesReady }}<i v-if="nodesTotal"> / {{ nodesTotal }}</i></b>
            <em :class="{ 'ok-text': nodesTotal > 0 && nodesReady >= nodesTotal }">{{ nodesTotal > 0 && nodesReady < nodesTotal ? `${nodesTotal - nodesReady} 个节点未就绪` : '全部节点就绪' }}</em>
          </div>
          <div class="mcard" :class="{ warn: wl.desired > 0 && wl.ready < wl.desired }">
            <span>工作负载就绪</span>
            <b>{{ wl.ready }}<i v-if="wl.desired"> / {{ wl.desired }}</i></b>
            <em :class="{ 'ok-text': wl.desired > 0 && wl.ready >= wl.desired }">{{ wl.desired > 0 && wl.ready < wl.desired ? `${wl.desired - wl.ready} 个副本未就绪` : '副本全部就绪' }}</em>
          </div>
          <div class="mcard" :class="{ warn: Number(podsStat.failed) > 0 }">
            <span>Pod 运行中</span>
            <b>{{ podsStat.running ?? 0 }}</b>
            <em>Pending {{ podsStat.pending ?? 0 }} · Failed <span class="err-text">{{ podsStat.failed ?? 0 }}</span> · Unknown {{ podsStat.unknown ?? 0 }}</em>
          </div>
          <div class="mcard" :class="{ err: warnCount > 0 }">
            <span>Warning 事件</span>
            <b :class="{ 'err-text': warnCount > 0 }">{{ warnCount }}</b>
            <em>最近 {{ recentEvents.length }} 条见下方</em>
          </div>
          <div class="mcard" :class="{ warn: Number(netpol.total) > 0 && Number(netpol.covered) < Number(netpol.total) }">
            <span>NetPol 覆盖</span>
            <b>{{ netpol.covered ?? 0 }}<i> / {{ netpol.total ?? 0 }}</i></b>
            <em>NetworkPolicy 覆盖命名空间</em>
          </div>
          <div class="mcard" :class="{ warn: Number(pvcStat.pending) > 0 || Number(pvcStat.lost) > 0 }">
            <span>PVC 已绑定</span>
            <b>{{ pvcStat.bound ?? 0 }}</b>
            <em>Pending {{ pvcStat.pending ?? 0 }} · Lost {{ pvcStat.lost ?? 0 }}</em>
          </div>
        </div>

        <section class="card">
          <div class="card-head"><h3>节点（{{ nodes.length }}）</h3><span class="muted tiny">点击行查看节点详情</span></div>
          <div class="table-scroll">
            <table class="table">
              <thead><tr><th>节点</th><th>角色</th><th>状态</th><th>资源压力</th><th>用量（CPU / 内存）</th><th>Allocatable</th><th>Capacity</th></tr></thead>
              <tbody>
                <tr v-for="n in nodes" :key="n.name" class="row-click" @click="openNodeDrawer(n)">
                  <td><b>{{ n.name }}</b></td>
                  <td>{{ rolesText(n) }}</td>
                  <td><span class="tag" :class="n.ready ? 'tag-ok' : 'tag-err'">{{ n.ready ? 'Ready' : 'NotReady' }}</span></td>
                  <td><span :class="pressClass(n)">{{ pressText(n) }}</span></td>
                  <td><span v-if="n.usage" class="mono">{{ usageText(n) }}</span><span v-else class="muted">指标源不可用</span></td>
                  <td class="mono tiny">{{ kvText(n.allocatable) }}</td>
                  <td class="mono tiny">{{ kvText(n.capacity) }}</td>
                </tr>
                <tr v-if="!nodes.length"><td colspan="7" class="empty-inline">暂无节点数据</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <div class="grid2">
          <section class="card">
            <div class="card-head"><h3>重启 Top 5</h3></div>
            <table v-if="restartTop.length" class="table">
              <thead><tr><th>Pod</th><th>命名空间</th><th>重启次数</th></tr></thead>
              <tbody>
                <tr v-for="r in restartTop" :key="`${r.namespace || ''}/${r.name}`">
                  <td class="mono">{{ r.name }}</td>
                  <td>{{ r.namespace || '—' }}</td>
                  <td><span :class="Number(r.restarts) > 0 ? 'err-text' : ''">{{ r.restarts ?? 0 }}</span></td>
                </tr>
              </tbody>
            </table>
            <div v-else class="empty-inline">暂无重启记录</div>
          </section>
          <section class="card">
            <div class="card-head"><h3>Warning 事件（{{ recentEvents.length }}）</h3></div>
            <table v-if="recentEvents.length" class="table">
              <thead><tr><th>时间</th><th>对象</th><th>消息</th></tr></thead>
              <tbody>
                <tr v-for="(ev, i) in recentEvents" :key="i">
                  <td class="mono tiny nowrap">{{ evTime(ev) }}</td>
                  <td class="tiny">{{ evObj(ev) }}</td>
                  <td class="tiny ev-msg">{{ evMsg(ev) }}</td>
                </tr>
              </tbody>
            </table>
            <div v-else class="empty-inline">暂无 Warning 事件</div>
          </section>
        </div>
      </template>
    </template>

    <div v-if="drawer.open" class="drawer-mask" @click="closeDrawer"></div>
    <aside v-if="drawer.open" class="drawer">
      <div class="drawer-head">
        <div><b>{{ drawer.node?.name }}</b><div class="muted tiny">节点详情</div></div>
        <button class="btn btn-sm btn-ghost" @click="closeDrawer">✕ 关闭</button>
      </div>
      <div class="drawer-body">
        <div class="kv"><span>角色</span><b>{{ rolesText(drawer.node) }}</b></div>
        <div class="kv"><span>状态</span><b><span class="tag" :class="drawer.node?.ready ? 'tag-ok' : 'tag-err'">{{ drawer.node?.ready ? 'Ready' : 'NotReady' }}</span></b></div>
        <div class="kv"><span>资源压力</span><b :class="pressClass(drawer.node)">{{ pressText(drawer.node) }}</b></div>
        <div class="kv"><span>用量（CPU / 内存）</span><b><span v-if="drawer.node?.usage" class="mono">{{ usageText(drawer.node) }}</span><span v-else class="muted">指标源不可用</span></b></div>
        <div class="kv"><span>Allocatable</span><b class="mono tiny">{{ kvText(drawer.node?.allocatable) }}</b></div>
        <div class="kv"><span>Capacity</span><b class="mono tiny">{{ kvText(drawer.node?.capacity) }}</b></div>
        <template v-if="nodeConditions.length">
          <h4 class="sub-title">Conditions</h4>
          <table class="table">
            <thead><tr><th>类型</th><th>状态</th><th>最近变更</th></tr></thead>
            <tbody>
              <tr v-for="(c, i) in nodeConditions" :key="i">
                <td>{{ c.type || '—' }}</td>
                <td><span class="tag" :class="String(c.status).toLowerCase() === 'true' ? 'tag-ok' : 'tag-slate'">{{ c.status }}</span></td>
                <td class="mono tiny">{{ c.last_transition_time || c.lastTransitionTime || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </template>
        <div class="drawer-sub">
          <h4 class="sub-title">节点 Pods（{{ drawer.pods.length }}）</h4>
          <button class="btn btn-sm" :disabled="drawer.loading" @click="loadNodePods">刷新</button>
        </div>
        <div v-if="drawer.loading && !drawer.pods.length" class="loading" style="padding:18px"><span class="spinner"></span>加载中…</div>
        <div v-else-if="drawer.err" class="muted tiny drawer-err">{{ drawer.err }} <button class="btn btn-sm" @click="loadNodePods">重试</button></div>
        <div v-else-if="drawer.pods.length" class="table-scroll drawer-table">
          <table class="table">
            <thead><tr><th>Pod</th><th>命名空间</th><th>Phase</th><th>Ready</th><th>重启</th></tr></thead>
            <tbody>
              <tr v-for="p in drawer.pods" :key="`${p.namespace || ''}/${p.name}`">
                <td class="mono">{{ p.name }}</td>
                <td>{{ p.namespace || '—' }}</td>
                <td><span class="tag" :class="phaseTagClass(p.phase)">{{ phaseLabel(p.phase) }}</span></td>
                <td class="mono">{{ p.ready || '—' }}</td>
                <td><span :class="Number(p.restarts) > 0 ? 'err-text' : ''">{{ p.restarts ?? 0 }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty-inline">该节点暂无 Pod</div>
      </div>
    </aside>
  </div>
</template>

<script>
export default { name: 'KubernetesOverview' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useTabActive, setTabState, getTabState } from '../../workbench/tabs'
import workbench from '../../workbench/tabs'
import { useClusterContext } from '../../workbench/clusters'
import { k8sApi, unwrap, mergeMeta, apiErrorText } from '../../api/kubernetes'

const { selectedClusterId, currentCluster, refreshClusters } = useClusterContext()
const { isActive } = useTabActive('KubernetesOverview')

const POLL_MS = 10000
const summary = ref(null)
const nodes = ref([])
const meta = reactive({ ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
const fatal = ref('')
const loading = ref(false)
const clusterErr = ref('')
const nowTick = ref(Date.now())
const drawer = reactive({ open: false, node: null, pods: [], loading: false, err: '' })

let controller = null
let pollTimer = null
let inFlight = false
let tabAlive = false
let loadedOnce = false

const cid = () => selectedClusterId.value
const clusterMissing = computed(() => !cid())
const hasData = computed(() => !!summary.value || nodes.value.length > 0)
const partialText = computed(() => meta.partialErrors.join('；'))

const s = computed(() => summary.value || {})
const nodesTotal = computed(() => Number(s.value.nodes?.total ?? 0))
const nodesReady = computed(() => Number(s.value.nodes?.ready ?? 0))
const wl = computed(() => {
  const w = s.value.workloads || {}
  const keys = ['deployments', 'statefulsets', 'daemonsets']
  return {
    desired: keys.reduce((a, k) => a + Number(w[k]?.desired ?? 0), 0),
    ready: keys.reduce((a, k) => a + Number(w[k]?.ready ?? 0), 0),
  }
})
const podsStat = computed(() => s.value.pods || {})
const warnCount = computed(() => Number(s.value.events?.warning_count ?? 0))
const recentEvents = computed(() => (Array.isArray(s.value.events?.recent) ? s.value.events.recent : []).slice(0, 5))
const netpol = computed(() => s.value.netpol || {})
const pvcStat = computed(() => s.value.storage?.pvc || {})
const restartTop = computed(() => (Array.isArray(s.value.pods?.restart_top) ? s.value.pods.restart_top : []).slice(0, 5))
const nodeConditions = computed(() => {
  const c = drawer.node?.conditions
  return Array.isArray(c) ? c : []
})
const stampText = computed(() => {
  if (!meta.ts) return ''
  const t = new Date(meta.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  return meta.cached ? `${t} · 缓存 ${meta.cacheAge.toFixed(0)}s` : t
})
const stale = computed(() => meta.ts > 0 && nowTick.value / 1000 - meta.ts > 60)
const subText = computed(() => {
  const c = currentCluster.value
  if (!c) return 'K3s 集群只读监控'
  return `${c.name}${c.version ? ' · ' + c.version : ''} · 只读监控`
})

async function initClusters(force = false) {
  clusterErr.value = ''
  try { await refreshClusters(force) } catch (e) { clusterErr.value = e?.message || '加载失败' }
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
    const [sr, nr] = await Promise.allSettled([
      k8sApi.summary(cidNow, undefined, { signal, timeoutMs: 20000 }),
      k8sApi.nodes(cidNow, undefined, { signal, timeoutMs: 20000 }),
    ])
    if (cid() !== cidNow) return
    const errs = []
    const metas = []
    if (sr.status === 'fulfilled') { const u = unwrap(sr.value); summary.value = u.data; metas.push(u.meta) }
    else errs.push(`概览读取失败：${apiErrorText(sr.reason)}`)
    if (nr.status === 'fulfilled') { const u = unwrap(nr.value); nodes.value = Array.isArray(u.data) ? u.data : []; metas.push(u.meta) }
    else errs.push(`节点列表读取失败：${apiErrorText(nr.reason)}`)
    const m = mergeMeta(metas)
    meta.ts = m.ts
    meta.cached = m.cached
    meta.cacheAge = m.cacheAge
    meta.partialErrors = [...m.partialErrors, ...errs]
    fatal.value = errs.length === 2 ? errs.join('；') : ''
    loadedOnce = true
    nowTick.value = Date.now()
  } finally {
    inFlight = false
    if (cid() === cidNow) loading.value = false
  }
}

function reload() { load() }

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

function openNodeDrawer(n) {
  drawer.open = true
  drawer.node = n
  drawer.pods = []
  drawer.err = ''
  loadNodePods()
}
function closeDrawer() { drawer.open = false }

async function loadNodePods() {
  const n = drawer.node
  const cidNow = cid()
  if (!n || !cidNow || drawer.loading) return
  drawer.loading = true
  drawer.err = ''
  try {
    const u = unwrap(await k8sApi.pods(cidNow, { node: n.name, limit: 200 }, { timeoutMs: 20000 }))
    if (cid() !== cidNow || !drawer.open) return
    drawer.pods = Array.isArray(u.data) ? u.data : []
    if (u.meta.partialErrors.length) drawer.err = u.meta.partialErrors.join('；')
  } catch (e) {
    if (e.name === 'AbortError') return
    drawer.err = apiErrorText(e)
  } finally {
    drawer.loading = false
  }
}

function rolesText(n) { return Array.isArray(n?.roles) && n.roles.length ? n.roles.join(', ') : '—' }
function pressText(n) {
  const p = n?.pressure || {}
  const seg = []
  if (p.cpu != null) seg.push(`CPU ${p.cpu}%`)
  if (p.memory != null) seg.push(`内存 ${p.memory}%`)
  if (p.pid != null) seg.push(`PID ${p.pid}%`)
  if (p.disk != null) seg.push(`磁盘 ${p.disk}%`)
  return seg.join(' · ') || '—'
}
function pressClass(n) {
  const p = n?.pressure || {}
  const vals = [p.cpu, p.memory, p.pid, p.disk].filter((v) => typeof v === 'number')
  if (!vals.length) return ''
  const mx = Math.max(...vals)
  return mx >= 85 ? 'press-err' : mx >= 65 ? 'press-warn' : 'press-ok'
}
function usageText(n) { return n?.usage ? `${n.usage.cpu ?? '—'} / ${n.usage.memory ?? '—'}` : '' }
function kvText(o) {
  return o && typeof o === 'object' ? Object.entries(o).map(([k, v]) => `${k} ${v}`).join(' · ') : '—'
}
function phaseLabel(phase) {
  return ({ Running: '运行中', Pending: '等待中', Failed: '失败', Unknown: '未知', Succeeded: '已完成' })[phase] || phase || '—'
}
function phaseTagClass(phase) {
  return ({ Running: 'tag-ok', Pending: 'tag-warn', Failed: 'tag-err', Unknown: 'tag-err', Succeeded: 'tag-slate' })[phase] || 'tag-slate'
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

function restoreUi() {
  const saved = getTabState(workbench.state.activeKey) || {}
  if (saved.scrollY !== undefined) {
    nextTick(() => { const el = document.querySelector('.content'); if (el) el.scrollTop = saved.scrollY })
  }
}
function saveUi() {
  const el = document.querySelector('.content')
  setTabState(workbench.state.activeKey, { scrollY: el ? el.scrollTop : 0 })
}

watch(selectedClusterId, (id) => {
  if (!tabAlive) return
  summary.value = null
  nodes.value = []
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
.mgrid { display: grid; grid-template-columns: repeat(6, minmax(150px, 1fr)); gap: 12px; }
.mcard { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); padding: 12px 14px; display: flex; flex-direction: column; gap: 4px; }
.mcard > span { font-size: 12px; color: var(--muted); }
.mcard b { font-size: 22px; font-weight: 700; }
.mcard b i { font-size: 12px; color: var(--muted); font-style: normal; font-weight: 500; }
.mcard em { font-style: normal; font-size: 11px; color: var(--muted); }
.mcard.err { border-color: #fecaca; box-shadow: inset 0 2px 0 var(--err); }
.mcard.warn { border-color: #fde68a; box-shadow: inset 0 2px 0 var(--warn); }
.err-text { color: var(--err); font-weight: 600; }
.ok-text { color: var(--ok); }
.press-ok { color: var(--ok); }
.press-warn { color: var(--warn); }
.press-err { color: var(--err); }
.card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.card-head h3 { margin: 0; font-size: 14px; }
.table-scroll { overflow: auto; }
.row-click { cursor: pointer; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.empty-inline { text-align: center; color: var(--muted); padding: 22px 10px; font-size: 13px; }
.ev-msg { word-break: break-all; }
.nowrap { white-space: nowrap; }
.tiny { font-size: 12px; }
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
.drawer-table { max-height: 420px; }
.drawer-err { color: var(--warn); }
.tag-ok { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #ecfdf5; color: #059669; }
.tag-warn { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; }
.tag-err { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fef2f2; color: #dc2626; }
@media (max-width: 1280px) { .mgrid { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 900px) { .mgrid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .grid2 { grid-template-columns: 1fr; } }
</style>
