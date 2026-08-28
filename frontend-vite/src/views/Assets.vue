<template>
  <div class="view assets-workbench">
    <div class="view-head">
      <div><h1 class="view-title">资产管理</h1><p class="view-sub">单主机工作台 · 容器、服务与系统资源统一管理</p></div>
      <div class="head-actions"><span v-if="lastUpdated" class="muted">更新于 {{ lastUpdated }}</span><button class="btn" :disabled="refreshing" @click="refreshCurrent">↻ 刷新</button></div>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>正在加载主机…</div>
    <EmptyState v-else-if="!hosts.length" icon="🖥" text="暂无主机，请在资源管理中录入" />

    <template v-else>
      <section class="card host-switcher">
        <button v-for="host in hosts" :key="host.id" class="host-pill" :class="{ active: host.id === selectedHostId }" @click="selectHost(host)">
          <span class="state-dot" :class="host.status === 'online' ? 'online' : 'offline'"></span>{{ host.name }}
        </button>
      </section>

      <section v-if="currentHost" class="card host-summary">
        <div class="summary-identity"><div class="summary-name">{{ currentHost.name }}</div><div class="muted summary-meta">{{ currentHost.host }} · Agent {{ monitor?.agent_version || currentHost.agent_version || '未部署' }} · Docker {{ containerCounts.running }} 运行 / {{ containerCounts.stopped }} 停止</div></div>
        <div v-for="item in summaryMetrics" :key="item.key" class="summary-metric"><div><span>{{ item.label }}</span><b>{{ item.value }}%</b></div><div class="metric-track"><i :style="{ width: `${item.value}%` }"></i></div></div>
        <div class="summary-spacer"></div><button class="btn btn-sm btn-primary" @click="openHostTerminal">终端连接</button><button class="btn btn-sm" @click="power('reboot')">重启</button><button class="btn btn-sm btn-danger" @click="power('shutdown')">关机</button>
      </section>

      <section class="card workbench">
        <nav class="asset-tabs">
          <button v-for="tab in tabs" :key="tab.key" class="asset-tab" :class="{ active: activeTab === tab.key }" @click="activeTab = tab.key">
            {{ tab.label }}<span v-if="tab.key === 'containers'" class="tab-count">{{ containerTotal }}</span>
          </button>
        </nav>

        <div v-if="activeTab === 'overview'" class="tab-panel overview-panel">
          <div class="metric-grid">
            <div class="metric-card"><span>负载 1 / 5 / 15 分钟</span><b>{{ metric('load1') }} / {{ metric('load5') }} / {{ metric('load15') }}</b></div>
            <div class="metric-card"><span>Swap</span><b>{{ metric('swap') }}%</b><small>{{ fmtBytes(metric('swap_used')) }} / {{ fmtBytes(metric('swap_total')) }}</small></div>
            <div class="metric-card"><span>运行时间</span><b>{{ fmtDuration(metric('uptime')) }}</b></div>
            <div class="metric-card"><span>监控来源</span><b>{{ monitorSource }}</b><small>{{ monitor?.error || '数据采集正常' }}</small></div>
          </div>
        </div>

        <div v-else-if="activeTab === 'containers'" class="tab-panel">
          <div class="container-toolbar">
            <button class="btn btn-sm btn-danger-soft" :disabled="operationBusy" @click="requestPrune">清理停止容器</button><span class="toolbar-divider"></span>
            <button v-for="action in lifecycleActions" :key="action.key" class="btn btn-sm" :disabled="!selectedContainers.size || operationBusy" @click="runContainerAction(action.key, action.label)">{{ action.label }}</button>
            <button class="btn btn-sm btn-danger" :disabled="!selectedContainers.size || operationBusy" @click="requestDelete">删除</button>
            <div class="toolbar-right"><select v-model="containerFilter" class="select" @change="loadContainers"><option value="all">状态：所有</option><option value="running">运行中</option><option value="paused">已暂停</option><option value="stopped">已停止</option></select><input v-model="containerSearch" class="search" placeholder="搜索名称或镜像" @input="scheduleContainerSearch" /><button class="btn btn-sm" @click="columnHelp = !columnHelp">⚙ 列表设置</button></div>
          </div>
          <div v-if="columnHelp" class="column-tip"><span>显示列：</span><label v-for="column in optionalColumns" :key="column.key"><input v-model="visibleColumns[column.key]" type="checkbox" @change="saveColumns" /> {{ column.label }}</label></div>
          <div v-if="containerLoading" class="loading"><span class="spinner"></span>正在读取 Docker 容器…</div>
          <div v-else class="table-scroll"><table class="table container-table">
            <thead><tr><th class="check-col"><input type="checkbox" :checked="allVisibleSelected" @change="toggleAll($event.target.checked)" /></th><th>名称</th><th v-if="visibleColumns.image">镜像</th><th>状态</th><th v-if="visibleColumns.resource">资源使用率</th><th v-if="visibleColumns.ip">IP 地址</th><th v-if="visibleColumns.ports">端口</th><th v-if="visibleColumns.service">关联服务</th><th class="actions-col">操作</th></tr></thead>
            <tbody>
              <tr v-for="container in containers" :key="container.id">
                <td class="check-col"><input type="checkbox" :checked="selectedContainers.has(container.id)" @change="toggleContainer(container.id, $event.target.checked)" /></td>
                <td><button class="name-link" @click="openInspect(container)">{{ container.name }}</button><div class="muted mono tiny">{{ container.short_id }}</div></td>
                <td v-if="visibleColumns.image"><div class="image-name" :title="container.image">{{ container.image }}</div></td>
                <td><span class="container-state" :class="stateClass(container.state)"><i></i>{{ stateLabel(container.state) }}</span><div v-if="container.health !== 'none'" class="muted tiny">健康：{{ container.health }}</div></td>
                <td v-if="visibleColumns.resource" class="resource-cell"><div>CPU: {{ Number(container.cpu_percent || 0).toFixed(2) }}%</div><div>内存: {{ Number(container.memory_percent || 0).toFixed(2) }}%</div></td>
                <td v-if="visibleColumns.ip" class="mono">{{ container.ip_addresses?.join(', ') || '-' }}</td>
                <td v-if="visibleColumns.ports"><div v-for="port in container.ports || []" :key="`${port.private}-${port.host_port}`" class="port-line">{{ formatPort(port) }}</div><span v-if="!container.ports?.length">-</span></td>
                <td v-if="visibleColumns.service">{{ container.service?.name || '-' }}</td>
                <td class="row-actions"><button class="link-btn" :disabled="container.state !== 'running'" @click="openContainerTerminal(container)">终端</button><button class="link-btn" @click="openContainerLogs(container)">日志</button><button class="link-btn" @click="openInspect(container)">详情</button></td>
              </tr>
              <tr v-if="!containers.length"><td :colspan="tableColumnCount"><EmptyState icon="📦" text="当前条件下没有容器" style="padding:28px" /></td></tr>
            </tbody>
          </table></div>
        </div>

        <div v-else-if="activeTab === 'services'" class="tab-panel table-scroll">
          <div v-if="serviceLoading" class="loading"><span class="spinner"></span>加载服务…</div>
          <table v-else class="table"><thead><tr><th>服务</th><th>容器/镜像</th><th>端口</th><th>状态</th><th>操作</th></tr></thead><tbody>
            <tr v-for="svc in hostServices" :key="svc.id"><td><b>{{ svc.name }}</b><div class="muted tiny">{{ svc.description || '' }}</div></td><td class="mono">{{ svc.container_name || svc.image || '-' }}</td><td class="mono">{{ svc.ports || '-' }}</td><td><span class="tag" :class="isServiceOnline(svc) ? 'tag-green' : 'tag-slate'">{{ svc.status || 'unknown' }}</span></td><td class="row-actions"><button class="link-btn" @click="serviceControl(svc, 'start')">启动</button><button class="link-btn" @click="serviceControl(svc, 'stop')">停止</button><button class="link-btn" @click="serviceControl(svc, 'restart')">重启</button><button class="link-btn" @click="openServiceLogs(svc)">日志</button></td></tr>
            <tr v-if="!hostServices.length"><td colspan="5"><EmptyState icon="📦" text="该主机暂无服务" style="padding:28px" /></td></tr>
          </tbody></table>
        </div>

        <div v-else-if="activeTab === 'disks'" class="tab-panel table-scroll"><table class="table"><thead><tr><th>挂载点</th><th>设备</th><th>已用 / 总量</th><th>使用率</th></tr></thead><tbody><tr v-for="disk in monitor?.disks || []" :key="`${disk.device}-${disk.mountpoint}`"><td class="mono">{{ disk.mountpoint }}</td><td class="mono">{{ disk.device }}</td><td>{{ fmtBytes(disk.used) }} / {{ fmtBytes(disk.total) }}</td><td>{{ disk.percent }}%</td></tr><tr v-if="!monitor?.disks?.length"><td colspan="4"><EmptyState icon="💽" text="暂无磁盘明细" style="padding:28px" /></td></tr></tbody></table></div>
        <div v-else-if="activeTab === 'network'" class="tab-panel table-scroll"><table class="table"><thead><tr><th>网卡</th><th>接收速率</th><th>发送速率</th><th>接收错误</th><th>发送错误</th></tr></thead><tbody><tr v-for="nic in monitor?.network_interfaces || []" :key="nic.interface"><td class="mono">{{ nic.interface }}</td><td>{{ nic.rx_rate_mbps }} Mbps</td><td>{{ nic.tx_rate_mbps }} Mbps</td><td>{{ nic.rx_errors || 0 }}</td><td>{{ nic.tx_errors || 0 }}</td></tr><tr v-if="!monitor?.network_interfaces?.length"><td colspan="5"><EmptyState icon="🌐" text="暂无网卡明细" style="padding:28px" /></td></tr></tbody></table></div>
        <div v-else class="tab-panel table-scroll"><table class="table"><thead><tr><th>进程</th><th>PID</th><th>CPU</th><th>内存</th></tr></thead><tbody><tr v-for="proc in monitor?.top_cpu_processes || []" :key="proc.pid"><td class="mono">{{ proc.command }}</td><td>{{ proc.pid }}</td><td>{{ proc.cpu_percent }}%</td><td>{{ proc.memory_percent }}%</td></tr><tr v-if="!monitor?.top_cpu_processes?.length"><td colspan="4"><EmptyState icon="⚙" text="暂无进程明细" style="padding:28px" /></td></tr></tbody></table></div>
      </section>
    </template>

    <TerminalPanel v-if="terminalSession" :session-id="terminalSession.id" :title="terminalSession.title" :allow-files="terminalSession.allowFiles" @close="terminalSession = null" />
    <Modal :visible="!!logTarget" :title="`日志 · ${logTarget?.name || ''}`" width="820px" @close="logTarget = null"><div v-if="logLoading" class="loading"><span class="spinner"></span>加载日志…</div><pre v-else class="log-pre">{{ logs || '（无日志输出）' }}</pre></Modal>
    <Modal :visible="!!inspectTarget" :title="`容器详情 · ${inspectTarget?.name || ''}`" width="880px" @close="inspectTarget = null">
      <div v-if="inspectLoading" class="loading"><span class="spinner"></span>加载详情…</div>
      <div v-else-if="inspectData" class="inspect-grid"><div><span>容器 ID</span><b class="mono">{{ inspectData.short_id }}</b></div><div><span>镜像</span><b>{{ inspectData.image }}</b></div><div><span>状态</span><b>{{ stateLabel(inspectData.state) }}</b></div><div><span>重启策略</span><b>{{ inspectData.restart_policy }}</b></div><div><span>网络</span><b>{{ inspectData.networks?.join(', ') || '-' }}</b></div><div><span>工作目录</span><b class="mono">{{ inspectData.working_dir || '-' }}</b></div><div class="inspect-wide"><span>挂载</span><pre>{{ JSON.stringify(inspectData.mounts || [], null, 2) }}</pre></div><div class="inspect-wide"><span>环境变量（敏感值已遮蔽）</span><pre>{{ (inspectData.environment || []).map(item => `${item.key}=${item.value}`).join('\n') || '-' }}</pre></div></div>
    </Modal>
    <Modal :visible="confirmState.visible" :title="confirmState.title" width="540px" @close="closeConfirm"><div class="danger-warning">⚠ 危险操作</div><p>{{ confirmState.message }}</p><label v-if="confirmState.kind === 'delete'" class="force-option"><input v-model="confirmState.force" type="checkbox" /> 强制删除仍在运行的容器</label><p class="muted">请输入“{{ confirmState.word }}”继续：</p><input v-model="confirmState.input" class="confirm-input" :placeholder="confirmState.word" @keyup.enter="confirmDangerous" /><div class="confirm-actions"><button class="btn" @click="closeConfirm">取消</button><button class="btn btn-danger" :disabled="confirmState.input !== confirmState.word || operationBusy" @click="confirmDangerous">确认执行</button></div></Modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import TerminalPanel from '../components/TerminalPanel.vue'

