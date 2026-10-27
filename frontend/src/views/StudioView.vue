<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  BookOpen,
  Bot,
  Boxes,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  CircleAlert,
  Clapperboard,
  Clock3,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Film,
  History,
  Image,
  ListChecks,
  Maximize2,
  MessageSquare,
  MessageSquareText,
  Mic,
  Minus,
  MoreHorizontal,
  Palette,
  Pause,
  Play,
  Plus,
  Redo2,
  RefreshCw,
  RotateCcw,
  Save,
  Scissors,
  Settings,
  SkipBack,
  SkipForward,
  Sparkles,
  Trash2,
  Undo2,
  Upload,
  Video,
  Volume2,
  X,
} from '@lucide/vue'
import { api, DEFAULT_FLOW_PRESETS, videoColorBoostFilter, type AspectRatio, type Character, type ElementKind, type OutputVariant, type ProjectInput, type ProjectSummary, type ReferenceColorRetouchAction, type Scene, type Shot, type ShotNote, type ShotTake, type Suggestion } from '@/lib/api'
import { buildShotVideoPrompt } from '@/lib/shotPrompt'
import { useStudioStore } from '@/stores/studio'
import TimelineEditor from '@/components/TimelineEditor.vue'
import ImageReferencePanel from '@/components/ImageReferencePanel.vue'
import ShotTakesPanel from '@/components/ShotTakesPanel.vue'
import ShotNotesPanel from '@/components/ShotNotesPanel.vue'
import CastManagerModal from '@/components/CastManagerModal.vue'
import DeepReviewPanel from '@/components/DeepReviewPanel.vue'
import EpisodeSwitcher from '@/components/EpisodeSwitcher.vue'
import OriginalScriptModal from '@/components/OriginalScriptModal.vue'
import ScriptHistoryModal from '@/components/ScriptHistoryModal.vue'
import CompilationManagerModal from '@/components/CompilationManagerModal.vue'

interface DisplaySuggestion {
  id: string | number
  category: string
  severity: string
  title: string
  reason: string
  change: string
  status: string
}

type ProductionStage = 'script' | 'shots' | 'storyboards' | 'clips' | 'final-cut'

const stageTitles: Record<ProductionStage, string> = {
  script: 'Script review',
  shots: 'Produce',
  storyboards: 'Produce',
  clips: 'Produce',
  'final-cut': 'Assemble',
}

const stageSteps: Record<ProductionStage, number> = {
  script: 2,
  shots: 3,
  storyboards: 3,
  clips: 3,
  'final-cut': 4,
}

const store = useStudioStore()
const route = useRoute()
const router = useRouter()
const activeScene = ref(0)
const activeShotId = ref<string | null>(null)
const showCreate = ref(false)
const createBackdropPressed = ref(false)
const showContinuation = ref(false)
const showScriptGuide = ref(false)
const scriptGuideCopied = ref(false)
const showShotNotesDrawer = ref(false)
const showPreview = ref(false)
const previewPlaying = ref(false)
const previewShotIndex = ref(0)
const previewElapsedMs = ref(0)
const projectMenuId = ref<string | null>(null)
const deleteCandidate = ref<ProjectSummary | null>(null)
const deletingProject = ref(false)
const deleteShotImageCandidate = ref(false)
const deleteShotClipCandidate = ref(false)
const deleteFinalCutCandidate = ref(false)
const deleteShotCandidate = ref<Shot | null>(null)
const deletingShot = ref(false)
const addingShotSceneId = ref<string | null>(null)
const uploadInput = ref<HTMLInputElement | null>(null)
const castRefInput = ref<HTMLInputElement | null>(null)
const castRefTargetId = ref<string | null>(null)
const selectedCharacterIds = ref<string[]>([])
const newCharacterName = ref('')
const castingBusy = ref(false)
const activeElementTab = ref<ElementKind>('character')
const videoGenerationMode = ref<'i2v' | 'r2v'>('i2v')
const includeSceneRefInR2V = ref(true)
const mentionField = ref<'video_prompt' | 'image_prompt' | null>(null)
const mentionQuery = ref('')
const mentionStartIndex = ref(-1)
const mentionActiveIdx = ref(0)
const clipUploadInput = ref<HTMLInputElement | null>(null)
const produceDetailOpen = ref(false)
const showPromptEditor = ref(false)
const pendingPromptEditor = ref(false)
const expandedProduceShotIds = ref<string[]>([])
const continuityDrafts = reactive<Record<string, { set_notes: string; lighting_notes: string; wardrobe_notes: string; props_notes: string }>>({})
const continuitySavingIds = ref<string[]>([])
function parseAspectParam(raw: unknown): AspectRatio | null {
  if (typeof raw !== 'string') return null
  const normalized = raw.trim().toLowerCase()
  if (normalized === '16:9' || normalized === '16x9' || normalized === 'landscape') return '16:9'
  if (normalized === '9:16' || normalized === '9x16' || normalized === 'shorts' || normalized === 'portrait') return '9:16'
  return null
}

const mediaAspect = ref<AspectRatio>(parseAspectParam(route.query.aspect ?? route.query.variant) ?? '16:9')
const finalCutAspect = ref<AspectRatio>(parseAspectParam(route.query.variant ?? route.query.aspect) ?? '16:9')
const generateBothAspects = ref(false)
const previewVideo = ref<HTMLVideoElement | null>(null)
const storyboardVideo = ref<HTMLVideoElement | null>(null)
const continuationForm = reactive({ script: '', target_duration_seconds: 30 })
const SCRIPT_GENERATION_PROMPT = `Create a production-ready AI short-film screenplay for Cutline.

Creative brief:
- Duration: [TOTAL SECONDS] seconds
- Aspect ratio: [16:9 or 9:16]
- Genre: [GENRE]
- Visual style: [STYLE]
- Audience and platform: [AUDIENCE / PLATFORM]
- Language and accent: [LANGUAGE / ACCENT]
- Use the story context already supplied. Resolve missing creative choices before writing the final screenplay; do not leave placeholders in the finished script.

Story and flow:
- Build a clear setup, goal or central question, obstacle, escalation, turning point, and payoff appropriate to the genre.
- Every shot must advance action, reveal information, develop emotion, or provide a meaningful reaction. Avoid repeated exposition and filler.
- Make dialogue respond naturally to the previous line or action. Allow breathing room for reactions and pauses.
- Vary framing deliberately while preserving geography, eyelines, screen direction, and cause and effect.
- Give the opening deliberate visual and sound design and the ending a satisfying payoff.

Import and timing rules:
- Deliver one episode only. Number every shot consecutively using the exact heading pattern SHOT 1 - Descriptive title.
- Use shots lasting 2–10 seconds. Vary their lengths to fit the action and performance.
- Every shot needs an explicit Time: MM:SS-MM:SS range using whole seconds. Start at 00:00, leave no gaps or overlaps, and end exactly at the declared duration.
- Use Format: [number] shots. The count must match the actual shot blocks.
- Every shot must include Scene, Location, Time of day, Time, Framing, Cast, Action, Dialogue, Sound, Image prompt, and Video prompt.
- Use plain field labels, not tables, bullets before labels, or bold formatting.
- Metadata does not configure the project: the aspect ratio must also be selected in Cutline.

Dialogue and sound:
- Put authoritative spoken text under Dialogue, with a consistent uppercase character cue and quoted speech. Keep acting directions outside the quoted words.
- Write Dialogue: None. when nobody speaks; also state No spoken dialogue in that shot's Video prompt.
- Prefer one speaker per generated shot when practical. Split complicated exchanges into motivated coverage.
- Cutline estimates English speech at roughly 2.5 words per second plus 0.4 seconds padding, capped at 10 seconds. This is a rough check; manually check long lines and allow time for performance and action.
- As a writing guideline, allow approximately 8–12 spoken words in a 6-second shot or 12–16 in an 8-second shot when acting needs room. Adjust for delivery and language; do not fill every second with words.
- If Video prompt repeats dialogue, copy the Dialogue wording exactly and identify its speaker. Do not introduce extra lines in Video prompt or Sound.
- Sound describes ambience, effects and music direction. Do not duplicate quoted dialogue there.
- Mark off-screen cues explicitly, for example GABE (O.S.) or GABE (ON PHONE). Describe the source of the voice and the visible listener's silent behavior; keep the speaker in Character consistency.
- Voice descriptions guide delivery but do not assign a TTS voice or guarantee lip sync. Speech and generated sound depend on the configured provider. Avoid planning duplicate dialogue from both video generation and a separate voice track.
- Maintain consistent ambience and music direction across connected shots. Continuous music beds, exact sound bridges and separately editable audio tracks require assembly in Cutline; the script does not create them automatically.

Cast, references and props:
- Declare Cast: [visible character names separated by commas] or Cast: None for every shot. Use plain canonical names without @ in Cast and dialogue cues.
- Track who remains physically present through each continuous scene. A new speaker or arrival does not remove existing people. In medium, wide or group coverage, retain established people in Cast and both prompts until a staged exit or justified change of coverage. Specify their positions and silent reactions. Widen a two-shot to group coverage when another person joins.
- For deliberate solo close-ups, list only the visible person in Cast and explicitly place the established listeners off camera. Keep scene presence distinct from visible cast; do not add someone merely because dialogue mentions their name.
- Keep character names identical across the character bible, Cast and dialogue. Verify off-screen speaker/cast routing after import.
- Put each character on one contiguous line under Character consistency:. Put physical identity and age first, wardrobe second, then an explicit Voice: description with accent, register, texture and delivery.
- Define Location, Set, Lighting, Wardrobe and Props in the preamble. These are continuity instructions; reference assets must still exist and be assigned in Cutline.
- Optional uppercase @mentions in Action and prompts identify recurring assets; they do not automatically lock references. Keep tags consistent with asset names.
- For tagged recurring props, use explicit definitions such as @GOLD_PEN is a brushed-gold fountain pen with a black cap. Tagged definitions are filtered to shots mentioning that tag; mention it in each relevant shot. Plain-language prop descriptions are also supported.
- Mention props in Action and prompts only when they are visible or involved in that shot. Do not add unrelated scene props to the foreground action.

Scene and wardrobe continuity:
- Give every physical space its own stable Location name, such as COMPOUND_COURTYARD, COMPOUND_PARLOUR or COMPOUND_KITCHEN. Reuse that exact Location across different scenes in the same space; do not use one compound-wide Location for all its rooms.
- Consecutive shots with identical Scene and Location values are grouped into one production scene. Cutline matches Location to a reusable set reference; each set must have a generated or uploaded reference image.
- Keep permanent architecture and furniture in the reusable set description. Put time-of-day lighting, temporary dressing and blocking in the scene notes; they must respect the canonical set layout.
- Set, Lighting, Wardrobe and Props are shared scene notes. Keep these values stable within a group; different shot-level values can overwrite or accumulate across the group.
- Use a distinct Scene label for a different day, time jump, changed wardrobe, or substantial set/lighting change. Specify the new context with the relevant Set:, Lighting:, Wardrobe: and Props: fields before Action, and repeat essential changes in the prompts.
- Keep canonical wardrobe through each continuous time span. Do not list several alternative outfits in a single scene's wardrobe instructions.
- Track screen positions, eyelines, entrances/exits, which hand holds an object, ownership, and object states such as open/closed or full/empty.
- Describe moment-to-moment prop movement in Action and prompts, not as contradictory changes to shared prop definitions.
- Each next opening frame must follow the previous ending logically, allowing a motivated change of camera angle. Previous-clip frames can be selected manually in Cutline when the composition is compatible.

Image and video direction:
- Image prompt describes one still frame at the START of the shot: visible cast, starting poses, relevant prop positions and states, composition, environment, lighting, style and aspect ratio.
- Video prompt describes what happens AFTER that opening frame: one principal action, manageable supporting movement, performance, camera behavior and a clear end state.
- Do not show an action completed in the opening image and then ask the video to perform it again. The starting image, Action, Framing and Video prompt must agree.
- Use one continuous camera setup per clip. Split new angles, locations, time jumps and complex action phases into separate shots.
- Wide shots emphasize geography and large readable actions; medium shots emphasize interactions/body language; close-ups emphasize a face or a clearly identified object. Match detail to what the framing can show.
- Prefer concrete positive physical directions, such as neutral expression or hands resting on the counter. Use negative constraints sparingly when essential; No spoken dialogue remains useful for silent shots.
- Use clip-relative instructions such as during the first two seconds or at the end of the clip. Keep episode timestamps in Time only.
- Repeat essential identity and style anchors concisely. Avoid contradictory instructions and overloaded prompts.
- Cutline anchors video to its supplied opening image and adds an instruction against on-screen text, subtitles, UI and location tags. Do not rely on generated readable lettering. Titles, captions, messages and end cards require text overlays during editing; arbitrary script fields do not create these automatically.
- Actual clip duration, speech, identity and motion still need review and take selection. Do not assume a requested shot length is an exact provider output.

Output:
- Return only the final screenplay, with no explanations, alternatives or Markdown fences.
- Before returning it, silently verify shot count, duration arithmetic, continuous timing, speech fit, consistent names, scene grouping, prop states, and opening-frame/action agreement.
- Follow this exact structure, replacing all placeholders with concrete content:

[FILM TITLE]

Genre: [genre]
Duration: [number] seconds
Format: [number] shots
Aspect ratio: [ratio]
Location: [primary location]
Set: [architecture, furnishings, background and spatial layout]
Lighting: [source, direction, quality and color]
Wardrobe: [one current outfit per character]
Props: [recurring prop definitions]

Character consistency:
[CHARACTER_NAME] — [Physical identity and age.] [Default wardrobe.] Voice: [accent, register, texture and delivery].

SHOT 1 - [SHOT TITLE]

Scene: [COMPACT SCENE LABEL]
Location: [SHOT LOCATION]
Time of day: [Morning / Afternoon / Evening / Night]
Time: 00:00-00:06
Framing: [shot size, angle and relevant screen positions]
Cast: [plain character names separated by commas, or None]

Action:
[Concrete visible action and meaningful change.]

Dialogue:
[CHARACTER_NAME]:
"[Exact spoken words.]"

Sound:
[Ambience, effects and music direction.]

Image prompt:
[One opening frame: visible cast, starting poses, relevant props and their starting states, composition, environment, lighting, visual style and aspect ratio.]

Video prompt:
[Action from that opening frame, performance, camera behavior, exact dialogue if applicable, silent listeners and clear end state.]

Continue with consecutive SHOT headings and the same fields until the declared duration is reached. The sample six-second range is illustrative; choose each shot's timing to fit the story. For a silent shot replace the entire Dialogue block with Dialogue: None. For a genuinely empty shot use Cast: None. Add changed scene-context fields before Action only when needed, using a distinct Scene label for the new context.`
const shotDraft = reactive({ framing: 'Medium', duration_ms: 5000, image_prompt: '', video_prompt: '', title_card_text: '' })

const form = reactive<ProjectInput>({
  title: 'Untitled short',
  aspect_ratio: '16:9',
  target_duration_seconds: 0,
  text_provider: 'mock',
  script: `INT. KITCHEN - NIGHT
Rain presses against the black kitchen windows.

MARA
We should leave before he comes back.

ELIAS
And go where?

EXT. DRIVEWAY - NIGHT
They run toward an idling car through the hard rain.`,
})

const scenes = computed(() => store.currentProject?.current_version?.scenes ?? [])
const selectedScene = computed(() => scenes.value[activeScene.value] ?? scenes.value[0])
const suggestions = computed<DisplaySuggestion[]>(() => {
  const source = store.currentProject?.current_version?.suggestions
  if (!source) return []
  return source.map((item: Suggestion) => ({
    id: item.id,
    category: item.category,
    severity: item.severity,
    title: item.title,
    reason: item.reason,
    change: typeof item.after_value === 'string' ? item.after_value : JSON.stringify(item.after_value),
    status: item.status,
  }))
})
const pendingCount = computed(() => suggestions.value.filter((item) => item.status === 'pending').length)
const advisoryCategories = new Set(['continuity', 'craft'])
const isAdvisory = (item: DisplaySuggestion) => advisoryCategories.has(item.category)
const scriptReadiness = computed(() => {
  const open = suggestions.value.filter((item) => item.status === 'pending')
  const blocking = open.filter((item) => item.severity === 'blocking').length
  const warnings = open.length - blocking
  const score = Math.max(0, 100 - blocking * 25 - warnings * 5)
  const label = blocking ? 'Blocked' : warnings ? 'Needs polish' : 'Ready'
  const tone = blocking ? 'blocked' : warnings ? 'polish' : 'ready'
  return { score, label, tone, blocking, warnings }
})
const projectTitle = computed(() => store.currentProject?.title ?? 'New project')
const projectDuration = computed(() => {
  const end = Math.max(...scenes.value.map((scene) => scene.end_ms ?? 0), 0)
  return formatTime(end)
})
const projectTargetDuration = computed(() => {
  const target = store.currentProject?.target_duration_seconds ?? 0
  return target > 0 ? formatTime(target * 1000) : 'automatic'
})
const activeProvider = computed(() => {
  const id = store.currentProject?.text_provider ?? 'ollama'
  return store.providers.find((provider) => provider.id === id)
})
const isScriptApproved = computed(() => store.currentProject?.status === 'shot_plan')
const activeStage = computed<ProductionStage>(() => {
  const stage = route.params.stage
  return typeof stage === 'string' && stage in stageTitles ? stage as ProductionStage : 'script'
})
const stageTitle = computed(() => stageTitles[activeStage.value])
const currentProgressStep = computed(() => stageSteps[activeStage.value])
const allShots = computed(() => scenes.value.flatMap((scene) => scene.shots))
const selectedShot = computed(() => allShots.value.find((shot) => shot.id === activeShotId.value) ?? allShots.value[0])
const previousShotId = computed(() => {
  if (!selectedShot.value) return undefined
  const shots = allShots.value
  const idx = shots.findIndex((s) => s.id === selectedShot.value!.id)
  return idx > 0 ? shots[idx - 1]?.id : undefined
})
const previousShot = computed(() => allShots.value.find((shot) => shot.id === previousShotId.value))
const canReferenceEarlier = computed(() => {
  if (previousShotId.value) return true
  const curEp = store.currentEpisode
  if (!curEp) return false
  return store.episodes.some(
    (ep) => ep.episode_number < curEp.episode_number && (ep.shot_count ?? 1) > 0,
  )
})
const skipDefaultImageReference = ref(false)
const lockSceneBlocking = ref(false)
const focalCharacterId = ref<string | null>(null)
watch(() => selectedShot.value?.id, () => {
  skipDefaultImageReference.value = false
  lockSceneBlocking.value = false
  focalCharacterId.value = null
})
watch(lockSceneBlocking, (locked) => {
  if (!locked) {
    focalCharacterId.value = null
  }
})
function resolveActiveShotMedia(
  shot: Shot | null | undefined,
  mediaType: 'image' | 'clip',
  aspect: AspectRatio,
  allowFallback = true,
) {
  if (!shot) return null
  const variant = shot.media_variants.find((item) => item.media_type === mediaType && item.aspect_ratio === aspect)
  const legacy = store.currentProject?.aspect_ratio === aspect ? (mediaType === 'clip' ? shot.clip : shot.image) : null
  const hasSlot = Boolean(variant || legacy)
  const selectedId = mediaType === 'clip' ? shot.selected_clip_take_id : shot.selected_image_take_id
  const activeTake = shot.takes?.find(
    (t) =>
      t.media_type === mediaType &&
      t.aspect_ratio === aspect &&
      (t.selected || (selectedId && t.id === selectedId)),
  )
  if (activeTake || hasSlot) {
    const media = activeTake ?? variant ?? legacy
    if (media) {
      const isAdapted = variant?.source === 'adapted' || (media as any).source === 'adapted'
      return {
        ...media,
        isAdapted,
        adaptedFrom: isAdapted ? (aspect === '16:9' ? '9:16' : '16:9') : undefined,
      }
    }
  }

  if (!allowFallback) return null

  // If no native slot/take exists for this aspect ratio, adapt from the other aspect ratio
  const otherAspect: AspectRatio = aspect === '16:9' ? '9:16' : '16:9'
  const fallbackVariant = shot.media_variants.find((item) => item.media_type === mediaType && item.aspect_ratio === otherAspect)
  const fallbackLegacy = (mediaType === 'clip' ? shot.clip : shot.image)
  const fallbackTake = shot.takes?.find(
    (t) =>
      t.media_type === mediaType &&
      t.aspect_ratio === otherAspect &&
      (t.selected || (selectedId && t.id === selectedId)),
  ) ?? shot.takes?.filter((t) => t.media_type === mediaType && t.aspect_ratio === otherAspect).pop()

  const fallback = fallbackTake ?? fallbackVariant ?? fallbackLegacy ?? null
  if (fallback && (fallback as any).url) {
    return {
      ...fallback,
      isAdapted: true,
      adaptedFrom: otherAspect,
    }
  }
  return null
}
const selectedAspectImage = computed(() => resolveActiveShotMedia(selectedShot.value, 'image', mediaAspect.value))
const selectedAspectReference = computed(() => {
  const shot = selectedShot.value
  if (!shot) return null
  return shot.image_references?.find((item) => item.aspect_ratio === mediaAspect.value)
    ?? shot.continuity_frames?.find((item) => item.aspect_ratio === mediaAspect.value)
    ?? null
})
const selectedAspectClip = computed(() => resolveActiveShotMedia(selectedShot.value, 'clip', mediaAspect.value))
const hasNativeAspectClip = computed(() => {
  const clip = resolveActiveShotMedia(selectedShot.value, 'clip', mediaAspect.value, false)
  return Boolean(clip && !(clip as any).isAdapted)
})
const hasNativeAspectImage = computed(() => {
  const img = resolveActiveShotMedia(selectedShot.value, 'image', mediaAspect.value, false)
  return Boolean(img && !(img as any).isAdapted)
})
const showStillImageInstead = ref(false)
watch(
  [() => selectedShot.value?.id, () => mediaAspect.value],
  () => {
    showStillImageInstead.value =
      !hasNativeAspectClip.value && (hasNativeAspectImage.value || Boolean(selectedAspectReference.value))
  },
  { immediate: true },
)
watch(
  () => hasNativeAspectClip.value,
  (hasClip) => {
    if (hasClip) {
      showStillImageInstead.value = false
    }
  },
)
const shouldDisplayClip = computed(() => {
  if (!selectedAspectClip.value) return false
  if (showStillImageInstead.value) return false
  return true
})
const selectedAspectClipSlot = computed(() => {
  const shot = selectedShot.value
  if (!shot) return null
  const variant = shot.media_variants.find((item) => item.media_type === 'clip' && item.aspect_ratio === mediaAspect.value)
  const legacy = store.currentProject?.aspect_ratio === mediaAspect.value ? shot.clip : null
  const otherAspect: AspectRatio = mediaAspect.value === '16:9' ? '9:16' : '16:9'
  const fallbackVariant = shot.media_variants.find((item) => item.media_type === 'clip' && item.aspect_ratio === otherAspect)
  return variant ?? legacy ?? shot.clip ?? fallbackVariant ?? null
})
const retouchingClipColor = ref(false)
const currentClipBoostStrength = computed(() => Number(selectedAspectClipSlot.value?.color_boost_strength ?? 0))
const currentClipBoostPercent = computed(() => Math.round(currentClipBoostStrength.value * 100))
const canUndoClipColor = computed(() => Boolean(selectedAspectClipSlot.value?.can_undo_color))
const canRedoClipColor = computed(() => Boolean(selectedAspectClipSlot.value?.can_redo_color))
const clipBoostBadgeLabel = computed(() => {
  const pct = currentClipBoostPercent.value
  if (pct === 0) return ''
  return pct > 0 ? `+${pct}%` : `${pct}%`
})
const clipBoostButtonTitle = computed(() => {
  if (currentClipBoostPercent.value === 100) {
    return 'Standard +100% video color boost is active — use - / + to step by 25%, or Reset to restore 0%'
  }
  if (currentClipBoostPercent.value === 0) {
    return 'Apply standard +100% video color & contrast boost preset'
  }
  return `Return to standard +100% video color boost preset (currently ${clipBoostBadgeLabel.value})`
})
async function runClipColorAction(action?: ReferenceColorRetouchAction, strength?: number) {
  if (!selectedShot.value || !selectedAspectClip.value || retouchingClipColor.value) return
  retouchingClipColor.value = true
  try {
    await store.retouchShotClipColor(
      selectedShot.value.id,
      mediaAspect.value,
      action ? { action, strength } : undefined,
    )
  } finally {
    retouchingClipColor.value = false
  }
}
const otherAspect = computed<AspectRatio>(() => mediaAspect.value === '9:16' ? '16:9' : '9:16')
const otherAspectImage = computed(() => resolveActiveShotMedia(selectedShot.value, 'image', otherAspect.value))
const otherAspectClip = computed(() => resolveActiveShotMedia(selectedShot.value, 'clip', otherAspect.value))
const otherAspectMedia = computed(() => otherAspectImage.value || otherAspectClip.value)
const canExpandFromOtherAspect = computed(() => !selectedAspectImage.value && !selectedAspectClip.value && !selectedAspectReference.value && Boolean(otherAspectMedia.value))
const selectedShotScene = computed(() => scenes.value.find((scene) => scene.shots.some((shot) => shot.id === selectedShot.value?.id)))
function cleanConversationUrl(url: string | null | undefined): string | null {
  if (!url) return null
  return url.replace(/gemini\.google\.com\/app\/c_([a-zA-Z0-9]+)/i, 'gemini.google.com/app/$1')
}

const selectedShotConversationUrl = computed(() => {
  const shot = selectedShot.value
  if (!shot) return null

  if (shot.conversation_url) return cleanConversationUrl(shot.conversation_url)

  const matchingTakes = shot.takes?.filter((take) => take.media_type === "image" && take.aspect_ratio === mediaAspect.value) || []
  const activeTake = matchingTakes.find((take) => take.id === shot.selected_image_take_id)
    || matchingTakes[matchingTakes.length - 1]
    || shot.takes?.find((take) => take.id === shot.selected_image_take_id)
  const takeUrl = activeTake?.conversation_url || (activeTake?.settings as Record<string, unknown> | undefined)?.conversation_url as string | undefined
  if (takeUrl) return cleanConversationUrl(takeUrl)

  const inFlightJob = store.imageJobs[shot.id]
  if (inFlightJob?.conversation_url) return cleanConversationUrl(inFlightJob.conversation_url)

  const clipJob = store.clipJobs[shot.id]
  if (clipJob?.conversation_url) return cleanConversationUrl(clipJob.conversation_url)

  return null
})
const deleteChatUrl = ref<string | null>(null)
const deletingChat = ref(false)
const deleteChatError = ref('')
const canDeleteChat = computed(() => Boolean(selectedShotConversationUrl.value))
const deleteChatProvider = computed(() => {
  const url = deleteChatUrl.value || ''
  if (/gemini\.google\.com/i.test(url)) return 'Gemini'
  if (/chatgpt\.com/i.test(url)) return 'ChatGPT'
  return 'your AI provider'
})
function beginDeleteChat() {
  deleteChatError.value = ''
  deleteChatUrl.value = selectedShotConversationUrl.value
}
async function confirmDeleteChat() {
  const projectId = store.currentProject?.id
  if (!projectId || !deleteChatUrl.value || deletingChat.value) return
  deletingChat.value = true
  deleteChatError.value = ''
  try {
    await api.deleteConversation(projectId, deleteChatUrl.value)
    deleteChatUrl.value = null
    await store.loadProject(projectId, { episodeId: store.currentEpisodeId || undefined })
  } catch (error) {
    deleteChatError.value = error instanceof Error ? error.message : 'Could not delete chat.'
  } finally {
    deletingChat.value = false
  }
}
const selectedAudioTake = computed(() => selectedShot.value?.takes?.find((take) => take.id === selectedShot.value?.selected_audio_take_id) ?? null)
const selectedAudioUrl = computed(() => selectedAudioTake.value?.url ?? (selectedShot.value?.dialogue_audio_filename ? `/media/${encodeURIComponent(selectedShot.value.dialogue_audio_filename)}` : null))
const previewShot = computed(() => allShots.value[previewShotIndex.value])
const nextPreviewShot = computed(() => allShots.value[previewShotIndex.value + 1] ?? null)
const previewAspect = computed<AspectRatio>(() => activeStage.value === 'final-cut' ? finalCutAspect.value : mediaAspect.value)
function previewMedia(shot: Shot | null | undefined, mediaType: 'image' | 'clip') {
  if (!shot) return null
  const exact = resolveActiveShotMedia(shot, mediaType, previewAspect.value)
  if (exact) return exact
  const legacy = mediaType === 'clip' ? shot.clip : shot.image
  if (legacy) return legacy
  return shot.media_variants.find((item) => item.media_type === mediaType) ?? null
}
const previewClip = computed(() => previewMedia(previewShot.value, 'clip'))
const previewImage = computed(() => previewMedia(previewShot.value, 'image'))
const nextPreviewClip = computed(() => previewMedia(nextPreviewShot.value, 'clip'))
const nextPreviewImage = computed(() => previewMedia(nextPreviewShot.value, 'image'))
const previewTotalMs = computed(() => allShots.value.reduce((total, shot) => total + shot.duration_ms, 0))
const previewPriorMs = computed(() => allShots.value.slice(0, previewShotIndex.value).reduce((total, shot) => total + shot.duration_ms, 0))
const previewCurrentMs = computed(() => Math.min(previewTotalMs.value, previewPriorMs.value + previewElapsedMs.value))
const previewProgress = computed(() => previewTotalMs.value ? (previewCurrentMs.value / previewTotalMs.value) * 100 : 0)
const selectedShotLabel = computed(() => {
  if (!selectedShot.value || !selectedShotScene.value) return 'No shot selected'
  return `Scene ${String(selectedShotScene.value.order_index + 1).padStart(2, '0')} · Shot ${String(selectedShot.value.order_index + 1).padStart(2, '0')}`
})
const clipCount = computed(() => allShots.value.filter((shot) => shotMedia(shot, 'clip')).length)
const mediaReadyCount = computed(() => allShots.value.filter((shot) => shotMedia(shot, 'clip') || shotMedia(shot, 'image')).length)
const finalCutReady = computed(() => allShots.value.length > 0 && mediaReadyCount.value === allShots.value.length)
const imageGenerationAvailable = computed(() => store.imageProviders.some((provider) => provider.available))
const imageProviderOrder = computed(() => store.imageProviders.map((provider) => provider.label).join(' → ') || 'No image provider configured')
const selectedImageJob = computed(() => selectedShot.value ? store.imageJobs[selectedShot.value.id] ?? null : null)
const isProcessingActiveClip = computed(() => {
  if (!selectedShot.value) return false
  return store.isProcessingClip(selectedShot.value.id, mediaAspect.value)
})
const isProcessingActiveImage = computed(() => {
  if (!selectedShot.value) return false
  return store.isProcessingShot(selectedShot.value.id, mediaAspect.value)
})
const isProcessingActiveStoryboard = computed(() => {
  return isProcessingActiveClip.value || isProcessingActiveImage.value
})
const activeStoryboardProcessingMessage = computed(() => {
  if (!selectedShot.value) return 'Processing…'
  if (isProcessingActiveClip.value) {
    return store.clipJobs[selectedShot.value.id]?.message || 'Generating video'
  }
  return selectedImageJob.value?.message || 'Rendering storyboard'
})
function isProcessingAspect(shotId: string, aspect: AspectRatio) {
  return store.isProcessingShot(shotId, aspect) || store.isProcessingClip(shotId, aspect)
}
const castCharacters = computed(() => store.currentProject?.characters ?? [])
const characterElements = computed(() =>
  castCharacters.value.filter((item) => (item.kind || 'character') === 'character'),
)
const sceneElements = computed(() =>
  castCharacters.value.filter((item) => item.kind === 'scene'),
)
const propElements = computed(() =>
  castCharacters.value.filter((item) => item.kind === 'prop'),
)
const visibleLibraryElements = computed(() =>
  castCharacters.value.filter((item) => (item.kind || 'character') === activeElementTab.value),
)
function locationKey(location: string): string {
  return location.trim()
    .replace(/^(?:INT\.?\/EXT\.?|INT\.?|EXT\.?)\s+/i, '')
    .replace(/\s*[-–—]\s*(?:DAY|NIGHT|MORNING|AFTERNOON|EVENING|DAWN|DUSK|CONTINUOUS|LATER|SAME(?:\s+TIME)?).*$/i, '')
    .toUpperCase().replace(/[^A-Z0-9]+/g, ' ').trim()
}
function sceneSet(scene: Scene): Character | undefined {
  const matches = sceneElements.value.filter(element => locationKey(element.name) === locationKey(scene.location))
  return matches.find(element => element.references.length) ?? matches[0]
}
async function assignSceneSet(scene: Scene, event: Event) {
  const name = (event.target as HTMLSelectElement).value
  const element = sceneElements.value.find(item => item.name === name)
  if (!element || isContinuitySaving(scene.id)) return
  continuitySavingIds.value = [...continuitySavingIds.value, scene.id]
  try {
    await store.updateScene(scene.id, {
      location: element.name,
      ...(locationKey(scene.location) !== locationKey(element.name) ? { set_notes: '' } : {}),
    })
  } catch {
    // Store surfaces the error.
  } finally {
    continuitySavingIds.value = continuitySavingIds.value.filter(id => id !== scene.id)
  }
}
function elementTag(element: Character): string {
  const clean = (element.name || 'ELEMENT')
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return clean.length > 40 ? clean.slice(0, 40) : clean
}
function elementHas3DView(element: Character): boolean {
  return element.references.some((ref) => ref.view_type === '3d_view')
}
function elementPrimaryReference(element: Character) {
  const threeD = element.references.filter((ref) => ref.view_type === '3d_view')
  if (threeD.length) return threeD[threeD.length - 1]!
  return element.references[element.references.length - 1] ?? null
}
const selectedCast = computed(() =>
  castCharacters.value.filter((character) => selectedCharacterIds.value.includes(character.id)),
)
const castChipsExpanded = ref(false)
const DEFAULT_VISIBLE_CHIP_COUNT = 8

