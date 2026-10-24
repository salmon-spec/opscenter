export const sources = { host: '主机', service: '服务', certificate: '证书', k3s: 'K3s', pve: 'PVE', electricity: '电费', datasource: '数据源' }
export const levels = { critical: '严重', warning: '警告', info: '提示' }
export const states = { pending: '观察中', firing: '触发中', recovered: '已恢复', cancelled: '已结束' }
export const handling = { unacked: '未确认', acked: '已确认', closed: '已完成' }
export const deliveryStates = { queued: '等待发送', sending: '发送中', accepted: '平台已接受', retry: '等待重试', failed: '发送失败', uncertain: '结果待核对', skipped: '已跳过', previewed: '仅预览' }
export const channels = { inapp: '站内', webhook: 'Webhook', pushplus: 'PushPlus', sms: '短信' }
export function formatValue(event) {
  const value = event.current_value
  if (value === null || value === undefined) return '—'
  if (['cpu','memory','disk','pve_storage_usage'].includes(event.metric)) return `${value}%`
  if (event.metric === 'cert_days_left') return `${value} 天`
  return {up:'在线',down:'离线',online:'在线',offline:'离线',running:'运行',stopped:'停止',unknown:'未知'}[value] || value
}
export function duration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '—'
  const value = Math.max(0, Math.round(seconds))
  if (value < 60) return `${value} 秒`
  if (value < 3600) return `${Math.floor(value / 60)} 分钟`
  if (value < 86400) return `${Math.floor(value / 3600)} 小时${value % 3600 >= 60 ? ` ${Math.floor(value % 3600 / 60)} 分` : ''}`
  return `${Math.floor(value / 86400)} 天 ${Math.floor(value % 86400 / 3600)} 小时`
}
export function eventDuration(event) {
  const start = Date.parse(event.fired_at || event.first_breached_at || event.created_at)
  return duration(((event.recovered_at || event.ended_at ? Date.parse(event.recovered_at || event.ended_at) : Date.now()) - start) / 1000)
}
