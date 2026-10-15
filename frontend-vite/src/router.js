/* OpsCenter 路由：hash 模式，视图内切换不刷新页面。
   v4.9 需求基线（2026-09-10）§3/§6：
   - 全部路由补 name（工作台页签唯一键依赖 routeName）与 hostScope/clusterScope meta；
   - hostScope/clusterScope: 'required' | 'optional' | 'none'，由 App.vue 条件式选择器消费；
   - /container 保留到 K3s 概览的兼容跳转；旧 Docker/主机控制页面不再提供入口。 */
import { createRouter, createWebHashHistory } from 'vue-router'
import ServicePlaza from './views/ServicePlaza.vue'
import DutyHome from './views/DutyHome.vue'

const Screen = () => import('./views/Screen.vue')
const Alerts = () => import('./views/Alerts.vue')
const ApiKeys = () => import('./views/ApiKeys.vue')
const Database = () => import('./views/Database.vue')
const SystemMonitor = () => import('./views/SystemMonitor.vue')
const LogCenter = () => import('./views/LogCenter.vue')
const KubernetesOverview = () => import('./views/kubernetes/ClusterOverview.vue')
const KubernetesWorkloads = () => import('./views/kubernetes/Workloads.vue')
const KubernetesPods = () => import('./views/kubernetes/Pods.vue')
const KubernetesNetwork = () => import('./views/kubernetes/Network.vue')
const KubernetesStorage = () => import('./views/kubernetes/Storage.vue')
const KubernetesOperations = () => import('./views/kubernetes/Operations.vue')
const Hosts = () => import('./views/Hosts.vue')
const SystemFiles = () => import('./views/SystemFiles.vue')
const SystemFirewall = () => import('./views/SystemFirewall.vue')
const SystemSSH = () => import('./views/SystemSSH.vue')
const SystemTerminal = () => import('./views/SystemTerminal.vue')
const SystemProcesses = () => import('./views/SystemProcesses.vue')
const ModuleManager = () => import('./views/ModuleManager.vue')
const ExtensionHost = () => import('./views/ExtensionHost.vue')

const routes = [
  { path: '/', name: 'DutyHome', component: DutyHome, meta: { title: '值班首页', hostScope: 'none', clusterScope: 'none' } },
  { path: '/admin', redirect: '/' },
  { path: '/services', name: 'ServicePlaza', component: ServicePlaza, meta: { title: '服务广场', hostScope: 'none', clusterScope: 'none' } },
  { path: '/assets', redirect: '/system/monitor' },
  { path: '/container', redirect: '/kubernetes/overview' },
  { path: '/docker', redirect: '/system/monitor' },
  { path: '/database', name: 'Database', component: Database, meta: { title: '数据库', hostScope: 'required', clusterScope: 'none' } },
  { path: '/logs', name: 'LogCenter', component: LogCenter, meta: { title: '日志中心', hostScope: 'none', clusterScope: 'none' } },
  { path: '/service-health', redirect: '/services' },
  { path: '/system', redirect: '/system/monitor' },
  { path: '/system/monitor', name: 'SystemMonitor', component: SystemMonitor, meta: { title: '系统应用', hostScope: 'required', clusterScope: 'none' } },
  { path: '/system/files', name: 'SystemFiles', component: SystemFiles, meta: { title: '文件管理', hostScope: 'required', clusterScope: 'none' } },
  { path: '/system/firewall', name: 'SystemFirewall', component: SystemFirewall, meta: { title: '防火墙', hostScope: 'required', clusterScope: 'none' } },
  { path: '/system/ssh', name: 'SystemSSH', component: SystemSSH, meta: { title: 'SSH 管理', hostScope: 'required', clusterScope: 'none' } },
  { path: '/system/terminal', name: 'SystemTerminal', component: SystemTerminal, meta: { title: '终端', hostScope: 'none', clusterScope: 'none' } },
  { path: '/system/processes', name: 'SystemProcesses', component: SystemProcesses, meta: { title: '进程管理', hostScope: 'required', clusterScope: 'none' } },
  { path: '/screen', name: 'Screen', component: Screen, meta: { title: '健康大屏', hostScope: 'none', clusterScope: 'none' } },
  { path: '/screen-standalone', name: 'ScreenStandalone', component: Screen, meta: { title: '健康大屏', standalone: true, hostScope: 'none', clusterScope: 'none' } },
  { path: '/alerts', name: 'Alerts', component: Alerts, meta: { title: '告警中心', hostScope: 'none', clusterScope: 'none' } },
  { path: '/api-keys', name: 'ApiKeys', component: ApiKeys, meta: { title: '开放 API', hostScope: 'none', clusterScope: 'none' } },
  { path: '/hosts', name: 'Hosts', component: Hosts, meta: { title: '主机管理', hostScope: 'none', clusterScope: 'none' } },
  { path: '/modules', name: 'ModuleManager', component: ModuleManager, meta: { title: '模块管理', hostScope: 'none', clusterScope: 'none' } },
  { path: '/extensions/middleware-console', redirect: '/' },
  { path: '/extensions/:moduleKey', name: 'ExtensionHost', component: ExtensionHost, meta: { title: '扩展模块', hostScope: 'none', clusterScope: 'none' } },
  { path: '/data-services', redirect: '/' },
  { path: '/data-services/:pathMatch(.*)*', redirect: '/' },
  { path: '/kubernetes/overview', name: 'KubernetesOverview', component: KubernetesOverview, meta: { title: '集群概览', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/workloads', name: 'KubernetesWorkloads', component: KubernetesWorkloads, meta: { title: '工作负载', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/pods', name: 'KubernetesPods', component: KubernetesPods, meta: { title: 'Pod', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/network', name: 'KubernetesNetwork', component: KubernetesNetwork, meta: { title: '网络与入口', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/storage', name: 'KubernetesStorage', component: KubernetesStorage, meta: { title: '存储', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/operations', name: 'KubernetesOperations', component: KubernetesOperations, meta: { title: '任务与事件', hostScope: 'none', clusterScope: 'required' } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
