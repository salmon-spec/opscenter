<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">服务广场</h1>
        <p class="view-sub">统一入口 · 完整信息与登录凭证集中管理</p>
      </div>
      <div style="display:flex;gap:8px">
        <input v-model="search" class="input" style="width:280px" :disabled="sortMode" placeholder="搜索服务名称 / 地址…" />
        <template v-if="sortMode"><button class="btn btn-primary" :disabled="orderSaving" @click="saveOrder">{{ orderSaving ? '保存中…' : '保存顺序' }}</button><button class="btn" @click="cancelOrder">取消排序</button></template>
        <button v-else class="btn" @click="startOrder">调整顺序</button>
        <template v-if="!sortMode"><button class="btn btn-primary" @click="openAdd">添加服务</button><button class="btn" @click="openHidden">隐藏服务<span v-if="hiddenServices.length"> ({{ hiddenServices.length }})</span></button><button class="btn" :disabled="scanBusy" @click="scanAllServices">{{ scanBusy ? '扫描中…' : '扫描全部主机' }}</button><button class="btn" @click="reload">刷新</button></template>
      </div>
    </div>

    <div class="health-overview">
      <button class="health-stat" :disabled="sortMode" :class="{active:healthFilter==='all'}" @click="healthFilter='all'"><strong>{{ overview.summary.total ?? services.length }}</strong><span>全部服务</span></button>
      <button class="health-stat is-up" :disabled="sortMode" :class="{active:healthFilter==='up'}" @click="healthFilter='up'"><strong>{{ overview.summary.up ?? statusCount('up') }}</strong><span>在线</span></button>
      <button class="health-stat is-down" :disabled="sortMode" :class="{active:healthFilter==='down'}" @click="healthFilter='down'"><strong>{{ overview.summary.down ?? statusCount('down') }}</strong><span>离线</span></button>
      <button class="health-stat is-warn" :disabled="sortMode" :class="{active:healthFilter==='degraded'}" @click="healthFilter='degraded'"><strong>{{ overview.summary.degraded ?? statusCount('degraded') }}</strong><span>波动中</span></button>
      <button class="health-stat" :disabled="sortMode" :class="{active:healthFilter==='unknown'}" @click="healthFilter='unknown'"><strong>{{ (overview.summary.unknown ?? statusCount('unknown')) + (overview.summary.disabled ?? statusCount('disabled')) }}</strong><span>未检测 / 停用</span></button>
      <div class="health-stat availability"><strong>{{ overview.summary.average_uptime_percent == null ? '-' : `${overview.summary.average_uptime_percent}%` }}</strong><span>24h 综合可用率</span><small v-if="overview.generated_at">统计于 {{ shortTime(overview.generated_at) }}</small></div>
    </div>

    <!-- 分组标签 -->
    <div class="group-tabs">
      <button class="g-tab" :disabled="sortMode" :class="{ active: activeGroup === 'all' }" @click="activeGroup = 'all'">全部 ({{ searched.length }})</button>
      <button
        v-for="g in visibleGroups" :key="g.id"
        class="g-tab" :disabled="sortMode" :class="{ active: activeGroup === g.id }"
        @click="activeGroup = g.id"
      >{{ g.name }} ({{ groupCount(g) }})</button>
    </div>

    <div v-if="loading && !services.length" class="loading"><span class="spinner"></span>正在加载服务…</div>
    <EmptyState v-else-if="!filtered.length" icon="🔍" text="没有匹配的服务" />
    <div v-else class="svc-grid">
      <div v-for="s in filtered" :key="s.id" class="card svc-card" :class="{'sorting-card':sortMode}" :draggable="sortMode" @dragstart="draggedKey=s.key" @dragover.prevent @drop="dropService(s.key)" @click="!sortMode&&openDetail(s)" @mouseenter="warmService(s)">
        <div class="svc-top">
          <span class="svc-icon" :style="{ background: groupColor(s) + '1a', color: groupColor(s) }">{{ iconOf(s) }}</span>
          <div v-if="sortMode" class="order-actions"><button class="btn btn-ghost btn-sm" title="上移" @click.stop="moveService(s,-1)">↑</button><button class="btn btn-ghost btn-sm" title="下移" @click.stop="moveService(s,1)">↓</button></div><span v-else class="dot" :class="statusDotClass(s.status)"></span>
        </div>
        <div class="svc-name">{{ s.name }}</div>
        <div class="svc-desc">{{ s.description || s.category || '' }}</div>
        <div class="svc-meta muted">
          <span>{{ s.server_name || '' }}</span>
          <span>{{ s.has_credentials ? '🔐 已配凭证' : (s.version ? `v${s.version}` : '') }}</span>
        </div>
        <div v-if="!sortMode" class="svc-actions">
          <a
            class="btn btn-primary btn-sm enter-btn"
            :href="s.entry_url" target="_blank" rel="noopener noreferrer"
            @click.stop
          >进入服务</a>
          <button class="btn btn-sm btn-ghost" title="查看详情" @click.stop="openDetail(s)">详情</button>
          <button class="btn btn-sm btn-ghost" title="从服务广场隐藏" @click.stop="hideService(s)">隐藏</button>
        </div>
      </div>
    </div>

    <!-- 详情抽屉 -->
    <ServiceDetailDrawer :visible="drawerVisible" :service="selected" @close="drawerVisible = false" @updated="handleServiceUpdated" @deleted="handleServiceDeleted" />

    <Modal :visible="addVisible" title="手动添加服务" width="620px" @close="addVisible = false">
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
        <div class="field"><label>登录账号</label><input v-model.trim="form.account" class="input" autocomplete="off" placeholder="可稍后在详情中补充" /></div>
        <div class="field"><label>登录密码</label><input v-model="form.password" class="input" type="password" autocomplete="new-password" placeholder="将加密保存" /></div>
        <label class="check-row full"><input v-model="form.pinned" type="checkbox" /> 添加后置顶</label>
      </div>
      <div class="modal-actions">
        <button class="btn" @click="addVisible = false">取消</button>
        <button class="btn btn-primary" :disabled="saving" @click="createService">{{ saving ? '保存中…' : '保存并加入广场' }}</button>
      </div>
    </Modal>

    <Modal :visible="hiddenVisible" title="隐藏的服务" width="900px" @close="hiddenVisible = false">
      <div v-if="hiddenLoading" class="loading"><span class="spinner"></span>正在加载…</div>
      <EmptyState v-else-if="!hiddenServices.length" icon="👁" text="暂无隐藏服务" />
      <table v-else class="table">
        <thead><tr><th>服务</th><th>主机</th><th>类型</th><th>地址/端口</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="service in hiddenServices" :key="service.id">
            <td><div style="font-weight:600">{{ service.name }}</div><div class="muted">{{ service.image || service.description || '-' }}</div></td>
            <td>{{ service.server_name || service.server_host || '-' }}</td>
            <td><span class="tag tag-slate">{{ kindLabel(service) }}</span></td>
            <td class="mono muted">{{ service.url || service.ports || '#none' }}</td>
            <td class="hidden-actions">
              <button class="btn btn-sm btn-primary" @click="restoreService(service)">恢复显示</button>
              <button v-if="service.deletable" class="btn btn-sm btn-danger" @click="deleteManualService(service)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </Modal>

  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import ServiceDetailDrawer from '../components/ServiceDetailDrawer.vue'

