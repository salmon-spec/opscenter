<template>
  <section class="screen-charts" aria-label="监控图表">
    <div class="charts-heading"><h2>指标与趋势</h2><span>历史图表每 60 秒更新 · 实时对比随大屏刷新</span></div>
    <div class="charts-grid">
      <article class="chart-panel trend-panel">
        <header><div><h3>主机资源趋势</h3><p>{{ history ? `${intervalLabel(trend.step)}均值 · ${stamp(historyAt)}` : 'CPU / 内存 / 磁盘历史使用率' }}</p></div><div class="chart-controls"><select v-model="hostId" aria-label="趋势主机"><option v-for="host in hosts" :key="host.id" :value="host.id">{{ host.name }}</option></select><select v-model.number="hours" aria-label="趋势时间范围"><option :value="1">近 1 小时</option><option :value="6">近 6 小时</option><option :value="24">近 24 小时</option></select></div></header>
        <div class="chart-legend"><span v-for="metric in chartMetrics" :key="metric.key"><i :style="{background:metric.color}"></i>{{ metric.label }}</span><span v-if="historyLoading" class="hint">更新中…</span></div>
        <p v-if="historyError" class="chart-error">{{ historyError }}{{ history ? ' · 显示上次查询' : '' }}</p>
        <div v-if="trend.lines.length" class="trend-plot" :class="{dim:!!historyError}">
          <svg viewBox="0 0 900 230" role="img" :aria-label="`${selectedHostName}近${hours}小时CPU、内存和磁盘均值曲线；悬停数据点查看数值`">
            <g v-for="value in [0,25,50,75,100]" :key="value"><line x1="44" x2="876" :y1="200-value*1.7" :y2="200-value*1.7" class="grid-line"/><text x="34" :y="204-value*1.7" text-anchor="end">{{ value }}%</text></g>
            <line x1="44" x2="876" y1="64" y2="64" class="threshold"/><text x="876" y="58" text-anchor="end" class="threshold-label">80% 提醒</text>
            <g v-for="line in trend.lines" :key="line.key"><g v-for="(segment,index) in line.segments" :key="index"><polyline :points="segment.map(p=>`${p.x},${p.y}`).join(' ')" :stroke="line.color" class="trend-line"/><circle v-for="point in segment" :key="point.time" :cx="point.x" :cy="point.y" :r="segment.length===1?3:4" :fill="line.color" :class="{'data-point':segment.length>1}"><title>{{ line.label }} · {{ dateTime(point.time) }} · 均值 {{ point.value.toFixed(1) }}% · 最低 {{ point.min.toFixed(1) }}% / 峰值 {{ point.max.toFixed(1) }}%</title></circle></g></g>
            <text v-for="index in [0,1,2,3,4]" :key="index" :x="44+index*208" y="223" :text-anchor="index===0?'start':index===4?'end':'middle'">{{ shortTime(trend.start+(trend.end-trend.start)*index/4) }}</text>
          </svg>
        </div><div v-else class="chart-empty">{{ !hosts.length ? '等待主机清单' : historyLoading ? '正在查询历史指标…' : historyError ? '历史指标暂不可用' : '所选时段暂无有效指标' }}</div>
        <footer>悬停曲线数据点查看均值与峰值 · 缺失采样断开显示 · 横轴按实际数据范围展开</footer>
      </article>

      <article class="chart-panel comparison-panel">
        <header><div><h3>主机资源对比</h3><p>按主机当前最高使用率排序</p></div><span class="axis-caption">0 — 100%</span></header>
        <div class="chart-legend"><span v-for="metric in chartMetrics" :key="metric.key"><i :style="{background:metric.color}"></i>{{ metric.label }}</span></div>
        <div class="comparison-list"><div v-for="host in comparison" :key="host.id" class="comparison-row"><span :title="host.name">{{ host.name }}</span><div class="bar-group"><div v-for="metric in chartMetrics" :key="metric.key" class="comparison-track" :title="sampleText(host,metric)"><i v-if="sampleValue(host,metric)!=null" :style="{width:sampleValue(host,metric)+'%',background:metric.color}"></i><span v-else class="missing">无实时数据</span></div></div><b>{{ host.pressure>=0 ? host.pressure.toFixed(1)+'%' : '—' }}</b></div><div v-if="!comparison.length" class="chart-empty">暂无主机资源数据</div></div>
        <footer>每组三条分别为 CPU / 内存 / 磁盘 · 右侧为最高值 · 失效数据不参与排序</footer>
      </article>

      <article class="chart-panel alert-chart-panel">
        <header><div><h3>新增告警趋势</h3><p>近 7 个自然日 · 北京时间 · 查询于 {{ stamp(eventsAt) }}</p></div><div class="chart-total">{{ events==null ? '—' : alertTrend.total }}<small> 条</small></div></header>
        <p v-if="eventsError" class="chart-error">{{ eventsError }}{{ events ? ' · 显示上次查询' : '' }}</p><p v-else-if="!alertSourceState.usable" class="chart-note">当前采集状态：{{ alertSourceState.label }} · 下图为已保存的历史事件</p>
        <div v-if="events!=null" class="daily-chart" :class="{dim:!!eventsError}" role="img" :aria-label="alertTrend.days.map(day=>`${day.day}新增${day.count}条`).join('，')"><div class="daily-scale"><span>{{ alertMax }}</span><span>{{ Math.round(alertMax/2) }}</span><span>0</span></div><div class="daily-columns"><div v-for="day in alertTrend.days" :key="day.day" class="daily-column" :title="`${day.day} 新增 ${day.count} 条告警`"><b>{{ day.count }}</b><div class="daily-track"><i :style="{height:(day.count/alertMax*100)+'%'}"></i></div><span>{{ day.day.slice(5).replace('-','/') }}</span></div></div></div><div v-else class="chart-empty">{{ eventsLoading ? '正在查询告警历史…' : '告警历史暂不可用' }}</div>
        <footer>按事件创建日期计数、ID 去重；包含已恢复事件<span v-if="alertTrend.omitted"> · {{ alertTrend.omitted }} 条缺少有效时间</span></footer>
      </article>

      <article class="chart-panel pod-chart-panel">
        <header><div><h3>K3s Pod 阶段分布</h3><p>采集 {{ stamp(clusterSource.value?.collected_at) }} · {{ clusterSourceState.label }}</p></div><router-link to="/kubernetes/pods">查看 Pod ↗</router-link></header>
        <div v-if="podParts.length" class="pod-distribution" :class="{dim:!clusterSourceState.usable}"><div class="donut" :style="{background:podGradient}" role="img" :aria-label="podParts.map(p=>`${p.label} ${p.value}`).join('，')"><div><b>{{ podTotal }}</b><span>Pod 总数</span></div></div><div class="distribution-legend"><div v-for="part in podParts" :key="part.key"><i :style="{background:podColors[part.key]}"></i><span>{{ part.label }}</span><b>{{ part.value }}</b><small>{{ podTotal ? (part.value/podTotal*100).toFixed(1) : '0.0' }}%</small></div></div></div><div v-else class="chart-empty">等待有效的 Pod 统计数据</div>
        <footer>其他阶段包含未归入 Running / Failed 的 Pod；以集群快照为准</footer>
      </article>
    </div>
  </section>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import { chartMetrics, dailyAlerts, resourceTrend } from '../utils/screenCharts'
