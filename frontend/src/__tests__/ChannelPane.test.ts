import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import type { MessageRecord } from '../api'
import ChannelPane from '../components/ChannelPane.vue'

const messages: MessageRecord[] = [
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
]

function render(props: Partial<InstanceType<typeof ChannelPane>['$props']> = {}) {
  return mount(ChannelPane, {
    props: {
      groupId: 'G-1',
      messages: [],
      loading: false,
      error: null,
      busy: false,
      operator: 'operator',
      createPlan: async () => true,
      decidePlan: async () => true,
      ...props,
    },
  })
}

describe('ChannelPane', () => {
  it('renders the message list with sender and body', () => {
    const wrapper = render({ messages })

    expect(wrapper.text()).toContain('大家好')
    expect(wrapper.text()).toContain('operator')
  })

  it('shows the empty state and disables the composer without a group', () => {
    const wrapper = render({ groupId: null })

    expect(wrapper.text()).toContain('从左侧选择一个群组')
    expect(wrapper.get('.composer-input').attributes('disabled')).toBeDefined()
  })

  it('emits send with the trimmed draft and clears the input', async () => {
    const wrapper = render()

    await wrapper.get('.composer-input').setValue('  查一下 T-1 的状态  ')
    await wrapper.get('.composer').trigger('submit')

    expect(wrapper.emitted('send')).toEqual([['查一下 T-1 的状态']])
    expect((wrapper.get('.composer-input').element as HTMLInputElement).value).toBe('')
  })

  it('does not emit send for a blank draft', async () => {
    const wrapper = render()

    await wrapper.get('.composer-input').setValue('   ')
    await wrapper.get('.composer').trigger('submit')

    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('shows the error notice ahead of the list', () => {
    const wrapper = render({ error: 'request.conflict: task is blocked', messages })

    expect(wrapper.get('[role="alert"]').text()).toContain('request.conflict')
  })

  it('creates a plan from the parsed JSON snapshot', async () => {
    const createPlan = vi.fn(
      async (_fields: { plan_id: string; snapshot: Record<string, unknown> }) => true,
    )
    const wrapper = render({ createPlan })

    await wrapper.get('[name="plan_id"]').setValue('PLAN-1')
    await wrapper.get('.plan-card form').trigger('submit')

    expect(createPlan).toHaveBeenCalledTimes(1)
    const fields = createPlan.mock.calls[0]?.[0]
    expect(fields?.plan_id).toBe('PLAN-1')
    // 默认模板是合法 JSON 对象（tasks + registered_capabilities）。
    expect(fields?.snapshot).toMatchObject({ tasks: [{ id: 'T-1' }] })
  })

  it('refuses malformed snapshot JSON before the network call', async () => {
    const createPlan = vi.fn(
      async (_fields: { plan_id: string; snapshot: Record<string, unknown> }) => true,
    )
    const wrapper = render({ createPlan })

    await wrapper.get('[name="plan_id"]').setValue('PLAN-1')
    await wrapper.get('[name="plan_snapshot"]').setValue('{ not json')
    await wrapper.get('.plan-card form').trigger('submit')

    expect(createPlan).not.toHaveBeenCalled()
    expect(wrapper.get('.form-error').text()).toContain('JSON 解析失败')
  })

  it('refuses a snapshot that is not a JSON object', async () => {
    const createPlan = vi.fn(
      async (_fields: { plan_id: string; snapshot: Record<string, unknown> }) => true,
    )
    const wrapper = render({ createPlan })

    await wrapper.get('[name="plan_id"]').setValue('PLAN-1')
    await wrapper.get('[name="plan_snapshot"]').setValue('[1, 2]')
    await wrapper.get('.plan-card form').trigger('submit')

    expect(createPlan).not.toHaveBeenCalled()
    expect(wrapper.get('.form-error').text()).toContain('必须是一个 JSON 对象')
  })

  it('approves and rejects with decided_by and an optional reason', async () => {
    const decidePlan = vi.fn(
      async (
        _planId: string,
        _approve: boolean,
        _fields: { decided_by: string; reason?: string },
      ) => true,
    )
    const wrapper = render({ decidePlan })

    await wrapper.get('[name="plan_id"]').setValue('PLAN-1')
    await wrapper.get('[name="plan_decided_by"]').setValue('operator')
    await wrapper.get('[name="plan_reject_reason"]').setValue('范围太大')
    await wrapper.get('.plan-decide').trigger('submit')
    // 通过不带理由（理由只属于驳回语义）。
    expect(decidePlan).toHaveBeenLastCalledWith('PLAN-1', true, { decided_by: 'operator' })

    // 成功会清空理由输入，故重新填入再驳回。
    await wrapper.get('[name="plan_reject_reason"]').setValue('范围太大')
    const rejectButton = wrapper.findAll('button').find((b) => b.text().includes('驳回计划'))
    await rejectButton?.trigger('click')
    expect(decidePlan).toHaveBeenLastCalledWith('PLAN-1', false, {
      decided_by: 'operator',
      reason: '范围太大',
    })
  })

  it('keeps decide buttons disabled without a plan id or operator', async () => {
    const wrapper = render()

    const approve = wrapper.findAll('button').find((b) => b.text().includes('通过计划'))
    expect(approve?.attributes('disabled')).toBeDefined()

    await wrapper.get('[name="plan_id"]').setValue('PLAN-1')
    await wrapper.get('[name="plan_decided_by"]').setValue('')
    expect(approve?.attributes('disabled')).toBeDefined()
  })

  it('follows the token subject until the operator field is hand-edited', async () => {
    const wrapper = render({ operator: 'human-nine' })

    expect(
      (wrapper.get('[name="plan_decided_by"]').element as HTMLInputElement).value,
    ).toBe('human-nine')

    await wrapper.get('[name="plan_decided_by"]').setValue('my-own-name')
    await wrapper.setProps({ operator: 'other-subject' })
    expect(
      (wrapper.get('[name="plan_decided_by"]').element as HTMLInputElement).value,
    ).toBe('my-own-name')
  })
})
