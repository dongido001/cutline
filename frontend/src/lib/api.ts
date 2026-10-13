export interface FlowModelPreset {
  id: string
  label: string
  short_label: string
  model_family: string
  flow_model_key: string
  credit_note: string
}

export const DEFAULT_FLOW_PRESETS: FlowModelPreset[] = [
  {
    id: 'veo_lite',
    label: 'Veo 3.1 Lite · Standard',
    short_label: 'Veo Lite · Standard (Low credits)',
    model_family: 'veo',
    flow_model_key: 'veo_3_1_i2v_lite',
    credit_note: 'Works on Free/Pro accounts · lowest credit cost',
  },
  {
    id: 'omni_flash',
    label: 'Gemini Omni 1.1 Flash',
    short_label: 'Omni 1.1 Flash (4–10s)',
    model_family: 'omni_flash',
    flow_model_key: 'abra_i2v_*',
    credit_note: 'Works on Free/Pro accounts · ~10–20 credits/clip',
  },
  {
    id: 'veo_lite_relaxed',
    label: 'Veo 3.1 Lite · Relaxed (0 credits · Ultra only)',
    short_label: 'Veo Lite · 0 Credits (Ultra sub only)',
    model_family: 'veo',
    flow_model_key: 'veo_3_1_i2v_lite_low_priority',
    credit_note: 'Requires paid Google AI Ultra subscription',
  },
  {
    id: 'veo_fast',
    label: 'Veo 3.1 Fast · Ultra',
    short_label: 'Veo Fast · Ultra',
    model_family: 'veo',
    flow_model_key: 'veo_3_1_i2v_s_fast_ultra',
    credit_note: 'Fastest Veo queue · uses credits',
  },
]

export interface ProviderStatus {
  id: string
  label: string
  configured: boolean
  available: boolean
  model: string
  detail: string
  active_preset?: string | null
  presets?: FlowModelPreset[] | null
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
  characters: Array<{ id: string; name: string; reference_ids: string[]; voice_description?: string; notes?: string }>
  character_voices?: Array<{ id: string; name: string; notes?: string; voice_description?: string }>
  dialogue?: DialogueLine[]
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
  source: 'generated' | 'uploaded' | 'adapted' | string
  provider: string
  prompt: string
  created_at: string
  color_boost_strength?: number
  can_undo_color?: boolean
  can_redo_color?: boolean
}

export type ShotClip = ShotImage

export type ReferenceColorRetouchAction =
  | 'boost'
  | 'increase'
  | 'decrease'
  | 'set'
  | 'undo'
  | 'redo'
  | 'reset'

export function videoColorBoostFilter(strength?: number | null): string {
  const s = Math.max(-1, Math.min(3, Number(strength ?? 0)))
  if (Math.abs(s) < 1e-4) return ''
  const contrast = Math.max(0.3, 1 + 0.14 * s).toFixed(3)
  const saturate = Math.max(0.1, 1 + 0.32 * s).toFixed(3)
  const brightness = Math.max(0.85, Math.min(1.15, 1 + 0.015 * s)).toFixed(3)
  return `contrast(${contrast}) saturate(${saturate}) brightness(${brightness})`
}

