import { Capacitor, CapacitorHttp } from '@capacitor/core'
import { SecureStorage } from '@aparajita/capacitor-secure-storage'

const TOKEN_KEY = 'ops-access-token'
const configuredBase = import.meta.env.VITE_MOBILE_API_BASE?.trim() || ''

function apiBase() {
  if (!configuredBase) throw new Error('尚未配置受信的 HTTPS 工作台地址')
  let url
  try {
    url = new URL(configuredBase)
  } catch {
    throw new Error('工作台地址格式无效')
  }
  if (url.protocol !== 'https:') throw new Error('手机端仅允许 HTTPS 工作台地址')
  if (url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('工作台配置必须是 HTTPS 源地址，不能包含账号或路径')
  }
  return url.origin
}

export function apiConfigError() {
  try {
    apiBase()
    return ''
  } catch (error) {
    return error.message
  }
}

let browserToken = ''

export async function getToken() {
  if (!Capacitor.isNativePlatform()) return browserToken
  return (await SecureStorage.get(TOKEN_KEY)) || ''
}

export async function setToken(token) {
  if (!Capacitor.isNativePlatform()) {
    browserToken = token
    return
  }
  await SecureStorage.set(TOKEN_KEY, token)
}

export async function clearToken() {
  if (!Capacitor.isNativePlatform()) {
    browserToken = ''
    return
  }
  await SecureStorage.remove(TOKEN_KEY)
}

export async function request(path, { method = 'GET', body, token = '', timeoutMs = 15000 } = {}) {
  const native = Capacitor.isNativePlatform()
  const controller = native ? null : new AbortController()
  const timer = native ? null : setTimeout(() => controller.abort(), timeoutMs)
  const url = `${apiBase()}${path}`
  try {
    const headers = {}
    if (token) headers.Authorization = `Bearer ${token}`
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    let status
    let data
    if (native) {
      const response = await CapacitorHttp.request({
        url,
        method,
        headers,
        data: body,
        connectTimeout: timeoutMs,
        readTimeout: timeoutMs,
        disableRedirects: true,
      })
      status = response.status
      data = response.data
      if (typeof data === 'string') data = JSON.parse(data)
    } else {
      const response = await fetch(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        redirect: 'error',
        signal: controller.signal,
      })
      status = response.status
      data = await response.json().catch(() => null)
    }
    if (status < 200 || status >= 300) {
      const error = new Error(status === 401 ? '账号或会话无效' : `请求失败（HTTP ${status}）`)
      error.status = status
      throw error
    }
    return data
  } catch (error) {
    if (controller?.signal.aborted || /timed?\s*out|timeout/i.test(error?.message || '')) {
      throw new Error('请求超时，请检查 VPN 和网络后重试')
    }
    if (native || error instanceof TypeError) throw new Error('无法连接工作台，请检查 VPN、地址和证书')
    throw error
  } finally {
    if (timer) clearTimeout(timer)
  }
}
