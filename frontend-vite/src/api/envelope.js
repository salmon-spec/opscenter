/* 统一响应信封工具（v5.0.0）：K8s 监控与数据服务共用。
   信封契约：{ data, data_timestamp, cached, cache_age_seconds, partial_errors, source_status }；
   兼容裸数组/对象（后端旧路径或简化路径）。 */

export function unwrap(res) {
  const envelope = res && typeof res === 'object' && !Array.isArray(res)
    && 'data' in res && ('data_timestamp' in res || 'cached' in res || 'partial_errors' in res)
  if (!envelope) {
    return { data: res ?? null, meta: { ts: 0, cached: false, cacheAge: 0, partialErrors: [], sourceStatus: {} } }
  }
  return {
    data: res.data ?? null,
    meta: {
      ts: Number(res.data_timestamp) || 0,
      cached: !!res.cached,
      cacheAge: Number(res.cache_age_seconds) || 0,
      partialErrors: Array.isArray(res.partial_errors) ? res.partial_errors : [],
      sourceStatus: res.source_status && typeof res.source_status === 'object' ? res.source_status : {},
    },
  }
}

export function mergeMeta(list) {
  const out = { ts: 0, cached: false, cacheAge: 0, partialErrors: [], sourceStatus: {} }
  for (const m of list || []) {
    if (!m) continue
    out.ts = Math.max(out.ts, Number(m.ts) || 0)
    out.cached = out.cached || !!m.cached
    out.cacheAge = Math.max(out.cacheAge, Number(m.cacheAge) || 0)
    if (Array.isArray(m.partialErrors)) out.partialErrors.push(...m.partialErrors)
    Object.assign(out.sourceStatus, m.sourceStatus || {})
  }
  return out
}

export function apiErrorText(err) {
  if (!err) return '未知错误'
  if (err.status === 401) return '需要运维令牌（OPERATOR_TOKEN）或登录'
  if (err.status === 404) return '后端接口未就绪（HTTP 404）'
  if (err.status === 503) return '后端暂不可用（HTTP 503）'
  if (err.name === 'TimeoutError') return '请求超时'
  return err.message || `HTTP ${err.status || '错误'}`
}
