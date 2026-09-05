<template>
  <div class="view reliability-view">
    <div class="view-head">
      <div><h1 class="view-title">服务健康</h1><p class="view-sub">稳定状态、故障事件与维护静默</p></div>
      <button class="btn" :disabled="loading" @click="reload">{{ loading ? '刷新中…' : '刷新' }}</button>
    </div>
    <div class="summary-grid">
      <div class="card metric"><strong>{{ overview.total || 0 }}</strong><span>服务</span></div>
      <div class="card metric danger"><strong>{{ overview.active_incidents || 0 }}</strong><span>活动事件</span></div>
      <div class="card metric warn"><strong>{{ overview.degraded || 0 }}</strong><span>波动中</span></div>
      <div class="card metric"><strong>{{ overview.silenced || 0 }}</strong><span>静默中</span></div>
    </div>
    <div class="health-tabs"><button class="g-tab" :class="{active:tab==='incidents'}" @click="tab='incidents'">故障事件</button><button class="g-tab" :class="{active:tab==='silences'}" @click="tab='silences'">维护静默</button></div>
    <section v-if="tab==='incidents'">
      <div class="filter-row"><select v-model="status" class="select" @change="loadIncidents"><option value="">全部状态</option><option value="open">待确认</option><option value="acknowledged">已确认</option><option value="resolved">已恢复</option></select></div>
      <div v-if="loading" class="loading"><span class="spinner"></span>加载中…</div>
      <div v-else-if="!incidents.length" class="card empty">暂无服务故障事件</div>
      <div v-else class="table-wrap card"><table class="table"><thead><tr><th>服务</th><th>主机</th><th>状态</th><th>开始时间</th><th>恢复时间</th><th>失败原因</th><th>操作</th></tr></thead><tbody><tr v-for="row in incidents" :key="row.id"><td><a v-if="row.entry_url" :href="row.entry_url" target="_blank" rel="noopener" class="service-link">{{ incidentServiceName(row) }}</a><span v-else>{{ incidentServiceName(row) }}</span><small class="cell-sub mono">{{ row.plaza_key }}</small></td><td>{{ row.server_name || '-' }}<small v-if="row.server_host" class="cell-sub mono">{{ row.server_host }}</small></td><td><span class="tag" :class="row.status==='resolved'?'tag-green':row.status==='acknowledged'?'tag-amber':'tag-red'">{{ statusText(row.status) }}</span></td><td>{{ fmtTime(row.opened_at) }}</td><td>{{ fmtTime(row.resolved_at) }}</td><td class="error-cell"><span class="error-type">{{ errorTypeText(row.last_error_code) }}</span>{{ row.last_error || '-' }}</td><td><button v-if="row.status==='open'" class="btn btn-sm" @click="ack(row)">确认</button></td></tr></tbody></table></div>
    </section>
    <section v-else>
      <div class="card silence-form"><select v-model="silenceForm.plaza_key" class="select"><option value="">选择服务</option><option v-for="service in services" :key="service.key" :value="service.key">{{ service.name }}</option></select><input v-model="silenceForm.ends_at" class="input" type="datetime-local" /><input v-model.trim="silenceForm.reason" class="input" placeholder="静默原因" /><button class="btn btn-primary" @click="createSilence">创建静默</button></div>
      <div v-if="!silences.length" class="card empty">暂无静默记录</div>
      <table v-else class="table card"><thead><tr><th>服务</th><th>开始</th><th>结束</th><th>原因</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="row in silences" :key="row.id"><td>{{ serviceName(row.plaza_key) }}</td><td>{{ fmtTime(row.starts_at) }}</td><td>{{ fmtTime(row.ends_at) }}</td><td>{{ row.reason }}</td><td><span class="tag" :class="row.active?'tag-green':'tag-slate'">{{ row.active?'生效中':'已结束' }}</span></td><td><button v-if="row.active" class="btn btn-sm" @click="endSilence(row)">提前结束</button></td></tr></tbody></table>
    </section>
    <p class="data-note">数据更新时间：{{ fmtTime(generatedAt) }}。页面可见时每 15 秒刷新摘要，不会触发外部服务探测。</p>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { api, fmtTime, toast } from '../api'

