<template><LifeWorkspace :kind="kind" /></template>
<script setup>
import LifeWorkspace from './LifeWorkspace.vue'
defineProps({ kind: { type: String, required: true } })
</script>