const route = useRoute(), router = useRouter()
const hosts = ref([]), selectedHostId = ref(''), loading = ref(true), refreshing = ref(false), monitor = ref(null)
const containers = ref([]), containerTotal = ref(0), containerLoading = ref(false), containerFilter = ref('all'), containerSearch = ref(''), selectedContainers = ref(new Set()), operationBusy = ref(false)
const activeTab = ref('containers'), hostServices = ref([]), serviceLoading = ref(false), terminalSession = ref(null)
const logTarget = ref(null), logLoading = ref(false), logs = ref(''), inspectTarget = ref(null), inspectLoading = ref(false), inspectData = ref(null)
const lastUpdated = ref(''), columnHelp = ref(false)
const confirmState = reactive({ visible: false, kind: '', title: '', message: '', word: '', input: '', force: false })
let refreshTimer = null, searchTimer = null
const tabs = [{key:'overview',label:'概览'},{key:'containers',label:'容器'},{key:'services',label:'服务'},{key:'disks',label:'磁盘'},{key:'network',label:'网络'},{key:'processes',label:'进程'}]
const lifecycleActions = [{key:'start',label:'启动'},{key:'stop',label:'停止'},{key:'restart',label:'重启'},{key:'kill',label:'强制停止'},{key:'pause',label:'暂停'},{key:'unpause',label:'恢复'}]
const optionalColumns = [{key:'image',label:'镜像'},{key:'resource',label:'资源使用率'},{key:'ip',label:'IP 地址'},{key:'ports',label:'端口'},{key:'service',label:'关联服务'}]
let savedColumns = {}
try { savedColumns = JSON.parse(localStorage.getItem('ops-assets-container-columns') || '{}') } catch { savedColumns = {} }
const visibleColumns = reactive({ image:true, resource:true, ip:true, ports:true, service:true, ...savedColumns })
const currentHost = computed(() => hosts.value.find(host => host.id === selectedHostId.value) || null)
const containerCounts = computed(() => ({
  running: Number(monitor.value?.metrics?.container_running ?? containers.value.filter(item=>item.state==='running').length),
  stopped: Number(monitor.value?.metrics?.container_stopped ?? containers.value.filter(item=>!['running','paused'].includes(item.state)).length),
}))
const summaryMetrics = computed(() => [{key:'cpu',label:'CPU',value:clampPercent(metric('cpu'))},{key:'memory',label:'内存',value:clampPercent(metric('memory'))},{key:'disk',label:'磁盘',value:clampPercent(metric('disk'))}])
const allVisibleSelected = computed(() => containers.value.length > 0 && containers.value.every(item => selectedContainers.value.has(item.id)))
const tableColumnCount = computed(() => 4 + optionalColumns.filter(column => visibleColumns[column.key]).length)
const monitorSource = computed(() => monitor.value?.source === 'agent' ? 'Agent' : monitor.value?.source === 'ssh' ? 'SSH' : '不可用')

