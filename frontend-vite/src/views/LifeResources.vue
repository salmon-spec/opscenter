<template>
  <section class="resource-page" :aria-label="spec.title">
    <div class="resource-toolbar">
      <h3>{{ spec.title }}</h3>
      <select v-if="spec.scope" v-model="scope" class="input" :aria-label="spec.scopeLabel"><option value="">{{ spec.scopeLabel }}</option><option v-for="r in scopes" :key="r.id" :value="String(r.id)">{{ r.title || r.name }} · {{ r.id }}</option></select>
      <input v-model="search" class="input search" aria-label="搜索管理记录" placeholder="搜索">
      <button class="btn" :disabled="busy || loading" @click="load">刷新</button>
      <button v-if="!spec.readonly && !spec.singleton && spec.create !== false" class="btn btn-primary" :disabled="busy || Boolean(spec.scope && !scope)" @click="open()">新建{{ spec.title }}</button>
    </div>
    <p v-if="error" class="notice error" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在读取…</p>
    <p v-else-if="spec.scope && !scope" class="empty">{{ spec.scopeLabel }}后管理{{ spec.title }}。</p>
    <p v-else-if="!filtered.length" class="empty">暂无记录。</p>
    <div v-else class="table-wrap"><table><thead><tr><th v-for="f in columns" :key="f.key">{{ f.label }}</th><th v-if="!spec.readonly">操作</th></tr></thead>
      <tbody><tr v-for="row in filtered" :key="row.id"><td v-for="f in columns" :key="f.key">{{ display(row[f.key], f) }}</td><td v-if="!spec.readonly" class="row-actions">
        <button v-if="spec.edit !== false" class="btn btn-sm" :disabled="busy" @click="open(row)">编辑</button>
        <button v-if="!spec.singleton" class="btn btn-sm" :disabled="busy" @click="deleting = row">删除</button>
      </td></tr></tbody></table></div>
    <div v-if="formOpen" class="modal-backdrop" @click.self="close">
      <section class="resource-modal" role="dialog" aria-modal="true" :aria-label="(editing ? '编辑' : '新建') + spec.title">
        <div class="resource-toolbar"><h3>{{ editing ? '编辑' : '新建' }}{{ spec.title }}</h3><button class="btn" :disabled="busy" @click="close">关闭</button></div>
        <form @submit.prevent="save"><div class="form-grid"><label v-for="f in spec.fields" :key="f.key" :class="{wide:f.type==='textarea'}"><span>{{ f.label }}{{ f.required ? ' *' : '' }}</span>
          <textarea v-if="f.type==='textarea'" v-model="form[f.key]" class="input" rows="4" maxlength="4000" :required="f.required"></textarea>
          <select v-else-if="f.type==='reference' || f.type==='select'" v-model="form[f.key]" class="input" :required="f.required" :disabled="Boolean(editing && f.immutable)"><option v-if="!f.required" value="">未设置</option><option v-for="o in options(f)" :key="o[0]" :value="o[0]">{{ o[1] }}</option></select>
          <input v-else-if="f.type==='checkbox'" v-model="form[f.key]" type="checkbox">
          <input v-else v-model="form[f.key]" class="input" :type="f.type" :required="f.required" :min="f.min" :step="f.type==='number'?'any':undefined" :maxlength="255" :pattern="f.pattern">
        </label></div><p v-if="formError" class="notice error" role="alert">{{ formError }}</p><button class="btn btn-primary" :disabled="busy">{{ busy ? '保存中…' : '保存' }}</button></form>
      </section>
    </div>
    <div v-if="deleting" class="modal-backdrop"><section class="resource-modal" role="dialog" aria-modal="true" aria-label="确认删除"><h3>删除{{ spec.title }}</h3><p>确认删除“{{ deleting.name || deleting.title || deleting.comment || deleting.id }}”？此操作不可撤销，关联记录可能受影响。</p><p v-if="formError" class="error">{{ formError }}</p><div class="resource-toolbar"><button class="btn" :disabled="busy" @click="deleting=null; formError=''">取消</button><button class="btn btn-primary" :disabled="busy" @click="remove">确认删除</button></div></section></div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api, toast } from '../api'
