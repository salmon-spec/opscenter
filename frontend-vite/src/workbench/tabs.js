/* OpsCenter v4.9 工作台页签 store（无 pinia，reactive 模块级单例，风格对齐 hostContext.js）。

── 对外 API（Wave2 页面接入用，函数签名务必按此调用）────────────────────────
- makeTabKey({routeName, clusterId='', serverId='', namespace='', resourceUid='', stableQuery=''}) → string
    页签唯一键：`${routeName}|${clusterId}|${serverId}|${namespace}|${resourceUid}|${stableQuery}`
- init(router)                       App.vue setup 中调用一次；注册 router.afterEach 自动开页签、
                                     visibilitychange 维护 uiVisible、从 localStorage 恢复页签（恢复后不自动导航）。
- openTab({route, titleSuffix='', context}) → key|null
    创建/激活页签；不负责导航（导航由调用方或 afterEach 负责）。route 需含 name/meta/path/query。
    context 可显式传 {clusterId, serverId, namespace, resourceUid, stableQuery} 覆盖自动推断；
    超过 MAX_TABS 时自动淘汰最早且未 pinned/未 unsaved/非 active 的页签；无可淘汰项时
    window.confirm('页签已达上限，关闭最早的未固定页签？')，取消则不创建（仅导航）。
- setTabContext(ctx)                 一次性上下文（resourceUid/namespace/stableQuery/titleSuffix 场景）：
    导航前调用，下一次 afterEach 自动开页签时消费一次后清空。示例见下方“资源级页签”。
- activateTab(key)                   应用页签上下文（切回 serverId/clusterId）并 router.push(path+query)。
- closeTab(key)                      SystemTerminal 页签或 unsaved 时 window.confirm；关闭后派发
    window.dispatchEvent(new CustomEvent('ops-tab-closed', {detail:{key, routeName}}))；
    关闭 active 时激活相邻页签（右邻优先）并导航。
- closeOthers(key) / closeRight(key) 批量关闭（pinned 页签保留；含 unsaved/终端时整批确认一次）。
- togglePin(key) / setActive(key)
- getTabState(key) → object          页签级非敏感 UI 状态（只读用；无则返回 {}，勿直接改返回值）
- setTabState(key, patch)            浅合并写入（scrollY、筛选、分页、抽屉开合等）
- markUnsaved(key, bool) / hasUnsaved(key) → boolean（内存态，不持久化）
- renameTab(key, title)              页面更新标题后缀，如 renameTab(key, '主机监控 · PVE')
- useTabActive(routeName) → { isActive: ComputedRef<boolean> }
    routeName 传路由 name 字符串（也可传 () => name）。当前 route.name === routeName 且页面可见（uiVisible）时为 true。
- isRouteActive(name) → boolean      模块级辅助；放进 computed 内调用即获得响应性。
- state（reactive：{tabs, activeKey}）、uiVisible（ref）、aliveNames（computed，KeepAlive include 用）
- MAX_TABS = 10

── 页面用法（Wave2 接入）── 隐藏页签暂停轮询：
    import { useTabActive } from '../workbench/tabs'
    const { isActive } = useTabActive('SystemMonitor')   // 传本页路由 name
    watch(isActive, (v) => { v ? startPolling() : stopPolling() })
  已知简化：同一路由开多个上下文页签时 isActive 按 routeName 判断会同时为 true（按指示接受）。

── 资源级页签（如 Pod 详情）：
    import workbench from '../workbench/tabs'
    workbench.setTabContext({ namespace: ns, resourceUid: podId, titleSuffix: podName })
    router.push({ path: '/kubernetes/pods', query: { ns, pod: podName } })
    afterEach 消费一次 setTabContext 生成带 resourceUid 的独立页签。

── KeepAlive 配合（App.vue 已接好）：
    <keep-alive :include="aliveNames"><component :is="Component" :key="viewKey" /></keep-alive>
    include 按组件 name（= 路由 name，见 router.js 骨架注释）过滤；key 用页签唯一键缓存实例，
    二者配合实现“同路由多上下文多实例”。别名映射见 COMPONENT_ALIASES（Docker→Assets 等）。

── 持久化：localStorage 'ops-workbench-tabs-v1' = { version, activeKey, tabs(仅元数据), states }
    只存非敏感 UI 状态；不存密码/Token/凭证/终端缓冲/日志内容。刷新后恢复全部页签（仅元数据），
    routeName 无法映射到现存路由的页签直接丢弃。 */
