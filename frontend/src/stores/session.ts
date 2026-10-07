// 会话状态：左栏（项目/群组）、中栏（群聊）、右栏（任务 + 线程）的加载与选择。
// 组件保持纯渲染，编排全部落在这里，vitest 可以直接测 store 而不必挂 DOM。
import { defineStore } from 'pinia'
import {
  api,
  describeError,
  type EventRecord,
  type GroupSummary,
  type MessageRecord,
  type ProjectSummary,
  type TaskDiff,
  type TaskSummary,
} from '../api'

interface SessionState {
  projects: ProjectSummary[]
  groups: GroupSummary[]
  tasks: TaskSummary[]
  messages: MessageRecord[]
  events: EventRecord[]
  diff: TaskDiff | null
  selectedTask: TaskSummary | null
  loading: Record<string, boolean>
  error: string | null
}

export const useSession = defineStore('session', {
  state: (): SessionState => ({
    projects: [],
    groups: [],
    tasks: [],
    messages: [],
    events: [],
    diff: null,
    selectedTask: null,
    loading: {},
    error: null,
  }),
  getters: {
    groupsOf(state) {
      return (projectId: string): GroupSummary[] =>
        state.groups.filter((group) => group.project_id === projectId)
    },
    firstGroupId(state): string | null {
      return state.groups.length > 0 ? (state.groups[0]?.group_id ?? null) : null
    },
  },
  actions: {
    async loadRail(): Promise<void> {
      this.error = null
      this.loading.rail = true
      try {
        const [projects, groups] = await Promise.all([api.listProjects(), api.listGroups()])
        this.projects = projects.projects
        this.groups = groups.groups
      } catch (error) {
        this.error = describeError(error)
      } finally {
        this.loading.rail = false
      }
    },
    async selectGroup(groupId: string): Promise<void> {
      this.error = null
      this.selectedTask = null
      this.events = []
      this.diff = null
      this.loading.group = true
      try {
        const [tasks, messages] = await Promise.all([
          api.listTasks(groupId),
          api.listMessages(groupId),
        ])
        this.tasks = tasks.tasks
        this.messages = messages.messages
      } catch (error) {
        this.error = describeError(error)
      } finally {
        this.loading.group = false
      }
    },
    async openTask(taskId: string): Promise<void> {
      this.error = null
      this.loading.task = true
      this.diff = null
      try {
        const [task, page] = await Promise.all([api.getTask(taskId), api.taskEvents(taskId)])
        this.selectedTask = task
        this.events = page.events
      } catch (error) {
        this.error = describeError(error)
      } finally {
        this.loading.task = false
      }
      // diff 是线程的一部分（diff-message 卡）：随打开一起取。取不到不拖垮
      // 线程本身——错误走共享 error 面；迟到的响应属于别的任务时丢弃。
      this.loading.diff = true
      try {
        const diff = await api.taskDiff(taskId)
        if (this.selectedTask?.task_id === taskId) {
          this.diff = diff
        }
      } catch (error) {
        this.error = describeError(error)
      } finally {
        this.loading.diff = false
      }
    },
    async expandDiff(): Promise<void> {
      const taskId = this.selectedTask?.task_id
      if (taskId === undefined || this.loading.diffPatch === true) {
        return
      }
      this.loading.diffPatch = true
      try {
        const diff = await api.taskDiff(taskId, {
          includePatch: true,
          ...(this.diff?.attempt_id != null ? { attemptId: this.diff.attempt_id } : {}),
        })
        if (this.selectedTask?.task_id === taskId) {
          this.diff = diff
        }
      } catch (error) {
        this.error = describeError(error)
      } finally {
        this.loading.diffPatch = false
      }
    },
    async sendMessage(groupId: string, body: string): Promise<boolean> {
      this.error = null
      try {
        await api.postMessage(groupId, {
          author_kind: 'human',
          author_id: 'operator',
          body,
        })
        const { messages } = await api.listMessages(groupId)
        this.messages = messages
        return true
      } catch (error) {
        this.error = describeError(error)
        return false
      }
    },
  },
})
