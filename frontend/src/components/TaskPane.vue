<script setup lang="ts">
import type { EventRecord, TaskSummary } from '../api'

defineProps<{
  tasks: TaskSummary[]
  selectedTask: TaskSummary | null
  events: EventRecord[]
  loading: boolean
  error: string | null
}>()

const emit = defineEmits<{ select: [taskId: string] }>()

const STATUS_TONE: Record<string, string> = {
  merged: 'ok',
  ready: 'ok',
  claimed: 'accent',
  in_progress: 'accent',
  verifying: 'accent',
  approved: 'accent',
  merging: 'accent',
  awaiting_review: 'accent',
  blocked: 'warn',
  escalated: 'warn',
  awaiting_human: 'warn',
  needs_help: 'warn',
  cancelled: 'bad',
  abandoned: 'bad',
  draft_rejected: 'bad',
}

function tone(status: string): string {
  return STATUS_TONE[status] ?? 'muted'
}
</script>

<template>
  <section class="pane tasks" aria-label="任务与线程">
    <header class="pane-head">
      <h2>任务</h2>
    </header>

    <p v-if="error" class="notice tone-bad" role="alert">{{ error }}</p>
    <p v-else-if="loading" class="notice">加载中…</p>
    <p v-else-if="tasks.length === 0" class="empty">这个群组还没有任务。</p>
    <ul v-else class="task-list">
      <li v-for="task in tasks" :key="task.task_id">
        <button
          type="button"
          class="task-row"
          :class="{ active: selectedTask?.task_id === task.task_id }"
          @click="emit('select', task.task_id)"
        >
          <span class="task-id">{{ task.task_id }}</span>
          <span class="status" :class="`tone-${tone(task.status)}`">{{ task.status }}</span>
          <span class="task-owner">{{ task.owner ?? '—' }}</span>
        </button>
      </li>
    </ul>

    <section v-if="selectedTask" class="thread" aria-label="任务线程">
      <header class="pane-head thread-head">
        <h2>线程 · {{ selectedTask.task_id }}</h2>
        <p class="thread-meta">
          owner {{ selectedTask.owner ?? '—' }} · epoch {{ selectedTask.current_epoch }}
        </p>
      </header>
      <p v-if="events.length === 0" class="empty">还没有事件。</p>
      <ol v-else class="event-list">
        <li v-for="event in events" :key="event.seq" class="event">
          <span class="event-seq">#{{ event.seq }}</span>
          <span class="event-name">{{ event.event_name }}</span>
          <span class="event-actor">{{ event.actor_id }}</span>
          <details v-if="Object.keys(event.payload).length > 0" class="event-payload">
            <summary>payload</summary>
            <pre>{{ JSON.stringify(event.payload, null, 2) }}</pre>
          </details>
        </li>
      </ol>
    </section>
  </section>
</template>
