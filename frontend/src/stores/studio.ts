import { computed, ref } from 'vue'
import { acceptHMRUpdate, defineStore } from 'pinia'
import { api, type AspectRatio, type CompilationCreateInput, type CompilationSummary, type CompilationUpdateInput, type DeriveTakeCropPayload, type ElementKind, type EpisodeCreate, type EpisodeUpdate, type ImageProviderStatus, type Job, type OutputVariant, type ProjectContinuationInput, type ProjectDetail, type ProjectInput, type ProjectSummary, type ProjectUpdateInput, type ProviderStatus, type ReferenceColorRetouchAction, type ReferenceViewType, type Shot, type ShotCreate, type ShotUpdate, type VoiceProviderInfo } from '@/lib/api'
import { cutlineWs } from '@/lib/ws'

const delay = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds))

export const useStudioStore = defineStore('studio', () => {
  const projects = ref<ProjectSummary[]>([])
  const currentProject = ref<ProjectDetail | null>(null)
  let projectLoadSequence = 0
  let requestedProjectId: string | null = null
  let requestedEpisodeId: string | null = null
  const currentEpisodeId = ref<string | null>(null)
  // Shot the user is working on, shared by Produce and Assemble so switching stages keeps the place.
  const focusShotId = ref<string | null>(null)
  const creatingEpisode = ref(false)
  const updatingProject = ref(false)
  const providers = ref<ProviderStatus[]>([])
  const videoProvider = ref<ProviderStatus | null>(null)
  const imageProviders = ref<ImageProviderStatus[]>([])
  const imageJobsByProject = ref<Record<string, Record<string, Job>>>({})
  const castJobsByProject = ref<Record<string, Record<string, Job>>>({})
  const clipJobsByProject = ref<Record<string, Record<string, Job>>>({})
  const lipsyncJobsByProject = ref<Record<string, Record<string, Job>>>({})
  const processingShotIdsByProject = ref<Record<string, string[]>>({})
  const processingClipIdsByProject = ref<Record<string, string[]>>({})
  const processingLipsyncIdsByProject = ref<Record<string, string[]>>({})

  const imageJobs = computed({
    get: () => {
      const pid = currentProject.value?.id
      return pid ? (imageJobsByProject.value[pid] || {}) : {}
    },
    set: (val: Record<string, Job>) => {
      const pid = currentProject.value?.id
      if (pid) imageJobsByProject.value = { ...imageJobsByProject.value, [pid]: val }
    },
  })

  const castJobs = computed({
    get: () => {
      const pid = currentProject.value?.id
      return pid ? (castJobsByProject.value[pid] || {}) : {}
    },
    set: (val: Record<string, Job>) => {
      const pid = currentProject.value?.id
      if (pid) castJobsByProject.value = { ...castJobsByProject.value, [pid]: val }
    },
  })

  const clipJobs = computed({
    get: () => {
      const pid = currentProject.value?.id
      return pid ? (clipJobsByProject.value[pid] || {}) : {}
    },
    set: (val: Record<string, Job>) => {
      const pid = currentProject.value?.id
      if (pid) clipJobsByProject.value = { ...clipJobsByProject.value, [pid]: val }
    },
  })

  const lipsyncJobs = computed({
    get: () => {
      const pid = currentProject.value?.id
      return pid ? (lipsyncJobsByProject.value[pid] || {}) : {}
    },
    set: (val: Record<string, Job>) => {
      const pid = currentProject.value?.id
      if (pid) lipsyncJobsByProject.value = { ...lipsyncJobsByProject.value, [pid]: val }
    },
  })

  const processingShotIds = computed({
    get: () => {
      const pid = currentProject.value?.id
      return pid ? (processingShotIdsByProject.value[pid] || []) : []
    },
    set: (val: string[]) => {
      const pid = currentProject.value?.id
      if (pid) processingShotIdsByProject.value = { ...processingShotIdsByProject.value, [pid]: val }
    },
  })

  const processingClipId = computed<string | null>({
    get: () => {
      const pid = currentProject.value?.id
      if (!pid) return null
      return (processingClipIdsByProject.value[pid] || [])[0] || null
    },
    set: (val: string | null) => {
      const pid = currentProject.value?.id
      if (pid) {
        processingClipIdsByProject.value = {
          ...processingClipIdsByProject.value,
          [pid]: val ? [val] : [],
        }
      }
    },
  })

  const processingLipsyncId = computed<string | null>({
    get: () => {
      const pid = currentProject.value?.id
      if (!pid) return null
      return (processingLipsyncIdsByProject.value[pid] || [])[0] || null
    },
    set: (val: string | null) => {
      const pid = currentProject.value?.id
      if (pid) {
        processingLipsyncIdsByProject.value = {
          ...processingLipsyncIdsByProject.value,
          [pid]: val ? [val] : [],
        }
      }
    },
  })

  const clipJob = computed<Job | null>(() => {
    const pid = currentProject.value?.id
    if (!pid) return null
    const jobs = Object.values(clipJobsByProject.value[pid] || {})
    const running = jobs.find((j) => j.status === 'running')
    if (running) return running
    const queued = jobs.find((j) => j.status === 'queued')
    if (queued) return queued
    return jobs.find((j) => j.status === 'completed') || null
  })

  const backgroundActiveJobsCount = computed(() => {
    const currentPid = currentProject.value?.id
    let count = 0
    for (const [pid, jobs] of Object.entries(clipJobsByProject.value)) {
      if (pid === currentPid) continue
      for (const job of Object.values(jobs)) {
        if (job.status === 'queued' || job.status === 'running') count++
      }
    }
    for (const [pid, jobs] of Object.entries(imageJobsByProject.value)) {
      if (pid === currentPid) continue
      for (const job of Object.values(jobs)) {
        if (job.status === 'queued' || job.status === 'running') count++
      }
    }
    return count
  })

  const currentJob = ref<Job | null>(null)
  const jobElapsedSeconds = ref(0)
  const busy = ref(false)
  const approvingScript = ref(false)
  const continuingScript = ref(false)
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

  const episodes = computed(() => currentProject.value?.episodes ?? [])
  const currentEpisode = computed(
    () => episodes.value.find((e) => e.id === currentEpisodeId.value) || episodes.value[0] || null
  )
  const episodeLabel = computed(() => currentProject.value?.episode_label || 'Episode')

  const activeCompilationId = ref<string | null>(null)
  const compilations = computed(() => currentProject.value?.compilations ?? [])
  const activeCompilation = computed(
    () => compilations.value.find((c) => c.id === activeCompilationId.value) || null
  )
  const isCompilationMode = computed(() => Boolean(activeCompilationId.value))

  const pendingSuggestions = computed(
    () => currentProject.value?.current_version?.suggestions.filter((item) => item.status === 'pending') ?? [],
  )

  function setProjectClipJob(projectId: string, shotId: string, job: Job) {
    const current = clipJobsByProject.value[projectId] || {}
    clipJobsByProject.value = {
      ...clipJobsByProject.value,
      [projectId]: { ...current, [shotId]: job },
    }
  }

  function setProjectImageJob(projectId: string, shotId: string, job: Job) {
    const current = imageJobsByProject.value[projectId] || {}
    imageJobsByProject.value = {
      ...imageJobsByProject.value,
      [projectId]: { ...current, [shotId]: job },
    }
  }

  function setProjectCastJob(projectId: string, characterId: string, job: Job) {
    const current = castJobsByProject.value[projectId] || {}
    castJobsByProject.value = {
      ...castJobsByProject.value,
      [projectId]: { ...current, [characterId]: job },
    }
  }

  function setProjectLipsyncJob(projectId: string, shotId: string, job: Job) {
    const current = lipsyncJobsByProject.value[projectId] || {}
    lipsyncJobsByProject.value = {
      ...lipsyncJobsByProject.value,
      [projectId]: { ...current, [shotId]: job },
    }
  }

  const processingShotAspectsByProject = ref<Record<string, Record<string, string>>>({})
  const processingClipAspectsByProject = ref<Record<string, Record<string, string>>>({})

  function jobMeta(job: Job): Record<string, unknown> {
    const rows = Array.isArray(job.attempts) ? job.attempts : []
    return (rows.find((item) => item && typeof item === 'object' && (item.shot_id || item.character_id)) as Record<string, unknown> | undefined) ?? {}
  }

  function beginShotProcessing(id: string, projectId?: string, aspect?: string) {
    const pid = projectId || currentProject.value?.id
    if (!pid) return
    const list = processingShotIdsByProject.value[pid] || []
    if (!list.includes(id)) {
      processingShotIdsByProject.value = {
        ...processingShotIdsByProject.value,
        [pid]: [...list, id],
      }
    }
    if (aspect) {
      const aspects = processingShotAspectsByProject.value[pid] || {}
      processingShotAspectsByProject.value = {
        ...processingShotAspectsByProject.value,
        [pid]: { ...aspects, [id]: aspect },
      }
    }
  }

  function endShotProcessing(id: string, projectId?: string) {
    const pid = projectId || currentProject.value?.id
    if (!pid) return
    const list = processingShotIdsByProject.value[pid] || []
    processingShotIdsByProject.value = {
      ...processingShotIdsByProject.value,
      [pid]: list.filter((shotId) => shotId !== id),
    }
    if (processingShotAspectsByProject.value[pid]?.[id]) {
      const aspects = { ...(processingShotAspectsByProject.value[pid] || {}) }
      delete aspects[id]
      processingShotAspectsByProject.value = {
        ...processingShotAspectsByProject.value,
        [pid]: aspects,
      }
    }
  }

  function beginClipProcessing(id: string, projectId?: string, aspect?: string) {
    const pid = projectId || currentProject.value?.id
    if (!pid) return
    const list = processingClipIdsByProject.value[pid] || []
    if (!list.includes(id)) {
      processingClipIdsByProject.value = {
        ...processingClipIdsByProject.value,
        [pid]: [...list, id],
      }
    }
    if (aspect) {
      const aspects = processingClipAspectsByProject.value[pid] || {}
      processingClipAspectsByProject.value = {
        ...processingClipAspectsByProject.value,
        [pid]: { ...aspects, [id]: aspect },
      }
    }
  }

  function endClipProcessing(id: string, projectId?: string) {
    const pid = projectId || currentProject.value?.id
    if (!pid) return
    const list = processingClipIdsByProject.value[pid] || []
    processingClipIdsByProject.value = {
      ...processingClipIdsByProject.value,
      [pid]: list.filter((shotId) => shotId !== id),
    }
    if (processingClipAspectsByProject.value[pid]?.[id]) {
      const aspects = { ...(processingClipAspectsByProject.value[pid] || {}) }
      delete aspects[id]
      processingClipAspectsByProject.value = {
        ...processingClipAspectsByProject.value,
        [pid]: aspects,
      }
    }
  }

  function beginLipsyncProcessing(id: string, projectId?: string) {
    const pid = projectId || currentProject.value?.id
    if (!pid) return
    const list = processingLipsyncIdsByProject.value[pid] || []
    if (!list.includes(id)) {
      processingLipsyncIdsByProject.value = {
        ...processingLipsyncIdsByProject.value,
        [pid]: [...list, id],
      }
    }
  }

  function endLipsyncProcessing(id: string, projectId?: string) {
    const pid = projectId || currentProject.value?.id
    if (!pid) return
    const list = processingLipsyncIdsByProject.value[pid] || []
    processingLipsyncIdsByProject.value = {
      ...processingLipsyncIdsByProject.value,
      [pid]: list.filter((shotId) => shotId !== id),
    }
  }

  function isProcessingShot(id: string, aspect?: string) {
    const pid = currentProject.value?.id
    if (!pid) return false
    const job = imageJobsByProject.value[pid]?.[id]
    if (job && (job.status === 'queued' || job.status === 'running')) {
      if (!aspect) return true
      const meta = jobMeta(job)
      const jobAspect = (typeof meta.aspect_ratio === 'string' && meta.aspect_ratio) || currentProject.value?.aspect_ratio
      const isBoth = Boolean(meta.both_aspects)
      if (!isBoth) {
        return jobAspect === aspect
      }
      if (job.message?.includes('(2/2)')) {
        const otherAspect = jobAspect === '16:9' ? '9:16' : '16:9'
        return aspect === otherAspect
      }
      return true
    }
    const list = processingShotIdsByProject.value[pid] || []
    if (list.includes(id)) {
      if (!aspect) return true
      const pendingAspect = processingShotAspectsByProject.value[pid]?.[id]
      return !pendingAspect || pendingAspect === aspect
    }
    return false
  }

  function isGeneratingCast(id: string) {
    const pid = currentProject.value?.id
    if (!pid) return false
    const job = castJobsByProject.value[pid]?.[id]
    return Boolean(job && (job.status === 'queued' || job.status === 'running'))
  }

  function isProcessingClip(id: string, aspect?: string) {
    const pid = currentProject.value?.id
    if (!pid) return false
    const job = clipJobsByProject.value[pid]?.[id]
    if (job && (job.status === 'queued' || job.status === 'running')) {
      if (!aspect) return true
      const meta = jobMeta(job)
      const jobAspect = (typeof meta.aspect_ratio === 'string' && meta.aspect_ratio) || currentProject.value?.aspect_ratio
      return jobAspect === aspect
    }
    const list = processingClipIdsByProject.value[pid] || []
    if (list.includes(id)) {
      if (!aspect) return true
      const pendingAspect = processingClipAspectsByProject.value[pid]?.[id]
      return !pendingAspect || pendingAspect === aspect
    }
    return false
  }

  function isCancellingJob(id: string) {
    return cancellingJobIds.value.includes(id)
  }

  function withQueueLabel(job: Job): Job {
    if (job.status === 'queued' && job.queue_position) {
      return { ...job, message: `Queued · position ${job.queue_position}` }
    }
    return job
  }

  function failActiveJob(job: Job | undefined, fallbackMessage: string): Job | null {
    if (!job || (job.status !== 'queued' && job.status !== 'running')) return null
    return {
      ...job,
      status: 'failed',
      message: fallbackMessage,
      error: job.error || fallbackMessage,
    }
  }

  const wsJobWaiters = new Map<string, Set<(job: Job) => void>>()

  function applyLiveShotUpdate(updatedShot: Shot, projectId: string, episodeId?: string | null) {
    if (!currentProject.value || currentProject.value.id !== projectId) return
    if (episodeId && currentEpisodeId.value && episodeId !== currentEpisodeId.value) return
    const scenes = currentProject.value.current_version?.scenes
    if (!scenes) return
    for (const scene of scenes) {
      const idx = scene.shots.findIndex((s) => s.id === updatedShot.id)
      if (idx !== -1) {
        scene.shots.splice(idx, 1, updatedShot)
        scene.shots = [...scene.shots]
        if (currentProject.value.current_version) {
          currentProject.value.current_version.scenes = [...scenes]
        }
        return
      }
    }
  }

  async function refreshShot(id: string): Promise<Shot | null> {
    try {
      const updated = await api.shot(id)
      const pid = currentProject.value?.id || ''
      applyLiveShotUpdate(updated, pid, currentEpisodeId.value)
      return updated
    } catch {
      return null
    }
  }

  cutlineWs.subscribe((event) => {
    if (event.type === 'shot.updated') {
      applyLiveShotUpdate(event.shot, event.project_id, event.episode_id)
      return
    }
    if (event.type === 'job.updated') {
      const job = withQueueLabel(event.job)
      const waiters = wsJobWaiters.get(job.id)
      if (waiters) {
        for (const notify of waiters) notify(job)
      }
      const pid = event.project_id
      if (pid && currentProject.value?.id === pid) {
        const meta = jobMeta(job)
        const shotId = typeof meta.shot_id === 'string' ? meta.shot_id : null
        const charId = typeof meta.character_id === 'string' ? meta.character_id : null
        const active = job.status === 'queued' || job.status === 'running'
        if (job.kind === 'create_draft') {
          draftJob.value = job
          creatingDraft.value = active
        } else if (job.kind === 'final_cut' || job.kind.startsWith('final_cut:')) {
          const jobCompId = job.compilation_id || null
          const activeCompId = activeCompilationId.value || null
          const inScope = activeCompId
            ? jobCompId === activeCompId
            : !jobCompId &&
              (!currentEpisodeId.value || !job.episode_id || job.episode_id === currentEpisodeId.value)
          if (inScope) {
            finalCutJob.value = job
            renderingFinalCut.value = active
          }
        } else if (job.kind === 'shot_image' && shotId) {
          setProjectImageJob(pid, shotId, job)
          if (active) beginShotProcessing(shotId, pid, typeof meta.aspect_ratio === 'string' ? meta.aspect_ratio : undefined)
          else endShotProcessing(shotId, pid)
          if (!active && job.status === 'completed') void refreshShot(shotId)
        } else if (job.kind === 'shot_clip' && shotId) {
          setProjectClipJob(pid, shotId, job)
          if (active) beginClipProcessing(shotId, pid, typeof meta.aspect_ratio === 'string' ? meta.aspect_ratio : undefined)
          else endClipProcessing(shotId, pid)
          if (!active && job.status === 'completed') void refreshShot(shotId)
        } else if (job.kind === 'shot_lipsync' && shotId) {
          setProjectLipsyncJob(pid, shotId, job)
          if (active) beginLipsyncProcessing(shotId, pid)
          else endLipsyncProcessing(shotId, pid)
          if (!active && job.status === 'completed') void refreshShot(shotId)
        } else if (job.kind === 'cast_reference' && charId) {
          setProjectCastJob(pid, charId, job)
        }
      }
    }
  })

  function waitForNextJobUpdate(jobId: string, fallbackMs: number): Promise<Job | null> {
    return new Promise((resolve) => {
      let settled = false
      const timer = setTimeout(() => {
        if (settled) return
        settled = true
        cleanup()
        resolve(null)
      }, fallbackMs)
      const onWsJob = (job: Job) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        cleanup()
        resolve(job)
      }
      const cleanup = () => {
        const set = wsJobWaiters.get(jobId)
        if (set) {
          set.delete(onWsJob)
          if (set.size === 0) wsJobWaiters.delete(jobId)
        }
      }
      let set = wsJobWaiters.get(jobId)
      if (!set) {
        set = new Set()
        wsJobWaiters.set(jobId, set)
      }
      set.add(onWsJob)
    })
  }

  async function watchJob(
    jobId: string,
    onUpdate: (job: Job) => void,
    options?: { reloadOnComplete?: boolean; episodeId?: string | null },
  ): Promise<Job | null> {
    if (!jobId) return null
    cutlineWs.ensureConnected(currentProject.value?.id ?? null)
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
        for (const listener of listeners) listener(job)
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
        let delayMs = 800
        let lastActivityAt = Date.now()
        let lastStateKey = `${job.status}:${job.progress}:${job.message}:${job.provider_phase}`
        while (job.status === 'queued' || job.status === 'running') {
          if (Date.now() - lastActivityAt > 25 * 60 * 1000) {
            throw new Error('Timed out waiting for job status')
          }
          // Prefer WS push; HTTP fallback is intentionally slow when the socket is up.
          const waitMs = cutlineWs.isConnected ? 15000 : delayMs
          const pushedJob = await waitForNextJobUpdate(jobId, waitMs)
          if (pushedJob) {
            job = pushedJob
            emit(job)
          } else {
            delayMs = Math.min(4000, Math.round(delayMs * 1.35))
            try {
              job = await fetchJob()
              emit(job)
            } catch {
              // Transient status failures should not abort an otherwise healthy job.
              continue
            }
          }
          const nextStateKey = `${job.status}:${job.progress}:${job.message}:${job.provider_phase}`
          if (nextStateKey !== lastStateKey) {
            lastStateKey = nextStateKey
            lastActivityAt = Date.now()
          }
        }
        if (
          options?.reloadOnComplete !== false &&
          job.status === 'completed' &&
          (options?.episodeId === undefined || options.episodeId === currentEpisodeId.value) &&
          currentProject.value &&
          job.project_id === currentProject.value.id &&
          (!requestedProjectId || requestedProjectId === job.project_id) &&
          (!requestedEpisodeId || requestedEpisodeId === currentEpisodeId.value)
        ) {
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
          setProjectCastJob(projectId, characterId, job)
          void watchJob(job.id, (next) => {
            setProjectCastJob(projectId, characterId, next)
          }, { reloadOnComplete: false })
            .then(async (finalJob) => {
              if (currentProject.value?.id === projectId) {
                if (finalJob?.status === 'failed') {
                  error.value = String(finalJob.error || 'Face reference generation failed')
                } else if (finalJob?.status === 'completed') {
                  try {
                    currentProject.value.characters = await api.characters(projectId)
                  } catch {}
                }
              }
            })
            .catch((cause) => {
              const message = cause instanceof Error ? cause.message : 'Face reference generation failed'
              const failed = failActiveJob(castJobsByProject.value[projectId]?.[characterId], message)
              if (failed) setProjectCastJob(projectId, characterId, failed)
              if (currentProject.value?.id === projectId) {
                error.value = message
              }
            })
          continue
        }
        if (job.kind === 'shot_image') {
          const shotId = typeof meta.shot_id === 'string' ? meta.shot_id : null
          if (!shotId) continue
          beginShotProcessing(shotId, projectId, typeof meta.aspect_ratio === 'string' ? meta.aspect_ratio : undefined)
          setProjectImageJob(projectId, shotId, job)
          void watchJob(job.id, (next) => {
            setProjectImageJob(projectId, shotId, next)
          }, { reloadOnComplete: false })
            .then((finalJob) => {
              if (finalJob?.status === 'completed') {
                void refreshShot(shotId)
              }
              if (currentProject.value?.id === projectId && finalJob?.status === 'failed') {
                error.value = String(finalJob.error || 'Image generation failed')
              }
            })
            .catch((cause) => {
              const message = cause instanceof Error ? cause.message : 'Image generation failed'
              const failed = failActiveJob(imageJobsByProject.value[projectId]?.[shotId], message)
              if (failed) setProjectImageJob(projectId, shotId, failed)
              if (currentProject.value?.id === projectId) {
                error.value = message
              }
            })
            .finally(() => {
              endShotProcessing(shotId, projectId)
            })
          continue
        }
        if (job.kind === 'shot_clip') {
          const shotId = typeof meta.shot_id === 'string' ? meta.shot_id : null
          if (!shotId) continue
          beginClipProcessing(shotId, projectId, typeof meta.aspect_ratio === 'string' ? meta.aspect_ratio : undefined)
          setProjectClipJob(projectId, shotId, job)
          void watchJob(job.id, (next) => {
            setProjectClipJob(projectId, shotId, next)
          }, { reloadOnComplete: false })
            .then((finalJob) => {
              if (finalJob?.status === 'completed') {
                void refreshShot(shotId)
              }
              if (currentProject.value?.id === projectId && finalJob?.status === 'failed') {
                error.value = String(finalJob.error || 'Could not generate video')
              }
            })
            .catch((cause) => {
              const message = cause instanceof Error ? cause.message : 'Could not generate video'
              const failed = failActiveJob(clipJobsByProject.value[projectId]?.[shotId], message)
              if (failed) setProjectClipJob(projectId, shotId, failed)
              if (currentProject.value?.id === projectId) {
                error.value = message
              }
            })
            .finally(() => {
              endClipProcessing(shotId, projectId)
            })
          continue
        }
        if (job.kind === 'shot_lipsync') {
          const shotId = typeof meta.shot_id === 'string' ? meta.shot_id : null
          if (!shotId) continue
          beginLipsyncProcessing(shotId, projectId)
          setProjectLipsyncJob(projectId, shotId, job)
          void watchJob(job.id, (next) => {
            setProjectLipsyncJob(projectId, shotId, next)
          }, { reloadOnComplete: false })
            .then((finalJob) => {
              if (finalJob?.status === 'completed') {
                void refreshShot(shotId)
              }
              if (currentProject.value?.id === projectId && finalJob?.status === 'failed') {
                error.value = String(finalJob.error || 'Dialogue lip-sync failed')
              }
            })
            .catch((cause) => {
              const message = cause instanceof Error ? cause.message : 'Dialogue lip-sync failed'
              const failed = failActiveJob(lipsyncJobsByProject.value[projectId]?.[shotId], message)
              if (failed) setProjectLipsyncJob(projectId, shotId, failed)
              if (currentProject.value?.id === projectId) {
                error.value = message
              }
            })
            .finally(() => {
              endLipsyncProcessing(shotId, projectId)
            })
          continue
        }
        if (job.kind === 'create_draft') {
          draftJob.value = job
          creatingDraft.value = true
          void watchJob(job.id, (next) => {
            if (currentProject.value?.id !== projectId) return
            draftJob.value = next
          })
            .then((finalJob) => {
              if (currentProject.value?.id !== projectId) return
              if (finalJob?.status === 'failed') {
                error.value = String(finalJob.error || 'Create draft failed')
              }
            })
            .catch((cause) => {
              if (currentProject.value?.id !== projectId) return
              const message = cause instanceof Error ? cause.message : 'Create draft failed'
              const failed = failActiveJob(draftJob.value ?? undefined, message)
              if (failed) draftJob.value = failed
              error.value = message
            })
            .finally(() => {
              if (currentProject.value?.id === projectId) {
                creatingDraft.value = false
              }
            })
          continue
        }
        if (job.kind === 'final_cut' || job.kind.startsWith('final_cut:')) {
          const jobCompId = job.compilation_id || null
          const activeCompId = activeCompilationId.value || null
          if (activeCompId) {
            if (jobCompId !== activeCompId) continue
          } else if (jobCompId) {
            continue
          } else if (
            currentEpisodeId.value &&
            job.episode_id &&
            job.episode_id !== currentEpisodeId.value
          ) {
            continue
          }
          finalCutJob.value = job
          renderingFinalCut.value = true
          void watchJob(job.id, (next) => {
            if (currentProject.value?.id !== projectId) return
            finalCutJob.value = next
          })
            .then(async (finalJob) => {
              if (currentProject.value?.id !== projectId) return
              if (finalJob) finalCutJob.value = finalJob
              if (finalJob?.status === 'failed') {
                error.value = String(finalJob.error || 'Final cut render failed')
              } else if (finalJob?.status === 'completed') {
                if (jobCompId) {
                  await refreshCompilations()
                } else {
                  await loadProject(projectId, {
                    episodeId: currentEpisodeId.value || undefined,
                    resumeJobs: false,
                  })
                }
              }
            })
            .catch((cause) => {
              if (currentProject.value?.id !== projectId) return
              const message = cause instanceof Error ? cause.message : 'Final cut render failed'
              const failed = failActiveJob(finalCutJob.value ?? undefined, message)
              if (failed) finalCutJob.value = failed
              error.value = message
            })
            .finally(() => {
              if (currentProject.value?.id === projectId) {
                renderingFinalCut.value = false
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
      if (finalCutJob.value?.id === jobId) finalCutJob.value = job
      if (draftJob.value?.id === jobId) {
        draftJob.value = job
        creatingDraft.value = false
      }
      for (const [pid, pJobs] of Object.entries(imageJobsByProject.value)) {
        const shotId = Object.entries(pJobs).find(([, item]) => item.id === jobId)?.[0]
        if (shotId) {
          setProjectImageJob(pid, shotId, job)
          endShotProcessing(shotId, pid)
        }
      }
      for (const [pid, pJobs] of Object.entries(clipJobsByProject.value)) {
        const shotId = Object.entries(pJobs).find(([, item]) => item.id === jobId)?.[0]
        if (shotId) {
          setProjectClipJob(pid, shotId, job)
          endClipProcessing(shotId, pid)
        }
      }
      for (const [pid, pJobs] of Object.entries(castJobsByProject.value)) {
        const charId = Object.entries(pJobs).find(([, item]) => item.id === jobId)?.[0]
        if (charId) setProjectCastJob(pid, charId, job)
      }
      for (const [pid, pJobs] of Object.entries(lipsyncJobsByProject.value)) {
        const shotId = Object.entries(pJobs).find(([, item]) => item.id === jobId)?.[0]
        if (shotId) {
          setProjectLipsyncJob(pid, shotId, job)
          endLipsyncProcessing(shotId, pid)
        }
      }
      return job
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not cancel job'
      throw cause
    } finally {
      cancellingJobIds.value = cancellingJobIds.value.filter((id) => id !== jobId)
    }
  }

  async function initialize(projectId?: string, episodeId?: string) {
    error.value = ''
    try {
      const [providerResult, imageProviderResult, videoProviderResult, projectResult] = await Promise.all([api.providers(), api.imageProviders(), api.videoProvider(), api.projects()])
      providers.value = providerResult
      imageProviders.value = imageProviderResult
      videoProvider.value = videoProviderResult
      projects.value = projectResult
      if (projectId) await loadProject(projectId, { resumeJobs: true, episodeId })
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not connect to the local API'
    }
  }

  async function loadProject(id: string, options?: { resumeJobs?: boolean; episodeId?: string }) {
    const sequence = ++projectLoadSequence
    requestedProjectId = id
    let epId = options?.episodeId || (currentProject.value?.id === id ? currentEpisodeId.value : undefined) || undefined
    if (!epId) {
      try {
        const saved = localStorage.getItem(`cutline:active-episode:${id}`)
        if (saved) epId = saved
      } catch {}
    }
    requestedEpisodeId = epId || null
    let project: ProjectDetail
    try {
      project = await api.project(id, epId)
    } catch (cause) {
      if (sequence !== projectLoadSequence) return
      throw cause
    }
    if (sequence !== projectLoadSequence) return
    currentEpisodeId.value = project.current_episode_id || null
    requestedEpisodeId = currentEpisodeId.value
    currentProject.value = project
    cutlineWs.ensureConnected(id)
    if (currentEpisodeId.value) {
      try {
        localStorage.setItem(`cutline:active-episode:${id}`, currentEpisodeId.value)
      } catch {}
    }
    if (options?.resumeJobs) {
      processingShotIdsByProject.value = { ...processingShotIdsByProject.value, [id]: [] }
      processingClipIdsByProject.value = { ...processingClipIdsByProject.value, [id]: [] }
      processingLipsyncIdsByProject.value = { ...processingLipsyncIdsByProject.value, [id]: [] }
      void resumeActiveJobs(id)
    }
  }

  async function selectEpisode(episodeId: string) {
    activeCompilationId.value = null
    if (!currentProject.value) return
    currentEpisodeId.value = episodeId
    try {
      localStorage.setItem(`cutline:active-episode:${currentProject.value.id}`, episodeId)
    } catch {}
    await loadProject(currentProject.value.id, { episodeId, resumeJobs: false })
  }

  async function createEpisode(input: EpisodeCreate) {
    if (!currentProject.value) return
    creatingEpisode.value = true
    try {
      const job = await api.createEpisode(currentProject.value.id, input)
      currentJob.value = job
      await watchJob(job.id, (next) => { currentJob.value = next })
      await loadProject(currentProject.value.id, { resumeJobs: false })
    } finally {
      creatingEpisode.value = false
    }
  }

  async function updateProjectSettings(input: ProjectUpdateInput) {
    if (!currentProject.value) return
    updatingProject.value = true
    try {
      const updated = await api.updateProject(currentProject.value.id, input)
      currentProject.value.title = updated.title
      currentProject.value.episode_label = updated.episode_label
      currentProject.value.aspect_ratio = updated.aspect_ratio
      currentProject.value.target_duration_seconds = updated.target_duration_seconds
      currentProject.value.text_provider = updated.text_provider
      currentProject.value.updated_at = updated.updated_at
      const idx = projects.value.findIndex((p) => p.id === updated.id)
      if (idx !== -1) {
        projects.value.splice(idx, 1, updated)
      }
    } finally {
      updatingProject.value = false
    }
  }

  async function updateEpisode(episodeId: string, input: EpisodeUpdate) {
    if (!currentProject.value) return
    const ep = await api.updateEpisode(currentProject.value.id, episodeId, input)
    const idx = currentProject.value.episodes.findIndex((e) => e.id === episodeId)
    if (idx !== -1) {
      currentProject.value.episodes.splice(idx, 1, ep)
    }
  }

  async function deleteEpisode(episodeId: string) {
    if (!currentProject.value) return
    const projectId = currentProject.value.id
    const remaining = (currentProject.value.episodes || []).filter((e) => e.id !== episodeId)
    const wasCurrent = currentEpisodeId.value === episodeId
    const nextEpisodeId = wasCurrent
      ? (remaining[remaining.length - 1]?.id ?? remaining[0]?.id ?? undefined)
      : (currentEpisodeId.value || undefined)
    try {
      if (nextEpisodeId) {
        localStorage.setItem(`cutline:active-episode:${projectId}`, nextEpisodeId)
      } else {
        localStorage.removeItem(`cutline:active-episode:${projectId}`)
      }
    } catch {}
    await api.deleteEpisode(projectId, episodeId)
    if (wasCurrent) {
      currentEpisodeId.value = nextEpisodeId ?? null
    }
    await loadProject(projectId, { resumeJobs: false, episodeId: nextEpisodeId })
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
    if (!currentProject.value) return
    error.value = ''
    approvingScript.value = true
    try {
      await api.approve(currentProject.value.id, currentEpisodeId.value || undefined)
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
    const projectId = currentProject.value.id
    const episodeId = currentEpisodeId.value
    error.value = ''
    try {
      await api.updateShot(id, input)
      if (currentProject.value?.id === projectId && currentEpisodeId.value === episodeId &&
          (!requestedProjectId || requestedProjectId === projectId) &&
          (!requestedEpisodeId || requestedEpisodeId === episodeId)) {
        await loadProject(projectId, { episodeId: episodeId || undefined })
      }
    } catch (cause) {
      if (currentProject.value?.id === projectId) {
        error.value = cause instanceof Error ? cause.message : 'Could not update shot'
      }
      throw cause
    }
  }

  async function updateScene(
    id: string,
    input: { location?: string; set_notes?: string; lighting_notes?: string; wardrobe_notes?: string; props_notes?: string },
  ) {
    if (!currentProject.value) return
    error.value = ''
    try {
      await api.updateScene(id, input)
      await loadProject(currentProject.value.id, { episodeId: currentEpisodeId.value || undefined })
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
    useDefaultImageReference = false,
    options?: {
      bothAspects?: boolean
      generateVideo?: boolean
      includeCastReferences?: boolean
      chatgptDual?: boolean
      lockBlocking?: boolean
      focalCharacterId?: string | null
    },
  ) {
    if (!currentProject.value) return
    const projectId = currentProject.value.id
    error.value = ''
    beginShotProcessing(id, projectId, (options?.bothAspects || options?.chatgptDual) ? undefined : aspectRatio)
    try {
      let job = await api.generateShotImage(
        id,
        aspectRatio,
        characterIds,
        refreshContinuity,
        imageReferenceId,
        useDefaultImageReference,
        options,
      )
      const initialJob = withQueueLabel(job)
      setProjectImageJob(projectId, id, initialJob)
      const finalJob = await watchJob(
        job.id,
        (next) => {
          setProjectImageJob(projectId, id, next)
        },
        { reloadOnComplete: false },
      )
      job = finalJob ?? (await api.job(job.id))
      if (job.status === 'completed') {
        if (currentProject.value?.id === projectId) {
          await refreshShot(id)
        }
      }
      if (job.status === 'failed') throw new Error(job.error || 'Image generation failed')
      if (job.status === 'cancelled') return
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not generate image'
      const failed = failActiveJob(imageJobsByProject.value[projectId]?.[id], message)
      if (failed) setProjectImageJob(projectId, id, failed)
      if (currentProject.value?.id === projectId) {
        error.value = message
      }
      throw cause
    } finally {
      endShotProcessing(id, projectId)
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

  async function createCharacter(name: string, notes = '', voiceDescription = '', kind: ElementKind = 'character') {
    if (!currentProject.value) return
    error.value = ''
    try {
      const created = await api.createCharacter(currentProject.value.id, { name, kind, notes, voice_description: voiceDescription })
      if (currentProject.value) {
        const exists = currentProject.value.characters.some((c) => c.id === created.id)
        if (!exists) {
          currentProject.value.characters = [...currentProject.value.characters, created].sort((a, b) => a.name.localeCompare(b.name))
        }
      }
      await loadProject(currentProject.value.id, { resumeJobs: false })
      return created
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not create element'
      throw cause
    }
  }

  async function syncCast() {
    if (!currentProject.value) return
    error.value = ''
    try {
      const synced = await api.syncCast(currentProject.value.id)
      if (currentProject.value) {
        currentProject.value.characters = synced
      }
      await loadProject(currentProject.value.id, { resumeJobs: false })
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not sync cast from script'
      throw cause
    }
  }

  async function syncElements() {
    if (!currentProject.value) return
    error.value = ''
    try {
      const synced = await api.syncElements(currentProject.value.id)
      if (currentProject.value) {
        currentProject.value.characters = synced
      }
      await loadProject(currentProject.value.id, { resumeJobs: false })
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not sync scenes and props from script'
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



  async function updateCharacter(id: string, input: { name?: string; kind?: ElementKind; notes?: string; voice_description?: string; voice_provider?: string; voice_id?: string | null; voice_settings?: Record<string, any> }) {
    if (!currentProject.value) return
    error.value = ''
    try {
      const updated = await api.updateCharacter(id, input)
      if (currentProject.value) {
        currentProject.value.characters = currentProject.value.characters.map((c) => (c.id === id ? updated : c))
      }
      await loadProject(currentProject.value.id, { resumeJobs: false })
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
      if (currentProject.value) {
        currentProject.value.characters = currentProject.value.characters.filter((c) => c.id !== id)
      }
      await loadProject(currentProject.value.id, { resumeJobs: false })
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete character'
      throw cause
    }
  }

  async function uploadCharacterReference(id: string, file: File, viewType?: ReferenceViewType) {
    if (!currentProject.value) return
    error.value = ''
    try {
      const updated = await api.uploadCharacterReference(id, file, viewType)
      if (currentProject.value) {
        currentProject.value.characters = currentProject.value.characters.map((c) => (c.id === id ? updated : c))
      }
      await loadProject(currentProject.value.id, { resumeJobs: false })
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not upload reference image'
      throw cause
    }
  }

  async function generateCharacterReference(id: string, look?: string) {
    if (!currentProject.value) return
    const projectId = currentProject.value.id
    error.value = ''
    try {
      let job = await api.generateCharacterReference(id, look)
      const initialJob = withQueueLabel(job)
      setProjectCastJob(projectId, id, initialJob)
      const finalJob = await watchJob(
        job.id,
        (next) => {
          setProjectCastJob(projectId, id, next)
        },
        { reloadOnComplete: false },
      )
      job = finalJob ?? (await api.job(job.id))
      if (job.status === 'failed') throw new Error(job.error || 'Face reference generation failed')
      if (job.status === 'cancelled') return
      if (currentProject.value?.id === projectId && (!requestedProjectId || requestedProjectId === projectId)) {
        try {
          currentProject.value.characters = await api.characters(projectId)
        } catch {}
      }
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not generate face reference'
      const failed = failActiveJob(castJobsByProject.value[projectId]?.[id], message)
      if (failed) setProjectCastJob(projectId, id, failed)
      if (currentProject.value?.id === projectId) {
        error.value = message
      }
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
    const pid = currentProject.value?.id
    if (!pid) return false
    const list = processingLipsyncIdsByProject.value[pid] || []
    if (list.includes(id)) return true
    const job = lipsyncJobsByProject.value[pid]?.[id]
    return Boolean(job && (job.status === 'queued' || job.status === 'running'))
  }

  async function lipsyncShot(id: string, aspectRatio?: AspectRatio) {
    if (!currentProject.value) return null
    const projectId = currentProject.value.id
    error.value = ''
    beginLipsyncProcessing(id, projectId)
    try {
      let job = await api.lipsyncShot(id, aspectRatio)
      const initialJob = withQueueLabel(job)
      setProjectLipsyncJob(projectId, id, initialJob)
      const finalJob = await watchJob(
        job.id,
        (next) => {
          setProjectLipsyncJob(projectId, id, next)
        },
        { reloadOnComplete: false },
      )
      job = finalJob ?? (await api.job(job.id))
      if (job.status === 'completed') {
        if (currentProject.value?.id === projectId) {
          await refreshShot(id)
        }
      }
      if (job.status === 'failed') throw new Error(job.error || 'Lip-sync generation failed')
      return job
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not generate lip-sync'
      const failed = failActiveJob(lipsyncJobsByProject.value[projectId]?.[id], message)
      if (failed) setProjectLipsyncJob(projectId, id, failed)
      if (currentProject.value?.id === projectId) {
        error.value = message
      }
      throw cause
    } finally {
      endLipsyncProcessing(id, projectId)
    }
  }

  async function selectShotTake(shotId: string, takeId: string) {
    if (!currentProject.value) return
    error.value = ""
    try {
      const updatedShot = await api.selectTake(shotId, takeId)
      if (currentProject.value?.current_version?.scenes) {
        for (const scene of currentProject.value.current_version.scenes) {
          const idx = scene.shots.findIndex((s) => s.id === shotId)
          if (idx !== -1) {
            scene.shots[idx] = updatedShot
            break
          }
        }
      }
      await loadProject(currentProject.value.id, { episodeId: currentEpisodeId.value || undefined })
      return updatedShot
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : "Could not select take"
      throw cause
    }
  }

  async function editShotImageTake(
    shotId: string,
    takeId: string,
    prompt: string,
    files: File[] = [],
  ) {
    if (!currentProject.value) return
    const projectId = currentProject.value.id
    error.value = ''
    beginShotProcessing(shotId, projectId)
    try {
      let job = await api.editShotImageTake(shotId, takeId, prompt, files)
      const initialJob = withQueueLabel(job)
      setProjectImageJob(projectId, shotId, initialJob)
      const finalJob = await watchJob(
        job.id,
        (next) => {
          setProjectImageJob(projectId, shotId, next)
        },
        { reloadOnComplete: false },
      )
      job = finalJob ?? (await api.job(job.id))
      if (job.status === 'completed') {
        if (currentProject.value?.id === projectId) {
          await refreshShot(shotId)
        }
      }
      if (job.status === 'failed') throw new Error(job.error || 'Follow-up image edit failed')
      if (job.status === 'cancelled') return
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not edit image take'
      const failed = failActiveJob(imageJobsByProject.value[projectId]?.[shotId], message)
      if (failed) setProjectImageJob(projectId, shotId, failed)
      if (currentProject.value?.id === projectId) {
        error.value = message
      }
      throw cause
    } finally {
      endShotProcessing(shotId, projectId)
    }
  }

  async function retouchShotTakeColor(shotId: string, takeId: string) {
    if (!currentProject.value) return
    const projectId = currentProject.value.id
    error.value = ''
    beginShotProcessing(shotId, projectId)
    try {
      const updatedShot = await api.retouchShotTakeColor(shotId, takeId)
      if (currentProject.value?.current_version?.scenes) {
        for (const scene of currentProject.value.current_version.scenes) {
          const idx = scene.shots.findIndex((s) => s.id === shotId)
          if (idx !== -1) {
            scene.shots[idx] = updatedShot
            break
          }
        }
      }
      await refreshShot(shotId)
      return updatedShot
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not enhance image color'
      if (currentProject.value?.id === projectId) {
        error.value = message
      }
      throw cause
    } finally {
      endShotProcessing(shotId, projectId)
    }
  }

  async function deriveTakeFromCrop(shotId: string, payload: DeriveTakeCropPayload) {
    if (!currentProject.value) return
    const projectId = currentProject.value.id
    error.value = ''
    beginShotProcessing(shotId, projectId, payload.aspect_ratio)
    try {
      const updatedShot = await api.deriveTakeFromCrop(shotId, payload)
      if (currentProject.value?.current_version?.scenes) {
        for (const scene of currentProject.value.current_version.scenes) {
          const idx = scene.shots.findIndex((s) => s.id === shotId)
          if (idx !== -1) {
            scene.shots[idx] = updatedShot
            break
          }
        }
      }
      await refreshShot(shotId)
      return updatedShot
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not derive take from crop'
      if (currentProject.value?.id === projectId) {
        error.value = message
      }
      throw cause
    } finally {
      endShotProcessing(shotId, projectId)
    }
  }

  async function retouchShotReferenceColor(
    shotId: string,
    aspectRatio: AspectRatio,
    options?: { action?: ReferenceColorRetouchAction; strength?: number },
  ) {
    if (!currentProject.value) return
    const projectId = currentProject.value.id
    error.value = ''
    beginShotProcessing(shotId, projectId, aspectRatio)
    try {
      const updatedShot = await api.retouchShotReferenceColor(shotId, aspectRatio, options)
      if (currentProject.value?.current_version?.scenes) {
        for (const scene of currentProject.value.current_version.scenes) {
          const idx = scene.shots.findIndex((s) => s.id === shotId)
          if (idx !== -1) {
            scene.shots[idx] = updatedShot
            break
          }
        }
      }
      await refreshShot(shotId)
      return updatedShot
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not enhance reference frame color'
      if (currentProject.value?.id === projectId) {
        error.value = message
      }
      throw cause
    } finally {
      endShotProcessing(shotId, projectId)
    }
  }

  async function retouchShotClipColor(
    shotId: string,
    aspectRatio: AspectRatio,
    options?: { action?: ReferenceColorRetouchAction; strength?: number },
  ) {
    if (!currentProject.value) return
    const projectId = currentProject.value.id
    error.value = ''
    try {
      const updatedShot = await api.retouchShotClipColor(shotId, aspectRatio, options)
      if (currentProject.value?.current_version?.scenes) {
        for (const scene of currentProject.value.current_version.scenes) {
          const idx = scene.shots.findIndex((s) => s.id === shotId)
          if (idx !== -1) {
            scene.shots[idx] = updatedShot
            break
          }
        }
      }
      return updatedShot
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not adjust video clip color'
      if (currentProject.value?.id === projectId) {
        error.value = message
      }
      throw cause
    }
  }

  async function repaintShotReference(
    shotId: string,
    aspectRatio: AspectRatio,
    prompt = 'repaint this image',
  ) {
    if (!currentProject.value) return
    const projectId = currentProject.value.id
    error.value = ''
    beginShotProcessing(shotId, projectId, aspectRatio)
    try {
      let job = await api.repaintShotReference(shotId, aspectRatio, prompt)
      const initialJob = withQueueLabel(job)
      setProjectImageJob(projectId, shotId, initialJob)
      const finalJob = await watchJob(
        job.id,
        (next) => {
          setProjectImageJob(projectId, shotId, next)
        },
        { reloadOnComplete: false },
      )
      job = finalJob ?? (await api.job(job.id))
      if (job.status === 'completed') {
        if (currentProject.value?.id === projectId) {
          await refreshShot(shotId)
        }
      }
      if (job.status === 'failed') throw new Error(job.error || 'AI color repaint failed')
      if (job.status === 'cancelled') return
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not repaint reference frame'
      const failed = failActiveJob(imageJobsByProject.value[projectId]?.[shotId], message)
      if (failed) setProjectImageJob(projectId, shotId, failed)
      if (currentProject.value?.id === projectId) {
        error.value = message
      }
      throw cause
    } finally {
      endShotProcessing(shotId, projectId)
    }
  }

  async function generateShotClip(
    id: string,
    aspectRatio?: AspectRatio,
    originatingProjectId?: string,
    originatingEpisodeId?: string | null,
    includeCastReferences = false,
    bothAspects = false,
    options?: {
      videoMode?: 'i2v' | 'r2v'
      includeSceneReference?: boolean
      elementIds?: string[]
    },
  ) {
    const projectId = originatingProjectId || currentProject.value?.id
    if (!projectId) return
    const episodeId = originatingEpisodeId === undefined ? currentEpisodeId.value : originatingEpisodeId
    if (currentProject.value?.id === projectId) error.value = ''
    beginClipProcessing(id, projectId, bothAspects ? undefined : aspectRatio)
    let job: Job | null = null
    try {
      const hasOptions = includeCastReferences || bothAspects || options?.videoMode === 'r2v' || options?.elementIds !== undefined
      job = hasOptions
        ? await api.generateShotClip(id, aspectRatio, includeCastReferences, bothAspects, options)
        : await api.generateShotClip(id, aspectRatio)
      const initialJob = withQueueLabel(job)
      setProjectClipJob(projectId, id, initialJob)
      const finalJob = await watchJob(
        job.id,
        (next) => {
          setProjectClipJob(projectId, id, next)
        },
        { episodeId, reloadOnComplete: false },
      )
      job = finalJob ?? (await api.job(job.id))
      if (job.status === 'completed') {
        if (currentProject.value?.id === projectId && (episodeId === undefined || currentEpisodeId.value === episodeId)) {
          await refreshShot(id)
        }
      }
      if (job.status === 'failed') throw new Error(job.error || 'Video generation failed')
      if (job.status === 'cancelled') return
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Could not generate video'
      const failed = failActiveJob(clipJobsByProject.value[projectId]?.[id], message)
      if (failed) setProjectClipJob(projectId, id, failed)
      if (currentProject.value?.id === projectId && currentEpisodeId.value === episodeId) {
        error.value = message
      }
      throw cause
    } finally {
      endClipProcessing(id, projectId)
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
      finalCutJob.value = await api.renderFinalCut(currentProject.value.id, variant, currentEpisodeId.value || undefined)
      const finalJob = await watchJob(finalCutJob.value.id, (next) => {
        finalCutJob.value = next
      })
      if (finalJob) finalCutJob.value = finalJob
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

  async function renderCompilationExport(compilationId: string, variant: OutputVariant = 'landscape') {
    if (!currentProject.value) return
    error.value = ''
    renderingFinalCut.value = true
    try {
      finalCutJob.value = await api.exportCompilation(compilationId, variant)
      const finalJob = await watchJob(finalCutJob.value.id, (next) => {
        finalCutJob.value = next
      })
      if (finalJob) finalCutJob.value = finalJob
      if (finalCutJob.value.status === 'failed') {
        throw new Error(finalCutJob.value.error || 'Compilation export render failed')
      }
      if (finalCutJob.value.status === 'cancelled') return
      await refreshCompilations()
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not render compilation export'
      throw cause
    } finally {
      renderingFinalCut.value = false
    }
  }

  async function createDraft(options?: { variant?: OutputVariant | 'both'; retry_failed_only?: boolean }) {
    if (!currentProject.value) return
    error.value = ''
    creatingDraft.value = true
    draftAttention.value = []
    const variant: OutputVariant | 'both' =
      options?.variant || (currentProject.value.aspect_ratio === '9:16' ? 'shorts' : 'landscape')
    try {
      const readiness = await api.draftReadiness(
        currentProject.value.id,
        currentEpisodeId.value || undefined,
        variant,
      )
      draftAttention.value = readiness.attention
      draftScenes.value = readiness.scenes || []
      draftJob.value = await api.createDraft(
        currentProject.value.id,
        { ...options, variant },
        currentEpisodeId.value || undefined,
      )
      let pollTicks = 0
      const finalJob = await watchJob(draftJob.value.id, (next) => {
        draftJob.value = next
        pollTicks += 1
        if (!cutlineWs.isConnected && pollTicks % 4 === 0 && currentProject.value) {
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
        const refreshed = await api.draftReadiness(
          currentProject.value.id,
          currentEpisodeId.value || undefined,
          variant,
        )
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
      await api.deleteFinalCut(currentProject.value.id, variant, currentEpisodeId.value || undefined)
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete final cut'
      throw cause
    }
  }

  function selectCompilation(compilationId: string | null) {
    activeCompilationId.value = compilationId
  }

  async function createCompilation(input: CompilationCreateInput): Promise<CompilationSummary> {
    if (!currentProject.value) throw new Error('No active project')
    error.value = ''
    try {
      const created = await api.createCompilation(currentProject.value.id, input)
      if (!currentProject.value.compilations) {
        currentProject.value.compilations = []
      }
      currentProject.value.compilations.push(created)
      activeCompilationId.value = created.id
      return created
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not create compilation'
      throw cause
    }
  }

  async function updateCompilation(compilationId: string, input: CompilationUpdateInput): Promise<CompilationSummary> {
    error.value = ''
    try {
      const updated = await api.updateCompilation(compilationId, input)
      if (currentProject.value?.compilations) {
        const idx = currentProject.value.compilations.findIndex((c) => c.id === compilationId)
        if (idx >= 0) {
          currentProject.value.compilations[idx] = updated
        }
      }
      return updated
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not update compilation'
      throw cause
    }
  }

  async function deleteCompilation(compilationId: string): Promise<void> {
    error.value = ''
    try {
      await api.deleteCompilation(compilationId)
      if (currentProject.value?.compilations) {
        currentProject.value.compilations = currentProject.value.compilations.filter((c) => c.id !== compilationId)
      }
      if (activeCompilationId.value === compilationId) {
        activeCompilationId.value = null
      }
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not delete compilation'
      throw cause
    }
  }

  async function refreshCompilations(): Promise<void> {
    if (!currentProject.value) return
    try {
      const list = await api.compilations(currentProject.value.id)
      if (currentProject.value) {
        currentProject.value.compilations = list
      }
    } catch (cause) {
      console.error('Could not refresh compilations', cause)
    }
  }

  return {
    projects,
    currentProject,
    currentEpisodeId,
    focusShotId,
    creatingEpisode,
    updatingProject,
    episodes,
    currentEpisode,
    episodeLabel,
    activeCompilationId,
    compilations,
    activeCompilation,
    isCompilationMode,
    selectCompilation,
    createCompilation,
    updateCompilation,
    deleteCompilation,
    refreshCompilations,
    selectEpisode,
    createEpisode,
    updateProjectSettings,
    updateEpisode,
    deleteEpisode,
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
    watchJob,
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
    syncElements,
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
    backgroundActiveJobsCount,
    lipsyncShot,
    selectShotTake,
    editShotImageTake,
    retouchShotTakeColor,
    deriveTakeFromCrop,
    retouchShotReferenceColor,
    retouchShotClipColor,
    repaintShotReference,
    refreshShot,
    generateShotClip,
    uploadShotClip,
    deleteShotClip,
    createDraft,
    renderFinalCut,
    renderCompilationExport,
    deleteFinalCut,
  }
})

if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useStudioStore, import.meta.hot))
}
