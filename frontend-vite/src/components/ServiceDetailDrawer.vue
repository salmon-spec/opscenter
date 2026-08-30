<template>
  <Teleport to="body">
    <transition name="slide">
      <div v-if="visible" class="drawer-mask" @click.self="closeDrawer">
        <div class="drawer">
          <div class="drawer-head">
            <div><h3>{{ detail?.name || service?.name || '服务详情' }}</h3><div class="muted head-meta">{{ detail?.category || service?.category || '' }}</div></div>
            <div class="head-actions"><button v-if="plazaKey && !editing" class="btn btn-sm" @click="startEdit">编辑信息</button><button class="btn btn-ghost btn-sm" @click="closeDrawer">✕</button></div>
          </div>
          <div class="drawer-body">
            <div v-if="loading" class="loading"><span class="spinner"></span>加载中…</div>
            <form v-else-if="editing" class="edit-form" @submit.prevent="saveEdit">
              <div class="form-grid">
                <div class="field full"><label>所属主机</label><select v-model="form.server_id" class="select"><option value="">使用默认主机</option><option v-for="host in hosts" :key="host.id" :value="host.id">{{ host.name }} · {{ host.host }}</option></select></div>
                <div class="field"><label>服务名称 *</label><input v-model.trim="form.name" class="input" required /></div>
                <div class="field"><label>分类 *</label><input v-model.trim="form.category" class="input" required /></div>
                <div class="field full"><label>访问地址 *</label><input v-model.trim="form.entry_url" class="input" required placeholder="http://10.66.66.x:端口/" /></div>
                <div class="field full"><label>健康检查地址</label><input v-model.trim="form.health_url" class="input" placeholder="http://10.66.66.x:端口/health" /></div>
                <div class="field"><label>负责人</label><input v-model.trim="form.owner" class="input" placeholder="团队或联系人" /></div>
                <div class="field"><label>图标标识</label><input v-model.trim="form.icon" class="input" placeholder="box / tool / chart" /></div>
                <div class="field full"><label>文档地址</label><input v-model.trim="form.documentation_url" class="input" placeholder="https://..." /></div>
                <div class="field full"><label>标签</label><input v-model.trim="form.tagsText" class="input" placeholder="多个标签使用逗号分隔" /></div>
                <div class="field full"><label>服务说明</label><textarea v-model.trim="form.description" class="textarea" rows="3" placeholder="用途、使用范围、注意事项"></textarea></div>
              </div>
              <div class="edit-section">
                <div class="section-title">登录信息</div>
                <div class="form-grid">
                  <div class="field"><label>账号</label><input v-model.trim="form.username" class="input" autocomplete="off" /></div>
                  <div class="field"><label>新密码</label><input v-model="form.password" class="input" type="password" autocomplete="new-password" placeholder="留空保留原密码" /></div>
                  <div class="field full"><label>登录备注</label><textarea v-model.trim="form.login_notes" class="textarea" rows="3" placeholder="登录步骤、二次验证、权限说明等；不要重复填写密码"></textarea></div>
                  <label v-if="detail?.has_credentials" class="check-row full"><input v-model="form.clear_password" type="checkbox" /> 清除已保存的密码</label>
                </div>
              </div>
              <div class="form-actions"><button type="button" class="btn" @click="editing = false">取消</button><button type="submit" class="btn btn-primary" :disabled="saving">{{ saving ? '保存中…' : '保存修改' }}</button></div>
            </form>
            <template v-else>
              <div class="detail-grid">
                <div class="d-item"><div class="d-label">状态</div><span class="tag" :class="statusClass">{{ statusText }}</span></div>
                <div class="d-item"><div class="d-label">所属主机</div><span>{{ hostText }}</span></div>
                <div class="d-item full"><div class="d-label">访问地址</div><a v-if="url" :href="url" target="_blank" rel="noopener" class="d-link">{{ url }}</a><span v-else>-</span></div>
                <div class="d-item full"><div class="d-label">健康检查</div><span class="mono">{{ detail?.health_url || '-' }}</span><span v-if="detail?.http_status" class="health-extra">HTTP {{ detail.http_status }} · {{ detail.latency_ms ?? '-' }}ms</span></div>
                <div class="d-item"><div class="d-label">分类</div><span>{{ detail?.category || service?.category || '-' }}</span></div>
                <div class="d-item"><div class="d-label">来源</div><span>{{ sourceText }}</span></div>
                <div class="d-item"><div class="d-label">部署方式</div><span>{{ deployTypeText }}</span></div>
                <div class="d-item"><div class="d-label">版本</div><span>{{ detail?.version || service?.version || '-' }}</span></div>
                <div class="d-item"><div class="d-label">运行时长</div><span>{{ fmtDuration(uptimeSeconds) }}</span></div>
                <div class="d-item"><div class="d-label">负责人</div><span>{{ detail?.owner || '-' }}</span></div>
                <div class="d-item"><div class="d-label">端口</div><span class="mono">{{ detail?.ports || service?.ports || detail?.port || '-' }}</span></div>
                <div class="d-item"><div class="d-label">容器名</div><span class="mono">{{ detail?.container_name || service?.container_name || '-' }}</span></div>
                <div class="d-item full"><div class="d-label">镜像</div><span class="mono">{{ detail?.image || service?.image || '-' }}</span></div>
              </div>
              <div v-if="detail?.tags?.length" class="tag-list"><span v-for="tag in detail.tags" :key="tag" class="tag tag-slate">{{ tag }}</span></div>
              <div class="d-section"><div class="d-section-title">服务说明</div><p class="detail-text">{{ detail?.description || service?.description || '暂无说明' }}</p><a v-if="detail?.documentation_url" :href="detail.documentation_url" target="_blank" rel="noopener" class="d-link docs-link">打开使用文档 ↗</a></div>
              <div v-if="plazaKey" class="d-section credential-card">
                <div class="credential-head"><div class="d-section-title">登录凭证</div><span class="security-tip">加密保存 · 显示后 60 秒自动隐藏</span></div>
                <div class="credential-row"><span class="d-label">账号</span><span class="mono credential-value">{{ detail?.credential_username || '未填写' }}</span><button v-if="detail?.credential_username" class="btn btn-ghost btn-sm" @click="copyText(detail.credential_username, '账号')">复制</button></div>
                <div class="credential-row"><span class="d-label">密码</span><span class="mono credential-value">{{ revealedPassword || (detail?.has_credentials ? '••••••••' : '未保存') }}</span><button v-if="detail?.has_credentials" class="btn btn-ghost btn-sm" @click="revealPassword">{{ revealedPassword ? '隐藏' : '显示' }}</button><button v-if="revealedPassword" class="btn btn-ghost btn-sm" @click="copyText(revealedPassword, '密码')">复制</button></div>
                <p v-if="detail?.login_notes" class="login-notes">{{ detail.login_notes }}</p><button v-if="!detail?.credential_username && !detail?.has_credentials" class="btn btn-sm" @click="startEdit">添加账号密码</button>
              </div>
              <div v-if="outbound.length || inbound.length" class="d-section"><div class="d-section-title">服务依赖</div><div class="dep-list"><div v-for="dep in outbound" :key="`out-${dep.id}`" class="dep-item"><span class="tag tag-green">下游</span><span>{{ dep.target_name }}</span><span class="muted">{{ dep.label || dep.relation_type }}</span></div><div v-for="dep in inbound" :key="`in-${dep.id}`" class="dep-item"><span class="tag tag-slate">上游</span><span>{{ dep.source_name }}</span><span class="muted">{{ dep.label || dep.relation_type }}</span></div></div></div>
            </template>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api, fmtDuration, toast } from '../api'

