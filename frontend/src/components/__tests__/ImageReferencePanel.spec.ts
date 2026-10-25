import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import ImageReferencePanel from '@/components/ImageReferencePanel.vue'
import { api, type ImageReferenceSource, type ImageReferenceSourceShot, type Shot } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      imageReferenceSources: vi.fn<typeof actual.api.imageReferenceSources>(),
      saveImageReference: vi.fn<typeof actual.api.saveImageReference>(),
      removeImageReference: vi.fn<typeof actual.api.removeImageReference>(),
    },
  }
})

const sourceStill: ImageReferenceSource = {
  source_shot_id: 'shot-2',
  source_shot_title: 'Shot 2',
  source_media_id: 'image-2',
  source_media_kind: 'take',
  source_media_type: 'image',
  source_aspect_ratio: '16:9',
  url: '/media/shot-2.png',
  mime_type: 'image/png',
}

const sourceClip: ImageReferenceSource = {
  source_shot_id: 'shot-1',
  source_shot_title: 'Shot 1',
  source_media_id: 'clip-1',
  source_media_kind: 'take',
  source_media_type: 'clip',
  source_aspect_ratio: '16:9',
  url: '/media/clip-1.mp4',
  mime_type: 'video/mp4',
}

const sources: ImageReferenceSourceShot[] = [
  { id: 'shot-1', title: 'Shot 1', media: [sourceClip] },
  { id: 'shot-2', title: 'Shot 2', media: [sourceStill] },
]

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    id: 'shot-3',
    scene_id: 'scene-1',
    title: 'Shot 3',
    order_index: 2,
    framing: 'Medium',
    description: '',
    prompt: '',
    video_prompt: '',
    image_url: null,
    image_filename: null,
    clip_url: null,
    clip_filename: null,
    image_references: [],
    takes: [],
    duration_seconds: 5,
    continuity: {},
    ...overrides,
  } as Shot
}

function mountPanel(shot = makeShot()) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useStudioStore()
  store.currentProject = { id: 'project-1' } as typeof store.currentProject
  store.loadProject = vi.fn<typeof store.loadProject>()
  return mount(ImageReferencePanel, {
    props: {
      shot,
      aspect: '16:9',
      previousShot: {
        id: 'shot-2',
        title: 'Shot 2',
        image: { id: 'image-2', url: '/media/shot-2.png', mime_type: 'image/png', source: 'generated', provider: 'test', prompt: '', created_at: new Date().toISOString() },
        media_variants: [],
      } as unknown as Shot,
      projectAspect: '16:9',
      canReferenceEarlier: true,
    },
    global: {
      plugins: [pinia],
      stubs: {
        Dialog: defineComponent({
          props: { open: Boolean },
          template: '<section v-if="open" role="dialog"><slot /></section>',
        }),
        VideoFramePicker: defineComponent({
          name: 'VideoFramePicker',
          emits: ['save', 'close'],
          setup(_, { emit }) {
            const file = new Blob(['frame'], { type: 'image/png' }) as File
            return { save: () => emit('save', { file, timeMs: 750 }) }
          },
          template: '<button class="save-frame" @click="save">Save frame</button>',
        }),
      },
    },
  })
}