const sortedCastCharacters = computed(() => {
  const selectedSet = new Set(selectedCharacterIds.value)
  return [...castCharacters.value].sort((a, b) => {
    const aSel = selectedSet.has(a.id) ? 1 : 0
    const bSel = selectedSet.has(b.id) ? 1 : 0
    if (aSel !== bSel) return bSel - aSel

    const kindOrder: Record<string, number> = { character: 0, scene: 1, prop: 2 }
    const aKind = kindOrder[a.kind || 'character'] ?? 3
    const bKind = kindOrder[b.kind || 'character'] ?? 3
    if (aKind !== bKind) return aKind - bKind

    return a.name.localeCompare(b.name)
  })
})

const visibleCastCharacters = computed(() => {
  if (castChipsExpanded.value) return sortedCastCharacters.value
  const selectedCount = sortedCastCharacters.value.filter((c) =>
    selectedCharacterIds.value.includes(c.id)
  ).length
  const limit = Math.max(DEFAULT_VISIBLE_CHIP_COUNT, selectedCount)
  return sortedCastCharacters.value.slice(0, limit)
})

const hasHiddenCastChips = computed(() => {
  return sortedCastCharacters.value.length > visibleCastCharacters.value.length
})

const hiddenCastChipCount = computed(() => {
  return sortedCastCharacters.value.length - visibleCastCharacters.value.length
})
const detectedPromptMentionElements = computed(() => {
  const text = `${shotDraft.video_prompt || ''} ${shotDraft.image_prompt || ''}`.toUpperCase()
  if (!text.includes('@')) return []
  return castCharacters.value.filter((el) => {
    const tag = elementTag(el)
    const rawName = el.name.trim().toUpperCase()
    return (tag && text.includes(`@${tag}`)) || (rawName && text.includes(`@${rawName}`))
  })
})
const autoMatchedSceneElement = computed(() => {
  if (!includeSceneRefInR2V.value || !selectedShotScene.value) return null
  return sceneSet(selectedShotScene.value) ?? null
})
const r2vAttachedElements = computed(() => {
  const seen = new Set<string>()
  const result: Character[] = []
  const pushUnique = (el: Character | null | undefined) => {
    if (!el || seen.has(el.id) || result.length >= 7) return
    seen.add(el.id)
    result.push(el)
  }
  for (const el of selectedCast.value) pushUnique(el)
  for (const el of detectedPromptMentionElements.value) pushUnique(el)
  if (autoMatchedSceneElement.value) pushUnique(autoMatchedSceneElement.value)
  return result
})
const r2vReadyReferenceCount = computed(() => {
  const elementRefs = r2vAttachedElements.value.filter((el) => el.references.length > 0).length
  const contRef = selectedAspectReference.value ? 1 : 0
  return Math.min(7, elementRefs + contRef)
})
const canGenerateClipNow = computed(() => {
  if (videoGenerationMode.value === 'r2v') {
    return r2vReadyReferenceCount.value > 0 || Boolean(selectedAspectImage.value)
  }
  return Boolean(selectedAspectImage.value || selectedAspectReference.value)
})
const mentionSuggestions = computed(() => {
  if (!mentionField.value) return []
  const q = mentionQuery.value.trim().toLowerCase()
  return castCharacters.value.filter((el) => {
    if (!q) return true
    const tag = elementTag(el).toLowerCase()
    const name = el.name.toLowerCase()
    const kind = (el.kind || 'character').toLowerCase()
    return tag.includes(q) || name.includes(q) || kind.includes(q)
  }).slice(0, 8)
})
function handlePromptTextareaInput(field: 'video_prompt' | 'image_prompt', event: Event) {
  const target = event.target as HTMLTextAreaElement
  const cursor = target.selectionStart ?? target.value.length
  const before = target.value.slice(0, cursor)
  const match = before.match(/(?:^|\s)@([A-Za-z0-9_-]*)$/)
  if (match) {
    mentionField.value = field
    mentionQuery.value = match[1] ?? ''
    mentionStartIndex.value = cursor - (match[1]?.length ?? 0) - 1
    mentionActiveIdx.value = 0
    const typedTag = (match[1] ?? '').toUpperCase()
    if (typedTag) {
      const exactEl = castCharacters.value.find(
        (el) => elementTag(el) === typedTag || el.name.trim().toUpperCase() === typedTag,
      )
      if (exactEl && !selectedCharacterIds.value.includes(exactEl.id)) {
        selectedCharacterIds.value = [...selectedCharacterIds.value, exactEl.id]
      }
    }
  } else if (mentionField.value === field) {
    mentionField.value = null
  }
}
function handlePromptTextareaKeydown(field: 'video_prompt' | 'image_prompt', event: KeyboardEvent) {
  if (mentionField.value !== field || !mentionSuggestions.value.length) return
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    mentionActiveIdx.value = (mentionActiveIdx.value + 1) % mentionSuggestions.value.length
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    mentionActiveIdx.value = (mentionActiveIdx.value - 1 + mentionSuggestions.value.length) % mentionSuggestions.value.length
  } else if (event.key === 'Enter' || event.key === 'Tab') {
    event.preventDefault()
    const picked = mentionSuggestions.value[mentionActiveIdx.value]
    if (picked) insertElementMention(picked, field)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    mentionField.value = null
  }
}
function insertElementMention(element: Character, field: 'video_prompt' | 'image_prompt' = mentionField.value || 'video_prompt') {
  const tag = `@${elementTag(element)}`
  const current = shotDraft[field] || ''
  if (mentionField.value === field && mentionStartIndex.value >= 0) {
    const afterStart = current.slice(mentionStartIndex.value)
    const replacedAfter = afterStart.replace(/^@[A-Za-z0-9_-]*/, `${tag} `)
    shotDraft[field] = current.slice(0, mentionStartIndex.value) + replacedAfter
  } else if (!current.toUpperCase().includes(tag)) {
    shotDraft[field] = current.trim() ? `${current.trim()} ${tag} ` : `${tag} `
  }
  if (!selectedCharacterIds.value.includes(element.id)) {
    selectedCharacterIds.value = [...selectedCharacterIds.value, element.id]
  }
  mentionField.value = null
  mentionQuery.value = ''
  mentionStartIndex.value = -1
}
const managingCharacterId = ref<string | null>(null)
const managingCharacter = computed(() =>
  castCharacters.value.find((character) => character.id === managingCharacterId.value) ?? null,
)
const characterNotesDraft = ref('')
const savingCharacterNotes = ref(false)
const characterNotesSaved = ref(false)

const characterNotesChanged = computed(() => {
  if (!managingCharacter.value) return false
  return characterNotesDraft.value.trim() !== (managingCharacter.value.notes || '').trim()
})

function openCastManager(characterId: string) {
  managingCharacterId.value = characterId
  const char = castCharacters.value.find((c) => c.id === characterId)
  characterNotesDraft.value = char?.notes || ''
  characterNotesSaved.value = false
}

async function saveCharacterNotes() {
  if (!managingCharacter.value || !characterNotesChanged.value || savingCharacterNotes.value) return
  savingCharacterNotes.value = true
  try {
    await store.updateCharacter(managingCharacter.value.id, { notes: characterNotesDraft.value })
    characterNotesSaved.value = true
    setTimeout(() => { characterNotesSaved.value = false }, 2000)
  } finally {
    savingCharacterNotes.value = false
  }
}

async function handleGenerateFaceFromManager() {
  if (!managingCharacter.value) return
  if (characterNotesChanged.value) {
    await saveCharacterNotes()
  }
  await generateCastFace(managingCharacter.value)
}

async function handleDeleteCharacterFromManager(characterId: string) {
  if (!confirm('Are you sure you want to remove this character from the cast?')) return
  await removeCastCharacter(characterId)
  managingCharacterId.value = null
}
const selectedShotVideoPrompt = computed(() => {
  if (!selectedShot.value) return ''
  try {
    return buildShotVideoPrompt(selectedShot.value, selectedShotScene.value, selectedCast.value, mediaAspect.value)
  } catch {
    return selectedShot.value.video_prompt || ''
  }
})
const copiedShotPrompt = ref(false)
const includeCastRefsInVideo = ref(false)
const userModifiedCharacterIds = reactive<Record<string, string[]>>({})
const userCastRefOverrides = reactive<Record<string, boolean>>({})

function handleToggleCastRefs(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  includeCastRefsInVideo.value = checked
  if (selectedShot.value) {
    userCastRefOverrides[selectedShot.value.id] = checked
    try {
      localStorage.setItem(`cutline:cast_refs:${selectedShot.value.id}`, String(checked))
    } catch {}
  }
}

function imageJobForShot(shotId: string) {
  return store.imageJobs[shotId] ?? null
}

function defaultCharacterIdsForShot(shot: Shot | null | undefined): string[] {
  if (!shot) return []
  const valid = new Set(castCharacters.value.map((character) => character.id))
  if (shot.id in userModifiedCharacterIds) {
    return (userModifiedCharacterIds[shot.id] ?? []).filter((id) => valid.has(id))
  }
  const persisted = (shot.character_ids || []).filter((id) => valid.has(id))
  if (persisted.length) return persisted
  if (Array.isArray(shot.character_ids) && (shot as any)._charactersExplicitlyEmpty) return []

  const dialogueNames = new Set(
    shot.dialogue.map((line) => line.character.trim().toLowerCase()).filter(Boolean),
  )
  const prompt = `${shot.framing} ${shot.image_prompt} ${shot.action} ${shot.video_prompt}`.toLowerCase()
  return castCharacters.value
    .filter((character) => {
      const name = character.name.trim().toLowerCase()
      const tag = `@${elementTag(character).toLowerCase()}`
      if (!name) return false
      return dialogueNames.has(name) || prompt.includes(name) || prompt.includes(tag)
    })
    .map((character) => character.id)
}

function syncSelectedCharactersForShot(shot: Shot | null | undefined) {
  if (!shot) return
  if (shot.id in userModifiedCharacterIds) {
    selectedCharacterIds.value = userModifiedCharacterIds[shot.id] ?? []
    return
  }
  const defaults = defaultCharacterIdsForShot(shot)
  const valid = new Set(castCharacters.value.map((character) => character.id))
  selectedCharacterIds.value = defaults.length
    ? defaults
    : selectedCharacterIds.value.filter((id) => valid.has(id))
}

async function toggleCharacterForGeneration(characterId: string) {
  if (!selectedShot.value) return
  const shotId = selectedShot.value.id
  const next = selectedCharacterIds.value.includes(characterId)
    ? selectedCharacterIds.value.filter((id) => id !== characterId)
    : [...selectedCharacterIds.value, characterId]
  selectedCharacterIds.value = next
  userModifiedCharacterIds[shotId] = next
  if (selectedShot.value) {
    selectedShot.value.character_ids = next
    if (next.length === 0) {
      (selectedShot.value as any)._charactersExplicitlyEmpty = true
    }
  }
  try {
    await store.updateShot(shotId, {
      ...shotDraft,
      character_ids: next,
    })
  } catch (err) {
    console.error('Failed to persist shot characters:', err)
  }
}

async function copyShotVideoPrompt() {
  if (!selectedShotVideoPrompt.value) return
  await navigator.clipboard.writeText(selectedShotVideoPrompt.value)
  copiedShotPrompt.value = true
  window.setTimeout(() => { copiedShotPrompt.value = false }, 1800)
}

watch(() => route.params.projectId, async (projectId) => {
  if (typeof projectId !== 'string' || store.currentProject?.id === projectId) return
  try {
    const queryEpisode = typeof route.query.episode === 'string' ? route.query.episode : undefined
    const queryCompilation = typeof route.query.compilation === 'string' ? route.query.compilation : undefined
    await store.loadProject(projectId, { resumeJobs: true, episodeId: queryEpisode })
    if (queryCompilation && activeStage.value === 'final-cut' && store.compilations.some((c) => c.id === queryCompilation)) {
      store.selectCompilation(queryCompilation)
    } else {
      store.selectCompilation(null)
    }
  } catch {
    await router.replace('/')
  }
})
function buildSyncedQuery(overrides: Record<string, string | undefined> = {}) {
  const next: Record<string, string> = {}
  for (const [key, val] of Object.entries(route.query)) {
    if (typeof val === 'string') next[key] = val
  }
  if (next.scene && !scenes.value.some((scene) => scene.id === next.scene)) {
    delete next.scene
  }
  if (store.currentEpisodeId) {
    next.episode = store.currentEpisodeId
  }
  if (store.currentProject) {
    next.aspect = mediaAspect.value
    if (next.variant !== undefined || activeStage.value === 'final-cut') {
      next.variant = mediaAspect.value === '9:16' ? 'shorts' : 'landscape'
    }
    if (activeStage.value === 'final-cut' && store.activeCompilationId) {
      next.compilation = store.activeCompilationId
    } else {
      delete next.compilation
    }
  }
  for (const [key, val] of Object.entries(overrides)) {
    if (val === undefined) {
      delete next[key]
    } else {
      next[key] = val
    }
  }
  return next
}

watch(() => store.currentEpisodeId, (newEpId) => {
  if (newEpId && route.query.episode !== newEpId && store.currentProject) {
    void router.replace({
      query: buildSyncedQuery({ episode: newEpId }),
    })
  }
})
watch(() => route.query.episode, async (newQueryEp) => {
  if (typeof newQueryEp === 'string' && newQueryEp && store.currentProject) {
    let targetEpId = newQueryEp
    const matchingEp = store.episodes.find(
      (e) => e.id === newQueryEp || String(e.episode_number) === newQueryEp
    )
    if (matchingEp) targetEpId = matchingEp.id
    if (targetEpId !== store.currentEpisodeId) {
      await store.selectEpisode(targetEpId)
    }
  }
})
watch(() => store.activeCompilationId, (newCompId) => {
  if (store.currentProject && activeStage.value === 'final-cut') {
    if (newCompId && route.query.compilation !== newCompId) {
      void router.replace({
        query: buildSyncedQuery({ compilation: newCompId }),
      })
    } else if (!newCompId && route.query.compilation) {
      void router.replace({
        query: buildSyncedQuery({ compilation: undefined }),
      })
    }
  }
})
watch(() => route.query.compilation, (newQueryComp) => {
  if (typeof newQueryComp === 'string' && newQueryComp && activeStage.value === 'final-cut' && store.currentProject) {
    if (newQueryComp !== store.activeCompilationId) {
      const exists = store.compilations.some((c) => c.id === newQueryComp)
      if (exists) {
        store.selectCompilation(newQueryComp)
      }
    }
  } else if (!newQueryComp && store.activeCompilationId && activeStage.value === 'final-cut') {
    store.selectCompilation(null)
  }
})
watch(activeStage, (newStage) => {
  if (newStage !== 'final-cut' && store.activeCompilationId) {
    store.selectCompilation(null)
  }
})
watch(() => store.currentProject?.id, (newId, oldId) => {
  if (!newId) return
  const urlAspect = parseAspectParam(route.query.aspect ?? route.query.variant)
  const projectDefault = (store.currentProject?.aspect_ratio as AspectRatio | undefined) ?? '16:9'
  const switchedProject = Boolean(oldId && newId !== oldId)
  mediaAspect.value = (!switchedProject && urlAspect) ? urlAspect : projectDefault
  finalCutAspect.value = (!switchedProject && parseAspectParam(route.query.variant ?? route.query.aspect)) || mediaAspect.value
  if (route.query.aspect !== mediaAspect.value) {
    void router.replace({
      query: buildSyncedQuery({ aspect: mediaAspect.value }),
    })
  }
  if (!route.params.shotId) {
    produceDetailOpen.value = false
    showPromptEditor.value = false
  }
}, { immediate: true })
watch(mediaAspect, (newAspect) => {
  finalCutAspect.value = newAspect
  const nextVariant: OutputVariant = newAspect === '9:16' ? 'shorts' : 'landscape'
  const shouldSyncVariant = route.query.variant !== undefined || activeStage.value === 'final-cut'
  if (
    store.currentProject &&
    (route.query.aspect !== newAspect || (shouldSyncVariant && route.query.variant !== nextVariant))
  ) {
    void router.replace({
      query: buildSyncedQuery({
        aspect: newAspect,
        ...(shouldSyncVariant ? { variant: nextVariant } : {}),
      }),
    })
  }
})
watch(() => route.query.aspect, (newQueryAspect) => {
  const parsed = parseAspectParam(newQueryAspect)
  if (parsed && parsed !== mediaAspect.value) {
    mediaAspect.value = parsed
  }
})
watch(
  () => scenes.value.map((scene) => `${scene.id}:${scene.shots[0]?.id || ''}:${scene.set_notes || ''}:${scene.lighting_notes || ''}:${scene.wardrobe_notes || ''}:${scene.props_notes || ''}`).join('|'),
  () => {
    ensureFirstShotsExpanded()
    syncContinuityDrafts()
  },
  { immediate: true },
)
watch(
  () => [selectedShot.value?.id, selectedShot.value?.takes?.length] as const,
  ([shotId]) => {
    if (!shotId) return
    if (shotId in userCastRefOverrides) {
      includeCastRefsInVideo.value = Boolean(userCastRefOverrides[shotId])
      return
    }
    try {
      const stored = localStorage.getItem(`cutline:cast_refs:${shotId}`)
      if (stored !== null) {
        const val = stored === 'true'
        includeCastRefsInVideo.value = val
        userCastRefOverrides[shotId] = val
        return
      }
    } catch {}

    const clipTakes = (selectedShot.value?.takes ?? []).filter((take) => take.media_type === 'clip')
    const latest = clipTakes.find((take) => take.id === selectedShot.value?.selected_clip_take_id) ?? clipTakes[clipTakes.length - 1]
    includeCastRefsInVideo.value = Boolean(
      latest?.settings?.include_cast_references
    )
  },
  { immediate: true },
)
watch(
  () => [selectedShot.value?.id, castCharacters.value.map((character) => `${character.id}:${character.references.length}`).join('|')] as const,
  () => {
    syncSelectedCharactersForShot(selectedShot.value)
  },
  { immediate: true },
)
watch(() => route.query.scene, (sceneId) => {
  const index = scenes.value.findIndex((scene) => scene.id === sceneId)
  activeScene.value = index >= 0 ? index : 0
}, { immediate: true })
watch(
  () => route.query.notes,
  (val) => {
    if (val === '1' || val === 'true') {
      showShotNotesDrawer.value = true
    }
  },
  { immediate: true },
)

function scrollElementIntoBrowser(el: HTMLElement, behavior: ScrollBehavior = 'smooth') {
  if (typeof el.scrollIntoView !== 'function') return
  const browser = el.closest('.shot-browser')
  if (!browser || typeof browser.getBoundingClientRect !== 'function' || typeof el.getBoundingClientRect !== 'function') {
    el.scrollIntoView({ block: 'center', behavior })
    return
  }
  const browserRect = browser.getBoundingClientRect()
  const elRect = el.getBoundingClientRect()
  // If the container has no computed layout (e.g. in test environments), scroll directly
  if (browserRect.height === 0 && browserRect.width === 0) {
    el.scrollIntoView({ block: 'center', behavior })
    return
  }
  const header = browser.querySelector('.shot-browser-heading') as HTMLElement | null
  const headerHeight = header?.offsetHeight || 80
  const isVisible = elRect.top >= browserRect.top + headerHeight && elRect.bottom <= browserRect.bottom
  if (!isVisible) {
    el.scrollIntoView({ block: 'center', behavior })
  }
}

function scrollShotIntoBrowserView(shotId?: string, behavior: ScrollBehavior = 'smooth') {
  if (!shotId) return
  void nextTick(() => {
    const el = document.getElementById(`browser-shot-${shotId}`)
    if (el) {
      scrollElementIntoBrowser(el, behavior)
    } else if (typeof window !== 'undefined') {
      window.requestAnimationFrame(() => {
        const retryEl = document.getElementById(`browser-shot-${shotId}`)
        if (retryEl) {
          scrollElementIntoBrowser(retryEl, behavior)
        }
      })
    }
  })
}

watch([() => route.params.shotId, allShots, activeStage], ([shotId, shots, stage]) => {
  const hasShotParam = typeof shotId === 'string' && shotId.length > 0
  const requestedShot = hasShotParam ? shots.find((shot) => shot.id === shotId) : undefined
  if (hasShotParam) {
    activeShotId.value = requestedShot?.id ?? (shots.length === 0 ? shotId : shots[0]?.id ?? null)
    if (requestedShot) {
      store.focusShotId = requestedShot.id
      const requestedSceneIndex = scenes.value.findIndex((scene) => scene.shots.some((shot) => shot.id === requestedShot.id))
      if (requestedSceneIndex >= 0) {
        activeScene.value = requestedSceneIndex
        const requestedSceneId = scenes.value[requestedSceneIndex]?.id
        if (requestedSceneId && route.query.scene !== requestedSceneId) {
          void router.replace({ query: buildSyncedQuery({ scene: requestedSceneId }) })
        }
      }
      scrollShotIntoBrowserView(requestedShot.id)
    }
    produceDetailOpen.value = true
    // Only replace route if shots have loaded and the project is initialized, and the requested shot is truly missing
    if (shots.length > 0 && requestedShot?.id !== shotId && activeShotId.value && store.currentProject) {
      void router.replace(projectRoute(store.currentProject.id, stage, activeShotId.value))
    }
  } else {
    activeShotId.value = shots.find((shot) => shot.id === store.focusShotId)?.id ?? shots[0]?.id ?? null
    produceDetailOpen.value = false
  }
}, { immediate: true })
watch(selectedShot, (shot, previous) => {
  if (!shot) return
  activeShotId.value = shot.id
  if (shot.id !== previous?.id) {
    showPromptEditor.value = pendingPromptEditor.value
    pendingPromptEditor.value = false
    if (produceDetailOpen.value) {
      scrollShotIntoBrowserView(shot.id)
    }
  }
  Object.assign(shotDraft, {
    framing: shot.framing,
    duration_ms: shot.duration_ms,
    image_prompt: shot.image_prompt,
    video_prompt: shot.video_prompt,
    title_card_text: shot.title_card_text,
  })
}, { immediate: true })
let previewTimer: number | undefined
let previewLastTick = 0

function stopPreviewTimer() {
  if (previewTimer !== undefined) window.clearInterval(previewTimer)
  previewTimer = undefined
}

function advancePreview(deltaMs: number) {
  if (!previewShot.value) return
  previewElapsedMs.value += deltaMs
  while (previewShot.value && previewElapsedMs.value >= previewShot.value.duration_ms) {
    const overflow = previewElapsedMs.value - previewShot.value.duration_ms
    if (previewShotIndex.value >= allShots.value.length - 1) {
      previewElapsedMs.value = previewShot.value.duration_ms
      previewPlaying.value = false
      return
    }
    previewShotIndex.value += 1
    previewElapsedMs.value = overflow
  }
}

watch([previewPlaying, showPreview], ([playing, open]) => {
  stopPreviewTimer()
  if (!playing || !open) return
  previewLastTick = performance.now()
  previewTimer = window.setInterval(() => {
    const now = performance.now()
    advancePreview(now - previewLastTick)
    previewLastTick = now
  }, 50)
})

watch([previewShot, previewPlaying], () => {
  void nextTick(syncPreviewVideo)
})

function handleGlobalPointerDown(event: PointerEvent) {
  if (!projectMenuId.value) return
  const target = event.target instanceof Element ? event.target : null
  if (target?.closest('.project-menu, .project-menu-trigger')) return
  projectMenuId.value = null
}

function handleGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (showShotNotesDrawer.value) {
      showShotNotesDrawer.value = false
      return
    }
    if (deleteChatUrl.value) {
      if (!deletingChat.value) deleteChatUrl.value = null
      return
    }
    if (showScriptGuide.value) {
      showScriptGuide.value = false
      return
    }
    if (projectMenuId.value) {
      projectMenuId.value = null
    }
  }
}

onUnmounted(() => {
  stopPreviewTimer()
  document.removeEventListener('pointerdown', handleGlobalPointerDown)
  document.removeEventListener('keydown', handleGlobalKeydown)
})
onMounted(async () => {
  document.addEventListener('pointerdown', handleGlobalPointerDown)
  document.addEventListener('keydown', handleGlobalKeydown)
  const projectId = typeof route.params.projectId === 'string' ? route.params.projectId : undefined
  const queryEpisode = typeof route.query.episode === 'string' ? route.query.episode : undefined
  const queryCompilation = typeof route.query.compilation === 'string' ? route.query.compilation : undefined
  await store.initialize(projectId, queryEpisode)
  if (!projectId && store.projects[0]) {
    await router.replace(projectRoute(store.projects[0].id, defaultStage(store.projects[0])))
  } else {
    if (queryCompilation && activeStage.value === 'final-cut' && store.currentProject) {
      const exists = store.compilations.some((c) => c.id === queryCompilation)
      if (exists) {
        store.selectCompilation(queryCompilation)
      } else {
        void router.replace({
          query: buildSyncedQuery({ compilation: undefined }),
        })
      }
    }
    if (store.currentEpisodeId && route.query.episode !== store.currentEpisodeId) {
      void router.replace({
        query: buildSyncedQuery({ episode: store.currentEpisodeId }),
      })
    }
  }
})

function formatTime(milliseconds: number | null) {
  if (milliseconds === null) return '--:--'
  const totalSeconds = Math.floor(milliseconds / 1000)
  return `${String(Math.floor(totalSeconds / 60)).padStart(2, '0')}:${String(totalSeconds % 60).padStart(2, '0')}`
}

