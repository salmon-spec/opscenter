import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { trendInterval, trendPoints, intervalLabel } from '../src/utils/monitorTrend.js'

const source = readFileSync(new URL('../src/views/SystemMonitor.vue', import.meta.url), 'utf8')
function mountMonitor() {
  const hooks = {}, calls = [], charts = [], observers = [], intervals = new Map()
  const host = { value: 'host-1' }, active = { value: true }
  const api = { get: async (path, query) => {
    calls.push({ path, query })
    if (path.endsWith('/timeseries')) return { series: { cpu: [[Date.now() / 1000, 25]] }, resolution: 'raw', point_count: 1 }
    if (path.endsWith('/overview')) return { hosts: [] }
    return { metrics: { cpu: 25 }, timestamp: Date.now() / 1000 }
  } }
  const ref = value => ({ value }), computed = fn => ({ get value() { return fn() } })
  const bindings = {
    ref, computed, nextTick: async fn => fn?.(), watch: () => {},
    onMounted: fn => { hooks.mount = fn }, onActivated: fn => { hooks.activate = fn },
    onDeactivated: fn => { hooks.deactivate = fn }, onUnmounted: fn => { hooks.unmount = fn },
    useHostContext: () => ({ selectedHostId: host, currentHost: ref({ name: 'node' }), refreshHosts: async () => {}, selectHost: () => {} }),
    useTabActive: () => ({ isActive: active }), api, trendInterval, trendPoints, intervalLabel,
    fmtBytes: String, fmtDuration: String, fmtTime: String,
    document: { hidden: false, addEventListener() {}, removeEventListener() {} },
    window: { addEventListener() {}, removeEventListener() {} },
    ResizeObserver: class { constructor(fn) { this.resize = fn; observers.push(this) } observe() {} disconnect() { this.disconnected = true } },
    setInterval: fn => { const id = Symbol(); intervals.set(id, fn); return id }, clearInterval: id => intervals.delete(id),
    echarts: { init: () => { const chart = { options: [], resizes: 0, setOption(option) { this.options.push(option) }, resize() { this.resizes++ }, dispose() {} }; charts.push(chart); return chart } },
  }
  const code = source.match(/<script setup>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
  const state = Function(...Object.keys(bindings), code + '\nreturn {chartEl,overviewEl,rangeKey,customStart,customEnd,renderChart};')(...Object.values(bindings))
  return { state, hooks, calls, charts, observers, active, intervals }
}
const flush = () => new Promise(resolve => setImmediate(resolve))

test('returning within one minute fetches current summary and a new rolling history window', async () => {
  const m = mountMonitor()
  await m.hooks.mount(); await flush()
  assert.equal(m.calls.length, 3)
  assert.equal(m.calls[0].query.refresh, true)
  const firstEnd = m.calls.find(c => c.path.endsWith('/timeseries')).query.end
  m.hooks.deactivate(); assert.equal(m.intervals.size, 0)
  await new Promise(resolve => setTimeout(resolve, 5))
  m.calls.length = 0
  m.hooks.activate(); await flush()
  assert.equal(m.calls.length, 3)
  assert.ok(m.calls.find(c => c.path.endsWith('/timeseries')).query.end > firstEnd)
  m.hooks.activate(); assert.equal(m.calls.length, 3, 'duplicate activation hooks must not create duplicate requests')
  m.hooks.unmount()
})

test('returning reveals the whole chart, resets zoom, and resizes with the container', async () => {
  const m = mountMonitor(); let scrollTop = 500
  m.state.chartEl.value = { clientWidth: 1000 }
  m.state.overviewEl.value = { closest: () => ({ scrollTo: ({ top }) => { scrollTop = top } }) }
  await m.hooks.mount(); await flush()
  assert.equal(scrollTop, 0)
  assert.ok(m.charts[0].options.length)
  assert.ok(m.charts[0].options.at(-1).dataZoom.every(z => z.start === 0 && z.end === 100))
  const before = m.charts[0].resizes; m.observers[0].resize()
  assert.equal(m.charts[0].resizes, before + 1)
  m.hooks.unmount(); assert.equal(m.observers[0].disconnected, true)
})
