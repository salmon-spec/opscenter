<template>
  <div class="module-host">
    <div v-if="loading" class="center-state"><span class="spinner"></span>正在加载模块</div>
    <div v-else-if="loadError" class="center-state error-state">
      <b>模块加载失败</b>
      <span>{{ loadError }}</span>
      <button class="btn" @click="loadModule">重试</button>
    </div>
    <div v-else-if="!module?.running" class="center-state">
      <b>模块入口当前已隐藏</b>
      <span>启用入口后会显示在侧边栏。页面服务状态请以模块健康检查为准。</span>
      <button v-if="!fixedKey" class="btn btn-primary" :disabled="starting" @click="startModule">{{ starting ? '启用中…' : '显示模块入口' }}</button>
    </div>
    <iframe
      v-else
      ref="frame"
      class="module-frame"
      :sandbox="sandboxValue"
      :src="module.entry_url"
      allow=""
      referrerpolicy="no-referrer"
      :title="module.name"
      @load="announceModule({ source: frame?.contentWindow })"
    ></iframe>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, toast } from '../api'

const props = defineProps({ fixedKey: { type: String, default: '' }, fixedName: { type: String, default: '' } })
const route = useRoute()
const moduleKey = computed(() => props.fixedKey || String(route.params.moduleKey || ''))
const frame = ref(null)
const module = ref(null)
const loading = ref(true)
const loadError = ref('')
const starting = ref(false)
let loadRevision = 0
let frameNonce = ''

function newNonce() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return [...bytes].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

const sandboxValue = computed(() => {
  const requested = Array.isArray(module.value?.manifest?.sandbox)
    ? module.value.manifest.sandbox
    : ['allow-scripts', 'allow-downloads']
  const safe = requested.filter((item) => ['allow-scripts', 'allow-downloads', 'allow-forms'].includes(item))
  return [...new Set(safe)].join(' ')
})

async function loadModule() {
  const revision = ++loadRevision
  const requestedKey = moduleKey.value
  loading.value = true
  loadError.value = ''
  module.value = null
  frameNonce = newNonce()
  starting.value = false
  try {
    const result = props.fixedKey
      ? await api.get(`/modules/${requestedKey}/api/v1/module`).then((info) => ({
        key: requestedKey, name: props.fixedName || requestedKey, version: info.version,
        description: '独立业务模块', running: true,
        entry_url: `/modules/${requestedKey}/ui/`,
        manifest: { sandbox: ['allow-scripts'], api_prefixes: [`/modules/${requestedKey}/api/v1/`] },
      }))
      : (await api.get('/modules')).items?.find((item) => item.key === requestedKey)
    if (!result) throw new Error('扩展模块不存在')
    if (revision === loadRevision) module.value = result
  } catch (error) {
    if (revision === loadRevision) loadError.value = error.message
  } finally {
    if (revision === loadRevision) loading.value = false
  }
}

async function startModule() {
  const revision = loadRevision
  const requestedKey = moduleKey.value
  starting.value = true
  try {
    const result = await api.post(`/modules/${requestedKey}/start`)
    if (requestedKey !== moduleKey.value || revision !== loadRevision) return
    module.value = result
    window.dispatchEvent(new CustomEvent('ops-modules-changed'))
    toast(`${module.value.name} 入口已显示`, 'ok')
  } catch (error) {
    if (revision === loadRevision) toast(error.message, 'err')
  } finally {
    if (revision === loadRevision) starting.value = false
  }
}

function normalizePolicyPath(value) {
  let decoded = String(value || '')
  for (let index = 0; index < 2; index += 1) {
    try {
      const next = decodeURIComponent(decoded)
      if (next === decoded) break
      decoded = next
    } catch {
      return ''
    }
  }
  if (!decoded.startsWith('/') || decoded.includes('\\') || decoded.includes('\0') || decoded.includes('?') || decoded.includes('#')) return ''
  try {
    return new URL(decoded, window.location.origin).pathname
  } catch {
    return ''
  }
}

