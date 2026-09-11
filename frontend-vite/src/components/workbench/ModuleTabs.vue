<template>
  <div v-if="module" class="module-tabs">
    <nav class="module-groups" :style="{ '--group-count': module.groups.length }" aria-label="模块导航">
      <router-link v-for="group in module.groups" :key="group.key" :to="group.path" class="module-group" :class="{ active: activeGroup?.key === group.key }">{{ group.label }}</router-link>
    </nav>
    <nav v-if="activeGroup?.items?.length > 1" class="module-pages" :style="{ '--page-count': activeGroup.items.length }" aria-label="功能导航">
      <router-link v-for="item in activeGroup.items" :key="item.path" :to="item.path" class="module-page" :class="{ active: route.path === item.path }">{{ item.label }}</router-link>
    </nav>
  </div>
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
const activeGroup = computed(() => module.value?.groups.find((group) => group.items.some((item) => item.path === route.path)) || null)
</script>

<style scoped>
.module-tabs { flex-shrink: 0; padding: 8px 18px; background: #fff; border-bottom: 1px solid var(--border); }
.module-groups, .module-pages { display: grid; gap: 6px; }
.module-groups { grid-template-columns: repeat(var(--group-count), minmax(0, 1fr)); }
.module-page, .module-group { display: flex; align-items: center; justify-content: center; min-width: 0; border-radius: 7px; text-decoration: none; white-space: nowrap; }
.module-group { min-height: 32px; color: var(--muted); font-size: 13px; font-weight: 600; }
.module-group:hover, .module-group.active { color: var(--brand); background: #eff6ff; }
.module-pages { grid-template-columns: repeat(var(--page-count), minmax(0, 1fr)); max-width: 540px; margin-top: 6px; }
.module-page { min-height: 27px; border: 1px solid var(--border); color: var(--muted); font-size: 12px; }
.module-page:hover, .module-page.active { color: var(--brand); border-color: #93c5fd; background: #f8fbff; }
@media (max-width: 720px) { .module-tabs { padding-inline: 10px; overflow-x: auto; } .module-groups { min-width: 390px; } }
</style>