describe('ImageReferencePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.imageReferenceSources).mockResolvedValue(sources)
    vi.mocked(api.saveImageReference).mockResolvedValue(makeShot())
    vi.mocked(api.removeImageReference).mockResolvedValue(undefined)
  })

  it('saves a still image from the previous shot', async () => {
    const wrapper = mountPanel()
    await wrapper.get('button').trigger('click')
    await flushPromises()

    expect(wrapper.get('select').element.value).toBe('shot-2')

    await wrapper.get('.source-card').trigger('click')
    await flushPromises()

    expect(api.saveImageReference).toHaveBeenCalledWith('shot-3', sourceStill, '16:9', undefined)
  })

  it('passes the captured video frame when a clip source is selected', async () => {
    const wrapper = mountPanel()
    const chooseButton = wrapper.findAll('button')[1]
    expect(chooseButton).toBeDefined()
    await chooseButton!.trigger('click')
    await flushPromises()
    await wrapper.get('select').setValue('shot-1')
    await flushPromises()
    await wrapper.get('.source-card').trigger('click')
    await wrapper.get('.save-frame').trigger('click')
    await flushPromises()

    const call = vi.mocked(api.saveImageReference).mock.calls[0]
    expect(call).toBeDefined()
    const [, source, aspect, frame] = call!
    expect(source).toStrictEqual(sourceClip)
    expect(aspect).toBe('16:9')
    expect(frame).toBeDefined()
    expect(frame!.timeMs).toBe(750)
    expect(frame!.file.type).toBe('image/png')
  })

  it('removes the active reference for the current aspect', async () => {
    const wrapper = mountPanel(makeShot({ image_references: [{
      id: 'ref-1',
      aspect_ratio: '16:9',
      mode: 'visual_continuity',
      created_at: new Date().toISOString(),
      ...sourceStill,
      filename: 'image-reference-ref-1.png',
      source_time_ms: null,
    }] }))

    await wrapper.get('.ghost-link').trigger('click')
    await flushPromises()

    expect(api.removeImageReference).toHaveBeenCalledWith('shot-3', '16:9')
  })

  it('does not automatically attach or preview a default reference', () => {
    const wrapper = mountPanel()
    expect(wrapper.find('img').exists()).toBe(false)
    expect(wrapper.text()).toContain('No reference selected')
    expect(wrapper.text()).not.toContain('Attached automatically on generate')
  })

  it('enables Choose reference shot on Shot 1 of a later episode when earlier episodes have shots', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useStudioStore()
    store.currentEpisodeId = 'ep-4'
    store.currentProject = {
      id: 'project-1',
      episodes: [
        { id: 'ep-1', project_id: 'project-1', episode_number: 1, title: 'Ep 1', status: 'approved', shot_count: 20, duration_ms: 120000, created_at: '', updated_at: '' },
        { id: 'ep-4', project_id: 'project-1', episode_number: 4, title: 'Ep 4', status: 'approved', shot_count: 20, duration_ms: 120000, created_at: '', updated_at: '' },
      ],
    } as typeof store.currentProject

    const wrapper = mount(ImageReferencePanel, {
      props: {
        shot: makeShot({ id: 'ep4-shot-1', order_index: 0 }),
        aspect: '9:16',
        previousShot: undefined,
        projectAspect: '16:9',
      },
      global: {
        plugins: [pinia],
        stubs: {
          Dialog: defineComponent({
            props: { open: Boolean },
            template: '<section v-if="open" role="dialog"><slot /></section>',
          }),
        },
      },
    })

    const chooseBtn = wrapper.get('button')
    expect((chooseBtn.element as HTMLButtonElement).disabled).toBe(false)
    expect(wrapper.text()).not.toContain('References become available after the first shot.')
  })

  it('triggers Boost Color, step adjustments, undo/redo/reset, and AI Repaint when a reference frame is active', async () => {
    const wrapper = mountPanel(makeShot({ image_references: [{
      id: 'ref-1',
      aspect_ratio: '16:9',
      mode: 'visual_continuity',
      created_at: new Date().toISOString(),
      ...sourceClip,
      filename: 'image-reference-ref-1.png',
      source_time_ms: 9967,
      color_boost_strength: 0.75,
      can_undo_color: true,
      can_redo_color: true,
    }] }))
    const store = useStudioStore()
    store.retouchShotReferenceColor = vi.fn().mockResolvedValue(undefined)
    store.repaintShotReference = vi.fn().mockResolvedValue(undefined)

    const boostBtn = wrapper.get('.reference-retouch-btn')
    expect(boostBtn.text()).toContain('Boost Color')
    expect(boostBtn.text()).toContain('+75%')
    await boostBtn.trigger('click')
    await flushPromises()
    expect(store.retouchShotReferenceColor).toHaveBeenCalledWith('shot-3', '16:9')

    await wrapper.get('button[aria-label="Reduce color boost"]').trigger('click')
    expect(store.retouchShotReferenceColor).toHaveBeenCalledWith('shot-3', '16:9', { action: 'decrease', strength: undefined })

    await wrapper.get('button[aria-label="Increase color boost"]').trigger('click')
    expect(store.retouchShotReferenceColor).toHaveBeenCalledWith('shot-3', '16:9', { action: 'increase', strength: undefined })

    await wrapper.get('button[aria-label="Undo color change"]').trigger('click')
    expect(store.retouchShotReferenceColor).toHaveBeenCalledWith('shot-3', '16:9', { action: 'undo', strength: undefined })

    await wrapper.get('button[aria-label="Redo color change"]').trigger('click')
    expect(store.retouchShotReferenceColor).toHaveBeenCalledWith('shot-3', '16:9', { action: 'redo', strength: undefined })

    await wrapper.get('button[aria-label="Reset color boost"]').trigger('click')
    expect(store.retouchShotReferenceColor).toHaveBeenCalledWith('shot-3', '16:9', { action: 'reset', strength: undefined })

    const repaintBtn = wrapper.get('.reference-repaint-btn')
    expect(repaintBtn.text()).toContain('AI Repaint')
    await repaintBtn.trigger('click')
    await flushPromises()
    expect(store.repaintShotReference).toHaveBeenCalledWith('shot-3', '16:9', 'repaint this image')

    const punchBtn = wrapper.get('.reference-punch-btn')
    expect(punchBtn.text()).toContain('Optical Punch-In')

    const blockingCheckbox = wrapper.get('.blocking-checkbox input')
    expect((blockingCheckbox.element as HTMLInputElement).checked).toBe(false)
    await blockingCheckbox.setValue(true)
    expect(wrapper.emitted('update:lockBlocking')?.[0]).toEqual([true])

    const zoomBtn = wrapper.get('.blocking-zoom-generate-btn')
    expect(zoomBtn.text()).toContain('Zoom Camera on')
    await zoomBtn.trigger('click')
    expect(wrapper.emitted('generate-locked-blocking')).toBeTruthy()
  })
})


