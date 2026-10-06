import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DeepReviewPanel from '@/components/DeepReviewPanel.vue'
import { api } from '@/lib/api'
import { deepReviewApi, type ReviewRun } from '@/lib/deepReview'

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    api: {
      ...actual.api,
      cancelJob: vi.fn<typeof actual.api.cancelJob>(),
    },
  }
})

vi.mock('@/lib/deepReview', async () => {
  const actual = await vi.importActual<typeof import('@/lib/deepReview')>('@/lib/deepReview')
  return {
    ...actual,
    deepReviewApi: {
      get: vi.fn<typeof actual.deepReviewApi.get>(),
      start: vi.fn<typeof actual.deepReviewApi.start>(),
      draft: vi.fn<typeof actual.deepReviewApi.draft>(),
      decide: vi.fn<typeof actual.deepReviewApi.decide>(),
      undo: vi.fn<typeof actual.deepReviewApi.undo>(),
    },
  }
})

function makeCompletedRun(overrides: Partial<ReviewRun> = {}): ReviewRun {
  return {
    id: 'run-1',
    script_version_id: 'ver-3',
    job_id: 'job-1',
    provider: 'ollama',
    status: 'completed',
    stale: false,
    error: null,
    context: [
      { episode_id: 'ep-1', episode_number: 1, title: 'Harbor Lock', version_id: 'ver-1', draft: false },
      { episode_id: 'ep-2', episode_number: 2, title: 'Night Shift', version_id: 'ver-2', draft: true },
      { episode_id: 'ep-3', episode_number: 3, title: 'Dead Drop', version_id: 'ver-3', draft: false },
    ],
    report: {
      assessment: 'Strong noir atmosphere, with one posture contradiction and one repeated line.',
      strengths: ['Tight callback to Episode 1 brass key.'],
      priorities: ['Resolve Maya posture between Shot 1 and Shot 2.'],
      findings: [
        {
          id: 'find-1',
          kind: 'confirmed_inconsistency',
          category: 'continuity',
          title: 'Seated vs standing contradiction',
          reason: 'Shot 1 places Maya in the booth, while Shot 2 says she remains standing without sitting.',
          recommendation: 'Have Maya rise from the booth in Shot 2.',
          evidence: [
            {
              version_id: 'ver-3',
              episode_id: 'ep-3',
              shot_id: 'shot-2',
              field: 'action',
              quote: 'Without sitting down, Maya remains standing by the door',
              label: 'Episode 3 · Shot 2',
            },
            {
              version_id: 'ver-1',
              episode_id: 'ep-1',
              shot_id: 'ep1-shot-1',
              field: 'action',
              quote: 'Maya locks the iron gate',
              label: 'Episode 1 · Shot 1',
            },
          ],
          patches: [
            {
              shot_id: 'shot-2',
              field: 'action',
              before: 'Without sitting down, Maya remains standing by the door.',
              after: 'Maya rises from the booth and pauses by the door.',
            },
          ],
          status: 'open',
        },
        {
          id: 'find-2',
          kind: 'editorial_suggestion',
          category: 'dialogue',
          title: 'Repeated line from Episode 1',
          reason: 'Maya repeats her line verbatim.',
          recommendation: 'Trim the repeated line.',
          evidence: [
            {
              version_id: 'ver-3',
              episode_id: 'ep-3',
              shot_id: 'shot-2',
              field: 'dialogue',
              quote: 'We already said nobody gets through tonight.',
              label: 'Episode 3 · Shot 2',
            },
          ],
          patches: [],
          status: 'open',
        },
      ],
    },
    draft_job: null,
    applied_version_id: null,
    undo_version_id: null,
    ...overrides,
  }
}

