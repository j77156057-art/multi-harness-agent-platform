import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import type { GroupSummary, ProjectSummary } from '../api'
import ProjectRail from '../components/ProjectRail.vue'

const projects: ProjectSummary[] = [
  { project_id: 'P-1', name: '项目一', created_at: '2026-10-07T00:00:00+00:00' },
]
const groups: GroupSummary[] = [
  { group_id: 'G-1', project_id: 'P-1', name: '群一', created_at: '2026-10-07T00:00:00+00:00' },
  { group_id: 'G-9', project_id: 'P-404', name: '孤儿群', created_at: '2026-10-07T00:00:00+00:00' },
]

function render(
  selectedGroupId: string | null = null,
  overrides: Partial<InstanceType<typeof ProjectRail>['$props']> = {},
) {
  return mount(ProjectRail, {
    props: {
      projects,
      groups,
      selectedGroupId,
      busy: false,
      auth: null,
      hasToken: false,
      createProject: async () => true,
      createGroup: async () => true,
      setToken: async () => false,
      clearToken: () => undefined,
      ...overrides,
    },
  })
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
    const wrapper = render()

    await wrapper.get('.rail-group').trigger('click')

    expect(wrapper.emitted('select')).toEqual([['G-1']])
  })

  it('shows the empty state without projects', () => {
    const wrapper = render(null, { projects: [], groups: [] })

    expect(wrapper.text()).toContain('还没有项目')
  })

  it('creates a project from the form and clears on success', async () => {
    const createProject = vi.fn(
      async (_fields: { project_id: string; name: string }) => true,
    )
    const wrapper = render(null, { createProject })

    await wrapper.get('.rail-add-project').trigger('click')
    await wrapper.get('[name="project_id"]').setValue('P-2')
    await wrapper.get('[name="project_name"]').setValue('项目二')
    await wrapper.get('.rail-form').trigger('submit')

    const fields = createProject.mock.calls[0]?.[0]
    expect(fields).toEqual({ project_id: 'P-2', name: '项目二' })
    // 成功即清空并收起表单。
    expect(wrapper.find('[name="project_id"]').exists()).toBe(false)
  })

  it('keeps the project form filled when creation fails', async () => {
    const wrapper = render(null, { createProject: async () => false })

    await wrapper.get('.rail-add-project').trigger('click')
    await wrapper.get('[name="project_id"]').setValue('P-2')
    await wrapper.get('[name="project_name"]').setValue('项目二')
    await wrapper.get('.rail-form').trigger('submit')

    expect((wrapper.get('[name="project_id"]').element as HTMLInputElement).value).toBe('P-2')
  })

  it('creates a group under the project whose form is open', async () => {
    const createGroup = vi.fn(
      async (_projectId: string, _fields: { group_id: string; name: string }) => true,
    )
    const wrapper = render(null, { createGroup })

    await wrapper.get('.rail-add-group').trigger('click')
    await wrapper.get('[name="group_id"]').setValue('G-2')
    await wrapper.get('[name="group_name"]').setValue('群二')
    await wrapper.get('.rail-form').trigger('submit')

    expect(createGroup).toHaveBeenCalledWith('P-1', { group_id: 'G-2', name: '群二' })
    expect(wrapper.find('[name="group_id"]').exists()).toBe(false)
  })

  it('disables the create button for blank fields and while busy', async () => {
    const wrapper = render(null, { busy: true })

    await wrapper.get('.rail-add-project').trigger('click')
    const button = wrapper.findAll('button').find((b) => b.text() === '创建项目')
    expect(button?.attributes('disabled')).toBeDefined()

    await wrapper.get('[name="project_id"]').setValue('P-2')
    await wrapper.get('[name="project_name"]').setValue('项目二')
    expect(button?.attributes('disabled')).toBeDefined()
  })

  it('labels every auth state (frontend-token)', () => {
    const label = (auth: InstanceType<typeof ProjectRail>['$props']['auth']) =>
      render(null, { auth }).get('.auth-status').text()

    expect(label(null)).toContain('未知')
    expect(label({ mode: 'off', subject: null, token_reason: null })).toContain('关闭')
    expect(
      label({ mode: 'enforce', subject: { type: 'human', id: 'operator' }, token_reason: null }),
    ).toBe('已认证为 human:operator')
    expect(
      label({ mode: 'enforce', subject: null, token_reason: 'token_missing' }),
    ).toContain('需要令牌')
    expect(
      label({ mode: 'enforce', subject: null, token_reason: 'token_invalid' }),
    ).toContain('令牌无效')
    expect(
      label({ mode: 'enforce', subject: null, token_reason: 'token_revoked' }),
    ).toContain('已吊销')
  })

  it('applies a token and clears the draft on success', async () => {
    const setToken = vi.fn(async (_raw: string) => true)
    const wrapper = render(null, { setToken })

    await wrapper.get('[name="auth_token"]').setValue('  apt_abc  ')
    await wrapper.get('.auth-panel form').trigger('submit')

    expect(setToken).toHaveBeenCalledWith('apt_abc')
    expect((wrapper.get('[name="auth_token"]').element as HTMLInputElement).value).toBe('')
  })

  it('keeps the draft when the token is refused, and offers clear only with a stored token', async () => {
    const refused = render(null, { setToken: async () => false })
    await refused.get('[name="auth_token"]').setValue('apt_bad')
    await refused.get('.auth-panel form').trigger('submit')
    expect((refused.get('[name="auth_token"]').element as HTMLInputElement).value).toBe('apt_bad')
    expect(refused.find('.auth-panel button[type="button"]').exists()).toBe(false)

    const clearing = vi.fn()
    const withToken = render(null, {
      hasToken: true,
      auth: { mode: 'enforce', subject: { type: 'human', id: 'x' }, token_reason: null },
      clearToken: clearing,
    })
    const clearButton = withToken.findAll('button').find((b) => b.text() === '清除令牌')
    await clearButton?.trigger('click')
    expect(clearing).toHaveBeenCalledTimes(1)
  })
})
