<template>
  <div class="view ds">
    <div class="view-head">
      <div>
        <h1 class="view-title">{{ kindName }}</h1>
        <p class="view-sub">{{ kindDesc }} · 只读纳管</p>
      </div>
      <div class="head-actions">
        <span class="tag" :class="statusClass" :title="statusMessage">{{ statusInfo.label }}</span>
        <span v-if="latencyText" class="muted tiny nowrap">{{ latencyText }}</span>
        <span v-if="endpointText" class="muted tiny mono endpoint" :title="endpointText">{{ endpointText }}</span>
        <span v-if="stampText" class="muted tiny nowrap">数据 {{ stampText }}<span v-if="stale" class="stale">数据陈旧</span></span>
        <button class="btn btn-sm" :disabled="loading" @click="reload">↻ 刷新</button>
      </div>
    </div>

    <div v-if="fatal && !hasData" class="card ds-error">
      <div class="big">⚠ {{ kindName }} 数据加载失败</div>
      <div class="muted">{{ fatal }}</div>
      <div><button class="btn btn-sm" :disabled="loading" @click="reload">重试</button></div>
    </div>
    <div v-else-if="loading && !hasData" class="loading"><span class="spinner"></span>正在读取 {{ kindName }} 数据…</div>
    <template v-else>
      <div v-if="partialText" class="partial">
        <span>⚠ 部分数据源异常：</span><span class="partial-item">{{ partialText }}</span>
      </div>

      <!-- Redis: keyspace 概览 + Key 浏览 -->
      <template v-if="kind === 'redis'">
        <section class="card">
          <div class="card-head">
            <h3>Keyspace 概览</h3>
            <span class="muted tiny">DBSIZE <b class="mono">{{ ksStats.dbsize ?? '—' }}</b></span>
          </div>
          <div class="ks-meta">
            <div class="kv"><span>内存用量</span><b class="mono">{{ ksMemText }}</b></div>
            <div class="kv"><span>maxmemory-policy</span><b class="mono">{{ ksStats.policy || '—' }}</b></div>
          </div>
          <StatBar v-if="ksMemPercent !== null" label="内存水位" :value="ksMemText" :percent="ksMemPercent" />
          <div v-if="ksStats.dbs.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>DB</th><th>Keys</th><th>Expires</th></tr></thead>
              <tbody>
                <tr v-for="(db, i) in ksStats.dbs" :key="i">
                  <td class="mono">{{ dbLabel(db) }}</td>
                  <td class="mono">{{ num(field(db, 'keys', 'key_count', 'size')) }}</td>
                  <td class="mono">{{ num(field(db, 'expires', 'expires_count')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🗄" :text="emptyText" />
        </section>

        <section class="card">
          <div class="card-head"><h3>Key 浏览</h3><span class="muted tiny">按需查询 · 不自动轮询</span></div>
          <div class="filters">
            <input v-model="keyPattern" class="input kw" placeholder="pattern，如 user:*" @keyup.enter="queryKeys" />
            <button class="btn btn-sm btn-primary" :disabled="keyLoading" @click="queryKeys">查询</button>
            <span v-if="keyErr" class="tiny err-text">{{ keyErr }} <button class="btn btn-sm" @click="fetchKeys">重试</button></span>
          </div>
          <div v-if="keys.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>Key</th><th>类型</th><th>TTL</th></tr></thead>
              <tbody>
                <tr v-for="k in keys" :key="String(field(k, 'key', 'name', 'id'))">
                  <td class="mono key-cell" :title="String(field(k, 'key', 'name', 'id'))">{{ field(k, 'key', 'name', 'id') }}</td>
                  <td><span class="tag tag-slate">{{ field(k, 'type', 'data_type') || '—' }}</span></td>
                  <td class="mono">{{ ttlText(field(k, 'ttl', 'ttl_seconds')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🔑" :text="keysEmptyText" />
          <div v-if="keys.length || keyStack.length || keyNextCursor" class="pager">
            <span class="muted tiny">cursor：{{ keyCursor }}<span v-if="keyNextCursor"> → {{ keyNextCursor }}</span></span>
            <div class="pager-btns">
              <button class="btn btn-sm" :disabled="keyLoading || !keyStack.length" @click="prevKeys">上一页</button>
              <button class="btn btn-sm" :disabled="keyLoading || !keyNextCursor" @click="nextKeys">下一页</button>
            </div>
          </div>
        </section>
      </template>

      <!-- RabbitMQ: queues -->
      <section v-else-if="kind === 'rabbitmq'" class="card">
        <div class="card-head"><h3>Queues（{{ queues.length }}）</h3></div>
        <div v-if="queues.length" class="table-scroll">
          <table class="table">
            <thead><tr><th>名称</th><th>Messages</th><th>Ready</th><th>Unacked</th><th>Consumers</th><th>State</th></tr></thead>
            <tbody>
              <tr v-for="q in queues" :key="String(field(q, 'name', 'queue'))">
                <td><b class="mono">{{ field(q, 'name', 'queue') || '—' }}</b></td>
                <td class="mono">{{ num(field(q, 'messages', 'message_count')) }}</td>
                <td class="mono">{{ num(field(q, 'ready', 'ready_messages')) }}</td>
                <td class="mono">{{ num(field(q, 'unacked', 'unacked_messages', 'unacknowledged')) }}</td>
                <td class="mono">{{ num(field(q, 'consumers', 'consumer_count')) }}</td>
                <td><span class="tag" :class="stateClass(field(q, 'state', 'status'))">{{ field(q, 'state', 'status') || '—' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState v-else icon="🐇" :text="emptyText" />
      </section>

      <!-- Kafka: Topics / Consumer Groups -->
      <section v-else-if="kind === 'kafka'" class="card">
        <div class="card-head">
          <h3>Kafka</h3>
          <div class="seg">
            <button class="btn btn-sm" :class="{ 'seg-on': kafkaTab === 'topics' }" @click="setKafkaTab('topics')">Topics（{{ topics.length }}）</button>
            <button class="btn btn-sm" :class="{ 'seg-on': kafkaTab === 'groups' }" @click="setKafkaTab('groups')">Consumer Groups（{{ groups.length }}）</button>
          </div>
        </div>
        <template v-if="kafkaTab === 'topics'">
          <div v-if="topics.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>Topic</th><th>Partitions</th><th>Replicas</th></tr></thead>
              <tbody>
                <tr v-for="t in topics" :key="String(field(t, 'name', 'topic'))">
                  <td><b class="mono">{{ field(t, 'name', 'topic') || '—' }}</b></td>
                  <td class="mono">{{ num(field(t, 'partitions', 'partition_count')) }}</td>
                  <td class="mono">{{ val(field(t, 'replicas', 'replication_factor', 'replica_count')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🟣" :text="emptyText" />
        </template>
        <template v-else>
          <div v-if="groups.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>Group</th><th>State</th><th>Members</th><th>Topics</th></tr></thead>
              <tbody>
                <tr v-for="g in groups" :key="String(field(g, 'group', 'group_id', 'name'))">
                  <td><b class="mono">{{ field(g, 'group', 'group_id', 'name') || '—' }}</b></td>
                  <td><span class="tag" :class="stateClass(field(g, 'state', 'status'))">{{ field(g, 'state', 'status') || '—' }}</span></td>
                  <td class="mono">{{ num(field(g, 'members', 'member_count')) }}</td>
                  <td class="tiny">{{ val(field(g, 'topics', 'topic_count', 'topics_count')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🟣" :text="emptyText" />
        </template>
      </section>

      <!-- ZooKeeper: path + children -->
      <section v-else-if="kind === 'zookeeper'" class="card">
        <div class="card-head"><h3>ZNode 浏览 <span class="mono tiny">{{ zkPath }}</span></h3><span class="muted tiny">点击有子节点的行进入下一层</span></div>
        <div class="filters">
          <input v-model="zkInput" class="input kw" placeholder="/path，如 /dubbo" @keyup.enter="goZk" />
          <button class="btn btn-sm btn-primary" :disabled="loading" @click="goZk">跳转</button>
        </div>
        <div class="crumbs">
          <button class="crumb" @click="goZkPath('/')">/</button>
          <template v-for="(seg, i) in zkCrumbs" :key="i">
            <span class="muted crumb-sep">/</span>
            <button class="crumb" @click="goZkPath(zkCrumbPath(i))">{{ seg }}</button>
          </template>
        </div>
        <div v-if="zkChildren.length" class="table-scroll">
          <table class="table">
            <thead><tr><th>名称</th><th>Has Children</th></tr></thead>
            <tbody>
              <tr v-for="c in zkChildren" :key="childName(c)" :class="{ 'row-click': c?.has_children }" @click="enterChild(c)">
                <td class="mono">{{ childName(c) }}</td>
                <td><span class="tag" :class="c?.has_children ? 'tag-ok' : 'tag-slate'">{{ c?.has_children ? '是' : '否' }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState v-else icon="🧩" :text="emptyText" />
      </section>

      <!-- Nacos: services / configs -->
      <section v-else-if="kind === 'nacos'" class="card">
        <div class="card-head">
          <h3>Nacos</h3>
          <div class="seg">
            <button class="btn btn-sm" :class="{ 'seg-on': nacosTab === 'services' }" @click="setNacosTab('services')">服务列表（{{ services.length }}）</button>
            <button class="btn btn-sm" :class="{ 'seg-on': nacosTab === 'configs' }" @click="setNacosTab('configs')">配置列表（{{ configs.length }}）</button>
          </div>
        </div>
        <template v-if="nacosTab === 'services'">
          <div v-if="services.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>服务名</th><th>Group</th><th>实例数</th><th>健康实例</th></tr></thead>
              <tbody>
                <tr v-for="s in services" :key="`${field(s, 'group', 'group_name') || ''}/${field(s, 'name', 'service_name')}`">
                  <td><b class="mono">{{ field(s, 'name', 'service_name') || '—' }}</b></td>
                  <td>{{ field(s, 'group', 'group_name') || '—' }}</td>
                  <td class="mono">{{ num(field(s, 'instance_count', 'instances')) }}</td>
                  <td class="mono">{{ num(field(s, 'healthy_count', 'healthy')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🧭" :text="emptyText" />
        </template>
        <template v-else>
          <div class="filters">
            <input v-model="nacosGroup" class="input kw" placeholder="group 过滤（可选）" @keyup.enter="applyNacosGroup" />
            <button class="btn btn-sm" :disabled="loading" @click="applyNacosGroup">查询</button>
          </div>
          <div v-if="configs.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>Data ID</th><th>Group</th><th>更新时间</th></tr></thead>
              <tbody>
                <tr v-for="c in configs" :key="`${field(c, 'group', 'group_name') || ''}/${field(c, 'data_id', 'dataId')}`">
                  <td class="mono">{{ field(c, 'data_id', 'dataId') || '—' }}</td>
                  <td>{{ field(c, 'group', 'group_name') || '—' }}</td>
                  <td class="muted tiny nowrap">{{ fmtTs(field(c, 'updated_at', 'updatedAt', 'modify_time', 'modified_at')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🧭" :text="emptyText" />
        </template>
      </section>

      <!-- MinIO: buckets / objects -->
      <section v-else-if="kind === 'minio'" class="card">
        <div class="card-head">
          <h3>{{ minioBucket ? `Objects · ${minioBucket}` : `Buckets（${buckets.length}）` }}</h3>
          <button v-if="minioBucket" class="btn btn-sm" :disabled="loading" @click="backBuckets">← 返回 Buckets</button>
        </div>
        <template v-if="minioBucket">
          <div v-if="objects.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>对象</th><th>大小</th><th>最后修改</th></tr></thead>
              <tbody>
                <tr v-for="o in objects" :key="String(field(o, 'name', 'key', 'object_name'))">
                  <td class="mono key-cell" :title="String(field(o, 'name', 'key', 'object_name'))">{{ field(o, 'name', 'key', 'object_name') }}</td>
                  <td class="mono">{{ fmtSize(field(o, 'size', 'size_bytes')) }}</td>
                  <td class="muted tiny nowrap">{{ fmtTs(field(o, 'last_modified', 'modified_at', 'mtime')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🪣" :text="emptyText" />
        </template>
        <template v-else>
          <div v-if="buckets.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>Bucket</th><th>创建时间</th></tr></thead>
              <tbody>
                <tr v-for="b in buckets" :key="String(field(b, 'name', 'bucket'))" class="row-click" @click="openBucket(b)">
                  <td><b class="mono">{{ field(b, 'name', 'bucket') || '—' }}</b></td>
                  <td class="muted tiny nowrap">{{ fmtTs(field(b, 'creation_date', 'created_at', 'creation_time')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🪣" :text="emptyText" />
        </template>
      </section>

      <!-- MongoDB: databases / collections -->
      <section v-else-if="kind === 'mongodb'" class="card">
        <div class="card-head">
          <h3>{{ mongoDbName ? `Collections · ${mongoDbName}` : `Databases（${databases.length}）` }}</h3>
          <button v-if="mongoDbName" class="btn btn-sm" :disabled="loading" @click="backDbs">← 返回 Databases</button>
        </div>
        <template v-if="mongoDbName">
          <div v-if="collections.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>集合</th><th>文档数</th><th>大小</th></tr></thead>
              <tbody>
                <tr v-for="c in collections" :key="String(field(c, 'name', 'collection'))">
                  <td><b class="mono">{{ field(c, 'name', 'collection') || '—' }}</b></td>
                  <td class="mono">{{ num(field(c, 'count', 'documents', 'document_count')) }}</td>
                  <td class="mono">{{ fmtSize(field(c, 'size', 'size_bytes', 'storage_size')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🍃" :text="emptyText" />
        </template>
        <template v-else>
          <div v-if="databases.length" class="table-scroll">
            <table class="table">
              <thead><tr><th>Database</th><th>磁盘大小</th><th>集合数</th></tr></thead>
              <tbody>
                <tr v-for="d in databases" :key="String(field(d, 'name', 'db'))" class="row-click" @click="openDb(d)">
                  <td><b class="mono">{{ field(d, 'name', 'db') || '—' }}</b></td>
                  <td class="mono">{{ fmtSize(field(d, 'size_on_disk', 'size', 'disk_size')) }}</td>
                  <td class="mono">{{ num(field(d, 'collections', 'collection_count')) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <EmptyState v-else icon="🍃" :text="emptyText" />
        </template>
      </section>
    </template>
  </div>
</template>

<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useTabActive } from '../../workbench/tabs'
import { dsApi, unwrap, mergeMeta, apiErrorText, DS_KINDS } from '../../api/dataservices'
import { fmtBytes, fmtTime } from '../../api'
import StatBar from '../../components/StatBar.vue'
import EmptyState from '../../components/EmptyState.vue'

const props = defineProps({
  kind: { type: String, required: true },
})

const route = useRoute()
const { isActive } = useTabActive(() => route.name)

const POLL_MS = 15000
const LIST_KEYS = ['items', 'list', 'children', 'keys', 'queues', 'topics', 'groups', 'services', 'configs', 'buckets', 'objects', 'databases', 'collections', 'rows', 'entries']

const kind = computed(() => props.kind)
const kindMeta = computed(() => DS_KINDS.find((k) => k.kind === kind.value) || { name: kind.value, desc: '' })
const kindName = computed(() => kindMeta.value.name)
const kindDesc = computed(() => kindMeta.value.desc)

const status = ref(null)
const meta = reactive({ ts: 0, cached: false, cacheAge: 0, partialErrors: [] })
const fatal = ref('')
const loading = ref(false)
const loadedOnce = ref(false)
const nowTick = ref(Date.now())

const ks = ref(null)
const keys = ref([])
const keyPattern = ref('*')
const keyLoading = ref(false)
const keyErr = ref('')
const keyCursor = ref(0)
const keyNextCursor = ref(0)
const keyStack = ref([])

const queues = ref([])
const kafkaTab = ref('topics')
const topics = ref([])
const groups = ref([])

const zkPath = ref('/')
const zkInput = ref('/')
const zkChildren = ref([])

const nacosTab = ref('services')
const services = ref([])
const configs = ref([])
const nacosGroup = ref('')

const buckets = ref([])
const minioBucket = ref('')
const objects = ref([])

const databases = ref([])
const mongoDbName = ref('')
const collections = ref([])

let primaryCtrl = null
let statusCtrl = null
let listCtrl = null
let pollTimer = null
let statusInFlight = false
let loadSeq = 0
let tabAlive = false

/* ── 状态徽标 ─────────────────────────────────────────────── */
function availableOf(d) {
  if (!d || typeof d !== 'object') return null
  if (typeof d.available === 'boolean') return d.available
  if (typeof d.ok === 'boolean') return d.ok
  const s = String(d.status ?? d.state ?? '').toLowerCase()
  if (['ok', 'up', 'available', 'healthy', 'running', 'connected'].includes(s)) return true
  if (['error', 'down', 'unavailable', 'unhealthy', 'failed', 'stopped'].includes(s)) return false
  return null
}
function configuredOf(d) {
  if (!d || typeof d !== 'object') return true
  return d.configured !== false
}
const statusInfo = computed(() => {
  const d = status.value
  if (!d) return { key: 'unknown', label: '检测中…' }
  if (!configuredOf(d)) return { key: 'unconfigured', label: '未配置' }
  const a = availableOf(d)
  if (a === true) return { key: 'ok', label: '可用' }
  if (a === false) return { key: 'err', label: '异常' }
  return { key: 'unknown', label: '未知' }
})
const statusClass = computed(() => ({ ok: 'tag-ok', err: 'tag-err' }[statusInfo.value.key] || 'tag-slate'))
const statusMessage = computed(() => status.value?.message || status.value?.error || '')
const statusUnavailable = computed(() => !configuredOf(status.value) || availableOf(status.value) === false)
const latencyText = computed(() => {
  const v = status.value?.latency_ms ?? status.value?.latency
  return typeof v === 'number' ? `${v} ms` : (v ? String(v) : '')
})
const endpointText = computed(() => status.value?.endpoint || status.value?.address || status.value?.url || '')
const stampText = computed(() => {
  if (!meta.ts) return ''
  const t = new Date(meta.ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
  return meta.cached ? `${t} · 缓存 ${meta.cacheAge.toFixed(0)}s` : t
})
const stale = computed(() => meta.ts > 0 && nowTick.value / 1000 - meta.ts > 60)
const partialText = computed(() => meta.partialErrors.join('；'))
const emptyText = computed(() => (statusUnavailable.value ? `${kindName.value} 未配置或不可达` : '暂无数据'))

/* ── 字段兼容读取 ─────────────────────────────────────────── */
function field(row, ...names) {
  if (row === null || row === undefined) return null
  if (typeof row !== 'object') return row
  for (const n of names) {
    const v = row[n]
    if (v !== undefined && v !== null) return v
  }
  return null
}
function listOf(d) {
  if (Array.isArray(d)) return d
  if (d && typeof d === 'object') {
    for (const k of LIST_KEYS) if (Array.isArray(d[k])) return d[k]
    if (Array.isArray(d.data)) return d.data
  }
  return []
}
function num(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? String(n) : String(v)
}
function val(v) {
  if (Array.isArray(v)) return v.length ? v.join(', ') : '—'
  if (v === null || v === undefined || v === '') return '—'
  return String(v)
}
function fmtSize(v) {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'string') return v
  return fmtBytes(v)
}
function fmtTs(v) {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'number') return fmtTime(new Date(v < 1e12 ? v * 1000 : v))
  return fmtTime(v)
}
function ttlText(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  if (n === -1) return '永久'
  if (n === -2) return '不存在'
  return `${n}s`
}
const OK_WORDS = ['running', 'stable', 'ok', 'up', 'active', 'connected', 'healthy', 'available', 'online']
const WARN_WORDS = ['idle', 'flow', 'paused', 'rebalancing', 'preparingrebalance', 'completingrebalance', 'completing', 'warning', 'warn', 'standby', 'unknown']
const ERR_WORDS = ['down', 'dead', 'stopped', 'error', 'failed', 'crashed', 'unhealthy', 'offline']
function stateClass(s) {
  const v = String(s || '').toLowerCase().replace(/[\s_-]/g, '')
  if (OK_WORDS.includes(v)) return 'tag-ok'
  if (ERR_WORDS.includes(v)) return 'tag-err'
  if (WARN_WORDS.includes(v)) return 'tag-warn'
  return 'tag-slate'
}

/* ── Redis keyspace ───────────────────────────────────────── */
const ksStats = computed(() => {
  const d = ks.value
  if (Array.isArray(d)) return { dbsize: null, dbs: d, memUsed: null, memMax: null, memUsedHuman: '', memMaxHuman: '', policy: '' }
  const o = d && typeof d === 'object' ? d : {}
  const mem = o.memory && typeof o.memory === 'object' ? o.memory : {}
  return {
    dbsize: field(o, 'dbsize', 'db_size', 'total_keys'),
    dbs: listOf(o.keyspace ?? o.dbs ?? o.databases ?? o.db),
    memUsed: field(mem, 'used', 'used_bytes') ?? field(o, 'used_memory', 'memory_used'),
    memMax: field(mem, 'max', 'max_bytes') ?? field(o, 'maxmemory', 'memory_max'),
    memUsedHuman: field(mem, 'used_human', 'usedHuman') ?? field(o, 'used_memory_human', 'memory_used_human'),
    memMaxHuman: field(mem, 'max_human', 'maxHuman') ?? field(o, 'maxmemory_human', 'memory_max_human'),
    policy: field(mem, 'policy', 'maxmemory_policy') ?? field(o, 'maxmemory_policy', 'memory_policy'),
  }
})
const ksMemPercent = computed(() => {
  const { memUsed, memMax } = ksStats.value
  const used = Number(memUsed)
  const max = Number(memMax)
  if (!Number.isFinite(used) || !Number.isFinite(max) || max <= 0) return null
  return Math.max(0, Math.min(100, Math.round((used / max) * 100)))
})
const ksMemText = computed(() => {
  const s = ksStats.value
  const used = s.memUsedHuman || (Number.isFinite(Number(s.memUsed)) ? fmtBytes(Number(s.memUsed)) : '—')
  const max = s.memMaxHuman || (Number.isFinite(Number(s.memMax)) && Number(s.memMax) > 0 ? fmtBytes(Number(s.memMax)) : '不限')
  return `${used} / ${max}`
})
function dbLabel(db) {
  const v = field(db, 'db', 'name', 'index', 'database')
  if (v === null || v === undefined) return '—'
  const s = String(v)
  return /^db/i.test(s) ? s : `db${s}`
}
const keysEmptyText = computed(() => {
  if (keyLoading.value) return '查询中…'
  if (keyErr.value) return '查询失败，请重试'
  if (statusUnavailable.value) return `${kindName.value} 未配置或不可达`
  return '输入 pattern 后点击查询'
})
function toCursor(v) {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n : 0
}

/* ── 数据加载 ─────────────────────────────────────────────── */
async function fetchPrimary(k, signal) {
  if (k === 'redis') {
    const u = unwrap(await dsApi.redisKeyspace({ signal, timeoutMs: 15000 }))
    ks.value = u.data ?? {}
    return u.meta
  }
  if (k === 'rabbitmq') {
    const u = unwrap(await dsApi.rabbitQueues({ signal, timeoutMs: 15000 }))
    queues.value = listOf(u.data)
    return u.meta
  }
  if (k === 'kafka') {
    if (kafkaTab.value === 'groups') {
      const u = unwrap(await dsApi.kafkaGroups({ signal, timeoutMs: 15000 }))
      groups.value = listOf(u.data)
      return u.meta
    }
    const u = unwrap(await dsApi.kafkaTopics({ signal, timeoutMs: 15000 }))
    topics.value = listOf(u.data)
    return u.meta
  }
  if (k === 'zookeeper') {
    const u = unwrap(await dsApi.zkTree({ path: zkPath.value || '/', depth: 1 }, { signal, timeoutMs: 15000 }))
    zkChildren.value = listOf(u.data)
    if (u.data && !Array.isArray(u.data) && u.data.path) {
      zkPath.value = String(u.data.path)
      zkInput.value = String(u.data.path)
    }
    return u.meta
  }
  if (k === 'nacos') {
    if (nacosTab.value === 'configs') {
      const u = unwrap(await dsApi.nacosConfigs({ group: nacosGroup.value.trim() || undefined, page: 1, size: 200 }, { signal, timeoutMs: 15000 }))
      configs.value = listOf(u.data)
      return u.meta
    }
    const u = unwrap(await dsApi.nacosServices({ signal, timeoutMs: 15000 }))
    services.value = listOf(u.data)
    return u.meta
  }
  if (k === 'minio') {
    if (minioBucket.value) {
      const u = unwrap(await dsApi.minioObjects({ bucket: minioBucket.value, prefix: '', limit: 100 }, { signal, timeoutMs: 15000 }))
      objects.value = listOf(u.data)
      return u.meta
    }
    const u = unwrap(await dsApi.minioBuckets({ signal, timeoutMs: 15000 }))
    buckets.value = listOf(u.data)
    return u.meta
  }
  if (k === 'mongodb') {
    if (mongoDbName.value) {
      const u = unwrap(await dsApi.mongoCollections({ db: mongoDbName.value, limit: 100 }, { signal, timeoutMs: 15000 }))
      collections.value = listOf(u.data)
      return u.meta
    }
    const u = unwrap(await dsApi.mongoDatabases({ signal, timeoutMs: 15000 }))
    databases.value = listOf(u.data)
    return u.meta
  }
  return { ts: 0, cached: false, cacheAge: 0, partialErrors: [] }
}

async function loadAll() {
  if (!kind.value) return
  const seq = ++loadSeq
  const k = kind.value
  primaryCtrl?.abort()
  const ctrl = new AbortController()
  primaryCtrl = ctrl
  loading.value = true
  try {
    const [sr, ir] = await Promise.allSettled([
      dsApi.status(k, { signal: ctrl.signal, timeoutMs: 15000 }),
      fetchPrimary(k, ctrl.signal),
    ])
    if (seq !== loadSeq || kind.value !== k) return
    const errs = []
    const metas = []
    if (sr.status === 'fulfilled') {
      const u = unwrap(sr.value)
      status.value = u.data
      metas.push(u.meta)
    } else if (sr.reason?.name !== 'AbortError') {
      errs.push(`状态读取失败：${apiErrorText(sr.reason)}`)
    }
    if (ir.status === 'fulfilled') {
      metas.push(ir.value)
    } else if (ir.reason?.name !== 'AbortError') {
      errs.push(`列表读取失败：${apiErrorText(ir.reason)}`)
    }
    const m = mergeMeta(metas)
    if (m.ts) meta.ts = m.ts
    meta.cached = m.cached
    meta.cacheAge = m.cacheAge
    meta.partialErrors = [...m.partialErrors, ...errs]
    fatal.value = sr.status === 'rejected' && ir.status === 'rejected' ? errs.join('；') : ''
    loadedOnce.value = true
    nowTick.value = Date.now()
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function loadStatusOnly() {
  if (statusInFlight || !kind.value) return
  statusInFlight = true
  const k = kind.value
  statusCtrl?.abort()
  const ctrl = new AbortController()
  statusCtrl = ctrl
  try {
    const u = unwrap(await dsApi.status(k, { signal: ctrl.signal, timeoutMs: 15000 }))
    if (kind.value !== k) return
    status.value = u.data
    if (u.meta.ts) meta.ts = u.meta.ts
    nowTick.value = Date.now()
  } catch { /* 轮询失败静默，保留上次状态 */ } finally {
    statusInFlight = false
  }
}

/* ── 交互加载（不带轮询） ─────────────────────────────────── */
async function fetchKeys() {
  if (keyLoading.value) return
  keyLoading.value = true
  keyErr.value = ''
  listCtrl?.abort()
  const ctrl = new AbortController()
  listCtrl = ctrl
  try {
    const u = unwrap(await dsApi.redisKeys(
      { pattern: keyPattern.value.trim() || '*', cursor: keyCursor.value, count: 100 },
      { signal: ctrl.signal, timeoutMs: 15000 },
    ))
    keys.value = listOf(u.data)
    keyNextCursor.value = toCursor(u.data?.cursor ?? u.data?.next_cursor ?? u.data?.nextCursor)
  } catch (e) {
    if (e.name !== 'AbortError') {
      keyErr.value = apiErrorText(e)
      keys.value = []
      keyNextCursor.value = 0
    }
  } finally {
    keyLoading.value = false
  }
}
function queryKeys() {
  keyStack.value = []
  keyCursor.value = 0
  fetchKeys()
}
function nextKeys() {
  if (!keyNextCursor.value) return
  keyStack.value = [...keyStack.value, keyCursor.value]
  keyCursor.value = keyNextCursor.value
  fetchKeys()
}
function prevKeys() {
  if (!keyStack.value.length) return
  keyCursor.value = keyStack.value[keyStack.value.length - 1]
  keyStack.value = keyStack.value.slice(0, -1)
  fetchKeys()
}

const zkCrumbs = computed(() => zkPath.value.split('/').filter(Boolean))
function zkCrumbPath(i) {
  return '/' + zkCrumbs.value.slice(0, i + 1).join('/')
}
function goZkPath(p) {
  zkPath.value = (p || '/').trim() || '/'
  zkInput.value = zkPath.value
  zkChildren.value = []
  loadAll()
}
function goZk() {
  goZkPath(zkInput.value)
}
function childName(c) {
  return String(field(c, 'name', 'path') ?? c ?? '')
}
function enterChild(c) {
  if (!c?.has_children) return
  const name = childName(c)
  const base = field(c, 'path') || (zkPath.value === '/' ? `/${name}` : `${zkPath.value.replace(/\/+$/, '')}/${name}`)
  goZkPath(base)
}
function setKafkaTab(t) {
  if (kafkaTab.value === t) return
  kafkaTab.value = t
  loadAll()
}
function setNacosTab(t) {
  if (nacosTab.value === t) return
  nacosTab.value = t
  loadAll()
}
function applyNacosGroup() {
  loadAll()
}
function openBucket(b) {
  minioBucket.value = String(field(b, 'name', 'bucket') ?? b)
  objects.value = []
  loadAll()
}
function backBuckets() {
  minioBucket.value = ''
  objects.value = []
  loadAll()
}
function openDb(d) {
  mongoDbName.value = String(field(d, 'name', 'db') ?? d)
  collections.value = []
  loadAll()
}
function backDbs() {
  mongoDbName.value = ''
  collections.value = []
  loadAll()
}

/* ── 生命周期与轮询 ───────────────────────────────────────── */
const primaryCount = computed(() => {
  switch (kind.value) {
    case 'redis': return ks.value ? 1 : 0
    case 'rabbitmq': return queues.value.length
    case 'kafka': return kafkaTab.value === 'groups' ? groups.value.length : topics.value.length
    case 'zookeeper': return zkChildren.value.length
    case 'nacos': return nacosTab.value === 'configs' ? configs.value.length : services.value.length
    case 'minio': return minioBucket.value ? objects.value.length : buckets.value.length
    case 'mongodb': return mongoDbName.value ? collections.value.length : databases.value.length
    default: return 0
  }
})
const hasData = computed(() => !!status.value || primaryCount.value > 0)

function reload() {
  loadAll()
}
function tick() {
  nowTick.value = Date.now()
  if (!isActive.value || !tabAlive || document.hidden || loading.value) return
  loadStatusOnly()
}
function startPolling() {
  stopPolling()
  pollTimer = setInterval(tick, POLL_MS)
}
function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}
function ensureFresh() {
  if (loading.value) return
  if (!loadedOnce.value || nowTick.value / 1000 - meta.ts > 15) loadAll()
}
function resetState() {
  loadSeq++
  primaryCtrl?.abort()
  statusCtrl?.abort()
  listCtrl?.abort()
  status.value = null
  ks.value = null
  keys.value = []
  keyErr.value = ''
  keyCursor.value = 0
  keyNextCursor.value = 0
  keyStack.value = []
  queues.value = []
  topics.value = []
  groups.value = []
  kafkaTab.value = 'topics'
  zkPath.value = '/'
  zkInput.value = '/'
  zkChildren.value = []
  services.value = []
  configs.value = []
  nacosTab.value = 'services'
  nacosGroup.value = ''
  buckets.value = []
  minioBucket.value = ''
  objects.value = []
  databases.value = []
  mongoDbName.value = ''
  collections.value = []
  meta.ts = 0
  meta.cached = false
  meta.cacheAge = 0
  meta.partialErrors = []
  fatal.value = ''
  loading.value = false
  loadedOnce.value = false
}

watch(kind, () => {
  resetState()
  if (tabAlive && isActive.value) loadAll()
})
watch(isActive, (v) => {
  if (!tabAlive) return
  if (v) {
    ensureFresh()
    startPolling()
  } else {
    stopPolling()
  }
})

onMounted(() => {
  tabAlive = true
  ensureFresh()
  if (isActive.value) startPolling()
})
onActivated(() => {
  tabAlive = true
  ensureFresh()
  if (isActive.value) startPolling()
})
onDeactivated(() => {
  tabAlive = false
  stopPolling()
})
onUnmounted(() => {
  tabAlive = false
  stopPolling()
  primaryCtrl?.abort()
  statusCtrl?.abort()
  listCtrl?.abort()
})
</script>

<style scoped>
.ds { display: flex; flex-direction: column; gap: 12px; }
.head-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.endpoint { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stamp { font-size: 12px; }
.stale { display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; border: 1px solid #fde68a; }
.partial { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; padding: 8px 12px; border: 1px solid #fde68a; background: #fffbeb; color: #b45309; border-radius: 8px; font-size: 12px; }
.partial-item { word-break: break-all; }
.ds-error { display: flex; flex-direction: column; gap: 10px; align-items: flex-start; }
.ds-error .big { font-weight: 700; color: var(--err); }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.card-head h3 { margin: 0; font-size: 14px; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }
.filters .input { width: auto; }
.filters .kw { min-width: 220px; }
.ks-meta { display: flex; gap: 18px; flex-wrap: wrap; margin-bottom: 10px; }
.kv { display: flex; gap: 10px; align-items: center; font-size: 13px; }
.kv span { color: var(--muted); }
.kv b { font-weight: 600; word-break: break-all; }
.table-scroll { overflow: auto; }
.row-click { cursor: pointer; }
.key-cell { max-width: 460px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.err-text { color: var(--err); font-weight: 600; }
.nowrap { white-space: nowrap; }
.tiny { font-size: 12px; }
.pager { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.pager-btns { display: flex; gap: 8px; }
.seg { display: flex; gap: 0; }
.seg .btn { border-radius: 0; }
.seg .btn:first-child { border-radius: 6px 0 0 6px; }
.seg .btn:last-child { border-radius: 0 6px 6px 0; }
.seg-on { background: var(--brand); border-color: var(--brand); color: #fff; }
.seg-on:hover { color: #fff; }
.crumbs { display: flex; align-items: center; flex-wrap: wrap; gap: 2px; margin-bottom: 10px; }
.crumb { border: 0; background: transparent; color: var(--brand); cursor: pointer; padding: 1px 4px; font-size: 12px; font-family: Consolas, 'Courier New', monospace; }
.crumb:hover { text-decoration: underline; }
.crumb-sep { font-size: 12px; }
.tag-ok { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #ecfdf5; color: #059669; }
.tag-warn { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fffbeb; color: #b45309; }
.tag-err { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; background: #fef2f2; color: #dc2626; }
</style>
