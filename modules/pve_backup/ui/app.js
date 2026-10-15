const $ = (id) => document.getElementById(id)
const expectedOrigin = new URL(location.href).origin
let nonce = ''
let nextId = 0
const pending = new Map()

function request(path) {
  if (!nonce) return Promise.reject(new Error('工作台尚未连接'))
  return new Promise((resolve, reject) => {
    const id = String(++nextId)
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('模块请求超时')) }, 15000)
    pending.set(id, { resolve, reject, timer })
    parent.postMessage({ type: 'ops-module-api-request', nonce, id, method: 'GET', path }, '*')
  })
}

window.addEventListener('message', (event) => {
  if (event.source !== parent || event.origin !== expectedOrigin) return
  const data = event.data || {}
  if (data.type === 'ops-module-info' && data.module?.key === 'pve-backup' && /^[a-f0-9]{32}$/.test(data.nonce || '')) {
    if (nonce === data.nonce) return
    nonce = data.nonce
    load()
  } else if (data.type === 'ops-module-api-response' && data.nonce === nonce) {
    const item = pending.get(String(data.id))
    if (!item) return
    clearTimeout(item.timer)
    pending.delete(String(data.id))
    data.ok ? item.resolve(data.data) : item.reject(new Error(data.data?.message || '读取失败'))
  }
})

function bytes(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let amount = Number(value), unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit++ }
  return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}`
}
function date(value) { return value ? new Date(value).toLocaleString('zh-CN') : '—' }
function set(id, value) { $(id).textContent = String(value ?? '—') }
function cell(value) { const td = document.createElement('td'); td.textContent = String(value ?? '—'); return td }
function empty(tbody, columns, message) { const tr = document.createElement('tr'); const td = cell(message); td.colSpan = columns; td.className = 'empty'; tr.append(td); tbody.replaceChildren(tr) }

function render(report) {
  const data = report.data || {}
  const notice = $('notice')
  notice.className = `notice ${report.status || 'unavailable'}`
  notice.textContent = report.error || (report.status === 'degraded' ? '部分数据不可用或已过期' : '')
  set('vm-total', data.vm_total)
  set('vm-running', `运行中 ${data.vm_running ?? '—'}`)
  set('storage-percent', data.storage_used_percent == null ? '—' : `${data.storage_used_percent}%`)
  set('storage-bytes', `${bytes(data.storage_used_bytes)} / ${bytes(data.storage_total_bytes)}`)
  set('coverage', `${data.backup_coverage_current ?? '—'} / ${data.vm_total ?? '—'}`)
  set('coverage-detail', `缺失 ${data.backup_coverage_missing ?? '—'} · 过期 ${data.backup_coverage_stale ?? '—'} · 未知 ${data.backup_coverage_unknown ?? '—'}`)
  set('recovery', data.restore_unverified == null ? '—' : `${Math.max(0, (data.vm_total || 0) - data.restore_unverified)} 已验证`)
  set('collected', `最近采集：${report.collected_at ? date(report.collected_at) : '暂无成功采集'}`)
  const guests = $('guests')
  if (!data.guests?.length) empty(guests, 7, '暂无虚拟机数据')
  else guests.replaceChildren(...data.guests.map((guest) => {
    const tr = document.createElement('tr')
    tr.append(cell(guest.vmid), cell(guest.name), cell(guest.node), cell(guest.status))
    const coverage = cell(({ current: '覆盖正常', stale: '备份过期', missing: '无备份', unknown: '无法确认' })[guest.coverage] || '未知')
    tr.append(coverage, cell(guest.latest_backup_at ? date(guest.latest_backup_at) : '无可见备份'),
      cell(({ archive_verified: '归档校验', isolated_boot: '隔离启动', business_verified: '业务验证' })[guest.recovery_test] || '未验证'))
    return tr
  }))
  const storages = $('storages')
  if (!data.storages?.length) empty(storages, 4, '暂无存储数据')
  else storages.replaceChildren(...data.storages.map((storage) => {
    const tr = document.createElement('tr')
    tr.append(cell(`${storage.node} / ${storage.storage}`), cell(storage.status), cell(storage.type),
      cell(`${bytes(storage.used_bytes)} / ${bytes(storage.total_bytes)}`))
    return tr
  }))
  renderEntries('jobs', data.jobs, (job) => [`${job.id || '备份计划'}`, `${job.schedule || '未设置周期'} · ${job.storage || '未指定存储'}`], '暂无可见计划')
  renderEntries('backups', data.backups?.slice(0, 8), (backup) => [`VM ${backup.vmid} · ${backup.storage}`, `${date(backup.created_at)} · ${bytes(backup.size_bytes)}`], '暂无可见文件')
}

function renderEntries(id, rows, format, fallback) {
  const list = $(id)
  if (!rows?.length) { const li = document.createElement('li'); li.className = 'empty'; li.textContent = fallback; list.replaceChildren(li); return }
  list.replaceChildren(...rows.map((row) => {
    const li = document.createElement('li'), title = document.createElement('b'), detail = document.createElement('small')
    ;[title.textContent, detail.textContent] = format(row)
    li.append(title, detail)
    return li
  }))
}

async function load() {
  $('refresh').disabled = true
  try { render(await request('/modules/pve-backup/api/v1/overview')) }
  catch (error) { $('notice').className = 'notice unavailable'; $('notice').textContent = error.message }
  finally { $('refresh').disabled = false }
}
$('refresh').addEventListener('click', load)
parent.postMessage({ type: 'ops-module-ready' }, '*')
