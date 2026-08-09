<template>
  <div class="v3-app">
    <h1>OpsCenter v3 (Vite 试点)</h1>
    <p class="sub">v3.28 F1-F2 前端工程化试点页面 — 全量迁移顺延 v3.29</p>
    <div class="demo">
      <StatCard label="服务器" :value="3" status="up" />
      <StatCard label="活跃告警" :value="1" status="danger" />
      <StatusBadge status="up" text="在线" />
      <StatusBadge status="down" text="离线" />
      <StatusBadge status="degraded" text="部分异常" />
    </div>
    <div class="footer">OpsCenter v3.28.0 · Vite</div>
  </div>
</template>

<script setup>
import StatCard from './components/StatCard.vue'
import StatusBadge from './components/StatusBadge.vue'
</script>

<style>
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --text2:#64748b; --border:#e2e8f0; }
body { margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif; }
.v3-app { max-width:720px; margin:0 auto; padding:40px 24px; }
h1 { font-size:24px; margin-bottom:6px; }
.sub { color:var(--text2); font-size:13px; margin-bottom:24px; }
.demo { display:flex; gap:12px; flex-wrap:wrap; align-items:center; }
.footer { margin-top:32px; color:var(--text2); font-size:12px; }
</style>
