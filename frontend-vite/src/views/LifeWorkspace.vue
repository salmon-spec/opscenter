<template>
  <div class="life-workspace">
    <div class="life-toolbar">
      <div class="tabs" role="tablist" aria-label="模块视图">
        <button v-for="tab in tabs" :key="tab.key" class="btn" :class="{ selected: active === tab.key }" role="tab" :aria-selected="active === tab.key" @click="active = tab.key">{{ tab.name }}</button>
      </div>
      <div class="actions"><button class="btn" :disabled="loading" @click="load">刷新</button><button v-if="active !== 'manage'" class="btn btn-primary" @click="openForm()">{{ config.createLabel }}</button></div>
    </div>
    <p v-if="error" role="alert" class="notice error">{{ error }}</p>
    <div v-if="loading" class="muted" role="status">正在读取 {{ config.title }}</div>
    <div v-if="active === 'overview'" class="overview-grid">
      <div v-for="stat in stats" :key="stat.label" class="stat"><span>{{ stat.label }}</span><strong>{{ stat.value }}</strong></div>
      <p v-if="kind === 'subscriptions'" class="muted wide">费用为按当前周期折算的预计支出，按原币种分别统计。</p>
    </div>
    <section v-if="active === 'manage'" class="management">
      <label class="management-picker">管理内容<select v-model="resourceKey" class="input" aria-label="管理内容"><option v-for="r in resourceSpecs" :key="r.key" :value="r.key">{{ r.title }}</option></select></label>
      <LifeResources v-if="resourceSpec" :key="kind + ':' + resourceKey" :kind="kind" :spec="resourceSpec" />
    </section>
    <LifeCalendar v-if="active === 'calendar'" :rows="rows" :kind="kind" @edit="openForm" />
    <section v-if="active === 'board'" class="task-board"><article v-for="column in boardColumns" :key="column.key" class="board-column"><h3>{{ column.name }} · {{ column.rows.length }}</h3><div v-for="row in column.rows" :key="row.id" class="task-card"><b>{{ row.title }}</b><p>{{ rowInfo(row) }}</p><small>{{ displayDate(rowDate(row)) }}</small><progress :value="row.done ? 1 : row.percent_done || 0" max="1"></progress><div class="actions"><button class="btn btn-sm" @click="openForm(row)">编辑</button><button class="btn btn-sm" :disabled="busy" @click="complete(row)">{{ row.done?'重新打开':'完成' }}</button><button class="btn btn-sm" @click="showDetails(row)">详情</button></div></div></article></section>
    <template v-if="!['reminders','settings','manage','calendar','board'].includes(active)">
      <div class="filters">
        <input v-model="search" class="input" aria-label="搜索" :placeholder="kind === 'home-inventory' ? '搜索名称、型号、序列号、位置' : '搜索名称或备注'">
        <select v-model="filter" class="input" aria-label="状态筛选"><option value="all">全部</option><option value="open">{{ kind === 'life-reminders' ? '未完成' : '使用中' }}</option><option value="due">未来30天到期</option></select>
        <button v-if="kind === 'subscriptions'" class="btn" @click="exportData">导出 CSV</button>
        <button v-if="kind === 'subscriptions'" class="btn" @click="importOpen = true">导入 CSV</button>
        <button v-if="kind === 'home-inventory'" class="btn" @click="exportData">导出清单与附件</button>
        <button v-if="kind === 'life-reminders'" class="btn" @click="addProject">新建清单</button>
        <button v-if="kind !== 'subscriptions'" class="btn" @click="addLabel">新建标签</button>
      </div>
      <p v-if="!loading && !filtered.length" class="empty">{{ search ? '没有匹配记录' : '暂无记录，从新建开始。' }}</p>
      <div v-else class="table-wrap">
        <table><thead><tr><th>{{ kind === 'home-inventory' ? '物品与位置' : '名称' }}</th><th>{{ kind === 'subscriptions' ? '周期费用' : kind === 'home-inventory' ? '型号 / 序列号' : '清单 / 优先级' }}</th><th>{{ kind === 'home-inventory' ? '保修日期' : '到期日期' }}</th><th>状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="row in filtered" :key="row.id">
            <td><b>{{ row.name || row.title }}</b><small>{{ secondary(row) }}</small></td>
            <td>{{ rowInfo(row) }}</td><td>{{ displayDate(rowDate(row)) }}</td>
            <td><span class="badge">{{ rowState(row) }}</span></td>
            <td class="row-actions">
              <button class="btn btn-sm" @click="openForm(row)">编辑</button>
              <button v-if="kind === 'life-reminders'" class="btn btn-sm" :disabled="busy" @click="complete(row)">{{ row.done ? '重新打开' : '完成' }}</button>
              <button v-if="kind !== 'subscriptions'" class="btn btn-sm" @click="showDetails(row)">{{ kind === 'home-inventory' ? '凭证与维护' : '详情与附件' }}</button>
              <button v-if="kind === 'home-inventory'" class="btn btn-sm" @click="confirmRecordAction(row,'duplicate')">复制</button><button class="btn btn-sm" @click="confirmRecordAction(row,'delete')">删除</button>
            </td>
          </tr></tbody>
        </table>
      </div>
    </template>
    <template v-if="active === 'reminders'">
      <div class="filters"><span class="badge">{{ notification.mode === 'webhook' ? '外部通知已启用' : '仅站内提醒' }}</span><span class="muted">最近扫描 {{ displayDate(reminderState.checked_at) }}</span><button class="btn" :disabled="busy" @click="scanReminders">立即扫描</button></div>
      <p v-for="(message, source) in reminderState.errors || {}" :key="source" class="notice error">{{ source }}：{{ message }}；该来源提醒暂缓投递。</p>
      <p v-if="!reminders.length" class="empty">还没有提醒。新建带日期的待办后，提醒会持久保存。</p>
      <article v-for="row in reminders" :key="row.id" class="reminder-row">
        <div><b>{{ row.title }}</b><small>{{ sourceName(row.source) }} · {{ displayDate(row.due_at) }} · {{ deliveryState(row) }}</small><small v-if="row.last_error" class="error">{{ row.last_error }}</small></div>
        <div class="actions"><button v-if="row.source !== 'tasks'" class="btn btn-sm" @click="goSource(row.source)">查看来源</button><button v-if="row.source !== 'tasks'" class="btn btn-sm" :disabled="busy || row.state === 'cancelled'" @click="createLinkedTask(row)">创建处理待办</button><button class="btn btn-sm" :disabled="row.acknowledged || row.state === 'cancelled'" @click="reminderAction(row, 'ack')">已知晓</button><button class="btn btn-sm" :disabled="row.state === 'cancelled'" @click="reminderAction(row, 'snooze')">稍后1小时</button></div>
      </article>
    </template>
    <form v-if="active === 'settings'" class="settings" @submit.prevent="saveNotification">
      <h3>提醒投递</h3><p class="muted">{{ notification.configured ? '已保存通知地址。留空可保留原地址。' : '尚未配置外部通道，当前只提供站内提醒。' }}</p>
      <label>Webhook 地址<input v-model="webhook" class="input" type="url" placeholder="https://…" autocomplete="off"></label>
      <label class="check"><input v-model="externalEnabled" type="checkbox">启用外部投递</label>
      <p class="muted">只向你配置的地址发送提醒标题、来源、时间与投递编号。22:00–08:00免打扰；失败最多重试3次，超时可能导致重复接收。</p>
      <button class="btn btn-primary" :disabled="busy">保存设置</button>
    </form>

    <div v-if="formOpen" class="modal-backdrop" @click.self="closeForm">
      <section class="life-modal" role="dialog" aria-modal="true" :aria-label="editing ? '编辑记录' : config.createLabel">
        <div class="modal-heading"><h3>{{ editing ? '编辑记录' : config.createLabel }}</h3><button class="btn" :disabled="busy" @click="closeForm">关闭</button></div>
        <form @submit.prevent="save">
          <div class="form-grid">
            <label v-for="field in fields" :key="field.key" :class="{ wide: field.type === 'textarea' }">
              <span>{{ field.label }}{{ field.required ? ' *' : '' }}</span>
              <textarea v-if="field.type === 'textarea'" v-model="form[field.key]" class="input" :maxlength="field.max || 4000" rows="3"></textarea>
              <select v-else-if="field.type === 'select' || field.type === 'multiple'" v-model="form[field.key]" class="input" :multiple="field.type === 'multiple'" :required="field.required"><option v-if="!field.required && field.type !== 'multiple'" value="">未设置</option><option v-for="option in options(field)" :key="option.value" :value="option.value">{{ option.label }}</option></select>
              <input v-else-if="field.type === 'checkbox'" v-model="form[field.key]" type="checkbox">
              <input v-else v-model="form[field.key]" class="input" :type="field.type || 'text'" :required="field.required" :min="field.min" :max="field.maxNumber" :step="field.type === 'number' ? field.step || '1' : undefined" :maxlength="field.max || 255" :pattern="field.pattern">
            </label>
          </div>
          <p v-if="formError" role="alert" class="notice error">{{ formError }}</p>
          <div class="form-footer"><span v-if="kind === 'subscriptions'" class="muted">登记续费日期不会向商家付款或取消订阅。</span><button class="btn btn-primary" :disabled="busy">{{ busy ? '保存中…' : '保存' }}</button></div>
        </form>
      </section>
    </div>

    <div v-if="detail" class="modal-backdrop" @click.self="detail = null">
      <section class="life-modal" role="dialog" aria-modal="true" aria-label="凭证与维护">
        <div class="modal-heading"><h3>{{ detail.name || detail.title }} · {{ kind === 'home-inventory' ? '凭证与维护' : '任务详情' }}</h3><button class="btn" @click="detail = null">关闭</button></div>
        <h4>照片、发票与保修凭证</h4><p class="muted">单个附件不超过5MiB，支持PDF、PNG、JPEG、WebP。</p>
        <input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp" :disabled="busy" aria-label="上传凭证" @change="uploadFile">
        <div v-for="attachment in detail.attachments || []" :key="attachment.id" class="reminder-row"><span>{{ attachment.file?.name || attachment.name || attachment.title || '附件' }}</span><button class="btn btn-sm" @click="downloadAttachment(attachment)">下载</button></div>
        <template v-if="kind === 'home-inventory'"><h4>维护记录</h4>
        <form class="form-grid" @submit.prevent="addMaintenance"><label>项目<input v-model="maintenanceForm.name" class="input" required maxlength="255"></label><label>完成日期<input v-model="maintenanceForm.completedDate" class="input" type="date" required></label><label>费用<input v-model="maintenanceForm.cost" class="input" type="number" min="0" step="0.01"></label><label>说明<input v-model="maintenanceForm.description" class="input" maxlength="4000"></label><button class="btn" :disabled="busy">添加维护记录</button></form>
        <article v-for="entry in maintenanceRows" :key="entry.id" class="reminder-row"><div><b>{{ entry.name }}</b><small>{{ displayDate(entry.completedDate) }} · {{ entry.cost }} · {{ entry.description }}</small></div></article></template>
        <template v-else><h4>任务评论</h4><form @submit.prevent="addComment"><label>评论内容<textarea v-model="commentText" class="input" required maxlength="4000" @input="commentKey = operationId()"></textarea></label><button class="btn" :disabled="busy">发表评论</button></form><article v-for="c in commentRows" :key="c.id" class="reminder-row"><div><b>{{ c.author?.name || c.author?.username || '评论' }}</b><p>{{ c.comment }}</p></div></article><h4>关联任务</h4><p v-if="!Object.keys(detail.related_tasks || {}).length" class="muted">暂无关联任务</p><div v-for="(tasks, relation) in detail.related_tasks || {}" :key="relation"><b>{{ relationName(relation) }}</b><p v-for="t in tasks" :key="t.id">{{ t.title }} · {{ t.done ? '已完成' : '未完成' }}</p></div><button class="btn" @click="newSubtask">新建子任务</button></template>
      </section>
    </div>
    <div v-if="recordAction" class="modal-backdrop"><section class="life-modal" role="dialog" aria-modal="true" aria-label="确认记录操作"><h3>{{ recordAction.action === 'delete' ? '删除记录' : '复制物品' }}</h3><p>{{ recordAction.action === 'delete' ? '删除后不可恢复。确认删除' : '创建包含附件与维护记录的副本：' }}“{{ recordAction.row.name || recordAction.row.title }}”？</p><p v-if="actionError" class="error">{{ actionError }}</p><div class="actions"><button class="btn" :disabled="busy" @click="recordAction=null">取消</button><button class="btn btn-primary" :disabled="busy" @click="applyRecordAction">确认{{ recordAction.action === 'delete' ? '删除' : '复制' }}</button></div></section></div>

    <div v-if="importOpen" class="modal-backdrop" @click.self="importOpen = false">
      <section class="life-modal" role="dialog" aria-modal="true" aria-label="导入订阅">
        <div class="modal-heading"><h3>导入订阅 CSV</h3><button class="btn" :disabled="busy" @click="importOpen = false">关闭</button></div>
        <p class="muted">请按导出CSV的表头准备，每批最多500条。先预览，修正重名和错误行后再确认。</p><input type="file" accept=".csv" aria-label="选择 CSV" @change="readCsv">
        <textarea v-model="csvText" class="input csv-text" aria-label="CSV 内容" @input="importPreview = null"></textarea><button class="btn" :disabled="busy" @click="previewImport">预览</button>
        <template v-if="importPreview"><p>{{ importPreview.rows.length }} 条有效记录，{{ importPreview.errors.length }} 条错误</p><p v-for="row in importPreview.errors" :key="row.line" class="error">第{{ row.line }}行：{{ row.error }}</p><p v-for="row in importPreview.rows" :key="row.line">{{ row.data.name }} · {{ row.data.price }} {{ row.duplicate ? '（重名，请修正）' : '' }}</p><button class="btn btn-primary" :disabled="busy || importPreview.errors.length > 0 || importPreview.rows.some(row => row.duplicate)" @click="confirmImport">确认导入</button></template>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, getAccessToken, toast } from '../api'