function formatElapsed(seconds: number) {
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

function shotCountLabel(count: number) {
  return `${count} ${count === 1 ? 'shot' : 'shots'}`
}

function sceneDurationLabel(scene: Scene) {
  const ms = Math.max(0, (scene.end_ms ?? 0) - (scene.start_ms ?? 0))
  if (!ms && scene.shots.length) {
    return `${(scene.shots.reduce((total, shot) => total + shot.duration_ms, 0) / 1000).toFixed(0)}s`
  }
  return `${(ms / 1000).toFixed(0)}s`
}

function shotMedia(shot: Shot, mediaType: 'image' | 'clip') {
  return resolveActiveShotMedia(shot, mediaType, mediaAspect.value)
    ?? (store.currentProject?.aspect_ratio === mediaAspect.value ? (mediaType === 'clip' ? shot.clip : shot.image) : null)
    ?? shot.media_variants.find((item) => item.media_type === mediaType && item.aspect_ratio === mediaAspect.value)
    ?? null
}

function triggerCreateDraft(retryFailedOnly = false) {
  const variant: OutputVariant | 'both' = generateBothAspects.value
    ? 'both'
    : (mediaAspect.value === '9:16' ? 'shorts' : 'landscape')
  void store.createDraft({ variant, retry_failed_only: retryFailedOnly })
}

async function handlePlaySelectedTake(take: ShotTake) {
  if (take.media_type !== 'clip') return
  await nextTick()
  const video = storyboardVideo.value
  if (!video) return
  try {
    video.currentTime = 0
    await video.play()
  } catch {
    // Browser autoplay policy fallback: try muted playback if unmuted play was blocked
  }
}

function shotThumbUrl(shot: Shot): string | null {
  const image = shotMedia(shot, 'image')
  if (image?.url) return image.url
  const ref = shot.image_references?.find((item) => item.aspect_ratio === mediaAspect.value)
    ?? shot.image_references?.[0]
  if (ref?.url) return ref.url
  const cont = shot.continuity_frames?.find((item) => item.aspect_ratio === mediaAspect.value)
    ?? shot.continuity_frames?.[0]
  if (cont?.url) return cont.url
  return null
}

function shotHasMedia(shot: Shot): boolean {
  return Boolean(
    shotMedia(shot, 'clip') ||
    shotMedia(shot, 'image') ||
    shot.image_references?.length ||
    shot.continuity_frames?.length
  )
}

function sceneReadyCount(scene: Scene) {
  return scene.shots.filter((shot) => shotMedia(shot, 'clip') || shotMedia(shot, 'image')).length
}

function sceneClipReadyCount(scene: Scene) {
  return scene.shots.filter((shot) => shotMedia(shot, 'clip')).length
}

function sceneDraftStatus(scene: Scene): 'pending' | 'in_progress' | 'ready' {
  if (!scene.shots.length) return 'pending'
  const clips = sceneClipReadyCount(scene)
  if (clips >= scene.shots.length) return 'ready'
  if (clips > 0 || sceneReadyCount(scene) > 0) return 'in_progress'
  return 'pending'
}

function scenePreviewShot(scene: Scene) {
  const selectedInScene = scene.shots.find((shot) => shot.id === activeShotId.value)
  if (selectedInScene) return selectedInScene
  return scene.shots.find((shot) => shotMedia(shot, 'clip'))
    ?? scene.shots.find((shot) => shotMedia(shot, 'image'))
    ?? scene.shots[0]
    ?? null
}

function scenePreviewClip(scene: Scene) {
  const shot = scenePreviewShot(scene)
  return shot ? shotMedia(shot, 'clip') : null
}

function scenePreviewImage(scene: Scene) {
  const shot = scenePreviewShot(scene)
  return shot ? shotMedia(shot, 'image') : null
}

function isProduceShotExpanded(shotId: string) {
  return expandedProduceShotIds.value.includes(shotId)
}

function produceSceneBlurb(scene: Scene) {
  const expanded = scene.shots.find((shot) => isProduceShotExpanded(shot.id))
  const selectedInScene = scene.shots.find((shot) => shot.id === activeShotId.value)
  const shot = expanded || selectedInScene || scenePreviewShot(scene)
  return (shot?.action || scene.action || 'Scene beat from your screenplay.').trim()
}

function toggleProduceShot(shotId: string) {
  if (isProduceShotExpanded(shotId)) {
    expandedProduceShotIds.value = expandedProduceShotIds.value.filter((id) => id !== shotId)
  } else {
    expandedProduceShotIds.value = [...expandedProduceShotIds.value, shotId]
  }
  selectShot(shotId)
}

function openProduceDetail(shotId: string, opts?: { editPrompts?: boolean }) {
  pendingPromptEditor.value = Boolean(opts?.editPrompts)
  selectShot(shotId)
  produceDetailOpen.value = true
  void nextTick(() => {
    showPromptEditor.value = pendingPromptEditor.value
    pendingPromptEditor.value = false
    scrollShotIntoBrowserView(shotId)
  })
}

async function closeProduceDetail() {
  const shotId = selectedShot.value?.id
  produceDetailOpen.value = false
  showPromptEditor.value = false
  const projectId = store.currentProject?.id ?? (route.params.projectId as string)
  if (!projectId) return
  if (shotId) {
    store.focusShotId = shotId
    if (!expandedProduceShotIds.value.includes(shotId)) {
      expandedProduceShotIds.value = [...expandedProduceShotIds.value, shotId]
    }
  }
  await router.push(projectRoute(projectId, 'shots'))
  if (!shotId) return
  await nextTick()
  document.getElementById(`produce-shot-${shotId}`)?.scrollIntoView({ block: 'center' })
}

function ensureFirstShotsExpanded() {
  const firstIds = scenes.value
    .map((scene) => scene.shots[0]?.id)
    .filter((id): id is string => Boolean(id))
  const valid = new Set(allShots.value.map((shot) => shot.id))
  const kept = expandedProduceShotIds.value.filter((id) => valid.has(id))
  expandedProduceShotIds.value = [...new Set([...kept, ...firstIds])]
}

function continuityDraft(scene: Scene): { set_notes: string; lighting_notes: string; wardrobe_notes: string; props_notes: string } {
  const existing = continuityDrafts[scene.id]
  if (existing) return existing
  const draft = {
    set_notes: scene.set_notes || '',
    lighting_notes: scene.lighting_notes || '',
    wardrobe_notes: scene.wardrobe_notes || '',
    props_notes: scene.props_notes || '',
  }
  continuityDrafts[scene.id] = draft
  return draft
}

function syncContinuityDrafts() {
  for (const scene of scenes.value) {
    continuityDrafts[scene.id] = {
      set_notes: scene.set_notes || '',
      lighting_notes: scene.lighting_notes || '',
      wardrobe_notes: scene.wardrobe_notes || '',
      props_notes: scene.props_notes || '',
    }
  }
}

function continuityFilledCount(scene: Scene) {
  const draft = continuityDraft(scene)
  return [draft.set_notes, draft.lighting_notes, draft.wardrobe_notes, draft.props_notes]
    .filter((value) => value.trim()).length
}

function isContinuitySaving(sceneId: string) {
  return continuitySavingIds.value.includes(sceneId)
}

async function saveContinuity(scene: Scene) {
  const draft = continuityDraft(scene)
  if (isContinuitySaving(scene.id)) return
  continuitySavingIds.value = [...continuitySavingIds.value, scene.id]
  try {
    await store.updateScene(scene.id, {
      set_notes: draft.set_notes.trim(),
      lighting_notes: draft.lighting_notes.trim(),
      wardrobe_notes: draft.wardrobe_notes.trim(),
      props_notes: draft.props_notes.trim(),
    })
  } catch {
    // Store surfaces the error.
  } finally {
    continuitySavingIds.value = continuitySavingIds.value.filter((id) => id !== scene.id)
  }
}

function beginRegenerateImage() {
  showPromptEditor.value = true
  void generateShotImage(false, generateBothAspects.value, false)
}

function beginRegenerateBothImages() {
  showPromptEditor.value = true
  void generateShotImage(false, true, false)
}

function beginRegenerateImageAndClip() {
  showPromptEditor.value = true
  void generateShotImage(true, generateBothAspects.value, false)
}

function beginExpandFromOtherAspect() {
  skipDefaultImageReference.value = false
  showPromptEditor.value = true
  void generateShotImage(false, false, false)
}

function beginGenerateFreshImage() {
  skipDefaultImageReference.value = true
  showPromptEditor.value = true
  void generateShotImage(false, false, true)
}

function beginGenerateChatGPTDual() {
  beginRegenerateBothImages()
}

function beginExpandAndGenerateClip() {
  skipDefaultImageReference.value = false
  showPromptEditor.value = true
  void generateShotImage(true, false, false)
}

function beginRegenerateClip(bothAspects = generateBothAspects.value) {
  showPromptEditor.value = true
  void generateShotClip(bothAspects)
}

const resettingFlow = ref(false)
const switchingFlowModel = ref(false)
const flowSyncNotice = ref('')

async function syncFlowAccount() {
  resettingFlow.value = true
  flowSyncNotice.value = ''
  try {
    const res = await api.resetFlowSession()
    flowSyncNotice.value = res.message || 'Flow account synced with active browser session.'
    await store.initialize(store.currentProject?.id)
  } catch (err: any) {
    flowSyncNotice.value = err?.message || 'Failed to sync Flow account'
  } finally {
    resettingFlow.value = false
  }
}

async function changeFlowModelFromEditor(event: Event) {
  const preset = (event.target as HTMLSelectElement).value
  if (!preset || switchingFlowModel.value) return
  switchingFlowModel.value = true
  flowSyncNotice.value = ''
  try {
    store.videoProvider = await api.setFlowVideoModel(preset)
    flowSyncNotice.value = `Video model set to ${store.videoProvider.model} (${store.videoProvider.detail})`
  } catch (err: any) {
    flowSyncNotice.value = err?.message || 'Failed to switch Flow video model'
  } finally {
    switchingFlowModel.value = false
  }
}


function sceneTime(scene: Scene) {
  return `${formatTime(scene.start_ms)}-${formatTime(scene.end_ms)}`
}

function shotTime(shot: Shot) {
  return `${formatTime(shot.start_ms)}-${formatTime(shot.end_ms)}`
}

function sceneCharacters(scene: Scene) {
  const names = scene.dialogue.map((line) => line.character)
  return [...new Set(names)].join(', ') || 'None'
}

function defaultStage(project: ProjectSummary): ProductionStage {
  return project.status === 'shot_plan' ? 'shots' : 'script'
}

function projectRoute(projectId: string, stage: ProductionStage, shotId?: string | null) {
  const query: Record<string, string> = {}
  const sameProject = store.currentProject?.id === projectId
  if (sameProject) {
    const shotScene = shotId
      ? scenes.value.find((scene) => scene.shots.some((shot) => shot.id === shotId))
      : undefined
    if (shotScene) {
      query.scene = shotScene.id
    } else if (typeof route.query.scene === 'string' && scenes.value.some((scene) => scene.id === route.query.scene)) {
      query.scene = route.query.scene
    }
    if (store.currentEpisodeId) query.episode = store.currentEpisodeId
    else if (route.query.episode) query.episode = String(route.query.episode)
    query.aspect = mediaAspect.value
    if (stage === 'final-cut') {
      query.variant = mediaAspect.value === '9:16' ? 'shorts' : 'landscape'
    }
    if (stage === 'final-cut' && store.activeCompilationId) {
      query.compilation = store.activeCompilationId
    }
  }

  return {
    name: 'project-stage',
    params: {
      projectId,
      stage,
      ...((stage === 'shots' || stage === 'clips') && shotId ? { shotId } : {}),
    },
    query,
  }
}

const focusShot = computed(() => allShots.value.find((shot) => shot.id === store.focusShotId) ?? null)

function stageRoute(stage: ProductionStage) {
  if (!store.currentProject) return { name: 'studio' }
  const shotId = focusShot.value?.id ?? selectedShot.value?.id
  if (stage === 'shots' || stage === 'clips') return projectRoute(store.currentProject.id, stage, shotId)
  const target = projectRoute(store.currentProject.id, stage)
  if (stage === 'final-cut' && activeStage.value !== 'final-cut' && focusShot.value) {
    target.query.shot = focusShot.value.id
  }
  return target
}

function progressClass(step: number) {
  return {
    complete: step < currentProgressStep.value,
    current: step === currentProgressStep.value,
  }
}

function selectScene(index: number) {
  activeScene.value = index
  const scene = scenes.value[index]
  void router.replace({ query: scene ? buildSyncedQuery({ scene: scene.id }) : buildSyncedQuery() })
}

const deepReviewRefreshKey = computed(() => {
  const p = store.currentProject
  if (!p?.current_version) return ''
  const shotKey = p.current_version.scenes
    .map(sc => `${sc.id}:${sc.set_notes}:${sc.lighting_notes}:${sc.wardrobe_notes}:${sc.props_notes}:${sc.shots.map(sh => `${sh.id}:${sh.title}:${sh.framing}:${sh.duration_ms}:${sh.action}:${sh.sound}:${sh.image_prompt}:${sh.video_prompt}:${JSON.stringify(sh.dialogue)}`).join(',')}`)
    .join('|')
  const castKey = (p.characters || []).map(c => `${c.id}:${c.name}:${c.notes}`).join('|')
  return `${p.updated_at}:${shotKey}:${castKey}`
})

async function handleDeepReviewApplied() {
  if (!store.currentProject) return
  await store.loadProject(store.currentProject.id, { episodeId: store.currentEpisodeId || undefined })
}

async function navigateToReviewShot(shotId: string) {
  const sceneIndex = scenes.value.findIndex(scene => scene.shots.some(shot => shot.id === shotId))
  if (sceneIndex >= 0) {
    selectScene(sceneIndex)
  }
  await nextTick()
  const el = document.getElementById(`review-shot-${shotId}`)
  el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

async function jumpToEpisodeShot(episodeId: string, shotId?: string) {
  if (episodeId && episodeId !== store.currentEpisodeId) {
    await store.selectEpisode(episodeId)
  }
  if (shotId) {
    await navigateToReviewShot(shotId)
  }
}

function selectShot(id: string, stage: 'shots' | 'clips' = 'shots') {
  activeShotId.value = id
  store.focusShotId = id
  if (store.currentProject) void router.push(projectRoute(store.currentProject.id, stage, id))
  if (produceDetailOpen.value) scrollShotIntoBrowserView(id)
}

function openTrimEditor() {
  if (!store.currentProject || !selectedShot.value) return
  void router.push({
    ...projectRoute(store.currentProject.id, 'final-cut'),
    query: buildSyncedQuery({
      trimShot: selectedShot.value.id,
      variant: mediaAspect.value === '9:16' ? 'shorts' : 'landscape',
      aspect: mediaAspect.value,
    }),
  })
}

function setFinalCutVariant(variant: OutputVariant) {
  const aspect: AspectRatio = variant === 'shorts' ? '9:16' : '16:9'
  finalCutAspect.value = aspect
  if (mediaAspect.value !== aspect) {
    mediaAspect.value = aspect
    return
  }
  if (route.query.variant !== variant || route.query.aspect !== aspect) {
    void router.replace({ query: buildSyncedQuery({ variant, aspect }) })
  }
}

async function decide(id: string | number, decision: 'accepted' | 'rejected') {
  if (typeof id === 'string' && store.currentProject) {
    await store.decideSuggestion(id, decision)
  }
}

async function loadDemoProject() {
  store.busy = true
  store.error = ''
  try {
    const project = await api.loadDemoProject()
    store.projects = await api.projects()
    await store.loadProject(project.id)
    showCreate.value = false
    await router.push(projectRoute(project.id, 'shots'))
  } catch (cause) {
    store.error = cause instanceof Error ? cause.message : 'Could not load demo project'
  } finally {
    store.busy = false
  }
}

async function submitProject() {
  try {
    await store.createProject({ ...form })
    showCreate.value = false
    if (store.currentProject) await router.push(projectRoute(store.currentProject.id, 'script'))
  } catch {
    // The store exposes the actionable error in the interface.
  }
}

async function submitContinuation() {
  const previousSceneCount = scenes.value.length
  try {
    await store.continueProject({ ...continuationForm })
    showContinuation.value = false
    continuationForm.script = ''
    activeScene.value = Math.min(previousSceneCount, scenes.value.length - 1)
    const scene = scenes.value[activeScene.value]
    if (scene) await router.replace({ query: { ...route.query, scene: scene.id } })
  } catch {
    // The store exposes the actionable error in the modal.
  }
}

function projectStatusTone(status: string): 'review' | 'produce' | 'complete' {
  const normalized = (status || '').toLowerCase()
  if (normalized === 'final_cut' || normalized === 'completed' || normalized === 'complete') {
    return 'complete'
  }
  if (normalized === 'shot_plan' || normalized === 'shots' || normalized === 'storyboards' || normalized === 'clips') {
    return 'produce'
  }
  return 'review'
}

function formatProjectMeta(project: ProjectSummary): string {
  const parts: string[] = []
  if (project.aspect_ratio) parts.push(project.aspect_ratio)
  if (project.updated_at) {
    const date = new Date(project.updated_at)
    if (!Number.isNaN(date.getTime())) {
      parts.push(
        new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date),
      )
    }
  }
  return parts.join(' · ')
}

async function handleShotNotesChanged(shot: Shot, notes: ShotNote[]) {
  shot.notes = notes
  shot.unresolved_note_count = notes.filter((note) => !note.resolved).length
  try {
    store.projects = await api.projects()
    if (store.currentProject) {
      const updated = store.projects.find((item) => item.id === store.currentProject?.id)
      if (updated && typeof updated.unresolved_note_count === 'number') {
        store.currentProject.unresolved_note_count = updated.unresolved_note_count
      }
    }
  } catch {}
}

function openShotNotes(shot?: Shot) {
  if (shot && shot.id !== activeShotId.value) {
    openProduceDetail(shot.id)
  }
  showShotNotesDrawer.value = true
}

function jumpToFirstUnresolvedShot() {
  const shotWithNote = allShots.value.find((s) => (s.unresolved_note_count ?? 0) > 0)
  if (shotWithNote) {
    openProduceDetail(shotWithNote.id)
    showShotNotesDrawer.value = true
  }
}

function openDeleteConfirmation(project: ProjectSummary) {
  projectMenuId.value = null
  deleteCandidate.value = project
}

async function selectProject(id: string) {
  projectMenuId.value = null
  const project = store.projects.find((item) => item.id === id)
  await router.push(projectRoute(id, project ? defaultStage(project) : 'script'))
}

async function confirmDeleteProject() {
  if (!deleteCandidate.value) return
  deletingProject.value = true
  try {
    await store.deleteProject(deleteCandidate.value.id)
    deleteCandidate.value = null
    if (store.currentProject) {
      await router.replace(projectRoute(store.currentProject.id, defaultStage(store.currentProject)))
    } else {
      await router.replace('/')
    }
  } catch {
    // The store keeps the API error visible in the confirmation dialog.
  } finally {
    deletingProject.value = false
  }
}

async function approveAndOpenShots() {
  await store.approveScript()
  if (store.currentProject?.status === 'shot_plan') {
    await router.push(projectRoute(store.currentProject.id, 'shots', allShots.value[0]?.id))
  }
}

async function saveShot() {
  if (!selectedShot.value) return
  await store.updateShot(selectedShot.value.id, {
    ...shotDraft,
    character_ids: selectedCharacterIds.value,
  })
}

async function syncCastFromScript() {
  if (castingBusy.value) return
  castingBusy.value = true
  try {
    await store.syncCast()
    try {
      await store.syncElements()
    } catch {
      // Optional scenes/props sync
    }
    syncSelectedCharactersForShot(selectedShot.value)
  } catch {
    // Store error surface.
  } finally {
    castingBusy.value = false
  }
}

async function generateCastFace(character: Character, look = '') {
  try {
    const defaultLook = look || (character.kind === 'scene' ? 'scene' : character.kind === 'prop' ? 'prop' : '')
    await store.generateCharacterReference(character.id, defaultLook)
  } catch {
    // Store error surface.
  }
}

async function generateElement3DView(character: Character) {
  try {
    await store.generateCharacterReference(character.id, '3d_view')
  } catch {
    // Store error surface.
  }
}

async function generateMissingCastFaces() {
  const missing = visibleLibraryElements.value.filter((character) => !character.references.length)
  for (const character of missing) {
    if (store.isGeneratingCast(character.id)) continue
    await generateCastFace(character)
  }
}

function castJobFor(characterId: string) {
  return store.castJobs[characterId] ?? null
}

async function generateShotImage(
  andVideo = false,
  bothAspects = generateBothAspects.value,
  forceFresh = false,
  chatgptDual = false,
) {
  if (!selectedShot.value) return
  try {
    await saveShot()
    const useDefault = (forceFresh || chatgptDual) ? false : !skipDefaultImageReference.value
    await store.generateShotImage(
      selectedShot.value.id,
      mediaAspect.value,
      selectedCharacterIds.value,
      false,
      (forceFresh || chatgptDual) ? null : selectedAspectReference.value?.id,
      useDefault,
      {
        bothAspects: chatgptDual ? true : bothAspects,
        generateVideo: andVideo,
        includeCastReferences: includeCastRefsInVideo.value,
        chatgptDual: false,
        lockBlocking: false,
        focalCharacterId: null,
      },
    )
  } catch {
    // The store exposes the provider or validation error in the workspace.
  }
}

async function generateLockedBlockingImage() {
  if (!selectedShot.value) return
  try {
    await saveShot()
    lockSceneBlocking.value = true
    const useDefault = !selectedAspectReference.value && !skipDefaultImageReference.value
    await store.generateShotImage(
      selectedShot.value.id,
      mediaAspect.value,
      selectedCharacterIds.value,
      false,
      selectedAspectReference.value?.id,
      useDefault,
      {
        bothAspects: false,
        generateVideo: false,
        includeCastReferences: false,
        chatgptDual: false,
        lockBlocking: true,
        focalCharacterId: focalCharacterId.value,
      },
    )
  } catch {
    // The store exposes the provider or validation error in the workspace.
  }
}

async function addCastCharacter() {
  const name = newCharacterName.value.trim()
  if (!name || castingBusy.value) return
  castingBusy.value = true
  try {
    const created = await store.createCharacter(name, '', activeElementTab.value)
    newCharacterName.value = ''
    if (created && !created.references?.length && imageGenerationAvailable.value) {
      void generateCastFace(created)
    }
  } catch {
    // Store error surface.
  } finally {
    castingBusy.value = false
  }
}

async function renameCastCharacter(character: Character, event: Event) {
  const name = (event.target as HTMLInputElement).value.trim()
  if (!name || name === character.name) return
  try {
    await store.updateCharacter(character.id, { name })
  } catch {
    ;(event.target as HTMLInputElement).value = character.name
  }
}

async function removeCastCharacter(characterId: string) {
  castingBusy.value = true
  try {
    await store.deleteCharacter(characterId)
    selectedCharacterIds.value = selectedCharacterIds.value.filter((id) => id !== characterId)
  } catch {
    // Store error surface.
  } finally {
    castingBusy.value = false
  }
}

function chooseCastReference(characterId: string) {
  castRefTargetId.value = characterId
  castRefInput.value?.click()
}

async function handleCastReferenceUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  const characterId = castRefTargetId.value
  if (!file || !characterId) return
  castingBusy.value = true
  try {
    await store.uploadCharacterReference(characterId, file)
    if (!selectedCharacterIds.value.includes(characterId)) {
      selectedCharacterIds.value = [...selectedCharacterIds.value, characterId]
    }
  } catch {
    // Store error surface.
  } finally {
    castingBusy.value = false
    castRefTargetId.value = null
    input.value = ''
  }
}

async function removeCastReference(referenceId: string) {
  castingBusy.value = true
  try {
    await store.deleteCharacterReference(referenceId)
  } catch {
    // Store error surface.
  } finally {
    castingBusy.value = false
  }
}

function chooseUpload() {
  uploadInput.value?.click()
}

const modalBackdropPressed = ref(false)

function trackModalBackdropPress(event: MouseEvent | PointerEvent) {
  modalBackdropPressed.value = event.target === event.currentTarget
}

function handleBackdropClose(event: MouseEvent, closeFn: () => void) {
  if (modalBackdropPressed.value && event.target === event.currentTarget) {
    closeFn()
  }
  modalBackdropPressed.value = false
}

function trackCreateBackdropPress(event: PointerEvent) {
  trackModalBackdropPress(event)
}

function closeCreateFromBackdrop(event: MouseEvent) {
  handleBackdropClose(event, () => { showCreate.value = false })
}

function setShotDuration(event: Event) {
  shotDraft.duration_ms = Math.round(Number((event.target as HTMLInputElement).value) * 1000)
}

function openAnimaticPreview() {
  const selectedIndex = selectedShot.value
    ? allShots.value.findIndex((shot) => shot.id === selectedShot.value?.id)
    : 0
  previewShotIndex.value = Math.max(0, selectedIndex)
  previewElapsedMs.value = 0
  showPreview.value = true
  previewPlaying.value = allShots.value.length > 0
  void nextTick(syncPreviewVideo)
}

function closeAnimaticPreview() {
  previewVideo.value?.pause()
  previewPlaying.value = false
  showPreview.value = false
}

function togglePreviewPlayback() {
  if (!previewShot.value) return
  const atEnd = previewShotIndex.value === allShots.value.length - 1
    && previewElapsedMs.value >= previewShot.value.duration_ms
  if (atEnd) {
    previewShotIndex.value = 0
    previewElapsedMs.value = 0
  }
  previewPlaying.value = !previewPlaying.value
  void nextTick(syncPreviewVideo)
}

function selectPreviewShot(index: number, play = false) {
  previewShotIndex.value = Math.min(Math.max(index, 0), Math.max(allShots.value.length - 1, 0))
  previewElapsedMs.value = 0
  if (play && allShots.value.length) previewPlaying.value = true
  void nextTick(syncPreviewVideo)
}

function seekPreview(event: Event) {
  const targetMs = Number((event.target as HTMLInputElement).value)
  let cursor = 0
  for (const [index, shot] of allShots.value.entries()) {
    const shotEnd = cursor + shot.duration_ms
    if (targetMs < shotEnd || index === allShots.value.length - 1) {
      previewShotIndex.value = index
      previewElapsedMs.value = Math.min(shot.duration_ms, Math.max(0, targetMs - cursor))
      void nextTick(syncPreviewVideo)
      return
    }
    cursor = shotEnd
  }
}

async function copyScriptGuide() {
  await navigator.clipboard.writeText(SCRIPT_GENERATION_PROMPT)
  scriptGuideCopied.value = true
  window.setTimeout(() => {
    scriptGuideCopied.value = false
  }, 1800)
}

const createFileInput = ref<HTMLInputElement | null>(null)
const continueFileInput = ref<HTMLInputElement | null>(null)
const episodeFileInput = ref<HTMLInputElement | null>(null)
const isDraggingCreate = ref(false)
const isDraggingContinuation = ref(false)
const isDraggingEpisodeFile = ref(false)
const createFileName = ref('')
const continuationFileName = ref('')
const episodeFileName = ref('')

const showEpisodeDropdown = ref(false)
const episodeDropdownRef = ref<HTMLElement | null>(null)
const showNewEpisodeModal = ref(false)
const showProjectSettingsModal = ref(false)
const showOriginalScriptModal = ref(false)
const showScriptHistory = ref(false)
const showCompilationModal = ref(false)
const compilationModalInitialEpisodeId = ref<string | null>(null)
const compilationModalInitialShotIds = ref<string[]>([])
const compilationModalInitialType = ref<'episode_shots' | 'multi_episode'>('episode_shots')
const compilationModalInitialCompilationId = ref<string | null>(null)
const compilationShots = ref<Shot[]>([])

function openCompilationModal(
  type: 'episode_shots' | 'multi_episode' = 'episode_shots',
  episodeId?: string | null,
  shotIds?: string[],
  compilationId?: string | null,
) {
  compilationModalInitialType.value = type
  compilationModalInitialEpisodeId.value = episodeId ?? store.currentEpisodeId ?? null
  compilationModalInitialShotIds.value = shotIds ?? []
  compilationModalInitialCompilationId.value = compilationId ?? null
  showCompilationModal.value = true
}

const shortsSelectionMode = ref(false)
const selectedShotIdsForShort = ref<string[]>([])

function toggleShortsSelectionMode() {
  shortsSelectionMode.value = !shortsSelectionMode.value
  if (!shortsSelectionMode.value) {
    selectedShotIdsForShort.value = []
  }
}

function toggleShotForShort(shotId: string) {
  const idx = selectedShotIdsForShort.value.indexOf(shotId)
  if (idx >= 0) {
    selectedShotIdsForShort.value.splice(idx, 1)
  } else {
    selectedShotIdsForShort.value.push(shotId)
  }
}

function isShotSelectedForShort(shotId: string): boolean {
  return selectedShotIdsForShort.value.includes(shotId)
}

function selectAllShotsForShort() {
  selectedShotIdsForShort.value = allShots.value.map((s) => s.id)
}

function clearShotsForShort() {
  selectedShotIdsForShort.value = []
}

const selectedShortsSummary = computed(() => {
  const selected = allShots.value.filter((s) => selectedShotIdsForShort.value.includes(s.id))
  const totalMs = selected.reduce((acc, s) => acc + (s.duration_ms || 4000), 0)
  const seconds = (totalMs / 1000).toFixed(1)
  return {
    count: selectedShotIdsForShort.value.length,
    durationMs: totalMs,
    durationFormatted: `${seconds}s`,
    isShortsReady: totalMs > 0 && totalMs <= 60000,
  }
})

function handleCreateShortFromSelection() {
  if (!selectedShotIdsForShort.value.length) return
  const shotIds = [...selectedShotIdsForShort.value]
  shortsSelectionMode.value = false
  selectedShotIdsForShort.value = []
  openCompilationModal('episode_shots', store.currentEpisodeId, shotIds)
}

watch(
  () => store.activeCompilationId,
  async (compilationId) => {
    if (compilationId) {
      try {
        compilationShots.value = await api.compilationShots(compilationId)
      } catch (err) {
        console.error('Failed to load compilation shots', err)
        compilationShots.value = []
      }
    } else {
      compilationShots.value = []
    }
  },
  { immediate: true },
)

