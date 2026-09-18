<template>
  <div class="view terminal-page">
    <aside class="terminal-sidebar">
      <section class="sidebar-section hosts-section">
        <div class="section-label"><span>目标主机</span><span>{{ hosts.length }}</span></div>
        <div class="host-picker">
          <span class="host-state" :class="currentHost?.status==='online'?'online':'offline'"></span>
          <select class="host-select" :value="selectedHostId" :disabled="!hosts.length" @change="selectTerminalHost($event.target.value)">
            <option value="" disabled>{{ hosts.length ? '请选择主机' : '暂无可用主机' }}</option>
            <option v-for="host in hosts" :key="host.id" :value="host.id">{{ host.name }}</option>
          </select>
        </div>
        <small class="host-address">{{ currentHost?.lan_ip || currentHost?.host || currentHost?.wireguard_ip || '地址未知' }}</small>
      </section>


      <section class="sidebar-section sessions-section">
        <div class="section-label"><span>当前主机会话</span><span>{{ visibleSessions.length }}</span></div>
        <div class="session-list">
          <div v-for="s in visibleSessions" :key="s.sessionId" class="session-item" :class="{active:s.sessionId===activeSessionId}" role="button" tabindex="0" @click="activate(s.sessionId)" @keydown.enter="activate(s.sessionId)">
            <span class="tab-dot" :class="tabDotClass(s.status)"></span>
            <input v-if="editingTitle===s.sessionId" v-model="renameText" class="session-title-input" @keydown.enter.stop="commitRename(s)" @blur="commitRename(s)" @click.stop />
            <span v-else class="session-title" :title="`${s.serverName} · ${s.sessionId}`" @dblclick.stop="beginRename(s)">{{ s.title }}</span>
            <button class="session-close" :disabled="deletingSession===s.sessionId" title="关闭会话" @click.stop="closeTab(s)">{{ deletingSession===s.sessionId?'…':'×' }}</button>
          </div>
          <p v-if="!visibleSessions.length" class="empty-list">该主机暂无会话</p>
        </div>
      </section>

      <div class="sidebar-footnote"><span class="host-state" :class="currentHost?.status==='online'?'online':'offline'"></span>{{ currentHost?.name || '未选择主机' }}</div>
    </aside>

    <main class="terminal-main">
      <div v-if="pageError" class="error-bar"><p>{{ pageError }}</p></div>
      <div v-show="visibleSessions.length && activeSessionId" class="term-stage">
        <div v-for="s in sessions" v-show="s.sessionId===activeSessionId" :key="s.sessionId" class="term-pane">
          <TerminalPanel embedded :session-id="s.sessionId" :title="s.title" :allow-files="s.allowFiles" :active="s.sessionId===activeSessionId" @state="onPanelState(s.sessionId,$event)" />
        </div>
      </div>
      <div v-if="!visibleSessions.length" class="connect">
        <div class="connect-icon">›_</div>
        <h2>{{ currentHost?.name || '请选择主机' }}</h2>
        <p>建立安全终端会话，断线后 5 分钟内可重新连接。</p>
        <button class="btn btn-primary" :disabled="!selectedHostId||creating" @click="createTerminal()">{{ creating?'创建中…':'连接终端' }}</button>
      </div>
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import TerminalPanel from '../components/TerminalPanel.vue'
import { useHostContext } from '../hostContext'

const { hosts, selectedHostId, currentHost, refreshHosts, selectHost } = useHostContext()
const sessions = ref([])
const visibleSessions = computed(() => sessions.value.filter((s) => s.serverId === selectedHostId.value))
const activeSessionId = ref('')
const creating = ref(false)
const pageError = ref('')
const deletingSession = ref('')
const editingTitle = ref('')
const renameText = ref('')
const STORAGE_KEY = 'ops-terminal-tabs'
let seq = 0

function nextTitle(serverName) { seq += 1; return `${serverName} · 终端 ${seq}` }

function persistTabs() {
  // 只持久化会话 ID 与标签元数据；不保存 WebSocket 内容、密码或 Token
  try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.value.map((s) => ({ ...s })))) } catch { /* sessionStorage 不可用时忽略 */ }
}

async function createTerminal(hostId = selectedHostId.value) {
  if (!hostId || creating.value) return
  creating.value = true
  pageError.value = ''
  try {
    const data = await api.post('/terminal/sessions', { server_id: hostId })
    const serverName = data.server_name || hosts.value.find((host) => host.id === hostId)?.name || '主机'
    const tab = {
      sessionId: data.session_id,
      serverId: hostId,
      serverName,
      title: nextTitle(serverName),
      status: 'connected',
      allowFiles: data.transport !== 'local-pty',
      createdAt: String(Date.now()),
    }
    sessions.value.push(tab)
    activeSessionId.value = tab.sessionId
    persistTabs()
  } catch (e) {
    pageError.value = e.message
  } finally {
    creating.value = false
  }
}

function activate(id) { activeSessionId.value = id }

async function selectTerminalHost(id) {
  if (!id) return
  selectHost(id)
  const existing = sessions.value.filter((s) => s.serverId === id)
  if (existing.length) {
    activeSessionId.value = existing.find((s) => s.sessionId === activeSessionId.value)?.sessionId || existing[0].sessionId
    return
  }
  await createTerminal(id)
}

function tabDotClass(status) {
  return ({ connecting: 'conn', connected: 'ok', disconnected: 'warn', closed: 'off', error: 'err' })[status] || 'off'
}

function onPanelState(sid, st) {
  const t = sessions.value.find((s) => s.sessionId === sid)
  if (!t) return
  t.status = st === 'connected' ? 'connected' : (st === 'connecting' ? 'connecting' : 'disconnected')
}

