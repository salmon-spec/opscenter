<template>
  <div class="view log-center">
    <div class="view-head"><div><h1 class="view-title">日志中心</h1><div class="view-sub">{{ currentHost?.name || '未选择主机' }} · systemd 与 Docker 日志长期检索</div></div><div class="status"><span class="dot" :class="status.ready?'ok':'warn'"></span>{{ statusText }}</div></div>
    <div v-if="error" class="notice error">{{ error }}</div>
    <section class="card toolbar">
      <div class="range-row"><b>时间段</b><button v-for="item in ranges" :key="item.key" class="range-btn" :class="{active:rangeKey===item.key}" @click="selectRange(item.key)">{{ item.label }}</button></div>
      <div v-if="rangeKey==='custom'" class="custom"><input v-model="customStart" type="datetime-local" /><span>至</span><input v-model="customEnd" type="datetime-local" /></div>
      <div class="filters"><select v-model="source"><option value="all">全部来源</option><option value="journal">systemd / journal</option><option value="docker">Docker 容器</option></select><input v-model.trim="service" maxlength="160" placeholder="服务名（精确，可选）" @keyup.enter="loadLogs" /><input v-model="search" maxlength="256" placeholder="日志关键字" @keyup.enter="loadLogs" /><select v-model.number="limit"><option :value="200">200 条</option><option :value="500">500 条</option><option :value="1000">1000 条</option><option :value="5000">5000 条</option></select><button class="btn" :disabled="loading||!status.ready" @click="loadLogs">{{ loading?'查询中…':'查询' }}</button></div>
    </section>
    <section class="card results">
      <div class="result-head"><div><h3>日志记录</h3><span class="muted">{{ entries.length }} 条 · 保留 {{ status.retention_days || 365 }} 天 · 不自动刷新</span></div><button class="btn btn-sm" :disabled="!entries.length" @click="exportText">导出</button></div>
      <div v-if="loading" class="empty">正在检索日志…</div>
      <div v-else-if="!entries.length" class="empty">{{ status.ready?'当前条件暂无日志':'请先部署或检查 Loki / Alloy' }}</div>
      <div v-else class="log-list"><div v-for="(entry,index) in entries" :key="`${entry.timestamp_ns}-${index}`" class="log-line"><time>{{ formatTime(entry.timestamp_ns) }}</time><span class="source" :class="entry.labels?.source">{{ entry.labels?.source || '-' }}</span><span class="service">{{ entry.labels?.service_name || entry.labels?.unit || entry.labels?.container || '-' }}</span><pre>{{ entry.line }}</pre></div></div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { useHostContext } from '../hostContext'

const { selectedHostId, currentHost, refreshHosts }=useHostContext()
const status=reactive({configured:false,ready:false,retention_days:365,message:''}),entries=ref([]),loading=ref(false),error=ref('')
const source=ref('all'),service=ref(''),search=ref(''),limit=ref(500),rangeKey=ref('1h'),customStart=ref(''),customEnd=ref('')
const ranges=[{key:'15m',label:'15分钟',minutes:15},{key:'1h',label:'1小时',minutes:60},{key:'6h',label:'6小时',minutes:360},{key:'24h',label:'24小时',minutes:1440},{key:'7d',label:'7天',minutes:10080},{key:'30d',label:'30天',minutes:43200},{key:'custom',label:'自定义'}]
const statusText=computed(()=>!status.configured?'未配置 Loki':status.ready?'日志存储正常':'Loki 不可用')
let controller=null
function localInput(value){const shifted=new Date(value.getTime()-value.getTimezoneOffset()*60000);return shifted.toISOString().slice(0,16)}
function range(){const end=rangeKey.value==='custom'&&customEnd.value?new Date(customEnd.value):new Date();const meta=ranges.find(item=>item.key===rangeKey.value);const start=rangeKey.value==='custom'&&customStart.value?new Date(customStart.value):new Date(end.getTime()-(meta?.minutes||60)*60000);if(start>=end)throw new Error('开始时间必须早于结束时间');return{start,end}}
function selectRange(key){rangeKey.value=key;if(key==='custom'){const end=new Date(),start=new Date(end.getTime()-3600000);customStart.value=localInput(start);customEnd.value=localInput(end)}}
async function loadStatus(){try{Object.assign(status,await api.get('/logs/status'))}catch(e){error.value=e.message}}
async function loadLogs(){if(!selectedHostId.value||!status.ready)return;controller?.abort();controller=new AbortController();const active=controller;loading.value=true;error.value='';try{const {start,end}=range();const data=await api.get(`/servers/${selectedHostId.value}/logs/query`,{start:start.toISOString(),end:end.toISOString(),source:source.value,service:service.value,search:search.value,limit:limit.value,direction:'backward'},{signal:active.signal});entries.value=data.entries||[]}catch(e){if(e.name!=='AbortError')error.value=e.message}finally{if(controller===active)loading.value=false}}
function formatTime(ns){const millis=Number(BigInt(ns)/1000000n);return new Date(millis).toLocaleString('zh-CN',{hour12:false})}
function exportText(){const content=entries.value.map(item=>`${formatTime(item.timestamp_ns)} [${item.labels?.source||'-'}] [${item.labels?.service_name||'-'}] ${item.line}`).join('\r\n');const url=URL.createObjectURL(new Blob([content],{type:'text/plain;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=`${currentHost.value?.name||'host'}-logs-${rangeKey.value}.log`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
watch(selectedHostId,()=>{controller?.abort();entries.value=[];if(status.ready)loadLogs()})
onMounted(async()=>{await refreshHosts();await loadStatus();if(status.ready)loadLogs()})
onUnmounted(()=>controller?.abort())
</script>

<style scoped>
.status{display:flex;align-items:center;gap:7px}.dot{width:8px;height:8px;border-radius:50%}.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}.notice{padding:10px 14px;border-radius:8px;margin-bottom:12px}.error{background:#fef2f2;color:var(--err)}.toolbar{padding:15px;margin-bottom:14px}.range-row,.custom,.filters{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.range-row b{font-size:13px}.range-btn{border:1px solid var(--border);background:var(--card);color:var(--muted);border-radius:999px;padding:5px 11px;cursor:pointer}.range-btn.active{background:var(--brand);border-color:var(--brand);color:white}.custom{margin-top:10px}.custom input,.filters input,.filters select{border:1px solid var(--border);border-radius:7px;padding:8px 10px;background:var(--card);color:var(--text)}.filters{border-top:1px solid var(--border);padding-top:12px;margin-top:12px}.filters input{min-width:210px}.results{padding:0;overflow:hidden}.result-head{display:flex;align-items:center;justify-content:space-between;padding:15px 17px;border-bottom:1px solid var(--border)}.result-head h3{margin:0 0 3px}.log-list{max-height:calc(100vh - 300px);min-height:300px;overflow:auto;background:#0b1220;color:#dbeafe;font:12px/1.55 Consolas,monospace}.log-line{display:grid;grid-template-columns:165px 64px 180px minmax(400px,1fr);gap:8px;padding:5px 12px;border-bottom:1px solid rgba(148,163,184,.08)}.log-line:hover{background:rgba(59,130,246,.08)}.log-line time{color:#94a3b8}.source{color:#38bdf8}.source.docker{color:#a78bfa}.service{color:#fbbf24;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.log-line pre{margin:0;white-space:pre-wrap;word-break:break-all;font:inherit}.empty{padding:50px;text-align:center;color:var(--muted)}@media(max-width:900px){.log-line{grid-template-columns:145px 58px minmax(300px,1fr)}.service{display:none}.filters input{min-width:160px}}
</style>
