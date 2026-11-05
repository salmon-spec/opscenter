<template>
  <section class="boards-manager">
    <header class="manager-head"><div><h1>编辑监控大屏</h1><p>选择样式与模块，调整布局，预览后发布。</p></div><router-link :to="route.path">返回监控大屏 ↗</router-link></header>
    <div v-if="error" class="notice error" role="alert">{{ error }} <button @click="reload">重新加载</button></div>
    <div v-if="message" class="notice" role="status">{{ message }}</div>
    <nav class="manager-tabs" aria-label="大屏编辑页签"><button v-for="t in tabs" :key="t.key" :class="{active:tab===t.key}" @click="tab=t.key">{{ t.label }}</button></nav>
    <template v-if="!draft">
      <div class="list-toolbar"><input v-model="search" aria-label="搜索大屏" placeholder="搜索大屏名称" /><button :disabled="busy||!builtin" @click="create()">＋ 新建大屏</button><button :disabled="busy||!defaultId" @click="resetDefault">使用系统默认</button></div>
      <p v-if="loading">正在读取大屏…</p>
      <div v-else class="board-cards">
        <article v-for="preset in tab==='templates'&&builtin?boardTemplates:[]" :key="preset.key" class="board-card preset-card" :style="themeVariables({...themes[preset.theme],font_size:13,radius:12})"><div class="preset-preview"><i v-for="n in 6" :key="n"></i></div><span class="badge">场景模板</span><h2>{{ preset.name }}</h2><p>{{ preset.description }}</p><button :disabled="busy" @click="createPreset(preset)">使用此样式</button></article>
        <article v-if="tab==='templates'&&builtin" class="board-card builtin"><span class="badge">系统模板</span><h2>{{ builtin.name }}</h2><p>状态总览、主机资源、K3s、告警和历史图表。</p><button :disabled="busy" @click="create(builtin)">使用模板</button></article>
        <article v-for="item in visibleBoards" :key="item.id" class="board-card">
          <div class="card-badges"><span v-if="item.id===defaultId" class="badge">当前默认</span><span class="badge quiet">{{ item.published_version ? `已发布 v${item.published_version}` : '草稿' }}</span><span v-if="item.is_template" class="badge quiet">模板</span></div>
          <h2>{{ item.name }}</h2><p>{{ item.config.widgets.length }} 个组件 · {{ formatTime(item.updated_at) }}</p>
          <div class="card-actions"><button :disabled="busy" @click="edit(item.id)">编辑</button><button :disabled="busy" @click="previewPublished(item)">预览</button><button :disabled="busy" @click="create(item)">复制</button><button :disabled="busy||!item.published_version||item.id===defaultId" @click="makeDefault(item)">设为默认</button><button :disabled="busy" @click="showHistory(item)">版本</button><button :disabled="busy||item.id===defaultId" @click="removeBoard(item)">删除</button></div>
        </article>
      </div>
      <p v-if="!loading&&!visibleBoards.length" class="empty">{{ tab==='templates'?'将大屏保存为模板后，可在这里重复使用。':'尚无自定义大屏，点击“新建大屏”开始。' }}</p>
    </template>
    <template v-else>
      <div class="editor-toolbar"><button :disabled="busy" @click="closeEditor">← 返回列表</button><input v-model="draft.name" :disabled="busy" maxlength="80" aria-label="大屏名称" /><span class="save-state">{{ dirty?'有未保存修改':'草稿已保存' }} · r{{ draft.revision }}</span><button :disabled="busy" @click="save">保存草稿</button><button :disabled="busy" @click="previewDraft">预览草稿</button><button class="primary" :disabled="busy" @click="publish">发布</button></div>
      <div class="editor-layout" :inert="busy || !!preview || !!history">
        <aside class="component-library"><h2>组件库 · 全模块</h2><button v-for="(label,type) in widgetLabels" :key="type" :disabled="draft.config.widgets.length>=30" @click="addWidget(type)">＋ {{ label }}</button><p>点击添加，拖动组件顶部调整位置，拖动右下角调整尺寸。最多30个组件。</p><h2>主题</h2><button v-for="(theme,key) in themes" :key="key" class="theme-choice" :style="{'--swatch':theme.accent,'--swatch-bg':theme.background}" @click="applyTheme(theme)"><i></i>{{ theme.name }}</button></aside>
        <div class="editor-canvas"><ScreenCanvas :key="canvasKey" :config="draft.config" :suspended="!!preview" editable :selected-id="selectedId" @select="selectedId=$event" @remove="removeWidget" @layout="updateLayout" /><p v-if="!draft.config.widgets.length" class="empty">从左侧添加第一个组件</p></div>
        <aside class="property-panel"><h2>大屏设置</h2><label>大屏标题<input v-model="draft.config.title" maxlength="80" /></label><label class="checkbox"><input v-model="draft.is_template" type="checkbox" />保存为可复用模板</label><label v-for="field in colorFields" :key="field.key">{{ field.label }}<input v-model="draft.config.theme[field.key]" type="color" /></label><label>面板风格<select v-model="draft.config.theme.style" aria-label="面板风格"><option v-for="(label,key) in panelStyles" :key="key" :value="key">{{ label }}</option></select></label><label>字号<input v-model.number="draft.config.theme.font_size" type="number" min="12" max="20" /></label><label>面板圆角<input v-model.number="draft.config.theme.radius" type="number" min="0" max="24" /></label>
          <template v-if="selected"><h2>组件属性</h2><label>展示样式<select v-model="selected.variant" aria-label="展示样式"><option v-for="(label,key) in widgetVariants" :key="key" :value="key">{{ label }}</option></select></label><label>组件强调色<input :value="selected.accent||draft.config.theme.accent" type="color" @input="selected.accent=$event.target.value" /></label><button v-if="selected.accent" @click="selected.accent=null">跟随主题颜色</button><label>标题<input v-model="selected.title" maxlength="80" /></label><label v-if="selected.type==='text'">内容<textarea v-model="selected.text" maxlength="4000" rows="5" /></label><p class="hint">支持数值精确定位，位置和尺寸以12列网格计。</p><label v-for="field in geometryFields" :key="field.key">{{ field.label }}<input :value="selected[field.key]" type="number" :min="field.min" :max="field.max" @change="positionWidget(field.key,$event.target.value)" /></label><button @click="duplicateWidget">复制组件</button><button @click="removeWidget(selected.id)">移除组件</button></template>
        </aside>
      </div>
    </template>
    <div v-if="preview" class="preview-overlay" role="dialog" aria-modal="true" aria-label="大屏预览" tabindex="-1" @keydown="dialogKeys"><header><strong>{{ preview.label }}</strong><button @click="preview=null">关闭预览</button></header><ScreenCanvas :config="preview.config" /></div>
    <div v-if="history" class="modal-backdrop" @click.self="history=null"><section class="history-dialog" role="dialog" aria-modal="true" aria-label="发布记录" tabindex="-1" @keydown="dialogKeys"><header><h2>{{ history.board.name }} · 发布记录</h2><button @click="history=null">关闭</button></header><p>恢复历史版本会生成草稿，预览并发布后生效。</p><p v-if="!history.items.length">暂无发布版本</p><article v-for="version in history.items" :key="version.version"><div><strong>v{{ version.version }} · {{ version.name }}</strong><small>{{ formatTime(version.created_at) }}</small></div><button :disabled="busy" @click="previewVersion(history.board.id,version.version)">预览</button><button :disabled="busy" @click="restore(history.board.id,version.version)">恢复到草稿</button></article></section></div>
  </section>
