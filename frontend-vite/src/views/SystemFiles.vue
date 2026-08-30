<template>
  <div class="view files-view">
    <div class="view-head">
      <div><h1 class="view-title">文件管理</h1><p class="view-sub">{{ currentHost?.name || '未选择主机' }} · 本机文件或远程 SFTP</p></div>
      <div class="actions"><span v-if="meta.duration_ms!==undefined" class="muted">{{ meta.source }} · {{ Number(meta.duration_ms).toFixed(0) }}ms</span><button class="btn" :disabled="loading" @click="loadFiles">↻ 刷新</button></div>
    </div>

    <section class="card browser">
      <div class="toolbar">
        <button class="btn btn-sm" :disabled="path==='/'" @click="goParent">↑ 上级</button>
        <input v-model="pathInput" class="path-input mono" @keyup.enter="navigate(pathInput)" />
        <button class="btn btn-sm" @click="navigate(pathInput)">前往</button>
        <button class="btn btn-sm" @click="createDirectory">新建目录</button>
        <button class="btn btn-sm" @click="uploadInput?.click()">上传</button>
        <button class="btn btn-sm" @click="openTrash">回收站</button>
        <input ref="uploadInput" class="hidden" type="file" @change="uploadFile" />
        <label class="hidden-toggle"><input v-model="showHidden" type="checkbox" @change="loadFiles" /> 显示隐藏文件</label>
        <input v-model="search" class="search" placeholder="筛选当前目录" />
      </div>

      <div v-if="error" class="notice error">{{ error }}</div>
      <div v-if="loading && !hydrated" class="loading"><span class="spinner"></span>正在读取目录…</div>
      <div v-else class="table-scroll">
        <table class="table file-table">
          <thead><tr><th>名称</th><th>权限</th><th>大小</th><th>修改时间</th><th class="operation">操作</th></tr></thead>
          <tbody>
            <tr v-for="item in filteredItems" :key="item.path" @dblclick="openItem(item)">
              <td><button class="name-link" @click="openItem(item)"><span>{{ item.type==='directory'?'📁':item.type==='symlink'?'🔗':'📄' }}</span>{{ item.name }}</button></td>
              <td class="mono muted">{{ item.mode }}</td><td>{{ item.type==='directory'?'—':fmtBytes(item.size) }}</td><td>{{ fmtDate(item.mtime) }}</td>
              <td class="row-actions"><button v-if="item.type==='file'" class="link-btn" @click.stop="openEditor(item)">编辑</button><button v-if="item.type==='file'" class="link-btn" @click.stop="downloadFile(item)">下载</button><button class="link-btn" @click.stop="renameItem(item)">改名</button><button class="link-btn danger" @click.stop="trashItem(item)">删除</button></td>
            </tr>
            <tr v-if="!filteredItems.length"><td colspan="5"><EmptyState icon="📂" text="当前目录为空" style="padding:32px" /></td></tr>
          </tbody>
        </table>
      </div>
      <div class="statusbar"><span>{{ meta.total || 0 }} 项<span v-if="meta.truncated">（仅显示前 2000 项）</span></span><span class="muted">删除操作会移入回收站，可从当前界面恢复</span></div>
    </section>

    <Modal :visible="!!editor.path" :title="`编辑 · ${editor.name}`" width="920px" @close="closeEditor">
      <div v-if="editor.loading" class="loading"><span class="spinner"></span>正在读取文件…</div>
      <template v-else><textarea v-model="editor.content" class="editor mono" spellcheck="false"></textarea><div class="editor-foot"><span class="muted">UTF-8 文本 · 最大 1MB · 保存时检查并发修改</span><div><button class="btn" @click="closeEditor">取消</button><button class="btn btn-primary" :disabled="editor.saving" @click="saveFile">{{ editor.saving?'保存中…':'保存' }}</button></div></div></template>
    </Modal>

    <Modal :visible="trashOpen" title="文件回收站" width="900px" @close="trashOpen=false">
      <div v-if="trashLoading" class="loading"><span class="spinner"></span>正在读取回收站…</div>
      <div v-else class="trash-scroll"><table class="table"><thead><tr><th>名称</th><th>原路径</th><th>删除时间</th><th>大小</th><th>操作</th></tr></thead><tbody>
        <tr v-for="item in trashItems" :key="item.trash_name"><td><span>{{ item.type==='directory'?'📁':'📄' }}</span> {{ item.name }}</td><td class="mono original-path">{{ item.original_path || '旧条目未记录' }}</td><td>{{ fmtDate(item.deleted_at) }}</td><td>{{ item.type==='directory'?'—':fmtBytes(item.size) }}</td><td class="row-actions"><button class="link-btn" @click="restoreItem(item)">恢复</button><button class="link-btn danger" @click="purgeItem(item)">彻底删除</button></td></tr>
        <tr v-if="!trashItems.length"><td colspan="5"><EmptyState icon="🗑" text="回收站为空" style="padding:32px" /></td></tr>
      </tbody></table></div>
      <div class="trash-foot"><span class="muted">彻底删除不可恢复</span><button class="btn" @click="loadTrash">↻ 刷新</button></div>
    </Modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { api, fmtBytes, toast } from '../api'
