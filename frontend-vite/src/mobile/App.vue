<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { apiConfigError, clearToken, getToken, request, setToken } from './api'

const username = ref('admin')
const password = ref('')
const configError = apiConfigError()
const token = ref('')
const activePage = ref('overview')
const hosts = ref([])
const events = ref([])
const services = ref([])
const loading = ref(false)
const loggingIn = ref(false)
const loginError = ref('')
const hostSearch = ref('')
const eventDays = ref(30)
const eventFilter = ref('active')
const lastUpdated = reactive({ hosts: '', events: '', services: '' })
const errors = reactive({ hosts: '', events: '', services: '' })
let invalidating = false

const onlineCount = computed(() => hosts.value.filter((host) => host.status === 'online').length)
const offlineCount = computed(() => hosts.value.filter((host) => host.status === 'offline').length)
const activeEvents = computed(() => events.value.filter((event) => ['pending', 'firing'].includes(event.status)))
const visibleEvents = computed(() => {
  const rows = eventFilter.value === 'active'
    ? activeEvents.value
    : eventFilter.value === 'recovered'
      ? events.value.filter((event) => event.status === 'recovered')
      : events.value
  return [...rows].sort((a, b) => timeValue(b.fired_at) - timeValue(a.fired_at)).slice(0, 100)
})
const upServices = computed(() => services.value.filter((service) => service.status === 'up').length)
const downServices = computed(() => services.value.filter((service) => service.status === 'down').length)
const visibleHosts = computed(() => {
  const query = hostSearch.value.trim().toLowerCase()
  if (!query) return hosts.value
  return hosts.value.filter((host) => [host.name, host.hostname, host.display_name, hostAddress(host)]
    .some((value) => String(value || '').toLowerCase().includes(query)))
})

function timeValue(value) {
  if (!value) return 0
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 0 : date.getTime()
}

function formatTime(value) {
  const timestamp = timeValue(value)
  return timestamp ? new Date(timestamp).toLocaleString('zh-CN', { hour12: false }) : '—'
}

function countDisplay(updatedAt, error, value) {
  if (updatedAt) return value
  return error ? '—' : '…'
}

function hostName(host) {
  return host.name || host.hostname || host.display_name || host.id || '未命名主机'
}

function hostAddress(host) {
  const preferred = host.preferred_management_channel === 'wireguard'
    ? host.wireguard_ip
    : host.preferred_management_channel === 'lan'
      ? host.lan_ip
      : ''
  return host.management_address_override || preferred || host.lan_ip || host.wireguard_ip || host.host || '管理地址未提供'
}

function hostStatusLabel(host) {
  return host.status === 'online' ? '在线' : host.status === 'offline' ? '离线' : '未知'
}

function hostStatusClass(host) {
  return host.status === 'online' ? 'is-online' : host.status === 'offline' ? 'is-offline' : 'is-unknown'
}

function eventStatusLabel(event) {
  return event.status === 'recovered' ? '已恢复'
    : event.status === 'acked' ? '已确认'
      : event.status === 'firing' ? '触发中'
        : event.status === 'pending' ? '待确认' : (event.status || '未知')
}

function eventStatusClass(event) {
  return ['pending', 'firing'].includes(event.status) ? 'is-offline'
    : event.status === 'acked' ? 'is-warning'
      : event.status === 'recovered' ? 'is-online' : 'is-unknown'
}

function serviceStatusLabel(service) {
  return service.status === 'up' ? '正常'
    : service.status === 'down' ? '异常'
      : service.status === 'disabled' ? '未启用' : '未知'
}

function serviceStatusClass(service) {
  return service.status === 'up' ? 'is-online'
    : service.status === 'down' ? 'is-offline' : 'is-unknown'
}

async function invalidateSession() {
  if (invalidating) return false
  invalidating = true
  let storageFailed = false
  try { await clearToken() } catch { storageFailed = true } finally { invalidating = false }
  token.value = ''
  hosts.value = []
  events.value = []
  services.value = []
  lastUpdated.hosts = ''
  lastUpdated.events = ''
  lastUpdated.services = ''
  if (storageFailed) loginError.value = '无法清除本机安全会话，请重新打开应用后重试'
  return storageFailed
}