import { computed, reactive, ref, unref } from 'vue'
import { useHostContext } from '../hostContext'
import { useClusterContext } from './clusters'

export const MAX_TABS = 10

const STORAGE_KEY = 'ops-workbench-tabs-v1'

// KeepAlive include 按组件 name 匹配；组件 name 与路由 name 不一致处在此映射
//（其余视图 name === 路由 name：见 views/*.vue 与 views/kubernetes/*.vue 骨架）。
const COMPONENT_ALIASES = { Docker: 'Assets', ScreenStandalone: 'Screen' }

const { selectedHostId, currentHost, selectHost } = useHostContext()
const { selectedClusterId, currentCluster, selectCluster } = useClusterContext()

export const state = reactive({ tabs: [], activeKey: '' })
const tabStates = reactive({})
const unsavedKeys = new Set()
export const uiVisible = ref(typeof document === 'undefined' ? true : !document.hidden)
let routerRef = null
let initialized = false
let pendingTabContext = null

export function makeTabKey({ routeName, clusterId = '', serverId = '', namespace = '', resourceUid = '', stableQuery = '' } = {}) {
  return `${routeName}|${clusterId}|${serverId}|${namespace}|${resourceUid}|${stableQuery}`
}

// 按路由 meta 自动推断页签上下文（hostScope/clusterScope === 'required' 时取当前全局选择）
function resolveContext(route) {
  const ctx = {}
  if (route.meta?.hostScope === 'required') ctx.serverId = selectedHostId.value || ''
  if (route.meta?.clusterScope === 'required') ctx.clusterId = selectedClusterId.value || ''
  return ctx
}

function autoTitleSuffix(route) {
  if (route.meta?.hostScope === 'required') return currentHost.value?.name || ''
  if (route.meta?.clusterScope === 'required') return currentCluster.value?.name || ''
  return ''
}

function titleFor(route, suffix) {
  const base = route.meta?.title || String(route.name || '')
  return suffix ? `${base} · ${suffix}` : base
}

function applyContext(tab) {
  const ctx = tab.context || {}
  if (ctx.serverId && ctx.serverId !== selectedHostId.value) selectHost(ctx.serverId)
  if (ctx.clusterId && ctx.clusterId !== selectedClusterId.value) selectCluster(ctx.clusterId)
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      version: 1,
      activeKey: state.activeKey,
      tabs: state.tabs.map((t) => ({
        key: t.key, routeName: t.routeName, title: t.title, path: t.path,
        query: t.query, pinned: t.pinned, createdAt: t.createdAt, context: t.context,
      })),
      states: { ...tabStates },
    }))
  } catch { /* 隐私模式/配额满时静默 */ }
}

function removeTab(key) {
  const idx = state.tabs.findIndex((t) => t.key === key)
  if (idx < 0) return null
  const [tab] = state.tabs.splice(idx, 1)
  delete tabStates[key]
  unsavedKeys.delete(key)
  window.dispatchEvent(new CustomEvent('ops-tab-closed', { detail: { key, routeName: tab.routeName } }))
  return tab
}

function activateTabInternal(tab) {
  if (!tab) return
  applyContext(tab)
  state.activeKey = tab.key
  routerRef?.push({ path: tab.path, query: tab.query }).catch(() => {})
}

