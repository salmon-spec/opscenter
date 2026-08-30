<template>
  <div class="view monitor-view">
    <div class="view-head">
      <div><h1 class="view-title">系统监控</h1><div class="view-sub">{{ currentHost?.name || '未选择主机' }} · 仅页面可见时每 5 秒刷新</div></div>
      <div class="actions"><span class="muted">更新于 {{ updatedAt }}</span><button class="btn" :disabled="loading" @click="loadSummary(true)">刷新</button></div>
    </div>
    <div v-if="error" class="notice error">{{ error }}</div>
    <section class="card history-controls">
      <div class="range-row"><b>历史时段</b><button v-for="item in ranges" :key="item.key" class="range-btn" :class="{active:rangeKey===item.key}" @click="selectRange(item.key)">{{ item.label }}</button></div>
      <div v-if="rangeKey==='custom'" class="custom-range"><input v-model="customStart" type="datetime-local" /><span>至</span><input v-model="customEnd" type="datetime-local" /><button class="btn btn-sm" @click="loadHistory">查询</button></div>
      <div class="metric-row"><b>指标</b><label v-for="item in metricOptions" :key="item.key"><input type="checkbox" :checked="selectedMetrics.includes(item.key)" @change="toggleMetric(item.key)" />{{ item.label }}</label><span class="muted">{{ historyResolution }} · {{ historyPointCount }} 点</span></div>
    </section>
    <div class="metric-grid">
      <article v-for="item in cards" :key="item.label" class="card metric-card">
        <div class="metric-label">{{ item.label }}</div><strong>{{ item.value }}</strong>
        <div v-if="item.percent!==null" class="bar"><i :style="{width:Math.min(100,item.percent)+'%'}"></i></div>
        <small>{{ item.note }}</small>
      </article>
    </div>
    <div class="chart-grid">
      <section class="card"><h3>主机指标趋势</h3><div v-if="historyLoading" class="chart-loading">正在加载历史数据…</div><div ref="chartEl" class="chart"></div></section>
      <section class="card"><h3>主机信息</h3><dl class="info"><template v-for="row in infoRows" :key="row[0]"><dt>{{ row[0] }}</dt><dd>{{ row[1] }}</dd></template></dl></section>
    </div>
    <section class="card section"><h3>磁盘</h3><table class="table"><thead><tr><th>挂载点</th><th>文件系统</th><th>容量</th><th>已用</th><th>使用率</th></tr></thead><tbody><tr v-for="disk in summary?.disks || []" :key="disk.mountpoint || disk.device"><td>{{ disk.mountpoint || '-' }}</td><td>{{ disk.fstype || disk.device || '-' }}</td><td>{{ fmtBytes(disk.total) }}</td><td>{{ fmtBytes(disk.used) }}</td><td>{{ disk.percent ?? '-' }}%</td></tr></tbody></table><div v-if="!(summary?.disks||[]).length" class="empty">暂无磁盘明细</div></section>
    <section class="card section"><h3>网络接口</h3><table class="table"><thead><tr><th>接口</th><th>地址</th><th>接收</th><th>发送</th></tr></thead><tbody><tr v-for="net in summary?.network_interfaces || []" :key="net.name || net.interface"><td>{{ net.name || net.interface }}</td><td class="mono">{{ net.address || net.ip || '-' }}</td><td>{{ fmtBytes(net.bytes_recv || net.rx_bytes) }}</td><td>{{ fmtBytes(net.bytes_sent || net.tx_bytes) }}</td></tr></tbody></table><div v-if="!(summary?.network_interfaces||[]).length" class="empty">暂无网卡明细</div></section>
    <section class="card section metric-log"><div class="section-head"><div><h3>指标日志</h3><span class="muted">{{ currentHost?.name }} · 按所选时间段与粒度展示</span></div><button class="btn btn-sm" :disabled="!metricRecords.length" @click="exportCsv">导出 CSV</button></div><div class="record-scroll"><table class="table"><thead><tr><th>时间</th><th>指标</th><th>平均值</th><th>最小值</th><th>最大值</th><th>样本数</th></tr></thead><tbody><tr v-for="row in metricRecords" :key="`${row.metric}-${row.timestamp}`"><td>{{ formatRecordTime(row.timestamp) }}</td><td>{{ metricLabel(row.metric) }}</td><td>{{ formatMetricValue(row.metric,row.avg) }}</td><td>{{ formatMetricValue(row.metric,row.min) }}</td><td>{{ formatMetricValue(row.metric,row.max) }}</td><td>{{ row.count }}</td></tr><tr v-if="!metricRecords.length"><td colspan="6" class="empty">当前时段暂无指标记录</td></tr></tbody></table></div></section>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { api, fmtBytes, fmtDuration, fmtTime } from '../api'
