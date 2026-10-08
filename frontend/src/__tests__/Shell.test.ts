import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { describe, expect, it, vi, afterEach } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { routes } from '../router'
import Shell from '../components/Shell.vue'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const ROUTES: Record<string, unknown> = {
  '/api/v1/projects': {
    projects: [{ project_id: 'P-1', name: '项目一', created_at: '2026-10-07T00:00:00+00:00' }],
  },
  '/api/v1/groups': {
    groups: [
      {
        group_id: 'G-1',
        project_id: 'P-1',
        name: '群一',
        created_at: '2026-10-07T00:00:00+00:00',
      },
    ],
  },
  '/api/v1/groups/G-1/tasks': {
    tasks: [
      {
        task_id: 'T-1',
        group_id: 'G-1',
        status: 'in_progress',
        owner: 'codex',
        current_attempt_id: 'T-1.a1',
        current_epoch: 1,
        waiting_since: null,
        escalated_at: null,
      },
    ],
  },
  '/api/v1/groups/G-1/messages': {
    messages: [
      {
        message_id: 'm-1',
        group_id: 'G-1',
        task_id: null,
        sender_type: 'human',
        sender_id: 'operator',
        body: '大家好',
        refs: [],
        created_seq: 11,
        created_at: '2026-10-07T12:00:00+00:00',
      },
    ],
  },
  '/api/v1/tasks/T-1': {
    task_id: 'T-1',
    group_id: 'G-1',
    status: 'in_progress',
    owner: 'codex',
    current_attempt_id: 'T-1.a1',
    current_epoch: 1,
    waiting_since: null,
    escalated_at: null,
  },
  '/api/v1/tasks/T-1/diff': {
    task_id: 'T-1',
    attempt_id: 'T-1.a1',
    available: true,
    reason: null,
    base_commit: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    head_commit: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
    branch: 'agent/g1/codex/T-1/a1',
    files: [{ path: 'src/app.py', insertions: 3, deletions: 1, binary: false }],
    files_changed: 1,
    insertions: 3,
    deletions: 1,
    patch: null,
    truncated: false,
  },
  '/api/v1/tasks/T-1/events': {
    events: [
      {
        seq: 4,
        event_name: 'claim',
        aggregate_type: 'task',
        aggregate_id: 'T-1',
        actor_type: 'harness',
        actor_id: 'codex',
        payload: { attempt_id: 'T-1.a1' },
        schema_version: 1,
        created_at: '2026-10-07T12:00:00+00:00',
      },
    ],
    next_seq: 4,
    has_more: false,
  },
  // T-2：awaiting_human 任务 + 一次真实的人工决策回执（human-console 卡）。
  '/api/v1/tasks/T-2': {
    task_id: 'T-2',
    group_id: 'G-1',
    status: 'awaiting_human',
    owner: 'codex',
    current_attempt_id: 'T-2.a1',
    current_epoch: 1,
    waiting_since: '2026-10-07T12:00:00+00:00',
    escalated_at: null,
  },
  '/api/v1/tasks/T-2/events': {
    events: [
      {
        seq: 9,
        event_name: 'human.question',
        aggregate_type: 'task',
        aggregate_id: 'T-2',
        actor_type: 'harness',
        actor_id: 'codex',
        payload: { decision_id: 'dec-1', question: '契约要冻结吗？', scope: 'task' },
        schema_version: 1,
        created_at: '2026-10-07T12:00:00+00:00',
      },
    ],
    next_seq: 9,
    has_more: false,
  },
  '/api/v1/tasks/T-2/diff': {
    task_id: 'T-2',
    attempt_id: 'T-2.a1',
    available: false,
    reason: 'no attempt has published a commit',
    base_commit: null,
    head_commit: null,
    branch: null,
    files: [],
    files_changed: 0,
    insertions: 0,
    deletions: 0,
    patch: null,
    truncated: false,
  },
  '/api/v1/human/resume': {
    task_id: 'T-2',
    event: 'human.resume',
    seq: 42,
    status: 'in_progress',
  },
}

// 前缀匹配按 key 长度降序，避免 /groups 抢在 /groups/G-1/tasks 前面。
const KEYS = Object.keys(ROUTES).sort((a, b) => b.length - a.length)

function stubFetch() {
  const mock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    const url = String(input)
    for (const key of KEYS) {
      if (url.startsWith(key)) {
        return jsonResponse(200, ROUTES[key])
      }
    }
    return jsonResponse(404, { error: { code: 'resource.not_found', message: url } })
  })
  vi.stubGlobal('fetch', mock)
  return mock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

async function mountShell(initialPath: string) {
  const mock = stubFetch()
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(initialPath)
  await router.isReady()
  const wrapper = mount(Shell, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, router, mock }
}

describe('Shell (route → store → three panes)', () => {
  it('lands on the first group and loads channel, tasks and rail', async () => {
    const { wrapper, router } = await mountShell('/')

    expect(router.currentRoute.value.fullPath).toBe('/g/G-1')
    expect(wrapper.get('.rail-group.active').text()).toContain('G-1')
    expect(wrapper.text()).toContain('大家好')
    expect(wrapper.get('.task-row').text()).toContain('T-1')
  })

  it('deep-links a task thread', async () => {
    const { wrapper } = await mountShell('/g/G-1/t/T-1')

    expect(wrapper.text()).toContain('线程 · T-1')
    expect(wrapper.text()).toContain('claim')
    expect(wrapper.get('.diff-card').text()).toContain('src/app.py')
  })

  it('human decision: resume posts the action and shows the notice banner', async () => {
    const { wrapper, mock } = await mountShell('/g/G-1/t/T-2')

    expect(wrapper.get('.decision-question').text()).toContain('契约要冻结吗？')
    const resume = wrapper
      .findAll('.decision-actions button')
      .find((button) => button.text() === '恢复工作')
    expect(resume).toBeDefined()

    await resume?.trigger('click')
    await flushPromises()
    await flushPromises()
    await flushPromises()

    const call = mock.mock.calls.find(([input]) => String(input) === '/api/v1/human/resume')
    expect(call).toBeDefined()
    expect(call?.[1]?.method).toBe('POST')
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      task_id: 'T-2',
      decided_by: 'operator',
    })
    expect(wrapper.get('.banner').text()).toContain('已恢复 T-2')
  })
})
