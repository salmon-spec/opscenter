<template>
  <div class="empty">
    <div class="empty-icon">{{ icon }}</div>
    <div>{{ text }}</div>
  </div>
</template>

<script setup>
defineProps({
  icon: { type: String, default: '📭' },
  text: { type: String, default: '暂无数据' },
})
</script>
