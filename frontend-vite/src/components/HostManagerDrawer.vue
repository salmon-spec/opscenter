<template>
  <Teleport to="body">
    <div v-if="visible" class="drawer-mask" @click.self="$emit('close')">
      <aside class="host-drawer">
        <header><div><h2>管理主机</h2><p>添加、改名和维护 SSH 连接</p></div><button class="btn btn-ghost" @click="$emit('close')">✕</button></header>
        <div class="drawer-actions"><button class="btn btn-primary" @click="beginAdd">＋ 添加主机</button><button v-if="outdatedCount" class="btn" :disabled="upgrading" @click="upgradeAll">升级旧 Agent ({{ outdatedCount }})</button><button v-if="missingAlloyCount" class="btn" :disabled="alloyBusy||!lokiConfigured" :title="lokiConfigured?'':'请先配置 Loki'" @click="deployMissingAlloy">部署日志采集 ({{ missingAlloyCount }})</button><button class="btn" @click="refreshHosts(true)">↻ 刷新</button></div>
        <div class="host-list">
          <article v-for="host in hosts" :key="host.id" class="host-row" :class="{selected:host.id===selectedHostId}">
            <button class="host-main" @click="selectHost(host.id)"><span class="dot" :class="host.status==='online'?'ok':'off'"></span><span><b>{{ host.name }}</b><small>{{ host.host }}:{{ host.ssh_port }} · Agent {{ agentLabel(host.agent_status) }} {{ host.agent_version?`v${host.agent_version}`:'' }}</small><small>日志采集 {{ alloyLabel(host.log_agent_status) }} {{ host.log_agent_version?`v${host.log_agent_version}`:'' }}<em v-if="host.log_agent_error" :title="host.log_agent_error">查看错误</em></small></span></button>
            <button v-if="needsUpgrade(host)" class="link-btn" :disabled="host.agent_status==='deploying'" @click="upgradeHost(host)">升级</button>
            <button v-if="canManageAlloy(host)&&host.log_agent_status!=='running'" class="link-btn" :disabled="alloyWorking(host)||!lokiConfigured" @click="deployAlloy(host)">部署采集</button><button v-if="canManageAlloy(host)" class="link-btn" :disabled="alloyWorking(host)" @click="checkAlloy(host)">检查采集</button>
            <button class="link-btn" @click="beginEdit(host)">编辑</button><button v-if="host.agent_type!=='local'&&!host.is_local" class="link-btn danger" @click="removeHost(host)">删除</button>
          </article>
        </div>
        <section v-if="editing" class="host-form">
          <h3>{{ form.id ? '编辑主机' : '添加主机' }}</h3>
          <label>主机名称<input v-model.trim="form.name" maxlength="50" /></label>
          <label>地址<input v-model.trim="form.host" :disabled="isLocalEdit" placeholder="10.66.66.x 或域名" /></label>
          <div class="form-grid"><label>SSH 端口<input v-model.number="form.ssh_port" :disabled="isLocalEdit" type="number" min="1" max="65535" /></label><label>SSH 用户<input v-model.trim="form.ssh_user" :disabled="isLocalEdit" /></label></div>
          <label>备注<input v-model.trim="form.remark" maxlength="500" /></label>
          <label>标签（逗号分隔）<input v-model.trim="form.tagsText" maxlength="300" placeholder="生产, 数据库, 华东" /></label>
          <label v-if="!isLocalEdit">认证方式<select v-model="form.auth_type"><option value="password">密码</option><option value="key">私钥</option></select></label>
          <label v-if="!isLocalEdit&&form.auth_type==='password'">{{ form.id ? '新密码（留空保留）' : 'SSH 密码' }}<input v-model="form.ssh_password" type="password" autocomplete="new-password" /></label>
          <label v-if="!isLocalEdit&&form.auth_type==='key'">{{ form.id ? '新私钥（留空保留）' : 'SSH 私钥' }}<textarea v-model="form.ssh_key" rows="5" /></label>
          <label v-if="!form.id" class="check"><input v-model="form.auto_deploy_agent" type="checkbox" /> 添加成功后自动部署监控 Agent</label>
          <div v-if="testMessage" class="test-result" :class="testOk?'ok':'bad'">{{ testMessage }}</div>
          <footer><button class="btn" @click="editing=false">取消</button><button v-if="!isLocalEdit" class="btn" :disabled="busy" @click="testConnection">{{ testing?'测试中…':'测试 SSH' }}</button><button class="btn btn-primary" :disabled="busy||!form.name||!form.host" @click="save">{{ saving?'保存中…':'保存' }}</button></footer>
        </section>
      </aside>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { api, toast } from '../api'
import { useHostContext } from '../hostContext'

