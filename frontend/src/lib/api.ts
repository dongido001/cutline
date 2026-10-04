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
  supports_image_references?: boolean
}

export interface ShotContinuityCharacter {
  character_id: string
  name: string
  has_reference: boolean
  reference_ids: string[]
}

export interface ShotContinuityStatus {
  snapshot: ContinuitySnapshot | null
  characters: ShotContinuityCharacter[]
  missing_character_references: string[]
  missing_fields: string[]
  ready: boolean
}

export interface ContinuitySnapshot {
  version: number
  character_ids: string[]
  characters: Array<{ id: string; name: string; reference_ids: string[] }>
  scene: {
    title: string
    action?: string
    location: string
    time_of_day: string
    set_notes: string
    lighting_notes: string
    wardrobe_notes: string
    props_notes: string
  }
  framing: string
  action: string
  video_prompt: string
  aspect_ratio: AspectRatio
  image_prompt_base: string
  effective_image_prompt: string
}

export interface DialogueLine {
  character: string
  text: string
  speaker?: string
  line?: string
}

export interface Shot {
  id: string
  order_index: number
  title: string
  start_ms: number | null
  end_ms: number | null
  action: string
  dialogue: DialogueLine[]
  sound: string
  duration_ms: number
  framing: string
  image_prompt: string
  video_prompt: string
  title_card_text: string
  character_ids: string[]
  continuity_snapshot?: ContinuitySnapshot | null
  continuity?: ShotContinuityStatus
  status: string
  selected_image_take_id?: string | null
  selected_clip_take_id?: string | null
  selected_audio_take_id?: string | null
  dialogue_audio_filename?: string | null
  dialogue_audio_mime?: string | null
  continuity_frames?: ShotContinuityFrame[]
  image_references?: ShotImageReference[]
  image: ShotImage | null
  clip: ShotClip | null
  media_variants: ShotMediaVariant[]
  conversation_url?: string | null
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
  settings: Record<string, unknown> & { image_reference?: ShotImageReference }
  selected: boolean
  conversation_url?: string | null
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

export interface ShotContinuityFrame {
  id: string
  aspect_ratio: AspectRatio
  url: string
  mime_type: string
  source_shot_id: string
  source_shot_title: string
  source_time_ms: number
  created_at: string
}

export interface ImageReferenceSource {
  source_shot_id: string
  source_shot_title: string
  source_media_id: string
  source_media_kind: string
  source_media_type: 'image' | 'clip'
  source_aspect_ratio: AspectRatio
  url: string
  mime_type: string
}

export interface ImageReferenceSourceShot {
  id: string
  title: string
  media: ImageReferenceSource[]
}

export interface ShotImageReference extends ImageReferenceSource {
  id: string
  aspect_ratio: AspectRatio
  filename: string
  source_time_ms: number | null
  mode: 'visual_continuity'
  created_at: string
}

export interface ShotMediaVariant extends ShotImage {
  media_type: 'image' | 'clip'
  aspect_ratio: '16:9' | '9:16'
}

export interface LipSyncStatus {
  id: string
  label: string
  configured: boolean
  available: boolean
  detail: string
  available_providers: string[]
}

export interface ShotCreate {
  title?: string
  action?: string
  dialogue?: Array<{ character: string; text: string; speaker?: string; line?: string }>
  sound?: string
  duration_ms?: number
  framing?: string
  image_prompt?: string
  video_prompt?: string
  title_card_text?: string
  character_ids?: string[]
  insert_after_shot_id?: string
}

export interface ShotUpdate {
  framing: string
  duration_ms: number
  image_prompt: string
  video_prompt: string
  title_card_text: string
  character_ids?: string[]
  dialogue?: Array<{ character: string; text: string; speaker?: string; line?: string }>
  title?: string
  action?: string
  sound?: string
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
  dialogue: DialogueLine[]
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
  episode_id?: string | null
  version_number: number
  source_text: string
  status: string
  scenes: Scene[]
  suggestions: Suggestion[]
}

export interface EpisodeSummary {
  id: string
  project_id: string
  episode_number: number
  title: string
  synopsis?: string | null
  status: string
  shot_count: number
  duration_ms: number
  created_at: string
  updated_at: string
}

export interface EpisodeCreate {
  title: string
  script: string
  target_duration_seconds?: number
}

export interface EpisodeUpdate {
  title?: string
  synopsis?: string | null
  status?: string
}

export interface ProjectUpdateInput {
  title?: string
  episode_label?: string
}

export interface ProjectSummary {
  id: string
  title: string
  status: string
  aspect_ratio: string
  target_duration_seconds: number
  text_provider: string
  episode_label: string
  created_at: string
  updated_at: string
}

export interface ProjectDetail extends ProjectSummary {
  episodes: EpisodeSummary[]
  current_episode_id: string | null
  current_version: ScriptVersion | null
  final_cut: ProjectFinalCut | null
  variant_exports: ProjectVariantExport[]
  characters: Character[]
  publishes: PublishRecord[]
}

export interface CharacterReference {
  id: string
  url: string
  mime_type: string
  created_at: string
}

export interface VoicePreset {
  id: string
  provider: string
  name: string
  gender: 'female' | 'male' | 'neutral'
  language: string
  description: string
  preview_url: string | null
}

export interface VoiceProviderInfo {
  id: string
  label: string
  configured: boolean
  available: boolean
  detail: string
  voices: VoicePreset[]
}

export interface VoiceAuditionResponse {
  url: string
  provider: string
  voice_id: string
  duration_ms: number
}

export interface Character {
  id: string
  name: string
  notes: string
  voice_provider?: string
  voice_id?: string | null
  voice_settings?: Record<string, any>
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


export interface PublishProviderStatus {
  id: 'youtube' | 'facebook'
  label: string
  configured: boolean
  available: boolean
  connected: boolean
  account_name: string | null
  detail: string
  privacy_options: string[]
}

export interface SocialAuthStatus {
  platform: 'youtube' | 'facebook'
  connected: boolean
  account_id: string | null
  account_name: string | null
  account_picture: string | null
  expires_at: string | null
  is_expired: boolean
}

export interface PublishAsset {
  id: string
  scope: 'final' | 'scene' | 'shot'
  label: string
  source_ref: string | null
  variant: OutputVariant
  aspect_ratio: AspectRatio
  duration_ms: number
  ready: boolean
  missing_count: number
  url: string | null
}

export interface PublishPlan {
  variant: OutputVariant
  aspect_ratio: AspectRatio
  providers: PublishProviderStatus[]
  final: PublishAsset[]
  scenes: PublishAsset[]
  shots: PublishAsset[]
}

export interface PublishRecord {
  id: string
  project_id: string
  job_id: string | null
  scope: 'final' | 'scenes' | 'shots'
  variant: OutputVariant
  platforms: Array<'youtube' | 'facebook'>
  title: string
  description: string
  tags: string[]
  privacy: Record<string, string>
  selected_asset_ids: string[]
  scheduled_at: string | null
  made_for_kids: boolean
  options: Record<string, unknown>
  status: string
  results: Array<Record<string, unknown>>
  created_at: string
  updated_at: string
}

export interface PublishCreate {
  platforms: Array<'youtube' | 'facebook'>
  scope: 'final' | 'scenes' | 'shots'
  variant: OutputVariant
  title: string
  description: string
  tags: string[]
  privacy: Record<'youtube' | 'facebook', string>
  selected_asset_ids: string[]
  scheduled_at?: string | null
  schedule_interval_hours?: number | null
  made_for_kids: boolean
  platform_options?: Record<string, Record<string, unknown>>
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
  mute_audio?: boolean
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
  attempts: Array<Record<string, any>>
  queue_position: number | null
  created_at?: string
  updated_at?: string
}

export interface QueueJobItem extends Job {
  project_title?: string
  elapsed_seconds?: number
}

export interface QueueStat {
  id: string
  name: string
  pending: number
  scheduled: number
  status: string
}

export interface BridgeStatus {
  id: string
  name: string
  url: string
  available: boolean
  detail: string
}

export interface SystemService {
  id: string
  name: string
  description: string
  category: string
  port: number | null
  status: 'running' | 'stopped' | 'warning'
  pid: number | null
  all_pids: number[]
  url: string | null
  warning: string | null
  details: Record<string, any>
  log_path: string
}

export interface QueueSystemStatus {
  queues: QueueStat[]
  active_jobs: number
  failed_jobs: number
  completed_jobs: number
  total_jobs: number
  bridges: BridgeStatus[]
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
      shot_count: number
      scene_count: number
      scenes: Array<{
        id: string
        title: string
        order_index: number
        shot_count: number
        images_ready: number
        clips_ready: number
        audio_ready: number
        audio_needed: number
        status: 'pending' | 'in_progress' | 'ready'
      }>
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
  lipsyncShot: (shotId: string, aspectRatio?: AspectRatio) =>
    request<Job>(`/api/shots/${shotId}/lipsync${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, {
      method: 'POST',
    }),
  lipsyncStatus: () => request<LipSyncStatus>('/api/system/lipsync/status'),
  generateShotAudio: (shotId: string) =>
    request<Shot>(`/api/shots/${shotId}/audio/generate`, { method: 'POST' }),
  selectTake: (shotId: string, takeId: string) =>
    request<Shot>(`/api/shots/${shotId}/takes/select`, {
      method: 'POST',
      body: JSON.stringify({ take_id: takeId }),
    }),
  projects: () => request<ProjectSummary[]>('/api/projects'),
  project: (id: string, episodeId?: string) =>
    request<ProjectDetail>(`/api/projects/${id}${episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''}`),
  updateProject: (id: string, input: ProjectUpdateInput) =>
    request<ProjectSummary>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  episodes: (projectId: string) => request<EpisodeSummary[]>(`/api/projects/${projectId}/episodes`),
  createEpisode: (projectId: string, input: EpisodeCreate) =>
    request<Job>(`/api/projects/${projectId}/episodes`, { method: 'POST', body: JSON.stringify(input) }),
  updateEpisode: (projectId: string, episodeId: string, input: EpisodeUpdate) =>
    request<EpisodeSummary>(`/api/projects/${projectId}/episodes/${episodeId}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteEpisode: (projectId: string, episodeId: string) =>
    request<void>(`/api/projects/${projectId}/episodes/${episodeId}`, { method: 'DELETE' }),
  createProject: (input: ProjectInput) =>
    request<Job>('/api/projects', { method: 'POST', body: JSON.stringify(input) }),
  continueProject: (id: string, input: ProjectContinuationInput) =>
    request<Job>(`/api/projects/${id}/continue`, { method: 'POST', body: JSON.stringify(input) }),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  projectJobs: (projectId: string, active = true) =>
    request<Job[]>(`/api/projects/${projectId}/jobs?active=${active ? 'true' : 'false'}`),
  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  allJobs: (params?: { status?: string; kind?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams()
    if (params?.status) query.set('status', params.status)
    if (params?.kind) query.set('kind', params.kind)
    if (params?.limit) query.set('limit', String(params.limit))
    if (params?.offset) query.set('offset', String(params.offset))
    const qs = query.toString()
    return request<QueueJobItem[]>(`/api/jobs${qs ? `?${qs}` : ''}`)
  },
  systemQueues: () => request<QueueSystemStatus>('/api/system/queues'),
  retryJob: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: 'POST' }),
  cancelAllJobs: () => request<{ cancelled: number }>('/api/jobs/cancel-all', { method: 'POST' }),
  cleanupJobs: (statusFilter?: string) =>
    request<{ deleted: number }>(`/api/jobs/cleanup${statusFilter ? `?status_filter=${statusFilter}` : ''}`, { method: 'DELETE' }),
  deleteJob: (id: string) => request<void>(`/api/jobs/${id}`, { method: 'DELETE' }),
  flushQueue: (queueName: string) =>
    request<{ status: string; message: string }>(`/api/system/queues/${queueName}/flush`, { method: 'POST' }),
  systemServices: () => request<SystemService[]>('/api/system/services'),
  startService: (id: string) =>
    request<{ ok: boolean; message?: string }>(`/api/system/services/${id}/start`, { method: 'POST' }),
  stopService: (id: string) =>
    request<{ ok: boolean; message?: string }>(`/api/system/services/${id}/stop`, { method: 'POST' }),
  restartService: (id: string) =>
    request<{ ok: boolean; message?: string }>(`/api/system/services/${id}/restart`, { method: 'POST' }),
  serviceLogs: (id: string, lines = 100) =>
    request<{ id: string; logs: string }>(`/api/system/services/${id}/logs?lines=${lines}`),
  createShot: (sceneId: string, input?: ShotCreate) =>
    request<Shot>(`/api/scenes/${sceneId}/shots`, {
      method: 'POST',
      body: JSON.stringify(input ?? {}),
    }),
  deleteShot: (id: string) =>
    request<void>(`/api/shots/${id}`, { method: 'DELETE' }),
  updateShot: (id: string, input: ShotUpdate) =>
    request<Shot>(`/api/shots/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  generateShotImage: (
    id: string,
    aspectRatio?: AspectRatio,
    characterIds?: string[],
    refreshContinuity = false,
    imageReferenceId?: string | null,
  ) =>
    request<Job>(`/api/shots/${id}/image/generate${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({
        character_ids: characterIds,
        refresh_continuity: refreshContinuity,
        image_reference_id: imageReferenceId ?? null,
      }),
    }),
  createCharacter: (projectId: string, input: { name: string; notes?: string }) =>
    request<Character>(`/api/projects/${projectId}/characters`, {
      method: 'POST',
      body: JSON.stringify({ name: input.name, notes: input.notes || '' }),
    }),
  voices: () => request<VoiceProviderInfo[]>('/api/voices'),
  auditionVoice: (input: { provider: string; voice_id: string; text: string; settings?: Record<string, any> }) =>
    request<VoiceAuditionResponse>('/api/voices/audition', { method: 'POST', body: JSON.stringify(input) }),
  uploadCustomVoice: (formData: FormData) =>
    request<VoicePreset>('/api/voices/custom', { method: 'POST', body: formData }),
  updateCharacter: (id: string, input: { name?: string; notes?: string; voice_provider?: string; voice_id?: string | null; voice_settings?: Record<string, any> }) =>
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
  imageReferenceSources: (id: string) =>
    request<ImageReferenceSourceShot[]>(`/api/shots/${id}/image-reference/sources`),
  saveImageReference: (id: string, source: ImageReferenceSource, aspect: AspectRatio, frame?: { file: File; timeMs: number }) => {
    const body = new FormData()
    body.append('source_shot_id', source.source_shot_id)
    body.append('source_media_id', source.source_media_id)
    body.append('source_media_kind', source.source_media_kind)
    body.append('aspect_ratio', aspect)
    body.append('mode', 'visual_continuity')
    if (frame) {
      body.append('file', frame.file)
      body.append('source_time_ms', String(frame.timeMs))
    }
    return request<Shot>(`/api/shots/${id}/image-reference`, { method: 'POST', body })
  },
  removeImageReference: (id: string, aspect: AspectRatio) =>
    request<void>(`/api/shots/${id}/image-reference?aspect_ratio=${encodeURIComponent(aspect)}`, { method: 'DELETE' }),
  saveShotContinuityFrame: (
    id: string,
    file: File,
    sourceShotId: string,
    sourceTimeMs: number,
    aspectRatio: AspectRatio,
  ) => {
    const body = new FormData()
    body.append('file', file)
    body.append('source_shot_id', sourceShotId)
    body.append('source_time_ms', String(sourceTimeMs))
    body.append('aspect_ratio', aspectRatio)
    return request<Shot>(`/api/shots/${id}/continuity-frame`, { method: 'POST', body })
  },
  deleteShotContinuityFrame: (id: string, aspectRatio: AspectRatio) =>
    request<void>(`/api/shots/${id}/continuity-frame?aspect_ratio=${encodeURIComponent(aspectRatio)}`, { method: 'DELETE' }),
  generateShotClip: (id: string, aspectRatio?: AspectRatio) =>
    request<Job>(`/api/shots/${id}/clip/generate${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, { method: 'POST' }),
  uploadShotClip: (id: string, file: File, aspectRatio?: AspectRatio) => {
    const body = new FormData()
    body.append('file', file)
    return request<Shot>(`/api/shots/${id}/clip/upload${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, { method: 'POST', body })
  },
  deleteShotClip: (id: string, aspectRatio?: AspectRatio) =>
    request<void>(`/api/shots/${id}/clip${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, { method: 'DELETE' }),
  renderFinalCut: (id: string, variant: OutputVariant = 'landscape', episodeId?: string) => {
    const qs = new URLSearchParams({ variant })
    if (episodeId) qs.set('episode_id', episodeId)
    return request<Job>(`/api/projects/${id}/final-cut/render?${qs.toString()}`, { method: 'POST' })
  },
  deleteFinalCut: (id: string, variant: OutputVariant = 'landscape') =>
    request<void>(`/api/projects/${id}/final-cut?variant=${variant}`, { method: 'DELETE' }),
  timeline: (id: string, edit: OutputVariant = 'landscape', episodeId?: string) => {
    const qs = new URLSearchParams({ edit })
    if (episodeId) qs.set('episode_id', episodeId)
    return request<ProjectTimeline>(`/api/projects/${id}/timeline?${qs.toString()}`)
  },
  saveTimeline: (id: string, timeline: ProjectTimeline, edit: OutputVariant = 'landscape', episodeId?: string) => {
    const sanitizePrimary = (item: TimelinePrimaryItem): TimelinePrimaryItem => ({
      ...item,
      duration_ms: Math.round(item.duration_ms),
      source_in_ms: Math.round(item.source_in_ms || 0),
      source_out_ms: item.source_out_ms != null ? Math.round(item.source_out_ms) : null,
      mute_audio: Boolean(item.mute_audio),
    })
    const sanitizeOverlay = <T extends { start_ms: number; duration_ms: number }>(overlay: T): T => ({
      ...overlay,
      start_ms: Math.round(overlay.start_ms),
      duration_ms: Math.round(overlay.duration_ms),
    })
    const cleanDoc = {
      ...timeline.document,
      primary: (timeline.document.primary || []).map(sanitizePrimary),
      image_overlays: (timeline.document.image_overlays || []).map(sanitizeOverlay),
      text_overlays: (timeline.document.text_overlays || []).map(sanitizeOverlay),
    }
    const qs = episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''
    return request<ProjectTimeline>(`/api/projects/${id}/timeline${qs}`, {
      method: 'PUT',
      body: JSON.stringify({
        revision: timeline.revision,
        document: cleanDoc,
        edit: timeline.edit || edit,
      }),
    })
  },
  resetTimeline: (id: string, episodeId?: string) => {
    const qs = episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''
    return request<ProjectTimeline>(`/api/projects/${id}/timeline/reset${qs}`, { method: 'POST' })
  },
  uploadTimelineAsset: (id: string, file: File, label = file.name) => {
    const body = new FormData()
    body.append('file', file)
    body.append('label', label)
    return request<ProjectTimeline>(`/api/projects/${id}/timeline/assets`, { method: 'POST', body })
  },
  deleteTimelineAsset: (id: string, assetId: string) => request<void>(`/api/projects/${id}/timeline/assets/${assetId}`, { method: 'DELETE' }),
  publishProviders: () => request<PublishProviderStatus[]>('/api/publish/providers'),
  publishPlan: (id: string, variant: OutputVariant = 'landscape') =>
    request<PublishPlan>(`/api/projects/${id}/publish/plan?variant=${variant}`),
  publishes: (id: string) => request<PublishRecord[]>(`/api/projects/${id}/publishes`),
  publishProject: (id: string, input: PublishCreate) =>
    request<Job>(`/api/projects/${id}/publish`, { method: 'POST', body: JSON.stringify(input) }),
  decide: (id: string, decision: 'accepted' | 'rejected') =>
    request<Suggestion>(`/api/suggestions/${id}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision }),
    }),
  approve: (id: string, episodeId?: string) =>
    request<ProjectSummary>(`/api/projects/${id}/approve${episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({ approved: true }),
    }),
  // Social OAuth
  socialAuthStatus: () => request<SocialAuthStatus[]>('/auth/social/status'),
  socialAuthStart: (platform: 'youtube' | 'facebook') =>
    request<{ url: string }>(`/auth/${platform}/start`),
  socialDisconnect: (platform: 'youtube' | 'facebook') =>
    request<void>(`/auth/${platform}/disconnect`, { method: 'DELETE' }),
}
