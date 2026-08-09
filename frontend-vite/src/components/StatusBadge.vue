<template>
  <span class="badge" :class="status">{{ text }}</span>
</template>

<script setup>
// v3.28 F2 试点组件 2：状态徽章
defineProps({
  status: { type: String, default: 'up' },
  text: String,
})
</script>

<style scoped>
.badge { font-size:11px; padding:3px 10px; border-radius:20px; font-weight:600; }
.badge.up { background:rgba(34,197,94,.15); color:#16a34a; }
.badge.down { background:rgba(239,68,68,.15); color:#dc2626; }
.badge.degraded { background:rgba(234,179,8,.15); color:#ca8a04; }
</style>
