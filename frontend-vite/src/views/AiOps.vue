<template>
  <div class="ai-ops-page">
    <header class="page-head">
      <div>
        <h1>AI 自动运维</h1>
        <p>基于主机、服务、指标和事件的只读诊断助手</p>
      </div>
      <span class="mode-badge">只读 · Dry-run</span>
    </header>

    <section class="panel setup-panel">
      <div class="panel-head">
        <div>
          <b>连接 AI 运维上下文</b>
          <small>使用 OpsCenter read API Key，密钥只保存在当前浏览器</small>
        </div>
        <button class="btn" :disabled="busyStatus" @click="loadStatus">{{ busyStatus ? '检查中…' : '检查能力' }}</button>
      </div>
      <div class="key-row">
        <label class="key-field">AI Read Key<input v-model.trim="apiKey" type="password" autocomplete="off" placeholder="oc_rt_…" @keyup.enter="saveKey" /></label>
        <button class="btn" @click="saveKey">保存密钥</button>
        <span v-if="status" class="provider-state" :class="status.provider?.configured ? 'ok' : 'warn'">
          {{ status.provider?.configured ? `模型已配置 · ${status.provider.model}` : '模型密钥尚未配置' }}
        </span>
      </div>
      <p class="hint">当前版本只生成诊断和建议，不执行重启、Shell、SSH、Docker 或 kubectl 操作。</p>
    </section>

    <section class="panel auto-panel">
      <div class="panel-head">
        <div>
          <b>AI 自动巡检</b>
          <small v-if="autoInspection?.enabled">发现异常服务后自动分析，间隔 {{ autoInspection.interval_seconds }} 秒</small>
          <small v-else>当前测试环境未开启自动巡检</small>
        </div>
        <div class="auto-actions">
          <span class="provider-state" :class="autoInspection?.enabled ? 'ok' : 'warn'">{{ autoInspection?.enabled ? '运行中 · 只读' : '未开启' }}</span>
          <button class="btn" :disabled="busyStatus" @click="loadInspections">刷新结果</button>
        </div>
      </div>
      <div v-if="inspections.length" class="inspection-list">
        <article v-for="item in inspections" :key="item.id">
          <div class="inspection-head">
            <b>{{ item.service_name }}</b>
            <span :class="item.status === 'completed' ? 'ok' : 'warn'">{{ item.status === 'completed' ? '已完成' : '失败' }}</span>
          </div>
          <small>服务状态：{{ item.service_status }} · {{ item.completed_at || item.started_at }}</small>
          <p v-if="item.analysis">{{ item.analysis.conclusion }}</p>
          <p v-else class="empty">{{ item.error?.message || '暂无诊断结果' }}</p>
          <div v-if="item.analysis?.recommended_actions?.length" class="inspection-actions">
            建议：{{ item.analysis.recommended_actions.map(actionLabel).join('、') }}（均为 dry-run）
          </div>
        </article>
      </div>
      <p v-else class="empty">暂未发现需要自动巡检的异常服务。</p>
    </section>

    <section class="panel question-panel">
      <div class="panel-head">
        <div>
          <b>描述需要排查的问题</b>
          <small>例如：检查当前哪些服务异常，并说明可能原因</small>
        </div>
        <span class="scope">上下文窗口：{{ incidentHours }} 小时</span>
      </div>
      <div class="question-row">
        <textarea v-model="question" maxlength="2000" placeholder="例如：检查当前有哪些服务异常？哪些主机指标已经过期？" @keydown.ctrl.enter="analyze" @keydown.meta.enter="analyze"></textarea>
        <button class="btn btn-primary" :disabled="busy || !question.trim()" @click="analyze">{{ busy ? '分析中…' : '生成诊断' }}</button>
      </div>
      <p class="hint">Ctrl/Cmd + Enter 可直接提交。AI 只会引用已持久化且已脱敏的上下文。</p>
    </section>

    <div v-if="error" class="error-box">{{ error }}</div>

    <section v-if="result" class="result-stack">
      <div class="panel summary-panel">
        <div class="panel-head">
          <div>
            <b>诊断结论</b>
            <small>问题：{{ result.question }}</small>
          </div>
          <span class="confidence">置信度 {{ confidenceText }}</span>
        </div>
        <p class="conclusion">{{ result.analysis.conclusion }}</p>
        <div class="stat-row" v-if="contextSummary">
          <span>主机 <b>{{ contextSummary.host_count }}</b></span>
          <span>服务 <b>{{ contextSummary.service_count }}</b></span>
          <span>活动事件 <b>{{ contextSummary.active_incident_count }}</b></span>
          <span>过期主机 <b :class="{ danger: contextSummary.stale_hosts }">{{ contextSummary.stale_hosts }}</b></span>
        </div>
      </div>

      <div class="result-grid">
        <section class="panel">
          <div class="panel-head"><b>证据</b><small>{{ result.analysis.evidence.length }} 项</small></div>
          <div v-if="result.analysis.evidence.length" class="evidence-list">
            <article v-for="(item, index) in result.analysis.evidence" :key="`${item.source}-${index}`">
              <span class="source-tag">{{ item.source }}</span>
              <div><p>{{ item.fact }}</p><small v-if="item.observed_at">观测时间 {{ item.observed_at }}</small></div>
            </article>
          </div>
          <p v-else class="empty">模型没有返回可验证证据。</p>
        </section>

        <section class="panel">
          <div class="panel-head"><b>建议动作</b><small>全部为 dry-run</small></div>
          <div v-if="result.analysis.recommended_actions.length" class="action-list">
            <article v-for="(item, index) in result.analysis.recommended_actions" :key="`${item.action}-${index}`">
              <div class="action-title"><b>{{ actionLabel(item.action) }}</b><span class="risk" :class="`risk-${item.risk}`">{{ item.risk }}</span></div>
              <p>{{ item.reason }}</p>
              <small>目标：{{ item.target }} · 需要批准 · 不执行</small>
            </article>
          </div>
          <p v-else class="empty">暂无建议动作。</p>
        </section>
      </div>

      <div v-if="result.analysis.limitations.length" class="panel limitations">
        <div class="panel-head"><b>限制与注意事项</b></div>
        <ul><li v-for="item in result.analysis.limitations" :key="item">{{ item }}</li></ul>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'