export function lutCssFilter(lutId?: string | null, strength?: number | null): string {
  if (!lutId || lutId === 'none') return ''
  const s = Math.max(0, Math.min(1, typeof strength === 'number' ? strength : 1))
  if (s < 0.001) return ''

  switch (lutId) {
    case 'vibrant_gaming': {
      const sat = (1 + 0.4 * s).toFixed(3)
      const con = (1 + 0.15 * s).toFixed(3)
      const bri = (1 + 0.02 * s).toFixed(3)
      return `saturate(${sat}) contrast(${con}) brightness(${bri})`
    }
    case 'warzone_crisp': {
      const con = (1 + 0.22 * s).toFixed(3)
      const sat = (1 + 0.2 * s).toFixed(3)
      const bri = (1 - 0.02 * s).toFixed(3)
      return `contrast(${con}) saturate(${sat}) brightness(${bri})`
    }
    case 'teal_orange': {
      const con = (1 + 0.18 * s).toFixed(3)
      const sat = (1 + 0.25 * s).toFixed(3)
      const sep = (0.2 * s).toFixed(3)
      const hue = (-15 * s).toFixed(1)
      return `contrast(${con}) saturate(${sat}) sepia(${sep}) hue-rotate(${hue}deg)`
    }
    case 'cyberpunk_neon': {
      const con = (1 + 0.25 * s).toFixed(3)
      const sat = (1 + 0.5 * s).toFixed(3)
      const hue = (18 * s).toFixed(1)
      return `contrast(${con}) saturate(${sat}) hue-rotate(${hue}deg)`
    }
    case 'warm_sunshine': {
      const sep = (0.22 * s).toFixed(3)
      const sat = (1 + 0.28 * s).toFixed(3)
      const con = (1 + 0.08 * s).toFixed(3)
      const bri = (1 + 0.03 * s).toFixed(3)
      return `sepia(${sep}) saturate(${sat}) contrast(${con}) brightness(${bri})`
    }
    case 'cold_tactical': {
      const sat = (1 - 0.25 * s).toFixed(3)
      const con = (1 + 0.2 * s).toFixed(3)
      const hue = (-10 * s).toFixed(1)
      const bri = (1 - 0.03 * s).toFixed(3)
      return `saturate(${sat}) contrast(${con}) hue-rotate(${hue}deg) brightness(${bri})`
    }
    case 'high_contrast_pop': {
      const con = (1 + 0.35 * s).toFixed(3)
      const sat = (1 + 0.2 * s).toFixed(3)
      return `contrast(${con}) saturate(${sat})`
    }
    case 'sunset_glow': {
      const sep = (0.25 * s).toFixed(3)
      const sat = (1 + 0.35 * s).toFixed(3)
      const hue = (-25 * s).toFixed(1)
      const con = (1 + 0.12 * s).toFixed(3)
      return `sepia(${sep}) saturate(${sat}) hue-rotate(${hue}deg) contrast(${con})`
    }
    case 'retro_arcade': {
      const con = (1 + 0.2 * s).toFixed(3)
      const sat = (1 + 0.3 * s).toFixed(3)
      const sep = (0.15 * s).toFixed(3)
      const hue = (10 * s).toFixed(1)
      return `contrast(${con}) saturate(${sat}) sepia(${sep}) hue-rotate(${hue}deg)`
    }
    case 'night_vision_stealth': {
      const hue = (65 * s).toFixed(1)
      const sat = (1 + 0.6 * s).toFixed(3)
      const con = (1 + 0.25 * s).toFixed(3)
      const bri = (1 - 0.05 * s).toFixed(3)
      return `hue-rotate(${hue}deg) saturate(${sat}) contrast(${con}) brightness(${bri})`
    }
    default:
      return ''
  }
}

