import { computed, ref } from 'vue'
import { api } from './api'

const hosts = ref([])
const selectedHostId = ref(localStorage.getItem('ops-global-host') || '')
const loading = ref(false)
let loaded = false

const currentHost = computed(() => hosts.value.find((host) => host.id === selectedHostId.value) || null)

async function refreshHosts(force = false) {
  if (loading.value || (loaded && !force)) return hosts.value
  loading.value = true
  try {
    const list = await api.get('/servers')
    hosts.value = Array.isArray(list) ? list : []
    const selected = hosts.value.find((host) => host.id === selectedHostId.value)
      || hosts.value.find((host) => host.status === 'online')
      || hosts.value[0]
    selectedHostId.value = selected?.id || ''
    if (selectedHostId.value) localStorage.setItem('ops-global-host', selectedHostId.value)
    loaded = true
    return hosts.value
  } finally {
    loading.value = false
  }
}

function selectHost(id) {
  if (!hosts.value.some((host) => host.id === id)) return
  selectedHostId.value = id
  localStorage.setItem('ops-global-host', id)
}

export function useHostContext() {
  return { hosts, selectedHostId, currentHost, loading, refreshHosts, selectHost }
}
