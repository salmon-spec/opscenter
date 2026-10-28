<template>
  <section>
    <button class="btn btn-ghost" :disabled="loading" @click="load">检测 WireGuard</button>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="result">
      <p>WG IP：{{ result.wireguard_ip || '—' }}</p>
      <p v-if="!result.wireguard">暂时无法读取 Agent，请检查监控连接。</p>
      <p v-else-if="!result.wireguard.supported">{{ result.wireguard.reason || '未检测到 WireGuard' }}</p>
      <template v-else>
        <p>已安装 WireGuard · {{ result.wireguard.interfaces?.length || 0 }} 个运行接口</p>
        <article v-for="iface in result.wireguard.interfaces" :key="iface.name">
          <h4>{{ iface.name }}</h4>
          <p>地址：{{ (iface.addresses || []).join('、') || '—' }}</p>
          <p>监听端口：{{ iface.listen_port }} · Peer：{{ iface.peers?.length || 0 }}</p>
          <p v-for="peer in iface.peers" :key="peer.public_key_fingerprint">
            {{ peer.endpoint || '未设置端点' }} · 最近握手：{{ peer.latest_handshake_age_seconds == null ? '尚未握手' : peer.latest_handshake_age_seconds + ' 秒前' }}
          </p>
        </article>
        <p>检测时间：{{ result.wireguard.generated_at || '—' }}；后台约每 3 分钟自动回填地址。</p>
      </template>
    </template>
  </section>
</template>
<script setup>
import { ref, watch } from 'vue'
import { api } from '../../api'
const props = defineProps({ hostId: { type: String, required: true } })
const result = ref(null), error = ref(''), loading = ref(false)
async function load() {
  const id = props.hostId
  loading.value = true; error.value = ''
  try {
    const value = await api.get(`/servers/${id}/wireguard`, undefined, { timeoutMs: 20000 })
    if (id === props.hostId) result.value = value
  } catch (e) { if (id === props.hostId) error.value = e.message }
  finally { if (id === props.hostId) loading.value = false }
}
watch(() => props.hostId, () => { result.value = null; load() }, { immediate: true })
</script>
