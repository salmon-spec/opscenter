<template>
  <div class="host-screen" ref="screenRoot">
    <header class="board-head">
      <div><div class="eyebrow">OPSCENTER · HOST MONITOR</div><h1>主机监控大屏</h1><p>每 10 秒刷新 · 异常主机优先 · {{ clockText }}</p></div>
      <div class="head-actions"><router-link v-if="standalone" to="/" class="board-button">返回工作台</router-link><router-link to="/hosts" class="board-button">主机管理</router-link><button class="board-button" :disabled="loading" @click="refresh(true)">{{ loading ? '刷新中…' : '立即刷新' }}</button><button class="board-button" @click="toggleFullscreen">{{ fullscreen ? '退出全屏' : '全屏' }}</button></div>
    </header>

    <div v-if="errors.length" class="board-error" role="alert"><b>部分数据暂不可用</b><span v-for="message in errors" :key="message">{{ message }}</span><span>保留最近数据，相关主机不会继续标为正常。</span></div>

    <section class="totals" aria-label="主机状态总览">
      <article><span>在线主机</span><strong :class="stats.offline || stats.unknown ? 'warn-text' : 'good-text'">{{ hosts.length ? stats.online : '—' }}<small>/ {{ hosts.length || '—' }}</small></strong><p>离线 {{ stats.offline }} · 状态待确认 {{ stats.unknown }}</p></article>
      <article><span>需要关注</span><strong :class="stats.attention ? 'warn-text' : 'good-text'">{{ hosts.length ? stats.attention : '—' }}</strong><p>离线、资源压力或采集异常</p></article>
      <article><span>Agent 运行正常</span><strong :class="stats.agent !== hosts.length ? 'warn-text' : 'good-text'">{{ hosts.length ? stats.agent : '—' }}<small>/ {{ hosts.length || '—' }}</small></strong><p>统一目标 v{{ targetVersion || '待确认' }}</p></article>
      <article><span>Agent 版本一致</span><strong :class="stats.version !== hosts.length ? 'warn-text' : 'good-text'">{{ hosts.length ? stats.version : '—' }}<small>/ {{ hosts.length || '—' }}</small></strong><p>{{ targetVersion ? `与 v${targetVersion} 对齐` : '正在读取部署版本' }}</p></article>
      <article><span>指标实时</span><strong :class="stats.fresh !== hosts.length ? 'warn-text' : 'good-text'">{{ hosts.length ? stats.fresh : '—' }}<small>/ {{ hosts.length || '—' }}</small></strong><p>CPU / 内存 / 磁盘均在 90 秒内</p></article>
    </section>

    <section class="board-toolbar">
      <div class="filters" aria-label="主机筛选"><button v-for="item in filters" :key="item.key" :class="{active:filter===item.key}" @click="filter=item.key">{{ item.name }} <b>{{ item.count }}</b></button></div>
      <input v-model="search" aria-label="搜索主机" placeholder="搜索主机名称、IP 或节点" />
      <span class="update-time">列表 {{ time(inventoryAt) }} · 指标 {{ time(metricsAt) }}</span>
    </section>

    <div v-if="!loaded" class="empty-board">正在读取主机状态…</div>
    <div v-else-if="!hosts.length" class="empty-board">暂无可用主机清单，请检查主机管理与连接状态。</div>
    <div v-else-if="!visibleHosts.length" class="empty-board">没有符合筛选条件的主机。</div>
    <section v-else class="host-grid" aria-label="各主机监控状态">
      <article v-for="host in visibleHosts" :key="host.id" class="host-card" :class="host.level">
        <div class="card-heading"><div><h2>{{ host.name }}</h2><p>{{ host.kubernetes_node_name || host.node_role || host.tags?.join(' · ') || host.runtime_type || '主机' }}</p></div><span class="state-badge"><i></i>{{ host.label }}</span></div>
        <div class="host-address">{{ host.host }}<span v-if="host.lan_ip && host.lan_ip !== host.host">LAN {{ host.lan_ip }}</span></div>
        <div class="host-metrics">
          <div v-for="sample in host.samples" :key="sample.key" class="metric" :class="{dim:!host.online || !sample.fresh}">
            <div><span>{{ metricLabels[sample.key] }}</span><b>{{ sample.value == null ? '—' : sample.value.toFixed(1)+'%' }}</b></div>
            <div class="track"><i :class="sample.value >= 90 ? 'high' : sample.value >= 80 ? 'warm' : ''" :style="{width:(sample.value ?? 0)+'%'}"></i></div>
            <small>{{ !host.online || !sample.fresh ? '当前数据不可用' : '实时' }} · {{ time(sample.at) }}</small>
          </div>
        </div>
        <div class="agent-row"><span><i :class="host.agentOk ? 'dot-good' : 'dot-warn'"></i>Agent {{ host.agent_status === 'running' ? '运行中' : agentLabel(host.agent_status) }}</span><b :class="host.versionOk && versionOk ? 'good-text' : 'warn-text'">v{{ host.agent_version || '未知' }}<small v-if="!versionOk"> · 目标版本待确认</small><small v-else-if="!host.versionOk"> · 待统一</small></b></div>
        <div class="card-footer"><span>上报 {{ time(host.last_seen) }} <small>{{ ageText(host.heartbeatAge) }} · 指标 {{ time(oldestAt(host.samples)) }}</small></span><button @click="openMonitor(host)">查看监控 →</button></div>
        <div v-if="host.reasons.length" class="reasons">{{ host.reasons.join(' · ') }}</div>
        <div v-else class="reasons healthy-note">主机与采集链路正常</div>
      </article>
    </section>

    <footer class="board-footer"><span><i class="dot-good"></i>正常 <i class="dot-warn"></i>需关注 <i class="dot-danger"></i>离线 / 严重异常 <i class="dot-unknown"></i>状态待确认</span><span>资源 ≥80% 提醒 · ≥90% 严重 · {{ active && !hidden ? '自动刷新中' : '页面未激活，暂停刷新' }}</span></footer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useTabActive } from '../workbench/tabs'
