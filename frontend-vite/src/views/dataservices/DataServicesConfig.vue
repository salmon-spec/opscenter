<template>
  <div ref="rootEl" class="view ds-config">
    <div class="view-head">
      <div>
        <h1 class="view-title">数据服务配置</h1>
        <p class="view-sub">连接参数加密保存 · 保存后立即用于监控与数据浏览</p>
      </div>
      <div class="head-actions">
        <span v-if="configuredCount" class="count-chip">{{ configuredCount }} 类已配置</span>
        <span v-if="updatedText" class="muted stamp">{{ updatedText }}</span>
        <button class="btn btn-sm" :disabled="loading" @click="load">{{ loading ? '加载中…' : '↻ 刷新' }}</button>
      </div>
    </div>

    <div class="notice">
      <span class="notice-mark">i</span>
      <div>
        <b>安全说明</b>
        <span>密码、Token 和连接串不会回显；留空表示保留原值。每个中间件类型维护一套独立连接。</span>
      </div>
    </div>

    <div v-if="fatal" class="card ds-error">
      <div class="big">⚠ 配置加载失败</div>
      <div class="muted">{{ fatal }}</div>
      <div><button class="btn btn-sm" :disabled="loading" @click="load">重试</button></div>
    </div>
    <div v-else-if="loading && !items.length" class="loading"><span class="spinner"></span>正在读取数据服务配置…</div>

    <div v-else class="config-list">
      <section v-for="item in items" :key="item.kind" class="config-card">
        <header class="card-head">
          <span class="service-icon">{{ iconOf(item.kind) }}</span>
          <div class="service-title">
            <div class="title-line">
              <h2>{{ item.name }}</h2>
              <span class="status-chip" :class="statusClass(item)">{{ statusText(item) }}</span>
            </div>
            <div class="meta-line">
              <span>{{ descOf(item.kind) }}</span>
              <template v-if="item.source !== 'unconfigured'">
                <i class="dot-sep"></i>
                <span :class="`source-${item.source}`">{{ sourceText(item.source) }}</span>
              </template>
              <template v-if="item.updated_at">
                <i class="dot-sep"></i>
                <span>保存于 {{ fmtTime(item.updated_at) }}</span>
              </template>
            </div>
          </div>
        </header>

        <div class="instance-panel">
          <div class="instance-head">
            <div>
              <b>监控实例</b>
              <span>主实例用于系统集成，额外实例仅采集监控指标</span>
            </div>
            <button class="btn btn-sm" @click="startAddInstance(item)">＋ 添加实例</button>
          </div>
          <div class="instance-list">
            <div class="instance-row primary">
              <span class="instance-badge">主</span>
              <div class="instance-main">
                <b>{{ item.name }}(主实例)</b>
                <small>{{ primarySummary(item) }}</small>
              </div>
              <span class="instance-tag">系统集成</span>
            </div>
            <div v-for="inst in (instances[item.kind] || [])" :key="inst.id" class="instance-row">
              <span class="instance-dot" :class="{ off: !inst.monitor_enabled }"></span>
              <div class="instance-main">
                <b>{{ inst.name }}</b>
                <small>{{ instanceSummary(inst) }}</small>
              </div>
              <span class="instance-tag" :class="{ muted: !inst.monitor_enabled }">
                {{ inst.monitor_enabled ? '监控中' : '已停用' }}
              </span>
              <div class="instance-actions">
                <button class="btn btn-sm btn-ghost" :disabled="instanceBusy[inst.id]" @click="testSavedInstance(inst)">测试</button>
                <button class="btn btn-sm btn-ghost" @click="startEditInstance(item, inst)">编辑</button>
                <button class="btn btn-sm btn-ghost" @click="promoteInstance(inst)">设为主实例</button>
                <button class="btn btn-sm btn-ghost danger" @click="removeInstance(inst)">删除</button>
              </div>
            </div>
            <div v-if="!(instances[item.kind] || []).length" class="instance-empty">暂无额外实例</div>
          </div>

          <div v-if="editingInstance[item.kind]" class="instance-editor">
            <div class="editor-head">
              <b>{{ editingInstance[item.kind].id ? '编辑监控实例' : '新增监控实例' }}</b>
              <button class="icon-btn" title="关闭" @click="closeInstanceEditor(item.kind)">×</button>
            </div>
            <div class="editor-grid">
              <label class="field">
                <span class="field-label">实例名称<b> *</b></span>
                <input v-model="editingInstance[item.kind].name" class="input" placeholder="例如：缓存从库 / 生产集群 B" />
              </label>
              <label v-for="field in item.fields" :key="`instance-${field.key}`" class="field">
                <span class="field-label" :class="{ 'field-label-inline': field.type === 'boolean' }">
                  <span>{{ field.label }}<b v-if="field.required"> *</b></span>
                  <span v-if="field.type === 'boolean'" class="toggle">
                    <input
                      type="checkbox"
                      :checked="Boolean(instanceValue(item.kind, field.key))"
                      @change="setInstanceValue(item.kind, field.key, $event.target.checked)"
                    />
                    <span class="toggle-track"><span class="toggle-knob"></span></span>
                    <span class="toggle-text">{{ instanceValue(item.kind, field.key) ? '已启用' : '已停用' }}</span>
                  </span>
                </span>
                <input
                  v-if="field.type !== 'boolean'"
                  class="input"
                  :type="field.type === 'password' ? 'password' : 'text'"
                  :value="instanceValue(item.kind, field.key)"
                  :placeholder="instancePlaceholder(editingInstance[item.kind], field)"
                  autocomplete="off"
                  @input="setInstanceValue(item.kind, field.key, $event.target.value)"
                />
              </label>
            </div>
            <div class="editor-foot">
              <label class="monitor-toggle">
                <input v-model="editingInstance[item.kind].monitor_enabled" type="checkbox" />
                启用监控采集
              </label>
              <div class="foot-actions">
                <button class="btn btn-sm btn-ghost" :disabled="instanceTesting[item.kind]" @click="testInstanceDraft(item)">
                  {{ instanceTesting[item.kind] ? '测试中…' : '测试连接' }}
                </button>
                <button class="btn btn-sm btn-primary" :disabled="instanceSaving[item.kind]" @click="saveInstance(item)">
                  {{ instanceSaving[item.kind] ? '保存中…' : '保存实例' }}
                </button>
              </div>
            </div>
            <div v-if="instanceResults[item.kind]" class="test-result" :class="{ ok: instanceResults[item.kind].available, err: !instanceResults[item.kind].available }">
              <span class="dot"></span>
              <span class="result-title">{{ instanceResults[item.kind].available ? '连接成功' : '连接失败' }}</span>
              <span v-if="instanceResults[item.kind].error" class="test-error">{{ instanceResults[item.kind].error }}</span>
            </div>
          </div>
        </div>

        <div class="primary-fields-label">主实例连接</div>
        <div class="fields">
          <label
            v-for="field in item.fields"
            :key="field.key"
            class="field"
            :class="{ 'field-wide': field.key === 'url' || field.key === 'management_url' }"
          >
            <span class="field-label" :class="{ 'field-label-inline': field.type === 'boolean' }">
              <span>{{ field.label }}<b v-if="field.required"> *</b></span>
              <span v-if="field.type === 'boolean'" class="toggle">
                <input
                  type="checkbox"
                  :checked="Boolean(valueOf(item, field.key))"
                  @change="setValue(item, field.key, $event.target.checked)"
                />
                <span class="toggle-track"><span class="toggle-knob"></span></span>
                <span class="toggle-text">{{ valueOf(item, field.key) ? '已启用' : '已停用' }}</span>
              </span>
            </span>
            <input
              v-if="field.type !== 'boolean'"
              class="input"
              :type="field.type === 'password' ? 'password' : 'text'"
              :value="valueOf(item, field.key)"
              :placeholder="placeholderOf(item, field)"
              autocomplete="off"
              @input="setValue(item, field.key, $event.target.value)"
            />
            <small v-if="field.secret && item.secret_configured?.[field.key]" class="field-hint">
              当前值：<span class="mono">{{ item.masked_values?.[field.key] || '已配置' }}</span>
            </small>
          </label>
        </div>

        <div v-if="results[item.kind]" class="test-result" :class="{ ok: results[item.kind].available, err: !results[item.kind].available }">
          <span class="dot"></span>
          <span class="result-title">{{ results[item.kind].available ? '连接成功' : '连接失败' }}</span>
          <b v-if="results[item.kind].latency_ms != null" class="result-latency">{{ Math.round(results[item.kind].latency_ms) }} ms</b>
          <span v-if="results[item.kind].error" class="test-error" :title="results[item.kind].error">{{ results[item.kind].error }}</span>
        </div>

        <footer class="card-foot">
          <span class="foot-hint">留空表示保留原值</span>
          <div class="foot-actions">
            <button
              class="btn btn-sm btn-ghost"
              :disabled="resetting[item.kind] || saving[item.kind] || item.source !== 'database'"
              @click="resetItem(item)"
            >
              {{ resetting[item.kind] ? '恢复中…' : '恢复默认' }}
            </button>
            <button
              class="btn btn-sm"
              :disabled="testing[item.kind] || saving[item.kind] || resetting[item.kind]"
              @click="testItem(item)"
            >
              {{ testing[item.kind] ? '测试中…' : '测试连接' }}
            </button>
            <button
              class="btn btn-sm btn-primary"
              :disabled="testing[item.kind] || saving[item.kind] || resetting[item.kind]"
              @click="saveItem(item)"
            >
              {{ saving[item.kind] ? '保存中…' : '保存配置' }}
            </button>
          </div>
        </footer>
      </section>
    </div>
  </div>
