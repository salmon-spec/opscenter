import { computed, ref } from 'vue'
import { api } from './api'

const HOST_CACHE_KEY = 'ops-host-cache-v1'
const HOST_CACHE_MAX_AGE = 10 * 60 * 1000

function readHostCache() {
  try {
    const cached = JSON.parse(localStorage.getItem(HOST_CACHE_KEY) || 'null')
    if (!cached?.items || !Array.isArray(cached.items)) return []
    if (Date.now() - Number(cached.time || 0) > HOST_CACHE_MAX_AGE) return []
    return cached.items
  } catch {
    return []
  }
}

const hosts = ref(readHostCache())
const selectedHostId = ref(localStorage.getItem('ops-global-host') || '')
const loading = ref(false)
let loaded = false
let refreshController = null
let refreshPromise = null

const currentHost = computed(() => hosts.value.find((host) => host.id === selectedHostId.value) || null)

async function refreshHosts(force = false) {
  if (loaded && !force) return hosts.value
  if (refreshPromise && !force) return refreshPromise
  if (force) refreshController?.abort()
  const controller = new AbortController()
  refreshController = controller
  loading.value = true
  const promise = (async () => {
    try {
      const list = await api.get('/servers', undefined, { signal: controller.signal })
      hosts.value = Array.isArray(list) ? list : []
      const selected = hosts.value.find((host) => host.id === selectedHostId.value)
        || hosts.value.find((host) => host.status === 'online')
        || hosts.value[0]
      selectedHostId.value = selected?.id || ''
      if (selectedHostId.value) localStorage.setItem('ops-global-host', selectedHostId.value)
      localStorage.setItem(HOST_CACHE_KEY, JSON.stringify({ items: hosts.value, time: Date.now() }))
      loaded = true
      return hosts.value
    } catch (error) {
      if (error.name !== 'AbortError' && !hosts.value.length) throw error
      return hosts.value
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

function selectHost(id) {
  if (!hosts.value.some((host) => host.id === id)) return
  selectedHostId.value = id
  localStorage.setItem('ops-global-host', id)
}

export function useHostContext() {
  return { hosts, selectedHostId, currentHost, loading, refreshHosts, selectHost }
}
