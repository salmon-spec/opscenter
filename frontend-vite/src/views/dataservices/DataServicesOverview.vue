<template>
  <div ref="rootEl" class="view ds">
    <div class="view-head">
      <div>
        <h1 class="view-title">数据服务</h1>
        <p class="view-sub">K3s 中间件纳管 · 只读概览与连通性探测</p>
      </div>
      <div class="head-actions">
        <span v-if="stampText" class="muted stamp">数据 {{ stampText }}<span v-if="stale" class="stale">数据陈旧</span></span>
        <button class="btn btn-sm" :disabled="loading" @click="reload">{{ loading ? '加载中…' : '↻ 刷新' }}</button>
        <button class="btn btn-sm btn-primary" :disabled="probingAll || !services.length" @click="probeAll">
          {{ probingAll ? '探测中…' : '全部探测' }}
        </button>
      </div>
    </div>

    <div v-if="fatal && !hasData" class="card ds-fatal">
      <div class="fatal-title">⚠ 数据服务加载失败</div>
      <div class="muted">{{ fatal }}</div>
      <div><button class="btn btn-sm" :disabled="loading" @click="reload">重试</button></div>
    </div>
    <div v-else-if="loading && !hasData" class="loading"><span class="spinner"></span>正在读取数据服务…</div>

    <template v-else>
      <div v-if="partialText" class="partial">
        <span>⚠ 部分数据源异常：</span><span class="partial-item">{{ partialText }}</span>
      </div>
      <div v-if="fatal && hasData" class="partial">
        <span>⚠ 刷新失败：</span><span class="partial-item">{{ fatal }}（当前显示上次数据）</span>
      </div>

      <div v-if="services.length" class="ds-grid">
        <div
          v-for="svc in services"
          :key="svc.kind"
          class="ds-card"
          :class="cardClass(svc)"
          role="button"
          tabindex="0"
          @click="openKind(svc.kind)"
          @keydown.enter="openKind(svc.kind)"
        >
          <div class="ds-head">
            <span class="ds-icon">{{ iconOf(svc.kind) }}</span>
            <div class="ds-title">
              <b>{{ nameOf(svc) }}</b>
              <span class="muted ds-desc">{{ descOf(svc.kind) }}</span>
            </div>
            <DsStatusTag :configured="!!svc.configured" :available="!!svc.available" />
          </div>

          <div class="ds-line">
            <span v-if="probing[svc.kind]" class="muted ds-probe"><span class="spinner spinner-sm"></span>探测中…</span>
            <span v-else class="ds-latency mono">{{ latencyText(svc) }}</span>
            <span v-if="svc.version" class="muted ds-ver">v{{ versionText(svc.version) }}</span>
          </div>

          <div class="ds-endpoint mono" :title="svc.endpoint || ''">{{ svc.endpoint || '—' }}</div>

          <div v-if="chips(svc).length" class="ds-chips">
            <span v-for="c in chips(svc)" :key="c.k" class="ds-chip"><i>{{ c.k }}</i><b :title="c.v">{{ c.v }}</b></span>
          </div>
          <div v-else-if="!svc.configured" class="muted ds-hint">未配置 MIDDLEWARE 环境变量</div>

          <div v-if="svc.error" class="ds-err" :title="svc.error">{{ svc.error }}</div>
        </div>
      </div>
      <div v-else class="card empty">暂无数据服务数据</div>
    </template>
  </div>
</template>

