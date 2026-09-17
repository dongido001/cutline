export interface ProviderStatus {
  id: string
  label: string
  configured: boolean
  available: boolean
  model: string
  detail: string
}

export interface ImageProviderStatus extends ProviderStatus {
  priority: number
  image_generation: boolean
  connected: boolean
  verified: boolean
}

export interface Shot {
  id: string
  order_index: number
  title: string
  start_ms: number | null
  end_ms: number | null
  action: string
  dialogue: Array<{ character: string; text: string }>
  sound: string
  duration_ms: number
  framing: string
  image_prompt: string
  video_prompt: string
  title_card_text: string
  character_ids: string[]
  status: string
  selected_image_take_id?: string | null
  selected_clip_take_id?: string | null
  selected_audio_take_id?: string | null
  dialogue_audio_filename?: string | null
  dialogue_audio_mime?: string | null
  image: ShotImage | null
  clip: ShotClip | null
  media_variants: ShotMediaVariant[]
  takes?: ShotTake[]
}

export interface ShotTake {
  id: string
  media_type: 'image' | 'clip' | 'audio'
  aspect_ratio: string
  url: string
  mime_type: string
  source: string
  provider: string
  prompt: string
  settings: Record<string, unknown>
  selected: boolean
  created_at: string
}

export interface ShotImage {
  id: string
  url: string
  mime_type: string
  source: 'generated' | 'uploaded'
  provider: string
  prompt: string
  created_at: string
}

export type ShotClip = ShotImage

export interface ShotMediaVariant extends ShotImage {
  media_type: 'image' | 'clip'
  aspect_ratio: '16:9' | '9:16'
}

export interface ShotUpdate {
  framing: string
  duration_ms: number
  image_prompt: string
  video_prompt: string
  title_card_text: string
  character_ids?: string[]
}

export interface Scene {
  id: string
  order_index: number
  title: string
  location: string
  time_of_day: string
  start_ms: number | null
  end_ms: number | null
  action: string
  dialogue: Array<{ character: string; text: string }>
  set_notes?: string
  lighting_notes?: string
  wardrobe_notes?: string
  props_notes?: string
  shots: Shot[]
}

export interface Suggestion {
  id: string
  category: string
  severity: string
  title: string
  reason: string
  target: Record<string, unknown>
  before_value: unknown
  after_value: unknown
  status: string
}

export interface ScriptVersion {
  id: string
  version_number: number
  source_text: string
  status: string
  scenes: Scene[]
  suggestions: Suggestion[]
}

export interface ProjectSummary {
  id: string
  title: string
  status: string
  aspect_ratio: string
  target_duration_seconds: number
  text_provider: string
  created_at: string
  updated_at: string
}

export interface ProjectDetail extends ProjectSummary {
  current_version: ScriptVersion | null
  final_cut: ProjectFinalCut | null
  variant_exports: ProjectVariantExport[]
  characters: Character[]
}

export interface CharacterReference {
  id: string
  url: string
  mime_type: string
  created_at: string
}

export interface Character {
  id: string
  name: string
  notes: string
  created_at: string
  references: CharacterReference[]
}

export interface ProjectFinalCut {
  id: string
  url: string
  mime_type: string
  duration_ms: number
  width: number
  height: number
  clip_count: number
  fallback_count: number
  created_at: string
}

export interface ProjectVariantExport extends ProjectFinalCut {
  variant: OutputVariant
  timeline_revision: number
  is_stale: boolean
}

export type OutputVariant = 'landscape' | 'shorts'
export type AspectRatio = '16:9' | '9:16'

export interface TimelineGeometry {
  source_aspect: 'auto' | AspectRatio
  fit: 'cover' | 'contain'
  focal_x: number
  focal_y: number
  zoom: number
  position: 'top-left' | 'top' | 'top-right' | 'center' | 'bottom-left' | 'bottom' | 'bottom-right'
  scale: number
}

export interface TimelineSource {
  kind: 'shot_clip' | 'shot_image' | 'asset'
  ref_id: string
}

export interface TimelinePrimaryItem {
  id: string
  kind: 'video' | 'image' | 'title'
  label: string
  source: TimelineSource | null
  duration_ms: number
  source_in_ms: number
  source_out_ms: number | null
  text: string
  variant: Partial<Record<OutputVariant, TimelineGeometry>>
}

