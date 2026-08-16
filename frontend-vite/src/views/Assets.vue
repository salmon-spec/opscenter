<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">资产管理</h1>
        <p class="view-sub">主机资源 · 服务操控 · 日志 · 终端连接（真实 SSH / SFTP）</p>
      </div>
      <button class="btn" @click="reload">刷新</button>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>正在加载主机…</div>
    <EmptyState v-else-if="!hosts.length" icon="🖥" text="暂无主机，请在资源管理中录入" />

    <div v-else class="host-list">
      <div
        v-for="host in hosts" :key="host.id"
        class="card host-card" :class="{ expanded: expandedId === host.id }"
      >
        <div class="host-row" @click="toggleHost(host)">
          <span class="host-icon">🖥</span>
          <div class="host-main">
            <div class="host-name">
              {{ host.name }}
              <span class="tag" :class="host.status === 'online' ? 'tag-green' : host.status === 'offline' ? 'tag-red' : 'tag-slate'">
                {{ host.status || 'unknown' }}
              </span>
            </div>
            <div class="host-meta muted">
              {{ host.host }} · {{ host.ssh_user || 'root' }} · agent: {{ host.agent_status || 'not_deployed' }} · 服务 {{ host.service_count || 0 }}
            </div>
          </div>
          <div class="host-bars" v-if="monitors[host.id]">
            <div class="h-bar"><span>CPU</span><b>{{ m(host).metrics.cpu }}%</b></div>
            <div class="h-bar"><span>内存</span><b>{{ m(host).metrics.memory }}%</b></div>
            <div class="h-bar"><span>磁盘</span><b>{{ m(host).metrics.disk }}%</b></div>
          </div>
          <div class="host-actions" @click.stop>
            <button class="btn btn-sm btn-primary" @click="openTerminal(host)">终端连接</button>
            <button class="btn btn-sm" @click="power(host, 'reboot')">重启</button>
            <button class="btn btn-sm btn-danger" @click="power(host, 'shutdown')">关机</button>
            <button class="btn btn-sm" @click="toast('远程开机需物理/IPMI 介入，暂不支持', 'err')">开机</button>
          </div>
        </div>

        <!-- 展开：主机内服务 -->
        <div v-if="expandedId === host.id" class="host-detail">
          <div v-if="serviceLoading[host.id]" class="loading"><span class="spinner"></span>加载服务…</div>
          <EmptyState v-else-if="!hostServices[host.id]?.length" icon="📦" text="该主机暂无服务" style="padding:20px" />
          <table v-else class="table">
            <thead>
              <tr><th>服务</th><th>容器/镜像</th><th>端口</th><th>状态</th><th style="width:260px">操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="svc in hostServices[host.id]" :key="svc.id">
                <td>
                  <div style="font-weight:600">{{ svc.name }}</div>
                  <div class="muted" style="font-size:12px">{{ svc.description || '' }}</div>
                </td>
                <td class="mono">{{ svc.container_name || svc.image || '-' }}</td>
                <td class="mono">{{ svc.ports || '-' }}</td>
                <td>
                  <span class="tag" :class="svc.status === 'online' || svc.status === 'up' || svc.status === 'running' ? 'tag-green' : 'tag-slate'">
                    {{ svc.status || 'unknown' }}
                  </span>
                </td>
                <td>
                  <button class="btn btn-sm" :disabled="busy[svc.id]" @click="serviceControl(host, svc, 'restart')">重启</button>
                  <button class="btn btn-sm" :disabled="busy[svc.id]" @click="serviceControl(host, svc, 'start')">启动</button>
                  <button class="btn btn-sm" :disabled="busy[svc.id]" @click="serviceControl(host, svc, 'stop')">停止</button>
                  <button class="btn btn-sm" @click="openLogs(host, svc)">日志</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- 终端（xterm + SFTP） -->
    <TerminalPanel v-if="terminalSession" :session-id="terminalSession.id" :title="terminalSession.hostName" @close="terminalSession = null" />

    <!-- 日志 -->
    <Modal :visible="!!logTarget" :title="`日志 · ${logTarget?.serviceName || ''}`" width="760px" @close="logTarget = null">
      <div class="log-box" ref="logBox">
        <div v-if="logLoading" class="loading"><span class="spinner"></span>加载日志…</div>
        <pre v-else class="log-pre">{{ logs || '（无日志输出）' }}</pre>
      </div>
    </Modal>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { api, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import TerminalPanel from '../components/TerminalPanel.vue'

const hosts = ref([])
const monitors = reactive({})
const loading = ref(true)
const expandedId = ref(null)
const hostServices = reactive({})
const serviceLoading = reactive({})
const busy = reactive({})
const terminalSession = ref(null)
const logTarget = ref(null)
const logs = ref('')
const logLoading = ref(false)

const m = (host) => monitors[host.id]

async function reload() {
  loading.value = true
  try {
    hosts.value = await api.get('/servers')
    // 并行拉取每台主机实时指标（失败不影响列表）
    await Promise.allSettled(hosts.value.map(async (h) => {
      try {
        const data = await api.get(`/servers/${h.id}/monitor`)
        monitors[h.id] = data.metrics || {}
      } catch { monitors[h.id] = null }
    }))
  } finally {
    loading.value = false
  }
}

async function toggleHost(host) {
  if (expandedId.value === host.id) {
    expandedId.value = null
    return
  }
  expandedId.value = host.id
  if (!hostServices[host.id]) await loadHostServices(host)
}

async function loadHostServices(host) {
  serviceLoading[host.id] = true
  try {
    const list = await api.get('/services/all', { server_id: host.id })
    hostServices[host.id] = list
  } catch (e) {
    hostServices[host.id] = []
    toast(`加载 ${host.name} 服务失败: ${e.message}`, 'err')
  } finally {
    serviceLoading[host.id] = false
  }
}

function serviceNameOf(svc) {
  // 仅用于日志弹窗标题展示；操控与日志统一走后端 /services/{id} 接口
  return svc.name
}

async function serviceControl(host, svc, action) {
  if (!confirm(`确认对 ${svc.name} 执行「${action}」？`)) return
  busy[svc.id] = true
  try {
    const res = await api.post(`/services/${svc.id}/control`, { action })
    toast(`${svc.name} ${action} 已执行`, 'ok')
    if (res?.output) logs.value = res.output
  } catch (e) {
    toast(e.message || '操作失败', 'err')
  } finally {
    busy[svc.id] = false
  }
}

async function openLogs(host, svc) {
  logTarget.value = { hostId: host.id, serviceName: serviceNameOf(svc) }
  logLoading.value = true
  logs.value = ''
  try {
    const res = await api.get(`/services/${svc.id}/logs`, { lines: 200 })
    logs.value = res.logs || ''
  } catch (e) {
    logs.value = `日志获取失败: ${e.message}`
  } finally {
    logLoading.value = false
  }
}

async function openTerminal(host) {
  try {
    const res = await api.post('/terminal/sessions', { server_id: host.id, cols: 100, rows: 30 })
    terminalSession.value = { id: res.session_id, hostName: host.name }
  } catch (e) {
    toast(`终端连接失败: ${e.message}`, 'err')
  }
}

async function power(host, action) {
  const text = action === 'reboot' ? '重启' : '关机'
  if (!confirm(`确认对 ${host.name} 执行「${text}」？该操作将中断主机上所有服务。`)) return
  try {
    await api.post(`/servers/${host.id}/power`, { action })
    host.status = 'offline'
    toast(`${host.name} 正在${text}…`, 'ok')
  } catch (e) {
    toast(e.message || `${text}失败`, 'err')
  }
}

onMounted(reload)
</script>

<style scoped>
.host-list { display: flex; flex-direction: column; gap: 12px; }
.host-card { padding: 14px 16px; }
.host-row { display: flex; align-items: center; gap: 14px; cursor: pointer; flex-wrap: wrap; }
.host-icon { font-size: 26px; }
.host-main { flex: 1; min-width: 220px; }
.host-name { font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.host-meta { font-size: 12px; margin-top: 3px; }
.host-bars { display: flex; gap: 14px; }
.h-bar { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.h-bar span { font-size: 11px; color: var(--muted); }
.h-bar b { font-size: 13px; }
.host-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.host-detail { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
.log-box { max-height: 60vh; overflow: auto; background: #0d1117; border-radius: 8px; padding: 12px; }
.log-pre { margin: 0; color: #e6edf3; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; font-family: Consolas, 'Courier New', monospace; }
</style>
