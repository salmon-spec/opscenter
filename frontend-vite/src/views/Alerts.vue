<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">告警中心</h1>
      <p class="view-sub">告警事件 · 规则 · 静默维护 · 证书检查</p>
      </div>
      <button class="btn" @click="reload">刷新</button>
    </div>

    <div class="alerts-tabs">
      <button v-for="t in tabs" :key="t.id" class="g-tab" :class="{ active: tab === t.id }" @click="tab = t.id">{{ t.label }}</button>
    </div>

    <!-- 事件 -->
    <div v-if="tab === 'events'">
      <div class="filter-row">
        <button
          v-for="f in statusFilters" :key="f.id"
          class="btn btn-sm" :class="eventStatus === f.id ? 'btn-primary' : ''"
          @click="eventStatus = f.id; loadEvents()"
        >{{ f.label }}</button>
      </div>
      <div v-if="eventsLoading" class="loading"><span class="spinner"></span>加载中…</div>
      <EmptyState v-else-if="!events.length" icon="🔔" text="暂无告警事件" />
      <table v-else class="table card">
        <thead>
          <tr><th>规则</th><th>主机</th><th>状态</th><th>触发时间</th><th>恢复时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="e in events" :key="e.id">
            <td>{{ e.rule_name || '-' }}</td>
            <td>{{ e.server_name || '-' }}</td>
            <td><span class="tag" :class="e.status === 'recovered' ? 'tag-green' : e.status === 'acked' ? 'tag-amber' : 'tag-red'">{{ e.status }}</span></td>
            <td class="muted">{{ fmtTime(e.fired_at) }}</td>
            <td class="muted">{{ fmtTime(e.recovered_at) }}</td>
            <td>
              <button v-if="['pending','firing'].includes(e.status)" class="btn btn-sm" @click="ackEvent(e)">确认</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 规则 -->
    <div v-if="tab === 'rules'">
      <button class="btn btn-primary" @click="openRule()">添加规则</button>
      <div v-if="rulesLoading" class="loading"><span class="spinner"></span>加载中…</div>
      <EmptyState v-else-if="!rules.length" icon="⚙️" text="暂无告警规则" />
      <table v-else class="table card">
        <thead>
          <tr><th>规则名</th><th>指标</th><th>条件</th><th>阈值</th><th>主机</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rules" :key="r.id">
            <td>{{ r.name }}</td>
            <td class="mono">{{ r.metric }}</td>
            <td class="mono">{{ r.operator }}</td>
            <td class="mono">{{ r.threshold }}</td>
            <td>{{ r.server_name || '全部' }}</td>
            <td><span class="tag" :class="r.enabled ? 'tag-green' : 'tag-slate'">{{ r.enabled ? '启用' : '停用' }}</span></td>
            <td><button class="btn btn-sm" @click="openRule(r)">编辑</button><button class="btn btn-sm" @click="toggleRule(r)">{{ r.enabled ? '停用' : '启用' }}</button><button class="btn btn-sm btn-danger" @click="deleteRule(r)">删除</button></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 静默 -->
    <div v-if="tab === 'silences'">
      <button class="btn btn-primary" @click="silenceVisible = true">添加静默</button>
      <div v-if="silencesLoading" class="loading"><span class="spinner"></span>加载中…</div>
      <EmptyState v-else-if="!silences.length" icon="🌙" text="暂无静默记录" />
      <table v-else class="table card" style="margin-top:14px">
        <thead>
          <tr><th>规则</th><th>主机</th><th>开始</th><th>结束</th><th>原因</th><th>状态</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="s in silences" :key="s.id">
            <td>{{ s.rule_name || '全部' }}</td>
            <td>{{ s.server_name || '全部' }}</td>
            <td class="muted">{{ fmtTime(s.starts_at) }}</td>
            <td class="muted">{{ fmtTime(s.ends_at) }}</td>
            <td>{{ s.reason || '-' }}</td>
            <td><span class="tag" :class="s.active ? 'tag-green' : 'tag-slate'">{{ s.active ? '生效中' : '已过期' }}</span></td>
            <td><button v-if="s.active" class="btn btn-sm" @click="endSilence(s)">提前结束</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="tab === 'certificates'">
      <div class="filter-row">
        <button class="btn btn-primary" @click="certificateVisible = true">添加证书目标</button>
        <button class="btn" :disabled="busy" @click="scanCertificates">立即扫描</button>
      </div>
      <div v-if="certificatesLoading" class="loading"><span class="spinner"></span>加载中…</div>
      <EmptyState v-else-if="!certificates.length" icon="🔒" text="暂无证书检查目标" />
      <table v-else class="table card">
        <thead><tr><th>域名</th><th>端口</th><th>主机</th><th>到期时间</th><th>剩余天数</th><th>最近检查</th><th>操作</th></tr></thead>
        <tbody><tr v-for="c in certificates" :key="c.id">
          <td class="mono">{{ c.domain }}</td><td>{{ c.port }}</td><td>{{ c.server_name || c.server_id || '-' }}</td>
          <td class="muted">{{ fmtTime(c.not_after) }}</td>
          <td><span v-if="c.days_left !== null" class="tag" :class="c.days_left <= 30 ? 'tag-red' : 'tag-green'">{{ c.days_left }} 天</span><span v-else class="muted">{{ c.last_error || '待检查' }}</span></td>
          <td class="muted">{{ fmtTime(c.updated_at) }}</td>
          <td><button class="btn btn-sm btn-danger" @click="deleteCertificate(c)">删除</button></td>
        </tr></tbody>
      </table>
    </div>
    <Modal :visible="ruleVisible" :title="ruleForm.id ? '编辑告警规则' : '添加告警规则'" width="520px" @close="ruleVisible = false">
      <div class="alert-form">
        <label>名称<input v-model.trim="ruleForm.name" class="input" /></label>
        <label>指标<input v-model.trim="ruleForm.metric" class="input" placeholder="cpu / memory / disk" /></label>
        <label>值类型<select v-model="ruleForm.value_type" class="select"><option value="numeric">数值</option><option value="string">文本状态</option></select></label>
        <label>条件<select v-model="ruleForm.operator" class="select"><option value=">">大于</option><option value="<">小于</option><option value=">=">大于等于</option><option value="<=">小于等于</option></select></label>
        <label>阈值<input v-model.trim="ruleForm.threshold" class="input" /></label>
        <label>持续秒数<input v-model.number="ruleForm.duration_sec" class="input" type="number" min="0" /></label>
      </div>
      <div class="modal-actions"><button class="btn" @click="ruleVisible = false">取消</button><button class="btn btn-primary" :disabled="busy || !ruleForm.name || !ruleForm.metric || !ruleForm.threshold" @click="saveRule">保存</button></div>
    </Modal>
    <Modal :visible="silenceVisible" title="添加静默" width="480px" @close="silenceVisible = false">
      <div class="alert-form"><label>规则<select v-model="silenceRuleId" class="select"><option value="">全部规则</option><option v-for="r in rules" :key="r.id" :value="r.id">{{ r.name }}</option></select></label><label>持续小时<input v-model.number="silenceHours" class="input" type="number" min="1" max="168" /></label><label>原因<input v-model.trim="silenceReason" class="input" /></label></div>
      <div class="modal-actions"><button class="btn" @click="silenceVisible = false">取消</button><button class="btn btn-primary" :disabled="busy || !silenceReason" @click="createSilence">创建</button></div>
    </Modal>
    <Modal :visible="certificateVisible" title="添加证书检查" width="480px" @close="certificateVisible = false">
      <div class="alert-form"><label>公网域名<input v-model.trim="certificateForm.domain" class="input" placeholder="example.com" /></label><label>端口<input v-model.number="certificateForm.port" class="input" type="number" min="1" max="65535" /></label><label>关联主机 ID（可选）<input v-model.trim="certificateForm.server_id" class="input" /></label></div>
      <div class="modal-actions"><button class="btn" @click="certificateVisible = false">取消</button><button class="btn btn-primary" :disabled="busy || !certificateForm.domain" @click="saveCertificate">添加</button></div>
    </Modal>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtTime, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'

