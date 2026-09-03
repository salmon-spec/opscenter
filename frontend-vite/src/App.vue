<template>
  <div class="shell">
    <!-- 侧栏：独立大屏页（standalone）隐藏 -->
    <aside v-if="!isStandalone" class="sidebar">
      <div class="logo">
        <span class="logo-badge">Ops</span>
        <div class="logo-text">
          <div class="logo-title">运维工作台</div>
          <div class="logo-ver">v{{ appVersion }}</div>
        </div>
      </div>
      <nav class="nav">
        <template v-for="item in navs" :key="item.path || item.key || item.label">
          <div v-if="item.children" class="nav-group-wrap">
            <button class="nav-item nav-group" :class="{active:groupActive(item)}" @click="toggleGroup(item)"><span class="nav-icon">{{ item.icon }}</span><span class="nav-label">{{ item.label }}</span><span class="chevron">{{ openGroups[item.key]?'⌃':'⌄' }}</span></button>
            <div v-if="openGroups[item.key]" class="nav-children"><router-link v-for="child in item.children" :key="child.path" :to="child.path" class="nav-item nav-child" :class="{active:isActive(child.path, child.exact)}">{{ child.label }}</router-link></div>
          </div>
          <router-link v-else :to="item.path" class="nav-item" :class="{ active: isActive(item.path, item.exact) }"><span class="nav-icon">{{ item.icon }}</span><span class="nav-label">{{ item.label }}</span></router-link>
        </template>
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
          <div class="global-host"><span class="dot" :class="currentHost?.status==='online'?'ok':'warn'"></span><select :value="selectedHostId" @change="selectHost($event.target.value)"><option v-for="host in hosts" :key="host.id" :value="host.id">{{ host.name }} · {{ host.host }}</option></select><button class="btn btn-sm" @click="hostDrawer=true">管理主机</button></div>
          <span class="muted">{{ nowStr }}</span>
        </div>
      </header>
      <div class="content" :class="{ standalone: isStandalone }">
        <router-view />
      </div>
    </div>

    <!-- 全局 toast -->
    <div class="toasts">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.type">{{ t.msg }}</div>
    </div>
    <HostManagerDrawer :visible="hostDrawer" @close="hostDrawer=false" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import HostManagerDrawer from './components/HostManagerDrawer.vue'
import { useHostContext } from './hostContext'
import packageInfo from '../package.json'

const route = useRoute()
const appVersion = packageInfo.version
const isStandalone = computed(() => !!route.meta.standalone)
const { hosts, selectedHostId, currentHost, refreshHosts, selectHost } = useHostContext()
const hostDrawer=ref(false)
// 分组展开状态：plaza=服务广场、system=系统（可复用，替代单一 systemOpen）
const openGroups = reactive({ plaza: true, system: route.path.startsWith('/system') })
function isActive(path, exact) { return exact ? route.path === path : (route.path === path || (path !== '/' && route.path.startsWith(path))) }
function groupActive(item) { return (item.children || []).some((c) => isActive(c.path, c.exact)) }
function toggleGroup(item) { openGroups[item.key] = !openGroups[item.key] }
watch(() => route.path, (path) => {
  for (const item of navs) {
    if (item.children && (item.children || []).some((c) => isActive(c.path, c.exact))) openGroups[item.key] = true
  }
}, { immediate: true })

const navs = [
  { key: 'plaza', label: '服务广场', icon: '▦', children: [
    { path: '/', label: '服务列表', exact: true },
    { path: '/service-health', label: '服务健康' },
  ]},
  { key: 'system', label: '系统', icon: '▥', children: [
    { path: '/system/monitor', label: '监控' },
    { path: '/container', label: '容器' },
    { path: '/database', label: '数据库' },
    { path: '/system/files', label: '文件' },
    { path: '/system/terminal', label: '终端' },
    { path: '/system/firewall', label: '防火墙' },
    { path: '/system/ssh', label: 'SSH 管理' },
    { path: '/system/processes', label: '进程管理' },
  ]},
  { path: '/screen', label: '健康大屏', icon: '📊' },
  { path: '/topology', label: '拓扑架构', icon: '🔗' },
  { path: '/alerts', label: '告警中心', icon: '🔔' },
  { path: '/api-keys', label: '开放API', icon: '🔑' },
  { path: '/logs', label: '日志中心', icon: '≡' },
]

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
  loadHosts()
  window.addEventListener('ops-toast', onToast)
})
onUnmounted(() => {
  clearInterval(clockTimer)
  window.removeEventListener('ops-toast', onToast)
})
</script>

<style scoped>
.shell { display: flex; height: 100vh; overflow: hidden; }
.sidebar {
  width: 200px; flex-shrink: 0; background: var(--sidebar); color: var(--sidebar-text);
  display: flex; flex-direction: column; padding: 16px 10px;
}
.logo { display: flex; align-items: center; gap: 10px; padding: 4px 8px 18px; border-bottom: 1px solid rgba(148,163,184,.15); }
.logo-badge {
  width: 36px; height: 36px; border-radius: 10px; background: var(--brand);
  color: #fff; display: flex; align-items: center; justify-content: center; font-weight: 800; font-size: 13px;
}
.logo-title { color: #fff; font-size: 14px; font-weight: 700; }
.logo-ver { font-size: 11px; color: var(--sidebar-text); }
.nav { flex: 1; margin-top: 14px; display: flex; flex-direction: column; gap: 2px; }
.nav-item {
  display: flex; align-items: center; gap: 10px; padding: 10px 12px; border-radius: 8px;
  color: var(--sidebar-text); text-decoration: none; font-size: 14px; transition: all .15s;
}
.nav-item:hover { background: rgba(148,163,184,.12); color: #fff; }
.nav-item.active { background: rgba(37,99,235,.22); color: var(--sidebar-active); font-weight: 600; }
.nav-icon { width: 20px; text-align: center; }
.nav-group-wrap{position:relative}.nav-group{width:100%;border:0;cursor:pointer}.chevron{margin-left:auto}.nav-children{display:flex;flex-direction:column;gap:2px}.nav-child{padding-left:42px;font-size:13px}
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
.global-host{display:flex;align-items:center;gap:7px;border:1px solid var(--border);border-radius:8px;padding:4px 5px 4px 9px;background:var(--card)}.global-host select{border:0;background:transparent;color:var(--text);outline:none;max-width:250px}.global-host .dot{flex:none}
.content { flex: 1; overflow: auto; }
.content.standalone { overflow: hidden; background: var(--screen-bg); }
/* 移动端：侧栏收窄为图标栏，分组子菜单弹出，不遮挡内容 */
@media (max-width: 768px) {
  .sidebar { width: 56px; padding: 14px 6px; }
  .logo-text { display: none; }
  .nav-item { justify-content: center; padding: 10px 0; }
  .nav-group .nav-label, .nav-group .chevron { display: none; }
  .nav-children { position: absolute; left: 52px; top: 0; min-width: 150px; background: var(--sidebar); padding: 6px; border-radius: 8px; box-shadow: 0 8px 30px rgba(0,0,0,.4); z-index: 120; }
  .nav-child { justify-content: flex-start; padding-left: 12px; }
  .global-host select { max-width: 130px; }
}
</style>
