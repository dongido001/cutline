<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Clapperboard,
  Clock3,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Film,
  Image,
  ListChecks,
  MessageSquare,
  MessageSquareText,
  Mic,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Scissors,
  Settings,
  SkipBack,
  SkipForward,
  Sparkles,
  Trash2,
  Upload,
  Video,
  Volume2,
  X,
} from '@lucide/vue'
import { api, type AspectRatio, type Character, type OutputVariant, type ProjectInput, type ProjectSummary, type Scene, type Shot, type ShotTake, type Suggestion } from '@/lib/api'
import { buildShotVideoPrompt } from '@/lib/shotPrompt'
import { useStudioStore } from '@/stores/studio'
import TimelineEditor from '@/components/TimelineEditor.vue'
import ImageReferencePanel from '@/components/ImageReferencePanel.vue'
import ShotTakesPanel from '@/components/ShotTakesPanel.vue'
import CastManagerModal from '@/components/CastManagerModal.vue'
import DeepReviewPanel from '@/components/DeepReviewPanel.vue'
import EpisodeSwitcher from '@/components/EpisodeSwitcher.vue'
import OriginalScriptModal from '@/components/OriginalScriptModal.vue'

interface DisplaySuggestion {
  id: string | number
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
const clipUploadInput = ref<HTMLInputElement | null>(null)
const produceDetailOpen = ref(false)
const showPromptEditor = ref(false)
const pendingPromptEditor = ref(false)
const expandedProduceShotIds = ref<string[]>([])
const continuityDrafts = reactive<Record<string, { set_notes: string; lighting_notes: string; wardrobe_notes: string; props_notes: string }>>({})
const continuitySavingIds = ref<string[]>([])
const mediaAspect = ref<AspectRatio>('16:9')
const finalCutAspect = ref<AspectRatio>('16:9')
const previewVideo = ref<HTMLVideoElement | null>(null)
const storyboardVideo = ref<HTMLVideoElement | null>(null)
const continuationForm = reactive({ script: '', target_duration_seconds: 30 })
const SCRIPT_GENERATION_PROMPT = `Create a production-ready AI short-film screenplay.

Requirements:
- Duration: [TOTAL SECONDS] seconds
- Aspect ratio: [16:9 or 9:16]
- Genre: [GENRE]
- Use shots lasting between 2 and 10 seconds.
- Number every shot consecutively.
- Timings must be continuous, with no gaps or overlaps.
- The final shot must end at the declared total duration.
- Every shot must include framing, visible action, sound, an image prompt, and a video prompt.
- Give consecutive shots the same compact Scene label when they share a production sequence.
- Put spoken dialogue under the action using an uppercase character cue.
- Write "Dialogue: None." when nobody speaks.
- Keep character appearance, clothing, location, lighting, and important props consistent between shots.
- Image prompts must describe one clear starting frame.
- Video prompts must describe movement, camera motion, expressions, and continuity for that exact shot.
- Return only the screenplay. Do not add explanations or Markdown fences.

Follow this exact structure:

[FILM TITLE]

Genre: [genre]
Duration: [number] seconds
Format: [number] shots x [number] seconds
Aspect ratio: [ratio]

Character consistency

[Describe each recurring character precisely.]

Location: [Describe the primary location and visual continuity.]

SHOT 1 - [SHOT TITLE]

Scene: [COMPACT SCENE LABEL]
Time: 00:00-00:06
Framing: [framing]

Action:
[Visible action during this shot.]

[CHARACTER NAME]:
"[Dialogue]"

Sound: [Ambient sound, speech, music, and sound effects.]

Image prompt:
[Describe the starting frame, character identity, clothing, location, lighting, composition, style, aspect ratio, and exclusions.]

Video prompt:
[Describe animation, character movement, camera movement, expressions, lip synchronization when needed, environmental motion, and identity preservation.]

Continue with SHOT 2 and the same fields until the declared duration is reached.`
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
    severity: item.severity,
    title: item.title,
    reason: item.reason,
    change: typeof item.after_value === 'string' ? item.after_value : JSON.stringify(item.after_value),
    status: item.status,
  }))
})
const pendingCount = computed(() => suggestions.value.filter((item) => item.status === 'pending').length)
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
watch(() => selectedShot.value?.id, () => {
  skipDefaultImageReference.value = false
})
function resolveActiveShotMedia(shot: Shot | null | undefined, mediaType: 'image' | 'clip', aspect: AspectRatio) {
  if (!shot) return null
  const variant = shot.media_variants.find((item) => item.media_type === mediaType && item.aspect_ratio === aspect)
  const legacy = store.currentProject?.aspect_ratio === aspect ? (mediaType === 'clip' ? shot.clip : shot.image) : null
  const hasSlot = Boolean(variant || legacy)
  if (!hasSlot) return null
  const selectedId = mediaType === 'clip' ? shot.selected_clip_take_id : shot.selected_image_take_id
  const activeTake = shot.takes?.find(
    (t) =>
      t.media_type === mediaType &&
      t.aspect_ratio === aspect &&
      (t.selected || (selectedId && t.id === selectedId))
  )
  return activeTake ?? variant ?? legacy
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
const selectedShotScene = computed(() => scenes.value.find((scene) => scene.shots.some((shot) => shot.id === selectedShot.value?.id)))
const selectedShotConversationUrl = computed(() => {
  const shot = selectedShot.value
  if (!shot) return null

  if (shot.conversation_url) return shot.conversation_url

  const matchingTakes = shot.takes?.filter((take) => take.media_type === "image" && take.aspect_ratio === mediaAspect.value) || []
  const activeTake = matchingTakes.find((take) => take.id === shot.selected_image_take_id)
    || matchingTakes[matchingTakes.length - 1]
    || shot.takes?.find((take) => take.id === shot.selected_image_take_id)
  const takeUrl = activeTake?.conversation_url || (activeTake?.settings as Record<string, unknown> | undefined)?.conversation_url as string | undefined
  if (takeUrl) return takeUrl

  const inFlightJob = store.imageJobs[shot.id]
  if (inFlightJob?.conversation_url) return inFlightJob.conversation_url

  const clipJob = store.clipJobs[shot.id]
  if (clipJob?.conversation_url) return clipJob.conversation_url

  return null
})
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
const clipCount = computed(() => allShots.value.filter((shot) => shot.clip).length)
const mediaReadyCount = computed(() => allShots.value.filter((shot) => shot.clip || shot.image).length)
const finalCutReady = computed(() => allShots.value.length > 0 && mediaReadyCount.value === allShots.value.length)
const imageGenerationAvailable = computed(() => store.imageProviders.some((provider) => provider.available))
const imageProviderOrder = computed(() => store.imageProviders.map((provider) => provider.label).join(' → ') || 'No image provider configured')
const selectedImageJob = computed(() => selectedShot.value ? store.imageJobs[selectedShot.value.id] ?? null : null)
const castCharacters = computed(() => store.currentProject?.characters ?? [])
const selectedCast = computed(() =>
  castCharacters.value.filter((character) => selectedCharacterIds.value.includes(character.id)),
)
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
const selectedShotVideoPrompt = computed(() => selectedShot.value
  ? buildShotVideoPrompt(selectedShot.value, selectedShotScene.value, selectedCast.value, mediaAspect.value)
  : '')
const copiedShotPrompt = ref(false)
const includeCastRefsInVideo = ref(false)

function imageJobForShot(shotId: string) {
  return store.imageJobs[shotId] ?? null
}

function defaultCharacterIdsForShot(shot: Shot | null | undefined): string[] {
  if (!shot) return []
  const valid = new Set(castCharacters.value.map((character) => character.id))
  const persisted = (shot.character_ids || []).filter((id) => valid.has(id))
  if (persisted.length) return persisted

  const dialogueNames = new Set(
    shot.dialogue.map((line) => line.character.trim().toLowerCase()).filter(Boolean),
  )
  const prompt = `${shot.image_prompt} ${shot.action} ${shot.video_prompt}`.toLowerCase()
  return castCharacters.value
    .filter((character) => {
      const name = character.name.trim().toLowerCase()
      if (!name) return false
      return dialogueNames.has(name) || prompt.includes(name)
    })
    .map((character) => character.id)
}

function syncSelectedCharactersForShot(shot: Shot | null | undefined) {
  const defaults = defaultCharacterIdsForShot(shot)
  const valid = new Set(castCharacters.value.map((character) => character.id))
  selectedCharacterIds.value = defaults.length
    ? defaults
    : selectedCharacterIds.value.filter((id) => valid.has(id))
}

function toggleCharacterForGeneration(characterId: string) {
  if (selectedCharacterIds.value.includes(characterId)) {
    selectedCharacterIds.value = selectedCharacterIds.value.filter((id) => id !== characterId)
  } else {
    selectedCharacterIds.value = [...selectedCharacterIds.value, characterId]
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
    await store.loadProject(projectId, { resumeJobs: true, episodeId: queryEpisode })
  } catch {
    await router.replace('/')
  }
})
watch(() => store.currentEpisodeId, (newEpId) => {
  if (newEpId && route.query.episode !== newEpId && store.currentProject) {
    void router.replace({
      query: {
        ...route.query,
        episode: newEpId,
      },
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
watch(() => store.currentProject?.id, () => {
  mediaAspect.value = (store.currentProject?.aspect_ratio as AspectRatio | undefined) ?? '16:9'
  if (!route.params.shotId) {
    produceDetailOpen.value = false
    showPromptEditor.value = false
  }
}, { immediate: true })
watch(
  () => scenes.value.map((scene) => `${scene.id}:${scene.shots[0]?.id || ''}:${scene.set_notes || ''}:${scene.lighting_notes || ''}:${scene.wardrobe_notes || ''}:${scene.props_notes || ''}`).join('|'),
  () => {
    ensureFirstShotsExpanded()
    syncContinuityDrafts()
  },
  { immediate: true },
)
watch(
  () => selectedShot.value?.id,
  () => {
    includeCastRefsInVideo.value = false
  },
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
watch([() => route.params.shotId, allShots, activeStage], ([shotId, shots, stage]) => {
  const hasShotParam = typeof shotId === 'string' && shotId.length > 0
  const requestedShot = hasShotParam ? shots.find((shot) => shot.id === shotId) : undefined
  if (hasShotParam) {
    activeShotId.value = requestedShot?.id ?? (shots.length === 0 ? shotId : shots[0]?.id ?? null)
    produceDetailOpen.value = true
    // Only replace route if shots have loaded and the project is initialized, and the requested shot is truly missing
    if (shots.length > 0 && requestedShot?.id !== shotId && activeShotId.value && store.currentProject) {
      void router.replace(projectRoute(store.currentProject.id, stage, activeShotId.value))
    }
  } else {
    activeShotId.value = shots[0]?.id ?? null
    produceDetailOpen.value = false
  }
}, { immediate: true })
watch(selectedShot, (shot, previous) => {
  if (!shot) return
  activeShotId.value = shot.id
  if (shot.id !== previous?.id) {
    showPromptEditor.value = pendingPromptEditor.value
    pendingPromptEditor.value = false
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
  if (event.key === 'Escape' && projectMenuId.value) {
    projectMenuId.value = null
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
  await store.initialize(projectId, queryEpisode)
  if (!projectId && store.projects[0]) {
    await router.replace(projectRoute(store.projects[0].id, defaultStage(store.projects[0])))
  } else if (store.currentEpisodeId && route.query.episode !== store.currentEpisodeId) {
    void router.replace({
      query: {
        ...route.query,
        episode: store.currentEpisodeId,
      },
    })
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
    ?? shot.media_variants.find((item) => item.media_type === mediaType)
    ?? null
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
  })
}

function closeProduceDetail() {
  produceDetailOpen.value = false
  showPromptEditor.value = false
  const projectId = store.currentProject?.id ?? (route.params.projectId as string)
  if (projectId) {
    void router.push(projectRoute(projectId, 'shots'))
  }
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
  void generateShotImage()
}

function beginRegenerateClip() {
  showPromptEditor.value = true
  void generateShotClip()
}

const resettingFlow = ref(false)
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
  if (route.query.scene) query.scene = String(route.query.scene)
  if (store.currentEpisodeId) query.episode = store.currentEpisodeId
  else if (route.query.episode) query.episode = String(route.query.episode)

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

function stageRoute(stage: ProductionStage) {
  if (!store.currentProject) return { name: 'studio' }
  return projectRoute(store.currentProject.id, stage, stage === 'shots' || stage === 'clips' ? selectedShot.value?.id : undefined)
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
  void router.replace({ query: scene ? { ...route.query, scene: scene.id } : route.query })
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
  if (store.currentProject) void router.push(projectRoute(store.currentProject.id, stage, id))
}

function openTrimEditor() {
  if (!store.currentProject || !selectedShot.value) return
  void router.push({
    ...projectRoute(store.currentProject.id, 'final-cut'),
    query: {
      trimShot: selectedShot.value.id,
      variant: mediaAspect.value === '9:16' ? 'shorts' : 'landscape',
    },
  })
}

function setFinalCutVariant(variant: OutputVariant) {
  finalCutAspect.value = variant === 'shorts' ? '9:16' : '16:9'
  if (route.query.variant !== variant) {
    void router.replace({ query: { ...route.query, variant } })
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
    syncSelectedCharactersForShot(selectedShot.value)
  } catch {
    // Store error surface.
  } finally {
    castingBusy.value = false
  }
}

async function generateCastFace(character: Character) {
  try {
    await store.generateCharacterReference(character.id)
  } catch {
    // Store error surface.
  }
}

async function generateMissingCastFaces() {
  const missing = castCharacters.value.filter((character) => !character.references.length)
  for (const character of missing) {
    if (store.isGeneratingCast(character.id)) continue
    await generateCastFace(character)
  }
}

function castJobFor(characterId: string) {
  return store.castJobs[characterId] ?? null
}

async function generateShotImage() {
  if (!selectedShot.value) return
  try {
    await saveShot()
    await store.generateShotImage(
      selectedShot.value.id,
      mediaAspect.value,
      selectedCharacterIds.value,
      false,
      selectedAspectReference.value?.id,
      !skipDefaultImageReference.value,
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
    await store.createCharacter(name)
    newCharacterName.value = ''
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

async function generateShotClip() {
  if (!selectedShot.value || !store.currentProject) return
  const shotId = selectedShot.value.id
  const projectId = store.currentProject.id
  const episodeId = store.currentEpisodeId
  const aspectRatio = mediaAspect.value
  const useCastRefs = includeCastRefsInVideo.value
  const input = { ...shotDraft, character_ids: [...selectedCharacterIds.value] }
  try {
    await store.updateShot(shotId, input)
    await store.generateShotClip(shotId, aspectRatio, projectId, episodeId, useCastRefs)
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
          @click="store.createDraft({ retry_failed_only: true })"
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
            />
          </div>
          <h1 class="project-stage-heading">{{ stageTitle }}</h1>
        </div>
        <div class="header-actions">
          <span v-if="store.backgroundActiveJobsCount > 0" class="background-jobs-badge" title="Active generations running in other projects">
            <RefreshCw :size="12" class="spinning" />
            {{ store.backgroundActiveJobsCount }} background {{ store.backgroundActiveJobsCount === 1 ? 'job' : 'jobs' }}
          </span>
          <span v-if="store.currentProject" class="save-state"><Check :size="14" /> Saved</span>
          <button class="secondary-button format-guide-trigger" title="Script format guide" @click="showScriptGuide = true"><BookOpen :size="15" /><span>Script format</span></button>
          <button class="secondary-button preview-trigger" @click="openAnimaticPreview"><Play :size="15" fill="currentColor" /> Preview</button>
          <button
            v-if="isScriptApproved"
            class="primary-button"
            :disabled="store.creatingDraft"
            :title="store.draftJob?.message || 'Generate missing images, clips, dialogue, and a preview'"
            @click="store.createDraft()"
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
            <span class="review-badge"><Sparkles :size="13" /> Complete</span>
          </div>

          <div class="suggestion-list">
            <article v-for="item in suggestions" :key="item.id" class="suggestion" :class="item.status">
              <div class="suggestion-title">
                <CircleAlert v-if="item.severity !== 'optional'" :size="16" />
                <Sparkles v-else :size="16" />
                <div><span>{{ item.severity }}</span><strong>{{ item.title }}</strong></div>
              </div>
              <p>{{ item.reason }}</p>
              <div class="proposed-change">{{ item.change }}</div>
              <div v-if="item.status === 'pending'" class="decision-row">
                <button class="reject-button" @click="decide(item.id, 'rejected')"><X :size="15" /> Reject</button>
                <button class="accept-button" @click="decide(item.id, 'accepted')"><Check :size="15" /> Accept</button>
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
                <button type="button" :class="{ active: mediaAspect === '16:9' }" @click="mediaAspect = '16:9'">16:9</button>
                <button type="button" :class="{ active: mediaAspect === '9:16' }" @click="mediaAspect = '9:16'">9:16</button>
              </div>
              <button class="secondary-button" type="button" @click="openAnimaticPreview"><Play :size="15" fill="currentColor" /> Preview</button>
              <button
                class="primary-button"
                type="button"
                :disabled="!isScriptApproved || store.creatingDraft"
                :title="store.draftJob?.message || 'Generate missing images, clips, dialogue, and a preview'"
                @click="store.createDraft()"
              >
                <Sparkles :size="15" />
                {{ store.creatingDraft ? (store.draftJob?.message || 'Creating draft…') : 'Create draft video' }}
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
            <button
              v-if="store.draftAttention.length"
              type="button"
              class="secondary-button"
              :disabled="store.creatingDraft"
              @click="store.createDraft({ retry_failed_only: true })"
            >Retry failed</button>
          </div>

          <section class="produce-cast-strip">
            <header>
              <div>
                <span class="section-label">Cast</span>
                <strong>One face library for the whole film</strong>
              </div>
              <div class="cast-header-actions">
                <button
                  class="secondary-button"
                  type="button"
                  :disabled="castingBusy || !castCharacters.some((character) => !character.references.length) || !imageGenerationAvailable"
                  :title="!imageGenerationAvailable ? 'Image provider offline — start with ./scripts/dev --bridges' : 'Generate reference portraits for characters missing a face'"
                  @click="generateMissingCastFaces"
                >
                  <Sparkles :size="15" />Generate missing faces
                </button>
                <button class="secondary-button" type="button" :disabled="castingBusy" @click="syncCastFromScript">Sync from script</button>
                <form class="cast-add" @submit.prevent="addCastCharacter">
                  <input v-model="newCharacterName" type="text" maxlength="120" placeholder="Add character" :disabled="castingBusy" />
                  <button class="secondary-button" type="submit" :disabled="castingBusy || !newCharacterName.trim()"><Plus :size="15" />Add</button>
                </form>
              </div>
            </header>
            <div v-if="!castCharacters.length" class="cast-empty">Sync cast from the script, then generate faces once. Shots inherit tags automatically.</div>
            <div v-else class="produce-cast-row">
              <article
                v-for="character in castCharacters"
                :key="character.id"
                class="produce-cast-card"
                @click="openCastManager(character.id)"
              >
                <div class="produce-cast-face">
                  <img v-if="character.references[0]" :src="character.references[0].url" :alt="character.name" />
                  <span v-else>{{ character.name.slice(0, 1) }}</span>
                </div>
                <strong>{{ character.name }}</strong>
                <small>{{ character.references.length ? `${character.references.length} ref${character.references.length === 1 ? '' : 's'}` : 'needs face' }}</small>
                <div class="produce-cast-card-actions" @click.stop>
                  <button type="button" class="ghost-link" @click="openCastManager(character.id)">
                    Manage
                  </button>
                  <button
                    type="button"
                    class="ghost-link"
                    :disabled="castingBusy || store.isGeneratingCast(character.id) || !imageGenerationAvailable"
                    :title="!imageGenerationAvailable ? 'Image provider offline — start with ./scripts/dev --bridges' : `Generate face reference for ${character.name}`"
                    @click="generateCastFace(character)"
                  >
                    {{ store.isGeneratingCast(character.id) ? 'Generating…' : character.references.length ? 'Add face' : 'Generate' }}
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
                  <label>Set / location<textarea v-model="continuityDraft(scene).set_notes" rows="2" placeholder="Environment, architecture, weather…" @blur="saveContinuity(scene)"></textarea></label>
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
                    type="button"
                    class="produce-shot-row"
                    :class="{ active: selectedShot?.id === shot.id, open: isProduceShotExpanded(shot.id) }"
                    @click="toggleProduceShot(shot.id)"
                  >
                    <span class="produce-shot-toggle">
                      <ChevronDown v-if="isProduceShotExpanded(shot.id)" :size="16" />
                      <ChevronRight v-else :size="16" />
                      <strong>Shot {{ shotIndex + 1 }}</strong>
                      <small>{{ shotTime(shot) }} · {{ shot.framing || 'Medium' }}</small>
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

        <footer v-if="!produceDetailOpen" class="produce-dock">
          <div>
            <strong>{{ allShots.length }} shots</strong>
            <span>{{ mediaReadyCount }} with media · {{ clipCount }} with video</span>
          </div>
          <div class="produce-dock-actions">
            <button type="button" class="secondary-button" @click="openAnimaticPreview"><Play :size="15" fill="currentColor" /> Preview</button>
            <button type="button" class="secondary-button" :disabled="!isScriptApproved" @click="router.push(stageRoute('final-cut'))"><Film :size="15" /> Assemble</button>
            <button
              type="button"
              class="primary-button"
              :disabled="!isScriptApproved || store.creatingDraft"
              @click="store.createDraft()"
            >
              <Sparkles :size="15" />
              {{ store.creatingDraft ? 'Creating draft…' : 'Create draft video' }}
            </button>
          </div>
        </footer>

        <div v-if="produceDetailOpen" class="shot-plan-workspace produce-detail">
          <aside class="shot-browser">
            <div class="shot-browser-heading">
              <button type="button" class="ghost-link back-to-story" @click="closeProduceDetail"><ChevronRight class="back-chevron" :size="16" /> Back to story</button>
              <div><span class="section-label">Shot list</span><strong>{{ allShots.length }} planned shots</strong></div>
              <span>{{ allShots.filter((shot) => shotHasMedia(shot)).length }}/{{ allShots.length }} ready</span>
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
                  class="shot-list-item"
                  :class="{ active: selectedShot?.id === shot.id }"
                  @click="selectShot(shot.id)"
                >
                  <span class="shot-list-thumb">
                    <img v-if="shotThumbUrl(shot)" :src="shotThumbUrl(shot)!" alt="" />
                    <Image v-else :size="18" />
                  </span>
                  <span>
                    <strong>{{ shot.title || `Shot ${String(shot.order_index + 1).padStart(2, '0')}` }}</strong>
                    <small>{{ shot.framing }} · {{ (shot.duration_ms / 1000).toFixed(1) }}s</small>
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
                  <button type="button" :class="{ active: mediaAspect === '16:9' }" @click="mediaAspect = '16:9'">16:9</button>
                  <button type="button" :class="{ active: mediaAspect === '9:16' }" @click="mediaAspect = '9:16'">9:16 Shorts</button>
                </div>
                <span class="image-source" :class="{ ready: selectedAspectImage || selectedAspectReference }">{{ selectedAspectImage ? selectedAspectImage.source.replace('_', ' ') : selectedAspectReference ? 'continuity frame' : `No ${mediaAspect} image` }}</span>
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
                <video v-if="selectedAspectClip" ref="storyboardVideo" :key="selectedAspectClip.url" :src="selectedAspectClip.url" controls playsinline preload="metadata"></video>
                <img v-else-if="selectedAspectImage" :src="selectedAspectImage.url" :alt="`${mediaAspect} storyboard for ${selectedShotLabel}`" />
                <img v-else-if="selectedAspectReference" :src="selectedAspectReference.url" :alt="`${mediaAspect} reference frame for ${selectedShotLabel}`" />
                <div v-else class="empty-storyboard"><Image :size="30" /><strong>No {{ mediaAspect }} media yet</strong><span>Generate a frame, then a clip — or run Create draft from the story view.</span></div>
                <div v-if="store.isProcessingShot(selectedShot.id) || store.isProcessingClip(selectedShot.id)" class="render-overlay">
                  <RefreshCw class="spinning" :size="24" />
                  <strong>{{ store.isProcessingClip(selectedShot.id) ? (store.clipJobs[selectedShot.id]?.message || 'Generating video') : (selectedImageJob?.message || 'Rendering storyboard') }}</strong>
                </div>
              </div>

              <div v-if="castCharacters.length" class="cast-generate-chips">
                <span class="section-label">Use face refs</span>
                <div class="cast-chip-row">
                  <button
                    v-for="character in castCharacters"
                    :key="character.id"
                    type="button"
                    class="cast-chip"
                    :class="{ active: selectedCharacterIds.includes(character.id), warn: !character.references.length }"
                    :disabled="store.isProcessingShot(selectedShot.id)"
                    @click="toggleCharacterForGeneration(character.id)"
                  >
                    {{ character.name }}
                    <small v-if="!character.references.length">no ref</small>
                  </button>
                </div>
                <label
                  v-if="selectedCharacterIds.length"
                  class="video-cast-ref-toggle"
                  title="Default (unchecked) locks Frame 1 strictly. Check this when a tagged character enters mid-shot and is not visible in the opening frame so Cutline also sends their Cast portrait image to Google Flow."
                >
                  <input
                    v-model="includeCastRefsInVideo"
                    type="checkbox"
                    :disabled="store.isProcessingClip(selectedShot.id)"
                  />
                  <span>Also send cast portrait(s) when generating video (for off-screen entrances)</span>
                </label>
              </div>

              <div class="storyboard-actions">
                <button class="primary-button" :disabled="store.isProcessingShot(selectedShot.id) || !imageGenerationAvailable" @click="beginRegenerateImage">
                  <RefreshCw v-if="selectedAspectImage" :size="15" /><Sparkles v-else :size="15" />
                  {{ selectedAspectImage ? `Regenerate ${mediaAspect} image` : `Generate ${mediaAspect} image` }}
                </button>
                <button
                  class="primary-button"
                  :disabled="store.isProcessingClip(selectedShot.id) || (!selectedAspectImage && !selectedAspectReference) || !store.videoProvider?.configured || !store.videoProvider?.available"
                  :title="(!selectedAspectImage && !selectedAspectReference) ? `Generate or upload a ${mediaAspect} image or reference frame first` : (!store.videoProvider?.configured || !store.videoProvider?.available) ? (store.videoProvider?.detail || 'Video provider is offline') : undefined"
                  @click="beginRegenerateClip"
                >
                  <Video :size="15" />
                  {{ selectedAspectClip ? 'Regenerate video' : 'Generate video' }}
                </button>
                <button
                  v-if="store.videoProvider?.configured"
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
                  v-if="selectedAspectClip"
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
            />

            <form class="shot-fields" @submit.prevent="saveShot">
              <div class="shot-field-row">
                <label>Framing<input v-model="shotDraft.framing" list="framing-options" /><datalist id="framing-options"><option value="Wide" /><option value="Medium" /><option value="Close-up" /><option value="Extreme close-up" /><option value="Over-the-shoulder" /><option value="Insert" /></datalist></label>
                <label>Duration (seconds)<input type="number" min="1" max="30" step="0.5" :value="shotDraft.duration_ms / 1000" @input="setShotDuration" /></label>
              </div>
              <p class="shot-action-blurb">{{ selectedShot.action || 'No action line on this shot.' }}</p>
              <div v-if="!showPromptEditor" class="prompt-collapsed">
                <div>
                  <strong>Prompts stay out of the way</strong>
                  <span>Cutline uses the saved image/video prompts plus scene continuity. Open them only when you need a regenerate tweak.</span>
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
                <label>Image prompt<textarea v-model="shotDraft.image_prompt" rows="5" required></textarea></label>
                <label>Video prompt<textarea v-model="shotDraft.video_prompt" rows="3"></textarea></label>
                <label>Title-card text (optional)<textarea v-model="shotDraft.title_card_text" rows="2" placeholder="Rendered over black during the final two seconds."></textarea></label>
              </template>
              <footer>
                <span>{{ selectedAspectClip ? 'Clip ready' : selectedAspectImage ? `${selectedAspectImage.provider || 'image'} · ${mediaAspect}` : imageGenerationAvailable ? `${imageProviderOrder} · ${mediaAspect}` : 'Upload available' }}</span>
                <button class="secondary-button"><Save :size="15" />Save shot</button>
              </footer>
            </form>
          </section>

          <div v-else class="empty-shot-plan"><ListChecks :size="28" /><strong>No shots were created</strong><span>Return to script review and add shot prompts before continuing.</span></div>
        </div>
      </div>

      <TimelineEditor v-else-if="activeStage === 'final-cut' && store.currentProject" :project="store.currentProject" :shots="allShots" @variant-change="setFinalCutVariant" />

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
        <button class="mini-icon" aria-label="Close script format guide" @click="showScriptGuide = false"><X :size="17" /></button>
      </header>
      <div class="guide-summary">
        <p>Traditional screenplays are supported, but this format preserves every supplied shot and timecode before AI review.</p>
        <div><strong>Recognized fields</strong><code>SHOT N - TITLE</code><code>Scene:</code><code>Location:</code><code>Time:</code><code>Framing:</code><code>Action:</code><code>Dialogue:</code><code>Sound:</code><code>Image prompt:</code><code>Video prompt:</code></div>
      </div>
      <div class="guide-prompt-heading"><div><span class="section-label">Reusable prompt</span><strong>Give this to your script generator</strong></div><button class="secondary-button guide-copy-button" :class="{ copied: scriptGuideCopied }" type="button" @click="copyScriptGuide"><Check v-if="scriptGuideCopied" :size="14" /><Copy v-else :size="14" /><span>{{ scriptGuideCopied ? 'Copied' : 'Copy prompt' }}</span></button></div>
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
  padding: 3px;
  gap: 2px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(255,255,255,.035);
}
.media-variant-switch button,
.variant-toggle button {
  min-height: 29px;
  border: 0 !important;
  border-radius: 7px !important;
  background: transparent !important;
  color: var(--muted) !important;
  box-shadow: none !important;
}
.media-variant-switch button:hover,
.variant-toggle button:hover { color: #fff !important; background: rgba(255,255,255,.045) !important; }
.media-variant-switch button.active,
.variant-toggle button.active {
  color: #fff !important;
  background: #2a302d !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.08), 0 1px 2px rgba(0,0,0,.25) !important;
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
}

.produce-cast-card:hover {
  border-color: var(--line-default) !important;
  transform: translateY(-1px);
}

.produce-cast-card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-top: 2px;
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

@media (max-width: 640px) {
  .studio-layout.assembling { display:flex; flex-direction:column; height:auto; min-height:calc(100dvh - 60px); }
  .assembling .project-rail { width:100%; min-height:0; padding:8px 12px; }
  .assembling .rail-projects, .assembling .provider-panel { display:none; }
  .assembling .stage-list { display:flex; flex-direction:row; gap:8px; padding:0; }
  .assembling .stage-list > .section-label { display:none; }
  .assembling .stage { flex:1; justify-content:center; }
  .assembling .workspace { min-width:0; overflow:visible; }
}
</style>