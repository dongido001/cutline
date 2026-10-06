import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { api, type ProjectDetail, type ProjectSummary } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import StudioView from '@/views/StudioView.vue'

const fixture = {
  id: 'project-1',
  title: 'Glass House',
  status: 'review',
  aspect_ratio: '16:9',
  target_duration_seconds: 60,
  text_provider: 'mock',
  episode_label: 'Episode',
  current_episode_id: 'ep-1',
  episodes: [],
  current_version: {
    id: 'v1',
    version_number: 1,
    status: 'review',
    scenes: [
      {
        id: 'demo-1',
        order_index: 0,
        title: 'INT. KITCHEN - NIGHT',
        location: 'INT. KITCHEN',
        time_of_day: 'NIGHT',
        start_ms: 0,
        end_ms: 12000,
        action: 'Rain presses against the black kitchen windows.',
        dialogue: [],
        shots: [
          {
            id: 'shot-1',
            order_index: 0,
            title: 'Kitchen wide',
            start_ms: 0,
            end_ms: 6000,
            action: '',
            dialogue: [],
            sound: '',
            duration_ms: 6000,
            framing: 'Medium',
            image_prompt: '',
            video_prompt: '',
            title_card_text: '',
            character_ids: [],
            status: 'planned',
            image: null,
            clip: null,
            media_variants: [],
          },
          {
            id: 'shot-2',
            order_index: 1,
            title: 'Mara reacts',
            start_ms: 6000,
            end_ms: 12000,
            action: '',
            dialogue: [],
            sound: '',
            duration_ms: 6000,
            framing: 'Close-up',
            image_prompt: '',
            video_prompt: '',
            title_card_text: '',
            character_ids: [],
            status: 'planned',
            image: null,
            clip: null,
            media_variants: [],
          },
        ],
      },
    ],
    suggestions: [
      {
        id: 's1',
        severity: 'blocking',
        title: "Clarify Mara's physical action",
        reason: 'The dialogue has no visible action to drive the shot.',
        after_value: 'Mara grips the counter, then turns toward the dark hallway.',
        status: 'pending',
      },
      {
        id: 's2',
        severity: 'warning',
        title: 'Extend the opening beat',
        reason: 'The current dialogue reads longer than the assigned timing.',
        after_value: '00:00-00:14',
        status: 'pending',
      },
      {
        id: 's3',
        severity: 'optional',
        title: 'Add a room-tone cue',
        reason: 'A quiet appliance hum will anchor the interior atmosphere.',
        after_value: 'SFX: refrigerator hum, distant traffic',
        status: 'pending',
      },
    ],
  },
} as unknown as ProjectDetail

const summary = { id: 'project-1', title: 'Glass House', status: 'review' } as ProjectSummary

function routes() {
  return [
    { path: '/', name: 'studio', component: StudioView },
    { path: '/projects/:projectId/:stage/:shotId?', name: 'project-stage', component: StudioView },
  ]
}

beforeEach(() => {
  vi.restoreAllMocks()
  fixture.current_version?.suggestions?.forEach((item) => {
    item.status = 'pending'
  })
  vi.spyOn(api, 'providers').mockResolvedValue([])
  vi.spyOn(api, 'imageProviders').mockResolvedValue([])
  vi.spyOn(api, 'videoProvider').mockResolvedValue({
    id: 'mock',
    label: 'Prototype',
    configured: true,
    available: true,
    model: 'deterministic-v1',
    detail: '',
  })
  vi.spyOn(api, 'projects').mockResolvedValue([summary])
  vi.spyOn(api, 'project').mockImplementation(async () => JSON.parse(JSON.stringify(fixture)) as ProjectDetail)
  vi.spyOn(api, 'projectJobs').mockResolvedValue([])
  vi.spyOn(api, 'decide').mockImplementation(async (id, decision) => {
    const suggestion = fixture.current_version?.suggestions?.find((item) => item.id === id)
    if (suggestion) suggestion.status = decision
    return suggestion as never
  })
})

describe('StudioView', () => {
  it('updates the open suggestion count when a decision is made', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: routes() })
    await router.push('/projects/project-1/script')
    await router.isReady()
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(StudioView, {
      global: { plugins: [pinia, router] },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('3 open suggestions')
    await useStudioStore().decideSuggestion('s1', 'accepted')
    await flushPromises()
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
    wrapper.unmount()
  })

  it('restores the shot detail view from its URL and returns to the Produce overview', async () => {
    const router = createRouter({ history: createMemoryHistory(), routes: routes() })
    await router.push('/projects/project-1/shots/shot-1')
    await router.isReady()

    const wrapper = mount(StudioView, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

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
    vi.spyOn(api, 'projects').mockResolvedValue([])
    const router = createRouter({ history: createMemoryHistory(), routes: routes() })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(StudioView, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    await wrapper.get('button[aria-label="Create project"]').trigger('click')
    expect(wrapper.text()).toContain('Import screenplay')

    const importButton = wrapper.findAll('.field-action-button').find((b) => b.text().includes('Import file'))
    expect(importButton).toBeDefined()

    const fileInput = wrapper.find<HTMLInputElement>('input[type="file"][accept*=".txt"]')
    expect(fileInput.exists()).toBe(true)

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

    const clearButton = wrapper.find('.loaded-file-clear')
    expect(clearButton.exists()).toBe(true)
    await clearButton.trigger('click')
    await flushPromises()
    expect(wrapper.find('.loaded-file-bar').exists()).toBe(false)

    wrapper.unmount()
  })
})
