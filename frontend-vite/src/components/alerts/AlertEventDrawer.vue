<template>
  <Teleport to="body">
    <div v-if="visible" class="event-mask" @click.self="$emit('close')">
      <section ref="panel" class="event-drawer" role="dialog" aria-modal="true" aria-labelledby="alert-detail-title" tabindex="-1" @keydown="handleKey">
        <header><div><span class="eyebrow">告警详情</span><h2 id="alert-detail-title">{{ event?.rule_name || '加载中…' }}</h2></div><button class="btn" aria-label="关闭告警详情" @click="$emit('close')">关闭</button></header>
        <div class="drawer-body">
          <p v-if="loading" class="muted">正在读取事件…</p>
          <p v-else-if="error" class="error" role="alert">{{ error }}</p>
          <template v-else-if="event">
            <div class="status-line"><span class="status" :class="event.severity">{{ levels[event.severity] || '警告' }}</span><span>{{ states[event.condition_status] || event.status }}</span><span class="muted">{{ handling[event.handling_status] }}</span></div>
            <dl class="facts">
              <div><dt>告警对象</dt><dd>{{ event.resource_name || event.server_name || '—' }}</dd></div><div><dt>来源</dt><dd>{{ sources[event.source] || '主机' }}</dd></div>
              <div><dt>当前值</dt><dd>{{ formatValue(event) }}</dd></div><div><dt>触发条件</dt><dd>{{ event.operator || '—' }} {{ event.threshold ?? '' }}</dd></div>
              <div><dt>触发时间</dt><dd>{{ event.fired_at ? fmtTime(event.fired_at) : '尚在观察期' }}</dd></div><div><dt>持续时间</dt><dd>{{ eventDuration(event) }}</dd></div>
              <div><dt>最近观测</dt><dd>{{ fmtTime(event.last_seen_at || event.observed_at) }}</dd></div><div><dt>恢复时间</dt><dd>{{ fmtTime(event.recovered_at) }}</dd></div>
              <div><dt>确认人</dt><dd>{{ event.acked_by || '—' }}</dd></div><div><dt>观测次数</dt><dd>{{ event.occurrences ?? '—' }}</dd></div>
            </dl>
            <p v-if="event.data_status === 'unknown'" class="notice">数据暂不可用，保留最近一次故障状态。</p>
            <p v-else-if="event.recovery_started_at && event.condition_status === 'firing'" class="notice">当前值已恢复，正在观察稳定期。</p>
            <p v-if="event.suppressed_reason" class="notice">通知已抑制：{{ event.suppressed_reason }}</p>
            <div class="links"><RouterLink v-if="event.source === 'host'" to="/hosts">主机管理</RouterLink><RouterLink v-if="event.source === 'service'" to="/services">服务广场</RouterLink><RouterLink v-if="event.source === 'k3s'" to="/kubernetes/overview">集群概览</RouterLink><RouterLink to="/logs">日志中心</RouterLink><a v-if="/^https?:\/\//.test(event.runbook_url || '')" :href="event.runbook_url" target="_blank" rel="noopener noreferrer">处置文档</a></div>
            <div class="event-actions"><button v-if="canEdit && event.condition_status === 'firing' && event.handling_status === 'unacked'" class="btn btn-primary" :disabled="busy" @click="act('ack')">确认告警</button><button v-if="canEdit && ['pending','firing'].includes(event.condition_status)" class="btn" @click="$emit('silence', event)">静默此对象</button><button v-if="canEdit && ['recovered','cancelled'].includes(event.condition_status) && event.handling_status !== 'closed'" class="btn" :disabled="busy" @click="act('close')">完成处理</button><button class="btn" @click="showPreview">通知预览</button></div>
            <div v-if="preview" class="message-preview"><strong>{{ preview.title }}</strong><pre>{{ preview.text }}</pre></div>
            <section class="section"><h3>状态记录</h3><ol class="timeline"><li v-for="(item,index) in event.timeline" :key="index"><span class="timeline-point" /><div><strong>{{ actionLabels[item.action] || item.action }}</strong><span class="muted">{{ item.actor === 'system' ? '系统' : item.actor }} · {{ fmtTime(item.at) }}</span><p v-if="item.message">{{ item.message }}</p></div></li></ol><p v-if="!event.timeline?.length" class="muted">此历史事件未记录状态时间线。</p></section>
            <section class="section"><h3>处理记录</h3><article v-for="note in event.notes" :key="note.id" class="note"><div class="muted">{{ note.actor }} · {{ fmtTime(note.created_at) }}</div><p>{{ note.text }}</p></article><p v-if="!event.notes?.length" class="muted">暂无处理记录。</p><form v-if="canEdit" @submit.prevent="comment"><label class="sr-only" for="alert-comment">添加处理记录</label><textarea id="alert-comment" v-model.trim="text" class="input comment" rows="3" maxlength="2000" placeholder="记录排查结果或处理措施" required /><button class="btn" :disabled="busy || !text">添加记录</button></form></section>
            <section class="section"><h3>通知记录</h3><div v-for="job in event.deliveries" :key="job.id" class="delivery"><div><strong>{{ job.policy_name }}</strong><small>{{ fmtTime(job.created_at) }} · {{ job.kind === 'recovered' ? '恢复通知' : '故障通知' }}</small><small v-if="job.error" class="error">{{ job.error }}</small></div><span>{{ deliveryStates[job.status] || job.status }}</span></div><p v-if="!event.deliveries?.length" class="muted">暂无通知记录。</p></section>
            <p class="event-id">事件 ID · {{ event.id }}</p>
          </template>
        </div>
      </section>
    </div>
  </Teleport>