</template>

<script setup>
import {computed,onMounted,onBeforeUnmount,ref,watch,nextTick} from 'vue'
import {onBeforeRouteLeave,onBeforeRouteUpdate,useRoute} from 'vue-router'
const route=useRoute()
import {api} from '../api'
import ScreenCanvas from './ScreenCanvas.vue'
import {widgetLabels,themes,clone,newWidget,applyLayout,panelStyles,widgetVariants,boardTemplates,templateConfig,themeVariables} from '../utils/screenBoards.js'
const tabs=[{key:'boards',label:'我的大屏'},{key:'templates',label:'模板与主题'}]
const colorFields=[{key:'background',label:'背景色'},{key:'surface',label:'面板色'},{key:'text',label:'文字色'},{key:'accent',label:'强调色'}]
const geometryFields=[{key:'x',label:'横向位置',min:0,max:10},{key:'y',label:'纵向位置',min:0,max:500},{key:'w',label:'宽度',min:2,max:12},{key:'h',label:'高度',min:2,max:30}]
const boards=ref([]),builtin=ref(null),defaultId=ref(null),loading=ref(true),busy=ref(false),error=ref(''),message=ref(''),tab=ref(route.query.panel==='templates'?'templates':'boards'),search=ref('')
const draft=ref(null),baseline=ref(''),selectedId=ref(''),canvasKey=ref(0),preview=ref(null),history=ref(null)
const snapshot=()=>draft.value?JSON.stringify({name:draft.value.name,config:draft.value.config,is_template:draft.value.is_template}):''
const dirty=computed(()=>!!draft.value&&snapshot()!==baseline.value)
const selected=computed(()=>draft.value?.config.widgets.find(w=>w.id===selectedId.value))
const visibleBoards=computed(()=>boards.value.filter(b=>(tab.value!=='templates'||b.is_template)&&b.name.toLowerCase().includes(search.value.toLowerCase())))
function formatTime(value){return value?new Date(value).toLocaleString('zh-CN',{hour12:false}):'—'}
function canLeave(){return !dirty.value||window.confirm('有未保存的修改，确定放弃这些修改？')}
async function run(fn){if(busy.value)return;busy.value=true;error.value='';message.value='';try{await fn()}catch(e){error.value=e.message}finally{busy.value=false}}
async function load(){loading.value=true;try{const data=await api.get('/screen-boards');boards.value=data.items;builtin.value=data.builtin;defaultId.value=data.default_id}finally{loading.value=false}}
async function reload(){if(!canLeave())return;await run(async()=>{if(draft.value)await openDraft(draft.value.id);await load()})}
function acceptDraft(data){draft.value=clone(data);draft.value.config.theme.style??='classic';for(const w of draft.value.config.widgets){w.variant??='detail';w.accent??=null}baseline.value=snapshot();selectedId.value='';canvasKey.value++}
async function openDraft(id){acceptDraft(await api.get(`/screen-boards/${id}`))}
async function edit(id){if(!canLeave())return;await run(()=>openDraft(id))}
async function create(source){if(!canLeave())return;await run(async()=>{const config=clone(source?.config||builtin.value.config);const name=source?`${source.name.slice(0,70)} 副本`:'新建大屏';const data=await api.post('/screen-boards',{name,config,is_template:false});acceptDraft(data);tab.value='boards';await load()})}
async function createPreset(preset){await create({name:preset.name,config:templateConfig(preset,builtin.value.config)})}
async function saveDraft(){
  if(!draft.value.name.trim()||!draft.value.config.title.trim())throw new Error('请填写大屏名称和标题')
  const d=draft.value;const data=await api.put(`/screen-boards/${d.id}`,{name:d.name,config:d.config,is_template:d.is_template,revision:d.revision})
  draft.value.revision=data.revision;draft.value.updated_at=data.updated_at;baseline.value=snapshot();return data
}
async function save(){await run(async()=>{await saveDraft();message.value='草稿已保存，已发布大屏继续使用原版本。';await load()})}
async function publish(){await run(async()=>{if(dirty.value)await saveDraft();const d=draft.value;const data=await api.post(`/screen-boards/${d.id}/publish`,{revision:d.revision});acceptDraft(data);message.value=`已发布 v${data.published_version}。${defaultId.value===d.id?'当前默认大屏已更新。':'可返回列表设为默认大屏。'}`;await load()})}
async function makeDefault(item){await run(async()=>{await api.post('/screen-boards/default',{board_id:item.id});message.value='默认大屏已切换。';await load()})}
async function resetDefault(){await run(async()=>{await api.post('/screen-boards/default',{board_id:null});message.value='已切换为系统默认大屏。';await load()})}
function closeEditor(){if(canLeave()){draft.value=null;baseline.value='';message.value=''}}
function addWidget(type){const widget=newWidget(type,draft.value.config.widgets);if(widget.y+widget.h>500){error.value='画布高度已达到上限';return}draft.value.config.widgets.push(widget);selectedId.value=widget.id}
function removeWidget(id){draft.value.config.widgets=draft.value.config.widgets.filter(w=>w.id!==id);if(selectedId.value===id)selectedId.value=''}
function duplicateWidget(){if(draft.value.config.widgets.length>=30)return;const w={...clone(selected.value),id:newWidget(selected.value.type,[]).id,y:Math.max(0,...draft.value.config.widgets.map(w=>w.y+w.h))};draft.value.config.widgets.push(w);selectedId.value=w.id}
function updateLayout(layout){draft.value.config.widgets=applyLayout(draft.value.config.widgets,layout)}
function positionWidget(key,value){const number=Number(value),field=geometryFields.find(f=>f.key===key);if(!Number.isInteger(number)||number<field.min||number>field.max)return;const w=selected.value;w[key]=number;if(w.x+w.w>12)w.x=12-w.w}
function applyTheme(theme){const {name,...colors}=theme;Object.assign(draft.value.config.theme,colors)}
function previewDraft(){preview.value={label:'草稿预览 · 尚未发布',config:clone(draft.value.config)}}
async function previewVersion(id,version){await run(async()=>{const data=await api.get(`/screen-boards/${id}/versions/${version}`);preview.value={label:`发布版本 v${version}`,config:data.config}})}
async function previewPublished(item){if(item.published_version)await previewVersion(item.id,item.published_version);else preview.value={label:'草稿预览 · 尚未发布',config:clone(item.config)}}
async function showHistory(item){await run(async()=>{const data=await api.get(`/screen-boards/${item.id}/versions`);history.value={board:item,items:data.items}})}
async function restore(id,version){if(!canLeave()||!window.confirm(`将 v${version} 恢复到草稿？当前已发布版本会继续展示。`))return;await run(async()=>{const current=await api.get(`/screen-boards/${id}`);const data=await api.post(`/screen-boards/${id}/versions/${version}/restore`,{revision:current.revision});acceptDraft(data);history.value=null;message.value=`已将 v${version} 恢复到草稿，请预览后发布。`;await load()})}
async function removeBoard(item){if(!window.confirm(`确定删除“${item.name}”及其发布记录？`))return;await run(async()=>{await api.del(`/screen-boards/${item.id}`,{query:{revision:item.revision}});await load()})}
function dialogKeys(e){
  if(e.key==='Escape'){if(preview.value)preview.value=null;else history.value=null;e.preventDefault();return}
  if(e.key!=='Tab')return
  const elements=[...e.currentTarget.querySelectorAll('button:not(:disabled),a[href],input,select,textarea')].filter(el=>el.getClientRects().length)
  const first=elements[0],last=elements.at(-1)
  if(e.shiftKey&&document.activeElement===first){last?.focus();e.preventDefault()}
  else if(!e.shiftKey&&document.activeElement===last){first?.focus();e.preventDefault()}
}
let returnFocus=null
watch(()=>!!preview.value||!!history.value,async open=>{
  if(open){returnFocus=document.activeElement;await nextTick();document.querySelector('.preview-overlay button,.history-dialog button')?.focus()}
  else returnFocus?.focus()
})
function unload(e){if(dirty.value){e.preventDefault();e.returnValue=''}}
onBeforeRouteUpdate((to,from)=>to.query.edit!==from.query.edit?canLeave():true)
watch(()=>route.query.panel,value=>{if(!draft.value)tab.value=value==='templates'?'templates':'boards'})
onBeforeRouteLeave(()=>canLeave())
onMounted(()=>{run(load);window.addEventListener('beforeunload',unload)})
onBeforeUnmount(()=>window.removeEventListener('beforeunload',unload))
</script>

