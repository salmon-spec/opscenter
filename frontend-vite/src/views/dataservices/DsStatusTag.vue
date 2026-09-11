<template>
  <span class="tag" :class="cls" :title="title">{{ label }}</span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  configured: { type: Boolean, default: false },
  available: { type: Boolean, default: false },
})

const label = computed(() => (props.available ? '可用' : props.configured ? '异常' : '未配置'))
const cls = computed(() => (props.available ? 'tag-green' : props.configured ? 'tag-red' : 'tag-slate'))
const title = computed(() => (props.available ? '配置已下发且探测可用' : props.configured ? '已配置但不可用' : '未配置 MIDDLEWARE 环境变量'))
</script>