import { operationId } from '../utils/life'
const props=defineProps({kind:{type:String,required:true},spec:{type:Object,required:true}})
const base=computed(()=>`/modules/${props.kind}/api/v1`)
const rows=ref([]),scopes=ref([]),scope=ref(''),references=ref({}),search=ref(''),loading=ref(false),busy=ref(false),error=ref('')
const formOpen=ref(false),editing=ref(null),form=ref({}),formError=ref(''),deleting=ref(null)
let operation='',revision=0,writeKey='',deleteKey=''
const columns=computed(()=>props.spec.fields.slice(0,5))
const filtered=computed(()=>rows.value.filter(r=>!search.value || props.spec.fields.some(f=>String(r[f.key]??'').toLowerCase().includes(search.value.toLowerCase()))))
function options(f){return f.options || (references.value[f.source] || []).map(r=>[f.valueKey?r[f.valueKey]:f.numeric?Number(r.id):String(r.id),r.title || r.name || r.username || r.id])}
function display(v,f){if(f.type==='checkbox')return Number(v)?'是':'否';if(f.type==='select' || f.type==='reference')return options(f).find(o=>String(o[0])===String(v))?.[1] || v || '—';return v==null || v==='' || String(v).startsWith('0001-')?'—':String(v)}
async function load(){const n=++revision;rows.value=[];if(props.spec.scope && !scope.value)return;loading.value=true;error.value='';try{const d=await api.get(base.value+'/workspace/'+props.spec.key,{scope:scope.value},{timeoutMs:30000});if(n===revision)rows.value=d.items || []}catch(e){if(n===revision)error.value=e.message}finally{if(n===revision)loading.value=false}}
async function initialize(){scope.value='';search.value='';formOpen.value=false;deleting.value=null;references.value={};scopes.value=[];error.value='';try{if(props.spec.scope){const d=await api.get(base.value+'/'+props.spec.scope);scopes.value=Array.isArray(d)?d:d.items || [];if(scopes.value.length)scope.value=String(scopes.value[0].id)}for(const source of new Set(props.spec.fields.map(f=>f.source).filter(Boolean))){const d=await api.get(base.value+'/workspace/'+source);references.value[source]=d.items || []}await load()}catch(e){error.value=e.message}}
async function open(row){formError.value='';try{const detail=row?.summary?await api.get(base.value+`/workspace/${props.spec.key}/${row.id}`,{scope:scope.value}):null;editing.value=row?{...row,revision:detail?.revision || row.revision}:null;form.value=Object.fromEntries(props.spec.fields.map(f=>{let v=detail?detail.values[f.key]:row?row[f.key]:f.default;if(f.type==='checkbox')v=Boolean(Number(v));if(f.type==='reference' && !v)v='';if(f.type==='datetime-local')v=v && !String(v).startsWith('0001-')?new Date(v).toLocaleString('sv-SE',{timeZone:'Asia/Shanghai'}).replace(' ','T').slice(0,16):'';return [f.key,v??f.default]}));writeKey=operationId();operation=row?'update':'create';formOpen.value=true}catch(e){error.value=e.message}}
function close(){if(!busy.value)formOpen.value=false}
async function save(){busy.value=true;formError.value='';try{const values={...form.value};for(const f of props.spec.fields){if(f.type==='number' || (f.numeric && f.type!=='reference'))values[f.key]=Number(values[f.key]);if(f.type==='reference')values[f.key]=String(values[f.key] || '');if(f.type==='datetime-local')values[f.key]=values[f.key]?new Date(values[f.key]+'+08:00').toISOString():'0001-01-01T00:00:00Z'}await api.post(base.value+'/workspace/'+props.spec.key,{operation,id:editing.value?.id || '',scope:scope.value,expected_revision:editing.value?.revision || '',values},{idempotencyKey:writeKey,timeoutMs:45000});formOpen.value=false;toast('已保存','ok');await load()}catch(e){formError.value=e.message}finally{busy.value=false}}
async function remove(){busy.value=true;formError.value='';try{await api.post(base.value+'/workspace/'+props.spec.key,{operation:'delete',id:deleting.value.id,scope:scope.value,expected_revision:deleting.value.revision},{idempotencyKey:deleteKey,timeoutMs:45000});deleting.value=null;toast('已删除','ok');await load()}catch(e){formError.value=e.message}finally{busy.value=false}}
watch(()=>props.spec.key,initialize)
watch(scope,load)
watch(deleting,()=>{deleteKey=operationId();formError.value=''})
onMounted(initialize)
</script>

<style scoped>
.resource-toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:16px 0}.resource-toolbar h3{margin:0 auto 0 0}.resource-toolbar select{max-width:300px}.search{max-width:240px}.input{padding:9px 11px;border:1px solid var(--border,#dce2eb);border-radius:6px;background:var(--bg-card,#fff);color:inherit;width:100%}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;text-align:left}td,th{padding:12px;border-bottom:1px solid var(--border,#e8ecf2);max-width:300px;overflow-wrap:anywhere}td{white-space:pre-wrap}.row-actions{white-space:nowrap}.row-actions button{margin:2px}.empty{padding:45px;text-align:center;color:var(--text-muted)}.notice{padding:12px;background:#fff3e8}.error{color:#b42318}.modal-backdrop{position:fixed;inset:0;background:#10182866;z-index:1000;display:flex;align-items:center;justify-content:center;padding:20px}.resource-modal{background:var(--bg-card,#fff);color:var(--text,#182230);border-radius:12px;padding:22px;width:min(780px,100%);max-height:90vh;overflow:auto}.form-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:18px 0}.form-grid label{display:flex;flex-direction:column;gap:7px}.wide{grid-column:1/-1}.form-grid input[type=checkbox]{align-self:flex-start}@media(max-width:700px){.form-grid{grid-template-columns:1fr}}
</style>
