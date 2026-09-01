<template>
  <div class="view">
    <div class="view-head">
      <div>
        <h1 class="view-title">开放 API</h1>
        <p class="view-sub">密钥管理 · 供其他服务调用数据（如 OpsBot / Hermes / 三省六部）</p>
      </div>
      <a class="btn" href="/api/v2/docs" target="_blank" rel="noopener">接口文档 (OpenAPI)</a>
    </div>

    <div class="card" style="margin-bottom:16px">
      <div style="font-weight:600;margin-bottom:12px">创建 API 密钥</div>
      <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
        <div class="field" style="margin:0;flex:1;min-width:200px">
          <label>名称</label>
          <input v-model="form.name" class="input" placeholder="如：opsbot-alert-consumer" />
        </div>
        <div class="field" style="margin:0">
          <label>权限范围</label>
          <select v-model="form.scope" class="select">
            <option value="read">只读 (read)</option>
            <option value="write">读写 (write)</option>
          </select>
        </div>
        <button class="btn btn-primary" :disabled="creating" @click="createKey">{{ creating ? '创建中…' : '创建' }}</button>
      </div>
      <!-- 创建成功后仅展示一次 -->
      <div v-if="createdToken" class="token-box">
        <div class="token-title">密钥已生成（仅显示一次，请立即保存）</div>
        <div class="token-value">{{ createdToken }}</div>
        <button class="btn btn-sm" @click="copyToken">复制</button>
      </div>
    </div>

    <div v-if="loading" class="loading"><span class="spinner"></span>加载中…</div>
    <EmptyState v-else-if="!keys.length" icon="🔑" text="暂无密钥" />
    <table v-else class="table card">
      <thead>
        <tr><th>名称</th><th>前缀</th><th>范围</th><th>状态</th><th>最近使用</th><th>创建时间</th><th>操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="k in keys" :key="k.id">
          <td style="font-weight:600">{{ k.name }}</td>
          <td class="mono">{{ k.prefix }}…</td>
          <td><span class="tag" :class="k.scope === 'write' ? 'tag-amber' : 'tag-slate'">{{ k.scope }}</span></td>
          <td><span class="tag" :class="k.enabled ? 'tag-green' : 'tag-red'">{{ k.enabled ? '启用' : '停用' }}</span></td>
          <td class="muted">{{ fmtTime(k.last_used_at) }}</td>
          <td class="muted">{{ fmtTime(k.created_at) }}</td>
          <td><button class="btn btn-sm btn-danger" @click="deleteKey(k)">删除</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtTime, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'

const keys = ref([])
const loading = ref(false)
const creating = ref(false)
const createdToken = ref('')
const form = ref({ name: '', scope: 'read' })

async function load() {
  loading.value = true
  try {
    keys.value = await api.get('/keys')
  } catch {
    keys.value = []
  } finally {
    loading.value = false
  }
}

async function createKey() {
  if (!form.value.name.trim()) { toast('请填写密钥名称', 'err'); return }
  creating.value = true
  try {
    const res = await api.post('/keys', { name: form.value.name.trim(), scope: form.value.scope })
    createdToken.value = res.api_key || res.token || res.key || ''
    form.value = { name: '', scope: 'read' }
    toast('密钥已创建', 'ok')
    load()
  } catch (e) {
    toast(e.message || '创建失败', 'err')
  } finally {
    creating.value = false
  }
}

async function copyToken() {
  try {
    await navigator.clipboard.writeText(createdToken.value)
    toast('已复制', 'ok')
  } catch {
    toast('复制失败，请手动选择复制', 'err')
  }
}

async function deleteKey(k) {
  if (!confirm(`确认删除密钥「${k.name}」？使用该密钥的服务将立即失效。`)) return
  try {
    await api.del(`/keys/${k.id}`)
    keys.value = keys.value.filter((x) => x.id !== k.id)
    toast('已删除', 'ok')
  } catch (e) {
    toast(e.message, 'err')
  }
}

onMounted(load)
</script>

<style scoped>
.token-box {
  margin-top: 14px; background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px;
  padding: 12px; display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
}
.token-title { font-size: 12px; color: #92400e; width: 100%; }
.token-value {
  font-family: Consolas, 'Courier New', monospace; font-size: 13px; color: #78350f;
  background: #fff; padding: 8px 10px; border-radius: 6px; flex: 1; word-break: break-all;
}
</style>
