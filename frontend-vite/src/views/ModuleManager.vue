<template>
  <div class="view module-manager">
    <div class="view-head">
      <div>
        <h1 class="view-title">模块管理</h1>
        <div class="view-sub">外部服务以独立模块接入；停止后不在侧边栏展示，模块故障不会中断主系统。</div>
      </div>
      <div class="head-actions">
        <button class="btn" :disabled="loading" @click="loadModules">刷新</button>
        <button class="btn btn-primary" @click="showRegister=!showRegister">{{ showRegister ? '收起接入表单' : '接入外部服务' }}</button>
      </div>
    </div>

    <div class="module-metrics">
      <div class="metric"><span>模块总数</span><b>{{ modules.length }}</b><small>内置与外部接入</small></div>
      <div class="metric"><span>运行中</span><b class="ok">{{ runningCount }}</b><small>显示在侧边栏</small></div>
      <div class="metric"><span>异常</span><b :class="{ warn: degradedCount > 0 }">{{ degradedCount }}</b><small>仅影响所属模块</small></div>
      <div class="metric"><span>隔离方式</span><b>Sandbox</b><small>无同源权限 iframe</small></div>
    </div>

    <section class="manager-panel">
      <div class="panel-head">
        <div><h3>已接入模块</h3><span class="muted">启动状态决定模块是否出现在侧边栏</span></div>
      </div>
      <div v-if="loading" class="loading"><span class="spinner"></span>正在加载模块</div>
      <div v-else-if="!modules.length" class="empty">暂无扩展模块</div>
      <div v-else class="table-wrap">
        <table class="table module-table">
          <thead>
            <tr><th>模块</th><th>来源</th><th>版本</th><th>运行状态</th><th>最近检查</th><th class="actions-col">操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in modules" :key="item.key">
              <td>
                <div class="module-cell">
                  <span class="module-icon">{{ item.icon || '◇' }}</span>
                  <span><b>{{ item.name }}</b><small>{{ item.description || item.key }}</small></span>
                </div>
              </td>
              <td><span class="tag" :class="item.source === 'builtin' ? 'tag-green' : 'tag-slate'">{{ item.source === 'builtin' ? '内置' : '外部' }}</span></td>
              <td class="mono">v{{ item.version || '1.0.0' }}</td>
              <td>
                <span class="state" :class="stateClass(item)"><i></i>{{ stateLabel(item) }}</span>
                <small v-if="item.last_error" class="error-text" :title="item.last_error">{{ item.last_error }}</small>
              </td>
              <td class="muted">{{ fmtTime(item.last_checked_at) }}</td>
              <td class="actions-col">
                <div class="row-actions">
                  <button v-if="item.running" class="btn btn-sm" :disabled="busy[item.key]" @click="openModule(item)">打开</button>
                  <button v-if="item.running" class="btn btn-sm" :disabled="busy[item.key]" @click="checkHealth(item)">检查</button>
                  <button v-if="item.running" class="btn btn-sm btn-danger" :disabled="busy[item.key]" @click="stopModule(item)">停止</button>
                  <button v-else class="btn btn-sm btn-primary" :disabled="busy[item.key]" @click="startModule(item)">启动</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section v-if="showRegister" class="register-panel">
      <div class="panel-head">
        <div><h3>接入外部服务</h3><span class="muted">填写外部服务页面地址，注册后可在受控 iframe 中启动。</span></div>
      </div>
      <form class="register-form" @submit.prevent="registerModule">
        <div class="field"><label>模块 Key</label><input v-model.trim="form.key" class="input" placeholder="例如 external-dashboard" required></div>
        <div class="field"><label>模块名称</label><input v-model.trim="form.name" class="input" placeholder="例如 外部监控面板" required></div>
        <div class="field wide"><label>入口地址</label><input v-model.trim="form.entry_url" class="input" placeholder="/v3/external/index.html 或 https://service.example/" required></div>
        <div class="field wide"><label>说明</label><input v-model.trim="form.description" class="input" placeholder="模块用途与只读范围"></div>
        <div class="form-actions">
          <span v-if="formError" class="form-error">{{ formError }}</span>
          <button type="submit" class="btn btn-primary" :disabled="registering">{{ registering ? '接入中…' : '确认接入' }}</button>
        </div>
      </form>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, fmtTime, toast } from '../api'

const router = useRouter()
const modules = ref([])
const loading = ref(false)
const registering = ref(false)
const showRegister = ref(false)
const formError = ref('')
const busy = reactive({})
const form = reactive({ key: '', name: '', entry_url: '', description: '' })

const runningCount = computed(() => modules.value.filter((m) => m.running).length)
const degradedCount = computed(() => modules.value.filter((m) => m.running && m.status === 'degraded').length)