import { useHostContext } from '../hostContext'
import { hostView, sortHosts } from '../utils/hostScreen'

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
function agentLabel(value){return {stopped:'已停止',not_deployed:'未安装',error:'异常',deploying:'更新中',unknown:'待确认'}[value]||'待确认'}
async function openMonitor(host){await refreshHosts();selectHost(host.id);router.push('/system/monitor')}
async function refresh(force=false){
  if(loading.value||(!force&&(!active.value||document.hidden)))return
  loading.value=true;controller=new AbortController()
  const options={signal:controller.signal,timeoutMs:15000},end=new Date(),messages=[]
  const jobs=[
    api.get('/modules/host-inventory/api/v1/hosts',null,options).then(data=>{
      if(!Array.isArray(data.items)||data.status==='unavailable')throw new Error('主机清单暂不可用')
      hosts.value=data.items;inventoryAt.value=new Date().toISOString();inventoryOk.value=true
    }).catch(error=>{inventoryOk.value=false;if(error.name!=='AbortError')messages.push(`主机状态：${error.message}`)}),
    api.get('/metrics/hosts/overview',{start:new Date(end.getTime()-10*60000).toISOString(),end:end.toISOString(),resolution:'raw',metrics:'cpu,memory,disk'},options).then(data=>{
      if(!Array.isArray(data.hosts))throw new Error('指标响应格式异常')
      metrics.value=Object.fromEntries(data.hosts.map(h=>[h.server_id,h.metrics||{}]));metricsAt.value=new Date().toISOString();metricsOk.value=true
    }).catch(error=>{metricsOk.value=false;if(error.name!=='AbortError')messages.push(`主机指标：${error.message}`)}),
  ]
  if(force||!versionOk.value||!versionAt||Date.now()-versionAt>60000)jobs.push(api.get('/agents/version',null,options).then(data=>{
    if(!/^\d+\.\d+\.\d+$/.test(data.current_version||''))throw new Error('目标版本未知')
    targetVersion.value=data.current_version;versionAt=Date.now();versionOk.value=true
  }).catch(error=>{versionOk.value=false;if(error.name!=='AbortError')messages.push(`Agent 版本：${error.message}`)}))
  await Promise.allSettled(jobs)
  errors.value=messages;loaded.value=true;loading.value=false
}
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
.host-screen{--fg:#e5edf8;--muted:#8f9fb7;--line:#24364c;min-height:100%;padding:24px;background:radial-gradient(ellipse at 10% 0,#122c42 0,transparent 45%),#0a1320;color:var(--fg);font-variant-numeric:tabular-nums}.host-screen:fullscreen{overflow:auto}.board-head{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:22px}.eyebrow{font-size:10px;font-weight:700;letter-spacing:3px;color:#62bfcf}.board-head h1{margin:6px 0;font-size:27px;letter-spacing:2px}.board-head p,.totals p{margin:5px 0 0;color:var(--muted);font-size:11px}.head-actions{display:flex;gap:8px;flex-wrap:wrap}.board-button{background:#17273b;color:#cee0f6;border:1px solid #31465f;border-radius:7px;padding:8px 12px;font-size:12px;text-decoration:none;cursor:pointer}.board-button:disabled{opacity:.5}.board-button:hover{border-color:#5cc8d1}.totals{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:20px}.totals article{padding:16px 18px;border:1px solid var(--line);background:#101e2fc9;border-radius:10px}.totals article>span{font-size:12px;color:#b0c3d9}.totals strong{display:block;font-size:34px;line-height:1.2;margin:8px 0}.totals strong small{font-size:17px;font-weight:500;color:var(--muted);margin-left:5px}.good-text{color:#60dcae}.warn-text{color:#ffc56d}.board-toolbar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:0 0 14px}.filters{display:flex;gap:5px;flex-wrap:wrap}.filters button{padding:7px 10px;background:#111e2e;border:1px solid var(--line);border-radius:6px;color:#9cafc9;cursor:pointer;font-size:11px}.filters button.active{color:#77e3d5;border-color:#2d8b87;background:#153533}.filters b{margin-left:4px}.board-toolbar input{background:#0e1c2c;border:1px solid var(--line);border-radius:6px;padding:8px 10px;color:var(--fg);width:210px;font-size:11px}.update-time{margin-left:auto;font-size:10px;color:var(--muted)}.host-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.host-card{min-width:0;padding:16px 18px 10px;background:linear-gradient(120deg,#152438,#101b2b);border:1px solid var(--line);border-top:3px solid #43be94;border-radius:10px}.host-card.warning{border-top-color:#e8b654}.host-card.danger{border-top-color:#ff6979;background:linear-gradient(120deg,#342232,#171d2c)}.host-card.unknown{border-top-color:#8694ab}.card-heading{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}.card-heading h2{margin:0;font-size:16px;font-weight:650}.card-heading p{margin:5px 0;color:var(--muted);font-size:10px}.state-badge{display:flex;align-items:center;gap:5px;border-radius:20px;padding:4px 8px;background:#1a413b;color:#78e6bc;font-size:10px;white-space:nowrap}.warning .state-badge{background:#453822;color:#ffca76}.danger .state-badge{background:#4e2835;color:#ff8996}.unknown .state-badge{background:#293448;color:#b2bdd0}.state-badge i,.board-footer i,.agent-row i{width:6px;height:6px;display:inline-block;border-radius:50%;background:currentColor}.host-address{display:flex;gap:8px;flex-wrap:wrap;margin:5px 0 16px;color:#b3c9e5;font:11px Consolas,monospace}.host-address span{color:#7189a6}.host-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}.metric>div:first-child{display:flex;align-items:baseline;justify-content:space-between;gap:4px;font-size:10px;color:#93a8c4}.metric b{font-size:17px;color:#e4edf9}.track{height:5px;margin-top:9px;border-radius:5px;background:#27374e;overflow:hidden}.track i{display:block;height:100%;border-radius:5px;background:#52caa7;transition:width .4s}.track i.warm{background:#f2b65d}.track i.high{background:#f27380}.metric small{display:block;margin:7px 0;color:#8295af;font-size:9px;white-space:nowrap}.metric.dim b{color:#8996ab}.metric.dim .track i{background:#65758b}.agent-row{display:flex;justify-content:space-between;align-items:center;gap:6px;padding:11px 0 9px;border-bottom:1px solid var(--line);margin-top:5px;font-size:11px}.agent-row>span{display:flex;gap:6px;align-items:center;color:#b0c2da}.agent-row b{font-size:11px}.agent-row small{font-weight:400;font-size:10px}.dot-good{background:#54d4a6!important}.dot-warn{background:#efba66!important}.dot-danger{background:#fb7d8c!important}.dot-unknown{background:#8796ad!important}.card-footer{display:flex;align-items:center;justify-content:space-between;gap:6px;padding-top:10px;font-size:10px;color:#94a6bf}.card-footer small{color:#6d829e}.card-footer button{padding:0;background:none;border:none;color:#71ced5;cursor:pointer;font-size:10px;white-space:nowrap}.reasons{min-height:24px;margin-top:7px;font-size:10px;color:#efbe79;line-height:1.5}.healthy-note{color:#668f8a}.board-footer{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:10px;margin-top:18px}.board-footer i{margin:0 4px 0 10px}.board-footer i:first-child{margin-left:0}.board-error{display:flex;flex-wrap:wrap;gap:8px;padding:10px 14px;margin-bottom:16px;border:1px solid #78523a;color:#f4c18c;background:#382b24;border-radius:8px;font-size:11px}.empty-board{text-align:center;padding:70px;color:var(--muted)}@media(min-width:1900px){.host-screen{padding:28px 36px}.host-grid{gap:18px}.host-card{padding:20px 24px 12px}.host-metrics{gap:20px}.metric b{font-size:23px}.card-heading h2{font-size:19px}}@media(max-width:1100px){.host-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.totals{grid-template-columns:repeat(3,minmax(0,1fr))}.board-head{align-items:flex-start}.host-screen{padding:18px}}@media(max-width:680px){.host-screen{padding:12px}.board-head{flex-direction:column;gap:12px}.host-grid{grid-template-columns:1fr}.totals{grid-template-columns:repeat(2,minmax(0,1fr))}.totals article{padding:12px}.totals strong{font-size:27px}.board-toolbar input{width:100%}.update-time{margin-left:0}.head-actions{gap:5px}.board-button{padding:7px 9px}.board-footer{line-height:1.8}}
</style>
<style scoped>
@media(min-width:1101px){
  .host-screen{padding:16px 20px}.board-head{margin-bottom:14px}.board-head h1{font-size:25px}.totals{margin-bottom:12px}.totals article{padding:11px 15px}.totals strong{font-size:29px;margin:5px 0}.host-grid{gap:12px}.host-card{padding:12px 15px 9px}.host-address{margin:4px 0 10px}.metric small{margin:4px 0}.agent-row{padding:7px 0;margin-top:3px}.card-footer{padding-top:7px}.healthy-note{display:none}.reasons{min-height:0;margin-top:6px}.board-footer{margin-top:12px}
}
@media(min-width:1101px) and (max-height:850px){
  .host-screen{padding:10px 14px}.board-head{margin-bottom:9px}.board-head h1{font-size:21px;margin:3px 0}.eyebrow{font-size:9px}.board-head p{font-size:10px;margin:2px 0}.board-button{padding:6px 10px}.totals{gap:8px;margin-bottom:9px}.totals article{padding:7px 12px}.totals article>span{font-size:10px}.totals strong{font-size:24px;margin:3px 0}.totals strong small{font-size:14px}.totals p{font-size:9px;margin-top:2px}.board-toolbar{margin-bottom:8px;gap:9px}.board-toolbar input{padding:6px 9px}.filters button{padding:5px 8px}.host-grid{gap:8px}.host-card{padding:9px 12px 8px}.card-heading h2{font-size:14px}.card-heading p{display:none}.host-address{font-size:10px;margin:4px 0 8px}.host-metrics{gap:12px}.metric b{font-size:18px}.metric small{display:none}.track{margin-top:5px;height:4px}.agent-row{padding:6px 0;margin-top:5px;font-size:10px}.agent-row b{font-size:10px}.card-footer{padding-top:6px;font-size:9px}.card-footer button{font-size:9px}.reasons{font-size:9px;margin-top:4px}.board-footer{margin-top:8px;font-size:9px}
}
</style>
