import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ReleaseKitPanel from '@/components/ReleaseKitPanel.vue'
import { api, type Job, type ProjectDetail, type ReleaseKit } from '@/lib/api'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      ...actual.api,
      releaseKit: vi.fn<typeof actual.api.releaseKit>(),
      updateReleaseKit: vi.fn<typeof actual.api.updateReleaseKit>(),
      suggestReleaseCopy: vi.fn<typeof actual.api.suggestReleaseCopy>(),
      generateReleaseCover: vi.fn<typeof actual.api.generateReleaseCover>(),
      deleteReleaseKitFrame: vi.fn<typeof actual.api.deleteReleaseKitFrame>(),
      addReleaseKitFrame: vi.fn<typeof actual.api.addReleaseKitFrame>(),
      promoteReleaseKitFrame: vi.fn<typeof actual.api.promoteReleaseKitFrame>(),
      autoImportReleaseKitFrames: vi.fn<typeof actual.api.autoImportReleaseKitFrames>(),
      projectJobs: vi.fn<typeof actual.api.projectJobs>(),
      job: vi.fn<typeof actual.api.job>(),
    },
  }
})

const project = { id: 'proj-1', title: 'Night Market' } as unknown as ProjectDetail

function makeKit(overrides: Partial<ReleaseKit> = {}): ReleaseKit {
  return {
    id: 'kit-1',
    project_id: 'proj-1',
    episode_id: 'ep-1',
    compilation_id: null,
    variant: 'landscape',
    scope_label: 'Episode 1: The Stall',
    brief: '',
    frames: [{ id: 'f1', filename: 'f.jpg', url: '/media/f.jpg', timeline_ms: 61000, source_ms: 1000, label: 'Argument', created_at: '' }],
    title_options: [
      { id: 't1', text: "She Won't Sell", angle: 'stakes', custom: false },
      { id: 't2', text: 'What would you risk?', angle: 'question', custom: false },
    ],
    selected_title_id: 't1',
    description: 'Ada draws a line.',
    tags: ['drama'],
    cover_ideas: [{ id: 'i1', concept: 'Ada guarding the stall', headline: 'NOT FOR SALE', prompt: 'Low angle' }],
    covers: [
      { id: 'c1', idea_id: 'i1', concept: 'Ada', headline: 'NOT FOR SALE', prompt: '', provider: 'gemini_web', status: 'ready', filename: 'c1.jpg', url: '/media/c1.jpg', error: null, job_id: 'j1', created_at: '' },
      { id: 'c2', idea_id: 'i1', concept: 'Ada', headline: 'NOT FOR SALE', prompt: '', provider: 'gemini_web', status: 'ready', filename: 'c2.jpg', url: '/media/c2.jpg', error: null, job_id: 'j2', created_at: '' },
    ],
    selected_cover_id: 'c1',
    copy_provider: 'chatgpt_web',
    created_at: '',
    updated_at: '',
    ...overrides,
  }
}

function mountPanel() {
  return mount(ReleaseKitPanel, {
    props: { project, variant: 'landscape', episodeId: 'ep-1' },
  })
}

