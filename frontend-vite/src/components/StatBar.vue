<template>
  <div class="stat-bar">
    <div class="stat-head">
      <span class="stat-label">{{ label }}</span>
      <span class="stat-value">{{ value }}</span>
    </div>
    <div class="stat-track">
      <div class="stat-fill" :style="{ width: pct + '%', background: color }"></div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  label: { type: String, default: '' },
  value: { type: [String, Number], default: '' },
  percent: { type: Number, default: 0 },
})

// 颜色随水位变化：<70 绿，70-89 黄，>=90 红
const color = computed(() => {
  if (props.percent >= 90) return 'var(--err)'
  if (props.percent >= 70) return 'var(--warn)'
  return 'var(--ok)'
})
const pct = computed(() => Math.max(0, Math.min(100, Number(props.percent) || 0)))
</script>

<style scoped>
.stat-bar { margin-bottom: 10px; }
.stat-head { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 4px; }
.stat-label { color: var(--muted); }
.stat-value { font-weight: 600; }
.stat-track { height: 6px; background: #eef1f6; border-radius: 3px; overflow: hidden; }
.stat-fill { height: 100%; border-radius: 3px; transition: width .4s; }
</style>