async function closeTab(s) {
  if (deletingSession.value) return
  deletingSession.value = s.sessionId
  try {
    // 显式销毁服务端会话；失败时本地标签也关闭（服务端宽限期后会自行清理）
    await api.del(`/terminal/sessions/${s.sessionId}`)
  } catch { /* 忽略：服务端可能已过期 */ }
  sessions.value = sessions.value.filter((x) => x.sessionId !== s.sessionId)
  if (activeSessionId.value === s.sessionId) activeSessionId.value = sessions.value.find((x) => x.serverId === selectedHostId.value)?.sessionId || ''
  persistTabs()
  deletingSession.value = ''
}

function beginRename(s) { editingTitle.value = s.sessionId; renameText.value = s.title }
function commitRename(s) {
  if (editingTitle.value !== s.sessionId) return
  const v = renameText.value.trim()
  if (v && v !== s.title) { s.title = v; persistTabs() }
  editingTitle.value = ''
}

async function restore() {
  let stored = []
  try { stored = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || '[]') } catch { stored = [] }
  seq = stored.length
  const restored = await Promise.all(stored.filter((t) => t?.sessionId).map(async (t) => {
    try {
      const st = await api.get(`/terminal/sessions/${t.sessionId}/status`)
      if (st && st.reconnectable) {
        return { ...t, status: st.state === 'reconnecting' ? 'disconnected' : 'connected' }
      }
      // 否则：会话已过期或被服务端清理，直接丢弃标签
    } catch { /* 查询失败时保守丢弃，避免出现无法连接的僵尸标签 */ }
    return null
  }))
  sessions.value.push(...restored.filter(Boolean))
  activeSessionId.value = sessions.value.find((s) => s.serverId === selectedHostId.value)?.sessionId || ''
  persistTabs()
}

onMounted(async () => { await refreshHosts(); await restore() })
</script>

<style scoped>
.terminal-page{height:100%;min-height:0;max-width:none;margin:0;padding:0;display:grid;grid-template-columns:184px minmax(0,1fr);overflow:hidden;background:#eef2f7}
.terminal-sidebar{min-height:0;display:flex;flex-direction:column;background:#fff;border-right:1px solid var(--border)}
.sidebar-section{padding:8px 6px 0}.section-label{height:25px;padding:0 6px;display:flex;align-items:center;justify-content:space-between;color:var(--muted);font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.session-list{display:flex;flex-direction:column;gap:4px}.hosts-section{flex:none}.sessions-section{flex:1;min-height:0;display:flex;flex-direction:column}.sessions-section .session-list{min-height:0;overflow:auto;padding-bottom:8px;scrollbar-width:thin}
.host-picker{margin:4px 6px 0;display:flex;align-items:center;gap:7px}.host-select{min-width:0;flex:1;height:34px;border:1px solid var(--border);border-radius:8px;background:#fff;color:var(--text);padding:0 26px 0 9px;font:inherit;font-size:12px;outline:none}.host-select:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(37,99,235,.12)}.host-select:disabled{background:#f8fafc;color:var(--muted)}.host-address{display:block;margin:5px 8px 0;color:var(--muted);font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.host-state{width:8px;height:8px;border-radius:50%;background:#94a3b8;flex:none}.host-state.online{background:var(--ok);box-shadow:0 0 0 3px rgba(34,197,94,.12)}.host-state.offline{background:#94a3b8}
.session-item{min-height:38px;padding:6px 5px 6px 9px;border:1px solid transparent;border-radius:8px;display:flex;align-items:center;gap:7px;cursor:pointer;outline:none}.session-item:hover{background:#f8fafc}.session-item.active{background:#f1f5f9;border-color:#cbd5e1}.session-title{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px}.session-title-input{min-width:0;flex:1;border:1px solid var(--primary);border-radius:5px;padding:3px 5px;font-size:12px}.session-close{width:24px;height:24px;padding:0;border:0;border-radius:5px;background:transparent;color:var(--muted);cursor:pointer}.session-close:hover{background:#fee2e2;color:var(--err)}
.sidebar-footnote{height:42px;padding:0 16px;border-top:1px solid var(--border);display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.terminal-main{min-width:0;min-height:0;padding:12px;display:flex;flex-direction:column;background:#e9eef5}
.tab-dot{width:8px;height:8px;border-radius:50%;flex:none}
.tab-dot.ok{background:var(--ok)}.tab-dot.conn{background:var(--warn)}.tab-dot.warn{background:var(--warn)}.tab-dot.off{background:#94a3b8}.tab-dot.err{background:var(--err)}
.error-bar{flex:none;margin:0 0 8px;padding:9px 12px;border:1px solid #fecaca;border-radius:8px;background:#fef2f2}
.error-bar p{margin:0;color:var(--err);font-size:13px}
.term-stage{flex:1;min-height:0;position:relative;overflow:hidden}
.term-pane{position:absolute;inset:0}
.connect{text-align:center;display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;min-height:0;border:1px dashed #cbd5e1;border-radius:12px;background:#fff}.connect-icon{width:58px;height:58px;border-radius:14px;display:grid;place-items:center;background:#0d1117;color:#7dd3fc;font:700 18px/1 Consolas,monospace}.connect h2{margin:16px 0 4px;font-size:18px}.connect p{max-width:390px;margin:0 0 18px;color:var(--muted);font-size:13px}.empty-list{margin:8px 6px;color:var(--muted);font-size:12px}
@media(max-width:760px){.terminal-page{grid-template-columns:156px minmax(0,1fr)}.terminal-main{padding:7px}}
</style>
