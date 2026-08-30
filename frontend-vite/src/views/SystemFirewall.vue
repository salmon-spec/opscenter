<template>
  <div class="view firewall-view">
    <div class="view-head"><div><h1 class="view-title">防火墙</h1><p class="view-sub">{{ currentHost?.name || '未选择主机' }} · UFW / Firewalld</p></div><button class="btn" :disabled="loading" @click="load">↻ 刷新</button></div>
    <div v-if="error" class="notice error">{{ error }}</div>
    <section class="card summary">
      <div><span class="muted">防火墙组件</span><b>{{ data.installed ? data.backend : '未安装' }}</b></div><div><span class="muted">运行状态</span><b :class="data.enabled?'ok':'off'">{{ data.enabled?'已启用':'未启用' }}</b></div><div><span class="muted">当前 SSH 端口</span><b>{{ data.ssh_port || 22 }}</b></div><div class="summary-actions"><button class="btn btn-sm" :disabled="!data.installed" @click="toggleState">{{ data.enabled?'停用防火墙':'启用防火墙' }}</button><button class="btn btn-primary btn-sm" :disabled="!data.installed" @click="formOpen=true">添加规则</button></div>
    </section>
    <section class="card rules">
      <div v-if="loading" class="loading"><span class="spinner"></span>正在读取防火墙规则…</div>
      <table v-else class="table"><thead><tr><th>编号</th><th>端口</th><th>协议</th><th>动作</th><th>来源</th><th>操作</th></tr></thead><tbody><tr v-for="rule in data.rules||[]" :key="rule.id"><td class="mono">{{ rule.id }}</td><td>{{ rule.port }}</td><td>{{ rule.protocol }}</td><td><span class="tag" :class="rule.action==='allow'?'tag-green':'tag-red'">{{ rule.action }}</span></td><td class="mono">{{ rule.source }}</td><td><button class="link-danger" @click="deleteRule(rule)">删除</button></td></tr><tr v-if="!data.rules?.length"><td colspan="6"><EmptyState icon="🛡" text="当前没有已识别的端口规则" style="padding:32px" /></td></tr></tbody></table>
    </section>
    <Modal :visible="formOpen" title="添加防火墙规则" width="520px" @close="formOpen=false"><div class="form"><label>端口或范围<input v-model="form.port" placeholder="例如 80 或 8000-8010" /></label><label>协议<select v-model="form.protocol"><option value="tcp">TCP</option><option value="udp">UDP</option></select></label><label>动作<select v-model="form.action"><option value="allow">允许</option><option value="deny">拒绝（UFW）</option></select></label><label>来源<input v-model="form.source" placeholder="any 或 10.66.66.0/24" /></label></div><div class="modal-actions"><button class="btn" @click="formOpen=false">取消</button><button class="btn btn-primary" :disabled="saving" @click="addRule">保存规则</button></div></Modal>
  </div>
</template>
<script setup>
import { onMounted, reactive, ref, watch } from 'vue'
import { api, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import { useHostContext } from '../hostContext'
const {selectedHostId,currentHost,refreshHosts}=useHostContext()
const data=reactive({installed:false,enabled:false,backend:null,rules:[],ssh_port:22}),loading=ref(false),error=ref(''),formOpen=ref(false),saving=ref(false)
const form=reactive({port:'',protocol:'tcp',action:'allow',source:'any'})
async function load(){if(!selectedHostId.value)return;loading.value=true;error.value='';try{Object.assign(data,await api.get(`/servers/${selectedHostId.value}/firewall`))}catch(e){error.value=e.message}finally{loading.value=false}}
async function addRule(){saving.value=true;try{await api.post(`/servers/${selectedHostId.value}/firewall/rules`,{...form});toast('防火墙规则已添加','ok');formOpen.value=false;Object.assign(form,{port:'',protocol:'tcp',action:'allow',source:'any'});await load()}catch(e){toast(`添加失败：${e.message}`,'err')}finally{saving.value=false}}
function includesSshPort(value){const parts=String(value).split(/[-:]/).map(Number);const start=parts[0],end=parts[1]||start,ssh=Number(data.ssh_port);return Number.isFinite(start)&&start<=ssh&&ssh<=end}
async function deleteRule(rule){let confirmSsh=false;if(includesSshPort(rule.port)){confirmSsh=confirm(`该规则包含当前 SSH 端口 ${data.ssh_port}，删除可能导致主机失联。确认继续？`);if(!confirmSsh)return}else if(!confirm(`确认删除 ${rule.port}/${rule.protocol} 规则？`))return;try{await api.post(`/servers/${selectedHostId.value}/firewall/rules/delete`,{rule_id:String(rule.id),port:String(rule.port),protocol:rule.protocol==='udp'?'udp':'tcp',confirm_ssh_disruption:confirmSsh});toast('规则已删除','ok');await load()}catch(e){toast(`删除失败：${e.message}`,'err')}}
async function toggleState(){const name=prompt(`${data.enabled?'停用':'启用'}防火墙可能影响网络访问。请输入主机名称“${currentHost.value?.name}”确认：`);if(name!==currentHost.value?.name)return;try{await api.post(`/servers/${selectedHostId.value}/firewall/state`,{enabled:!data.enabled,confirm_name:name});toast('防火墙状态已更新','ok');await load()}catch(e){toast(`操作失败：${e.message}`,'err')}}
watch(selectedHostId,load);onMounted(async()=>{await refreshHosts();load()})
</script>
<style scoped>
.firewall-view{max-width:1500px;margin:0 auto}.notice{padding:10px 14px;border-radius:7px;margin-bottom:12px}.error{background:#fef2f2;color:var(--err)}.summary{display:flex;align-items:center;gap:45px;padding:18px;margin-bottom:14px}.summary>div{display:flex;flex-direction:column;gap:6px}.summary-actions{margin-left:auto;flex-direction:row!important}.ok{color:var(--ok)}.off{color:var(--muted)}.rules{overflow:auto}.link-danger{border:0;background:none;color:var(--err);cursor:pointer}.form{display:grid;grid-template-columns:1fr 1fr;gap:14px}.form label{display:flex;flex-direction:column;gap:6px;color:var(--muted);font-size:12px}.form input,.form select{border:1px solid var(--border);border-radius:6px;padding:9px;background:var(--card);color:var(--text)}.modal-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}@media(max-width:800px){.summary{align-items:flex-start;gap:18px;flex-wrap:wrap}.summary-actions{margin-left:0}.form{grid-template-columns:1fr}}
</style>