function metric(key){return monitor.value?.metrics?.[key]??0} function clampPercent(value){return Math.max(0,Math.min(100,Number(value||0)))}
function fmtBytes(value){const n=Number(value||0);if(!n)return'0 B';const u=['B','KB','MB','GB','TB'],i=Math.min(Math.floor(Math.log(n)/Math.log(1024)),u.length-1);return`${(n/1024**i).toFixed(i>1?1:0)} ${u[i]}`}
function fmtDuration(seconds){const n=Number(seconds||0);if(!n)return'—';const d=Math.floor(n/86400),h=Math.floor((n%86400)/3600);return d?`${d}天 ${h}小时`:`${h}小时`}
function stateLabel(state){return({running:'已启动',paused:'已暂停',exited:'已停止',created:'已创建',dead:'异常',removing:'删除中'})[state]||state||'未知'}
function stateClass(state){return state==='running'?'running':state==='paused'?'paused':state==='dead'?'error':'stopped'}
function formatPort(port){return port.host_port?`${port.host_ip||'0.0.0.0'}:${port.host_port} → ${port.private}`:port.private} function isServiceOnline(svc){return['online','up','running'].includes(svc.status)}
function saveColumns(){localStorage.setItem('ops-assets-container-columns',JSON.stringify(visibleColumns))}