async function loadHosts() {
  try {
    const result = await request('/modules/host-inventory/api/v1/hosts', { token: token.value })
    if (result.status === 'unavailable' || !Array.isArray(result.items)) {
      throw new Error(result.error || '主机清单暂不可用')
    }
    hosts.value = result.items
    lastUpdated.hosts = new Date().toLocaleString('zh-CN', { hour12: false })
    errors.hosts = ''
  } catch (cause) {
    if (cause.status === 401) await invalidateSession()
    errors.hosts = cause.message || '主机清单加载失败'
  }
}

async function loadEvents() {
  try {
    const result = await request('/modules/alerts/api/v1/events?days=' + eventDays.value, { token: token.value })
    if (!Array.isArray(result)) throw new Error('告警数据格式无效')
    events.value = result
    lastUpdated.events = new Date().toLocaleString('zh-CN', { hour12: false })
    errors.events = ''
  } catch (cause) {
    if (cause.status === 401) await invalidateSession()
    errors.events = cause.message || '告警列表加载失败'
  }
}

async function loadServices() {
  try {
    const result = await request('/modules/service-catalog/api/v1/services', { token: token.value })
    if (!Array.isArray(result)) throw new Error('服务数据格式无效')
    let healthAvailable = true
    try {
      const health = await request('/modules/service-catalog/api/v1/health-overview?hours=24', { token: token.value })
      const healthByKey = new Map((health?.items || []).map((item) => [item.key, item]))
      services.value = result.map((service) => ({ ...service, ...(healthByKey.get(service.key) || {}) }))
    } catch (healthError) {
      if (healthError.status === 401) throw healthError
      services.value = result
      healthAvailable = false
    }
    lastUpdated.services = new Date().toLocaleString('zh-CN', { hour12: false })
    errors.services = healthAvailable ? '' : '健康摘要暂不可用，当前显示服务清单状态'
  } catch (cause) {
    if (cause.status === 401) await invalidateSession()
    errors.services = cause.message || '服务清单加载失败'
  }
}

async function refreshAll() {
  if (!token.value || loading.value) return
  loading.value = true
  try {
    await Promise.all([loadHosts(), loadEvents(), loadServices()])
    if (!token.value) {
      hosts.value = []
      events.value = []
      services.value = []
      lastUpdated.hosts = ''
      lastUpdated.events = ''
      lastUpdated.services = ''
    }
  }
  finally { loading.value = false }
}

async function login() {
  if (!username.value.trim() || !password.value) {
    loginError.value = '请输入账号和密码'
    return
  }
  loggingIn.value = true
  loginError.value = ''
  try {
    const result = await request('/api/v2/auth/login', {
      method: 'POST',
      body: { username: username.value.trim(), password: password.value },
    })
    if (!result?.access_token) throw new Error('登录响应缺少访问凭据')
    await setToken(result.access_token)
    token.value = result.access_token
    password.value = ''
    activePage.value = 'overview'
    await refreshAll()
  } catch (cause) {
    loginError.value = cause.message || '登录失败'
  } finally {
    loggingIn.value = false
  }
}

async function logout() {
  const storageFailed = await invalidateSession()
  errors.hosts = ''
  errors.events = ''
  errors.services = ''
  if (!storageFailed) loginError.value = ''
}

onMounted(async () => {
  try {
    token.value = await getToken()
    if (token.value) await refreshAll()
  } catch {
    loginError.value = '安全存储不可用，请重新打开应用或重新登录'
  }
})
</script>