const PLAZA_CACHE_KEY='ops-plaza-cache-v2'
function readCache(){try{const value=JSON.parse(localStorage.getItem(PLAZA_CACHE_KEY)||'null');return Array.isArray(value?.items)?value.items:[]}catch{return[]}}
const services = ref(readCache())
const groups = ref([])
const loading = ref(!services.value.length)
const search = ref('')
const activeGroup = ref('all')
const healthFilter = ref('all')
const overview = ref({ summary: {}, items: [], generated_at: null })
const drawerVisible = ref(false)
const selected = ref(null)
const hosts = ref([])
const addVisible = ref(false)
const hiddenVisible = ref(false)
const hiddenLoading = ref(false)
const saving = ref(false)
const scanBusy = ref(false)
const sortMode = ref(false), sortDraft = ref([]), draggedKey = ref(''), orderSaving = ref(false)
const categories = ['应用服务', '代码与CI/CD', '监控与日志', '安全与运维', '开发工具', '文档工具', '未分类']
const emptyForm = () => ({ server_id: '', name: '', url: '', category: '应用服务', description: '', health_path: '', pinned: false, account: '', password: '' })
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
  return (sortMode.value ? sortDraft.value : services.value)
    .filter((s) => !kw || s.name.toLowerCase().includes(kw) || (s.entry_url || '').toLowerCase().includes(kw) || (s.description || '').toLowerCase().includes(kw))
})

