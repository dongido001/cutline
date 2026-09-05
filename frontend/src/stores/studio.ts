import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api, type AspectRatio, type ImageProviderStatus, type Job, type OutputVariant, type ProjectContinuationInput, type ProjectDetail, type ProjectInput, type ProjectSummary, type ProviderStatus, type ShotUpdate } from '@/lib/api'

const delay = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds))

export const useStudioStore = defineStore('studio', () => {
  const projects = ref<ProjectSummary[]>([])
  const currentProject = ref<ProjectDetail | null>(null)
  const providers = ref<ProviderStatus[]>([])
  const videoProvider = ref<ProviderStatus | null>(null)
  const imageProviders = ref<ImageProviderStatus[]>([])
  const imageJobs = ref<Record<string, Job>>({})
  const currentJob = ref<Job | null>(null)
  const jobElapsedSeconds = ref(0)
  const busy = ref(false)
  const approvingScript = ref(false)
  const continuingScript = ref(false)
  const processingShotIds = ref<string[]>([])
  const processingClipId = ref<string | null>(null)
  const finalCutJob = ref<Job | null>(null)
  const renderingFinalCut = ref(false)
  const error = ref('')

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

  async function initialize(projectId?: string) {
    error.value = ''
    try {
      const [providerResult, imageProviderResult, videoProviderResult, projectResult] = await Promise.all([api.providers(), api.imageProviders(), api.videoProvider(), api.projects()])
      providers.value = providerResult
      imageProviders.value = imageProviderResult
      videoProvider.value = videoProviderResult
      projects.value = projectResult
      if (projectId) await loadProject(projectId)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not connect to the local API'
    }
  }

  async function loadProject(id: string) {
    currentProject.value = await api.project(id)
  }

  async function deleteProject(id: string) {
    error.value = ''
    try {
      await api.deleteProject(id)
      projects.value = await api.projects()
      if (currentProject.value?.id === id) {
        currentProject.value = null
        if (projects.value[0]) await loadProject(projects.value[0].id)
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

  async function generateShotImage(id: string, aspectRatio?: AspectRatio, characterIds: string[] = []) {
    if (!currentProject.value) return
    error.value = ''
    beginShotProcessing(id)
    try {
      let job = await api.generateShotImage(id, aspectRatio, characterIds)
      imageJobs.value = { ...imageJobs.value, [id]: job }
      while (job.status === 'queued' || job.status === 'running') {
        await delay(500)
        job = await api.job(job.id)
        if (job.status === 'queued' && job.queue_position) {
          job.message = `Queued · position ${job.queue_position}`
        }
        imageJobs.value = { ...imageJobs.value, [id]: job }
      }
      if (job.status === 'failed') throw new Error(job.error || 'Image generation failed')
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not generate image'
      throw cause
    } finally {
      endShotProcessing(id)
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

  async function updateCharacter(id: string, input: { name?: string; notes?: string }) {
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

  async function generateShotClip(id: string, aspectRatio?: AspectRatio) {
    if (!currentProject.value) return
    error.value = ''
    processingClipId.value = id
    try {
      let job = await api.generateShotClip(id, aspectRatio)
      while (job.status === 'queued' || job.status === 'running') {
        await delay(750)
        job = await api.job(job.id)
      }
      if (job.status === 'failed') throw new Error(job.error || 'Video generation failed')
      await loadProject(currentProject.value.id)
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
      await loadProject(currentProject.value.id)
    } catch (cause) {
      error.value = cause instanceof Error ? cause.message : 'Could not render final cut'
      throw cause
    } finally {
      renderingFinalCut.value = false
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
    currentJob,
    jobElapsedSeconds,
    busy,
    approvingScript,
    continuingScript,
    processingShotIds,
    isProcessingShot,
    processingClipId,
    finalCutJob,
    renderingFinalCut,
    error,
    pendingSuggestions,
    initialize,
    loadProject,
    deleteProject,
    createProject,
    continueProject,
    decideSuggestion,
    approveScript,
    updateShot,
    generateShotImage,
    createCharacter,
    updateCharacter,
    deleteCharacter,
    uploadCharacterReference,
    deleteCharacterReference,
    uploadShotImage,
    deleteShotImage,
    generateShotClip,
    uploadShotClip,
    deleteShotClip,
    renderFinalCut,
    deleteFinalCut,
  }
})