const activeTimelineShots = computed(() => {
  if (store.isCompilationMode) {
    return compilationShots.value
  }
  return allShots.value
})

async function handleOpenCompilation(compilationId: string) {
  store.selectCompilation(compilationId)
  if (activeStage.value !== 'final-cut') {
    await router.push({
      ...stageRoute('final-cut'),
      query: buildSyncedQuery({ compilation: compilationId }),
    })
  } else if (route.query.compilation !== compilationId) {
    await router.replace({
      query: buildSyncedQuery({ compilation: compilationId }),
    })
  }
}
const scriptCopied = ref(false)
const originalScriptText = computed(() => {
  return store.currentProject?.current_version?.source_text || 'No script available.'
})

async function copyOriginalScript() {
  if (!originalScriptText.value) return
  await navigator.clipboard.writeText(originalScriptText.value)
  scriptCopied.value = true
  setTimeout(() => {
    scriptCopied.value = false
  }, 2000)
}
const newEpisodeForm = reactive({
  title: '',
  script: '',
  target_duration_seconds: 60,
})
const projectSettingsForm = reactive({
  title: '',
  episode_label: 'Episode',
  aspect_ratio: '16:9' as AspectRatio,
  target_duration_seconds: 60,
  text_provider: 'mock' as ProjectInput['text_provider'],
})
const episodeTitlesForm = reactive<Record<string, string>>({})

const episodeLabel = computed(() => store.episodeLabel)
const currentEpisodeDisplay = computed(() => {
  if (!store.currentEpisode) return `${episodeLabel.value}s`
  return `${episodeLabel.value} ${store.currentEpisode.episode_number}: ${store.currentEpisode.title}`
})

function handleDocumentClick(e: MouseEvent) {
  if (episodeDropdownRef.value && !episodeDropdownRef.value.contains(e.target as Node)) {
    showEpisodeDropdown.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})

onUnmounted(() => {
  document.removeEventListener('click', handleDocumentClick)
})

async function handleSelectEpisode(episodeId: string) {
  showEpisodeDropdown.value = false
  await store.selectEpisode(episodeId)
}

function openNewEpisodeModal() {
  showEpisodeDropdown.value = false
  const nextNum = (store.episodes.length || 0) + 1
  newEpisodeForm.title = `${episodeLabel.value} ${nextNum}`
  newEpisodeForm.script = ''
  newEpisodeForm.target_duration_seconds = store.currentProject?.target_duration_seconds || 60
  episodeFileName.value = ''
  showNewEpisodeModal.value = true
}

async function handleCreateEpisodeSubmit() {
  if (!newEpisodeForm.script.trim() || !newEpisodeForm.title.trim()) return
  await store.createEpisode({
    title: newEpisodeForm.title.trim(),
    script: newEpisodeForm.script.trim(),
    target_duration_seconds: newEpisodeForm.target_duration_seconds,
  })
  showNewEpisodeModal.value = false
}

function openProjectSettings() {
  showEpisodeDropdown.value = false
  projectMenuId.value = null
  if (store.currentProject) {
    projectSettingsForm.title = store.currentProject.title
    projectSettingsForm.episode_label = store.currentProject.episode_label || 'Episode'
    projectSettingsForm.aspect_ratio = store.currentProject.aspect_ratio === '9:16' ? '9:16' : '16:9'
    projectSettingsForm.target_duration_seconds = store.currentProject.target_duration_seconds ?? 0
    projectSettingsForm.text_provider = (store.currentProject.text_provider as ProjectInput['text_provider']) || 'mock'
    for (const ep of store.episodes) {
      episodeTitlesForm[ep.id] = ep.title || ''
    }
  }
  showProjectSettingsModal.value = true
}

async function openProjectSettingsFor(project: ProjectSummary) {
  projectMenuId.value = null
  if (store.currentProject?.id !== project.id) {
    await selectProject(project.id)
  }
  openProjectSettings()
}

async function handleSaveProjectSettings() {
  if (!projectSettingsForm.title.trim()) return
  const currentLabel = projectSettingsForm.episode_label.trim() || 'Episode'
  await store.updateProjectSettings({
    title: projectSettingsForm.title.trim(),
    episode_label: currentLabel,
    aspect_ratio: projectSettingsForm.aspect_ratio,
    target_duration_seconds: projectSettingsForm.target_duration_seconds,
    text_provider: projectSettingsForm.text_provider,
  })
  for (const ep of store.episodes) {
    const updatedTitle = episodeTitlesForm[ep.id]?.trim()
    if (updatedTitle !== undefined && updatedTitle !== (ep.title || '')) {
      await store.updateEpisode(ep.id, {
        title: updatedTitle || `${currentLabel} ${ep.episode_number}`,
      })
    }
  }
  showProjectSettingsModal.value = false
}

function openScreenplayFilePicker(target: 'create' | 'continue' | 'episode') {
  if (target === 'create') {
    createFileInput.value?.click()
  } else if (target === 'continue') {
    continueFileInput.value?.click()
  } else {
    episodeFileInput.value?.click()
  }
}

async function readFileAsText(file: File): Promise<string> {
  if (typeof file.text === 'function') {
    return await file.text()
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(file)
  })
}

function extractScreenplayTitle(text: string): string {
  const firstLines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  for (const line of firstLines) {
    if (/^(Genre|Duration|Format|Aspect ratio|Character consistency|Location|SHOT\s+\d+)/i.test(line)) {
      continue
    }
    const cleaned = line.replace(/^[#\s*["]+|[#\s*\]"]+$/g, '').trim()
    if (cleaned && cleaned.length <= 80) {
      return cleaned
    }
  }
  return ''
}

async function processScreenplayFile(file: File, target: 'create' | 'continue' | 'episode') {
  if (!file) return
  try {
    const content = await readFileAsText(file)
    if (target === 'create') {
      form.script = content
      createFileName.value = file.name
      if (!form.title.trim() || form.title === 'Untitled short') {
        const titleFromScript = extractScreenplayTitle(content)
        if (titleFromScript) {
          form.title = titleFromScript
        } else {
          const base = file.name.replace(/\.[^/.]+$/, '').replace(/[-_]+/g, ' ')
          form.title = base.charAt(0).toUpperCase() + base.slice(1)
        }
      }
    } else if (target === 'continue') {
      continuationForm.script = content
      continuationFileName.value = file.name
    } else if (target === 'episode') {
      newEpisodeForm.script = content
      episodeFileName.value = file.name
      const titleFromScript = extractScreenplayTitle(content)
      if (titleFromScript) {
        newEpisodeForm.title = titleFromScript
      }
    }
  } catch (err) {
    console.error('Failed to read screenplay file:', err)
  }
}

function handleScreenplayFileSelected(event: Event, target: 'create' | 'continue' | 'episode') {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) {
    void processScreenplayFile(file, target)
  }
  input.value = ''
}

function handleScreenplayDrop(event: DragEvent, target: 'create' | 'continue' | 'episode') {
  if (target === 'create') {
    isDraggingCreate.value = false
  } else if (target === 'continue') {
    isDraggingContinuation.value = false
  } else {
    isDraggingEpisodeFile.value = false
  }
  const file = event.dataTransfer?.files?.[0]
  if (file) {
    void processScreenplayFile(file, target)
  }
}

function clearLoadedFile(target: 'create' | 'continue' | 'episode') {
  if (target === 'create') {
    createFileName.value = ''
    form.script = ''
    if (createFileInput.value) createFileInput.value.value = ''
  } else if (target === 'continue') {
    continuationFileName.value = ''
    continuationForm.script = ''
    if (continueFileInput.value) continueFileInput.value.value = ''
  } else {
    episodeFileName.value = ''
    newEpisodeForm.script = ''
    if (episodeFileInput.value) episodeFileInput.value.value = ''
  }
}


async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !selectedShot.value) return
  try {
    await store.uploadShotImage(selectedShot.value.id, file, mediaAspect.value)
  } catch {
    // The store exposes the upload error in the workspace.
  } finally {
    input.value = ''
  }
}

async function handleAddShot(sceneId: string, insertAfterShotId?: string) {
  if (addingShotSceneId.value) return
  addingShotSceneId.value = sceneId
  try {
    const scene = scenes.value.find((s) => s.id === sceneId)
    const existingCount = scene?.shots?.length ?? 0
    const newTitle = `Shot ${existingCount + 1}`
    const created = await store.createShot(sceneId, {
      title: newTitle,
      framing: 'Medium',
      duration_ms: 5000,
      insert_after_shot_id: insertAfterShotId,
    })
    if (created) {
      activeShotId.value = created.id
      if (!expandedProduceShotIds.value.includes(created.id)) {
        expandedProduceShotIds.value = [...expandedProduceShotIds.value, created.id]
      }
      openProduceDetail(created.id, { editPrompts: true })
    }
  } catch {
    // store exposes error in state
  } finally {
    addingShotSceneId.value = null
  }
}

async function confirmDeleteShot() {
  const shotToDelete = deleteShotCandidate.value || selectedShot.value
  if (!shotToDelete) return
  deletingShot.value = true
  try {
    const currentList = allShots.value
    const currentIndex = currentList.findIndex((s) => s.id === shotToDelete.id)
    let fallbackShotId: string | null = null
    if (currentIndex > 0) {
      fallbackShotId = currentList[currentIndex - 1]?.id ?? null
    } else if (currentIndex < currentList.length - 1) {
      fallbackShotId = currentList[currentIndex + 1]?.id ?? null
    }

    await store.deleteShot(shotToDelete.id)
    deleteShotCandidate.value = null
    if (fallbackShotId) {
      activeShotId.value = fallbackShotId
      selectShot(fallbackShotId)
    }
  } catch {
    // store exposes error
  } finally {
    deletingShot.value = false
  }
}

async function confirmDeleteShotImage() {
  if (!selectedShot.value) return
  try {
    await store.deleteShotImage(selectedShot.value.id, mediaAspect.value)
    deleteShotImageCandidate.value = false
  } catch {
    // The store exposes the deletion error in the workspace.
  }
}

function chooseClipUpload() {
  clipUploadInput.value?.click()
}

async function generateShotClip(bothAspects = generateBothAspects.value) {
  if (!selectedShot.value || !store.currentProject) return
  const shotId = selectedShot.value.id
  const projectId = store.currentProject.id
  const episodeId = store.currentEpisodeId
  const aspectRatio = mediaAspect.value
  const useCastRefs = videoGenerationMode.value === 'r2v' ? true : includeCastRefsInVideo.value
  const input = { ...shotDraft, character_ids: [...selectedCharacterIds.value] }
  try {
    await store.updateShot(shotId, input)
    if (videoGenerationMode.value === 'r2v') {
      await store.generateShotClip(shotId, aspectRatio, projectId, episodeId, useCastRefs, bothAspects, {
        videoMode: 'r2v',
        includeSceneReference: includeSceneRefInR2V.value,
        elementIds: r2vAttachedElements.value.map((el) => el.id),
      })
    } else {
      await store.generateShotClip(shotId, aspectRatio, projectId, episodeId, useCastRefs, bothAspects)
    }
  } catch {
    // The store exposes the provider or workflow error in the workspace.
  }
}

async function handleClipUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !selectedShot.value) return
  try {
    await store.uploadShotClip(selectedShot.value.id, file, mediaAspect.value)
  } catch {
    // The store exposes the upload error in the workspace.
  } finally {
    input.value = ''
  }
}

async function confirmDeleteShotClip() {
  if (!selectedShot.value) return
  try {
    await store.deleteShotClip(selectedShot.value.id, mediaAspect.value)
    deleteShotClipCandidate.value = false
  } catch {
    // The store exposes the deletion error in the workspace.
  }
}

async function generateShotLipsync() {
  if (!selectedShot.value) return
  try {
    await store.lipsyncShot(selectedShot.value.id, mediaAspect.value)
  } catch {
    // store exposes error
  }
}

async function generateShotAudio() {
  if (!selectedShot.value) return
  try {
    await store.generateShotAudio(selectedShot.value.id)
  } catch {
    // The store exposes the generation error in the workspace.
  }
}

async function generateVariantFrame() {
  if (!selectedShot.value) return
  try {
    await saveShot()
    await store.generateShotImage(
      selectedShot.value.id,
      mediaAspect.value,
      selectedCharacterIds.value,
      true,
      selectedAspectReference.value?.id,
      !skipDefaultImageReference.value,
    )
  } catch {
    // The store exposes the provider error in the workspace.
  }
}

function syncPreviewVideo() {
  const video = previewVideo.value
  const shot = previewShot.value
  if (!video || !shot || !previewClip.value) return
  const targetSeconds = Math.min(
    Number.isFinite(video.duration) ? video.duration : Number.MAX_SAFE_INTEGER,
    previewElapsedMs.value / 1000,
  )
  if (Math.abs(video.currentTime - targetSeconds) > 0.25) video.currentTime = targetSeconds
  video.playbackRate = 1
  if (previewPlaying.value) void video.play().catch(() => {})
  else video.pause()
}

async function renderFinalCut() {
  try {
    await store.renderFinalCut()
  } catch {
    // The store exposes the render error in the workspace.
  }
}

async function confirmDeleteFinalCut() {
  try {
    await store.deleteFinalCut()
    deleteFinalCutCandidate.value = false
  } catch {
    // The store exposes the deletion error in the workspace.
  }
}

function formatRenderedAt(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(value))
}
</script>

