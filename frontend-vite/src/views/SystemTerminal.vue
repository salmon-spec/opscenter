<template>
  <div class="view terminal-page">
    <div class="view-head"><div><h1 class="view-title">终端</h1><div class="view-sub">{{ currentHost?.name || '未选择主机' }} · 新建会话使用顶栏当前主机，可同时打开多个标签</div></div></div>
    <div class="term-tabs">
      <div v-for="s in sessions" :key="s.sessionId" class="term-tab" :class="{active:s.sessionId===activeSessionId}" @click="activate(s.sessionId)">
        <span class="tab-dot" :class="tabDotClass(s.status)"></span>
        <input v-if="editingTitle===s.sessionId" v-model="renameText" class="tab-title-input" @keydown.enter="commitRename(s)" @blur="commitRename(s)" @click.stop />
        <span v-else class="tab-title" :title="`${s.serverName} · ${s.sessionId}`" @dblclick="beginRename(s)">{{ s.title }}</span>
        <button class="tab-close" :disabled="deletingSession===s.sessionId" @click.stop="closeTab(s)">{{ deletingSession===s.sessionId?'…':'×' }}</button>
      </div>
      <button class="btn btn-sm btn-primary new-tab" :disabled="!selectedHostId||creating" @click="createTerminal">{{ creating?'创建中…':'＋ 新建终端' }}</button>
    </div>
    <div v-if="pageError" class="card error-bar"><p>{{ pageError }}</p></div>
    <div v-show="sessions.length && activeSessionId" class="term-stage">
      <div v-for="s in sessions" v-show="s.sessionId===activeSessionId" :key="s.sessionId" class="term-pane">
        <TerminalPanel embedded :session-id="s.sessionId" :title="s.title" :allow-files="s.allowFiles" :active="s.sessionId===activeSessionId" @state="onPanelState(s.sessionId,$event)" />
      </div>
    </div>
    <div v-if="!sessions.length" class="card connect"><p>选择主机后建立安全终端会话，可同时打开多个标签；断线 5 分钟内可重连。</p><button class="btn btn-primary" :disabled="!selectedHostId" @click="createTerminal">连接终端</button></div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'
import TerminalPanel from '../components/TerminalPanel.vue'
import { useHostContext } from '../hostContext'

const { selectedHostId, currentHost, refreshHosts } = useHostContext()
const sessions = ref([])
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

async function createTerminal() {
  if (!selectedHostId.value || creating.value) return
  creating.value = true
  pageError.value = ''
  try {
    const data = await api.post('/terminal/sessions', { server_id: selectedHostId.value })
    const serverName = data.server_name || currentHost.value?.name || '主机'
    const tab = {
      sessionId: data.session_id,
      serverId: selectedHostId.value,
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

function tabDotClass(s) {
  return ({ connecting: 'conn', connected: 'ok', disconnected: 'warn', closed: 'off', error: 'err' })[s.status] || 'off'
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
  if (activeSessionId.value === s.sessionId) activeSessionId.value = sessions.value[0]?.sessionId || ''
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
  activeSessionId.value = sessions.value[0]?.sessionId || ''
  persistTabs()
}

onMounted(async () => { await refreshHosts(); await restore() })
</script>

<style scoped>
.terminal-page{height:calc(100vh - 56px);display:flex;flex-direction:column}
.terminal-page>.view-head{flex:none}
.term-tabs{display:flex;align-items:center;gap:4px;flex-wrap:nowrap;overflow-x:auto;padding:6px 0;flex:none;scrollbar-width:thin}
.term-tab{display:flex;align-items:center;gap:6px;padding:5px 6px 5px 10px;border:1px solid var(--border);border-radius:8px;background:var(--card);cursor:pointer;max-width:220px;flex:none;white-space:nowrap;transition:border-color .12s, background .12s}
.term-tab.active{border-color:var(--primary);background:rgba(37,99,235,.1)}
.term-tab:hover:not(.active){border-color:#94a3b8}
.tab-dot{width:8px;height:8px;border-radius:50%;flex:none}
.tab-dot.ok{background:var(--ok)}.tab-dot.conn{background:var(--warn)}.tab-dot.warn{background:var(--warn)}.tab-dot.off{background:#94a3b8}.tab-dot.err{background:var(--err)}
.tab-title{font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text)}
.tab-title-input{border:1px solid var(--primary);border-radius:4px;font-size:13px;padding:2px 4px;width:130px;background:var(--bg);color:var(--text)}
.tab-close{border:0;background:transparent;color:var(--muted);cursor:pointer;font-size:14px;line-height:1;padding:2px 5px;border-radius:4px}
.tab-close:hover{background:rgba(239,68,68,.12);color:var(--err)}
.new-tab{flex:none}
.error-bar{flex:none;margin:0 0 8px}
.error-bar p{margin:0;color:var(--err);font-size:13px}
.term-stage{flex:1;min-height:380px;position:relative;overflow:hidden}
.term-pane{position:absolute;inset:0}
.connect{text-align:center;display:flex;flex-direction:column;align-items:center;justify-content:center;flex:1;min-height:420px}
.connect p{color:var(--muted)}
</style>