const props = defineProps({ visible: Boolean })
defineEmits(['close'])
const { hosts, selectedHostId, refreshHosts, selectHost } = useHostContext()
const editing=ref(false),saving=ref(false),testing=ref(false),testMessage=ref(''),testOk=ref(false),currentAgentVersion=ref('2.5.0'),upgrading=ref(false),alloyBusy=ref(false),lokiConfigured=ref(false)
const form=reactive({id:'',name:'',host:'',ssh_port:22,ssh_user:'root',remark:'',tagsText:'',auth_type:'password',ssh_password:'',ssh_key:'',auto_deploy_agent:true,agent_type:'remote',is_local:false})
const busy=computed(()=>saving.value||testing.value),isLocalEdit=computed(()=>!!form.id&&(form.agent_type==='local'||form.is_local))
const versionParts=value=>String(value||'0').match(/\d+/g)?.slice(0,4).map(Number)||[0]
function compareVersion(a,b){const x=versionParts(a),y=versionParts(b);for(let i=0;i<Math.max(x.length,y.length);i++){const d=(x[i]||0)-(y[i]||0);if(d)return d}return 0}
function needsUpgrade(host){return host.agent_status==='running'&&compareVersion(host.agent_version,currentAgentVersion.value)<0&&(host.agent_type==='local'||host.has_credentials)}
const outdatedCount=computed(()=>hosts.value.filter(needsUpgrade).length)
const canManageAlloy=host=>host.agent_type!=='local'&&host.has_credentials
const alloyWorking=host=>['deploying','checking'].includes(host.log_agent_status)
const missingAlloyCount=computed(()=>hosts.value.filter(host=>canManageAlloy(host)&&host.log_agent_status!=='running').length)
let statusTimer=null
function reset(){Object.assign(form,{id:'',name:'',host:'',ssh_port:22,ssh_user:'root',remark:'',tagsText:'',auth_type:'password',ssh_password:'',ssh_key:'',auto_deploy_agent:true,agent_type:'remote',is_local:false});testMessage.value=''}
function beginAdd(){reset();editing.value=true}
function beginEdit(host){Object.assign(form,{id:host.id,name:host.name,host:host.host,ssh_port:host.ssh_port||22,ssh_user:host.ssh_user||'root',remark:host.remark||'',tagsText:(host.tags||[]).join(', '),auth_type:'password',ssh_password:'',ssh_key:'',auto_deploy_agent:false,agent_type:host.agent_type,is_local:host.is_local});testMessage.value='';editing.value=true}
function agentLabel(value){return({running:'运行中',deploying:'部署中',error:'异常',not_deployed:'未部署'})[value]||value||'未部署'}
function alloyLabel(value){return({running:'运行中',deploying:'部署中',checking:'检查中',stopped:'已停止',error:'异常',not_deployed:'未部署',unknown:'未检查'})[value]||'未检查'}
async function testConnection(){if(!form.host)return;testing.value=true;testMessage.value='';try{const data=await api.post('/test-ssh',{host:form.host,port:form.ssh_port,username:form.ssh_user,password:form.auth_type==='password'?form.ssh_password:null,ssh_key:form.auth_type==='key'?form.ssh_key:null});testOk.value=!!data.success;testMessage.value=data.message||data.error||(testOk.value?'连接成功':'连接失败')}catch(error){testOk.value=false;testMessage.value=error.message}finally{testing.value=false}}
async function save(){saving.value=true;try{const payload={name:form.name,remark:form.remark,tags:form.tagsText.split(',').map(x=>x.trim()).filter(Boolean)};if(!isLocalEdit.value)Object.assign(payload,{host:form.host,ssh_port:Number(form.ssh_port),ssh_user:form.ssh_user,ssh_password:form.auth_type==='password'?form.ssh_password:undefined,ssh_key:form.auth_type==='key'?form.ssh_key:undefined});if(form.id){await api.put(`/servers/${form.id}`,payload);toast('主机信息已更新','ok')}else{Object.assign(payload,{auto_deploy_agent:form.auto_deploy_agent,is_local:false});const data=await api.post('/servers',payload);selectHost(data.id);toast(data.agent_status==='deploying'?'主机已添加，Agent 正在后台部署':'主机已添加','ok')}editing.value=false;await refreshHosts(true)}catch(error){toast(`保存失败：${error.message}`,'err')}finally{saving.value=false}}
async function removeHost(host){const value=window.prompt(`删除后关联服务也会移除。请输入主机名称“${host.name}”确认：`);if(value!==host.name)return;try{await api.del(`/servers/${host.id}`);toast('主机已删除','ok');await refreshHosts(true)}catch(error){toast(`删除失败：${error.message}`,'err')}}
async function loadAgentVersion(){try{const [agent,alloy]=await Promise.all([api.get('/agents/version'),api.get('/logs/agents/version')]);currentAgentVersion.value=agent.current_version||'2.5.0';lokiConfigured.value=!!alloy.loki_configured}catch{/* 使用内置目标版本 */}}
async function upgradeHost(host){try{await api.post(`/servers/${host.id}/upgrade-agent`);toast(`${host.name} Agent 已进入后台升级`,'ok');await refreshHosts(true)}catch(error){toast(`升级失败：${error.message}`,'err')}}
async function upgradeAll(){upgrading.value=true;try{const data=await api.post('/agents/upgrade-outdated');toast(`已安排 ${data.accepted||0} 台主机后台升级到 v${data.target_version}`,'ok');await refreshHosts(true)}catch(error){toast(`批量升级失败：${error.message}`,'err')}finally{upgrading.value=false}}
async function deployAlloy(host){try{await api.post(`/servers/${host.id}/logs/agent/deploy`);toast(`${host.name} 日志采集器已进入后台部署`,'ok');await refreshHosts(true)}catch(error){toast(`部署失败：${error.message}`,'err')}}
async function checkAlloy(host){try{await api.post(`/servers/${host.id}/logs/agent/check`);await refreshHosts(true)}catch(error){toast(`检查失败：${error.message}`,'err')}}
async function deployMissingAlloy(){alloyBusy.value=true;try{const data=await api.post('/logs/agents/deploy-missing');toast(`已安排 ${data.accepted||0} 台主机部署日志采集器`,'ok');await refreshHosts(true)}catch(error){toast(`批量部署失败：${error.message}`,'err')}finally{alloyBusy.value=false}}
function hasBackgroundWork(){return hosts.value.some(host=>host.agent_status==='deploying'||alloyWorking(host))}
function startPolling(){clearInterval(statusTimer);statusTimer=setInterval(()=>{if(props.visible&&hasBackgroundWork())refreshHosts(true)},2500)}
watch([()=>props.visible,hasBackgroundWork],([visible,working])=>{if(visible){loadAgentVersion();if(working)startPolling();else clearInterval(statusTimer)}else clearInterval(statusTimer)},{immediate:true})
onUnmounted(()=>clearInterval(statusTimer))
</script>

