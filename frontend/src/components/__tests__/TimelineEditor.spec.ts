import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import TimelineEditor from '@/components/TimelineEditor.vue'
import { api, type ProjectDetail, type ProjectTimeline, type Shot } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'

const mockRoute = reactive<{ query: Record<string, string | undefined> }>({
  query: {},
})
const mockRouterReplace = vi.fn<(to: unknown) => Promise<void>>()

vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({
    replace: mockRouterReplace,
    push: vi.fn<(to: unknown) => Promise<void>>(),
  }),
}))

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      ...actual.api,
      timeline: vi.fn<typeof actual.api.timeline>(),
      saveTimeline: vi.fn<typeof actual.api.saveTimeline>(),
      resetTimeline: vi.fn<typeof actual.api.resetTimeline>(),
    },
  }
})

function makeProject(): ProjectDetail {
  return {
    id: 'proj-1',
    title: 'Test Movie',
    status: 'shot_plan',
    aspect_ratio: '16:9',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    episodes: [],
    current_episode_id: 'ep-1',
    current_version: null,
    final_cut: null,
    variant_exports: [],
    publishes: [],
    characters: [],
  } as unknown as ProjectDetail
}

function makeShots(): Shot[] {
  return [
    {
      id: 'shot-1',
      title: 'Shot 1',
      duration_ms: 2000,
      clip: { id: 'clip-1', url: '/media/clip1.mp4', mime_type: 'video/mp4' },
      media_variants: [],
    },
    {
      id: 'shot-2',
      title: 'Shot 2',
      duration_ms: 3000,
      clip: { id: 'clip-2', url: '/media/clip2.mp4', mime_type: 'video/mp4' },
      media_variants: [],
    },
  ] as unknown as Shot[]
}

function makeTimeline(): ProjectTimeline {
  return {
    revision: 1,
    edit: 'landscape',
    assets: [],
    document: {
      version: 1,
      primary: [
        {
          id: 'item-1',
          kind: 'video',
          label: 'Shot 1',
          source: { kind: 'shot_clip', ref_id: 'shot-1' },
          duration_ms: 2000,
          source_in_ms: 0,
          source_out_ms: 2000,
          text: '',
          mute_audio: false,
          variant: {},
        },
        {
          id: 'item-2',
          kind: 'video',
          label: 'Shot 2',
          source: { kind: 'shot_clip', ref_id: 'shot-2' },
          duration_ms: 3000,
          source_in_ms: 0,
          source_out_ms: 3000,
          text: '',
          mute_audio: false,
          variant: {},
        },
      ],
      image_overlays: [],
      text_overlays: [],
    },
  }
}

function mountEditor() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useStudioStore()
  const project = makeProject()
  store.currentProject = project
  store.currentEpisodeId = 'ep-1'
  store.loadProject = vi.fn<typeof store.loadProject>().mockResolvedValue(undefined)

  return mount(TimelineEditor, {
    props: {
      project,
      shots: makeShots(),
    },
    global: {
      plugins: [pinia],
      stubs: {
        PublishPanel: true,
      },
    },
  })
}

