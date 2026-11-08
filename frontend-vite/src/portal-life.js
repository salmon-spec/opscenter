import {createApp} from 'vue'
import {createRouter,createMemoryHistory} from 'vue-router'
import {routes} from './routes'
import PortalNative from './PortalNative.vue'
import {portalReady,openPortalModule} from './portal-api.js'
import catalog from '../../modules/ops_portal/native_catalog.json'
import workbench from './workbench/tabs'
import {portalModuleForRoute} from './portal-navigation'
import './styles.css'
const life=['subscriptions','home-inventory','life-reminders']
const router=createRouter({history:createMemoryHistory(),routes:routes.filter(r=>r.name!=='ExtensionHost')})
portalReady.then(async module=>{
 const entry=catalog.find(x=>x.key===module.key)
 const validView=view=>typeof view==='string'&&view.startsWith('/')&&!view.startsWith('//')&&portalModuleForRoute(view.split('?')[0],catalog)===module.key
 const start=validView(module.view)?module.view:entry?.route||(life.includes(module.key)?'/extensions/'+module.key:null)
 if(!start)return
 workbench.init(router)
 await router.push(start);await router.isReady()
 router.beforeEach(to=>{
  const key=portalModuleForRoute(to.path,catalog)
  if(key&&key!==module.key){openPortalModule(key,to.fullPath);return false}
 })
 window.addEventListener('ops-portal-view',event=>{if(validView(event.detail))router.push(event.detail)})
 createApp(PortalNative).use(router).mount('#app')
})
