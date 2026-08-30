<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">服务广场</h1>
        <p class="view-sub">统一入口 · 各服务使用原有账号密码登录</p>
      </div>
      <div style="display:flex;gap:8px">
        <input v-model="search" class="input" style="width:280px" placeholder="搜索服务名称 / 地址…" />
        <button class="btn btn-primary" @click="openAdd">添加服务</button>
        <button class="btn" @click="openHidden">隐藏服务<span v-if="hiddenServices.length"> ({{ hiddenServices.length }})</span></button>
        <button class="btn" @click="reload">刷新</button>
      </div>
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
          <span v-if="s.version" class="mono">v{{ s.version }}</span>
        </div>
        <div class="svc-actions">
          <a
            class="btn btn-primary btn-sm enter-btn"
            :href="s.entry_url" target="_blank" rel="noopener noreferrer"
            @click.stop
          >进入服务</a>
          <button class="btn btn-sm btn-ghost" title="查看详情" @click.stop="openDetail(s)">详情</button>
          <button class="btn btn-sm btn-ghost" title="从服务广场隐藏" @click.stop="hideService(s)">隐藏</button>
          <button v-if="s.manual" class="btn btn-sm btn-danger" title="永久删除手动服务" @click.stop="deleteManualService(s)">删除</button>
        </div>
      </div>
    </div>

    <!-- 详情抽屉 -->
    <ServiceDetailDrawer :visible="drawerVisible" :service="selected" @close="drawerVisible = false" />

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
        <div class="field full"><label>说明</label><textarea v-model.trim="form.description" class="textarea" rows="3" placeholder="服务用途、登录方式等（不要填写密码）"></textarea></div>
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
const drawerVisible = ref(false)
const selected = ref(null)
const hosts = ref([])
const addVisible = ref(false)
const hiddenVisible = ref(false)
const hiddenLoading = ref(false)
const saving = ref(false)
const categories = ['应用服务', '代码与CI/CD', '监控与日志', '安全与运维', '开发工具', '文档工具', '未分类']
const emptyForm = () => ({ server_id: '', name: '', url: '', category: '应用服务', description: '', health_path: '', pinned: false })
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
    .sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0))
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

function warmService(service){try{const origin=new URL(service.entry_url,location.href).origin;if(document.head.querySelector(`link[data-ops-origin="${origin}"]`))return;const link=document.createElement('link');link.rel='preconnect';link.href=origin;link.crossOrigin='anonymous';link.dataset.opsOrigin=origin;document.head.appendChild(link)}catch{/* 非标准地址忽略 */}}

async function reload(silent=false) {
  loading.value = true
  try {
    const svc = await api.get('/services/plaza')
    if(Array.isArray(svc)){services.value=svc;localStorage.setItem(PLAZA_CACHE_KEY,JSON.stringify({items:svc,time:Date.now()}));svc.slice(0,6).forEach(warmService)}
    groups.value = PLAZA_GROUPS
  } catch(error) {
    if(!services.value.length&&!silent)toast(`服务广场加载失败：${error.message}`,'error')
  } finally {
    loading.value = false
  }
}

async function loadHosts() {
  if (!hosts.value.length) hosts.value = await api.get('/servers')
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
    if (service.manual && service.service_id) {
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
.g-tab {
  padding: 6px 14px; border-radius: 999px; border: 1px solid var(--border);
  background: #fff; font-size: 13px; color: var(--muted); cursor: pointer;
}
.g-tab.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.svc-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
.svc-card { display: flex; flex-direction: column; gap: 8px; cursor: pointer; transition: all .15s; }
.svc-card:hover { border-color: var(--brand); box-shadow: 0 8px 22px rgba(37,99,235,.12); transform: translateY(-2px); }
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
  .view-head { align-items: flex-start; }
  .view-head > div:last-child { flex-wrap: wrap; }
  .view-head .input { width: 100% !important; }
  .form-grid { grid-template-columns: 1fr; }
  .form-grid .full { grid-column: auto; }
}
</style>
