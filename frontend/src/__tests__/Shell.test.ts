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
}

// 前缀匹配按 key 长度降序，避免 /groups 抢在 /groups/G-1/tasks 前面。
const KEYS = Object.keys(ROUTES).sort((a, b) => b.length - a.length)

function stubFetch() {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
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
  stubFetch()
  const router = createRouter({ history: createMemoryHistory(), routes })
  await router.push(initialPath)
  await router.isReady()
  const wrapper = mount(Shell, { global: { plugins: [createPinia(), router] } })
  await flushPromises()
  await flushPromises()
  return { wrapper, router }
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
  })
})