async function loadHosts(){loading.value=true;try{hosts.value=await api.get('/servers');const requested=String(route.query.host||''),saved=localStorage.getItem('ops-assets-host')||'',chosen=hosts.value.find(h=>h.id===requested)||hosts.value.find(h=>h.id===saved)||hosts.value.find(h=>h.status==='online')||hosts.value[0];if(chosen)await selectHost(chosen,false)}catch(error){toast(`加载主机失败：${error.message}`,'err')}finally{loading.value=false}}
async function selectHost(host,updateRoute=true){if(!host)return;selectedHostId.value=host.id;selectedContainers.value=new Set();localStorage.setItem('ops-assets-host',host.id);if(updateRoute)await router.replace({query:{...route.query,host:host.id}});await refreshCurrent()}
async function refreshCurrent(){if(!selectedHostId.value||refreshing.value)return;refreshing.value=true;try{await Promise.allSettled([loadMonitor(),loadContainers(),loadServices()]);lastUpdated.value=new Date().toLocaleTimeString('zh-CN',{hour12:false})}finally{refreshing.value=false}}
async function loadMonitor(){try{monitor.value=await api.get(`/servers/${selectedHostId.value}/monitor`)}catch(error){monitor.value={metrics:{},error:error.message};toast(`监控读取失败：${error.message}`,'err')}}
async function loadContainers(){if(!selectedHostId.value)return;containerLoading.value=true;try{const data=await api.get(`/servers/${selectedHostId.value}/containers`,{status:containerFilter.value,search:containerSearch.value,page_size:200});containers.value=data.items||[];containerTotal.value=data.total||0;selectedContainers.value=new Set([...selectedContainers.value].filter(id=>containers.value.some(item=>item.id===id)))}catch(error){containers.value=[];containerTotal.value=0;toast(`容器读取失败：${error.message}`,'err')}finally{containerLoading.value=false}}
async function loadServices(){if(!selectedHostId.value)return;serviceLoading.value=true;try{hostServices.value=await api.get('/services/all',{server_id:selectedHostId.value})}catch(error){hostServices.value=[];toast(`服务读取失败：${error.message}`,'err')}finally{serviceLoading.value=false}}
function scheduleContainerSearch(){clearTimeout(searchTimer);searchTimer=setTimeout(loadContainers,300)}
function toggleContainer(id,checked){const next=new Set(selectedContainers.value);checked?next.add(id):next.delete(id);selectedContainers.value=next} function toggleAll(checked){selectedContainers.value=checked?new Set(containers.value.map(item=>item.id)):new Set()}

