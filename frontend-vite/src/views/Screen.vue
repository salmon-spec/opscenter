<template>
  <div v-if="error" class="screen-load-error" role="alert">{{ error }} <button @click="load">重新加载</button><router-link to="/screen/manage">大屏管理</router-link></div>
  <ScreenCanvas v-else-if="board" :key="`${board.id}:${board.version}`" :config="board.config" />
  <p v-else class="screen-load-error" role="status">正在读取已发布大屏…</p>
</template>
<script setup>
import {onMounted,onActivated,onBeforeUnmount,ref} from 'vue'
import {api} from '../api'
import ScreenCanvas from './ScreenCanvas.vue'
const board=ref(null),error=ref('')
let controller=null
async function load(){
  controller?.abort();controller=new AbortController()
  try{const data=await api.get('/screen-boards/default',null,{signal:controller.signal,timeoutMs:15000});board.value=data;error.value=''}
  catch(e){if(e.name!=='AbortError')error.value=`大屏配置读取失败：${e.message}`}
}
onMounted(load)
onActivated(()=>{if(board.value)load()})
onBeforeUnmount(()=>controller?.abort())
</script>
<style scoped>.screen-load-error{padding:32px;display:flex;gap:16px;align-items:center}</style>
