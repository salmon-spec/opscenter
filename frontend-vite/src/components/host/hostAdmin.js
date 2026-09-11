import { onUnmounted } from 'vue'
import { useHostContext } from '../../hostContext'

export function compareVersions(a, b) {
  const parts = (value) => String(value || '0').match(/\d+/g)?.slice(0, 4).map(Number) || [0]
  const x = parts(a)
  const y = parts(b)
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const diff = (x[i] || 0) - (y[i] || 0)
    if (diff) return diff
  }
  return 0
}

export function isHostOutdated(host, currentVersion) {
  return !!host && host.agent_status === 'running'
    && compareVersions(host.agent_version, currentVersion) < 0
    && (host.agent_type === 'local' || host.has_credentials)
}

export function buildServerPayload(form) {
  const isLocalEdit = !!form.id && (form.agent_type === 'local' || form.is_local)
  const payload = {
    name: form.name,
    remark: form.remark,
    tags: String(form.tagsText || '').split(',').map((item) => item.trim()).filter(Boolean),
  }
  if (!isLocalEdit) {
    Object.assign(payload, {
      host: form.host,
      ssh_port: Number(form.ssh_port),
      ssh_user: form.ssh_user,
      ssh_password: form.auth_type === 'password' ? form.ssh_password : undefined,
      ssh_key: form.auth_type === 'key' ? form.ssh_key : undefined,
    })
  }
  Object.assign(payload, {
    lan_ip: form.lan_ip || '',
    wireguard_ip: form.wireguard_ip || '',
    preferred_management_channel: form.preferred_management_channel || 'auto',
    management_address_override: form.management_address_override || '',
    kubernetes_node_name: form.kubernetes_node_name || '',
    node_role: form.node_role || '',
    runtime_type: form.runtime_type || '',
  })
  if (!form.id) {
    Object.assign(payload, { auto_deploy_agent: !!form.auto_deploy_agent, is_local: false })
  }
  return payload
}

export function agentStatusLabel(value) {
  return { running: '运行中', deploying: '部署中', error: '异常', not_deployed: '未部署' }[value] || value || '未部署'
}

export function logAgentStatusLabel(value) {
  return {
    running: '运行中', deploying: '部署中', checking: '检查中', stopped: '已停止',
    error: '异常', not_deployed: '未部署', unknown: '未检查',
  }[value] || '未检查'
}

export function agentTagClass(value) {
  return { running: 'tag-green', deploying: 'tag-amber', error: 'tag-red', not_deployed: 'tag-slate' }[value] || 'tag-slate'
}

export function logTagClass(value) {
  return {
    running: 'tag-green', deploying: 'tag-amber', checking: 'tag-amber',
    stopped: 'tag-red', error: 'tag-red', not_deployed: 'tag-slate', unknown: 'tag-slate',
  }[value] || 'tag-slate'
}

export function monitoringAddress(server) {
  return server?.lan_ip || server?.host || '—'
}

export function monitoringChannelLabel(server) {
  return server?.lan_ip ? '局域网 LAN（优先）' : '主地址（无 LAN 时备用）'
}

export function runtimeTypeLabel(value) {
  return { containerd: '容器（containerd）', docker: 'Docker', none: '无' }[value] || '未设置'
}

export function useHostPolling({ visible, hasWork } = {}) {
  const { hosts, refreshHosts } = useHostContext()
  let timer = null
  function hasBackgroundWork() {
    if (hasWork) return !!hasWork()
    return hosts.value.some((host) => host.agent_status === 'deploying'
      || ['deploying', 'checking'].includes(host.log_agent_status))
  }
  function tick() {
    if ((!visible || visible()) && hasBackgroundWork()) refreshHosts(true)
  }
  function startPolling() {
    stopPolling()
    timer = setInterval(tick, 2500)
  }
  function stopPolling() {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }
  onUnmounted(stopPolling)
  return { startPolling, stopPolling }
}