<style scoped>
.drawer-mask{position:fixed;inset:0;background:rgba(15,23,42,.48);z-index:2400;display:flex;justify-content:flex-end}.host-drawer{width:min(560px,96vw);height:100%;background:var(--bg);box-shadow:-12px 0 40px rgba(15,23,42,.2);padding:20px;overflow:auto}.host-drawer header{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:1px solid var(--border);padding-bottom:14px}.host-drawer h2,.host-drawer h3{margin:0}.host-drawer p{margin:5px 0 0;color:var(--muted);font-size:13px}.drawer-actions{display:flex;gap:8px;margin:16px 0}.host-list{display:flex;flex-direction:column;gap:7px}.host-row{display:flex;align-items:center;border:1px solid var(--border);background:var(--card);border-radius:9px;padding:7px}.host-row.selected{border-color:var(--primary)}.host-main{border:0;background:none;color:var(--text);display:flex;align-items:center;gap:9px;flex:1;text-align:left;cursor:pointer}.host-main span:last-child{display:flex;flex-direction:column;gap:3px}.host-main small{color:var(--muted)}.dot{width:8px;height:8px;border-radius:50%}.dot.ok{background:var(--ok)}.dot.off{background:#94a3b8}.link-btn{border:0;background:none;color:var(--primary);cursor:pointer}.link-btn.danger{color:var(--err)}.host-form{margin-top:18px;padding:16px;background:var(--card);border:1px solid var(--border);border-radius:10px}.host-form>label,.form-grid label{display:flex;flex-direction:column;gap:5px;margin-top:11px;font-size:13px;color:var(--muted)}.host-form input,.host-form select,.host-form textarea{border:1px solid var(--border);background:var(--bg);color:var(--text);padding:9px;border-radius:6px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.host-form .check{flex-direction:row;align-items:center;color:var(--text)}.host-form footer{display:flex;justify-content:flex-end;gap:8px;margin-top:16px}.test-result{margin-top:10px;padding:8px;border-radius:6px;font-size:12px}.test-result.ok{color:#15803d;background:#dcfce7}.test-result.bad{color:#b91c1c;background:#fee2e2}@media(max-width:600px){.form-grid{grid-template-columns:1fr}}
.host-drawer{width:min(700px,96vw)}.drawer-actions{flex-wrap:wrap}.host-main small em{font-style:normal;color:var(--err);margin-left:5px}.link-btn{font-size:12px}
</style>
