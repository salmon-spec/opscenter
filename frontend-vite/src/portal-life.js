import {createApp, h, ref} from 'vue'
import {createRouter,createMemoryHistory,RouterView} from 'vue-router'
import LifeModule from './views/LifeModule.vue'
import {portalReady,openPortalModule} from './portal-api.js'
import './styles.css'
const keys=['subscriptions','home-inventory','life-reminders'];
const router=createRouter({history:createMemoryHistory(),routes:keys.map(kind=>({path:'/extensions/'+kind,component:LifeModule,props:{kind}}))});
portalReady.then(async module=>{
 if(!keys.includes(module.key))return;
 await router.push('/extensions/'+module.key);await router.isReady();
 router.beforeEach(to=>{const key=to.path.split('/').at(-1);if(key!==module.key){if(keys.includes(key))openPortalModule(key);return false;}});
 createApp({setup(){const notice=ref('');window.addEventListener('ops-toast',e=>{notice.value=e.detail.msg;setTimeout(()=>notice.value='',5000)});return()=>h('div',{class:'portal-native'},[h(RouterView),notice.value?h('div',{role:'status',class:'portal-notice'},notice.value):null]);}}).use(router).mount('#app');
});
