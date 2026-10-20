export const METRIC_STALE_SECONDS = 90
export const HOST_STALE_SECONDS = 300

export function ageSeconds(value, now = Date.now()) {
  if (!value) return null
  let raw = String(value)
  // Backend naive timestamps represent UTC, independent of the browser's local zone.
  if (!/(Z|[+-]\d{2}:\d{2})$/i.test(raw)) raw += 'Z'
  const stamp = Date.parse(raw)
  return Number.isFinite(stamp) && stamp <= now + 30000 ? Math.max(0, (now - stamp) / 1000) : null
}

export function hostView(host, metrics = {}, target = '', now = Date.now(), inventoryOk = true, metricsOk = true) {
  const heartbeatAge = ageSeconds(host.last_seen, now)
  const online = inventoryOk && host.status === 'online' && heartbeatAge != null && heartbeatAge <= HOST_STALE_SECONDS
  const offline = inventoryOk && host.status === 'offline'
  const samples = ['cpu', 'memory', 'disk'].map(key => {
    const sample = metrics[key] || {}
    const age = ageSeconds(sample.last_at, now)
    const value = sample.latest
    const valid = typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
    const fresh = metricsOk && valid && age != null && age <= METRIC_STALE_SECONDS
    return { key, value: valid ? value : null, at: sample.last_at, age, fresh }
  })
  const reasons = []
  if (offline) reasons.push('主机离线')
  else if (!online) reasons.push(inventoryOk ? '主机状态待确认' : '主机状态刷新失败')
  const agentOk = online && host.agent_status === 'running'
  if (online && !agentOk) reasons.push('Agent 异常')
  const versionOk = !!target && host.agent_version === target
  if (!target) reasons.push('目标版本待确认')
  if (!host.agent_version) reasons.push('Agent 版本未知')
  else if (target && !versionOk) reasons.push('Agent 版本待统一')
  if (online && samples.some(s => !s.fresh)) reasons.push(metricsOk ? '指标缺失或超过 90 秒' : '指标刷新失败')
  const pressure = online ? Math.max(-1, ...samples.filter(s => s.fresh).map(s => s.value)) : -1
  if (pressure >= 90) reasons.push('资源使用率 ≥90%')
  else if (pressure >= 80) reasons.push('资源使用率 ≥80%')
  let level = offline || (online && !agentOk) || pressure >= 90 ? 'danger' : reasons.length ? 'warning' : 'healthy'
  if (!online && !offline) level = 'unknown'
  return { ...host, online, offline, agentOk, versionOk, samples, reasons, pressure, level, heartbeatAge,
    metricsFresh: online && samples.every(s => s.fresh),
    label: offline ? '离线' : !online ? '状态待确认' : !agentOk ? 'Agent 异常' : reasons.length ? '需关注' : '正常' }
}

export function sortHosts(hosts) {
  const rank = h => h.offline ? 0 : ({ danger: 1, unknown: 2, warning: 3, healthy: 4 })[h.level]
  return [...hosts].sort((a,b) => rank(a) - rank(b) || a.name.localeCompare(b.name, 'zh-CN'))
}
