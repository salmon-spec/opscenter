<template>
  <div class="module-host">
    <div class="host-bar">
      <div class="host-identity">
        <span class="host-icon">{{ module?.icon || '◇' }}</span>
        <div><b>{{ module?.name || moduleKey }}</b><small>{{ module?.description || '扩展模块' }}</small></div>
      </div>
      <div class="host-meta">
        <span class="state" :class="module?.running ? 'running' : 'stopped'"><i></i>{{ module?.running ? '隔离运行中' : '已停止' }}</span>
        <span v-if="module?.version" class="muted">v{{ module.version }}</span>
        <router-link class="btn btn-sm" to="/modules">模块管理</router-link>
      </div>
    </div>

    <div v-if="loading" class="center-state"><span class="spinner"></span>正在加载模块</div>
    <div v-else-if="loadError" class="center-state error-state">
      <b>模块加载失败</b>
      <span>{{ loadError }}</span>
      <button class="btn" @click="loadModule">重试</button>
    </div>
    <div v-else-if="!module?.running" class="center-state">
      <b>模块当前未启动</b>
      <span>启动后模块会在受控 iframe 中运行，并出现在侧边栏。</span>
      <button class="btn btn-primary" :disabled="starting" @click="startModule">{{ starting ? '启动中…' : '启动模块' }}</button>
    </div>
    <iframe
      v-else
      ref="frame"
      class="module-frame"
      :src="module.entry_url"
      :sandbox="sandboxValue"
      allow=""
      referrerpolicy="no-referrer"
      :title="module.name"
      @load="announceModule"
    ></iframe>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api, toast } from '../api'

const route = useRoute()
const moduleKey = computed(() => String(route.params.moduleKey || ''))
const frame = ref(null)
const module = ref(null)
const loading = ref(true)
const loadError = ref('')
const starting = ref(false)

const sandboxValue = computed(() => {
  const requested = Array.isArray(module.value?.manifest?.sandbox)
    ? module.value.manifest.sandbox
    : ['allow-scripts', 'allow-downloads']
  const safe = requested.filter((item) => ['allow-scripts', 'allow-downloads', 'allow-forms'].includes(item))
  return [...new Set(safe)].join(' ')
})

async function loadModule() {
  loading.value = true
  loadError.value = ''
  try {
    module.value = await api.get(`/modules/${moduleKey.value}`)
  } catch (error) {
    loadError.value = error.message
  } finally {
    loading.value = false
  }
}

async function startModule() {
  starting.value = true
  try {
    module.value = await api.post(`/modules/${moduleKey.value}/start`)
    window.dispatchEvent(new CustomEvent('ops-modules-changed'))
    toast(`${module.value.name} 已启动`, 'ok')
  } catch (error) {
    toast(error.message, 'err')
  } finally {
    starting.value = false
  }
}

function allowedPath(path) {
  if (!path || path.startsWith('//') || path.includes('..') || path.includes('\\')) return false
  const prefixes = module.value?.manifest?.api_prefixes
  if (!Array.isArray(prefixes) || !prefixes.length) return false
  return prefixes.some((prefix) => typeof prefix === 'string' && prefix.startsWith('/') && path.startsWith(prefix))
}

function reply(id, ok, data) {
  frame.value?.contentWindow?.postMessage({
    type: 'ops-module-api-response',
    id,
    ok,
    data: ok ? data : { message: String(data?.message || data || '模块请求失败') },
  }, '*')
}

async function handleApiRequest(data) {
  const id = String(data.id || '')
  if (!id || !allowedPath(String(data.path || ''))) return reply(id, false, { message: '模块无权访问该接口' })
  const method = String(data.method || 'GET').toUpperCase()
  if (!['GET', 'POST'].includes(method)) return reply(id, false, { message: '模块仅允许只读 API 请求' })
  try {
    const result = method === 'GET'
      ? await api.get(data.path, data.query || {}, { timeoutMs: 20000 })
      : await api.post(data.path, data.body ?? {}, { query: data.query || {}, timeoutMs: 20000 })
    reply(id, true, result)
  } catch (error) {
    reply(id, false, { message: error.message, status: error.status })
  }
}

async function handleDownload(data) {
  const id = String(data.id || '')
  const path = String(data.path || '')
  if (!id || !allowedPath(path)) return reply(id, false, { message: '模块无权下载该对象' })
  try {
    const blob = await api.download(path, data.query || {}, { timeoutMs: 120000 })
    const objectUrl = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = objectUrl
    anchor.download = String(data.filename || 'download')
    anchor.style.display = 'none'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(objectUrl), 30000)
    reply(id, true, { downloaded: true })
  } catch (error) {
    reply(id, false, { message: error.message, status: error.status })
  }
}

function announceModule(event) {
  if (event.source !== frame.value?.contentWindow || !module.value) return
  const info = JSON.parse(JSON.stringify({
    key: module.value.key,
    name: module.value.name,
    version: module.value.version,
    manifest: module.value.manifest || {},
  }))
  frame.value.contentWindow.postMessage({
    type: 'ops-module-info',
    module: info,
  }, '*')
}

function onMessage(event) {
  if (!frame.value || event.source !== frame.value.contentWindow) return
  const data = event.data || {}
  if (data.type === 'ops-module-ready') {
    announceModule({ source: frame.value.contentWindow })
  } else if (data.type === 'ops-module-api-request') {
    handleApiRequest(data)
  } else if (data.type === 'ops-module-download-request') {
    handleDownload(data)
  }
}

onMounted(() => {
  window.addEventListener('message', onMessage)
  loadModule()
})
onUnmounted(() => window.removeEventListener('message', onMessage))
</script>

<style scoped>
.module-host { height: 100%; min-height: 620px; display: flex; flex-direction: column; background: #eef2f7; }
.host-bar { min-height: 54px; flex: none; display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 8px 14px; background: #fff; border-bottom: 1px solid var(--border); }
.host-identity { min-width: 0; display: flex; align-items: center; gap: 10px; }
.host-icon { width: 34px; height: 34px; flex: none; display: grid; place-items: center; border-radius: 8px; background: #eff6ff; color: var(--brand); font-size: 18px; }
.host-identity b, .host-identity small { display: block; }
.host-identity small { color: var(--muted); margin-top: 2px; max-width: 560px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.host-meta { display: flex; align-items: center; gap: 10px; white-space: nowrap; }
.state { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; }
.state i { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
.state.running { color: var(--ok); }
.state.running i { background: var(--ok); }
.state.stopped { color: var(--muted); }
.module-frame { width: 100%; flex: 1; min-height: 0; border: 0; background: #f4f6fa; }
.center-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--muted); text-align: center; padding: 24px; }
.center-state b { color: var(--text); font-size: 16px; }
.error-state { color: var(--err); }
@media (max-width: 720px) {
  .host-bar { align-items: flex-start; padding: 9px 10px; }
  .host-identity small, .host-meta > .muted { display: none; }
  .host-meta { gap: 6px; }
}
</style>