import EmptyState from '../components/EmptyState.vue'
import Modal from '../components/Modal.vue'
import { useHostContext } from '../hostContext'

const { selectedHostId,currentHost,refreshHosts }=useHostContext()
const path=ref('/'),pathInput=ref('/'),items=ref([]),loading=ref(false),hydrated=ref(false),error=ref(''),search=ref(''),showHidden=ref(false),uploadInput=ref(null)
const meta=reactive({total:0,source:'',duration_ms:undefined,truncated:false,parent:'/'})
const editor=reactive({path:'',name:'',content:'',mtime:null,loading:false,saving:false})
const trashOpen=ref(false),trashLoading=ref(false),trashItems=ref([])
let controller=null
const filteredItems=computed(()=>{const needle=search.value.trim().toLowerCase();return needle?items.value.filter(item=>item.name.toLowerCase().includes(needle)):items.value})
function fmtDate(value){return value?new Date(Number(value)*1000).toLocaleString('zh-CN',{hour12:false}):'—'}
function joinPath(parent,name){return parent==='/'?`/${name}`:`${parent.replace(/\/$/,'')}/${name}`}
function baseName(value){return value.split('/').filter(Boolean).pop()||'/'}
async function loadFiles(){if(!selectedHostId.value)return;const hostId=selectedHostId.value;controller?.abort();controller=new AbortController();loading.value=true;error.value='';try{const data=await api.get(`/servers/${hostId}/files`,{path:path.value,show_hidden:showHidden.value},{signal:controller.signal});if(hostId!==selectedHostId.value)return;path.value=data.path;pathInput.value=data.path;items.value=data.items||[];Object.assign(meta,data);hydrated.value=true}catch(e){if(e.name!=='AbortError')error.value=e.message}finally{if(hostId===selectedHostId.value)loading.value=false}}
async function navigate(target){path.value=target||'/';await loadFiles()}
function goParent(){navigate(meta.parent||'/')}
function openItem(item){if(item.type==='directory')navigate(item.path);else if(item.type==='file')openEditor(item)}
async function createDirectory(){const name=window.prompt('请输入目录名称');if(!name)return;try{await api.post(`/servers/${selectedHostId.value}/files/directories`,{parent:path.value,name});toast('目录已创建','ok');await loadFiles()}catch(e){toast(`创建失败：${e.message}`,'err')}}
function bytesToBase64(buffer){const bytes=new Uint8Array(buffer);let binary='';const chunk=0x8000;for(let i=0;i<bytes.length;i+=chunk)binary+=String.fromCharCode(...bytes.subarray(i,i+chunk));return btoa(binary)}
async function uploadFile(event){const file=event.target.files?.[0];event.target.value='';if(!file)return;if(file.size>10*1024*1024){toast('单文件上传最大支持 10MB','err');return}try{await api.post(`/servers/${selectedHostId.value}/files/upload`,{parent:path.value,name:file.name,content_base64:bytesToBase64(await file.arrayBuffer())});toast('文件上传完成','ok');await loadFiles()}catch(e){toast(`上传失败：${e.message}`,'err')}}
async function downloadFile(item){try{const data=await api.get(`/servers/${selectedHostId.value}/files/download`,{path:item.path});const binary=atob(data.content_base64);const bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i++)bytes[i]=binary.charCodeAt(i);const url=URL.createObjectURL(new Blob([bytes]));const link=document.createElement('a');link.href=url;link.download=data.name||item.name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(e){toast(`下载失败：${e.message}`,'err')}}
async function renameItem(item){const name=window.prompt('请输入新名称',item.name);if(!name||name===item.name)return;try{await api.post(`/servers/${selectedHostId.value}/files/move`,{source:item.path,target:joinPath(path.value,name)});toast('改名完成','ok');await loadFiles()}catch(e){toast(`改名失败：${e.message}`,'err')}}
async function trashItem(item){const confirmName=window.prompt(`将“${item.name}”移入回收站。请输入名称确认：`);if(confirmName!==item.name)return;try{await api.post(`/servers/${selectedHostId.value}/files/trash`,{path:item.path,confirm_name:confirmName});toast('已移入回收站','ok');await loadFiles()}catch(e){toast(`删除失败：${e.message}`,'err')}}
async function openTrash(){trashOpen.value=true;await loadTrash()}
async function loadTrash(){if(!selectedHostId.value)return;trashLoading.value=true;try{const data=await api.get(`/servers/${selectedHostId.value}/files/trash`);trashItems.value=data.items||[]}catch(e){toast(`回收站读取失败：${e.message}`,'err')}finally{trashLoading.value=false}}
async function restoreItem(item){let target=item.original_path||window.prompt('该旧条目未记录原路径，请输入完整恢复路径：',joinPath(path.value,item.name));if(!target)return;try{await api.post(`/servers/${selectedHostId.value}/files/trash/restore`,{trash_name:item.trash_name,target});toast('文件已恢复','ok');await loadTrash();await loadFiles()}catch(e){if(e.status===409){target=window.prompt('原路径已有同名文件，请输入新的完整恢复路径：',target);if(target)try{await api.post(`/servers/${selectedHostId.value}/files/trash/restore`,{trash_name:item.trash_name,target});toast('文件已恢复','ok');await loadTrash();await loadFiles()}catch(retry){toast(`恢复失败：${retry.message}`,'err')}}else toast(`恢复失败：${e.message}`,'err')}}
async function purgeItem(item){const name=window.prompt(`彻底删除“${item.name}”后无法恢复。请输入文件名确认：`);if(name!==item.name)return;try{await api.post(`/servers/${selectedHostId.value}/files/trash/purge`,{trash_name:item.trash_name,confirm_name:name});toast('已彻底删除','ok');await loadTrash()}catch(e){toast(`彻底删除失败：${e.message}`,'err')}}
async function openEditor(item){Object.assign(editor,{path:item.path,name:item.name,content:'',mtime:null,loading:true,saving:false});try{const data=await api.get(`/servers/${selectedHostId.value}/files/content`,{path:item.path});editor.content=data.content;editor.mtime=data.mtime}catch(e){toast(`读取失败：${e.message}`,'err');closeEditor()}finally{editor.loading=false}}
function closeEditor(){Object.assign(editor,{path:'',name:'',content:'',mtime:null,loading:false,saving:false})}
async function saveFile(){editor.saving=true;try{const data=await api.put(`/servers/${selectedHostId.value}/files/content`,{content:editor.content,expected_mtime:editor.mtime},{query:{path:editor.path}});editor.mtime=data.mtime;toast('文件已保存','ok');closeEditor();await loadFiles()}catch(e){toast(`保存失败：${e.message}`,'err')}finally{editor.saving=false}}
watch(selectedHostId,()=>{controller?.abort();path.value='/';pathInput.value='/';items.value=[];trashItems.value=[];trashOpen.value=false;hydrated.value=false;loadFiles()})
onMounted(async()=>{await refreshHosts();loadFiles()})
</script>