const tabs = [
  { id: 'events', label: '告警事件' },
  { id: 'rules', label: '告警规则' },
      { id: 'silences', label: '静默维护' },
  { id: 'certificates', label: '证书检查' },
]
const statusFilters = [
  { id: '', label: '全部' },
  { id: 'pending', label: '待确认' },
  { id: 'firing', label: '触发中' },
  { id: 'acked', label: '已确认' },
  { id: 'recovered', label: '已恢复' },
]

const tab = ref('events')
const eventStatus = ref('')
const events = ref([])
const rules = ref([])
const silences = ref([])
const certificates = ref([])
const eventsLoading = ref(false)
const rulesLoading = ref(false)
const silencesLoading = ref(false)
const certificatesLoading = ref(false)
const busy = ref(false)
const ruleVisible = ref(false)
const ruleForm = ref({})
const silenceVisible = ref(false)
const silenceRuleId = ref('')
const silenceHours = ref(2)
const silenceReason = ref('')
const certificateVisible = ref(false)
const certificateForm = ref({ domain: '', port: 443, server_id: '' })

function openRule(rule = null) {
  ruleForm.value = rule ? { ...rule } : { name: '', metric: '', value_type: 'numeric', operator: '>', threshold: '', duration_sec: 60, cooldown_sec: 300, enabled: true }
  ruleVisible.value = true
}

