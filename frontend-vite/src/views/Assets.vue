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
          <div class="host-bars" v-if="m(host)?.metrics && !m(host)?.error">
            <div class="h-bar"><span>CPU</span><b>{{ metricValue(host, 'cpu') }}%</b></div>
            <div class="h-bar"><span>内存</span><b>{{ metricValue(host, 'memory') }}%</b></div>
            <div class="h-bar"><span>磁盘</span><b>{{ metricValue(host, 'disk') }}%</b></div>
          </div>
          <div class="monitor-state" :class="{ error: m(host)?.error }">
            {{ monitorLabel(host) }}
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
          <div v-if="m(host)?.source === 'agent'" class="monitor-detail">
            <div class="detail-title">
              实时资源 <span class="muted">Agent {{ m(host)?.agent_version || host.agent_version || '-' }}</span>
            </div>
            <div class="metric-grid">
              <div class="metric-card"><span>负载 1 / 5 / 15 分钟</span><b>{{ metricValue(host, 'load1') }} / {{ metricValue(host, 'load5') }} / {{ metricValue(host, 'load15') }}</b></div>
              <div class="metric-card"><span>Swap</span><b>{{ metricValue(host, 'swap') }}%</b><small>{{ fmtBytes(m(host)?.metrics?.swap_used) }} / {{ fmtBytes(m(host)?.metrics?.swap_total) }}</small></div>
              <div class="metric-card"><span>运行时间</span><b>{{ fmtDuration(m(host)?.metrics?.uptime) }}</b></div>
              <div class="metric-card"><span>容器</span><b>{{ metricValue(host, 'container_running') }} 运行 / {{ metricValue(host, 'container_stopped') }} 停止</b></div>
            </div>

            <div class="detail-grid">
              <section class="detail-panel">
                <h3>磁盘与挂载点</h3>
                <div v-if="!m(host)?.disks?.length" class="detail-empty">Agent 升级后将显示多磁盘信息</div>
                <table v-else class="mini-table">
                  <thead><tr><th>挂载点</th><th>设备</th><th>已用</th><th>使用率</th></tr></thead>
                  <tbody><tr v-for="disk in m(host).disks" :key="`${disk.device}-${disk.mountpoint}`">
                    <td class="mono">{{ disk.mountpoint }}</td><td class="mono">{{ disk.device }}</td>
                    <td>{{ fmtBytes(disk.used) }} / {{ fmtBytes(disk.total) }}</td><td>{{ disk.percent }}%</td>
                  </tr></tbody>
                </table>
              </section>
              <section class="detail-panel">
                <h3>网卡流量</h3>
                <div v-if="!m(host)?.network_interfaces?.length" class="detail-empty">暂无网卡明细</div>
                <table v-else class="mini-table">
                  <thead><tr><th>网卡</th><th>接收</th><th>发送</th><th>错误</th></tr></thead>
                  <tbody><tr v-for="nic in m(host).network_interfaces" :key="nic.interface">
                    <td class="mono">{{ nic.interface }}</td><td>{{ nic.rx_rate_mbps }} Mbps</td><td>{{ nic.tx_rate_mbps }} Mbps</td>
                    <td>{{ Number(nic.rx_errors || 0) + Number(nic.tx_errors || 0) }}</td>
                  </tr></tbody>
                </table>
              </section>
              <section class="detail-panel">
                <h3>CPU Top 进程</h3>
                <div v-if="!m(host)?.top_cpu_processes?.length" class="detail-empty">暂无进程明细</div>
                <table v-else class="mini-table">
                  <thead><tr><th>进程</th><th>PID</th><th>CPU</th><th>内存</th></tr></thead>
                  <tbody><tr v-for="proc in m(host).top_cpu_processes.slice(0, 8)" :key="proc.pid">
                    <td class="mono">{{ proc.command }}</td><td>{{ proc.pid }}</td><td>{{ proc.cpu_percent }}%</td><td>{{ proc.memory_percent }}%</td>
                  </tr></tbody>
                </table>
              </section>
              <section class="detail-panel">
                <h3>容器资源</h3>
                <div v-if="!m(host)?.container_stats?.length" class="detail-empty">暂无运行容器或 Docker 指标</div>
                <table v-else class="mini-table">
                  <thead><tr><th>容器</th><th>CPU</th><th>内存</th><th>网络 IO</th></tr></thead>
                  <tbody><tr v-for="ctr in m(host).container_stats" :key="ctr.container || ctr.name">
                    <td class="mono">{{ ctr.name }}</td><td>{{ ctr.cpu_percent }}</td><td>{{ ctr.memory_usage }} ({{ ctr.memory_percent }})</td><td>{{ ctr.network_io }}</td>
                  </tr></tbody>
                </table>
              </section>
            </div>
          </div>

          <div class="detail-title service-title">主机服务</div>
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
const metricValue = (host, key) => m(host)?.metrics?.[key] ?? '—'

