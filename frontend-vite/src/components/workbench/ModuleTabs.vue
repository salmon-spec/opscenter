<template>
  <nav v-if="module" class="module-tabs" aria-label="模块导航">
    <router-link v-for="item in pages" :key="item.path" :to="item.path" class="module-tab" :class="{ active: route.path === item.path }">{{ item.label }}</router-link>
  </nav>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
import {moduleNavigation as modules} from '../../workbench/moduleNavigation'

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