const KEY_STORAGE = 'ops-ai-api-key'
const apiKey = ref('')
const question = ref('')
const incidentHours = 24
const status = ref(null)
const result = ref(null)
const inspections = ref([])
const error = ref('')
const busy = ref(false)
const busyStatus = ref(false)

const contextSummary = computed(() => result.value?.context?.summary || null)
const confidenceText = computed(() => `${Math.round(Number(result.value?.analysis?.confidence || 0) * 100)}%`)
const autoInspection = computed(() => status.value?.auto_inspection || null)

const actionNames = {
  observe: '继续观察',
  reprobe: '重新探测服务',
  silence: '创建临时静默',
  restart_test_workload: '重启测试工作负载',
}

function actionLabel(action) { return actionNames[action] || '只读观察' }

function saveKey() {
  if (apiKey.value) localStorage.setItem(KEY_STORAGE, apiKey.value)
  else localStorage.removeItem(KEY_STORAGE)
  error.value = ''
  loadStatus()
  loadInspections()
}

async function requestAi(path, options = {}) {
  if (!apiKey.value) throw new Error('请先填写 AI Read Key')
  const response = await fetch(`/api/v2${path}`, {
    method: options.method || 'GET',
    headers: { Authorization: `Bearer ${apiKey.value}`, 'Content-Type': 'application/json' },
    body: options.body ? JSON.stringify(options.body) : undefined,
  })
  let data = null
  try { data = await response.json() } catch { /* safe error below */ }
  if (!response.ok) {
    const detail = data?.detail
    throw new Error(typeof detail === 'string' ? detail : (detail?.message || `请求失败（HTTP ${response.status}）`))
  }
  return data
}

async function loadStatus() {
  busyStatus.value = true
  error.value = ''
  try {
    const response = await requestAi('/ai-ops/status')
    status.value = response.data
  } catch (err) {
    status.value = null
    error.value = err.message
  } finally { busyStatus.value = false }
}

async function loadInspections() {
  if (!apiKey.value) return
  try {
    const response = await requestAi('/ai-ops/inspections?limit=20')
    inspections.value = response.data?.items || []
    if (!status.value && response.data?.auto_inspection) status.value = { auto_inspection: response.data.auto_inspection }
  } catch (err) {
    if (!status.value) error.value = err.message
  }
}

async function analyze() {
  if (!question.value.trim()) return
  busy.value = true
  error.value = ''
  try {
    const response = await requestAi('/ai-ops/analyze', {
      method: 'POST',
      body: { question: question.value.trim(), incident_hours: incidentHours },
    })
    result.value = response.data
  } catch (err) {
    result.value = null
    error.value = err.message
  } finally { busy.value = false }
}

onMounted(() => {
  apiKey.value = localStorage.getItem(KEY_STORAGE) || ''
  if (apiKey.value) {
    loadStatus()
    loadInspections()
  }
})
</script>

