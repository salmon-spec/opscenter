// Display buckets follow the selected duration; source records remain unchanged.
export function trendInterval(seconds) {
  return [30, 120, 300, 1800, 7200, 21600, 86400, 604800].find(step => seconds / step <= 366) || Math.ceil(seconds / 366)
}

export function trendPoints(rows, step) {
  const buckets = new Map()
  for (const [time, avg, min = avg, max = avg, count = 1] of rows) {
    if (!Number.isFinite(time) || !Number.isFinite(avg) || !(count > 0)) continue
    const at = Math.floor(time / step) * step
    const bucket = buckets.get(at) || { sum: 0, count: 0, min: Infinity, max: -Infinity }
    bucket.sum += avg * count
    bucket.count += count
    bucket.min = Math.min(bucket.min, min ?? avg)
    bucket.max = Math.max(bucket.max, max ?? avg)
    buckets.set(at, bucket)
  }
  const result = []
  for (const [at, bucket] of [...buckets].sort((a, b) => a[0] - b[0])) {
    if (result.length && at * 1000 - result.at(-1)[0] > step * 1500) result.push([at * 1000 - step * 1000, null])
    result.push([at * 1000, bucket.sum / bucket.count, bucket.min, bucket.max, bucket.count])
  }
  return result
}

export function intervalLabel(seconds) {
  return seconds < 60 ? `${seconds}秒` : seconds < 3600 ? `${seconds / 60}分钟` : seconds < 86400 ? `${seconds / 3600}小时` : `${Math.round(seconds / 86400)}天`
}