function stateLabel(item) {
  if (!item.running) return '已停止'
  if (item.status === 'degraded') return '运行中 · 异常'
  if (item.status === 'error') return '运行中 · 错误'
  return '运行中'
}
function stateClass(item) {
  if (!item.running) return 'stopped'
  return item.status === 'running' ? 'running' : 'degraded'
}

async function loadModules() {
  loading.value = true
  try {
    modules.value = (await api.get('/modules')).items || []
  } catch (error) {
    toast(`模块列表加载失败：${error.message}`, 'err')
  } finally {
    loading.value = false
  }
}

async function withBusy(item, action) {
  busy[item.key] = true
  try {
    await action()
    await loadModules()
    window.dispatchEvent(new CustomEvent('ops-modules-changed'))
  } catch (error) {
    toast(error.message, 'err')
  } finally {
    busy[item.key] = false
  }
}

function startModule(item) {
  return withBusy(item, async () => {
    await api.post(`/modules/${item.key}/start`)
    toast(`${item.name} 已启动`, 'ok')
  })
}

function stopModule(item) {
  return withBusy(item, async () => {
    await api.post(`/modules/${item.key}/stop`)
    toast(`${item.name} 已停止`, 'ok')
  })
}

function checkHealth(item) {
  return withBusy(item, async () => {
    const result = await api.post(`/modules/${item.key}/health`)
    if (result.health?.available) toast(`${item.name} 健康检查正常`, 'ok')
    else toast(`${item.name} 健康检查异常：${result.health?.error || '不可用'}`, 'err')
  })
}

function openModule(item) {
  router.push(`/extensions/${item.key}`)
}

async function registerModule() {
  formError.value = ''
  registering.value = true
  try {
    await api.post('/modules/register', { ...form, permissions: ['readonly'] })
    toast('外部模块已接入', 'ok')
    showRegister.value = false
    Object.assign(form, { key: '', name: '', entry_url: '', description: '' })
    await loadModules()
    window.dispatchEvent(new CustomEvent('ops-modules-changed'))
  } catch (error) {
    formError.value = error.message
    toast(error.message, 'err')
  } finally {
    registering.value = false
  }
}

onMounted(loadModules)
</script>

<style scoped>
.module-manager { max-width: 1500px; }
.head-actions { display: flex; gap: 8px; }
.module-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
.metric { min-width: 0; padding: 13px 14px; border: 1px solid var(--border); border-radius: 8px; background: #fff; }
.metric span, .metric small { display: block; color: var(--muted); font-size: 11px; }
.metric b { display: block; margin: 4px 0 2px; font-size: 20px; line-height: 1.15; }
.metric b.ok { color: var(--ok); }
.metric b.warn { color: var(--warn); }
.manager-panel, .register-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 14px; }
.panel-head { min-height: 58px; display: flex; align-items: center; justify-content: space-between; padding: 11px 15px; border-bottom: 1px solid var(--border); }
.panel-head h3 { margin: 0 0 3px; font-size: 14px; }
.panel-head span { font-size: 12px; }
.table-wrap { overflow-x: auto; }
.module-table { min-width: 980px; }
.module-table th { white-space: nowrap; }
.module-cell { display: flex; align-items: center; gap: 10px; min-width: 230px; }
.module-cell > span:last-child { min-width: 0; }
.module-cell b, .module-cell small { display: block; }
.module-cell small { margin-top: 3px; color: var(--muted); max-width: 340px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.module-icon { width: 34px; height: 34px; flex: none; display: grid; place-items: center; border-radius: 8px; background: #eff6ff; color: var(--brand); font-size: 18px; }
.state { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; font-size: 12px; font-weight: 600; }
.state i { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
.state.running { color: var(--ok); }
.state.running i { background: var(--ok); }
.state.degraded { color: var(--warn); }
.state.degraded i { background: var(--warn); }
.state.stopped { color: var(--muted); }
.error-text { display: block; max-width: 230px; margin-top: 4px; color: var(--err); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.actions-col { text-align: right; }
.row-actions { display: flex; justify-content: flex-end; gap: 6px; white-space: nowrap; }
.register-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 14px; padding: 16px; }
.register-form .wide, .register-form .form-actions { grid-column: 1 / -1; }
.form-actions { display: flex; justify-content: flex-end; align-items: center; gap: 10px; margin-top: 3px; }
.form-error { max-width: 70%; white-space: normal; color: var(--err); opacity: 1 !important; }
@media (max-width: 900px) {
  .module-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .register-form { grid-template-columns: 1fr; }
  .register-form .wide, .register-form .form-actions { grid-column: auto; }
}
@media (max-width: 560px) {
  .module-metrics { grid-template-columns: 1fr; }
  .head-actions { width: 100%; }
  .head-actions .btn { flex: 1; }
}
</style>