async function runContainerAction(action,label,options={}){const ids=options.ids||[...selectedContainers.value];if(!ids.length)return;if(!options.skipConfirm&&!window.confirm(`确认对所选 ${ids.length} 个容器执行“${label}”？`))return;operationBusy.value=true;try{const result=await api.post(`/servers/${selectedHostId.value}/containers/actions`,{container_ids:ids,action,force:!!options.force});const failed=(result.results||[]).filter(item=>!item.ok);toast(failed.length?`${label}完成，${failed.length} 项失败`:`${label}完成，共 ${ids.length} 项`,failed.length?'err':'ok');selectedContainers.value=new Set();await Promise.all([loadContainers(),loadMonitor()])}catch(error){toast(`${label}失败：${error.message}`,'err')}finally{operationBusy.value=false}}
function requestDelete(){const ids=[...selectedContainers.value];if(!ids.length||!window.confirm(`即将删除 ${ids.length} 个容器。删除后容器内部未挂载的数据不可恢复，是否继续？`))return;openConfirm('delete','删除容器',`请输入确认文字以删除所选 ${ids.length} 个容器。`,'确认删除')}
function requestPrune(){if(!window.confirm('清理操作将删除当前主机上的全部已停止容器，是否继续？'))return;openConfirm('prune','清理停止容器','此操作仅清理当前主机的已停止容器。','确认清理')}
function openConfirm(kind,title,message,word){Object.assign(confirmState,{visible:true,kind,title,message,word,input:'',force:false})} function closeConfirm(){confirmState.visible=false;confirmState.input='';confirmState.force=false}
async function confirmDangerous(){if(confirmState.input!==confirmState.word)return;const kind=confirmState.kind,force=confirmState.force;closeConfirm();if(kind==='delete')return runContainerAction('remove','删除',{skipConfirm:true,force});operationBusy.value=true;try{await api.post(`/servers/${selectedHostId.value}/containers/prune`,{});toast('停止容器清理完成','ok');await Promise.all([loadContainers(),loadMonitor()])}catch(error){toast(`清理失败：${error.message}`,'err')}finally{operationBusy.value=false}}