</template>

<script>
export default { name: 'DataServicesConfig' }
</script>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { toast } from '../../api'
import { DS_KINDS, dsApi, apiErrorText } from '../../api/dataservices'

const items = ref([])
const drafts = ref({})
const testing = ref({})
const saving = ref({})
const resetting = ref({})
const results = ref({})
const instances = ref({})
const editingInstance = ref({})
const instanceSaving = ref({})
const instanceTesting = ref({})
const instanceBusy = ref({})
const instanceResults = ref({})
const loading = ref(false)
const fatal = ref('')
const updatedAt = ref(0)

let controller = null

const kindMeta = new Map(DS_KINDS.map((item) => [item.kind, item]))
const configuredCount = computed(() => items.value.filter((item) => item.configured).length)
const updatedText = computed(() => {
  if (!updatedAt.value) return ''
  return `更新于 ${new Date(updatedAt.value).toLocaleTimeString('zh-CN', { hour12: false })}`
})

function iconOf(kind) { return kindMeta.get(kind)?.icon || '📦' }
function descOf(kind) { return kindMeta.get(kind)?.desc || '' }
function sourceText(source) {
  return { database: '自定义配置', environment: '环境变量配置', unconfigured: '未配置' }[source] || source
}
function statusText(item) {
  if (!item.configured) return '未配置'
  if (item.enabled === false) return '已停用'
  return '已配置'
}
function statusClass(item) {
  if (!item.configured) return 'tag-slate'
  return item.enabled === false ? 'tag-amber' : 'tag-green'
}
function fmtTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
function valueOf(item, key) { return drafts.value[item.kind]?.[key] ?? '' }
function setValue(item, key, value) {
  drafts.value[item.kind] = { ...(drafts.value[item.kind] || {}), [key]: value }
}
function placeholderOf(item, field) {
  if (field.secret && item.secret_configured?.[field.key]) return '已配置，留空保持不变'
  return field.placeholder || ''
}
function applyItem(item, resetDraft = true) {
  const idx = items.value.findIndex((current) => current.kind === item.kind)
  if (idx >= 0) items.value[idx] = item
  if (resetDraft) drafts.value[item.kind] = { ...(item.values || {}) }
}

