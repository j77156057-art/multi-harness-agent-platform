<script setup lang="ts">
// 中栏（R10 §22「群聊/方案」）：群聊读/发 + 方案区（新建计划与审批，
// human-console 卡）。计划快照是 JSON 文本，本地先 parse 再发请求——
// 坏 JSON 不该变成一次网络往返。
import { computed, ref, watch } from 'vue'
import type { MessageRecord } from '../api'

const props = defineProps<{
  groupId: string | null
  messages: MessageRecord[]
  loading: boolean
  error: string | null
  busy: boolean
  operator: string
  createPlan: (fields: {
    plan_id: string
    snapshot: Record<string, unknown>
  }) => Promise<boolean>
  decidePlan: (
    planId: string,
    approve: boolean,
    fields: { decided_by: string; reason?: string },
  ) => Promise<boolean>
}>()

const emit = defineEmits<{ send: [body: string] }>()

const draft = ref('')
const canSend = computed(() => props.groupId !== null && draft.value.trim().length > 0)

function submit(): void {
  if (!canSend.value) return
  emit('send', draft.value.trim())
  draft.value = ''
}

// 合法最小示例（与 PlanValidator 的字段对齐：capabilities 必须已登记）。
const SNAPSHOT_TEMPLATE = `{
  "tasks": [
    {
      "id": "T-1",
      "capabilities": ["python"],
      "depends_on": [],
      "exclusive_resources": [],
      "acceptance": []
    }
  ],
  "registered_capabilities": ["python"]
}`

const planId = ref('')
const snapshotText = ref(SNAPSHOT_TEMPLATE)
const snapshotError = ref<string | null>(null)
const decidedBy = ref('operator')
const rejectReason = ref('')

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

function parseSnapshot(): Record<string, unknown> | null {
  snapshotError.value = null
  try {
    const parsed: unknown = JSON.parse(snapshotText.value)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      snapshotError.value = '快照必须是一个 JSON 对象'
      return null
    }
    return parsed as Record<string, unknown>
  } catch (error) {
    snapshotError.value = `JSON 解析失败：${error instanceof Error ? error.message : String(error)}`
    return null
  }
}

async function submitPlan(): Promise<void> {
  if (planId.value.trim().length === 0 || props.busy) return
  const snapshot = parseSnapshot()
  if (snapshot === null) return
  const ok = await props.createPlan({ plan_id: planId.value.trim(), snapshot })
  if (ok) {
    snapshotText.value = SNAPSHOT_TEMPLATE
  }
}

async function decide(approve: boolean): Promise<void> {
  if (planId.value.trim().length === 0 || decidedBy.value.trim().length === 0 || props.busy) {
    return
  }
  const ok = await props.decidePlan(planId.value.trim(), approve, {
    decided_by: decidedBy.value.trim(),
    ...(approve || rejectReason.value.trim().length === 0
      ? {}
      : { reason: rejectReason.value.trim() }),
  })
  if (ok) {
    rejectReason.value = ''
  }
}
</script>

<template>
  <section class="pane channel" aria-label="项目群">
    <header class="pane-head">
      <h2>项目群{{ groupId ? ` · ${groupId}` : '' }}</h2>
    </header>

    <details class="plan-card" :open="false">
      <summary>方案（计划与审批）</summary>
      <form class="rail-form" @submit.prevent="submitPlan">
        <label>
          计划 ID
          <input v-model="planId" name="plan_id" placeholder="PLAN-X" />
        </label>
        <label>
          快照 JSON
          <textarea v-model="snapshotText" name="plan_snapshot" rows="10"></textarea>
        </label>
        <p v-if="snapshotError" class="form-error" role="alert">{{ snapshotError }}</p>
        <button type="submit" :disabled="planId.trim().length === 0 || busy">
          新建计划（draft）
        </button>
      </form>

      <form class="rail-form plan-decide" @submit.prevent="decide(true)">
        <label>
          操作者
          <input v-model="decidedBy" name="plan_decided_by" placeholder="operator" />
        </label>
        <label>
          驳回理由（驳回时用）
          <input v-model="rejectReason" name="plan_reject_reason" placeholder="可空" />
        </label>
        <p class="plan-actions">
          <button
            type="submit"
            :disabled="planId.trim().length === 0 || decidedBy.trim().length === 0 || busy"
          >
            通过计划
          </button>
          <button
            type="button"
            :disabled="planId.trim().length === 0 || decidedBy.trim().length === 0 || busy"
            @click="decide(false)"
          >
            驳回计划
          </button>
        </p>
      </form>
      <p class="rail-note">审批通过后：ready 任务进入任务栏；等待中的沿依赖图放行。</p>
    </details>

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
