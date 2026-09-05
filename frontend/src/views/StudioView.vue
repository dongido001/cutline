<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  BookOpen,
  Bot,
  Check,
  ChevronRight,
  CircleAlert,
  Clock3,
  Copy,
  Download,
  ExternalLink,
  Film,
  Image,
  ListChecks,
  MessageSquareText,
  MoreHorizontal,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Scissors,
  SkipBack,
  SkipForward,
  Sparkles,
  Trash2,
  Upload,
  Video,
  Volume2,
  X,
} from '@lucide/vue'
import type { AspectRatio, Character, OutputVariant, ProjectInput, ProjectSummary, Scene, Shot, Suggestion } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import TimelineEditor from '@/components/TimelineEditor.vue'

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
  shots: 'Shot plan',
  storyboards: 'Storyboards',
  clips: 'Clips',
  'final-cut': 'Final cut',
}

const stageSteps: Record<ProductionStage, number> = {
  script: 2,
  shots: 3,
  storyboards: 4,
  clips: 4,
  'final-cut': 5,
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
const uploadInput = ref<HTMLInputElement | null>(null)
const castRefInput = ref<HTMLInputElement | null>(null)
const castRefTargetId = ref<string | null>(null)
const selectedCharacterIds = ref<string[]>([])
const newCharacterName = ref('')
const castingBusy = ref(false)
const clipUploadInput = ref<HTMLInputElement | null>(null)
const mediaAspect = ref<AspectRatio>('16:9')
const finalCutAspect = ref<AspectRatio>('16:9')
const previewVideo = ref<HTMLVideoElement | null>(null)
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
const demoSuggestions = ref<DisplaySuggestion[]>([
  {
    id: 1,
    severity: 'blocking',
    title: "Clarify Mara's physical action",
    reason: 'The dialogue has no visible action to drive the shot.',
    change: 'Mara grips the counter, then turns toward the dark hallway.',
    status: 'pending',
  },
  {
    id: 2,
    severity: 'warning',
    title: 'Extend the opening beat',
    reason: 'The current dialogue reads longer than the assigned timing.',
    change: '00:00-00:14',
    status: 'pending',
  },
  {
    id: 3,
    severity: 'optional',
    title: 'Add a room-tone cue',
    reason: 'A quiet appliance hum will anchor the interior atmosphere.',
    change: 'SFX: refrigerator hum, distant traffic',
    status: 'pending',
  },
])

const demoScenes: Scene[] = [
  {
    id: 'demo-1',
    order_index: 0,
    title: 'INT. KITCHEN - NIGHT',
    location: 'INT. KITCHEN',
    time_of_day: 'NIGHT',
    start_ms: 0,
    end_ms: 12000,
    action: 'Rain presses against the black kitchen windows. The only light comes from the open refrigerator.',
    dialogue: [
      { character: 'MARA', text: 'We should leave before he comes back.' },
      { character: 'ELIAS', text: 'And go where?' },
    ],
    shots: [
      { id: 'shot-1', order_index: 0, title: 'Kitchen wide', start_ms: 0, end_ms: 6000, action: '', dialogue: [], sound: '', duration_ms: 6000, framing: 'Medium', image_prompt: '', video_prompt: '', title_card_text: '', status: 'planned', image: null, clip: null, media_variants: [] },
      { id: 'shot-2', order_index: 1, title: 'Mara reacts', start_ms: 6000, end_ms: 12000, action: '', dialogue: [], sound: '', duration_ms: 6000, framing: 'Close-up', image_prompt: '', video_prompt: '', title_card_text: '', status: 'planned', image: null, clip: null, media_variants: [] },
    ],
  },
  {
    id: 'demo-2', order_index: 1, title: 'INT. HALLWAY - NIGHT', location: 'INT. HALLWAY', time_of_day: 'NIGHT', start_ms: 12000, end_ms: 28000,
    action: 'Mara leads Elias through the narrow hallway as headlights sweep across the walls.', dialogue: [], shots: [],
  },
  {
    id: 'demo-3', order_index: 2, title: 'EXT. DRIVEWAY - NIGHT', location: 'EXT. DRIVEWAY', time_of_day: 'NIGHT', start_ms: 28000, end_ms: 46000,
    action: 'They run toward an idling car through the hard rain.', dialogue: [], shots: [],
  },
  {
    id: 'demo-4', order_index: 3, title: 'EXT. NIGHT ROAD - NIGHT', location: 'EXT. NIGHT ROAD', time_of_day: 'NIGHT', start_ms: 46000, end_ms: 62000,
    action: 'The car disappears into the unlit road.', dialogue: [], shots: [],
  },
]

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

const scenes = computed(() => store.currentProject?.current_version?.scenes ?? demoScenes)
const selectedScene = computed(() => scenes.value[activeScene.value] ?? scenes.value[0])
const suggestions = computed<DisplaySuggestion[]>(() => {
  const source = store.currentProject?.current_version?.suggestions
  if (!source) return demoSuggestions.value
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
const projectTitle = computed(() => store.currentProject?.title ?? 'Glass House')
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
const selectedAspectImage = computed(() => {
  const shot = selectedShot.value
  if (!shot) return null
  return shot.media_variants.find((item) => item.media_type === 'image' && item.aspect_ratio === mediaAspect.value)
    ?? (store.currentProject?.aspect_ratio === mediaAspect.value ? shot.image : null)
})
const selectedAspectClip = computed(() => {
  const shot = selectedShot.value
  if (!shot) return null
  return shot.media_variants.find((item) => item.media_type === 'clip' && item.aspect_ratio === mediaAspect.value)
    ?? (store.currentProject?.aspect_ratio === mediaAspect.value ? shot.clip : null)
})
const selectedShotScene = computed(() => scenes.value.find((scene) => scene.shots.some((shot) => shot.id === selectedShot.value?.id)))
const previewShot = computed(() => allShots.value[previewShotIndex.value])
const nextPreviewShot = computed(() => allShots.value[previewShotIndex.value + 1] ?? null)
const previewAspect = computed<AspectRatio>(() => activeStage.value === 'final-cut' ? finalCutAspect.value : mediaAspect.value)
function previewMedia(shot: Shot | null | undefined, mediaType: 'image' | 'clip') {
  if (!shot) return null
  const exact = shot.media_variants.find((item) => item.media_type === mediaType && item.aspect_ratio === previewAspect.value)
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

function imageJobForShot(shotId: string) {
  return store.imageJobs[shotId] ?? null
}

function defaultCharacterIdsForShot(shot: Shot | null | undefined): string[] {
  if (!shot) return []
  const dialogueNames = new Set(
    shot.dialogue.map((line) => line.character.trim().toLowerCase()).filter(Boolean),
  )
  const prompt = `${shot.image_prompt} ${shot.action}`.toLowerCase()
  return castCharacters.value
    .filter((character) => {
      const name = character.name.trim().toLowerCase()
      if (!name || !character.references.length) return false
      return dialogueNames.has(name) || prompt.includes(name)
    })
    .map((character) => character.id)
}

function syncSelectedCharactersForShot(shot: Shot | null | undefined) {
  const defaults = defaultCharacterIdsForShot(shot)
  const valid = new Set(castCharacters.value.map((character) => character.id))
  selectedCharacterIds.value = (defaults.length ? defaults : selectedCharacterIds.value.filter((id) => valid.has(id)))
}

function toggleCharacterForGeneration(characterId: string) {
  if (selectedCharacterIds.value.includes(characterId)) {
    selectedCharacterIds.value = selectedCharacterIds.value.filter((id) => id !== characterId)
  } else {
    selectedCharacterIds.value = [...selectedCharacterIds.value, characterId]
  }
}

watch(() => route.params.projectId, async (projectId) => {
  if (typeof projectId !== 'string' || store.currentProject?.id === projectId) return
  try {
    await store.loadProject(projectId)
  } catch {
    await router.replace('/')
  }
})
watch(() => store.currentProject?.id, () => {
  mediaAspect.value = (store.currentProject?.aspect_ratio as AspectRatio | undefined) ?? '16:9'
}, { immediate: true })
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
  const requestedShot = typeof shotId === 'string' ? shots.find((shot) => shot.id === shotId) : undefined
  activeShotId.value = requestedShot?.id ?? shots[0]?.id ?? null
  if ((stage === 'shots' || stage === 'clips') && activeShotId.value && shotId !== activeShotId.value && store.currentProject) {
    void router.replace(projectRoute(store.currentProject.id, stage, activeShotId.value))
  }
}, { immediate: true })
watch(selectedShot, (shot) => {
  if (!shot) return
  activeShotId.value = shot.id
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

onUnmounted(stopPreviewTimer)
onMounted(async () => {
  const projectId = typeof route.params.projectId === 'string' ? route.params.projectId : undefined
  await store.initialize(projectId)
  if (!projectId && store.projects[0]) {
    await router.replace(projectRoute(store.projects[0].id, defaultStage(store.projects[0])))
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
  return {
    name: 'project-stage',
    params: {
      projectId,
      stage,
      ...((stage === 'shots' || stage === 'clips') && shotId ? { shotId } : {}),
    },
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
    return
  }
  const suggestion = demoSuggestions.value.find((item) => item.id === id)
  if (suggestion) suggestion.status = decision
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
  await store.updateShot(selectedShot.value.id, { ...shotDraft })
}

async function generateShotImage() {
  if (!selectedShot.value) return
  try {
    await saveShot()
    await store.generateShotImage(selectedShot.value.id, mediaAspect.value, selectedCharacterIds.value)
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

function trackCreateBackdropPress(event: PointerEvent) {
  createBackdropPressed.value = event.target === event.currentTarget
}

function closeCreateFromBackdrop(event: MouseEvent) {
  const clickedBackdrop = event.target === event.currentTarget
  if (createBackdropPressed.value && clickedBackdrop) showCreate.value = false
  createBackdropPressed.value = false
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
  if (!selectedShot.value) return
  try {
    await saveShot()
    await store.generateShotClip(selectedShot.value.id, mediaAspect.value)
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

async function generateVariantFrame() {
  if (!selectedShot.value) return
  try {
    await saveShot()
    await store.generateShotImage(selectedShot.value.id, mediaAspect.value, selectedCharacterIds.value)
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
  <main class="studio-layout">
    <aside class="project-rail">
      <div class="rail-section rail-projects">
        <div class="section-label-row">
          <span class="section-label">Projects</span>
          <button class="mini-icon" title="New project" aria-label="Create project" @click="showCreate = true"><Plus :size="15" /></button>
        </div>
        <div
          v-for="project in store.projects"
          :key="project.id"
          class="project-item"
          :class="{ active: store.currentProject?.id === project.id }"
        >
          <button class="project-select" @click="selectProject(project.id)">
            <span class="project-thumb"><Film :size="17" /></span>
            <span class="project-copy"><strong>{{ project.title }}</strong><small>{{ project.status.replace('_', ' ') }}</small></span>
          </button>
          <button
            class="project-menu-trigger"
            :aria-label="`Project actions for ${project.title}`"
            :aria-expanded="projectMenuId === project.id"
            title="Project actions"
            @click="projectMenuId = projectMenuId === project.id ? null : project.id"
          ><MoreHorizontal :size="16" /></button>
          <div v-if="projectMenuId === project.id" class="project-menu">
            <button @click="openDeleteConfirmation(project)"><Trash2 :size="14" />Delete project</button>
          </div>
        </div>
        <button v-if="!store.projects.length" class="project-item active" @click="showCreate = true">
          <span class="project-thumb"><Film :size="17" /></span>
          <span class="project-copy"><strong>Glass House</strong><small>Demo workspace</small></span>
          <MoreHorizontal :size="16" />
        </button>
      </div>

      <div class="rail-section stage-list">
        <span class="section-label">Production</span>
        <RouterLink class="stage" :class="{ active: activeStage === 'script', complete: activeStage !== 'script' }" :to="stageRoute('script')"><MessageSquareText :size="16" /><span>Script review</span><small>{{ pendingCount }}</small></RouterLink>
        <RouterLink class="stage" :class="{ active: activeStage === 'shots' }" :to="stageRoute('shots')"><ListChecks :size="16" /><span>Shot plan</span></RouterLink>
        <RouterLink class="stage" :class="{ active: activeStage === 'storyboards' }" :to="stageRoute('storyboards')"><Image :size="16" /><span>Storyboards</span></RouterLink>
        <RouterLink class="stage" :class="{ active: activeStage === 'clips' }" :to="stageRoute('clips')"><Video :size="16" /><span>Clips</span></RouterLink>
        <RouterLink class="stage" :class="{ active: activeStage === 'final-cut' }" :to="stageRoute('final-cut')"><Film :size="16" /><span>Final cut</span></RouterLink>
      </div>

      <div class="provider-panel">
        <div class="provider-title"><Bot :size="15" /><span>Text provider</span></div>
        <strong>{{ activeProvider?.model ?? 'qwen3:8b' }}</strong>
        <span class="provider-state">
          <span class="status-dot" :class="{ muted: !activeProvider?.available }"></span>
          {{ activeProvider?.available ? 'Connected' : 'Not connected' }}
        </span>
        <button @click="showCreate = true">Configure</button>
      </div>
    </aside>

    <section class="workspace" :class="{ 'final-cut-stage': activeStage === 'final-cut' }">
      <div v-if="store.error" class="error-banner"><CircleAlert :size="15" />{{ store.error }}</div>
      <header class="project-header">
        <div>
          <div class="breadcrumb"><span>Projects</span><ChevronRight :size="14" /><span>{{ projectTitle }}</span></div>
          <h1>{{ stageTitle }}</h1>
        </div>
        <div class="header-actions">
          <span class="save-state"><Check :size="14" /> Saved</span>
          <button class="secondary-button format-guide-trigger" title="Script format guide" @click="showScriptGuide = true"><BookOpen :size="15" /><span>Script format</span></button>
          <button class="secondary-button preview-trigger" @click="openAnimaticPreview"><Play :size="15" fill="currentColor" /> Preview</button>
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
        <div class="progress-step" :class="progressClass(3)"><span><Check v-if="currentProgressStep > 3" :size="13" /><template v-else>3</template></span><strong>Shot plan</strong></div>
        <div class="progress-line" :class="{ complete: currentProgressStep > 3 }"></div>
        <div class="progress-step" :class="progressClass(4)"><span><Check v-if="currentProgressStep > 4" :size="13" /><template v-else>4</template></span><strong>Generate</strong></div>
        <div class="progress-line" :class="{ complete: currentProgressStep > 4 }"></div>
        <div class="progress-step" :class="progressClass(5)"><span>5</span><strong>Assemble</strong></div>
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
            <div class="time-chip"><Clock3 :size="14" /> {{ sceneTime(selectedScene) }}</div>
          </div>

          <article class="screenplay">
            <template v-if="selectedScene.shots.length > 1">
              <section v-for="(shot, shotIndex) in selectedScene.shots" :key="shot.id" class="screenplay-shot">
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
        </aside>
      </div>

      <div v-else-if="activeStage === 'shots'" class="shot-plan-workspace">
        <aside class="shot-browser">
          <div class="shot-browser-heading">
            <div><span class="section-label">Shot list</span><strong>{{ allShots.length }} planned shots</strong></div>
            <span>{{ allShots.filter((shot) => shot.image).length }}/{{ allShots.length }} ready</span>
          </div>
          <div class="shot-list">
            <section v-for="(scene, sceneIndex) in scenes" :key="scene.id" class="shot-scene-group">
              <header><strong>{{ String(sceneIndex + 1).padStart(2, '0') }} {{ scene.title }}</strong><span>{{ shotCountLabel(scene.shots.length) }}</span></header>
              <button
                v-for="shot in scene.shots"
                :key="shot.id"
                class="shot-list-item"
                :class="{ active: selectedShot?.id === shot.id }"
                @click="selectShot(shot.id)"
              >
                <span class="shot-list-thumb">
                  <img v-if="shot.image" :src="shot.image.url" alt="" />
                  <Image v-else :size="18" />
                </span>
                <span>
                  <strong>{{ shot.title || `Shot ${String(shot.order_index + 1).padStart(2, '0')}` }}</strong>
                  <small>{{ shot.framing }} · {{ (shot.duration_ms / 1000).toFixed(1) }}s</small>
                  <small v-if="store.isProcessingShot(shot.id)" class="shot-job-copy">{{ imageJobForShot(shot.id)?.message || 'Starting image generation' }}</small>
                </span>
                <RefreshCw v-if="store.isProcessingShot(shot.id)" class="shot-processing-icon" :size="15" />
                <Check v-else-if="shot.image" class="shot-ready-icon" :size="15" />
              </button>
            </section>
          </div>
        </aside>

        <section v-if="selectedShot" class="shot-editor">
          <header class="shot-editor-header">
            <div><span class="eyebrow">{{ selectedShotLabel }}</span><h2>{{ selectedShotScene?.title }}</h2></div>
            <div class="media-variant-switch" role="group" aria-label="Storyboard image aspect ratio">
              <button type="button" :class="{ active: mediaAspect === '16:9' }" @click="mediaAspect = '16:9'">16:9</button>
              <button type="button" :class="{ active: mediaAspect === '9:16' }" @click="mediaAspect = '9:16'">9:16 Shorts</button>
            </div>
            <span class="image-source" :class="{ ready: selectedAspectImage }">{{ selectedAspectImage ? selectedAspectImage.source.replace('_', ' ') : `No ${mediaAspect} image` }}</span>
          </header>

          <div class="storyboard-area">
            <div class="storyboard-frame" :class="{ portrait: mediaAspect === '9:16' }">
              <img v-if="selectedAspectImage" :src="selectedAspectImage.url" :alt="`${mediaAspect} storyboard for ${selectedShotLabel}`" />
              <div v-else class="empty-storyboard"><Image :size="30" /><strong>No {{ mediaAspect }} storyboard yet</strong><span>Generate from the image prompt or upload a frame for this format.</span></div>
              <div v-if="store.isProcessingShot(selectedShot.id)" class="render-overlay"><RefreshCw :size="24" /><strong>{{ selectedImageJob?.message || 'Rendering storyboard' }}</strong></div>
            </div>

            <section class="cast-library">
              <header>
                <div>
                  <span class="section-label">Cast library</span>
                  <strong>Reusable face references</strong>
                </div>
                <form class="cast-add" @submit.prevent="addCastCharacter">
                  <input v-model="newCharacterName" type="text" maxlength="120" placeholder="Add character (e.g. MARA)" :disabled="castingBusy" />
                  <button class="secondary-button" type="submit" :disabled="castingBusy || !newCharacterName.trim()"><Plus :size="15" />Add</button>
                </form>
              </header>
              <div v-if="!castCharacters.length" class="cast-empty">Add named cast members, upload face stills, then select them when generating for consistency.</div>
              <div v-else class="cast-list">
                <article v-for="character in castCharacters" :key="character.id" class="cast-card">
                  <div class="cast-card-head">
                    <input
                      class="cast-name-input"
                      :value="character.name"
                      @change="renameCastCharacter(character, $event)"
                      :disabled="castingBusy"
                    />
                    <button class="icon-danger-button" type="button" title="Remove character" aria-label="Remove character" :disabled="castingBusy" @click="removeCastCharacter(character.id)">
                      <Trash2 :size="14" />
                    </button>
                  </div>
                  <div class="cast-refs">
                    <div v-for="reference in character.references" :key="reference.id" class="cast-ref">
                      <img :src="reference.url" :alt="`${character.name} reference`" />
                      <button type="button" class="cast-ref-remove" title="Remove reference" aria-label="Remove reference" :disabled="castingBusy" @click="removeCastReference(reference.id)"><X :size="12" /></button>
                    </div>
                    <button class="cast-ref-add" type="button" :disabled="castingBusy" @click="chooseCastReference(character.id)">
                      <Upload :size="14" />
                      Add face
                    </button>
                  </div>
                </article>
              </div>
              <input ref="castRefInput" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" @change="handleCastReferenceUpload" />
            </section>

            <div v-if="castCharacters.length" class="cast-generate-chips">
              <span class="section-label">Use face refs</span>
              <div class="cast-chip-row">
                <button
                  v-for="character in castCharacters"
                  :key="character.id"
                  type="button"
                  class="cast-chip"
                  :class="{ active: selectedCharacterIds.includes(character.id), disabled: !character.references.length }"
                  :disabled="!character.references.length || store.isProcessingShot(selectedShot.id)"
                  :title="character.references.length ? `Use ${character.name} face references` : `Upload a face reference for ${character.name} first`"
                  @click="toggleCharacterForGeneration(character.id)"
                >
                  {{ character.name }}
                  <small v-if="!character.references.length">no ref</small>
                </button>
              </div>
              <p v-if="selectedCast.length" class="cast-chip-hint">Generating with {{ selectedCast.map((item) => item.name).join(', ') }}</p>
            </div>

            <div class="storyboard-actions">
              <button class="primary-button" :disabled="store.isProcessingShot(selectedShot.id) || !imageGenerationAvailable" @click="generateShotImage">
                <RefreshCw v-if="selectedAspectImage" :size="15" /><Sparkles v-else :size="15" />
                {{ selectedAspectImage ? `Regenerate ${mediaAspect} image` : `Generate ${mediaAspect} image` }}
              </button>
              <button class="secondary-button" :disabled="store.isProcessingShot(selectedShot.id)" @click="chooseUpload"><Upload :size="15" />Upload image</button>
              <button v-if="selectedAspectImage" class="icon-danger-button" :title="`Delete ${mediaAspect} image`" :aria-label="`Delete ${mediaAspect} shot image`" @click="deleteShotImageCandidate = true"><Trash2 :size="16" /></button>
              <input ref="uploadInput" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" @change="handleUpload" />
            </div>
            <div v-if="selectedImageJob" class="image-job-status" :class="selectedImageJob.status">
              <div>
                <strong>{{ selectedImageJob.message }}</strong>
                <span v-if="selectedImageJob.provider_phase">{{ selectedImageJob.provider_phase.replace('_', ' ') }}</span>
                <span v-if="selectedImageJob.status === 'failed' && selectedImageJob.error" class="image-job-error">{{ selectedImageJob.error }}</span>
              </div>
              <a v-if="selectedImageJob.conversation_url" :href="selectedImageJob.conversation_url" target="_blank" rel="noopener noreferrer"><ExternalLink :size="14" />Open generation chat</a>
              <button v-if="selectedImageJob.status === 'failed'" class="secondary-button" @click="generateShotImage"><RefreshCw :size="14" />Retry</button>
            </div>
          </div>

          <form class="shot-fields" @submit.prevent="saveShot">
            <div class="shot-field-row">
              <label>Framing<input v-model="shotDraft.framing" list="framing-options" /><datalist id="framing-options"><option value="Wide" /><option value="Medium" /><option value="Close-up" /><option value="Extreme close-up" /><option value="Over-the-shoulder" /><option value="Insert" /></datalist></label>
              <label>Duration (seconds)<input type="number" min="1" max="30" step="0.5" :value="shotDraft.duration_ms / 1000" @input="setShotDuration" /></label>
            </div>
            <label>Image prompt<textarea v-model="shotDraft.image_prompt" rows="5" required></textarea></label>
            <label>Video prompt<textarea v-model="shotDraft.video_prompt" rows="3"></textarea></label>
            <label>Title-card text (optional)<textarea v-model="shotDraft.title_card_text" rows="2" placeholder="Rendered over black during the final two seconds."></textarea></label>
            <footer>
              <span>{{ selectedAspectImage?.source === 'generated' ? `${selectedAspectImage.provider} renderer · ${mediaAspect}` : selectedAspectImage ? `Manual upload · ${mediaAspect}` : imageGenerationAvailable ? `${imageProviderOrder} · ${mediaAspect}` : 'Image providers offline · manual upload available' }}</span>
              <button class="secondary-button"><Save :size="15" />Save shot</button>
            </footer>
          </form>
        </section>

        <div v-else class="empty-shot-plan"><ListChecks :size="28" /><strong>No shots were created</strong><span>Return to script review and add shot prompts before continuing.</span></div>
      </div>

      <div v-else-if="activeStage === 'clips'" class="shot-plan-workspace clips-workspace">
        <aside class="shot-browser">
          <div class="shot-browser-heading">
            <div><span class="section-label">Clip list</span><strong>{{ allShots.length }} planned clips</strong></div>
            <span>{{ allShots.filter((shot) => shot.clip).length }}/{{ allShots.length }} ready</span>
          </div>
          <div class="shot-list">
            <section v-for="(scene, sceneIndex) in scenes" :key="scene.id" class="shot-scene-group">
              <header><strong>{{ String(sceneIndex + 1).padStart(2, '0') }} {{ scene.title }}</strong><span>{{ shotCountLabel(scene.shots.length) }}</span></header>
              <button
                v-for="shot in scene.shots"
                :key="shot.id"
                class="shot-list-item"
                :class="{ active: selectedShot?.id === shot.id }"
                @click="selectShot(shot.id, 'clips')"
              >
                <span class="shot-list-thumb clip-thumb">
                  <img v-if="shot.image" :src="shot.image.url" alt="" />
                  <Video v-else :size="18" />
                  <span v-if="shot.clip" class="clip-play-mark"><Play :size="10" fill="currentColor" /></span>
                </span>
                <span><strong>{{ shot.title || `Shot ${String(shot.order_index + 1).padStart(2, '0')}` }}</strong><small>{{ (shot.duration_ms / 1000).toFixed(1) }}s · {{ shot.clip ? 'video ready' : shot.image ? 'frame ready' : 'needs frame' }}</small></span>
                <Check v-if="shot.clip" class="shot-ready-icon" :size="15" />
              </button>
            </section>
          </div>
        </aside>

        <section v-if="selectedShot" class="shot-editor clip-editor">
          <header class="shot-editor-header">
            <div><span class="eyebrow">{{ selectedShotLabel }}</span><h2>{{ selectedShot.title || selectedShotScene?.title }}</h2></div>
            <div class="media-variant-switch" role="group" aria-label="Media aspect ratio"><button type="button" :class="{ active: mediaAspect === '16:9' }" @click="mediaAspect = '16:9'">16:9</button><button type="button" :class="{ active: mediaAspect === '9:16' }" @click="mediaAspect = '9:16'">9:16 Shorts</button></div>
            <span class="image-source" :class="{ ready: selectedAspectClip }">{{ selectedAspectClip ? selectedAspectClip.source.replace('_', ' ') : 'No clip' }}</span>
          </header>

          <div class="video-provider-bar" :class="{ ready: store.videoProvider?.configured && store.videoProvider?.available }">
            <span class="provider-dot"></span>
            <div><strong>{{ store.videoProvider?.label || 'ComfyUI' }}</strong><small>{{ store.videoProvider?.detail || 'Checking local video provider' }}</small></div>
          </div>

          <div class="storyboard-area clip-area">
            <div class="storyboard-frame clip-frame" :class="{ portrait: mediaAspect === '9:16' }">
              <video v-if="selectedAspectClip" :key="selectedAspectClip.url" :src="selectedAspectClip.url" controls playsinline preload="metadata"></video>
              <img v-else-if="selectedAspectImage" :src="selectedAspectImage.url" :alt="`Starting frame for ${selectedShotLabel}`" />
              <div v-else class="empty-storyboard"><Video :size="30" /><strong>No starting frame</strong><span>Generate or upload an image in Shot plan before creating this clip.</span></div>
              <div v-if="store.processingClipId === selectedShot.id" class="render-overlay"><RefreshCw :size="24" /><strong>Generating video</strong><span>This runs in the dedicated video queue.</span></div>
            </div>
            <p v-if="store.error" class="form-error clip-error">{{ store.error }}</p>
            <div class="storyboard-actions">
              <button
                class="primary-button"
                :disabled="store.processingClipId === selectedShot.id || !selectedAspectImage || !store.videoProvider?.configured || !store.videoProvider?.available"
                :title="store.videoProvider?.detail"
                @click="generateShotClip"
              >
                <RefreshCw v-if="selectedAspectClip" :size="15" /><Sparkles v-else :size="15" />
                {{ selectedAspectClip ? 'Regenerate video' : 'Generate video' }}
              </button>
              <button v-if="!selectedAspectImage" class="secondary-button" :disabled="store.isProcessingShot(selectedShot.id)" @click="generateVariantFrame"><Image :size="15" />Generate {{ mediaAspect }} frame</button>
              <button class="secondary-button" :disabled="store.processingClipId === selectedShot.id" @click="chooseClipUpload"><Upload :size="15" />Upload video</button>
              <button v-if="selectedAspectClip" class="secondary-button" @click="openTrimEditor"><Scissors :size="15" />Trim</button>
              <button v-if="selectedAspectClip" class="icon-danger-button" title="Delete video" aria-label="Delete shot video" @click="deleteShotClipCandidate = true"><Trash2 :size="16" /></button>
              <input ref="clipUploadInput" class="visually-hidden" type="file" accept="video/mp4,video/webm,video/quicktime,.mov" @change="handleClipUpload" />
            </div>
          </div>

          <form class="shot-fields clip-fields" @submit.prevent="saveShot">
            <label>Video prompt<textarea v-model="shotDraft.video_prompt" rows="6" placeholder="Describe subject motion, camera motion, expressions, environment, and continuity." required></textarea></label>
            <label>Title-card text (optional)<textarea v-model="shotDraft.title_card_text" rows="2" placeholder="Rendered over black during the final two seconds."></textarea></label>
            <div class="shot-field-row">
              <label>Duration (seconds)<input type="number" min="1" max="30" step="0.5" :value="shotDraft.duration_ms / 1000" @input="setShotDuration" /></label>
              <label>Starting frame<input :value="selectedAspectImage ? `${mediaAspect} image ready` : `Missing ${mediaAspect} image`" disabled /></label>
            </div>
            <footer>
              <span>{{ selectedAspectClip ? `${selectedAspectClip.provider} · ${selectedAspectClip.mime_type}` : 'MP4, WebM, or MOV · 250 MB maximum' }}</span>
              <button class="secondary-button"><Save :size="15" />Save prompt</button>
            </footer>
          </form>
        </section>

        <div v-else class="empty-shot-plan"><Video :size="28" /><strong>No shots were created</strong><span>Approve a script with shots before creating clips.</span></div>
      </div>

      <TimelineEditor v-else-if="activeStage === 'final-cut' && store.currentProject" :project="store.currentProject" :shots="allShots" @variant-change="setFinalCutVariant" />

      <section v-else class="stage-placeholder">
        <Image v-if="activeStage === 'storyboards'" :size="30" />
        <Film v-else :size="30" />
        <strong>{{ stageTitle }}</strong>
        <span>{{ activeStage === 'storyboards' ? 'No storyboard sequence yet.' : 'No assembled cut yet.' }}</span>
      </section>
    </section>
  </main>

  <div v-if="showCreate" class="modal-backdrop" @pointerdown="trackCreateBackdropPress" @click="closeCreateFromBackdrop">
    <form class="project-modal" @submit.prevent="submitProject">
      <header><div><span class="section-label">New project</span><h2>Import screenplay</h2></div><button type="button" class="mini-icon" aria-label="Close" @click="showCreate = false"><X :size="17" /></button></header>
      <label>Project title<input v-model="form.title" required /></label>
      <div class="form-grid">
        <label>Target length (optional)<select v-model="form.target_duration_seconds"><option :value="0">Automatic from script</option><option :value="30">30 seconds</option><option :value="60">1 minute</option><option :value="180">3 minutes</option><option :value="300">5 minutes</option></select><small>Planning guidance only. Supplied scenes and timestamps are preserved.</small></label>
        <label>Aspect ratio<select v-model="form.aspect_ratio"><option value="16:9">Landscape 16:9</option><option value="9:16">Portrait 9:16</option></select></label>
      </div>
      <label>Text provider<select v-model="form.text_provider"><option v-for="provider in store.providers" :key="provider.id" :value="provider.id" :disabled="!provider.available">{{ provider.label }} · {{ provider.model }}{{ provider.available ? '' : ' (offline)' }}</option><option v-if="!store.providers.length" value="mock">Prototype parser</option></select></label>
      <div class="screenplay-input">
        <div class="field-heading"><label for="screenplay-source">Screenplay</label><button type="button" @click="showScriptGuide = true"><BookOpen :size="13" />Format guide</button></div>
        <textarea id="screenplay-source" v-model="form.script" rows="13" required spellcheck="false"></textarea>
      </div>
      <div
        v-if="store.currentJob && store.busy"
        class="job-progress"
        :class="{ indeterminate: store.currentJob.status === 'running' && store.currentJob.progress < 0.5 }"
      >
        <span :style="{ width: `${store.currentJob.progress * 100}%` }"></span>
        <small>{{ store.currentJob.message }} · {{ formatElapsed(store.jobElapsedSeconds) }}</small>
      </div>
      <p v-if="store.error" class="form-error">{{ store.error }}</p>
      <footer><button type="button" class="secondary-button" @click="showCreate = false">Cancel</button><button class="primary-button" :disabled="store.busy"><Sparkles :size="15" />{{ store.busy ? 'Analyzing...' : 'Analyze script' }}</button></footer>
    </form>
  </div>

  <div v-if="showScriptGuide" class="modal-backdrop guide-backdrop" @click.self="showScriptGuide = false">
    <section class="guide-modal" role="dialog" aria-modal="true" aria-labelledby="script-guide-title">
      <header>
        <div><span class="section-label">Reference</span><h2 id="script-guide-title">Structured script format</h2></div>
        <button class="mini-icon" aria-label="Close script format guide" @click="showScriptGuide = false"><X :size="17" /></button>
      </header>
      <div class="guide-summary">
        <p>Traditional screenplays are supported, but this format preserves every supplied shot and timecode before AI review.</p>
        <div><strong>Recognized fields</strong><code>SHOT N - TITLE</code><code>Scene:</code><code>Location:</code><code>Time:</code><code>Framing:</code><code>Action:</code><code>Dialogue:</code><code>Sound:</code><code>Image prompt:</code><code>Video prompt:</code></div>
      </div>
      <div class="guide-prompt-heading"><div><span class="section-label">Reusable prompt</span><strong>Give this to your script generator</strong></div><button class="secondary-button" @click="copyScriptGuide"><Check v-if="scriptGuideCopied" :size="14" /><Copy v-else :size="14" />{{ scriptGuideCopied ? 'Copied' : 'Copy prompt' }}</button></div>
      <pre>{{ SCRIPT_GENERATION_PROMPT }}</pre>
    </section>
  </div>

  <div v-if="showContinuation" class="modal-backdrop" @click.self="!store.continuingScript && (showContinuation = false)">
    <form class="project-modal continuation-modal" @submit.prevent="submitContinuation">
      <header><div><span class="section-label">Continue project</span><h2>Append screenplay</h2></div><button type="button" class="mini-icon" aria-label="Close" :disabled="store.continuingScript" @click="showContinuation = false"><X :size="17" /></button></header>
      <div class="continuation-summary"><span>Current cut</span><strong>{{ projectDuration }}</strong><span>Text provider</span><strong>{{ activeProvider?.label || store.currentProject?.text_provider }}</strong></div>
      <label>Planning target for this section<input v-model.number="continuationForm.target_duration_seconds" type="number" min="5" max="1800" step="5" required /><small>This guides scene timing but does not cap the finished movie.</small></label>
      <div class="screenplay-input">
        <div class="field-heading"><label for="continuation-source">New screenplay section</label><button type="button" @click="showScriptGuide = true"><BookOpen :size="13" />Format guide</button></div>
        <textarea id="continuation-source" v-model="continuationForm.script" rows="13" required spellcheck="false" placeholder="Paste only the new scenes or shots that continue the story."></textarea>
      </div>
      <div v-if="store.currentJob && store.continuingScript" class="job-progress" :class="{ indeterminate: store.currentJob.status === 'running' && store.currentJob.progress < 0.5 }"><span :style="{ width: `${store.currentJob.progress * 100}%` }"></span><small>{{ store.currentJob.message }} · {{ formatElapsed(store.jobElapsedSeconds) }}</small></div>
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
            <video v-if="previewClip" ref="previewVideo" :src="previewClip.url" playsinline preload="auto" @loadedmetadata="syncPreviewVideo" @canplay="syncPreviewVideo"></video>
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

  <div v-if="deleteCandidate" class="modal-backdrop" @click.self="deleteCandidate = null">
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

  <div v-if="deleteShotImageCandidate" class="modal-backdrop" @click.self="deleteShotImageCandidate = false">
    <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-shot-image-title">
      <header><div class="danger-icon"><Trash2 :size="18" /></div><button class="mini-icon" aria-label="Close" @click="deleteShotImageCandidate = false"><X :size="17" /></button></header>
      <div><h2 id="delete-shot-image-title">Delete this {{ mediaAspect }} storyboard image?</h2><p>The shot, prompts, and other aspect-ratio image will remain.</p></div>
      <footer><button class="secondary-button" @click="deleteShotImageCandidate = false">Cancel</button><button class="danger-button" :disabled="Boolean(selectedShot && store.isProcessingShot(selectedShot.id))" @click="confirmDeleteShotImage"><Trash2 :size="15" />Delete {{ mediaAspect }} image</button></footer>
    </section>
  </div>

  <div v-if="deleteShotClipCandidate" class="modal-backdrop" @click.self="deleteShotClipCandidate = false">
    <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-shot-clip-title">
      <header><div class="danger-icon"><Trash2 :size="18" /></div><button class="mini-icon" aria-label="Close" @click="deleteShotClipCandidate = false"><X :size="17" /></button></header>
      <div><h2 id="delete-shot-clip-title">Delete this shot video?</h2><p>The shot, storyboard image, and prompts will remain. Only the generated or uploaded video will be removed.</p></div>
      <footer><button class="secondary-button" @click="deleteShotClipCandidate = false">Cancel</button><button class="danger-button" :disabled="store.processingClipId === selectedShot?.id" @click="confirmDeleteShotClip"><Trash2 :size="15" />Delete video</button></footer>
    </section>
  </div>

  <div v-if="deleteFinalCutCandidate" class="modal-backdrop" @click.self="deleteFinalCutCandidate = false">
    <section class="confirm-modal" role="dialog" aria-modal="true" aria-labelledby="delete-final-cut-title">
      <header><div class="danger-icon"><Trash2 :size="18" /></div><button class="mini-icon" aria-label="Close" @click="deleteFinalCutCandidate = false"><X :size="17" /></button></header>
      <div><h2 id="delete-final-cut-title">Delete the rendered final cut?</h2><p>Your individual clips, storyboard images, prompts, and timeline will remain.</p></div>
      <footer><button class="secondary-button" @click="deleteFinalCutCandidate = false">Cancel</button><button class="danger-button" @click="confirmDeleteFinalCut"><Trash2 :size="15" />Delete final cut</button></footer>
    </section>
  </div>
</template>
