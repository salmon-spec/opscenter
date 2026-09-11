<template>
  <div class="host-list">
    <div class="filter-bar">
      <select class="select filter-item" :value="filters.status" @change="setFilter('status', $event.target.value)">
        <option value="">状态 · 全部</option>
        <option value="online">在线</option>
        <option value="offline">离线</option>
      </select>
      <select class="select filter-item" :value="filters.role" @change="setFilter('role', $event.target.value)">
        <option value="">角色 · 全部</option>
        <option v-for="role in roleOptions" :key="role" :value="role">{{ role }}</option>
      </select>
      <select class="select filter-item" :value="filters.clusterId" @change="setFilter('clusterId', $event.target.value)">
        <option value="">集群 · 全部</option>
        <option v-for="cluster in clusterOptions" :key="clusterIdOf(cluster)" :value="clusterIdOf(cluster)">{{ clusterNameOf(cluster) }}</option>
      </select>
      <select class="select filter-item" :value="filters.agent" @change="setFilter('agent', $event.target.value)">
        <option value="">Agent · 全部</option>
        <option value="outdated">待升级</option>
        <option value="ok">正常</option>
      </select>
      <input class="input filter-item" :value="filters.tag" placeholder="标签关键字" @input="setFilter('tag', $event.target.value)" />
      <input class="input filter-item filter-kw" :value="filters.keyword" placeholder="名称 / 地址 / IP" @input="setFilter('keyword', $event.target.value)" />
      <button class="btn btn-sm" @click="clearFilters">重置</button>
    </div>

    <div v-if="loading && !hosts.length" class="loading"><span class="spinner"></span>加载中…</div>
    <EmptyState v-else-if="!filtered.length" icon="🖥" text="没有符合条件的主机" />
    <div v-else class="table-wrap">
      <table class="table hosts-table">
        <thead>
          <tr>
            <th>名称</th><th>主地址</th><th>LAN IP</th><th>WG IP</th><th>角色 / 节点</th><th>状态</th>
            <th>CPU / 内存 / 磁盘</th><th>Agent</th><th>日志采集器</th><th>服务数</th><th>标签</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="host in filtered" :key="host.id" class="host-tr" @click="$emit('detail', host)">
            <td>
              <b>{{ host.name }}</b>
              <em v-if="host.remark" class="remark" :title="host.remark">{{ host.remark }}</em>
            </td>
            <td class="mono">{{ host.host }}</td>
            <td class="mono">{{ host.lan_ip || '—' }}</td>
            <td class="mono">{{ host.wireguard_ip || '—' }}</td>
            <td>
              <span v-if="host.node_role" class="tag tag-slate tag-mini">{{ host.node_role }}</span>
              <span v-if="host.kubernetes_node_name" class="node-name mono">{{ host.kubernetes_node_name }}</span>
              <span v-if="!host.node_role && !host.kubernetes_node_name" class="muted">—</span>
            </td>
            <td>
              <span class="status-cell"><span class="dot" :class="host.status === 'online' ? 'ok' : 'off'"></span>{{ host.status === 'online' ? '在线' : '离线' }}</span>
            </td>
            <td class="mono metrics-cell">{{ metricsCell(host) }}</td>
            <td>
              <span class="tag" :class="agentTagClass(host.agent_status)">{{ agentStatusLabel(host.agent_status) }}</span>
              <span class="agent-ver" :class="{ outdated: isOutdated(host) }">{{ host.agent_version ? 'v' + host.agent_version : '' }}<template v-if="isOutdated(host)">（待升级）</template></span>
            </td>
            <td>
              <span class="tag" :class="logTagClass(host.log_agent_status)">{{ logAgentStatusLabel(host.log_agent_status) }}</span>
              <span v-if="host.log_agent_version" class="agent-ver">v{{ host.log_agent_version }}</span>
            </td>
            <td>{{ host.service_count ?? '—' }}</td>
            <td>
              <template v-if="(host.tags || []).length">
                <span v-for="tag in (host.tags || []).slice(0, 2)" :key="tag" class="tag tag-slate tag-mini">{{ tag }}</span>
                <span v-if="(host.tags || []).length > 2" class="muted more-tags">+{{ host.tags.length - 2 }}</span>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td class="ops" @click.stop>
              <button class="link-btn" @click="$emit('detail', host)">详情</button>
              <button class="link-btn" @click="$emit('edit', host)">编辑</button>
              <button v-if="host.agent_type !== 'local' && !host.is_local" class="link-btn danger" :disabled="deletingId === host.id" @click="openDelete(host)">{{ deletingId === host.id ? '删除中…' : '删除' }}</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <Modal :visible="deleteTarget !== null" title="删除主机" width="480px" @close="cancelDelete">
      <div class="delete-confirm">
        <p>删除后关联服务与监控历史也会一并移除。<b>删除资产不会卸载远端 Agent</b>；如需卸载，请先在该主机上单独执行 Agent 卸载。</p>
        <label>请输入主机名称「<b>{{ deleteTarget?.name || '' }}</b>」以确认：<input v-model.trim="deleteConfirmText" :disabled="!!deletingId" placeholder="主机名称" /></label>
        <footer>
          <button class="btn" :disabled="!!deletingId" @click="deleteTarget = null">取消</button>
          <button class="btn btn-danger" :disabled="!deleteConfirmText || deleteConfirmText !== deleteTarget?.name || !!deletingId" @click="confirmDelete">{{ deletingId ? '正在删除主机…' : '确认删除' }}</button>
        </footer>
      </div>
    </Modal>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { api, toast } from '../../api'
