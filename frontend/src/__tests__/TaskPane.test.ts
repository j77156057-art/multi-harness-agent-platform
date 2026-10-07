import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { EventRecord, TaskSummary } from '../api'
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

function render(props: Partial<InstanceType<typeof TaskPane>['$props']> = {}) {
  return mount(TaskPane, {
    props: { tasks: [], selectedTask: null, events: [], loading: false, error: null, ...props },
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
})