<style scoped>
.ai-ops-page{max-width:1180px;margin:0 auto;padding:24px 28px 40px;color:var(--text)}
.page-head{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:18px}.page-head h1{margin:0;font-size:25px}.page-head p{margin:6px 0 0;color:var(--muted);font-size:13px}.mode-badge{padding:6px 10px;border:1px solid #bfdbfe;border-radius:999px;background:#eff6ff;color:#2563eb;font-size:12px;font-weight:600}
.panel{background:#fff;border:1px solid var(--border);border-radius:12px;padding:18px;box-shadow:0 2px 8px rgba(15,23,42,.03)}.setup-panel,.auto-panel,.question-panel{margin-bottom:14px}.panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:14px}.panel-head b{display:block;font-size:14px}.panel-head small{display:block;margin-top:4px;color:var(--muted);font-size:12px;font-weight:400}.btn{height:34px;border:1px solid #cbd5e1;border-radius:8px;background:#fff;color:#315b98;padding:0 13px;cursor:pointer;white-space:nowrap}.btn:hover:not(:disabled){border-color:#60a5fa;background:#f8fbff}.btn:disabled{opacity:.55;cursor:not-allowed}.btn-primary{border-color:#2563eb;background:#2563eb;color:#fff;min-width:108px}.btn-primary:hover:not(:disabled){background:#1d4ed8}
.key-row{display:flex;align-items:end;gap:10px}.key-field{display:flex;flex-direction:column;gap:6px;flex:1;color:#64748b;font-size:12px}.key-field input{height:36px;border:1px solid var(--border);border-radius:8px;padding:0 10px;color:var(--text);font:inherit}.provider-state{font-size:12px;padding-bottom:9px}.provider-state.ok{color:#12965a}.provider-state.warn{color:#b7791f}.hint{margin:10px 0 0;color:var(--muted);font-size:12px}.auto-actions{display:flex;align-items:center;gap:12px}.inspection-list{display:grid;gap:8px}.inspection-list article{padding:11px;border:1px solid #e5eaf1;border-radius:8px}.inspection-head{display:flex;justify-content:space-between;gap:12px}.inspection-head span{font-size:12px}.inspection-list small{display:block;margin-top:5px;color:var(--muted);font-size:11px}.inspection-list p{margin:8px 0 0;line-height:1.55;font-size:13px}.inspection-actions{margin-top:8px;color:#64748b;font-size:12px}.question-row{display:flex;gap:12px;align-items:flex-end}.question-row textarea{width:100%;min-height:88px;resize:vertical;border:1px solid var(--border);border-radius:8px;padding:10px;color:var(--text);font:inherit;line-height:1.5}.scope{color:var(--muted);font-size:12px;white-space:nowrap}.error-box{margin:0 0 14px;padding:12px 14px;border:1px solid #fecaca;border-radius:9px;background:#fff1f2;color:#b42318;font-size:13px}.result-stack{display:grid;gap:14px}.summary-panel{border-left:3px solid #2563eb}.confidence{color:#2563eb;font-size:13px;font-weight:600}.conclusion{margin:0;color:#172b4d;line-height:1.7}.stat-row{display:flex;gap:24px;margin-top:16px;padding-top:12px;border-top:1px solid #edf1f6;color:var(--muted);font-size:12px}.stat-row b{color:var(--text);margin-left:4px}.stat-row b.danger{color:#dc2626}.result-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.evidence-list,.action-list{display:grid;gap:8px}.evidence-list article{display:grid;grid-template-columns:auto 1fr;gap:10px;padding:10px;background:#f8fafc;border-radius:8px}.evidence-list p,.action-list p{margin:0;line-height:1.55;font-size:13px}.evidence-list small,.action-list small{color:var(--muted);font-size:11px}.source-tag{height:21px;padding:2px 7px;border-radius:5px;background:#e0ecff;color:#2563eb;font-size:11px;white-space:nowrap}.action-list article{padding:11px;border:1px solid #e5eaf1;border-radius:8px}.action-title{display:flex;align-items:center;justify-content:space-between;margin-bottom:5px}.risk{padding:2px 6px;border-radius:5px;font-size:11px}.risk-R0{background:#ecfdf3;color:#15803d}.risk-R1{background:#eff6ff;color:#2563eb}.risk-R2{background:#fff7ed;color:#c2410c}.risk-R3{background:#fff1f2;color:#dc2626}.empty{margin:0;color:var(--muted);font-size:13px}.limitations{color:#6b7280;font-size:12px}.limitations ul{margin:0;padding-left:18px;line-height:1.8}
@media(max-width:760px){.ai-ops-page{padding:18px 12px}.page-head{gap:10px}.key-row,.question-row{align-items:stretch;flex-direction:column}.provider-state{padding:0}.auto-actions{align-items:stretch;flex-direction:column}.result-grid{grid-template-columns:1fr}.stat-row{gap:12px;flex-wrap:wrap}}
</style>

