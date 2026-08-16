<template>
  <div class="sftp">
    <div class="sftp-toolbar">
      <span class="mono sftp-path">{{ cwd || '/' }}</span>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <button class="btn btn-sm" @click="load()">刷新</button>
        <button class="btn btn-sm" :disabled="isRoot" @click="goUp">上级</button>
        <label class="btn btn-sm btn-primary">
          上传
          <input type="file" multiple style="display:none" @change="onUpload" />
        </label>
        <button class="btn btn-sm" @click="showMkdir = true">新建目录</button>
      </div>
    </div>

    <div v-if="error" class="sftp-error">{{ error }}</div>
    <div v-if="loading" class="loading"><span class="spinner"></span>加载中…</div>
    <table v-else class="table sftp-table">
      <thead>
        <tr><th>名称</th><th>类型</th><th>大小</th><th>修改时间</th><th style="width:130px">操作</th></tr>
      </thead>
      <tbody>
        <tr v-for="item in items" :key="item.path">
          <td>
            <a class="sftp-name" @click="openItem(item)">
              {{ item.is_dir ? '📁' : '📄' }} {{ item.name }}
            </a>
          </td>
          <td>{{ item.is_dir ? '目录' : '文件' }}</td>
          <td class="mono">{{ item.is_dir ? '-' : fmtBytes(item.size) }}</td>
          <td class="muted">{{ fmtTime(item.mtime) }}</td>
          <td>
            <button v-if="!item.is_dir" class="btn btn-sm" @click="download(item)">下载</button>
            <button class="btn btn-sm" @click="startRename(item)">重命名</button>
            <button class="btn btn-sm btn-danger" @click="askDelete(item)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
    <EmptyState v-if="!loading && !items.length && !error" icon="📂" text="目录为空" style="padding:28px" />

    <!-- 新建目录 -->
    <Modal :visible="showMkdir" title="新建目录" width="420px" @close="showMkdir = false">
      <div class="field">
        <label>目录名</label>
        <input v-model="newDirName" class="input" placeholder="如：backup" @keyup.enter="mkdir" />
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button class="btn" @click="showMkdir = false">取消</button>
        <button class="btn btn-primary" @click="mkdir">创建</button>
      </div>
    </Modal>

    <!-- 重命名 -->
    <Modal :visible="!!renameTarget" title="重命名" width="420px" @close="renameTarget = null">
      <div class="field">
        <label>新名称</label>
        <input v-model="newName" class="input" @keyup.enter="rename" />
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button class="btn" @click="renameTarget = null">取消</button>
        <button class="btn btn-primary" @click="rename">确认</button>
      </div>
    </Modal>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { api, fmtBytes, fmtTime, toast } from '../api'
import Modal from './Modal.vue'
import EmptyState from './EmptyState.vue'

const props = defineProps({ sessionId: { type: String, required: true } })

const cwd = ref('.')
const items = ref([])
const loading = ref(false)
const error = ref('')
const showMkdir = ref(false)
const newDirName = ref('')
const renameTarget = ref(null)
const newName = ref('')

const isRoot = () => cwd.value === '.' || cwd.value === '/'

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/terminal/sessions/${props.sessionId}/files`, { path: cwd.value })
    items.value = (data.entries || []).map((e) => ({
      name: e.name,
      is_dir: !!(e.is_dir || e.type === 'dir'),
      size: e.size || 0,
      mtime: e.mtime || null,
      path: join(cwd.value, e.name),
    }))
  } catch (e) {
    error.value = e.message || '目录读取失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

function join(base, name) {
  if (base === '.') return name
  if (base === '/') return '/' + name
  return base + '/' + name
}

function openItem(item) {
  if (!item.is_dir) return
  cwd.value = item.path
  load()
}

function goUp() {
  const parts = cwd.value.split('/').filter(Boolean)
  parts.pop()
  cwd.value = parts.length ? '/' + parts.join('/') : '.'
  load()
}

async function onUpload(e) {
  const files = Array.from(e.target.files || [])
  if (!files.length) return
  for (const f of files) {
    const form = new FormData()
    form.append('file', f)
    try {
      await fetch(`/api/v2/terminal/sessions/${props.sessionId}/files/upload?path=${encodeURIComponent(cwd.value)}`, {
        method: 'POST',
        body: form,
      })
      toast(`已上传 ${f.name}`, 'ok')
    } catch (err) {
      toast(`上传失败 ${f.name}: ${err.message}`, 'err')
    }
  }
  e.target.value = ''
  load()
}

function download(item) {
  window.open(`/api/v2/terminal/sessions/${props.sessionId}/files/download?path=${encodeURIComponent(item.path)}`, '_blank')
}

async function mkdir() {
  const name = newDirName.value.trim()
  if (!name) return
  try {
    await api.post(`/terminal/sessions/${props.sessionId}/files/mkdir`, { path: join(cwd.value, name) })
    toast('目录已创建', 'ok')
    showMkdir.value = false
    newDirName.value = ''
    load()
  } catch (e) {
    toast(e.message, 'err')
  }
}

function startRename(item) {
  renameTarget.value = item
  newName.value = item.name
}

async function rename() {
  if (!renameTarget.value || !newName.value.trim()) return
  try {
    const target = renameTarget.value
    const newPath = join(parentPath(target.path), newName.value.trim())
    await api.post(`/terminal/sessions/${props.sessionId}/files/rename`, {
      old_path: target.path,
      new_path: newPath,
    })
    toast('重命名成功', 'ok')
    renameTarget.value = null
    load()
  } catch (e) {
    toast(e.message, 'err')
  }
}

function parentPath(p) {
  const parts = p.split('/').filter(Boolean)
  parts.pop()
  return parts.length ? '/' + parts.join('/') : '.'
}

function askDelete(item) {
  if (!confirm(`确认删除 ${item.name} ？目录将递归删除。`)) return
  api.post(`/terminal/sessions/${props.sessionId}/files/delete`, { path: item.path })
    .then(() => { toast('已删除', 'ok'); load() })
    .catch((e) => toast(e.message, 'err'))
}

onMounted(load)
</script>

<style scoped>
.sftp { flex: 1; display: flex; flex-direction: column; padding: 12px 16px; color: #e6edf3; }
.sftp-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }
.sftp-path { color: #58a6ff; background: #161b22; padding: 5px 10px; border-radius: 6px; }
.sftp-table th { color: #8b949e; background: #161b22; border-color: #21262d; }
.sftp-table td { border-color: #21262d; }
.sftp-table tr:hover td { background: #161b22; }
.sftp-name { cursor: pointer; color: #e6edf3; }
.sftp-name:hover { color: #58a6ff; text-decoration: underline; }
.sftp-error { color: #f85149; padding: 10px; font-size: 13px; }
</style>
