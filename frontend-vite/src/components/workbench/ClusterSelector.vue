<template>
  <div class="global-cluster">
    <span class="dot" :class="dotClass"></span>
    <select :value="selectedClusterId" aria-label="选择集群" @change="selectCluster($event.target.value)">
      <option v-if="!clusters.length" value="" disabled>{{ error ? '加载失败' : '选择集群' }}</option>
      <option v-for="cluster in clusters" :key="cluster.id" :value="cluster.id">{{ cluster.name }}{{ cluster.version ? ' · ' + cluster.version : '' }}</option>
    </select>
    <button v-if="error" class="btn btn-sm" @click="retry">重试</button>
  </div>
</template>

<script setup>
/* 顶栏条件式集群选择器（需求基线 §6）：由 App.vue 按 route.meta.clusterScope==='required' 控制显示。
   数据源 workbench/clusters.js，与主机选择完全独立；后端 /clusters 未就绪时空列表 + 重试，不白屏。 */
import { computed, onMounted, ref } from 'vue'
import { useClusterContext } from '../../workbench/clusters'

const { clusters, selectedClusterId, currentCluster, refreshClusters, selectCluster } = useClusterContext()
const error = ref('')
const dotClass = computed(() => (currentCluster.value?.status === 'ready' ? 'ok' : 'warn'))

async function load(force) {
  error.value = ''
  try {
    await refreshClusters(force)
  } catch (e) {
    error.value = e?.message || '加载失败'
  }
}
function retry() { load(true) }
onMounted(() => { load(false) })
</script>

<style scoped>
.global-cluster { display: flex; align-items: center; gap: 7px; border: 1px solid var(--border); border-radius: 8px; padding: 4px 5px 4px 9px; background: var(--card); }
.global-cluster select { border: 0; background: transparent; color: var(--text); outline: none; max-width: 250px; }
.global-cluster .dot { flex: none; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.dot.ok { background: var(--ok); }
.dot.warn { background: var(--warn); }
@media (max-width: 768px) { .global-cluster select { max-width: 130px; } }
</style>
