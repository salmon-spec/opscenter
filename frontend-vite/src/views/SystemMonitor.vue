<template>
  <div class="view monitor-view">
    <div v-if="error" class="notice error">{{ error }}</div>
    <div v-if="summary?.stale" class="notice stale">Agent 暂时无响应，正在显示 {{ Number(summary.cache_age_seconds || 0).toFixed(0) }} 秒前的最近数据。</div>
    <section ref="overviewEl" class="monitor-overview" aria-label="主机指标监控">
    <div class="metric-grid">
      <article v-for="item in cards" :key="item.label" class="card metric-card">
        <div class="metric-label">{{ item.label }}</div><strong>{{ item.value }}</strong>
        <div v-if="item.percent!==null" class="bar"><i :style="{width:Math.min(100,item.percent)+'%'}"></i></div>
        <small>{{ item.note }}</small>
      </article>
    </div>
      <section class="card trend-card">
        <div class="trend-toolbar">
          <div class="trend-title"><h3>主机指标监控</h3><small>{{ currentHost?.name || '未选择主机' }} · 更新于 {{ updatedAt }}</small></div>
          <div class="range-row" aria-label="历史时段"><span>历史时段</span><button v-for="item in ranges" :key="item.key" class="range-btn" :class="{active:rangeKey===item.key}" @click="selectRange(item.key)">{{ item.label }}</button></div>
          <button class="btn btn-sm" :disabled="loading || historyLoading" @click="refresh">刷新</button>
        </div>
        <div v-if="rangeKey==='custom'" class="custom-range"><input v-model="customStart" aria-label="开始时间" type="datetime-local" /><span>至</span><input v-model="customEnd" aria-label="结束时间" type="datetime-local" /><button class="btn btn-sm" @click="loadHistory">查询</button></div>
        <div class="metric-row"><span>指标</span><label v-for="item in metricOptions" :key="item.key"><input type="checkbox" :checked="selectedMetrics.includes(item.key)" @change="toggleMetric(item.key)" />{{ item.label }}</label><span class="muted">{{ historyResolution }} · {{ historyPointCount }} 点</span></div>
        <div class="chart-body"><div v-if="historyLoading" class="chart-loading">正在加载历史数据…</div><div ref="chartEl" class="chart" aria-label="主机指标趋势图"></div></div>
        <div class="trend-note">{{ displayNote }} · 实时指标每 5 秒、趋势每分钟刷新</div>
      </section>
    </section>
    <section class="card section"><h3>主机信息</h3><dl class="info"><template v-for="row in infoRows" :key="row[0]"><dt>{{ row[0] }}</dt><dd>{{ row[1] }}</dd></template></dl></section>
    <section class="card section"><h3>磁盘</h3><table class="table"><thead><tr><th>挂载点</th><th>文件系统</th><th>容量</th><th>已用</th><th>使用率</th></tr></thead><tbody><tr v-for="disk in summary?.disks || []" :key="disk.mountpoint || disk.device"><td>{{ disk.mountpoint || '-' }}</td><td>{{ disk.fstype || disk.device || '-' }}</td><td>{{ fmtBytes(disk.total) }}</td><td>{{ fmtBytes(disk.used) }}</td><td>{{ disk.percent ?? '-' }}%</td></tr></tbody></table><div v-if="!(summary?.disks||[]).length" class="empty">暂无磁盘明细</div></section>
    <section class="card section"><h3>网络接口</h3><table class="table"><thead><tr><th>接口</th><th>地址</th><th>接收</th><th>发送</th></tr></thead><tbody><tr v-for="net in summary?.network_interfaces || []" :key="net.name || net.interface"><td>{{ net.name || net.interface }}</td><td class="mono">{{ net.address || net.ip || '-' }}</td><td>{{ fmtBytes(net.bytes_recv || net.rx_bytes) }}</td><td>{{ fmtBytes(net.bytes_sent || net.tx_bytes) }}</td></tr></tbody></table><div v-if="!(summary?.network_interfaces||[]).length" class="empty">暂无网卡明细</div></section>
    <section class="card section host-overview"><div class="section-head"><div><h3>各主机指标概览</h3><span class="muted">{{ historyResolution }} · 与当前历史时段一致 · 点击主机切换查看</span></div><span v-if="overviewLoading" class="muted">统计中…</span></div><div class="record-scroll"><table class="table"><thead><tr><th>主机</th><th>状态</th><th>CPU（最新 / 平均 / 峰值）</th><th>内存（最新 / 平均 / 峰值）</th><th>磁盘（最新 / 平均 / 峰值）</th><th>样本数</th><th>最后数据</th></tr></thead><tbody><tr v-for="row in hostOverview" :key="row.server_id" :class="{selected:row.server_id===selectedHostId}" @click="selectHost(row.server_id)"><td><button class="host-link">{{ row.name }}</button><small class="mono">{{ row.host }}</small></td><td><span class="host-state" :class="row.status==='online'?'online':'offline'">{{ row.status==='online'?'在线':'离线' }}</span></td><td>{{ overviewMetric(row,'cpu') }}</td><td>{{ overviewMetric(row,'memory') }}</td><td>{{ overviewMetric(row,'disk') }}</td><td>{{ overviewSamples(row) }}</td><td>{{ overviewLastAt(row) }}</td></tr><tr v-if="!hostOverview.length&&!overviewLoading"><td colspan="7" class="empty">暂无主机数据</td></tr></tbody></table></div></section>
    <section class="card section metric-log"><div class="section-head"><div><h3>指标日志</h3><span class="muted">{{ currentHost?.name }} · 页面显示最近 100 条，CSV 导出最近 1000 条</span></div><button class="btn btn-sm" :disabled="!metricRecords.length" @click="exportCsv">导出 CSV</button></div><div class="record-scroll"><table class="table"><thead><tr><th>时间</th><th>指标</th><th>平均值</th><th>最小值</th><th>最大值</th><th>样本数</th></tr></thead><tbody><tr v-for="row in visibleMetricRecords" :key="`${row.metric}-${row.timestamp}`"><td>{{ formatRecordTime(row.timestamp) }}</td><td>{{ metricLabel(row.metric) }}</td><td>{{ formatMetricValue(row.metric,row.avg) }}</td><td>{{ formatMetricValue(row.metric,row.min) }}</td><td>{{ formatMetricValue(row.metric,row.max) }}</td><td>{{ row.count }}</td></tr><tr v-if="!metricRecords.length"><td colspan="6" class="empty">当前时段暂无指标记录</td></tr></tbody></table></div></section>
  </div>
