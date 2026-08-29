<template>
  <div class="view assets-workbench">
    <div class="view-head">
      <div><h1 class="view-title">容器管理</h1><p class="view-sub">容器、镜像、Docker 网络与存储卷</p></div>
      <div class="head-actions"><span v-if="lastUpdated" class="muted">更新于 {{ lastUpdated }}</span><button class="btn" :disabled="refreshing" @click="refreshCurrent">↻ 刷新</button></div>
    </div>

    <div v-if="hostLoading" class="loading"><span class="spinner"></span>正在加载主机…</div>
    <EmptyState v-else-if="!hosts.length" icon="🖥" text="暂无主机，请在资源管理中录入" />

    <template v-else>
      <section class="card workbench">
        <nav class="asset-tabs">
          <button v-for="tab in tabs" :key="tab.key" class="asset-tab" :class="{ active: activeTab === tab.key }" @click="selectTab(tab.key)">
            {{ tab.label }}<span v-if="tabCount(tab.key) !== null" class="tab-count">{{ tabCount(tab.key) }}</span>
          </button>
        </nav>

        <div v-if="activeTab === 'containers'" class="tab-panel">
          <div class="container-toolbar">
            <button class="btn btn-sm btn-danger-soft" :disabled="operationBusy" @click="requestPrune">清理停止容器</button><span class="toolbar-divider"></span>
            <button class="btn btn-sm" :disabled="containerLoading" @click="loadContainers(true,true)">刷新资源占用</button>
            <button v-for="action in lifecycleActions" :key="action.key" class="btn btn-sm" :disabled="!selectedContainers.size || operationBusy" @click="runContainerAction(action.key, action.label)">{{ action.label }}</button>
            <button class="btn btn-sm btn-danger" :disabled="!selectedContainers.size || operationBusy" @click="requestDelete">删除</button>
            <div class="toolbar-right"><span class="muted tiny">{{ containerDataTime ? `数据 ${containerDataTime}` : '' }}</span><select v-model="containerFilter" class="select" @change="loadContainers"><option value="all">状态：所有</option><option value="running">运行中</option><option value="paused">已暂停</option><option value="stopped">已停止</option></select><input v-model="containerSearch" class="search" placeholder="搜索名称或镜像" @input="scheduleContainerSearch" /><button class="btn btn-sm" @click="columnHelp = !columnHelp">⚙ 列表设置</button></div>
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

        <div v-else-if="dockerResourceTabs.includes(activeTab)" class="tab-panel">
          <div class="container-toolbar">
            <button class="btn btn-sm btn-danger-soft" :disabled="resourceLoading || operationBusy" @click="requestResourcePrune">{{ resourcePruneLabel }}</button>
            <span class="toolbar-divider"></span>
            <button class="btn btn-sm btn-danger" :disabled="!selectedResources.size || operationBusy" @click="requestResourceDelete">删除所选</button>
            <div class="resource-summary"><b>{{ currentResourceMeta.total || 0 }}</b> 项 · 使用中 {{ currentResourceMeta.in_use || 0 }}<span v-if="activeTab === 'images'"> · 共 {{ fmtBytes(currentResourceMeta.total_size) }}</span></div>
            <div class="toolbar-right"><input v-model="resourceSearch" class="search" :placeholder="`搜索${activeResourceLabel}`" @input="scheduleResourceSearch" /><button class="btn btn-sm" :disabled="resourceLoading" @click="loadDockerResources()">↻ 刷新</button></div>
          </div>
          <div v-if="resourceLoading" class="loading"><span class="spinner"></span>正在读取 Docker {{ activeResourceLabel }}…</div>
          <div v-else class="table-scroll"><table class="table docker-resource-table">
            <thead><tr><th class="check-col"><input type="checkbox" :checked="allResourcesSelected" @change="toggleAllResources($event.target.checked)" /></th><template v-if="activeTab === 'images'"><th>镜像</th><th>镜像 ID</th><th>大小</th><th>创建时间</th><th>使用状态</th></template><template v-else-if="activeTab === 'networks'"><th>名称</th><th>驱动</th><th>网段</th><th>容器数</th><th>范围</th></template><template v-else><th>名称</th><th>驱动</th><th>挂载点</th><th>大小</th><th>使用状态</th></template></tr></thead>
            <tbody>
              <tr v-for="item in currentResources" :key="item.id"><td class="check-col"><input type="checkbox" :disabled="item.system" :checked="selectedResources.has(item.id)" @change="toggleResource(item.id, $event.target.checked)" /></td>
                <template v-if="activeTab === 'images'"><td><b>{{ item.repo_tags?.[0] || '悬空镜像' }}</b><div v-for="tag in (item.repo_tags || []).slice(1)" :key="tag" class="muted tiny">{{ tag }}</div></td><td class="mono">{{ item.short_id }}</td><td>{{ fmtBytes(item.size) }}</td><td>{{ fmtDate(item.created_at) }}</td><td><span class="tag" :class="item.in_use ? 'tag-green' : 'tag-slate'">{{ item.in_use ? '使用中' : (item.dangling ? '悬空' : '未使用') }}</span></td></template>
                <template v-else-if="activeTab === 'networks'"><td><b>{{ item.name }}</b><div v-if="item.system" class="muted tiny">Docker 默认网络</div></td><td class="mono">{{ item.driver }}</td><td class="mono">{{ item.subnets?.join(', ') || '-' }}</td><td>{{ item.container_count }}</td><td>{{ item.scope || '-' }}</td></template>
                <template v-else><td><b>{{ item.name }}</b></td><td class="mono">{{ item.driver }}</td><td class="mono resource-path" :title="item.mountpoint">{{ item.mountpoint || '-' }}</td><td>{{ item.size ? fmtBytes(item.size) : '未统计' }}</td><td><span class="tag" :class="item.in_use ? 'tag-green' : 'tag-slate'">{{ item.in_use ? '使用中' : '未使用' }}</span></td></template>
              </tr>
              <tr v-if="!currentResources.length"><td colspan="6"><EmptyState :icon="activeTab === 'images' ? '🧱' : activeTab === 'networks' ? '🔗' : '💾'" :text="`当前主机暂无${activeResourceLabel}`" style="padding:28px" /></td></tr>
            </tbody>
          </table></div>
        </div>

      </section>
    </template>

    <TerminalPanel v-if="terminalSession" :session-id="terminalSession.id" :title="terminalSession.title" :allow-files="terminalSession.allowFiles" @close="terminalSession = null" />
    <Modal :visible="!!logTarget" :title="`日志 · ${logTarget?.name || ''}`" width="820px" @close="logTarget = null"><div v-if="logLoading" class="loading"><span class="spinner"></span>加载日志…</div><pre v-else class="log-pre">{{ logs || '（无日志输出）' }}</pre></Modal>
    <Modal :visible="!!inspectTarget" :title="`容器详情 · ${inspectTarget?.name || ''}`" width="880px" @close="inspectTarget = null">
      <div v-if="inspectLoading" class="loading"><span class="spinner"></span>加载详情…</div>
      <div v-else-if="inspectData" class="inspect-grid"><div><span>容器 ID</span><b class="mono">{{ inspectData.short_id }}</b></div><div><span>镜像</span><b>{{ inspectData.image }}</b></div><div><span>状态</span><b>{{ stateLabel(inspectData.state) }}</b></div><div><span>重启策略</span><b>{{ inspectData.restart_policy }}</b></div><div><span>网络</span><b>{{ inspectData.networks?.join(', ') || '-' }}</b></div><div><span>工作目录</span><b class="mono">{{ inspectData.working_dir || '-' }}</b></div><div class="inspect-wide"><span>挂载</span><pre>{{ JSON.stringify(inspectData.mounts || [], null, 2) }}</pre></div><div class="inspect-wide"><span>环境变量（敏感值已遮蔽）</span><pre>{{ (inspectData.environment || []).map(item => `${item.key}=${item.value}`).join('\n') || '-' }}</pre></div></div>
    </Modal>
    <Modal :visible="confirmState.visible" :title="confirmState.title" width="540px" @close="closeConfirm"><div class="danger-warning">⚠ 危险操作</div><p>{{ confirmState.message }}</p><label v-if="confirmState.kind === 'delete' || confirmState.kind === 'resource-delete'" class="force-option"><input v-model="confirmState.force" type="checkbox" /> {{ confirmState.kind === 'delete' ? '强制删除仍在运行的容器' : '强制删除资源（使用中的资源仍可能被 Docker 拒绝）' }}</label><p class="muted">请输入“{{ confirmState.word }}”继续：</p><input v-model="confirmState.input" class="confirm-input" :placeholder="confirmState.word" @keyup.enter="confirmDangerous" /><div class="confirm-actions"><button class="btn" @click="closeConfirm">取消</button><button class="btn btn-danger" :disabled="confirmState.input !== confirmState.word || operationBusy" @click="confirmDangerous">确认执行</button></div></Modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { api, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import TerminalPanel from '../components/TerminalPanel.vue'
import { useHostContext } from '../hostContext'

const { hosts, selectedHostId, currentHost, loading:hostLoading, refreshHosts } = useHostContext()
const refreshing = ref(false)
const containers = ref([]), containerTotal = ref(0), containerLoading = ref(false), containerFilter = ref('all'), containerSearch = ref(''), selectedContainers = ref(new Set()), operationBusy = ref(false)
const activeTab = ref('containers'), terminalSession = ref(null)
const dockerResources = reactive({images:[],networks:[],volumes:[]}), resourceMeta = reactive({images:{},networks:{},volumes:{}})
const resourceLoading = ref(false), resourceSearch = ref(''), selectedResources = ref(new Set())
const logTarget = ref(null), logLoading = ref(false), logs = ref(''), inspectTarget = ref(null), inspectLoading = ref(false), inspectData = ref(null)
const lastUpdated = ref(''), containerDataTime=ref(''), columnHelp = ref(false)
const confirmState = reactive({ visible: false, kind: '', title: '', message: '', word: '', input: '', force: false, resource: '', ids: [] })
let searchTimer = null, resourceSearchTimer = null, containerController = null
const dockerResourceTabs = ['images','networks','volumes']
const resourceLabels = {images:'镜像',networks:'网络',volumes:'存储卷'}
const tabs = [{key:'containers',label:'容器'},{key:'images',label:'镜像'},{key:'networks',label:'Docker 网络'},{key:'volumes',label:'存储卷'}]
const lifecycleActions = [{key:'start',label:'启动'},{key:'stop',label:'停止'},{key:'restart',label:'重启'},{key:'kill',label:'强制停止'},{key:'pause',label:'暂停'},{key:'unpause',label:'恢复'}]
const optionalColumns = [{key:'image',label:'镜像'},{key:'resource',label:'资源使用率'},{key:'ip',label:'IP 地址'},{key:'ports',label:'端口'},{key:'service',label:'关联服务'}]
let savedColumns = {}
try { savedColumns = JSON.parse(localStorage.getItem('ops-assets-container-columns') || '{}') } catch { savedColumns = {} }
const visibleColumns = reactive({ image:true, resource:true, ip:true, ports:true, service:true, ...savedColumns })
const containerCounts = computed(() => ({
  running: Number(monitor.value?.metrics?.container_running ?? containers.value.filter(item=>item.state==='running').length),
  stopped: Number(monitor.value?.metrics?.container_stopped ?? containers.value.filter(item=>!['running','paused'].includes(item.state)).length),
}))
const allVisibleSelected = computed(() => containers.value.length > 0 && containers.value.every(item => selectedContainers.value.has(item.id)))
const currentResources = computed(() => dockerResources[activeTab.value] || [])
const currentResourceMeta = computed(() => resourceMeta[activeTab.value] || {})
const activeResourceLabel = computed(() => resourceLabels[activeTab.value] || '')
const resourcePruneLabel = computed(() => activeTab.value === 'images' ? '清理悬空镜像' : `清理未使用${activeResourceLabel.value}`)
const allResourcesSelected = computed(() => {const selectable=currentResources.value.filter(item=>!item.system);return selectable.length>0&&selectable.every(item=>selectedResources.value.has(item.id))})
const tableColumnCount = computed(() => 4 + optionalColumns.filter(column => visibleColumns[column.key]).length)
function fmtBytes(value){const n=Number(value||0);if(!n)return'0 B';const u=['B','KB','MB','GB','TB'],i=Math.min(Math.floor(Math.log(n)/Math.log(1024)),u.length-1);return`${(n/1024**i).toFixed(i>1?1:0)} ${u[i]}`}
function fmtDate(value){if(value===null||value===undefined||value==='')return'—';const date=typeof value==='number'?new Date(value*1000):new Date(value);return Number.isNaN(date.getTime())?String(value):date.toLocaleString('zh-CN',{hour12:false})}
function stateLabel(state){return({running:'已启动',paused:'已暂停',exited:'已停止',created:'已创建',dead:'异常',removing:'删除中'})[state]||state||'未知'}
function stateClass(state){return state==='running'?'running':state==='paused'?'paused':state==='dead'?'error':'stopped'}
function formatPort(port){return port.host_port?`${port.host_ip||'0.0.0.0'}:${port.host_port} → ${port.private}`:port.private}
function saveColumns(){localStorage.setItem('ops-assets-container-columns',JSON.stringify(visibleColumns))}
function tabCount(key){if(key==='containers')return containerTotal.value;if(dockerResourceTabs.includes(key))return resourceMeta[key].total??null;return null}

async function selectTab(key){activeTab.value=key;selectedResources.value=new Set();resourceSearch.value='';if(dockerResourceTabs.includes(key))await loadDockerResources(key)}
async function refreshCurrent(includeResources=true){if(!selectedHostId.value||refreshing.value)return;refreshing.value=true;try{const tasks=[loadContainers(false,true)];if(includeResources&&dockerResourceTabs.includes(activeTab.value))tasks.push(loadDockerResources(activeTab.value));await Promise.allSettled(tasks);lastUpdated.value=new Date().toLocaleTimeString('zh-CN',{hour12:false})}finally{refreshing.value=false}}
async function loadContainers(includeStats=false,refresh=false){if(!selectedHostId.value)return;const hostId=selectedHostId.value;containerController?.abort();containerController=new AbortController();const cacheKey=`ops-containers-${hostId}`;if(!includeStats&&!refresh){try{const cached=JSON.parse(sessionStorage.getItem(cacheKey)||'null');if(cached?.items){containers.value=cached.items;containerTotal.value=cached.total||cached.items.length;containerDataTime.value='缓存 '+new Date(cached.time).toLocaleTimeString('zh-CN',{hour12:false})}}catch{}}containerLoading.value=true;try{const data=await api.get(`/servers/${hostId}/containers`,{status:containerFilter.value,search:containerSearch.value,page_size:200,include_stats:includeStats,refresh},{signal:containerController.signal});if(hostId!==selectedHostId.value)return;containers.value=data.items||[];containerTotal.value=data.total||0;const stamp=Number(data.data_timestamp||Date.now()/1000)*1000;containerDataTime.value=new Date(stamp).toLocaleTimeString('zh-CN',{hour12:false})+(data.cached?' · 缓存':'');sessionStorage.setItem(cacheKey,JSON.stringify({items:containers.value,total:containerTotal.value,time:stamp}));selectedContainers.value=new Set([...selectedContainers.value].filter(id=>containers.value.some(item=>item.id===id)))}catch(error){if(error.name==='AbortError')return;if(!containers.value.length){containers.value=[];containerTotal.value=0}toast(`容器读取失败：${error.message}`,'err')}finally{if(hostId===selectedHostId.value)containerLoading.value=false}}
async function loadDockerResources(resource=activeTab.value){if(!selectedHostId.value||!dockerResourceTabs.includes(resource))return;resourceLoading.value=true;try{const data=await api.get(`/servers/${selectedHostId.value}/docker/${resource}`,{search:resourceSearch.value});dockerResources[resource]=data.items||[];resourceMeta[resource]={total:data.total||0,in_use:data.in_use||0,total_size:data.total_size||0};selectedResources.value=new Set([...selectedResources.value].filter(id=>dockerResources[resource].some(item=>item.id===id)))}catch(error){dockerResources[resource]=[];resourceMeta[resource]={};toast(`${resourceLabels[resource]}读取失败：${error.message}`,'err')}finally{resourceLoading.value=false}}
function scheduleContainerSearch(){clearTimeout(searchTimer);searchTimer=setTimeout(loadContainers,300)}
function scheduleResourceSearch(){clearTimeout(resourceSearchTimer);resourceSearchTimer=setTimeout(()=>loadDockerResources(),300)}
function toggleContainer(id,checked){const next=new Set(selectedContainers.value);checked?next.add(id):next.delete(id);selectedContainers.value=next} function toggleAll(checked){selectedContainers.value=checked?new Set(containers.value.map(item=>item.id)):new Set()}
function toggleResource(id,checked){const next=new Set(selectedResources.value);checked?next.add(id):next.delete(id);selectedResources.value=next} function toggleAllResources(checked){selectedResources.value=checked?new Set(currentResources.value.filter(item=>!item.system).map(item=>item.id)):new Set()}

async function runContainerAction(action,label,options={}){const ids=options.ids||[...selectedContainers.value];if(!ids.length)return;if(!options.skipConfirm&&!window.confirm(`确认对所选 ${ids.length} 个容器执行“${label}”？`))return;operationBusy.value=true;try{const result=await api.post(`/servers/${selectedHostId.value}/containers/actions`,{container_ids:ids,action,force:!!options.force});const failed=(result.results||[]).filter(item=>!item.ok);toast(failed.length?`${label}完成，${failed.length} 项失败`:`${label}完成，共 ${ids.length} 项`,failed.length?'err':'ok');selectedContainers.value=new Set();await loadContainers()}catch(error){toast(`${label}失败：${error.message}`,'err')}finally{operationBusy.value=false}}
function requestDelete(){const ids=[...selectedContainers.value];if(!ids.length||!window.confirm(`即将删除 ${ids.length} 个容器。删除后容器内部未挂载的数据不可恢复，是否继续？`))return;openConfirm('delete','删除容器',`请输入确认文字以删除所选 ${ids.length} 个容器。`,'确认删除')}
function requestPrune(){if(!window.confirm('清理操作将删除当前主机上的全部已停止容器，是否继续？'))return;openConfirm('prune','清理停止容器','此操作仅清理当前主机的已停止容器。','确认清理')}
function requestResourceDelete(){const ids=[...selectedResources.value];if(!ids.length)return;openConfirm('resource-delete',`删除${activeResourceLabel.value}`,`将从当前主机删除所选 ${ids.length} 项${activeResourceLabel.value}。使用中的资源默认不会被删除。`,'确认删除',{resource:activeTab.value,ids})}
function requestResourcePrune(){openConfirm('resource-prune',resourcePruneLabel.value,`将清理当前主机上所有符合条件的${activeResourceLabel.value}，此操作不可恢复。`,'确认清理',{resource:activeTab.value})}
function openConfirm(kind,title,message,word,extra={}){Object.assign(confirmState,{visible:true,kind,title,message,word,input:'',force:false,resource:'',ids:[],...extra})} function closeConfirm(){confirmState.visible=false;confirmState.input='';confirmState.force=false}
async function confirmDangerous(){if(confirmState.input!==confirmState.word)return;const kind=confirmState.kind,force=confirmState.force,resource=confirmState.resource,ids=[...confirmState.ids];closeConfirm();if(kind==='delete')return runContainerAction('remove','删除',{skipConfirm:true,force});operationBusy.value=true;try{if(kind==='resource-delete'){const result=await api.post(`/servers/${selectedHostId.value}/docker/${resource}/delete`,{resource_ids:ids,force});const failed=(result.results||[]).filter(item=>!item.ok);toast(failed.length?`删除完成，${failed.length} 项失败`:`已删除 ${ids.length} 项${resourceLabels[resource]}`,failed.length?'err':'ok');selectedResources.value=new Set();await loadDockerResources(resource)}else if(kind==='resource-prune'){await api.post(`/servers/${selectedHostId.value}/docker/${resource}/prune`,{});toast(`${resourceLabels[resource]}清理完成`,'ok');await loadDockerResources(resource)}else{await api.post(`/servers/${selectedHostId.value}/containers/prune`,{});toast('停止容器清理完成','ok');await loadContainers()}}catch(error){toast(`操作失败：${error.message}`,'err')}finally{operationBusy.value=false}}

async function openContainerLogs(container){logTarget.value=container;logLoading.value=true;logs.value='';try{const data=await api.get(`/servers/${selectedHostId.value}/containers/${encodeURIComponent(container.id)}/logs`,{lines:300});logs.value=data.logs||''}catch(error){logs.value=`日志获取失败：${error.message}`}finally{logLoading.value=false}}
async function openInspect(container){inspectTarget.value=container;inspectLoading.value=true;inspectData.value=null;try{inspectData.value=await api.get(`/servers/${selectedHostId.value}/containers/${encodeURIComponent(container.id)}/inspect`)}catch(error){toast(`详情读取失败：${error.message}`,'err');inspectTarget.value=null}finally{inspectLoading.value=false}}
async function openContainerTerminal(container){try{const data=await api.post('/terminal/sessions',{server_id:selectedHostId.value,cols:110,rows:32,mode:'container',container_id:container.id});terminalSession.value={id:data.session_id,title:`${currentHost.value.name} / ${container.name}`,allowFiles:false}}catch(error){toast(`容器终端连接失败：${error.message}`,'err')}}
watch(selectedHostId,async()=>{selectedContainers.value=new Set();selectedResources.value=new Set();containers.value=[];dockerResourceTabs.forEach(key=>{dockerResources[key]=[];resourceMeta[key]={}});await refreshCurrent()})
onMounted(async()=>{await refreshHosts();await loadContainers(false,false)})
onUnmounted(()=>{clearTimeout(searchTimer);clearTimeout(resourceSearchTimer);containerController?.abort()})
</script>

<style scoped>
.assets-workbench{max-width:1680px;margin:0 auto}.head-actions{display:flex;align-items:center;gap:12px}.host-switcher{display:flex;gap:9px;padding:12px;overflow-x:auto;margin-bottom:12px}.host-pill{border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:8px;padding:9px 13px;white-space:nowrap;cursor:pointer}.host-pill.active{border-color:var(--primary);color:var(--primary);background:rgba(37,99,235,.08);box-shadow:inset 0 0 0 1px var(--primary)}.state-dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:6px}.state-dot.online{background:#22c55e}.state-dot.offline{background:#94a3b8}.host-summary{padding:16px;display:flex;align-items:center;gap:18px;flex-wrap:wrap;margin-bottom:12px}.summary-identity{min-width:260px}.summary-name{font-size:18px;font-weight:700}.summary-meta{margin-top:4px;font-size:12px}.summary-spacer{flex:1}.summary-metric{width:125px}.summary-metric>div:first-child{display:flex;justify-content:space-between;font-size:12px}.summary-metric span{color:var(--muted)}.metric-track{height:6px;background:rgba(148,163,184,.2);border-radius:6px;overflow:hidden;margin-top:7px}.metric-track i{display:block;height:100%;background:var(--primary);border-radius:inherit}.workbench{padding:0;overflow:hidden}.asset-tabs{display:flex;gap:28px;padding:0 18px;border-bottom:1px solid var(--border);overflow-x:auto}.asset-tab{padding:15px 3px 13px;border:0;border-bottom:2px solid transparent;background:none;color:var(--muted);cursor:pointer;white-space:nowrap}.asset-tab.active{color:var(--primary);border-color:var(--primary);font-weight:700}.tab-count{margin-left:5px;font-size:11px;background:rgba(37,99,235,.1);padding:1px 6px;border-radius:9px}.tab-panel{min-height:420px}.overview-panel{padding:18px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px}.metric-card{border:1px solid var(--border);border-radius:8px;padding:16px;display:flex;flex-direction:column;gap:6px}.metric-card span,.metric-card small{color:var(--muted);font-size:12px}.metric-card b{font-size:17px}.container-toolbar{padding:14px 16px;display:flex;align-items:center;gap:7px;flex-wrap:wrap;border-bottom:1px solid var(--border)}.toolbar-divider{height:23px;width:1px;background:var(--border);margin:0 3px}.toolbar-right{margin-left:auto;display:flex;gap:8px}.resource-summary{color:var(--muted);font-size:12px;margin-left:6px}.resource-summary b{color:var(--text)}.select,.search,.confirm-input{border:1px solid var(--border);background:var(--card);color:var(--text);border-radius:6px;padding:7px 10px;outline:none}.search{width:220px}.select:focus,.search:focus,.confirm-input:focus{border-color:var(--primary)}.column-tip{padding:9px 16px;color:var(--muted);background:rgba(37,99,235,.05);border-bottom:1px solid var(--border);font-size:12px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}.column-tip label{color:var(--text);cursor:pointer}.table-scroll{overflow-x:auto}.container-table{min-width:1120px}.docker-resource-table{min-width:880px}.resource-path{max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.check-col{width:44px;text-align:center!important}.actions-col{width:150px}.name-link,.link-btn{border:0;background:none;color:var(--primary);cursor:pointer;padding:2px 5px}.name-link{padding-left:0;font-weight:650}.link-btn:disabled{color:var(--muted);cursor:not-allowed}.image-name{max-width:270px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.tiny{font-size:11px;margin-top:3px}.resource-cell,.port-line{white-space:nowrap;line-height:1.7}.row-actions{white-space:nowrap}.container-state{display:inline-flex;align-items:center;gap:5px;border-radius:12px;padding:3px 8px;font-size:12px}.container-state i{width:6px;height:6px;border-radius:50%}.container-state.running{color:#16a34a;background:rgba(34,197,94,.1)}.container-state.running i{background:#22c55e}.container-state.paused{color:#d97706;background:rgba(245,158,11,.12)}.container-state.paused i{background:#f59e0b}.container-state.stopped{color:#64748b;background:rgba(148,163,184,.13)}.container-state.stopped i{background:#94a3b8}.container-state.error{color:#dc2626;background:rgba(239,68,68,.1)}.container-state.error i{background:#ef4444}.btn-danger-soft{color:var(--err);border-color:rgba(239,68,68,.4)}.log-pre{margin:0;max-height:65vh;overflow:auto;background:#0d1117;color:#e6edf3;border-radius:8px;padding:14px;white-space:pre-wrap;word-break:break-all;font:12px/1.6 Consolas,monospace}.inspect-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.inspect-grid>div{border:1px solid var(--border);border-radius:7px;padding:11px;display:flex;flex-direction:column;gap:5px;min-width:0}.inspect-grid span{color:var(--muted);font-size:12px}.inspect-grid b{overflow-wrap:anywhere}.inspect-wide{grid-column:1/-1}.inspect-grid pre{margin:4px 0 0;max-height:210px;overflow:auto;background:rgba(148,163,184,.1);padding:9px;border-radius:5px;font-size:11px}.danger-warning{color:var(--err);font-weight:700}.force-option{display:block;margin:12px 0}.confirm-input{width:100%}.confirm-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}
@media(max-width:1100px){.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.toolbar-right{width:100%;margin-left:0}.search{flex:1}}@media(max-width:700px){.view-head{align-items:flex-start}.head-actions .muted{display:none}.host-summary{gap:12px}.summary-identity{width:100%}.summary-metric{width:calc(33.333% - 8px)}.summary-spacer{display:none}.metric-grid,.inspect-grid{grid-template-columns:1fr}.inspect-wide{grid-column:auto}.toolbar-right{flex-wrap:wrap}.search{width:100%;flex-basis:100%}}
</style>
