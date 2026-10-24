<template>
  <div class="monitor-screen" :class="{'standalone-board':standalone}" ref="screenRoot">
    <header class="board-head">
      <div class="board-title"><span class="brand-mark">O</span><div><h1>运行监控</h1><p>OpsCenter <span>/</span> 主机 · 告警 · K3s</p></div></div>
      <div class="head-actions"><span class="clock">{{ clockText }}</span><button class="board-button" @click="showCharts">查看图表 ↓</button><router-link v-if="standalone" to="/" class="board-button">工作台 ↗</router-link><button class="board-button" :disabled="loading" @click="refresh(true)">{{ loading ? '刷新中…' : '刷新' }}</button><button class="board-button" @click="toggleFullscreen">{{ fullscreen ? '退出全屏' : '全屏' }}</button></div>
    </header>

    <section class="overview" aria-label="运行状态总览">
      <article><span>主机在线</span><strong :class="stats.offline||stats.unknown?'warning':''">{{ loaded ? stats.online : '—' }}<small>/ {{ hosts.length || '—' }}</small></strong><p>{{ loaded ? `离线 ${stats.offline} · 待确认 ${stats.unknown}` : '正在读取资产清单' }}</p></article>
      <article><span>待关注告警 <em>聚合</em></span><strong :class="alertState.usable&&alertItems.length?'danger':''">{{ alertState.usable ? alertItems.length : '—' }}<small v-if="alertState.usable">严重 {{ criticalCount }}</small></strong><p :class="alertState.level">{{ alertState.usable ? '告警事件与主机监测发现' : alertState.label }}</p></article>
      <article><span>K3s 节点就绪</span><strong :class="clusterState.usable&&cluster.nodes?.ready!==cluster.nodes?.total?'warning':''">{{ clusterState.usable ? count(cluster.nodes?.ready) : '—' }}<small>/ {{ count(cluster.nodes?.total) }}</small></strong><p :class="clusterState.level">{{ clusterState.label }} · {{ sourceAge('cluster') }}</p></article>
      <article><span>Pod 运行中</span><strong>{{ clusterState.usable ? count(cluster.pods?.running) : '—' }}<small>/ {{ count(cluster.pods?.total) }}</small></strong><p :class="cluster.pods?.failed ? 'danger' : ''">{{ clusterState.usable ? `Failed ${count(cluster.pods?.failed)} · Warning ${count(cluster.warning_events)}` : clusterState.label }}</p></article>
    </section>

    <div class="board-layout">
      <section class="panel hosts-panel">
        <header class="panel-head"><h2>主机资源 <small>{{ hosts.length }} 台</small></h2><router-link to="/hosts">主机管理 ↗</router-link></header>
        <div class="host-toolbar"><div class="filters"><button v-for="item in filters" :key="item.key" :aria-pressed="filter===item.key" :class="{selected:filter===item.key}" @click="filter=item.key">{{ item.name }} <b>{{ item.count }}</b></button></div><input v-model="search" aria-label="搜索主机" placeholder="搜索名称 / IP" /></div>
        <div v-if="errors.length" class="source-error" role="alert">{{ errors.join('；') }} · 当前状态待确认</div>
        <div class="table-wrap">
          <table class="host-table"><thead><tr><th>主机 / 地址</th><th>状态</th><th v-for="label in metricLabels" :key="label">{{ label }}</th><th>采集</th></tr></thead>
            <tbody><tr v-for="host in visibleHosts" :key="host.id" :class="host.level">
              <td><button class="host-name" @click="openMonitor(host)">{{ host.name }} <span>↗</span></button><small class="host-ip">{{ host.host }}</small></td>
              <td><span class="state" :class="host.level" :title="host.reasons.join(' · ')"><i></i>{{ host.label }}</span></td>
              <td v-for="sample in host.samples" :key="sample.key"><div class="resource" :class="{stale:!host.online||!sample.fresh}" :title="metricTitle(host,sample)"><b :class="sample.fresh&&host.online?(sample.value>=90?'danger':sample.value>=80?'warning':''):''">{{ sample.value == null ? '—' : sample.value.toFixed(1) }}<small v-if="sample.value!=null">%</small></b><div class="meter"><i :class="sample.value>=90?'high':sample.value>=80?'warm':sample.key" :style="{width:(sample.value??0)+'%'}"></i></div><small v-if="!host.online||!sample.fresh">上次数据</small></div></td>
              <td class="collection"><span :class="host.agentOk&&host.metricsFresh?'muted':'warning'">{{ host.metricsFresh ? '实时' : '待确认' }}</span><small :title="`Agent ${agentLabel(host.agent_status)} · v${host.agent_version||'未知'} · 目标 v${targetVersion||'未知'}`">{{ time(oldestAt(host.samples)) }}</small></td>
            </tr><tr v-if="!visibleHosts.length"><td colspan="6" class="empty">{{ !loaded ? '正在读取主机…' : hosts.length ? '没有符合筛选条件的主机' : '暂无可用主机清单' }}</td></tr></tbody>
          </table>
        </div>
        <footer class="panel-foot"><span>异常优先 · CPU / 内存 / 磁盘</span><span :class="stats.agent!==hosts.length||!versionOk?'warning':''">Agent {{ stats.agent }}/{{ hosts.length }} · v{{ targetVersion||'—' }} · 版本一致 {{ stats.version }}/{{ hosts.length }}</span></footer>
      </section>

      <div class="side-panels">
        <section class="panel cluster-panel" :class="{'snapshot-stale':!clusterState.usable}" aria-label="K3s 集群监控">
          <header class="panel-head"><h2>K3s 集群</h2><router-link to="/kubernetes/overview">集群详情 ↗</router-link></header>
          <div class="cluster-top"><span class="state" :class="clusterState.level"><i></i>{{ clusterState.label }}</span><small>采集 {{ time(sources.cluster.value?.collected_at) }}</small></div>
          <p v-if="sources.cluster.error||sources.cluster.value?.error" class="source-error">{{ sources.cluster.error||sources.cluster.value.error }}</p>
          <div class="cluster-stats"><div><span>节点 Ready</span><b>{{ count(cluster.nodes?.ready) }}<small> / {{ count(cluster.nodes?.total) }}</small></b></div><div><span>PVC 未绑定</span><b :class="cluster.pvcs?.unbound?'warning':''">{{ count(cluster.pvcs?.unbound) }}<small> / {{ count(cluster.pvcs?.total) }}</small></b></div><div><span>Warning 事件</span><b :class="cluster.warning_events?'warning':''">{{ count(cluster.warning_events) }}</b></div></div>
          <div class="pod-heading"><h3>Pod 阶段分布</h3><span>{{ count(cluster.pods?.total) }} total</span></div>
          <div v-if="segments.length" class="pod-bar" role="img" :aria-label="segments.map(s=>`${s.label} ${s.value}`).join('，')"><i v-for="segment in segments" :key="segment.key" :class="segment.key" :style="{flexGrow:segment.value}" :title="`${segment.label} ${segment.value}`"></i></div><div v-else class="pod-bar unavailable"></div>
          <div class="pod-legend"><span v-for="segment in segments" :key="segment.key"><i :class="segment.key"></i>{{ segment.label }} <b>{{ segment.value }}</b></span><small v-if="!segments.length">等待有效的集群快照</small></div>
          <div class="cluster-events"><div v-if="!clusterFindingsState.usable" class="source-error">事件：{{ clusterFindingsState.label }}</div><router-link v-for="item in clusterItems.slice(0,2)" :key="item.id" to="/kubernetes/operations" :title="item.summary"><b>{{ item.title }}</b><span>{{ item.resource_id }}</span></router-link><p v-if="clusterFindingsState.usable&&!clusterItems.length" class="muted">当前快照无 Warning 事件</p><router-link v-if="clusterItems.length>2" to="/kubernetes/operations">查看全部 {{ clusterItems.length }} 条事件 →</router-link></div>
        </section>

        <section class="panel alerts-panel" aria-label="告警监控">
          <header class="panel-head"><h2>告警队列 <small>{{ alertState.usable ? alertItems.length : '—' }}</small></h2><router-link to="/alerts">告警中心 ↗</router-link></header>
          <div class="alert-toolbar"><div class="filters"><button :class="{selected:alertFilter==='all'}" :aria-pressed="alertFilter==='all'" @click="alertFilter='all'">全部</button><button :class="{selected:alertFilter==='critical'}" :aria-pressed="alertFilter==='critical'" @click="alertFilter='critical'">严重 {{ criticalCount }}</button></div><span :class="alertState.level">{{ alertState.label }} · {{ sourceAge('alertFindings') }}</span></div>
          <p v-if="!alertState.usable" class="source-error">{{ sources.alertFindings.error||sources.alertFindings.value?.error||alertState.label }} · 列表保留最近采集</p>
          <div class="alert-list"><router-link v-for="item in shownAlerts" :key="item.id" :to="'/alerts'" class="alert-item"><span class="severity" :class="item.severity">{{ {critical:'严重',warning:'关注',info:'信息'}[item.severity] }}</span><div><b>{{ item.title }}</b><p>{{ item.summary }}</p></div><time :title="item.observed_at">{{ time(item.observed_at) }}</time></router-link><div v-if="!shownAlerts.length" class="alert-empty"><span :class="alertState.usable?'healthy':'unknown'">{{ !alertState.usable ? '告警状态待确认' : alertFilter==='critical' ? '暂无严重告警' : '暂无待关注告警' }}</span><p>{{ alertState.usable ? '持续监测中，有异常时在此显示' : '等待有效采集，或点击刷新重试' }}</p></div></div>
          <footer class="panel-foot"><span>事件口径 · 近 30 天</span><span v-if="alertSummaryState.usable">触发 {{ count(alertCounts.firing) }} · 待确认 {{ count(alertCounts.pending) }} · 已确认 {{ count(alertCounts.acked) }}</span><span v-else :class="alertSummaryState.level">{{ alertSummaryState.label }}</span></footer>
        </section>
      </div>
    </div>
    <ScreenCharts :hosts="views" :cluster-source="sources.cluster" :alerts-source="sources.alertSummary" :active="active&&!hidden" :now="now" />
    <footer class="board-footer"><span><i class="live-dot"></i>{{ active&&!hidden?'每 10 秒刷新':'刷新已暂停' }} <span>资源 ≥80% 提醒 / ≥90% 严重</span></span><span>主机指标 {{ time(metricsAt) }} · 资产 {{ time(inventoryAt) }} · 过期数据不计为正常</span></footer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useTabActive } from '../workbench/tabs'
