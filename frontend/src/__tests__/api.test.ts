import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api, describeError } from '../api'

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
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

  it('fetches a task thread page', async () => {
    const mock = stubFetch(jsonResponse(200, { events: [], next_seq: 7, has_more: false }))

    const page = await api.taskEvents('T-1', 7)

    expect(mock.mock.calls[0]?.[0]).toBe('/api/v1/tasks/T-1/events?since_seq=7')
    expect(page.next_seq).toBe(7)
  })
})