import { useHostContext } from '../hostContext'

const { selectedHostId, currentHost, refreshHosts } = useHostContext()
const summary=ref(null),loading=ref(false),error=ref(''),chartEl=ref(null),history=ref({}),historyLoading=ref(false),historyResolution=ref('-'),historyPointCount=ref(0)
const ranges=[{key:'1h',label:'1小时',hours:1},{key:'6h',label:'6小时',hours:6},{key:'24h',label:'24小时',hours:24},{key:'7d',label:'7天',hours:168},{key:'30d',label:'30天',hours:720},{key:'90d',label:'90天',hours:2160},{key:'1y',label:'1年',hours:8760},{key:'custom',label:'自定义'}]
const metricOptions=[{key:'cpu',label:'CPU',color:'#2563eb'},{key:'memory',label:'内存',color:'#7c3aed'},{key:'disk',label:'磁盘',color:'#ea580c'},{key:'load1',label:'负载',color:'#059669'},{key:'net_rx',label:'网络接收',color:'#0891b2'},{key:'net_tx',label:'网络发送',color:'#db2777'}]
const rangeKey=ref('1h'),selectedMetrics=ref(['cpu','memory','disk']),customStart=ref(''),customEnd=ref('')
let timer=null, controller=null, historyController=null, chart=null
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
  historyController?.abort();historyController=new AbortController();const activeController=historyController
  historyLoading.value=true;error.value=''
  try{const end=rangeKey.value==='custom'&&customEnd.value?new Date(customEnd.value):new Date();const selected=ranges.find(item=>item.key===rangeKey.value);const start=rangeKey.value==='custom'&&customStart.value?new Date(customStart.value):new Date(end.getTime()-(selected?.hours||1)*3600000);if(start>=end)throw new Error('开始时间必须早于结束时间');const data=await api.get(`/servers/${selectedHostId.value}/metrics/timeseries`,{metrics:selectedMetrics.value.join(','),start:start.toISOString(),end:end.toISOString(),resolution:'auto'},{signal:activeController.signal});history.value=data.series||{};historyResolution.value=({raw:'原始数据','5m':'5分钟聚合','1h':'1小时聚合'})[data.resolution]||data.resolution;historyPointCount.value=data.point_count||0;renderChart()}catch(e){if(e.name!=='AbortError')error.value=e.message}finally{if(historyController===activeController)historyLoading.value=false}
}
function renderChart(){
  if(!chartEl.value)return;chart ||= echarts.init(chartEl.value)
  const series=selectedMetrics.value.map(key=>{const meta=metricOptions.find(item=>item.key===key);return{name:meta?.label||key,type:'line',showSymbol:false,sampling:'lttb',lineStyle:{width:1.8,color:meta?.color},itemStyle:{color:meta?.color},data:(history.value[key]||[]).map(([t,v])=>[new Date(t*1000),v])}})
  chart.setOption({tooltip:{trigger:'axis'},legend:{data:series.map(item=>item.name)},grid:{left:52,right:18,top:42,bottom:38},dataZoom:[{type:'inside'}],xAxis:{type:'time'},yAxis:{type:'value',scale:true},series},true)
}
const metricRecords=computed(()=>Object.entries(history.value).flatMap(([metric,rows])=>(rows||[]).map(([timestamp,avg,min,max,count])=>({metric,timestamp,avg,min,max,count}))).sort((a,b)=>b.timestamp-a.timestamp).slice(0,1000))
function metricLabel(key){return metricOptions.find(item=>item.key===key)?.label||key}
function formatMetricValue(metric,value){if(value===undefined||value===null)return'-';if(['cpu','memory','disk'].includes(metric))return`${Number(value).toFixed(2)}%`;if(['net_rx','net_tx'].includes(metric))return`${fmtBytes(value)}/s`;return Number(value).toFixed(2)}
function formatRecordTime(value){return new Date(value*1000).toLocaleString('zh-CN',{hour12:false})}
function selectRange(key){rangeKey.value=key;if(key==='custom'){const end=new Date();const start=new Date(end.getTime()-24*3600000);customStart.value=toLocalInput(start);customEnd.value=toLocalInput(end)}else loadHistory()}
function toLocalInput(value){const shifted=new Date(value.getTime()-value.getTimezoneOffset()*60000);return shifted.toISOString().slice(0,16)}
function toggleMetric(key){const values=[...selectedMetrics.value];const index=values.indexOf(key);if(index>=0){if(values.length===1)return;values.splice(index,1)}else if(values.length<6)values.push(key);selectedMetrics.value=values;loadHistory()}
function exportCsv(){const rows=[['时间','主机','指标','平均值','最小值','最大值','样本数'],...metricRecords.value.map(row=>[formatRecordTime(row.timestamp),currentHost.value?.name||'',metricLabel(row.metric),row.avg,row.min,row.max,row.count])];const text='\ufeff'+rows.map(row=>row.map(value=>`"${String(value??'').replaceAll('"','""')}"`).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=`${currentHost.value?.name||'host'}-metrics-${rangeKey.value}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function start(){stop();if(!document.hidden){loadSummary();timer=setInterval(()=>{if(!document.hidden&&!loading.value)loadSummary()},5000)}}
function stop(){if(timer){clearInterval(timer);timer=null}}
function visibility(){document.hidden?stop():start()}
function resizeChart(){chart?.resize()}
watch(selectedHostId,()=>{controller?.abort();historyController?.abort();summary.value=null;history.value={};loadHistory();start()})
onMounted(async()=>{await refreshHosts();await nextTick();loadHistory();start();document.addEventListener('visibilitychange',visibility);window.addEventListener('resize',resizeChart)})
onUnmounted(()=>{stop();controller?.abort();historyController?.abort();chart?.dispose();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('resize',resizeChart)})
</script>

<style scoped>
.actions{display:flex;align-items:center;gap:12px}.notice{padding:10px 14px;border-radius:8px;margin-bottom:12px}.error{background:#fef2f2;color:var(--err)}.history-controls{padding:14px;margin-bottom:14px}.range-row,.metric-row,.custom-range{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.range-row b,.metric-row b{font-size:13px;margin-right:4px}.range-btn{border:1px solid var(--border);background:var(--card);color:var(--muted);border-radius:999px;padding:5px 11px;cursor:pointer}.range-btn.active{background:var(--brand);border-color:var(--brand);color:#fff}.custom-range{margin:10px 0}.custom-range input{border:1px solid var(--border);border-radius:6px;padding:7px;background:var(--card);color:var(--text)}.metric-row{border-top:1px solid var(--border);padding-top:11px;margin-top:11px}.metric-row label{font-size:12px;display:flex;gap:4px}.metric-row .muted{margin-left:auto}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.metric-label{color:var(--muted);font-size:13px}.metric-card strong{display:block;font-size:25px;margin:8px 0}.metric-card small{color:var(--muted)}.bar{height:5px;background:#eef2f7;border-radius:5px;margin-bottom:8px;overflow:hidden}.bar i{display:block;height:100%;background:var(--brand)}.chart-grid{display:grid;grid-template-columns:2fr 1fr;gap:14px;margin-top:14px}.card h3{font-size:15px;margin:0 0 14px}.chart{height:300px}.chart-loading{position:absolute;margin:120px 0 0 40%;color:var(--muted);z-index:1}.info{display:grid;grid-template-columns:100px 1fr;gap:12px;margin:0}.info dt{color:var(--muted)}.info dd{margin:0;word-break:break-all}.section{margin-top:14px;overflow:auto}.section-head{display:flex;align-items:center;justify-content:space-between}.section-head h3{margin-bottom:3px}.record-scroll{max-height:480px;overflow:auto}.metric-log .table{min-width:760px}@media(max-width:900px){.metric-grid{grid-template-columns:repeat(2,1fr)}.chart-grid{grid-template-columns:1fr}.metric-row .muted{margin-left:0;width:100%}}
</style>