async function openContainerLogs(container){logTarget.value=container;logLoading.value=true;logs.value='';try{const data=await api.get(`/servers/${selectedHostId.value}/containers/${encodeURIComponent(container.id)}/logs`,{lines:300});logs.value=data.logs||''}catch(error){logs.value=`日志获取失败：${error.message}`}finally{logLoading.value=false}}
async function openServiceLogs(svc){logTarget.value={name:svc.name};logLoading.value=true;logs.value='';try{const data=await api.get(`/services/${svc.id}/logs`,{lines:300});logs.value=data.logs||''}catch(error){logs.value=`日志获取失败：${error.message}`}finally{logLoading.value=false}}
async function openInspect(container){inspectTarget.value=container;inspectLoading.value=true;inspectData.value=null;try{inspectData.value=await api.get(`/servers/${selectedHostId.value}/containers/${encodeURIComponent(container.id)}/inspect`)}catch(error){toast(`详情读取失败：${error.message}`,'err');inspectTarget.value=null}finally{inspectLoading.value=false}}
async function openHostTerminal(){try{const data=await api.post('/terminal/sessions',{server_id:selectedHostId.value,cols:110,rows:32,mode:'host'});terminalSession.value={id:data.session_id,title:currentHost.value.name,allowFiles:true}}catch(error){toast(`终端连接失败：${error.message}`,'err')}}
async function openContainerTerminal(container){try{const data=await api.post('/terminal/sessions',{server_id:selectedHostId.value,cols:110,rows:32,mode:'container',container_id:container.id});terminalSession.value={id:data.session_id,title:`${currentHost.value.name} / ${container.name}`,allowFiles:false}}catch(error){toast(`容器终端连接失败：${error.message}`,'err')}}
async function serviceControl(svc,action){if(!window.confirm(`确认对 ${svc.name} 执行“${action}”？`))return;try{await api.post(`/services/${svc.id}/control`,{action});toast(`${svc.name} 操作完成`,'ok');await Promise.all([loadServices(),loadContainers()])}catch(error){toast(`服务操作失败：${error.message}`,'err')}}
async function power(action){const label=action==='reboot'?'重启':'关机';if(!window.confirm(`确认对 ${currentHost.value.name} 执行“${label}”？该主机上的全部服务将中断。`))return;try{await api.post(`/servers/${selectedHostId.value}/power`,{action});toast(`已发送${label}指令`,'ok')}catch(error){toast(`${label}失败：${error.message}`,'err')}}
function visibilityRefresh(){if(!document.hidden)refreshCurrent()}
onMounted(async()=>{await loadHosts();refreshTimer=setInterval(()=>{if(!document.hidden)refreshCurrent()},10000);document.addEventListener('visibilitychange',visibilityRefresh)})
onUnmounted(()=>{clearInterval(refreshTimer);clearTimeout(searchTimer);document.removeEventListener('visibilitychange',visibilityRefresh)})
</script>