const filtered = computed(() => {
  return searched.value
    .filter((s) => activeGroup.value === 'all' || groupOf(s) === activeGroup.value)
    .filter((s) => healthFilter.value === 'all' || (healthFilter.value === 'unknown' ? ['unknown','disabled'].includes(s.status || 'unknown') : s.status === healthFilter.value))
})

const visibleGroups = computed(() => groups.value.filter((g) => groupCount(g) > 0))

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

function startOrder(){search.value='';activeGroup.value='all';healthFilter.value='all';sortDraft.value=[...services.value];sortMode.value=true}
function cancelOrder(){sortMode.value=false;sortDraft.value=[];draggedKey.value=''}
function moveService(service,delta){const from=sortDraft.value.findIndex(item=>item.key===service.key),to=from+delta;if(from<0||to<0||to>=sortDraft.value.length)return;const rows=[...sortDraft.value];rows.splice(to,0,rows.splice(from,1)[0]);sortDraft.value=rows}
function dropService(targetKey){const from=sortDraft.value.findIndex(item=>item.key===draggedKey.value),to=sortDraft.value.findIndex(item=>item.key===targetKey);if(from<0||to<0||from===to)return;const rows=[...sortDraft.value];rows.splice(to,0,rows.splice(from,1)[0]);sortDraft.value=rows;draggedKey.value=''}
async function saveOrder(){orderSaving.value=true;try{await api.put('/services/plaza/order',{ordered_keys:sortDraft.value.map(item=>item.key)});services.value=[...sortDraft.value];localStorage.setItem(PLAZA_CACHE_KEY,JSON.stringify({items:services.value,time:Date.now()}));cancelOrder();toast('服务顺序已保存','success')}catch(error){toast(`顺序保存失败：${error.message}`,'error')}finally{orderSaving.value=false}}

function statusCount(status){return services.value.filter(s=>(s.status||'unknown')===status).length}
function shortTime(value){const date=new Date(value);return Number.isNaN(date.getTime())?'-':date.toLocaleTimeString('zh-CN',{hour12:false,hour:'2-digit',minute:'2-digit'})}

async function handleServiceUpdated(updated) {
  if (updated) selected.value = { ...selected.value, ...updated }
  await reload(true)
}

async function handleServiceDeleted() {
  drawerVisible.value = false
  selected.value = null
  await reload(true)
}

function warmService(service){try{const origin=new URL(service.entry_url,location.href).origin;if(document.head.querySelector(`link[data-ops-origin="${origin}"]`))return;const link=document.createElement('link');link.rel='preconnect';link.href=origin;link.crossOrigin='anonymous';link.dataset.opsOrigin=origin;document.head.appendChild(link)}catch{/* 非标准地址忽略 */}}

async function reload(silent=false) {
  loading.value = true
  try {
    const svc = await api.get('/services/plaza')
    if(Array.isArray(svc)){services.value=svc;localStorage.setItem(PLAZA_CACHE_KEY,JSON.stringify({items:svc,time:Date.now()}));svc.slice(0,6).forEach(warmService);loadHealthOverview()}
    groups.value = PLAZA_GROUPS
  } catch(error) {
    if(!services.value.length&&!silent)toast(`服务广场加载失败：${error.message}`,'error')
  } finally {
    loading.value = false
  }
}

