<template>
  <div class="view password-manager">
    <div class="view-head">
      <div><h1 class="view-title">个人密码库</h1><div class="view-sub">集中保存账号、密码与安全笔记，手机和电脑同步使用。</div></div>
      <button class="btn" :disabled="loading" @click="load">{{ loading ? '检查中…' : '刷新状态' }}</button>
    </div>
    <p v-if="error" class="notice" role="alert">{{ error }}</p>
    <section class="vault-panel">
      <div class="vault-heading"><span class="vault-icon" aria-hidden="true">PW</span><div><h2>Vaultwarden</h2><span class="connection" :class="{ online: health.available }" role="status">{{ loading ? '正在检查连接' : health.available ? '入口连接正常' : '入口暂不可用' }}</span></div></div>
      <p>打开密码库后，使用密码库账号登录并解锁。</p>
      <div v-if="health.url" class="server-address"><code>{{ health.url }}</code><button class="btn btn-sm" @click="copyAddress">复制地址</button></div>
      <div class="vault-actions">
        <a v-if="health.available && health.url" class="btn btn-primary" :href="health.url" target="_blank" rel="noopener noreferrer">打开密码库 ↗</a>
        <button v-else class="btn btn-primary" disabled>打开密码库</button>
        <span v-if="health.version" class="muted">网页客户端 {{ health.version }}</span>
      </div>
      <p v-if="health.checked_at" class="muted checked">检查时间 {{ fmtTime(health.checked_at) }}<span v-if="health.latency_ms != null"> · {{ health.latency_ms }} ms</span></p>
    </section>
    <div class="client-grid">
      <section class="vault-panel"><h3>浏览器自动填充</h3><ol><li>安装 Bitwarden 浏览器扩展。</li><li>登录页面选择“自托管”，填入上方服务器地址。</li><li>登录密码库，按需启用自动填充与自动锁定。</li></ol><a href="https://bitwarden.com/download/" target="_blank" rel="noopener noreferrer">下载浏览器扩展 ↗</a></section>
      <section class="vault-panel"><h3>手机同步</h3><ol><li>安装 Bitwarden 手机 App。</li><li>连接家庭网络对应的 WireGuard VPN。</li><li>选择“自托管”，使用同一服务器地址和密码库账号。</li></ol><a href="https://bitwarden.com/help/change-client-environment/" target="_blank" rel="noopener noreferrer">查看客户端配置说明 ↗</a></section>
    </div>
    <section class="vault-panel certificate-note"><h3>首次连接与证书</h3><p>首次在新设备使用时，需信任家庭服务的内部 CA 证书。若已为工作台安装该证书，可直接使用。</p><a href="/v3/certificates/opscenter-internal-ca.crt" download>下载内部 CA 证书</a><p class="muted">SHA256 指纹</p><code class="fingerprint">E99B1D892C63201092F9BAE25E4708D8E906577B8E8A96E92093520CCE30F09D</code><p class="muted">请核对证书指纹后安装；遇到证书错误时先检查系统时间和证书信任。</p></section>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtTime, toast } from '../api'
import { copyToClipboard } from '../utils/clipboard'

const health = ref({})
const loading = ref(false)
const error = ref('')
async function load() {
  loading.value = true
  error.value = ''
  health.value = {}
  try {
    const result = await api.get('/modules/password-manager/status')
    health.value = result.health || {}
    error.value = health.value.error || ''
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
.fingerprint{font-size:11px;overflow-wrap:anywhere}
.password-manager{max-width:1100px}.vault-panel{padding:24px;background:#fff;border:1px solid var(--border);border-radius:12px;margin-bottom:16px}.vault-heading{display:flex;align-items:center;gap:14px}.vault-icon{display:grid;place-items:center;width:52px;height:52px;border-radius:14px;background:#eef4ff;color:var(--brand);font-size:20px;font-weight:700}.vault-heading h2{margin:0 0 5px;font-size:20px}.connection{color:var(--warn);font-size:13px}.connection.online{color:var(--ok)}.vault-panel p,.vault-panel li{font-size:14px;line-height:1.8;color:var(--muted)}.server-address{display:flex;align-items:center;flex-wrap:wrap;gap:12px;padding:12px 14px;border-radius:8px;background:#f6f8fb}.server-address code{overflow-wrap:anywhere}.vault-actions{display:flex;align-items:center;flex-wrap:wrap;gap:16px;margin-top:20px}.vault-actions a{text-decoration:none}.checked{margin:18px 0 0;font-size:12px!important}.client-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.vault-panel h3{font-size:15px;margin:0 0 12px}.vault-panel ol{padding-left:20px}.vault-panel a{font-size:13px}.notice{padding:12px 16px;background:#fff7e8;border:1px solid #f0d89e;border-radius:8px;color:#805f20}.certificate-note p{margin:8px 0}.certificate-note .muted{font-size:12px}@media(max-width:700px){.client-grid{grid-template-columns:1fr;gap:0}.vault-panel{padding:18px}}
</style>
