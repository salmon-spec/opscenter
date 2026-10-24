<template>
  <Modal :visible="visible" :title="rule?.id ? '编辑告警规则' : '添加告警规则'" width="640px" @close="$emit('close')">
    <form @submit.prevent="save">
      <div class="fields">
        <label class="full">规则模板<select class="select" v-model="form.metric" @change="applyTemplate"><option v-for="item in templates" :key="item.id" :value="item.metric">{{ item.name }}</option></select></label>
        <label>名称<input class="input" v-model.trim="form.name" required maxlength="120" /></label>
        <label>级别<select class="select" v-model="form.severity"><option v-for="(label, id) in levels" :key="id" :value="id">{{ label }}</option></select></label>
        <label class="full">作用对象<select class="select" v-model="form.resource_id"><option value="">全部{{ sources[template?.source] || '对象' }}</option><option v-for="target in matchingTargets" :key="target.id" :value="target.id">{{ target.name }}</option></select><small v-if="!matchingTargets.length">对象列表将在数据源完成首次评估后更新。</small></label>
        <label>比较条件<select class="select" v-model="form.operator"><option v-for="(label, id) in operators" :key="id" :value="id">{{ label }}</option></select></label>
        <label>触发阈值{{ template?.unit ? `（${template.unit}）` : '' }}<input class="input" v-model="form.threshold" required /></label>
        <label>持续时间（秒）<input class="input" type="number" min="0" max="86400" v-model.number="form.duration_sec" required /></label>
        <label>恢复稳定期（秒）<input class="input" type="number" min="0" max="86400" v-model.number="form.recovery_sec" required /></label>
        <label v-if="form.value_type === 'numeric' && !['==','!='].includes(form.operator)">恢复阈值<input class="input" v-model="form.recovery_threshold" placeholder="留空沿用触发阈值" /></label>
        <label class="full">处置文档<input class="input" v-model.trim="form.runbook_url" type="url" placeholder="https://…" /></label>
        <fieldset class="full"><legend>通知策略</legend><div class="checks"><label><input type="checkbox" v-model="allPolicies" /> 使用全部匹配策略</label><template v-if="!allPolicies"><label v-for="policy in policies" :key="policy.id"><input type="checkbox" :value="policy.id" v-model="form.policy_ids" />{{ policy.name }}</label></template></div><small>未配置策略时，告警仍记录在事件列表。</small></fieldset>
        <label v-if="template?.source === 'service'" class="check full"><input type="checkbox" v-model="form.inhibit_on_host_down" /> 关联主机离线时抑制服务通知</label>
        <label class="check full"><input type="checkbox" v-model="form.enabled" /> 启用规则</label>
      </div>
      <div v-if="error" class="form-error" role="alert">{{ error }}</div>
      <div v-if="preview" class="preview"><strong>当前条件预览</strong><p v-if="!preview.items.length">暂无可用对象，请检查数据源。</p><div v-for="item in preview.items" :key="item.id" class="preview-row"><span>{{ item.name }}</span><span>{{ item.value ?? '无有效数据' }} · {{ item.matches === null ? '未知' : item.matches ? '符合触发条件' : '未触发' }}</span></div><small>预览不会创建事件或发送通知。</small></div>
      <div class="actions"><button class="btn" type="button" :disabled="busy" @click="check">预览条件</button><span /><button class="btn" type="button" @click="$emit('close')">取消</button><button class="btn btn-primary" :disabled="busy">{{ busy ? '处理中…' : '保存规则' }}</button></div>
    </form>
  </Modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../../api'
import Modal from '../Modal.vue'
import { levels, sources } from './format'
const props = defineProps({ visible: Boolean, rule: Object, templates: Array, targets: Array, policies: Array })
const emit = defineEmits(['close', 'saved'])
const form = ref({}), busy = ref(false), error = ref(''), preview = ref(null), allPolicies = ref(true)
const template = computed(() => props.templates?.find(t => t.metric === form.value.metric))
const matchingTargets = computed(() => (props.targets || []).filter(t => t.source === template.value?.source))
const operators = computed(() => form.value.value_type === 'string' ? { '==': '等于', '!=': '不等于' } : { '>': '大于', '>=': '大于等于', '<': '小于', '<=': '小于等于', '==': '等于', '!=': '不等于' })
function applyTemplate() {
  const t = props.templates.find(t => t.metric === form.value.metric)
  if (t) Object.assign(form.value, t, { resource_id: '', server_id: null, recovery_threshold: t.recovery_threshold || '' })
  preview.value = null
}
watch(() => props.visible, visible => {
  if (!visible) return
  form.value = props.rule ? { recovery_sec: 0, severity: 'warning', policy_ids: [], runbook_url: '', ...props.rule, resource_id: props.rule.resource_id || props.rule.server_id || '', recovery_threshold: props.rule.recovery_threshold ?? '' } : { ...props.templates?.[0], resource_id: '', enabled: true, severity: 'warning', policy_ids: [], runbook_url: '' }
  form.value.policy_ids = [...(form.value.policy_ids || [])]
  allPolicies.value = !form.value.policy_ids.length
  error.value = ''; preview.value = null
})
function payload() {
  const { id, source, unit, ...value } = form.value
  return { ...value, server_id: null, resource_id: value.resource_id || null, threshold: String(value.threshold), recovery_threshold: ['==','!='].includes(value.operator) || value.recovery_threshold === '' || value.recovery_threshold == null ? null : String(value.recovery_threshold), policy_ids: allPolicies.value ? [] : value.policy_ids }
}
async function check() {
  busy.value = true; error.value = ''
  try { preview.value = await api.post('/modules/alerts/api/v1/rules/preview', payload()) }
  catch (e) { error.value = typeof e.message === 'string' ? e.message : '规则预览失败' }
  finally { busy.value = false }
}
async function save() {
  if (!allPolicies.value && !form.value.policy_ids.length) { error.value = '请选择通知策略，或使用全部匹配策略'; return }
  busy.value = true; error.value = ''
  try {
    if (props.rule?.id) await api.put(`/modules/alerts/api/v1/rules/${props.rule.id}`, payload())
    else await api.post('/modules/alerts/api/v1/rules', payload())
    emit('saved'); emit('close')
  } catch (e) { error.value = typeof e.message === 'string' ? e.message : '保存失败，请检查表单' }
  finally { busy.value = false }
}
</script>

<style scoped>
.fields{display:grid;grid-template-columns:1fr 1fr;gap:16px}.fields>label{display:grid;gap:7px;font-size:13px}.full{grid-column:1/-1}.fields .check,.checks label{display:flex;align-items:center;gap:7px}small{font-size:12px;color:var(--muted);font-weight:400}fieldset{border:1px solid var(--border);border-radius:6px;padding:12px}legend{font-size:13px;padding:0 5px}.checks{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:7px}.actions{display:flex;gap:8px;border-top:1px solid var(--border);padding-top:16px;margin-top:22px}.actions>span{flex:1}.form-error{color:var(--err);margin-top:14px}.preview{border:1px solid var(--border);border-radius:6px;margin-top:16px;padding:14px;font-size:13px}.preview-row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-bottom:1px solid var(--border)}.preview small{display:block;margin-top:10px}@media(max-width:600px){.fields{grid-template-columns:1fr}.full{grid-column:auto}}
</style>
