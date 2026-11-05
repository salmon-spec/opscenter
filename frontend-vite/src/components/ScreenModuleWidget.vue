<template>
  <section class="module-widget" :class="`display-${widget.variant||'detail'}`" :aria-label="definition.name">
    <header><div class="module-heading"><span class="module-icon">{{ definition.icon }}</span><div><small>{{ definition.name }}</small><h2>{{ widget.title||definition.name }}</h2></div></div><router-link :to="definition.link">详情 ↗</router-link></header>
    <p v-if="warning" class="module-warning" role="status">{{ warning }}<span v-if="source?.value && source?.error"> · 保留上次读取</span></p>
    <div v-if="source?.value" class="module-metrics">
      <article v-for="(row,index) in metrics" :key="row.label" :style="{'--metric-color':colors[index%colors.length]}">
        <div v-if="widget.variant==='detail'&&metricPercent(row)!=null" class="metric-ring" :style="{background:`conic-gradient(var(--metric-color) ${metricPercent(row)}%, var(--line) 0)`}"><b>{{ Math.round(metricPercent(row)) }}<small>%</small></b></div>
        <div><span>{{ row.label }}</span><strong>{{ metricText(row.value) }}<small>{{ row.unit }}</small></strong><small v-if="row.total!=null" class="metric-total">共 {{ metricText(row.total) }} {{ row.unit }}</small></div>
      </article>
    </div>
    <div v-else class="module-empty"><span>{{ source?.error?'暂时无法读取':'正在读取…' }}</span><p>配置完成并取得数据后在此展示</p></div>
    <footer><span :class="{attention:!!warning}"><i></i>{{ warning?'需关注':'摘要数据' }}</span><time>{{ source?.at ? `读取 ${new Date(source.at).toLocaleTimeString('zh-CN',{hour12:false})}`:'等待首次读取' }}</time></footer>
  </section>
</template>
<script setup>
import {computed} from 'vue'
import {moduleWidgets,moduleMetrics,moduleWarning,metricText,metricPercent} from '../utils/screenModules.js'
const props=defineProps({widget:{type:Object,required:true},source:Object,now:Number})
const definition=computed(()=>moduleWidgets[props.widget.type])
const metrics=computed(()=>moduleMetrics(props.widget.type,props.source?.value))
const colors=['var(--widget-accent)','var(--blue)','var(--violet)','#df9b58']
const warning=computed(()=>{
  const value=props.source?.value
  if(props.source?.error)return props.source.error
  if(!value)return ''
  const stamp=value.collected_at||value.updated_at||value.health?.checked_at
  const at=typeof stamp==='number'?stamp*1000:Date.parse(stamp)
  if(Number.isFinite(at)&&(props.now-at)/1000>(value.stale_after_seconds??(props.widget.type==='electricity'?86400:300)))return '采集数据已过期'
  if(props.source.at&&props.now-props.source.at>180000)return '读取已暂停 · 上次数据'
  return moduleWarning(value)
})
</script>
<style scoped>
.module-widget{display:flex;flex-direction:column;min-height:100%;padding:20px;gap:18px;color:var(--text)}header,.module-heading,footer{display:flex;align-items:center;justify-content:space-between;gap:12px}.module-heading{justify-content:flex-start;min-width:0}.module-icon{display:grid;place-items:center;flex-shrink:0;width:42px;height:42px;border-radius:13px;background:color-mix(in srgb,var(--widget-accent) 16%,transparent);color:var(--widget-accent);font-size:21px;font-weight:700}h2{margin:3px 0 0;font-size:16px;overflow-wrap:anywhere}.module-heading small{font-size:10px;color:var(--muted);letter-spacing:1px}header a{font-size:11px;color:var(--widget-accent);white-space:nowrap}.module-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(115px,1fr));gap:12px;flex:1;align-content:center}.module-metrics article{display:flex;gap:12px;align-items:center;padding:14px 10px;border-left:3px solid var(--metric-color);min-width:0}.module-metrics article>div{min-width:0}.module-metrics span{font-size:11px;color:var(--muted)}strong{display:block;font-size:clamp(20px,2vw,32px);font-weight:650;margin-top:6px;color:var(--metric-color);overflow-wrap:anywhere;line-height:1.3}strong small{font-size:11px;margin-left:5px;font-weight:400;color:var(--muted)}.metric-total{font-size:10px;color:var(--muted)}.metric-ring{width:62px;height:62px;border-radius:50%;padding:5px;flex-shrink:0}.metric-ring b{background:var(--surface);border-radius:50%;display:flex;justify-content:center;align-items:center;height:100%;font-size:17px}.metric-ring small{font-size:10px}footer{font-size:10px;color:var(--muted);margin-top:auto;border-top:1px solid var(--line);padding-top:12px}footer i{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--widget-accent);margin-right:5px}.attention,.module-warning{color:var(--amber)}.module-warning{margin:0;font-size:11px;overflow-wrap:anywhere}.module-empty{flex:1;display:grid;align-content:center;text-align:center;min-height:100px;color:var(--muted)}.module-empty p{font-size:11px}.display-cards .module-metrics article{display:block;padding:18px;border:1px solid color-mix(in srgb,var(--metric-color) 30%,transparent);border-radius:12px;background:linear-gradient(135deg,color-mix(in srgb,var(--metric-color) 15%,transparent),transparent)}.display-cards strong{font-size:30px}.display-compact{padding:14px;gap:10px}.display-compact .module-metrics{display:flex;flex-direction:column;gap:0}.display-compact .module-metrics article{padding:9px 0;border-left:0;border-bottom:1px solid var(--line)}.display-compact .module-metrics article>div{display:flex;align-items:center;justify-content:space-between;width:100%;gap:10px}.display-compact strong{font-size:18px;margin:0}.display-compact .metric-total{display:none}.display-compact .module-icon{width:30px;height:30px;font-size:16px}.display-compact .module-heading small{display:none}
@media(max-width:700px){.module-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.module-widget{padding:14px}.metric-ring{width:48px;height:48px}}
</style>
