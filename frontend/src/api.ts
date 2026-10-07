// 前端唯一的数据出入口：/api/v1。R10 §22 —— 前端不能直接改权威字段，
// 所有操作都经本模块走 HTTP；错误按后端的统一信封解析。

export const API_PREFIX = '/api/v1'

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
  const response = await fetch(API_PREFIX + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
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

interface EventPage {
  events: EventRecord[]
  next_seq: number
  has_more: boolean
}

export const api = {
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
  listMessages(groupId: string, sinceSeq = 0): Promise<{ messages: MessageRecord[] }> {
    return request(`/groups/${encodeURIComponent(groupId)}/messages?since_seq=${sinceSeq}`)
  },
  postMessage(groupId: string, message: NewMessage): Promise<MessageRecord> {
    return request(`/groups/${encodeURIComponent(groupId)}/messages`, {
      method: 'POST',
      body: JSON.stringify(message),
    })
  },
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.code}: ${error.message}`
  }
  return error instanceof Error ? error.message : String(error)
}
