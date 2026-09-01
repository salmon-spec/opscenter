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
                <div class="section-title">健康检查策略</div>
                <div class="form-grid">
                  <label class="check-row full"><input v-model="form.probe_enabled" type="checkbox" /> 启用后台健康检查</label>
                  <div class="field"><label>检查间隔（秒）</label><input v-model.number="form.probe_interval_seconds" class="input" type="number" min="30" max="3600" /></div>
                  <div class="field"><label>超时时间（秒）</label><input v-model.number="form.probe_timeout_seconds" class="input" type="number" min="1" max="30" step="0.5" /></div>
                  <div class="field full"><label>视为成功的 HTTP 状态码</label><input v-model.trim="form.probe_success_statuses" class="input" placeholder="200-399,401,403" /></div>
                  <div class="field"><label>连续失败阈值</label><input v-model.number="form.probe_failure_threshold" class="input" type="number" min="1" max="10" /></div>
                  <div class="field"><label>连续恢复阈值</label><input v-model.number="form.probe_recovery_threshold" class="input" type="number" min="1" max="5" /></div>
                  <label class="check-row full"><input v-model="form.probe_verify_tls" type="checkbox" /> 校验 HTTPS 证书</label>
                  <label class="check-row full"><input v-model="form.probe_notifications_enabled" type="checkbox" /> 发送故障与恢复通知</label>
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
              <div v-if="plazaKey" class="d-section credential-card">
                <div class="credential-head"><div class="d-section-title">登录凭证（{{ credentials.length }}）</div><div class="credential-head-actions"><span class="security-tip">加密保存 · 显示后 60 秒自动隐藏</span><button class="btn btn-sm" @click="openCredentialEditor()">添加账号</button></div></div>
                <div v-if="credentialEditing" class="credential-editor form-grid">
                  <div class="field"><label>凭证名称 *</label><input v-model.trim="credentialForm.label" class="input" placeholder="管理员 / 只读账号" /></div>
                  <div class="field"><label>账号</label><input v-model.trim="credentialForm.username" class="input" autocomplete="off" /></div>
                  <div class="field"><label>{{ credentialForm.id ? '新密码' : '密码' }}</label><input v-model="credentialForm.password" class="input" type="password" autocomplete="new-password" :placeholder="credentialForm.id ? '留空保留原密码' : '可留空'" /></div>
                  <label class="check-row"><input v-model="credentialForm.is_default" type="checkbox" /> 设为默认账号</label>
                  <div class="field full"><label>登录备注</label><textarea v-model.trim="credentialForm.notes" class="textarea" rows="2" placeholder="登录步骤、权限说明等；不要填写密码"></textarea></div>
                  <div class="credential-editor-actions full"><button class="btn btn-sm" @click="cancelCredentialEditor">取消</button><button class="btn btn-primary btn-sm" :disabled="credentialSaving" @click="saveCredential">{{ credentialSaving ? '保存中…' : '保存凭证' }}</button></div>
                </div>
                <div v-if="credentials.length" class="credential-list">
                  <div v-for="credential in credentials" :key="credential.id" class="credential-item">
                    <div class="credential-item-head"><strong>{{ credential.label }}</strong><span v-if="credential.is_default" class="tag tag-green">默认</span><span class="credential-spacer"></span><button class="btn btn-ghost btn-sm" @click="openCredentialEditor(credential)">编辑</button><button class="btn btn-ghost btn-sm" @click="deleteCredential(credential)">删除</button></div>
                    <div class="credential-row"><span class="d-label">账号</span><span class="mono credential-value">{{ credential.username || '未填写' }}</span><button v-if="credential.username" class="btn btn-ghost btn-sm" @click="copyText(credential.username, '账号')">复制</button></div>
                    <div class="credential-row"><span class="d-label">密码</span><span class="mono credential-value">{{ revealedPasswords[credential.id] || (credential.has_password ? '••••••••' : '未保存') }}</span><button v-if="credential.has_password" class="btn btn-ghost btn-sm" @click="revealCredential(credential)">{{ revealedPasswords[credential.id] ? '隐藏' : '显示' }}</button><button v-if="revealedPasswords[credential.id]" class="btn btn-ghost btn-sm" @click="copyText(revealedPasswords[credential.id], '密码')">复制</button></div>
                    <p v-if="credential.notes" class="login-notes">{{ credential.notes }}</p>
                  </div>
                </div>
                <div v-else-if="!credentialEditing" class="empty-inline">尚未添加账号密码</div>
                <div v-if="detail?.credential_access_history?.length" class="access-history"><div class="d-label">最近查看记录</div><div v-for="row in detail.credential_access_history.slice(0, 3)" :key="row.id" class="access-row"><span>{{ row.actor }}</span><span>{{ row.ip }}</span><span>{{ fmtTime(row.created_at) }}</span></div></div>
              </div>
              <div class="d-section"><div class="d-section-title">服务说明</div><p class="detail-text">{{ detail?.description || service?.description || '暂无说明' }}</p><a v-if="detail?.documentation_url" :href="detail.documentation_url" target="_blank" rel="noopener" class="d-link docs-link">打开使用文档 ↗</a></div>
              <div v-if="plazaKey" class="d-section health-card">
                <div class="credential-head"><div class="d-section-title">健康检查</div><button class="btn btn-sm" :disabled="probing" @click="probeNow">{{ probing ? '检测中…' : '立即检测' }}</button></div>
                <div class="health-summary">
                  <div><span class="summary-value">{{ uptimeText }}</span><span class="summary-label">24h 可用率</span></div>
                  <div><span class="summary-value">{{ detail?.probe_summary?.checks_24h ?? 0 }}</span><span class="summary-label">24h 检测次数</span></div>
                  <div><span class="summary-value">{{ detail?.probe_summary?.avg_latency_ms_24h ?? '-' }}<small v-if="detail?.probe_summary?.avg_latency_ms_24h != null">ms</small></span><span class="summary-label">平均延迟</span></div>
                </div>
                <div class="policy-line">{{ detail?.probe_policy?.enabled ? `每 ${detail.probe_policy.interval_seconds} 秒检测` : '后台检测已停用' }} · 超时 {{ detail?.probe_policy?.timeout_seconds ?? 4 }} 秒 · 成功码 {{ detail?.probe_policy?.success_statuses || '200-399,401,403' }}</div>
                <div class="policy-line">连续失败 {{ detail?.probe_policy?.failure_threshold ?? 3 }} 次告警 · 连续成功 {{ detail?.probe_policy?.recovery_threshold ?? 1 }} 次恢复 · {{ detail?.probe_policy?.notifications_enabled ? '通知开启' : '通知关闭' }}</div>
                <div v-if="detail?.active_incident" class="incident-box"><div><strong>活动事件</strong> · {{ detail.active_incident.status }} · {{ fmtTime(detail.active_incident.opened_at) }}</div><div class="probe-error">{{ detail.active_incident.last_error || '未提供错误' }}</div><button v-if="detail.active_incident.status==='open'" class="btn btn-sm" @click="ackIncident">确认事件</button></div>
                <div v-if="detail?.active_silence" class="silence-box">已静默至 {{ fmtTime(detail.active_silence.ends_at) }} · {{ detail.active_silence.reason }} <button class="btn btn-sm" @click="endSilence">提前结束</button></div>
                <button v-else class="btn btn-sm" @click="createSilence">静默 2 小时</button>
                <div v-if="detail?.recent_probes?.length" class="probe-list"><div v-for="row in detail.recent_probes" :key="row.id" class="probe-row"><span class="status-dot" :class="row.status === 'up' ? 'dot-up' : 'dot-down'"></span><span>{{ fmtTime(row.checked_at) }}</span><span>HTTP {{ row.http_status ?? '-' }}</span><span>{{ row.latency_ms ?? '-' }}ms</span><span class="probe-error">{{ row.error }}</span></div></div>
                <div v-else class="empty-inline">暂无历史检测，点击“立即检测”生成首条记录</div>
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
import { api, fmtDuration, fmtTime, toast } from '../api'