import { useHostContext } from '../../hostContext'
import Modal from '../Modal.vue'
import EmptyState from '../EmptyState.vue'
import {
  agentStatusLabel, agentTagClass, logAgentStatusLabel, logTagClass,
  isHostOutdated,
} from './hostAdmin'

const props = defineProps({
  filters: { type: Object, required: true },
  metricsMap: { type: Object, default: () => ({}) },
  clusterOptions: { type: Array, default: () => [] },
  agentVersion: { type: String, default: '2.6.0' },
})
const emit = defineEmits(['update:filters', 'detail', 'edit'])

const { hosts, selectedHostId, loading, refreshHosts, selectHost } = useHostContext()

function setFilter(key, value) {
  emit('update:filters', { ...props.filters, [key]: value })
}
function clearFilters() {
  emit('update:filters', { status: '', role: '', clusterId: '', agent: '', tag: '', keyword: '' })
}

const roleOptions = computed(() => [...new Set(hosts.value.map((h) => h.node_role).filter(Boolean))].sort())
function clusterIdOf(cluster) {
  return String(cluster?.id ?? cluster?.cluster_id ?? '')
}
function clusterNameOf(cluster) {
  return cluster?.name ?? cluster?.cluster_name ?? (clusterIdOf(cluster) || '未命名集群')
}

const filtered = computed(() => {
  const f = props.filters || {}
  const keyword = String(f.keyword || '').trim().toLowerCase()
  const tag = String(f.tag || '').trim().toLowerCase()
  return hosts.value.filter((host) => {
    if (f.status && host.status !== f.status) return false
    if (f.role && (host.node_role || '') !== f.role) return false
    if (f.clusterId && String(host.cluster_id || '') !== f.clusterId) return false
    if (f.agent === 'outdated' && !isOutdated(host)) return false
    if (f.agent === 'ok' && isOutdated(host)) return false
    if (tag && !(host.tags || []).some((item) => String(item).toLowerCase().includes(tag))) return false
    if (keyword) {
      const hay = [host.name, host.host, host.lan_ip, host.wireguard_ip, host.kubernetes_node_name]
        .filter(Boolean).join(' ').toLowerCase()
      if (!hay.includes(keyword)) return false
    }
    return true
  })
})

function isOutdated(host) {
  return isHostOutdated(host, props.agentVersion)
}
function metricsCell(host) {
  const m = props.metricsMap[host.id]
  if (!m || (m.cpu == null && m.memory == null && m.disk == null)) return '—'
  const fmt = (value) => (value == null ? '—' : `${Math.round(value)}%`)
  return `${fmt(m.cpu)} / ${fmt(m.memory)} / ${fmt(m.disk)}`
}

const deleteTarget = ref(null)
const deleteConfirmText = ref('')
const deletingId = ref('')
function openDelete(host) {
  deleteConfirmText.value = ''
  deleteTarget.value = host
}
function cancelDelete() {
  if (!deletingId.value) deleteTarget.value = null
}
async function confirmDelete() {
  const host = deleteTarget.value
  if (!host || deleteConfirmText.value !== host.name) return
  const wasSelected = selectedHostId.value === host.id
  deletingId.value = host.id
  toast(`正在删除主机「${host.name}」…`, 'info')
  try {
    const data = await api.del(`/servers/${host.id}`)
    deleteTarget.value = null
    await refreshHosts(true)
    if (wasSelected) {
      const fallback = hosts.value.find((h) => h.agent_type === 'local' || h.is_local) || hosts.value[0]
      if (fallback) selectHost(fallback.id)
    }
    toast(data.message || '主机已删除', 'ok')
  } catch (error) {
    toast(`删除失败：${error.message}`, 'err')
  } finally {
    deletingId.value = ''
  }
}
</script>

<style scoped>
.host-list { display: flex; flex-direction: column; gap: 12px; }
.filter-bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.filter-item { width: auto; min-width: 130px; }
.filter-kw { min-width: 180px; }
.table-wrap { overflow-x: auto; background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); }
.hosts-table { min-width: 1180px; }
.hosts-table .host-tr { cursor: pointer; }
.remark { display: block; font-style: normal; color: var(--muted); font-size: 11px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.node-name { display: block; color: var(--muted); font-size: 12px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tag-mini { margin-right: 4px; }
.more-tags { font-size: 11px; }
.status-cell { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot.ok { background: var(--ok); }
.dot.off { background: #94a3b8; }
.metrics-cell { white-space: nowrap; }
.agent-ver { margin-left: 6px; font-size: 12px; color: var(--muted); }
.agent-ver.outdated { color: var(--warn); font-weight: 600; }
.ops { white-space: nowrap; }
.link-btn { border: 0; background: none; color: var(--brand); cursor: pointer; font-size: 12px; margin-right: 8px; }
.link-btn.danger { color: var(--err); }
.link-btn:disabled { opacity: .5; cursor: not-allowed; }
.delete-confirm p { margin: 0 0 12px; line-height: 1.6; font-size: 13px; }
.delete-confirm label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--muted); }
.delete-confirm input { border: 1px solid var(--border); background: var(--bg); color: var(--text); padding: 9px; border-radius: 6px; }
.delete-confirm footer { display: flex; justify-content: flex-end; gap: 8px; margin-top: 16px; }
</style>