export interface TimelineImageOverlay {
  id: string
  source: TimelineSource
  start_ms: number
  duration_ms: number
  opacity: number
  variant: Partial<Record<OutputVariant, TimelineGeometry>>
}

export interface TimelineTextOverlay {
  id: string
  text: string
  start_ms: number
  duration_ms: number
  presentation: 'overlay' | 'full-frame'
  size: 'small' | 'medium' | 'large'
  color: 'white' | 'black'
  background: 'none' | 'shadow' | 'solid' | 'translucent'
  variant: Partial<Record<OutputVariant, TimelineGeometry>>
}

export interface TimelineDocument {
  version: 1
  primary: TimelinePrimaryItem[]
  image_overlays: TimelineImageOverlay[]
  text_overlays: TimelineTextOverlay[]
}

export interface TimelineAsset {
  id: string
  url: string
  mime_type: string
  label: string
  created_at: string
}

export interface ProjectTimeline {
  revision: number
  document: TimelineDocument
  edit?: OutputVariant
  assets: TimelineAsset[]
}

export interface Job {
  id: string
  project_id: string
  kind: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  progress: number
  message: string
  error: string | null
  provider: string | null
  provider_phase: 'submitting' | 'generating' | 'downloading' | 'saving' | 'completed' | 'failed' | 'cancelled' | null
  conversation_url: string | null
  attempts: Array<Record<string, unknown>>
  queue_position: number | null
  created_at?: string
  updated_at?: string
}

export interface ProjectInput {
  title: string
  script: string
  aspect_ratio: '16:9' | '9:16'
  target_duration_seconds: number
  text_provider: 'mock' | 'ollama' | 'gemini_web' | 'chatgpt_web' | 'compatible'
}

