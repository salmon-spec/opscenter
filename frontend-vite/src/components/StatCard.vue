<template>
  <div class="stat-card">
    <div class="stat-label">{{ label }}</div>
    <div class="stat-value">{{ value }}</div>
    <span class="stat-dot" :class="status"></span>
  </div>
</template>

<script setup>
// v3.28 F2 试点组件 1：统计卡片
defineProps({
  label: String,
  value: [Number, String],
  status: { type: String, default: 'up' },
})
</script>

<style scoped>
.stat-card { position:relative; background:var(--card); border:1px solid var(--border); border-radius:12px; padding:16px 18px; min-width:130px; }
.stat-label { font-size:12px; color:var(--text2); }
.stat-value { font-size:26px; font-weight:700; margin-top:4px; }
.stat-dot { position:absolute; top:14px; right:14px; width:8px; height:8px; border-radius:50%; }
.stat-dot.up { background:#22c55e; }
.stat-dot.danger { background:#ef4444; }
.stat-dot.degraded { background:#eab308; }
</style>
