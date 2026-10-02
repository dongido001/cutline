import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api, type AspectRatio, type ImageProviderStatus, type Job, type OutputVariant, type ProjectContinuationInput, type ProjectDetail, type ProjectInput, type ProjectSummary, type ProviderStatus, type ShotCreate, type ShotUpdate, type VoiceProviderInfo, type VoiceAuditionResponse } from '@/lib/api'

const delay = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds))

export const useStudioStore = defineStore('studio', () => {
  const projects = ref<ProjectSummary[]>([])
  const currentProject = ref<ProjectDetail | null>(null)
  const providers = ref<ProviderStatus[]>([])
  const videoProvider = ref<ProviderStatus | null>(null)
  const imageProviders = ref<ImageProviderStatus[]>([])
  const imageJobs = ref<Record<string, Job>>({})
  const castJobs = ref<Record<string, Job>>({})
  const clipJobs = ref<Record<string, Job>>({})
  const processingLipsyncId = ref<string | null>(null)
  const lipsyncJobs = ref<Record<string, Job>>({})
  const currentJob = ref<Job | null>(null)
  const clipJob = ref<Job | null>(null)
  const jobElapsedSeconds = ref(0)
  const busy = ref(false)
  const approvingScript = ref(false)
  const continuingScript = ref(false)
  const processingShotIds = ref<string[]>([])
  const processingClipId = ref<string | null>(null)
  const processingAudioId = ref<string | null>(null)
  const voiceProviders = ref<VoiceProviderInfo[]>([])
  const auditionAudio = ref<Record<string, string>>({})
  const auditioningVoiceId = ref<string | null>(null)
  const finalCutJob = ref<Job | null>(null)
  const draftJob = ref<Job | null>(null)
  const draftAttention = ref<Array<{ code: string; message: string; shot_id?: string }>>([])
  const draftScenes = ref<Array<{
    id: string
    title: string
    order_index: number
    shot_count: number
    images_ready: number
    clips_ready: number
    audio_ready: number
    audio_needed: number
    status: 'pending' | 'in_progress' | 'ready'
  }>>([])
  const creatingDraft = ref(false)
  const renderingFinalCut = ref(false)
  const cancellingJobIds = ref<string[]>([])
  const error = ref('')
  const watchingJobIds = new Set<string>()
  const watchers = new Map<string, { promise: Promise<Job | null>; listeners: Set<(job: Job) => void> }>()

  const pendingSuggestions = computed(
    () => currentProject.value?.current_version?.suggestions.filter((item) => item.status === 'pending') ?? [],
  )

  function beginShotProcessing(id: string) {
    if (!processingShotIds.value.includes(id)) processingShotIds.value = [...processingShotIds.value, id]
  }

  function endShotProcessing(id: string) {
    processingShotIds.value = processingShotIds.value.filter((shotId) => shotId !== id)
  }

  function isProcessingShot(id: string) {
    return processingShotIds.value.includes(id)
  }

  function isGeneratingCast(id: string) {
    const job = castJobs.value[id]
    return Boolean(job && (job.status === 'queued' || job.status === 'running'))
  }

  function isProcessingClip(id: string) {
    const job = clipJobs.value[id]
    return Boolean(job && (job.status === 'queued' || job.status === 'running'))
  }

  function isCancellingJob(id: string) {
    return cancellingJobIds.value.includes(id)
  }

  function jobMeta(job: Job): Record<string, unknown> {
    const rows = Array.isArray(job.attempts) ? job.attempts : []
    return (rows.find((item) => item && typeof item === 'object' && (item.shot_id || item.character_id)) as Record<string, unknown> | undefined) ?? {}
  }

  function withQueueLabel(job: Job): Job {
    if (job.status === 'queued' && job.queue_position) {
      return { ...job, message: `Queued · position ${job.queue_position}` }
    }
    return job
  }

  async function watchJob(
    jobId: string,
    onUpdate: (job: Job) => void,
    options?: { reloadOnComplete?: boolean },
  ): Promise<Job | null> {
    if (!jobId) return null
    const existing = watchers.get(jobId)
    if (existing) {
      existing.listeners.add(onUpdate)
      try {
        return await existing.promise
      } finally {
        existing.listeners.delete(onUpdate)
      }
    }
    const listeners = new Set<(job: Job) => void>([onUpdate])
    const run = (async (): Promise<Job | null> => {
      watchingJobIds.add(jobId)
      const emit = (job: Job) => {
        for (const listener of [...listeners]) listener(job)
      }
      const fetchJob = async (): Promise<Job> => {
        let lastError: unknown
        for (let attempt = 0; attempt < 4; attempt += 1) {
          try {
            return withQueueLabel(await api.job(jobId))
          } catch (error) {
            lastError = error
            await delay(400 * (attempt + 1))
          }
        }
        throw lastError instanceof Error ? lastError : new Error('Failed to load job status')
      }
      try {
        let job = await fetchJob()
        emit(job)
        while (job.status === 'queued' || job.status === 'running') {
          await delay(500)
          try {
            job = await fetchJob()
            emit(job)
          } catch {
            // Transient status failures should not abort an otherwise healthy job.
            continue
          }
        }
        if (options?.reloadOnComplete !== false && job.status === 'completed' && currentProject.value) {
          await loadProject(currentProject.value.id, { resumeJobs: false })
        }
        return job
      } finally {
        watchingJobIds.delete(jobId)
        watchers.delete(jobId)
      }
    })()
    watchers.set(jobId, { promise: run, listeners })
    return run
  }

  async function resumeActiveJobs(projectId: string) {
    try {
      const jobs = await api.projectJobs(projectId, true)
      for (const raw of jobs) {
        const job = withQueueLabel(raw)
        const meta = jobMeta(job)
        if (job.kind === 'cast_reference') {
          const characterId = typeof meta.character_id === 'string' ? meta.character_id : null
          if (!characterId) continue
          castJobs.value = { ...castJobs.value, [characterId]: job }
          void watchJob(job.id, (next) => {
            if (currentProject.value?.id !== projectId) return
            castJobs.value = { ...castJobs.value, [characterId]: next }
          }).then((finalJob) => {
            if (currentProject.value?.id !== projectId) return
            if (finalJob?.status === 'failed') {
              error.value = String(finalJob.error || 'Face reference generation failed')
            }
          })
          continue
        }
        if (job.kind === 'shot_image') {
          const shotId = typeof meta.shot_id === 'string' ? meta.shot_id : null
          if (!shotId) continue
          beginShotProcessing(shotId)
          imageJobs.value = { ...imageJobs.value, [shotId]: job }
          void watchJob(job.id, (next) => {
            if (currentProject.value?.id !== projectId) return
            imageJobs.value = { ...imageJobs.value, [shotId]: next }
          }).then((finalJob) => {
            if (currentProject.value?.id !== projectId) return
            endShotProcessing(shotId)
            if (finalJob?.status === 'failed') {
              error.value = String(finalJob.error || 'Image generation failed')
            }
          })
          continue
        }
        if (job.kind === 'shot_clip') {
          const shotId = typeof meta.shot_id === 'string' ? meta.shot_id : null
          if (!shotId) continue
          processingClipId.value = shotId
          clipJob.value = job
          clipJobs.value = { ...clipJobs.value, [shotId]: job }
          void watchJob(job.id, (next) => {
            if (currentProject.value?.id !== projectId) return
            clipJob.value = next
            clipJobs.value = { ...clipJobs.value, [shotId]: next }
          }).then((finalJob) => {
            if (currentProject.value?.id !== projectId) return
            if (processingClipId.value === shotId) processingClipId.value = null
            if (finalJob?.status === 'failed') {
              error.value = String(finalJob.error || 'Could not generate video')
            }
          })
          continue
        }
        if (job.kind === 'shot_lipsync') {
          const shotId = typeof meta.shot_id === 'string' ? meta.shot_id : null
          if (!shotId) continue
          processingLipsyncId.value = shotId
          lipsyncJobs.value = { ...lipsyncJobs.value, [shotId]: job }
          void watchJob(job.id, (next) => {
            if (currentProject.value?.id !== projectId) return
            lipsyncJobs.value = { ...lipsyncJobs.value, [shotId]: next }
          }).then((finalJob) => {
            if (currentProject.value?.id !== projectId) return
            if (processingLipsyncId.value === shotId) processingLipsyncId.value = null
            if (finalJob?.status === 'failed') {
              error.value = String(finalJob.error || 'Dialogue lip-sync failed')
            }
          })
          continue
        }
        if (job.kind === 'create_draft') {
          draftJob.value = job
          creatingDraft.value = true
          void watchJob(job.id, (next) => {
            if (currentProject.value?.id !== projectId) return
            draftJob.value = next
          }).then((finalJob) => {
            if (currentProject.value?.id !== projectId) return
            creatingDraft.value = false
            if (finalJob?.status === 'failed') {
              error.value = String(finalJob.error || 'Create draft failed')
            }
          })
        }
      }
    } catch {
      // Project load still succeeds even if job resume fails.
    }
  }

  async function cancelJob(jobId: string) {
    if (!jobId || isCancellingJob(jobId)) return
    cancellingJobIds.value = [...cancellingJobIds.value, jobId]
    error.value = ''
    try {
      const job = await api.cancelJob(jobId)
      if (currentJob.value?.id === jobId) currentJob.value = job
      if (clipJob.value?.id === jobId) clipJob.value = job
      if (finalCutJob.value?.id === jobId) finalCutJob.value = job
      const shotId = Object.entries(imageJobs.value).find(([, item]) => item.id === jobId)?.[0]
      if (shotId) imageJobs.value = { ...imageJobs.value, [shotId]: job }
      const clipShotId = Object.entries(clipJobs.value).find(([, item]) => item.id === jobId)?.[0]
      if (clipShotId) clipJobs.value = { ...clipJobs.value, [clipShotId]: job }
      const characterId = Object.entries(castJobs.value).find(([, item]) => item.id === jobId)?.[0]
      if (characterId) castJobs.value = { ...castJobs.value, [characterId]: job }
      return job
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not cancel job'
      throw cause
    } finally {
      cancellingJobIds.value = cancellingJobIds.value.filter((id) => id !== jobId)
    }
  }

  async function initialize(projectId?: string) {
    error.value = ''
    try {
      const [providerResult, imageProviderResult, videoProviderResult, projectResult] = await Promise.all([api.providers(), api.imageProviders(), api.videoProvider(), api.projects()])
      providers.value = providerResult
      imageProviders.value = imageProviderResult
      videoProvider.value = videoProviderResult
      projects.value = projectResult
      if (projectId) await loadProject(projectId, { resumeJobs: true })
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not connect to the local API'
    }
  }

  async function loadProject(id: string, options?: { resumeJobs?: boolean }) {
    currentProject.value = await api.project(id)
    if (options?.resumeJobs) {
      imageJobs.value = {}
      castJobs.value = {}
      clipJobs.value = {}
      clipJob.value = null
      processingShotIds.value = []
      processingClipId.value = null
      void resumeActiveJobs(id)
    }
  }

  async function selectProject(id: string) {
    await loadProject(id, { resumeJobs: true })
  }

  async function deleteProject(id: string) {
    error.value = ''
    try {
      await api.deleteProject(id)
      projects.value = await api.projects()
      if (currentProject.value?.id === id) {
        currentProject.value = null
        if (projects.value[0]) await loadProject(projects.value[0].id, { resumeJobs: true })
      }
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete project'
      throw cause
    }
  }

  async function createProject(input: ProjectInput) {
    busy.value = true
    error.value = ''
    jobElapsedSeconds.value = 0
    const startedAt = Date.now()
    try {
      currentJob.value = await api.createProject(input)
      while (currentJob.value.status === 'queued' || currentJob.value.status === 'running') {
        await delay(600)
        jobElapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
        currentJob.value = await api.job(currentJob.value.id)
      }
      if (currentJob.value.status === 'failed') throw new Error(currentJob.value.error || 'Analysis failed')
      if (currentJob.value.status === 'cancelled') return
      projects.value = await api.projects()
      await loadProject(currentJob.value.project_id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not create project'
      throw cause
    } finally {
      busy.value = false
    }
  }

  async function continueProject(input: ProjectContinuationInput) {
    if (!currentProject.value) return
    continuingScript.value = true
    error.value = ''
    jobElapsedSeconds.value = 0
    const startedAt = Date.now()
    try {
      currentJob.value = await api.continueProject(currentProject.value.id, input)
      while (currentJob.value.status === 'queued' || currentJob.value.status === 'running') {
        await delay(600)
        jobElapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
        currentJob.value = await api.job(currentJob.value.id)
      }
      if (currentJob.value.status === 'failed') throw new Error(currentJob.value.error || 'Continuation analysis failed')
      if (currentJob.value.status === 'cancelled') return
      await loadProject(currentProject.value.id)
      projects.value = await api.projects()
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not continue script'
      throw cause
    } finally {
      continuingScript.value = false
    }
  }

  async function decideSuggestion(id: string, decision: 'accepted' | 'rejected') {
    if (!currentProject.value) return
    error.value = ''
    await api.decide(id, decision)
    await loadProject(currentProject.value.id)
  }

  async function approveScript() {
    if (!currentProject.value || currentProject.value.status === 'shot_plan') return
    error.value = ''
    approvingScript.value = true
    try {
      await api.approve(currentProject.value.id)
      await loadProject(currentProject.value.id)
      projects.value = await api.projects()
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not approve script'
    } finally {
      approvingScript.value = false
    }
  }

  async function createShot(sceneId: string, input?: ShotCreate) {
    if (!currentProject.value) return null
    error.value = ''
    try {
      const created = await api.createShot(sceneId, input)
      await loadProject(currentProject.value.id)
      return created
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not create shot'
      throw cause
    }
  }

  async function deleteShot(id: string) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.deleteShot(id)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete shot'
      throw cause
    }
  }

  async function updateShot(id: string, input: ShotUpdate) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.updateShot(id, input)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not update shot'
      throw cause
    }
  }

  async function updateScene(
    id: string,
    input: { set_notes?: string; lighting_notes?: string; wardrobe_notes?: string; props_notes?: string },
  ) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.updateScene(id, input)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not update scene continuity'
      throw cause
    }
  }

  async function generateShotImage(
    id: string,
    aspectRatio?: AspectRatio,
    characterIds?: string[],
    refreshContinuity = false,
    imageReferenceId?: string | null,
  ) {
    if (!currentProject.value) return
    error.value = ''
    beginShotProcessing(id)
    try {
      let job = await api.generateShotImage(id, aspectRatio, characterIds, refreshContinuity, imageReferenceId)
      imageJobs.value = { ...imageJobs.value, [id]: withQueueLabel(job) }
      const finalJob = await watchJob(job.id, (next) => {
        imageJobs.value = { ...imageJobs.value, [id]: next }
      })
      job = finalJob ?? (await api.job(job.id))
      if (job.status === 'failed') throw new Error(job.error || 'Image generation failed')
      if (job.status === 'cancelled') return
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not generate image'
      throw cause
    } finally {
      endShotProcessing(id)
    }
  }

  async function generateShotAudio(id: string) {
    if (!currentProject.value) return
    error.value = ''
    processingAudioId.value = id
    try {
      await api.generateShotAudio(id)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not generate narration'
      throw cause
    } finally {
      processingAudioId.value = null
    }
  }

  async function createCharacter(name: string, notes = '') {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.createCharacter(currentProject.value.id, { name, notes })
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not create character'
      throw cause
    }
  }

  async function syncCast() {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.syncCast(currentProject.value.id)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not sync cast from script'
      throw cause
    }
  }

  async function loadVoiceProviders() {
    try {
      voiceProviders.value = await api.voices()
    } catch {
      // Best-effort load
    }
  }

  async function auditionVoice(provider: string, voiceId: string, text: string = 'sample', characterId?: string) {
    auditioningVoiceId.value = voiceId
    try {
      const res = await api.auditionVoice({ provider, voice_id: voiceId, text })
      if (characterId) {
        auditionAudio.value = { ...auditionAudio.value, [characterId]: res.url }
      } else {
        auditionAudio.value = { ...auditionAudio.value, [voiceId]: res.url }
      }
      return res
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Audition failed'
      throw cause
    } finally {
      auditioningVoiceId.value = null
    }
  }

  async function uploadCustomVoice(file: File, name: string, gender: string = 'male', language: string = 'en-NG', description: string = '') {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('name', name)
    fd.append('gender', gender)
    fd.append('language', language)
    fd.append('description', description)
    try {
      const preset = await api.uploadCustomVoice(fd)
      await loadVoiceProviders()
      return preset
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Failed to upload custom voice'
      throw cause
    }
  }



  async function updateCharacter(id: string, input: { name?: string; notes?: string; voice_provider?: string; voice_id?: string | null; voice_settings?: Record<string, any> }) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.updateCharacter(id, input)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not update character'
      throw cause
    }
  }

  async function deleteCharacter(id: string) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.deleteCharacter(id)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete character'
      throw cause
    }
  }

  async function uploadCharacterReference(id: string, file: File) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.uploadCharacterReference(id, file)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not upload reference image'
      throw cause
    }
  }

  async function generateCharacterReference(id: string, look?: string) {
    if (!currentProject.value) return
    error.value = ''
    try {
      let job = await api.generateCharacterReference(id, look)
      castJobs.value = { ...castJobs.value, [id]: withQueueLabel(job) }
      const finalJob = await watchJob(job.id, (next) => {
        castJobs.value = { ...castJobs.value, [id]: next }
      })
      job = finalJob ?? (await api.job(job.id))
      if (job.status === 'failed') throw new Error(job.error || 'Face reference generation failed')
      if (job.status === 'cancelled') return
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not generate face reference'
      throw cause
    }
  }

  async function deleteCharacterReference(id: string) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.deleteCharacterReference(id)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete reference image'
      throw cause
    }
  }

  async function uploadShotImage(id: string, file: File, aspectRatio?: AspectRatio) {
    if (!currentProject.value) return
    error.value = ''
    beginShotProcessing(id)
    try {
      await api.uploadShotImage(id, file, aspectRatio)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not upload image'
      throw cause
    } finally {
      endShotProcessing(id)
    }
  }

  async function deleteShotImage(id: string, aspectRatio?: AspectRatio) {
    if (!currentProject.value) return
    error.value = ''
    beginShotProcessing(id)
    try {
      await api.deleteShotImage(id, aspectRatio)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete image'
      throw cause
    } finally {
      endShotProcessing(id)
    }
  }

  async function saveShotContinuityFrame(
    id: string,
    file: File,
    sourceShotId: string,
    sourceTimeMs: number,
    aspectRatio: AspectRatio,
  ) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.saveShotContinuityFrame(id, file, sourceShotId, sourceTimeMs, aspectRatio)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not save continuity frame'
      throw cause
    }
  }

  async function deleteShotContinuityFrame(id: string, aspectRatio: AspectRatio) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.deleteShotContinuityFrame(id, aspectRatio)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not remove continuity frame'
      throw cause
    }
  }

  function isProcessingLipsync(id: string) {
    return processingLipsyncId.value === id || lipsyncJobs.value[id]?.status === 'running' || lipsyncJobs.value[id]?.status === 'queued'
  }

  async function lipsyncShot(id: string, aspectRatio?: AspectRatio) {
    if (!currentProject.value) return null
    error.value = ''
    processingLipsyncId.value = id
    try {
      let job = await api.lipsyncShot(id, aspectRatio)
      const initialJob = withQueueLabel(job)
      lipsyncJobs.value = { ...lipsyncJobs.value, [id]: initialJob }
      const finalJob = await watchJob(job.id, (next) => {
        lipsyncJobs.value = { ...lipsyncJobs.value, [id]: next }
      })
      job = finalJob ?? (await api.job(job.id))
      await loadProject(currentProject.value.id)
      if (job.status === 'failed') throw new Error(job.error || 'Lip-sync generation failed')
      return job
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not generate lip-sync'
      throw cause
    } finally {
      if (processingLipsyncId.value === id) processingLipsyncId.value = null
    }
  }

  async function generateShotClip(id: string, aspectRatio?: AspectRatio) {
    if (!currentProject.value) return
    error.value = ''
    processingClipId.value = id
    try {
      let job = await api.generateShotClip(id, aspectRatio)
      const initialJob = withQueueLabel(job)
      clipJob.value = initialJob
      clipJobs.value = { ...clipJobs.value, [id]: initialJob }
      const finalJob = await watchJob(job.id, (next) => {
        clipJob.value = next
        clipJobs.value = { ...clipJobs.value, [id]: next }
      })
      job = finalJob ?? (await api.job(job.id))
      if (job.status === 'failed') throw new Error(job.error || 'Video generation failed')
      if (job.status === 'cancelled') return
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not generate video'
      throw cause
    } finally {
      processingClipId.value = null
    }
  }

  async function uploadShotClip(id: string, file: File, aspectRatio?: AspectRatio) {
    if (!currentProject.value) return
    error.value = ''
    processingClipId.value = id
    try {
      await api.uploadShotClip(id, file, aspectRatio)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not upload video'
      throw cause
    } finally {
      processingClipId.value = null
    }
  }

  async function deleteShotClip(id: string, aspectRatio?: AspectRatio) {
    if (!currentProject.value) return
    error.value = ''
    processingClipId.value = id
    try {
      await api.deleteShotClip(id, aspectRatio)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete video'
      throw cause
    } finally {
      processingClipId.value = null
    }
  }

  async function renderFinalCut(variant: OutputVariant = 'landscape') {
    if (!currentProject.value) return
    error.value = ''
    renderingFinalCut.value = true
    try {
      finalCutJob.value = await api.renderFinalCut(currentProject.value.id, variant)
      while (finalCutJob.value.status === 'queued' || finalCutJob.value.status === 'running') {
        await delay(750)
        finalCutJob.value = await api.job(finalCutJob.value.id)
      }
      if (finalCutJob.value.status === 'failed') {
        throw new Error(finalCutJob.value.error || 'Final cut render failed')
      }
      if (finalCutJob.value.status === 'cancelled') return
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not render final cut'
      throw cause
    } finally {
      renderingFinalCut.value = false
    }
  }

  async function createDraft(options?: { variant?: OutputVariant; retry_failed_only?: boolean }) {
    if (!currentProject.value) return
    error.value = ''
    creatingDraft.value = true
    draftAttention.value = []
    try {
      const readiness = await api.draftReadiness(currentProject.value.id)
      draftAttention.value = readiness.attention
      draftScenes.value = readiness.scenes || []
      draftJob.value = await api.createDraft(currentProject.value.id, options)
      let pollTicks = 0
      const finalJob = await watchJob(draftJob.value.id, (next) => {
        draftJob.value = next
        pollTicks += 1
        if (pollTicks % 4 === 0 && currentProject.value) {
          void loadProject(currentProject.value.id, { resumeJobs: false })
        }
      })
      if (finalJob?.status === 'failed') {
        throw new Error(finalJob.error || 'Create draft failed')
      }
      if (finalJob?.error) {
        draftAttention.value = [
          ...draftAttention.value,
          ...String(finalJob.error)
            .split(';')
            .map((message) => ({ code: 'draft', message: message.trim() }))
            .filter((item) => item.message),
        ]
      }
      await loadProject(currentProject.value.id, { resumeJobs: false })
      try {
        const refreshed = await api.draftReadiness(currentProject.value.id)
        draftScenes.value = refreshed.scenes || []
      } catch {
        // Banner still works from local scene ready counts.
      }
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not create draft'
      throw cause
    } finally {
      creatingDraft.value = false
    }
  }

  async function deleteFinalCut(variant: OutputVariant = 'landscape') {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.deleteFinalCut(currentProject.value.id, variant)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete final cut'
      throw cause
    }
  }

  return {
    projects,
    currentProject,
    providers,
    videoProvider,
    imageProviders,
    imageJobs,
    castJobs,
    clipJobs,
    currentJob,
    clipJob,
    jobElapsedSeconds,
    busy,
    approvingScript,
    continuingScript,
    processingShotIds,
    isProcessingShot,
    isProcessingClip,
    isGeneratingCast,
    isCancellingJob,
    cancelJob,
    processingClipId,
    processingAudioId,
    finalCutJob,
    draftJob,
    draftAttention,
    draftScenes,
    creatingDraft,
    renderingFinalCut,
    error,
    pendingSuggestions,
    initialize,
    loadProject,
    selectProject,
    deleteProject,
    createProject,
    continueProject,
    decideSuggestion,
    approveScript,
    createShot,
    deleteShot,
    updateShot,
    updateScene,
    generateShotImage,
    generateShotAudio,
    createCharacter,
    syncCast,
    voiceProviders,
    auditionAudio,
    auditioningVoiceId,
    loadVoiceProviders,
    auditionVoice,
    uploadCustomVoice,
    updateCharacter,
    deleteCharacter,
    uploadCharacterReference,
    generateCharacterReference,
    deleteCharacterReference,
    uploadShotImage,
    deleteShotImage,
    saveShotContinuityFrame,
    deleteShotContinuityFrame,
    processingLipsyncId,
    lipsyncJobs,
    isProcessingLipsync,
    lipsyncShot,
    generateShotClip,
    uploadShotClip,
    deleteShotClip,
    createDraft,
    renderFinalCut,
    deleteFinalCut,
  }
})
