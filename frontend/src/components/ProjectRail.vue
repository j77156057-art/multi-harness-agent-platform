<script setup lang="ts">
import type { GroupSummary, ProjectSummary } from '../api'

const props = defineProps<{
  projects: ProjectSummary[]
  groups: GroupSummary[]
  selectedGroupId: string | null
}>()

const emit = defineEmits<{ select: [groupId: string] }>()

function groupsOf(projectId: string): GroupSummary[] {
  return props.groups.filter((group) => group.project_id === projectId)
}
</script>

<template>
  <aside class="pane rail" aria-label="项目与群组">
    <p v-if="projects.length === 0" class="empty">还没有项目。用 CLI 或 API 创建后刷新。</p>
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
      </section>
    </nav>
    <p class="rail-note">Harness 成员（下一片）：需要一个成员列表端点。</p>
  </aside>
</template>
