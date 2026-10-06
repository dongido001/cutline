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
})
