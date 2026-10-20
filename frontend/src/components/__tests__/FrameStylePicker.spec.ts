import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FrameStylePicker from '@/components/FrameStylePicker.vue'
import type { TimelineGeometry } from '@/lib/api'

function geometry(overrides: Partial<TimelineGeometry> = {}): TimelineGeometry {
  return {
    source_aspect: 'auto',
    fit: 'cover',
    focal_x: 0.5,
    focal_y: 0.5,
    zoom: 1,
    position: 'center',
    scale: 1,
    ...overrides,
  }
}

describe('FrameStylePicker', () => {
  it('hides thickness and color until a border style is chosen', () => {
    const wrapper = mount(FrameStylePicker, { props: { geometry: geometry() } })
    expect(wrapper.find('[aria-label="Border thickness"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="Corner radius"]').exists()).toBe(true)
  })

  it('emits a style patch and a commit when a style is picked', async () => {
    const wrapper = mount(FrameStylePicker, { props: { geometry: geometry() } })
    const neon = wrapper.findAll('.frame-style-btn').find((b) => b.text() === 'Neon')!
    await neon.trigger('click')
    expect(wrapper.emitted('update')?.[0]).toEqual([{ frame_style: 'neon' }])
    expect(wrapper.emitted('change')).toHaveLength(1)
  })

  it('applies corner presets and color swatches', async () => {
    const wrapper = mount(FrameStylePicker, { props: { geometry: geometry({ frame_style: 'solid' }) } })
    const rounded = wrapper.findAll('.frame-chip').find((b) => b.text() === 'Rounded')!
    await rounded.trigger('click')
    await wrapper.get('[aria-label="Border color #39D0FF"]').trigger('click')
    expect(wrapper.emitted('update')).toEqual([[{ frame_radius: 0.04 }], [{ frame_color: '#39D0FF' }]])
  })

  it('streams slider input without committing until change', async () => {
    const wrapper = mount(FrameStylePicker, { props: { geometry: geometry({ frame_style: 'dashed' }) } })
    const thickness = wrapper.get('[aria-label="Border thickness"]')
    ;(thickness.element as HTMLInputElement).value = '0.02'
    await thickness.trigger('input')
    expect(wrapper.emitted('update')?.[0]).toEqual([{ frame_width: 0.02 }])
    expect(wrapper.emitted('change')).toBeUndefined()
    await thickness.trigger('change')
    expect(wrapper.emitted('change')).toHaveLength(1)
  })
})
