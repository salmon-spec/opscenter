<template>
  <section v-if="summary" class="monitor-strip">
    <div class="monitor-summary">
      <div class="summary-item"><span>数据源可用</span><b>{{ summary.services_available }}/{{ summary.services_total }}</b></div>
      <div class="summary-item"><span>已配置</span><b>{{ summary.services_configured }}/{{ summary.services_total }}</b></div>
      <div class="summary-item"><span>指标项</span><b>{{ summary.metrics_total }}</b></div>
      <div v-if="summary.metrics_truncated" class="truncated-note">已截断</div>
      <div class="summary-item" :class="{ warn: summary.warning_metrics > 0, err: summary.critical_metrics > 0 }">
        <span>异常指标</span><b>{{ summary.critical_metrics + summary.warning_metrics }}</b>
      </div>
    </div>
    <div v-if="attention.length" class="monitor-attention">
      <span v-for="item in attention" :key="item.kind" class="attention-chip" :class="{ err: item.critical_count > 0 }">
        {{ item.name }} · {{ item.critical_count || item.warning_count }}
      </span>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { dsApi, unwrap } from '../../api/dataservices'

const summary = ref(null)
const services = ref([])
let controller = null
let loadSeq = 0

const attention = computed(() => services.value
  .filter((item) => item.critical_count > 0 || item.warning_count > 0)
  .sort((a, b) => (b.critical_count - a.critical_count) || (b.warning_count - a.warning_count))
  .slice(0, 6))

async function load(force = false) {
  const seq = ++loadSeq
  controller?.abort()
  controller = new AbortController()
  try {
    const result = unwrap(await dsApi.monitorOverview({
      force,
      signal: controller.signal,
      timeoutMs: 12000,
    }))
    if (seq !== loadSeq) return
    summary.value = result.data?.summary || null
    services.value = Array.isArray(result.data?.services) ? result.data.services : []
  } catch (error) {
    if (error.name !== 'AbortError' && seq === loadSeq) {
      summary.value = null
      services.value = []
    }
  }
}

onMounted(() => load())
onUnmounted(() => {
  loadSeq += 1
  controller?.abort()
})
defineExpose({ reload: () => load(true) })
</script>

<style scoped>
.monitor-strip { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: #fff; }
.monitor-summary { display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 18px; flex: 1; min-width: 0; }
.summary-item { min-width: 0; }
.summary-item span { display: block; color: var(--muted); font-size: 11px; }
.summary-item b { display: block; margin-top: 2px; font-size: 18px; }
.summary-item.warn b { color: var(--warn); }
.summary-item.err b { color: var(--err); }
.monitor-attention { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px; max-width: 46%; }
.attention-chip { padding: 2px 8px; border-radius: 999px; background: #fffbeb; color: #b45309; font-size: 11px; white-space: nowrap; }
.attention-chip.err { background: #fef2f2; color: #dc2626; }
.truncated-note { align-self: center; color: #b45309; font-size: 11px; }
@media (max-width: 900px) {
  .monitor-strip { align-items: stretch; flex-direction: column; }
  .monitor-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .monitor-attention { max-width: none; justify-content: flex-start; }
}
</style>
