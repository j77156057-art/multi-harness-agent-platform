<script setup lang="ts">
// 三栏壳：同时挂在 / 、/g/:groupId、/g/:groupId/t/:taskId 三条路由上（组件跨导航
// 复用）。路由只承载"选中状态"，数据编排全走 session store。
import { storeToRefs } from 'pinia'
import { onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSession } from '../stores/session'
import ChannelPane from './ChannelPane.vue'
import ProjectRail from './ProjectRail.vue'
import TaskPane from './TaskPane.vue'

const store = useSession()
const route = useRoute()
const router = useRouter()
const { projects, groups, tasks, messages, events, selectedTask, error, loading } =
  storeToRefs(store)

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
</script>

<template>
  <div class="shell">
    <ProjectRail
      :projects="projects"
      :groups="groups"
      :selected-group-id="selectedGroupId"
      @select="onSelectGroup"
    />
    <ChannelPane
      :group-id="selectedGroupId"
      :messages="messages"
      :loading="loading.group === true"
      :error="error"
      @send="onSend"
    />
    <TaskPane
      :tasks="tasks"
      :selected-task="selectedTask"
      :events="events"
      :loading="loading.task === true || loading.group === true"
      :error="error"
      @select="onSelectTask"
    />
  </div>
</template>