<style scoped>
.assets-workbench{max-width:1680px;margin:0 auto}.head-actions{display:flex;align-items:center;gap:12px}.host-switcher{display:flex;gap:9px;padding:12px;overflow-x:auto;margin-bottom:12px}.host-pill{border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:8px;padding:9px 13px;white-space:nowrap;cursor:pointer}.host-pill.active{border-color:var(--primary);color:var(--primary);background:rgba(37,99,235,.08);box-shadow:inset 0 0 0 1px var(--primary)}.state-dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:6px}.state-dot.online{background:#22c55e}.state-dot.offline{background:#94a3b8}.host-summary{padding:16px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:12px}.summary-identity{min-width:260px}.summary-name{font-size:18px;font-weight:700}.summary-meta{margin-top:4px;font-size:12px}.summary-spacer{flex:1}.summary-metric{width:125px}.summary-metric>div:first-child{display:flex;justify-content:space-between;font-size:12px}.summary-metric span{color:var(--muted)}.metric-track{height:6px;background:rgba(148,163,184,.2);border-radius:6px;overflow:hidden;margin-top:7px}.metric-track i{display:block;height:100%;background:var(--primary);border-radius:inherit}.workbench{padding:0;overflow:hidden}.asset-tabs{display:flex;gap:28px;padding:0 18px;border-bottom:1px solid var(--border);overflow-x:auto}.asset-tab{padding:15px 3px 13px;border:0;border-bottom:2px solid transparent;background:none;color:var(--muted);cursor:pointer;white-space:nowrap}.asset-tab.active{color:var(--primary);border-color:var(--primary);font-weight:700}.tab-count{margin-left:5px;font-size:11px;background:rgba(37,99,235,.1);padding:1px 6px;border-radius:9px}.tab-panel{min-height:420px}.overview-panel{padding:18px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px}.metric-card{border:1px solid var(--border);border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:6px}.metric-card span,.metric-card small{color:var(--muted);font-size:12px}.metric-card b{font-size:17px}.container-toolbar{padding:14px 16px;display:flex;align-items:center;gap:7px;flex-wrap:wrap;border-bottom:1px solid var(--border)}.toolbar-divider{height:23px;width:1px;background:var(--border);margin:0 3px}.toolbar-right{margin-left:auto;display:flex;gap:8px}.select,.search,.confirm-input{border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:6px;padding:7px 10px;outline:none}.search{width:220px}.select:focus,.search:focus,.confirm-input:focus{border-color:var(--primary)}.column-tip{padding:9px 16px;color:var(--muted);background:rgba(37,99,235,.05);border-bottom:1px solid var(--border);font-size:12px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}.column-tip label{color:var(--text);cursor:pointer}.table-scroll{overflow-x:auto}.container-table{min-width:1120px}.check-col{width:44px;text-align:center!important}.actions-col{width:150px}.name-link,.link-btn{border:0;background:none;color:var(--primary);cursor:pointer;padding:2px 5px}.name-link{padding-left:0;font-weight:650}.link-btn:disabled{color:var(--muted);cursor:not-allowed}.image-name{max-width:270px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tiny{font-size:11px;margin-top:3px}.resource-cell,.port-line{white-space:nowrap;line-height:1.7}.row-actions{white-space:nowrap}.container-state{display:inline-flex;align-items:center;gap:5px;border-radius:12px;padding:3px 8px;font-size:12px}.container-state i{width:6px;height:6px;border-radius:50%}.container-state.running{color:#16a34a;background:rgba(34,197,94,.1)}.container-state.running i{background:#22c55e}.container-state.paused{color:#d97706;background:rgba(245,158,11,.12)}.container-state.paused i{background:#f59e0b}.container-state.stopped{color:#64748b;background:rgba(148,163,184,.13)}.container-state.stopped i{background:#94a3b8}.container-state.error{color:#dc2626;background:rgba(239,68,68,.1)}.container-state.error i{background:#ef4444}.btn-danger-soft{color:var(--err);border-color:rgba(239,68,68,.4)}.log-pre{margin:0;max-height:65vh;overflow:auto;background:#0d1117;color:#e6edf3;border-radius:8px;padding:14px;white-space:pre-wrap;word-break:break-all;font:12px/1.6 Consolas,monospace}.inspect-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.inspect-grid>div{border:1px solid var(--border);border-radius:7px;padding:11px;display:flex;flex-direction:column;gap:5px;min-width:0}.inspect-grid span{color:var(--muted);font-size:12px}.inspect-grid b{overflow-wrap:anywhere}.inspect-wide{grid-column:1/-1}.inspect-grid pre{margin:4px 0 0;max-height:210px;overflow:auto;background:rgba(148,163,184,.1);padding:9px;border-radius:5px;font-size:11px}.danger-warning{color:var(--err);font-weight:700}.force-option{display:block;margin:12px 0}.confirm-input{width:100%}.confirm-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}
@media(max-width:1100px){.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.toolbar-right{width:100%;margin-left:0}.search{flex:1}}@media(max-width:700px){.view-head{align-items:flex-start}.head-actions .muted{display:none}.host-summary{gap:12px}.summary-identity{width:100%}.summary-metric{width:calc(33.333% - 8px)}.summary-spacer{display:none}.metric-grid,.inspect-grid{grid-template-columns:1fr}.inspect-wide{grid-column:auto}.toolbar-right{flex-wrap:wrap}.search{width:100%;flex-basis:100%}}
</style>