const tab=ref('incidents'),status=ref(''),loading=ref(false),incidents=ref([]),silences=ref([]),services=ref([]),overview=ref({}),generatedAt=ref(null)
const silenceForm=reactive({plaza_key:'',ends_at:'',reason:''})
let timer=null,controller=null,summaryController=null
const serviceName=(key)=>services.value.find(item=>item.key===key)?.name||key
const incidentServiceName=(row)=>row.service_name||serviceName(row.plaza_key)
const statusText=(value)=>({open:'待确认',acknowledged:'已确认',resolved:'已恢复'}[value]||value)
const errorTypeText=(value)=>({http_status:'HTTP 状态',timeout:'连接超时',dns_error:'DNS 解析',tls_certificate:'TLS 证书',tls_error:'TLS 连接',connection_refused:'拒绝连接',network_error:'网络异常'}[value]||'网络异常')
function applySummary(summary){overview.value=summary.summary||{};generatedAt.value=summary.generated_at}
async function loadSummary(){summaryController?.abort();summaryController=new AbortController();try{applySummary(await api.get('/services/plaza/health-overview',{hours:24},{signal:summaryController.signal}))}catch(error){if(error.name!=='AbortError')console.warn('服务健康摘要刷新失败',error)}}
async function reload(){if(loading.value)return;controller?.abort();summaryController?.abort();controller=new AbortController();loading.value=true;try{const [summary,eventData,silenceData,serviceData]=await Promise.all([api.get('/services/plaza/health-overview',{hours:24},{signal:controller.signal}),api.get('/services/plaza/incidents',{status:status.value||undefined,hours:24*30,limit:200},{signal:controller.signal}),api.get('/services/plaza/silences',{}, {signal:controller.signal}),api.get('/services/plaza',{}, {signal:controller.signal})]);applySummary(summary);incidents.value=eventData.items||[];silences.value=silenceData||[];services.value=serviceData||[]}catch(error){if(error.name!=='AbortError')toast(`服务健康加载失败：${error.message}`,'error')}finally{loading.value=false}}
async function loadIncidents(){try{const data=await api.get('/services/plaza/incidents',{status:status.value||undefined,hours:24*30,limit:200});incidents.value=data.items||[]}catch(error){toast(error.message,'error')}}
async function ack(row){try{const data=await api.post(`/services/plaza/incidents/${row.id}/acknowledge`);Object.assign(row,data);toast('事件已确认','success')}catch(error){toast(error.message,'error')}}
async function createSilence(){if(!silenceForm.plaza_key||!silenceForm.ends_at||!silenceForm.reason){toast('请选择服务并填写结束时间和原因','error');return}try{await api.post('/services/plaza/silences',{plaza_key:silenceForm.plaza_key,ends_at:new Date(silenceForm.ends_at).toISOString(),reason:silenceForm.reason});Object.assign(silenceForm,{plaza_key:'',ends_at:'',reason:''});await reload();toast('维护静默已创建','success')}catch(error){toast(error.message,'error')}}
async function endSilence(row){if(!confirm('确认提前结束该静默？'))return;try{await api.del(`/services/plaza/silences/${row.id}`);await reload();toast('静默已结束','success')}catch(error){toast(error.message,'error')}}
function schedule(){clearInterval(timer);timer=setInterval(()=>{if(document.visibilityState==='visible'&&!loading.value)loadSummary()},15000)}
onMounted(()=>{reload();schedule()})
onBeforeUnmount(()=>{clearInterval(timer);controller?.abort();summaryController?.abort()})
</script>

<style scoped>
.reliability-view{padding:22px}.summary-grid{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:12px;margin-bottom:16px}.metric{padding:16px;display:flex;flex-direction:column}.metric strong{font-size:26px}.metric span,.data-note{font-size:12px;color:var(--muted)}.metric.danger strong{color:var(--danger)}.metric.warn strong{color:var(--warn)}.health-tabs{display:flex;gap:8px;margin:14px 0}.filter-row{display:flex;max-width:220px;margin-bottom:12px}.empty{padding:36px;text-align:center;color:var(--muted)}.table-wrap{overflow-x:auto}.table-wrap .table{box-shadow:none;margin:0;min-width:980px}.service-link{color:var(--primary);text-decoration:none}.service-link:hover{text-decoration:underline}.cell-sub{display:block;margin-top:3px;color:var(--muted);font-size:11px}.error-cell{max-width:340px;color:var(--danger)}.error-type{display:inline-block;margin-right:6px;padding:2px 6px;border-radius:4px;background:color-mix(in srgb,var(--danger) 12%,transparent);font-size:11px;white-space:nowrap}.silence-form{display:grid;grid-template-columns:1fr 1fr 1.5fr auto;gap:10px;padding:14px;margin-bottom:14px}.data-note{text-align:right;margin-top:12px}@media(max-width:800px){.summary-grid{grid-template-columns:repeat(2,1fr)}.silence-form{grid-template-columns:1fr}.reliability-view{padding:14px}}
</style>