import { intervalLabel } from '../utils/monitorTrend'
import { podSegments, sourceState } from '../utils/monitorScreen'
const props=defineProps({hosts:{type:Array,default:()=>[]},clusterSource:{type:Object,required:true},alertsSource:{type:Object,required:true},active:Boolean,now:Number})
const hostId=ref(''),hours=ref(1),history=ref(null),historyAt=ref(null),historyLoading=ref(false),historyError=ref('')
const events=ref(null),eventsAt=ref(null),eventsError=ref(''),eventsLoading=ref(false)
let historyController=null,eventsController=null,timer=null
const trend=computed(()=>resourceTrend(history.value?.series,hours.value))
const selectedHostName=computed(()=>props.hosts.find(host=>host.id===hostId.value)?.name||'主机')
const comparison=computed(()=>[...props.hosts].sort((a,b)=>b.pressure-a.pressure||a.name.localeCompare(b.name,'zh-CN')))
const alertTrend=computed(()=>dailyAlerts(events.value||[],props.now))
const alertMax=computed(()=>Math.max(1,...alertTrend.value.days.map(day=>day.count)))
const alertSourceState=computed(()=>sourceState(props.alertsSource,props.now))
const clusterSourceState=computed(()=>sourceState(props.clusterSource,props.now))
const podParts=computed(()=>podSegments(props.clusterSource.value?.data))
const podTotal=computed(()=>podParts.value.reduce((sum,part)=>sum+part.value,0))
const podColors={running:'#79c99e',failed:'#f17d82',other:'#787d8b'}
const podGradient=computed(()=>{let offset=0;return podTotal.value?'conic-gradient('+podParts.value.map(part=>{const start=offset;offset+=part.value/podTotal.value*100;return `${podColors[part.key]} ${start}% ${offset}%`}).join(',')+')':'#34353e'})
function shortTime(value){return new Date(value).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}
function dateTime(value){return new Date(value).toLocaleString('zh-CN',{hour12:false})}
function stamp(value){if(!value)return '—';return dateTime(typeof value==='number'?value:Date.parse(value))}
function sampleValue(host,metric){const sample=host.samples.find(sample=>sample.key===metric.key);return host.online&&sample?.fresh?sample.value:null}
function sampleText(host,metric){const value=sampleValue(host,metric);return `${host.name} · ${metric.label} ${value==null?'无实时数据':value.toFixed(1)+'%'}`}
async function loadHistory(){
  if(!props.active||!hostId.value)return
  historyController?.abort();const controller=new AbortController();historyController=controller;historyLoading.value=true
  const id=hostId.value,span=hours.value,end=new Date()
  try{const data=await api.get(`/servers/${id}/metrics/timeseries`,{start:new Date(end.getTime()-span*3600000).toISOString(),end:end.toISOString(),resolution:'auto',metrics:'cpu,memory,disk'},{signal:controller.signal,timeoutMs:15000})
    if(controller!==historyController)return
    if(!data?.series||typeof data.series!=='object')throw new Error('历史指标格式异常')
    history.value=data;historyAt.value=Date.now();historyError.value=''
  }catch(error){if(controller===historyController&&error.name!=='AbortError')historyError.value=error.message}
  finally{if(controller===historyController)historyLoading.value=false}
}
async function loadEvents(){
  if(!props.active)return
  eventsController?.abort();const controller=new AbortController();eventsController=controller;eventsLoading.value=true
  try{const data=await api.get('/modules/alerts/api/v1/events',{days:7},{signal:controller.signal,timeoutMs:15000})
    if(controller!==eventsController)return
    if(!Array.isArray(data))throw new Error('告警历史格式异常')
    events.value=data;eventsAt.value=Date.now();eventsError.value=''
  }catch(error){if(controller===eventsController&&error.name!=='AbortError')eventsError.value=error.message}
  finally{if(controller===eventsController)eventsLoading.value=false}
}
watch(()=>props.hosts.map(host=>host.id).join(','),()=>{if(!props.hosts.some(host=>host.id===hostId.value))hostId.value=props.hosts.find(host=>host.online)?.id||props.hosts[0]?.id||''},{immediate:true})
watch([hostId,hours],()=>{historyController?.abort();history.value=null;historyError.value='';loadHistory()})
watch(()=>props.active,value=>{clearInterval(timer);if(value){loadHistory();loadEvents();timer=setInterval(()=>{loadHistory();loadEvents()},60000)}else{historyController?.abort();eventsController?.abort()}},{immediate:true})
onUnmounted(()=>{clearInterval(timer);historyController?.abort();eventsController?.abort()})
</script>

