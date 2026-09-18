<template>
  <div class="screen-root" ref="screenRoot">
    <header class="screen-head">
      <div>
        <h1 class="screen-title">OpsCenter 健康大屏</h1>
        <div class="screen-sub">
          核心数据每 10 秒刷新 · 页面更新 {{ lastRefresh }} · 最旧主机数据 {{ lastDataAt }}<template v-if="dataTimestamp"> · 数据时间 {{ fmtTime(dataTimestamp) }}</template><template v-if="cachedFlag"> · 缓存命中<template v-if="cacheAgeText">（{{ cacheAgeText }}）</template></template>
          <span v-if="staleFlag" class="stale-tag err">数据陈旧</span>
          <span v-if="pageHidden"> · 页面已隐藏，暂停轮询</span>
          <span v-else-if="!pollGate"> · 页签未激活，暂停轮询</span>
        </div>
      </div>
      <div class="screen-tools">
        <router-link v-if="standalone" to="/" class="btn btn-ghost screen-btn">← 返回工作台</router-link>
        <button class="btn screen-btn" @click="toggleFullscreen">{{ isFullscreen ? '退出全屏' : '全屏' }}</button>
      </div>
    </header>

    <div v-if="partialErrors.length" class="partial-errors">
      <span class="perr-icon">⚠</span>
      <div class="perr-body">
        <b>部分数据异常（时间 {{ lastRefresh }}）</b>
        <p v-for="(e, i) in partialErrors" :key="i" class="perr-item">{{ e }}</p>
      </div>
    </div>

    <!-- 第一层：平台总览 -->
    <div class="res-grid">
      <router-link to="/kubernetes/overview" class="res-card">
        <div class="res-head"><span class="res-label">K3s 节点</span><b v-if="firstLoading" class="skel skel-num"></b><b v-else class="res-num" :class="{ 'res-na': !nodesInfo, 'res-warn': nodesInfo && nodesInfo.ready < nodesInfo.total }">{{ nodesInfo ? `${nodesInfo.ready ?? 0}/${nodesInfo.total ?? 0}` : 'N/A' }}</b></div>
        <p v-if="firstLoading" class="skel skel-line"></p>
        <p v-else class="res-meta">{{ nodesInfo ? `Ready ${nodesInfo.ready ?? 0}/${nodesInfo.total ?? 0}${rolesText}` : 'K3s 数据不可用 / 未配置' }}</p>
      </router-link>
      <router-link to="/kubernetes/workloads" class="res-card">
        <div class="res-head"><span class="res-label">工作负载可用率</span><b v-if="firstLoading" class="skel skel-num"></b><b v-else class="res-num" :class="{ 'res-na': !workloadAvail, 'res-warn': workloadAvail && workloadAvail.pct != null && workloadAvail.pct < 100 }">{{ workloadAvail ? (workloadAvail.pct == null ? '--' : workloadAvail.pct + '%') : 'N/A' }}</b></div>
        <p v-if="firstLoading" class="skel skel-line"></p>
        <p v-else class="res-meta">{{ workloadAvail ? workloadAvail.rows.map((r) => `${r.label} ${r.ready}/${r.desired}`).join(' · ') : 'K3s 数据不可用 / 未配置' }}</p>
      </router-link>
      <router-link to="/kubernetes/pods" class="res-card">
        <div class="res-head"><span class="res-label">Pod 异常</span><b v-if="firstLoading" class="skel skel-num"></b><b v-else class="res-num" :class="{ 'res-na': !podAbnormal, 'res-err': podAbnormal && podAbnormal.count > 0 }">{{ podAbnormal ? podAbnormal.count : 'N/A' }}</b></div>
        <p v-if="firstLoading" class="skel skel-line"></p>
        <p v-else class="res-meta">{{ podAbnormal ? `Failed ${podsInfo?.failed ?? 0} · Pending ${podsInfo?.pending ?? 0} · 重启最高 ${podAbnormal.top ? `${podAbnormal.top.namespace}/${podAbnormal.top.name} ${podAbnormal.top.restarts} 次` : '无'}` : 'K3s 数据不可用 / 未配置' }}</p>
      </router-link>
      <router-link to="/" class="res-card">
        <div class="res-head"><span class="res-label">服务可访问率</span><b v-if="firstLoading" class="skel skel-num"></b><b v-else class="res-num" :class="{ 'res-err': serviceAccess && serviceAccess.pct != null && serviceAccess.pct < 100 }">{{ serviceAccess ? (serviceAccess.pct == null ? '--' : serviceAccess.pct + '%') : '--' }}</b></div>
        <p v-if="firstLoading" class="skel skel-line"></p>
        <p v-else class="res-meta">{{ serviceAccess ? `${serviceAccess.fallback ? '探活在线' : '外部可达'} ${serviceAccess.ok}/${serviceAccess.total}${serviceAccess.fallback ? ' · 双层健康未启用' : ''}` : '暂无服务数据' }}</p>
      </router-link>
      <router-link to="/alerts" class="res-card">
        <div class="res-head"><span class="res-label">活跃告警</span><b v-if="firstLoading" class="skel skel-num"></b><b v-else class="res-num" :class="{ 'res-err': alertsSummary.firing }">{{ alertsSummary.firing ?? '--' }}</b></div>
        <p v-if="firstLoading" class="skel skel-line"></p>
        <p v-else class="res-meta">触发中 · 已确认 {{ alertsSummary.acknowledged ?? '--' }}</p>
      </router-link>
      <router-link to="/database" class="res-card">
        <div class="res-head"><span class="res-label">数据库</span><b v-if="firstLoading" class="skel skel-num"></b><b v-else class="res-num">{{ databasesSummary.total ?? '--' }}</b></div>
        <p v-if="firstLoading" class="skel skel-line"></p>
        <p v-else class="res-meta">{{ databasesSummary.total === 0 ? '暂无实例' : `已连接 ${databasesSummary.connected || 0} · 待接入 ${databasesSummary.pending || 0} · 异常 ${databasesSummary.error || 0}` }}</p>
      </router-link>
      <router-link to="/logs" class="res-card">
        <div class="res-head"><span class="res-label">日志</span><b v-if="firstLoading" class="skel skel-num"></b><b v-else class="res-num">{{ logsSummary.running ?? '--' }}/{{ logsSummary.total ?? '--' }}</b></div>
        <p v-if="firstLoading" class="skel skel-line"></p>
        <p v-else class="res-meta">采集器运行/总数 · 摄入状态在日志中心按需检查 · 异常 {{ logsSummary.abnormal ?? '--' }}</p>
      </router-link>
    </div>

    <!-- 第二/三/五层：主机资源 | K3s 运行状态 | 风险与恢复 -->
    <div class="screen-grid">
      <section class="panel">
        <div class="panel-title">主机资源
          <span class="screen-muted" style="font-size:12px">异常优先 · 在线 {{ hostsSummary.online ?? 0 }}/{{ hostsSummary.total ?? 0 }}<template v-if="hostsSummary.stale"> · 陈旧 {{ hostsSummary.stale }}</template><template v-if="freshnessText"> · 指标时间 {{ freshnessText }}</template></span>
        </div>
        <p v-if="layerError('hosts')" class="panel-err">{{ layerError('hosts') }}</p>
        <div v-if="firstLoading" class="skel-box"><div v-for="n in 4" :key="n" class="skel" :style="{ width: n % 2 ? '62%' : '88%' }"></div></div>
        <div v-else-if="!servers.length" class="screen-empty">暂无主机数据</div>
        <div v-else>
          <div v-for="h in sortedServers" :key="h.id" class="host-line">
            <div class="host-line-head">
              <span>{{ h.name }}<em v-if="h.stale" class="stale-tag">陈旧</em></span>
              <span class="screen-muted">{{ h.host }} · 上报 {{ h.last_seen ? fmtTime(h.last_seen) : '无数据' }}<template v-if="h.metrics_at"> · 指标 {{ fmtShort(h.metrics_at) }}</template></span>
            </div>
            <div class="host-line-bars">
              <div class="hlb"><span>CPU</span><div class="hlb-track"><div class="hlb-fill" :style="fillStyle(h.cpu)"></div></div><b class="hlb-val">{{ fmtPct(h.cpu) }}</b></div>
              <div class="hlb"><span>MEM</span><div class="hlb-track"><div class="hlb-fill" :style="fillStyle(h.memory)"></div></div><b class="hlb-val">{{ fmtPct(h.memory) }}</b></div>
              <div class="hlb"><span>DISK</span><div class="hlb-track"><div class="hlb-fill" :style="fillStyle(h.disk)"></div></div><b class="hlb-val">{{ fmtPct(h.disk) }}</b></div>
            </div>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-title">K3s 运行状态
          <span v-if="hasK3s" class="screen-muted" style="font-size:12px">Pod {{ podsInfo?.running ?? 0 }}/{{ podsInfo?.total ?? 0 }} 运行</span>
        </div>
        <p v-if="layerError('k3s')" class="panel-err">{{ layerError('k3s') }}</p>
        <div v-if="firstLoading" class="skel-box"><div v-for="n in 4" :key="n" class="skel" :style="{ width: n % 2 ? '55%' : '80%' }"></div></div>
        <div v-else-if="!hasK3s" class="screen-empty">K3s 数据不可用 / 未配置</div>
        <template v-else>
          <div class="wl-rows">
            <div v-for="w in workloadAvail.rows" :key="w.label" class="hlb">
              <span class="wl-label">{{ w.label }}</span>
              <div class="hlb-track"><div class="hlb-fill" :style="wlFill(w)"></div></div>
              <b class="hlb-val">{{ w.ready }}/{{ w.desired }}</b>
            </div>
          </div>
          <div class="k3s-chips">
            <span class="k3s-chip" :class="{ 'chip-bad': (podsInfo?.pending ?? 0) > 0 }">Pending <b>{{ podsInfo?.pending ?? 0 }}</b></span>
            <span class="k3s-chip" :class="{ 'chip-bad': (podsInfo?.failed ?? 0) > 0 }">Failed <b>{{ podsInfo?.failed ?? 0 }}</b></span>
            <span v-if="podsInfo?.unknown" class="k3s-chip">Unknown <b>{{ podsInfo.unknown }}</b></span>
            <span class="k3s-chip" :class="{ 'chip-bad': (pvcInfo?.lost ?? 0) > 0 }">PVC 绑定 <b>{{ pvcInfo?.bound ?? 0 }}</b> · 待 <b>{{ pvcInfo?.pending ?? 0 }}</b> · 失 <b>{{ pvcInfo?.lost ?? 0 }}</b></span>
            <span class="k3s-chip" :class="{ 'chip-bad': (jobsInfo?.recent_failed ?? 0) > 0 }">Job 成功 <b>{{ jobsInfo?.recent_success ?? 0 }}</b> · 失败 <b>{{ jobsInfo?.recent_failed ?? 0 }}</b></span>
          </div>
          <div v-if="restartRows.length" class="scroll-y">
            <table class="mini-table">
              <thead><tr><th>重启 Pod</th><th>命名空间</th><th>次数</th></tr></thead>
              <tbody><tr v-for="p in restartRows" :key="p.namespace + '/' + p.name"><td>{{ p.name }}</td><td class="screen-muted">{{ p.namespace }}</td><td>{{ p.restarts }}</td></tr></tbody>
            </table>
          </div>
          <div v-else class="screen-empty screen-empty-sm">无异常重启 Pod</div>
          <div v-if="cronjobs.length" class="cron-list">
            <div v-for="(c, idx) in cronjobs" :key="idx" class="cron-row" :class="{ dim: c.suspend }">
              <span class="cron-name">{{ c.namespace }}/{{ c.name }}</span>
              <span class="screen-muted">{{ c.schedule || '-' }} · 最近 {{ c.last_schedule_time ? fmtShort(c.last_schedule_time) : '从未调度' }}<template v-if="c.suspend"> · 已暂停</template></span>
            </div>
          </div>
        </template>
      </section>

      <section class="panel">
        <div class="panel-title">风险与恢复</div>
        <div v-if="firstLoading" class="skel-box"><div v-for="n in 4" :key="n" class="skel" :style="{ width: n % 2 ? '70%' : '45%' }"></div></div>
        <template v-else>
          <div class="risk-block">
            <div class="risk-line"><span class="risk-k">数据状态</span><span class="risk-v">{{ dataTimestamp ? fmtTime(dataTimestamp) : '未知' }}<template v-if="cachedFlag"> · 缓存命中<template v-if="cacheAgeText"> {{ cacheAgeText }}</template></template></span><span v-if="staleFlag" class="stale-tag err">数据陈旧<template v-if="cacheAgeText"> · {{ cacheAgeText }}</template></span></div>
            <div class="risk-line"><span class="risk-k">Warning 事件</span><b class="risk-v" :class="{ 'risk-bad': warningEvents > 0 }">{{ warningEvents == null ? 'K3s 数据不可用' : warningEvents }}</b></div>
            <div class="risk-line"><span class="risk-k">采集失败</span><span class="risk-v" :class="{ 'risk-bad': partialErrors.length }">{{ partialErrors.length ? `${partialErrors.length} 条（见顶部明细，按卡片降级）` : '无' }}</span></div>
          </div>
          <div v-if="sourceStatus.length" class="src-chips">
            <span v-for="s in sourceStatus" :key="s.k" class="k3s-chip" :class="{ 'chip-bad': /fail|error|down|异常|不可用/i.test(s.v) }">{{ s.k }}: {{ s.v.length > 42 ? s.v.slice(0, 42) + '…' : s.v }}</span>
          </div>
          <div class="risk-sub">备份 / 定时任务</div>
          <div v-if="!hasK3s" class="screen-empty screen-empty-sm">K3s 数据不可用 / 未配置</div>
          <div v-else-if="!cronjobs.length" class="screen-empty screen-empty-sm">无定时任务</div>
          <div v-else class="cron-list">
            <div v-for="(c, idx) in cronjobs" :key="idx" class="cron-row" :class="{ dim: c.suspend }">
              <span class="cron-name">{{ c.name }}</span>
              <span class="screen-muted">{{ c.namespace }} · 最近 {{ c.last_schedule_time ? fmtShort(c.last_schedule_time) : '从未调度' }}</span>
              <span v-if="c.suspend" class="stale-tag err">已暂停</span>
            </div>
          </div>
          <div class="risk-sub">最近告警</div>
          <div v-if="!alerts.length" class="screen-empty screen-empty-sm">当前无告警 🎉</div>
          <div v-else class="alert-list">
            <div v-for="e in alerts" :key="e.id" class="alert-item" @click="go('/alerts')">
              <span class="alert-dot err"></span>
              <div class="alert-main">
                <div class="alert-name">{{ e.rule_name || '未知规则' }}</div>
                <div class="alert-meta">{{ e.server_name || '' }} · {{ fmtTime(e.fired_at || e.created_at) }} · {{ e.current_value || '' }}</div>
              </div>
              <span class="tag tag-red">{{ e.status }}</span>
            </div>
          </div>
        </template>
      </section>
    </div>

    <!-- 第四层：服务与网络 | 资源趋势 -->
    <div class="screen-grid2">
      <section class="panel">
        <div class="panel-title">服务与网络
          <span class="screen-muted" style="font-size:12px">
            <template v-if="servicesDual">外部可达 {{ dualExtOk }}/{{ dualExtTotal }}<template v-if="dualExtTotal < servicesDual.length"> · 未配置 {{ servicesDual.length - dualExtTotal }}</template></template>
            <template v-else>双层健康未启用 · 探活 up {{ healthSummary.up }} / down {{ healthSummary.down }} / 共 {{ healthSummary.total }}</template>
          </span>
        </div>
        <p v-if="layerError('services')" class="panel-err">{{ layerError('services') }}</p>
        <div v-if="firstLoading" class="skel-box"><div v-for="n in 4" :key="n" class="skel" :style="{ width: n % 2 ? '75%' : '50%' }"></div></div>
        <template v-else>
          <div v-if="servicesDual" class="scroll-y">
            <table class="mini-table svc-table">
              <thead><tr><th>服务</th><th>内部健康</th><th>外部访问</th><th>延迟</th><th>备注</th></tr></thead>
              <tbody>
                <tr v-for="r in dualRows" :key="r.name" :class="'svc-row-' + r.level">
                  <td class="svc-name">{{ r.name }}</td>
                  <td><span class="svc-badge" :class="r.i == null ? 'na' : (r.iOk ? 'ok' : 'bad')">{{ r.i == null ? '不可用' : (r.iOk ? '正常' : '异常') }}</span></td>
                  <td><span class="svc-badge" :class="r.e == null ? 'na' : (r.eOk ? 'ok' : 'bad')">{{ r.e == null ? '未配置' : (r.eOk ? '正常' : '异常') }}</span></td>
                  <td class="screen-muted">{{ r.lat }}</td>
                  <td class="svc-note" :title="r.note">{{ r.note || '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else>
            <div class="screen-empty screen-empty-sm">双层健康数据未启用，展示服务广场探活状态</div>
            <div v-if="!services.length" class="screen-empty">暂无服务数据</div>
            <div v-else class="health-grid">
              <div v-for="s in services" :key="s.id" class="health-tile" :class="'st-' + (s.status === 'unknown' ? 'unknown' : s.status)" :title="s.name" @click="go('/')">
                <span class="tile-name">{{ s.name }}</span>
              </div>
            </div>
          </div>
          <div class="wg-block" @click="goWireguard">
            <span class="risk-k">WireGuard</span>
            <span class="k3s-chip">已纳管 <b>{{ wgSummary.managed ?? '--' }}</b></span>
            <span class="k3s-chip">健康 <b>{{ wgSummary.healthy ?? '--' }}</b></span>
            <span class="k3s-chip" :class="{ 'chip-warn': wgSummary.warning > 0 }">警告 <b>{{ wgSummary.warning ?? '--' }}</b></span>
            <span class="k3s-chip" :class="{ 'chip-bad': wgSummary.offline > 0 }">离线 <b>{{ wgSummary.offline ?? '--' }}</b></span>
            <span class="k3s-chip">未纳管 <b>{{ wgSummary.unmanaged ?? '--' }}</b></span>
            <span v-if="wgFreshText" class="screen-muted wg-at">拓扑时间 {{ wgFreshText }}</span>
          </div>
        </template>
      </section>

      <section class="panel">
        <div class="panel-title">资源趋势
          <select v-model="trendHost" class="screen-select" @change="queueTrend(true)">
            <option value="__top3__">风险 Top 3 主机</option>
            <option v-for="h in servers" :key="h.id" :value="h.id">{{ h.name }}</option>
          </select>
          <select v-model="trendMetric" class="screen-select" @change="queueTrend(true)">
            <option value="cpu">CPU</option>
            <option value="memory">内存</option>
            <option value="disk">磁盘</option>
            <option value="net">网络</option>
          </select>
          <select v-model="trendRange" class="screen-select" @change="queueTrend(true)">
            <option value="1">近 1 小时</option>
            <option value="6">近 6 小时</option>
            <option value="24">近 24 小时</option>
          </select>
        </div>
        <div ref="trendChartEl" class="chart"></div>
      </section>
    </div>

    <!-- 兼容资源：独立 Docker 主机（docker_hosts_count>0 才渲染） -->
    <div v-if="dockerHostsCount > 0" class="compat-zone">
      <div class="compat-title">兼容资源 · 独立 Docker 主机 {{ dockerHostsCount }} 台</div>
      <div class="compat-row">
        <router-link to="/docker" class="res-card">
          <div class="res-head"><span class="res-label">Docker 容器</span><b class="res-num">{{ containersSummary.running ?? '--' }}</b></div>
          <p class="res-meta">运行中 · 已停止 {{ containersSummary.stopped ?? '--' }}<template v-if="containersSummary.unknown_hosts"> · 未知主机 {{ containersSummary.unknown_hosts }}</template></p>
        </router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import echarts from '../utils/echarts'
import { api, fmtTime } from '../api'
import { useTabActive } from '../workbench/tabs'

const router = useRouter()
const route = useRoute()
const standalone = ref(!!(window.location.hash.includes('screen-standalone')))
const screenRoot = ref(null)
const trendChartEl = ref(null)
const isFullscreen = ref(false)
const lastRefresh = ref('-')
const lastDataAt = ref('-')
const pageHidden = ref(false)
const clockTick = ref(0)

const summary = ref(null)
const partialErrors = ref([])
const trendHost = ref('__top3__')
const trendMetric = ref('cpu')
const trendRange = ref('6')
const firstLoading = ref(true)

let trendChart = null
let coreTimer = null
let trendTimer = null
let clockTimer = null
let controller = null
let trendController = null
let requestInFlight = false
let lastTrendAt = 0
let lastDataReceivedAt = 0

// 轮询闸门：页签内（Screen）与独立大屏（ScreenStandalone）两种运行模式都要生效；
// useTabActive = 当前路由名匹配 且 页面可见（tabs.init 已在 App.vue 无条件执行）。
// 初次路由尚未解析（name 为空）时保持激活，避免冷加载首轮轮询被跳过。
const { isActive: inTabActive } = useTabActive('Screen')
const { isActive: standaloneActive } = useTabActive('ScreenStandalone')
const pollGate = computed(() => inTabActive.value || standaloneActive.value || route.name == null)

const hostsSummary = computed(() => summary.value?.hosts_summary || {})
const containersSummary = computed(() => summary.value?.containers_summary || {})
const databasesSummary = computed(() => summary.value?.databases_summary || {})
const servicesSummary = computed(() => summary.value?.services_summary || {})
const logsSummary = computed(() => summary.value?.logs_summary || {})
const wgSummary = computed(() => summary.value?.wireguard_summary || {})
const alertsSummary = computed(() => summary.value?.alerts_summary || {})
const servers = computed(() => summary.value?.servers || [])
const services = computed(() => summary.value?.services || [])
const alerts = computed(() => summary.value?.active_alerts || [])

// ---- 新契约字段（BE-3 /screen/summary）----
const dataTimestamp = computed(() => summary.value?.data_timestamp || '')
const cachedFlag = computed(() => summary.value?.cached === true)
const cacheAgeSeconds = computed(() => {
  const n = Number(summary.value?.cache_age_seconds)
  return Number.isFinite(n) ? n : null
})
const cacheAgeText = computed(() => (cacheAgeSeconds.value == null ? '' : `${Math.round(cacheAgeSeconds.value)}s`))
const dockerHostsCount = computed(() => {
  const raw = summary.value?.docker_hosts_count
  const n = Number(raw)
  return raw == null || !Number.isFinite(n) ? 0 : n
})
const sourceStatus = computed(() => {
  const s = summary.value?.source_status
  if (!s || typeof s !== 'object' || Array.isArray(s)) return []
  return Object.entries(s).map(([k, v]) => ({ k, v: typeof v === 'string' ? v : JSON.stringify(v) }))
})
const k3s = computed(() => {
  const s = summary.value?.k3s
  return s && typeof s === 'object' ? s : null
})
const hasK3s = computed(() => !!k3s.value)
const nodesInfo = computed(() => k3s.value?.nodes || null)
const workloads = computed(() => k3s.value?.workloads || null)
const podsInfo = computed(() => k3s.value?.pods || null)
const pvcInfo = computed(() => k3s.value?.storage?.pvc || null)
const jobsInfo = computed(() => k3s.value?.jobs || null)
const cronjobs = computed(() => (Array.isArray(k3s.value?.cronjobs) ? k3s.value.cronjobs : []))
const warningEvents = computed(() => {
  if (!k3s.value) return null
  const n = Number(k3s.value.warning_events)
  return Number.isFinite(n) ? n : 0
})
const servicesDual = computed(() => {
  const d = summary.value?.services_dual
  return Array.isArray(d) && d.length ? d : null
})

const rolesText = computed(() => {
  const roles = nodesInfo.value?.roles
  return Array.isArray(roles) && roles.length ? ` · 角色 ${roles.join('/')}` : ''
})

const workloadAvail = computed(() => {
  const w = workloads.value
  if (!w) return null
  const parts = [['deployments', 'Deployment'], ['statefulsets', 'StatefulSet'], ['daemonsets', 'DaemonSet']]
  let ready = 0
  let desired = 0
  const rows = []
  for (const [key, label] of parts) {
    const v = w[key] || {}
    const d = Number(v.desired ?? 0)
    const r = Number(v.ready ?? 0)
    ready += r
    desired += d
    rows.push({ label, ready: r, desired: d })
  }
  return { ready, desired, pct: desired > 0 ? Math.round((ready / desired) * 1000) / 10 : null, rows }
})

const podAbnormal = computed(() => {
  const p = podsInfo.value
  if (!p) return null
  const top = Array.isArray(p.restart_top) ? p.restart_top[0] : null
  return {
    count: Number(p.failed ?? 0) + Number(p.pending ?? 0) + Number(top?.restarts ?? 0),
    top: top || null,
  }
})

const restartRows = computed(() => (Array.isArray(podsInfo.value?.restart_top) ? podsInfo.value.restart_top.slice(0, 6) : []))

const serviceAccess = computed(() => {
  const dual = servicesDual.value
  if (dual) {
    const withExt = dual.filter((s) => s.external)
    const ok = withExt.filter((s) => s.external.ok).length
    if (withExt.length) return { ok, total: withExt.length, pct: Math.round((ok / withExt.length) * 1000) / 10, fallback: false }
  }
  const s = servicesSummary.value || {}
  const total = Number(s.total ?? 0)
  const up = Number(s.up ?? 0)
  if (total > 0) return { ok: up, total, pct: Math.round((up / total) * 1000) / 10, fallback: true }
  return null
})

// 数据陈旧判定：优先服务端 cache_age_seconds（无时钟偏差），缺失时用 data_timestamp 推算
const staleInfo = computed(() => {
  void clockTick.value
  const s = summary.value
  if (!s) return null
  let age = cacheAgeSeconds.value
  if (age == null && s.data_timestamp) {
    const t = Date.parse(s.data_timestamp)
    if (!Number.isNaN(t)) age = Math.max(0, (Date.now() - t) / 1000)
  }
  if (age == null) return null
  return { stale: age > 60, age: Math.round(age) }
})
const staleFlag = computed(() => !!staleInfo.value?.stale)

const healthSummary = computed(() => {
  const up = services.value.filter((s) => s.status === 'up' || s.status === 'online').length
  const down = services.value.filter((s) => s.status === 'down' || s.status === 'offline').length
  return { up, down, total: services.value.length }
})

// 双层健康行（§7.2）：internal 可为 null（后端不在集群内无法 ClusterIP 探测）→ 中性"不可用"，不判失败
const dualRows = computed(() => (servicesDual.value || []).map((s) => {
  const i = s.internal || null
  const e = s.external || null
  const iOk = i ? !!i.ok : null
  const eOk = e ? !!e.ok : null
  let level = 'na'
  let label = '无数据'
  if (iOk === false && eOk === false) { level = 'bad'; label = '服务故障' }
  else if (iOk === false && eOk === true) { level = 'warn'; label = '监控异常' }
  else if (iOk === true && eOk === false) { level = 'warn'; label = '访问链路异常' }
  else if (iOk === true && eOk === true) { level = 'ok'; label = '正常' }
  else if (iOk === true || eOk === true) { level = 'ok'; label = '部分正常' }
  else if (iOk === false || eOk === false) { level = 'warn'; label = '单侧异常' }
  let note = ''
  if (i == null) note = '内部探测不可用'
  else if (iOk === false && eOk === true) note = i.error ? `内部探测失败：${i.error}` : '内部失联但外部可达，检查探测配置或路由'
  else if (iOk === true && eOk === false) note = e.error ? `外部访问失败：${e.error}` : '服务运行中，访问链路异常'
  else if (iOk === false && eOk === false) note = e?.error || i.error || '内外均不可达'
  else if (e?.url) note = e.url
  else if (i?.via) note = `经 ${i.via}`
  const lat = e && e.latency_ms != null ? `外 ${fmtLat(e.latency_ms)}` : (i && i.latency_ms != null ? `内 ${fmtLat(i.latency_ms)}` : '--')
  return { name: s.name, i, e, iOk, eOk, level, label, lat, note }
}))
const dualExtOk = computed(() => dualRows.value.filter((r) => r.eOk).length)
const dualExtTotal = computed(() => dualRows.value.filter((r) => r.eOk !== null).length)

const freshnessText = computed(() => {
  const t = summary.value?.freshness?.metrics_at
  return t ? fmtTime(t) : ''
})
const wgFreshText = computed(() => {
  const t = summary.value?.freshness?.wireguard_at
  return t ? fmtShort(t) : ''
})

const sortedServers = computed(() => {
  const score = (h) => {
    const v = [h.cpu, h.memory, h.disk]
    return Math.max(...v.map((x) => (x == null ? -1 : x)))
  }
  return [...servers.value].sort((a, b) => score(b) - score(a))
})

function fmtPct(v) { return v == null ? '--' : `${Number(v).toFixed(1)}%` }
function fillStyle(v) {
  if (v == null) return { width: '0%' }
  return { width: Math.min(100, Number(v)) + '%', background: levelColor(v) }
}
function wlFill(w) {
  if (!w.desired) return { width: '0%' }
  const pct = Math.min(100, (w.ready / w.desired) * 100)
  return { width: pct + '%', background: levelColor(pct) }
}
function levelColor(v) {
  if (v >= 90) return '#ef4444'
  if (v >= 70) return '#f59e0b'
  return '#22c55e'
}
function fmtLat(ms) {
  const n = Number(ms)
  return Number.isFinite(n) ? `${Math.round(n)}ms` : '--'
}
function fmtShort(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return String(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
function go(path) { router.push(path) }
function goWireguard() { router.push({ path: '/topology', query: { scenario: 'wireguard' } }) }

// 单层错误只错该层卡片：按关键字把 partial_errors 归位到对应面板
const LAYER_ERROR_PATTERNS = [
  ['hosts', /主机/],
  ['k3s', /K3s|Kubernetes|k8s|kube|集群/i],
  ['services', /服务|探活|dual/i],
  ['wireguard', /WG|WireGuard/i],
  ['logs', /日志/],
  ['alerts', /告警/],
]
function layerError(kind) {
  const p = LAYER_ERROR_PATTERNS.find(([k]) => k === kind)
  if (!p) return ''
  return partialErrors.value.find((e) => p[1].test(e)) || ''
}

// ---------------- 核心聚合 ----------------
function pollAllowed() {
  return pollGate.value && document.visibilityState === 'visible'
}

function dataAgeSeconds() {
  if (!summary.value) return Infinity
  let age = lastDataReceivedAt ? (Date.now() - lastDataReceivedAt) / 1000 : 0
  const ts = summary.value.data_timestamp
  if (ts) {
    const t = Date.parse(ts)
    if (!Number.isNaN(t)) age = Math.max(age, (Date.now() - t) / 1000)
  }
  return age
}

async function loadSummary() {
  if (requestInFlight) return
  if (!pollAllowed()) return
  requestInFlight = true
  if (controller) controller.abort()
  controller = new AbortController()
  try {
    const firstLoad = !summary.value
    const data = await api.get('/screen/summary', null, { signal: controller.signal, timeoutMs: 8000 })
    summary.value = data
    partialErrors.value = data.partial_errors || []
    lastDataReceivedAt = Date.now()
    lastRefresh.value = fmtTime(new Date().toISOString())
    lastDataAt.value = fmtTime(data.freshness?.metrics_at)
    if (firstLoad) queueTrend(true)
  } catch (err) {
    if (err.status === 304) {
      // ETag 命中：静默保留旧数据
      return
    }
    if (err.name !== 'AbortError') {
      // 保留上一份可用数据；只更新时间戳
      partialErrors.value = [...partialErrors.value, `核心聚合失败：${err.message}`].slice(-6)
    }
  } finally {
    requestInFlight = false
    firstLoading.value = false
  }
}

async function queueTrend(force = false) {
  if (document.visibilityState === 'hidden') return
  if (!pollGate.value) return
  // 趋势独立于核心刷新，60 秒节流
  const now = Date.now()
  if (!force && now - (lastTrendAt || 0) < 15000) return
  lastTrendAt = now
  await loadTrend()
}

async function loadTrend() {
  if (!trendChartEl.value) return
  trendController?.abort()
  trendController = new AbortController()
  const activeController = trendController
  const hours = Number(trendRange.value)
  const end = new Date()
  const start = new Date(end.getTime() - hours * 3600 * 1000)
  let ids = []
  if (trendHost.value === '__top3__') {
    ids = sortedServers.value.slice(0, 3).map((h) => h.id)
  } else {
    ids = [trendHost.value]
  }
  const isNet = trendMetric.value === 'net'
  const metrics = isNet ? 'net_rx,net_tx' : trendMetric.value
  const series = []
  const color = { cpu: '#3b82f6', memory: '#10b981', disk: '#f59e0b', net_rx: '#38bdf8', net_tx: '#fb7185' }
  const unit = isNet ? 'KB/s' : '%'
  const results = await Promise.allSettled(ids.map(async (id) => {
    const h = servers.value.find((x) => x.id === id)
    if (!h) return
    const d = await api.get(`/servers/${id}/metrics/timeseries`, {
      metrics, start: start.toISOString(), end: end.toISOString(), resolution: 'auto',
    }, { signal: activeController.signal, timeoutMs: 12000 })
    if (!d?.series) return
    for (const [name, points] of Object.entries(d.series)) {
      if (points?.length) {
        const isNetP = name === 'net_rx' || name === 'net_tx'
        series.push({
          name: isNetP ? `${h.name} ${name === 'net_rx' ? '下行' : '上行'}` : `${h.name}`,
          type: 'line', smooth: true, showSymbol: false,
          itemStyle: { color: color[name] || color[trendMetric.value] },
          areaStyle: isNetP ? { opacity: .15 } : undefined,
          data: points.map((pt) => [pt[0] * 1000, isNetP ? Number((pt[1] / 1024).toFixed(2)) : Number(pt[1].toFixed(1))]),
        })
      }
    }
  }))
  if (trendController !== activeController) return
  if (results.some((result) => result.status === 'rejected' && result.reason?.name !== 'AbortError')) {
    partialErrors.value = [...partialErrors.value, '部分主机趋势加载失败'].slice(-6)
  }
  if (trendChart) {
    trendChart.setOption({
      series,
      xAxis: { type: 'time' },
      yAxis: { type: 'value', max: isNet ? undefined : 100, axisLabel: { color: '#7f95b5', formatter: isNet ? '{value}' : '{value}%' }, name: unit },
    })
  }
}

function initChart() {
  trendChart = echarts.init(trendChartEl.value)
  trendChart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#7f95b5' },
    tooltip: { trigger: 'axis' },
    legend: { textStyle: { color: '#7f95b5' }, top: 0, type: 'scroll' },
    grid: { left: 48, right: 16, top: 34, bottom: 24 },
    xAxis: { type: 'time', axisLine: { lineStyle: { color: '#2a4060' } }, axisLabel: { color: '#7f95b5' } },
    yAxis: { type: 'value', axisLabel: { color: '#7f95b5' }, splitLine: { lineStyle: { color: 'rgba(90,130,190,.12)' } } },
    series: [],
  })
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    const p = screenRoot.value.requestFullscreen()
    if (p && p.catch) p.catch(() => window.open(location.href.split('#')[0] + '#/screen-standalone', '_blank'))
  } else {
    document.exitFullscreen()
  }
}

function onVisibility() {
  const hidden = document.visibilityState !== 'visible'
  pageHidden.value = hidden
  if (!hidden) {
    loadSummary()
    queueTrend()
  }
}

// 页签重新激活时：数据距今 >10s 立即刷新
watch(pollGate, (v, old) => {
  if (v && !old) {
    if (!summary.value || dataAgeSeconds() > 10) {
      loadSummary()
      queueTrend(true)
    }
  }
})

onMounted(() => {
  initChart()
  loadSummary()
  coreTimer = setInterval(loadSummary, 10000)
  trendTimer = setInterval(queueTrend, 60000)
  clockTimer = setInterval(() => { clockTick.value += 1 }, 5000)
  document.addEventListener('visibilitychange', onVisibility)
  document.addEventListener('fullscreenchange', onFsChange)
  window.addEventListener('resize', resizeChart)
})
onUnmounted(() => {
  clearInterval(coreTimer)
  clearInterval(trendTimer)
  clearInterval(clockTimer)
  document.removeEventListener('visibilitychange', onVisibility)
  document.removeEventListener('fullscreenchange', onFsChange)
  window.removeEventListener('resize', resizeChart)
  if (controller) controller.abort()
  if (trendController) trendController.abort()
  if (trendChart) trendChart.dispose()
})

function onFsChange() { isFullscreen.value = !!document.fullscreenElement }
function resizeChart() { trendChart?.resize() }
</script>

<style scoped>
.screen-root {
  height: 100%; overflow: auto; background: var(--screen-bg); color: var(--screen-text);
  padding: 18px 22px;
}
.screen-head { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 14px; }
.screen-title { margin: 0; font-size: 22px; }
.screen-sub { color: var(--screen-muted); font-size: 12px; margin-top: 4px; }
.screen-tools { display: flex; gap: 8px; }
.screen-btn { background: rgba(20,35,58,.8); border-color: var(--screen-border); color: var(--screen-text); }
.screen-select { margin-left: auto; background: #14233a; color: var(--screen-text); border: 1px solid var(--screen-border); border-radius: 6px; padding: 4px 8px; }
.partial-errors { display: flex; gap: 10px; align-items: flex-start; background: rgba(180,83,9,.14); border: 1px solid rgba(245,158,11,.4); border-radius: 10px; padding: 10px 14px; margin-bottom: 14px; }
.perr-icon { font-size: 16px; }
.perr-body b { font-size: 13px; color: #fcd34d; }
.perr-item { margin: 2px 0 0; font-size: 12px; color: var(--screen-muted); }
.stale-tag { font-style: normal; color: #f59e0b; border: 1px solid rgba(245,158,11,.5); border-radius: 4px; padding: 0 4px; font-size: 10px; margin-left: 6px; }
.stale-tag.err { color: #f87171; border-color: rgba(248,113,113,.55); }
.res-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: 12px; margin-bottom: 16px; }
.res-card {
  background: var(--screen-panel); border: 1px solid var(--screen-border); border-radius: 12px;
  padding: 12px 14px; text-decoration: none; color: var(--screen-text); display: block; transition: border-color .15s;
}
.res-card:hover { border-color: #3b82f6; }
.res-head { display: flex; align-items: center; justify-content: space-between; }
.res-label { font-size: 12px; color: var(--screen-muted); }
.res-num { font-size: 22px; }
.res-num.res-err { color: #f87171; }
.res-num.res-warn { color: #fcd34d; }
.res-num.res-na { color: var(--screen-muted); font-size: 16px; }
.res-meta { margin: 6px 0 0; font-size: 12px; color: var(--screen-muted); }
.screen-grid { display: grid; grid-template-columns: 1.05fr 1.25fr 1fr; gap: 14px; margin-bottom: 14px; }
.screen-grid2 { display: grid; grid-template-columns: 1.4fr 1fr; gap: 14px; }
.panel { background: var(--screen-panel); border: 1px solid var(--screen-border); border-radius: 12px; padding: 14px 16px; min-height: 240px; }
.panel-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.panel-err { margin: 0 0 10px; font-size: 11px; color: #fca5a5; }
.screen-muted { color: var(--screen-muted); }
.screen-empty { color: var(--screen-muted); font-size: 13px; text-align: center; padding: 30px 0; }
.screen-empty-sm { padding: 12px 0; font-size: 12px; }
.host-line { margin-bottom: 12px; }
.host-line-head { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 5px; gap: 8px; }
.host-line-head span:first-child { flex: none; }
.host-line-head .screen-muted { text-align: right; }
.host-line-bars { display: flex; gap: 10px; }
.hlb { flex: 1; display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--screen-muted); }
.hlb-track { flex: 1; height: 6px; background: rgba(127,149,181,.18); border-radius: 3px; overflow: hidden; }
.hlb-fill { height: 100%; border-radius: 3px; transition: width .4s; }
.hlb-val { min-width: 44px; text-align: right; font-weight: 600; font-size: 11px; }
.wl-rows { display: flex; flex-direction: column; gap: 7px; margin-bottom: 10px; }
.wl-label { width: 84px; flex: none; }
.k3s-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
.k3s-chip { font-size: 11px; color: var(--screen-muted); border: 1px solid var(--screen-border); border-radius: 6px; padding: 2px 8px; background: rgba(20,35,58,.5); }
.k3s-chip b { color: var(--screen-text); font-size: 11px; }
.k3s-chip.chip-bad b { color: #f87171; }
.k3s-chip.chip-warn b { color: #fcd34d; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.mini-table th, .mini-table td { padding: 4px 6px; text-align: left; border-bottom: 1px solid rgba(90,130,190,.16); overflow-wrap: anywhere; }
.mini-table th { color: var(--screen-muted); font-weight: 500; font-size: 11px; }
.scroll-y { max-height: 220px; overflow: auto; }
.svc-name { font-weight: 600; }
.svc-row-bad .svc-name { color: #fca5a5; }
.svc-row-warn .svc-name { color: #fcd34d; }
.svc-note { max-width: 230px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--screen-muted); }
.svc-badge { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; border: 1px solid transparent; }
.svc-badge.ok { color: #86efac; border-color: rgba(34,197,94,.45); background: rgba(34,197,94,.12); }
.svc-badge.bad { color: #fca5a5; border-color: rgba(239,68,68,.55); background: rgba(239,68,68,.12); }
.svc-badge.warn { color: #fcd34d; border-color: rgba(245,158,11,.5); background: rgba(245,158,11,.12); }
.svc-badge.na { color: var(--screen-muted); border-color: var(--screen-border); background: rgba(127,149,181,.1); }
.cron-list { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; max-height: 180px; overflow: auto; }
.cron-row { display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
.cron-name { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dim { opacity: .45; }
.risk-block { display: flex; flex-direction: column; gap: 7px; margin-bottom: 10px; }
.risk-line { display: flex; align-items: center; gap: 8px; font-size: 12px; flex-wrap: wrap; }
.risk-k { font-size: 12px; color: var(--screen-muted); flex: none; }
.risk-v { font-weight: 600; font-size: 12px; }
.risk-bad { color: #f87171; }
.risk-sub { font-size: 12px; color: var(--screen-muted); margin: 10px 0 4px; border-top: 1px dashed var(--screen-border); padding-top: 8px; }
.src-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.wg-block { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 12px; border-top: 1px dashed var(--screen-border); padding-top: 10px; cursor: pointer; }
.wg-at { font-size: 11px; margin-left: auto; }
.health-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(92px, 1fr)); gap: 8px; max-height: 300px; overflow: auto; }
.health-tile { border: 1px solid var(--screen-border); border-radius: 8px; padding: 8px 6px; text-align: center; font-size: 12px; cursor: pointer; background: rgba(20,35,58,.5); }
.health-tile:hover { border-color: #3b82f6; }
.st-up { border-color: rgba(34,197,94,.45); color: #86efac; }
.st-down { border-color: rgba(239,68,68,.55); color: #fca5a5; }
.st-warn { border-color: rgba(245,158,11,.5); color: #fcd34d; }
.st-unknown { color: var(--screen-muted); }
.tile-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }
.alert-list { max-height: 260px; overflow: auto; display: flex; flex-direction: column; gap: 8px; }
.alert-item { display: flex; align-items: flex-start; gap: 8px; border-bottom: 1px solid rgba(90,130,190,.14); padding-bottom: 8px; cursor: pointer; }
.alert-dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 5px; flex: none; }
.alert-dot.err { background: #ef4444; }
.alert-main { flex: 1; min-width: 0; }
.alert-name { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.alert-meta { font-size: 11px; color: var(--screen-muted); margin-top: 2px; }
.chart { height: 240px; }
.compat-zone { margin-top: 14px; padding-top: 10px; border-top: 1px dashed var(--screen-border); }
.compat-title { font-size: 12px; color: var(--screen-muted); margin-bottom: 8px; }
.compat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(165px, 1fr)); gap: 12px; }
.skel { height: 12px; border-radius: 5px; background: linear-gradient(90deg, rgba(127,149,181,.14), rgba(127,149,181,.3), rgba(127,149,181,.14)); background-size: 200% 100%; animation: skel 1.2s linear infinite; }
.skel-num { display: inline-block; width: 56px; height: 22px; }
.skel-line { width: 80%; margin: 8px 0 0; }
.skel-box { display: flex; flex-direction: column; gap: 12px; padding: 8px 0; }
@keyframes skel { from { background-position: 200% 0; } to { background-position: -200% 0; } }
@media (max-width: 1100px) {
  .screen-grid, .screen-grid2 { grid-template-columns: 1fr; }
}
</style>
