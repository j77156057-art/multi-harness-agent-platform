<script setup lang="ts">
import { ref, watch } from 'vue'
import type { EventRecord, TaskDiff, TaskSummary } from '../api'

const props = defineProps<{
  tasks: TaskSummary[]
  selectedTask: TaskSummary | null
  events: EventRecord[]
  diff: TaskDiff | null
  diffLoading: boolean
  loading: boolean
  error: string | null
}>()

const emit = defineEmits<{ select: [taskId: string]; expandDiff: [] }>()

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

function short(commit: string | null): string {
  return commit === null ? '—' : commit.slice(0, 7)
}

// patch 拉回来即默认展开（用户点了"展开 patch"，结果却折叠着会更奇怪）。
const patchOpen = ref(props.diff?.patch != null)
watch(
  () => props.diff?.patch,
  (patch) => {
    if (patch !== null && patch !== undefined) {
      patchOpen.value = true
    }
  },
)
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

      <section class="diff-card" aria-label="任务差异">
        <header class="diff-head">
          <h3>差异</h3>
          <span v-if="diffLoading" class="diff-meta">加载中…</span>
        </header>
        <p v-if="diff === null && !diffLoading" class="diff-note">还没有差异读数。</p>
        <template v-else-if="diff !== null">
          <p v-if="!diff.available" class="diff-note">差异不可用：{{ diff.reason }}</p>
          <template v-else>
            <p class="diff-summary">
              <span>{{ diff.files_changed }} 个文件</span>
              <span class="diff-add">+{{ diff.insertions }}</span>
              <span class="diff-del">−{{ diff.deletions }}</span>
              <span class="diff-meta">
                {{ short(diff.base_commit) }}..{{ short(diff.head_commit) }}
              </span>
            </p>
            <p v-if="diff.files_changed === 0" class="diff-note">与基线相比没有差异。</p>
            <ul v-else class="diff-files">
              <li v-for="file in diff.files" :key="file.path" class="diff-file">
                <span class="diff-path">{{ file.path }}</span>
                <span v-if="file.binary" class="diff-meta">二进制</span>
                <span v-else class="diff-counts">
                  <span class="diff-add">+{{ file.insertions }}</span>
                  <span class="diff-del">−{{ file.deletions }}</span>
                </span>
              </li>
            </ul>
            <p v-if="diff.files_changed > 0" class="diff-actions">
              <button
                v-if="diff.patch === null"
                type="button"
                :disabled="diffLoading"
                @click="emit('expandDiff')"
              >
                {{ diffLoading ? '拉取 patch…' : '展开 patch' }}
              </button>
              <button v-else type="button" @click="patchOpen = !patchOpen">
                {{ patchOpen ? '收起 patch' : '展开 patch' }}
              </button>
            </p>
            <p v-if="diff.truncated" class="diff-note">patch 预览已截断（有界预览）。</p>
            <pre v-if="diff.patch !== null && patchOpen" class="diff-patch">{{ diff.patch }}</pre>
          </template>
        </template>
      </section>

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
