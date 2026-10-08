<script setup lang="ts">
// 左栏（R10 §22「项目/群组」）：列表 + 管理面的创建表单（human-console 卡）
// + 认证面板（frontend-token 卡）：令牌只存本机，状态由 whoami 自省驱动。
// 表单成功才清空并收起；失败保留输入，错误走 store 的 error 面。
import { computed, ref } from 'vue'
import type { GroupSummary, ProjectSummary, WhoAmI } from '../api'

const props = defineProps<{
  projects: ProjectSummary[]
  groups: GroupSummary[]
  selectedGroupId: string | null
  busy: boolean
  auth: WhoAmI | null
  hasToken: boolean
  createProject: (fields: { project_id: string; name: string }) => Promise<boolean>
  createGroup: (projectId: string, fields: { group_id: string; name: string }) => Promise<boolean>
  setToken: (raw: string) => Promise<boolean>
  clearToken: () => void
}>()

const emit = defineEmits<{ select: [groupId: string] }>()

function groupsOf(projectId: string): GroupSummary[] {
  return props.groups.filter((group) => group.project_id === projectId)
}

const projectOpen = ref(false)
const newProjectId = ref('')
const newProjectName = ref('')
const projectValid = computed(
  () => newProjectId.value.trim().length > 0 && newProjectName.value.trim().length > 0,
)

async function submitProject(): Promise<void> {
  if (!projectValid.value || props.busy) return
  const ok = await props.createProject({
    project_id: newProjectId.value.trim(),
    name: newProjectName.value.trim(),
  })
  if (ok) {
    newProjectId.value = ''
    newProjectName.value = ''
    projectOpen.value = false
  }
}

const groupFormFor = ref<string | null>(null)
const newGroupId = ref('')
const newGroupName = ref('')
const groupValid = computed(
  () => newGroupId.value.trim().length > 0 && newGroupName.value.trim().length > 0,
)

function toggleGroupForm(projectId: string): void {
  groupFormFor.value = groupFormFor.value === projectId ? null : projectId
  newGroupId.value = ''
  newGroupName.value = ''
}

async function submitGroup(projectId: string): Promise<void> {
  if (!groupValid.value || props.busy) return
  const ok = await props.createGroup(projectId, {
    group_id: newGroupId.value.trim(),
    name: newGroupName.value.trim(),
  })
  if (ok) {
    newGroupId.value = ''
    newGroupName.value = ''
    groupFormFor.value = null
  }
}

// ---- 认证面板（frontend-token 卡）----
const tokenDraft = ref('')

const AUTH_LABEL: Record<string, string> = {
  token_missing: '需要令牌',
  token_invalid: '令牌无效',
  token_revoked: '令牌已吊销',
}

const authLabel = computed(() => {
  const auth = props.auth
  if (auth === null) return '认证：未知'
  if (auth.subject !== null) return `已认证为 ${auth.subject.type}:${auth.subject.id}`
  if (auth.token_reason !== null) return AUTH_LABEL[auth.token_reason] ?? '令牌不可用'
  return auth.mode === 'off' ? '认证：关闭（AP_AUTH_MODE=off）' : '需要令牌'
})

const authTone = computed(() => {
  const auth = props.auth
  if (auth?.subject != null) return 'tone-ok'
  if (auth?.token_reason != null) return 'tone-warn'
  return 'tone-muted'
})

async function submitToken(): Promise<void> {
  const raw = tokenDraft.value.trim()
  if (raw.length === 0 || props.busy) return
  const ok = await props.setToken(raw)
  if (ok) {
    tokenDraft.value = ''
  }
}
</script>

<template>
  <aside class="pane rail" aria-label="项目与群组">
    <p v-if="projects.length === 0" class="empty">还没有项目——用下面的表单建一个。</p>
    <nav v-else class="rail-nav">
      <section v-for="project in projects" :key="project.project_id" class="rail-project">
        <h2 class="rail-heading">{{ project.name }}</h2>
        <ul class="rail-groups">
          <li v-for="group in groupsOf(project.project_id)" :key="group.group_id">
            <button
              type="button"
              class="rail-group"
              :class="{ active: group.group_id === selectedGroupId }"
              :aria-current="group.group_id === selectedGroupId ? 'true' : undefined"
              @click="emit('select', group.group_id)"
            >
              <span class="rail-group-name">{{ group.name }}</span>
              <span class="rail-group-id">{{ group.group_id }}</span>
            </button>
          </li>
        </ul>
        <button
          type="button"
          class="rail-add-group"
          :aria-expanded="groupFormFor === project.project_id"
          @click="toggleGroupForm(project.project_id)"
        >
          ＋ 群组
        </button>
        <form
          v-if="groupFormFor === project.project_id"
          class="rail-form"
          @submit.prevent="submitGroup(project.project_id)"
        >
          <label>
            群组 ID
            <input v-model="newGroupId" name="group_id" placeholder="G-X" />
          </label>
          <label>
            名称
            <input v-model="newGroupName" name="group_name" placeholder="显示名" />
          </label>
          <button type="submit" :disabled="!groupValid || busy">创建群组</button>
        </form>
      </section>
    </nav>

    <button
      type="button"
      class="rail-add-project"
      :aria-expanded="projectOpen"
      @click="projectOpen = !projectOpen"
    >
      ＋ 新建项目
    </button>
    <form v-if="projectOpen" class="rail-form" @submit.prevent="submitProject">
      <label>
        项目 ID
        <input v-model="newProjectId" name="project_id" placeholder="P-X" />
      </label>
      <label>
        名称
        <input v-model="newProjectName" name="project_name" placeholder="显示名" />
      </label>
      <button type="submit" :disabled="!projectValid || busy">创建项目</button>
    </form>

    <section class="auth-panel" aria-label="认证">
      <p class="auth-status" :class="authTone">{{ authLabel }}</p>
      <form class="rail-form" @submit.prevent="submitToken">
        <label>
          令牌（仅存本机）
          <input
            v-model="tokenDraft"
            type="password"
            name="auth_token"
            placeholder="apt_…"
            autocomplete="off"
          />
        </label>
        <button type="submit" :disabled="tokenDraft.trim().length === 0 || busy">应用令牌</button>
      </form>
      <button v-if="hasToken" type="button" class="rail-add-project" @click="clearToken">
        清除令牌
      </button>
    </section>

    <p class="rail-note">Harness 成员（下一片）：需要一个成员列表端点。</p>
  </aside>
</template>
