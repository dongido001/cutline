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
      uploadTimelineAsset: vi.fn<typeof actual.api.uploadTimelineAsset>(),
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
      image: { id: 'img-1', url: '/media/img1.png', mime_type: 'image/png' },
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
      audio_overlays: [],
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

  it('supports adding, inspecting, duplicating, and muting audio overlays on the AUDIO track', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    const trackNames = wrapper.findAll('.track-row > strong').map((el) => el.text())
    expect(trackNames).toEqual(['PRIMARY', 'VIDEO', 'IMAGE', 'TEXT', 'AUDIO'])

    // Add shot audio onto AUDIO track from Add media & text menu
    await wrapper.get('button[title="Add shot audio onto AUDIO track"]').trigger('click')
    await flushPromises()

    const audioBlocks = wrapper.findAll('.overlay-block.audio')
    expect(audioBlocks).toHaveLength(1)
    expect(audioBlocks[0]!.classes()).toContain('selected')
    expect(wrapper.find('.inspector-eyebrow').text()).toBe('Inspector · AUDIO')
    expect(wrapper.find('.inspector-header strong').text()).toBe('Shot 1 audio')

    // Mute the audio overlay via inspector toggle
    await wrapper.get('.audio-toggle-action').trigger('click')
    expect(wrapper.find('.overlay-block.audio').classes()).toContain('is-muted')

    // Duplicate the selected audio overlay
    await wrapper.get('button[title="Duplicate this audio clip (Ctrl+D)"]').trigger('click')
    expect(wrapper.findAll('.overlay-block.audio')).toHaveLength(2)

    wrapper.unmount()
    await flushPromises()

    expect(api.saveTimeline).toHaveBeenCalled()
    const calls = vi.mocked(api.saveTimeline).mock.calls
    const lastSaved = calls[calls.length - 1]?.[1]
    expect(lastSaved?.document.audio_overlays).toHaveLength(2)
    expect(lastSaved?.document.audio_overlays?.[0]?.mute_audio).toBe(true)
  })

  it('keeps newly selected orientation after switching episodes even when route.query.variant lags', async () => {
    mockRoute.query = { episode: 'ep-6', variant: 'shorts', aspect: '9:16' }
    const wrapper = mountEditor()
    const store = useStudioStore()
    store.currentEpisodeId = 'ep-6'
    await flushPromises()
    vi.mocked(api.timeline).mockClear()

    // Switch from Episode 6 to Episode 5 and simulate store.currentProject object replacement
    store.currentEpisodeId = 'ep-5'
    const updatedProject = { ...makeProject(), current_episode_id: 'ep-5' } as unknown as ProjectDetail
    store.currentProject = updatedProject
    await wrapper.setProps({ project: updatedProject })
    await flushPromises()

    // Episode switch should load the timeline once for ep-5 (not duplicate on project object reassignment)
    expect(api.timeline).toHaveBeenCalledTimes(1)
    expect(api.timeline).toHaveBeenLastCalledWith('proj-1', 'shorts', 'ep-5')

    // Click Landscape 16:9 while mockRoute.query.variant still lags at 'shorts'
    const variantBtns = wrapper.findAll('.variant-toggle button')
    expect(variantBtns[1]!.classes()).toContain('active')
    await variantBtns[0]!.trigger('click')
    await flushPromises()

    // Landscape 16:9 must stay selected and not bounce back to shorts
    expect(variantBtns[0]!.classes()).toContain('active')
    expect(variantBtns[1]!.classes()).not.toContain('active')
    expect(wrapper.emitted('variantChange')?.map((e) => e[0])).toEqual(['shorts', 'landscape'])
    expect(api.timeline).toHaveBeenCalledTimes(2)
    expect(api.timeline).toHaveBeenLastCalledWith('proj-1', 'landscape', 'ep-5')

    wrapper.unmount()
  })

  it('supports adding and inspecting image overlays on the IMAGE track', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    // Initially no image overlays
    expect(wrapper.findAll('.overlay-block.image')).toHaveLength(0)

    // Click the "+" button on the IMAGE track header
    await wrapper.get('.image-track-row .track-action-btn').trigger('click')
    await flushPromises()

    // Add Image Overlay modal should be visible
    expect(wrapper.find('.add-image-card').exists()).toBe(true)

    // Insert first shot image overlay
    const insertBtn = wrapper.find('.add-image-card .add-shot-insert-btn')
    expect(insertBtn.exists()).toBe(true)
    await insertBtn.trigger('click')
    await flushPromises()

    // Modal should close and IMAGE track should now have an image block
    expect(wrapper.find('.add-image-card').exists()).toBe(false)
    const imageBlocks = wrapper.findAll('.overlay-block.image')
    expect(imageBlocks).toHaveLength(1)
    expect(imageBlocks[0]!.classes()).toContain('selected')

    // Inspector should show image overlay controls
    expect(wrapper.find('.inspector-eyebrow').text()).toBe('Inspector · IMAGE')
    expect(wrapper.find('.inspector-header strong').text()).toBe('Image overlay')

    wrapper.unmount()
  })

  it('supports adding multiple secondary video overlays that default to full-frame scaling matching the primary clip', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(0)

    // Click the "+" button on the VIDEO track header to add first secondary video layer
    await wrapper.get('.video-track-row .track-action-btn').trigger('click')
    await flushPromises()
    expect(wrapper.find('.add-image-card').exists()).toBe(true)

    const insertBtns = wrapper.findAll('.add-image-card .add-shot-insert-btn')
    expect(insertBtns.length).toBeGreaterThanOrEqual(2)
    await insertBtns[0]!.trigger('click')
    await flushPromises()

    // Open again and add a second overlapping secondary video layer at the same playhead
    await wrapper.get('.video-track-row .track-action-btn').trigger('click')
    await flushPromises()
    await wrapper.findAll('.add-image-card .add-shot-insert-btn')[1]!.trigger('click')
    await flushPromises()

    const videoBlocks = wrapper.findAll('.overlay-block.video')
    expect(videoBlocks).toHaveLength(2)
    // Both overlapping layers are stacked into separate vertical lanes (top: 5px and top: 37px)
    expect(videoBlocks[0]!.attributes('style')).toContain('top: 5px')
    expect(videoBlocks[1]!.attributes('style')).toContain('top: 37px')

    // Both active secondary videos render in Program Monitor with full-frame default scaling
    const previewOverlays = wrapper.findAll('.preview-video-overlay')
    expect(previewOverlays).toHaveLength(2)
    expect(previewOverlays[0]!.classes()).toContain('is-full-frame')
    expect(previewOverlays[1]!.classes()).toContain('is-full-frame')

    // Inspector shows VIDEO layer controls with full-frame position and cover fit
    expect(wrapper.find('.inspector-eyebrow').text()).toBe('Inspector · VIDEO')

    wrapper.unmount()
    await flushPromises()

    const calls = vi.mocked(api.saveTimeline).mock.calls
    const lastSaved = calls[calls.length - 1]?.[1]
    expect(lastSaved?.document.video_overlays).toHaveLength(2)
    expect(lastSaved?.document.video_overlays?.[0]?.variant.landscape?.position).toBe('full-frame')
    expect(lastSaved?.document.video_overlays?.[0]?.variant.landscape?.fit).toBe('cover')
    expect(lastSaved?.document.video_overlays?.[1]?.variant.landscape?.position).toBe('full-frame')
  })

  it('moves a primary video clip onto the secondary VIDEO track on drop, duplicates when holding Ctrl or Shift, and supports Undo/Redo', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(0)

    // 1. Default drag from PRIMARY (#2 Shot 2) onto VIDEO track -> moves clip from PRIMARY to VIDEO
    const primaryBlocks = wrapper.findAll('.timeline-block')
    const videoTrackRow = wrapper.get('.video-track-row')
    await primaryBlocks[1]!.trigger('dragstart')
    await videoTrackRow.trigger('dragover')
    await videoTrackRow.trigger('drop')
    await flushPromises()

    expect(wrapper.findAll('.timeline-block')).toHaveLength(1)
    expect(wrapper.find('.timeline-block b').text()).toBe('Shot 1')
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(1)
    expect(wrapper.find('.overlay-block.video .overlay-label').text()).toBe('Shot 2')
    expect(wrapper.find('.inspector-eyebrow').text()).toBe('Inspector · VIDEO')

    // 2. Undo restores Shot 2 back to PRIMARY and removes it from VIDEO
    const undoBtn = wrapper.get('button[title="Undo (Ctrl+Z)"]')
    expect(undoBtn.attributes('disabled')).toBeUndefined()
    await undoBtn.trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)
    expect(wrapper.findAll('.timeline-block b').map((b) => b.text())).toEqual(['Shot 1', 'Shot 2'])
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(0)

    // 3. Redo reapplies the move from PRIMARY to VIDEO
    const redoBtn = wrapper.get('button[title="Redo (Ctrl+Shift+Z / Ctrl+Y)"]')
    await redoBtn.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.timeline-block')).toHaveLength(1)
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(1)

    // Undo again so we have 2 primary clips
    await undoBtn.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)

    // 4. Hold Shift (or Ctrl) while dragging Shot 1 onto VIDEO track -> duplicates onto VIDEO without removing from PRIMARY
    const blocksBeforeDup = wrapper.findAll('.timeline-block')
    await blocksBeforeDup[0]!.trigger('dragstart', { shiftKey: true })
    await videoTrackRow.trigger('dragover', { shiftKey: true })
    await videoTrackRow.trigger('drop', { shiftKey: true })
    await flushPromises()

    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(1)
    expect(wrapper.find('.overlay-block.video .overlay-label').text()).toBe('Shot 1 (copy)')

    // 5. Hold Ctrl on drop onto VIDEO track -> also duplicates onto VIDEO without removing from PRIMARY
    await wrapper.findAll('.timeline-block')[1]!.trigger('dragstart')
    await videoTrackRow.trigger('dragover', { ctrlKey: true })
    await videoTrackRow.trigger('drop', { ctrlKey: true })
    await flushPromises()

    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(2)

    // Undo removes the second duplicate while keeping the first duplicate and both primary clips
    await undoBtn.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(1)

    // Select the VIDEO block so its inspector is open; because Shot 1 on PRIMARY has active audio underneath, it auto-muted (video-only B-roll)
    await wrapper.get('.overlay-block.video').trigger('click')
    await flushPromises()
    expect(wrapper.find('.overlay-block.video').classes()).toContain('is-muted')

    // 1-click inline speaker icon on the VIDEO block toggles its audio active/muted directly
    await wrapper.get('.overlay-block.video .overlay-audio-toggle').trigger('click')
    await flushPromises()
    expect(wrapper.find('.overlay-block.video').classes()).not.toContain('is-muted')

    // Detach audio from the VIDEO layer onto the AUDIO track -> creates an AUDIO block and sets VIDEO layer to video-only
    await wrapper.get('button[title="Extract this layer\'s audio onto the AUDIO track and make this layer video-only"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('.overlay-block.video').classes()).toContain('is-muted')
    expect(wrapper.findAll('.overlay-block.audio')).toHaveLength(1)
    expect(wrapper.find('.overlay-block.audio .overlay-label').text()).toBe('Shot 1 audio')

    // Undo detaching audio in a single step
    await undoBtn.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.overlay-block.audio')).toHaveLength(0)

    // 6. Select the VIDEO layer and apply a crop region preset ('Left ½'), verifying clip-path updates
    await wrapper.get('.overlay-block.video').trigger('click')
    await flushPromises()
    const leftHalfBtn = wrapper.findAll('.quick-dur-buttons button').find((b) => b.text() === 'Left ½')
    expect(leftHalfBtn).toBeDefined()
    await leftHalfBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.preview-video-overlay').attributes('style')).toContain('clip-path: inset(0.00% 50.00% 0.00% 0.00%)')

    // 7. Test position nudge pad: clicking right shifts offset_x and updates preview translate
    const nudgeRightBtn = wrapper.get('.nudge-btn.nudge-right')
    await nudgeRightBtn.trigger('click')
    await flushPromises()
    expect(wrapper.find('.preview-video-overlay').attributes('style')).toContain('translate: 2.00cqw')

    // Clicking center re-centers position offset back to 0
    const nudgeCenterBtn = wrapper.get('.nudge-btn.nudge-center')
    await nudgeCenterBtn.trigger('click')
    await flushPromises()
    expect(wrapper.find('.preview-video-overlay').attributes('style')).not.toContain('translate:')

    // 8. Verify the VIDEO inspector shows the layer stack counter and '+ Add video layer' button
    expect(wrapper.find('.inspector-layer-order span').text()).toBe('Layer 1 of 1')
    const addLayerBtn = wrapper.get('button[title="Add another secondary video layer onto the VIDEO track"]')
    expect(addLayerBtn.text()).toBe('+ Add video layer')

    wrapper.unmount()
  })

  it('supports selecting multiple videos across tracks, shows selection count indicator, multi-splits at playhead, and handles voids vs auto-snap on delete', async () => {
    const customTimeline = makeTimeline()
    customTimeline.document.primary = [
      {
        id: 'primary-clip-1',
        kind: 'video',
        label: 'Main Camera',
        source: { kind: 'shot_clip', ref_id: 'shot-1' },
        duration_ms: 3000,
        source_in_ms: 0,
        source_out_ms: 3000,
        text: '',
        mute_audio: false,
        variant: {},
      },
    ]
    customTimeline.document.video_overlays = [
      {
        id: 'video-overlay-1',
        label: 'B-Roll Angle',
        source: { kind: 'shot_clip', ref_id: 'shot-2' },
        start_ms: 0,
        duration_ms: 3000,
        source_in_ms: 0,
        opacity: 1,
        volume: 1,
        mute_audio: true,
        variant: {},
      },
    ]

    vi.mocked(api.timeline).mockResolvedValue(customTimeline)

    const wrapper = mountEditor()
    await flushPromises()

    // Initially, primary clip is selected (single selection)
    expect(wrapper.findAll('.timeline-block.selected')).toHaveLength(1)
    expect(wrapper.find('.multi-select-pill').exists()).toBe(false)

    // 1. Shift-click the secondary VIDEO overlay to multi-select both Main Camera and B-Roll Angle
    const videoOverlayBlock = wrapper.get('.overlay-block.video')
    await videoOverlayBlock.trigger('click', { shiftKey: true })
    await flushPromises()

    // Both clips should now have the .selected class
    expect(wrapper.find('.timeline-block').classes()).toContain('selected')
    expect(wrapper.find('.overlay-block.video').classes()).toContain('selected')

    // 1b. Selecting ONLY the secondary video overlay and moving the timeline slider preserves secondary video selection
    await videoOverlayBlock.trigger('click')
    await flushPromises()
    expect(wrapper.find('.overlay-block.video').classes()).toContain('selected')
    expect(wrapper.find('.timeline-block').classes()).not.toContain('selected')

    const playheadInput = wrapper.get('.timeline-input')
    await playheadInput.setValue('500')
    await playheadInput.trigger('input')
    await flushPromises()

    // It must stay selected on the secondary video and NOT reset to the primary video
    expect(wrapper.find('.overlay-block.video').classes()).toContain('selected')
    expect(wrapper.find('.timeline-block').classes()).not.toContain('selected')

    // Re-select both for multi-split testing
    await wrapper.find('.timeline-block').trigger('click', { shiftKey: true })
    await flushPromises()
    expect(wrapper.find('.timeline-block').classes()).toContain('selected')
    expect(wrapper.find('.overlay-block.video').classes()).toContain('selected')

    // 2. Selection indicator should now be visible in toolbar and inspector
    const multiSelectPill = wrapper.find('.multi-select-pill')
    expect(multiSelectPill.exists()).toBe(true)
    expect(multiSelectPill.find('.multi-select-pill-count').text()).toBe('2 selected')
    expect(multiSelectPill.find('.multi-select-pill-summary').text()).toBe('(1 Primary, 1 Video)')

    // Inspector should show MULTI-SELECTION header and both item chips
    expect(wrapper.find('.inspector-eyebrow').text()).toContain('MULTI-SELECTION')
    expect(wrapper.find('.inspector-header strong').text()).toBe('2 items selected')
    const chipTexts = wrapper.findAll('.multi-select-item-chip').map((c) => c.text())
    expect(chipTexts).toHaveLength(2)
    expect(chipTexts.some((t) => t.includes('Main Camera'))).toBe(true)
    expect(chipTexts.some((t) => t.includes('B-Roll Angle'))).toBe(true)

    // 3. Move playhead to 1500ms (intersects both clips)
    await playheadInput.setValue('1500')
    await playheadInput.trigger('input')
    await flushPromises()

    // The Split button in toolbar shows Split (2) and is enabled
    const splitBtn = wrapper.findAll('.timeline-toolbar button').find((b) => b.text().includes('Split'))
    expect(splitBtn).toBeDefined()
    expect(splitBtn!.text()).toContain('Split (2)')
    expect(splitBtn!.attributes('disabled')).toBeUndefined()

    // 4. Click Split: splits both the primary clip AND the video overlay simultaneously at 1500ms
    await splitBtn!.trigger('click')
    await flushPromises()

    // Now primary has 2 clips, and video overlays has 2 clips
    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(2)

    // The primary clips are 1500ms each
    const primaryBlocks = wrapper.findAll('.timeline-block')
    expect(primaryBlocks[0]?.text()).toContain('1.5s')
    expect(primaryBlocks[1]?.text()).toContain('1.5s')

    // The video overlays are 1500ms each
    const overlayBlocks = wrapper.findAll('.overlay-block.video')
    expect(overlayBlocks[0]?.text()).toContain('1.5s')
    expect(overlayBlocks[1]?.text()).toContain('1.5s')

    // 5. Test Undo: reverts multi-split in a single undo step
    const undoBtn = wrapper.get('button[title="Undo (Ctrl+Z)"]')
    await undoBtn.trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.timeline-block')).toHaveLength(1)
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(1)
    expect(wrapper.find('.timeline-block').text()).toContain('3.0s')
    expect(wrapper.find('.overlay-block.video').text()).toContain('3.0s')

    // 6. Test Redo: re-applies both splits in a single redo step
    const redoBtn = wrapper.get('button[title="Redo (Ctrl+Shift+Z / Ctrl+Y)"]')
    await redoBtn.trigger('click')
    await flushPromises()

    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(2)

    // 7. Test single delete on secondary video overlay: leaves a VOID (does not alter primary duration)
    // Select only the second video overlay
    await wrapper.findAll('.overlay-block.video')[1]!.trigger('click')
    await flushPromises()

    const deleteBtn = wrapper.get('button[title*="Delete"]')
    await deleteBtn.trigger('click')
    await flushPromises()

    // Secondary video overlays now has 1 clip (leaving void from 1500ms to 3000ms)
    expect(wrapper.findAll('.overlay-block.video')).toHaveLength(1)
    // Primary track is untouched and still has 2 clips totaling 3.0s
    expect(wrapper.findAll('.timeline-block')).toHaveLength(2)

    wrapper.unmount()
  })

  it('aligns secondary video overlay length precisely with primary clip across splits and long timelines without outgrowing it', async () => {
    const customTimeline = makeTimeline()
    // Total primary duration = 100,000ms (100s)
    customTimeline.document.primary = [
      {
        id: 'primary-clip-1',
        kind: 'video',
        label: 'Primary 1',
        source: { kind: 'shot_clip', ref_id: 'shot-1' },
        duration_ms: 10000, // 10s
        source_in_ms: 0,
        source_out_ms: 10000,
        text: '',
        mute_audio: false,
        variant: {},
      },
      {
        id: 'primary-clip-2',
        kind: 'video',
        label: 'Primary 2',
        source: { kind: 'shot_clip', ref_id: 'shot-2' },
        duration_ms: 90000, // 90s
        source_in_ms: 0,
        source_out_ms: 90000,
        text: '',
        mute_audio: false,
        variant: {},
      },
    ]
    // Video overlay aligned with primary-clip-2 (starts at 10s, duration 90s)
    customTimeline.document.video_overlays = [
      {
        id: 'overlay-clip-1',
        label: 'Overlay 2',
        source: { kind: 'shot_clip', ref_id: 'shot-2' },
        start_ms: 10000,
        duration_ms: 90000,
        source_in_ms: 0,
        opacity: 1,
        volume: 1,
        mute_audio: false,
        variant: {},
      },
    ]

    vi.mocked(api.timeline).mockResolvedValue(customTimeline)

    const wrapper = mount(TimelineEditor, {
      props: { project: makeProject(), shots: makeShots() },
    })
    await flushPromises()

    // Select both primary clip #2 and overlay clip #1
    await wrapper.findAll('.timeline-block')[1]!.trigger('click')
    await wrapper.find('.overlay-block.video').trigger('click', { shiftKey: true })
    await flushPromises()

    // Move playhead to 50,000ms (50s)
    const playheadInput = wrapper.get('.timeline-input')
    await playheadInput.setValue('50000')
    await playheadInput.trigger('input')
    await flushPromises()

    // Split both at 50s
    const splitBtn = wrapper.findAll('.timeline-toolbar button').find((b) => b.text().includes('Split'))
    await splitBtn!.trigger('click')
    await flushPromises()

    const primaryBlocks = wrapper.findAll('.timeline-block')
    const overlayBlocks = wrapper.findAll('.overlay-block.video')

    expect(primaryBlocks).toHaveLength(3) // 10s, 40s, 50s
    expect(overlayBlocks).toHaveLength(2) // 40s, 50s

    // Primary #3 (the right split) has duration 50s: 50,000 / 100,000 = 50%
    const rightPrimaryStyle = primaryBlocks[2]?.attributes('style')
    // Overlay #2 (the right split) has duration 50s: 50,000 / 100,000 = 50%
    const rightOverlayStyle = overlayBlocks[1]?.attributes('style')

    expect(rightPrimaryStyle).toContain('width: 50%')
    expect(rightOverlayStyle).toContain('width: 50%')
    expect(rightPrimaryStyle).toContain('left: 50%')
    expect(rightOverlayStyle).toContain('left: 50%')

    // Now test a small split (e.g. 500ms in a 100,000ms timeline: 0.5% width, < 2%)
    // Overlay must NOT be clamped to 2% and must match primary width exactly
    await wrapper.findAll('.timeline-block')[2]!.trigger('click')
    await overlayBlocks[1]!.trigger('click', { shiftKey: true })
    await flushPromises()

    await playheadInput.setValue('50500')
    await playheadInput.trigger('input')
    await flushPromises()

    const splitBtn2 = wrapper.findAll('.timeline-toolbar button').find((b) => b.text().includes('Split'))
    await splitBtn2!.trigger('click')
    await flushPromises()

    const primarySmall = wrapper.findAll('.timeline-block')[2] // 500ms
    const overlaySmall = wrapper.findAll('.overlay-block.video')[1] // 500ms

    // 500 / 100,000 * 100 = 0.5%
    expect(primarySmall?.attributes('style')).toContain('width: 0.5%')
    expect(overlaySmall?.attributes('style')).toContain('width: 0.5%')

    wrapper.unmount()
  })
})