<template>
  <main class="studio-layout" :class="{ 'assembling': activeStage === 'final-cut' }">
    <aside class="project-rail">
      <div class="rail-section rail-projects">
        <div class="section-label-row rail-projects-header">
          <div class="rail-projects-title">
            <span class="section-label">Projects</span>
            <span v-if="store.projects.length" class="rail-projects-count">{{ store.projects.length }}</span>
          </div>
          <button class="mini-icon rail-new-project-btn" title="New project" aria-label="Create project" @click="showCreate = true"><Plus :size="15" /></button>
        </div>
        <div class="project-list">
          <div
            v-for="project in store.projects"
            :key="project.id"
            class="project-item"
            :class="[
              `tone-${projectStatusTone(project.status)}`,
              { active: store.currentProject?.id === project.id, 'menu-open': projectMenuId === project.id },
            ]"
          >
            <button class="project-select" :title="project.title" @click="selectProject(project.id)">
              <span class="project-thumb" :class="`tone-${projectStatusTone(project.status)}`">
                <Clapperboard v-if="projectStatusTone(project.status) === 'produce'" :size="14" />
                <MessageSquareText v-else-if="projectStatusTone(project.status) === 'review'" :size="14" />
                <Film v-else :size="14" />
              </span>
              <span class="project-copy">
                <strong>{{ project.title }}</strong>
                <small class="project-meta">
                  <span class="project-status-dot" :class="`tone-${projectStatusTone(project.status)}`"></span>
                  <span class="project-status-label">{{ project.status.replace('_', ' ') }}</span>
                  <span v-if="formatProjectMeta(project)" class="project-meta-extra">· {{ formatProjectMeta(project) }}</span>
                </small>
                <span v-if="project.unresolved_note_count" class="project-note-badge"><MessageSquare :size="12" />{{ project.unresolved_note_count }}</span>
              </span>
            </button>
            <button
              class="project-menu-trigger"
              :aria-label="`Project actions for ${project.title}`"
              :aria-expanded="projectMenuId === project.id"
              title="Project actions"
              @click.stop="projectMenuId = projectMenuId === project.id ? null : project.id"
            ><MoreHorizontal :size="15" /></button>
            <div v-if="projectMenuId === project.id" class="project-menu" @click.stop>
              <button type="button" @click="openProjectSettingsFor(project)"><Settings :size="14" />Project settings</button>
              <button type="button" class="danger-item" @click="openDeleteConfirmation(project)"><Trash2 :size="14" />Delete project</button>
            </div>
          </div>
          <button v-if="!store.projects.length" class="project-item project-empty-action" :disabled="store.busy" @click="loadDemoProject">
            <span class="project-thumb tone-produce"><Play :size="14" /></span>
            <span class="project-copy"><strong>{{ store.busy ? 'Loading…' : 'Load demo project' }}</strong><small class="project-meta"><span class="project-status-label">Jasmine &amp; Skylar · 16 shots</span></small></span>
          </button>
          <button v-if="!store.projects.length" class="project-item project-empty-action" @click="showCreate = true">
            <span class="project-thumb tone-review"><Film :size="14" /></span>
            <span class="project-copy"><strong>Create a project</strong><small class="project-meta"><span class="project-status-label">Import a screenplay</span></small></span>
          </button>
        </div>
      </div>

      <div class="rail-section stage-list">
        <span class="section-label">Story</span>
        <RouterLink class="stage" :class="{ active: activeStage === 'script', complete: activeStage !== 'script' }" :to="stageRoute('script')"><MessageSquareText :size="16" /><span>Script</span><small>{{ pendingCount }}</small></RouterLink>
        <RouterLink class="stage" :class="{ active: activeStage === 'shots' || activeStage === 'storyboards' || activeStage === 'clips' }" :to="stageRoute('shots')"><Clapperboard :size="16" /><span>Produce</span></RouterLink>
        <RouterLink class="stage" :class="{ active: activeStage === 'final-cut' }" :to="stageRoute('final-cut')"><Film :size="16" /><span>Assemble</span></RouterLink>
      </div>

      <div class="provider-panel">
        <div class="provider-title"><Bot :size="15" /><span>Text provider</span></div>
        <strong>{{ activeProvider?.model ?? 'qwen3:8b' }}</strong>
        <span class="provider-state">
          <span class="status-dot" :class="{ muted: !activeProvider?.available }"></span>
          {{ activeProvider?.available ? 'Connected' : 'Not connected' }}
        </span>
        <button @click="store.currentProject ? openProjectSettings() : (showCreate = true)">Configure</button>
      </div>
    </aside>

    <section class="workspace" :class="{ 'final-cut-stage': activeStage === 'final-cut' }">
      <div v-if="store.error" class="error-banner"><CircleAlert :size="15" />{{ store.error }}</div>
      <div v-if="flowSyncNotice" class="info-banner">
        <div><Check :size="15" /><span>{{ flowSyncNotice }}</span></div>
        <button class="mini-icon" aria-label="Dismiss" @click="flowSyncNotice = ''"><X :size="14" /></button>
      </div>
      <div v-if="store.draftAttention.length" class="error-banner">
        <CircleAlert :size="15" />
        <span>Needs attention: {{ store.draftAttention.map((item) => item.message).slice(0, 4).join(' · ') }}</span>
        <button
          v-if="isScriptApproved"
          type="button"
          class="secondary-button"
          :disabled="store.creatingDraft"
          @click="triggerCreateDraft(true)"
        >
          Retry failed steps
        </button>
      </div>
      <header class="project-header">
        <div class="project-header-left">
          <div class="breadcrumb">
            <span class="breadcrumb-root">Projects</span>
            <ChevronRight :size="13" class="breadcrumb-separator" />
            <button
              type="button"
              class="project-title-link"
              @click="openProjectSettings"
              :title="'Project Settings: ' + projectTitle"
            >
              <Film :size="13" class="project-title-icon" />
              <span class="project-title-text">{{ projectTitle }}</span>
            </button>
            <ChevronRight :size="13" class="breadcrumb-separator" />
            <EpisodeSwitcher
              @open-settings="openProjectSettings"
              @open-new-episode="openNewEpisodeModal"
              @open-compilation-modal="openCompilationModal('episode_shots')"
              @select-compilation="handleOpenCompilation"
            />
            <button
              type="button"
              class="compilations-header-pill"
              :class="{ 'is-active': store.isCompilationMode }"
              title="Manage Compilations & Short Cuts"
              @click="openCompilationModal('episode_shots')"
            >
              <Scissors v-if="store.isCompilationMode && (store.activeCompilation?.episode_id || store.activeCompilation?.shot_ids?.length)" :size="13" />
              <Boxes v-else :size="13" />
              <span>Compilations</span>
              <span v-if="store.compilations.length" class="compilations-count-pill">{{ store.compilations.length }}</span>
            </button>
            <button
              v-if="store.currentProject?.unresolved_note_count"
              type="button"
              class="project-notes-header-pill"
              :title="`${store.currentProject.unresolved_note_count} unresolved shot note${store.currentProject.unresolved_note_count === 1 ? '' : 's'} in this project. Click to jump to the first unresolved shot.`"
              @click="jumpToFirstUnresolvedShot"
            >
              <MessageSquare :size="12" />
              <span>{{ store.currentProject.unresolved_note_count }} note{{ store.currentProject.unresolved_note_count === 1 ? '' : 's' }}</span>
            </button>
          </div>
          <h1 class="project-stage-heading">{{ stageTitle }}</h1>
        </div>
        <div class="header-actions">
          <span v-if="store.backgroundActiveJobsCount > 0" class="background-jobs-badge" title="Active generations running in other projects">
            <RefreshCw :size="12" class="spinning" />
            {{ store.backgroundActiveJobsCount }} background {{ store.backgroundActiveJobsCount === 1 ? 'job' : 'jobs' }}
          </span>
          <span v-if="store.currentProject" class="save-state"><Check :size="14" /> Saved</span>
          <button
            v-if="store.currentProject?.current_version"
            type="button"
            class="secondary-button version-history-trigger"
            title="Browse and restore saved script versions"
            @click="showScriptHistory = true"
          >
            <History :size="15" />
            <span>Version history</span>
            <span class="version-pill-mini">v{{ store.currentProject.current_version.version_number }}</span>
          </button>
          <button class="secondary-button format-guide-trigger" title="Script format guide" @click="showScriptGuide = true"><BookOpen :size="15" /><span>Script format</span></button>
          <button class="secondary-button preview-trigger" @click="openAnimaticPreview"><Play :size="15" fill="currentColor" /> Preview</button>
          <button
            v-if="isScriptApproved"
            class="primary-button"
            :disabled="store.creatingDraft"
            :title="store.draftJob?.message || 'Generate missing images, clips, dialogue, and a preview'"
            @click="triggerCreateDraft()"
          >
            <Sparkles :size="15" />
            {{ store.creatingDraft ? (store.draftJob?.message || 'Creating draft…') : 'Create draft video' }}
          </button>
          <button v-if="activeStage === 'script'" class="primary-button" :disabled="isScriptApproved || store.approvingScript" @click="approveAndOpenShots">
            <Check v-if="isScriptApproved" :size="16" />
            {{ store.approvingScript ? 'Approving...' : isScriptApproved ? 'Script approved' : 'Approve script' }}
            <ChevronRight v-if="!isScriptApproved" :size="16" />
          </button>
        </div>
      </header>

      <div v-if="activeStage !== 'final-cut'" class="progress-strip">
        <div class="progress-step complete"><span><Check :size="13" /></span><strong>Imported</strong></div>
        <div class="progress-line complete"></div>
        <div class="progress-step" :class="progressClass(2)"><span><Check v-if="currentProgressStep > 2" :size="13" /><template v-else>2</template></span><strong>Review</strong></div>
        <div class="progress-line" :class="{ complete: currentProgressStep > 2 }"></div>
        <div class="progress-step" :class="progressClass(3)"><span><Check v-if="currentProgressStep > 3" :size="13" /><template v-else>3</template></span><strong>Produce</strong></div>
        <div class="progress-line" :class="{ complete: currentProgressStep > 3 }"></div>
        <div class="progress-step" :class="progressClass(4)"><span>4</span><strong>Assemble</strong></div>
      </div>

      <div v-if="activeStage === 'script'" class="review-grid">
        <aside class="scene-panel">
          <div class="panel-heading">
            <div><span class="section-label">Scenes</span><strong>{{ scenes.length }} {{ scenes.length === 1 ? 'scene' : 'scenes' }} · actual {{ projectDuration }} · target {{ projectTargetDuration }}</strong></div>
            <button class="mini-icon" title="Continue script" aria-label="Continue script" :disabled="!isScriptApproved" @click="showContinuation = true"><Plus :size="15" /></button>
          </div>
          <button
            v-for="(scene, index) in scenes"
            :key="scene.id"
            class="scene-row"
            :class="{ active: activeScene === index }"
            @click="selectScene(index)"
          >
            <span class="scene-number">{{ String(index + 1).padStart(2, '0') }}</span>
            <span class="scene-info"><strong>{{ scene.title }}</strong><small>{{ sceneTime(scene) }} · {{ shotCountLabel(scene.shots.length) }}</small></span>
            <span class="scene-status" :class="{ ready: index > 0 || !pendingCount }">{{ index === 0 && pendingCount ? 'Needs review' : 'Ready' }}</span>
          </button>
        </aside>

        <section v-if="selectedScene" class="script-panel">
          <div class="script-toolbar">
            <div>
              <span class="eyebrow">Scene {{ String(activeScene + 1).padStart(2, '0') }}</span>
              <h2>{{ selectedScene.title }}</h2>
            </div>
            <div class="script-toolbar-right">
              <button
                type="button"
                class="view-original-script-btn"
                title="Browse and restore saved script versions"
                @click="showScriptHistory = true"
              >
                <History :size="14" />
                <span>Version history</span>
                <span v-if="store.currentProject?.current_version?.version_number" class="version-pill-mini">
                  v{{ store.currentProject.current_version.version_number }}
                </span>
              </button>
              <button
                type="button"
                class="view-original-script-btn"
                title="View original screenplay text"
                @click="showOriginalScriptModal = true"
              >
                <FileText :size="14" />
                <span>Original Script</span>
              </button>
              <div class="time-chip"><Clock3 :size="14" /> {{ sceneTime(selectedScene) }}</div>
            </div>
          </div>

          <article :id="selectedScene.shots.length === 1 && selectedScene.shots[0] ? `review-shot-${selectedScene.shots[0].id}` : undefined" class="screenplay">
            <template v-if="selectedScene.shots.length > 1">
              <section v-for="(shot, shotIndex) in selectedScene.shots" :key="shot.id" :id="`review-shot-${shot.id}`" class="screenplay-shot">
                <header><span>Shot {{ String(shotIndex + 1).padStart(2, '0') }}</span><strong>{{ shot.title }}</strong><small>{{ shotTime(shot) }}</small></header>
                <p class="action-text">{{ shot.action || 'No visual action has been provided for this shot.' }}</p>
                <div v-for="line in shot.dialogue" :key="`${shot.id}-${line.character}-${line.text}`" class="dialogue-block">
                  <strong>{{ line.character }}</strong>
                  <p>{{ line.text }}</p>
                </div>
                <p v-if="shot.sound" class="screenplay-sound"><strong>Sound</strong>{{ shot.sound }}</p>
              </section>
            </template>
            <template v-else>
              <p class="action-text">{{ selectedScene.action || 'No visual action has been provided for this scene.' }}</p>
              <div v-for="line in selectedScene.dialogue" :key="`${line.character}-${line.text}`" class="dialogue-block">
                <strong>{{ line.character }}</strong>
                <p>{{ line.text }}</p>
              </div>
            </template>
            <p v-if="activeScene === 0 && pendingCount" class="suggestion-insert"><Sparkles :size="15" /> Review the proposed production details before approving this scene.</p>
          </article>

          <footer class="scene-production">
            <div><span>Characters</span><strong>{{ sceneCharacters(selectedScene) }}</strong></div>
            <div><span>Location</span><strong>{{ selectedScene.location }}</strong></div>
            <div><span>Planned shots</span><strong>{{ shotCountLabel(selectedScene.shots.length) }}</strong></div>
          </footer>
        </section>

        <aside class="suggestions-panel">
          <div class="panel-heading suggestions-heading">
            <div><span class="section-label">AI review</span><strong>{{ pendingCount }} open suggestions</strong></div>
            <span class="review-badge readiness" :class="scriptReadiness.tone" :title="`${scriptReadiness.blocking} blocking, ${scriptReadiness.warnings} to polish`"><Sparkles :size="13" /> {{ scriptReadiness.label }} · {{ scriptReadiness.score }}</span>
          </div>

          <div class="suggestion-list">
            <article v-for="item in suggestions" :key="item.id" class="suggestion" :class="item.status">
              <div class="suggestion-title">
                <CircleAlert v-if="item.severity !== 'optional'" :size="16" />
                <Sparkles v-else :size="16" />
                <div><span>{{ item.severity }}<template v-if="isAdvisory(item)"> · {{ item.category }}</template></span><strong>{{ item.title }}</strong></div>
              </div>
              <p>{{ item.reason }}</p>
              <div class="proposed-change">{{ item.change }}</div>
              <div v-if="item.status === 'pending'" class="decision-row">
                <button class="reject-button" @click="decide(item.id, 'rejected')"><X :size="15" /> Reject</button>
                <button class="accept-button" @click="decide(item.id, 'accepted')"><Check :size="15" /> {{ isAdvisory(item) ? 'Acknowledge' : 'Accept' }}</button>
              </div>
              <div v-else class="decision-state" :class="item.status">
                <Check v-if="item.status === 'accepted'" :size="15" />
                <X v-else :size="15" />
                {{ item.status }}
              </div>
            </article>
            <div v-if="!suggestions.length" class="empty-review"><Check :size="20" /><strong>Review complete</strong><span>No open suggestions remain.</span></div>
          </div>

          <DeepReviewPanel
            v-if="store.currentProject?.current_version"
            :version-id="store.currentProject.current_version.id"
            :episode-id="store.currentEpisodeId"
            :refresh-key="deepReviewRefreshKey"
            @applied="handleDeepReviewApplied"
            @navigate="navigateToReviewShot"
            @jump="jumpToEpisodeShot"
          />
        </aside>
      </div>

      <div v-else-if="activeStage === 'shots' || activeStage === 'storyboards' || activeStage === 'clips'" class="produce-workspace">
        <section v-if="!produceDetailOpen" class="produce-story">
          <header class="produce-hero">
            <div>
              <span class="eyebrow">Tell the story</span>
              <h2>Scene by scene — Cutline fills in the rest</h2>
              <p>Review the beat, keep faces consistent, then generate the whole draft in one pass.</p>
            </div>
            <div class="produce-hero-actions">
              <div class="media-variant-switch" role="group" aria-label="Output aspect ratio">
                <button type="button" :class="{ active: mediaAspect === '16:9' }" @click="mediaAspect = '16:9'; generateBothAspects = false">16:9</button>
                <button type="button" :class="{ active: mediaAspect === '9:16' }" @click="mediaAspect = '9:16'; generateBothAspects = false">9:16</button>
                <button
                  type="button"
                  :class="{ active: generateBothAspects }"
                  title="When enabled, generation creates both 16:9 and 9:16 aspect ratios"
                  @click="generateBothAspects = !generateBothAspects"
                >Both (16:9 + 9:16)</button>
              </div>
              <button
                v-if="store.currentProject?.current_version"
                class="secondary-button"
                type="button"
                title="Browse and restore saved script versions"
                @click="showScriptHistory = true"
              >
                <History :size="15" />
                <span>Version history</span>
              </button>
              <button class="secondary-button" type="button" @click="openAnimaticPreview"><Play :size="15" fill="currentColor" /> Preview</button>
              <button
                class="primary-button"
                type="button"
                :disabled="!isScriptApproved || store.creatingDraft"
                :title="store.draftJob?.message || 'Generate missing images, clips, dialogue, and a preview'"
                @click="triggerCreateDraft()"
              >
                <Sparkles :size="15" />
                {{ store.creatingDraft ? (store.draftJob?.message || 'Creating draft…') : (generateBothAspects ? 'Create draft (16:9 + 9:16)' : 'Create draft video') }}
              </button>
            </div>
          </header>

          <div v-if="store.creatingDraft || store.draftJob" class="produce-draft-banner" :class="store.draftJob?.status">
            <RefreshCw v-if="store.creatingDraft || store.draftJob?.status === 'running' || store.draftJob?.status === 'queued'" class="spinning" :size="16" />
            <Check v-else-if="store.draftJob?.status === 'completed'" :size="16" />
            <CircleAlert v-else :size="16" />
            <div>
              <strong>{{ store.draftJob?.message || 'Preparing draft…' }}</strong>
              <span>{{ mediaReadyCount }}/{{ allShots.length }} shots have media · {{ clipCount }} clips ready</span>
              <div v-if="scenes.length" class="produce-scene-progress">
                <button
                  v-for="(scene, sceneIndex) in scenes"
                  :key="scene.id"
                  type="button"
                  class="produce-scene-pill"
                  :class="sceneDraftStatus(scene)"
                  :title="`${scene.title}: ${sceneClipReadyCount(scene)}/${scene.shots.length} clips`"
                  @click="activeShotId = scene.shots[0]?.id || activeShotId"
                >{{ sceneIndex + 1 }}</button>
              </div>
            </div>
            <div class="produce-draft-banner-actions">
              <button
                v-if="store.draftJob && (store.creatingDraft || store.draftJob.status === 'running' || store.draftJob.status === 'queued')"
                type="button"
                class="secondary-button"
                :disabled="store.isCancellingJob(store.draftJob.id)"
                @click="store.cancelJob(store.draftJob.id)"
              >
                {{ store.isCancellingJob(store.draftJob.id) ? 'Cancelling…' : 'Cancel draft' }}
              </button>
              <button
                v-if="store.draftAttention.length"
                type="button"
                class="secondary-button"
                :disabled="store.creatingDraft"
                @click="triggerCreateDraft(true)"
              >Retry failed</button>
            </div>
          </div>

          <section class="produce-cast-strip">
            <header>
              <div class="elements-library-header">
                <div>
                  <span class="section-label">Elements Library · @References</span>
                  <strong>Characters (Main + 3D View), Locations / Sets &amp; Props</strong>
                </div>
                <div class="elements-tab-bar" role="tablist" aria-label="Reference element categories">
                  <button
                    type="button"
                    role="tab"
                    class="element-tab-btn kind-character"
                    :class="{ active: activeElementTab === 'character' }"
                    :aria-selected="activeElementTab === 'character'"
                    @click="activeElementTab = 'character'"
                  >
                    <span class="element-dot kind-character"></span>
                    <span>Characters</span>
                    <small>{{ characterElements.length }}</small>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    class="element-tab-btn kind-scene"
                    title="Reusable location references, not screenplay scene count"
                    :class="{ active: activeElementTab === 'scene' }"
                    :aria-selected="activeElementTab === 'scene'"
                    @click="activeElementTab = 'scene'"
                  >
                    <span class="element-dot kind-scene"></span>
                    <span>Locations / Sets</span>
                    <small>{{ sceneElements.length }}</small>
                  </button>
                  <button
                    type="button"
                    role="tab"
                    class="element-tab-btn kind-prop"
                    :class="{ active: activeElementTab === 'prop' }"
                    :aria-selected="activeElementTab === 'prop'"
                    @click="activeElementTab = 'prop'"
                  >
                    <span class="element-dot kind-prop"></span>
                    <span>Props</span>
                    <small>{{ propElements.length }}</small>
                  </button>
                </div>
              </div>
              <div class="cast-header-actions">
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="castingBusy || !visibleLibraryElements.some((character) => !character.references.length) || !imageGenerationAvailable"
                  :title="!imageGenerationAvailable ? 'Image provider offline — start with ./scripts/dev --bridges' : `Generate missing ${activeElementTab} references`"
                  @click="generateMissingCastFaces"
                >
                  <Sparkles :size="15" />
                  {{ activeElementTab === 'character' ? 'Generate missing faces' : activeElementTab === 'scene' ? 'Generate missing sets' : 'Generate missing props' }}
                </button>
                <button class="secondary-button" type="button" :disabled="castingBusy" @click="syncCastFromScript">
                  Sync from script
                </button>
                <form class="cast-add" @submit.prevent="addCastCharacter">
                  <input
                    v-model="newCharacterName"
                    type="text"
                    maxlength="120"
                    :placeholder="activeElementTab === 'character' ? 'Add character' : activeElementTab === 'scene' ? 'Add location / set' : 'Add prop'"
                    :disabled="castingBusy"
                  />
                  <button class="secondary-button" type="submit" :disabled="castingBusy || !newCharacterName.trim()"><Plus :size="15" />Add</button>
                </form>
              </div>
            </header>
            <div v-if="!visibleLibraryElements.length" class="cast-empty">
              <template v-if="activeElementTab === 'character'">
                Sync cast from the script, then generate Main View + 3D View turnaround sheets. Type <code>@CHARACTER</code> in any shot prompt to lock identity.
              </template>
              <template v-else-if="activeElementTab === 'scene'">
                Add each physical space separately, such as Compound, Parlour, or Kitchen. Generate or upload its reference, then choose it under Reusable set for each scene.
              </template>
              <template v-else>
                Click <strong>Sync from script</strong> to extract recurring props from continuity notes, or add a Prop reference on a white studio background with <code>@PROP</code>.
              </template>
            </div>
            <div v-else class="produce-cast-row">
              <article
                v-for="character in visibleLibraryElements"
                :key="character.id"
                class="produce-cast-card"
                :class="`kind-${character.kind || 'character'}`"
                @click="openCastManager(character.id)"
              >
                <div class="produce-cast-face">
                  <img
                    v-if="elementPrimaryReference(character)"
                    :src="elementPrimaryReference(character)!.url"
                    :alt="character.name"
                  />
                  <RefreshCw v-else-if="store.isGeneratingCast(character.id)" :size="16" class="spinning" />
                  <span v-else>{{ character.name.slice(0, 1) }}</span>
                  <span
                    v-if="elementHas3DView(character)"
                    class="element-3d-corner-badge"
                    title="16:9 Character 3D Turnaround Sheet ready"
                  >3D</span>
                </div>
                <div class="produce-cast-meta">
                  <strong>{{ character.name }}</strong>
                  <span class="element-tag-pill" :class="`kind-${character.kind || 'character'}`">@{{ elementTag(character) }}</span>
                </div>
                <small>
                  {{
                    store.isGeneratingCast(character.id)
                      ? 'Generating ref…'
                      : elementHas3DView(character)
                        ? `${character.references.length} refs · 3D ready`
                        : character.references.length
                          ? `${character.references.length} ref${character.references.length === 1 ? '' : 's'}`
                          : activeElementTab === 'character'
                            ? 'needs face'
                            : 'needs ref'
                  }}
                </small>
                <div class="produce-cast-card-actions" @click.stop>
                  <button type="button" class="ghost-link" @click="openCastManager(character.id)">
                    Manage
                  </button>
                  <button
                    v-if="(character.kind || 'character') === 'character' && character.references.length && !elementHas3DView(character)"
                    type="button"
                    class="ghost-link accent-3d-link"
                    :disabled="castingBusy || store.isGeneratingCast(character.id) || !imageGenerationAvailable"
                    title="Generate Step 2: 16:9 Character 3D Turnaround Sheet (front, side, back + close-up)"
                    @click="generateElement3DView(character)"
                  >
                    + 3D View
                  </button>
                  <button
                    v-else
                    type="button"
                    class="ghost-link"
                    :disabled="castingBusy || store.isGeneratingCast(character.id) || !imageGenerationAvailable"
                    :title="!imageGenerationAvailable ? 'Image provider offline — start with ./scripts/dev --bridges' : `Generate reference for ${character.name}`"
                    @click="generateCastFace(character)"
                  >
                    {{ store.isGeneratingCast(character.id) ? 'Generating…' : character.references.length ? 'Add ref' : 'Generate' }}
                  </button>
                </div>
              </article>
            </div>
            <input ref="castRefInput" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" @change="handleCastReferenceUpload" />
          </section>

          <div v-if="scenes.length" class="produce-groups">
            <article v-for="(scene, sceneIndex) in scenes" :key="scene.id" class="produce-group">
              <header class="produce-group-head">
                <div>
                  <span class="eyebrow">Shot Group {{ sceneIndex + 1 }}</span>
                  <h3>{{ scene.title }}</h3>
                  <p class="produce-group-sub">{{ scene.location }}{{ scene.time_of_day ? ` · ${scene.time_of_day}` : '' }}</p>
                </div>
                <div class="produce-group-meta">
                  <span>Duration: {{ sceneDurationLabel(scene) }}</span>
                  <span>{{ sceneClipReadyCount(scene) }}/{{ scene.shots.length }} clips · {{ sceneReadyCount(scene) }}/{{ scene.shots.length }} ready</span>
                  <span class="produce-scene-status" :class="sceneDraftStatus(scene)">{{ sceneDraftStatus(scene) === 'ready' ? 'Draft ready' : sceneDraftStatus(scene) === 'in_progress' ? 'Drafting…' : 'Not started' }}</span>
                  <span v-if="continuityFilledCount(scene)" class="continuity-pill">{{ continuityFilledCount(scene) }} continuity notes</span>
                  <button
                    type="button"
                    class="secondary-button"
                    :disabled="!scene.shots[0]"
                    @click="scene.shots[0] && openProduceDetail(scene.shots[0].id)"
                  >Edit shot</button>
                </div>
              </header>

              <section class="produce-continuity">
                <header>
                  <div>
                    <span class="section-label">Continuity</span>
                    <strong>Keep this scene visually consistent</strong>
                  </div>
                  <button
                    type="button"
                    class="secondary-button"
                    :disabled="isContinuitySaving(scene.id)"
                    @click="saveContinuity(scene)"
                  >
                    {{ isContinuitySaving(scene.id) ? 'Saving…' : 'Save notes' }}
                  </button>
                </header>
                <div class="continuity-grid">
                  <label>Reusable set
                    <select :value="sceneSet(scene)?.name ?? scene.location" :aria-label="`Reusable set for ${scene.title}`" :disabled="isContinuitySaving(scene.id)" @change="assignSceneSet(scene, $event)">
                      <option v-if="!sceneSet(scene)" :value="scene.location">{{ scene.location || 'Choose a set' }} · not linked</option>
                      <option v-for="element in sceneElements" :key="element.id" :value="element.name">{{ element.name }}{{ element.references.length ? '' : ' · reference needed' }}</option>
                    </select>
                    <span>{{ sceneSet(scene)?.references.length ? 'Set reference is attached automatically to new generations.' : 'Add or sync a set in Locations / Sets, then generate or upload its reference.' }}</span>
                  </label>
                  <label>Scene dressing / staging<textarea v-model="continuityDraft(scene).set_notes" rows="2" placeholder="Environment, architecture, weather…" @blur="saveContinuity(scene)"></textarea></label>
                  <label>Lighting<textarea v-model="continuityDraft(scene).lighting_notes" rows="2" placeholder="Key light, time of day mood…" @blur="saveContinuity(scene)"></textarea></label>
                  <label>Wardrobe<textarea v-model="continuityDraft(scene).wardrobe_notes" rows="2" placeholder="Outfits that must match across shots…" @blur="saveContinuity(scene)"></textarea></label>
                  <label>Props<textarea v-model="continuityDraft(scene).props_notes" rows="2" placeholder="Important objects that recur…" @blur="saveContinuity(scene)"></textarea></label>
                </div>
                <p class="continuity-hint">These notes are appended automatically when Cutline generates frames and clips for this scene.</p>
              </section>

              <div class="produce-group-body">
                <div class="produce-script">
                  <button
                    v-for="(shot, shotIndex) in scene.shots"
                    :key="shot.id"
                    :id="`produce-shot-${shot.id}`"
                    type="button"
                    class="produce-shot-row"
                    :class="{
                      active: selectedShot?.id === shot.id,
                      open: !shortsSelectionMode && isProduceShotExpanded(shot.id),
                      'is-short-selected': isShotSelectedForShort(shot.id),
                    }"
                    @click="shortsSelectionMode ? toggleShotForShort(shot.id) : toggleProduceShot(shot.id)"
                  >
                    <span
                      v-if="shortsSelectionMode"
                      class="produce-shot-select-check"
                      :class="{ checked: isShotSelectedForShort(shot.id) }"
                    >
                      <Check v-if="isShotSelectedForShort(shot.id)" :size="12" />
                    </span>
                    <span class="produce-shot-toggle">
                      <ChevronDown v-if="isProduceShotExpanded(shot.id)" :size="16" />
                      <ChevronRight v-else :size="16" />
                      <strong>Shot {{ shotIndex + 1 }}</strong>
                      <small>{{ shotTime(shot) }} · {{ shot.framing || 'Medium' }}</small>
                      <span
                        v-if="shot.unresolved_note_count"
                        class="shot-note-badge"
                        :title="`${shot.unresolved_note_count} unresolved note${shot.unresolved_note_count === 1 ? '' : 's'}. Click to view notes.`"
                        @click.stop="openShotNotes(shot)"
                      >
                        <MessageSquare :size="12" />{{ shot.unresolved_note_count }}
                      </span>
                      <Check v-if="shotMedia(shot, 'clip')" class="shot-ready-icon" :size="14" />
                      <Image v-else-if="shotMedia(shot, 'image')" class="shot-ready-icon muted" :size="14" />
                      <RefreshCw v-else-if="store.isProcessingShot(shot.id)" class="shot-processing-icon" :size="14" />
                    </span>
                    <div v-if="isProduceShotExpanded(shot.id)" class="produce-shot-copy" @click.stop>
                      <p>{{ shot.action || 'No visual action yet.' }}</p>
                      <div v-for="line in shot.dialogue" :key="`${shot.id}-${line.character}-${line.text}`" class="dialogue-block compact">
                        <strong>{{ line.character }}</strong>
                        <p>{{ line.text }}</p>
                      </div>
                      <p v-if="shot.sound" class="screenplay-sound"><strong>Sound</strong>{{ shot.sound }}</p>
                      <button type="button" class="ghost-link" @click="openProduceDetail(shot.id, { editPrompts: true })">Adjust prompts & regenerate →</button>
                    </div>
                  </button>
                  <button
                    type="button"
                    class="add-shot-row-button"
                    :disabled="addingShotSceneId === scene.id"
                    @click="handleAddShot(scene.id)"
                  >
                    <Plus :size="14" />
                    <span>{{ addingShotSceneId === scene.id ? 'Adding shot…' : 'Add shot' }}</span>
                  </button>
                </div>

                <div class="produce-preview">
                  <div class="produce-preview-frame" :class="{ portrait: mediaAspect === '9:16' }">
                    <video
                      v-if="scenePreviewClip(scene)"
                      :key="scenePreviewClip(scene)!.url"
                      :src="scenePreviewClip(scene)!.url"
                      controls
                      playsinline
                      preload="metadata"
                    ></video>
                    <img
                      v-else-if="scenePreviewImage(scene)"
                      :src="scenePreviewImage(scene)!.url"
                      :alt="scene.title"
                    />
                    <div v-else class="empty-storyboard">
                      <Clapperboard :size="28" />
                      <strong>Waiting for draft</strong>
                      <span>Create draft video to generate this scene’s frames and clips.</span>
                    </div>
                  </div>
                  <p class="produce-preview-blurb">{{ produceSceneBlurb(scene) }}</p>
                  <div class="produce-preview-actions">
                    <button
                      type="button"
                      class="primary-button"
                      :disabled="!scenePreviewShot(scene) || store.isProcessingShot(scenePreviewShot(scene)!.id) || !imageGenerationAvailable"
                      @click="selectShot(scenePreviewShot(scene)!.id); generateShotImage()"
                    >
                      <Sparkles :size="15" />
                      {{ scenePreviewImage(scene) ? 'Regenerate frame' : 'Generate frame' }}
                    </button>
                    <button type="button" class="secondary-button" :disabled="!scene.shots[0]" @click="scene.shots[0] && openProduceDetail(scene.shots[0].id)">Open shot</button>
                  </div>
                </div>
              </div>
            </article>
          </div>

          <div v-else class="empty-shot-plan"><ListChecks :size="28" /><strong>No scenes yet</strong><span>Approve a script to unlock Produce.</span></div>
        </section>

        <footer v-if="!produceDetailOpen && shortsSelectionMode" class="produce-dock shorts-selection-dock">
          <div class="shorts-dock-summary">
            <Scissors :size="16" class="shorts-dock-icon" />
            <strong>{{ selectedShortsSummary.count }} of {{ allShots.length }} shots selected</strong>
            <span>&middot; {{ selectedShortsSummary.durationFormatted }}</span>
            <span v-if="selectedShortsSummary.isShortsReady" class="shorts-dock-pill ready">✓ YouTube Shorts Ready (&lt;60s)</span>
            <span v-else-if="selectedShortsSummary.durationMs > 60000" class="shorts-dock-pill warn">Longer Short (&gt;60s)</span>
          </div>
          <div class="produce-dock-actions">
            <button type="button" class="secondary-button" @click="selectAllShotsForShort">Select all</button>
            <button v-if="selectedShotIdsForShort.length" type="button" class="secondary-button" @click="clearShotsForShort">Clear</button>
            <button
              type="button"
              class="primary-button"
              :disabled="!selectedShotIdsForShort.length"
              @click="handleCreateShortFromSelection"
            >
              <Sparkles :size="15" />
              <span>Create Short Cut ({{ selectedShotIdsForShort.length }})</span>
            </button>
            <button type="button" class="secondary-button" @click="toggleShortsSelectionMode">Cancel</button>
          </div>
        </footer>

        <footer v-else-if="!produceDetailOpen" class="produce-dock">
          <div>
            <strong>{{ allShots.length }} shots</strong>
            <span>{{ mediaReadyCount }} with media · {{ clipCount }} with video</span>
          </div>
          <div class="produce-dock-actions">
            <button type="button" class="secondary-button" title="Select shots for a YouTube Shorts cut" @click="toggleShortsSelectionMode">
              <Scissors :size="15" /> Create Short
            </button>
            <button type="button" class="secondary-button" @click="openAnimaticPreview"><Play :size="15" fill="currentColor" /> Preview</button>
            <button type="button" class="secondary-button" :disabled="!isScriptApproved" @click="router.push(stageRoute('final-cut'))"><Film :size="15" /> Assemble</button>
            <button
              type="button"
              class="primary-button"
              :disabled="!isScriptApproved || store.creatingDraft"
              @click="triggerCreateDraft()"
            >
              <Sparkles :size="15" />
              {{ store.creatingDraft ? 'Creating draft…' : 'Create draft video' }}
            </button>
          </div>
        </footer>

        <div v-if="produceDetailOpen" class="shot-plan-workspace produce-detail">
          <aside class="shot-browser">
            <div class="shot-browser-heading">
              <div class="shot-browser-heading-top">
                <button type="button" class="ghost-link back-to-story" @click="closeProduceDetail">
                  <ChevronRight class="back-chevron" :size="14" />
                  <span>Back to story</span>
                </button>
                <span class="shot-browser-ready-pill">
                  {{ allShots.filter((shot) => shotHasMedia(shot)).length }}/{{ allShots.length }} ready
                </span>
              </div>
              <div class="shot-browser-heading-main">
                <div class="shot-browser-title-group">
                  <span class="section-label">Shot list</span>
                  <strong>{{ allShots.length }} planned shots</strong>
                </div>
                <button
                  type="button"
                  class="ghost-link select-shorts-link"
                  :class="{ active: shortsSelectionMode }"
                  title="Select shots for a Short Cut"
                  @click="toggleShortsSelectionMode"
                >
                  <Scissors :size="13" />
                  <span>{{ shortsSelectionMode ? 'Cancel' : 'Create Short' }}</span>
                </button>
              </div>
            </div>
            <div class="shot-list">
              <section v-for="(scene, sceneIndex) in scenes" :key="scene.id" class="shot-scene-group">
                <header>
                  <strong>{{ String(sceneIndex + 1).padStart(2, '0') }} {{ scene.title }}</strong>
                  <div class="shot-scene-header-meta">
                    <span>{{ shotCountLabel(scene.shots.length) }}</span>
                    <button
                      type="button"
                      class="mini-add-shot-button"
                      title="Add shot to scene"
                      :disabled="addingShotSceneId === scene.id"
                      @click.stop="handleAddShot(scene.id)"
                    >
                      <Plus :size="13" />
                    </button>
                  </div>
                </header>
                <button
                  v-for="shot in scene.shots"
                  :key="shot.id"
                  :id="'browser-shot-' + shot.id"
                  class="shot-list-item"
                  :class="{
                    active: !shortsSelectionMode && selectedShot?.id === shot.id,
                    'is-short-selected': isShotSelectedForShort(shot.id),
                  }"
                  @click="shortsSelectionMode ? toggleShotForShort(shot.id) : selectShot(shot.id)"
                >
                  <span
                    v-if="shortsSelectionMode"
                    class="produce-shot-select-check"
                    :class="{ checked: isShotSelectedForShort(shot.id) }"
                  >
                    <Check v-if="isShotSelectedForShort(shot.id)" :size="12" />
                  </span>
                  <span class="shot-list-thumb">
                    <img v-if="shotThumbUrl(shot)" :src="shotThumbUrl(shot)!" alt="" />
                    <Image v-else :size="18" />
                  </span>
                  <span>
                    <div class="shot-list-title-row">
                      <strong>{{ shot.title || `Shot ${String(shot.order_index + 1).padStart(2, '0')}` }}</strong>
                      <span
                        v-if="shot.unresolved_note_count"
                        class="shot-note-badge shot-list-item-badge"
                        :title="`${shot.unresolved_note_count} unresolved note${shot.unresolved_note_count === 1 ? '' : 's'}. Click to view notes.`"
                        @click.stop="openShotNotes(shot)"
                      >
                        <MessageSquare :size="10" />{{ shot.unresolved_note_count }}
                      </span>
                    </div>
                    <small class="shot-list-meta" :title="`${shot.framing || 'Medium'} · ${(shot.duration_ms / 1000).toFixed(1)}s`">
                      <span class="shot-list-framing">{{ shot.framing || 'Medium' }}</span>
                      <span class="shot-list-duration">· {{ (shot.duration_ms / 1000).toFixed(1) }}s</span>
                    </small>
                    <small v-if="store.isProcessingShot(shot.id)" class="shot-job-copy">{{ imageJobForShot(shot.id)?.message || 'Starting image generation' }}</small>
                    <small v-else-if="store.isProcessingClip(shot.id)" class="shot-job-copy">{{ store.clipJobs[shot.id]?.message || 'Generating video' }}</small>
                  </span>
                  <RefreshCw v-if="store.isProcessingShot(shot.id) || store.isProcessingClip(shot.id)" class="shot-processing-icon spinning" :size="15" />
                  <Check v-else-if="shotHasMedia(shot)" class="shot-ready-icon" :size="15" />
                </button>
                <button
                  type="button"
                  class="sidebar-add-shot-btn"
                  title="Add shot to this scene"
                  :disabled="addingShotSceneId === scene.id"
                  @click="handleAddShot(scene.id)"
                >
                  <Plus :size="13" />
                  <span>{{ addingShotSceneId === scene.id ? 'Adding…' : 'Add shot' }}</span>
                </button>
              </section>
            </div>
          </aside>

          <section v-if="selectedShot" class="shot-editor">
            <header class="shot-editor-header">
              <div><span class="eyebrow">{{ selectedShotLabel }}</span><h2>{{ selectedShotScene?.title }}</h2></div>
              <div class="shot-header-right-group">
                <div class="media-variant-switch" role="group" aria-label="Storyboard image aspect ratio">
                  <button type="button" :class="{ active: mediaAspect === '16:9' }" @click="mediaAspect = '16:9'; generateBothAspects = false">
                    <span>16:9</span>
                    <RefreshCw v-if="selectedShot && isProcessingAspect(selectedShot.id, '16:9')" class="spinning aspect-spin" :size="11" />
                  </button>
                  <button type="button" :class="{ active: mediaAspect === '9:16' }" @click="mediaAspect = '9:16'; generateBothAspects = false">
                    <span>9:16 Shorts</span>
                    <RefreshCw v-if="selectedShot && isProcessingAspect(selectedShot.id, '9:16')" class="spinning aspect-spin" :size="11" />
                  </button>
                  <button
                    type="button"
                    :class="{ active: generateBothAspects }"
                    title="When enabled, generating image or video creates both 16:9 and 9:16 aspect ratios"
                    @click="generateBothAspects = !generateBothAspects"
                  ><span>Both (16:9 + 9:16)</span></button>
                </div>
                <span class="image-source" :class="{ ready: selectedAspectImage || selectedAspectReference }">{{ selectedAspectImage ? selectedAspectImage.source.replace('_', ' ') : selectedAspectReference ? 'continuity frame' : `No ${mediaAspect} image` }}</span>
                <span v-if="(selectedAspectClip as any)?.isAdapted && shouldDisplayClip" class="adapted-clip-badge" :title="`Using video adapted from ${(selectedAspectClip as any)?.adaptedFrom}. You can generate native ${mediaAspect} video anytime.`">
                  Video adapted from {{ (selectedAspectClip as any)?.adaptedFrom }}
                </span>
                <button
                  v-if="(selectedAspectClip as any)?.isAdapted && (selectedAspectImage || selectedAspectReference)"
                  type="button"
                  class="adapted-preview-pill"
                  :title="showStillImageInstead ? `Play video adapted from ${(selectedAspectClip as any)?.adaptedFrom}` : 'View the still storyboard frame for this shot'"
                  @click="showStillImageInstead = !showStillImageInstead"
                >
                  <component :is="showStillImageInstead ? Play : Image" :size="10" />
                  <span>{{ showStillImageInstead ? `Play ${(selectedAspectClip as any)?.adaptedFrom} Video` : 'Show Still Frame' }}</span>
                </button>
                <a
                  v-if="selectedShotConversationUrl"
                  :href="selectedShotConversationUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="chat-session-badge"
                  title="Open AI chat session in ChatGPT"
                >
                  <MessageSquare :size="12" />
                  <span>Chat session</span>
                  <ExternalLink :size="10" />
                </a>
                <button v-if="canDeleteChat" type="button" class="secondary-button" :disabled="deletingChat" title="Remove linked chat from Cutline (keeps chat in ChatGPT/Gemini)" @click="beginDeleteChat"><Trash2 :size="14" />Delete chat</button>
                <button
                  type="button"
                  class="shot-notes-header-btn"
                  :class="{
                    'has-unresolved': (selectedShot.unresolved_note_count ?? 0) > 0,
                    'has-notes': (selectedShot.notes?.length ?? 0) > 0,
                    active: showShotNotesDrawer,
                  }"
                  :title="selectedShot.unresolved_note_count ? `${selectedShot.unresolved_note_count} unresolved note(s) for this shot` : ((selectedShot.notes?.length ?? 0) > 0 ? `${selectedShot.notes?.length} note(s) for this shot` : 'Shot notes and reminders')"
                  @click="showShotNotesDrawer = !showShotNotesDrawer"
                >
                  <MessageSquareText :size="14" />
                  <span>Notes</span>
                  <span
                    v-if="selectedShot.unresolved_note_count"
                    class="shot-notes-header-badge unresolved"
                  >
                    {{ selectedShot.unresolved_note_count }}
                  </span>
                  <span
                    v-else-if="selectedShot.notes?.length"
                    class="shot-notes-header-badge"
                  >
                    {{ selectedShot.notes.length }}
                  </span>
                </button>
                <button
                  v-if="selectedShotScene"
                  type="button"
                  class="add-shot-header-btn"
                  title="Add a new shot directly after this shot"
                  :disabled="addingShotSceneId === selectedShotScene.id"
                  @click="handleAddShot(selectedShotScene.id, selectedShot.id)"
                >
                  <Plus :size="14" />
                  <span>{{ addingShotSceneId === selectedShotScene.id ? 'Adding…' : '+ Add shot' }}</span>
                </button>
                <button
                  type="button"
                  class="delete-shot-header-btn"
                  title="Delete shot"
                  @click="deleteShotCandidate = selectedShot"
                >
                  <Trash2 :size="14" />
                  <span>Delete shot</span>
                </button>
              </div>
            </header>

            <div class="storyboard-area">
              <div class="storyboard-frame" :class="{ portrait: mediaAspect === '9:16' }">
                <video
                  v-if="shouldDisplayClip && selectedAspectClip"
                  ref="storyboardVideo"
                  :key="selectedAspectClip.url"
                  :src="selectedAspectClip.url"
                  :style="currentClipBoostStrength ? { filter: videoColorBoostFilter(currentClipBoostStrength) } : undefined"
                  controls
                  playsinline
                  preload="metadata"
                ></video>
                <img
                  v-else-if="selectedAspectImage"
                  :src="selectedAspectImage.url"
                  :alt="`${mediaAspect} storyboard for ${selectedShotLabel}`"
                />
                <img
                  v-else-if="selectedAspectReference"
                  :src="selectedAspectReference.url"
                  :alt="`${mediaAspect} reference frame for ${selectedShotLabel}`"
                />
                <div v-else-if="canExpandFromOtherAspect && otherAspectMedia" class="cross-aspect-placeholder">
                  <img :src="otherAspectMedia.url" class="cross-aspect-ghost" :alt="`${otherAspect} reference available`" />
                  <div class="cross-aspect-badge-overlay">
                    <span class="cross-aspect-tag">{{ otherAspect }} reference ready</span>
                    <strong>Keep this scene consistent</strong>
                    <p>Expand from this approved {{ otherAspect }} frame into {{ mediaAspect }} using your prompt, or generate a fresh image.</p>
                  </div>
                </div>
                <div v-else class="empty-storyboard"><Image :size="30" /><strong>No {{ mediaAspect }} media yet</strong><span>Generate a frame, then a clip — or run Create draft from the story view.</span></div>
                <div
                  v-if="shouldDisplayClip && (selectedAspectClip as any)?.isAdapted"
                  class="adapted-overlay-badge"
                  :title="`Using video adapted from ${(selectedAspectClip as any)?.adaptedFrom}`"
                >
                  Video adapted from {{ (selectedAspectClip as any)?.adaptedFrom }}
                </div>
                <div v-if="isProcessingActiveStoryboard" class="render-overlay">
                  <RefreshCw class="spinning" :size="24" />
                  <strong>{{ activeStoryboardProcessingMessage }}</strong>
                </div>
              </div>

              <div class="cast-generate-chips">
                <div class="video-mode-header-row">
                  <div>
                    <span class="section-label">Elements &amp; Video Mode</span>
                    <small class="video-mode-sub">
                      {{
                        videoGenerationMode === 'r2v'
                          ? 'Direct Reference-to-Video (Omni Flash · up to 7 Character 3D/Main, Scene & Prop refs — no still frame required)'
                          : 'Frame-to-Video (animates the rendered storyboard frame)'
                      }}
                    </small>
                  </div>
                  <div class="media-variant-switch video-mode-switch" role="group" aria-label="Video generation mode">
                    <button
                      type="button"
                      :class="{ active: videoGenerationMode === 'i2v' }"
                      title="Animate from a rendered storyboard frame (Image-to-Video)"
                      @click="videoGenerationMode = 'i2v'"
                    >
                      Frame-to-Video (I2V)
                    </button>
                    <button
                      type="button"
                      :class="{ active: videoGenerationMode === 'r2v' }"
                      title="Generate video directly from up to 7 @Character 3D/Main, @Scene, and @Prop reference images"
                      @click="videoGenerationMode = 'r2v'; showPromptEditor = true"
                    >
                      Reference-to-Video (R2V)
                    </button>
                  </div>
                </div>

                <p v-for="warning in selectedShot?.continuity?.cast_warnings ?? []" :key="warning" class="form-error" role="status">{{ warning }}</p>
                <div v-if="castCharacters.length" class="cast-chip-row">
                  <button
                    v-for="character in visibleCastCharacters"
                    :key="character.id"
                    type="button"
                    class="cast-chip"
                    :class="[
                      `kind-${character.kind || 'character'}`,
                      { active: selectedCharacterIds.includes(character.id), warn: !character.references.length },
                    ]"
                    :disabled="store.isProcessingShot(selectedShot.id)"
                    @click="toggleCharacterForGeneration(character.id)"
                  >
                    <span class="element-dot" :class="`kind-${character.kind || 'character'}`"></span>
                    <span>@{{ elementTag(character) }}</span>
                    <span v-if="elementHas3DView(character)" class="chip-3d-tag">3D</span>
                    <small v-if="!character.references.length">no ref</small>
                  </button>

                  <button
                    v-if="hasHiddenCastChips || castChipsExpanded"
                    type="button"
                    class="cast-chip-toggle-btn"
                    :title="castChipsExpanded ? 'Collapse element list' : `Show all ${sortedCastCharacters.length} elements`"
                    @click="castChipsExpanded = !castChipsExpanded"
                  >
                    <component :is="castChipsExpanded ? ChevronUp : ChevronDown" :size="13" />
                    <span>{{ castChipsExpanded ? 'Show less' : `+${hiddenCastChipCount} more` }}</span>
                  </button>
                </div>

                <div v-if="videoGenerationMode === 'r2v'" class="r2v-elements-tray">
                  <div class="r2v-tray-head">
                    <strong>Attached R2V References ({{ r2vReadyReferenceCount }}/7 ready)</strong>
                    <label class="r2v-scene-check">
                      <input v-model="includeSceneRefInR2V" type="checkbox" />
                      <span>Auto-attach scene location ref</span>
                    </label>
                  </div>
                  <div v-if="r2vAttachedElements.length" class="r2v-tray-grid">
                    <div
                      v-for="(el, idx) in r2vAttachedElements"
                      :key="el.id"
                      class="r2v-tray-card"
                      :class="`kind-${el.kind || 'character'}`"
                      @click="openCastManager(el.id)"
                    >
                      <div class="r2v-tray-thumb">
                        <img v-if="elementPrimaryReference(el)" :src="elementPrimaryReference(el)!.url" :alt="el.name" />
                        <span v-else class="r2v-missing-thumb">No ref</span>
                        <span class="r2v-ref-index">Ref {{ idx + 1 }}</span>
                        <span v-if="elementHas3DView(el)" class="r2v-3d-pill">3D Sheet</span>
                      </div>
                      <div class="r2v-tray-meta">
                        <span class="element-tag-pill" :class="`kind-${el.kind || 'character'}`">@{{ elementTag(el) }}</span>
                        <small>{{ el.name }}</small>
                      </div>
                    </div>
                  </div>
                  <p v-else class="r2v-tray-empty">
                    Select elements above or type <code>@CHARACTER</code>, <code>@SCENE</code>, or <code>@PROP</code> in the prompt below to attach up to 7 references.
                  </p>
                </div>

                <label
                  v-else-if="selectedCharacterIds.length"
                  class="video-cast-ref-toggle"
                  title="Default (unchecked) locks Frame 1 strictly. Check this when a tagged character enters mid-shot and is not visible in the opening frame so Cutline also sends their Cast portrait image to Google Flow."
                >
                  <input
                    :checked="includeCastRefsInVideo"
                    type="checkbox"
                    :disabled="store.isProcessingClip(selectedShot.id)"
                    @change="handleToggleCastRefs"
                  />
                  <span>Also send cast portrait(s) when generating video (for off-screen entrances)</span>
                </label>
              </div>

              <div class="storyboard-actions">
                <template v-if="canExpandFromOtherAspect">
                  <button
                    class="primary-button"
                    :disabled="store.isProcessingShot(selectedShot.id) || !imageGenerationAvailable"
                    :title="`Expand the approved ${otherAspect} frame into ${mediaAspect} using this shot's prompt and scene continuity`"
                    @click="beginExpandFromOtherAspect"
                  >
                    <Maximize2 :size="15" />
                    Expand from {{ otherAspect }}
                  </button>
                  <button
                    class="secondary-button"
                    :disabled="store.isProcessingShot(selectedShot.id) || !imageGenerationAvailable"
                    :title="`Generate a completely new ${mediaAspect} image from prompt from scratch without using the ${otherAspect} frame`"
                    @click="beginGenerateFreshImage"
                  >
                    <Sparkles :size="15" />
                    Generate fresh (from prompt)
                  </button>
                  <button
                    class="secondary-button"
                    :disabled="store.isProcessingShot(selectedShot.id) || !imageGenerationAvailable"
                    title="Generate both 16:9 and 9:16 storyboard images in one pass"
                    @click="beginRegenerateBothImages"
                  >
                    <Sparkles :size="15" />
                    Generate 16:9 + 9:16 images
                  </button>
                  <button
                    class="primary-button"
                    :disabled="store.isProcessingShot(selectedShot.id) || store.isProcessingClip(selectedShot.id) || !imageGenerationAvailable || !store.videoProvider?.configured || !store.videoProvider?.available"
                    :title="`Expand from ${otherAspect} and automatically animate video clip on Google Flow`"
                    @click="beginExpandAndGenerateClip"
                  >
                    <Clapperboard :size="15" />
                    Expand from {{ otherAspect }} + video
                  </button>
                </template>
                <template v-else>
                  <button class="primary-button" :disabled="store.isProcessingShot(selectedShot.id) || !imageGenerationAvailable" @click="beginRegenerateImage">
                    <RefreshCw v-if="selectedAspectImage" :size="15" /><Sparkles v-else :size="15" />
                    {{ generateBothAspects ? (selectedAspectImage ? 'Regenerate 16:9 + 9:16 images' : 'Generate 16:9 + 9:16 images') : (selectedAspectImage ? `Regenerate ${mediaAspect} image` : `Generate ${mediaAspect} image`) }}
                  </button>
                  <button
                    v-if="!generateBothAspects"
                    class="secondary-button"
                    :disabled="store.isProcessingShot(selectedShot.id) || !imageGenerationAvailable"
                    title="Generate both 16:9 and 9:16 storyboard images in one pass (generates the active dimension first, then converts the other)"
                    @click="beginRegenerateBothImages"
                  >
                    <Sparkles :size="15" />
                    Generate 16:9 + 9:16 images
                  </button>
                  <button
                    class="primary-button"
                    :disabled="store.isProcessingShot(selectedShot.id) || store.isProcessingClip(selectedShot.id) || !imageGenerationAvailable || !store.videoProvider?.configured || !store.videoProvider?.available"
                    :title="!imageGenerationAvailable ? 'Image provider is offline' : (!store.videoProvider?.configured || !store.videoProvider?.available) ? (store.videoProvider?.detail || 'Video provider is offline') : `Generate storyboard image(s) and automatically animate video clip(s) with ${store.videoProvider?.label || 'video provider'}`"
                    @click="beginRegenerateImageAndClip"
                  >
                    <Clapperboard :size="15" />
                    {{ generateBothAspects ? 'Generate 16:9 + 9:16 image + video' : `Generate ${mediaAspect} image + video` }}
                  </button>
                  <button
                    v-if="!selectedAspectImage && otherAspectMedia"
                    class="secondary-button"
                    :disabled="store.isProcessingShot(selectedShot.id) || !imageGenerationAvailable"
                    :title="`Expand from the approved ${otherAspect} frame instead of using this ${mediaAspect} reference`"
                    @click="beginExpandFromOtherAspect"
                  >
                    <Maximize2 :size="15" />
                    Expand from {{ otherAspect }} instead
                  </button>
                </template>
                <button
                  v-if="generateBothAspects"
                  class="primary-button"
                  :disabled="store.isProcessingClip(selectedShot.id) || !canGenerateClipNow || !store.videoProvider?.configured || !store.videoProvider?.available"
                  :title="!canGenerateClipNow ? (videoGenerationMode === 'r2v' ? 'Attach at least 1 element with a reference image first' : `Generate or upload a ${mediaAspect} image or reference frame first`) : (!store.videoProvider?.configured || !store.videoProvider?.available) ? (store.videoProvider?.detail || 'Video provider is offline') : `Generate ${mediaAspect} video only`"
                  @click="beginRegenerateClip(false)"
                >
                  <Video :size="15" />
                  {{ hasNativeAspectClip ? `Regenerate ${mediaAspect} video` : `Generate ${mediaAspect} video` }}
                </button>
                <button
                  class="primary-button"
                  :class="{ 'r2v-generate-btn': videoGenerationMode === 'r2v' }"
                  :disabled="store.isProcessingClip(selectedShot.id) || !canGenerateClipNow || !store.videoProvider?.configured || !store.videoProvider?.available"
                  :title="!canGenerateClipNow ? (videoGenerationMode === 'r2v' ? 'Attach at least 1 element with a reference image first' : `Generate or upload a ${mediaAspect} image or reference frame first`) : (!store.videoProvider?.configured || !store.videoProvider?.available) ? (store.videoProvider?.detail || 'Video provider is offline') : undefined"
                  @click="beginRegenerateClip(generateBothAspects)"
                >
                  <Video :size="15" />
                  {{
                    videoGenerationMode === 'r2v'
                      ? (hasNativeAspectClip ? `Regenerate R2V video (${r2vReadyReferenceCount} refs)` : `Generate R2V video (${r2vReadyReferenceCount} refs)`)
                      : (generateBothAspects ? (hasNativeAspectClip ? 'Regenerate 16:9 + 9:16 videos' : 'Generate 16:9 + 9:16 videos') : (hasNativeAspectClip ? 'Regenerate video' : `Generate ${mediaAspect} video`))
                  }}
                </button>
                <select
                  v-if="store.videoProvider?.id === 'flowkit'"
                  class="flow-model-inline-select"
                  :value="store.videoProvider?.active_preset || 'veo_lite'"
                  :disabled="switchingFlowModel || store.isProcessingClip(selectedShot.id)"
                  :title="store.videoProvider?.detail || 'Select Google Flow video model'"
                  aria-label="Google Flow video model"
                  @change="changeFlowModelFromEditor"
                >
                  <option
                    v-for="preset in (store.videoProvider?.presets?.length ? store.videoProvider.presets : DEFAULT_FLOW_PRESETS)"
                    :key="preset.id"
                    :value="preset.id"
                  >
                    {{ preset.short_label }}
                  </option>
                </select>
                <button
                  v-if="store.videoProvider?.id === 'flowkit' && store.videoProvider?.configured"
                  type="button"
                  class="secondary-button"
                  :disabled="resettingFlow"
                  title="Switched Google Flow accounts in your browser? Click to re-sync FlowKit session."
                  @click="syncFlowAccount"
                >
                  <RefreshCw :class="{ spinning: resettingFlow }" :size="14" />
                  {{ resettingFlow ? 'Syncing Flow…' : 'Sync Flow' }}
                </button>
                <a
                  v-if="selectedShotConversationUrl"
                  :href="selectedShotConversationUrl"
                  target="_blank"
                  rel="noopener noreferrer"
                  class="secondary-button chat-session-btn"
                  title="Open the AI chat session that created this shot in ChatGPT"
                >
                  <MessageSquare :size="14" />
                  <span>Chat session</span>
                  <ExternalLink :size="12" />
                </a>
                <button v-if="canDeleteChat" type="button" class="secondary-button" :disabled="deletingChat" title="Remove linked chat from Cutline (keeps chat in ChatGPT/Gemini)" @click="beginDeleteChat"><Trash2 :size="14" />Delete chat</button>
                <button
                  v-if="selectedShot.dialogue?.length"
                  class="secondary-button lipsync-toolbar-button"
                  :disabled="store.isProcessingLipsync(selectedShot.id) || (!selectedAspectImage && !selectedAspectClip)"
                  title="Synchronize character lips and facial performance with dialogue audio"
                  @click="generateShotLipsync"
                >
                  <RefreshCw v-if="store.isProcessingLipsync(selectedShot.id)" class="spinning" :size="14" />
                  <Mic v-else :size="14" />
                  {{ store.isProcessingLipsync(selectedShot.id) ? 'Lip-syncing…' : 'Lip-sync' }}
                </button>
                <button class="secondary-button" type="button" @click="showPromptEditor = !showPromptEditor">
                  {{ showPromptEditor ? 'Hide prompts' : 'Adjust prompts' }}
                </button>
                <button class="secondary-button" :disabled="store.isProcessingShot(selectedShot.id)" @click="chooseUpload"><Upload :size="15" />Upload image</button>
                <button class="secondary-button" :disabled="store.isProcessingClip(selectedShot.id)" @click="chooseClipUpload"><Upload :size="15" />Upload video</button>
                <div
                  v-if="selectedAspectClip && (hasNativeAspectClip || shouldDisplayClip)"
                  class="clip-color-controls"
                  role="group"
                  aria-label="Video color boost controls"
                >
                  <button
                    type="button"
                    class="secondary-button clip-retouch-btn"
                    :disabled="retouchingClipColor || store.isProcessingClip(selectedShot.id) || currentClipBoostPercent === 100"
                    :title="clipBoostButtonTitle"
                    @click="runClipColorAction()"
                  >
                    <RefreshCw v-if="retouchingClipColor" :size="14" class="spinning" />
                    <Palette v-else :size="14" />
                    <span>{{ retouchingClipColor ? 'Adjusting…' : 'Boost Video Color' }}</span>
                    <span v-if="clipBoostBadgeLabel" class="clip-boost-pill">{{ clipBoostBadgeLabel }}</span>
                  </button>
                  <button
                    type="button"
                    class="secondary-button clip-color-step-btn"
                    :disabled="retouchingClipColor || store.isProcessingClip(selectedShot.id) || currentClipBoostStrength <= -1"
                    aria-label="Reduce video color boost"
                    title="Reduce video color boost (-25%)"
                    @click="runClipColorAction('decrease')"
                  >
                    <Minus :size="13" />
                  </button>
                  <button
                    type="button"
                    class="secondary-button clip-color-step-btn"
                    :disabled="retouchingClipColor || store.isProcessingClip(selectedShot.id) || currentClipBoostStrength >= 3"
                    aria-label="Increase video color boost"
                    title="Increase video color boost (+25%)"
                    @click="runClipColorAction('increase')"
                  >
                    <Plus :size="13" />
                  </button>
                  <button
                    type="button"
                    class="secondary-button clip-color-step-btn"
                    :disabled="retouchingClipColor || store.isProcessingClip(selectedShot.id) || !canUndoClipColor"
                    aria-label="Undo video color change"
                    title="Undo video color change"
                    @click="runClipColorAction('undo')"
                  >
                    <Undo2 :size="13" />
                  </button>
                  <button
                    type="button"
                    class="secondary-button clip-color-step-btn"
                    :disabled="retouchingClipColor || store.isProcessingClip(selectedShot.id) || !canRedoClipColor"
                    aria-label="Redo video color change"
                    title="Redo video color change"
                    @click="runClipColorAction('redo')"
                  >
                    <Redo2 :size="13" />
                  </button>
                  <button
                    v-if="currentClipBoostPercent !== 0"
                    type="button"
                    class="secondary-button clip-color-step-btn"
                    :disabled="retouchingClipColor || store.isProcessingClip(selectedShot.id)"
                    aria-label="Reset video color boost"
                    title="Reset video color boost to original (0%)"
                    @click="runClipColorAction('reset')"
                  >
                    <RotateCcw :size="13" />
                  </button>
                </div>
                <button
                  v-if="selectedAspectImage"
                  class="secondary-button destructive-action-button"
                  :title="`Delete ${mediaAspect} image`"
                  :aria-label="`Delete ${mediaAspect} shot image`"
                  @click="deleteShotImageCandidate = true"
                >
                  <Trash2 :size="15" />
                  Delete {{ mediaAspect }} image
                </button>
                <button
                  v-if="hasNativeAspectClip"
                  class="secondary-button destructive-action-button"
                  title="Delete video"
                  aria-label="Delete shot video"
                  @click="deleteShotClipCandidate = true"
                >
                  <Trash2 :size="15" />
                  Delete video
                </button>
                <input ref="uploadInput" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" @change="handleUpload" />
                <input ref="clipUploadInput" class="visually-hidden" type="file" accept="video/mp4,video/webm,video/quicktime,.mov" @change="handleClipUpload" />
              </div>
            </div>

            <section v-if="selectedShot.dialogue.length" class="narration-section">
              <div class="narration-heading">
                <div><span class="section-label">Dialogue & Voice</span><small>Generate character voice and synchronize mouth movements.</small></div>
                <div class="narration-actions">
                  <button class="secondary-button" type="button" :disabled="store.processingAudioId === selectedShot.id" @click="generateShotAudio">
                    <RefreshCw v-if="selectedAudioUrl" :size="15" /><Sparkles v-else :size="15" />
                    {{ store.processingAudioId === selectedShot.id ? 'Generating…' : selectedAudioUrl ? 'Regenerate voice' : 'Generate voice' }}
                  </button>
                  <button
                    class="primary-button lipsync-button"
                    type="button"
                    :disabled="store.isProcessingLipsync(selectedShot.id) || (!selectedAspectImage && !selectedAspectClip)"
                    title="Synchronize character facial performance and lips with the dialogue audio track"
                    @click="generateShotLipsync"
                  >
                    <RefreshCw v-if="store.isProcessingLipsync(selectedShot.id)" class="spinning" :size="15" />
                    <Mic v-else :size="15" />
                    <span>{{ store.isProcessingLipsync(selectedShot.id) ? (store.lipsyncJobs[selectedShot.id]?.message || 'Lip-syncing…') : 'Lip-Sync Dialogue' }}</span>
                  </button>
                </div>
              </div>
              <audio v-if="selectedAudioUrl" :key="selectedAudioUrl" :src="selectedAudioUrl" controls preload="metadata"></audio>
            </section>

            <ShotTakesPanel
              v-if="selectedShot"
              :shot="selectedShot"
              :aspect="mediaAspect"
              @play-take="handlePlaySelectedTake"
            />

            <ImageReferencePanel
              v-if="selectedShot"
              :shot="selectedShot"
              :aspect="mediaAspect"
              :previous-shot="previousShot"
              :project-aspect="(store.currentProject?.aspect_ratio as AspectRatio | undefined)"
              :can-reference-earlier="canReferenceEarlier"
              v-model:skip-default="skipDefaultImageReference"
              v-model:lock-blocking="lockSceneBlocking"
              v-model:focal-character-id="focalCharacterId"
              @generate-locked-blocking="generateLockedBlockingImage"
            />

            <form class="shot-fields" @submit.prevent="saveShot">
              <div class="shot-field-row">
                <label>Framing<input v-model="shotDraft.framing" list="framing-options" /><datalist id="framing-options"><option value="Wide" /><option value="Medium" /><option value="Close-up" /><option value="Extreme close-up" /><option value="Over-the-shoulder" /><option value="Insert" /></datalist></label>
                <label>Duration (seconds)<input type="number" min="1" max="30" step="0.5" :value="shotDraft.duration_ms / 1000" @input="setShotDuration" /></label>
              </div>
              <label v-if="selectedShotScene">Reusable set
                <select :value="sceneSet(selectedShotScene)?.name ?? selectedShotScene.location" aria-label="Reusable set for this scene" :disabled="isContinuitySaving(selectedShotScene.id)" @change="assignSceneSet(selectedShotScene, $event)">
                  <option v-if="!sceneSet(selectedShotScene)" :value="selectedShotScene.location">{{ selectedShotScene.location || 'Choose a set' }} · not linked</option>
                  <option v-for="element in sceneElements" :key="element.id" :value="element.name">{{ element.name }}{{ element.references.length ? '' : ' · reference needed' }}</option>
                </select>
                <span>{{ sceneSet(selectedShotScene)?.references.length ? 'Uses this set for every shot in the scene.' : 'Generate or upload this set reference in Locations / Sets.' }}</span>
              </label>
              <p class="shot-action-blurb">{{ selectedShot.action || 'No action line on this shot.' }}</p>
              <div v-if="!showPromptEditor" class="prompt-collapsed">
                <div>
                  <strong>Prompts stay out of the way</strong>
                  <span>Cutline uses the saved image/video prompts plus scene continuity. Open them only when you need a regenerate tweak or <code>@ELEMENT</code> mention.</span>
                </div>
                <button type="button" class="secondary-button" @click="showPromptEditor = true">Adjust prompts</button>
              </div>
              <template v-else>
                <div v-if="selectedShotConversationUrl" class="chat-session-callout">
                  <div class="chat-session-callout-header">
                    <MessageSquare :size="15" />
                    <strong>AI Chat Session Active</strong>
                  </div>
                  <p>This shot was generated in a dedicated provider conversation. You can review prompt variations or chat directly:</p>
                  <a :href="selectedShotConversationUrl" target="_blank" rel="noopener noreferrer" class="chat-session-callout-link">
                    <span>{{ selectedShotConversationUrl }}</span>
                    <ExternalLink :size="13" />
                  </a>
                </div>
                <div class="combined-prompt-heading">
                  <div><strong>Google Flow video prompt</strong><span>Copy-ready prompt combining this shot, cast, and scene continuity.</span></div>
                  <button class="secondary-button" type="button" @click="copyShotVideoPrompt"><Check v-if="copiedShotPrompt" :size="14" /><Copy v-else :size="14" />{{ copiedShotPrompt ? 'Copied' : 'Copy prompt' }}</button>
                </div>
                <textarea class="combined-prompt" :value="selectedShotVideoPrompt" rows="13" readonly aria-label="Copy-ready Google Flow video prompt"></textarea>

                <div v-if="castCharacters.length" class="mention-quick-bar">
                  <span class="mention-quick-label">Insert <code>@</code> reference in prompt:</span>
                  <div class="mention-quick-pills">
                    <button
                      v-for="el in castCharacters"
                      :key="el.id"
                      type="button"
                      class="element-tag-pill clickable"
                      :class="`kind-${el.kind || 'character'}`"
                      :title="`Insert @${elementTag(el)} into video prompt and attach reference`"
                      @click="insertElementMention(el, 'video_prompt')"
                    >
                      <span>@{{ elementTag(el) }}</span>
                      <small v-if="elementHas3DView(el)">3D</small>
                    </button>
                  </div>
                </div>

                <div class="prompt-field-with-mentions">
                  <label>
                    <span>Image prompt <small class="mention-hint">(type <code>@</code> to reference Characters, Locations / Sets, or Props)</small></span>
                    <textarea
                      v-model="shotDraft.image_prompt"
                      rows="5"
                      required
                      @input="handlePromptTextareaInput('image_prompt', $event)"
                      @keydown="handlePromptTextareaKeydown('image_prompt', $event)"
                    ></textarea>
                  </label>
                  <div v-if="mentionField === 'image_prompt' && mentionSuggestions.length" class="mention-autocomplete-popover" role="listbox">
                    <button
                      v-for="(item, idx) in mentionSuggestions"
                      :key="item.id"
                      type="button"
                      class="mention-option"
                      :class="{ active: idx === mentionActiveIdx }"
                      @mousedown.prevent="insertElementMention(item, 'image_prompt')"
                    >
                      <span class="element-tag-pill" :class="`kind-${item.kind || 'character'}`">@{{ elementTag(item) }}</span>
                      <strong>{{ item.name }}</strong>
                      <small>{{ item.kind || 'character' }}{{ elementHas3DView(item) ? ' · 3D ready' : '' }}</small>
                    </button>
                  </div>
                </div>

                <div class="prompt-field-with-mentions">
                  <label>
                    <span>Video prompt <small class="mention-hint">(type <code>@</code> to bind Character 3D/Main, Scene, or Prop refs)</small></span>
                    <textarea
                      v-model="shotDraft.video_prompt"
                      rows="3"
                      @input="handlePromptTextareaInput('video_prompt', $event)"
                      @keydown="handlePromptTextareaKeydown('video_prompt', $event)"
                    ></textarea>
                  </label>
                  <div v-if="mentionField === 'video_prompt' && mentionSuggestions.length" class="mention-autocomplete-popover" role="listbox">
                    <button
                      v-for="(item, idx) in mentionSuggestions"
                      :key="item.id"
                      type="button"
                      class="mention-option"
                      :class="{ active: idx === mentionActiveIdx }"
                      @mousedown.prevent="insertElementMention(item, 'video_prompt')"
                    >
                      <span class="element-tag-pill" :class="`kind-${item.kind || 'character'}`">@{{ elementTag(item) }}</span>
                      <strong>{{ item.name }}</strong>
                      <small>{{ item.kind || 'character' }}{{ elementHas3DView(item) ? ' · 3D ready' : '' }}</small>
                    </button>
                  </div>
                </div>

                <div v-if="detectedPromptMentionElements.length" class="detected-mentions-bar">
                  <span>Active <code>@</code> prompt bindings:</span>
                  <span
                    v-for="el in detectedPromptMentionElements"
                    :key="el.id"
                    class="element-tag-pill"
                    :class="`kind-${el.kind || 'character'}`"
                  >
                    @{{ elementTag(el) }} ({{ el.name }})
                  </span>
                </div>

                <label>Title-card text (optional)<textarea v-model="shotDraft.title_card_text" rows="2" placeholder="Rendered over black during the final two seconds."></textarea></label>
              </template>
              <footer>
                <span>{{ selectedAspectClip ? ((selectedAspectClip as any)?.isAdapted ? `Clip adapted (${(selectedAspectClip as any)?.adaptedFrom})` : 'Clip ready') : selectedAspectImage ? `${selectedAspectImage.provider || 'image'} · ${mediaAspect}` : imageGenerationAvailable ? `${imageProviderOrder} · ${mediaAspect}` : 'Upload available' }}</span>
                <button class="secondary-button"><Save :size="15" />Save shot</button>
              </footer>
            </form>
          </section>

          <div v-else class="empty-shot-plan"><ListChecks :size="28" /><strong>No shots were created</strong><span>Return to script review and add shot prompts before continuing.</span></div>
        </div>
      </div>

      <TimelineEditor
        v-else-if="activeStage === 'final-cut' && store.currentProject"
        :project="store.currentProject"
        :shots="activeTimelineShots"
        :compilation-id="store.activeCompilationId || undefined"
        @variant-change="setFinalCutVariant"
        @open-compilation-modal="(id) => openCompilationModal('episode_shots', null, [], id)"
      />

      <section v-else class="stage-placeholder">
        <Film :size="30" />
        <strong>{{ stageTitle }}</strong>
        <span>No assembled cut yet.</span>
      </section>
    </section>
  </main>

  <div v-if="showCreate" class="modal-backdrop" @pointerdown="trackCreateBackdropPress" @click="closeCreateFromBackdrop">
    <form class="project-modal" @submit.prevent="submitProject">
      <header><div><span class="section-label">New project</span><h2>Import screenplay</h2></div><button type="button" class="mini-icon" aria-label="Close" @click="showCreate = false"><X :size="17" /></button></header>
      <button type="button" class="secondary-button" :disabled="store.busy" @click="loadDemoProject">{{ store.busy ? 'Loading…' : 'Load demo project' }}</button>
      <p>Explore Jasmine &amp; Skylar: 16 planned shots, cast, and continuity notes. Images and clips are optional downloads.</p>
      <label>Project title<input v-model="form.title" required /></label>
      <div class="form-grid">
        <label>Target length (optional)<select v-model="form.target_duration_seconds"><option :value="0">Automatic from script</option><option :value="30">30 seconds</option><option :value="60">1 minute</option><option :value="180">3 minutes</option><option :value="300">5 minutes</option></select><small>Planning guidance only. Supplied scenes and timestamps are preserved.</small></label>
        <label>Aspect ratio<select v-model="form.aspect_ratio"><option value="16:9">Landscape 16:9</option><option value="9:16">Portrait 9:16</option></select></label>
      </div>
      <label>Text provider<select v-model="form.text_provider"><option v-for="provider in store.providers" :key="provider.id" :value="provider.id" :disabled="!provider.available">{{ provider.label }} · {{ provider.model }}{{ provider.available ? '' : ' (offline)' }}</option><option v-if="!store.providers.length" value="mock">Prototype parser</option></select></label>
      <div class="screenplay-input">
        <div class="field-heading">
          <label for="screenplay-source">Screenplay</label>
          <div class="field-actions">
            <button type="button" class="field-action-button" @click="openScreenplayFilePicker('create')">
              <Upload :size="13" />
              <span>Import file</span>
            </button>
            <button type="button" class="field-action-button" @click="showScriptGuide = true">
              <BookOpen :size="13" />
              <span>Format guide</span>
            </button>
          </div>
        </div>
        <div
          class="screenplay-dropzone"
          :class="{ 'is-dragging': isDraggingCreate }"
          @dragover.prevent="isDraggingCreate = true"
          @dragleave.prevent="isDraggingCreate = false"
          @drop.prevent="handleScreenplayDrop($event, 'create')"
        >
          <textarea
            id="screenplay-source"
            v-model="form.script"
            rows="13"
            required
            spellcheck="false"
            placeholder="Type, paste, or drop a screenplay file (.txt, .md, .fountain)..."
          ></textarea>
          <div v-if="createFileName" class="loaded-file-bar">
            <div class="loaded-file-info">
              <FileText :size="13" />
              <span>Loaded: <strong>{{ createFileName }}</strong></span>
            </div>
            <button type="button" class="loaded-file-clear" title="Clear file" @click="clearLoadedFile('create')">
              <X :size="13" />
            </button>
          </div>
          <div v-if="isDraggingCreate" class="dropzone-overlay">
            <Upload :size="24" />
            <span>Drop screenplay file here (.txt, .md, .fountain)</span>
          </div>
        </div>
        <input
          ref="createFileInput"
          type="file"
          accept=".txt,.md,.markdown,.fountain,.screenplay,text/plain,text/markdown"
          style="display: none"
          @change="handleScreenplayFileSelected($event, 'create')"
        />
      </div>
        <div
          v-if="store.currentJob && store.busy"
          class="job-progress"
          :class="{ indeterminate: store.currentJob.status === 'running' && store.currentJob.progress < 0.5 }"
        >
          <span :style="{ width: `${store.currentJob.progress * 100}%` }"></span>
          <small>{{ store.currentJob.message }} · {{ formatElapsed(store.jobElapsedSeconds) }}</small>
          <button
            type="button"
            class="job-cancel-button"
            :disabled="store.isCancellingJob(store.currentJob.id)"
            @click="store.cancelJob(store.currentJob.id)"
          >{{ store.isCancellingJob(store.currentJob.id) ? 'Cancelling…' : 'Cancel' }}</button>
        </div>
      <p v-if="store.error" class="form-error">{{ store.error }}</p>
      <footer><button type="button" class="secondary-button" @click="showCreate = false">Cancel</button><button class="primary-button" :disabled="store.busy"><Sparkles :size="15" />{{ store.busy ? 'Analyzing...' : 'Analyze script' }}</button></footer>
    </form>
  </div>

  <div v-if="showScriptGuide" class="modal-backdrop guide-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => showScriptGuide = false)">
    <section class="guide-modal" role="dialog" aria-modal="true" aria-labelledby="script-guide-title">
      <header>
        <div><span class="section-label">Reference</span><h2 id="script-guide-title">Structured script format</h2></div>
        <button type="button" class="mini-icon guide-close-btn" aria-label="Close script format guide" title="Close" @click="showScriptGuide = false"><X :size="18" /></button>
      </header>
      <div class="guide-summary">
        <p>Traditional screenplays are supported, but this format preserves every supplied shot and timecode before AI review.</p>
        <div><strong>Recognized fields</strong><code>SHOT N - TITLE</code><code>Scene:</code><code>Location:</code><code>Set:</code><code>Lighting:</code><code>Wardrobe:</code><code>Props:</code><code>Time of day:</code><code>Time:</code><code>Framing:</code><code>Cast:</code><code>Action:</code><code>Dialogue:</code><code>Sound:</code><code>Image prompt:</code><code>Video prompt:</code></div>
      </div>
      <div class="guide-prompt-heading">
        <div><span class="section-label">Reusable prompt</span><strong>Give this to your script generator</strong></div>
        <button class="secondary-button guide-copy-button" :class="{ copied: scriptGuideCopied }" type="button" @click="copyScriptGuide">
          <Check v-if="scriptGuideCopied" :size="14" />
          <Copy v-else :size="14" />
          <span>{{ scriptGuideCopied ? 'Copied' : 'Copy prompt' }}</span>
        </button>
      </div>
      <pre>{{ SCRIPT_GENERATION_PROMPT }}</pre>
    </section>
  </div>

  <!-- New Episode / Part Modal -->
  <div v-if="showNewEpisodeModal" class="modal-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => !store.creatingEpisode && (showNewEpisodeModal = false))">
    <form class="project-modal new-episode-modal" @submit.prevent="handleCreateEpisodeSubmit">
      <header>
        <div>
          <span class="section-label">Add {{ episodeLabel }}</span>
          <h2>New {{ episodeLabel }} / Part</h2>
        </div>
        <button type="button" class="mini-icon" aria-label="Close" :disabled="store.creatingEpisode" @click="showNewEpisodeModal = false">
          <X :size="17" />
        </button>
      </header>

      <div class="field">
        <label for="new-episode-title">{{ episodeLabel }} Title</label>
        <input
          id="new-episode-title"
          v-model="newEpisodeForm.title"
          type="text"
          :placeholder="`${episodeLabel} ${(store.episodes.length || 0) + 1} title...`"
          required
        />
      </div>

      <div class="screenplay-input">
        <div class="screenplay-input-header">
          <label for="new-episode-script">Screenplay</label>
          <div class="field-actions">
            <button type="button" class="field-action-button" @click="openScreenplayFilePicker('episode')">
              <Upload :size="13" />
              <span>Import file</span>
            </button>
            <button type="button" class="field-action-button" @click="showScriptGuide = true">
              <BookOpen :size="13" />
              <span>Guide</span>
            </button>
          </div>
        </div>

        <div
          class="screenplay-dropzone"
          :class="{ 'is-dragging': isDraggingEpisodeFile, 'has-file': Boolean(episodeFileName) }"
          @dragover.prevent="isDraggingEpisodeFile = true"
          @dragleave.prevent="isDraggingEpisodeFile = false"
          @drop.prevent="handleScreenplayDrop($event, 'episode')"
        >
          <textarea
            id="new-episode-script"
            v-model="newEpisodeForm.script"
            rows="10"
            :placeholder="`Paste ${episodeLabel.toLowerCase()} screenplay or drop file (.txt, .md, .fountain)...`"
            required
          ></textarea>
          <div v-if="episodeFileName" class="screenplay-file-pill">
            <FileText :size="13" />
            <span>{{ episodeFileName }}</span>
            <button type="button" class="pill-remove" @click="clearLoadedFile('episode')"><X :size="11" /></button>
          </div>
        </div>
        <input
          ref="episodeFileInput"
          type="file"
          accept=".txt,.md,.markdown,.fountain,.screenplay,text/plain,text/markdown"
          style="display: none"
          @change="handleScreenplayFileSelected($event, 'episode')"
        />
      </div>

      <div class="modal-row">
        <div class="field">
          <label for="new-episode-duration">Target duration (seconds)</label>
          <input id="new-episode-duration" v-model.number="newEpisodeForm.target_duration_seconds" type="number" min="10" max="600" step="5" />
        </div>
      </div>

      <footer>
        <button type="button" class="secondary-button" :disabled="store.creatingEpisode" @click="showNewEpisodeModal = false">Cancel</button>
        <button type="submit" class="primary-button" :disabled="store.creatingEpisode || !newEpisodeForm.script.trim() || !newEpisodeForm.title.trim()">
          <Sparkles v-if="!store.creatingEpisode" :size="15" />
          <span>{{ store.creatingEpisode ? `Creating ${episodeLabel}...` : `Create ${episodeLabel}` }}</span>
        </button>
      </footer>
    </form>
  </div>

  <!-- Project Settings / Label Modal -->
  <OriginalScriptModal
    :show="showOriginalScriptModal"
    :source-text="originalScriptText"
    :episode-title="currentEpisodeDisplay"
    @close="showOriginalScriptModal = false"
  />
  <ScriptHistoryModal v-if="store.currentProject?.current_version"
    :show="showScriptHistory" :version-id="store.currentProject.current_version.id"
    :episode-title="currentEpisodeDisplay" @close="showScriptHistory = false"
    @restored="store.loadProject(store.currentProject.id, { resumeJobs: false })" />
  <CompilationManagerModal
    :show="showCompilationModal"
    :initial-type="compilationModalInitialType"
    :initial-episode-id="compilationModalInitialEpisodeId"
    :initial-shot-ids="compilationModalInitialShotIds"
    :initial-compilation-id="compilationModalInitialCompilationId"
    @close="showCompilationModal = false"
    @open-compilation="handleOpenCompilation"
  />

  <div v-if="showProjectSettingsModal" class="modal-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => !store.updatingProject && (showProjectSettingsModal = false))">
    <form class="project-modal project-settings-modal" @submit.prevent="handleSaveProjectSettings">
      <header>
        <div>
          <span class="section-label">Project Settings</span>
          <h2>Project Naming & Structure</h2>
        </div>
        <button type="button" class="mini-icon" aria-label="Close" :disabled="store.updatingProject" @click="showProjectSettingsModal = false">
          <X :size="17" />
        </button>
      </header>

      <div class="field">
        <label for="project-settings-title">Project / Show Title</label>
        <input
          id="project-settings-title"
          v-model="projectSettingsForm.title"
          type="text"
          placeholder="Series or project title..."
          required
        />
      </div>

      <div class="form-grid">
        <label>
          Aspect ratio
          <select v-model="projectSettingsForm.aspect_ratio">
            <option value="16:9">Landscape 16:9</option>
            <option value="9:16">Portrait 9:16</option>
          </select>
        </label>
        <label>
          Target length (optional)
          <select v-model.number="projectSettingsForm.target_duration_seconds">
            <option :value="0">Automatic from script</option>
            <option :value="30">30 seconds</option>
            <option :value="60">1 minute</option>
            <option :value="180">3 minutes</option>
            <option :value="300">5 minutes</option>
          </select>
        </label>
      </div>

      <label>
        Text provider
        <select v-model="projectSettingsForm.text_provider">
          <option v-for="provider in store.providers" :key="provider.id" :value="provider.id" :disabled="!provider.available && provider.id !== projectSettingsForm.text_provider">
            {{ provider.label }} · {{ provider.model }}{{ provider.available ? '' : ' (offline)' }}
          </option>
          <option v-if="!store.providers.length" value="mock">Prototype parser</option>
        </select>
      </label>

      <div class="field">
        <label>Installment Naming Terminology</label>
        <p class="field-hint">Choose how chapters or segments in this project are labeled across Cutline.</p>
        <div class="label-options-grid">
          <button
            type="button"
            class="label-choice-card"
            :class="{ 'is-selected': projectSettingsForm.episode_label === 'Episode' }"
            @click="projectSettingsForm.episode_label = 'Episode'"
          >
            <strong>Episode</strong>
            <small>Episode 1, Episode 2, Episode 3</small>
          </button>
          <button
            type="button"
            class="label-choice-card"
            :class="{ 'is-selected': projectSettingsForm.episode_label === 'Part' }"
            @click="projectSettingsForm.episode_label = 'Part'"
          >
            <strong>Part</strong>
            <small>Part 1, Part 2, Part 3</small>
          </button>
          <button
            type="button"
            class="label-choice-card"
            :class="{ 'is-selected': projectSettingsForm.episode_label === 'Chapter' }"
            @click="projectSettingsForm.episode_label = 'Chapter'"
          >
            <strong>Chapter</strong>
            <small>Chapter 1, Chapter 2, Chapter 3</small>
          </button>
          <button
            type="button"
            class="label-choice-card"
            :class="{ 'is-selected': !['Episode', 'Part', 'Chapter'].includes(projectSettingsForm.episode_label) }"
            @click="projectSettingsForm.episode_label = 'Custom'"
          >
            <strong>Custom</strong>
            <small>Scene, Act, Segment...</small>
          </button>
        </div>
        <input
          v-if="!['Episode', 'Part', 'Chapter'].includes(projectSettingsForm.episode_label) || projectSettingsForm.episode_label === 'Custom'"
          v-model="projectSettingsForm.episode_label"
          type="text"
          class="custom-label-input"
          placeholder="Enter custom label (e.g. Scene, Act, Reel)..."
        />
      </div>

      <div v-if="store.episodes.length" class="field">
        <label>{{ projectSettingsForm.episode_label || 'Episode' }} Titles</label>
        <p class="field-hint">Update the title for each {{ (projectSettingsForm.episode_label || 'Episode').toLowerCase() }} in this project.</p>
        <div class="episode-settings-list">
          <div v-for="ep in store.episodes" :key="ep.id" class="episode-setting-item">
            <span class="episode-setting-badge">{{ projectSettingsForm.episode_label || 'Episode' }} {{ ep.episode_number }}</span>
            <input
              v-model="episodeTitlesForm[ep.id]"
              type="text"
              class="episode-setting-title-input"
              :placeholder="`${projectSettingsForm.episode_label || 'Episode'} ${ep.episode_number} title...`"
            />
          </div>
        </div>
      </div>

      <footer>
        <button type="button" class="secondary-button" :disabled="store.updatingProject" @click="showProjectSettingsModal = false">Cancel</button>
        <button type="submit" class="primary-button" :disabled="store.updatingProject || !projectSettingsForm.title.trim()">
          <span>{{ store.updatingProject ? 'Saving...' : 'Save Settings' }}</span>
        </button>
      </footer>
    </form>
  </div>

  <div v-if="showContinuation" class="modal-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => !store.continuingScript && (showContinuation = false))">
    <form class="project-modal continuation-modal" @submit.prevent="submitContinuation">
      <header><div><span class="section-label">Continue project</span><h2>Append screenplay</h2></div><button type="button" class="mini-icon" aria-label="Close" :disabled="store.continuingScript" @click="showContinuation = false"><X :size="17" /></button></header>
      <div class="continuation-summary"><span>Current cut</span><strong>{{ projectDuration }}</strong><span>Text provider</span><strong>{{ activeProvider?.label || store.currentProject?.text_provider }}</strong></div>
      <label>Planning target for this section<input v-model.number="continuationForm.target_duration_seconds" type="number" min="5" max="1800" step="5" required /><small>This guides scene timing but does not cap the finished movie.</small></label>
      <div class="screenplay-input">
        <div class="field-heading">
          <label for="continuation-source">New screenplay section</label>
          <div class="field-actions">
            <button type="button" class="field-action-button" @click="openScreenplayFilePicker('continue')">
              <Upload :size="13" />
              <span>Import file</span>
            </button>
            <button type="button" class="field-action-button" @click="showScriptGuide = true">
              <BookOpen :size="13" />
              <span>Format guide</span>
            </button>
          </div>
        </div>
        <div
          class="screenplay-dropzone"
          :class="{ 'is-dragging': isDraggingContinuation }"
          @dragover.prevent="isDraggingContinuation = true"
          @dragleave.prevent="isDraggingContinuation = false"
          @drop.prevent="handleScreenplayDrop($event, 'continue')"
        >
          <textarea
            id="continuation-source"
            v-model="continuationForm.script"
            rows="13"
            required
            spellcheck="false"
            placeholder="Paste or drop new scenes or shots that continue the story (.txt, .md, .fountain)..."
          ></textarea>
          <div v-if="continuationFileName" class="loaded-file-bar">
            <div class="loaded-file-info">
              <FileText :size="13" />
              <span>Loaded: <strong>{{ continuationFileName }}</strong></span>
            </div>
            <button type="button" class="loaded-file-clear" title="Clear file" @click="clearLoadedFile('continue')">
              <X :size="13" />
            </button>
          </div>
          <div v-if="isDraggingContinuation" class="dropzone-overlay">
            <Upload :size="24" />
            <span>Drop screenplay file here (.txt, .md, .fountain)</span>
          </div>
        </div>
        <input
          ref="continueFileInput"
          type="file"
          accept=".txt,.md,.markdown,.fountain,.screenplay,text/plain,text/markdown"
          style="display: none"
          @change="handleScreenplayFileSelected($event, 'continue')"
        />
      </div>
      <div v-if="store.currentJob && store.continuingScript" class="job-progress" :class="{ indeterminate: store.currentJob.status === 'running' && store.currentJob.progress < 0.5 }">
        <span :style="{ width: `${store.currentJob.progress * 100}%` }"></span>
        <small>{{ store.currentJob.message }} · {{ formatElapsed(store.jobElapsedSeconds) }}</small>
        <button
          type="button"
          class="job-cancel-button"
          :disabled="store.isCancellingJob(store.currentJob.id)"
          @click="store.cancelJob(store.currentJob.id)"
        >{{ store.isCancellingJob(store.currentJob.id) ? 'Cancelling…' : 'Cancel' }}</button>
      </div>
      <p v-if="store.error" class="form-error">{{ store.error }}</p>
      <footer><button type="button" class="secondary-button" :disabled="store.continuingScript" @click="showContinuation = false">Cancel</button><button class="primary-button" :disabled="store.continuingScript || !continuationForm.script.trim()"><Sparkles :size="15" />{{ store.continuingScript ? 'Analyzing...' : 'Analyze and append' }}</button></footer>
    </form>
  </div>

  <div v-if="showPreview" class="preview-backdrop" @click.self="closeAnimaticPreview">
    <section class="preview-modal" :class="{ portrait: previewAspect === '9:16' }" role="dialog" aria-modal="true" aria-labelledby="preview-title">
      <header>
        <div><span class="section-label">Storyboard animatic</span><h2 id="preview-title">{{ projectTitle }}</h2></div>
        <div class="preview-header-status"><span>{{ previewAspect }} preview · {{ allShots.filter((shot) => previewMedia(shot, 'clip')).length }} clips · {{ allShots.filter((shot) => previewMedia(shot, 'image')).length }}/{{ allShots.length }} frames</span><button class="preview-icon-button" aria-label="Close preview" @click="closeAnimaticPreview"><X :size="19" /></button></div>
      </header>

      <div class="preview-stage" :class="{ portrait: previewAspect === '9:16' }">
        <Transition name="preview-media">
          <div v-if="previewShot" :key="previewShot.id" class="preview-media">
            <video v-if="previewClip" :key="previewClip.url" ref="previewVideo" :src="previewClip.url" playsinline preload="auto" @loadedmetadata="syncPreviewVideo" @canplay="syncPreviewVideo"></video>
            <img v-else-if="previewImage" :src="previewImage.url" :alt="previewShot.title || 'Storyboard frame'" />
            <div v-else class="preview-missing"><Image :size="42" /><strong>Storyboard frame unavailable</strong><span>{{ previewShot.title || 'No shots in this project' }}</span></div>
          </div>
        </Transition>
        <video v-if="nextPreviewClip" class="preview-preload" :src="nextPreviewClip.url" playsinline muted preload="auto"></video>
        <img v-else-if="nextPreviewImage" class="preview-preload" :src="nextPreviewImage.url" alt="" />
        <div v-if="previewShot" class="preview-shot-caption"><span>Shot {{ previewShotIndex + 1 }} of {{ allShots.length }}</span><strong>{{ previewShot.title || selectedShotScene?.title }}</strong><small>{{ previewShot.framing }} · {{ (previewShot.duration_ms / 1000).toFixed(1) }}s</small></div>
      </div>

      <div class="preview-controls">
        <div class="preview-transport">
          <button class="preview-icon-button" aria-label="Previous shot" :disabled="previewShotIndex === 0" @click="selectPreviewShot(previewShotIndex - 1)"><SkipBack :size="18" fill="currentColor" /></button>
          <button class="preview-play-button" :aria-label="previewPlaying ? 'Pause preview' : 'Play preview'" :disabled="!previewShot" @click="togglePreviewPlayback"><Pause v-if="previewPlaying" :size="19" fill="currentColor" /><Play v-else :size="19" fill="currentColor" /></button>
          <button class="preview-icon-button" aria-label="Next shot" :disabled="previewShotIndex >= allShots.length - 1" @click="selectPreviewShot(previewShotIndex + 1)"><SkipForward :size="18" fill="currentColor" /></button>
        </div>
        <span class="preview-time">{{ formatTime(previewCurrentMs) }} / {{ formatTime(previewTotalMs) }}</span>
        <div class="preview-scrubber"><span :style="{ width: `${previewProgress}%` }"></span><input type="range" min="0" :max="previewTotalMs" step="50" :value="previewCurrentMs" aria-label="Preview timeline" @input="seekPreview" /></div>
      </div>

      <div class="preview-filmstrip">
        <button v-for="(shot, index) in allShots" :key="shot.id" :class="{ active: index === previewShotIndex, missing: !previewMedia(shot, 'image') }" :aria-label="`Open shot ${index + 1}`" @click="selectPreviewShot(index)">
          <span class="preview-thumb"><img v-if="previewMedia(shot, 'image')" :src="previewMedia(shot, 'image')!.url" alt="" /><Image v-else :size="17" /><Video v-if="previewMedia(shot, 'clip')" class="preview-clip-badge" :size="11" /></span>
          <small>{{ String(index + 1).padStart(2, '0') }}</small>
        </button>
      </div>
    </section>
  </div>

  <div v-if="deleteChatUrl" class="modal-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => { if (!deletingChat) deleteChatUrl = null })">
    <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-chat-title" :aria-busy="deletingChat">
      <header><div class="danger-icon"><Trash2 :size="18" /></div><button type="button" class="mini-icon" aria-label="Close delete chat dialog" :disabled="deletingChat" @click="deleteChatUrl = null"><X :size="17" /></button></header>
      <div>
        <h2 id="delete-chat-title">Delete chat from Cutline?</h2>
        <p>This removes the linked chat from Cutline. The conversation in {{ deleteChatProvider }} remains safe and untouched. Future generations or edits for this shot will start fresh in a new chat.</p>
        <a :href="deleteChatUrl" target="_blank" rel="noopener noreferrer">Review chat in browser</a>
      </div>
      <p v-if="deleteChatError" class="form-error" role="alert">{{ deleteChatError }}</p>
      <footer><button type="button" class="secondary-button" :disabled="deletingChat" @click="deleteChatUrl = null">Cancel</button><button type="button" class="danger-button" :disabled="deletingChat" @click="confirmDeleteChat"><Trash2 :size="15" />{{ deletingChat ? 'Deleting…' : 'Delete chat' }}</button></footer>
    </section>
  </div>

  <div v-if="deleteCandidate" class="modal-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => deleteCandidate = null)">
    <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-project-title">
      <header>
        <div class="danger-icon"><Trash2 :size="18" /></div>
        <button class="mini-icon" aria-label="Close" @click="deleteCandidate = null"><X :size="17" /></button>
      </header>
      <div>
        <h2 id="delete-project-title">Delete “{{ deleteCandidate.title }}”?</h2>
        <p>This permanently removes the screenplay, scenes, suggestions, and generated job history for this project.</p>
      </div>
      <p v-if="store.error" class="form-error">{{ store.error }}</p>
      <footer>
        <button class="secondary-button" :disabled="deletingProject" @click="deleteCandidate = null">Cancel</button>
        <button class="danger-button" :disabled="deletingProject" @click="confirmDeleteProject"><Trash2 :size="15" />{{ deletingProject ? 'Deleting...' : 'Delete project' }}</button>
      </footer>
    </section>
  </div>

  <div v-if="deleteShotCandidate" class="modal-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => deleteShotCandidate = null)">
    <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-shot-modal-title">
      <header><div class="danger-icon"><Trash2 :size="18" /></div><button class="mini-icon" aria-label="Close" @click="deleteShotCandidate = null"><X :size="17" /></button></header>
      <div>
        <h2 id="delete-shot-modal-title">Delete {{ deleteShotCandidate.title || 'this shot' }}?</h2>
        <p>This permanently removes this shot and all its generated storyboard images, video clips, and takes.</p>
      </div>
      <p v-if="store.error" class="form-error">{{ store.error }}</p>
      <footer>
        <button class="secondary-button" :disabled="deletingShot" @click="deleteShotCandidate = null">Cancel</button>
        <button class="danger-button" :disabled="deletingShot" @click="confirmDeleteShot"><Trash2 :size="15" />{{ deletingShot ? 'Deleting…' : 'Delete shot' }}</button>
      </footer>
    </section>
  </div>

  <div v-if="deleteShotImageCandidate" class="modal-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => deleteShotImageCandidate = false)">
    <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-shot-image-title">
      <header><div class="danger-icon"><Trash2 :size="18" /></div><button class="mini-icon" aria-label="Close" @click="deleteShotImageCandidate = false"><X :size="17" /></button></header>
      <div><h2 id="delete-shot-image-title">Delete this {{ mediaAspect }} storyboard image?</h2><p>The shot, prompts, and other aspect-ratio image will remain.</p></div>
      <footer><button class="secondary-button" @click="deleteShotImageCandidate = false">Cancel</button><button class="danger-button" :disabled="Boolean(selectedShot && store.isProcessingShot(selectedShot.id))" @click="confirmDeleteShotImage"><Trash2 :size="15" />Delete {{ mediaAspect }} image</button></footer>
    </section>
  </div>

  <div v-if="deleteShotClipCandidate" class="modal-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => deleteShotClipCandidate = false)">
    <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-shot-clip-title">
      <header><div class="danger-icon"><Trash2 :size="18" /></div><button class="mini-icon" aria-label="Close" @click="deleteShotClipCandidate = false"><X :size="17" /></button></header>
      <div><h2 id="delete-shot-clip-title">Delete this shot video?</h2><p>The shot, storyboard image, and prompts will remain. Only the generated or uploaded video will be removed.</p></div>
      <footer><button class="secondary-button" @click="deleteShotClipCandidate = false">Cancel</button><button class="danger-button" :disabled="Boolean(selectedShot && store.isProcessingClip(selectedShot.id))" @click="confirmDeleteShotClip"><Trash2 :size="15" />Delete video</button></footer>
    </section>
  </div>

  <div v-if="deleteFinalCutCandidate" class="modal-backdrop" @mousedown="trackModalBackdropPress" @click="handleBackdropClose($event, () => deleteFinalCutCandidate = false)">
    <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-final-cut-title">
      <header><div class="danger-icon"><Trash2 :size="18" /></div><button class="mini-icon" aria-label="Close" @click="deleteFinalCutCandidate = false"><X :size="17" /></button></header>
      <div><h2 id="delete-final-cut-title">Delete the rendered final cut?</h2><p>Your individual clips, storyboard images, prompts, and timeline will remain.</p></div>
      <footer><button class="secondary-button" @click="deleteFinalCutCandidate = false">Cancel</button><button class="danger-button" @click="confirmDeleteFinalCut"><Trash2 :size="15" />Delete final cut</button></footer>
    </section>
  </div>
  <!-- Cast Reference Manager Modal -->
  <CastManagerModal
    v-if="managingCharacter"
    :character="managingCharacter"
    :image-generation-available="imageGenerationAvailable"
    @close="managingCharacterId = null"
    @deleted-character="selectedCharacterIds = selectedCharacterIds.filter((id) => id !== $event)"
  />

  <!-- Shot Notes Slide-over Drawer -->
  <Transition name="drawer-fade">
    <div
      v-if="showShotNotesDrawer && selectedShot"
      class="shot-notes-drawer-backdrop"
      @mousedown="trackModalBackdropPress"
      @click="handleBackdropClose($event, () => showShotNotesDrawer = false)"
    >
      <aside
        class="shot-notes-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="shot-notes-drawer-title"
        @click.stop
      >
        <header class="shot-notes-drawer-header">
          <div class="drawer-header-left">
            <div class="drawer-header-icon">
              <MessageSquareText :size="18" />
            </div>
            <div>
              <div class="drawer-header-meta">
                <span class="drawer-eyebrow">{{ selectedShotLabel }}</span>
                <span v-if="selectedShotScene?.title" class="drawer-scene-title">· {{ selectedShotScene.title }}</span>
              </div>
              <h2 id="shot-notes-drawer-title">Shot notes &amp; reminders</h2>
            </div>
          </div>
          <div class="drawer-header-right">
            <span
              v-if="selectedShot.unresolved_note_count"
              class="drawer-status-pill unresolved"
            >
              {{ selectedShot.unresolved_note_count }} unresolved
            </span>
            <span
              v-else-if="selectedShot.notes?.length"
              class="drawer-status-pill resolved"
            >
              <Check :size="12" /> All resolved
            </span>
            <button
              type="button"
              class="mini-icon drawer-close-btn"
              aria-label="Close notes drawer"
              title="Close (Esc)"
              @click="showShotNotesDrawer = false"
            >
              <X :size="18" />
            </button>
          </div>
        </header>

        <div class="shot-notes-drawer-body">
          <ShotNotesPanel
            :shot="selectedShot"
            :in-drawer="true"
            :hide-header="true"
            @changed="handleShotNotesChanged(selectedShot, $event)"
          />
        </div>
      </aside>
    </div>
  </Transition>
