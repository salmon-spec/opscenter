<template>
  <Teleport to="body">
    <div v-if="visible" class="drawer-mask" @click.self="$emit('close')">
      <aside class="editor-drawer">
        <header>
          <div>
            <h2>{{ form.id ? '编辑主机' : '添加主机' }}</h2>
            <p>SSH 连接信息与 LAN/WG 双通道地址</p>
          </div>
          <button class="btn btn-ghost" @click="$emit('close')">✕</button>
        </header>

        <div class="editor-body">
          <section class="form-section">
            <h3>基础信息</h3>
            <label>主机名称<input v-model.trim="form.name" maxlength="50" /></label>
            <label>地址<input v-model.trim="form.host" :disabled="isLocalEdit" placeholder="10.66.66.x 或域名" /></label>
            <div class="form-grid">
              <label>SSH 端口<input v-model.number="form.ssh_port" :disabled="isLocalEdit" type="number" min="1" max="65535" /></label>
              <label>SSH 用户<input v-model.trim="form.ssh_user" :disabled="isLocalEdit" /></label>
            </div>
            <label>备注<input v-model.trim="form.remark" maxlength="500" /></label>
            <label>标签（逗号分隔）<input v-model.trim="form.tagsText" maxlength="300" placeholder="生产, 数据库, 华东" /></label>
            <label v-if="!isLocalEdit">认证方式
              <select v-model="form.auth_type">
                <option value="password">密码</option>
                <option value="key">私钥</option>
              </select>
            </label>
            <label v-if="!isLocalEdit && form.auth_type === 'password'">{{ form.id ? '新密码（留空保留）' : 'SSH 密码' }}<input v-model="form.ssh_password" type="password" autocomplete="new-password" /></label>
            <label v-if="!isLocalEdit && form.auth_type === 'key'">{{ form.id ? '新私钥（留空保留）' : 'SSH 私钥' }}<textarea v-model="form.ssh_key" rows="5" /></label>
            <label v-if="!form.id" class="check"><input v-model="form.auto_deploy_agent" type="checkbox" /> 添加成功后自动部署监控 Agent</label>
          </section>

          <section class="form-section">
            <h3>监控地址与 K3s 映射</h3>
            <div class="form-grid">
              <label>局域网 IP（LAN）<input v-model.trim="form.lan_ip" placeholder="192.168.1.x（可空）" /></label>
              <label>WireGuard IP<input v-model.trim="form.wireguard_ip" placeholder="10.66.66.x（可空）" /></label>
            </div>
            <p class="address-policy">主机监控固定优先使用 LAN；未配置 LAN 时使用主地址。WireGuard / 公网地址继续用于服务广场访问。</p>
            <div class="form-grid">
              <label>K8s 节点名<input v-model.trim="form.kubernetes_node_name" placeholder="集群内节点名（可空）" /></label>
              <label>节点角色<input v-model.trim="form.node_role" placeholder="control-plane / worker / infra" /></label>
            </div>
            <label>运行时类型
              <select v-model="form.runtime_type">
                <option value="">未设置</option>
                <option value="containerd">容器（containerd）</option>
                <option value="docker">Docker</option>
                <option value="none">无</option>
              </select>
            </label>
          </section>

          <div v-if="testMessage" class="test-result" :class="testOk ? 'ok' : 'bad'">{{ testMessage }}</div>
        </div>

        <footer class="editor-foot">
          <button class="btn" @click="$emit('close')">取消</button>
          <button v-if="!isLocalEdit" class="btn" :disabled="busy" @click="testConnection">{{ testing ? '测试中…' : '测试 SSH' }}</button>
          <button class="btn btn-primary" :disabled="busy || !form.name || !form.host" @click="save">{{ saving ? '保存中…' : '保存' }}</button>
        </footer>
      </aside>
    </div>
  </Teleport>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api, toast } from '../../api'
import { buildServerPayload } from './hostAdmin'

const props = defineProps({
  visible: Boolean,
  host: { type: Object, default: null },
})
const emit = defineEmits(['close', 'saved'])

const saving = ref(false)
const testing = ref(false)
const testMessage = ref('')
const testOk = ref(false)

