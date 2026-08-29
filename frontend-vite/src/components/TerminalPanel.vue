<template>
  <Teleport to="body" :disabled="embedded">
    <div class="term-mask" :class="{ embedded }">
      <div class="term-box" :class="{ embedded }">
        <div class="term-head">
          <span class="term-title">终端 · {{ title }}</span>
          <div v-if="allowFiles" class="term-tabs">
            <button class="tab" :class="{ active: tab === 'term' }" @click="switchTab('term')">终端</button>
            <button class="tab" :class="{ active: tab === 'files' }" @click="switchTab('files')">文件传输</button>
          </div>
          <button v-if="!embedded" class="btn btn-ghost btn-sm" @click="$emit('close')">关闭</button>
        </div>
        <div v-show="tab === 'term'" class="term-body">
          <div ref="termEl" class="term-el"></div>
          <div v-if="wsState !== 'open'" class="term-state">
            {{ wsState === 'connecting' ? '正在连接 SSH…' : '连接已断开' }}
            <button v-if="wsState === 'closed'" class="reconnect" @click="reconnect">重新连接</button>
          </div>
        </div>
        <div v-show="tab === 'files'" class="files-body">
          <SftpPanel :session-id="sessionId" />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { wsUrl } from '../api'
import SftpPanel from './SftpPanel.vue'

const props = defineProps({
  sessionId: { type: String, required: true },
  title: { type: String, default: '' },
  allowFiles: { type: Boolean, default: true },
  embedded: { type: Boolean, default: false },
})
defineEmits(['close'])

const termEl = ref(null)
const tab = ref('term')
const wsState = ref('connecting')
let term = null
let fitAddon = null
let ws = null
let resizeObserver = null

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj))
}

function fit() {
  if (!term || !fitAddon) return
  try {
    fitAddon.fit()
    send({ type: 'resize', cols: term.cols, rows: term.rows })
  } catch { /* 容器隐藏时忽略 */ }
}

function connect() {
  wsState.value = 'connecting'
  if (!term) {
  term = new Terminal({
    cursorBlink: true,
    scrollback: 5000,
    fontSize: 13,
    fontFamily: 'Consolas, "Courier New", monospace',
    theme: { background: '#0d1117', foreground: '#e6edf3', cursor: '#58a6ff' },
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(termEl.value)
  fit()

  term.onData((data) => send({ type: 'input', data }))
  term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }))
  }

  ws = new WebSocket(wsUrl(`/ws/terminal/${props.sessionId}`))
  ws.onopen = () => { wsState.value = 'open' }
  ws.onmessage = (e) => term.write(String(e.data))
  ws.onclose = () => { wsState.value = 'closed' }
  ws.onerror = () => { wsState.value = 'closed' }

  // 容器尺寸变化时自动 fit（含全屏/窗口缩放）
  if (!resizeObserver) {
    resizeObserver = new ResizeObserver(() => fit())
    resizeObserver.observe(termEl.value)
  }
}

function reconnect() {
  if (ws) { try { ws.close() } catch { /* ignore */ } }
  connect()
}

function switchTab(t) {
  tab.value = t
  if (t === 'term') setTimeout(fit, 60)
}

onMounted(connect)
onUnmounted(() => {
  if (ws) { try { ws.close() } catch { /* ignore */ } }
  if (resizeObserver) resizeObserver.disconnect()
  if (term) term.dispose()
})
</script>

<style scoped>
.term-mask {
  position: fixed; inset: 0; background: rgba(15,23,42,.6); z-index: 2100;
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
.term-mask.embedded { position: relative; inset: auto; z-index: auto; padding: 0; background: transparent; width: 100%; height: 100%; }
.term-box {
  width: 92vw; height: 86vh; background: #0d1117; border-radius: 12px;
  display: flex; flex-direction: column; overflow: hidden; box-shadow: 0 20px 60px rgba(0,0,0,.4);
}
.term-box.embedded { width: 100%; height: 100%; border-radius: 10px; box-shadow: none; }
.term-head {
  display: flex; align-items: center; gap: 14px; padding: 10px 16px;
  background: #161b22; border-bottom: 1px solid #21262d;
}
.term-title { color: #e6edf3; font-size: 13px; font-weight: 600; flex: 1; }
.term-tabs { display: flex; gap: 4px; }
.tab {
  padding: 5px 14px; border-radius: 6px; border: none; background: transparent;
  color: #8b949e; font-size: 13px; cursor: pointer;
}
.tab.active { background: #21262d; color: #e6edf3; }
.term-body { flex: 1; position: relative; padding: 8px 10px; }
.term-el { height: 100%; }
.term-state {
  position: absolute; bottom: 14px; left: 50%; transform: translateX(-50%);
  background: rgba(255,255,255,.9); color: #111; font-size: 12px;
  padding: 5px 12px; border-radius: 6px;
}
.reconnect { border: 0; background: transparent; color: #2563eb; cursor: pointer; margin-left: 8px; }
.files-body { flex: 1; overflow: hidden; display: flex; flex-direction: column; }
</style>
