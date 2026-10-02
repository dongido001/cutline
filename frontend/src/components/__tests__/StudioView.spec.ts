import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import StudioView from '@/views/StudioView.vue'

describe('StudioView', () => {
  it('updates the open suggestion count when a decision is made', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'studio', component: StudioView },
        { path: '/projects/:projectId/:stage/:shotId?', name: 'project-stage', component: StudioView },
      ],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(StudioView, {
      global: {
        plugins: [createPinia(), router],
      },
    })
    expect(wrapper.text()).toContain('3 open suggestions')
    await wrapper.find('.accept-button').trigger('click')
    expect(wrapper.text()).toContain('2 open suggestions')

    await wrapper.find('.preview-trigger').trigger('click')
    expect(wrapper.get('.preview-modal').text()).toContain('Storyboard animatic')
    expect(wrapper.get('.preview-modal').text()).toContain('Shot 1 of 2')
    await wrapper.get('button[aria-label="Next shot"]').trigger('click')
    expect(wrapper.get('.preview-modal').text()).toContain('Shot 2 of 2')
    await wrapper.get('button[aria-label="Close preview"]').trigger('click')

    await wrapper.find('.format-guide-trigger').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('Structured script format')
    expect(wrapper.get('[role="dialog"]').text()).toContain('SHOT N - TITLE')
    expect(wrapper.get('[role="dialog"]').text()).toContain('Copy prompt')
  })

  it('restores the shot detail view from its URL and returns to the Produce overview', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'studio', component: StudioView },
        { path: '/projects/:projectId/:stage/:shotId?', name: 'project-stage', component: StudioView },
      ],
    })
    await router.push('/projects/project-1/shots/shot-1')
    await router.isReady()

    const wrapper = mount(StudioView, {
      global: {
        plugins: [createPinia(), router],
      },
    })
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.produce-detail').exists()).toBe(true)
    expect(wrapper.find('.produce-story').exists()).toBe(false)

    await wrapper.get('.back-to-story').trigger('click')
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/projects/project-1/shots')
    expect(wrapper.find('.produce-story').exists()).toBe(true)
    expect(wrapper.find('.produce-detail').exists()).toBe(false)
    wrapper.unmount()
  })

  it('supports importing screenplay files via the create modal', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'studio', component: StudioView },
        { path: '/projects/:projectId/:stage/:shotId?', name: 'project-stage', component: StudioView },
      ],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(StudioView, {
      global: {
        plugins: [createPinia(), router],
      },
    })
    await wrapper.vm.$nextTick()

    // Open create project modal
    await wrapper.get('button[aria-label="Create project"]').trigger('click')
    expect(wrapper.text()).toContain('Import screenplay')

    const importButton = wrapper.findAll('.field-action-button').find((b) => b.text().includes('Import file'))
    expect(importButton).toBeDefined()

    // Check hidden file input
    const fileInput = wrapper.find<HTMLInputElement>('input[type="file"][accept*=".txt"]')
    expect(fileInput.exists()).toBe(true)

    // Simulate selecting a markdown screenplay file
    const sampleScript = 'Title: Cyber Heist\n\nSHOT 1 - ROOFTOP - NIGHT (0:00 - 0:04)\nFraming: Wide shot\nAction: A hacker types furiously.\nSound: Rain pouring.\nDialogue: None.\nImage Prompt: Cyberpunk roof with neon reflections.\nVideo Prompt: Slow camera zoom on hacker.'
    const file = new File([sampleScript], 'cyber_heist.md', { type: 'text/markdown' })

    Object.defineProperty(fileInput.element, 'files', {
      value: [file],
      writable: true,
    })
    await fileInput.trigger('change')
    await flushPromises()

    const textarea = wrapper.find<HTMLTextAreaElement>('#screenplay-source')
    expect(textarea.element.value).toContain('Cyber Heist')
    expect(wrapper.text()).toContain('cyber_heist.md')

    const titleInput = wrapper.find<HTMLInputElement>('form.project-modal input[required]')
    expect(titleInput.element.value).toBe('Title: Cyber Heist')

    // Test clear loaded file chip
    const clearButton = wrapper.find('.loaded-file-clear')
    expect(clearButton.exists()).toBe(true)
    await clearButton.trigger('click')
    await flushPromises()
    expect(wrapper.find('.loaded-file-bar').exists()).toBe(false)

    wrapper.unmount()
  })
})