<style scoped>
.files-view{max-width:1680px;margin:0 auto}.actions{display:flex;align-items:center;gap:10px}.browser{overflow:hidden}.toolbar{display:flex;align-items:center;gap:8px;padding:13px;border-bottom:1px solid var(--border);flex-wrap:wrap}.path-input{flex:1;min-width:280px;border:1px solid var(--border);border-radius:6px;padding:8px 10px;background:var(--card);color:var(--text)}.search{width:190px;border:1px solid var(--border);border-radius:6px;padding:8px 10px}.hidden{display:none}.hidden-toggle{font-size:12px;color:var(--muted);white-space:nowrap}.notice{padding:10px 14px}.error{background:#fef2f2;color:var(--err)}.table-scroll{overflow:auto;min-height:420px}.file-table{min-width:850px}.operation{width:220px}.name-link,.link-btn{border:0;background:none;color:var(--primary);cursor:pointer}.name-link{display:flex;align-items:center;gap:8px;font-weight:600}.row-actions{white-space:nowrap}.link-btn.danger{color:var(--err)}.statusbar{display:flex;justify-content:space-between;gap:16px;padding:9px 14px;border-top:1px solid var(--border);font-size:12px}.editor{width:100%;height:58vh;resize:vertical;border:1px solid var(--border);border-radius:7px;padding:12px;background:#0d1117;color:#e6edf3;font-size:13px;line-height:1.55}.editor-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px}.editor-foot>div{display:flex;gap:8px}.trash-scroll{max-height:60vh;overflow:auto}.original-path{max-width:360px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.trash-foot{display:flex;align-items:center;justify-content:space-between;margin-top:14px}
</style>