<style scoped>
.boards-manager{padding:24px;background:var(--bg,#f3f5f9);color:var(--text,#202b3c);min-height:100%}.manager-head,.list-toolbar,.editor-toolbar,.card-actions,.card-badges,.history-dialog header,.history-dialog article{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.manager-head{justify-content:space-between;margin-bottom:20px}.manager-head h1{font-size:24px;margin:0}.manager-head p,.hint,.component-library p,.board-card p{color:var(--muted,#61728a);font-size:12px;line-height:1.7}.boards-manager a{color:#53b8df}.boards-manager button{padding:8px 12px;border:1px solid #42516a;background:#1c2a3d;color:#e4edf7;border-radius:6px;cursor:pointer;font-size:12px}.boards-manager button:hover{background:#2b4059}.boards-manager button:disabled{opacity:.45;cursor:default}.boards-manager input,.boards-manager textarea{background:#101c2d;color:#e4edf7;border:1px solid #42516a;border-radius:5px;padding:8px;min-width:0;max-width:100%}.boards-manager input[type=color]{width:100%;height:36px;padding:3px}.manager-tabs{display:flex;gap:8px;border-bottom:1px solid #34435a;padding-bottom:12px;margin-bottom:18px}.manager-tabs .active,.boards-manager .primary{background:#176f85;border-color:#54bad2}.list-toolbar{margin-bottom:20px}.board-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:16px}.board-card{color:#e4edf7;border:1px solid #34435a;background:#172334;border-radius:10px;padding:20px}.board-card h2{font-size:17px;margin:16px 0 8px}.badge{font-size:11px;background:#155764;color:#b0f4eb;padding:4px 7px;border-radius:4px}.badge.quiet{background:#2c3b50;color:#c5d0df}.card-actions{gap:6px;margin-top:18px}.card-actions button{padding:6px 9px}.notice{padding:12px 16px;margin-bottom:12px;border:1px solid #2b8a76;background:#103c37;border-radius:6px;color:#d0f7e8}.notice.error{border-color:#ac6849;background:#422b26;color:#ffcfb8}.notice button{margin-left:12px}.editor-toolbar{position:sticky;top:0;z-index:5;background:#162234;padding:12px;border:1px solid #34435a;border-radius:8px;margin-bottom:12px}.save-state{font-size:12px;color:#aebdd1;margin-right:auto}.editor-layout{display:grid;grid-template-columns:145px minmax(0,1fr) 190px;gap:12px;align-items:start}.component-library,.property-panel{position:sticky;top:90px;background:#172334;border:1px solid #34435a;border-radius:8px;padding:12px;color:#dce3ed}.component-library h2,.property-panel h2{font-size:14px;margin:8px 0 14px}.component-library button{display:block;width:100%;text-align:left;margin-bottom:8px}.property-panel label{display:grid;gap:6px;font-size:12px;margin-bottom:12px}.property-panel .checkbox{display:flex;align-items:center}.property-panel button{margin:4px 0}.editor-canvas{min-width:0;overflow:auto;border:1px solid #34435a;border-radius:8px}.editor-canvas :deep(.monitor-screen){padding:12px}.editor-canvas :deep(.board-head){flex-wrap:wrap}.editor-canvas :deep(.head-actions){flex-wrap:wrap}.empty{padding:32px;text-align:center;color:#99aabd}.preview-overlay{position:fixed;inset:0;z-index:1200;background:#111217;overflow:auto}.preview-overlay>header{position:sticky;top:0;background:#152338;color:#e4edf7;z-index:10;padding:12px 24px;display:flex;justify-content:space-between;align-items:center}.modal-backdrop{position:fixed;inset:0;z-index:1100;background:#0009;display:grid;place-items:center;padding:24px}.history-dialog{background:#172334;color:#dce3ed;border:1px solid #42516a;padding:24px;border-radius:10px;width:min(700px,100%);max-height:85vh;overflow:auto}.history-dialog header{justify-content:space-between}.history-dialog h2{font-size:18px}.history-dialog article{padding:14px 0;border-bottom:1px solid #34435a}.history-dialog article>div{margin-right:auto}.history-dialog small{display:block;color:#99aabd;margin-top:4px}@media(max-width:1100px){.editor-layout{grid-template-columns:125px minmax(0,1fr)}.property-panel{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:14px}.property-panel label{width:160px}.property-panel h2{width:100%}}@media(max-width:700px){.boards-manager{padding:12px}.editor-layout{grid-template-columns:1fr}.component-library{display:flex;flex-wrap:wrap;gap:6px}.component-library h2,.component-library p{width:100%}.component-library button{width:auto}.editor-toolbar input{width:100%}}

.component-library{max-height:calc(100vh - 180px);overflow:auto;position:sticky;top:12px}.theme-choice{display:flex;gap:8px;align-items:center}.theme-choice i{width:20px;height:20px;border-radius:6px;background:var(--swatch-bg);border:4px solid var(--swatch)}.property-panel select{width:100%;padding:8px;background:#101c2d;color:#edf3ff;border:1px solid #45536a;border-radius:4px}.preset-card{background:var(--surface)!important;color:var(--text)!important;border-color:var(--accent)}.preset-card h2{color:var(--text)}.preset-preview{background:var(--bg);padding:16px;display:grid;grid-template-columns:repeat(3,1fr);gap:7px;border-radius:10px;margin-bottom:14px}.preset-preview i{height:30px;background:linear-gradient(130deg,var(--green),var(--surface));border-radius:5px}.preset-preview i:nth-child(2n){opacity:.5}.preset-preview i:first-child{grid-column:span 2}
@media(max-width:1100px){.component-library{position:static;max-height:340px}}
</style>