function summaryOf(item, fallback = '') {
  const required = (item.fields || []).find((field) => field.required) || (item.fields || [])[0]
  if (!required) return fallback
  return item.masked_values?.[required.key] || item.values?.[required.key] || fallback
}
function primarySummary(item) {
  return summaryOf(item, sourceText(item.source))
}
function instanceSummary(item) {
  return summaryOf(item, item.monitor_enabled ? '待采集' : '已停止采集')
}
function instanceValue(kind, key) {
  return editingInstance.value[kind]?.values?.[key] ?? ''
}
function setInstanceValue(kind, key, value) {
  const editor = editingInstance.value[kind]
  if (!editor) return
  editor.values = { ...(editor.values || {}), [key]: value }
}
function instancePlaceholder(editor, field) {
  if (field.secret && editor?.secret_configured?.[field.key]) return '已配置，留空保持不变'
  return field.placeholder || ''
}
function startAddInstance(item) {
  const values = {}
  for (const field of item.fields || []) {
    values[field.key] = field.type === 'boolean'
      ? Boolean(field.default ?? true)
      : (field.default ?? '')
  }
  editingInstance.value[item.kind] = {
    id: '', name: '', values, monitor_enabled: true,
    secret_configured: {}, masked_values: {},
  }
  instanceResults.value[item.kind] = null
}
function startEditInstance(item, instance) {
  editingInstance.value[item.kind] = {
    id: instance.id,
    name: instance.name,
    values: { ...(instance.values || {}) },
    monitor_enabled: Boolean(instance.monitor_enabled),
    secret_configured: { ...(instance.secret_configured || {}) },
    masked_values: { ...(instance.masked_values || {}) },
  }
  instanceResults.value[item.kind] = null
}
function closeInstanceEditor(kind) {
  delete editingInstance.value[kind]
  instanceResults.value[kind] = null
}
function instancePayload(item) {
  const editor = editingInstance.value[item.kind]
  const payload = { name: String(editor?.name || '').trim(), monitor_enabled: Boolean(editor?.monitor_enabled) }
  for (const field of item.fields || []) {
    if (field.type === 'boolean') {
      payload[field.key] = Boolean(editor?.values?.[field.key])
      continue
    }
    const value = editor?.values?.[field.key]
    if (field.secret && String(value ?? '').trim() === '') continue
    payload[field.key] = value
  }
  return payload
}
async function testInstanceDraft(item) {
  const editor = editingInstance.value[item.kind]
  if (!String(editor?.name || '').trim()) {
    toast('请先填写实例名称', 'err')
    return
  }
  instanceTesting.value[item.kind] = true
  try {
    const result = await dsApi.testNewInstance(item.kind, instancePayload(item), { timeoutMs: 15000 })
    instanceResults.value[item.kind] = result
    toast(`${item.name}：${result.available ? '连接成功' : '连接失败'}`, result.available ? 'ok' : 'err')
  } catch (error) {
    if (error.name !== 'AbortError') {
      instanceResults.value[item.kind] = { available: false, error: apiErrorText(error) }
      toast(`${item.name}：${apiErrorText(error)}`, 'err')
    }
  } finally {
    instanceTesting.value[item.kind] = false
  }
}
function upsertInstance(kind, saved) {
  const current = [...(instances.value[kind] || [])]
  const index = current.findIndex((instance) => instance.id === saved.id)
  if (index >= 0) current[index] = saved
  else current.push(saved)
  instances.value = { ...instances.value, [kind]: current }
}
async function saveInstance(item) {
  const editor = editingInstance.value[item.kind]
  if (!String(editor?.name || '').trim()) {
    toast('实例名称不能为空', 'err')
    return
  }
  instanceSaving.value[item.kind] = true
  try {
    const payload = instancePayload(item)
    const saved = editor.id
      ? await dsApi.updateInstance(editor.id, payload, { timeoutMs: 10000 })
      : await dsApi.createInstance(item.kind, payload, { timeoutMs: 10000 })
    upsertInstance(item.kind, saved)
    closeInstanceEditor(item.kind)
    toast(`${item.name} 实例已保存`, 'ok')
  } catch (error) {
    if (error.name !== 'AbortError') toast(`${item.name}：${apiErrorText(error)}`, 'err')
  } finally {
    instanceSaving.value[item.kind] = false
  }
}
async function testSavedInstance(instance) {
  instanceBusy.value[instance.id] = true
  try {
    const result = await dsApi.testInstance(instance.id, { timeoutMs: 15000 })
    toast(`${instance.name}：${result.available ? '连接成功' : '连接失败'}`, result.available ? 'ok' : 'err')
  } catch (error) {
    if (error.name !== 'AbortError') toast(`${instance.name}：${apiErrorText(error)}`, 'err')
  } finally {
    instanceBusy.value[instance.id] = false
  }
}
async function removeInstance(instance) {
  if (!window.confirm(`确定删除监控实例 ${instance.name}？`)) return
  instanceBusy.value[instance.id] = true
  try {
    await dsApi.deleteInstance(instance.id, { timeoutMs: 10000 })
    instances.value = {
      ...instances.value,
      [instance.kind]: (instances.value[instance.kind] || []).filter((item) => item.id !== instance.id),
    }
    toast(`${instance.name} 已删除`, 'ok')
  } catch (error) {
    if (error.name !== 'AbortError') toast(apiErrorText(error), 'err')
  } finally {
    instanceBusy.value[instance.id] = false
  }
}
async function promoteInstance(instance) {
  if (!window.confirm(`将 ${instance.name} 设为主实例？原主实例会保留为监控实例。`)) return
  instanceBusy.value[instance.id] = true
  try {
    await dsApi.promoteInstance(instance.id, { timeoutMs: 10000 })
    toast(`${instance.name} 已设为主实例`, 'ok')
    await load()
  } catch (error) {
    if (error.name !== 'AbortError') toast(apiErrorText(error), 'err')
  } finally {
    instanceBusy.value[instance.id] = false
  }
}

