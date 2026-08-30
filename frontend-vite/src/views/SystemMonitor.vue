<template>
  <div class="view monitor-view">
    <div class="view-head">
      <div><h1 class="view-title">系统监控</h1><div class="view-sub">{{ currentHost?.name || '未选择主机' }} · 仅页面可见时每 5 秒刷新</div></div>
      <div class="actions"><span class="muted">更新于 {{ updatedAt }}</span><button class="btn" :disabled="loading" @click="loadSummary(true)">刷新</button></div>
    </div>
    <div v-if="error" class="notice error">{{ error }}</div>
    <div class="metric-grid">
      <article v-for="item in cards" :key="item.label" class="card metric-card">
        <div class="metric-label">{{ item.label }}</div><strong>{{ item.value }}</strong>
        <div v-if="item.percent!==null" class="bar"><i :style="{width:Math.min(100,item.percent)+'%'}"></i></div>
        <small>{{ item.note }}</small>
      </article>
    </div>
    <div class="chart-grid">
      <section class="card"><h3>CPU / 内存趋势</h3><div ref="chartEl" class="chart"></div></section>
      <section class="card"><h3>主机信息</h3><dl class="info"><template v-for="row in infoRows" :key="row[0]"><dt>{{ row[0] }}</dt><dd>{{ row[1] }}</dd></template></dl></section>
    </div>
    <section class="card section"><h3>磁盘</h3><table class="table"><thead><tr><th>挂载点</th><th>文件系统</th><th>容量</th><th>已用</th><th>使用率</th></tr></thead><tbody><tr v-for="disk in summary?.disks || []" :key="disk.mountpoint || disk.device"><td>{{ disk.mountpoint || '-' }}</td><td>{{ disk.fstype || disk.device || '-' }}</td><td>{{ fmtBytes(disk.total) }}</td><td>{{ fmtBytes(disk.used) }}</td><td>{{ disk.percent ?? '-' }}%</td></tr></tbody></table><div v-if="!(summary?.disks||[]).length" class="empty">暂无磁盘明细</div></section>
    <section class="card section"><h3>网络接口</h3><table class="table"><thead><tr><th>接口</th><th>地址</th><th>接收</th><th>发送</th></tr></thead><tbody><tr v-for="net in summary?.network_interfaces || []" :key="net.name || net.interface"><td>{{ net.name || net.interface }}</td><td class="mono">{{ net.address || net.ip || '-' }}</td><td>{{ fmtBytes(net.bytes_recv || net.rx_bytes) }}</td><td>{{ fmtBytes(net.bytes_sent || net.tx_bytes) }}</td></tr></tbody></table><div v-if="!(summary?.network_interfaces||[]).length" class="empty">暂无网卡明细</div></section>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { api, fmtBytes, fmtDuration, fmtTime } from '../api'
import { useHostContext } from '../hostContext'

const { selectedHostId, currentHost, refreshHosts } = useHostContext()
const summary=ref(null),loading=ref(false),error=ref(''),chartEl=ref(null),history=ref({cpu:[],memory:[]})
let timer=null, controller=null, chart=null
const m=computed(()=>summary.value?.metrics||{})
const cards=computed(()=>[
  {label:'CPU',value:`${Number(m.value.cpu||0).toFixed(1)}%`,percent:Number(m.value.cpu||0),note:`负载 ${m.value.load1||0} / ${m.value.load5||0} / ${m.value.load15||0}`},
  {label:'内存',value:`${Number(m.value.memory||0).toFixed(1)}%`,percent:Number(m.value.memory||0),note:`${fmtBytes(m.value.memory_used)} / ${fmtBytes(m.value.memory_total)}`},
  {label:'磁盘',value:`${Number(m.value.disk||0).toFixed(1)}%`,percent:Number(m.value.disk||0),note:`${fmtBytes(m.value.disk_used)} / ${fmtBytes(m.value.disk_total)}`},
  {label:'网络',value:`↓ ${fmtBytes(m.value.net_rx)}/s`,percent:null,note:`↑ ${fmtBytes(m.value.net_tx)}/s`},
])
const updatedAt=computed(()=>summary.value?new Date((summary.value.timestamp||0)*1000).toLocaleTimeString():'-')
const infoRows=computed(()=>[['主机名',summary.value?.hostname||currentHost.value?.name||'-'],['平台',summary.value?.platform||'-'],['内核',summary.value?.kernel||'-'],['CPU 核心',m.value.cpu_count||'-'],['运行时间',fmtDuration(m.value.uptime)],['数据来源',summary.value?.source||'-'],['接口耗时',summary.value?.duration_ms!==undefined?`${Number(summary.value.duration_ms).toFixed(0)} ms${summary.value.cached?`（缓存 ${Number(summary.value.cache_age_seconds||0).toFixed(1)}s）`:''}`:'-'],['Agent',summary.value?.agent_version||currentHost.value?.agent_version||'-']])