const props = defineProps({ visible: Boolean, service: { type: Object, default: null } })
const emit = defineEmits(['close', 'updated'])
const detail = ref(null), loading = ref(false), editing = ref(false), saving = ref(false), probing = ref(false), hosts = ref([])
const credentialEditing = ref(false), credentialSaving = ref(false), revealedPasswords = reactive({}), revealTimers = new Map()
const credentialForm = reactive({ id: '', label: '', username: '', password: '', notes: '', is_default: false })
const form = reactive({ server_id: '', name: '', category: '', icon: '', entry_url: '', health_url: '', owner: '', documentation_url: '', tagsText: '', description: '', probe_enabled: true, probe_interval_seconds: 60, probe_timeout_seconds: 4, probe_success_statuses: '200-399,401,403', probe_verify_tls: true, probe_failure_threshold: 3, probe_recovery_threshold: 1, probe_notifications_enabled: true })
const plazaKey = computed(() => props.service?.key || (props.service?.id?.startsWith?.('plaza:') ? props.service.id.slice(6) : ''))
const url = computed(() => detail.value?.entry_url || detail.value?.url || props.service?.entry_url || props.service?.url || '')
const statusText = computed(() => ({ online:'在线',up:'在线',offline:'离线',down:'离线',degraded:'异常',disabled:'已停用' }[detail.value?.status || props.service?.status] || '未知'))
const statusClass = computed(() => ({ online:'tag-green',up:'tag-green',offline:'tag-red',down:'tag-red',degraded:'tag-amber' }[detail.value?.status || props.service?.status] || 'tag-slate'))
const deployTypeText = computed(() => ({ docker:'Docker',systemd:'systemd',compose:'Compose',manual:'手动' }[detail.value?.deploy_type || props.service?.deploy_type] || detail.value?.deploy_type || '-'))
const sourceText = computed(() => ({ catalog:'内置目录',manual:'手动添加',docker_auto:'Docker 扫描',docker_label:'Docker 标签',nginx:'Nginx 扫描',agent:'Agent 扫描' }[detail.value?.source] || detail.value?.source || '-'))
const hostText = computed(() => { const name=detail.value?.server?.name || props.service?.server_name, host=detail.value?.server?.host || props.service?.server_host; return name&&host?`${name} (${host})`:name||host||'-' })
const uptimeSeconds = computed(() => { if(detail.value?.running_seconds!=null)return detail.value.running_seconds;const st=detail.value?.started_at||props.service?.started_at;if(!st)return null;const t=new Date(st).getTime();return t>0?Math.floor((Date.now()-t)/1000):null })
const outbound = computed(() => detail.value?.relations?.outgoing || []), inbound = computed(() => detail.value?.relations?.incoming || [])
const credentials = computed(() => detail.value?.credentials || [])
const uptimeText = computed(() => detail.value?.probe_summary?.uptime_percent_24h == null ? '-' : `${detail.value.probe_summary.uptime_percent_24h}%`)

