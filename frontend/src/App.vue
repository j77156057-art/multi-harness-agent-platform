<script setup lang="ts">
// 应用外壳：标题 + 三栏（三栏壳 Shell 由路由承载，见 router.ts）。
</script>

<template>
  <div class="app">
    <header class="app-head">
      <h1>Agent 协作平台 · 控制台</h1>
      <p class="app-sub">R10 §22 三栏：项目/群组 · 群聊 · 任务与线程 —— 所有操作经 /api/v1</p>
    </header>
    <router-view />
  </div>
</template>
