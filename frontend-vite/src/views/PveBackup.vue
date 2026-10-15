<template>
  <div class="view pve-backup">
    <div class="view-head">
      <div>
        <h1 class="view-title">PVE 与备份</h1>
        <p class="view-sub">只读展示虚拟机、存储和备份覆盖；本模块不执行 PVE 操作。</p>
      </div>
      <button class="btn" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button>
    </div>

    <div v-if="report.error" class="notice" :class="report.status">
      {{ report.error }}<span v-if="report.status === 'unavailable'">。配置并验证 PVE 只读 API 与 CA 后，模块会自动展示数据。</span>
    </div>

    <div class="metrics">
      <div class="card metric"><span>虚拟机 / 容器</span><b>{{ data.vm_total ?? '—' }}</b><small>运行中 {{ data.vm_running ?? '—' }}</small></div>
      <div class="card metric"><span>存储已用</span><b>{{ percent(data.storage_used_percent) }}</b><small>{{ bytes(data.storage_used_bytes) }} / {{ bytes(data.storage_total_bytes) }}</small></div>
      <div class="card metric"><span>备份覆盖</span><b>{{ data.backup_coverage_current ?? '—' }} / {{ data.vm_total ?? '—' }}</b><small>缺失 {{ data.backup_coverage_missing ?? '—' }} · 过期 {{ data.backup_coverage_stale ?? '—' }}</small></div>
      <div class="card metric"><span>恢复验证</span><b>未验证</b><small>只看到备份文件不代表恢复成功</small></div>
    </div>

    <section class="card section">
      <h2>虚拟机备份覆盖</h2>
      <div v-if="!data.guests?.length" class="empty">{{ report.status === 'unavailable' ? 'PVE 数据尚不可用' : '没有可展示的虚拟机' }}</div>
      <div v-else class="table-wrap"><table class="table">
        <thead><tr><th>VMID</th><th>名称</th><th>节点</th><th>运行状态</th><th>覆盖</th><th>最近备份</th><th>恢复验证</th></tr></thead>
        <tbody><tr v-for="guest in data.guests" :key="guest.vmid">
          <td class="mono">{{ guest.vmid }}</td><td>{{ guest.name }}</td><td>{{ guest.node || '—' }}</td><td>{{ guest.status }}</td>
          <td><span class="tag" :class="coverageClass(guest.coverage)">{{ coverageLabel(guest.coverage) }}</span></td>
          <td>{{ guest.latest_backup_at ? fmtTime(guest.latest_backup_at) : '无可见备份' }}</td><td>未验证</td>
        </tr></tbody>
      </table></div>
    </section>

    <div class="detail-grid">
      <section class="card section">
        <h2>存储</h2>
        <div v-if="!data.storages?.length" class="empty">暂无存储数据</div>
        <div v-else class="table-wrap"><table class="table">
          <thead><tr><th>节点 / 存储</th><th>状态</th><th>类型</th><th>已用 / 总量</th></tr></thead>
          <tbody><tr v-for="storage in data.storages" :key="`${storage.node}:${storage.storage}`">
            <td>{{ storage.node }} / {{ storage.storage }}</td><td>{{ storage.status || '—' }}</td><td>{{ storage.type || '—' }}</td>
            <td>{{ bytes(storage.used_bytes) }} / {{ bytes(storage.total_bytes) }}</td>
          </tr></tbody>
        </table></div>
      </section>
      <section class="card section">
        <h2>备份计划与最近文件</h2>
        <p v-if="!data.jobs?.length" class="empty">未发现可见的备份计划</p>
        <ul v-else class="jobs"><li v-for="job in data.jobs" :key="job.id || job.schedule">
          <b>{{ job.id || '备份计划' }}</b><span>{{ job.enabled === false || job.enabled === 0 ? '停用' : '启用' }} · {{ job.schedule || '未设置周期' }} · {{ job.storage || '未指定存储' }}</span>
        </li></ul>
        <h3>最近备份文件</h3>
        <div v-if="!data.backups?.length" class="empty">暂无可见备份文件</div>
        <ul v-else class="jobs"><li v-for="backup in data.backups.slice(0, 8)" :key="backup.volid">
          <b>VM {{ backup.vmid }} · {{ backup.storage }}</b><span>{{ fmtTime(backup.created_at) }} · {{ bytes(backup.size_bytes) }}</span>
        </li></ul>
      </section>
    </div>
    <footer class="muted collected">最近采集：{{ report.collected_at ? fmtTime(report.collected_at) : '暂无成功采集' }}</footer>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, fmtTime, toast } from '../api'

const report = ref({ status: 'unavailable', data: {}, error: '' })
const data = computed(() => report.value.data || {})
const loading = ref(false)

function bytes(value) {
  if (value == null) return '—'
  const size = Number(value)
  if (!Number.isFinite(size)) return '—'
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let amount = size, unit = 0
  while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit++ }
  return `${amount.toFixed(unit ? 1 : 0)} ${units[unit]}`
}
function percent(value) { return value != null && Number.isFinite(Number(value)) ? `${value}%` : '—' }
function coverageLabel(value) { return ({ current: '覆盖正常', stale: '备份过期', missing: '无备份' })[value] || '未知' }
function coverageClass(value) { return ({ current: 'tag-green', stale: 'tag-amber', missing: 'tag-red' })[value] || '' }

async function load() {
  loading.value = true
  try {
    report.value = await api.get('/modules/pve-backup/api/v1/overview')
  } catch (error) {
    toast(`PVE 数据读取失败：${error.message}`, 'err')
    report.value = { status: 'unavailable', data: {}, error: error.message }
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.notice { padding: 12px 14px; margin-bottom: 14px; border-radius: 8px; background: #fff7ed; color: #9a3412; }
.notice.unavailable { background: #fef2f2; color: #991b1b; }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.metric { padding: 14px; min-width: 0; }
.metric span, .metric small { display: block; color: var(--muted); font-size: 12px; }
.metric b { display: block; margin: 7px 0 4px; font-size: 22px; }
.section { padding: 16px; margin-top: 14px; min-width: 0; }
.section h2 { margin: 0 0 12px; font-size: 15px; }
.section h3 { margin: 16px 0 8px; font-size: 13px; }
.table-wrap { overflow-x: auto; }
.table { min-width: 720px; }
.empty { padding: 12px 0; color: var(--muted); font-size: 13px; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.jobs { display: grid; gap: 8px; list-style: none; margin: 0; padding: 0; }
.jobs li { display: grid; gap: 4px; padding: 8px 0; border-bottom: 1px solid var(--border); }
.jobs span, .collected { color: var(--muted); font-size: 12px; }
.collected { margin-top: 12px; }
@media (max-width: 900px) { .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); } .detail-grid { grid-template-columns: 1fr; } }
@media (max-width: 520px) { .metrics { grid-template-columns: 1fr; } }
</style>
