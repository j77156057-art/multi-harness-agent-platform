<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { EventRecord, TaskDiff, TaskSummary } from '../api'
import type { HumanAction } from '../stores/session'

const props = defineProps<{
  tasks: TaskSummary[]
  selectedTask: TaskSummary | null
  events: EventRecord[]
  diff: TaskDiff | null
  diffLoading: boolean
  loading: boolean
  error: string | null
  busy: boolean
  operator: string
  humanAct: (action: HumanAction) => Promise<boolean>
  reviewClaim?: { taskId: string; reviewId: string; reviewedCommit: string } | null
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

// ---- 人工决策条（human-console 卡）----
// 动作集镜像后端 human/service.py 的状态守卫：非法状态不给按钮，后端仍是
// 唯一裁判（前端 409 也照常展示）。escalated 在 _CANCEL_STATES 之外——不能取消。
type DecisionAction = 'resume' | 'reopen' | 'release' | 'adjudicate' | 'cancel'

const RESUME_STATES = new Set(['awaiting_human', 'plan_drift', 'impl_drift'])
const REOPEN_STATES = new Set(['awaiting_human', 'plan_drift', 'impl_drift', 'abandoned'])
const CANCEL_STATES = new Set([
  'queued',
  'draft',
  'ready',
  'claimed',
  'in_progress',
  'verifying',
  'needs_help',
  'blocked',
  'changes_requested',
  'plan_drift',
  'impl_drift',
  'awaiting_review',
  'approved',
  'merging',
  'stale',
  'reclaiming',
  'awaiting_human',
])

const actions = computed<DecisionAction[]>(() => {
  const status = props.selectedTask?.status
  if (status === undefined) return []
  const list: DecisionAction[] = []
  if (RESUME_STATES.has(status)) list.push('resume')
  if (REOPEN_STATES.has(status)) list.push('reopen')
  if (status === 'escalated') list.push('release', 'adjudicate')
  if (CANCEL_STATES.has(status)) list.push('cancel')
  return list
})

const reviewFindings = ref('')
const reviewVerdict = ref<'approve' | 'request_changes'>('approve')
const reviewLease = computed(
  () => props.reviewClaim?.taskId === props.selectedTask?.task_id,
)
const reviewId = computed(() => props.reviewClaim?.reviewId ?? '')
const reviewCommit = ref('')
watch(
  () => props.reviewClaim,
  (claim) => {
    reviewCommit.value =
      claim != null && claim.taskId === props.selectedTask?.task_id ? claim.reviewedCommit : ''
  },
  { immediate: true },
)
const reviewAvailable = computed(
  () => props.selectedTask?.status === 'awaiting_review' && props.selectedTask.current_attempt_id !== null,
)

// awaiting_human 的待答问题在事件投影里（human.question 的 payload），
// 不新增读端点；options 不在事件里，答复走自由文本。
const openQuestion = computed<string | null>(() => {
  if (props.selectedTask?.status !== 'awaiting_human') return null
  for (let i = props.events.length - 1; i >= 0; i -= 1) {
    const event = props.events[i]
    if (event?.event_name === 'human.question') {
      const question = event.payload.question
      return typeof question === 'string' ? question : null
    }
  }
  return null
})

const decidedBy = ref('operator')
const answer = ref('')
const cancelReason = ref('')
const adjudicateQuestion = ref('')
const adjudicateOptions = ref('')

// 操作者跟随令牌主体（frontend-token 卡）：仅当字段还是默认/空值时同步——
// 用户手填过的名字不覆盖。
watch(
  () => props.operator,
  (value) => {
    if (decidedBy.value.trim() === '' || decidedBy.value === 'operator') {
      decidedBy.value = value
    }
  },
  { immediate: true },
)

const operatorValid = computed(() => decidedBy.value.trim().length > 0)

async function submit(kind: DecisionAction): Promise<void> {
  const task = props.selectedTask
  if (task === null || props.busy || !operatorValid.value) return
  const base = { taskId: task.task_id, decided_by: decidedBy.value.trim() }
  let action: HumanAction
  switch (kind) {
    case 'resume':
    case 'reopen': {
      const trimmed = answer.value.trim()
      action = { kind, ...base, ...(trimmed.length > 0 ? { answer: trimmed } : {}) }
      break
    }
    case 'release':
      action = { kind, ...base }
      break
    case 'adjudicate': {
      const question = adjudicateQuestion.value.trim()
      if (question.length === 0) return
      const options = adjudicateOptions.value
        .split(',')
        .map((item) => item.trim())
        .filter((item) => item.length > 0)
      action = { kind, ...base, question, ...(options.length > 0 ? { options } : {}) }
      break
    }
    case 'cancel': {
      const trimmed = cancelReason.value.trim()
      action = { kind, ...base, ...(trimmed.length > 0 ? { reason: trimmed } : {}) }
      break
    }
  }
  const ok = await props.humanAct(action)
  if (ok) {
    answer.value = ''
    cancelReason.value = ''
    adjudicateQuestion.value = ''
    adjudicateOptions.value = ''
  }
}

async function claimReview(): Promise<void> {
  const task = props.selectedTask
  if (task === null || task.current_attempt_id === null || props.busy || !operatorValid.value) return
  const ok = await props.humanAct({
    kind: 'review_claim',
    taskId: task.task_id,
    attemptId: task.current_attempt_id,
    decided_by: decidedBy.value.trim(),
  })
  if (ok) {
    reviewCommit.value = ''
  }
}

async function submitReview(): Promise<void> {
  const task = props.selectedTask
  if (
    task === null ||
    task.current_attempt_id === null ||
    props.busy ||
    !operatorValid.value ||
    reviewId.value.trim() === '' ||
    reviewCommit.value.trim() === ''
  ) return
  const findings = reviewFindings.value.trim()
  await props.humanAct({
    kind: 'review_submit',
    taskId: task.task_id,
    attemptId: task.current_attempt_id,
    decided_by: decidedBy.value.trim(),
    review_id: reviewId.value.trim(),
    reviewed_commit: reviewCommit.value.trim(),
    verdict: reviewVerdict.value,
    ...(findings.length > 0 ? { findings: [{ severity: 'note', message: findings }] } : {}),
  })
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

      <section class="decision-card" aria-label="人工决策">
        <header class="decision-head">
          <h3>人工决策</h3>
          <span class="status" :class="`tone-${tone(selectedTask.status)}`">
            {{ selectedTask.status }}
          </span>
        </header>
        <p v-if="openQuestion" class="decision-question">待答：{{ openQuestion }}</p>

        <template v-if="actions.length > 0">
          <label class="decision-field">
            操作者
            <input v-model="decidedBy" name="decided_by" placeholder="operator" />
          </label>
          <label v-if="actions.includes('resume') || actions.includes('reopen')" class="decision-field">
            答复（resume/reopen 时随决定入台账；可空）
            <textarea v-model="answer" name="decision_answer" rows="2"></textarea>
          </label>
          <template v-if="actions.includes('adjudicate')">
            <label class="decision-field">
              裁决问题
              <input v-model="adjudicateQuestion" name="adjudicate_question" placeholder="把升级改写成一个问题" />
            </label>
            <label class="decision-field">
              选项（逗号分隔；可空）
              <input v-model="adjudicateOptions" name="adjudicate_options" placeholder="A, B" />
            </label>
          </template>
          <label v-if="actions.includes('cancel')" class="decision-field">
            取消理由（cancel 时记入事件）
            <input v-model="cancelReason" name="cancel_reason" placeholder="可空" />
          </label>

          <p class="decision-actions">
            <button
              v-if="actions.includes('resume')"
              type="button"
              :disabled="busy || !operatorValid"
              @click="submit('resume')"
            >
              恢复工作
            </button>
            <button
              v-if="actions.includes('reopen')"
              type="button"
              :disabled="busy || !operatorValid"
              @click="submit('reopen')"
            >
              重开
            </button>
            <button
              v-if="actions.includes('release')"
              type="button"
              :disabled="busy || !operatorValid"
              @click="submit('release')"
            >
              释放
            </button>
            <button
              v-if="actions.includes('adjudicate')"
              type="button"
              :disabled="busy || !operatorValid || adjudicateQuestion.trim().length === 0"
              @click="submit('adjudicate')"
            >
              裁决
            </button>
            <button
              v-if="actions.includes('cancel')"
              type="button"
              :disabled="busy || !operatorValid"
              @click="submit('cancel')"
            >
              取消任务
            </button>
          </p>
        </template>
        <p v-else class="decision-none">当前状态没有可用的人工动作（终态或由巡检器接管）。</p>
      </section>

      <section v-if="reviewAvailable" class="decision-card" aria-label="管理员审核">
        <header class="decision-head">
          <h3>管理员审核</h3>
          <span class="status tone-accent">{{ reviewLease ? '已领取' : '待领取' }}</span>
        </header>
        <p class="decision-note">三个 Harness 的提交都在这里由管理员逐项审核。</p>
        <label class="decision-field">
          审核 ID
          <input :value="reviewId" name="review_id" placeholder="领取后自动填写" disabled />
        </label>
        <label class="decision-field">
          reviewed commit
          <input v-model="reviewCommit" name="reviewed_commit" placeholder="提交的 commit" />
        </label>
        <label class="decision-field">
          审核意见
          <textarea v-model="reviewFindings" name="review_findings" rows="2" placeholder="可空"></textarea>
        </label>
        <p class="decision-actions">
          <button v-if="!reviewLease" type="button" :disabled="busy || !operatorValid" @click="claimReview">
            领取审核
          </button>
          <template v-else>
            <button type="button" :disabled="busy || reviewCommit.trim().length === 0" @click="reviewVerdict = 'approve'; submitReview()">
              审核通过
            </button>
            <button type="button" :disabled="busy || reviewCommit.trim().length === 0" @click="reviewVerdict = 'request_changes'; submitReview()">
              要求修改
            </button>
          </template>
        </p>
      </section>

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
          <span class="event-time">{{ event.created_at.slice(0, 19).replace('T', ' ') }}</span>
          <details v-if="Object.keys(event.payload).length > 0" class="event-payload">
            <summary>payload</summary>
            <pre>{{ JSON.stringify(event.payload, null, 2) }}</pre>
          </details>
        </li>
      </ol>
    </section>
  </section>
</template>