const props = defineProps({ visible: Boolean, service: { type: Object, default: null } })
const emit = defineEmits(['close', 'updated'])
const detail = ref(null), loading = ref(false), editing = ref(false), saving = ref(false), hosts = ref([]), revealedPassword = ref('')
let hideTimer = null
const form = reactive({ server_id: '', name: '', category: '', icon: '', entry_url: '', health_url: '', owner: '', documentation_url: '', tagsText: '', description: '', username: '', password: '', login_notes: '', clear_password: false })
const plazaKey = computed(() => props.service?.key || (props.service?.id?.startsWith?.('plaza:') ? props.service.id.slice(6) : ''))
const url = computed(() => detail.value?.entry_url || detail.value?.url || props.service?.entry_url || props.service?.url || '')
const statusText = computed(() => ({ online:'在线',up:'在线',offline:'离线',down:'离线',degraded:'异常' }[detail.value?.status || props.service?.status] || '未知'))
const statusClass = computed(() => ({ online:'tag-green',up:'tag-green',offline:'tag-red',down:'tag-red',degraded:'tag-amber' }[detail.value?.status || props.service?.status] || 'tag-slate'))
const deployTypeText = computed(() => ({ docker:'Docker',systemd:'systemd',compose:'Compose',manual:'手动' }[detail.value?.deploy_type || props.service?.deploy_type] || detail.value?.deploy_type || '-'))
const sourceText = computed(() => ({ catalog:'内置目录',manual:'手动添加',docker_auto:'Docker 扫描',docker_label:'Docker 标签',nginx:'Nginx 扫描',agent:'Agent 扫描' }[detail.value?.source] || detail.value?.source || '-'))
const hostText = computed(() => { const name=detail.value?.server?.name || props.service?.server_name, host=detail.value?.server?.host || props.service?.server_host; return name&&host?`${name} (${host})`:name||host||'-' })
const uptimeSeconds = computed(() => { if(detail.value?.running_seconds!=null)return detail.value.running_seconds;const st=detail.value?.started_at||props.service?.started_at;if(!st)return null;const t=new Date(st).getTime();return t>0?Math.floor((Date.now()-t)/1000):null })
const outbound = computed(() => detail.value?.relations?.outgoing || []), inbound = computed(() => detail.value?.relations?.incoming || [])

