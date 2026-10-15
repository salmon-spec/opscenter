<template>
  <nav v-if="module" class="module-tabs" aria-label="模块导航">
    <router-link v-for="item in pages" :key="item.path" :to="item.path" class="module-tab" :class="{ active: route.path === item.path }">{{ item.label }}</router-link>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const modules = [
  { prefix: '/kubernetes/', groups: [
    { key: 'overview', label: '集群概览', path: '/kubernetes/overview', items: [{ label: '集群概览', path: '/kubernetes/overview' }] },
    { key: 'apps', label: '应用负载', path: '/kubernetes/workloads', items: [{ label: '工作负载', path: '/kubernetes/workloads' }, { label: 'Pod', path: '/kubernetes/pods' }] },
    { key: 'resources', label: '资源与运维', path: '/kubernetes/network', items: [{ label: '网络与入口', path: '/kubernetes/network' }, { label: '存储', path: '/kubernetes/storage' }, { label: '任务与事件', path: '/kubernetes/operations' }] },
  ] },
  { paths: ['/system/', '/database'], groups: [
    { key: 'system-app', label: '系统应用', path: '/system/monitor', items: [
      { label: '监控', path: '/system/monitor' },
      { label: '文件', path: '/system/files' },
      { label: '进程', path: '/system/processes' },
      { label: 'SSH', path: '/system/ssh' },
      { label: '防火墙', path: '/system/firewall' },
      { label: '终端', path: '/system/terminal' },
    ] },
    { key: 'runtime', label: '运行环境', path: '/database', items: [{ label: '数据库', path: '/database' }] },
  ] },
]
const module = computed(() => modules.find((item) => item.prefix ? route.path.startsWith(item.prefix) : item.paths.some((path) => route.path === path || route.path.startsWith(path))) || null)
const pages = computed(() => {
  if (!module.value) return []
  if (module.value.paths?.includes('/database')) {
    return route.path.startsWith('/system/') ? module.value.groups[0].items : module.value.groups[1].items
  }
  return module.value.groups.flatMap((group) => group.items)
})
</script>

<style scoped>
.module-tabs{height:44px;flex-shrink:0;padding:6px 14px;display:flex;align-items:center;gap:4px;overflow-x:auto;background:#fff;border-bottom:1px solid var(--border)}
.module-tab{height:31px;padding:0 14px;display:flex;align-items:center;justify-content:center;border-radius:7px;color:var(--muted);font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap}
.module-tab:hover,.module-tab.active{color:var(--brand);background:#eff6ff}
@media(max-width:720px){.module-tabs{padding-inline:8px}.module-tab{padding-inline:10px}}
</style>
