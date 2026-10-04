<script setup lang="ts">
// MVP-01 只放占位。实质界面在 MVP-09。
// 现在只做一件事：把后端 /spec/status 显示出来，作为"前后端联通"的最小验证。
import { onMounted, ref } from 'vue'

interface SpecStatus {
  spec: string
  total: number
  passed: number
  failed: number
  ok: boolean
  failures: string[]
}

const status = ref<SpecStatus | null>(null)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    const r = await fetch('/api/spec/status')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    status.value = (await r.json()) as SpecStatus
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
})
</script>

<template>
  <main>
    <h1>Agent Collaboration Platform — MVP</h1>
    <p class="muted">骨架已就位。业务端点从 MVP-02 开始。</p>

    <section>
      <h2>后端联通</h2>
      <p v-if="error">无法连接后端：{{ error }}</p>
      <p v-else-if="!status">加载中…</p>
      <div v-else>
        <p>规范一致性：{{ status.passed }} / {{ status.total }} 项通过</p>
        <p :class="status.ok ? 'ok' : 'bad'">
          {{ status.ok ? '通过' : `失败 ${status.failed} 项` }}
        </p>
        <ul v-if="status.failures.length">
          <li v-for="(f, i) in status.failures" :key="i">{{ f }}</li>
        </ul>
      </div>
    </section>
  </main>
</template>
