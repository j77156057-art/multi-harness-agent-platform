import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { GroupSummary, ProjectSummary } from '../api'
import ProjectRail from '../components/ProjectRail.vue'

const projects: ProjectSummary[] = [
  { project_id: 'P-1', name: '项目一', created_at: '2026-10-07T00:00:00+00:00' },
]
const groups: GroupSummary[] = [
  { group_id: 'G-1', project_id: 'P-1', name: '群一', created_at: '2026-10-07T00:00:00+00:00' },
  { group_id: 'G-9', project_id: 'P-404', name: '孤儿群', created_at: '2026-10-07T00:00:00+00:00' },
]

function render(selectedGroupId: string | null = null) {
  return mount(ProjectRail, { props: { projects, groups, selectedGroupId } })
}

describe('ProjectRail', () => {
  it('renders groups under their project and marks the active one', () => {
    const wrapper = render('G-1')

    expect(wrapper.text()).toContain('项目一')
    expect(wrapper.text()).toContain('群一')
    const active = wrapper.get('.rail-group.active')
    expect(active.text()).toContain('G-1')
    expect(active.attributes('aria-current')).toBe('true')
  })

  it('drops groups whose project is not listed', () => {
    const wrapper = render()

    expect(wrapper.text()).not.toContain('孤儿群')
  })

  it('emits select when a group is clicked', async () => {
    const wrapper = mount(ProjectRail, {
      props: { projects, groups, selectedGroupId: null },
    })

    await wrapper.get('.rail-group').trigger('click')

    expect(wrapper.emitted('select')).toEqual([['G-1']])
  })

  it('shows the empty state without projects', () => {
    const wrapper = mount(ProjectRail, {
      props: { projects: [], groups: [], selectedGroupId: null },
    })

    expect(wrapper.text()).toContain('还没有项目')
  })
})