</template>
<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api, fmtTime, toast } from '../../api'
import { sources, levels, states, handling, deliveryStates, eventDuration, formatValue } from './format'
const props = defineProps({ visible: Boolean, eventId: String, canEdit: Boolean })
const emit = defineEmits(['close','changed','silence'])
const event = ref(null), loading = ref(false), busy = ref(false), error = ref(''), text = ref(''), preview = ref(null), panel = ref(null)
let generation = 0, previousFocus = null
const actionLabels = { pending: '进入观察期', firing: '触发告警', recovered: '故障恢复', acked: '确认告警', closed: '完成处理', cancelled: '观察结束', rule_archived: '规则归档' }
async function load() {
  const id = ++generation; loading.value = true; error.value = ''
  try { const value = await api.get(`/modules/alerts/api/v1/events/${props.eventId}`); if (id === generation) event.value = value }
  catch(e) { if (id === generation) error.value = e.message }
  finally { if (id === generation) loading.value = false }
}
watch(() => [props.visible, props.eventId], async ([visible]) => {
  if (!visible) { generation++; previousFocus?.focus?.(); return }
  previousFocus = document.activeElement; event.value = null; text.value = ''; preview.value = null
  load(); await nextTick(); panel.value?.focus()
}, { immediate: true })
async function act(action) {
  busy.value = true
  try { await api.post(`/modules/alerts/api/v1/events/${props.eventId}/${action}`); await load(); emit('changed') }
  catch(e) { toast(e.message, 'err') }
  finally { busy.value = false }
}
async function comment() {
  busy.value = true
  try { await api.post(`/modules/alerts/api/v1/events/${props.eventId}/comments`, { text: text.value }); text.value = ''; await load() }
  catch(e) { toast(e.message, 'err') }
  finally { busy.value = false }
}
async function showPreview() {
  try { preview.value = await api.get(`/modules/alerts/api/v1/notification-preview/${props.eventId}`) }
  catch(e) { toast(e.message, 'err') }
}
function handleKey(e) {
  if (e.key === 'Escape') emit('close')
  if (e.key !== 'Tab') return
  const focusable = [...panel.value.querySelectorAll('button:not(:disabled),a,input,textarea')]
  const first = focusable[0], last = focusable.at(-1)
  if (e.shiftKey && (document.activeElement === first || document.activeElement === panel.value)) { e.preventDefault(); last?.focus() }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus() }
}
onBeforeUnmount(() => { generation++ })
</script>
<style scoped>
.event-mask{position:fixed;inset:0;z-index:1800;background:rgba(15,23,42,.32);display:flex;justify-content:flex-end}.event-drawer{background:#fff;width:620px;max-width:100vw;height:100%;display:flex;flex-direction:column;outline:none;box-shadow:-8px 0 32px #0f172a12}.event-drawer header{padding:24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border);gap:16px}.eyebrow{color:var(--muted);font-size:12px}h2{font-size:19px;line-height:1.5;margin:5px 0 0}.drawer-body{padding:24px;overflow:auto;font-size:13px}.status-line{display:flex;align-items:center;gap:14px}.status{border:1px solid var(--border);border-radius:4px;padding:3px 8px}.critical{color:#b91c1c;border-color:#fecaca;background:#fef2f2}.warning{color:#92400e;border-color:#fde68a;background:#fffbeb}.info{color:#475569;background:#f8fafc}.facts{display:grid;grid-template-columns:1fr 1fr;gap:20px 22px;padding:24px 0;margin:0}.facts dt{color:var(--muted);margin-bottom:6px;font-size:12px}.facts dd{margin:0;overflow-wrap:anywhere}.notice{background:#fafafa;border-left:2px solid #cbd5e1;padding:12px;line-height:1.7}.links{display:flex;gap:18px;flex-wrap:wrap}.links a{color:var(--brand);text-decoration:none}.event-actions{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}.section{border-top:1px solid var(--border);padding-top:20px;margin-top:22px}.section h3{font-size:14px;margin:0 0 16px}.timeline{padding:0;list-style:none}.timeline li{position:relative;display:flex;gap:12px;padding-bottom:18px}.timeline-point{width:7px;height:7px;border-radius:50%;background:#94a3b8;flex-shrink:0;margin-top:6px}.timeline strong{display:block;font-size:13px;margin-bottom:4px;font-weight:500}.timeline .muted{font-size:12px}.timeline p{margin:6px 0;white-space:pre-wrap}.note{padding:10px 0;border-bottom:1px solid #f1f5f9}.note p{white-space:pre-wrap;line-height:1.7;margin:7px 0}.comment{width:100%;resize:vertical;margin:12px 0 8px}.delivery{display:flex;align-items:flex-start;justify-content:space-between;padding:12px 0;gap:12px;border-bottom:1px solid #f1f5f9}.delivery strong{font-weight:500}.delivery small{display:block;color:var(--muted);margin-top:5px}.delivery .error,.error{color:var(--err)}.event-id{font-size:11px;color:var(--muted);margin-top:28px;overflow-wrap:anywhere}.message-preview{background:#f8fafc;border:1px solid var(--border);padding:14px;border-radius:5px}.message-preview pre{font-family:inherit;white-space:pre-wrap;line-height:1.8;margin-bottom:0}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}@media(max-width:540px){.facts{grid-template-columns:1fr}.event-drawer header,.drawer-body{padding:18px}}
</style>
