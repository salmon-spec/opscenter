<template>
  <!-- 数据服务 · Redis：keyspace 概览 + Key 按需浏览（只读，不轮询重查询） -->
  <DataServiceKind kind="redis" />
</template>

<script setup>
import DataServiceKind from './DataServiceKind.vue'
</script>
