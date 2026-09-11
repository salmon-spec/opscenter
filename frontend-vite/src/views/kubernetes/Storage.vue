<template>
  <div class="view k8s">
    <div class="view-head">
      <div>
        <h1 class="view-title">存储</h1>
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
        <div class="big">⚠ 存储数据加载失败</div>
        <div class="muted">{{ fatal }}</div>
        <div><button class="btn btn-sm" :disabled="loading" @click="load">重试</button></div>
      </div>
      <div v-else-if="loading && !hasData" class="loading"><span class="spinner"></span>正在读取存储数据…</div>
      <template v-else>
        <div v-if="partialText" class="partial">
          <span>⚠ 部分数据源异常：</span><span class="partial-item">{{ partialText }}</span>
        </div>
        <div v-if="warnText" class="partial warn-strip">
          <span>⚠ 存储风险：</span><span class="partial-item">{{ warnText }}</span>
        </div>

        <section class="card">
          <div class="card-head"><h3>PersistentVolumeClaim（{{ pvcs.length }}）</h3></div>
          <div class="table-scroll">
            <table class="table">
              <thead><tr><th>名称</th><th>命名空间</th><th>状态</th><th>容量</th><th>StorageClass</th><th>访问模式</th><th>绑定卷</th><th>Age</th></tr></thead>
              <tbody>
                <tr v-for="(p, i) in pvcs" :key="`${p.namespace || ''}/${p.name || i}`">
                  <td><b>{{ p.name || '—' }}</b></td>
                  <td>{{ p.namespace || '—' }}</td>
                  <td><span class="tag" :class="pvcPhaseClass(p.phase)">{{ pvcPhaseLabel(p.phase) }}</span></td>
                  <td>{{ p.capacity || '—' }}</td>
                  <td>{{ p.storage_class || p.storageClass || '—' }}</td>
                  <td class="tiny">{{ accessModes(p) }}</td>
                  <td class="mono tiny">{{ p.volume_name || p.volume || '—' }}</td>
                  <td class="nowrap">{{ fmtAge(p.age) }}</td>
                </tr>
                <tr v-if="!pvcs.length"><td colspan="8" class="empty-inline">暂无 PVC</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="card">
          <div class="card-head"><h3>PersistentVolume（{{ pvs.length }}）</h3></div>
          <div class="table-scroll">
            <table class="table">
              <thead><tr><th>名称</th><th>状态</th><th>容量</th><th>回收策略</th><th>StorageClass</th><th>绑定 Claim</th><th>Age</th></tr></thead>
              <tbody>
                <tr v-for="(pv, i) in pvs" :key="pv.name || i">
                  <td><b>{{ pv.name || '—' }}</b></td>
                  <td><span class="tag" :class="pvPhaseClass(pv.phase)">{{ pvPhaseLabel(pv.phase) }}</span></td>
                  <td>{{ pv.capacity || '—' }}</td>
                  <td><span class="tag" :class="reclaimClass(pv.reclaim_policy)">{{ reclaimLabel(pv.reclaim_policy) }}</span></td>
                  <td>{{ pv.storage_class || pv.storageClass || '—' }}</td>
                  <td class="tiny">{{ claimText(pv) }}</td>
                  <td class="nowrap">{{ fmtAge(pv.age) }}</td>
                </tr>
                <tr v-if="!pvs.length"><td colspan="7" class="empty-inline">暂无 PV</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </template>
    </template>
  </div>
</template>

<script>
export default { name: 'KubernetesStorage' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useTabActive, setTabState, getTabState } from '../../workbench/tabs'
import workbench from '../../workbench/tabs'
import { useClusterContext } from '../../workbench/clusters'
import { k8sApi, unwrap, apiErrorText } from '../../api/kubernetes'

const { selectedClusterId, currentCluster, refreshClusters } = useClusterContext()
const { isActive } = useTabActive('KubernetesStorage')