async function loadSummary(refresh=false){
  if(!selectedHostId.value||loading.value)return
  controller?.abort();controller=new AbortController();loading.value=true;error.value=''
  try{summary.value=await api.get(`/servers/${selectedHostId.value}/system/summary`,{refresh},{signal:controller.signal})}
  catch(e){if(e.name!=='AbortError')error.value=e.message}finally{loading.value=false}
}
async function loadHistory(){
  if(!selectedHostId.value)return
  try{const [cpu,memory]=await Promise.all(['cpu','memory'].map(metric=>api.get(`/servers/${selectedHostId.value}/history`,{metric,hours:1})));history.value={cpu:cpu.values||[],memory:memory.values||[]};renderChart()}catch{/* 历史数据不可用不影响实时监控 */}
}
function renderChart(){
  if(!chartEl.value)return;chart ||= echarts.init(chartEl.value)
  const series=(rows)=>rows.map(([t,v])=>[new Date(t*1000),v])
  chart.setOption({tooltip:{trigger:'axis'},legend:{data:['CPU','内存']},grid:{left:44,right:16,top:36,bottom:30},xAxis:{type:'time'},yAxis:{type:'value',min:0,max:100,axisLabel:{formatter:'{value}%'}},series:[{name:'CPU',type:'line',showSymbol:false,data:series(history.value.cpu)},{name:'内存',type:'line',showSymbol:false,data:series(history.value.memory)}]})
}
function start(){stop();if(!document.hidden){loadSummary();timer=setInterval(()=>{if(!document.hidden&&!loading.value)loadSummary()},5000)}}
function stop(){if(timer){clearInterval(timer);timer=null}}
function visibility(){document.hidden?stop():start()}
function resizeChart(){chart?.resize()}
watch(selectedHostId,()=>{controller?.abort();summary.value=null;loadHistory();start()})
onMounted(async()=>{await refreshHosts();await nextTick();loadHistory();start();document.addEventListener('visibilitychange',visibility);window.addEventListener('resize',resizeChart)})
onUnmounted(()=>{stop();controller?.abort();chart?.dispose();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('resize',resizeChart)})
</script>

<style scoped>
.actions{display:flex;align-items:center;gap:12px}.notice{padding:10px 14px;border-radius:8px;margin-bottom:12px}.error{background:#fef2f2;color:var(--err)}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.metric-label{color:var(--muted);font-size:13px}.metric-card strong{display:block;font-size:25px;margin:8px 0}.metric-card small{color:var(--muted)}.bar{height:5px;background:#eef2f7;border-radius:5px;margin-bottom:8px;overflow:hidden}.bar i{display:block;height:100%;background:var(--brand)}.chart-grid{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:14px}.card h3{font-size:15px;margin:0 0 14px}.chart{height:270px}.info{display:grid;grid-template-columns:100px 1fr;gap:12px;margin:0}.info dt{color:var(--muted)}.info dd{margin:0;word-break:break-all}.section{margin-top:14px;overflow:auto}@media(max-width:900px){.metric-grid{grid-template-columns:repeat(2,1fr)}.chart-grid{grid-template-columns:1fr}}
</style>
