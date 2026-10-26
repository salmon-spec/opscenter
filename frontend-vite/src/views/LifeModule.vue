<template>
  <div class="life-module">
    <div class="native-toolbar">
      <div role="tablist" aria-label="应用界面">
        <button class="btn" role="tab" :aria-selected="active === 'native'" @click="active = 'native'">完整应用</button>
        <button class="btn" role="tab" :aria-selected="active === 'workbench'" @click="active = 'workbench'">工作台管理与提醒</button>
      </div>
      <button v-if="active === 'native'" class="btn" :disabled="loading" @click="connect">{{ loading ? '正在连接…' : '重新连接' }}</button>
    </div>
    <template v-if="active === 'native'">
      <p v-if="error" class="notice" role="alert">{{ error }}</p>
      <form v-if="launch" ref="launchForm" :action="launch.origin + '/_ops/launch'" method="post" :target="frameName" hidden>
        <input name="ticket" :value="launch.ticket">
      </form>
      <iframe v-if="launch" :name="frameName" class="native-frame" :title="title + '完整应用'"
        sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-modals allow-popups allow-popups-to-escape-sandbox"
        allow="camera; clipboard-read; clipboard-write; fullscreen" referrerpolicy="no-referrer"></iframe>
    </template>
    <LifeWorkspace v-else :kind="kind" />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { CONFIG } from '../utils/life'
import LifeWorkspace from './LifeWorkspace.vue'

const props = defineProps({ kind: { type: String, required: true } })
const title = computed(() => CONFIG[props.kind].title)
const active = ref('native'), launch = ref(null), launchForm = ref(null)
const loading = ref(false), error = ref('')
const frameName = computed(() => 'life-native-' + props.kind)
let revision = 0
async function connect() {
  const attempt = ++revision
  loading.value = true
  error.value = ''
  launch.value = null
  try {
    const result = await api.post(`/modules/${props.kind}/api/v1/native/session`, { parent_origin: window.location.origin })
    if (attempt !== revision) return
    if (new URL(result.origin).origin === window.location.origin) throw new Error('完整应用需要独立来源')
    launch.value = result
    await nextTick()
    if (attempt === revision) launchForm.value.submit()
  } catch (err) { if (attempt === revision) error.value = err.message }
  finally { if (attempt === revision) loading.value = false }
}
watch(() => props.kind, () => { active.value = 'native'; connect() })
watch(active, value => { if (value === 'native') connect() })
onMounted(connect)
</script>

<style scoped>
.life-module{height:100%;min-height:650px;display:flex;flex-direction:column;gap:10px}.native-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex:none}.native-toolbar [role=tablist]{display:flex;gap:8px}.native-toolbar [aria-selected=true]{background:var(--primary,#2563eb);color:#fff}.native-frame{width:100%;flex:1;min-height:590px;border:1px solid var(--border);border-radius:8px;background:#fff}.notice{padding:14px;color:#9b3529;background:#fff3ef;border-radius:8px}.life-module :deep(.life-workspace){flex:1}
</style>
