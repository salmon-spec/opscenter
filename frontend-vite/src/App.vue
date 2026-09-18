<template>
  <div class="shell">
    <!-- 侧栏：独立大屏页（standalone）隐藏 -->
    <aside v-if="!isStandalone" class="sidebar" :class="{ collapsed: sidebarCollapsed }">
      <div class="logo">
        <span class="logo-badge">Ops</span>
        <div class="logo-text">
          <div class="logo-title">运维工作台</div>
          <div class="logo-ver">v{{ appVersion }}</div>
        </div>
        <button class="sidebar-toggle" :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'" @click="toggleSidebar">{{ sidebarCollapsed ? '›' : '‹' }}</button>
      </div>
      <nav class="nav">
        <router-link v-for="item in navs" :key="item.path" :to="item.path" class="nav-item" :title="sidebarCollapsed ? item.label : ''" :class="{ active: itemActive(item) }"><span class="nav-icon">{{ item.icon }}</span><span class="nav-label">{{ item.label }}</span></router-link>
      </nav>
      <div class="sidebar-foot">
        <div v-if="hostSummary.total > 0" class="host-mini">
          <span class="dot" :class="hostSummary.online === hostSummary.total ? 'ok' : 'warn'"></span>
          主机 {{ hostSummary.online }}/{{ hostSummary.total }} 在线
        </div>
      </div>
    </aside>

    <div class="main">
      <header v-if="!isStandalone" class="topbar">
        <h2 class="topbar-title">{{ route.meta.title || '工作台' }}</h2>
        <div class="topbar-right">
          <ClusterSelector v-if="showClusterSelector" />
          <span class="muted">{{ nowStr }}</span>
        </div>
      </header>
      <ModuleTabs v-if="!isStandalone" />
      <div class="workspace-body">
        <HostSelector v-if="showHostSelector" @manage="hostDrawer = true" />
        <div class="content" :class="{ standalone: isStandalone }">
          <router-view v-slot="{ Component }">
            <keep-alive :include="aliveNames">
              <component :is="Component" :key="viewKey" />
            </keep-alive>
          </router-view>
        </div>
      </div>
    </div>

    <!-- 全局 toast -->
    <div class="toasts">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.type">{{ t.msg }}</div>
    </div>
    <HostManagerDrawer :visible="hostDrawer" @close="hostDrawer=false" />
    <div v-if="loginVisible" class="auth-mask">
      <form class="auth-card" @submit.prevent="submitLogin">
        <h3>登录运维工作台</h3>
        <p>当前环境已启用访问保护。</p>
        <label>账号<input v-model.trim="loginForm.username" autocomplete="username" autofocus /></label>
        <label>密码或运维令牌<input v-model="loginForm.password" type="password" autocomplete="current-password" /></label>
        <div v-if="loginError" class="auth-error">{{ loginError }}</div>
        <button class="btn btn-primary" type="submit" :disabled="loginBusy">{{ loginBusy ? '登录中…' : '登录' }}</button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import router from './router'
import HostManagerDrawer from './components/HostManagerDrawer.vue'
import HostSelector from './components/workbench/HostSelector.vue'
import ClusterSelector from './components/workbench/ClusterSelector.vue'
import ModuleTabs from './components/workbench/ModuleTabs.vue'
import { useHostContext } from './hostContext'
import workbench from './workbench/tabs'
import packageInfo from '../package.json'
import { api, setAccessToken } from './api'