<style scoped>
.screen-charts{margin-top:24px;color:#e8e9ed;font-variant-numeric:tabular-nums}.charts-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.charts-heading h2{font-size:15px;margin:0;font-weight:600}.charts-heading>span{font-size:10px;color:#a0a3ad}.charts-grid{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(330px,1fr);gap:16px}.chart-panel{min-width:0;background:#1b1c22;border:1px solid #303137;border-radius:5px;overflow:hidden}.chart-panel header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px 8px;min-height:64px}.chart-panel h3{font-size:13px;font-weight:600;margin:0}.chart-panel header p{font-size:10px;color:#a0a3ad;margin:5px 0 0}.chart-controls{display:flex;gap:6px}.chart-controls select{font:inherit;font-size:11px;color:#e8e9ed;border:1px solid #41434c;border-radius:3px;background:#24252d;padding:5px;max-width:160px}.chart-controls select:focus-visible{outline:2px solid #e9ac69}.chart-legend{display:flex;gap:16px;font-size:10px;color:#a0a3ad;padding:4px 16px 8px}.chart-legend i,.distribution-legend i{display:inline-block;width:7px;height:7px;border-radius:2px;margin-right:5px}.chart-legend .hint{margin-left:auto}.trend-plot{padding:0 12px}.trend-plot svg{display:block;width:100%;height:244px;overflow:visible}.trend-plot text{fill:#a0a3ad;font-size:11px;font-family:inherit}.grid-line{stroke:#353640;stroke-width:1;stroke-dasharray:3 4}.trend-line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round;vector-effect:non-scaling-stroke}.threshold{stroke:#e9ac69;stroke-opacity:.35;stroke-dasharray:5 5}.trend-plot .threshold-label{fill:#a98c66;font-size:10px}.data-point{opacity:0;cursor:crosshair}.data-point:hover{opacity:1;stroke:#1b1c22;stroke-width:1}.chart-panel footer{padding:10px 16px;border-top:1px solid #303137;font-size:9px;color:#a0a3ad;line-height:1.6}.chart-empty{height:244px;display:flex;align-items:center;justify-content:center;color:#a0a3ad;font-size:12px}.chart-error,.chart-note{font-size:10px;color:#eeb566;padding:0 16px;margin:4px 0 8px}.dim{opacity:.45}.axis-caption{font-size:10px;color:#a0a3ad;white-space:nowrap}.comparison-list{height:244px;overflow:auto;padding:0 16px 10px}.comparison-row{display:grid;grid-template-columns:125px minmax(50px,1fr) 45px;gap:10px;align-items:center;margin:0 0 7px}.comparison-row>span{font-size:10px;color:#c9cbd4;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.comparison-row>b{font-size:10px;text-align:right;font-weight:400;color:#a0a3ad}.bar-group{display:grid;gap:2px}.comparison-track{position:relative;height:5px;background:#303137;border-radius:1px}.comparison-track i{display:block;height:100%;border-radius:1px;min-width:0}.missing{display:block;color:#6f7280;font-size:7px;line-height:5px;text-align:right}.chart-total{font-size:25px;font-weight:500}.chart-total small{font-size:11px;color:#a0a3ad}.daily-chart{height:218px;display:flex;gap:12px;margin:0 22px 8px 16px;padding-top:14px}.daily-scale{font-size:9px;color:#a0a3ad;display:flex;flex-direction:column;justify-content:space-between;padding:12px 0 27px;min-width:20px}.daily-columns{flex:1;display:flex;gap:16px;border-bottom:1px solid #41434c;background:repeating-linear-gradient(to top,transparent,transparent calc(50% - 1px),#303137 50%);min-width:0}.daily-column{flex:1;display:flex;align-items:center;flex-direction:column;min-width:0;padding-top:2px}.daily-column>b{font-size:11px;font-weight:500;height:20px}.daily-track{height:160px;width:50%;display:flex;align-items:flex-end;border-bottom:1px solid #70737e}.daily-track i{display:block;background:#e9ac69;width:100%;border-radius:3px 3px 0 0;min-height:0}.daily-column>span{font-size:10px;color:#a0a3ad;margin-top:7px;white-space:nowrap}.pod-distribution{height:248px;display:flex;justify-content:center;align-items:center;gap:26px;padding:16px}.donut{width:166px;height:166px;border-radius:50%;display:grid;place-items:center;flex-shrink:0}.donut>div{width:128px;height:128px;background:#1b1c22;border-radius:50%;display:flex;flex-direction:column;justify-content:center;align-items:center}.donut b{font-size:32px;font-weight:500}.donut span{font-size:10px;color:#a0a3ad}.distribution-legend{display:grid;gap:18px;min-width:165px}.distribution-legend>div{display:grid;grid-template-columns:12px 65px 28px 42px;align-items:center;gap:5px;font-size:11px}.distribution-legend i{margin:0}.distribution-legend b{text-align:right;font-weight:500}.distribution-legend small{text-align:right;color:#a0a3ad;font-size:10px}.chart-panel a{font-size:11px;color:#b7bac7;text-decoration:none}.chart-panel a:hover{color:#fff}@media(min-width:1800px){.trend-plot svg,.comparison-list{height:300px}.daily-chart{height:260px}.daily-track{height:200px}.pod-distribution{height:290px}.comparison-row{margin-bottom:12px}}@media(max-width:1100px){.charts-grid{grid-template-columns:1fr 1fr}.trend-panel{grid-column:1/-1}.alert-chart-panel{grid-column:1/-1;grid-row:3}.comparison-list{height:248px}}@media(max-width:700px){.charts-heading{align-items:flex-start;flex-direction:column}.charts-grid{grid-template-columns:1fr}.trend-panel,.alert-chart-panel{grid-column:auto;grid-row:auto}.chart-panel header{align-items:flex-start;flex-wrap:wrap}.chart-controls{flex-wrap:wrap}.trend-plot svg{height:210px}.pod-distribution{gap:16px}.donut{width:130px;height:130px}.donut>div{width:98px;height:98px}.distribution-legend{min-width:140px}.distribution-legend>div{grid-template-columns:9px 59px 22px 38px;gap:3px;font-size:10px}.daily-columns{gap:8px}.daily-chart{margin-right:16px}.comparison-row{grid-template-columns:120px minmax(50px,1fr) 45px}}
</style>
