<template>
  <div class="view terminal-page">
    <div class="view-head"><div><h1 class="view-title">终端</h1><div class="view-sub">{{ currentHost?.name || '未选择主机' }} · SSH 会话</div></div><button v-if="sessionId" class="btn" @click="createTerminal">重新建立会话</button></div>
    <div v-if="error" class="card connect"><p>{{ error }}</p><button class="btn btn-primary" @click="createTerminal">重试连接</button></div>
    <div v-else-if="loading" class="card loading"><span class="spinner"></span>正在建立 SSH 会话…</div>
    <TerminalPanel v-else-if="sessionId" :key="sessionId" embedded :session-id="sessionId" :title="currentHost?.name" />
    <div v-else class="card connect"><p>选择主机后建立安全终端会话。</p><button class="btn btn-primary" :disabled="!selectedHostId" @click="createTerminal">连接终端</button></div>
  </div>
</template>
<script setup>
import { onMounted, ref, watch } from 'vue'
import { api } from '../api'
import TerminalPanel from '../components/TerminalPanel.vue'
import { useHostContext } from '../hostContext'
const {selectedHostId,currentHost,refreshHosts}=useHostContext()
const sessionId=ref(''),loading=ref(false),error=ref('')
async function createTerminal(){if(!selectedHostId.value)return;loading.value=true;error.value='';sessionId.value='';try{const data=await api.post('/terminal/sessions',{server_id:selectedHostId.value});sessionId.value=data.session_id}catch(e){error.value=e.message}finally{loading.value=false}}
watch(selectedHostId,()=>{sessionId.value='';error.value='';createTerminal()})
onMounted(async()=>{await refreshHosts();createTerminal()})
</script>
<style scoped>.terminal-page{height:calc(100vh - 56px);display:flex;flex-direction:column}.terminal-page>.view-head{flex:none}.terminal-page>:last-child{flex:1;min-height:420px}.connect{text-align:center;display:flex;flex-direction:column;align-items:center;justify-content:center}.connect p{color:var(--muted)}</style>