</template>

<style scoped>
/* CUTLINE MODERN STUDIO */
.studio-layout { background: var(--soft); color: var(--ink); }
.panel-heading {
  min-height: 48px;
  padding: 0 16px;
  border-color: var(--line) !important;
  background: rgba(20,24,22,.86) !important;
  backdrop-filter: blur(16px);
}
.panel-heading h2,
.panel-heading h3 { color: #f4f6f5; letter-spacing: -.02em; }
.panel-heading small,
.panel-heading p { color: var(--muted); }
.preview-stage {
  border-radius: 14px !important;
  border: 1px solid rgba(255,255,255,.08) !important;
  background: #080a09 !important;
  box-shadow: 0 18px 50px rgba(0,0,0,.32), inset 0 0 0 1px rgba(255,255,255,.018);
  overflow: hidden;
}
.preview-stage img,
.preview-stage video { border-radius: 12px; }
.media-variant-switch,
.variant-toggle {
  display: inline-flex;
  align-items: center;
  padding: 3px;
  gap: 2px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(255,255,255,.035);
}
.media-variant-switch button,
.variant-toggle button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  white-space: nowrap;
  min-height: 29px;
  height: 29px;
  padding: 0 10px;
  border: 0 !important;
  border-radius: 7px !important;
  background: transparent !important;
  color: var(--muted) !important;
  box-shadow: none !important;
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  flex-shrink: 0;
}
.media-variant-switch button:hover,
.variant-toggle button:hover { color: #fff !important; background: rgba(255,255,255,.045) !important; }
.media-variant-switch button.active,
.variant-toggle button.active {
  color: #fff !important;
  background: #2a302d !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 1px 2px rgba(0,0,0,.25) !important;
}
.aspect-spin {
  flex-shrink: 0;
  margin: 0;
}
.review-badge.readiness.polish {
  color: #b7791f;
}

.review-badge.readiness.blocked {
  color: #c53030;
}

.review-badge {
  padding: 5px 9px;
  background: rgba(73,169,120,.1) !important;
  border: 1px solid rgba(73,169,120,.24) !important;
  color: #7fd1a5 !important;
  font-size: 11px;
  font-weight: 650;
}
.produce-scene-pill {
  min-height: 30px;
  padding: 0 10px;
  background: rgba(232,93,74,.09) !important;
  border: 1px solid rgba(232,93,74,.22) !important;
  color: #ff9c90 !important;
}
.shot-card,
.scene-card,
.asset-card,
.character-card,
.suggestion-card,
.provider-card {
  border-radius: 12px !important;
  border-color: var(--line) !important;
  background: rgba(255,255,255,.025) !important;
  box-shadow: none !important;
}
.shot-card:hover,
.scene-card:hover,
.asset-card:hover,
.character-card:hover,
.suggestion-card:hover {
  border-color: var(--line-strong) !important;
  background: rgba(255,255,255,.045) !important;
}
.shot-card.active,
.scene-card.active { border-color: rgba(232,93,74,.45) !important; box-shadow: 0 0 0 1px rgba(232,93,74,.14) !important; }
textarea,
input,
select {
  background: #101412 !important;
  border-color: var(--line) !important;
  color: #eff2f0 !important;
}
textarea::placeholder,
input::placeholder { color: #68716c; }
.confirm-modal { background: rgba(0,0,0,.58) !important; backdrop-filter: blur(5px); }
.job-cancel-button { border-radius: 8px !important; }
.status-dot { background: var(--success); }
.status-dot.muted { background: #59605c; }

.field-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.field-action-button {
  padding: 0;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 0;
  background: transparent;
  color: var(--accent, #49a978);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s ease, color 0.15s ease;
}

.field-action-button:hover {
  color: #7fd1a5;
  opacity: 0.9;
}

.screenplay-dropzone {
  position: relative;
  display: flex;
  flex-direction: column;
}

.screenplay-dropzone.is-dragging {
  border-radius: 8px;
  outline: 2px dashed var(--accent, #49a978);
  outline-offset: -2px;
}

.dropzone-overlay {
  position: absolute;
  inset: 0;
  background: rgba(11, 14, 17, 0.92);
  backdrop-filter: blur(4px);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #7fd1a5;
  font-size: 13px;
  font-weight: 600;
  pointer-events: none;
  z-index: 10;
}

.loaded-file-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 10px;
  margin-top: 6px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  font-size: 11px;
  color: var(--muted, #94a3b8);
}

.loaded-file-info {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #e2e8f0;
}

.loaded-file-info strong {
  color: #fff;
  font-weight: 600;
}

.loaded-file-clear {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2px;
  border: 0;
  background: transparent;
  color: var(--muted, #94a3b8);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.loaded-file-clear:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}


.chat-session-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  border-radius: 4px;
  background: rgba(16, 185, 129, 0.12);
  color: #10b981;
  border: 1px solid rgba(16, 185, 129, 0.25);
  font-size: 11px;
  font-weight: 600;
  text-decoration: none;
  transition: all 0.15s ease;
}

.chat-session-badge:hover {
  background: rgba(16, 185, 129, 0.22);
  color: #34d399;
}

.chat-session-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  text-decoration: none;
}

.chat-session-callout {
  padding: 12px 14px;
  background: rgba(24, 26, 25, 0.8);
  border: 1px solid var(--line-default);
  border-radius: 8px;
  margin-bottom: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.chat-session-callout-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-primary);
}

.chat-session-callout p {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.chat-session-callout-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #38bdf8;
  text-decoration: none;
  word-break: break-all;
}

.chat-session-callout-link:hover {
  text-decoration: underline;
}

.episode-settings-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 200px;
  overflow-y: auto;
  padding: 4px 2px;
}

.episode-setting-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.episode-setting-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.08);
  color: #c5ccc7;
  white-space: nowrap;
  min-width: 80px;
  text-align: center;
}

.episode-setting-title-input {
  flex: 1;
  height: 32px;
  padding: 0 10px;
  border-radius: 6px;
  border: 1px solid var(--line-subtle);
  background: rgba(0, 0, 0, 0.35);
  color: var(--text-primary);
  font-size: 13px;
  transition: border-color 0.15s ease;
}

.episode-setting-title-input:focus {
  border-color: var(--line-default);
  outline: none;
}
</style>


<style scoped>
/* Final Studio workspace polish: preserve all behavior, modernize hierarchy. */
.studio-layout {
  grid-template-columns: 256px minmax(0, 1fr);
  background: var(--workspace-bg);
  color: var(--text-primary);
}

.project-rail {
  border-right: 1px solid var(--line-subtle);
  background:
    linear-gradient(180deg, rgba(255,255,255,.018), transparent 28%),
    #0b0e11;
  box-shadow: none;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.rail-projects {
  flex: 1 1 auto;
  min-height: 0;
  padding: 0 8px 10px;
  overflow-y: auto;
  overflow-x: hidden;
  border-bottom: 1px solid var(--line-subtle);
}

.rail-projects-header {
  position: sticky;
  top: 0;
  z-index: 3;
  padding: 12px 6px 8px;
  margin-bottom: 2px;
  background: rgba(11, 14, 17, 0.92);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
}

.rail-projects-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.rail-projects-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 16px;
  padding: 0 5px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--text-tertiary, #8c9590);
  font-size: 10px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.rail-new-project-btn {
  width: 26px;
  height: 26px;
  min-height: 26px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.035);
  color: #cbd2ce;
}

.rail-new-project-btn:hover:not(:disabled) {
  background: var(--accent, #eb6554);
  border-color: var(--accent, #eb6554);
  color: #ffffff;
}

.project-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.stage-list {
  flex-shrink: 0;
}

.provider-panel {
  flex-shrink: 0;
}

.project-header {
  position: relative;
  z-index: 60;
  min-height: 56px;
  border-bottom: 1px solid var(--line-subtle);
  background: rgba(11, 14, 17, .88);
  backdrop-filter: blur(18px);
}

.project-item {
  position: relative;
  width: 100%;
  margin-top: 0;
  padding-right: 4px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  transition: background-color 140ms ease, border-color 140ms ease;
}

.project-item.project-empty-action {
  grid-template-columns: 26px minmax(0, 1fr);
  gap: 9px;
  padding: 8px;
  text-align: left;
  cursor: pointer;
}

.project-item:hover,
.project-item.menu-open {
  background: rgba(255, 255, 255, 0.042);
  border-color: rgba(255, 255, 255, 0.065);
}

.project-item.active {
  background: rgba(255, 255, 255, 0.072);
  border-color: rgba(255, 255, 255, 0.11);
  border-left-color: transparent;
  box-shadow: none;
}

.project-item.active::before {
  content: '';
  position: absolute;
  left: -1px;
  top: -1px;
  bottom: -1px;
  width: 10px;
  border-radius: 8px 0 0 8px;
  border-left: 3px solid var(--accent, #eb6554);
  pointer-events: none;
}

.project-select {
  min-width: 0;
  width: 100%;
  padding: 7px 4px 7px 8px;
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr);
  align-items: flex-start;
  gap: 9px;
  border: 0 !important;
  border-radius: 7px;
  background: transparent !important;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.project-select:hover {
  background: transparent !important;
  border-color: transparent !important;
}

.project-thumb {
  width: 26px;
  height: 26px;
  margin-top: 1px;
  border-radius: 6px;
  display: grid;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-secondary);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.035);
  flex-shrink: 0;
}

.project-thumb.tone-produce {
  background: rgba(235, 101, 84, 0.11);
  border-color: rgba(235, 101, 84, 0.22);
  color: #ff9485;
}

.project-thumb.tone-complete {
  background: rgba(84, 185, 130, 0.12);
  border-color: rgba(84, 185, 130, 0.25);
  color: #6cd39b;
}

.project-thumb.tone-review {
  background: rgba(122, 162, 255, 0.11);
  border-color: rgba(122, 162, 255, 0.22);
  color: #9bb6ff;
}

.project-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
}

.project-copy strong {
  width: 100%;
  color: #e4e9e6;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: -0.01em;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: normal;
  word-break: break-word;
}

.project-item.active .project-copy strong {
  color: #ffffff;
  font-weight: 650;
}

.project-copy small.project-meta {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 100%;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  color: #8c9590;
  font-size: 10.5px;
  font-weight: 500;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.project-status-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  flex-shrink: 0;
  background: #8c9590;
}

.project-status-dot.tone-produce {
  background: #eb6554;
}

.project-status-dot.tone-complete {
  background: #54b982;
}

.project-status-dot.tone-review {
  background: #7aa2ff;
}

.project-status-label {
  text-transform: capitalize;
  color: #9ea7a2;
}

.project-item.tone-complete .project-status-label {
  color: #79cfa0;
}

.project-meta-extra {
  color: #6c7570;
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
}

.project-menu-trigger {
  width: 26px;
  height: 26px;
  min-height: 26px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: #8c9590;
  display: grid;
  place-items: center;
  cursor: pointer;
  opacity: 0;
  outline: none;
  transition: opacity 140ms ease, background-color 140ms ease, border-color 140ms ease, color 140ms ease;
}

.project-menu-trigger:focus-visible {
  border-color: rgba(255, 255, 255, 0.28);
  color: #ffffff;
}

.project-item:hover .project-menu-trigger,
.project-item:focus-within .project-menu-trigger,
.project-item.active .project-menu-trigger,
.project-menu-trigger[aria-expanded='true'] {
  opacity: 1;
}

@media (hover: none) {
  .project-menu-trigger {
    opacity: 1;
  }
}

.project-menu-trigger:hover,
.project-menu-trigger[aria-expanded='true'] {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}

.project-menu {
  position: absolute;
  z-index: 20;
  top: calc(100% + 2px);
  right: 4px;
  min-width: 164px;
  padding: 5px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  background: #14181b;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(16px);
}

.project-menu button {
  width: 100%;
  height: 32px;
  padding: 0 9px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: #e4e9e6;
  font-size: 12px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  transition: background-color 120ms ease, color 120ms ease;
}

.project-menu button:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
}

