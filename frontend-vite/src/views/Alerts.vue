<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">告警中心</h1>
        <p class="view-sub">告警事件 · 规则 · 静默维护</p>
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
            <td><span class="tag" :class="e.status === 'resolved' ? 'tag-green' : e.status === 'acked' ? 'tag-amber' : 'tag-red'">{{ e.status }}</span></td>
            <td class="muted">{{ fmtTime(e.fired_at) }}</td>
            <td class="muted">{{ fmtTime(e.recovered_at) }}</td>
            <td>
              <button v-if="e.status === 'pending' || e.status === 'firing'" class="btn btn-sm" @click="ack(e)">确认</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 规则 -->
    <div v-if="tab === 'rules'">
      <div v-if="rulesLoading" class="loading"><span class="spinner"></span>加载中…</div>
      <EmptyState v-else-if="!rules.length" icon="⚙️" text="暂无告警规则" />
      <table v-else class="table card">
        <thead>
          <tr><th>规则名</th><th>指标</th><th>条件</th><th>阈值</th><th>主机</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in rules" :key="r.id">
            <td>{{ r.name }}</td>
            <td class="mono">{{ r.metric }}</td>
            <td class="mono">{{ r.operator }}</td>
            <td class="mono">{{ r.threshold }}</td>
            <td>{{ r.server_name || '全部' }}</td>
            <td><span class="tag" :class="r.enabled ? 'tag-green' : 'tag-slate'">{{ r.enabled ? '启用' : '停用' }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 静默 -->
    <div v-if="tab === 'silences'">
      <div class="card silence-form">
        <div style="font-weight:600;margin-bottom:12px">新建静默窗口</div>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px">
          <div class="field" style="margin:0">
            <label>开始时间</label>
            <input v-model="silenceForm.starts_at" class="input" type="datetime-local" />
          </div>
          <div class="field" style="margin:0">
            <label>结束时间</label>
            <input v-model="silenceForm.ends_at" class="input" type="datetime-local" />
          </div>
          <div class="field" style="margin:0">
            <label>主机（可选）</label>
            <select v-model="silenceForm.server_id" class="select">
              <option value="">全部主机</option>
              <option v-for="h in hosts" :key="h.id" :value="h.id">{{ h.name }}</option>
            </select>
          </div>
          <div class="field" style="margin:0">
            <label>规则（可选）</label>
            <select v-model="silenceForm.rule_id" class="select">
              <option value="">全部规则</option>
              <option v-for="r in rules" :key="r.id" :value="r.id">{{ r.name }}</option>
            </select>
          </div>
          <div class="field" style="margin:0">
            <label>原因</label>
            <input v-model="silenceForm.reason" class="input" placeholder="维护窗口/发布窗口…" />
          </div>
          <div style="display:flex;align-items:flex-end">
            <button class="btn btn-primary" :disabled="silenceSaving" @click="createSilence">{{ silenceSaving ? '创建中…' : '创建' }}</button>
          </div>
        </div>
      </div>

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
            <td><button class="btn btn-sm btn-danger" @click="deleteSilence(s)">删除</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtTime, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'

const tabs = [
  { id: 'events', label: '告警事件' },
  { id: 'rules', label: '告警规则' },
  { id: 'silences', label: '静默维护' },
]
const statusFilters = [
  { id: '', label: '全部' },
  { id: 'pending', label: '待确认' },
  { id: 'firing', label: '触发中' },
  { id: 'acked', label: '已确认' },
  { id: 'resolved', label: '已恢复' },
]

const tab = ref('events')
const eventStatus = ref('')
const events = ref([])
const rules = ref([])
const silences = ref([])
const hosts = ref([])
const eventsLoading = ref(false)
const rulesLoading = ref(false)
const silencesLoading = ref(false)
const silenceSaving = ref(false)
const silenceForm = ref({ starts_at: '', ends_at: '', server_id: '', rule_id: '', reason: '' })

async function loadEvents() {
  eventsLoading.value = true
  try {
    events.value = await api.get('/alert-events', { status: eventStatus.value || undefined, days: 30 })
  } finally {
    eventsLoading.value = false
  }
}

async function loadRules() {
  rulesLoading.value = true
  try {
    rules.value = await api.get('/alert-rules')
  } finally {
    rulesLoading.value = false
  }
}

async function loadSilences() {
  silencesLoading.value = true
  try {
    silences.value = await api.get('/alert-silences')
  } finally {
    silencesLoading.value = false
  }
}

async function ack(e) {
  try {
    await api.post(`/alert-events/${e.id}/ack`)
    e.status = 'acked'
    toast('已确认', 'ok')
  } catch (err) {
    toast(err.message, 'err')
  }
}

async function createSilence() {
  if (!silenceForm.value.starts_at || !silenceForm.value.ends_at) {
    toast('请填写开始/结束时间', 'err')
    return
  }
  silenceSaving.value = true
  try {
    await api.post('/alert-silences', {
      starts_at: new Date(silenceForm.value.starts_at).toISOString(),
      ends_at: new Date(silenceForm.value.ends_at).toISOString(),
      server_id: silenceForm.value.server_id || null,
      rule_id: silenceForm.value.rule_id || null,
      reason: silenceForm.value.reason,
    })
    toast('静默已创建', 'ok')
    silenceForm.value = { starts_at: '', ends_at: '', server_id: '', rule_id: '', reason: '' }
    loadSilences()
  } catch (e) {
    toast(e.message || '创建失败', 'err')
  } finally {
    silenceSaving.value = false
  }
}

async function deleteSilence(s) {
  if (!confirm('确认解除该静默？')) return
  try {
    await api.del(`/alert-silences/${s.id}`)
    toast('已解除', 'ok')
    loadSilences()
  } catch (e) {
    toast(e.message, 'err')
  }
}

function reload() {
  loadEvents()
  loadRules()
  loadSilences()
}

onMounted(() => {
  reload()
  api.get('/servers').then((l) => { hosts.value = l }).catch(() => {})
})
</script>

<style scoped>
.alerts-tabs { display: flex; gap: 8px; margin-bottom: 14px; }
.g-tab {
  padding: 6px 14px; border-radius: 999px; border: 1px solid var(--border);
  background: #fff; font-size: 13px; color: var(--muted); cursor: pointer;
}
.g-tab.active { background: var(--brand); border-color: var(--brand); color: #fff; }
.filter-row { display: flex; gap: 8px; margin-bottom: 12px; }
.silence-form { margin-bottom: 8px; }
</style>