const POLL_MS = 15000
const pvcs = ref([])
const pvs = ref([])
const meta = reactive({ ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
const fatal = ref('')
const loading = ref(false)
const clusterErr = ref('')
const nowTick = ref(Date.now())

let controller = null
let pollTimer = null
let inFlight = false
let tabAlive = false
let loadedOnce = false

const cid = () => selectedClusterId.value
const clusterMissing = computed(() => !cid())
const hasData = computed(() => pvcs.value.length > 0 || pvs.value.length > 0)
const partialText = computed(() => meta.partialErrors.join('；'))
const pvcPending = computed(() => pvcs.value.filter((p) => String(p.phase || '').toLowerCase() === 'pending').length)
const pvcLost = computed(() => pvcs.value.filter((p) => String(p.phase || '').toLowerCase() === 'lost').length)
const pvAbnormal = computed(() => pvs.value.filter((p) => ['failed', 'released'].includes(String(p.phase || '').toLowerCase())).length)
const warnText = computed(() => {
  const parts = []
  if (pvcPending.value > 0) parts.push(`${pvcPending.value} 个 PVC 处于 Pending`)
  if (pvcLost.value > 0) parts.push(`${pvcLost.value} 个 PVC 已 Lost`)
  if (pvAbnormal.value > 0) parts.push(`${pvAbnormal.value} 个 PV 状态异常（Failed/Released）`)
  return parts.join('；')
})
const stampText = computed(() => {
  if (!meta.ts) return ''
  const t = new Date(meta.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  return meta.cached ? `${t} · 缓存 ${meta.cacheAge.toFixed(0)}s` : t
})
const stale = computed(() => meta.ts > 0 && nowTick.value / 1000 - meta.ts > 60)
const subText = computed(() => {
  const c = currentCluster.value
  if (!c) return 'PersistentVolumeClaim 与 PersistentVolume'
  return `${c.name}${c.version ? ' · ' + c.version : ''} · PVC / PV`
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
    const res = await k8sApi.storage(cidNow, undefined, { signal, timeoutMs: 20000 })
    if (cid() !== cidNow) return
    const u = unwrap(res)
    const d = u.data || {}
    const pc = Array.isArray(d.pvcs) ? d.pvcs : (Array.isArray(d.pvc) ? d.pvc : [])
    const pv = Array.isArray(d.pvs) ? d.pvs : (Array.isArray(d.pv) ? d.pv : [])
    pvcs.value = pc
    pvs.value = pv
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

function pvcPhaseLabel(phase) {
  const p = String(phase || '').toLowerCase()
  if (p === 'bound') return '已绑定'
  if (p === 'pending') return '待绑定'
  if (p === 'lost') return '已丢失'
  return phase || '—'
}
function pvcPhaseClass(phase) {
  const p = String(phase || '').toLowerCase()
  if (p === 'bound') return 'tag-ok'
  if (p === 'pending') return 'tag-warn'
  if (p === 'lost') return 'tag-err'
  return 'tag-slate'
}
function pvPhaseLabel(phase) {
  const p = String(phase || '').toLowerCase()
  if (p === 'bound') return '已绑定'
  if (p === 'available') return '可用'
  if (p === 'released') return '已释放'
  if (p === 'failed') return '失败'
  return phase || '—'
}
function pvPhaseClass(phase) {
  const p = String(phase || '').toLowerCase()
  if (p === 'bound') return 'tag-ok'
  if (p === 'available') return 'tag-ok'
  if (p === 'released') return 'tag-warn'
  if (p === 'failed') return 'tag-err'
  return 'tag-slate'
}
function reclaimLabel(policy) {
  const p = String(policy || '').toLowerCase()
  if (p === 'retain') return '保留（Retain）'
  if (p === 'delete') return '删除（Delete）'
  if (p === 'recycle') return '回收（Recycle）'
  return policy || '—'
}
function reclaimClass(policy) {
  const p = String(policy || '').toLowerCase()
  if (p === 'retain') return 'tag-slate'
  if (p === 'delete') return 'tag-warn'
  return 'tag-slate'
}
function accessModes(p) {
  const modes = Array.isArray(p?.access_modes) ? p.access_modes : (Array.isArray(p?.accessModes) ? p.accessModes : [])
  return modes.join(', ') || '—'
}
function claimText(pv) {
  const ns = pv?.claim_namespace || pv?.claimNamespace
  const name = pv?.claim_name || pv?.claim
  if (!name) return '—'
  return ns ? `${ns}/${name}` : name
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
  pvcs.value = []
  pvs.value = []
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
.warn-strip { border-color: #fde68a; }
.k8s-guide { display: flex; flex-direction: column; align-items: center; gap: 10px; padding: 48px 16px; text-align: center; }
.guide-icon { font-size: 40px; }
.k8s-error { display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.k8s-error .big { font-weight: 700; color: var(--err); }
.card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.card-head h3 { margin: 0; font-size: 14px; }
.table-scroll { overflow: auto; }
.empty-inline { text-align: center; color: var(--muted); padding: 22px 10px; font-size: 13px; }
.nowrap { white-space: nowrap; }
.tiny { font-size: 12px; }
.tag-ok { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #ecfdf5; color: #059669; }
.tag-warn { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; }
.tag-err { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fef2f2; color: #dc2626; }
</style>
