<template>
  <div class="view reset-view">
    <div class="view-head">
      <div>
        <h1 class="view-title">切换统一账号</h1>
        <p class="view-sub">逐项退出各应用旧会话，全部确认后再退出 Keycloak。</p>
      </div>
    </div>

    <div class="card reset-card">
      <div v-if="loading" class="loading"><span class="spinner"></span>正在加载注销清单…</div>
      <div v-else-if="error" class="error-box">{{ error }}</div>
      <template v-else>
        <div v-for="(target, index) in targets" :key="target.name" class="reset-step">
          <div class="step-number">{{ index + 1 }}</div>
          <div class="step-body">
            <div class="step-title">{{ target.name }}</div>
            <div class="muted step-help">
              {{ target.mode === 'manual' ? '在新窗口中手动退出当前账号，然后返回本页确认。' : '打开受控注销页，完成后返回本页确认。' }}
            </div>
          </div>
          <button class="btn" type="button" @click="openTarget(target)">打开注销页</button>
          <label class="confirm-label">
            <input v-model="completed[target.name]" type="checkbox" /> 已完成
          </label>
        </div>

        <div class="reset-finish">
          <p v-if="!allRequiredDone" class="muted">还有 {{ remaining }} 个必需步骤未确认，暂不能完成账号切换。</p>
          <p v-else class="success-text">应用会话均已确认退出，可以清除工作台会话并退出 Keycloak。</p>
          <button class="btn btn-primary" type="button" :disabled="!allRequiredDone" @click="finishSwitch">
            退出 Keycloak 并切换账号
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const loading = ref(true)
const error = ref('')
const targets = ref([])
const completed = reactive({})

const requiredTargets = computed(() => targets.value.filter((target) => target.required))
const remaining = computed(() => requiredTargets.value.filter((target) => !completed[target.name]).length)
const allRequiredDone = computed(() => requiredTargets.value.length > 0 && remaining.value === 0)

async function loadTargets() {
  try {
    const result = await api.get('/sso/reset-targets')
    targets.value = Array.isArray(result?.targets) ? result.targets : []
    if (!targets.value.length) error.value = '注销清单为空，请联系管理员检查配置。'
  } catch (err) {
    error.value = err.message || '注销清单加载失败'
  } finally {
    loading.value = false
  }
}

function openTarget(target) {
  window.open(target.url, '_blank', 'noopener,noreferrer')
}

function finishSwitch() {
  if (!allRequiredDone.value) return
  window.location.assign('/api/v2/sso/account-switch')
}

onMounted(loadTargets)
</script>

<style scoped>
.reset-view { max-width: 980px; }
.reset-card { padding: 8px 22px 22px; }
.reset-step { display: flex; align-items: center; gap: 14px; padding: 16px 0; border-bottom: 1px solid var(--border); }
.step-number { width: 28px; height: 28px; flex: 0 0 28px; border-radius: 50%; background: rgba(37,99,235,.1); color: var(--brand); display: grid; place-items: center; font-weight: 700; }
.step-body { flex: 1; min-width: 0; }
.step-title { font-weight: 700; margin-bottom: 4px; }
.step-help { font-size: 12px; }
.confirm-label { min-width: 84px; font-size: 13px; cursor: pointer; }
.reset-finish { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding-top: 22px; }
.reset-finish p { margin: 0; }
.success-text { color: var(--ok); }
.error-box { margin-top: 14px; padding: 12px; border-radius: 8px; color: #b91c1c; background: #fef2f2; }
.btn:disabled { cursor: not-allowed; opacity: .5; }
@media (max-width: 760px) {
  .reset-step { align-items: flex-start; flex-wrap: wrap; }
  .step-body { flex-basis: calc(100% - 50px); }
  .reset-finish { align-items: stretch; flex-direction: column; }
}
</style>
