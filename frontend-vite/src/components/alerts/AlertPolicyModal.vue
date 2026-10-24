<template>
  <Modal :visible="visible" :title="policy?.id ? '编辑通知策略' : '添加通知策略'" width="600px" @close="$emit('close')">
    <form @submit.prevent="save">
      <div class="fields">
        <label>策略名称<input class="input" v-model.trim="form.name" maxlength="100" required /></label>
        <label>渠道<select class="select" v-model="form.channel"><option v-for="(label,id) in channels" :key="id" :value="id">{{ label }}</option></select></label>
        <label v-if="form.channel === 'webhook'" class="full">Webhook 地址<input class="input" type="url" v-model.trim="form.url" :required="!policy?.configured" :placeholder="policy?.configured ? '留空保留现有地址' : 'https://…'" /></label>
        <label v-if="form.channel === 'sms'" class="full">接收号码<input class="input" type="tel" v-model.trim="form.phone" :placeholder="policy?.configured ? '已配置，留空保留' : '手机号码'" /></label>
        <label v-if="form.channel !== 'inapp'" class="full">{{ form.channel === 'pushplus' ? 'PushPlus 令牌' : form.channel === 'sms' ? '短信模块令牌' : 'Bearer 令牌（可选）' }}<input class="input" type="password" autocomplete="new-password" v-model="form.token" :placeholder="policy?.configured ? '已配置，留空保留' : ''" /><small>保存后加密存储，页面不回显。</small></label>
        <fieldset class="full"><legend>告警来源</legend><div class="checks"><label v-for="(label,id) in sources" :key="id"><input v-model="form.sources" type="checkbox" :value="id" />{{ label }}</label></div><small>不勾选则匹配全部来源。</small></fieldset>
        <fieldset class="full"><legend>告警级别</legend><div class="checks"><label v-for="(label,id) in levels" :key="id"><input v-model="form.severities" type="checkbox" :value="id" />{{ label }}</label></div><small>不勾选则匹配全部级别。</small></fieldset>
        <label>重复提醒间隔（分钟）<input class="input" type="number" min="0" max="10080" v-model.number="repeatMinutes" required /><small>0 表示只提醒一次。</small></label>
        <label>每次故障提醒上限<input class="input" type="number" min="0" max="100" v-model.number="form.max_notifications" required /><small>0 表示不限，恢复通知单独计算。</small></label>
        <label>通知合并<select class="select" v-model="form.group_by"><option value="none">逐条发送</option><option value="source">按来源合并</option><option value="host">按关联主机合并</option></select></label>
        <label>合并等待（秒）<input class="input" type="number" min="0" max="300" v-model.number="form.group_wait_sec" required /></label>
        <label class="full">未确认后延迟通知（分钟）<input class="input" type="number" min="0" max="1440" v-model.number="escalationMinutes" required /><small>0 立即通知。可另建短信策略，用于故障长时间未确认后的升级提醒。</small></label>
        <label>免打扰开始<input class="input" type="time" v-model="quietStart" /></label><label>免打扰结束<input class="input" type="time" v-model="quietEnd" /></label>
        <label class="check full"><input type="checkbox" v-model="form.notify_recovery" /> 故障恢复时通知</label>
        <label class="check full"><input type="checkbox" v-model="form.dry_run" /> 仅预览，记录通知内容</label>
        <label class="check full"><input type="checkbox" v-model="form.enabled" /> 启用策略</label>
      </div>
      <p class="hint">时间采用北京时间。通知成功表示平台接受请求。</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="actions"><button class="btn" type="button" @click="$emit('close')">取消</button><button class="btn btn-primary" :disabled="busy">{{ busy ? '保存中…' : '保存策略' }}</button></div>
    </form>
  </Modal>
</template>
<script setup>
import { ref, watch } from 'vue'
import { api } from '../../api'
import Modal from '../Modal.vue'
import { sources, levels, channels } from './format'
const props = defineProps({ visible: Boolean, policy: Object })
const emit = defineEmits(['close','saved'])
const form = ref({}), repeatMinutes = ref(60), escalationMinutes = ref(0), quietStart = ref(''), quietEnd = ref(''), busy = ref(false), error = ref('')
const timeString = v => v == null ? '' : `${String(Math.floor(v / 60)).padStart(2,'0')}:${String(v % 60).padStart(2,'0')}`
const minutes = v => v ? v.split(':').reduce((h,m) => +h * 60 + +m) : null
watch(() => props.visible, value => {
  if (!value) return
  form.value = { name: '', channel: 'inapp', sources: [], severities: [], enabled: true, dry_run: true, max_notifications: 3, group_by: 'none', group_wait_sec: 0, notify_recovery: true, ...props.policy, url: '', token: '', phone: '' }
  form.value.sources = [...form.value.sources]; form.value.severities = [...form.value.severities]
  repeatMinutes.value = (props.policy?.repeat_sec ?? 3600) / 60
  escalationMinutes.value = (props.policy?.escalate_after_sec ?? 0) / 60
  quietStart.value = timeString(props.policy?.quiet_start); quietEnd.value = timeString(props.policy?.quiet_end); error.value = ''
})
async function save() {
  if (!!quietStart.value !== !!quietEnd.value || (quietStart.value && quietStart.value === quietEnd.value)) { error.value = '请设置不同的免打扰开始与结束时间'; return }
  busy.value = true; error.value = ''
  const payload = { ...form.value, repeat_sec: Math.round(repeatMinutes.value * 60), escalate_after_sec: Math.round(escalationMinutes.value * 60), quiet_start: minutes(quietStart.value), quiet_end: minutes(quietEnd.value) }
  try {
    if (props.policy?.id) await api.put(`/modules/alerts/api/v1/policies/${props.policy.id}`, payload)
    else await api.post('/modules/alerts/api/v1/policies', payload)
    emit('saved'); emit('close')
  } catch(e) { error.value = typeof e.message === 'string' ? e.message : '保存失败，请检查表单' }
  finally { busy.value = false }
}
</script>
<style scoped>
.fields{display:grid;grid-template-columns:1fr 1fr;gap:16px}.fields>label{display:grid;gap:7px;font-size:13px}.full{grid-column:1/-1}.fields .check,.checks label{display:flex;align-items:center;gap:7px}small,.hint{color:var(--muted);font-size:12px}.checks{display:flex;gap:12px;flex-wrap:wrap}fieldset{border:1px solid var(--border);border-radius:6px;padding:12px}legend{font-size:13px;padding:0 5px}fieldset small{display:block;margin-top:8px}.actions{display:flex;justify-content:flex-end;gap:8px;margin-top:20px;border-top:1px solid var(--border);padding-top:16px}.form-error{color:var(--err)}@media(max-width:600px){.fields{grid-template-columns:1fr}.full{grid-column:auto}}
</style>
