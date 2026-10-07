import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { EventRecord, TaskDiff, TaskSummary } from '../api'
import TaskPane from '../components/TaskPane.vue'

const tasks: TaskSummary[] = [
  {
    task_id: 'T-1',
    group_id: 'G-1',
    status: 'merged',
    owner: 'codex',
    current_attempt_id: 'T-1.a1',
    current_epoch: 2,
    waiting_since: null,
    escalated_at: null,
  },
  {
    task_id: 'T-2',
    group_id: 'G-1',
    status: 'escalated',
    owner: null,
    current_attempt_id: null,
    current_epoch: 0,
    waiting_since: null,
    escalated_at: null,
  },
]

const events: EventRecord[] = [
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
  {
    seq: 5,
    event_name: 'start',
    aggregate_type: 'task',
    aggregate_id: 'T-1',
    actor_type: 'harness',
    actor_id: 'codex',
    payload: {},
    schema_version: 1,
    created_at: '2026-10-07T12:01:00+00:00',
  },
]

const diffFixture: TaskDiff = {
  task_id: 'T-1',
  attempt_id: 'T-1.a1',
  available: true,
  reason: null,
  base_commit: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  head_commit: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  branch: 'agent/g1/codex/T-1/a1',
  files: [
    { path: 'src/app.py', insertions: 3, deletions: 1, binary: false },
    { path: 'assets/logo.png', insertions: null, deletions: null, binary: true },
  ],
  files_changed: 2,
  insertions: 3,
  deletions: 1,
  patch: null,
  truncated: false,
}

function render(props: Partial<InstanceType<typeof TaskPane>['$props']> = {}) {
  return mount(TaskPane, {
    props: {
      tasks: [],
      selectedTask: null,
      events: [],
      diff: null,
      diffLoading: false,
      loading: false,
      error: null,
      ...props,
    },
  })
}

describe('TaskPane', () => {
  it('tones task statuses and emits select', async () => {
    const wrapper = render({ tasks })

    expect(wrapper.get('.tone-ok').text()).toBe('merged')
    expect(wrapper.get('.tone-warn').text()).toBe('escalated')

    await wrapper.findAll('.task-row')[1]?.trigger('click')
    expect(wrapper.emitted('select')).toEqual([['T-2']])
  })

  it('renders the thread with events for the selected task', () => {
    const wrapper = render({ tasks, selectedTask: tasks[0] ?? null, events })

    expect(wrapper.text()).toContain('线程 · T-1')
    expect(wrapper.text()).toContain('owner codex')
    expect(wrapper.text()).toContain('claim')
    expect(wrapper.text()).toContain('#4')
    // 非空 payload 才有折叠块；空 payload 不渲染。
    expect(wrapper.findAll('.event-payload')).toHaveLength(1)
    expect(wrapper.get('.event-payload').text()).toContain('T-1.a1')
  })

  it('shows an empty thread note when the task has no events', () => {
    const wrapper = render({ tasks, selectedTask: tasks[1] ?? null, events: [] })

    expect(wrapper.text()).toContain('还没有事件')
  })

  it('shows the group-empty state and the error notice', () => {
    expect(render().text()).toContain('这个群组还没有任务')

    const failing = render({ error: 'resource.not_found: unknown task' })
    expect(failing.get('[role="alert"]').text()).toContain('resource.not_found')
  })

  it('renders the diff card with files, counts and binaries', () => {
    const wrapper = render({
      tasks,
      selectedTask: tasks[0] ?? null,
      events,
      diff: diffFixture,
    })

    expect(wrapper.get('.diff-card').text()).toContain('2 个文件')
    expect(wrapper.get('.diff-card').text()).toContain('+3')
    expect(wrapper.get('.diff-card').text()).toContain('−1')
    expect(wrapper.get('.diff-card').text()).toContain('src/app.py')
    expect(wrapper.get('.diff-card').text()).toContain('assets/logo.png')
    expect(wrapper.get('.diff-card').text()).toContain('二进制')
    expect(wrapper.get('.diff-card').text()).toContain('aaaaaaa..bbbbbbb')
    expect(wrapper.find('.diff-patch').exists()).toBe(false)
  })

  it('emits expandDiff from the diff card button', async () => {
    const wrapper = render({
      tasks,
      selectedTask: tasks[0] ?? null,
      events,
      diff: diffFixture,
    })

    const button = wrapper.findAll('button').find((b) => b.text().includes('展开 patch'))
    expect(button).toBeDefined()
    await button?.trigger('click')
    expect(wrapper.emitted('expandDiff')).toEqual([[]])
  })

  it('shows the patch and the truncation note once it is loaded', () => {
    const wrapper = render({
      tasks,
      selectedTask: tasks[0] ?? null,
      events,
      diff: { ...diffFixture, patch: 'diff --git a/x b/x\n+hello\n', truncated: true },
    })

    expect(wrapper.get('.diff-patch').text()).toContain('diff --git a/x b/x')
    expect(wrapper.get('.diff-card').text()).toContain('已截断')
  })

  it('shows the honest unavailable reason instead of a fake empty diff', () => {
    const wrapper = render({
      tasks,
      selectedTask: tasks[0] ?? null,
      events,
      diff: {
        ...diffFixture,
        available: false,
        reason: 'no attempt has published a commit',
        files: [],
        files_changed: 0,
        insertions: 0,
        deletions: 0,
      },
    })

    const card = wrapper.get('.diff-card')
    expect(card.text()).toContain('差异不可用：no attempt has published a commit')
    expect(card.find('.diff-files').exists()).toBe(false)
  })

  it('shows the diff loading state', () => {
    const wrapper = render({
      tasks,
      selectedTask: tasks[0] ?? null,
      events,
      diff: null,
      diffLoading: true,
    })

    expect(wrapper.get('.diff-head').text()).toContain('加载中')
  })
})
