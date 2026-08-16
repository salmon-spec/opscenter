<template>
  <Teleport to="body">
    <transition name="slide">
      <div v-if="visible" class="drawer-mask" @click.self="$emit('close')">
        <div class="drawer">
          <div class="drawer-head">
            <div>
              <h3>{{ service?.name || '服务详情' }}</h3>
              <div class="muted" style="font-size:12px">{{ service?.category || '' }}</div>
            </div>
            <button class="btn btn-ghost btn-sm" @click="$emit('close')">✕</button>
          </div>
          <div class="drawer-body">
            <div v-if="loading" class="loading"><span class="spinner"></span>加载中…</div>
            <template v-else>
              <div class="detail-grid">
                <div class="d-item">
                  <div class="d-label">状态</div>
                  <span class="tag" :class="statusClass">{{ statusText }}</span>
                </div>
                <div class="d-item">
                  <div class="d-label">地址</div>
                  <a v-if="url" :href="url" target="_blank" rel="noopener" class="d-link">{{ url }}</a>
                  <span v-else>-</span>
                </div>
                <div class="d-item"><div class="d-label">分类</div><span>{{ detail?.category || service?.category || '-' }}</span></div>
                <div class="d-item"><div class="d-label">部署方式</div><span>{{ deployTypeText }}</span></div>
                <div class="d-item"><div class="d-label">版本</div><span>{{ detail?.version || service?.version || '-' }}</span></div>
                <div class="d-item"><div class="d-label">运行时长</div><span>{{ fmtDuration(uptimeSeconds) }}</span></div>
                <div class="d-item"><div class="d-label">所属主机</div><span>{{ hostText }}</span></div>
                <div class="d-item"><div class="d-label">端口</div><span class="mono">{{ detail?.ports || service?.ports || '-' }}</span></div>
                <div class="d-item"><div class="d-label">容器名</div><span class="mono">{{ detail?.container_name || service?.container_name || '-' }}</span></div>
                <div class="d-item"><div class="d-label">镜像</div><span class="mono">{{ detail?.image || service?.image || '-' }}</span></div>
              </div>

              <div v-if="detail?.description || service?.description" class="d-section">
                <div class="d-section-title">说明</div>
                <p class="muted" style="margin:0">{{ detail?.description || service?.description }}</p>
              </div>

              <div class="d-section">
                <div class="d-section-title">下游依赖（本服务 → 其他）</div>
                <div v-if="outbound.length" class="dep-list">
                  <div v-for="dep in outbound" :key="dep.id" class="dep-item">
                    <span class="tag tag-green">{{ dep.relation_type }}</span>
                    <span class="dep-name">{{ dep.target_name }}</span>
                    <span v-if="dep.label" class="muted" style="font-size:12px">{{ dep.label }}</span>
                  </div>
                </div>
                <EmptyState v-else icon="🔗" text="暂无下游依赖" style="padding:18px" />
              </div>

              <div class="d-section">
                <div class="d-section-title">上游依赖（其他 → 本服务）</div>
                <div v-if="inbound.length" class="dep-list">
                  <div v-for="dep in inbound" :key="dep.id" class="dep-item">
                    <span class="tag tag-slate">{{ dep.relation_type }}</span>
                    <span class="dep-name">{{ dep.source_name }}</span>
                    <span v-if="dep.label" class="muted" style="font-size:12px">{{ dep.label }}</span>
                  </div>
                </div>
                <EmptyState v-else icon="🔗" text="暂无上游依赖" style="padding:18px" />
              </div>
            </template>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { api, fmtDuration } from '../api'
import EmptyState from './EmptyState.vue'

const props = defineProps({
  visible: Boolean,
  service: { type: Object, default: null },
})
defineEmits(['close'])

const detail = ref(null)
const loading = ref(false)

const url = computed(() => detail.value?.url || props.service?.url || '')
const statusText = computed(() => {
  const s = detail.value?.status || props.service?.status
  if (s === 'online' || s === 'up') return '在线'
  if (s === 'offline' || s === 'down') return '离线'
  if (s === 'degraded') return '异常'
  return '未知'
})
const statusClass = computed(() => {
  const s = detail.value?.status || props.service?.status
  if (s === 'online' || s === 'up') return 'tag-green'
  if (s === 'offline' || s === 'down') return 'tag-red'
  if (s === 'degraded') return 'tag-amber'
  return 'tag-slate'
})
const deployTypeText = computed(() => {
  const t = detail.value?.deploy_type || props.service?.deploy_type
  return { docker: 'Docker', systemd: 'systemd', compose: 'Compose', manual: '手动' }[t] || t || '-'
})
const hostText = computed(() => {
  const name = detail.value?.server?.name || props.service?.server_name
  const host = detail.value?.server?.host || props.service?.server_host
  if (name && host) return `${name} (${host})`
  return name || host || '-'
})
const uptimeSeconds = computed(() => {
  if (detail.value?.running_seconds !== undefined && detail.value?.running_seconds !== null) return detail.value.running_seconds
  const st = detail.value?.started_at || props.service?.started_at
  if (!st) return null
  const t = new Date(st).getTime()
  return t > 0 ? Math.floor((Date.now() - t) / 1000) : null
})
const outbound = computed(() => detail.value?.relations?.outgoing || [])
const inbound = computed(() => detail.value?.relations?.incoming || [])

watch(
  () => [props.visible, props.service?.id],
  async ([visible, id]) => {
    if (!visible || !id) { detail.value = null; return }
    loading.value = true
    try {
      detail.value = await api.get(`/services/${id}/detail`)
    } catch {
      // 后端详情接口未就绪时退回列表字段展示
      detail.value = null
    } finally {
      loading.value = false
    }
  },
  { immediate: true }
)
</script>

<style scoped>
.drawer-mask {
  position: fixed; inset: 0; background: rgba(15,23,42,.4); z-index: 1900;
  display: flex; justify-content: flex-end;
}
.drawer {
  width: 460px; max-width: 92vw; height: 100%; background: #fff;
  box-shadow: -10px 0 30px rgba(0,0,0,.15); display: flex; flex-direction: column;
}
.drawer-head {
  display: flex; align-items: flex-start; justify-content: space-between;
  padding: 18px 20px; border-bottom: 1px solid var(--border);
}
.drawer-head h3 { margin: 0 0 4px; font-size: 17px; }
.drawer-body { padding: 18px 20px; overflow: auto; }
.detail-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; }
.d-item .d-label { font-size: 11px; color: var(--muted); margin-bottom: 3px; }
.d-item span, .d-item a { font-size: 13px; word-break: break-all; }
.d-link { color: var(--brand); text-decoration: none; }
.d-link:hover { text-decoration: underline; }
.d-section { margin-top: 20px; }
.d-section-title { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.dep-list { display: flex; flex-direction: column; gap: 6px; }
.dep-item { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.dep-name { font-weight: 500; }
.slide-enter-active, .slide-leave-active { transition: transform .2s; }
.slide-enter-from, .slide-leave-to { transform: translateX(100%); }
</style>