const route = useRoute()
const appVersion = packageInfo.version
const isStandalone = computed(() => !!route.meta.standalone)
// 工作台页签（v4.9）：afterEach 自动开页签 + localStorage 恢复（恢复后不自动导航）
workbench.init(router)
const { hosts, refreshHosts } = useHostContext()
const hostDrawer=ref(false)
const loginVisible=ref(false)
const loginBusy=ref(false)
const loginError=ref('')
const loginForm=reactive({ username:'admin', password:'' })
function requireLogin(){ loginVisible.value=true; loginError.value='' }
async function submitLogin(){
  if(!loginForm.username||!loginForm.password)return
  loginBusy.value=true;loginError.value=''
  try{
    const result=await api.post('/auth/login',loginForm)
    setAccessToken(result.access_token)
    window.location.reload()
  }catch(error){loginError.value=error.message}
  finally{loginBusy.value=false}
}
const SIDEBAR_KEY = 'ops-sidebar-collapsed'
const sidebarCollapsed = ref(localStorage.getItem(SIDEBAR_KEY) === '1')
function toggleSidebar() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  localStorage.setItem(SIDEBAR_KEY, sidebarCollapsed.value ? '1' : '0')
}
// 条件式选择器（需求基线 §6）：hostScope/clusterScope==='required' 才显示，互斥由路由 meta 保证
const showHostSelector = computed(() => route.meta.hostScope === 'required' && !route.meta.hostSelectorInside)
const showClusterSelector = computed(() => route.meta.clusterScope === 'required')
// KeepAlive：include 按组件 name（=路由 name）过滤，key 用页签唯一键 → 同路由多上下文多实例。
// activeKey 必须与当前路由匹配才作为 key，否则回退 route.fullPath（防止跨组件 key 碰撞）。
const aliveNames = workbench.aliveNames
const viewKey = computed(() => {
  const tab = workbench.state.tabs.find((t) => t.key === workbench.state.activeKey)
  return tab && tab.routeName === route.name && tab.path === route.path ? tab.key : route.fullPath
})
const navs = [
  { path: '/', label: '服务广场', icon: '▦', exact: true },
  { path: '/kubernetes/overview', label: 'K3s 集群', icon: '☸', matches: ['/kubernetes/'] },
  { path: '/data-services', label: '数据服务', icon: '◫', matches: ['/data-services'] },
  { path: '/system/monitor', label: '系统', icon: '▥', matches: ['/system/', '/docker', '/database'] },
  { path: '/screen', label: '健康大屏', icon: '📊' },
  { path: '/hosts', label: '主机管理', icon: '▣' },
  { path: '/topology', label: '拓扑架构', icon: '🔗' },
  { path: '/alerts', label: '告警中心', icon: '🔔' },
  { path: '/ai-ops', label: 'AI 运维', icon: '✦' },
  { path: '/api-keys', label: '开放API', icon: '🔑' },
  { path: '/logs', label: '日志中心', icon: '≡' },
]
function isActive(path, exact) { return exact ? route.path === path : (route.path === path || (path !== '/' && route.path.startsWith(path))) }
function itemActive(item) { return item.matches ? item.matches.some((path) => route.path.startsWith(path)) : isActive(item.path, item.exact) }

// 顶栏时钟 + 主机概览
const nowStr = ref('')
const hostSummary = ref({ total: 0, online: 0 })
let clockTimer = null

function tick() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  nowStr.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

async function loadHosts() {
  try {
    const list = await refreshHosts()
    hostSummary.value = {
      total: list.length,
      online: list.filter((s) => s.status === 'online').length,
    }
  } catch { /* 后端未就绪时静默 */ }
}

// 全局 toast
const toasts = ref([])
let toastId = 0
function onToast(e) {
  const t = { id: ++toastId, msg: e.detail.msg, type: e.detail.type || 'info' }
  toasts.value.push(t)
  setTimeout(() => { toasts.value = toasts.value.filter((x) => x.id !== t.id) }, 3200)
}

onMounted(() => {
  tick()
  clockTimer = setInterval(tick, 1000)
  window.addEventListener('ops-toast', onToast)
  window.addEventListener('ops-auth-required', requireLogin)
  loadHosts()
})
onUnmounted(() => {
  clearInterval(clockTimer)
  window.removeEventListener('ops-toast', onToast)
  window.removeEventListener('ops-auth-required', requireLogin)
})
</script>