function clearReveal(){revealedPassword.value='';if(hideTimer)clearTimeout(hideTimer);hideTimer=null}
function closeDrawer(){editing.value=false;clearReveal();emit('close')}
async function loadDetail(){const id=props.service?.id;if(!id)return;loading.value=true;clearReveal();try{detail.value=plazaKey.value?await api.get(`/services/plaza/${encodeURIComponent(plazaKey.value)}/detail`):await api.get(`/services/${id}/detail`)}catch(error){detail.value=null;toast(`详情加载失败：${error.message}`,'error')}finally{loading.value=false}}
async function startEdit(){try{if(!detail.value)await loadDetail();if(!hosts.value.length)hosts.value=await api.get('/servers');const d=detail.value||props.service||{};Object.assign(form,{server_id:d.server?.id||d.server_id||'',name:d.name||'',category:d.category||'',icon:d.icon||'box',entry_url:d.entry_url||d.url||'',health_url:d.health_url||'',owner:d.owner||'',documentation_url:d.documentation_url||'',tagsText:(d.tags||[]).join(', '),description:d.description||'',username:d.credential_username||'',password:'',login_notes:d.login_notes||'',clear_password:false});editing.value=true}catch(error){toast(`编辑信息加载失败：${error.message}`,'error')}}
async function saveEdit(){if(!form.name||!form.category||!/^https?:\/\//i.test(form.entry_url)){toast('请填写名称、分类和正确的 HTTP(S) 地址','error');return}saving.value=true;try{const payload={server_id:form.server_id||null,name:form.name,category:form.category,icon:form.icon,entry_url:form.entry_url,health_url:form.health_url,owner:form.owner,documentation_url:form.documentation_url,tags:form.tagsText.split(/[,，]/).map(x=>x.trim()).filter(Boolean),description:form.description,username:form.username,login_notes:form.login_notes,clear_password:form.clear_password};if(form.password)payload.password=form.password;await api.put(`/services/plaza/${encodeURIComponent(plazaKey.value)}`,payload);editing.value=false;await loadDetail();emit('updated',detail.value);toast('服务信息已保存','success')}catch(error){toast(`保存失败：${error.message}`,'error')}finally{saving.value=false}}
async function revealPassword(){if(revealedPassword.value){clearReveal();return}try{const data=await api.post(`/services/plaza/${encodeURIComponent(plazaKey.value)}/credentials/reveal`);revealedPassword.value=data.password||'';hideTimer=setTimeout(clearReveal,60000)}catch(error){toast(`密码显示失败：${error.message}`,'error')}}
async function copyText(value,label){try{await navigator.clipboard.writeText(value);toast(`${label}已复制`,'success')}catch{toast(`无法复制${label}，请手动选择`,'error')}}
watch(()=>[props.visible,props.service?.id,props.service?.key],([visible])=>{if(!visible){detail.value=null;editing.value=false;clearReveal();return}loadDetail()},{immediate:true})
onBeforeUnmount(clearReveal)
</script>

<style scoped>
.drawer-mask{position:fixed;inset:0;background:rgba(15,23,42,.4);z-index:1900;display:flex;justify-content:flex-end}.drawer{width:600px;max-width:96vw;height:100%;background:#fff;box-shadow:-10px 0 30px rgba(0,0,0,.15);display:flex;flex-direction:column}.drawer-head{display:flex;align-items:flex-start;justify-content:space-between;padding:18px 20px;border-bottom:1px solid var(--border)}.drawer-head h3{margin:0 0 4px;font-size:18px}.head-meta{font-size:12px}.head-actions{display:flex;gap:8px}.drawer-body{padding:18px 20px;overflow:auto}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 18px}.d-item.full{grid-column:1/-1}.d-label{font-size:11px;color:var(--muted);margin-bottom:4px}.d-item span,.d-item a{font-size:13px;word-break:break-all}.d-link{color:var(--brand);text-decoration:none}.d-link:hover{text-decoration:underline}.health-extra{margin-left:10px;color:var(--muted)}.d-section{margin-top:22px}.d-section-title,.section-title{font-size:13px;font-weight:650;margin-bottom:9px}.detail-text,.login-notes{white-space:pre-wrap;margin:0;color:var(--muted);font-size:13px;line-height:1.65}.docs-link{display:inline-block;margin-top:8px;font-size:13px}.tag-list{display:flex;gap:6px;flex-wrap:wrap;margin-top:15px}.credential-card{padding:14px;border:1px solid var(--border);border-radius:10px;background:#f8fafc}.credential-head{display:flex;justify-content:space-between;align-items:center}.security-tip{font-size:11px;color:var(--ok)}.credential-row{display:flex;align-items:center;gap:8px;min-height:38px}.credential-row>.d-label{width:42px;margin:0}.credential-value{flex:1;font-size:13px}.login-notes{padding-top:9px;border-top:1px dashed var(--border);margin:6px 0 10px}.dep-list{display:flex;flex-direction:column;gap:7px}.dep-item{display:flex;align-items:center;gap:8px;font-size:13px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.form-grid .full{grid-column:1/-1}.edit-section{border-top:1px solid var(--border);padding-top:16px;margin-top:6px}.check-row{display:flex;align-items:center;gap:8px;font-size:13px}.form-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px;padding-top:14px;border-top:1px solid var(--border)}.slide-enter-active,.slide-leave-active{transition:transform .2s}.slide-enter-from,.slide-leave-to{transform:translateX(100%)}@media(max-width:640px){.detail-grid,.form-grid{grid-template-columns:1fr}.d-item.full,.form-grid .full{grid-column:auto}.drawer{max-width:100vw}.health-extra{display:block;margin:4px 0 0}}
</style>
