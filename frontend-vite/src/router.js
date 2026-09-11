/* OpsCenter 路由：hash 模式，视图内切换不刷新页面。
   v4.9 需求基线（2026-09-10）§3/§6：
   - 全部路由补 name（工作台页签唯一键依赖 routeName）与 hostScope/clusterScope meta；
   - hostScope/clusterScope: 'required' | 'optional' | 'none'，由 App.vue 条件式选择器消费；
   - /container 按用户决策 302 到 K3s 集群概览，独立 Docker 入口为 /docker。 */
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
const K3sConsolePreview = () => import('./views/K3sConsolePreview.vue')
const KubernetesOverview = () => import('./views/kubernetes/ClusterOverview.vue')
const KubernetesWorkloads = () => import('./views/kubernetes/Workloads.vue')
const KubernetesPods = () => import('./views/kubernetes/Pods.vue')
const KubernetesNetwork = () => import('./views/kubernetes/Network.vue')
const KubernetesStorage = () => import('./views/kubernetes/Storage.vue')
const KubernetesOperations = () => import('./views/kubernetes/Operations.vue')
const Hosts = () => import('./views/Hosts.vue')
// v5.0.0 数据服务（K3s 中间件纳管）：组件文件名=路由 name=KeepAlive include 名
const DataServicesOverview = () => import('./views/dataservices/DataServicesOverview.vue')
const DataServicesRedis = () => import('./views/dataservices/DataServicesRedis.vue')
const DataServicesRabbitMQ = () => import('./views/dataservices/DataServicesRabbitMQ.vue')
const DataServicesKafka = () => import('./views/dataservices/DataServicesKafka.vue')
const DataServicesZooKeeper = () => import('./views/dataservices/DataServicesZooKeeper.vue')
const DataServicesNacos = () => import('./views/dataservices/DataServicesNacos.vue')
const DataServicesMinio = () => import('./views/dataservices/DataServicesMinio.vue')
const DataServicesMongoDB = () => import('./views/dataservices/DataServicesMongoDB.vue')

const routes = [
  { path: '/', name: 'ServicePlaza', component: ServicePlaza, meta: { title: '服务广场', hostScope: 'none', clusterScope: 'none' } },
  { path: '/assets', redirect: '/system/monitor' },
  { path: '/container', redirect: '/kubernetes/overview' },
  { path: '/docker', name: 'Docker', component: Assets, meta: { title: 'Docker', hostScope: 'required', clusterScope: 'none' } },
  { path: '/database', name: 'Database', component: Database, meta: { title: '数据库', hostScope: 'required', clusterScope: 'none' } },
  { path: '/logs', name: 'LogCenter', component: LogCenter, meta: { title: '日志中心', hostScope: 'none', clusterScope: 'none' } },
  { path: '/service-health', name: 'ServiceHealth', component: ServiceHealth, meta: { title: '服务健康', hostScope: 'none', clusterScope: 'none' } },
  { path: '/system', redirect: '/system/monitor' },
  { path: '/system/monitor', name: 'SystemMonitor', component: SystemMonitor, meta: { title: '主机监控', hostScope: 'required', clusterScope: 'none' } },
  { path: '/system/files', name: 'SystemFiles', component: SystemFiles, meta: { title: '系统 · 文件', hostScope: 'required', clusterScope: 'none' } },
  { path: '/system/firewall', name: 'SystemFirewall', component: SystemFirewall, meta: { title: '系统 · 防火墙', hostScope: 'required', clusterScope: 'none' } },
  { path: '/system/ssh', name: 'SystemSSH', component: SystemSSH, meta: { title: '系统 · SSH 管理', hostScope: 'required', clusterScope: 'none' } },
  { path: '/system/terminal', name: 'SystemTerminal', component: SystemTerminal, meta: { title: '系统 · 终端', hostScope: 'required', clusterScope: 'none' } },
  { path: '/system/processes', name: 'SystemProcesses', component: SystemProcesses, meta: { title: '系统 · 进程管理', hostScope: 'required', clusterScope: 'none' } },
  { path: '/screen', name: 'Screen', component: Screen, meta: { title: '健康大屏', hostScope: 'none', clusterScope: 'none' } },
  { path: '/screen-standalone', name: 'ScreenStandalone', component: Screen, meta: { title: '健康大屏', standalone: true, hostScope: 'none', clusterScope: 'none' } },
  { path: '/topology', name: 'Topology', component: Topology, meta: { title: '拓扑架构', hostScope: 'none', clusterScope: 'none' } },
  { path: '/alerts', name: 'Alerts', component: Alerts, meta: { title: '告警中心', hostScope: 'none', clusterScope: 'none' } },
  { path: '/api-keys', name: 'ApiKeys', component: ApiKeys, meta: { title: '开放 API', hostScope: 'none', clusterScope: 'none' } },
  { path: '/hosts', name: 'Hosts', component: Hosts, meta: { title: '主机管理', hostScope: 'none', clusterScope: 'none' } },
  // v5.0.0 数据服务（中间件纳管与只读浏览）
  { path: '/data-services', name: 'DataServicesOverview', component: DataServicesOverview, meta: { title: '数据服务', hostScope: 'none', clusterScope: 'none' } },
  { path: '/data-services/redis', name: 'DataServicesRedis', component: DataServicesRedis, meta: { title: 'Redis 缓存', hostScope: 'none', clusterScope: 'none' } },
  { path: '/data-services/rabbitmq', name: 'DataServicesRabbitMQ', component: DataServicesRabbitMQ, meta: { title: 'RabbitMQ', hostScope: 'none', clusterScope: 'none' } },
  { path: '/data-services/kafka', name: 'DataServicesKafka', component: DataServicesKafka, meta: { title: 'Kafka', hostScope: 'none', clusterScope: 'none' } },
  { path: '/data-services/zookeeper', name: 'DataServicesZooKeeper', component: DataServicesZooKeeper, meta: { title: 'ZooKeeper', hostScope: 'none', clusterScope: 'none' } },
  { path: '/data-services/nacos', name: 'DataServicesNacos', component: DataServicesNacos, meta: { title: 'Nacos', hostScope: 'none', clusterScope: 'none' } },
  { path: '/data-services/minio', name: 'DataServicesMinio', component: DataServicesMinio, meta: { title: 'MinIO', hostScope: 'none', clusterScope: 'none' } },
  { path: '/data-services/mongodb', name: 'DataServicesMongoDB', component: DataServicesMongoDB, meta: { title: 'MongoDB', hostScope: 'none', clusterScope: 'none' } },
  { path: '/kubernetes/overview', name: 'KubernetesOverview', component: KubernetesOverview, meta: { title: '集群概览', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/workloads', name: 'KubernetesWorkloads', component: KubernetesWorkloads, meta: { title: '工作负载', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/pods', name: 'KubernetesPods', component: KubernetesPods, meta: { title: 'Pod', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/network', name: 'KubernetesNetwork', component: KubernetesNetwork, meta: { title: '网络与入口', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/storage', name: 'KubernetesStorage', component: KubernetesStorage, meta: { title: '存储', hostScope: 'none', clusterScope: 'required' } },
  { path: '/kubernetes/operations', name: 'KubernetesOperations', component: KubernetesOperations, meta: { title: '任务与事件', hostScope: 'none', clusterScope: 'required' } },
  { path: '/preview/k3s-console', name: 'K3sConsolePreview', component: K3sConsolePreview, meta: { title: 'K3s 控制台预览', standalone: true, hostScope: 'none', clusterScope: 'none' } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
