import { ageSeconds } from './hostScreen.js'

// A transport success does not guarantee a current collector snapshot.
export function sourceState(source, now = Date.now()) {
  if (!source.value) return { usable: false, label: source.error ? '读取失败' : '读取中', level: 'unknown' }
  const age = ageSeconds(source.value.collected_at, now)
  if (source.error) return { usable: false, label: '刷新失败 · 上次数据', level: 'warning' }
  if (source.value.status === 'unavailable') return { usable: false, label: '数据源不可用', level: 'unknown' }
  if (age == null || age > (source.value.stale_after_seconds || 120)) return { usable: false, label: '数据已过期', level: 'warning' }
  if (source.value.error) return { usable: false, label: '采集异常 · 上次数据', level: 'warning' }
  return { usable: true, label: source.value.status === 'degraded' ? '需关注' : '正常', level: source.value.status === 'degraded' ? 'warning' : 'healthy' }
}

export function orderFindings(items = []) {
  const rank = { critical: 0, warning: 1, info: 2 }
  return [...new Map(items.map(item => [item.id, item])).values()].sort((a, b) =>
    (rank[a.severity] ?? 3) - (rank[b.severity] ?? 3) || (Date.parse(b.observed_at) || 0) - (Date.parse(a.observed_at) || 0) || a.id.localeCompare(b.id))
}

export function podSegments(data) {
  const { total, running, failed } = data?.pods || {}
  if (![total, running, failed].every(n => Number.isInteger(n) && n >= 0) || total < running + failed) return []
  return [{ key: 'running', label: 'Running', value: running }, { key: 'failed', label: 'Failed', value: failed },
    { key: 'other', label: '其他阶段', value: total - running - failed }]
}
