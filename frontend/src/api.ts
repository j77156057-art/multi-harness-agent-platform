// 前端唯一的数据出入口：/api/v1。R10 §22 —— 前端不能直接改权威字段，
// 所有操作都经本模块走 HTTP；错误按后端的统一信封解析。

export const API_PREFIX = '/api/v1'

// 主体令牌（frontend-token 卡）：模块级持有者，store 负责从 localStorage 装载/
// 清除；所有请求在有令牌时带 Authorization: Bearer。故意不放 localStorage 读取
// 逻辑在这里——持久化归 store，api 只管"发请求时带什么"。
let authToken: string | null = null

export function setApiToken(token: string | null): void {
  authToken = token
}

export function getApiToken(): string | null {
  return authToken
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (authToken !== null) {
    headers.Authorization = `Bearer ${authToken}`
  }
  const response = await fetch(API_PREFIX + path, { headers, ...init })
  if (!response.ok) {
    let code = `http.${response.status}`
    let message = response.statusText
    try {
      const body: unknown = await response.json()
      const error = (body as { error?: { code?: unknown; message?: unknown } }).error
      if (error) {
        code = String(error.code ?? code)
        message = String(error.message ?? message)
      }
    } catch {
      // 非 JSON 错误体：保留状态码兜底。
    }
    throw new ApiError(response.status, code, message)
  }
  return (await response.json()) as T
}

export interface ProjectSummary {
  project_id: string
  name: string
  created_at: string
}

export interface GroupSummary {
  group_id: string
  project_id: string
  name: string
  created_at: string
}

export interface TaskSummary {
  task_id: string
  group_id: string
  status: string
  owner: string | null
  current_attempt_id: string | null
  current_epoch: number
  waiting_since: string | null
  escalated_at: string | null
  // R10 §8 budget-override reads: the release control needs to know whether
  // the task has spent its attempts and whether a human already overrode it.
  attempt_no?: number
  max_attempts?: number
  budget_override_by?: string | null
  [key: string]: unknown
}

export interface EventRecord {
  seq: number
  event_name: string
  aggregate_type: string
  aggregate_id: string
  actor_type: string
  actor_id: string
  payload: Record<string, unknown>
  schema_version: number
  created_at: string
}

export interface MessageRecord {
  message_id: string
  group_id: string
  task_id: string | null
  sender_type: string
  sender_id: string
  body: string
  refs: string[]
  created_seq: number
  created_at: string
}

export interface NewMessage {
  author_kind: string
  author_id: string
  body: string
}

// 决策动词的统一回执（/human/* 五操作 + resume/reopen 共享形状）。
export interface HumanOutcome {
  task_id: string
  event: string
  seq: number
  status: string
}

export interface ReviewClaim {
  review_id: string
  reviewed_commit: string
  lease_until: string
  reused: boolean
}

export interface ReviewOutcome {
  review_id: string
  task_id: string
  verdict: string
  event: string
  seq: number
  replayed?: boolean
}

export interface PlanCreated {
  plan_id: string
  snapshot_id: string
  plan_hash: string
}

export interface PlanApproved {
  plan_id: string
  ready: string[]
  waiting: string[]
}

export interface PlanRejected {
  plan_id: string
  rejected: string[]
}

export interface DiffFile {
  path: string
  insertions: number | null
  deletions: number | null
  binary: boolean
}

// 任务线程的 diff 读数（后端按需从 canonical 仓计算；diff-message 卡）。
// available=false 时 files 为空、reason 说明原因——不发明空 diff。
export interface TaskDiff {
  task_id: string
  attempt_id: string | null
  available: boolean
  reason: string | null
  base_commit: string | null
  head_commit: string | null
  branch: string | null
  files: DiffFile[]
  files_changed: number
  insertions: number
  deletions: number
  patch: string | null
  truncated: boolean
}

interface EventPage {
  events: EventRecord[]
  next_seq: number
  has_more: boolean
}

// 只读自省（frontend-token 卡）：off / 已认证 / 需要令牌 / 令牌无效或吊销。
export interface WhoAmI {
  mode: string
  subject: { type: string; id: string } | null
  token_reason: string | null
}

