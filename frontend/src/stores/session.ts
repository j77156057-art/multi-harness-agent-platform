// 会话状态：左栏（项目/群组）、中栏（群聊）、右栏（任务 + 线程）的加载与选择，
// 以及管理面/人工决策面的动作编排。组件保持纯渲染，编排全部落在这里，vitest
// 可以直接测 store 而不必挂 DOM。
import { defineStore } from 'pinia'
import {
  api,
  describeError,
  setApiToken,
  type EventRecord,
  type GroupSummary,
  type MessageRecord,
  type ProjectSummary,
  type TaskDiff,
  type TaskSummary,
  type WhoAmI,
} from '../api'

//: 令牌持久化键（frontend-token 卡）：只存本地，不上送任何日志。
const TOKEN_STORAGE_KEY = 'ap.humanToken'

export type HumanAction =
  | { kind: 'resume'; taskId: string; decided_by: string; answer?: string }
  | { kind: 'reopen'; taskId: string; decided_by: string; answer?: string }
  | { kind: 'release'; taskId: string; decided_by: string }
  | {
      kind: 'adjudicate'
      taskId: string
      decided_by: string
      question: string
      options?: string[]
    }
  | { kind: 'cancel'; taskId: string; decided_by: string; reason?: string }

interface SessionState {
  projects: ProjectSummary[]
  groups: GroupSummary[]
  tasks: TaskSummary[]
  messages: MessageRecord[]
  events: EventRecord[]
  diff: TaskDiff | null
  selectedTask: TaskSummary | null
  selectedGroupId: string | null
  // 主体令牌与认证自省（frontend-token 卡）：enforce 下请求带 Bearer；
  // off 下 whoami 报 mode=off，界面据此显示"认证：关闭"。
  token: string | null
  auth: WhoAmI | null
  // 动作结果的成功面（notice）与失败面（error）分开：动作成功不该看起来像没事
  // 发生，动作失败也不该只靠列表里少了一行。
  notice: string | null
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
    selectedGroupId: null,
    token: null,
    auth: null,
    notice: null,
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
    // 表单的操作者默认值：认到主体就用主体，认不到退回 operator（off 模式）。
    operator(state): string {
      return state.auth?.subject?.id ?? 'operator'
    },
  },
  actions: {
    // ---- 令牌（frontend-token 卡）----
    initAuth(): void {
      let stored: string | null = null
      try {
        stored = localStorage.getItem(TOKEN_STORAGE_KEY)
      } catch {
        // localStorage 不可用（隐私模式等）：令牌只在内存里，不报错。
      }
      this.token = stored
      setApiToken(stored)
    },
    async loadAuth(): Promise<void> {
      this.loading.auth = true
      try {
        this.auth = await api.whoami()
      } catch (error) {
        this.error = describeError(error)
        this.auth = null
      } finally {
        this.loading.auth = false
      }
    },
    async setToken(raw: string): Promise<boolean> {
      const trimmed = raw.trim()
      this.token = trimmed.length > 0 ? trimmed : null
      try {
        if (this.token === null) {
          localStorage.removeItem(TOKEN_STORAGE_KEY)
        } else {
          localStorage.setItem(TOKEN_STORAGE_KEY, this.token)
        }
      } catch {
        // 见 initAuth：持久化失败不阻断本次会话使用。
      }
      setApiToken(this.token)
      await this.loadAuth()
      return this.auth?.subject != null
    },
    clearToken(): void {
      this.token = null
      try {
        localStorage.removeItem(TOKEN_STORAGE_KEY)
      } catch {
        // 同上。
      }
      setApiToken(null)
      void this.loadAuth()
    },
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
      this.selectedGroupId = groupId
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
    // 只刷新当前群组的任务列表：动作改的是任务状态，不该把选中线程/群聊重置。
    async refreshTasks(): Promise<void> {
      const groupId = this.selectedGroupId
      if (groupId === null) {
        return
      }
      try {
        const { tasks } = await api.listTasks(groupId)
        if (this.selectedGroupId === groupId) {
          this.tasks = tasks
        }
      } catch (error) {
        this.error = describeError(error)
      }
    },
    async createProject(fields: { project_id: string; name: string }): Promise<boolean> {
      this.error = null
      this.notice = null
      this.loading.act = true
      try {
        await api.createProject(fields)
        await this.loadRail()
        this.notice = `已创建项目 ${fields.project_id}`
        return true
      } catch (error) {
        this.error = describeError(error)
        return false
      } finally {
        this.loading.act = false
      }
    },
    async createGroup(
      projectId: string,
      fields: { group_id: string; name: string },
    ): Promise<string | null> {
      this.error = null
      this.notice = null
      this.loading.act = true
      try {
        await api.createGroup(projectId, fields)
        await this.loadRail()
        this.notice = `已创建群组 ${fields.group_id}`
        return fields.group_id
      } catch (error) {
        this.error = describeError(error)
        return null
      } finally {
        this.loading.act = false
      }
    },
    async createPlan(fields: {
      plan_id: string
      snapshot: Record<string, unknown>
    }): Promise<boolean> {
      const groupId = this.selectedGroupId
      if (groupId === null) {
        this.error = '先选择一个群组，计划属于群组'
        return false
      }
      this.error = null
      this.notice = null
      this.loading.act = true
      try {
        const created = await api.createPlan(groupId, fields)
        this.notice = `已创建计划 ${created.plan_id}（draft；审批通过后才生成 ready 任务）`
        return true
      } catch (error) {
        this.error = describeError(error)
        return false
      } finally {
        this.loading.act = false
      }
    },
    async decidePlan(
      planId: string,
      approve: boolean,
      fields: { decided_by: string; reason?: string },
    ): Promise<boolean> {
      this.error = null
      this.notice = null
      this.loading.act = true
      try {
        if (approve) {
          const outcome = await api.approvePlan(planId, fields)
          this.notice =
            `计划 ${outcome.plan_id} 已批：${outcome.ready.length} 个 ready、` +
            `${outcome.waiting.length} 个等待中`
        } else {
          const outcome = await api.rejectPlan(planId, fields)
          this.notice = `计划 ${outcome.plan_id} 已驳回：${outcome.rejected.length} 个 draft 任务被拒`
        }
        await this.refreshTasks()
        return true
      } catch (error) {
        this.error = describeError(error)
        return false
      } finally {
        this.loading.act = false
      }
    },
    // §18 五决策动词：动作合法性由后端守卫状态裁决（前端只按状态给按钮），
    // 成功即刷新线程与任务列表，让事件流与状态徽章当场一致。
    async humanAct(action: HumanAction): Promise<boolean> {
      this.error = null
      this.notice = null
      this.loading.act = true
      try {
        switch (action.kind) {
          case 'resume': {
            const outcome = await api.humanResume(action.taskId, {
              decided_by: action.decided_by,
              ...(action.answer ? { answer: action.answer } : {}),
            })
            this.notice = `已恢复 ${outcome.task_id} → ${outcome.status}`
            break
          }
          case 'reopen': {
            const outcome = await api.humanReopen(action.taskId, {
              decided_by: action.decided_by,
              ...(action.answer ? { answer: action.answer } : {}),
            })
            this.notice = `已重开 ${outcome.task_id} → ${outcome.status}`
            break
          }
          case 'release': {
            const outcome = await api.humanRelease(action.taskId, {
              decided_by: action.decided_by,
            })
            this.notice = `已释放 ${outcome.task_id} → ${outcome.status}`
            break
          }
          case 'adjudicate': {
            const outcome = await api.humanAdjudicate(action.taskId, {
              decided_by: action.decided_by,
              question: action.question,
              ...(action.options !== undefined ? { options: action.options } : {}),
            })
            this.notice = `已裁决 ${outcome.task_id}（decision ${outcome.decision_id}）`
            break
          }
          case 'cancel': {
            const outcome = await api.humanCancel(action.taskId, {
              decided_by: action.decided_by,
              ...(action.reason ? { reason: action.reason } : {}),
            })
            this.notice = `已取消 ${outcome.task_id} → ${outcome.status}`
            break
          }
        }
        await this.openTask(action.taskId)
        await this.refreshTasks()
        return true
      } catch (error) {
        this.error = describeError(error)
        return false
      } finally {
        this.loading.act = false
      }
    },
  },
})
