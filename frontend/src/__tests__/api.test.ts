import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, describeError, setApiToken } from '../api'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  setApiToken(null) // 模块级令牌持有者要归位，测试间不泄漏
})

function stubFetch(...responses: Response[]) {
  const mock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
    const next = responses.shift()
    if (next === undefined) {
      throw new Error('fetch called more times than stubbed')
    }
    return next
  })
  vi.stubGlobal('fetch', mock)
  return mock
}

describe('api client', () => {
  it('builds /api/v1 URLs with encoded ids and optional filters', async () => {
    const mock = stubFetch(jsonResponse(200, { tasks: [] }))

    await api.listTasks('G 1/2', 'blocked')

    const [url, init] = mock.mock.calls[0] ?? []
    expect(url).toBe('/api/v1/groups/G%201%2F2/tasks?status=blocked')
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json' })
  })

  it('reads the error envelope into an ApiError', async () => {
    stubFetch(
      jsonResponse(409, {
        error: { code: 'request.conflict', message: 'task is blocked', retryable: false },
      }),
    )

    const caught = await api.listTasks('G-1').catch((error: unknown) => error)

    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).status).toBe(409)
    expect((caught as ApiError).code).toBe('request.conflict')
    expect((caught as ApiError).message).toBe('task is blocked')
  })

  it('maps a non-JSON error body to the http fallback code', async () => {
    stubFetch(new Response('boom', { status: 500 }))

    const caught = await api.listProjects().catch((error: unknown) => error)

    expect(caught).toBeInstanceOf(ApiError)
    expect((caught as ApiError).status).toBe(500)
    expect((caught as ApiError).code).toBe('http.500')
    expect(describeError(caught)).toContain('http.500')
  })

  it('posts a message with a JSON body', async () => {
    const mock = stubFetch(jsonResponse(200, { message_id: 'm-1' }))

    await api.postMessage('G-1', { author_kind: 'human', author_id: 'operator', body: 'hi' })

    const [url, init] = mock.mock.calls[0] ?? []
    expect(url).toBe('/api/v1/groups/G-1/messages')
    expect(init?.method).toBe('POST')
    expect(JSON.parse(String(init?.body))).toEqual({
      author_kind: 'human',
      author_id: 'operator',
      body: 'hi',
    })
  })

  it('builds diff URLs with attempt and patch flags', async () => {
    const mock = stubFetch(
      jsonResponse(200, { task_id: 'T-1', available: false }),
      jsonResponse(200, { task_id: 'T-1', available: false }),
    )

    await api.taskDiff('T-1')
    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/tasks/T-1/diff')

    await api.taskDiff('T-1', { attemptId: 'T-1.a2', includePatch: true })
    expect(mock.mock.calls[1]?.[0]).toBe(
      '/api/v1/tasks/T-1/diff?attempt_id=T-1.a2&include_patch=1',
    )
  })

  it('fetches a task thread page', async () => {
    const mock = stubFetch(jsonResponse(200, { events: [], next_seq: 7, has_more: false }))

    const page = await api.taskEvents('T-1', 7)

    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/tasks/T-1/events?since_seq=7')
    expect(page.next_seq).toBe(7)
  })

  it('attaches the bearer token only when one is set', async () => {
    const mock = stubFetch(jsonResponse(200, { projects: [] }), jsonResponse(200, { projects: [] }))

    await api.listProjects()
    const [, anonymous] = mock.mock.calls[0] ?? []
    expect((anonymous?.headers as Record<string, string>).Authorization).toBeUndefined()

    setApiToken('apt_token_1')
    await api.listProjects()
    const [, authenticated] = mock.mock.calls[1] ?? []
    expect((authenticated?.headers as Record<string, string>).Authorization).toBe(
      'Bearer apt_token_1',
    )
  })

  it('posts project and group creations on the management paths', async () => {
    const mock = stubFetch(
      jsonResponse(200, { project_id: 'P-2' }),
      jsonResponse(200, { group_id: 'G-2' }),
    )

    await api.createProject({ project_id: 'P 2', name: '项目二' })
    await api.createGroup('P 2', { group_id: 'G-2', name: '群二' })

    const [projectUrl, projectInit] = mock.mock.calls[0] ?? []
    expect(projectUrl).toBe('/api/v1/projects')
    expect(projectInit?.method).toBe('POST')
    expect(JSON.parse(String(projectInit?.body))).toEqual({ project_id: 'P 2', name: '项目二' })

    const [groupUrl, groupInit] = mock.mock.calls[1] ?? []
    expect(groupUrl).toBe('/api/v1/projects/P%202/groups')
    expect(JSON.parse(String(groupInit?.body))).toEqual({ group_id: 'G-2', name: '群二' })
  })

  it('creates a plan with the raw snapshot and decides it by id', async () => {
    const mock = stubFetch(
      jsonResponse(200, { plan_id: 'PLAN-1', snapshot_id: 'snap-1', plan_hash: 'h' }),
      jsonResponse(200, { plan_id: 'PLAN 1', ready: ['T-1'], waiting: [] }),
      jsonResponse(200, { plan_id: 'PLAN 1', rejected: ['T-2'] }),
    )

    await api.createPlan('G-1', {
      plan_id: 'PLAN-1',
      snapshot: { tasks: [{ id: 'T-1' }] },
    })
    await api.approvePlan('PLAN 1', { decided_by: 'operator' })
    await api.rejectPlan('PLAN 1', { decided_by: 'operator', reason: '范围太大' })

    const [createUrl, createInit] = mock.mock.calls[0] ?? []
    expect(createUrl).toBe('/api/v1/groups/G-1/plans')
    expect(JSON.parse(String(createInit?.body))).toEqual({
      plan_id: 'PLAN-1',
      snapshot: { tasks: [{ id: 'T-1' }] },
    })

    expect(mock.mock.calls[1]?.[0]).toBe('/api/v1/plans/PLAN%201/approve')
    expect(JSON.parse(String(mock.mock.calls[1]?.[1]?.body))).toEqual({ decided_by: 'operator' })

    expect(mock.mock.calls[2]?.[0]).toBe('/api/v1/plans/PLAN%201/reject')
    expect(JSON.parse(String(mock.mock.calls[2]?.[1]?.body))).toEqual({
      decided_by: 'operator',
      reason: '范围太大',
    })
  })

  it('posts the five human decisions with task_id merged into the body', async () => {
    const mock = stubFetch(
      jsonResponse(200, { task_id: 'T-1', event: 'human.resume', seq: 1, status: 'in_progress' }),
      jsonResponse(200, { task_id: 'T-1', event: 'human.reopen', seq: 2, status: 'ready' }),
      jsonResponse(200, { task_id: 'T-1', event: 'human.release', seq: 3, status: 'ready' }),
      jsonResponse(200, { task_id: 'T-1', decision_id: 'dec-9', seq: 4 }),
      jsonResponse(200, { task_id: 'T-1', event: 'human.cancel', seq: 5, status: 'cancelled' }),
    )

    await api.humanResume('T-1', { decided_by: 'operator', answer: '继续' })
    await api.humanReopen('T-1', { decided_by: 'operator' })
    await api.humanRelease('T-1', { decided_by: 'operator' })
    await api.humanAdjudicate('T-1', {
      decided_by: 'operator',
      question: '选哪条路？',
      options: ['A', 'B'],
    })
    await api.humanCancel('T-1', { decided_by: 'operator', reason: '方向已变' })

    const urls = mock.mock.calls.map(([url]) => url)
    expect(urls).toEqual([
      '/api/v1/human/resume',
      '/api/v1/human/reopen',
      '/api/v1/human/release',
      '/api/v1/human/adjudicate',
      '/api/v1/human/cancel',
    ])
    expect(JSON.parse(String(mock.mock.calls[0]?.[1]?.body))).toEqual({
      task_id: 'T-1',
      decided_by: 'operator',
      answer: '继续',
    })
    expect(JSON.parse(String(mock.mock.calls[3]?.[1]?.body))).toEqual({
      task_id: 'T-1',
      decided_by: 'operator',
      question: '选哪条路？',
      options: ['A', 'B'],
    })
    expect(JSON.parse(String(mock.mock.calls[4]?.[1]?.body))).toEqual({
      task_id: 'T-1',
      decided_by: 'operator',
      reason: '方向已变',
    })
  })

  it('posts the budget override with the release decision', async () => {
    const mock = stubFetch(
      jsonResponse(200, {
        task_id: 'T-1',
        event: 'human.release',
        seq: 3,
        status: 'ready',
        budget_override_by: 'operator',
      }),
    )

    await api.humanRelease('T-1', {
      decided_by: 'operator',
      budget_override: true,
      reason: 'owner approved',
    })

    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/human/release')
    expect(JSON.parse(String(mock.mock.calls[0]?.[1]?.body))).toEqual({
      task_id: 'T-1',
      decided_by: 'operator',
      budget_override: true,
      reason: 'owner approved',
    })
  })

  it('uses the administrator review claim and submit endpoints', async () => {
    const mock = stubFetch(
      jsonResponse(200, {
        review_id: 'rv-1',
        reviewed_commit: 'a'.repeat(40),
        lease_until: '2026-10-15T01:00:00Z',
        reused: false,
      }),
      jsonResponse(200, {
        review_id: 'rv-1',
        task_id: 'T-1',
        verdict: 'approve',
        event: 'approve',
        seq: 9,
      }),
    )

    await api.humanReviewClaim('T-1', 'T-1.a1', { decided_by: 'admin' })
    await api.humanReviewSubmit('T-1', 'T-1.a1', {
      decided_by: 'admin',
      review_id: 'rv-1',
      verdict: 'approve',
      reviewed_commit: 'a'.repeat(40),
    })

    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/human/review/claim')
    expect(mock.mock.calls[1]?.[0]).toBe('/api/v1/human/review/submit')
    expect(JSON.parse(String(mock.mock.calls[1]?.[1]?.body))).toMatchObject({
      task_id: 'T-1',
      attempt_id: 'T-1.a1',
      decided_by: 'admin',
      review_id: 'rv-1',
      verdict: 'approve',
    })
  })
})