async function write(action, refresh) {
  busy.value = true
  try { await action(); await refresh(); toast('操作完成', 'ok'); return true }
  catch (error) { toast(error.message, 'err'); return false }
  finally { busy.value = false }
}

async function ackEvent(event) { await write(() => api.post(`/modules/alerts/api/v1/events/${event.id}/ack`), loadEvents) }
async function saveRule() {
  const { id, ...payload } = ruleForm.value
  if (await write(() => id ? api.put(`/modules/alerts/api/v1/rules/${id}`, payload) : api.post('/modules/alerts/api/v1/rules', payload), loadRules)) ruleVisible.value = false
}
async function toggleRule(rule) { await write(() => api.put(`/modules/alerts/api/v1/rules/${rule.id}`, { ...rule, enabled: !rule.enabled }), loadRules) }
async function deleteRule(rule) {
  if (!confirm(`删除规则「${rule.name}」及其事件历史？`)) return
  await write(() => api.del(`/modules/alerts/api/v1/rules/${rule.id}`), loadRules)
}
async function createSilence() {
  if (!Number.isFinite(silenceHours.value) || silenceHours.value < 1 || silenceHours.value > 168) return
  const now = new Date()
  if (await write(() => api.post('/modules/alerts/api/v1/silences', { rule_id: silenceRuleId.value || null, server_id: null, starts_at: now.toISOString(), ends_at: new Date(now.getTime() + silenceHours.value * 3600000).toISOString(), reason: silenceReason.value }), loadSilences)) silenceVisible.value = false
}
async function endSilence(silence) { await write(() => api.del(`/modules/alerts/api/v1/silences/${silence.id}`), loadSilences) }
async function saveCertificate() {
  const payload = { ...certificateForm.value, server_id: certificateForm.value.server_id || null }
  if (await write(() => api.post('/modules/alerts/api/v1/certificates', payload), loadCertificates)) {
    certificateVisible.value = false
    certificateForm.value = { domain: '', port: 443, server_id: '' }
  }
}
async function deleteCertificate(certificate) {
  if (confirm(`删除证书检查「${certificate.domain}」？`)) await write(() => api.del(`/modules/alerts/api/v1/certificates/${certificate.id}`), loadCertificates)
}
async function scanCertificates() { await write(() => api.post('/modules/alerts/api/v1/certificates/scan'), loadCertificates) }

async function loadEvents() {
  eventsLoading.value = true
  try {
    events.value = await api.get('/modules/alerts/api/v1/events', { status: eventStatus.value || undefined, days: 30 })
  } finally {
    eventsLoading.value = false
  }
}

async function loadRules() {
  rulesLoading.value = true
  try {
    rules.value = await api.get('/modules/alerts/api/v1/rules')
  } finally {
    rulesLoading.value = false
  }
}

async function loadSilences() {
  silencesLoading.value = true
  try {
    silences.value = await api.get('/modules/alerts/api/v1/silences')
  } finally {
    silencesLoading.value = false
  }
}

async function loadCertificates() {
  certificatesLoading.value = true
  try { certificates.value = await api.get('/modules/alerts/api/v1/certificates') }
  finally { certificatesLoading.value = false }
}

function reload() {
  loadEvents()
  loadRules()
  loadSilences()
  loadCertificates()
}

onMounted(reload)
</script>

<style scoped>
.alerts-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.g-tab {
  padding: 6px 14px; border-radius: 999px; border: 1px solid var(--border);
  background: #fff; font-size: 13px; color: var(--muted); cursor: pointer;
}
.g-tab.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.filter-row { display: flex; gap: 8px; margin-bottom: 12px; }
.alert-form { display: grid; gap: 10px; margin-top: 12px; }
.alert-form label { display: grid; gap: 5px; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
</style>