function evictOne() {
  let victim = state.tabs.find((t) => !t.pinned && !unsavedKeys.has(t.key) && t.key !== state.activeKey)
  if (!victim) {
    if (!state.tabs.some((t) => !t.pinned)) return false
    if (!window.confirm('页签已达上限，关闭最早的未固定页签？')) return false
    victim = state.tabs.find((t) => !t.pinned && t.key !== state.activeKey) || state.tabs.find((t) => !t.pinned)
  }
  if (!victim) return false
  removeTab(victim.key)
  return true
}

export function openTab({ route, titleSuffix, context } = {}) {
  if (!route?.name || route.meta?.standalone) return null
  // 页面显式 context 在自动推断（全局主机/集群选择）之上叠加，可覆盖同名字段
  const ctx = context ? { ...resolveContext(route), ...context } : resolveContext(route)
  const key = makeTabKey({ routeName: route.name, ...ctx })
  const existing = state.tabs.find((t) => t.key === key)
  if (existing) {
    // 同一上下文重复导航：页签跟随最新 path/query，避免页签与地址栏脱节
    existing.path = route.path
    existing.query = { ...(route.query || {}) }
    state.activeKey = key
    persist()
    return key
  }
  if (state.tabs.length >= MAX_TABS && !evictOne()) {
    state.activeKey = ''
    persist()
    return null
  }
  const suffix = titleSuffix !== undefined ? titleSuffix : autoTitleSuffix(route)
  const tab = {
    key,
    routeName: route.name,
    title: titleFor(route, suffix),
    path: route.path,
    query: { ...(route.query || {}) },
    pinned: false,
    createdAt: Date.now(),
    context: { ...ctx },
  }
  state.tabs.push(tab)
  state.activeKey = key
  persist()
  return key
}

export function setTabContext(ctx) {
  pendingTabContext = ctx || null
}

export function activateTab(key) {
  const tab = state.tabs.find((t) => t.key === key)
  if (!tab) return
  activateTabInternal(tab)
}

export function closeTab(key) {
  const tab = state.tabs.find((t) => t.key === key)
  if (!tab) return
  const confirmMsg = tab.routeName === 'SystemTerminal'
    ? '关闭该终端页签将释放终端会话，确认关闭？'
    : (unsavedKeys.has(key) ? '页签存在未保存的修改，确认关闭？' : '')
  if (confirmMsg && !window.confirm(confirmMsg)) return
  const wasActive = state.activeKey === key
  const idx = state.tabs.findIndex((t) => t.key === key)
  removeTab(key)
  if (wasActive) activateTabInternal(state.tabs[idx] || state.tabs[idx - 1] || null)
  if (wasActive && !state.tabs.length) state.activeKey = ''
  persist()
}

function closeBatch(victims, keepKey) {
  if (!victims.length) return
  const risky = victims.some((t) => unsavedKeys.has(t.key) || t.routeName === 'SystemTerminal')
  if (risky && !window.confirm('将关闭的页签中包含未保存内容或终端会话，确认关闭？')) return
  const keys = new Set(victims.map((t) => t.key))
  for (const t of [...state.tabs]) if (keys.has(t.key)) removeTab(t.key)
  if (keys.has(state.activeKey)) {
    const keep = state.tabs.find((t) => t.key === keepKey)
    if (keep) activateTabInternal(keep)
    else state.activeKey = ''
  }
  persist()
}

export function closeOthers(key) {
  closeBatch(state.tabs.filter((t) => t.key !== key && !t.pinned), key)
}

export function closeRight(key) {
  const idx = state.tabs.findIndex((t) => t.key === key)
  if (idx < 0) return
  closeBatch(state.tabs.slice(idx + 1).filter((t) => !t.pinned), key)
}

export function togglePin(key) {
  const tab = state.tabs.find((t) => t.key === key)
  if (!tab) return
  tab.pinned = !tab.pinned
  persist()
}

