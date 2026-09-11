/* OpsCenter v3.29 API 客户端：同源 /api/v2（开发期由 Vite 代理到后端） */

const API_BASE = import.meta.env.VITE_API_BASE || ''
const TOKEN_KEY = 'ops-access-token'

export const getAccessToken = () => localStorage.getItem(TOKEN_KEY) || ''
export const setAccessToken = (token) => localStorage.setItem(TOKEN_KEY, token || '')
export const clearAccessToken = () => localStorage.removeItem(TOKEN_KEY)

async function request(path, { method = 'GET', body, query, signal, timeoutMs } = {}) {
  let url = API_BASE + '/api/v2' + path
  if (query) {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== '') p.append(k, v)
    }
    const qs = p.toString()
    if (qs) url += '?' + qs
  }
  const timeoutController = timeoutMs ? new AbortController() : null
  const abort = () => timeoutController?.abort()
  if (signal?.aborted) abort()
  else if (signal) signal.addEventListener('abort', abort, { once: true })
  const timer = timeoutController ? setTimeout(abort, timeoutMs) : null
  const opts = { method, headers: {}, signal: timeoutController?.signal || signal }
  const token = getAccessToken()
  if (token) opts.headers.Authorization = `Bearer ${token}`
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  try {
    const res = await fetch(url, opts)
    let data = null
    try { data = await res.json() } catch { /* 非 JSON 响应忽略 */ }
    if (!res.ok) {
      if (res.status === 401 && path !== '/auth/login') {
        clearAccessToken()
        window.dispatchEvent(new CustomEvent('ops-auth-required'))
      }
      const err = new Error((data && (data.detail || data.msg)) || `HTTP ${res.status}`)
      err.status = res.status
      err.data = data
      throw err
    }
    return data
  } catch (err) {
    if (timeoutController?.signal.aborted && !signal?.aborted) {
      const timeoutError = new Error(`请求超时（${timeoutMs}ms）`)
      timeoutError.name = 'TimeoutError'
      throw timeoutError
    }
    throw err
  } finally {
    if (timer) clearTimeout(timer)
    if (signal) signal.removeEventListener('abort', abort)
  }
}

export const api = {
  get: (path, query, options = {}) => request(path, { query, signal: options.signal, timeoutMs: options.timeoutMs }),
  post: (path, body, options = {}) => request(path, { method: 'POST', body, query: options.query, signal: options.signal, timeoutMs: options.timeoutMs }),
  put: (path, body, options = {}) => request(path, { method: 'PUT', body, query: options.query, signal: options.signal, timeoutMs: options.timeoutMs }),
  patch: (path, body, options = {}) => request(path, { method: 'PATCH', body, query: options.query, signal: options.signal, timeoutMs: options.timeoutMs }),
  del: (path, options = {}) => request(path, { method: 'DELETE', query: options.query, signal: options.signal, timeoutMs: options.timeoutMs }),
}

/* WebSocket 地址：与页面同源（开发期由 Vite 代理 /ws） */
export function wsUrl(path) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const url = new URL(`${proto}://${location.host}${path}`)
  const token = getAccessToken()
  if (token) url.searchParams.set('token', token)
  return url.toString()
}

/* 通用 toast（全局事件总线，App.vue 监听渲染） */
export function toast(msg, type = 'info') {
  window.dispatchEvent(new CustomEvent('ops-toast', { detail: { msg, type } }))
}

/* 格式化 */
export function fmtBytes(b) {
  if (b === null || b === undefined || isNaN(b)) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = Number(b)
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

export function fmtDuration(seconds) {
  if (!seconds || seconds < 0) return '-'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}天${h}小时`
  if (h > 0) return `${h}小时${m}分`
  return `${m}分钟`
}

export function fmtTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
