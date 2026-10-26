<template>
  <section class="life-calendar" aria-label="日期日历">
    <div class="calendar-toolbar"><button class="btn" aria-label="上个月" @click="move(-1)">‹</button><h3>{{ year }} 年 {{ month+1 }} 月</h3><button class="btn" aria-label="下个月" @click="move(1)">›</button><button class="btn" @click="today">本月</button></div>
    <div class="calendar-grid"><b v-for="day in ['一','二','三','四','五','六','日']" :key="day" class="weekday">{{ day }}</b>
      <div v-for="cell in cells" :key="cell.date" class="calendar-day" :class="{muted:!cell.current,today:cell.date===currentDate}"><time :datetime="cell.date">{{ cell.day }}</time><button v-for="row in cell.events" :key="row.id" class="calendar-event" @click="$emit('edit',row)">{{ row.name || row.title }}<small>{{ row.done ? '已完成' : kind==='home-inventory'?'保修到期':kind==='subscriptions'?'下次续费':'截止' }}</small></button></div>
    </div>
  </section>
</template>
<script setup>
import { computed,ref } from 'vue'
const props=defineProps({rows:{type:Array,required:true},kind:{type:String,required:true}})
defineEmits(['edit'])
const currentDate=new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'})
const year=ref(Number(currentDate.slice(0,4))),month=ref(Number(currentDate.slice(5,7))-1)
function today(){year.value=Number(currentDate.slice(0,4));month.value=Number(currentDate.slice(5,7))-1}
function move(n){const d=new Date(year.value,month.value+n,1);year.value=d.getFullYear();month.value=d.getMonth()}
const cells=computed(()=>{const first=new Date(year.value,month.value,1);const offset=(first.getDay()+6)%7;return Array.from({length:42},(_,i)=>{const d=new Date(year.value,month.value,1-offset+i);const date=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;return {date,day:d.getDate(),current:d.getMonth()===month.value,events:props.rows.filter(r=>{let raw=props.kind==='subscriptions'?r.next_payment:props.kind==='home-inventory'?r.warrantyExpires:r.due_date;if(!raw || r.inactive || r.archived || r.lifetimeWarranty)return false;const key=raw.length<=10?raw:new Date(raw).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});return key===date})}})})
</script>
<style scoped>
.calendar-toolbar{display:flex;align-items:center;gap:14px;margin:16px 0}.calendar-toolbar h3{margin:0}.calendar-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border-top:1px solid var(--border);border-left:1px solid var(--border)}.weekday,.calendar-day{padding:10px;border-right:1px solid var(--border);border-bottom:1px solid var(--border)}.weekday{text-align:center}.calendar-day{min-height:110px}.calendar-day.muted{background:var(--bg-hover);color:var(--text-muted)}.calendar-day.today{box-shadow:inset 0 0 0 2px var(--primary,#2563eb)}.calendar-event{display:block;width:100%;border:0;border-radius:5px;padding:5px;text-align:left;background:var(--bg-hover,#edf3fd);color:inherit;margin-top:5px;overflow-wrap:anywhere;cursor:pointer}.calendar-event small{display:block;color:var(--text-muted)}@media(max-width:700px){.calendar-day{padding:3px;min-height:75px}.calendar-event{font-size:11px}}
</style>
