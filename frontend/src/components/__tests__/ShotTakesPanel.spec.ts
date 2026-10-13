import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ShotTakesPanel from '@/components/ShotTakesPanel.vue'
import { useStudioStore } from '@/stores/studio'
import type { Shot, ShotTake } from '@/lib/api'

// Mock URL methods
globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock-url')
globalThis.URL.revokeObjectURL = vi.fn()

describe('ShotTakesPanel follow-up composer', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  const mockShot: Shot = {
    id: 'shot-1',
    order_index: 0,
    title: 'Shot 01',
    start_ms: 0,
    end_ms: 5000,
    duration_ms: 5000,
    framing: 'Medium',
    action: '',
    dialogue: [],
    sound: '',
    image_prompt: 'A person standing by the counter',
    video_prompt: '',
    title_card_text: '',
    character_ids: [],
    status: 'planned',
    image: null,
    clip: null,
    media_variants: [],
    takes: [
      {
        id: 'take-1',
        shot_id: 'shot-1',
        aspect_ratio: '16:9',
        media_type: 'image',
        order_index: 0,
        selected: true,
        source: 'chatgpt',
        url: '/media/take-1.png',
        created_at: '2026-10-10T12:00:00Z',
      } as unknown as ShotTake,
    ],
  }

  it('preserves typed text in editPrompt when an image is dropped', async () => {
    const wrapper = mount(ShotTakesPanel, {
      props: {
        shot: mockShot,
        aspect: '16:9',
      },
    })
    await flushPromises()

    const textarea = wrapper.find<HTMLTextAreaElement>('.composer-textarea')
    expect(textarea.exists()).toBe(true)

    // User types into the textarea
    await textarea.setValue('Change the lighting to warm sunset')
    expect((textarea.element as HTMLTextAreaElement).value).toBe('Change the lighting to warm sunset')

    // User drops a screenshot file
    const mockFile = new File(['mock content'], 'Screenshot 2026-10-10.png', { type: 'image/png' })
    const dropEvent = {
      preventDefault: vi.fn(),
      stopPropagation: vi.fn(),
      dataTransfer: {
        files: [mockFile],
      },
    }

    await textarea.trigger('drop', dropEvent)
    await flushPromises()

    // The typed text should still be preserved
    expect((textarea.element as HTMLTextAreaElement).value).toBe('Change the lighting to warm sunset')

    // The dropped file should be attached as a chip
    const chips = wrapper.findAll('.attachment-chip')
    expect(chips.length).toBe(1)
  })

  it('triggers Instant Color Boost and AI Repaint actions on an image take', async () => {
    const store = useStudioStore()
    const boostSpy = vi.spyOn(store, 'retouchShotTakeColor').mockResolvedValue(mockShot)
    const editSpy = vi.spyOn(store, 'editShotImageTake').mockResolvedValue(undefined)

    const wrapper = mount(ShotTakesPanel, {
      props: {
        shot: mockShot,
        aspect: '16:9',
      },
    })
    await flushPromises()

    const boostBtn = wrapper.find('.take-retouch-btn.boost')
    expect(boostBtn.exists()).toBe(true)
    await boostBtn.trigger('click')
    await flushPromises()
    expect(boostSpy).toHaveBeenCalledWith('shot-1', 'take-1')

    const repaintBtn = wrapper.find('.take-retouch-btn.repaint')
    expect(repaintBtn.exists()).toBe(true)
    await repaintBtn.trigger('click')
    await flushPromises()
    expect(editSpy).toHaveBeenCalledWith('shot-1', 'take-1', 'Retouch this image', [])
  })
})