export function setActive(key) {
  if (!state.tabs.some((t) => t.key === key)) return
  state.activeKey = key
  persist()
}

export function getTabState(key) {
  return tabStates[key] || {}
}

export function setTabState(key, patch) {
  if (!key || !patch || typeof patch !== 'object') return
  tabStates[key] = { ...(tabStates[key] || {}), ...patch }
  persist()
}

export function markUnsaved(key, value = true) {
  if (value) unsavedKeys.add(key)
  else unsavedKeys.delete(key)
}

export function hasUnsaved(key) {
  return unsavedKeys.has(key)
}

export function renameTab(key, title) {
  const tab = state.tabs.find((t) => t.key === key)
  if (!tab || !title) return
  tab.title = String(title)
  persist()
}

export function isRouteActive(name) {
  if (!uiVisible.value) return false
  return routerRef?.currentRoute?.value?.name === name
}

export function useTabActive(routeName) {
  const getter = typeof routeName === 'function' ? routeName : () => unref(routeName)
  const isActive = computed(() => isRouteActive(getter()))
  return { isActive }
}

export const aliveNames = computed(() => {
  const names = new Set()
  for (const tab of state.tabs) names.add(COMPONENT_ALIASES[tab.routeName] || tab.routeName)
  return [...names]
})

function restore(router) {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null')
    if (!raw || !Array.isArray(raw.tabs)) return
    const known = new Set(router.getRoutes().map((r) => r.name).filter(Boolean))
    const seen = new Set()
    const tabs = []
    for (const t of raw.tabs) {
      if (!t?.key || !known.has(t.routeName) || seen.has(t.key)) continue
      seen.add(t.key)
      tabs.push({
        key: t.key,
        routeName: t.routeName,
        title: String(t.title || t.routeName),
        path: String(t.path || '/'),
        query: t.query && typeof t.query === 'object' && !Array.isArray(t.query) ? { ...t.query } : {},
        pinned: !!t.pinned,
        createdAt: Number(t.createdAt) || Date.now(),
        context: t.context && typeof t.context === 'object' ? { ...t.context } : {},
      })
      if (tabs.length >= MAX_TABS) break
    }
    state.tabs = tabs
    state.activeKey = tabs.some((t) => t.key === raw.activeKey) ? raw.activeKey : ''
    if (raw.states && typeof raw.states === 'object') {
      for (const [k, v] of Object.entries(raw.states)) {
        // 只保留仍存在的页签的 UI 状态，丢弃已失效页签的残留
        if (k && seen.has(k) && v && typeof v === 'object' && !Array.isArray(v)) tabStates[k] = v
      }
    }
  } catch { /* 存储损坏时静默丢弃 */ }
}

export function init(router) {
  if (initialized || !router) return
  initialized = true
  routerRef = router
  restore(router)
  router.afterEach((to) => {
    if (to.meta?.standalone) { pendingTabContext = null; return }
    const ctx = pendingTabContext
    pendingTabContext = null
    // ctx = {clusterId?, serverId?, namespace?, resourceUid?, stableQuery?, titleSuffix?}
    // 除 titleSuffix 外的字段进入 makeTabKey 组键
    openTab({ route: to, context: ctx || undefined, titleSuffix: ctx?.titleSuffix })
  })
  document.addEventListener('visibilitychange', () => {
    uiVisible.value = !document.hidden
  })
}

/* 聚合导出：App.vue 与页面均可 `import workbench from '../workbench/tabs'` 后按成员调用；
   也可按需具名导入（如 `import { useTabActive } from '../workbench/tabs'`）。 */
export default {
  MAX_TABS, state, uiVisible, aliveNames,
  makeTabKey, init, openTab, setTabContext, activateTab,
  closeTab, closeOthers, closeRight, togglePin, setActive,
  getTabState, setTabState, markUnsaved, hasUnsaved, renameTab,
  useTabActive, isRouteActive,
}