.project-menu button.danger-item {
  color: #f4968e;
}

.project-menu button.danger-item:hover {
  background: rgba(235, 101, 84, 0.15);
  color: #ffb8b1;
}

.background-jobs-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 500;
  color: var(--accent, #ea580c);
  background: rgba(234, 88, 12, 0.1);
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid rgba(234, 88, 12, 0.2);
}

.save-state,
.provider-state {
  color: var(--text-tertiary);
}

.project-header-left {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.breadcrumb {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  line-height: 1;
  color: var(--text-tertiary);
  flex-wrap: nowrap;
}

.breadcrumb-root {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-tertiary);
  user-select: none;
}

.breadcrumb-separator {
  color: rgba(255, 255, 255, 0.22);
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
}

.project-title-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #eff2f0;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: -0.01em;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
  line-height: 1;
  flex-shrink: 0;
}

.project-title-link:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.project-title-icon {
  color: var(--accent);
  flex-shrink: 0;
}

.project-title-text {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.compilations-header-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #c0c7d4;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
  line-height: 1;
  flex-shrink: 0;
}

.compilations-header-pill:hover {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.2);
  color: #fff;
}

.compilations-header-pill.is-active {
  background: rgba(99, 102, 241, 0.16);
  border-color: rgba(99, 102, 241, 0.4);
  color: #a5b4fc;
}

