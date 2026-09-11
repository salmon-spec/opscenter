<template>
  <div class="upgrade-panel">
    <div class="panel-head">
      <b>Agent 升级</b>
      <span class="tag" :class="stateTagClass">{{ stateLabel }}</span>
    </div>

    <div class="info-grid">
      <div class="info-item"><span class="muted">当前版本</span><b>{{ server?.agent_version ? 'v' + server.agent_version : '—' }}</b></div>
      <div class="info-item"><span class="muted">目标版本</span><b>{{ targetVersion ? 'v' + targetVersion : (targetLoading ? '检查中…' : '—') }}</b></div>
      <div class="info-item"><span class="muted">连接通道</span><span>{{ channelText }}</span></div>
      <div class="info-item"><span class="muted">影响说明</span><span>升级期间该主机 Agent 短暂中断采集</span></div>
    </div>

    <div v-if="task" class="task-state">
      <div class="task-line">
        <span class="muted">任务 {{ taskShortId }}</span>
        <span class="tag" :class="taskTagClass">{{ taskLabel }}</span>
        <span v-if="task.message" class="task-msg">{{ task.message }}</span>
      </div>
      <p v-if="task.status === 'failed' && (task.error || lastError)" class="task-err">{{ task.error || lastError }}</p>
    </div>

    <p v-if="successCriteria" class="criteria muted">成功标准：{{ successCriteria }}</p>

    <div class="panel-foot">
      <button v-if="canRetry" class="btn btn-sm btn-danger" @click="confirmOpen = true">重试升级</button>
      <button v-else-if="!taskRunning" class="btn btn-sm btn-primary" :disabled="!server?.id || targetLoading" @click="confirmOpen = true">升级 Agent</button>
      <span v-else class="muted running-hint">升级任务进行中，每 2.5 秒自动刷新状态…</span>
    </div>

    <div v-if="confirmOpen" class="confirm-box">
      <p>确认将主机「{{ server?.name || '' }}」的 Agent 从 <b>{{ server?.agent_version ? 'v' + server.agent_version : '当前版本' }}</b> 升级到 <b>{{ targetVersion ? 'v' + targetVersion : '目标版本' }}</b>？升级为后台任务，期间该主机 Agent 会短暂中断采集。</p>
      <footer>
        <button class="btn btn-sm" @click="confirmOpen = false">取消</button>
        <button class="btn btn-sm btn-primary" :disabled="starting" @click="startUpgrade">{{ starting ? '提交中…' : '确认升级' }}</button>
      </footer>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, toast } from '../../api'
import { useTabActive } from '../../workbench/tabs'
import { monitoringAddress, monitoringChannelLabel } from './hostAdmin'

const props = defineProps({
  server: { type: Object, default: null },
  visible: Boolean,
})
const emit = defineEmits(['upgraded'])

const { isActive } = useTabActive('Hosts')

const targetVersion = ref('')
const targetLoading = ref(false)
const task = ref(null)
const successCriteria = ref('')
const lastError = ref('')
const confirmOpen = ref(false)
const starting = ref(false)
let timer = null

const taskRunning = computed(() => task.value?.status === 'deploying' || props.server?.agent_status === 'deploying')
const canRetry = computed(() => task.value?.status === 'failed')

const stateTagClass = computed(() => {
  if (taskRunning.value) return 'tag-amber'
  if (task.value?.status === 'success') return 'tag-green'
  if (task.value?.status === 'failed') return 'tag-red'
  return 'tag-slate'
})
const stateLabel = computed(() => {
  if (taskRunning.value) return '部署中'
  if (task.value?.status === 'success') return '成功'
  if (task.value?.status === 'failed') return '失败'
  return '待执行'
})
const taskTagClass = computed(() => {
  if (task.value?.status === 'success') return 'tag-green'
  if (task.value?.status === 'failed') return 'tag-red'
  return 'tag-amber'
})
const taskLabel = computed(() => {
  if (!task.value) return ''
  return task.value.phase || { deploying: '部署中', success: '成功', failed: '失败' }[task.value.status] || task.value.status
})
const taskShortId = computed(() => String(task.value?.task_id || '').slice(0, 8) || '—')
const channelText = computed(() => {
  const server = props.server
  if (!server) return '—'
  const parts = [monitoringChannelLabel(server)]
  const target = monitoringAddress(server)
  if (target) parts.push(`目标 ${target}`)
  return parts.join(' · ')
})

async function loadTarget() {
  if (targetVersion.value) return
  targetLoading.value = true
  try {
    const data = await api.get('/agents/version')
    targetVersion.value = data.current_version || ''
  } catch {
    targetVersion.value = ''
  } finally {
    targetLoading.value = false
  }
}

async function refreshStatus() {
  if (!props.server?.id) return
  try {
    const data = await api.get(`/servers/${props.server.id}/agent/upgrade/status`)
    const prev = task.value
    task.value = data.task || null
    successCriteria.value = data.success_criteria || ''
    lastError.value = data.last_error || ''
    if (prev && prev.status === 'deploying' && data.task?.status === 'success') {
      toast('Agent 升级成功，已通过成功标准校验', 'ok')
      emit('upgraded')
    }
  } catch {
    /* 状态接口失败时静默，下一轮重试 */
  }
  syncPolling()
}

function syncPolling() {
  const shouldPoll = props.visible && isActive.value && taskRunning.value
  if (shouldPoll && !timer) timer = setInterval(refreshStatus, 2500)
  if (!shouldPoll && timer) {
    clearInterval(timer)
    timer = null
  }
}

async function startUpgrade() {
  if (!props.server?.id) return
  starting.value = true
  try {
    await api.post(`/servers/${props.server.id}/upgrade-agent`)
    toast('Agent 升级任务已提交，正在后台执行', 'ok')
    confirmOpen.value = false
    await refreshStatus()
  } catch (error) {
    toast(`升级提交失败：${error.message}`, 'err')
  } finally {
    starting.value = false
  }
}

watch([() => props.visible, () => props.server?.id, isActive], ([visible, serverId]) => {
  if (visible && serverId) {
    loadTarget()
    refreshStatus()
  }
  syncPolling()
}, { immediate: true })

onUnmounted(() => {
  if (timer) clearInterval(timer)
  timer = null
})
</script>

<style scoped>
.upgrade-panel { margin-top: 14px; padding: 14px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; }
.panel-head { display: flex; align-items: center; gap: 8px; }
.panel-head b { font-size: 13px; }
.info-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 16px; margin-top: 12px; font-size: 13px; }
.info-item { display: flex; flex-direction: column; gap: 2px; }
.info-item .muted { font-size: 11px; }
.task-state { margin-top: 12px; }
.task-line { display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
.task-msg { color: var(--muted); }
.task-err { margin: 6px 0 0; color: var(--err); font-size: 12px; line-height: 1.5; word-break: break-all; }
.criteria { margin: 10px 0 0; font-size: 12px; }
.panel-foot { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.running-hint { font-size: 12px; }
.confirm-box { margin-top: 12px; padding: 12px; background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; }
.confirm-box p { margin: 0 0 10px; font-size: 12px; line-height: 1.6; }
.confirm-box footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
