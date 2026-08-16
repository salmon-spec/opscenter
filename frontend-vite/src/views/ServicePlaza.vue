<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">服务广场</h1>
        <p class="view-sub">统一入口 · 点击「免密进入」直达服务</p>
      </div>
      <div style="display:flex;gap:8px">
        <input v-model="search" class="input" style="width:280px" placeholder="搜索服务名称 / 地址…" />
        <button class="btn" @click="reload">刷新</button>
      </div>
    </div>

    <!-- 分组标签 -->
    <div class="group-tabs">
      <button class="g-tab" :class="{ active: activeGroup === 'all' }" @click="activeGroup = 'all'">全部 ({{ filtered.length }})</button>
      <button
        v-for="g in groups" :key="g.id"
        class="g-tab" :class="{ active: activeGroup === g.id }"
        @click="activeGroup = g.id"
      >{{ g.name }} ({{ groupCount(g) }})</button>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>正在加载服务…</div>
    <EmptyState v-else-if="!filtered.length" icon="🔍" text="没有匹配的服务" />
    <div v-else class="svc-grid">
      <div v-for="s in filtered" :key="s.id" class="card svc-card" @click="openDetail(s)">
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
            :href="s.url" target="_blank" rel="noopener"
            @click.stop
          >进入</a>
          <button class="btn btn-sm" @click.stop="ssoOpen(s)">免密进入</button>
          <button class="btn btn-sm btn-ghost" title="查看详情" @click.stop="openDetail(s)">详情</button>
        </div>
      </div>
    </div>

    <!-- 详情抽屉 -->
    <ServiceDetailDrawer :visible="drawerVisible" :service="selected" @close="drawerVisible = false" />

    <!-- 登录框（免密进入） -->
    <Modal :visible="loginVisible" title="登录工作台（免密跳转）" width="400px" @close="loginVisible = false">
      <div class="field">
        <label>用户名</label>
        <input v-model="loginForm.username" class="input" placeholder="admin" @keyup.enter="doLogin" />
      </div>
      <div class="field">
        <label>密码</label>
        <input v-model="loginForm.password" class="input" type="password" placeholder="••••••••" @keyup.enter="doLogin" />
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button class="btn" @click="loginVisible = false">取消</button>
        <button class="btn btn-primary" :disabled="loginLoading" @click="doLogin">
          {{ loginLoading ? '登录中…' : '登录并跳转' }}
        </button>
      </div>
    </Modal>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, toast } from '../api'
import Modal from '../components/Modal.vue'
import EmptyState from '../components/EmptyState.vue'
import ServiceDetailDrawer from '../components/ServiceDetailDrawer.vue'

const services = ref([])
const groups = ref([])
const loading = ref(true)
const search = ref('')
const activeGroup = ref('all')
const drawerVisible = ref(false)
const selected = ref(null)

const loginVisible = ref(false)
const loginLoading = ref(false)
const loginForm = ref({ username: 'admin', password: '' })
let pendingUrl = ''

// 分类 → 分组映射（与后端 CATEGORY_TO_GROUP 对齐）
const CATEGORY_TO_GROUP = {
  '代码与CI/CD': 'cicd', 'CI/CD': 'cicd', '监控与日志': 'monitor', '监控': 'monitor',
  '网络与代理': 'network', '数据存储': 'database', '消息与注册': 'middleware',
  '自动化工作流': 'auto_workflow', '自动化': 'auto_workflow', '运维管理': 'ops',
  '运维面板': 'ops', '应用服务': 'app', '文档工具': 'app', '开发工具': 'app',
  '数据平台': 'app', '前端应用': 'app', '安全与认证': 'security',
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
  return services.value.filter((s) => groupOf(s) === g.id).length
}

const filtered = computed(() => {
  const kw = search.value.trim().toLowerCase()
  return services.value
    .filter((s) => activeGroup.value === 'all' || groupOf(s) === activeGroup.value)
    .filter((s) => !kw || s.name.toLowerCase().includes(kw) || (s.url || '').toLowerCase().includes(kw) || (s.description || '').toLowerCase().includes(kw))
    .sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0))
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

async function reload() {
  loading.value = true
  try {
    const [svc, grp] = await Promise.allSettled([
      api.get('/services-with-status'),
      api.get('/group-config/merged'),
    ])
    services.value = svc.status === 'fulfilled' ? svc.value : []
    groups.value = grp.status === 'fulfilled' ? grp.value : []
  } finally {
    loading.value = false
  }
}

// 免密进入：先检查工作台会话，未登录则弹登录框
async function ssoOpen(s) {
  try {
    const me = await api.get('/auth/me')
    if (me?.username) { window.open(s.url, '_blank', 'noopener'); return }
    pendingUrl = s.url
    loginForm.value = { username: 'admin', password: '' }
    loginVisible.value = true
  } catch (e) {
    if (e.status === 404) {
      // SSO 未部署（404）：免登录模式直接打开
      window.open(s.url, '_blank', 'noopener')
      return
    }
    // 401 等：弹出登录框
    pendingUrl = s.url
    loginForm.value = { username: 'admin', password: '' }
    loginVisible.value = true
  }
}

async function doLogin() {
  loginLoading.value = true
  try {
    await api.post('/auth/login', loginForm.value)
    loginVisible.value = false
    toast('登录成功，正在跳转…', 'ok')
    if (pendingUrl) window.open(pendingUrl, '_blank', 'noopener')
  } catch (e) {
    toast(e.message || '登录失败', 'err')
  } finally {
    loginLoading.value = false
  }
}

onMounted(reload)
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
.svc-actions .btn { flex: 1; }
.enter-btn { text-decoration: none; }
</style>