import { useHostContext } from '../hostContext'
import { hostView, sortHosts, ageSeconds } from '../utils/hostScreen'
import { sourceState, orderFindings, podSegments } from '../utils/monitorScreen'
import ScreenCharts from '../components/ScreenCharts.vue'

const route=useRoute(), router=useRouter(), screenRoot=ref(null), fullscreen=ref(false)
const standalone=computed(()=>route.name==='ScreenStandalone')
const {isActive:inTab}=useTabActive('Screen'), {isActive:separate}=useTabActive('ScreenStandalone')
const active=computed(()=>inTab.value||separate.value)
const {selectHost,refreshHosts}=useHostContext()
const hosts=ref([]), metrics=ref({}), targetVersion=ref(''), inventoryAt=ref(null), metricsAt=ref(null)
const inventoryOk=ref(false),metricsOk=ref(false),versionOk=ref(false),loaded=ref(false),loading=ref(false)
const errors=ref([]),filter=ref('all'),search=ref(''),now=ref(Date.now()),hidden=ref(document.hidden)
const metricLabels={cpu:'CPU',memory:'内存',disk:'磁盘'}
let timer=null,clock=null,controller=null,versionAt=0
const sources=ref(Object.fromEntries(['cluster','clusterFindings','alertSummary','alertFindings'].map(key=>[key,{value:null,error:''}])))
const alertFilter=ref('all')
const cluster=computed(()=>sources.value.cluster.value?.data || {})
const clusterState=computed(()=>sourceState(sources.value.cluster,now.value))
const alertState=computed(()=>sourceState(sources.value.alertFindings,now.value))
const alertSummaryState=computed(()=>sourceState(sources.value.alertSummary,now.value))
const clusterFindingsState=computed(()=>sourceState(sources.value.clusterFindings,now.value))
const alertItems=computed(()=>orderFindings(sources.value.alertFindings.value?.items || []))
const shownAlerts=computed(()=>alertItems.value.filter(item=>alertFilter.value==='all'||item.severity==='critical'))
const clusterItems=computed(()=>orderFindings(sources.value.clusterFindings.value?.items || []))
const segments=computed(()=>podSegments(cluster.value))
const alertCounts=computed(()=>sources.value.alertSummary.value?.data || {})
const criticalCount=computed(()=>alertItems.value.filter(item=>item.severity==='critical').length)
function count(value){return Number.isInteger(value)&&value>=0 ? value : '—'}
function sourceAge(key){return ageText(ageSeconds(sources.value[key].value?.collected_at,now.value))}
function metricTitle(host,sample){return `${metricLabels[sample.key]} · ${time(sample.at)} · ${!host.online||!sample.fresh?'上次数据': '实时数据'}`}
const clockText=computed(()=>new Date(now.value).toLocaleString('zh-CN',{hour12:false}))
const views=computed(()=>sortHosts(hosts.value.map(h=>hostView(h,metrics.value[h.id],versionOk.value?targetVersion.value:'',now.value,inventoryOk.value,metricsOk.value))))
const stats=computed(()=>({
  online:views.value.filter(h=>h.online).length,offline:views.value.filter(h=>h.offline).length,
  unknown:views.value.filter(h=>!h.online&&!h.offline).length,attention:views.value.filter(h=>h.level!=='healthy').length,
  agent:views.value.filter(h=>h.agentOk).length,version:versionOk.value?views.value.filter(h=>h.versionOk).length:0,
  fresh:views.value.filter(h=>h.metricsFresh).length,
}))
const filters=computed(()=>[{key:'all',name:'全部主机',count:hosts.value.length},{key:'attention',name:'需关注',count:stats.value.attention},{key:'offline',name:'离线',count:stats.value.offline},{key:'agent',name:'Agent 待统一',count:views.value.filter(h=>!h.versionOk).length}])
const visibleHosts=computed(()=>views.value.filter(h=>{
  if(filter.value==='attention'&&h.level==='healthy')return false
  if(filter.value==='offline'&&!h.offline)return false
  if(filter.value==='agent'&&h.versionOk)return false
  return `${h.name} ${h.host} ${h.lan_ip||''} ${h.kubernetes_node_name||''}`.toLowerCase().includes(search.value.trim().toLowerCase())
}))
function time(value){if(!value)return '—';let raw=String(value);if(!/(Z|[+-]\d{2}:\d{2})$/i.test(raw))raw+='Z';const d=new Date(raw);return Number.isNaN(d.getTime())?'—':d.toLocaleTimeString('zh-CN',{hour12:false})}
function ageText(age){return age==null?'时间未知':age<60?`${Math.floor(age)} 秒前`:age<3600?`${Math.floor(age/60)} 分钟前`:`${Math.floor(age/3600)} 小时前`}
function oldestAt(samples){return samples.reduce((oldest,sample)=>sample.age!=null&&(!oldest||sample.age>oldest.age)?sample:oldest,null)?.at}
function agentLabel(value){return {running:'运行中',stopped:'已停止',not_deployed:'未安装',error:'异常',deploying:'更新中',unknown:'待确认'}[value]||'待确认'}
async function openMonitor(host){await refreshHosts();selectHost(host.id);router.push('/system/monitor')}
async function refresh(force=false){
  if(loading.value||(!force&&(!active.value||document.hidden)))return
  loading.value=true;controller=new AbortController()
  const options={signal:controller.signal,timeoutMs:15000},end=new Date(),messages=[]
  const jobs=Object.entries({cluster:['k3s','summary'],clusterFindings:['k3s','findings'],alertSummary:['alerts','summary'],alertFindings:['alerts','findings']}).map(([key,[module,endpoint]])=>
    api.get(`/modules/${module}/api/v1/${endpoint}`,null,options).then(data=>{
      if(!['ok','degraded','unavailable'].includes(data?.status)||(endpoint==='findings'&&!Array.isArray(data.items))||(endpoint==='summary'&&(!data.data||typeof data.data!=='object')))throw new Error('响应格式异常')
      if(data.status==='unavailable')throw new Error(data.error||'数据源不可用')
      sources.value[key]={value:{...data,stale_after_seconds:data.stale_after_seconds??(module==='alerts'?300:120)},error:''}
    }).catch(error=>{sources.value[key].error=error.name==='AbortError'?'请求已暂停':error.message})
  )
  jobs.push(
    api.get('/modules/host-inventory/api/v1/hosts',null,options).then(data=>{
      if(!Array.isArray(data.items)||data.status==='unavailable')throw new Error('主机清单暂不可用')
      hosts.value=data.items;inventoryAt.value=new Date().toISOString();inventoryOk.value=true
    }).catch(error=>{inventoryOk.value=false;if(error.name!=='AbortError')messages.push(`主机状态：${error.message}`)}),
    api.get('/metrics/hosts/overview',{start:new Date(end.getTime()-10*60000).toISOString(),end:end.toISOString(),resolution:'raw',metrics:'cpu,memory,disk'},options).then(data=>{
      if(!Array.isArray(data.hosts))throw new Error('指标响应格式异常')
      metrics.value=Object.fromEntries(data.hosts.map(h=>[h.server_id,h.metrics||{}]));metricsAt.value=new Date().toISOString();metricsOk.value=true
    }).catch(error=>{metricsOk.value=false;if(error.name!=='AbortError')messages.push(`主机指标：${error.message}`)}),
  )
  if(force||!versionOk.value||!versionAt||Date.now()-versionAt>60000)jobs.push(api.get('/agents/version',null,options).then(data=>{
    if(!/^\d+\.\d+\.\d+$/.test(data.current_version||''))throw new Error('目标版本未知')
    targetVersion.value=data.current_version;versionAt=Date.now();versionOk.value=true
  }).catch(error=>{versionOk.value=false;if(error.name!=='AbortError')messages.push(`Agent 版本：${error.message}`)}))
  await Promise.allSettled(jobs)
  errors.value=messages;loaded.value=true;loading.value=false
}
function showCharts(){screenRoot.value?.querySelector('.screen-charts')?.scrollIntoView({behavior:'smooth',block:'start'})}
function stop(){clearInterval(timer);timer=null;controller?.abort()}
function start(){stop();if(!active.value||document.hidden)return;refresh();timer=setInterval(()=>refresh(),10000)}
function visibility(){hidden.value=document.hidden;document.hidden?stop():start()}
function fullscreenChanged(){fullscreen.value=document.fullscreenElement===screenRoot.value}
async function toggleFullscreen(){try{document.fullscreenElement?await document.exitFullscreen():await screenRoot.value?.requestFullscreen()}catch{errors.value=['浏览器暂不支持全屏，请使用独立大屏入口。']}}
watch(active,value=>value?start():stop())
onMounted(()=>{document.addEventListener('visibilitychange',visibility);document.addEventListener('fullscreenchange',fullscreenChanged);clock=setInterval(()=>{now.value=Date.now()},1000);start()})
onUnmounted(()=>{stop();clearInterval(clock);document.removeEventListener('visibilitychange',visibility);document.removeEventListener('fullscreenchange',fullscreenChanged)})
</script>