describe('ReleaseKitPanel', () => {
  beforeEach(() => {
    vi.mocked(api.releaseKit).mockReset().mockResolvedValue(makeKit())
    vi.mocked(api.updateReleaseKit).mockReset().mockImplementation(async (_id, patch) => makeKit(patch as Partial<ReleaseKit>))
    vi.mocked(api.projectJobs).mockReset().mockResolvedValue([])
    vi.mocked(api.suggestReleaseCopy).mockReset()
    vi.mocked(api.generateReleaseCover).mockReset()
    vi.mocked(api.job).mockReset()
  })

  it('loads the kit for the episode and variant scope', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(api.releaseKit).toHaveBeenCalledWith('proj-1', { variant: 'landscape', episodeId: 'ep-1', compilationId: undefined })
    expect(wrapper.text()).toContain('Episode 1: The Stall')
    expect(wrapper.findAll('.frame-card')).toHaveLength(1)
    expect(wrapper.findAll('.title-option:not(.add)')).toHaveLength(2)
  })

  it('seeks across clips and adds the exact frame', async () => {
    vi.mocked(api.addReleaseKitFrame).mockResolvedValue(makeKit())
    const segments = [
      { id: 'a', start_ms: 0, duration_ms: 2000, source: { kind: 'shot_clip' as const, ref_id: 'shot-1' }, source_in_ms: 0, label: 'Opening', url: '/media/a.mp4', mime_type: 'video/mp4' },
      { id: 'b', start_ms: 2000, duration_ms: 3000, source: { kind: 'shot_image' as const, ref_id: 'shot-2' }, source_in_ms: 500, label: 'Close-up', url: '/media/b.png', mime_type: 'image/png' },
    ]
    const wrapper = mount(ReleaseKitPanel, {
      props: { project, variant: 'landscape', episodeId: 'ep-1', segments, initialMs: 1000 },
    })
    await flushPromises()
    expect(wrapper.find('.scrub-preview video').attributes('src')).toBe('/media/a.mp4')
    expect(wrapper.get('.scrub-time').text()).toContain('0:01.00')

    await wrapper.get('input[aria-label="Seek frame"]').setValue('3200')
    expect(wrapper.find('.scrub-preview video').exists()).toBe(false)
    expect(wrapper.get('.scrub-preview img').attributes('src')).toBe('/media/b.png')
    expect(wrapper.get('.scrub-label').text()).toBe('Close-up')

    await wrapper.get('button[aria-label="Forward one frame"]').trigger('click')
    const add = wrapper.findAll('button').find((item) => item.text().includes('Add this frame'))!
    await add.trigger('click')
    await flushPromises()
    expect(api.addReleaseKitFrame).toHaveBeenCalledWith('kit-1', {
      source: { kind: 'shot_image', ref_id: 'shot-2' },
      source_ms: 1733,
      timeline_ms: 3233,
      label: 'Close-up',
    })

    await wrapper.get('button[aria-label="Next clip"]').trigger('click')
    expect(wrapper.get('.scrub-time').text()).toContain('0:02.00')
    await wrapper.get('button[aria-label="Previous clip"]').trigger('click')
    expect(wrapper.get('.scrub-time').text()).toContain('0:00.00')
  })

  it('selects a title and a cover', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.findAll('.title-option .radio')[1]!.trigger('click')
    expect(api.updateReleaseKit).toHaveBeenCalledWith('kit-1', { selected_title_id: 't2' })
    await flushPromises()
    await wrapper.findAll('.cover-frame')[1]!.trigger('click')
    expect(api.updateReleaseKit).toHaveBeenLastCalledWith('kit-1', { selected_cover_id: 'c2' })
  })

  it('adds tags on Enter without duplicates', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    const input = wrapper.find('.tag-box input')
    await input.setValue('Drama, #family')
    await input.trigger('keydown', { key: 'Enter' })
    expect(api.updateReleaseKit).toHaveBeenCalledWith('kit-1', { tags: ['drama', 'family'] })
  })

  it('runs a suggestion job and reloads the kit when it finishes', async () => {
    vi.useFakeTimers()
    try {
      const queued = { id: 'job-1', kind: 'release_copy', status: 'queued', progress: 0, message: 'Queued' } as Job
      vi.mocked(api.suggestReleaseCopy).mockResolvedValue(queued)
      vi.mocked(api.job).mockResolvedValue({ ...queued, status: 'completed', progress: 1 })
      const wrapper = mountPanel()
      await flushPromises()
      await wrapper.find('select[aria-label="Text AI"]').setValue('gemini_web')
      await wrapper.find('.suggest-row button.primary').trigger('click')
      await flushPromises()
      expect(api.suggestReleaseCopy).toHaveBeenCalledWith('kit-1', 'gemini_web')
      vi.mocked(api.releaseKit).mockClear()
      await vi.advanceTimersByTimeAsync(1300)
      await flushPromises()
      expect(api.job).toHaveBeenCalledWith('job-1')
      expect(api.releaseKit).toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('generates a cover from an idea with the edited headline', async () => {
    vi.mocked(api.generateReleaseCover).mockResolvedValue({ id: 'job-2' } as Job)
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('.idea-card .headline input').setValue('NEVER SELLING')
    await wrapper.find('.idea-card button.primary').trigger('click')
    await flushPromises()
    expect(api.generateReleaseCover).toHaveBeenCalledWith('kit-1', { idea_id: 'i1', headline: 'NEVER SELLING', provider: null })
  })

  it('promotes a video frame to a thumbnail cover', async () => {
    vi.mocked(api.promoteReleaseKitFrame).mockResolvedValue(makeKit())
    const wrapper = mountPanel()
    await flushPromises()
    const promoteBtn = wrapper.find('.mini-frame-footer button.primary')
    expect(promoteBtn.exists()).toBe(true)
    await promoteBtn.trigger('click')
    await flushPromises()
    expect(api.promoteReleaseKitFrame).toHaveBeenCalledWith('kit-1', 'f1', { style: 'punch', headline: null })
  })

  it('auto-imports shot frames into the kit', async () => {
    vi.mocked(api.autoImportReleaseKitFrames).mockResolvedValue(makeKit())
    const wrapper = mountPanel()
    await flushPromises()
    const autoImportBtn = wrapper.findAll('button').find((btn) => btn.text().includes('Auto-Import Shot Frames'))!
    expect(autoImportBtn).toBeDefined()
    await autoImportBtn.trigger('click')
    await flushPromises()
    expect(api.autoImportReleaseKitFrames).toHaveBeenCalledWith('kit-1')
  })

  it('toggles studio tabs and mockup views', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    expect(wrapper.find('.thumbnail-studio').isVisible()).toBe(true)

    // Switch to Titles & Description tab
    await wrapper.findAll('.tab-btn')[1]!.trigger('click')
    expect(wrapper.find('.metadata-studio').isVisible()).toBe(true)

    // Switch to Video Frames tab
    await wrapper.findAll('.tab-btn')[2]!.trigger('click')
    expect(wrapper.find('.frames-studio').isVisible()).toBe(true)

    // Switch back to Studio
    await wrapper.findAll('.tab-btn')[0]!.trigger('click')
    expect(wrapper.find('.thumbnail-studio').isVisible()).toBe(true)

    // Switch mockup view
    await wrapper.find('.mockup-btn').trigger('click') // artwork
    expect(wrapper.find('.art-frame').exists()).toBe(true)
  })

  it('suggests headline hooks for prompt and auto-fills on submit if left empty', async () => {
    vi.mocked(api.generateReleaseCover).mockResolvedValue({ id: 'job-custom' } as Job)
    const wrapper = mountPanel()
    await flushPromises()

    const promptTextarea = wrapper.find('.custom-cover textarea')
    await promptTextarea.setValue('they pushed too early')
    await flushPromises()

    // Suggestions chips should be rendered
    const hookPills = wrapper.findAll('.hook-pill')
    expect(hookPills.length).toBeGreaterThan(0)
    expect(hookPills.some((p) => p.text().includes('PUSHED TOO EARLY'))).toBe(true)

    // Clicking a chip sets the custom headline
    await hookPills[0]!.trigger('click')
    const headlineInput = wrapper.find('.custom-cover .headline-field-row input')
    expect((headlineInput.element as HTMLInputElement).value).toContain('PUSHED TOO EARLY')

    // Submit form
    await wrapper.find('.custom-cover').trigger('submit')
    await flushPromises()
    expect(api.generateReleaseCover).toHaveBeenCalledWith('kit-1', expect.objectContaining({
      prompt: 'they pushed too early',
      headline: expect.stringContaining('PUSHED TOO EARLY'),
    }))
  })
})
