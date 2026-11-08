<template>
 <main class="portal-native">
  <div v-if="hostRequired || clusterRequired" class="native-context">
   <label v-if="hostRequired">目标主机 <select :value="selectedHostId" aria-label="选择目标主机" @change="selectHost($event.target.value)"><option v-if="!hosts.length" value="">暂无主机</option><option v-for="host in hosts" :key="host.id" :value="host.id">{{ host.name }} · {{ host.lan_ip || host.host }}</option></select></label>
   <ClusterSelector v-if="clusterRequired" />
   <span v-if="error" role="status">{{ error }}</span><button v-if="error" class="btn btn-sm" @click="loadHosts">重试</button>
  </div>
  <nav v-if="clusterRequired" class="native-sections" aria-label="集群功能"><router-link v-for="item in clusterPages" :key="item.path" :to="item.path">{{ item.meta.title }}</router-link></nav>
  <RouterView />
  <div v-if="notice" role="status" class="portal-notice">{{ notice }}</div>
 </main>
</template>
<script setup>
import {computed,onMounted,onBeforeUnmount,ref} from 'vue'
import {useRoute} from 'vue-router'
import {useHostContext} from './hostContext'
import ClusterSelector from './components/workbench/ClusterSelector.vue'
import {routes} from './routes'
const route=useRoute(), notice=ref(''),error=ref('')
const {hosts,selectedHostId,selectHost,refreshHosts}=useHostContext()
const hostRequired=computed(()=>route.meta.hostScope==='required')
const clusterRequired=computed(()=>route.meta.clusterScope==='required')
const clusterPages=routes.filter(r=>r.path.startsWith('/kubernetes/'))
let timer
function toast(e){notice.value=e.detail.msg;clearTimeout(timer);timer=setTimeout(()=>notice.value='',5000)}
async function loadHosts(){error.value='';try{await refreshHosts(true)}catch(e){error.value=e.message}}
onMounted(()=>{window.addEventListener('ops-toast',toast);if(hostRequired.value)loadHosts()})
onBeforeUnmount(()=>{window.removeEventListener('ops-toast',toast);clearTimeout(timer)})
</script>
<style>
html,body,#app{min-height:100%;margin:0}.portal-native{padding:12px;min-width:0}.native-context{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:10px;font-size:12px}.native-context label{display:flex;align-items:center;gap:8px}.native-context select{max-width:min(400px,65vw);padding:6px 10px;border:1px solid var(--border);border-radius:6px;background:var(--card);color:var(--text)}.native-sections{display:flex;gap:4px;overflow:auto;border-bottom:1px solid var(--border);margin-bottom:12px}.native-sections a{white-space:nowrap;padding:8px 14px;text-decoration:none;color:var(--muted);font-size:12px}.native-sections .router-link-active{color:var(--primary);border-bottom:2px solid var(--primary)}.portal-notice{position:fixed;bottom:24px;left:15%;max-width:70%;background:#18334b;color:white;padding:12px 24px;border-radius:8px;z-index:9999}
</style>