</template>

<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, onUnmounted, ref, watch } from 'vue'
import echarts from '../utils/echarts'
import { trendInterval, trendPoints, intervalLabel } from '../utils/monitorTrend'
import { api, fmtBytes, fmtDuration, fmtTime } from '../api'
import { useHostContext } from '../hostContext'
import { useTabActive } from '../workbench/tabs'

const { selectedHostId, currentHost, refreshHosts, selectHost } = useHostContext()
const { isActive } = useTabActive('SystemMonitor')
const summary=ref(null),loading=ref(false),error=ref(''),chartEl=ref(null),history=ref({}),historyLoading=ref(false),historyResolution=ref('-'),historyPointCount=ref(0)
const hostOverview=ref([]),overviewLoading=ref(false),historyWindow=ref(null),overviewEl=ref(null)
const displayStep=computed(()=>trendInterval(historyWindow.value?(historyWindow.value.end-historyWindow.value.start)/1000:3600))
const displayNote=computed(()=>{
  const times=Object.values(history.value).flatMap(rows=>rows.map(row=>row[0])).filter(Number.isFinite)
  const coverage=times.length?` · 数据 ${new Date(Math.min(...times)*1000).toLocaleDateString('zh-CN')}—${new Date(Math.max(...times)*1000).toLocaleDateString('zh-CN')}`:' · 暂无数据'
  return `${intervalLabel(displayStep.value)}均值趋势 · 悬停查看最低 / 峰值${coverage}`
})
const ranges=[{key:'1h',label:'1小时',hours:1},{key:'6h',label:'6小时',hours:6},{key:'24h',label:'24小时',hours:24},{key:'7d',label:'7天',hours:168},{key:'30d',label:'30天',hours:720},{key:'90d',label:'90天',hours:2160},{key:'1y',label:'1年',hours:8760},{key:'custom',label:'自定义'}]
const metricOptions=[
  {key:'cpu',label:'CPU',color:'#2563eb',unit:'%',axis:'percent'},
  {key:'memory',label:'内存',color:'#7c3aed',unit:'%',axis:'percent'},
  {key:'disk',label:'磁盘',color:'#ea580c',unit:'%',axis:'percent'},
  {key:'load1',label:'负载',color:'#059669',unit:'',axis:'load'},
  {key:'net_rx',label:'网络接收',color:'#0891b2',unit:'B/s',axis:'rate'},
  {key:'net_tx',label:'网络发送',color:'#db2777',unit:'B/s',axis:'rate'},
]
const axisOrder=['percent','rate','load']
const rangeKey=ref('1h'),selectedMetrics=ref(['cpu','memory','disk']),customStart=ref(''),customEnd=ref('')
let timer=null, historyTimer=null, controller=null, historyController=null, chart=null,chartObserver=null,ready=false
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
  const hostId=selectedHostId.value;if(!hostId||loading.value&&!refresh)return
  controller?.abort();controller=new AbortController();const activeController=controller;loading.value=true;error.value=''
  try{const data=await api.get(`/servers/${hostId}/system/summary`,{refresh},{signal:activeController.signal,timeoutMs:8000});if(hostId===selectedHostId.value)summary.value=data}
  catch(e){if(e.name!=='AbortError'&&hostId===selectedHostId.value)error.value=e.message}finally{if(controller===activeController)loading.value=false}
}
function activeRange(){const end=rangeKey.value==='custom'&&customEnd.value?new Date(customEnd.value):new Date();const selected=ranges.find(item=>item.key===rangeKey.value);const start=rangeKey.value==='custom'&&customStart.value?new Date(customStart.value):new Date(end.getTime()-(selected?.hours||1)*3600000);if(start>=end)throw new Error('开始时间必须早于结束时间');return{start,end}}
async function loadHistory(){
  const hostId=selectedHostId.value;if(!hostId)return
  historyController?.abort();historyController=new AbortController();const activeController=historyController
  historyLoading.value=true;overviewLoading.value=true;error.value=''
  try{
    const {start,end}=activeRange();const query={start:start.toISOString(),end:end.toISOString(),resolution:'auto'}
    const reportError=e=>{if(e.name!=='AbortError'&&hostId===selectedHostId.value&&historyController===activeController)error.value=e.message}
    await Promise.all([
      api.get(`/servers/${hostId}/metrics/timeseries`,{...query,metrics:selectedMetrics.value.join(',')},{signal:activeController.signal,timeoutMs:12000}).then(data=>{if(hostId!==selectedHostId.value||historyController!==activeController)return;historyWindow.value={start,end};history.value=data.series||{};historyResolution.value=({raw:'原始数据','5m':'5分钟聚合','1h':'1小时聚合'})[data.resolution]||data.resolution;historyPointCount.value=data.point_count||0;renderChart()}).catch(reportError).finally(()=>{if(historyController===activeController)historyLoading.value=false}),
      api.get('/metrics/hosts/overview',{...query,metrics:'cpu,memory,disk'},{signal:activeController.signal,timeoutMs:12000}).then(overview=>{if(hostId===selectedHostId.value&&historyController===activeController)hostOverview.value=overview.hosts||[]}).catch(reportError).finally(()=>{if(historyController===activeController)overviewLoading.value=false}),
    ])
  }catch(e){if(e.name!=='AbortError')error.value=e.message;historyLoading.value=false;overviewLoading.value=false}
}
function trendAxis(axis,index,activeAxes){
  const position=axis==='percent'?'left':'right'
  const rightIndex=activeAxes.filter(item=>item!=='percent').indexOf(axis)
  const label={color:'#64748b'}
  if(axis==='percent')label.formatter=value=>`${value}%`
  if(axis==='rate')label.formatter=value=>`${fmtBytes(value)}/s`
  return{type:'value',min:0,...(axis==='percent'?{max:100}:{}),splitNumber:4,position,offset:axis==='percent'?0:rightIndex*64,name:axis==='percent'?'使用率 (%)':axis==='rate'?'网络速率':'系统负载',nameTextStyle:{color:'#64748b'},axisLabel:label,axisLine:{show:true,lineStyle:{color:'#cbd5e1'}},splitLine:{show:index===0,lineStyle:{color:'rgba(100,116,139,.14)'}}}
}
function renderChart(){
  if(!chartEl.value?.clientWidth || !historyWindow.value)return;chart ||= echarts.init(chartEl.value)
  const active=selectedMetrics.value.map(key=>metricOptions.find(item=>item.key===key)).filter(Boolean)
  const activeAxes=axisOrder.filter(axis=>active.some(meta=>meta.axis===axis))
  const metricKeyByName=new Map()
  const series=active.map(meta=>{const name=`${meta.label}${meta.unit?`（${meta.unit}）`:''}`;metricKeyByName.set(name,meta.key);return{name,type:'line',showSymbol:(history.value[meta.key]||[]).length<=2,symbolSize:5,smooth:0.2,smoothMonotone:'x',connectNulls:false,yAxisIndex:activeAxes.indexOf(meta.axis),lineStyle:{width:2,color:meta.color},itemStyle:{color:meta.color},data:trendPoints(history.value[meta.key]||[],displayStep.value)}})
  const tooltipFormatter=params=>{
    const rows=(Array.isArray(params)?params:[params]).filter(item=>item.value?.[1]!==null)
    if(!rows.length)return ''
    const time=new Date(Number(rows[0].value[0])).toLocaleString('zh-CN',{hour12:false})
    return [time,...rows.map(item=>{const key=metricKeyByName.get(item.seriesName);const [,avg,min,max]=item.value;return `${item.marker}${item.seriesName}：${formatMetricValue(key,avg)}<br/><span style="color:#64748b">最低 ${formatMetricValue(key,min)} / 峰值 ${formatMetricValue(key,max)}</span>`})].join('<br/>')
  }
  const rightAxisCount=activeAxes.filter(axis=>axis!=='percent').length
  chart.setOption({tooltip:{trigger:'axis',formatter:tooltipFormatter},legend:{data:series.map(item=>item.name)},grid:{left:activeAxes.includes('percent')?64:24,right:24+rightAxisCount*64,top:42,bottom:82},animation:false,dataZoom:[{type:'inside',start:0,end:100,filterMode:'none'},{type:'slider',start:0,end:100,height:24,bottom:14,filterMode:'none',brushSelect:false}],xAxis:{type:'time',splitNumber:6,axisLabel:{hideOverlap:true,formatter:value=>{const date=new Date(value);return (historyWindow.value.end-historyWindow.value.start)<=86400000?date.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false}):date.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'})}}},yAxis:activeAxes.map((axis,index)=>trendAxis(axis,index,activeAxes)),series},true)
}
const metricRecords=computed(()=>Object.entries(history.value).flatMap(([metric,rows])=>(rows||[]).map(([timestamp,avg,min,max,count])=>({metric,timestamp,avg,min,max,count}))).sort((a,b)=>b.timestamp-a.timestamp).slice(0,1000))
const visibleMetricRecords=computed(()=>metricRecords.value.slice(0,100))
function metricLabel(key){return metricOptions.find(item=>item.key===key)?.label||key}
function formatMetricValue(metric,value){if(value===undefined||value===null)return'-';if(['cpu','memory','disk'].includes(metric))return`${Number(value).toFixed(2)}%`;if(['net_rx','net_tx'].includes(metric))return`${fmtBytes(value)}/s`;return Number(value).toFixed(2)}
function formatRecordTime(value){return new Date(value*1000).toLocaleString('zh-CN',{hour12:false})}
function overviewMetric(row,key){const item=row.metrics?.[key];return item?`${Number(item.latest).toFixed(1)}% / ${Number(item.average).toFixed(1)}% / ${Number(item.maximum).toFixed(1)}%`:'-'}
function overviewSamples(row){return Math.max(0,...Object.values(row.metrics||{}).map(item=>Number(item.samples||0)))}
function overviewLastAt(row){const values=Object.values(row.metrics||{}).map(item=>item.last_at).filter(Boolean).sort();return values.length?fmtTime(values.at(-1)):'-'}
function selectRange(key){rangeKey.value=key;if(key==='custom'){const end=new Date();const start=new Date(end.getTime()-24*3600000);customStart.value=toLocalInput(start);customEnd.value=toLocalInput(end)}else loadHistory()}
function toLocalInput(value){const shifted=new Date(value.getTime()-value.getTimezoneOffset()*60000);return shifted.toISOString().slice(0,16)}
function toggleMetric(key){const values=[...selectedMetrics.value];const index=values.indexOf(key);if(index>=0){if(values.length===1)return;values.splice(index,1)}else if(values.length<6)values.push(key);selectedMetrics.value=values;loadHistory()}
function exportCsv(){const rows=[['时间','主机','指标','平均值','最小值','最大值','样本数'],...metricRecords.value.map(row=>[formatRecordTime(row.timestamp),currentHost.value?.name||'',metricLabel(row.metric),row.avg,row.min,row.max,row.count])];const text='\ufeff'+rows.map(row=>row.map(value=>`"${String(value??'').replaceAll('"','""')}"`).join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([text],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download=`${currentHost.value?.name||'host'}-metrics-${rangeKey.value}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function refresh(){await Promise.all([loadSummary(true),loadHistory()])}
function resetView(){overviewEl.value?.closest('.content')?.scrollTo({top:0});renderChart();resizeChart()}
function start(){
  if(!ready||!isActive.value||document.hidden||timer)return
  refresh();nextTick(resetView)
  timer=setInterval(()=>{if(isActive.value&&!document.hidden&&!loading.value)loadSummary()},5000)
  historyTimer=setInterval(()=>{if(isActive.value&&!document.hidden&&rangeKey.value!=='custom'&&!historyLoading.value)loadHistory()},60000)
}
function stop(){if(timer){clearInterval(timer);timer=null}if(historyTimer){clearInterval(historyTimer);historyTimer=null}}
function visibility(){document.hidden?stop():start()}
function resizeChart(){chart?.resize()}
watch(selectedHostId,()=>{
  controller?.abort();historyController?.abort();loading.value=false;summary.value=null;history.value={};historyWindow.value=null
  if(ready&&isActive.value&&!document.hidden){refresh();nextTick(resetView)}
})
watch(isActive,(value)=>{value?start():stop()},{flush:'post'})
onMounted(async()=>{
  try{await refreshHosts()}catch(e){error.value=e.message}
  await nextTick();ready=true
  chartObserver=new ResizeObserver(resizeChart);if(chartEl.value)chartObserver.observe(chartEl.value)
  start();document.addEventListener('visibilitychange',visibility);window.addEventListener('resize',resizeChart)
})
onActivated(start)
onDeactivated(()=>{stop()})
onUnmounted(()=>{stop();controller?.abort();historyController?.abort();chartObserver?.disconnect();chart?.dispose();document.removeEventListener('visibilitychange',visibility);window.removeEventListener('resize',resizeChart)})
</script>

<style scoped>
.actions{display:flex;align-items:center;gap:12px}.notice{padding:10px 14px;border-radius:8px;margin-bottom:12px}.error{background:#fef2f2;color:var(--err)}.stale{background:#fff7ed;color:#c2410c}.history-controls{padding:14px;margin-bottom:14px}.range-row,.metric-row,.custom-range{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.range-row b,.metric-row b{font-size:13px;margin-right:4px}.range-btn{border:1px solid var(--border);background:var(--card);color:var(--muted);border-radius:999px;padding:5px 11px;cursor:pointer}.range-btn.active{background:var(--brand);border-color:var(--brand);color:#fff}.custom-range{margin:10px 0}.custom-range input{border:1px solid var(--border);border-radius:6px;padding:7px;background:var(--card);color:var(--text)}.metric-row{border-top:1px solid var(--border);padding-top:11px;margin-top:11px}.metric-row label{font-size:12px;display:flex;gap:4px}.metric-row .muted{margin-left:auto}.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.metric-label{color:var(--muted);font-size:13px}.metric-card strong{display:block;font-size:25px;margin:8px 0}.metric-card small{color:var(--muted)}.bar{height:5px;background:#eef2f7;border-radius:5px;margin-bottom:8px;overflow:hidden}.bar i{display:block;height:100%;background:var(--brand)}.chart-grid{display:grid;grid-template-columns:1fr;gap:14px;margin-top:14px}.card h3{font-size:15px;margin:0 0 14px}.trend-card{min-width:0;position:relative}.trend-card .section-head{gap:12px;flex-wrap:wrap;margin-bottom:12px}.trend-card .section-head h3{margin:0}.chart{height:380px}.trend-card .muted{font-size:12px}.chart-loading{position:absolute;margin:120px 0 0 40%;color:var(--muted);z-index:1}.info{display:grid;grid-template-columns:100px 1fr 100px 1fr;gap:12px;margin:0}.info dt{color:var(--muted)}.info dd{margin:0;word-break:break-all}.section{margin-top:14px;overflow:auto}.section-head{display:flex;align-items:center;justify-content:space-between}.section-head h3{margin-bottom:3px}.record-scroll{max-height:480px;overflow:auto}.metric-log .table,.host-overview .table{min-width:760px}.host-overview tbody tr{cursor:pointer}.host-overview tbody tr:hover,.host-overview tbody tr.selected{background:rgba(37,99,235,.06)}.host-link{display:block;border:0;background:none;color:var(--brand);font-weight:700;padding:0;cursor:pointer}.host-overview td small{display:block;color:var(--muted);margin-top:3px}.host-state{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px}.host-state.online{color:var(--ok);background:rgba(34,197,94,.1)}.host-state.offline{color:var(--muted);background:rgba(148,163,184,.12)}@media(max-width:900px){.metric-grid{grid-template-columns:repeat(2,1fr)}.chart-grid{grid-template-columns:1fr}.metric-row .muted{margin-left:0;width:100%}.info{grid-template-columns:90px 1fr}.chart{height:340px}}
</style>

<style scoped>
.monitor-view{padding:16px 24px}.monitor-overview{height:calc(100dvh - 132px);min-height:440px;display:flex;flex-direction:column;gap:12px}.metric-grid{flex:none;gap:12px}.metric-card{padding:12px 16px}.metric-card strong{font-size:24px;line-height:1.1;margin:5px 0}.metric-card small{font-size:11px}.metric-label{font-size:12px}.bar{height:3px;margin-bottom:6px}.trend-card{display:flex;flex-direction:column;flex:1;min-height:0;padding:14px 16px}.trend-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.trend-title h3{margin:0;font-size:14px}.trend-title small{display:block;color:var(--muted);font-size:11px;margin-top:4px}.range-row{margin-left:auto;gap:5px}.range-row>span{font-size:12px;color:var(--muted);margin-right:4px}.range-btn{border-radius:5px;padding:5px 8px;font-size:12px}.metric-row{flex:none;margin-top:10px;padding-top:9px;font-size:12px}.chart-body{position:relative;flex:1;min-height:220px;margin-top:6px}.chart{height:100%;min-height:220px}.chart-loading{inset:0;display:grid;place-items:center;margin:0;pointer-events:none;background:rgba(255,255,255,.65)}.trend-note{font-size:11px;color:var(--muted);line-height:1.5;flex:none;margin-top:4px}.custom-range{font-size:12px;flex:none}.custom-range input{min-width:0}
@media(max-width:1100px){.monitor-overview{height:auto;min-height:0}.chart-body{height:340px;flex:none}.range-row{margin-left:0}.trend-toolbar{gap:10px}.trend-toolbar>.btn{margin-left:auto}.metric-card{padding:12px}.monitor-view{padding:12px}.metric-row .muted{width:auto;margin-left:auto}}
@media(max-width:620px){.metric-grid{gap:8px}.chart-body{height:300px}.trend-note{line-height:1.7}.metric-row .muted{margin-left:0;width:100%}.custom-range input{max-width:100%}}
</style>
