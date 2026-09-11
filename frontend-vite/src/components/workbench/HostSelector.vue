<template>
  <div class="global-host">
    <span class="dot" :class="currentHost?.status === 'online' ? 'ok' : 'warn'"></span>
    <select :value="selectedHostId" aria-label="选择主机" @change="selectHost($event.target.value)">
      <option v-if="!hosts.length" value="" disabled>选择主机</option>
      <option v-for="host in hosts" :key="host.id" :value="host.id">{{ host.name }} · {{ host.host }}</option>
    </select>
    <button class="btn btn-sm" @click="$emit('manage')">管理主机</button>
  </div>
</template>

<script setup>
/* 顶栏条件式主机选择器（需求基线 §6）：由 App.vue 按 route.meta.hostScope==='required' 控制显示。
   样式与交互原样迁移自 App.vue 顶栏 .global-host（scoped 样式随组件走）。 */
import { onMounted } from 'vue'
import { useHostContext } from '../../hostContext'

defineEmits(['manage'])
const { hosts, selectedHostId, currentHost, refreshHosts, selectHost } = useHostContext()
onMounted(() => { refreshHosts().catch(() => {}) })
</script>

<style scoped>
.global-host { display: flex; align-items: center; gap: 7px; border: 1px solid var(--border); border-radius: 8px; padding: 4px 5px 4px 9px; background: var(--card); }
.global-host select { border: 0; background: transparent; color: var(--text); outline: none; max-width: 250px; }
.global-host .dot { flex: none; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot.ok { background: var(--ok); }
.dot.warn { background: var(--warn); }
@media (max-width: 768px) { .global-host select { max-width: 130px; } }
</style>
