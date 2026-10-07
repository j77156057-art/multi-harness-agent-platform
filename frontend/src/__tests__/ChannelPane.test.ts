import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
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
    props: { groupId: 'G-1', messages: [], loading: false, error: null, ...props },
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
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('send')).toEqual([['查一下 T-1 的状态']])
    expect((wrapper.get('.composer-input').element as HTMLInputElement).value).toBe('')
  })

  it('does not emit send for a blank draft', async () => {
    const wrapper = render()

    await wrapper.get('.composer-input').setValue('   ')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('shows the error notice ahead of the list', () => {
    const wrapper = render({ error: 'request.conflict: task is blocked', messages })

    expect(wrapper.get('[role="alert"]').text()).toContain('request.conflict')
  })
})