<template>
  <main class="mobile-shell">
    <header class="app-header">
      <div class="brand-mark" aria-hidden="true">O</div>
      <div><p class="eyebrow">OPS CENTER</p><h1>运维工作台</h1></div>
      <button v-if="token" class="icon-button" type="button" aria-label="退出登录" @click="logout">退出</button>
    </header>

    <template v-if="!token">
      <section class="welcome-card">
        <p class="eyebrow">MOBILE OPS</p><h2>随时掌握运维状态</h2>
        <p>请先连接 WireGuard，再使用工作台账号登录。</p>
      </section>
      <form class="login-card" @submit.prevent="login">
        <label><span>账号</span><input v-model="username" autocomplete="username" inputmode="text" required /></label>
        <label><span>密码</span><input v-model="password" autocomplete="off" autocapitalize="none" spellcheck="false" type="password" required /></label>
        <p v-if="configError" class="error-message" role="status">{{ configError }}，请完成 P0 网络配置。</p>
        <p v-else-if="!loginError" class="hint">登录凭据只保存在本机安全存储中。</p>
        <p v-if="loginError" class="error-message" role="alert">{{ loginError }}</p>
        <button class="primary-button" type="submit" :disabled="loggingIn || !!configError">{{ loggingIn ? '正在连接…' : '登录工作台' }}</button>
      </form>
    </template>

    <template v-else>
      <div class="page-heading">
        <div><p class="eyebrow">{{ activePage === 'overview' ? 'ON CALL' : 'MOBILE OPS' }}</p>
          <h2>{{ activePage === 'overview' ? '值班概览' : activePage === 'hosts' ? '主机' : activePage === 'alerts' ? '告警' : '服务' }}</h2>
        </div>
        <button class="refresh-button" type="button" :disabled="loading" @click="refreshAll">{{ loading ? '刷新中' : '刷新' }}</button>
      </div>

      <template v-if="activePage === 'overview'">
        <section class="metric-grid">
          <button class="metric-card" type="button" @click="activePage = 'hosts'">
            <span>主机在线</span><strong>{{ countDisplay(lastUpdated.hosts, errors.hosts, onlineCount) }}<small v-if="lastUpdated.hosts"> / {{ hosts.length }}</small></strong>
            <em v-if="errors.hosts">{{ errors.hosts }}</em><em v-else-if="offlineCount">{{ offlineCount }} 台离线</em><em v-else>运行状态</em>
          </button>
          <button class="metric-card" type="button" @click="activePage = 'alerts'">
            <span>待处理告警</span><strong>{{ countDisplay(lastUpdated.events, errors.events, activeEvents.length) }}</strong><em v-if="errors.events">{{ errors.events }}</em><em v-else>近 {{ eventDays }} 天数据</em>
          </button>
          <button class="metric-card" type="button" @click="activePage = 'services'">
            <span>服务健康</span><strong>{{ countDisplay(lastUpdated.services, errors.services, upServices) }}<small v-if="lastUpdated.services"> / {{ services.length }}</small></strong>
            <em v-if="errors.services">{{ errors.services }}</em><em v-else-if="downServices">{{ downServices }} 项异常</em><em v-else>健康探测</em>
          </button>
        </section>
        <p class="updated-at">最近刷新 {{ [lastUpdated.hosts, lastUpdated.events, lastUpdated.services].filter(Boolean).sort().at(-1) || '—' }}</p>
        <section class="section-card">
          <div class="section-title"><h3>异常主机</h3><button type="button" class="text-button" @click="activePage = 'hosts'">查看全部</button></div>
          <p v-if="errors.hosts" class="error-message">{{ errors.hosts }}</p>
          <article v-for="host in hosts.filter((item) => item.status === 'offline').slice(0, 3)" :key="host.id" class="compact-row">
            <span class="status-dot is-offline"></span><strong>{{ hostName(host) }}</strong><span>离线</span>
          </article>
          <p v-if="!errors.hosts && !offlineCount" class="empty-state compact-empty">没有离线主机</p>
        </section>
        <section class="section-card">
          <div class="section-title"><h3>最新告警</h3><button type="button" class="text-button" @click="activePage = 'alerts'">查看全部</button></div>
          <p v-if="errors.events" class="error-message">{{ errors.events }}</p>
          <article v-for="event in visibleEvents.slice(0, 4)" :key="event.id" class="event-row">
            <span class="status-dot" :class="eventStatusClass(event)"></span>
            <div><strong>{{ event.rule_name || '告警事件' }}</strong><small>{{ event.server_name || '未关联主机' }} · {{ formatTime(event.fired_at) }}</small></div>
            <span class="row-status">{{ eventStatusLabel(event) }}</span>
          </article>
          <p v-if="!errors.events && !visibleEvents.length" class="empty-state compact-empty">没有待处理告警</p>
        </section>
      </template>

      <template v-else-if="activePage === 'hosts'">
        <section class="summary-card">
          <div><p class="eyebrow">主机清单</p><h3>{{ onlineCount }} <span>/ {{ hosts.length }} 台在线</span></h3></div>
          <span v-if="offlineCount" class="count-warning">{{ offlineCount }} 台离线</span>
        </section>
        <p v-if="lastUpdated.hosts" class="updated-at">数据读取于 {{ lastUpdated.hosts }}</p>
        <p v-if="errors.hosts" class="error-message" role="alert">{{ errors.hosts }}</p>
        <section class="host-list">
          <label class="search-box"><span aria-hidden="true">⌕</span><input v-model="hostSearch" type="search" placeholder="搜索主机名称或地址" /></label>
          <article v-for="host in visibleHosts" :key="host.id" class="host-card">
            <span class="status-dot" :class="hostStatusClass(host)" />
            <div class="host-info"><h3>{{ hostName(host) }}</h3><p>{{ hostAddress(host) }}</p></div>
            <span class="host-status">{{ hostStatusLabel(host) }}</span>
          </article>
          <p v-if="!visibleHosts.length && !loading && !errors.hosts" class="empty-state">{{ hosts.length ? '没有匹配的主机' : '当前没有主机数据' }}</p>
        </section>
      </template>

      <template v-else-if="activePage === 'alerts'">
        <section class="section-card">
          <div class="section-title">
            <div><p class="eyebrow">告警事件</p><h3>{{ activeEvents.length }} 条待处理</h3></div>
            <select v-model.number="eventDays" class="days-select" aria-label="告警时间范围" @change="loadEvents">
              <option :value="30">近30天</option><option :value="365">近一年</option>
            </select>
          </div>
          <div class="filter-pills">
            <button type="button" :class="{ selected: eventFilter === 'active' }" @click="eventFilter = 'active'">待处理</button>
            <button type="button" :class="{ selected: eventFilter === 'all' }" @click="eventFilter = 'all'">全部</button>
            <button type="button" :class="{ selected: eventFilter === 'recovered' }" @click="eventFilter = 'recovered'">已恢复</button>
          </div>
        </section>
        <p v-if="errors.events" class="error-message" role="alert">{{ errors.events }}</p>
        <section class="section-card event-list">
          <article v-for="event in visibleEvents" :key="event.id" class="event-row">
            <span class="status-dot" :class="eventStatusClass(event)"></span>
            <div><strong>{{ event.rule_name || '告警事件' }}</strong><small>{{ event.server_name || '未关联主机' }} · {{ formatTime(event.fired_at) }}</small></div>
            <span class="row-status">{{ eventStatusLabel(event) }}</span>
          </article>
          <p v-if="!visibleEvents.length && !errors.events" class="empty-state">当前筛选没有告警事件</p>
        </section>
      </template>

      <template v-else>
        <section class="metric-grid service-metrics">
          <div class="metric-card"><span>正常</span><strong>{{ upServices }}</strong><em>探测可用</em></div>
          <div class="metric-card"><span>异常</span><strong>{{ downServices }}</strong><em>需关注</em></div>
          <div class="metric-card"><span>总数</span><strong>{{ services.length }}</strong><em>服务清单</em></div>
        </section>
        <p v-if="lastUpdated.services" class="updated-at">清单读取于 {{ lastUpdated.services }}</p>
        <p v-if="errors.services" class="error-message" role="alert">{{ errors.services }}</p>
        <section class="host-list">
          <article v-for="service in services" :key="service.key || service.id" class="service-card">
            <span class="status-dot" :class="serviceStatusClass(service)"></span>
            <div class="host-info">
              <h3>{{ service.name || service.service_name || '未命名服务' }}</h3>
              <p>{{ service.stale ? '探测数据已过期' : service.server_name || service.category || '服务健康状态' }}</p>
            </div>
            <span class="host-status">{{ serviceStatusLabel(service) }}</span>
          </article>
          <p v-if="!services.length && !loading && !errors.services" class="empty-state">当前没有服务数据</p>
        </section>
      </template>

      <nav class="bottom-nav" aria-label="主要导航">
        <button v-for="item in [{id:'overview',label:'概览',icon:'⌂'},{id:'hosts',label:'主机',icon:'▤'},{id:'alerts',label:'告警',icon:'!'},{id:'services',label:'服务',icon:'◇'}]" :key="item.id" type="button" :class="{ active: activePage === item.id }" @click="activePage = item.id">
          <span>{{ item.icon }}</span><small>{{ item.label }}</small>
        </button>
      </nav>
    </template>

    <footer>只读巡检版 · {{ new Date().getFullYear() }} OpsCenter</footer>
  </main>
</template>
