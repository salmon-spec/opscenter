/* OpsCenter v4.9 集群上下文（镜像 hostContext.js 模式）。
   与主机选择完全独立：localStorage 'ops-cluster-v1'，切换一方不影响另一方（需求基线 §6）。
   后端 GET /api/v2/clusters 由另一 agent 并行实现；未就绪时 refreshClusters 抛错，
   调用方（ClusterSelector / K3s 页面）需 catch 并提供空列表 + 重试降级，不白屏。 */
import { computed, ref } from 'vue'
import { api } from '../api'

const clusters = ref([])
const selectedClusterId = ref(localStorage.getItem('ops-cluster-v1') || '')
const loading = ref(false)
let loaded = false
let refreshController = null
let refreshPromise = null

const currentCluster = computed(() => clusters.value.find((cluster) => cluster.id === selectedClusterId.value) || null)

async function refreshClusters(force = false) {
  if (loaded && !force) return clusters.value
  if (refreshPromise && !force) return refreshPromise
  if (force) refreshController?.abort()
  const controller = new AbortController()
  refreshController = controller
  loading.value = true
  const promise = (async () => {
    try {
      const list = await api.get('/clusters', undefined, { signal: controller.signal, timeoutMs: 8000 })
      clusters.value = Array.isArray(list) ? list : []
      loaded = true
      if (!clusters.value.some((cluster) => cluster.id === selectedClusterId.value)) {
        selectedClusterId.value = clusters.value[0]?.id || ''
        if (selectedClusterId.value) localStorage.setItem('ops-cluster-v1', selectedClusterId.value)
      }
      return clusters.value
    } catch (error) {
      if (error.name !== 'AbortError') throw error
      return clusters.value
    } finally {
      if (refreshController === controller) {
        loading.value = false
        refreshPromise = null
      }
    }
  })()
  refreshPromise = promise
  return refreshPromise
}

function selectCluster(id) {
  if (!clusters.value.some((cluster) => cluster.id === id)) return
  selectedClusterId.value = id
  localStorage.setItem('ops-cluster-v1', id)
}

export function useClusterContext() {
  return { clusters, selectedClusterId, currentCluster, loading, refreshClusters, selectCluster }
}
