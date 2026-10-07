<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MessageRecord } from '../api'

const props = defineProps<{
  groupId: string | null
  messages: MessageRecord[]
  loading: boolean
  error: string | null
}>()

const emit = defineEmits<{ send: [body: string] }>()

const draft = ref('')
const canSend = computed(() => props.groupId !== null && draft.value.trim().length > 0)

function submit(): void {
  if (!canSend.value) return
  emit('send', draft.value.trim())
  draft.value = ''
}
</script>

<template>
  <section class="pane channel" aria-label="项目群">
    <header class="pane-head">
      <h2>项目群{{ groupId ? ` · ${groupId}` : '' }}</h2>
    </header>

    <p v-if="error" class="notice tone-bad" role="alert">{{ error }}</p>
    <p v-else-if="loading" class="notice">加载中…</p>
    <p v-else-if="groupId === null" class="empty">从左侧选择一个群组。</p>
    <p v-else-if="messages.length === 0" class="empty">还没有消息——发第一条。</p>

    <ol class="messages">
      <li v-for="message in messages" :key="message.message_id" class="message">
        <div class="message-meta">
          <span class="message-sender">{{ message.sender_id }}</span>
          <span class="message-time">{{ message.created_at.slice(0, 19).replace('T', ' ') }}</span>
        </div>
        <p class="message-body">{{ message.body }}</p>
      </li>
    </ol>

    <form class="composer" @submit.prevent="submit">
      <input
        v-model="draft"
        class="composer-input"
        :disabled="groupId === null"
        placeholder="对群组发言（human / operator）"
        aria-label="消息内容"
      />
      <button type="submit" :disabled="!canSend">发送</button>
    </form>
  </section>
</template>