function clearReveal(id){const ids=id?[id]:Object.keys(revealedPasswords);ids.forEach(key=>{delete revealedPasswords[key];if(revealTimers.has(key)){clearTimeout(revealTimers.get(key));revealTimers.delete(key)}})}
function closeDrawer(){editing.value=false;credentialEditing.value=false;clearReveal();emit('close')}
async function loadCredentialHistory(){const key=plazaKey.value;if(!key||!detail.value)return;try{const rows=await api.get(`/services/plaza/${encodeURIComponent(key)}/credential-access-history`,{limit:5});if(detail.value&&plazaKey.value===key)detail.value.credential_access_history=rows}catch{if(detail.value&&plazaKey.value===key)detail.value.credential_access_history=[]}}
async function loadDetail(){const id=props.service?.id;if(!id)return;loading.value=true;clearReveal();try{detail.value=plazaKey.value?await api.get(`/services/plaza/${encodeURIComponent(plazaKey.value)}/detail`):await api.get(`/services/${id}/detail`);if(plazaKey.value)loadCredentialHistory()}catch(error){detail.value=null;toast(`详情加载失败：${error.message}`,'error')}finally{loading.value=false}}
async function startEdit(){try{if(!detail.value)await loadDetail();if(!hosts.value.length)hosts.value=await api.get('/servers');const d=detail.value||props.service||{},p=d.probe_policy||{};Object.assign(form,{server_id:d.server?.id||d.server_id||'',name:d.name||'',category:d.category||'',icon:d.icon||'box',entry_url:d.entry_url||d.url||'',health_url:d.health_url||'',owner:d.owner||'',documentation_url:d.documentation_url||'',tagsText:(d.tags||[]).join(', '),description:d.description||'',probe_enabled:p.enabled!==false,probe_interval_seconds:p.interval_seconds||60,probe_timeout_seconds:p.timeout_seconds||4,probe_success_statuses:p.success_statuses||'200-399,401,403',probe_verify_tls:p.verify_tls!==false,probe_failure_threshold:p.failure_threshold||3,probe_recovery_threshold:p.recovery_threshold||1,probe_notifications_enabled:p.notifications_enabled!==false});editing.value=true}catch(error){toast(`编辑信息加载失败：${error.message}`,'error')}}
async function saveEdit(){if(!form.name||!form.category||!/^https?:\/\//i.test(form.entry_url)){toast('请填写名称、分类和正确的 HTTP(S) 地址','error');return}saving.value=true;try{const payload={server_id:form.server_id||null,name:form.name,category:form.category,icon:form.icon,entry_url:form.entry_url,health_url:form.health_url,owner:form.owner,documentation_url:form.documentation_url,tags:form.tagsText.split(/[,，]/).map(x=>x.trim()).filter(Boolean),description:form.description,probe_enabled:form.probe_enabled,probe_interval_seconds:form.probe_interval_seconds,probe_timeout_seconds:form.probe_timeout_seconds,probe_success_statuses:form.probe_success_statuses,probe_verify_tls:form.probe_verify_tls,probe_failure_threshold:form.probe_failure_threshold,probe_recovery_threshold:form.probe_recovery_threshold,probe_notifications_enabled:form.probe_notifications_enabled};await api.put(`/services/plaza/${encodeURIComponent(plazaKey.value)}`,payload);editing.value=false;await loadDetail();emit('updated',detail.value);toast('服务信息已保存','success')}catch(error){toast(`保存失败：${error.message}`,'error')}finally{saving.value=false}}
function openCredentialEditor(credential){Object.assign(credentialForm,{id:credential?.id||'',label:credential?.label||'',username:credential?.username||'',password:'',notes:credential?.notes||'',is_default:credential?.is_default||!credentials.value.length});credentialEditing.value=true}
function cancelCredentialEditor(){credentialEditing.value=false;Object.assign(credentialForm,{id:'',label:'',username:'',password:'',notes:'',is_default:false})}
async function saveCredential(){if(!credentialForm.label){toast('请填写凭证名称','error');return}credentialSaving.value=true;try{const payload={label:credentialForm.label,username:credentialForm.username,notes:credentialForm.notes,is_default:credentialForm.is_default};if(credentialForm.password)payload.password=credentialForm.password;const base=`/services/plaza/${encodeURIComponent(plazaKey.value)}/credentials`;if(credentialForm.id)await api.put(`${base}/${credentialForm.id}`,payload);else await api.post(base,payload);cancelCredentialEditor();await loadDetail();emit('updated',detail.value);toast('登录凭证已保存','success')}catch(error){toast(`凭证保存失败：${error.message}`,'error')}finally{credentialSaving.value=false}}
async function deleteCredential(credential){if(!confirm(`确认删除凭证「${credential.label}」？`))return;try{clearReveal(credential.id);await api.del(`/services/plaza/${encodeURIComponent(plazaKey.value)}/credentials/${credential.id}`);await loadDetail();emit('updated',detail.value);toast('凭证已删除','success')}catch(error){toast(`删除失败：${error.message}`,'error')}}
async function revealCredential(credential){if(revealedPasswords[credential.id]){clearReveal(credential.id);return}try{const data=await api.post(`/services/plaza/${encodeURIComponent(plazaKey.value)}/credentials/${credential.id}/reveal`);revealedPasswords[credential.id]=data.password||'';detail.value.credential_access_history=[{id:`local-${Date.now()}`,credential_id:credential.id,action:'reveal',actor:'admin',ip:'当前访问',created_at:new Date().toISOString()},...(detail.value.credential_access_history||[])];revealTimers.set(credential.id,setTimeout(()=>clearReveal(credential.id),60000))}catch(error){toast(`密码显示失败：${error.message}`,'error')}}
async function probeNow(){probing.value=true;try{await api.post(`/services/plaza/${encodeURIComponent(plazaKey.value)}/probe`);await loadDetail();emit('updated',detail.value);toast('健康检查已完成','success')}catch(error){toast(`检测失败：${error.message}`,'error')}finally{probing.value=false}}
async function ackIncident(){try{await api.post(`/services/plaza/incidents/${detail.value.active_incident.id}/acknowledge`);await loadDetail();toast('事件已确认','success')}catch(error){toast(error.message,'error')}}
async function createSilence(){const reason=window.prompt('请输入静默原因','计划维护');if(!reason)return;try{await api.post('/services/plaza/silences',{plaza_key:plazaKey.value,ends_at:new Date(Date.now()+2*60*60*1000).toISOString(),reason});await loadDetail();toast('已静默 2 小时','success')}catch(error){toast(error.message,'error')}}
async function endSilence(){if(!confirm('确认提前结束该静默？'))return;try{await api.del(`/services/plaza/silences/${detail.value.active_silence.id}`);await loadDetail();toast('静默已结束','success')}catch(error){toast(error.message,'error')}}
async function copyText(value,label){try{await navigator.clipboard.writeText(value);toast(`${label}已复制`,'success')}catch{toast(`无法复制${label}，请手动选择`,'error')}}
watch(()=>[props.visible,props.service?.id,props.service?.key],([visible])=>{if(!visible){detail.value=null;editing.value=false;clearReveal();return}loadDetail()},{immediate:true})
onBeforeUnmount(clearReveal)
</script>

<style scoped>
.drawer-mask{position:fixed;inset:0;background:rgba(15,23,42,.4);z-index:1900;display:flex;justify-content:flex-end}.drawer{width:600px;max-width:96vw;height:100%;background:#fff;box-shadow:-10px 0 30px rgba(0,0,0,.15);display:flex;flex-direction:column}.drawer-head{display:flex;align-items:flex-start;justify-content:space-between;padding:18px 20px;border-bottom:1px solid var(--border)}.drawer-head h3{margin:0 0 4px;font-size:18px}.head-meta{font-size:12px}.head-actions{display:flex;gap:8px}.drawer-body{padding:18px 20px;overflow:auto}.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px 18px}.d-item.full{grid-column:1/-1}.d-label{font-size:11px;color:var(--muted);margin-bottom:4px}.d-item span,.d-item a{font-size:13px;word-break:break-all}.d-link{color:var(--brand);text-decoration:none}.d-link:hover{text-decoration:underline}.health-extra{margin-left:10px;color:var(--muted)}.d-section{margin-top:22px}.d-section-title,.section-title{font-size:13px;font-weight:650;margin-bottom:9px}.detail-text,.login-notes{white-space:pre-wrap;margin:0;color:var(--muted);font-size:13px;line-height:1.65}.docs-link{display:inline-block;margin-top:8px;font-size:13px}.tag-list{display:flex;gap:6px;flex-wrap:wrap;margin-top:15px}.credential-card{padding:14px;border:1px solid var(--border);border-radius:10px;background:#f8fafc}.credential-head,.credential-head-actions,.credential-item-head{display:flex;gap:8px;justify-content:space-between;align-items:center}.credential-head-actions{flex-wrap:wrap}.security-tip{font-size:11px;color:var(--ok)}.credential-list{display:flex;flex-direction:column;gap:10px}.credential-item{padding:10px;border:1px solid var(--border);border-radius:8px;background:#fff}.credential-item-head{justify-content:flex-start}.credential-spacer{flex:1}.credential-row{display:flex;align-items:center;gap:8px;min-height:34px}.credential-row>.d-label{width:42px;margin:0}.credential-value{flex:1;font-size:13px}.credential-editor{margin:8px 0 12px;padding:10px;border:1px dashed var(--brand);border-radius:8px;background:#fff}.credential-editor-actions{display:flex;justify-content:flex-end;gap:8px}.login-notes{padding-top:9px;border-top:1px dashed var(--border);margin:6px 0 10px}.dep-list{display:flex;flex-direction:column;gap:7px}.dep-item{display:flex;align-items:center;gap:8px;font-size:13px}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.form-grid .full{grid-column:1/-1}.edit-section{border-top:1px solid var(--border);padding-top:16px;margin-top:6px}.check-row{display:flex;align-items:center;gap:8px;font-size:13px}.form-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:18px;padding-top:14px;border-top:1px solid var(--border)}.slide-enter-active,.slide-leave-active{transition:transform .2s}.slide-enter-from,.slide-leave-to{transform:translateX(100%)}@media(max-width:640px){.detail-grid,.form-grid{grid-template-columns:1fr}.d-item.full,.form-grid .full{grid-column:auto}.drawer{max-width:100vw}.health-extra{display:block;margin:4px 0 0}.credential-head{align-items:flex-start;flex-direction:column}}
.health-card{padding:14px;border:1px solid #dbeafe;border-radius:10px;background:#f8fbff}.health-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}.health-summary>div{display:flex;flex-direction:column;padding:10px;border-radius:8px;background:#fff}.summary-value{font-size:18px;font-weight:700;color:#0f172a}.summary-value small{font-size:11px;margin-left:2px}.summary-label{font-size:11px;color:var(--muted);margin-top:3px}.policy-line,.empty-inline{font-size:11px;color:var(--muted);line-height:1.6}.probe-list{margin-top:10px;border-top:1px solid #dbeafe}.probe-row{display:grid;grid-template-columns:10px 1.7fr .8fr .7fr 1fr;gap:7px;align-items:center;padding:7px 0;border-bottom:1px solid #eaf2ff;font-size:11px;color:var(--muted)}.status-dot{width:7px;height:7px;border-radius:50%}.dot-up{background:var(--ok)}.dot-down{background:var(--danger)}.probe-error{color:var(--danger);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.access-history{border-top:1px dashed var(--border);margin-top:10px;padding-top:9px}.access-row{display:grid;grid-template-columns:.7fr 1fr 1.5fr;gap:8px;font-size:11px;color:var(--muted);padding:3px 0}@media(max-width:640px){.health-summary{grid-template-columns:1fr}.probe-row{grid-template-columns:10px 1fr .7fr}.probe-row>*:nth-child(n+4){display:none}}
.incident-box,.silence-box{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0;padding:9px;border-radius:8px;background:#fff7ed;font-size:12px}.silence-box{background:#f1f5f9}
</style>