function fmtBytes(value) {
  const n = Number(value || 0)
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1)
  return `${(n / (1024 ** index)).toFixed(index > 1 ? 1 : 0)} ${units[index]}`
}

function fmtDuration(seconds) {
  const total = Number(seconds || 0)
  if (!total) return '—'
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  return days ? `${days}天 ${hours}小时` : `${hours}小时`
}

function monitorLabel(host) {
  const state = m(host)
  if (!state) return '监控加载中'
  if (state.error) return `监控异常：${state.error}`
  return `监控：${state.source === 'agent' ? 'Agent' : state.source === 'ssh' ? 'SSH' : '未知'}`
}

async function reload() {
  loading.value = true
  try {
    hosts.value = await api.get('/servers')
    // 并行拉取每台主机实时指标（失败不影响列表）
    await Promise.allSettled(hosts.value.map(async (h) => {
      try {
        const data = await api.get(`/servers/${h.id}/monitor`)
        monitors[h.id] = data
      } catch (e) {
        monitors[h.id] = { metrics: null, source: null, error: e.message || '请求失败' }
      }
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
.monitor-state { max-width: 220px; font-size: 11px; color: var(--muted); }
.monitor-state.error { color: var(--err); }
.h-bar { display: flex; flex-direction: column; align-items: center; gap: 2px; }
.h-bar span { font-size: 11px; color: var(--muted); }
.h-bar b { font-size: 13px; }
.host-actions { display: flex; gap: 6px; flex-wrap: wrap; }
.host-detail { margin-top: 14px; border-top: 1px solid var(--border); padding-top: 12px; }
.monitor-detail { margin-bottom: 18px; }
.detail-title { font-size: 14px; font-weight: 700; margin-bottom: 10px; display: flex; gap: 8px; align-items: baseline; }
.detail-title .muted { font-size: 11px; font-weight: 400; }
.service-title { border-top: 1px solid var(--border); padding-top: 14px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(150px, 1fr)); gap: 10px; margin-bottom: 12px; }
.metric-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; background: var(--bg-soft, rgba(127,127,127,.04)); }
.metric-card span, .metric-card small { color: var(--muted); font-size: 11px; }
.metric-card b { font-size: 14px; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.detail-panel { border: 1px solid var(--border); border-radius: 8px; padding: 10px; min-width: 0; overflow-x: auto; }
.detail-panel h3 { font-size: 12px; margin: 0 0 8px; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 11px; }
.mini-table th, .mini-table td { text-align: left; padding: 6px; border-bottom: 1px solid var(--border); white-space: nowrap; }
.mini-table th { color: var(--muted); font-weight: 500; }
.detail-empty { color: var(--muted); font-size: 11px; padding: 8px 0; }
@media (max-width: 1100px) { .metric-grid, .detail-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) { .metric-grid, .detail-grid { grid-template-columns: 1fr; } }
.log-box { max-height: 60vh; overflow: auto; background: #0d1117; border-radius: 8px; padding: 12px; }
.log-pre { margin: 0; color: #e6edf3; font-size: 12px; line-height: 1.6; white-space: pre-wrap; word-break: break-all; font-family: Consolas, 'Courier New', monospace; }
</style>
