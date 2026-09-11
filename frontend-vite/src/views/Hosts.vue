<template>
  <div ref="rootEl" class="view hosts-view">
    <div class="view-head">
      <div>
        <h1 class="view-title">主机管理</h1>
        <p class="view-sub">主机资产盘点 · LAN/WG 双通道 · Agent 与日志采集器维护</p>
      </div>
      <div class="head-actions">
        <div class="ov-chips">
          <span class="ov-chip">总数 <b>{{ overview.total }}</b></span>
          <span class="ov-chip">在线 <b class="ok-text">{{ overview.online }}</b></span>
          <span class="ov-chip">Agent 待升级 <b :class="overview.outdated ? 'warn-text' : ''">{{ overview.outdated }}</b></span>
        </div>
        <button class="btn" :disabled="hostLoading" @click="reloadAll">↻ 刷新</button>
        <button class="btn btn-primary" @click="openAdd">＋ 添加主机</button>
      </div>
    </div>

    <HostList
      :filters="filters"
      :metrics-map="metricsMap"
      :cluster-options="clusterOptions"
      :agent-version="agentVersion"
      @update:filters="onFilters"
      @detail="openDetail"
      @edit="openEdit"
    />

    <HostEditorDrawer :visible="editorVisible" :host="editingHost" @close="editorVisible = false" @saved="onSaved" />
    <HostDetailDrawer :visible="detailVisible" :host-id="detailId" @close="detailVisible = false" @upgraded="reloadAll" />
  </div>
</template>

<script>
export default { name: 'Hosts' }
</script>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { useHostContext } from '../hostContext'
import workbench, { useTabActive, setTabState, getTabState } from '../workbench/tabs'
import { useHostPolling, isHostOutdated } from '../components/host/hostAdmin'
import HostList from '../components/host/HostList.vue'
import HostEditorDrawer from '../components/host/HostEditorDrawer.vue'
import HostDetailDrawer from '../components/host/HostDetailDrawer.vue'

const { hosts, loading: hostLoading, refreshHosts } = useHostContext()
const { isActive } = useTabActive('Hosts')

const tabKey = computed(() => workbench.state.activeKey)

const defaultFilters = { status: '', role: '', clusterId: '', agent: '', tag: '', keyword: '' }
const filters = reactive({ ...defaultFilters })
function onFilters(patch) {
  Object.assign(filters, patch)
}
function restoreFilters() {
  const saved = tabKey.value ? getTabState(tabKey.value) : {}
  Object.assign(filters, defaultFilters, saved.filters || {})
}
watch(filters, () => {
  if (tabKey.value) setTabState(tabKey.value, { filters: { ...filters } })
})

const agentVersion = ref('2.6.0')
async function loadAgentVersion() {
  try {
    const data = await api.get('/agents/version')
    agentVersion.value = data.current_version || '2.6.0'
  } catch {
    /* 保持内置目标版本 */
  }
}

const metricsMap = ref({})
async function loadMetrics() {
  try {
    const data = await api.get('/metrics/hosts/overview', { metrics: 'cpu,memory,disk' }, { timeoutMs: 15000 })
    const map = {}
    for (const host of data.hosts || []) {
      const m = host.metrics || {}
      map[host.server_id] = {
        cpu: m.cpu?.latest ?? null,
        memory: m.memory?.latest ?? null,
        disk: m.disk?.latest ?? null,
        last_at: m.cpu?.last_at || m.memory?.last_at || m.disk?.last_at || null,
      }
    }
    metricsMap.value = map
  } catch {
    metricsMap.value = {}
  }
}

const clusterOptions = ref([])
async function loadClusters() {
  try {
    const list = await api.get('/clusters', undefined, { timeoutMs: 5000 })
    clusterOptions.value = Array.isArray(list) ? list : []
  } catch {
    clusterOptions.value = []
  }
}

const overview = computed(() => ({
  total: hosts.value.length,
  online: hosts.value.filter((host) => host.status === 'online').length,
  outdated: hosts.value.filter((host) => isHostOutdated(host, agentVersion.value)).length,
}))

function reloadAll() {
  refreshHosts(true)
  loadMetrics()
}

const editorVisible = ref(false)
const editingHost = ref(null)
const detailVisible = ref(false)
const detailId = ref('')
function openAdd() {
  editingHost.value = null
  editorVisible.value = true
}
function openEdit(host) {
  editingHost.value = host
  editorVisible.value = true
}
function openDetail(host) {
  detailId.value = host.id
  detailVisible.value = true
}
async function onSaved() {
  editorVisible.value = false
  await refreshHosts(true)
  loadMetrics()
}
watch(hosts, (list) => {
  if (detailId.value && detailVisible.value && !list.some((host) => host.id === detailId.value)) {
    detailVisible.value = false
  }
  if (editingHost.value && editorVisible.value && !list.some((host) => host.id === editingHost.value.id)) {
    editorVisible.value = false
    editingHost.value = null
  }
})

const polling = useHostPolling({ visible: () => isActive.value })
watch(isActive, (active) => {
  if (active) polling.startPolling()
  else polling.stopPolling()
})

const rootEl = ref(null)
function contentEl() {
  return rootEl.value?.closest('.content') || null
}
onActivated(() => {
  const saved = tabKey.value ? getTabState(tabKey.value) : {}
  const el = contentEl()
  nextTick(() => {
    if (el && saved.scrollY) el.scrollTop = saved.scrollY
  })
  if (isActive.value) polling.startPolling()
})
onDeactivated(() => {
  const el = contentEl()
  if (el && tabKey.value) setTabState(tabKey.value, { scrollY: el.scrollTop })
  polling.stopPolling()
})

onMounted(() => {
  restoreFilters()
  refreshHosts()
  loadAgentVersion()
  loadMetrics()
  loadClusters()
  if (isActive.value) polling.startPolling()
})
</script>

<style scoped>
.hosts-view { display: flex; flex-direction: column; }
.head-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ov-chips { display: flex; gap: 8px; }
.ov-chip {
  display: inline-flex; align-items: center; gap: 5px; padding: 5px 12px;
  background: var(--card); border: 1px solid var(--border); border-radius: 999px; font-size: 12px; color: var(--muted);
}
.ov-chip b { color: var(--text); font-size: 13px; }
.ov-chip b.ok-text { color: var(--ok); }
.ov-chip b.warn-text { color: var(--warn); }
</style>
