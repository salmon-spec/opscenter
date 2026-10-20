<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">服务广场</h1>
        <p class="view-sub">统一服务入口与健康摘要</p>
      </div>
      <div style="display:flex;gap:8px">
        <input v-model="search" class="input" style="width:280px" placeholder="搜索服务名称 / 地址…" />
        <button class="btn" :class="{ 'btn-primary': viewMode === 'plaza' }" @click="viewMode = 'plaza'">服务广场</button>
        <button v-if="canEditCatalog" class="btn" :class="{ 'btn-primary': viewMode === 'inventory' }" @click="viewMode = 'inventory'">完整服务清单 ({{ inventory.length }})</button>
        <button class="btn" :disabled="!canEditCatalog" @click="openManager">服务管理</button>
        <button class="btn" @click="viewMode === 'plaza' ? reload() : loadInventory()">刷新</button>
      </div>
    </div>

    <template v-if="viewMode === 'plaza'">
    <div class="health-overview">
      <button class="health-stat" :class="{active:healthFilter==='all'}" @click="healthFilter='all'"><strong>{{ overview.summary.total ?? services.length }}</strong><span>广场服务</span></button>
      <button class="health-stat is-up" :class="{active:healthFilter==='up'}" @click="healthFilter='up'"><strong>{{ overview.summary.up ?? statusCount('up') }}</strong><span>在线</span></button>
      <button class="health-stat is-down" :class="{active:healthFilter==='down'}" @click="healthFilter='down'"><strong>{{ overview.summary.down ?? statusCount('down') }}</strong><span>离线</span></button>
      <button class="health-stat is-warn" :class="{active:healthFilter==='degraded'}" @click="healthFilter='degraded'"><strong>{{ overview.summary.degraded ?? statusCount('degraded') }}</strong><span>波动中</span></button>
      <button class="health-stat" :class="{active:healthFilter==='unknown'}" @click="healthFilter='unknown'"><strong>{{ (overview.summary.unknown ?? statusCount('unknown')) + (overview.summary.disabled ?? statusCount('disabled')) }}</strong><span>未检测 / 停用</span></button>
      <div class="health-stat availability"><strong>{{ overview.summary.average_uptime_percent == null ? '-' : `${overview.summary.average_uptime_percent}%` }}</strong><span>24h 综合可用率</span><small v-if="healthTimestamp" :class="{ stale: probeDataStale }">{{ probeDataStale ? '探测数据过期' : uptimeHistoryPending ? '探测正常 · 24h 可用率历史补齐中' : '探测于' }} {{ shortTime(healthTimestamp) }}</small></div>
    </div>

    <!-- 分组标签 -->
    <div class="group-tabs">
      <button class="g-tab" :class="{ active: activeGroup === 'all' }" @click="activeGroup = 'all'">全部 ({{ searched.length }})</button>
      <button
        v-for="g in visibleGroups" :key="g.id"
        class="g-tab" :class="{ active: activeGroup === g.id }"
        @click="activeGroup = g.id"
      >{{ g.name }} ({{ groupCount(g) }})</button>
    </div>

    <div v-if="loading && !services.length" class="loading"><span class="spinner"></span>正在加载服务…</div>
    <EmptyState v-else-if="!filtered.length" icon="🔍" text="没有匹配的服务" />
    <div v-else class="svc-grid">
      <div v-for="s in filtered" :key="s.id" class="card svc-card" @click="openDetail(s)" @mouseenter="warmService(s)">
        <div class="svc-top">
          <span class="svc-icon" :style="{ background: groupColor(s) + '1a', color: groupColor(s) }">{{ iconOf(s) }}</span>
          <span class="dot" :class="statusDotClass(s.status)"></span>
        </div>
        <div class="svc-name">{{ s.name }}</div>
        <div class="svc-desc">{{ s.description || s.category || '' }}</div>
        <div class="svc-meta muted">
          <span>{{ s.server_name || '' }}</span>
          <span>{{ s.has_credentials ? '🔐 已配凭证' : (s.version ? `v${s.version}` : '') }}</span>
        </div>
        <div class="svc-freshness" :class="{ stale: s.stale }">{{ serviceFreshnessText(s) }}</div>
        <div class="svc-actions">
          <a
            class="btn btn-primary btn-sm enter-btn"
            :href="s.entry_url" target="_blank" rel="noopener noreferrer"
            @click.stop
          >进入服务</a>
          <button class="btn btn-sm btn-ghost" title="查看详情" @click.stop="openDetail(s)">详情</button>
        </div>
      </div>
    </div>
    </template>

    <template v-else>
      <p class="view-sub">历史扫描与手动登记的全部服务；隐藏记录也在此列出。</p>
      <div v-if="inventoryLoading" class="loading"><span class="spinner"></span>正在加载服务清单…</div>
      <EmptyState v-else-if="!filteredInventory.length" icon="🔍" text="没有匹配的服务" />
      <table v-else class="table">
        <thead><tr><th>服务</th><th>主机</th><th>来源</th><th>地址 / 端口</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="service in filteredInventory" :key="service.id">
            <td><strong>{{ service.name }}</strong><span v-if="service.hidden" class="tag tag-slate">隐藏</span><div class="muted">{{ service.description || service.category || '-' }}</div></td>
            <td>{{ service.server_name || service.server_host || '-' }}</td>
            <td>{{ service.source || '-' }}</td>
            <td class="mono">{{ service.url || service.ports || (service.port ? `${service.proto || 'tcp'}:${service.port}` : '-') }}</td>
            <td>{{ service.status || 'unknown' }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 详情抽屉 -->
    <ServiceDetailDrawer :visible="drawerVisible" :service="selected" :read-only="!canEditCatalog" @close="drawerVisible = false" @updated="handleServiceUpdated" />

    <Modal :visible="managerVisible" title="服务管理" width="880px" @close="closeManager">
      <div v-if="managerPage === 'list'" class="manager-toolbar">
        <div class="manager-tabs">
          <button v-for="scope in [{id:'plaza',label:'广场服务'},{id:'hidden',label:'隐藏记录'},{id:'inventory',label:'完整清单'}]" :key="scope.id" class="btn btn-sm" :class="{'btn-primary':managerScope===scope.id}" :disabled="sortMode" @click="managerScope=scope.id">{{ scope.label }}</button>
        </div>
        <input v-model="managerSearch" class="input manager-search" :disabled="sortMode" placeholder="筛选服务名称 / 地址…" />
        <button v-if="!sortMode" class="btn btn-primary btn-sm" @click="openAdd">添加服务</button>
        <button v-if="!sortMode && managerScope === 'plaza'" class="btn btn-sm" :disabled="managerLoading || services.length < 2" @click="startOrder">调整顺序</button>
        <template v-if="sortMode">
          <button class="btn btn-sm" :disabled="orderSaving" @click="cancelOrder">取消排序</button>
          <button class="btn btn-primary btn-sm" :disabled="orderSaving" @click="saveOrder">{{ orderSaving ? '保存中…' : '保存顺序' }}</button>
        </template>
      </div>
      <template v-if="managerPage === 'list'">
        <p class="muted manager-note">{{ sortMode ? '使用上移、下移调整广场显示顺序，保存后生效。' : '在这里添加服务、管理显示状态、删除登记记录或调整广场顺序。' }}</p>
        <div v-if="managerLoading" class="loading"><span class="spinner"></span>正在加载服务…</div>
        <EmptyState v-else-if="!managerRows.length" icon="🔍" text="没有匹配的服务" />
        <div v-else class="manager-table">
          <table class="table">
            <thead><tr><th>服务</th><th>所属主机</th><th>显示状态</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="(service,index) in managerRows" :key="service.id">
                <td><strong>{{ service.name }}</strong><div class="muted manager-url">{{ service.entry_url || service.url || service.ports || '—' }}</div></td>
                <td>{{ service.server_name || service.server_host || '—' }}</td>
                <td><span class="tag" :class="managerScope === 'hidden' || service.hidden ? 'tag-slate' : 'tag-green'">{{ managerScope === 'hidden' || service.hidden ? '已隐藏' : '显示中' }}</span></td>
                <td><div class="manager-actions">
                  <template v-if="sortMode">
                    <button class="btn btn-sm" :disabled="orderSaving || index===0" @click="moveService(service,-1)">上移</button>
                    <button class="btn btn-sm" :disabled="orderSaving || index===managerRows.length-1" @click="moveService(service,1)">下移</button>
                  </template>
                  <template v-else>
                    <button v-if="managerScope === 'hidden' || service.hidden" class="btn btn-sm" @click="restoreService(service)">显示</button>
                    <button v-else class="btn btn-sm" @click="hideService(service)">隐藏</button>
                    <button class="btn btn-sm btn-danger" @click="deleteService(service)">删除</button>
                  </template>
                </div></td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <template v-else>
        <div class="manager-toolbar"><button class="btn btn-sm" :disabled="saving" @click="managerPage='list'">← 返回服务列表</button><strong>添加服务</strong></div>
      <div class="form-grid">
        <div class="field full"><label>所属主机 *</label>
          <select v-model="form.server_id" class="select">
            <option value="">请选择主机</option>
            <option v-for="host in hosts" :key="host.id" :value="host.id">{{ host.name }} · {{ host.host }}</option>
          </select>
        </div>
        <div class="field"><label>服务名称 *</label><input v-model.trim="form.name" class="input" placeholder="例如：内部 Wiki" /></div>
        <div class="field"><label>分类</label>
          <select v-model="form.category" class="select">
            <option v-for="category in categories" :key="category" :value="category">{{ category }}</option>
          </select>
        </div>
        <div class="field full"><label>访问地址 *</label><input v-model.trim="form.url" class="input" placeholder="http://10.66.66.x:端口/" /></div>
        <div class="field full"><label>健康检查路径</label><input v-model.trim="form.health_path" class="input" placeholder="例如 /health；留空时探测访问地址" /></div>
        <div class="field full"><label>说明</label><textarea v-model.trim="form.description" class="textarea" rows="3" placeholder="服务用途、登录方式和注意事项"></textarea></div>
        <p class="field full muted">当前仅登记入口和探活路径；服务凭证暂不通过 HTTP 表单收集。</p>
        <label class="check-row full"><input v-model="form.pinned" type="checkbox" /> 添加后置顶</label>
      </div>
      <div class="modal-actions">
        <button class="btn" @click="managerPage = 'list'">取消</button>
        <button class="btn btn-primary" :disabled="saving || !canEditCatalog" @click="createService">{{ saving ? '保存中…' : '保存并加入广场' }}</button>
      </div>
      </template>
    </Modal>

  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api, getAccessToken, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import ServiceDetailDrawer from '../components/ServiceDetailDrawer.vue'

const PLAZA_CACHE_KEY='ops-plaza-cache-v2'
function readCache(){try{const value=JSON.parse(localStorage.getItem(PLAZA_CACHE_KEY)||'null');return Array.isArray(value?.items)?value.items:[]}catch{return[]}}
const services = ref(readCache())
const inventory = ref([])
const inventoryLoading = ref(false)
const viewMode = ref('plaza')
const groups = ref([])
const loading = ref(!services.value.length)
const search = ref('')
const activeGroup = ref('all')
const healthFilter = ref('all')
const overview = ref({ summary: {}, items: [], generated_at: null })
const probeDataStale = computed(() => overview.value.items?.length
  ? overview.value.items.some((item) => item.stale)
  : !!overview.value.stale)
const uptimeHistoryPending = computed(() => !!overview.value.stale && !probeDataStale.value)
const healthTimestamp = computed(() => {
  const timestamps = (overview.value.items || []).map((item) => item.status_checked_at || item.last_checked_at).filter(Boolean)
  return timestamps.length ? timestamps.sort().at(-1) : overview.value.data_timestamp || overview.value.generated_at
})
const drawerVisible = ref(false)
const selected = ref(null)
const canEditCatalog = ref(!!getAccessToken())
function syncAuth() {
  canEditCatalog.value = !!getAccessToken()
  if (canEditCatalog.value) loadInventory()
  else { inventory.value = []; viewMode.value = 'plaza' }
}
window.addEventListener('ops-auth-changed', syncAuth)
onUnmounted(() => window.removeEventListener('ops-auth-changed', syncAuth))
const hosts = ref([])
const managerVisible = ref(false), managerPage = ref('list'), managerScope = ref('plaza')
const managerSearch = ref(''), managerLoading = ref(false)
const saving = ref(false)
const sortMode = ref(false), sortDraft = ref([]), orderSaving = ref(false)
const categories = ['应用服务', '代码与CI/CD', '监控与日志', '安全与运维', '开发工具', '文档工具', '未分类']
const emptyForm = () => ({ server_id: '', name: '', url: '', category: '应用服务', description: '', health_path: '', pinned: false, server_name: '', server_host: '' })
const form = ref(emptyForm())
const hiddenServices = ref([])

const PLAZA_GROUPS = [
  { id: 'cicd', name: '代码与CI/CD', color: '#2dd4bf' },
  { id: 'app', name: '应用服务', color: '#f59e0b' },
  { id: 'monitor', name: '监控与日志', color: '#3b82f6' },
  { id: 'security_ops', name: '安全与运维', color: '#ef4444' },
]

// 分类 → 分组映射（与后端 CATEGORY_TO_GROUP 对齐）
const CATEGORY_TO_GROUP = {
  '代码与CI/CD': 'cicd', 'CI/CD': 'cicd', '监控与日志': 'monitor', '监控': 'monitor',
  '网络与代理': 'network', '数据存储': 'database', '消息与注册': 'middleware',
  '自动化工作流': 'auto_workflow', '自动化': 'auto_workflow', '运维管理': 'ops',
  '运维面板': 'ops', '应用服务': 'app', '文档工具': 'app', '开发工具': 'app',
  '数据平台': 'app', '前端应用': 'app', '安全与认证': 'security_ops',
  '安全与运维': 'security_ops',
}

const ICONS = {
  code: '</>', server: '🖥', hammer: '🔨', chart: '📈', shield: '🛡',
  database: '🗄', globe: '🌐', box: '📦', tool: '🛠', cube: '🧊',
  bolt: '⚡', eye: '👁', inbox: '📥', monitor: '🖥',
}

function iconOf(s) {
  return ICONS[s.icon] || (s.name ? s.name[0].toUpperCase() : '?')
}

function groupColor(s) {
  const g = groups.value.find((x) => x.id === groupOf(s))
  return g?.color || '#64748b'
}

function groupOf(s) {
  return CATEGORY_TO_GROUP[s.category] || s.category || 'ungrouped'
}

function groupCount(g) {
  return searched.value.filter((s) => groupOf(s) === g.id).length
}

const searched = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return services.value
    .filter((s) => !kw || s.name.toLowerCase().includes(kw) || (s.entry_url || '').toLowerCase().includes(kw) || (s.description || '').toLowerCase().includes(kw))
})