async function load() {
  loading.value = true
  controller?.abort()
  controller = new AbortController()
  try {
    const [response, instanceResponse] = await Promise.all([
      dsApi.configs({ signal: controller.signal, timeoutMs: 10000 }),
      dsApi.instances({}, { signal: controller.signal, timeoutMs: 10000 }),
    ])
    const list = Array.isArray(response?.items) ? response.items : []
    const instanceMap = {}
    for (const instance of instanceResponse?.items || []) {
      if (!instanceMap[instance.kind]) instanceMap[instance.kind] = []
      instanceMap[instance.kind].push(instance)
    }
    items.value = list
    instances.value = instanceMap
    for (const item of list) applyItem(item)
    updatedAt.value = Date.now()
    fatal.value = ''
  } catch (error) {
    if (error.name !== 'AbortError') fatal.value = apiErrorText(error)
  } finally {
    loading.value = false
  }
}

function payloadOf(item) {
  const payload = {}
  for (const field of item.fields || []) {
    if (field.type === 'boolean') {
      payload[field.key] = Boolean(valueOf(item, field.key))
      continue
    }
    const value = valueOf(item, field.key)
    if (field.secret && String(value ?? '').trim() === '') continue
    payload[field.key] = value
  }
  return payload
}

async function testItem(item) {
  testing.value[item.kind] = true
  try {
    const result = await dsApi.testConfig(item.kind, payloadOf(item), { timeoutMs: 15000 })
    results.value[item.kind] = result
    toast(`${item.name}：${result.available ? '连接成功' : '连接失败'}`, result.available ? 'ok' : 'err')
  } catch (error) {
    if (error.name !== 'AbortError') {
      results.value[item.kind] = { available: false, latency_ms: null, error: apiErrorText(error) }
      toast(`${item.name}：${apiErrorText(error)}`, 'err')
    }
  } finally {
    testing.value[item.kind] = false
  }
}

