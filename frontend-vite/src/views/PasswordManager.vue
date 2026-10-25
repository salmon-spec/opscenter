<template>
  <div class="password-manager">
    <div class="vault-toolbar">
      <span class="connection" :class="{ online: health.available }" role="status">{{ loading ? '正在检查密码库' : health.available ? '密码库已连接' : '密码库暂不可用' }}</span>
      <div class="toolbar-actions"><button class="btn btn-sm" :disabled="loading" @click="load">刷新密码库</button></div>
    </div>
    <p v-if="error" class="notice" role="alert">{{ error }}</p>
    <section v-if="health.available && !secureWorkspace" class="secure-workspace">
      <h2>使用工作台安全连接</h2>
      <p>密码库的加密功能需要 HTTPS。请在安全连接中登录工作台，然后使用此页面管理密码。</p>
      <a class="btn btn-primary" :href="workspaceUrl">进入 HTTPS 工作台</a>
    </section>
    <iframe v-if="canEmbed" :key="frameRevision" class="vault-frame" :src="health.url + '/'"
      title="个人密码库" sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-modals"
      allow="clipboard-write" referrerpolicy="no-referrer"></iframe>
    <details class="client-settings">
      <summary>客户端与证书</summary>
      <div class="settings-body">
        <p>浏览器扩展和手机 App 使用 Bitwarden，登录时选择“自托管”，填入服务器地址。</p>
        <div v-if="health.url" class="server-address"><code>{{ health.url }}</code><button class="btn btn-sm" @click="copyAddress">复制地址</button></div>
        <p>新设备请先连接 WireGuard，并信任家庭服务 CA 证书。</p>
        <a href="/v3/certificates/opscenter-internal-ca.crt" download>下载内部 CA 证书</a>
        <p class="muted">SHA256：<code>E99B1D892C63201092F9BAE25E4708D8E906577B8E8A96E92093520CCE30F09D</code></p>
      </div>
    </details>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api, toast } from '../api'
import { copyToClipboard } from '../utils/clipboard'

const health = ref({})
const loading = ref(false)
const error = ref('')
const frameRevision = ref(0)
const workspaceUrl = computed(() => health.value.workbench_origin ? health.value.workbench_origin + '/v3/#/extensions/password-manager' : '')
const secureWorkspace = computed(() => window.isSecureContext && window.location.origin === health.value.workbench_origin)
const canEmbed = computed(() => Boolean(health.value.available && secureWorkspace.value && health.value.url && new URL(health.value.url).origin !== window.location.origin))
async function load() {
  loading.value = true
  error.value = ''
  health.value = {}
  try {
    const result = await api.get('/modules/password-manager/status')
    health.value = result.health || {}
    error.value = health.value.error || ''
    frameRevision.value += 1
  } catch (err) {
    error.value = err.status === 409 ? '密码管理入口已隐藏，请在模块管理中显示入口。' : err.message
  } finally {
    loading.value = false
  }
}
async function copyAddress() {
  try { await copyToClipboard(health.value.url); toast('服务器地址已复制', 'ok') }
  catch { toast('复制失败，请手动复制服务器地址', 'err') }
}
onMounted(load)
</script>

<style scoped>
.password-manager{height:100%;min-height:560px;display:flex;flex-direction:column;gap:12px}.vault-toolbar{display:flex;align-items:center;justify-content:space-between;gap:12px;flex:none}.connection{color:var(--warn);font-size:13px}.connection.online{color:var(--ok)}.vault-frame{width:100%;flex:1;min-height:480px;border:1px solid var(--border);border-radius:10px;background:#fff}.notice{margin:0;padding:12px 16px;background:#fff7e8;border:1px solid #f0d89e;border-radius:8px;color:#805f20}.secure-workspace{padding:24px;background:#fff;border:1px solid var(--border);border-radius:10px}.secure-workspace h2{font-size:18px}.secure-workspace p{line-height:1.7;color:var(--muted)}.secure-workspace a{text-decoration:none}.client-settings{flex:none;padding:12px 16px;background:#fff;border:1px solid var(--border);border-radius:8px;font-size:13px}.client-settings summary{cursor:pointer;font-weight:600}.settings-body{padding-top:4px}.settings-body p{line-height:1.7;color:var(--muted)}.settings-body code{overflow-wrap:anywhere}.settings-body .muted{font-size:11px}.server-address{display:flex;align-items:center;flex-wrap:wrap;gap:12px;padding:10px;border-radius:6px;background:#f6f8fb}
</style>