<style scoped>
.shell { display: flex; height: 100vh; overflow: hidden; }
.auth-mask{position:fixed;inset:0;z-index:4000;background:rgba(15,23,42,.55);display:grid;place-items:center}.auth-card{width:min(380px,calc(100vw - 32px));padding:26px;background:#fff;border:1px solid var(--border);border-radius:14px;box-shadow:0 24px 70px rgba(15,23,42,.25);display:flex;flex-direction:column;gap:14px}.auth-card h3,.auth-card p{margin:0}.auth-card p{color:var(--muted);font-size:13px}.auth-card label{display:flex;flex-direction:column;gap:6px;font-size:13px}.auth-card input{height:38px;padding:0 10px;border:1px solid var(--border);border-radius:8px}.auth-error{color:var(--danger);font-size:12px}.auth-card button{align-self:stretch}
.sidebar {
  width: 200px; flex-shrink: 0; background: var(--sidebar); color: var(--sidebar-text);
  display: flex; flex-direction: column; padding: 16px 10px; transition: width .2s ease, padding .2s ease;
}
.logo { position: relative; display: flex; align-items: center; gap: 10px; padding: 4px 8px 18px; border-bottom: 1px solid rgba(148,163,184,.15); }
.logo-badge {
  width: 36px; height: 36px; border-radius: 10px; background: var(--brand);
  color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 13px;
}
.logo-title { color: #fff; font-size: 14px; font-weight: 700; }
.logo-ver { font-size: 11px; color: var(--sidebar-text); }
.sidebar-toggle { margin-left: auto; width: 24px; height: 24px; padding: 0; border: 1px solid rgba(148,163,184,.24); border-radius: 7px; background: rgba(148,163,184,.08); color: var(--sidebar-text); cursor: pointer; font-size: 20px; line-height: 20px; }
.sidebar-toggle:hover { color: #fff; background: rgba(148,163,184,.18); }
.nav { flex: 1; margin-top: 14px; display: flex; flex-direction: column; gap: 2px; }
.nav-item {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 8px;
  color: var(--sidebar-text); text-decoration: none; font-size: 14px; transition: all .15s;
}
.nav-item:hover { background: rgba(148,163,184,.12); color: var(--sidebar-text); }
.nav-item.active, .nav-item.active:hover { background: rgba(37,99,235,.22); color: var(--sidebar-active); font-weight: 600; }
.nav-icon { width: 20px; text-align: center; }
.sidebar-foot { padding: 12px 8px 4px; border-top: 1px solid rgba(148,163,184,.15); }
.host-mini { font-size: 12px; display: flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot.ok { background: var(--ok); }
.dot.warn { background: var(--warn); }
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.topbar {
  height: 56px; flex-shrink: 0; background: #fff; border-bottom: 1px solid var(--border);
  display: flex; align-items: center; justify-content: space-between; padding: 0 24px;
}
.topbar-title { font-size: 16px; margin: 0; }
.topbar-right { display: flex; align-items: center; gap: 12px; }
.workspace-body { flex: 1; min-height: 0; display: flex; overflow: hidden; }
.content { flex: 1; min-width: 0; overflow: auto; }
.content.standalone { overflow: hidden; background: var(--screen-bg); }
.sidebar.collapsed { width: 64px; padding-inline: 6px; }
.sidebar.collapsed .logo { justify-content: center; padding-inline: 0; }
.sidebar.collapsed .logo-badge { width: 34px; height: 34px; }
.sidebar.collapsed .logo-text, .sidebar.collapsed .nav-label, .sidebar.collapsed .host-mini { display: none; }
.sidebar.collapsed .sidebar-toggle { position: absolute; right: -17px; top: 9px; z-index: 130; background: var(--sidebar); }
.sidebar.collapsed .nav-item { justify-content: center; padding: 10px 0; }
/* 移动端：侧栏收窄为图标栏，分组子菜单弹出，不遮挡内容 */
@media (max-width: 768px) {
  .sidebar { width: 56px; padding: 14px 6px; }
  .logo-text { display: none; }
  .nav-item { justify-content: center; padding: 10px 0; }
  .sidebar-toggle { display: none; }
}
</style>