import { CONFIG, formFields, normalizeForm, operationId } from '../utils/life'
import LifeResources from './LifeResources.vue'
import LifeCalendar from './LifeCalendar.vue'

const props = defineProps({ kind: { type: String, required: true } })
const router = useRouter()
const kind = computed(() => props.kind)
const config = computed(() => CONFIG[kind.value])
const base = computed(() => `/modules/${kind.value}/api/v1`)
const active = ref('overview'), rows = ref([]), catalog = ref({}), overview = ref({})
const resourceSpecs=ref([]), resourceKey=ref('')
const resourceSpec=computed(()=>resourceSpecs.value.find(r=>r.key===resourceKey.value))
const loading = ref(false), busy = ref(false), error = ref(''), search = ref(''), filter = ref('all')
const formOpen = ref(false), form = ref({}), editing = ref(null), formError = ref('')
const commentText=ref(''), commentRows=ref([]), recordAction=ref(null),actionError=ref('')
let commentKey=operationId(),recordActionKey=''
const detail = ref(null), maintenanceRows = ref([]), maintenanceForm = ref({name:'', completedDate:new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}), cost:'0', description:''})
const reminders = ref([]), reminderState = ref({}), notification = ref({}), webhook = ref(''), externalEnabled = ref(false)
const importOpen = ref(false), csvText = ref(''), importPreview = ref(null)
let formKey = '', importKey = '', revision = 0
const completionKeys = new Map()
const fields = computed(() => formFields(kind.value, Boolean(editing.value)))
const tabs = computed(() => [{key:'overview',name:'概览'},{key:'list',name:kind.value === 'life-reminders' ? '任务列表' : '全部记录'}, {key:'upcoming',name:kind.value === 'home-inventory' ? '即将过保' : '未来7天'}, ...(kind.value === 'life-reminders' ? [{key:'today',name:'今天'},{key:'reminders',name:'提醒记录'},{key:'settings',name:'通知设置'}] : []),{key:'calendar',name:'日期日历'},...(kind.value==='life-reminders'?[{key:'board',name:'任务看板'}]:[]),{key:'manage',name:'业务管理'}])
const stats = computed(() => {
  const d = overview.value
  if (kind.value === 'subscriptions') return [{label:'订阅总数',value:d.total || 0},{label:'使用中',value:d.active || 0},...Object.entries(d.estimated_annual || {}).map(([id,v])=>({label:`预计年支出 · ${currency(id)}`,value:v}))]
  if (kind.value === 'home-inventory') return [{label:'家庭物品',value:d.total || 0},{label:'收纳位置',value:d.locations || 0},{label:'已登记保修',value:d.with_warranty || 0}]
  return [{label:'任务总数',value:d.total || 0},{label:'未完成',value:d.open || 0},{label:'提醒方式',value:notification.value.mode === 'webhook' ? '外部通知' : '仅站内'}]
})
const filtered = computed(() => rows.value.filter(row => {
  if (search.value && !JSON.stringify(row).toLowerCase().includes(search.value.toLowerCase())) return false
  if (filter.value === 'open' && (row.inactive || row.archived || row.done)) return false
  const due = rowDate(row), days = due ? (new Date(due).getTime() - Date.now()) / 86400000 : Infinity
  if (filter.value === 'due' && (days < -1 || days > 30 || row.done || row.inactive || row.lifetimeWarranty)) return false
  if (active.value === 'upcoming' && (days < -1 || days > (kind.value === 'home-inventory' ? 30 : 7) || row.done || row.inactive || row.lifetimeWarranty)) return false
  if (active.value === 'today' && (!due || new Date(due).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}) !== new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}) || row.done)) return false
  return true
}))
const boardColumns=computed(()=>[{key:'todo',name:'待办',rows:rows.value.filter(r=>!r.done && !r.percent_done)},{key:'doing',name:'进行中',rows:rows.value.filter(r=>!r.done && r.percent_done>0)},{key:'done',name:'已完成',rows:rows.value.filter(r=>r.done)}])
function currency(id) { return catalog.value.currencies?.find(x=>String(x.id)===String(id))?.code || `币种${id}` }
function displayDate(value) { if (!value || String(value).startsWith('0001-')) return '未登记'; return String(value).length <= 10 ? value : new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}) }
function rowDate(row) { return kind.value === 'subscriptions' ? row.next_payment : kind.value === 'home-inventory' ? row.warrantyExpires : row.due_date?.startsWith('0001-') ? '' : row.due_date }
function rowInfo(row) { if(kind.value === 'subscriptions') return `${row.price} ${currency(row.currency_id)} / ${row.frequency}${['','天','周','月','年'][row.cycle]}`; if(kind.value === 'home-inventory') return [row.modelNumber,row.serialNumber].filter(Boolean).join(' / ') || '—'; return `${catalog.value.projects?.find(x=>x.id===row.project_id)?.title || '清单'} / P${row.priority}` }
function secondary(row) { return kind.value === 'home-inventory' ? row.parent?.name || row.location?.name || row.description : kind.value === 'life-reminders' ? row.description : row.notes }
function rowState(row) { if(kind.value === 'life-reminders') return row.done ? '已完成' : row.repeat_after ? '重复待办' : '待办'; if(kind.value === 'home-inventory') return row.archived ? '已归档' : row.lifetimeWarranty ? '终身保修' : '使用中'; return row.inactive ? '已停用' : row.auto_renew ? '自动续订已登记' : '手动续订' }
function sourceName(source) { return {'tasks':'生活待办','subscriptions':'订阅与续费','home-inventory':'家庭物品'}[source] || source }
function deliveryState(row) { return row.acknowledged ? '已知晓' : {pending:'等待投递',in_app:'站内提醒',sent:'已投递',failed:'投递失败',cancelled:'已取消'}[row.state] || row.state }
function goSource(source) { router.push('/extensions/'+source) }
function options(field) {
  const values = field.options || catalog.value[field.catalog] || []
  return values.map(x=>typeof x === 'object' && 'value' in x ? x : {value:x.id,label:field.catalog==='types' && ['Item','Location'].includes(x.name) ? (x.isLocation ? '位置' : '物品') : x.name || x.title || x.code})
}
async function load() {
  const rev = ++revision; loading.value = true; error.value = ''
  try {
    const [list, lookups, summary, management] = await Promise.all([api.get(base.value+config.value.endpoint,{}, {timeoutMs:25000}),api.get(base.value+'/catalog',{}, {timeoutMs:25000}),api.get(base.value+'/summary',{}, {timeoutMs:25000}),api.get(base.value+'/workspace/catalog')])
    if(rev !== revision) return
    rows.value = list.items || []; catalog.value = lookups; overview.value = summary.data || {};resourceSpecs.value=management.resources || [];if(!resourceSpecs.value.some(r=>r.key===resourceKey.value))resourceKey.value=resourceSpecs.value[0]?.key || ''
    if(kind.value === 'life-reminders') await loadReminders()
  } catch(e) { if(rev === revision) error.value=e.message } finally { if(rev === revision) loading.value=false }
}
async function openForm(row) {
  try {
    const current = row && kind.value === 'home-inventory' ? await api.get(base.value+'/items/'+row.id) : row
    editing.value = current || null; form.value=normalizeForm(kind.value,current,catalog.value); formError.value=''; formKey=operationId(); formOpen.value=true
  } catch(e) { toast(e.message,'err') }
}
function closeForm() { if(!busy.value) formOpen.value=false }
function payload() {
  const result={...form.value}
  for(const f of fields.value) if(f.type === 'number' || (f.type === 'select' && f.numeric)) result[f.key]=result[f.key] === '' ? null : ['price','purchasePrice','soldPrice'].includes(f.key) ? String(result[f.key]) : Number(result[f.key])
  for(const key of ['parentId','purchaseDate','soldDate','warrantyExpires','start_date','cancellation_date','url','category_id','payment_method_id','parent_id']) if(result[key] === '') result[key]=null
  if(kind.value === 'life-reminders') for(const key of ['due_date','reminder','start_date','end_date']) result[key]=result[key] ? new Date(result[key]+'+08:00').toISOString() : null
  return result
}
async function save() {
  busy.value=true; formError.value=''
  try { const path=base.value+config.value.endpoint+(editing.value?'/'+editing.value.id:''); await api[editing.value?'put':'post'](path,payload(),{idempotencyKey:formKey,timeoutMs:45000}); formOpen.value=false; toast('已保存','ok'); await load() }
  catch(e){ formError.value=e.message+'。若提示结果待核对，请关闭并刷新确认。' } finally { busy.value=false }
}
async function complete(row) { const action=JSON.stringify([row.id,row.due_date,!row.done]);if(!completionKeys.has(action))completionKeys.set(action,operationId());busy.value=true;try { await api.post(base.value+`/tasks/${row.id}/completion`,{done:!row.done},{idempotencyKey:completionKeys.get(action),timeoutMs:45000});completionKeys.delete(action);await load() }catch(e){toast(e.message,'err')}finally{busy.value=false} }
async function addProject() { const title=window.prompt('清单名称');if(!title?.trim())return;try{await api.post(base.value+'/projects',{title},{idempotencyKey:operationId()});await load()}catch(e){toast(e.message,'err')} }
async function addLabel() { const title=window.prompt('标签名称');if(!title?.trim())return;try{await api.post(base.value+'/labels',{title},{idempotencyKey:operationId()});await load()}catch(e){toast(e.message,'err')} }
async function loadReminders() { const d=await api.get(base.value+'/reminders');reminders.value=d.items || [];reminderState.value=d.scheduler || {};notification.value=d.notification || {};externalEnabled.value=notification.value.enabled }
async function scanReminders() { busy.value=true;try{await api.post(base.value+'/reminders/scan',{}, {timeoutMs:60000});await loadReminders()}catch(e){toast(e.message,'err')}finally{busy.value=false} }
async function reminderAction(row,action) { try{await api.post(base.value+`/reminders/${row.id}/${action}`,action==='snooze'?{minutes:60}:{});await loadReminders()}catch(e){toast(e.message,'err')} }
async function createLinkedTask(row) { busy.value=true;try{await api.post(base.value+'/linked-task',{source:row.source,source_id:row.source_id},{timeoutMs:45000});toast('处理待办已就绪','ok');await load()}catch(e){toast(e.message,'err')}finally{busy.value=false} }
async function saveNotification(){busy.value=true;try{notification.value=await api.put(base.value+'/notification-settings',{url:webhook.value,enabled:externalEnabled.value});webhook.value='';toast('设置已保存','ok')}catch(e){toast(e.message,'err')}finally{busy.value=false}}
function saveBlob(blob,name){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function exportData(){try{const ext=kind.value==='subscriptions'?'csv':'zip';saveBlob(await api.download(base.value+'/export.'+ext,{}, {timeoutMs:180000}),kind.value+'.'+ext)}catch(e){toast(e.message,'err')}}
async function showDetails(row){try{detail.value=await api.get(base.value+(kind.value==='home-inventory'?'/items/':'/tasks/')+row.id);if(kind.value==='home-inventory')maintenanceRows.value=(await api.get(base.value+'/items/'+row.id+'/maintenance')).items || [];else{const d=await api.get(base.value+'/workspace/comments',{scope:row.id});commentRows.value=d.items || [];commentText.value='';commentKey=operationId()}}catch(e){toast(e.message,'err')}}
function relationName(r){return {subtask:'子任务',parenttask:'父任务',related:'相关任务',duplicateof:'重复于',duplicates:'重复任务',blocking:'阻塞',blocked:'被阻塞',precedes:'前置任务',follows:'后续任务'}[r] || r}
async function addComment(){busy.value=true;try{await api.post(base.value+'/workspace/comments',{operation:'create',scope:String(detail.value.id),values:{comment:commentText.value}},{idempotencyKey:commentKey});await showDetails(detail.value);toast('评论已保存','ok')}catch(e){toast(e.message,'err')}finally{busy.value=false}}
async function newSubtask(){const id=detail.value.id;detail.value=null;await openForm();form.value.parent_id=id}
function confirmRecordAction(row,action){recordAction.value={row,action};actionError.value='';recordActionKey=operationId()}
async function applyRecordAction(){busy.value=true;try{const {row,action}=recordAction.value;await api.post(base.value+'/record-actions/'+action,{id:String(row.id),expected_revision:row.workbench_revision || row.updatedAt || row.updated || ''},{idempotencyKey:recordActionKey,timeoutMs:45000});recordAction.value=null;await load();toast(action==='delete'?'已删除':'已复制','ok')}catch(e){actionError.value=e.message}finally{busy.value=false}}
async function uploadFile(event){const file=event.target.files?.[0];if(!file)return;if(file.size>5*1024*1024){toast('附件不能超过5MiB','err');return}busy.value=true;try{const body=new FormData();body.append('file',file);const res=await fetch(base.value+`/${kind.value==='home-inventory'?'items':'tasks'}/${detail.value.id}/attachments`,{method:'POST',headers:{Authorization:'Bearer '+getAccessToken(),'Idempotency-Key':operationId()},body});if(!res.ok){const d=await res.json();throw new Error(d.detail || '上传失败')}await showDetails(detail.value);toast('附件已保存','ok')}catch(e){toast(e.message,'err')}finally{busy.value=false;event.target.value=''}}
async function downloadAttachment(file){try{saveBlob(await api.download(base.value+`/${kind.value==='home-inventory'?'items':'tasks'}/${detail.value.id}/attachments/${file.id}`),file.file?.name || file.name || '附件')}catch(e){toast(e.message,'err')}}
async function addMaintenance(){busy.value=true;try{await api.post(base.value+`/items/${detail.value.id}/maintenance`,maintenanceForm.value,{idempotencyKey:operationId()});await showDetails(detail.value);maintenanceForm.value.name='';toast('维护记录已保存','ok')}catch(e){toast(e.message,'err')}finally{busy.value=false}}
async function readCsv(event){const file=event.target.files?.[0];if(!file)return;if(file.size>1024*1024){toast('CSV不能超过1MiB','err');return}csvText.value=await file.text();importPreview.value=null;importKey=operationId()}
async function previewImport(){busy.value=true;try{importPreview.value=await api.post(base.value+'/import/preview',{csv_text:csvText.value});importKey=operationId()}catch(e){toast(e.message,'err')}finally{busy.value=false}}
async function confirmImport(){busy.value=true;try{await api.post(base.value+'/import/confirm',{csv_text:csvText.value},{idempotencyKey:importKey,timeoutMs:180000});importOpen.value=false;await load();toast('导入完成','ok')}catch(e){toast(e.message,'err')}finally{busy.value=false}}
watch(kind,()=>{active.value='overview';search.value='';filter.value='all';formOpen.value=false;detail.value=null;rows.value=[];catalog.value={};overview.value={};reminders.value=[];load()})
onMounted(load)
</script>

<style scoped>
.task-board{display:grid;grid-template-columns:repeat(3,minmax(240px,1fr));gap:16px;overflow:auto;margin:18px 0}.board-column{background:var(--bg-hover,#f5f7fa);border-radius:10px;padding:14px}.task-card{border:1px solid var(--border);background:var(--bg-card,#fff);padding:14px;margin:12px 0;border-radius:8px}.task-card progress{display:block;width:100%;margin:12px 0}.management-picker{display:flex;align-items:center;gap:12px;margin-top:18px}.management-picker select{max-width:280px}.life-workspace{padding:18px;min-width:0;overflow:auto}.life-toolbar,.actions,.filters,.modal-heading,.form-footer{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.life-toolbar,.modal-heading{justify-content:space-between}.tabs{display:flex;gap:6px;flex-wrap:wrap}.selected{background:var(--primary,#2563eb);color:white}.overview-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin:18px 0}.stat{border:1px solid var(--border,#dce2eb);border-radius:10px;padding:18px;display:flex;flex-direction:column;gap:10px;background:var(--bg-card,#fff)}.stat strong{font-size:25px}.stat span,.muted,small{color:var(--text-muted,#667085)}.filters{margin:18px 0}.filters input{flex:1;min-width:180px}.input{width:100%;padding:9px 11px;border:1px solid var(--border,#dce2eb);border-radius:6px;background:var(--bg-card,#fff);color:inherit}.filters select{width:auto}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left}td,th{padding:13px 10px;border-bottom:1px solid var(--border,#e8ecf2)}th{font-weight:500;color:var(--text-muted,#667085)}td small{display:block;max-width:420px;white-space:pre-wrap;overflow-wrap:anywhere;margin-top:5px}.badge{display:inline-block;font-size:12px;padding:4px 8px;border-radius:5px;background:var(--bg-hover,#edf3fd)}.row-actions{white-space:nowrap}.row-actions button{margin:2px}.empty{text-align:center;padding:60px 16px;color:var(--text-muted,#667085)}.notice{padding:12px;border-radius:6px;background:#fff3e8}.error{color:#b42318}.modal-backdrop{position:fixed;inset:0;background:#10182866;z-index:1000;display:flex;align-items:center;justify-content:center;padding:20px}.life-modal{background:var(--bg-card,#fff);color:var(--text,#182230);border-radius:12px;padding:22px;width:min(780px,100%);max-height:90vh;overflow:auto}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:18px 0}.form-grid label,.settings label{display:flex;flex-direction:column;gap:7px}.form-grid input[type=checkbox]{align-self:flex-start}.wide{grid-column:1/-1}.form-footer{justify-content:flex-end;margin-top:18px}.settings{max-width:650px}.settings label{margin:18px 0}.settings .check{flex-direction:row}.reminder-row{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:15px 0;border-bottom:1px solid var(--border,#e8ecf2)}.reminder-row small{display:block;margin-top:6px}.csv-text{min-height:140px;margin:15px 0}.modal-heading h3{margin:0}h4{margin:22px 0 10px}@media(max-width:700px){.life-workspace{padding:12px}.form-grid{grid-template-columns:1fr}.reminder-row{align-items:flex-start;flex-direction:column}.life-modal{padding:16px}.modal-backdrop{padding:10px}}
</style>