async function saveItem(item) {
  saving.value[item.kind] = true
  try {
    const saved = await dsApi.saveConfig(item.kind, payloadOf(item), { timeoutMs: 10000 })
    applyItem(saved)
    results.value[item.kind] = null
    toast(`${item.name} 配置已保存并生效`, 'ok')
  } catch (error) {
    if (error.name !== 'AbortError') toast(`${item.name}：${apiErrorText(error)}`, 'err')
  } finally {
    saving.value[item.kind] = false
  }
}

async function resetItem(item) {
  if (!window.confirm(`确定将 ${item.name} 恢复为环境变量默认配置？`)) return
  resetting.value[item.kind] = true
  try {
    const reset = await dsApi.resetConfig(item.kind, { timeoutMs: 10000 })
    applyItem(reset)
    results.value[item.kind] = null
    toast(`${item.name} 已恢复默认配置`, 'ok')
  } catch (error) {
    if (error.name !== 'AbortError') toast(`${item.name}：${apiErrorText(error)}`, 'err')
  } finally {
    resetting.value[item.kind] = false
  }
}

onMounted(load)
onUnmounted(() => controller?.abort())
</script>

<style scoped>
.ds-config { display: flex; flex-direction: column; gap: 14px; }

.head-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.stamp { font-size: 12px; white-space: nowrap; }
.count-chip {
  display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 999px;
  background: #eef2ff; color: #4338ca; font-size: 12px; white-space: nowrap;
}