describe('TimelineEditor state persistence', () => {
  beforeEach(() => {
    localStorage.clear()
    mockRoute.query = {}
    vi.clearAllMocks()
    vi.mocked(api.timeline).mockImplementation(async (_id, edit) => ({
      ...makeTimeline(),
      edit: edit || 'landscape',
    }))
    vi.mocked(api.saveTimeline).mockImplementation(async (_id, tl, edit) => ({
      ...JSON.parse(JSON.stringify(tl)),
      revision: tl.revision + 1,
      edit: edit || tl.edit || 'landscape',
    }))
  })

  it('persists and restores preview mute, zoom level, selected item, and variant across remounts', async () => {
    const wrapper1 = mountEditor()
    await flushPromises()

    // Toggle preview mute
    const muteBtn = wrapper1.find('button[aria-label="Mute preview audio"]')
    expect(muteBtn.exists()).toBe(true)
    await muteBtn.trigger('click')
    expect(wrapper1.find('button[aria-label="Unmute preview audio"]').exists()).toBe(true)

    // Zoom in twice (1 -> 1.5 -> 2.0)
    const zoomInBtn = wrapper1.findAll('.zoom-toolbar-group button')[2]
    await zoomInBtn!.trigger('click')
    await zoomInBtn!.trigger('click')
    expect(wrapper1.find('.zoom-badge-button').text()).toBe('2.0x')

    // Select second timeline block (starts at 2000ms)
    const blocks = wrapper1.findAll('.timeline-block')
    expect(blocks.length).toBe(2)
    await blocks[1]!.trigger('click')
    expect(blocks[1]!.classes()).toContain('selected')

    // Switch variant to shorts
    const variantBtns = wrapper1.findAll('.variant-toggle button')
    await variantBtns[1]!.trigger('click')
    await flushPromises()

    wrapper1.unmount()

    // Remount without route query params and verify state is restored
    const wrapper2 = mountEditor()
    await flushPromises()

    expect(wrapper2.find('button[aria-label="Unmute preview audio"]').exists()).toBe(true)
    expect(wrapper2.find('.zoom-badge-button').text()).toBe('2.0x')
    const restoredBlocks = wrapper2.findAll('.timeline-block')
    expect(restoredBlocks[1]!.classes()).toContain('selected')
    expect(wrapper2.findAll('.variant-toggle button')[1]!.classes()).toContain('active')
    expect(wrapper2.find('.playhead-time').text()).toBe('2.00s')

    wrapper2.unmount()
  })

  it('flushes pending debounced clip mute_audio save immediately on unmount', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    // Toggle per-clip mute_audio via inspector button
    const clipMuteBtn = wrapper.find('.audio-toggle-action')
    expect(clipMuteBtn.exists()).toBe(true)
    await clipMuteBtn.trigger('click')
    await flushPromises()

    // Unmount before the 650ms debounce timer fires
    expect(api.saveTimeline).not.toHaveBeenCalled()
    wrapper.unmount()
    await flushPromises()

    expect(api.saveTimeline).toHaveBeenCalledTimes(1)
    const savedArg = vi.mocked(api.saveTimeline).mock.calls[0]?.[1]
    expect(savedArg?.document.primary[0]?.mute_audio).toBe(true)
  })
  it('fits the available workspace and ignores edit shortcuts while a dialog is open', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    const scroll = wrapper.find('.timeline-scroll').element
    Object.defineProperty(scroll, 'clientWidth', { configurable: true, value: 600 })
    window.dispatchEvent(new Event('resize'))
    await flushPromises()
    expect(wrapper.find('.timeline-scroll').attributes('style')).toContain('--track-width: 490px')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '?' }))
    await flushPromises()
    expect(wrapper.find('.shortcuts-card').exists()).toBe(true)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' }))
    await flushPromises()
    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.find('.shortcuts-card').exists()).toBe(false)
    wrapper.unmount()
  })

  it('supports transport clip jump/frame step, drag-and-drop clip reordering, and auto-closing add menu', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    // Jump to next clip via transport button
    await wrapper.get('button[aria-label="Next clip"]').trigger('click')
    expect(wrapper.find('.playhead-time').text()).toBe('2.00s')
    expect(wrapper.findAll('.timeline-block')[1]!.classes()).toContain('selected')

    // Step forward 1 frame (50ms)
    await wrapper.get('button[aria-label="Step forward"]').trigger('click')
    expect(wrapper.find('.playhead-time').text()).toBe('2.05s')

    // Drag first clip onto second clip to reorder
    const blocks = wrapper.findAll('.timeline-block')
    await blocks[0]!.trigger('dragstart')
    await blocks[1]!.trigger('dragover')
    await blocks[1]!.trigger('drop')
    await flushPromises()

    const reorderedLabels = wrapper.findAll('.timeline-block b').map((node) => node.text())
    expect(reorderedLabels).toEqual(['Shot 2', 'Shot 1'])

    // Open Add media & text menu and verify it closes when adding a title card
    const addMenu = wrapper.get('details.add-menu').element as HTMLDetailsElement
    addMenu.open = true
    await wrapper.get('button[title="Add title card"]').trigger('click')
    expect(addMenu.open).toBe(false)
    expect(wrapper.findAll('.timeline-block')).toHaveLength(3)

    // Seek via timeline range input and verify Spacebar immediately toggles playback while range input is the event target
    const rangeInput = wrapper.get('input.timeline-input')
    await rangeInput.setValue('1000')
    const spaceOnRange = new KeyboardEvent('keydown', { key: ' ', code: 'Space', bubbles: true })
    Object.defineProperty(spaceOnRange, 'target', { value: rangeInput.element })
    window.dispatchEvent(spaceOnRange)
    await flushPromises()
    expect(wrapper.find('button[aria-label="Pause timeline"]').exists()).toBe(true)

    // Pressing Spacebar while a timeline clip block is the event target also toggles playback
    const firstBlock = wrapper.get('.timeline-block')
    const spaceOnBlock = new KeyboardEvent('keydown', { key: ' ', code: 'Space', bubbles: true })
    Object.defineProperty(spaceOnBlock, 'target', { value: firstBlock.element })
    window.dispatchEvent(spaceOnBlock)
    await flushPromises()
    expect(wrapper.find('button[aria-label="Play timeline"]').exists()).toBe(true)

    wrapper.unmount()
  })
})
