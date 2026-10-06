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
})