const form = reactive({
  id: '', name: '', host: '', ssh_port: 22, ssh_user: 'root', remark: '', tagsText: '',
  auth_type: 'password', ssh_password: '', ssh_key: '', auto_deploy_agent: true,
  agent_type: 'remote', is_local: false,
  lan_ip: '', wireguard_ip: '', preferred_management_channel: 'auto',
  management_address_override: '', kubernetes_node_name: '', node_role: '', runtime_type: '',
})

const isLocalEdit = computed(() => !!form.id && (form.agent_type === 'local' || form.is_local))
const busy = computed(() => saving.value || testing.value)

function fillForm(host) {
  Object.assign(form, {
    id: host?.id || '',
    name: host?.name || '',
    host: host?.host || '',
    ssh_port: host?.ssh_port || 22,
    ssh_user: host?.ssh_user || 'root',
    remark: host?.remark || '',
    tagsText: (host?.tags || []).join(', '),
    auth_type: 'password',
    ssh_password: '',
    ssh_key: '',
    auto_deploy_agent: !host?.id,
    agent_type: host?.agent_type || 'remote',
    is_local: !!host?.is_local,
    lan_ip: host?.lan_ip || '',
    wireguard_ip: host?.wireguard_ip || '',
    preferred_management_channel: host?.preferred_management_channel || 'auto',
    management_address_override: host?.management_address_override || '',
    kubernetes_node_name: host?.kubernetes_node_name || '',
    node_role: host?.node_role || '',
    runtime_type: host?.runtime_type || '',
  })
  testMessage.value = ''
}

watch(() => [props.visible, props.host?.id], ([visible]) => {
  if (visible) fillForm(props.host)
})

async function testConnection() {
  if (!form.host) return
  testing.value = true
  testMessage.value = ''
  try {
    const data = await api.post('/test-ssh', {
      host: form.host,
      port: form.ssh_port,
      username: form.ssh_user,
      password: form.auth_type === 'password' ? form.ssh_password : null,
      ssh_key: form.auth_type === 'key' ? form.ssh_key : null,
    })
    testOk.value = !!data.success
    testMessage.value = data.message || data.error || (testOk.value ? '连接成功' : '连接失败')
  } catch (error) {
    testOk.value = false
    testMessage.value = error.message
  } finally {
    testing.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const payload = buildServerPayload(form)
    if (form.id) {
      await api.put(`/servers/${form.id}`, payload)
      toast('主机信息已更新', 'ok')
    } else {
      const data = await api.post('/servers', payload)
      toast(data.agent_status === 'deploying' ? '主机已添加，Agent 正在后台部署' : '主机已添加', 'ok')
    }
    emit('saved')
  } catch (error) {
    toast(`保存失败：${error.message}`, 'err')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.drawer-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, .48); z-index: 2400; display: flex; justify-content: flex-end; }
.editor-drawer { width: min(640px, 96vw); height: 100%; background: var(--bg); box-shadow: -12px 0 40px rgba(15, 23, 42, .2); padding: 20px; display: flex; flex-direction: column; }
.editor-drawer header { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid var(--border); padding-bottom: 14px; }
.address-policy { margin: 0; padding: 10px 12px; color: var(--muted); background: var(--card); border: 1px solid var(--border); border-radius: 8px; font-size: 12px; line-height: 1.6; }
.editor-drawer h2, .editor-drawer h3 { margin: 0; }
.editor-drawer p { margin: 5px 0 0; color: var(--muted); font-size: 13px; }
.editor-body { flex: 1; overflow: auto; padding: 4px 2px 12px; }
.form-section { margin-top: 14px; padding: 14px 16px; background: var(--card); border: 1px solid var(--border); border-radius: 10px; }
.form-section h3 { font-size: 13px; color: var(--muted); margin: 0 0 4px; }
.form-section > label, .form-grid label { display: flex; flex-direction: column; gap: 5px; margin-top: 11px; font-size: 13px; color: var(--muted); }
.form-section input, .form-section select, .form-section textarea { border: 1px solid var(--border); background: var(--bg); color: var(--text); padding: 9px; border-radius: 6px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.form-section .check { flex-direction: row; align-items: center; color: var(--text); }
.editor-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border); }
.test-result { margin-top: 12px; padding: 8px; border-radius: 6px; font-size: 12px; }
.test-result.ok { color: #15803d; background: #dcfce7; }
.test-result.bad { color: #b91c1c; background: #fee2e2; }
@media (max-width: 640px) { .form-grid { grid-template-columns: 1fr; } }
</style>
