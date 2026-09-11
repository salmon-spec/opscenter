<template>
  <div v-if="state.tabs.length" class="workbench-tabs">
    <div class="wt-scroll">
      <div
        v-for="tab in state.tabs"
        :key="tab.key"
        class="wt-tab"
        :class="{ active: tab.key === state.activeKey }"
        :title="tab.title"
        @click="activate(tab)"
        @contextmenu.prevent="openMenu($event, tab)"
      >
        <span v-if="tab.pinned" class="wt-pin" title="已固定"></span>
        <span class="wt-title">{{ tab.title }}</span>
        <span v-if="!tab.pinned" class="wt-close" title="关闭" @click.stop="onClose(tab)">×</span>
      </div>
    </div>
    <div v-if="menu.visible" class="wt-menu" :style="{ left: menu.x + 'px', top: menu.y + 'px' }" @click.stop>
      <button class="wt-menu-item" @click="menuClose">关闭</button>
      <button class="wt-menu-item" @click="menuCloseOthers">关闭其他</button>
      <button class="wt-menu-item" @click="menuCloseRight">关闭右侧</button>
      <button class="wt-menu-item" @click="menuTogglePin">{{ menu.tab?.pinned ? '取消固定' : '固定' }}</button>
    </div>
  </div>
</template>

<script setup>
/* 工作台页签栏（需求基线 §5）：点击激活（router.push 页签记录的 path+query），右键菜单
   关闭/关闭其他/关闭右侧/固定·取消固定。样式在 styles.css 末尾 Workbench tabs 区块。 */
import { onMounted, onUnmounted, reactive, watch } from 'vue'
import { useRouter } from 'vue-router'
import { state, activateTab, closeTab, closeOthers, closeRight, togglePin } from '../../workbench/tabs'

const router = useRouter()
const menu = reactive({ visible: false, x: 0, y: 0, tab: null })

function openMenu(e, tab) {
  menu.visible = true
  menu.tab = tab
  menu.x = Math.min(e.clientX, window.innerWidth - 150)
  menu.y = Math.min(e.clientY, window.innerHeight - 140)
}
function hideMenu() { menu.visible = false }
function activate(tab) { hideMenu(); activateTab(tab.key) }
function onClose(tab) { hideMenu(); closeTab(tab.key) }
function menuClose() { const t = menu.tab; hideMenu(); if (t) closeTab(t.key) }
function menuCloseOthers() { const t = menu.tab; hideMenu(); if (t) closeOthers(t.key) }
function menuCloseRight() { const t = menu.tab; hideMenu(); if (t) closeRight(t.key) }
function menuTogglePin() { const t = menu.tab; hideMenu(); if (t) togglePin(t.key) }

watch(() => router.currentRoute.value.fullPath, hideMenu)
onMounted(() => window.addEventListener('click', hideMenu))
onUnmounted(() => window.removeEventListener('click', hideMenu))
</script>