async function loadHealthOverview(){try{const data=await api.get('/services/plaza/health-overview',{hours:24});overview.value=data||{summary:{},items:[]};const healthByKey=new Map((data.items||[]).map(item=>[item.key,item]));services.value=services.value.map(service=>{const health=healthByKey.get(service.key);return health?{...service,status:health.status,last_checked_at:health.last_checked_at,uptime_percent_24h:health.uptime_percent}:service});localStorage.setItem(PLAZA_CACHE_KEY,JSON.stringify({items:services.value,time:Date.now()}))}catch{/* 总览失败不影响服务入口 */}}

async function loadHosts() {
  if (!hosts.value.length) hosts.value = await api.get('/servers')
}

async function scanAllServices() {
  scanBusy.value = true
  try {
    const result = await api.post('/scan')
    await reload(true)
    const failed = (result.servers || []).filter((item) => item.status !== 'ok').length
    toast(`扫描完成：新增 ${result.added || 0}，更新 ${result.updated || 0}${failed ? `，${failed} 台主机未完成` : ''}`, failed ? 'warning' : 'success')
  } catch (error) {
    toast(`扫描失败：${error.message}`, 'error')
  } finally {
    scanBusy.value = false
  }
}

async function openAdd() {
  try {
    await loadHosts()
    form.value = emptyForm()
    addVisible.value = true
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
    const { server_id, ...payload } = form.value
    payload.health_path = payload.health_path || null
    await api.post(`/services?server_id=${encodeURIComponent(server_id)}`, payload)
    addVisible.value = false
    toast('服务已添加到服务广场', 'success')
    await reload()
  } catch (error) {
    toast(`添加失败：${error.message}`, 'error')
  } finally {
    saving.value = false
  }
}

async function openHidden() {
  hiddenVisible.value = true
  hiddenLoading.value = true
  try {
    hiddenServices.value = await api.get('/services/plaza/hidden')
  } catch (error) {
    toast(`隐藏服务加载失败：${error.message}`, 'error')
  } finally {
    hiddenLoading.value = false
  }
}

async function restoreService(service) {
  try {
    if (service.kind === 'catalog') {
      await api.put(`/services/plaza/${encodeURIComponent(service.key)}/visibility`, { hidden: false })
    } else {
      await api.put(`/services/${service.service_id || service.id}`, { hidden: false })
    }
    hiddenServices.value = hiddenServices.value.filter((item) => item.id !== service.id)
    toast(`${service.name} 已恢复显示`, 'success')
    await reload()
  } catch (error) {
    toast(`恢复失败：${error.message}`, 'error')
  }
}

function kindLabel(service) {
  if (service.kind === 'catalog') return '内置服务'
  if (service.kind === 'manual') return '手动服务'
  return '扫描服务'
}

async function hideService(service) {
  if (!confirm(`确认从服务广场隐藏「${service.name}」？之后可在“隐藏服务”中恢复。`)) return
  try {
    if (service.service_id) {
      await api.put(`/services/${service.service_id}`, { hidden: true })
    } else {
      await api.put(`/services/plaza/${encodeURIComponent(service.key)}/visibility`, { hidden: true })
    }
    toast(`${service.name} 已隐藏`, 'success')
    await reload()
  } catch (error) {
    toast(`隐藏失败：${error.message}`, 'error')
  }
}

async function deleteManualService(service) {
  const serviceId = service.service_id || service.id
  if (!serviceId || !confirm(`确认永久删除手动服务「${service.name}」？此操作不可恢复。`)) return
  try {
    await api.del(`/services/${serviceId}`)
    hiddenServices.value = hiddenServices.value.filter((item) => item.id !== service.id)
    toast(`${service.name} 已删除`, 'success')
    await reload()
  } catch (error) {
    toast(`删除失败：${error.message}`, 'error')
  }
}

onMounted(()=>reload(true))
</script>

<style scoped>
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
