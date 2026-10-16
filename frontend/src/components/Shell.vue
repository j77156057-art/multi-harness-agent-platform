<script setup lang="ts">
// 三栏壳：同时挂在 / 、/g/:groupId、/g/:groupId/t/:taskId 三条路由上（组件跨导航
// 复用）。路由只承载"选中状态"，数据编排全走 session store；这里另接管理面与
// 人工决策面的动作（human-console 卡），并通过 notice 横幅给出动作结果。
import { storeToRefs } from 'pinia'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSession, type HumanAction } from '../stores/session'
import ChannelPane from './ChannelPane.vue'
import ProjectRail from './ProjectRail.vue'
import TaskPane from './TaskPane.vue'

const store = useSession()
const route = useRoute()
const router = useRouter()
const {
  projects,
  groups,
  tasks,
  messages,
  events,
  diff,
  selectedTask,
  auth,
  token,
  notice,
  error,
  loading,
  reviewClaim,
} = storeToRefs(store)

const operator = computed(() => store.operator)

const selectedGroupId = ref<string | null>(null)

function param(name: 'groupId' | 'taskId'): string | null {
  const value = route.params[name]
  return typeof value === 'string' && value.length > 0 ? value : null
}

let loadedGroup: string | null = null
let loadedTask: string | null = null

async function sync(): Promise<void> {
  const groupId = param('groupId')
  const taskId = param('taskId')
  selectedGroupId.value = groupId
  if (groupId !== null && groupId !== loadedGroup) {
    await store.selectGroup(groupId)
    loadedGroup = groupId
    loadedTask = null
  }
  if (taskId !== null && taskId !== loadedTask) {
    await store.openTask(taskId)
    loadedTask = taskId
  }
}

onMounted(async () => {
  // 令牌先于一切请求恢复（enforce 下第一笔加载就要带上）。
  store.initAuth()
  await store.loadAuth()
  await store.loadRail()
  if (param('groupId') === null && store.firstGroupId !== null) {
    await router.replace(`/g/${store.firstGroupId}`)
    return
  }
  await sync()
})

watch(
  () => route.fullPath,
  () => {
    void sync()
  },
)

function onSelectGroup(groupId: string): void {
  void router.push(`/g/${groupId}`)
}

function onSelectTask(taskId: string): void {
  const groupId = param('groupId')
  if (groupId !== null) {
    void router.push(`/g/${groupId}/t/${taskId}`)
  }
}

async function onSend(body: string): Promise<void> {
  const groupId = param('groupId')
  if (groupId !== null) {
    await store.sendMessage(groupId, body)
  }
}

function onExpandDiff(): void {
  void store.expandDiff()
}

// ---- 管理面与决策面的接线（human-console 卡）----

async function onCreateProject(fields: {
  project_id: string
  name: string
}): Promise<boolean> {
  return store.createProject(fields)
}

async function onCreateGroup(
  projectId: string,
  fields: { group_id: string; name: string },
): Promise<boolean> {
  const groupId = await store.createGroup(projectId, fields)
  if (groupId === null) {
    return false
  }
  await router.push(`/g/${groupId}`)
  return true
}

async function onCreatePlan(fields: {
  plan_id: string
  snapshot: Record<string, unknown>
}): Promise<boolean> {
  return store.createPlan(fields)
}

async function onDecidePlan(
  planId: string,
  approve: boolean,
  fields: { decided_by: string; reason?: string },
): Promise<boolean> {
  return store.decidePlan(planId, approve, fields)
}

async function onHumanAct(action: HumanAction): Promise<boolean> {
  return store.humanAct(action)
}

async function onSetToken(raw: string): Promise<boolean> {
  return store.setToken(raw)
}

function onClearToken(): void {
  store.clearToken()
}
</script>

<template>
  <div class="shell">
    <p v-if="notice" class="banner" role="status" aria-live="polite">{{ notice }}</p>
    <ProjectRail
      :projects="projects"
      :groups="groups"
      :selected-group-id="selectedGroupId"
      :busy="loading.act === true"
      :auth="auth"
      :has-token="token !== null"
      :create-project="onCreateProject"
      :create-group="onCreateGroup"
      :set-token="onSetToken"
      :clear-token="onClearToken"
      @select="onSelectGroup"
    />
    <ChannelPane
      :group-id="selectedGroupId"
      :messages="messages"
      :loading="loading.group === true"
      :error="error"
      :busy="loading.act === true"
      :operator="operator"
      :create-plan="onCreatePlan"
      :decide-plan="onDecidePlan"
      @send="onSend"
    />
    <TaskPane
      :tasks="tasks"
      :selected-task="selectedTask"
      :events="events"
      :diff="diff"
      :diff-loading="loading.diff === true || loading.diffPatch === true"
      :loading="loading.task === true || loading.group === true"
      :error="error"
      :busy="loading.act === true"
      :operator="operator"
      :human-act="onHumanAct"
      :review-claim="reviewClaim"
      @select="onSelectTask"
      @expand-diff="onExpandDiff"
    />
  </div>
</template>