describe('DeepReviewPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders empty state and starts a review run', async () => {
    vi.mocked(deepReviewApi.get).mockResolvedValue({ latest: null, previous_success: null })
    vi.mocked(deepReviewApi.start).mockResolvedValue({
      ...makeCompletedRun(),
      status: 'queued',
      report: null,
    })

    const wrapper = mount(DeepReviewPanel, {
      props: { versionId: 'ver-3', episodeId: 'ep-3' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('A closer look at your story')
    const runBtn = wrapper.find('button.deep-primary')
    expect(runBtn.text()).toContain('Run review')

    await runBtn.trigger('click')
    await flushPromises()

    expect(deepReviewApi.start).toHaveBeenCalledWith('ver-3')
    expect(wrapper.text()).toContain('Review queued…')
    wrapper.unmount()
  })

  it('filters findings, navigates to evidence, and previews/applies revisions', async () => {
    const run = makeCompletedRun()
    vi.mocked(deepReviewApi.get).mockResolvedValue({ latest: run, previous_success: run })
    vi.mocked(deepReviewApi.decide).mockResolvedValue({
      version_id: 'ver-4',
      run: {
        ...run,
        stale: true,
        applied_version_id: 'ver-4',
        undo_version_id: 'ver-3',
      },
    })

    const wrapper = mount(DeepReviewPanel, {
      props: { versionId: 'ver-3', episodeId: 'ep-3' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Strong noir atmosphere')
    expect(wrapper.text()).toContain('(draft)')
    expect(wrapper.findAll('article.deep-finding')).toHaveLength(2)

    // Filter by Confirmed
    const filterButtons = wrapper.findAll('.deep-filters button')
    await filterButtons[1]!.trigger('click')
    expect(wrapper.findAll('article.deep-finding')).toHaveLength(1)
    expect(wrapper.text()).toContain('Seated vs standing contradiction')

    // Evidence navigation (current episode shot & earlier episode jump)
    const evidenceButtons = wrapper.findAll('.deep-evidence button')
    await evidenceButtons[0]!.trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['shot-2'])

    await evidenceButtons[1]!.trigger('click')
    expect(wrapper.emitted('jump')?.[0]).toEqual(['ep-1', 'ep1-shot-1'])

    // Open preview and apply revision
    const decisionButtons = wrapper.findAll('.deep-decisions button')
    await decisionButtons[0]!.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Preview the correction')
    const applyBtn = wrapper.find('.deep-preview .deep-primary')
    await applyBtn.trigger('click')
    await flushPromises()

    expect(deepReviewApi.decide).toHaveBeenCalledWith('run-1', 'find-1', {
      decision: 'accepted',
      expected_version_id: 'ver-3',
      patches: run.report!.findings[0]!.patches,
    })
    expect(wrapper.emitted('applied')).toBeTruthy()
    wrapper.unmount()
  })

  it('shows stale warning, undo action, and preserves previous_success when rerun fails', async () => {
    const completed = makeCompletedRun({
      stale: true,
      applied_version_id: 'ver-4',
      undo_version_id: 'ver-3',
    })
    const failedLatest: ReviewRun = {
      ...completed,
      id: 'run-2',
      status: 'failed',
      error: 'Provider timeout',
      report: null,
    }
    vi.mocked(deepReviewApi.get).mockResolvedValue({
      latest: failedLatest,
      previous_success: completed,
    })
    vi.mocked(deepReviewApi.undo).mockResolvedValue({ version_id: 'ver-3' })

    const wrapper = mount(DeepReviewPanel, {
      props: { versionId: 'ver-4', episodeId: 'ep-3' },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Review failed: Provider timeout')
    expect(wrapper.text()).toContain('Showing the last completed review.')
    expect(wrapper.text()).toContain('This review is outdated.')

    const undoBtn = wrapper.find('button.deep-undo')
    expect(undoBtn.exists()).toBe(true)
    await undoBtn.trigger('click')
    await flushPromises()

    expect(deepReviewApi.undo).toHaveBeenCalledWith('run-1', 'ver-4')
    expect(wrapper.emitted('applied')).toBeTruthy()
    wrapper.unmount()
  })
})
