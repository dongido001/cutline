import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ShotNotesPanel from '../ShotNotesPanel.vue'
import { api, type Shot, type ShotNote } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  api: {
    shotNotes: vi.fn(),
    createShotNote: vi.fn(),
    updateShotNote: vi.fn(),
    deleteShotNote: vi.fn(),
    addShotNoteAttachments: vi.fn(),
    deleteShotNoteAttachment: vi.fn(),
  },
}))

describe('ShotNotesPanel', () => {
  const dummyShot = {
    id: 'shot-123',
    title: 'The encounter',
    duration_ms: 5000,
    framing: 'Medium',
    action: 'Tobi speaks',
    dialogue: [],
    sound: '',
    title_card_text: '',
    order_index: 0,
    media_variants: [],
  } as unknown as Shot

  it('renders notes, supports adding a note and resolving it', async () => {
    const existingNotes: ShotNote[] = [
      {
        id: 'note-1',
        shot_id: 'shot-123',
        body: 'Remove boom mic from upper corner',
        resolved: false,
        attachments: [],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      },
    ]
    vi.mocked(api.shotNotes).mockResolvedValue(existingNotes)

    const wrapper = mount(ShotNotesPanel, {
      props: { shot: dummyShot },
    })

    await flushPromises()
    expect(wrapper.text()).toContain('Shot notes')
    expect(wrapper.text()).toContain('1 unresolved')
    expect(wrapper.text()).toContain('Remove boom mic from upper corner')

    // Mark as resolved
    vi.mocked(api.updateShotNote).mockResolvedValue({
      ...existingNotes[0]!,
      resolved: true,
    })

    const resolveBtn = wrapper.find('.resolve-toggle-btn')
    expect(resolveBtn.exists()).toBe(true)
    await resolveBtn.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('changed')).toBeTruthy()
    expect(wrapper.text()).toContain('All resolved')
  })

  it('supports adding a new note and attaching files/screenshots', async () => {
    vi.mocked(api.shotNotes).mockResolvedValue([])
    vi.mocked(api.createShotNote).mockImplementation(async (_shotId, body) => ({
      id: 'note-new',
      shot_id: 'shot-123',
      body,
      resolved: false,
      attachments: [],
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }))

    const wrapper = mount(ShotNotesPanel, {
      props: { shot: dummyShot },
    })
    await flushPromises()

    const textarea = wrapper.find('textarea')
    await textarea.setValue('Fix the lighting flicker')

    const form = wrapper.find('form.note-composer')
    await form.trigger('submit')
    await flushPromises()

    expect(api.createShotNote).toHaveBeenCalledWith('shot-123', 'Fix the lighting flicker', [])
    expect(wrapper.text()).toContain('Fix the lighting flicker')
  })

  it('supports in-drawer mode and hideHeader prop', async () => {
    vi.mocked(api.shotNotes).mockResolvedValue([])

    const wrapper = mount(ShotNotesPanel, {
      props: {
        shot: dummyShot,
        inDrawer: true,
        hideHeader: true,
      },
    })
    await flushPromises()

    expect(wrapper.classes()).toContain('in-drawer')
    expect(wrapper.find('.notes-header').exists()).toBe(false)
    expect(wrapper.find('form.note-composer').exists()).toBe(true)
  })
})