const filtered = computed(() => {
  return searched.value
    .filter((s) => activeGroup.value === 'all' || groupOf(s) === activeGroup.value)
    .filter((s) => healthFilter.value === 'all' || (healthFilter.value === 'unknown' ? ['unknown','disabled'].includes(s.status || 'unknown') : s.status === healthFilter.value))
})

const visibleGroups = computed(() => groups.value.filter((g) => groupCount(g) > 0))
const filteredInventory = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return inventory.value.filter((item) => !kw || [item.name, item.server_name, item.server_host, item.url, item.ports].some((value) => String(value || '').toLowerCase().includes(kw)))
})

function statusDotClass(st) {
  if (st === 'online' || st === 'up') return 'ok'
  if (st === 'offline' || st === 'down') return 'err'
  if (st === 'degraded') return 'warn'
  return ''
}

function openDetail(s) {
  selected.value = s
  drawerVisible.value = true
}

function startOrder(){managerSearch.value='';sortDraft.value=[...services.value];sortMode.value=true}
function cancelOrder(){sortMode.value=false;sortDraft.value=[]}
function moveService(service,delta){const from=sortDraft.value.findIndex(item=>item.key===service.key),to=from+delta;if(from<0||to<0||to>=sortDraft.value.length)return;const rows=[...sortDraft.value];rows.splice(to,0,rows.splice(from,1)[0]);sortDraft.value=rows}
async function saveOrder(){orderSaving.value=true;try{await api.put('/modules/service-catalog/api/v1/services/order',{ordered_keys:sortDraft.value.map(item=>item.key)});services.value=[...sortDraft.value];localStorage.setItem(PLAZA_CACHE_KEY,JSON.stringify({items:services.value,time:Date.now()}));cancelOrder();toast('服务顺序已保存','success')}catch(error){toast(`顺序保存失败：${error.message}`,'error')}finally{orderSaving.value=false}}