.notice {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 14px; border: 1px solid #bfdbfe; border-radius: 10px;
  background: #f0f7ff; color: #1e40af; font-size: 12px; line-height: 1.6;
}
.notice-mark {
  flex: none; width: 16px; height: 16px; margin-top: 1px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  background: #2563eb; color: #fff; font-size: 11px; font-weight: 700; font-style: italic;
}
.notice b { margin-right: 6px; }

.ds-error { display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.ds-error .big { font-weight: 700; color: var(--err); }

/* 每类中间件一行，避免多列卡片高度不齐造成的杂乱 */
.config-list { display: flex; flex-direction: column; gap: 14px; }
.config-card {
  display: flex; flex-direction: column;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: #fff; min-width: 0;
  transition: border-color .15s, box-shadow .15s;
}
.config-card:hover { border-color: #d3dded; box-shadow: 0 6px 18px rgba(15, 27, 45, .05); }

.instance-panel { padding: 14px 18px 0; }
.instance-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.instance-head > div { display: flex; align-items: baseline; gap: 9px; min-width: 0; }
.instance-head b { font-size: 13px; }
.instance-head span { color: var(--muted); font-size: 12px; }
.instance-list { border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.instance-row { display: flex; align-items: center; gap: 10px; min-height: 50px; padding: 8px 12px; border-top: 1px solid var(--border); background: #fff; }
.instance-row:first-child { border-top: 0; }
.instance-row.primary { background: #f8fafc; }
.instance-badge { flex: none; width: 22px; height: 22px; border-radius: 6px; display: inline-flex; align-items: center; justify-content: center; background: #dbeafe; color: #1d4ed8; font-size: 11px; font-weight: 700; }
.instance-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: #10b981; }
.instance-dot.off { background: #94a3b8; }
.instance-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.instance-main b, .instance-main small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.instance-main b { font-size: 13px; }
.instance-main small { color: var(--muted); font-size: 11px; font-family: Consolas, 'Courier New', monospace; }
.instance-tag { flex: none; padding: 2px 8px; border-radius: 999px; background: #ecfdf5; color: #047857; font-size: 11px; white-space: nowrap; }
.instance-tag.muted { background: #f1f5f9; color: #64748b; }
.instance-actions { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
.instance-actions .btn-sm { padding: 3px 7px; font-size: 11px; }
.instance-actions .danger { color: var(--err); }
.instance-empty { padding: 12px; color: var(--muted); font-size: 12px; text-align: center; }
.instance-editor { margin-top: 10px; padding: 12px; border: 1px solid #bfdbfe; border-radius: 8px; background: #f8fbff; }
.editor-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.editor-head b { font-size: 13px; }
.icon-btn { width: 26px; height: 26px; border: 0; border-radius: 6px; background: transparent; color: var(--muted); cursor: pointer; font-size: 18px; line-height: 1; }
.icon-btn:hover { background: #e2e8f0; color: var(--text); }
.editor-grid { display: grid; grid-template-columns: 1fr; gap: 12px; }
.editor-foot { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-top: 12px; padding-top: 10px; border-top: 1px solid #dbeafe; }
.monitor-toggle { display: inline-flex; align-items: center; gap: 6px; color: #475569; font-size: 12px; }
.primary-fields-label { padding: 14px 18px 0; color: #475569; font-size: 12px; font-weight: 700; }

.card-head {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 18px; border-bottom: 1px solid var(--border);
}
.service-icon {
  flex: none; width: 36px; height: 36px; border-radius: 10px;
  display: inline-flex; align-items: center; justify-content: center;
  background: #f1f5f9; font-size: 18px; line-height: 1;
}
.service-title { display: flex; flex-direction: column; gap: 4px; min-width: 0; flex: 1; }
.title-line { display: flex; align-items: center; gap: 10px; min-width: 0; }
.title-line h2 { margin: 0; font-size: 15px; font-weight: 600; line-height: 1.2; }
.status-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 600; white-space: nowrap;
}
.status-chip::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.status-chip.tag-green { background: #ecfdf5; color: #047857; }
.status-chip.tag-amber { background: #fffbeb; color: #b45309; }
.status-chip.tag-slate { background: #f1f5f9; color: #64748b; }

.meta-line {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
  font-size: 12px; color: var(--muted); line-height: 1.4;
}
.meta-line .source-database { color: #2563eb; font-weight: 600; }
.meta-line .source-environment { color: #7c3aed; font-weight: 600; }
.meta-line .source-unconfigured { font-weight: 600; }
.dot-sep { width: 3px; height: 3px; border-radius: 50%; background: #cbd5e1; flex: none; }

.fields {
  display: grid; grid-template-columns: 1fr;
  gap: 14px 18px; padding: 16px 18px 18px;
}
.field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.field-wide { grid-column: 1 / -1; }
.field-label { display: block; color: #475569; font-size: 12px; font-weight: 600; line-height: 1.4; }
.field-label b { color: var(--err); margin-left: 1px; }
.field-label-inline { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 24px; }

.input { background: #fff; }
.input::placeholder { color: #9aa8bd; }
.field-hint { color: var(--muted); font-size: 11px; line-height: 1.5; overflow-wrap: anywhere; }

.toggle { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; }
.toggle input { position: absolute; opacity: 0; width: 0; height: 0; }
.toggle-track {
  position: relative; width: 34px; height: 19px; border-radius: 999px; background: #cbd5e1;
  transition: background .15s; flex: none;
}
.toggle-knob {
  position: absolute; top: 2px; left: 2px; width: 15px; height: 15px; border-radius: 50%;
  background: #fff; box-shadow: 0 1px 3px rgba(15, 23, 42, .28); transition: transform .15s;
}
.toggle input:checked + .toggle-track { background: var(--brand); }
.toggle input:checked + .toggle-track .toggle-knob { transform: translateX(15px); }
.toggle input:focus-visible + .toggle-track { box-shadow: 0 0 0 3px rgba(37, 99, 235, .2); }
.toggle-text { font-size: 12px; color: var(--muted); font-weight: 500; }

.test-result {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  margin: 0 16px 14px; padding: 8px 12px; border-radius: 8px; font-size: 12px; line-height: 1.4;
}
.test-result .dot { flex: none; width: 7px; height: 7px; border-radius: 50%; background: currentColor; }
.test-result .result-title { font-weight: 600; }
.test-result .result-latency { font-variant-numeric: tabular-nums; opacity: .85; }
.test-result.ok { color: #047857; background: #ecfdf5; border: 1px solid #a7f3d0; }
.test-result.err { color: #b91c1c; background: #fef2f2; border: 1px solid #fecaca; }
.test-error { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; opacity: .9; }

.card-foot {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px;
  margin-top: auto; padding: 12px 18px;
  border-top: 1px solid var(--border); background: #f8fafc;
  border-radius: 0 0 var(--radius) var(--radius);
}
.foot-hint { font-size: 12px; color: var(--muted); }
.foot-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.foot-actions .btn-sm { padding: 5px 12px; }

@media (min-width: 720px) {
  .editor-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .fields { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (min-width: 1100px) {
  .fields { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
</style>
