import { trendInterval, trendPoints } from './monitorTrend.js'

export const chartMetrics = [
  { key: 'cpu', label: 'CPU', color: '#e9ac69' },
  { key: 'memory', label: '内存', color: '#83b8e1' },
  { key: 'disk', label: '磁盘', color: '#b19bcf' },
]

export function resourceTrend(series, hours) {
  const step = trendInterval(hours * 3600)
  const lines = chartMetrics.map(metric => ({ ...metric, points: trendPoints(
    (series?.[metric.key] || []).filter(row => Number.isFinite(row[1]) && row[1] >= 0 && row[1] <= 100), step) }))
  const times = lines.flatMap(line => line.points.filter(point => point[1] != null).map(point => point[0]))
  if (!times.length) return { lines: [], start: null, end: null, step }
  const start = Math.min(...times), end = Math.max(...times, start + step * 1000)
  const x = time => 44 + (time - start) / (end - start) * 832
  const y = value => 200 - value * 1.7
  return { start, end, step, lines: lines.map(line => {
    const segments = [[]]
    for (const point of line.points) {
      if (point[1] == null) { segments.push([]); continue }
      segments.at(-1).push({ time: point[0], value: point[1], min: point[2], max: point[3], x: x(point[0]), y: y(point[1]) })
    }
    return { ...line, segments: segments.filter(segment => segment.length) }
  }) }
}

const dateKey = time => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(time))
function eventTime(value) {
  if (!value) return NaN
  const text = String(value)
  return Date.parse(/(Z|[+-]\d{2}:\d{2})$/i.test(text) ? text : text + 'Z')
}

export function dailyAlerts(events, now = Date.now()) {
  const days = Array.from({ length: 7 }, (_, index) => ({ day: dateKey(now - (6 - index) * 86400000), count: 0 }))
  const seen = new Set()
  let omitted = 0
  for (const event of events) {
    if (event.id && seen.has(event.id)) continue
    if (event.id) seen.add(event.id)
    const time = eventTime(event.created_at || event.first_breached_at || event.fired_at)
    if (!Number.isFinite(time)) { omitted++; continue }
    if (time > now) continue
    const bucket = days.find(day => day.day === dateKey(time))
    if (bucket) bucket.count++
  }
  return { days, omitted, total: days.reduce((sum, day) => sum + day.count, 0) }
}
