import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
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
})