export interface ShotContinuityFrame {
  id: string
  aspect_ratio: AspectRatio
  url: string
  mime_type: string
  source_shot_id: string
  source_shot_title: string
  source_time_ms: number
  created_at: string
  color_boost_strength?: number
  can_undo_color?: boolean
  can_redo_color?: boolean
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
  mode: 'visual_continuity' | 'aspect_conversion'
  created_at: string
  color_boost_strength?: number
  can_undo_color?: boolean
  can_redo_color?: boolean
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

export interface ScriptHistory {
  current_version_id: string
  current_fingerprint: string
  versions: Array<{ id: string; version_number: number; status: string; created_at: string }>
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
  aspect_ratio?: AspectRatio
  target_duration_seconds?: number
  text_provider?: 'mock' | 'ollama' | 'gemini_web' | 'chatgpt_web' | 'compatible'
  flow_project_id?: string | null
}

export interface ProjectSummary {
  id: string
  title: string
  status: string
  aspect_ratio: string
  target_duration_seconds: number
  text_provider: string
  flow_project_id?: string | null
  episode_label: string
  created_at: string
  updated_at: string
}

export interface CompilationSummary {
  id: string
  project_id: string
  title: string
  description?: string | null
  episode_ids: string[]
  settings: Record<string, any>
  created_at: string
  updated_at: string
  duration_ms?: number
  clip_count?: number
}

export interface CompilationCreateInput {
  title: string
  description?: string | null
  episode_ids: string[]
  settings?: Record<string, any>
}

export interface CompilationUpdateInput {
  title?: string
  description?: string | null
  episode_ids?: string[]
  settings?: Record<string, any>
}

export interface ProjectDetail extends ProjectSummary {
  episodes: EpisodeSummary[]
  current_episode_id: string | null
  current_version: ScriptVersion | null
  final_cut: ProjectFinalCut | null
  variant_exports: ProjectVariantExport[]
  characters: Character[]
  publishes: PublishRecord[]
  compilations: CompilationSummary[]
}

export type ElementKind = 'character' | 'scene' | 'prop'
export type ReferenceViewType = 'main' | '3d_view' | 'scene' | 'prop'

export interface CharacterReference {
  id: string
  view_type?: ReferenceViewType
  source?: string
  provider?: string
  prompt?: string
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
  kind?: ElementKind
  name: string
  notes: string
  voice_description?: string
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
  position: 'top-left' | 'top' | 'top-right' | 'center' | 'bottom-left' | 'bottom' | 'bottom-right' | 'full-frame'
  scale: number
  crop_top?: number
  crop_bottom?: number
  crop_left?: number
  crop_right?: number
  /** Horizontal shift as a fraction of frame width (-1..1, negative = left). */
  offset_x?: number
  /** Vertical shift as a fraction of frame height (-1..1, negative = up). */
  offset_y?: number
  lut?: string
  lut_strength?: number
}

export interface LUTPreset {
  id: string
  label: string
  description: string
  category: string
  css_filter: string
}

export const DEFAULT_LUT_PRESETS: LUTPreset[] = [
  {
    id: 'vibrant_gaming',
    label: 'Vibrant Gaming',
    description: 'Punchy saturation and clarity for colorful games',
    category: 'Gaming',
    css_filter: 'saturate(1.4) contrast(1.15) brightness(1.02)',
  },
  {
    id: 'warzone_crisp',
    label: 'Warzone Crisp',
    description: 'Cuts through dust and fog haze with deep shadows',
    category: 'Gaming',
    css_filter: 'contrast(1.22) saturate(1.2) brightness(0.98)',
  },
  {
    id: 'teal_orange',
    label: 'Teal & Orange',
    description: 'Cinematic Hollywood action block with cool shadows and warm skin tones',
    category: 'Cinematic',
    css_filter: 'contrast(1.18) saturate(1.25) sepia(0.2) hue-rotate(-15deg)',
  },
  {
    id: 'cyberpunk_neon',
    label: 'Cyberpunk Neon',
    description: 'Deep midnight blues, electric cyan, and rich neon highlights',
    category: 'Creative',
    css_filter: 'contrast(1.25) saturate(1.5) hue-rotate(18deg)',
  },
  {
    id: 'warm_sunshine',
    label: 'Warm Sunshine',
    description: 'Golden hour warmth with lifted soft shadows',
    category: 'Atmospheric',
    css_filter: 'sepia(0.22) saturate(1.28) contrast(1.08) brightness(1.03)',
  },
  {
    id: 'cold_tactical',
    label: 'Cold Tactical',
    description: 'Militaristic desaturated slate blues with high edge contrast',
    category: 'Gaming',
    css_filter: 'saturate(0.75) contrast(1.2) hue-rotate(-10deg) brightness(0.97)',
  },
  {
    id: 'high_contrast_pop',
    label: 'High Contrast Pop',
    description: 'Deep crushed blacks and gleaming bright highlights for social clips',
    category: 'Social',
    css_filter: 'contrast(1.35) saturate(1.2)',
  },
  {
    id: 'sunset_glow',
    label: 'Sunset Glow',
    description: 'Rich amber-peach highlights with deep violet shadows',
    category: 'Atmospheric',
    css_filter: 'sepia(0.25) saturate(1.35) hue-rotate(-25deg) contrast(1.12)',
  },
  {
    id: 'retro_arcade',
    label: 'Retro 80s Arcade',
    description: 'Vintage black-point lift and nostalgic synthwave cross-processing',
    category: 'Creative',
    css_filter: 'contrast(1.2) saturate(1.3) sepia(0.15) hue-rotate(10deg)',
  },
  {
    id: 'night_vision_stealth',
    label: 'Night Vision / Stealth',
    description: 'Tactical phosphor green monochrome with high silhouette clarity',
    category: 'Gaming',
    css_filter: 'hue-rotate(65deg) saturate(1.6) contrast(1.25) brightness(0.95)',
  },
]

export interface TimelineSource {
  kind: 'shot_clip' | 'shot_image' | 'shot_audio' | 'asset'
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

export interface TimelineVideoOverlay {
  id: string
  label: string
  source: TimelineSource
  start_ms: number
  duration_ms: number
  source_in_ms: number
  opacity: number
  volume: number
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

export interface TimelineAudioOverlay {
  id: string
  label: string
  source: TimelineSource
  start_ms: number
  duration_ms: number
  source_in_ms: number
  volume: number
  mute_audio?: boolean
}

export interface TimelineDocument {
  version: 1
  primary: TimelinePrimaryItem[]
  video_overlays?: TimelineVideoOverlay[]
  image_overlays: TimelineImageOverlay[]
  text_overlays: TimelineTextOverlay[]
  audio_overlays?: TimelineAudioOverlay[]
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
  episode_id?: string | null
  compilation_id?: string | null
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

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData
  const systemToken = import.meta.env.VITE_CUTLINE_SYSTEM_TOKEN
  const response = await fetch(path, {
    ...init,
    headers: isFormData
      ? { ...(systemToken ? { 'X-Cutline-System-Token': systemToken } : {}), ...init?.headers }
      : {
          'Content-Type': 'application/json',
          ...(systemToken ? { 'X-Cutline-System-Token': systemToken } : {}),
          ...init?.headers,
        },
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
  scriptHistory: (versionId: string) => request<ScriptHistory>(`/api/script-versions/${versionId}/history`),
  scriptVersion: (versionId: string) => request<ScriptVersion>(`/api/script-versions/${versionId}`),
  restoreScriptVersion: (versionId: string, history: ScriptHistory) => request<ScriptVersion>(
    `/api/script-versions/${versionId}/restore`, { method: 'POST', body: JSON.stringify({
      expected_version_id: history.current_version_id, expected_fingerprint: history.current_fingerprint,
    }) }),
  providers: () => request<ProviderStatus[]>('/api/providers'),
  imageProviders: () => request<ImageProviderStatus[]>('/api/image-providers'),
  videoProvider: () => request<ProviderStatus>('/api/video-provider'),
  setFlowVideoModel: (preset: string) =>
    request<ProviderStatus>('/api/video-provider/flow-model', {
      method: 'POST',
      body: JSON.stringify({ preset }),
    }),
  resetFlowSession: (projectId?: string) =>
    request<{ status: string; message: string; session_project?: Record<string, any> }>(
      `/api/video-provider/reset-flow-session${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`,
      { method: 'POST' }
    ),
  audioProvider: () => request<ProviderStatus>('/api/audio-provider'),
  draftReadiness: (projectId: string, episodeId?: string, variant?: 'landscape' | 'shorts' | 'both') => {
    const params = new URLSearchParams()
    if (episodeId) params.set('episode_id', episodeId)
    if (variant) params.set('variant', variant)
    const qs = params.toString()
    return request<{
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
    }>(`/api/projects/${projectId}/draft/readiness${qs ? `?${qs}` : ''}`)
  },
  createDraft: (projectId: string, input?: { variant?: 'landscape' | 'shorts' | 'both'; retry_failed_only?: boolean }, episodeId?: string) =>
    request<Job>(`/api/projects/${projectId}/draft${episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''}`, {
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
  editShotImageTake: (shotId: string, takeId: string, prompt: string, files: File[] = []) => {
    const body = new FormData()
    body.append('prompt', prompt)
    for (const file of files) {
      body.append('files', file)
    }
    return request<Job>(`/api/shots/${shotId}/takes/${takeId}/edit`, {
      method: 'POST',
      body,
    })
  },
  retouchShotTakeColor: (shotId: string, takeId: string) =>
    request<Shot>(`/api/shots/${shotId}/takes/${takeId}/retouch-color`, {
      method: 'POST',
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
  loadDemoProject: () => request<ProjectSummary>('/api/demo-project', { method: 'POST' }),
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
  shot: (id: string) => request<Shot>(`/api/shots/${id}`),
  generateShotImage: (
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
    },
  ) =>
    request<Job>(`/api/shots/${id}/image/generate${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`, {
      method: 'POST',
      body: JSON.stringify({
        character_ids: characterIds,
        refresh_continuity: refreshContinuity,
        image_reference_id: imageReferenceId ?? null,
        use_default_image_reference: useDefaultImageReference,
        both_aspects: Boolean(options?.bothAspects),
        generate_video: Boolean(options?.generateVideo),
        include_cast_references: Boolean(options?.includeCastReferences),
        chatgpt_dual: Boolean(options?.chatgptDual),
      }),
    }),
  characters: (projectId: string) =>
    request<Character[]>(`/api/projects/${projectId}/characters`),
  createCharacter: (projectId: string, input: { name: string; kind?: ElementKind; notes?: string; voice_description?: string }) =>
    request<Character>(`/api/projects/${projectId}/characters`, {
      method: 'POST',
      body: JSON.stringify({
        name: input.name,
        kind: input.kind || 'character',
        notes: input.notes || '',
        voice_description: input.voice_description || '',
      }),
    }),
  voices: () => request<VoiceProviderInfo[]>('/api/voices'),
  auditionVoice: (input: { provider: string; voice_id: string; text: string; settings?: Record<string, any> }) =>
    request<VoiceAuditionResponse>('/api/voices/audition', { method: 'POST', body: JSON.stringify(input) }),
  uploadCustomVoice: (formData: FormData) =>
    request<VoicePreset>('/api/voices/custom', { method: 'POST', body: formData }),
  updateCharacter: (id: string, input: { name?: string; kind?: ElementKind; notes?: string; voice_description?: string; voice_provider?: string; voice_id?: string | null; voice_settings?: Record<string, any> }) =>
    request<Character>(`/api/characters/${id}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteCharacter: (id: string) => request<void>(`/api/characters/${id}`, { method: 'DELETE' }),
  uploadCharacterReference: (id: string, file: File, viewType?: ReferenceViewType) => {
    const body = new FormData()
    body.append('file', file)
    const qs = viewType ? `?view_type=${encodeURIComponent(viewType)}` : ''
    return request<Character>(`/api/characters/${id}/references${qs}`, { method: 'POST', body })
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
  syncElements: (projectId: string) =>
    request<Character[]>(`/api/projects/${projectId}/elements/sync`, { method: 'POST' }),
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
  retouchShotReferenceColor: (
    id: string,
    aspect: AspectRatio,
    options?: { action?: ReferenceColorRetouchAction; strength?: number },
  ) => {
    const params = new URLSearchParams({ aspect_ratio: aspect })
    if (options?.action) params.set('action', options.action)
    if (options?.strength !== undefined) params.set('strength', String(options.strength))
    return request<Shot>(`/api/shots/${id}/image-reference/retouch-color?${params.toString()}`, { method: 'POST' })
  },
  retouchShotClipColor: (
    id: string,
    aspect: AspectRatio,
    options?: { action?: ReferenceColorRetouchAction; strength?: number },
  ) => {
    const params = new URLSearchParams({ aspect_ratio: aspect })
    if (options?.action) params.set('action', options.action)
    if (options?.strength !== undefined) params.set('strength', String(options.strength))
    return request<Shot>(`/api/shots/${id}/clip/retouch-color?${params.toString()}`, { method: 'POST' })
  },
  repaintShotReference: (id: string, aspect: AspectRatio, prompt = 'repaint this image') => {
    const body = new FormData()
    body.append('aspect_ratio', aspect)
    body.append('prompt', prompt)
    return request<Job>(`/api/shots/${id}/image-reference/repaint`, { method: 'POST', body })
  },
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
  generateShotClip: (
    id: string,
    aspectRatio?: AspectRatio,
    includeCastReferences = false,
    bothAspects = false,
    options?: {
      videoMode?: 'i2v' | 'r2v'
      includeSceneReference?: boolean
      elementIds?: string[]
    },
  ) => {
    const videoMode = options?.videoMode || 'i2v'
    const hasPayload = includeCastReferences || bothAspects || videoMode === 'r2v' || options?.elementIds !== undefined
    return request<Job>(
      `/api/shots/${id}/clip/generate${aspectRatio ? `?aspect_ratio=${encodeURIComponent(aspectRatio)}` : ''}`,
      hasPayload
        ? {
            method: 'POST',
            body: JSON.stringify({
              include_cast_references: includeCastReferences || videoMode === 'r2v',
              both_aspects: bothAspects,
              video_mode: videoMode,
              include_scene_reference: options?.includeSceneReference ?? true,
              ...(options?.elementIds ? { element_ids: options.elementIds } : {}),
            }),
          }
        : { method: 'POST' },
    )
  },
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
  deleteFinalCut: (id: string, variant: OutputVariant = 'landscape', episodeId?: string) => {
    const qs = new URLSearchParams({ variant })
    if (episodeId) qs.set('episode_id', episodeId)
    return request<void>(`/api/projects/${id}/final-cut?${qs.toString()}`, { method: 'DELETE' })
  },
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
    const sanitizeVideoOverlay = (overlay: TimelineVideoOverlay): TimelineVideoOverlay => ({
      ...overlay,
      start_ms: Math.round(overlay.start_ms),
      duration_ms: Math.round(overlay.duration_ms),
      source_in_ms: Math.round(overlay.source_in_ms || 0),
      opacity: Number.isFinite(overlay.opacity) ? Math.max(0, Math.min(1, Number(overlay.opacity))) : 1,
      volume: Number.isFinite(overlay.volume) ? Math.max(0, Math.min(2, Number(overlay.volume))) : 1,
      mute_audio: Boolean(overlay.mute_audio),
    })
    const sanitizeAudioOverlay = (overlay: TimelineAudioOverlay): TimelineAudioOverlay => ({
      ...overlay,
      start_ms: Math.round(overlay.start_ms),
      duration_ms: Math.round(overlay.duration_ms),
      source_in_ms: Math.round(overlay.source_in_ms || 0),
      volume: Number.isFinite(overlay.volume) ? Math.max(0, Math.min(2, Number(overlay.volume))) : 1,
      mute_audio: Boolean(overlay.mute_audio),
    })
    const cleanDoc = {
      ...timeline.document,
      primary: (timeline.document.primary || []).map(sanitizePrimary),
      video_overlays: (timeline.document.video_overlays || []).map(sanitizeVideoOverlay),
      image_overlays: (timeline.document.image_overlays || []).map(sanitizeOverlay),
      text_overlays: (timeline.document.text_overlays || []).map(sanitizeOverlay),
      audio_overlays: (timeline.document.audio_overlays || []).map(sanitizeAudioOverlay),
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
  getTimelineLuts: () => request<LUTPreset[]>('/api/timeline/luts'),
  resetTimeline: (id: string, episodeId?: string) => {
    const qs = episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''
    return request<ProjectTimeline>(`/api/projects/${id}/timeline/reset${qs}`, { method: 'POST' })
  },
  uploadTimelineAsset: (
    id: string,
    file: File,
    label = file.name,
    edit: OutputVariant = 'landscape',
    episodeId?: string,
    kind?: 'image' | 'video' | 'audio',
    compilationId?: string,
  ) => {
    const body = new FormData()
    body.append('file', file)
    body.append('label', label)
    if (kind) body.append('kind', kind)
    const qs = new URLSearchParams({ edit })
    if (compilationId) qs.set('compilation_id', compilationId)
    else if (episodeId) qs.set('episode_id', episodeId)
    return request<ProjectTimeline>(`/api/projects/${id}/timeline/assets?${qs.toString()}`, { method: 'POST', body })
  },
  deleteTimelineAsset: (id: string, assetId: string, episodeId?: string, compilationId?: string) => {
    const qs = new URLSearchParams()
    if (compilationId) qs.set('compilation_id', compilationId)
    else if (episodeId) qs.set('episode_id', episodeId)
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    return request<void>(`/api/projects/${id}/timeline/assets/${assetId}${suffix}`, { method: 'DELETE' })
  },
  publishProviders: () => request<PublishProviderStatus[]>('/api/publish/providers'),
  publishPlan: (id: string, variant: OutputVariant = 'landscape', episodeId?: string) => {
    const qs = new URLSearchParams({ variant })
    if (episodeId) qs.set('episode_id', episodeId)
    return request<PublishPlan>(`/api/projects/${id}/publish/plan?${qs.toString()}`)
  },
  publishes: (id: string) => request<PublishRecord[]>(`/api/projects/${id}/publishes`),
  publishProject: (id: string, input: PublishCreate, episodeId?: string) => {
    const qs = episodeId ? `?episode_id=${encodeURIComponent(episodeId)}` : ''
    return request<Job>(`/api/projects/${id}/publish${qs}`, { method: 'POST', body: JSON.stringify(input) })
  },
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
  // Compilations
  compilations: (projectId: string) => request<CompilationSummary[]>(`/api/projects/${projectId}/compilations`),
  createCompilation: (projectId: string, input: CompilationCreateInput) =>
    request<CompilationSummary>(`/api/projects/${projectId}/compilations`, { method: 'POST', body: JSON.stringify(input) }),
  getCompilation: (compilationId: string) => request<CompilationSummary>(`/api/compilations/${compilationId}`),
  updateCompilation: (compilationId: string, input: CompilationUpdateInput) =>
    request<CompilationSummary>(`/api/compilations/${compilationId}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteCompilation: (compilationId: string) =>
    request<void>(`/api/compilations/${compilationId}`, { method: 'DELETE' }),
  compilationTimeline: (compilationId: string, edit: OutputVariant = 'landscape') =>
    request<ProjectTimeline>(`/api/compilations/${compilationId}/timeline?edit=${edit}`),
  compilationShots: (compilationId: string) =>
    request<Shot[]>(`/api/compilations/${compilationId}/shots`),
  saveCompilationTimeline: (compilationId: string, timeline: ProjectTimeline, edit: OutputVariant = 'landscape') => {
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
    const sanitizeVideoOverlay = (overlay: TimelineVideoOverlay): TimelineVideoOverlay => ({
      ...overlay,
      start_ms: Math.round(overlay.start_ms),
      duration_ms: Math.round(overlay.duration_ms),
      source_in_ms: Math.round(overlay.source_in_ms || 0),
      opacity: Number.isFinite(overlay.opacity) ? Math.max(0, Math.min(1, Number(overlay.opacity))) : 1,
      volume: Number.isFinite(overlay.volume) ? Math.max(0, Math.min(2, Number(overlay.volume))) : 1,
      mute_audio: Boolean(overlay.mute_audio),
    })
    const sanitizeAudioOverlay = (overlay: TimelineAudioOverlay): TimelineAudioOverlay => ({
      ...overlay,
      start_ms: Math.round(overlay.start_ms),
      duration_ms: Math.round(overlay.duration_ms),
      source_in_ms: Math.round(overlay.source_in_ms || 0),
      volume: Number.isFinite(overlay.volume) ? Math.max(0, Math.min(2, Number(overlay.volume))) : 1,
      mute_audio: Boolean(overlay.mute_audio),
    })
    const cleanDoc = {
      ...timeline.document,
      primary: (timeline.document.primary || []).map(sanitizePrimary),
      video_overlays: (timeline.document.video_overlays || []).map(sanitizeVideoOverlay),
      image_overlays: (timeline.document.image_overlays || []).map(sanitizeOverlay),
      text_overlays: (timeline.document.text_overlays || []).map(sanitizeOverlay),
      audio_overlays: (timeline.document.audio_overlays || []).map(sanitizeAudioOverlay),
    }
    return request<ProjectTimeline>(`/api/compilations/${compilationId}/timeline`, {
      method: 'PUT',
      body: JSON.stringify({
        revision: timeline.revision,
        document: cleanDoc,
        edit: timeline.edit || edit,
      }),
    })
  },
  resetCompilationTimeline: (compilationId: string, edit: OutputVariant = 'landscape') =>
    request<ProjectTimeline>(
      `/api/compilations/${compilationId}/timeline/reset?edit=${encodeURIComponent(edit)}`,
      { method: 'POST' },
    ),
  exportCompilation: (compilationId: string, variant: OutputVariant = 'landscape') =>
    request<Job>(`/api/compilations/${compilationId}/export?variant=${variant}`, { method: 'POST' }),
  compilationExports: (compilationId: string) =>
    request<ProjectVariantExport[]>(`/api/compilations/${compilationId}/exports`),
  deleteCompilationExport: (compilationId: string, variant: OutputVariant = 'landscape') =>
    request<void>(`/api/compilations/${compilationId}/exports?variant=${variant}`, { method: 'DELETE' }),
}
