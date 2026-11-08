<template>
 <main class="portal-native" :class="{'native-terminal':route.name==='SystemTerminal'}">
  <nav v-if="functionGroups.length" data-native-functions aria-label="模块功能">
   <div v-for="group in functionGroups" :key="group.label" :data-function-group="group.label"><button v-for="item in group.items" :key="item.path||item.selector" :class="{active:functionActive(item)}" @click="selectFunction(item)">{{ item.label }}</button></div>
  </nav>
  <div v-if="hostRequired || clusterRequired" class="native-context">
   <label v-if="hostRequired">目标主机 <select :value="selectedHostId" aria-label="选择目标主机" @change="selectHost($event.target.value)"><option v-if="!hosts.length" value="">暂无主机</option><option v-for="host in hosts" :key="host.id" :value="host.id">{{ host.name }} · {{ host.lan_ip || host.host }}</option></select></label>
   <ClusterSelector v-if="clusterRequired" />
   <span v-if="error" role="status">{{ error }}</span><button v-if="error" class="btn btn-sm" @click="loadHosts">重试</button>
  </div>
  <RouterView v-slot="{Component}"><KeepAlive><component :is="Component" :key="route.name" /></KeepAlive></RouterView>
  <div v-if="notice" role="status" class="portal-notice">{{ notice }}</div>
 </main>
</template>
<script setup>
import {computed,onMounted,onBeforeUnmount,ref} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {useHostContext} from './hostContext'
import {openPortalApp,portalReady} from './portal-api'
import ClusterSelector from './components/workbench/ClusterSelector.vue'
import {moduleNavigation} from './workbench/moduleNavigation'
const route=useRoute(),router=useRouter(), notice=ref(''),error=ref(''),moduleKey=ref(''),section=ref('')
portalReady.then(module=>{moduleKey.value=module.key})
const {hosts,selectedHostId,selectHost,refreshHosts}=useHostContext()
const hostRequired=computed(()=>route.meta.hostScope==='required'||route.name==='LogCenter')
const clusterRequired=computed(()=>route.meta.clusterScope==='required')
const functionGroups=computed(()=>{
 const group=moduleNavigation.find(item=>item.prefix?route.path.startsWith(item.prefix):item.paths.some(path=>route.path===path||route.path.startsWith(path)))
 if(group)return group.groups
 const definitions={
  'core-overview':[{label:'值班首页',path:'/'}],
  'core-hosts':[{label:'主机列表',path:'/hosts'}],
  'core-logs':[{label:'日志检索',selector:'.log-center .toolbar'},{label:'采集状态',selector:'.collector-card'},{label:'存储健康',selector:'.loki-health'}],
  'core-screen':[{label:'监控大屏',path:'/screen'},{label:'我的大屏',path:'/screen?edit=1'},{label:'模板与主题',path:'/screen?edit=1&panel=templates'}],
  'core-modules':[{label:'模块目录',path:'/modules'}],
 }
 const items=definitions[moduleKey.value];return items?[{label:'功能导航',items}]:[]
})
function functionActive(item){return item.path?router.resolve(item.path).fullPath===route.fullPath:section.value===item.selector||(!section.value&&item===functionGroups.value[0]?.items[0])}
function selectFunction(item){if(item.path)router.push(item.path);else{section.value=item.selector;document.querySelector(item.selector)?.scrollIntoView({behavior:'smooth',block:'start'})}}
let timer
function toast(e){notice.value=e.detail.msg;clearTimeout(timer);timer=setTimeout(()=>notice.value='',5000)}
function openExternal(event){const link=event.target.closest?.('a[target="_blank"]');if(!link||event.defaultPrevented)return;const url=new URL(link.href);if(['http:','https:'].includes(url.protocol)&&url.origin!==location.origin){event.preventDefault();openPortalApp(url.href)}}
async function loadHosts(){error.value='';try{await refreshHosts(true)}catch(e){error.value=e.message}}
onMounted(()=>{window.addEventListener('ops-toast',toast);document.addEventListener('click',openExternal,true);if(hostRequired.value)loadHosts()})
onBeforeUnmount(()=>{window.removeEventListener('ops-toast',toast);document.removeEventListener('click',openExternal,true);clearTimeout(timer)})
</script>
<style>
[data-native-functions]{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}[data-native-functions]>div{display:flex;gap:4px;flex-wrap:wrap}[data-native-functions] button{border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text);padding:6px 10px}
html,body,#app{min-height:100%;margin:0}.portal-native{padding:12px;min-width:0}.native-context{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px;font-size:12px}.native-context label{display:flex;align-items:center;gap:8px}.native-context select{max-width:min(400px,65vw);padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text)}.native-sections{display:flex;gap:4px;overflow:auto;border-bottom:1px solid var(--border);margin-bottom:12px}.native-sections a{white-space:nowrap;padding:8px 14px;text-decoration:none;color:var(--muted);font-size:12px}.native-sections .router-link-active{color:var(--primary);border-bottom:2px solid var(--primary)}.portal-notice{position:fixed;bottom:24px;left:15%;max-width:70%;background:#18334b;color:white;padding:12px 24px;border-radius:8px;z-index:9999}

.portal-native.native-terminal{height:100dvh;box-sizing:border-box;display:flex;flex-direction:column}.portal-native.native-terminal .terminal-page{display:flex;flex-direction:column;flex:1;min-height:0}.portal-native .terminal-page .terminal-sidebar{flex-direction:row;flex:none;gap:14px;align-items:flex-start;border-right:0;border-bottom:1px solid var(--border);padding:0 8px 8px}.portal-native .terminal-sidebar .hosts-section{width:230px;max-width:45%;padding:4px 0 0}.portal-native .terminal-sidebar .sessions-section{padding:4px 0 0;min-width:0}.portal-native .terminal-sidebar .session-list{flex-direction:row;overflow:auto}.portal-native .terminal-sidebar .session-item{flex:none;min-width:120px;max-width:220px}.portal-native .terminal-sidebar .sidebar-footnote{display:none}.portal-native .terminal-page .terminal-main{flex:1}.portal-native .db-view .layout{grid-template-columns:minmax(0,1fr)}.portal-native .db-view .instances{display:flex;align-items:center;gap:8px;overflow:auto}.portal-native .db-view .instance-head{flex:none;gap:8px}.portal-native .db-view .instance{flex:none;min-width:180px;width:auto}
</style>