export const api = {
  whoami(): Promise<WhoAmI> {
    return request('/auth/whoami')
  },
  listProjects(): Promise<{ projects: ProjectSummary[] }> {
    return request('/projects')
  },
  listGroups(projectId?: string): Promise<{ groups: GroupSummary[] }> {
    const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''
    return request(`/groups${query}`)
  },
  listTasks(groupId: string, status?: string): Promise<{ tasks: TaskSummary[] }> {
    const query = status ? `?status=${encodeURIComponent(status)}` : ''
    return request(`/groups/${encodeURIComponent(groupId)}/tasks${query}`)
  },
  getTask(taskId: string): Promise<TaskSummary> {
    return request(`/tasks/${encodeURIComponent(taskId)}`)
  },
  taskEvents(taskId: string, sinceSeq = 0): Promise<EventPage> {
    return request(`/tasks/${encodeURIComponent(taskId)}/events?since_seq=${sinceSeq}`)
  },
  taskDiff(
    taskId: string,
    options: { attemptId?: string; includePatch?: boolean } = {},
  ): Promise<TaskDiff> {
    const params = new URLSearchParams()
    if (options.attemptId !== undefined) {
      params.set('attempt_id', options.attemptId)
    }
    if (options.includePatch === true) {
      params.set('include_patch', '1')
    }
    const query = params.size > 0 ? `?${params.toString()}` : ''
    return request(`/tasks/${encodeURIComponent(taskId)}/diff${query}`)
  },
  listMessages(groupId: string, sinceSeq = 0): Promise<{ messages: MessageRecord[] }> {
    return request(`/groups/${encodeURIComponent(groupId)}/messages?since_seq=${sinceSeq}`)
  },
  postMessage(groupId: string, message: NewMessage): Promise<MessageRecord> {
    return request(`/groups/${encodeURIComponent(groupId)}/messages`, {
      method: 'POST',
      body: JSON.stringify(message),
    })
  },

  // ---- 管理面（management 路由；human-console 卡）----
  createProject(body: { project_id: string; name: string }): Promise<{ project_id: string }> {
    return request('/projects', { method: 'POST', body: JSON.stringify(body) })
  },
  createGroup(
    projectId: string,
    body: { group_id: string; name: string },
  ): Promise<{ group_id: string }> {
    return request(`/projects/${encodeURIComponent(projectId)}/groups`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },
  createPlan(
    groupId: string,
    body: { plan_id: string; snapshot: Record<string, unknown> },
  ): Promise<PlanCreated> {
    return request(`/groups/${encodeURIComponent(groupId)}/plans`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },
  approvePlan(
    planId: string,
    body: { decided_by: string; reason?: string },
  ): Promise<PlanApproved> {
    return request(`/plans/${encodeURIComponent(planId)}/approve`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },
  rejectPlan(
    planId: string,
    body: { decided_by: string; reason?: string },
  ): Promise<PlanRejected> {
    return request(`/plans/${encodeURIComponent(planId)}/reject`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },

  // ---- §18 人工决策面（human 路由）----
  humanResume(
    taskId: string,
    body: { decided_by: string; answer?: string },
  ): Promise<HumanOutcome> {
    return request('/human/resume', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId, ...body }),
    })
  },
  humanReopen(
    taskId: string,
    body: { decided_by: string; answer?: string },
  ): Promise<HumanOutcome> {
    return request('/human/reopen', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId, ...body }),
    })
  },
  humanRelease(
    taskId: string,
    body: { decided_by: string; budget_override?: boolean; reason?: string },
  ): Promise<HumanOutcome> {
    return request('/human/release', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId, ...body }),
    })
  },
  humanAdjudicate(
    taskId: string,
    body: { decided_by: string; question: string; options?: string[] },
  ): Promise<{ task_id: string; decision_id: string; seq: number }> {
    return request('/human/adjudicate', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId, ...body }),
    })
  },
  humanCancel(taskId: string, body: { decided_by: string; reason?: string }): Promise<HumanOutcome> {
    return request('/human/cancel', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId, ...body }),
    })
  },
  humanReviewClaim(
    taskId: string,
    attemptId: string,
    body: { decided_by: string; review_id?: string },
  ): Promise<ReviewClaim> {
    return request('/human/review/claim', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId, attempt_id: attemptId, ...body }),
    })
  },
  humanReviewSubmit(
    taskId: string,
    attemptId: string,
    body: {
      decided_by: string
      review_id: string
      verdict: 'approve' | 'request_changes'
      reviewed_commit: string
      findings?: Record<string, unknown>[]
    },
  ): Promise<ReviewOutcome> {
    return request('/human/review/submit', {
      method: 'POST',
      body: JSON.stringify({ task_id: taskId, attempt_id: attemptId, ...body }),
    })
  },
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.code}: ${error.message}`
  }
  return error instanceof Error ? error.message : String(error)
}