export interface ProjectContinuationInput {
  script: string
  target_duration_seconds: number
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData
  const response = await fetch(path, {
    ...init,
    headers: isFormData ? init?.headers : { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    const detail = body.detail
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((item: { msg?: string }) => item.msg || JSON.stringify(item)).join('; ')
        : (detail ? JSON.stringify(detail) : response.statusText)
    throw new Error(message || 'Request failed')
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  providers: () => request<ProviderStatus[]>('/api/providers'),
  imageProviders: () => request<ImageProviderStatus[]>('/api/image-providers'),
  videoProvider: () => request<ProviderStatus>('/api/video-provider'),
  audioProvider: () => request<ProviderStatus>('/api/audio-provider'),
  draftReadiness: (projectId: string) =>
    request<{
      attention: Array<{ code: string; message: string; shot_id?: string }>
      missing_images: string[]
      missing_clips: string[]
      missing_audio: string[]
      can_start: boolean
      video_provider: ProviderStatus
      vibevoice: { configured: boolean; available: boolean; detail: string }
    }>(`/api/projects/${projectId}/draft/readiness`),
  createDraft: (projectId: string, input?: { variant?: 'landscape' | 'shorts'; retry_failed_only?: boolean }) =>
    request<Job>(`/api/projects/${projectId}/draft`, {
      method: 'POST',
      body: JSON.stringify({
        variant: input?.variant || 'landscape',
        retry_failed_only: Boolean(input?.retry_failed_only),
      }),
    }),
  updateScene: (
    id: string,
    input: { set_notes?: string; lighting_notes?: string; wardrobe_notes?: string; props_notes?: string },
  ) => request<Scene>(`/api/scenes/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  selectTake: (shotId: string, takeId: string) =>
    request<Shot>(`/api/shots/${shotId}/takes/select`, {
      method: 'POST',
      body: JSON.stringify({ take_id: takeId }),
    }),
  projects: () => request<ProjectSummary[]>('/api/projects'),
  project: (id: string) => request<ProjectDetail>(`/api/projects/${id}`),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  createProject: (input: ProjectInput) =>
    request<Job>('/api/projects', { method: 'POST', body: JSON.stringify(input) }),
  continueProject: (id: string, input: ProjectContinuationInput) =>
    request<Job>(`/api/projects/${id}/continue`, { method: 'POST', body: JSON.stringify(input) }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  projectJobs: (projectId: string, active = true) =>
    request<Job[]>(`/api/projects/${projectId}/jobs?active=${active ? 'true' : 'false'}`),
  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  updateShot: (id: string, input: ShotUpdate) =>
    request<Shot>(`/api/shots/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  generateShotImage: (id: string, aspectRatio?: AspectRatio, characterIds: string[] = []) =>
    request<Job>(`/api/shots/${id}/image/generate${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({ character_ids: characterIds }),
    }),
  createCharacter: (projectId: string, input: { name: string; notes?: string }) =>
    request<Character>(`/api/projects/${projectId}/characters`, {
      method: 'POST',
      body: JSON.stringify({ name: input.name, notes: input.notes || '' }),
    }),
  updateCharacter: (id: string, input: { name?: string; notes?: string }) =>
    request<Character>(`/api/characters/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteCharacter: (id: string) => request<void>(`/api/characters/${id}`, { method: 'DELETE' }),
  uploadCharacterReference: (id: string, file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<Character>(`/api/characters/${id}/references`, { method: 'POST', body })
  },
  generateCharacterReference: (id: string, look?: string) =>
    request<Job>(
      `/api/characters/${id}/references/generate${look ? `?look=${encodeURIComponent(look)}` : ''}`,
      { method: 'POST' },
    ),
  deleteCharacterReference: (id: string) =>
    request<void>(`/api/character-references/${id}`, { method: 'DELETE' }),
  syncCast: (projectId: string) =>
    request<Character[]>(`/api/projects/${projectId}/cast/sync`, { method: 'POST' }),
  uploadShotImage: (id: string, file: File, aspectRatio?: AspectRatio) => {
    const body = new FormData()
    body.append('file', file)
    return request<Shot>(`/api/shots/${id}/image/upload${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, { method: 'POST', body })
  },
  deleteShotImage: (id: string, aspectRatio?: AspectRatio) =>
    request<void>(`/api/shots/${id}/image${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, { method: 'DELETE' }),
  generateShotClip: (id: string, aspectRatio?: AspectRatio) =>
    request<Job>(`/api/shots/${id}/clip/generate${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, { method: 'POST' }),
  uploadShotClip: (id: string, file: File, aspectRatio?: AspectRatio) => {
    const body = new FormData()
    body.append('file', file)
    return request<Shot>(`/api/shots/${id}/clip/upload${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, { method: 'POST', body })
  },
  deleteShotClip: (id: string, aspectRatio?: AspectRatio) =>
    request<void>(`/api/shots/${id}/clip${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, { method: 'DELETE' }),
  renderFinalCut: (id: string, variant: OutputVariant = 'landscape') =>
    request<Job>(`/api/projects/${id}/final-cut/render?variant=${variant}`, { method: 'POST' }),
  deleteFinalCut: (id: string, variant: OutputVariant = 'landscape') =>
    request<void>(`/api/projects/${id}/final-cut?variant=${variant}`, { method: 'DELETE' }),
  timeline: (id: string, edit: OutputVariant = 'landscape') =>
    request<ProjectTimeline>(`/api/projects/${id}/timeline?edit=${edit}`),
  saveTimeline: (id: string, timeline: ProjectTimeline, edit: OutputVariant = 'landscape') =>
    request<ProjectTimeline>(`/api/projects/${id}/timeline`, {
      method: 'PUT',
      body: JSON.stringify({
        revision: timeline.revision,
        document: timeline.document,
        edit: timeline.edit || edit,
      }),
    }),
  resetTimeline: (id: string) => request<ProjectTimeline>(`/api/projects/${id}/timeline/reset`, { method: 'POST' }),
  uploadTimelineAsset: (id: string, file: File, label = file.name) => {
    const body = new FormData()
    body.append('file', file)
    body.append('label', label)
    return request<ProjectTimeline>(`/api/projects/${id}/timeline/assets`, { method: 'POST', body })
  },
  deleteTimelineAsset: (id: string, assetId: string) => request<void>(`/api/projects/${id}/timeline/assets/${assetId}`, { method: 'DELETE' }),
  decide: (id: string, decision: 'accepted' | 'rejected') =>
    request<Suggestion>(`/api/suggestions/${id}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
    }),
  approve: (id: string) =>
    request<ProjectSummary>(`/api/projects/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approved: true }),
    }),
}