function normalizeModulePrefix(prefix) {
  if (typeof prefix !== 'string' || !prefix.startsWith('/')) return ''
  const moduleKeyValue = String(module.value?.key || '')
  if (!moduleKeyValue) return ''
  try {
    const url = new URL(prefix, window.location.origin)
    if (url.origin !== window.location.origin || url.search || url.hash) return ''
    const normalized = normalizePolicyPath(url.pathname)
    const requiredPrefix = `/modules/${moduleKeyValue}/`
    return normalized.startsWith(requiredPrefix) ? normalized : ''
  } catch {
    return ''
  }
}

function resolveAllowedPath(path) {
  const raw = String(path || '')
  if (!raw || raw.startsWith('//') || raw.includes('\\')) return ''
  const prefixes = module.value?.manifest?.api_prefixes
  if (!Array.isArray(prefixes) || !prefixes.length) return ''
  try {
    const url = new URL(raw, window.location.origin)
    if (url.origin !== window.location.origin || url.search || url.hash) return ''
    const normalized = normalizePolicyPath(url.pathname)
    if (!normalized) return ''
    const allowed = prefixes
      .map(normalizeModulePrefix)
      .filter(Boolean)
      .map((prefix) => prefix.endsWith('/') ? prefix : `${prefix}/`)
    return allowed.some((prefix) => normalized.startsWith(prefix)) ? normalized : ''
  } catch {
    return ''
  }
}

function reply(id, ok, data) {
  frame.value?.contentWindow?.postMessage({
    type: 'ops-module-api-response',
    nonce: frameNonce,
    id,
    ok,
    data: ok ? data : { message: String(data?.message || data || '模块请求失败') },
  }, '*')
}

async function handleApiRequest(data) {
  if (props.fixedKey && data.nonce !== frameNonce) return
  const revision = loadRevision
  const id = String(data.id || '')
  const path = resolveAllowedPath(data.path)
  if (!id || !path) return reply(id, false, { message: '模块无权访问该接口' })
  const method = String(data.method || 'GET').toUpperCase()
  if (!['GET', 'POST'].includes(method)) return reply(id, false, { message: '模块仅允许只读 API 请求' })
  const requestedTimeout = Number(data.timeoutMs)
  const timeoutMs = Number.isFinite(requestedTimeout)
    ? Math.min(Math.max(requestedTimeout, 1000), 120000)
    : 20000
  try {
    const result = method === 'GET'
      ? await api.get(path, data.query || {}, { timeoutMs })
      : await api.post(path, data.body ?? {}, { query: data.query || {}, timeoutMs })
    if (revision === loadRevision) reply(id, true, result)
  } catch (error) {
    if (revision === loadRevision) reply(id, false, { message: error.message, status: error.status })
  }
}

async function handleDownload(data) {
  if (props.fixedKey && data.nonce !== frameNonce) return
  const revision = loadRevision
  const id = String(data.id || '')
  const path = resolveAllowedPath(data.path)
  if (!id || !path) return reply(id, false, { message: '模块无权下载该对象' })
  try {
    const blob = await api.download(path, data.query || {}, { timeoutMs: 120000 })
    if (revision !== loadRevision) return
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
    if (revision === loadRevision) reply(id, false, { message: error.message, status: error.status })
  }
}

function announceModule(event) {
  const target = frame.value?.contentWindow
  if (!target || event.source !== target || !module.value) return
  const info = JSON.parse(JSON.stringify({
    key: module.value.key,
    name: module.value.name,
    version: module.value.version,
    manifest: module.value.manifest || {},
  }))
  target.postMessage({
    type: 'ops-module-info',
    nonce: frameNonce,
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
watch(moduleKey, () => loadModule())
onUnmounted(() => {
  loadRevision += 1
  window.removeEventListener('message', onMessage)
})
</script>

<style scoped>
.module-host { height: 100%; min-height: 620px; display: flex; flex-direction: column; background: #eef2f7; }
.module-frame { width: 100%; flex: 1; min-height: 0; border: 0; background: #f4f6fa; }
.center-state { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--muted); text-align: center; padding: 24px; }
.center-state b { color: var(--text); font-size: 16px; }
.error-state { color: var(--err); }
</style>
