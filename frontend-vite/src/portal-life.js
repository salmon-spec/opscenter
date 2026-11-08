import {createApp} from 'vue'
import {createRouter,createMemoryHistory} from 'vue-router'
import {routes} from './routes'
import PortalNative from './PortalNative.vue'
import {portalReady,openPortalModule} from './portal-api.js'
import catalog from '../../modules/ops_portal/native_catalog.json'
import workbench from './workbench/tabs'
import './styles.css'
const life=['subscriptions','home-inventory','life-reminders']
const router=createRouter({history:createMemoryHistory(),routes:routes.filter(r=>r.name!=='ExtensionHost')})
portalReady.then(async module=>{
 const entry=catalog.find(x=>x.key===module.key)
 const start=entry?.route||(life.includes(module.key)?'/extensions/'+module.key:null)
 if(!start)return
 workbench.init(router)
 await router.push(start);await router.isReady()
 router.beforeEach(to=>{
  const target=catalog.find(x=>x.route===to.path||(x.key==='core-kubernetes'&&to.path.startsWith('/kubernetes/')))
  const key=target?.key||(to.path.startsWith('/extensions/')?to.path.split('/').at(-1):null)
  if(key&&key!==module.key){openPortalModule(key);return false}
 })
 createApp(PortalNative).use(router).mount('#app')
})