.compilations-count-pill {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}

.project-stage-heading {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.015em;
  color: var(--text-primary);
}

.workspace {
  background:
    radial-gradient(circle at 58% -10%, rgba(235,101,84,.055), transparent 30%),
    var(--workspace-bg);
}

.stage-list {
  gap: 4px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--line-subtle);
  background: rgba(10, 12, 15, .9);
  backdrop-filter: blur(18px);
}

.stage {
  min-height: 32px;
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 0 11px;
  color: var(--text-tertiary);
  background: transparent;
  font-size: 12px;
  font-weight: 600;
}

.stage:hover {
  color: var(--text-secondary);
  background: rgba(255,255,255,.035);
}

.stage.active {
  color: var(--text-primary);
  border-color: var(--line-default);
  background: rgba(255,255,255,.06);
  box-shadow: 0 1px 0 rgba(255,255,255,.025) inset;
}

.stage.complete::after {
  background: #5fc98a;
  box-shadow: 0 0 0 3px rgba(95,201,138,.09);
}

.header-actions {
  gap: 7px;
}

.provider-panel,
.scene-card,
.shot-card,
.asset-card,
.character-card,
.suggestion-card,
.provider-card {
  border: 1px solid var(--line-subtle);
  border-radius: var(--radius-md);
  background: linear-gradient(180deg, rgba(255,255,255,.025), rgba(255,255,255,.012));
  box-shadow: none;
}

.scene-card:hover,
.shot-card:hover,
.asset-card:hover,
.character-card:hover,
.suggestion-card:hover,
.provider-card:hover {
  border-color: var(--line-default);
  background: linear-gradient(180deg, rgba(255,255,255,.043), rgba(255,255,255,.018));
  transform: translateY(-1px);
}

.scene-card.active,
.shot-card.active {
  border-color: rgba(235,101,84,.5);
  background: linear-gradient(180deg, rgba(235,101,84,.105), rgba(235,101,84,.045));
  box-shadow: 0 0 0 1px rgba(235,101,84,.07) inset;
}

.panel-heading {
  margin-bottom: 12px;
}

.panel-heading h2,
.panel-heading h3,
.provider-title {
  color: var(--text-primary);
  letter-spacing: -.018em;
}

.panel-heading p,
.provider-panel p,
.scene-card p,
.shot-card p {
  color: var(--text-secondary);
  line-height: 1.55;
}

.preview-stage {
  border: 1px solid var(--line-subtle);
  border-radius: var(--radius-lg);
  background:
    radial-gradient(circle at 50% 8%, rgba(255,255,255,.055), transparent 34%),
    #060708;
  box-shadow: var(--shadow-panel), inset 0 1px 0 rgba(255,255,255,.035);
  overflow: hidden;
}

.media-variant-switch,
.variant-toggle {
  border-color: var(--line-subtle);
  background: rgba(0,0,0,.28);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.025);
}

.review-badge,
.produce-scene-pill {
  border: 1px solid var(--line-default);
  border-radius: 999px;
  background: rgba(255,255,255,.045);
  color: var(--text-secondary);
  box-shadow: inset 0 1px 0 rgba(255,255,255,.025);
}

.error-banner {
  border: 1px solid rgba(238,92,92,.3);
  border-radius: var(--radius-md);
  background: var(--danger-soft);
  color: #ffb7b7;
}

.confirm-modal {
  border: 1px solid var(--line-default);
  border-radius: 16px;
  background: rgba(18,21,26,.98);
  box-shadow: var(--shadow-float);
}

.status-dot {
  width: 7px;
  height: 7px;
  box-shadow: 0 0 0 3px rgba(87,196,132,.08);
}

.status-dot.muted {
  box-shadow: none;
}

@media (max-width: 1100px) {
  .project-rail {
    width: 210px;
  }

  .stage-list {
    overflow-x: auto;
    scrollbar-width: none;
  }

  .stage-list::-webkit-scrollbar {
    display: none;
  }
}

.produce-cast-card {
  cursor: pointer;
  transition: transform 0.15s ease, border-color 0.15s ease;
  width: 132px;
  min-width: 132px;
  max-width: 140px;
  display: flex;
  flex-direction: column;
}

.produce-cast-card:hover {
  border-color: var(--line-default) !important;
  transform: translateY(-1px);
}

.produce-cast-card small {
  min-height: 16px;
  line-height: 1.3;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.produce-cast-card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-top: auto;
  padding-top: 4px;
}

.script-toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.view-original-script-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--line-subtle);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.view-original-script-btn:hover {
  background: rgba(255, 255, 255, 0.09);
  color: var(--text-primary);
  border-color: var(--line-default);
}

.version-pill-mini {
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  color: var(--text-tertiary);
  font-size: 10px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.view-original-script-btn:hover .version-pill-mini {
  background: rgba(235, 101, 84, 0.16);
  color: #ffb4a8;
}


.episode-settings-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 200px;
  overflow-y: auto;
  padding: 4px 2px;
}

.episode-setting-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.episode-setting-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 4px 8px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.08);
  color: #c5ccc7;
  white-space: nowrap;
  min-width: 80px;
  text-align: center;
}

.episode-setting-title-input {
  flex: 1;
  height: 32px;
  padding: 0 10px;
  border-radius: 6px;
  border: 1px solid var(--line-subtle);
  background: rgba(0, 0, 0, 0.35);
  color: var(--text-primary);
  font-size: 13px;
  transition: border-color 0.15s ease;
}

.episode-setting-title-input:focus {
  border-color: var(--line-default);
  outline: none;
}

.video-cast-ref-toggle {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}

.video-cast-ref-toggle input[type="checkbox"] {
  accent-color: #eb6554;
  cursor: pointer;
}

.flow-model-inline-select {
  height: 36px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--line-default, rgba(255, 255, 255, 0.14));
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-primary, #f3f5f4);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.flow-model-inline-select:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.24);
}

.flow-model-inline-select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

@media (max-width: 640px) {
  .studio-layout.assembling { display:flex; flex-direction:column; height:auto; min-height:calc(100dvh - 60px); }
  .assembling .project-rail { width:100%; min-height:0; padding:8px 12px; }
  .assembling .rail-projects, .assembling .provider-panel { display:none; }
  .assembling .stage-list { display:flex; flex-direction:row; gap:8px; padding:0; }
  .assembling .stage-list > .section-label { display:none; }
  .assembling .stage { flex:1; justify-content:center; }
  .assembling .workspace { min-width:0; overflow:visible; }
}

/* Elements Library, 3D Turnaround Badges, R2V Tray & @Mention Autocomplete */
.elements-library-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 16px;
}

.elements-tab-bar {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border-radius: 10px;
  background: rgba(0, 0, 0, 0.35);
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.element-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: 7px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-secondary, #a7b0ac);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.element-tab-btn small {
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  font-size: 10px;
  font-weight: 700;
}

.element-tab-btn.active {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
  border-color: rgba(255, 255, 255, 0.14);
}

.element-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
  flex-shrink: 0;
}

.element-dot.kind-character { background: #b68cff; box-shadow: 0 0 6px rgba(182, 140, 255, 0.5); }
.element-dot.kind-scene { background: #52d193; box-shadow: 0 0 6px rgba(82, 209, 147, 0.5); }
.element-dot.kind-prop { background: #5eb5ff; box-shadow: 0 0 6px rgba(94, 181, 255, 0.5); }

.produce-cast-face {
  position: relative;
}

.element-3d-corner-badge {
  position: absolute;
  bottom: 2px;
  right: 2px;
  padding: 1px 5px;
  border-radius: 5px;
  background: rgba(164, 112, 255, 0.92);
  color: #fff;
  font-size: 9px;
  font-weight: 800;
  letter-spacing: 0.04em;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.45);
}

.produce-cast-meta {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.produce-cast-meta strong {
  font-size: 12px;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  line-height: 1.25;
  min-height: 30px;
}

.element-tag-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  width: fit-content;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 2px 7px;
  border-radius: 999px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 10.5px;
  font-weight: 700;
  border: 1px solid transparent;
}

.element-tag-pill.kind-character {
  background: rgba(164, 112, 255, 0.16);
  border-color: rgba(164, 112, 255, 0.36);
  color: #d7bdff;
}

.element-tag-pill.kind-scene {
  background: rgba(73, 186, 129, 0.16);
  border-color: rgba(73, 186, 129, 0.36);
  color: #93ecc0;
}

.element-tag-pill.kind-prop {
  background: rgba(74, 158, 255, 0.16);
  border-color: rgba(74, 158, 255, 0.36);
  color: #a5d0ff;
}

.element-tag-pill.clickable {
  cursor: pointer;
  transition: transform 0.12s ease, filter 0.12s ease;
}

.element-tag-pill.clickable:hover {
  transform: translateY(-1px);
  filter: brightness(1.15);
}

.element-tag-pill small {
  padding: 0 4px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.35);
  font-size: 9px;
}

.accent-3d-link {
  color: #cdb0ff !important;
  font-weight: 700;
}

.video-mode-header-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}

.video-mode-sub {
  display: block;
  color: var(--text-secondary, #98a29d);
  font-size: 11.5px;
  margin-top: 2px;
}

.chip-3d-tag {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(164, 112, 255, 0.28);
  color: #e4d4ff;
  font-size: 9.5px;
  font-weight: 800;
}

.r2v-elements-tray {
  margin-top: 10px;
  padding: 12px;
  border-radius: 12px;
  background: rgba(164, 112, 255, 0.05);
  border: 1px solid rgba(164, 112, 255, 0.22);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.r2v-tray-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}

.r2v-scene-check {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-secondary);
  cursor: pointer;
}

.r2v-tray-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(145px, 1fr));
  gap: 8px;
}

.r2v-tray-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 7px;
  border-radius: 9px;
  background: rgba(0, 0, 0, 0.35);
  border: 1px solid rgba(255, 255, 255, 0.08);
  cursor: pointer;
  transition: border-color 0.15s ease;
}

.r2v-tray-card:hover {
  border-color: rgba(255, 255, 255, 0.24);
}

.r2v-tray-thumb {
  position: relative;
  aspect-ratio: 16 / 9;
  border-radius: 6px;
  overflow: hidden;
  background: #0b0e0c;
  display: flex;
  align-items: center;
  justify-content: center;
}

.r2v-tray-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.r2v-missing-thumb {
  font-size: 10.5px;
  color: #ff9c90;
}

.r2v-ref-index {
  position: absolute;
  top: 4px;
  left: 4px;
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.72);
  color: #fff;
  font-size: 9.5px;
  font-weight: 700;
}

.r2v-3d-pill {
  position: absolute;
  bottom: 4px;
  right: 4px;
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(164, 112, 255, 0.88);
  color: #fff;
  font-size: 9.5px;
  font-weight: 800;
}

.r2v-tray-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.r2v-tray-meta small {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.r2v-tray-empty {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.mention-quick-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.07);
}

.mention-quick-label {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.mention-quick-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.prompt-field-with-mentions {
  position: relative;
  display: flex;
  flex-direction: column;
}

.mention-hint {
  font-weight: 400;
  color: var(--text-secondary);
  margin-left: 4px;
}

.mention-autocomplete-popover {
  position: absolute;
  left: 0;
  right: 0;
  top: 100%;
  margin-top: 4px;
  z-index: 40;
  padding: 6px;
  border-radius: 10px;
  background: #141816;
  border: 1px solid rgba(255, 255, 255, 0.16);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55);
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 220px;
  overflow-y: auto;
}

.mention-option {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 7px;
  border: 0;
  background: transparent;
  color: #f2f5f3;
  font-size: 12.5px;
  text-align: left;
  cursor: pointer;
}

.mention-option:hover,
.mention-option.active {
  background: rgba(255, 255, 255, 0.08);
}

.mention-option small {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 11px;
}

.detected-mentions-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-secondary);
}

.clip-color-controls {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}

.clip-retouch-btn {
  border-color: rgba(245, 158, 11, 0.35);
  background: rgba(245, 158, 11, 0.08);
  color: #fcd34d;
  gap: 6px;
}

.clip-retouch-btn:hover:not(:disabled) {
  border-color: rgba(245, 158, 11, 0.55);
  background: rgba(245, 158, 11, 0.16);
}

.clip-boost-pill {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 10px;
  font-weight: 700;
  background: rgba(245, 158, 11, 0.24);
  color: #fde68a;
  letter-spacing: 0.02em;
}

.clip-color-step-btn {
  padding-inline: 8px;
  min-width: 30px;
  justify-content: center;
}

/* Shot Notes Header Button */
.shot-notes-header-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 10px;
  border-radius: 8px;
  border: 1px solid var(--line, rgba(255, 255, 255, 0.1));
  background: rgba(255, 255, 255, 0.04);
  color: var(--muted, #94a3b8);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.shot-notes-header-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
  border-color: rgba(255, 255, 255, 0.2);
}
.shot-notes-header-btn.active {
  background: rgba(255, 255, 255, 0.12);
  color: #ffffff;
  border-color: rgba(255, 255, 255, 0.28);
}
.shot-notes-header-btn.has-unresolved {
  background: rgba(246, 196, 83, 0.12);
  border-color: rgba(246, 196, 83, 0.45);
  color: #f6c453;
}
.shot-notes-header-btn.has-unresolved:hover {
  background: rgba(246, 196, 83, 0.2);
  border-color: rgba(246, 196, 83, 0.65);
  color: #ffd874;
}
.shot-notes-header-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  background: rgba(255, 255, 255, 0.15);
  color: #ffffff;
}
.shot-notes-header-badge.unresolved {
  background: #f6c453;
  color: #141715;
}

/* Shot Notes Slide-over Drawer */
.shot-notes-drawer-backdrop {
  position: fixed;
  inset: 0;
  z-index: 999;
  background: rgba(0, 0, 0, 0.52);
  backdrop-filter: blur(2px);
  display: flex;
  justify-content: flex-end;
}
.shot-notes-drawer {
  width: min(540px, 100vw);
  height: 100vh;
  background: #141715;
  border-left: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: -12px 0 40px rgba(0, 0, 0, 0.65);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.shot-notes-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
  background: #181c19;
  gap: 12px;
  flex-shrink: 0;
}
.drawer-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.drawer-header-icon {
  width: 36px;
  height: 36px;
  border-radius: 9px;
  background: rgba(246, 196, 83, 0.12);
  color: #f6c453;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.drawer-header-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
}
.drawer-eyebrow {
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #f6c453;
}
.drawer-scene-title {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}
.shot-notes-drawer-header h2 {
  margin: 2px 0 0;
  font-size: 16px;
  font-weight: 700;
  color: #eff2ef;
}
.drawer-header-right {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.drawer-status-pill {
  border-radius: 999px;
  padding: 3px 9px;
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.drawer-status-pill.unresolved {
  background: rgba(246, 196, 83, 0.15);
  color: #f6c453;
  border: 1px solid rgba(246, 196, 83, 0.35);
}
.drawer-status-pill.resolved {
  background: rgba(74, 222, 128, 0.12);
  color: #4ade80;
  border: 1px solid rgba(74, 222, 128, 0.3);
}
.drawer-close-btn {
  background: transparent;
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: var(--muted);
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
}
.drawer-close-btn:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.2);
}
.shot-notes-drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

/* Slide-in & Fade Transition */
.drawer-fade-enter-active,
.drawer-fade-leave-active {
  transition: opacity 0.2s ease;
}
.drawer-fade-enter-from,
.drawer-fade-leave-to {
  opacity: 0;
}
.drawer-fade-enter-active .shot-notes-drawer {
  animation: drawerSlideIn 0.22s cubic-bezier(0.16, 1, 0.3, 1);
}
.drawer-fade-leave-active .shot-notes-drawer {
  animation: drawerSlideOut 0.18s cubic-bezier(0.7, 0, 0.84, 0);
}
@keyframes drawerSlideIn {
  from {
    transform: translateX(100%);
  }
  to {
    transform: translateX(0);
  }
}
@keyframes drawerSlideOut {
  from {
    transform: translateX(0);
  }
  to {
    transform: translateX(100%);
  }
}

.shot-note-badge {
  cursor: pointer;
  transition: transform 0.15s ease, opacity 0.15s ease;
}
.shot-note-badge:hover {
  transform: scale(1.08);
  opacity: 0.95;
}
</style>

