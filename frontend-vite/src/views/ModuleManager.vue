<template>
  <div class="view module-manager">
    <div class="view-head">
      <div>
        <h1 class="view-title">模块管理</h1>
        <div class="view-sub">管理模块目录与导航可见性；这里的显示状态不代表模块进程或容器运行状态。</div>
      </div>
      <div class="head-actions"><button class="btn" :disabled="loading" @click="loadModules">刷新</button></div>
    </div>


    <div class="module-metrics">
      <div class="metric"><span>模块总数</span><b>{{ modules.length }}</b><small>内置与外部接入</small></div>
      <div class="metric"><span>入口已显示</span><b class="ok">{{ visibleCount }}</b><small>仅代表导航目录状态</small></div>
      <div class="metric"><span>独立运行状态</span><b>未聚合</b><small>请查看各模块健康接口</small></div>
      <div class="metric"><span>隔离方式</span><b>Sandbox</b><small>无同源权限 iframe</small></div>
    </div>

    <section class="manager-panel">
      <div class="panel-head">
        <div><h3>模块目录</h3><span class="muted">目录只控制导航可见性，不启停服务或代表运行健康。</span></div>
      </div>
      <div v-if="loading" class="loading"><span class="spinner"></span>正在加载模块</div>
      <div v-else-if="!modules.length" class="empty">暂无扩展模块</div>
      <div v-else class="table-wrap">
        <table class="table module-table">
          <thead>
            <tr><th>模块</th><th>来源</th><th>版本</th><th>导航入口状态</th><th class="actions-col">操作</th></tr>
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
              </td>
              <td class="actions-col">
                <div class="row-actions">
              <button v-if="item.running" class="btn btn-sm" @click="openModule(item)">打开</button>
              <button v-if="item.running" class="btn btn-sm" :disabled="busyKey === item.key" @click="checkHealth(item)">健康检查</button>
              <button v-if="item.running" class="btn btn-sm btn-danger" :disabled="busyKey === item.key" @click="setVisible(item, false)">隐藏入口</button>
              <button v-else class="btn btn-sm btn-primary" :disabled="busyKey === item.key" @click="setVisible(item, true)">显示入口</button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, fmtTime, toast } from '../api'

const router = useRouter()
const modules = ref([])
const loading = ref(false)
const busyKey = ref('')
const visibleCount = computed(() => modules.value.filter((m) => m.running).length)

function stateLabel(item) {
  if (!item.running) return '入口已隐藏'
  return '入口已显示'
}
function stateClass(item) {
  if (!item.running) return 'stopped'
  return item.running ? 'running' : 'stopped'
}

async function loadModules() {
  loading.value = true
  try {
    modules.value = ((await api.get('/modules')).items || []).filter((item) => item.key !== 'middleware-console')
  } catch (error) {
    toast(`模块列表加载失败：${error.message}`, 'err')
  } finally {
    loading.value = false
  }
}

function openModule(item) {
  router.push(`/extensions/${item.key}`)
}

async function setVisible(item, visible) {
  busyKey.value = item.key
  try {
    await api.post(`/modules/${item.key}/${visible ? 'start' : 'stop'}`)
    window.dispatchEvent(new Event('ops-modules-changed'))
    await loadModules()
  } catch (error) { toast(error.message, 'err') }
  finally { busyKey.value = '' }
}

async function checkHealth(item) {
  busyKey.value = item.key
  try {
    const result = await api.post(`/modules/${item.key}/health`)
    toast(result.health?.available ? `${item.name} 正常` : (result.health?.error || `${item.name} 不可用`), result.health?.available ? 'ok' : 'err')
  } catch (error) { toast(error.message, 'err') }
  finally { busyKey.value = '' }
}

onMounted(loadModules)
</script>

<style scoped>
.module-manager { max-width: 1500px; }
.readonly-notice { margin-bottom: 12px; padding: 10px 12px; border: 1px solid #f0d89e; border-radius: 7px; background: #fff9e9; color: #805f20; font-size: 12px; }
.head-actions { display: flex; gap: 8px; }
.module-metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }
.metric { min-width: 0; padding: 13px 14px; border: 1px solid var(--border); border-radius: 8px; background: #fff; }
.metric span, .metric small { display: block; color: var(--muted); font-size: 11px; }
.metric b { display: block; margin: 4px 0 2px; font-size: 20px; line-height: 1.15; }
.metric b.ok { color: var(--ok); }
.metric b.warn { color: var(--warn); }
.manager-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 14px; }
.panel-head { min-height: 58px; display: flex; align-items: center; justify-content: space-between; padding: 11px 15px; border-bottom: 1px solid var(--border); }
.panel-head h3 { margin: 0 0 3px; font-size: 14px; }
.panel-head span { font-size: 12px; }
.table-wrap { overflow-x: auto; }
.module-table { min-width: 780px; }
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
.actions-col { text-align: right; }
.row-actions { display: flex; justify-content: flex-end; gap: 6px; white-space: nowrap; }
.register-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 14px; padding: 16px; }
.register-form .wide, .register-form .form-actions { grid-column: 1 / -1; }
.form-actions { display: flex; justify-content: flex-end; align-items: center; gap: 10px; margin-top: 3px; }
@media (max-width: 900px) {
  .module-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 560px) {
  .module-metrics { grid-template-columns: 1fr; }
  .head-actions { width: 100%; }
  .head-actions .btn { flex: 1; }
}
</style>
