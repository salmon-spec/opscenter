<template>
  <aside class="host-sidebar">
    <div class="host-sidebar-head"><span>目标主机</span><b>{{ hosts.length }}</b></div>
    <div class="host-list">
      <button v-for="host in hosts" :key="host.id" class="host-item" :class="{ active: host.id === selectedHostId }" @click="selectHost(host.id)">
        <span class="host-state" :class="host.status === 'online' ? 'online' : 'offline'"></span>
        <span class="host-copy"><b>{{ host.name }}</b><small>{{ host.lan_ip || host.host || host.wireguard_ip || '地址未知' }}</small></span>
        <span class="host-arrow">›</span>
      </button>
      <p v-if="!hosts.length" class="empty-list">暂无可用主机</p>
    </div>
    <button class="manage-hosts" @click="$emit('manage')">管理主机</button>
  </aside>
</template>

<script setup>
import { onMounted } from 'vue'
import { useHostContext } from '../../hostContext'

defineEmits(['manage'])
const { hosts, selectedHostId, refreshHosts, selectHost } = useHostContext()
onMounted(() => { refreshHosts().catch(() => {}) })
</script>

<style scoped>
.host-sidebar{width:184px;min-height:0;flex:none;display:flex;flex-direction:column;background:#fff;border-right:1px solid var(--border)}
.host-sidebar-head{height:38px;padding:0 12px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border);color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.04em}
.host-sidebar-head b{min-width:22px;height:22px;padding:0 6px;border-radius:11px;display:grid;place-items:center;background:#eff6ff;color:var(--primary);font-size:11px}
.host-list{flex:1;min-height:0;overflow:auto;padding:8px 6px;display:flex;flex-direction:column;gap:3px}
.host-item{width:100%;padding:8px 6px;border:1px solid transparent;border-radius:8px;background:transparent;display:flex;align-items:center;gap:7px;text-align:left;color:var(--text);cursor:pointer}
.host-item:hover{background:#f8fafc}.host-item.active{background:#eff6ff;border-color:#bfdbfe}
.host-state{width:7px;height:7px;border-radius:50%;background:#94a3b8;flex:none}.host-state.online{background:var(--ok);box-shadow:0 0 0 2px rgba(34,197,94,.12)}
.host-copy{min-width:0;flex:1;display:flex;flex-direction:column;gap:1px}.host-copy b,.host-copy small{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.host-copy b{font-size:12px}.host-copy small{color:var(--muted);font-size:10px}.host-arrow{color:#94a3b8}
.manage-hosts{height:36px;margin:6px;border:1px solid var(--border);border-radius:7px;background:#fff;color:var(--muted);cursor:pointer;font-size:12px}.manage-hosts:hover{color:var(--primary);border-color:#93c5fd;background:#f8fbff}
.empty-list{margin:8px 6px;color:var(--muted);font-size:12px}
@media(max-width:760px){.host-sidebar{width:156px}.host-copy small{display:none}}
</style>
