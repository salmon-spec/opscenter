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
  { prefix: '/data-services', groups: [
    { key: 'overview', label: '实例总览', path: '/data-services', items: [{ label: '实例总览', path: '/data-services' }] },
    { key: 'cache', label: '缓存与消息', path: '/data-services/redis', items: [{ label: 'Redis', path: '/data-services/redis' }, { label: 'RabbitMQ', path: '/data-services/rabbitmq' }, { label: 'Kafka', path: '/data-services/kafka' }, { label: 'ZooKeeper', path: '/data-services/zookeeper' }] },
    { key: 'registry', label: '配置与存储', path: '/data-services/nacos', items: [{ label: 'Nacos', path: '/data-services/nacos' }, { label: 'MinIO', path: '/data-services/minio' }, { label: 'MongoDB', path: '/data-services/mongodb' }] },
  ] },
  { paths: ['/system/', '/docker', '/database'], groups: [
    { key: 'monitor', label: '主机监控', path: '/system/monitor', items: [{ label: '主机监控', path: '/system/monitor' }] },
    { key: 'terminal', label: '终端', path: '/system/terminal', items: [{ label: '终端', path: '/system/terminal' }] },
    { key: 'runtime', label: '运行环境', path: '/docker', items: [{ label: 'Docker', path: '/docker' }, { label: '数据库', path: '/database' }] },
    { key: 'tools', label: '运维工具', path: '/system/files', items: [{ label: '文件', path: '/system/files' }, { label: '进程管理', path: '/system/processes' }] },
    { key: 'security', label: '安全管理', path: '/system/firewall', items: [{ label: '防火墙', path: '/system/firewall' }, { label: 'SSH 管理', path: '/system/ssh' }] },
  ] },
]
const module = computed(() => modules.find((item) => item.prefix ? route.path.startsWith(item.prefix) : item.paths.some((path) => route.path === path || route.path.startsWith(path))) || null)
const pages = computed(() => module.value?.groups.flatMap((group) => group.items) || [])
</script>

<style scoped>
.module-tabs{height:44px;flex-shrink:0;padding:6px 14px;display:flex;align-items:center;gap:4px;overflow-x:auto;background:#fff;border-bottom:1px solid var(--border)}
.module-tab{height:31px;padding:0 14px;display:flex;align-items:center;justify-content:center;border-radius:7px;color:var(--muted);font-size:13px;font-weight:600;text-decoration:none;white-space:nowrap}
.module-tab:hover,.module-tab.active{color:var(--brand);background:#eff6ff}
@media(max-width:720px){.module-tabs{padding-inline:8px}.module-tab{padding-inline:10px}}
</style>
