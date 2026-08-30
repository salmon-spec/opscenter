<template>
  <div class="view ssh-view">
    <div class="view-head">
      <div><h1 class="view-title">SSH 管理</h1><p class="view-sub">{{ currentHost?.name || '未选择主机' }} · 服务、会话、登录记录与授权密钥</p></div>
      <button class="btn" :disabled="loading" @click="loadAll">↻ 刷新</button>
    </div>
    <div v-if="error" class="notice error">{{ error }}</div>

    <section class="card summary">
      <div><span>服务</span><b>{{ overview.service || '-' }}</b></div>
      <div><span>状态</span><b :class="overview.status==='active'?'ok':'off'">{{ overview.status || '-' }}</b></div>
      <div><span>SSH 端口</span><b>{{ overview.effective?.port || overview.configured_port || 22 }}</b></div>
      <div><span>在线会话</span><b>{{ overview.session_count || 0 }}</b></div>
      <button class="btn btn-sm config-button" @click="openConfig">安全配置</button>
    </section>

    <div class="tabs">
      <button v-for="item in tabs" :key="item.id" :class="{active:tab===item.id}" @click="tab=item.id">{{ item.label }}</button>
    </div>

    <section v-if="tab==='sessions'" class="card table-card">
      <table class="table"><thead><tr><th>用户</th><th>终端</th><th>登录时间</th><th>来源</th></tr></thead><tbody>
        <tr v-for="(row,index) in sessions" :key="`${row.terminal}-${index}`"><td>{{ row.user }}</td><td class="mono">{{ row.terminal }}</td><td>{{ row.login_at }}</td><td class="mono">{{ row.remote || '本机' }}</td></tr>
        <tr v-if="!sessions.length"><td colspan="4"><EmptyState icon="⌁" text="当前没有在线 SSH 会话" style="padding:32px" /></td></tr>
      </tbody></table>
    </section>

    <section v-else-if="tab==='logins'" class="card table-card">
      <table class="table"><thead><tr><th>用户</th><th>终端</th><th>来源</th><th>记录</th></tr></thead><tbody>
        <tr v-for="(row,index) in logins" :key="index"><td>{{ row.user }}</td><td class="mono">{{ row.terminal }}</td><td class="mono">{{ row.remote }}</td><td>{{ row.detail }}</td></tr>
        <tr v-if="!logins.length"><td colspan="4"><EmptyState icon="◷" text="暂无可用的登录记录" style="padding:32px" /></td></tr>
      </tbody></table>
    </section>

    <section v-else class="card key-card">
      <div class="key-toolbar"><label>Linux 用户<input v-model.trim="keyUser" maxlength="32" @keyup.enter="loadKeys" /></label><button class="btn btn-sm" @click="loadKeys">查询</button><button class="btn btn-primary btn-sm" @click="keyModal=true">添加公钥</button></div>
      <table class="table"><thead><tr><th>类型</th><th>SHA256 指纹</th><th>备注</th><th>操作</th></tr></thead><tbody>
        <tr v-for="key in keys" :key="key.fingerprint || key.comment"><td>{{ key.type }}</td><td class="mono fingerprint">{{ key.fingerprint || '-' }}</td><td>{{ key.comment || '-' }}</td><td><button v-if="key.fingerprint" class="link-danger" @click="removeKey(key)">删除</button></td></tr>
        <tr v-if="!keys.length"><td colspan="4"><EmptyState icon="⌁" text="该用户暂无已识别的授权密钥" style="padding:32px" /></td></tr>
      </tbody></table>
      <p class="hint">为避免泄露凭证，界面和接口只展示公钥指纹，不回传完整公钥。</p>
    </section>

    <Modal :visible="keyModal" title="添加 SSH 公钥" width="640px" @close="keyModal=false">
      <div class="form single"><label>Linux 用户<input v-model.trim="keyUser" /></label><label>OpenSSH 公钥<textarea v-model.trim="publicKey" rows="5" placeholder="ssh-ed25519 AAAA... comment"></textarea></label></div>
      <div class="modal-actions"><button class="btn" @click="keyModal=false">取消</button><button class="btn btn-primary" :disabled="saving" @click="addKey">添加</button></div>
    </Modal>

    <Modal :visible="configModal" title="SSH 安全配置" width="680px" @close="configModal=false">
      <div class="warning">修改端口或认证方式可能导致主机失联。系统会先执行 sshd -t，失败自动回滚；修改端口前请先放行防火墙。</div>
      <div class="form config-grid">
        <label>监听端口<input v-model.number="config.port" type="number" min="1" max="65535" /></label>
        <label>Root 登录<select v-model="config.permit_root_login"><option value="prohibit-password">仅密钥</option><option value="no">禁止</option><option value="yes">允许</option></select></label>
        <label><span>密码认证</span><select v-model="config.password_authentication"><option :value="false">关闭</option><option :value="true">开启</option></select></label>
        <label><span>公钥认证</span><select v-model="config.pubkey_authentication"><option :value="true">开启</option><option :value="false">关闭</option></select></label>
        <label>最大认证次数<input v-model.number="config.max_auth_tries" type="number" min="1" max="10" /></label>
        <label>空闲检测间隔（秒）<input v-model.number="config.client_alive_interval" type="number" min="0" max="3600" /></label>
      </div>
      <div class="modal-actions"><button class="btn" @click="configModal=false">取消</button><button class="btn btn-primary" :disabled="saving" @click="saveConfig">校验并应用</button></div>
    </Modal>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { api, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import { useHostContext } from '../hostContext'

const { selectedHostId, currentHost, refreshHosts } = useHostContext()
const tabs=[{id:'sessions',label:'在线会话'},{id:'logins',label:'登录记录'},{id:'keys',label:'授权密钥'}]
const tab=ref('sessions'),loading=ref(false),saving=ref(false),error=ref(''),sessions=ref([]),logins=ref([]),keys=ref([])
const keyUser=ref('root'),publicKey=ref(''),keyModal=ref(false),configModal=ref(false)
const overview=reactive({service:'',status:'',configured_port:22,effective:{},session_count:0})
const config=reactive({port:22,permit_root_login:'prohibit-password',password_authentication:false,pubkey_authentication:true,max_auth_tries:6,client_alive_interval:300})

const yes=value=>String(value).toLowerCase()==='yes'
async function loadAll(){if(!selectedHostId.value)return;loading.value=true;error.value='';try{const id=selectedHostId.value;const [head,sessionData,loginData]=await Promise.all([api.get(`/servers/${id}/ssh/overview`),api.get(`/servers/${id}/ssh/sessions`),api.get(`/servers/${id}/ssh/logins`,{limit:100})]);Object.assign(overview,head);sessions.value=sessionData.items||[];logins.value=loginData.items||[]}catch(e){error.value=e.message}finally{loading.value=false}}
async function loadKeys(){if(!selectedHostId.value)return;try{const result=await api.get(`/servers/${selectedHostId.value}/ssh/authorized-keys`,{user:keyUser.value});keys.value=result.items||[]}catch(e){keys.value=[];toast(`密钥读取失败：${e.message}`,'err')}}
async function addKey(){saving.value=true;try{await api.post(`/servers/${selectedHostId.value}/ssh/authorized-keys`,{user:keyUser.value,public_key:publicKey.value});toast('公钥已添加','ok');publicKey.value='';keyModal.value=false;await loadKeys()}catch(e){toast(`添加失败：${e.message}`,'err')}finally{saving.value=false}}
async function removeKey(key){if(!confirm(`确认删除 ${key.comment||key.fingerprint}？删除后对应客户端将无法再使用该密钥登录。`))return;try{await api.post(`/servers/${selectedHostId.value}/ssh/authorized-keys/delete`,{user:keyUser.value,fingerprint:key.fingerprint});toast('公钥已删除','ok');await loadKeys()}catch(e){toast(`删除失败：${e.message}`,'err')}}
function openConfig(){const e=overview.effective||{};Object.assign(config,{port:Number(e.port||overview.configured_port||22),permit_root_login:e.permitrootlogin||'prohibit-password',password_authentication:yes(e.passwordauthentication),pubkey_authentication:e.pubkeyauthentication===undefined?true:yes(e.pubkeyauthentication),max_auth_tries:Number(e.maxauthtries||6),client_alive_interval:Number(e.clientaliveinterval||300)});configModal.value=true}
async function saveConfig(){const name=prompt(`请输入主机名称“${currentHost.value?.name}”确认应用 SSH 配置：`);if(name!==currentHost.value?.name)return;saving.value=true;try{await api.put(`/servers/${selectedHostId.value}/ssh/config`,{...config,confirm_name:name});toast('SSH 配置已校验并应用','ok');configModal.value=false;await refreshHosts(true);await loadAll()}catch(e){toast(`配置失败：${e.message}`,'err')}finally{saving.value=false}}
watch(selectedHostId,()=>{keys.value=[];loadAll()});watch(tab,value=>{if(value==='keys'&&!keys.value.length)loadKeys()});onMounted(async()=>{await refreshHosts();await loadAll()})
</script>

<style scoped>
.ssh-view{max-width:1500px;margin:0 auto}.notice{padding:10px 14px;border-radius:7px;margin-bottom:12px}.error{background:#fef2f2;color:var(--err)}.summary{display:flex;align-items:center;gap:50px;padding:18px 22px;margin-bottom:14px}.summary>div{display:flex;flex-direction:column;gap:6px}.summary span,.hint{color:var(--muted);font-size:12px}.config-button{margin-left:auto}.ok{color:var(--ok)}.off{color:var(--muted)}.tabs{display:flex;gap:3px;margin:0 0 12px}.tabs button{padding:9px 18px;border:0;border-radius:6px;background:transparent;color:var(--muted);cursor:pointer}.tabs button.active{background:var(--primary);color:#fff}.table-card,.key-card{overflow:auto}.key-toolbar{display:flex;align-items:flex-end;gap:9px;padding:16px}.key-toolbar label,.form label{display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:12px}.key-toolbar input,.form input,.form select,.form textarea{border:1px solid var(--border);border-radius:6px;padding:9px;background:var(--card);color:var(--text)}.fingerprint{font-size:12px}.link-danger{border:0;background:none;color:var(--err);cursor:pointer}.hint{padding:0 16px 14px}.form{display:grid;gap:14px}.single{grid-template-columns:1fr}.config-grid{grid-template-columns:1fr 1fr}.warning{padding:10px 12px;margin-bottom:15px;border-radius:7px;background:#fff7ed;color:#c2410c;font-size:13px}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}@media(max-width:800px){.summary{align-items:flex-start;gap:18px;flex-wrap:wrap}.config-button{margin-left:0}.config-grid{grid-template-columns:1fr}.key-toolbar{flex-wrap:wrap}}
</style>
