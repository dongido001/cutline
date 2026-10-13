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

  it('updates storyboard video source and starts playback when selecting a video take', async () => {
    const projectWithTakes = JSON.parse(JSON.stringify(fixture)) as ProjectDetail
    projectWithTakes.status = 'shot_plan'
    const shot = projectWithTakes.current_version!.scenes[0]!.shots[0]!
    shot.status = 'clip_ready'
    shot.selected_clip_take_id = 'take-2'
    shot.clip = {
      id: 'clip-1',
      url: '/media/clip-take-2.mp4',
      mime_type: 'video/mp4',
      source: 'generated',
      provider: 'flowkit',
      prompt: 'Push in',
      created_at: '2026-10-06T10:05:00Z',
    }
    shot.media_variants = [
      {
        id: 'var-1',
        media_type: 'clip',
        aspect_ratio: '16:9',
        url: '/media/clip-take-1.mp4?v=100',
        mime_type: 'video/mp4',
        source: 'generated',
        provider: 'flowkit',
        prompt: 'Push in',
        created_at: '2026-10-06T10:00:00Z',
      },
    ]
    shot.takes = [
      {
        id: 'take-1',
        media_type: 'clip',
        aspect_ratio: '16:9',
        url: '/media/clip-take-1.mp4?v=100',
        mime_type: 'video/mp4',
        source: 'generated',
        provider: 'flowkit',
        prompt: 'Push in',
        selected: false,
        settings: {},
        created_at: '2026-10-06T10:00:00Z',
      },
      {
        id: 'take-2',
        media_type: 'clip',
        aspect_ratio: '16:9',
        url: '/media/clip-take-2.mp4?v=200',
        mime_type: 'video/mp4',
        source: 'generated',
        provider: 'flowkit',
        prompt: 'Push in',
        selected: true,
        settings: {},
        created_at: '2026-10-06T10:05:00Z',
      },
    ]

    vi.spyOn(api, 'project').mockImplementation(async () => JSON.parse(JSON.stringify(projectWithTakes)) as ProjectDetail)
    const selectTakeSpy = vi.spyOn(api, 'selectTake').mockImplementation(async (_shotId, takeId) => {
      shot.selected_clip_take_id = takeId
      for (const t of shot.takes!) {
        t.selected = t.id === takeId
      }
      const chosen = shot.takes!.find((t) => t.id === takeId)!
      shot.clip!.url = chosen.url.split('?')[0]!
      shot.media_variants[0]!.url = chosen.url
      return JSON.parse(JSON.stringify(shot))
    })

    const playSpy = vi.spyOn(window.HTMLMediaElement.prototype, 'play').mockResolvedValue()

    const router = createRouter({ history: createMemoryHistory(), routes: routes() })
    await router.push('/projects/project-1/shots/shot-1')
    await router.isReady()

    const wrapper = mount(StudioView, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    // Even if media_variants had a stale URL from take-1, active take-2 is resolved
    const mainVideoBefore = wrapper.get('.storyboard-frame video')
    expect(mainVideoBefore.attributes('src')).toBe('/media/clip-take-2.mp4?v=200')

    // Click "Select" on Take 1 card
    const selectBtn = wrapper.findAll('.take-select-btn').find((b) => b.text() === 'Select')
    expect(selectBtn).toBeDefined()
    await selectBtn!.trigger('click')
    await flushPromises()

    expect(selectTakeSpy).toHaveBeenCalledWith('shot-1', 'take-1')
    const mainVideoAfter = wrapper.get('.storyboard-frame video')
    expect(mainVideoAfter.attributes('src')).toBe('/media/clip-take-1.mp4?v=100')
    expect(playSpy).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('keeps selected orientation when switching from Episode 6 to Episode 5 in Assemble without page refresh', async () => {
    vi.spyOn(api, 'project').mockImplementation(async (_id, episodeId) => {
      const copy = JSON.parse(JSON.stringify(fixture)) as ProjectDetail
      copy.status = 'shot_plan'
      copy.aspect_ratio = '9:16'
      copy.current_episode_id = episodeId || 'ep-6'
      copy.variant_exports = []
      copy.publishes = []
      copy.episodes = [
        { id: 'ep-5', episode_number: 5, title: 'Episode 5', status: 'shot_plan', shot_count: 2 } as never,
        { id: 'ep-6', episode_number: 6, title: 'Episode 6', status: 'shot_plan', shot_count: 2 } as never,
      ]
      return copy
    })
    const timelineSpy = vi.spyOn(api, 'timeline').mockImplementation(async (_id, edit) => ({
      revision: 1,
      edit: edit || 'shorts',
      assets: [],
      document: {
        version: 1,
        primary: [],
        image_overlays: [],
        text_overlays: [],
        audio_overlays: [],
      },
    }))

    const router = createRouter({ history: createMemoryHistory(), routes: routes() })
    await router.push('/projects/project-1/final-cut?episode=ep-6&variant=shorts&aspect=9:16')
    await router.isReady()

    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(StudioView, {
      global: { plugins: [pinia, router], stubs: { PublishPanel: true } },
    })
    await flushPromises()

    const store = useStudioStore()
    expect(store.currentEpisodeId).toBe('ep-6')

    // Switch to Episode 5
    await store.selectEpisode('ep-5')
    await flushPromises()
    expect(store.currentEpisodeId).toBe('ep-5')
    expect(router.currentRoute.value.query.episode).toBe('ep-5')

    timelineSpy.mockClear()

    // Select Landscape 16:9 orientation in TimelineEditor
    const variantBtns = wrapper.findAll('.variant-toggle button')
    expect(variantBtns).toHaveLength(2)
    expect(variantBtns[1]!.classes()).toContain('active')

    await variantBtns[0]!.trigger('click')
    await flushPromises()

    expect(variantBtns[0]!.classes()).toContain('active')
    expect(variantBtns[1]!.classes()).not.toContain('active')
    expect(router.currentRoute.value.query.variant).toBe('landscape')
    expect(router.currentRoute.value.query.aspect).toBe('16:9')
    expect(timelineSpy).toHaveBeenCalledTimes(1)
    expect(timelineSpy).toHaveBeenCalledWith('project-1', 'landscape', 'ep-5')

    wrapper.unmount()
  })

  it('supports Characters, Scenes, and Props tabs, @mention autocomplete, and direct R2V video generation', async () => {
    const r2vProject = JSON.parse(JSON.stringify(fixture)) as ProjectDetail
    r2vProject.status = 'shot_plan'
    r2vProject.characters = [
      {
        id: 'char-1',
        name: 'MARA ELIAS',
        notes: 'Lead detective',
        voice_description: '',
        kind: 'character',
        created_at: '2026-10-12T08:00:00Z',
        references: [
          {
            id: 'ref-main',
            url: '/media/mara-main.png',
            mime_type: 'image/png',
            source: 'generated',
            provider: 'chatgpt_web',
            prompt: 'main',
            view_type: 'main',
            created_at: '2026-10-12T08:00:00Z',
          },
          {
            id: 'ref-3d',
            url: '/media/mara-3d.png',
            mime_type: 'image/png',
            source: 'generated',
            provider: 'chatgpt_web',
            prompt: '3d',
            view_type: '3d_view',
            created_at: '2026-10-12T08:05:00Z',
          },
        ],
      },
      {
        id: 'scene-1',
        name: 'INT. KITCHEN',
        notes: 'Dark rainy kitchen',
        voice_description: '',
        kind: 'scene',
        created_at: '2026-10-12T08:06:00Z',
        references: [
          {
            id: 'ref-scene',
            url: '/media/kitchen.png',
            mime_type: 'image/png',
            source: 'generated',
            provider: 'chatgpt_web',
            prompt: 'scene',
            view_type: 'scene',
            created_at: '2026-10-12T08:06:00Z',
          },
        ],
      },
      {
        id: 'prop-1',
        name: 'BRASS COMPASS',
        notes: 'Antique brass compass',
        voice_description: '',
        kind: 'prop',
        created_at: '2026-10-12T08:07:00Z',
        references: [
          {
            id: 'ref-prop',
            url: '/media/compass.png',
            mime_type: 'image/png',
            source: 'generated',
            provider: 'chatgpt_web',
            prompt: 'prop',
            view_type: 'prop',
            created_at: '2026-10-12T08:07:00Z',
          },
        ],
      },
    ]

    vi.spyOn(api, 'project').mockImplementation(async () => JSON.parse(JSON.stringify(r2vProject)) as ProjectDetail)
    vi.spyOn(api, 'updateShot').mockImplementation(async (_id, payload) => ({
      ...r2vProject.current_version!.scenes[0]!.shots[0]!,
      ...payload,
    }) as never)
    const clipSpy = vi.spyOn(api, 'generateShotClip').mockResolvedValue({
      id: 'job-r2v-1',
      project_id: 'project-1',
      kind: 'shot_clip',
      status: 'queued',
      progress: 0,
      message: 'Queued (R2V)',
      attempts: [{ shot_id: 'shot-1', aspect_ratio: '16:9', video_mode: 'r2v' }],
      created_at: '2026-10-12T08:10:00Z',
      updated_at: '2026-10-12T08:10:00Z',
    } as never)

    const router = createRouter({ history: createMemoryHistory(), routes: routes() })
    await router.push('/projects/project-1/shots')
    await router.isReady()

    const wrapper = mount(StudioView, {
      global: { plugins: [createPinia(), router] },
    })
    await flushPromises()

    // Verify Characters | Scenes | Props tabs in Produce strip
    const tabs = wrapper.findAll('.element-tab-btn')
    expect(tabs).toHaveLength(3)
    expect(tabs[0]!.text()).toContain('Characters')
    expect(tabs[1]!.text()).toContain('Scenes')
    expect(tabs[2]!.text()).toContain('Props')
    expect(wrapper.text()).toContain('@MARA_ELIAS')
    expect(wrapper.text()).toContain('3D ready')

    await tabs[2]!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('@BRASS_COMPASS')

    // Open shot detail and switch to Reference-to-Video (R2V)
    await router.push('/projects/project-1/shots/shot-1')
    await flushPromises()

    const r2vBtn = wrapper.findAll('.video-mode-switch button').find((b) => b.text().includes('Reference-to-Video (R2V)'))
    expect(r2vBtn).toBeDefined()
    await r2vBtn!.trigger('click')
    await flushPromises()

    // Click @MARA_ELIAS and @BRASS_COMPASS quick-insert pills
    const maraQuickPill = wrapper.findAll('.mention-quick-bar .element-tag-pill').find((b) => b.text().includes('@MARA_ELIAS'))
    const propQuickPill = wrapper.findAll('.mention-quick-bar .element-tag-pill').find((b) => b.text().includes('@BRASS_COMPASS'))
    await maraQuickPill!.trigger('click')
    await propQuickPill!.trigger('click')
    await flushPromises()

    expect(wrapper.find('.r2v-elements-tray').text()).toContain('Attached R2V References (3/7 ready)')

    const genR2vBtn = wrapper.find('.r2v-generate-btn')
    expect(genR2vBtn.exists()).toBe(true)
    expect(genR2vBtn.attributes('disabled')).toBeUndefined()
    await genR2vBtn.trigger('click')
    await flushPromises()

    expect(clipSpy).toHaveBeenCalledWith(
      'shot-1',
      '16:9',
      true,
      false,
      expect.objectContaining({
        videoMode: 'r2v',
        includeSceneReference: true,
        elementIds: expect.arrayContaining(['char-1', 'prop-1', 'scene-1']),
      }),
    )

    wrapper.unmount()
  })
})


