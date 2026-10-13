import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { api, type ProjectDetail, type Job } from '@/lib/api'
import { useStudioStore } from '../studio'

const project = (id: string, episode = `${id}-episode`) => ({ id, current_episode_id: episode } as ProjectDetail)
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

beforeEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('project navigation during generation', () => {
  it('ignores stale project loads and restores episodes per project', async () => {
    const store = useStudioStore()
    store.currentProject = project('A')
    store.currentEpisodeId = 'A-episode'
    localStorage.setItem('cutline:active-episode:B', 'B-saved')
    const slow = deferred<ProjectDetail>()
    const load = vi.spyOn(api, 'project').mockImplementation((id) => id === 'B' ? slow.promise : Promise.resolve(project(id)))
    const pending = store.loadProject('B')
    await store.loadProject('C')
    slow.resolve(project('B'))
    await pending
    expect(store.currentProject?.id).toBe('C')
    expect(load).toHaveBeenCalledWith('B', 'B-saved')
    expect(load).toHaveBeenCalledWith('C', undefined)
  })

  it('does not reload the current project when saving a background shot finishes', async () => {
    const store = useStudioStore()
    store.currentProject = project('A')
    const save = deferred<Awaited<ReturnType<typeof api.updateShot>>>()
    vi.spyOn(api, 'updateShot').mockReturnValue(save.promise)
    const load = vi.spyOn(api, 'project')
    const pending = store.updateShot('A-shot', { framing: 'Wide', duration_ms: 4000, image_prompt: '', video_prompt: 'Wave', title_card_text: '' })
    store.currentProject = project('B')
    save.resolve({} as Awaited<ReturnType<typeof api.updateShot>>)
    await pending
    expect(load).not.toHaveBeenCalled()
  })

  it.each(['project', 'episode'])('does not let completion refresh interrupt an in-flight %s navigation', async (target) => {
    const store = useStudioStore()
    store.currentProject = project('A')
    const job = { id: 'job-A', project_id: 'A', kind: 'shot_clip', status: 'queued' } as Job
    const result = deferred<Job>()
    const destination = deferred<ProjectDetail>()
    vi.spyOn(api, 'generateShotClip').mockResolvedValue(job)
    vi.spyOn(api, 'job').mockReturnValue(result.promise)
    const load = vi.spyOn(api, 'project').mockReturnValue(destination.promise)
    const generation = store.generateShotClip('A-shot', '16:9')
    await flushPromises()
    store.currentEpisodeId = 'A-episode'
    const destinationId = target === 'project' ? 'B' : 'A'
    const episodeId = `${destinationId}-other-episode`
    const navigation = store.loadProject(destinationId, { episodeId })
    result.resolve({ ...job, status: 'completed' })
    await generation
    expect(load).toHaveBeenCalledTimes(1)
    destination.resolve(project(destinationId, episodeId))
    await navigation
    expect(store.currentProject?.id).toBe(destinationId)
    expect(store.currentEpisodeId).toBe(episodeId)
  })

  it('keeps background completion and failure in their originating project', async () => {
    const store = useStudioStore()
    store.currentProject = project('B')
    const job = { id: 'job-A', project_id: 'A', kind: 'shot_clip', status: 'queued' } as Job
    vi.spyOn(api, 'generateShotClip').mockResolvedValue(job)
    vi.spyOn(api, 'job').mockResolvedValue({ ...job, status: 'failed', error: 'provider failed' })
    const load = vi.spyOn(api, 'project')
    await expect(store.generateShotClip('A-shot', '16:9', 'A')).rejects.toThrow('provider failed')
    expect(store.error).toBe('')
    expect(store.clipJobs['A-shot']).toBeUndefined()
    expect(load).not.toHaveBeenCalled()
    store.currentProject = project('A')
    expect(store.clipJobs['A-shot']?.status).toBe('failed')
  })

  it('updates cast characters and face references immediately without requiring a page reload', async () => {
    const store = useStudioStore()
    vi.spyOn(api, 'projectJobs').mockResolvedValue([])
    vi.spyOn(api, 'project').mockResolvedValue({
      ...project('B', 'B-real-episode'),
      characters: [],
    } as ProjectDetail)

    // Simulate loading project B with a stale episode query param from project A
    await store.loadProject('B', { episodeId: 'A-stale-episode', resumeJobs: false })
    expect(store.currentEpisodeId).toBe('B-real-episode')

    const createdChar = {
      id: 'char-1',
      project_id: 'B',
      name: 'MARA',
      notes: 'Steady gaze',
      voice_description: '',
      voice_provider: 'vibevoice',
      voice_id: null,
      voice_settings: {},
      references: [],
      created_at: '2026-10-07T00:00:00Z',
    }
    vi.spyOn(api, 'createCharacter').mockResolvedValue(createdChar)
    await store.createCharacter('MARA')
    expect(store.currentProject?.characters.map((c) => c.name)).toEqual(['MARA'])

    const updatedCharacters = [
      {
        ...createdChar,
        references: [{ id: 'ref-1', url: '/media/cast-ref-1.png', mime_type: 'image/png', created_at: '2026-10-07T00:00:01Z' }],
      },
    ]
    const castJob = { id: 'job-cast-1', project_id: 'B', kind: 'cast_reference', status: 'queued' } as Job
    vi.spyOn(api, 'generateCharacterReference').mockResolvedValue(castJob)
    vi.spyOn(api, 'job').mockResolvedValue({ ...castJob, status: 'completed' })
    vi.spyOn(api, 'characters').mockResolvedValue(updatedCharacters)
    vi.spyOn(api, 'project').mockResolvedValue({
      ...project('B', 'B-real-episode'),
      characters: updatedCharacters,
    } as ProjectDetail)

    await store.generateCharacterReference('char-1')
    expect(store.currentProject?.characters[0]?.references).toHaveLength(1)
    expect(store.currentProject?.characters[0]?.references[0]?.url).toBe('/media/cast-ref-1.png')
  })

  it('correctly scopes isProcessingClip to the target aspect ratio', () => {
    const store = useStudioStore()
    store.currentProject = project('A')
    const job = {
      id: 'job-clip-portrait',
      project_id: 'A',
      kind: 'shot_clip',
      status: 'running',
      message: 'Sending shot to Google Flow',
      attempts: [{ shot_id: 'shot-1', aspect_ratio: '9:16' }],
    } as unknown as Job

    store.clipJobs = { 'shot-1': job }
    expect(store.isProcessingClip('shot-1')).toBe(true)
    expect(store.isProcessingClip('shot-1', '9:16')).toBe(true)
    expect(store.isProcessingClip('shot-1', '16:9')).toBe(false)
  })

  it('correctly unblocks the first aspect in isProcessingShot when phase 2 (2/2) starts', () => {
    const store = useStudioStore()
    store.currentProject = project('A')
    const job = {
      id: 'job-image-both',
      project_id: 'A',
      kind: 'shot_image',
      status: 'running',
      message: 'Generating 9:16 storyboard (2/2)',
      attempts: [{ shot_id: 'shot-1', aspect_ratio: '16:9', both_aspects: true }],
    } as unknown as Job

    store.imageJobs = { 'shot-1': job }
    expect(store.isProcessingShot('shot-1')).toBe(true)
    expect(store.isProcessingShot('shot-1', '16:9')).toBe(false)
    expect(store.isProcessingShot('shot-1', '9:16')).toBe(true)
  })

  it('updates shot takes in real time when refreshShot is called', async () => {
    const store = useStudioStore()
    const initialShot = {
      id: 'shot-1',
      title: 'Shot 1',
      takes: [],
    } as unknown as import('@/lib/api').Shot
    store.currentProject = {
      id: 'project-1',
      current_version: {
        scenes: [
          {
            id: 'scene-1',
            shots: [initialShot],
          },
        ],
      },
    } as unknown as ProjectDetail

    const updatedShot = {
      ...initialShot,
      takes: [
        {
          id: 'take-1',
          shot_id: 'shot-1',
          media_type: 'clip',
          aspect_ratio: '16:9',
          url: '/media/take-1.mp4',
          created_at: '2026-10-09T12:00:00Z',
          selected: true,
        },
      ],
    } as unknown as import('@/lib/api').Shot

    vi.spyOn(api, 'shot').mockResolvedValue(updatedShot)

    await store.refreshShot('shot-1')

    const shots = store.currentProject?.current_version?.scenes[0]?.shots
    expect(shots?.[0]?.takes).toHaveLength(1)
    expect(shots?.[0]?.takes?.[0]?.id).toBe('take-1')
  })
})