<script>
export default { name: 'DataServicesOverview' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import workbench, { useTabActive, setTabState, getTabState } from '../../workbench/tabs'
import { toast } from '../../api'
import { dsApi, DS_KINDS, unwrap, apiErrorText } from '../../api/dataservices'
import DsStatusTag from './DsStatusTag.vue'

const router = useRouter()
const { isActive } = useTabActive('DataServicesOverview')

const POLL_MS = 15000
const FRESH_S = 15

const kindMeta = new Map(DS_KINDS.map((k) => [k.kind, k]))

const services = ref([])
const meta = reactive({ ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
const fatal = ref('')
const loading = ref(false)
const probingAll = ref(false)
const probing = reactive({})
const nowTick = ref(Date.now())
const rootEl = ref(null)

let controller = null
let probeController = null
let pollTimer = null
let inFlight = false
let tabAlive = false
let loadedOnce = false

const hasData = computed(() => services.value.length > 0)
const partialText = computed(() => meta.partialErrors.join('；'))
const stampText = computed(() => {
  if (!meta.ts) return ''
  const t = new Date(meta.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  return meta.cached ? `${t} · 缓存 ${meta.cacheAge.toFixed(0)}s` : t
})
const stale = computed(() => meta.ts > 0 && nowTick.value / 1000 - meta.ts > 60)

function iconOf(kind) { return kindMeta.get(kind)?.icon || '📦' }
function nameOf(svc) { return svc.name || kindMeta.get(svc.kind)?.name || svc.kind }
function descOf(kind) { return kindMeta.get(kind)?.desc || '' }
function versionText(v) { return String(v || '').replace(/^v/i, '') }
function latencyText(svc) {
  const n = Number(svc?.latency_ms)
  return svc?.latency_ms != null && Number.isFinite(n) ? `${Math.round(n)} ms` : '—'
}
function cardClass(svc) {
  return { 'is-off': !svc.configured, 'is-err': !!svc.configured && !svc.available }
}
function chips(svc) {
  const s = svc?.summary
  if (!s || typeof s !== 'object' || Array.isArray(s)) return []
  return Object.entries(s)
    .filter(([, v]) => v !== null && v !== undefined && v !== '' && typeof v !== 'object')
    .slice(0, 3)
    .map(([k, v]) => ({ k, v: String(v) }))
}

function applyOverview(list) {
  if (!Array.isArray(list)) return
  const byKind = new Map(
    list
      .filter((s) => s && typeof s === 'object' && s.kind)
      .map((s) => [s.kind, s]),
  )
  services.value = DS_KINDS.map((k) => {
    const s = byKind.get(k.kind) || {}
    return {
      kind: k.kind,
      name: s.name || k.name,
      configured: !!s.configured,
      available: !!s.available,
      latency_ms: s.latency_ms ?? null,
      endpoint: s.endpoint || '',
      version: s.version || '',
      summary: s.summary && typeof s.summary === 'object' && !Array.isArray(s.summary) ? s.summary : {},
      error: s.error || '',
    }
  })
}

function mergeService(updated, kindFallback) {
  if (!updated || typeof updated !== 'object' || Array.isArray(updated)) return
  const kind = updated.kind || kindFallback
  const idx = services.value.findIndex((s) => s.kind === kind)
  if (idx < 0) return
  services.value[idx] = { ...services.value[idx], ...updated, kind }
}

async function load() {
  if (inFlight) return
  inFlight = true
  loading.value = true
  controller?.abort()
  controller = new AbortController()
  const { signal } = controller
  try {
    const { data, meta: m } = unwrap(await dsApi.overview({ signal, timeoutMs: 10000 }))
    const list = data && typeof data === 'object' && !Array.isArray(data) ? data.services : data
    applyOverview(list)
    meta.ts = m.ts
    meta.cached = m.cached
    meta.cacheAge = m.cacheAge
    meta.partialErrors = m.partialErrors
    fatal.value = ''
    loadedOnce = true
    nowTick.value = Date.now()
  } catch (e) {
    if (e.name !== 'AbortError') fatal.value = apiErrorText(e)
  } finally {
    inFlight = false
    loading.value = false
  }
}

function reload() { load() }

async function probeAll() {
  if (probingAll.value || !services.value.length) return
  probingAll.value = true
  probeController?.abort()
  probeController = new AbortController()
  const { signal } = probeController
  let ok = 0
  let bad = 0
  try {
    for (const item of DS_KINDS) {
      if (signal.aborted) break
      probing[item.kind] = true
      try {
        const u = unwrap(await dsApi.probe(item.kind, { signal, timeoutMs: 10000 }))
        const payload = u.data && typeof u.data === 'object' && !Array.isArray(u.data) && Array.isArray(u.data.services)
          ? u.data.services.find((s) => s?.kind === item.kind)
          : u.data
        mergeService(payload, item.kind)
        const cur = services.value.find((s) => s.kind === item.kind)
        if (cur?.available) ok += 1
        else bad += 1
      } catch (e) {
        if (e.name === 'AbortError') break
        bad += 1
        mergeService({ error: apiErrorText(e) }, item.kind)
      } finally {
        probing[item.kind] = false
      }
    }
    if (!signal.aborted) {
      nowTick.value = Date.now()
      toast(`全部探测完成：可用 ${ok} · 异常 ${bad}`, bad ? 'err' : 'ok')
    }
  } finally {
    probingAll.value = false
  }
}

function tick() {
  nowTick.value = Date.now()
  if (!isActive.value || !tabAlive || document.hidden || inFlight) return
  load()
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(tick, POLL_MS)
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function ensureFresh() {
  const fresh = loadedOnce && meta.ts > 0 && nowTick.value / 1000 - meta.ts <= FRESH_S
  if (!fresh) load()
}

function openKind(kind) {
  router.push(`/data-services/${kind}`)
}

function contentEl() {
  return rootEl.value?.closest('.content') || null
}
function restoreUi() {
  const key = workbench.state.activeKey
  const saved = key ? getTabState(key) : {}
  const el = contentEl()
  nextTick(() => {
    if (el && saved.scrollY) el.scrollTop = saved.scrollY
  })
}
function saveUi() {
  const el = contentEl()
  if (el && workbench.state.activeKey) setTabState(workbench.state.activeKey, { scrollY: el.scrollTop })
}

watch(isActive, (v) => {
  if (!tabAlive) return
  if (v) {
    ensureFresh()
    startPolling()
  } else {
    stopPolling()
  }
})

onMounted(() => {
  tabAlive = true
  restoreUi()
  ensureFresh()
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
  probeController?.abort()
})
</script>

<style scoped>
.ds { display: flex; flex-direction: column; gap: 12px; }
.head-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.stamp { font-size: 12px; }
.stale { display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
.partial { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 8px 12px; border: 1px solid #fde68a; background: #fffbeb; color: #b45309; border-radius: 8px; font-size: 12px; }
.partial-item { word-break: break-all; }
.ds-fatal { display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.fatal-title { font-weight: 700; color: var(--err); }
.ds-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 12px; }
.ds-card { display: flex; flex-direction: column; gap: 8px; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; cursor: pointer; transition: border-color .15s, box-shadow .15s; }
.ds-card:hover { border-color: var(--brand); box-shadow: 0 6px 16px rgba(15, 27, 45, .07); }
.ds-card:focus-visible { outline: 2px solid var(--brand); outline-offset: 1px; }
.ds-card.is-ok { box-shadow: inset 0 2px 0 var(--ok); }
.ds-card.is-err { border-color: #fecaca; box-shadow: inset 0 2px 0 var(--err); }
.ds-card.is-off { background: #fafbfd; }
.ds-card.is-off .ds-icon { filter: grayscale(1); opacity: .55; }
.ds-head { display: flex; align-items: flex-start; gap: 8px; }
.ds-icon { font-size: 22px; line-height: 1.2; flex: none; }
.ds-title { display: flex; flex-direction: column; gap: 1px; min-width: 0; flex: 1; }
.ds-title b { font-size: 14px; }
.ds-desc { font-size: 11px; }
.ds-head .tag { flex: none; }
.ds-line { display: flex; align-items: center; gap: 8px; min-height: 18px; }
.ds-latency { font-weight: 600; color: var(--text); }
.ds-ver { font-size: 11px; }
.ds-probe { font-size: 12px; }
.spinner-sm { width: 12px; height: 12px; margin-right: 4px; vertical-align: -2px; }
.ds-endpoint { font-size: 11px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ds-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.ds-chip { display: inline-flex; align-items: center; gap: 4px; padding: 1px 8px; border-radius: 999px; background: #f1f5f9; font-size: 11px; color: var(--muted); max-width: 100%; }
.ds-chip i { font-style: normal; }
.ds-chip b { color: var(--text); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 140px; }
.ds-hint { font-size: 11px; }
.ds-err { font-size: 11px; color: var(--err); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