<style scoped>
.monitor-screen{--bg:#111217;--surface:#1b1c22;--line:#303137;--muted:#a0a3ad;--text:#e8e9ed;--green:#79c99e;--amber:#eeb566;--red:#f17d82;background:var(--bg);color:var(--text);min-height:100%;padding:20px 24px;font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;font-variant-numeric:tabular-nums;color-scheme:dark}.monitor-screen:fullscreen{overflow:auto}.monitor-screen *{box-sizing:border-box}.board-head,.board-title,.head-actions,.panel-head,.panel-foot,.board-footer,.cluster-top,.pod-heading,.alert-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px}.board-head{margin-bottom:20px}.board-title{justify-content:flex-start}.brand-mark{display:grid;place-items:center;width:34px;height:34px;background:#e9a45d;color:#1b1c22;font-size:23px;font-weight:800;border-radius:5px}.board-title h1{font-size:21px;font-weight:600;margin:0;letter-spacing:.5px}.board-title p{margin:2px 0 0;color:var(--muted);font-size:11px}.board-title p span{padding:0 7px;color:#5e606b}.clock{color:var(--muted);font-size:12px;margin-right:8px}.board-button{background:#24252d;border:1px solid #3e404a;color:var(--text);border-radius:4px;padding:6px 12px;cursor:pointer;text-decoration:none;font:inherit;font-size:12px}.board-button:hover{background:#31323d}.board-button:disabled{opacity:.5}.overview{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid var(--line);background:var(--surface);border-radius:5px;margin-bottom:16px}.overview article{padding:14px 20px;border-right:1px solid var(--line)}.overview article:last-child{border:0}.overview article>span{font-size:12px;color:var(--muted)}.overview em{font-style:normal;font-size:10px;border:1px solid #454650;padding:1px 4px;border-radius:3px;margin-left:5px}.overview strong{display:block;font-weight:500;font-size:32px;line-height:1.3;margin:3px 0}.overview strong small{font-size:15px;color:var(--muted);margin-left:8px;font-weight:400}.overview p{font-size:11px;margin:0;color:var(--muted)}.healthy{color:var(--green)!important}.warning{color:var(--amber)!important}.danger,.critical{color:var(--red)!important}.unknown,.muted{color:var(--muted)!important}.board-layout{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(330px,1fr);gap:16px;align-items:start}.panel{background:var(--surface);border:1px solid var(--line);border-radius:5px;min-width:0;overflow:hidden}.panel-head{min-height:45px;padding:10px 16px;border-bottom:1px solid var(--line)}h2{font-size:14px;margin:0;font-weight:600}h2 small{font-size:11px;font-weight:400;color:var(--muted);margin-left:6px}.panel a{color:#b7bac7;text-decoration:none;font-size:11px}.panel a:hover{color:#fff}.host-toolbar{padding:12px 16px;display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap}.filters{display:flex;gap:3px}.filters button{font:inherit;font-size:11px;padding:5px 7px;border:1px solid transparent;border-radius:3px;color:var(--muted);background:transparent;cursor:pointer;white-space:nowrap}.filters button.selected{border-color:#52535d;color:var(--text);background:#2c2d36}.filters b{font-size:10px;font-weight:500;margin-left:3px}.host-toolbar input{background:#111217;border:1px solid #3d3e47;border-radius:3px;color:var(--text);font:inherit;font-size:11px;padding:6px 8px;width:150px;min-width:0}.table-wrap{overflow:auto}.host-table{border-collapse:collapse;width:100%;text-align:left;white-space:nowrap}.host-table th{font-size:10px;font-weight:500;color:var(--muted);background:#16171d;padding:8px 12px;border-bottom:1px solid var(--line)}.host-table td{padding:12px;border-bottom:1px solid #2a2b32;color:var(--text)}.host-table th:first-child,.host-table td:first-child{padding-left:16px}.host-table tbody tr:last-child td{border:0}.host-table tbody tr:hover{background:#24252d}.host-name{font:inherit;color:var(--text);background:none;border:0;padding:0;cursor:pointer;font-size:12px;text-align:left}.host-name:hover{text-decoration:underline}.host-name span{color:#747885;font-size:10px}.host-ip{display:block;font:10px/1.5 ui-monospace,Consolas,monospace;color:var(--muted);margin-top:3px}.state{display:inline-flex;gap:5px;align-items:center;font-size:10px}.state i,.live-dot{width:6px;height:6px;border-radius:50%;background:currentColor;display:inline-block;flex-shrink:0}.resource{min-width:65px}.resource b{font-size:12px;font-weight:500;color:var(--text)}.resource b small{font-size:10px;color:var(--muted);margin-left:2px}.meter{height:4px;background:#353640;margin-top:5px;border-radius:1px;overflow:hidden}.meter i{display:block;height:100%;background:#9da7bd}.meter .memory{background:#879ab4}.meter .disk{background:#a49bb6}.meter .warm{background:var(--amber)}.meter .high{background:var(--red)}.resource.stale{opacity:.45}.resource>small{font-size:9px}.collection{font-size:10px}.collection small{display:block;color:var(--muted);font-size:9px;margin-top:4px}.panel-foot{padding:10px 16px;border-top:1px solid var(--line);font-size:10px;color:var(--muted);flex-wrap:wrap}.side-panels{display:grid;gap:16px}.cluster-top{padding:12px 16px 6px}.cluster-top small{font-size:10px;color:var(--muted)}.cluster-stats{display:grid;grid-template-columns:repeat(3,1fr);margin:8px 16px 16px;gap:8px}.snapshot-stale .cluster-stats,.snapshot-stale .pod-bar,.snapshot-stale .pod-legend{opacity:.45}.cluster-stats span{font-size:10px;color:var(--muted);display:block}.cluster-stats b{font-size:22px;font-weight:500;display:block;margin-top:4px}.cluster-stats b small{font-size:11px;color:var(--muted);font-weight:400}.pod-heading{margin:0 16px 7px}.pod-heading h3{font-size:11px;font-weight:500;margin:0}.pod-heading span{font-size:10px;color:var(--muted)}.pod-bar{height:10px;display:flex;gap:2px;margin:0 16px;background:#34353e;border-radius:2px;overflow:hidden}.pod-bar i{flex-basis:0;min-width:0}.running{background:var(--green)}.failed{background:var(--red)}.other{background:#787d8b}.pod-legend{display:flex;gap:14px;flex-wrap:wrap;margin:8px 16px 12px;font-size:9px;color:var(--muted)}.pod-legend i{display:inline-block;width:6px;height:6px;margin-right:4px;border-radius:1px}.pod-legend b{font-weight:500;color:var(--text);margin-left:3px}.cluster-events{padding:0 16px 10px;font-size:10px}.cluster-events a{display:flex;gap:10px;padding:5px 0;border-top:1px solid var(--line);font-size:10px}.cluster-events a b{color:var(--amber);flex-shrink:0}.cluster-events a span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cluster-events p{margin:0;font-size:10px}.alert-toolbar{padding:8px 12px;border-bottom:1px solid var(--line)}.alert-toolbar>span{font-size:9px}.alert-list{max-height:230px;overflow:auto;min-height:110px}.alert-item{display:flex;align-items:flex-start;gap:9px;padding:12px 14px;border-bottom:1px solid var(--line)}.alert-item:last-child{border:0}.alert-item:hover{background:#24252d}.severity{font-size:9px;flex-shrink:0;border:1px solid currentColor;border-radius:3px;padding:1px 4px;color:var(--amber)}.alert-item>div{flex:1;min-width:0}.alert-item b{font-weight:500;font-size:11px;display:block;overflow-wrap:anywhere;color:var(--text)}.alert-item p{color:var(--muted);margin:4px 0 0;font-size:10px;line-height:1.5;overflow-wrap:anywhere}.alert-item time{font-size:9px;color:var(--muted);white-space:nowrap}.alert-empty{padding:24px 16px}.alert-empty>span{font-size:13px}.alert-empty p{color:var(--muted);font-size:10px;margin:6px 0 0}.source-error{color:var(--amber);font-size:10px;padding:8px 16px;margin:0;overflow-wrap:anywhere}.cluster-events .source-error{padding:0 0 5px}.empty{height:200px;text-align:center;color:var(--muted)!important;font-size:12px}.board-footer{padding-top:14px;font-size:10px;color:var(--muted);flex-wrap:wrap}.board-footer>span:first-child{display:flex;gap:7px;align-items:center}.board-footer>span:first-child>span{margin-left:12px}.live-dot{color:var(--green)}button:focus-visible,a:focus-visible,input:focus-visible{outline:2px solid #e9a45d;outline-offset:3px}
@media(min-width:1400px) and (max-height:850px){.monitor-screen{padding:14px 18px}.board-head{margin-bottom:14px}.overview article{padding:10px 18px}.overview strong{font-size:28px}.host-table td{padding:9px 12px}.host-toolbar{padding:9px 16px}.board-footer{padding-top:10px}}
@media(min-width:1800px){.monitor-screen{padding:28px 32px}.host-table td{padding:17px 16px}.host-name{font-size:14px}.resource b{font-size:15px}.host-ip{font-size:12px}.alert-list{max-height:330px}.cluster-stats{margin-top:16px;margin-bottom:24px}.cluster-stats b{font-size:28px}.alert-item{padding:16px}.alert-item b{font-size:13px}.alert-item p{font-size:12px}.state{font-size:12px}.pod-legend{font-size:11px}.cluster-events{padding-bottom:18px}}
@media(max-width:1100px){.board-layout{grid-template-columns:1fr}.side-panels{grid-template-columns:1fr 1fr;align-items:start}.clock{display:none}}
@media(max-width:700px){.monitor-screen{padding:12px}.board-head{align-items:flex-start}.head-actions{flex-wrap:wrap;justify-content:flex-end;gap:5px}.board-title p{display:none}.board-title h1{font-size:18px}.brand-mark{width:28px;height:28px;font-size:20px}.board-button{padding:5px 8px;font-size:11px}.overview{grid-template-columns:repeat(2,1fr)}.overview article{padding:12px}.overview article:nth-child(2){border-right:0}.overview article:nth-child(-n+2){border-bottom:1px solid var(--line)}.overview strong{font-size:27px}.overview strong small{font-size:12px}.side-panels{grid-template-columns:1fr}.host-table{min-width:640px}.board-footer{line-height:1.8}.board-footer>span:first-child{flex-wrap:wrap}.board-footer>span:first-child>span{margin:0}.filters{flex-wrap:wrap}}
@media(min-width:1101px) and (max-height:850px){.board-head{height:38px;margin-bottom:10px}.board-title h1{font-size:18px}.board-title p{font-size:9px}.overview article{padding:8px 16px}.overview strong{font-size:26px}.host-toolbar{padding:6px 12px}.host-table td{padding:4px 12px}.host-ip{display:inline-block;margin:0 0 0 8px;font-size:9px}.host-name{font-size:11px}.resource b{display:block;line-height:14px;font-size:11px}.resource .meter{height:3px;margin-top:3px}.collection small{display:none}.panel-head{min-height:38px;padding:8px 16px}.host-table th{padding:6px 12px}.panel-foot{padding:8px 16px}.board-footer{padding-top:10px}.cluster-stats{margin-bottom:12px}.cluster-events{padding-bottom:8px}}
@media(min-width:1101px) and (max-height:850px){.cluster-top{padding-top:6px}.cluster-stats{margin-top:4px;margin-bottom:6px}.cluster-stats b{font-size:18px}.pod-legend{margin-top:4px;margin-bottom:6px}.cluster-events{max-height:48px;overflow:auto;padding-bottom:4px}.alert-toolbar{padding-top:5px;padding-bottom:5px}.alerts-panel>.source-error{padding-top:4px;padding-bottom:4px}.alert-list{min-height:54px;max-height:90px}.alert-item{padding-top:8px;padding-bottom:8px}.alert-empty{padding:14px 16px}}
.monitor-screen.standalone-board{height:100%;min-height:0;overflow:auto}.monitor-screen:fullscreen{height:100vh;overflow:auto}
</style>