function statusCount(status){return services.value.filter(s=>(s.status||'unknown')===status).length}
function shortTime(value){const numeric=typeof value==='number'||/^\d+(\.\d+)?$/.test(String(value||''));const date=new Date(numeric?Number(value)*1000:value);return Number.isNaN(date.getTime())?'-':date.toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit'})}
function serviceFreshnessText(service){if(service.probe_enabled===false||service.status==='disabled')return '未启用健康检查';const rawAge=service.status_age_seconds;const age=rawAge===null||rawAge===undefined||rawAge===''?NaN:Number(rawAge);let checked='尚未检测';if(Number.isFinite(age)){checked=age<60?'刚刚检测':age<3600?`${Math.floor(age/60)} 分钟前检测`:`${Math.floor(age/3600)} 小时前检测`}else if(service.status_checked_at||service.last_checked_at){checked=`${shortTime(service.status_checked_at||service.last_checked_at)} 检测`}return service.stale?`数据过期 · ${checked}`:checked}

async function handleServiceUpdated(updated) {
  if (updated) selected.value = { ...selected.value, ...updated }
  await reload(true)
}

function warmService(service){try{const origin=new URL(service.entry_url,location.href).origin;if(document.head.querySelector(`link[data-ops-origin="${origin}"]`))return;const link=document.createElement('link');link.rel='preconnect';link.href=origin;link.crossOrigin='anonymous';link.dataset.opsOrigin=origin;document.head.appendChild(link)}catch{/* 非标准地址忽略 */}}

async function reload(silent=false) {
  loading.value = true
  try {
    const svc = await api.get('/modules/service-catalog/api/v1/services')
    if(Array.isArray(svc)){services.value=svc;localStorage.setItem(PLAZA_CACHE_KEY,JSON.stringify({items:svc,time:Date.now()}));svc.slice(0,6).forEach(warmService);await loadHealthOverview()}
    groups.value = PLAZA_GROUPS
  } catch(error) {
    if(!services.value.length&&!silent)toast(`服务广场加载失败：${error.message}`,'error')
  } finally {
    loading.value = false
  }
}

async function loadInventory() {
  if (!canEditCatalog.value) return
  inventoryLoading.value = true
  try { inventory.value = await api.get('/services/all') }
  catch (error) { toast(`完整服务清单加载失败：${error.message}`, 'error') }
  finally { inventoryLoading.value = false }
}

async function loadHealthOverview(){try{const data=await api.get('/modules/service-catalog/api/v1/health-overview',{hours:24});overview.value=data||{summary:{},items:[]};const healthByKey=new Map((data.items||[]).map(item=>[item.key,item]));services.value=services.value.map(service=>{const health=healthByKey.get(service.key);return health?{...service,...health}:service});localStorage.setItem(PLAZA_CACHE_KEY,JSON.stringify({items:services.value,time:Date.now()}))}catch{/* 总览失败不影响服务入口 */}}

const managerRows = computed(() => {
  const rows = sortMode.value ? sortDraft.value : managerScope.value === 'inventory' ? inventory.value : managerScope.value === 'hidden' ? hiddenServices.value : services.value
  const keyword = managerSearch.value.trim().toLowerCase()
  return rows.filter(service => !keyword || [service.name,service.entry_url,service.url,service.server_name].some(value => String(value || '').toLowerCase().includes(keyword)))
})
async function refreshManager() { await Promise.all([reload(true), loadInventory(), loadHiddenServices()]) }
async function openManager() {
  managerVisible.value=true; managerPage.value='list'; managerSearch.value=''; managerScope.value=viewMode.value === 'inventory' ? 'inventory' : 'plaza'
  cancelOrder(); managerLoading.value=true
  try { await refreshManager() } finally { managerLoading.value=false }
}
function closeManager() { if (saving.value || orderSaving.value) return; cancelOrder(); managerVisible.value=false }

async function loadHosts() {
  if (!hosts.value.length) hosts.value = (await api.get('/modules/host-inventory/api/v1/hosts')).items
}

async function openAdd() {
  try {
    await loadHosts()
    form.value = emptyForm()
    managerPage.value = 'add'
  } catch (error) {
    toast(`主机列表加载失败：${error.message}`, 'error')
  }
}

async function createService() {
  if (!form.value.server_id || !form.value.name || !/^https?:\/\//i.test(form.value.url)) {
    toast('请填写所属主机、服务名称和正确的 HTTP(S) 地址', 'error')
    return
  }
  saving.value = true
  try {
    const host = hosts.value.find((item) => item.id === form.value.server_id)
    await api.post('/modules/service-catalog/api/v1/services', {
      ...form.value, server_name: host?.name || '', server_host: host?.host || '',
    })
    managerPage.value = 'list'
    managerScope.value = 'plaza'
    managerSearch.value = ''
    toast('服务已添加到服务广场', 'success')
    await refreshManager()
  } catch (error) {
    toast(`添加失败：${error.message}`, 'error')
  } finally {
    saving.value = false
  }
}

async function loadHiddenServices() {
  try {
    const [plazaHidden, legacyServices] = await Promise.all([
      api.get('/modules/service-catalog/api/v1/hidden'),
      api.get('/services/all'),
    ])
    hiddenServices.value = [
      ...plazaHidden,
      ...legacyServices.filter((item) => item.hidden).map((item) => ({
        ...item, id: `legacy-${item.id}`, service_id: item.id,
        legacy_hidden: true, kind: 'scan',
      })),
    ]
  } catch (error) {
    toast(`隐藏服务加载失败：${error.message}`, 'error')
  }
}

async function restoreService(service) {
  try {
    if (service.legacy_hidden || !service.key) {
      if (service.kind === 'catalog') await api.put(`/modules/service-catalog/api/v1/legacy/catalog/${encodeURIComponent(service.key)}/visibility`, { hidden: false })
      else await api.put(`/modules/service-catalog/api/v1/legacy/services/${service.service_id || service.id}`, { hidden: false })
    } else {
      await api.put(`/modules/service-catalog/api/v1/services/${encodeURIComponent(service.key)}/visibility`, { hidden: false })
    }
    hiddenServices.value = hiddenServices.value.filter((item) => item.id !== service.id)
    toast(`${service.name} 已恢复显示`, 'success')
    await refreshManager()
  } catch (error) {
    toast(`恢复失败：${error.message}`, 'error')
  }
}

async function hideService(service) {
  if (!confirm(`确认从服务广场隐藏「${service.name}」？之后可在服务管理的“隐藏记录”中恢复。`)) return
  try {
    if (service.key) await api.put(`/modules/service-catalog/api/v1/services/${encodeURIComponent(service.key)}/visibility`, { hidden: true })
    else await api.put(`/modules/service-catalog/api/v1/legacy/services/${service.service_id || service.id}`, { hidden: true })
    toast(`${service.name} 已隐藏`, 'success')
    await refreshManager()
  } catch (error) {
    toast(`隐藏失败：${error.message}`, 'error')
  }
}

async function deleteService(service) {
  if (!canEditCatalog.value || !confirm(`确认删除服务「${service.name}」？这会移除登记入口，不会停止实际运行的服务。`)) return
  try {
    if (service.key) {
      await api.del(`/modules/service-catalog/api/v1/services/plaza/${encodeURIComponent(service.key)}`)
    } else if (service.kind === 'manual' && service.service_id && service.id?.startsWith('manual:')) {
      await api.post(`/modules/service-catalog/api/v1/services/manual/${service.service_id}/archive`)
    } else {
      await api.del(`/modules/service-catalog/api/v1/legacy/services/${service.service_id || service.id}`)
    }
    hiddenServices.value = hiddenServices.value.filter((item) => item.id !== service.id)
    drawerVisible.value = false
    selected.value = null
    toast(`${service.name} 已删除`, 'success')
    await refreshManager()
  } catch (error) {
    toast(`删除失败：${error.message}`, 'error')
  }
}

onMounted(() => { reload(true); if (canEditCatalog.value) loadInventory() })
</script>

<style scoped>
.manager-toolbar,.manager-tabs,.manager-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.manager-toolbar{margin-bottom:14px}.manager-search{flex:1;min-width:160px}.manager-note{margin:0 0 12px;font-size:12px}.manager-table{max-height:52vh;overflow:auto}.manager-table th{position:sticky;top:0;background:#fff;z-index:1}.manager-url{max-width:330px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.manager-actions{flex-wrap:nowrap}

.group-tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
.health-overview{display:grid;grid-template-columns:repeat(6,minmax(110px,1fr));gap:10px;margin:0 0 16px}.health-stat{display:flex;flex-direction:column;align-items:flex-start;gap:3px;padding:12px 14px;border:1px solid var(--border);border-radius:10px;background:var(--card);color:var(--text);cursor:pointer;text-align:left}.health-stat:hover,.health-stat.active{border-color:var(--brand);box-shadow:0 4px 14px rgba(37,99,235,.1)}.health-stat strong{font-size:22px}.health-stat span{font-size:12px;color:var(--muted)}.health-stat small{font-size:10px;color:var(--muted)}.health-stat.is-up strong{color:var(--ok)}.health-stat.is-down strong{color:var(--err)}.health-stat.is-warn strong{color:var(--warn)}.health-stat.availability{cursor:default;background:#f8fbff}.health-stat.availability strong{color:var(--brand)}
.g-tab {
  padding: 6px 14px; border-radius: 999px; border: 1px solid var(--border);
  background: #fff; font-size: 13px; color: var(--muted); cursor: pointer;
}
.g-tab.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.svc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
.svc-card { display: flex; flex-direction: column; gap: 8px; cursor: pointer; transition: all .15s; }
.svc-card:hover { border-color: var(--brand); box-shadow: 0 8px 22px rgba(37,99,235,.12); transform: translateY(-2px); }
.sorting-card{cursor:grab;border-style:dashed}.sorting-card:active{cursor:grabbing}.order-actions{display:flex;gap:2px}
.svc-top { display: flex; align-items: flex-start; justify-content: space-between; }
.svc-icon {
  width: 40px; height: 40px; border-radius: 10px; display: flex; align-items: center;
  justify-content: center; font-size: 18px; font-weight: 700;
}
.dot { width: 9px; height: 9px; border-radius: 50%; background: #94a3b8; }
.dot.ok { background: var(--ok); box-shadow: 0 0 0 3px rgba(22,163,74,.15); }
.dot.err { background: var(--err); box-shadow: 0 0 0 3px rgba(220,38,38,.15); }
.dot.warn { background: var(--warn); box-shadow: 0 0 0 3px rgba(217,119,6,.15); }
.svc-name { font-size: 15px; font-weight: 600; }
.svc-desc { font-size: 12px; color: var(--muted); min-height: 32px; }
.svc-meta { font-size: 12px; display: flex; justify-content: space-between; gap: 6px; }
.svc-freshness { font-size: 11px; color: var(--muted); }
.svc-freshness.stale, .health-stat small.stale { color: var(--err); font-weight: 600; }
.svc-actions { display: flex; gap: 6px; }
.svc-actions { flex-wrap: wrap; }
.svc-actions .btn { flex: 1 1 auto; }
.enter-btn { text-decoration: none; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 14px; }
.form-grid .full { grid-column: 1 / -1; }
.check-row { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
.hidden-actions { display: flex; gap: 6px; white-space: nowrap; }
@media (max-width: 760px) {
  .health-overview { grid-template-columns:repeat(2,1fr); }
  .view-head { align-items: flex-start; }
  .view-head > div:last-child { flex-wrap: wrap; }
  .view-head .input { width: 100% !important; }
  .form-grid { grid-template-columns: 1fr; }
  .form-grid .full { grid-column: auto; }
}
</style>
