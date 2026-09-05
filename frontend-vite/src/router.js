/* OpsCenter v3.29 路由：hash 模式，视图内切换不刷新页面 */
import { createRouter, createWebHashHistory } from 'vue-router'
import ServicePlaza from './views/ServicePlaza.vue'

const Assets = () => import('./views/Assets.vue')
const Screen = () => import('./views/Screen.vue')
const Topology = () => import('./views/Topology.vue')
const Alerts = () => import('./views/Alerts.vue')
const ApiKeys = () => import('./views/ApiKeys.vue')
const Database = () => import('./views/Database.vue')
const SystemMonitor = () => import('./views/SystemMonitor.vue')
const SystemFiles = () => import('./views/SystemFiles.vue')
const SystemFirewall = () => import('./views/SystemFirewall.vue')
const SystemSSH = () => import('./views/SystemSSH.vue')
const SystemTerminal = () => import('./views/SystemTerminal.vue')
const SystemProcesses = () => import('./views/SystemProcesses.vue')
const LogCenter = () => import('./views/LogCenter.vue')
const ServiceHealth = () => import('./views/ServiceHealth.vue')

const routes = [
  { path: '/', component: ServicePlaza, meta: { title: '服务广场' } },
  { path: '/assets', redirect: '/system/monitor' },
  { path: '/container', component: Assets, meta: { title: '容器' } },
  { path: '/database', component: Database, meta: { title: '数据库' } },
  { path: '/logs', component: LogCenter, meta: { title: '日志中心' } },
  { path: '/service-health', component: ServiceHealth, meta: { title: '服务健康' } },
  { path: '/system', redirect: '/system/monitor' },
  { path: '/system/monitor', component: SystemMonitor, meta: { title: '系统 · 监控' } },
  { path: '/system/files', component: SystemFiles, meta: { title: '系统 · 文件' } },
  { path: '/system/firewall', component: SystemFirewall, meta: { title: '系统 · 防火墙' } },
  { path: '/system/ssh', component: SystemSSH, meta: { title: '系统 · SSH 管理' } },
  { path: '/system/terminal', component: SystemTerminal, meta: { title: '系统 · 终端' } },
  { path: '/system/processes', component: SystemProcesses, meta: { title: '系统 · 进程管理' } },
  { path: '/screen', component: Screen, meta: { title: '监控大屏' } },
  { path: '/screen-standalone', component: Screen, meta: { title: '监控大屏', standalone: true } },
  { path: '/topology', component: Topology, meta: { title: '拓扑架构' } },
  { path: '/alerts', component: Alerts, meta: { title: '告警中心' } },
  { path: '/api-keys', component: ApiKeys, meta: { title: '开放 API' } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
