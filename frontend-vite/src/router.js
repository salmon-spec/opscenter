/* OpsCenter v3.29 路由：hash 模式，视图内切换不刷新页面 */
import { createRouter, createWebHashHistory } from 'vue-router'
import ServicePlaza from './views/ServicePlaza.vue'
import Assets from './views/Assets.vue'
import Screen from './views/Screen.vue'
import Topology from './views/Topology.vue'
import Alerts from './views/Alerts.vue'
import ApiKeys from './views/ApiKeys.vue'
import SwitchAccount from './views/SwitchAccount.vue'

const routes = [
  { path: '/', component: ServicePlaza, meta: { title: '服务广场' } },
  { path: '/assets', component: Assets, meta: { title: '资产管理' } },
  { path: '/screen', component: Screen, meta: { title: '监控大屏' } },
  { path: '/screen-standalone', component: Screen, meta: { title: '监控大屏', standalone: true } },
  { path: '/topology', component: Topology, meta: { title: '拓扑架构' } },
  { path: '/alerts', component: Alerts, meta: { title: '告警中心' } },
  { path: '/api-keys', component: ApiKeys, meta: { title: '开放 API' } },
  { path: '/switch-account', component: SwitchAccount, meta: { title: '切换统一账号' } },
]

export default createRouter({
  history: createWebHashHistory(),
  routes,
})
