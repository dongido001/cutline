<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PublishPanel from '@/components/PublishPanel.vue'
import ReleaseKitPanel from '@/components/ReleaseKitPanel.vue'
import LutPicker from '@/components/LutPicker.vue'
import FrameStylePicker from '@/components/FrameStylePicker.vue'
import AudioLibraryPanel from '@/components/AudioLibraryPanel.vue'
import SaveToLibraryControl from '@/components/SaveToLibraryControl.vue'
import StemSeparationControls from '@/components/StemSeparationControls.vue'
import { fetchLutCube, LutGlRenderer } from '@/lib/lutGl'
import {
  api,
  DEFAULT_LUT_PRESETS,
  lutCssFilter,
  registerLutPresets,
  videoColorBoostFilter,
  type AspectRatio,
  type LayoutTemplatePreset,
  type LUTPreset,
  type OutputVariant,
  type LibraryAudio,
  type ProjectDetail,
  type ProjectTimeline,
  type ReleaseFrameSegment,
  type ProjectVariantExport,
  type Shot,
  type StemSeparatorId,
  type StemSeparatorStatus,
  type TimelineAsset,
  type TimelineAudioOverlay,
  type TimelineCanvasLayout,
  type TimelineGeometry,
  type TimelineImageOverlay,
  type TimelinePrimaryItem,
  type TimelineSource,
  type TimelineTransition,
  type TimelineTransitionKind,
  type TimelineVideoOverlay,
} from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import { AlignCenter, Boxes, Library, Camera, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, CircleAlert, CopyPlus, Crosshair, Download, Film, Frame, GripVertical, ImagePlus, Keyboard, Layers, LoaderCircle, MessageSquare, MoveLeft, MoveRight, Music, Package, Palette, Pause, Pencil, Play, Plus, Redo2, RefreshCw, Repeat, Save, Scissors, Share2, SkipBack, SkipForward, Split, Trash2, Type, Undo2, Upload, Volume2, VolumeX, X, ZoomIn, ZoomOut } from '@lucide/vue'

const props = defineProps<{ project: ProjectDetail; shots: Shot[]; compilationId?: string }>()
const emit = defineEmits<{ variantChange: [variant: OutputVariant]; openCompilationModal: [compilationId: string] }>()
const store = useStudioStore()
const route = useRoute()
const router = useRouter()
const timeline = ref<ProjectTimeline | null>(null)
const compilationExports = ref<ProjectVariantExport[]>([])

interface PersistedTimelineUiState {
  isMuted?: boolean
  zoomLevel?: number
  playheadMs?: number
  selectedKind?: 'primary' | 'video' | 'image' | 'text' | 'audio'
  selectedId?: string
  variant?: OutputVariant
  scrollLeft?: number
}

const GLOBAL_PREFS_KEY = 'cutline:timeline-prefs'

function uiStorageKey(projectId = props.project.id, episodeId = store.currentEpisodeId, compilationId = props.compilationId): string {
  if (compilationId) {
    return `cutline:timeline-ui:${projectId}:compilation:${compilationId}`
  }
  return `cutline:timeline-ui:${projectId}:${episodeId || 'default'}`
}

function readPersistedUiState(projectId = props.project.id, episodeId = store.currentEpisodeId, compilationId = props.compilationId): PersistedTimelineUiState {
  const result: PersistedTimelineUiState = {}
  try {
    const rawGlobal = localStorage.getItem(GLOBAL_PREFS_KEY)
    if (rawGlobal) {
      const parsedGlobal = JSON.parse(rawGlobal) as { isMuted?: boolean }
      if (typeof parsedGlobal?.isMuted === 'boolean') result.isMuted = parsedGlobal.isMuted
    }
  } catch {}
  try {
    const rawScoped =
      localStorage.getItem(uiStorageKey(projectId, episodeId, compilationId)) ||
      (!compilationId && episodeId ? localStorage.getItem(uiStorageKey(projectId, null)) : null)
    if (rawScoped) {
      const parsed = JSON.parse(rawScoped) as PersistedTimelineUiState
      if (parsed && typeof parsed === 'object') Object.assign(result, parsed)
    }
  } catch {}
  return result
}

const initialUiState = readPersistedUiState()
const requestedVariant = route.query.variant
const requestedAspect = route.query.aspect
const variant = ref<OutputVariant>(
  requestedVariant === 'shorts' || requestedVariant === 'landscape'
    ? requestedVariant
    : requestedAspect === '9:16' || requestedAspect === '16:9'
      ? requestedAspect === '9:16' ? 'shorts' : 'landscape'
    : initialUiState.variant === 'shorts' || initialUiState.variant === 'landscape'
      ? initialUiState.variant
      : props.project.aspect_ratio === '9:16' ? 'shorts' : 'landscape',
)
const selectedKind = ref<'primary' | 'video' | 'image' | 'text' | 'audio'>(
  initialUiState.selectedKind === 'video' || initialUiState.selectedKind === 'image' || initialUiState.selectedKind === 'text' || initialUiState.selectedKind === 'audio'
    ? initialUiState.selectedKind
    : 'primary',
)
const selectedId = ref(typeof initialUiState.selectedId === 'string' ? initialUiState.selectedId : '')
export type SelectedTrackKind = 'primary' | 'video' | 'image' | 'text' | 'audio'
export interface SelectedEntry {
  kind: SelectedTrackKind
  id: string
}
const selectedEntries = ref<SelectedEntry[]>(
  selectedId.value ? [{ kind: selectedKind.value, id: selectedId.value }] : []
)
const playheadMs = ref(typeof initialUiState.playheadMs === 'number' && initialUiState.playheadMs >= 0 ? initialUiState.playheadMs : 0)
const playing = ref(false)
const loopPlayback = ref(false)
const isMuted = ref(Boolean(initialUiState.isMuted))
const previewVideo = ref<HTMLVideoElement | null>(null)
const previewImage = ref<HTMLImageElement | null>(null)
const lutCanvas = ref<HTMLCanvasElement | null>(null)
const lutGl = ref<LutGlRenderer | null>(null)
const lutGlActive = ref(false)
const lutGlSupported = LutGlRenderer.isSupported()
const sourceDurationMs = ref(0)
const timelineScrollRef = ref<HTMLElement | null>(null)
const addMenuRef = ref<HTMLDetailsElement | null>(null)
const containerWidth = ref(1000)
const zoomLevel = ref(
  typeof initialUiState.zoomLevel === 'number' && initialUiState.zoomLevel >= 1 && initialUiState.zoomLevel <= 10
    ? initialUiState.zoomLevel
    : 1,
)
const currentScrollLeft = ref(typeof initialUiState.scrollLeft === 'number' && initialUiState.scrollLeft >= 0 ? initialUiState.scrollLeft : 0)
const showShortcutsModal = ref(false)
const showUnresolvedNotesModal = ref(false)
const dragSourceIndex = ref<number | null>(null)
const dragOverIndex = ref<number | null>(null)
const primaryTrackRef = ref<HTMLElement | null>(null)
const overlayToPrimaryDropMs = ref<number | null>(null)
const dragDuplicateModifier = ref(false)
// Publish modal state is URL-driven (?publish=1) so refresh preserves it
const showPublishModal = computed(() => route.query.publish === '1')

function formatTimecode(ms: number): string {
  const clamped = Math.max(0, Math.round(ms))
  const totalSeconds = clamped / 1000
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.floor(totalSeconds % 60)
  const tenths = Math.floor((clamped % 1000) / 100)
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${tenths}`
}

function closeAddMenu() {
  if (addMenuRef.value) {
    addMenuRef.value.open = false
  }
}

function handleDocumentPointerDown(event: PointerEvent) {
  if (event.target instanceof Element && !event.target.closest('.transition-popover, .transition-marker')) {
    transitionPopoverIndex.value = null
  }
  if (!addMenuRef.value?.open) return
  const target = event.target
  if (target instanceof Node && !addMenuRef.value.contains(target)) {
    addMenuRef.value.open = false
  }
}

function persistUiState() {
  const payload: PersistedTimelineUiState = {
    isMuted: isMuted.value,
    zoomLevel: zoomLevel.value,
    playheadMs: Math.round(playheadMs.value),
    selectedKind: selectedKind.value,
    selectedId: selectedId.value,
    variant: variant.value,
    scrollLeft: timelineScrollRef.value ? Math.round(timelineScrollRef.value.scrollLeft) : Math.round(currentScrollLeft.value),
  }
  try {
    localStorage.setItem(GLOBAL_PREFS_KEY, JSON.stringify({ isMuted: isMuted.value }))
    localStorage.setItem(uiStorageKey(), JSON.stringify(payload))
  } catch {}
}

function handleTimelineScroll() {
  if (!timelineScrollRef.value) return
  currentScrollLeft.value = timelineScrollRef.value.scrollLeft
  persistUiState()
}

function openPublishModal() {
  router.replace({ query: { ...route.query, publish: '1' } })
}

function closePublishModal() {
  const q = { ...route.query }
  delete q.publish
  router.replace({ query: q })
}

const showReleaseKit = computed(() => route.query.kit === '1')
const releaseKitRefresh = ref(0)
const capturingFrame = ref(false)
const frameNotice = ref('')
let frameNoticeTimer: number | null = null
const releaseKitScope = computed(() => ({
  variant: variant.value,
  episodeId: props.compilationId ? null : store.currentEpisodeId || null,
  compilationId: props.compilationId || null,
}))

function openReleaseKit() {
  const q = { ...route.query }
  delete q.publish
  q.kit = '1'
  router.replace({ query: q })
}

function closeReleaseKit() {
  const q = { ...route.query }
  delete q.kit
  router.replace({ query: q })
}

function closeModalsFromRoute() {
  if (showPublishModal.value || showReleaseKit.value) {
    const q = { ...route.query }
    delete q.publish
    delete q.kit
    router.replace({ query: q })
  }
}

async function captureReleaseFrame() {
  const item = activeItem.value
  if (!item?.source || item.source.kind === 'shot_audio') {
    localError.value = 'Move the playhead over a video or image clip to grab a frame.'
    return
  }
  capturingFrame.value = true
  try {
    await flushPendingSave()
    const kit = await api.releaseKit(props.project.id, releaseKitScope.value)
    const updated = await api.addReleaseKitFrame(kit.id, {
      source: item.source,
      source_ms: Math.max(0, Math.round(primarySourceMs(item, activeLocalMs.value))),
      timeline_ms: Math.max(0, Math.round(playheadMs.value)),
      label: item.label,
    })
    releaseKitRefresh.value += 1
    frameNotice.value = `Frame added to the ${variant.value === 'shorts' ? 'Shorts' : 'landscape'} release kit (${updated.frames.length}/8)`
    if (frameNoticeTimer !== null) window.clearTimeout(frameNoticeTimer)
    frameNoticeTimer = window.setTimeout(() => { frameNotice.value = '' }, 3500)
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Could not grab the frame'
  } finally {
    capturingFrame.value = false
  }
}

function updateContainerWidth() {
  if (timelineScrollRef.value) {
    const available = timelineScrollRef.value.clientWidth - 110
    containerWidth.value = Math.max(240, available)
  }
}

const trackWidthPx = computed(() => {
  const base = containerWidth.value
  return Math.round(base * zoomLevel.value)
})

function zoomIn() {
  zoomLevel.value = Math.min(10, Math.round((zoomLevel.value + 0.5) * 10) / 10)
  nextTick(() => scrollPlayheadIntoView())
}

function zoomOut() {
  zoomLevel.value = Math.max(1, Math.round((zoomLevel.value - 0.5) * 10) / 10)
  nextTick(() => scrollPlayheadIntoView())
}

function resetZoom() {
  zoomLevel.value = 1
  currentScrollLeft.value = 0
  nextTick(() => {
    if (timelineScrollRef.value) timelineScrollRef.value.scrollLeft = 0
    scrollPlayheadIntoView()
  })
}

function handleTimelineWheel(event: WheelEvent) {
  if (event.ctrlKey || event.metaKey || event.altKey) {
    event.preventDefault()
    if (event.deltaY < 0) zoomIn()
    else if (event.deltaY > 0) zoomOut()
  }
}

function scrollPlayheadIntoView() {
  if (zoomLevel.value <= 1 || !timelineScrollRef.value) return
  const container = timelineScrollRef.value
  const targetX = (playheadPercent.value / 100) * trackWidthPx.value + 70
  const scrollLeft = container.scrollLeft
  const clientWidth = container.clientWidth
  if (targetX < scrollLeft + 60 || targetX > scrollLeft + clientWidth - 60) {
    container.scrollLeft = Math.max(0, targetX - clientWidth / 2)
    currentScrollLeft.value = container.scrollLeft
  }
}
const loading = ref(true)
const saving = ref(false)
const saveState = ref('Saved')
const localError = ref('')
const history = ref<string[]>([])
const future = ref<string[]>([])
let saveTimer: number | undefined
let pendingSave = false
let dirtyDocument = false
let playbackTimer: number | undefined
type OverlayItem = { id: string; start_ms: number; duration_ms: number; lane?: number | null }
type OverlayEditMode = 'move' | 'resize-start' | 'resize-end'
let overlayEdit: {
  pointerId: number
  kind: 'video' | 'image' | 'text' | 'audio'
  item: OverlayItem
  mode: OverlayEditMode
  startX: number
  startY: number
  trackTop: number
  trackWidth: number
  initialStart: number
  initialDuration: number
  initialLane: number
  laneCount: number
  before: string
  moved: boolean
} | null = null

const targetAspect = computed<AspectRatio>(() => variant.value === 'shorts' ? '9:16' : '16:9')
const variantExport = computed(() => {
  if (props.compilationId) {
    return compilationExports.value.find((item) => item.variant === variant.value) ?? null
  }
  return props.project.variant_exports?.find((item) => item.variant === variant.value) ?? null
})
const downloadFilename = computed(() => {
  if (props.compilationId) {
    const compTitle = store.activeCompilation?.title || 'Compilation'
    return `${props.project.title}_${compTitle}_${variant.value}.mp4`.replace(/[\s/\\:]+/g, '_')
  }
  const epPart = store.currentEpisode ? `_${store.episodeLabel}_${store.currentEpisode.episode_number}` : ''
  return `${props.project.title}${epPart}_${variant.value}.mp4`.replace(/[\s/\\:]+/g, '_')
})
const primary = computed(() => timeline.value?.document.primary ?? [])
const totalMs = computed(() => primary.value.reduce((sum, item) => sum + item.duration_ms, 0))
const playheadPercent = computed(() => totalMs.value ? Math.min(100, Math.max(0, playheadMs.value / totalMs.value * 100)) : 0)
const timelineTicks = computed(() => {
  const width = trackWidthPx.value
  const totalSec = totalMs.value / 1000
  if (totalSec <= 0 || width <= 0) return []
  const pxPerSec = width / totalSec
  let intervalSec = 30
  if (pxPerSec >= 120) intervalSec = 1
  else if (pxPerSec >= 60) intervalSec = 2
  else if (pxPerSec >= 25) intervalSec = 5
  else if (pxPerSec >= 12) intervalSec = 10
  else if (pxPerSec >= 6) intervalSec = 15
  else if (pxPerSec >= 3) intervalSec = 30
  else intervalSec = 60

  const count = Math.floor(totalSec / intervalSec)
  const ticks = []
  for (let i = 0; i <= count; i++) {
    const sec = i * intervalSec
    if (sec > totalSec) break
    const percent = (sec / totalSec) * 100
    const minutes = Math.floor(sec / 60)
    const remainder = Math.round(sec % 60)
    ticks.push({
      percent,
      label: `${minutes}:${String(remainder).padStart(2, '0')}`,
    })
  }
  return ticks
})
const starts = computed(() => {
  let elapsed = 0
  return primary.value.map((item) => {
    const value = elapsed
    elapsed += item.duration_ms
    return value
  })
})
const selectedPrimaryIndex = computed(() => primary.value.findIndex((item) => item.id === selectedId.value))
const selectedPrimary = computed(() => selectedPrimaryIndex.value >= 0 ? primary.value[selectedPrimaryIndex.value] : null)
const selectedTrimOutMs = computed(() => selectedPrimary.value ? selectedPrimary.value.source_out_ms ?? selectedPrimary.value.source_in_ms + selectedPrimary.value.duration_ms : 0)
const trimMaximumMs = computed(() => Math.max(sourceDurationMs.value, selectedTrimOutMs.value, 100))
const trimRangeStyle = computed(() => {
  const max = trimMaximumMs.value
  const inMs = selectedPrimary.value?.source_in_ms ?? 0
  const outMs = selectedTrimOutMs.value
  const left = Math.max(0, Math.min(100, (inMs / max) * 100))
  const width = Math.max(2, Math.min(100 - left, ((outMs - inMs) / max) * 100))
  return {
    left: `${left}%`,
    width: `${width}%`,
  }
})
const videoOverlays = computed(() => timeline.value?.document.video_overlays ?? [])
const selectedVideo = computed(() => videoOverlays.value.find((item) => item.id === selectedId.value) ?? null)
const selectedVideoIndex = computed(() => videoOverlays.value.findIndex((item) => item.id === selectedId.value))
const selectedImage = computed(() => timeline.value?.document.image_overlays.find((item) => item.id === selectedId.value) ?? null)
const selectedText = computed(() => timeline.value?.document.text_overlays.find((item) => item.id === selectedId.value) ?? null)
const audioOverlays = computed(() => timeline.value?.document.audio_overlays ?? [])
const selectedAudio = computed(() => audioOverlays.value.find((item) => item.id === selectedId.value) ?? null)
const activeIndex = computed(() => {
  const found = starts.value.findIndex((start, index) => playheadMs.value >= start && playheadMs.value < start + (primary.value[index]?.duration_ms ?? 0))
  return found < 0 ? Math.max(0, primary.value.length - 1) : found
})
const activeItem = computed(() => primary.value[activeIndex.value] ?? null)
const activeLocalMs = computed(() => playheadMs.value - (starts.value[activeIndex.value] ?? 0))
const TRANSITION_OPTIONS: { kind: TimelineTransitionKind; label: string }[] = [
  { kind: 'cut', label: 'Cut (no transition)' },
  { kind: 'fade', label: 'Crossfade' },
  { kind: 'dissolve', label: 'Grain dissolve' },
  { kind: 'fadeblack', label: 'Dip to black' },
  { kind: 'fadewhite', label: 'Dip to white' },
  { kind: 'wipeleft', label: 'Wipe left' },
  { kind: 'wiperight', label: 'Wipe right' },
  { kind: 'slideleft', label: 'Slide left' },
  { kind: 'slideright', label: 'Slide right' },
]
const transitionLabel = (kind: TimelineTransitionKind) => TRANSITION_OPTIONS.find((option) => option.kind === kind)?.label ?? kind
const defaultTransition = computed(() => timeline.value?.document.default_transition ?? null)
const transitionPopoverIndex = ref<number | null>(null)
const transitionDurationDrafts = ref<Record<string, number>>({})
function transitionDuration(key: string | number, durationMs: number) {
  return transitionDurationDrafts.value[key] ?? durationMs / 1000
}
/** Transition into primary[index] after the default and the clip lengths are applied; null is a hard cut. */
function effectiveTransition(index: number): { kind: TimelineTransitionKind; duration_ms: number } | null {
  const item = primary.value[index]
  const previous = primary.value[index - 1]
  if (!item || !previous) return null
  const transition = item.transition ?? defaultTransition.value
  if (!transition || transition.kind === 'cut') return null
  return { kind: transition.kind, duration_ms: Math.min(transition.duration_ms, previous.duration_ms, item.duration_ms) }
}
function setDefaultTransition(kind: TimelineTransitionKind, durationMs = defaultTransition.value?.duration_ms ?? 500) {
  delete transitionDurationDrafts.value.default
  if (!timeline.value) return
  mutate(() => {
    timeline.value!.document.default_transition = kind === 'cut' ? null : { kind, duration_ms: durationMs }
  })
}
function setItemTransition(index: number, transition: TimelineTransition | null) {
  delete transitionDurationDrafts.value[index]
  const item = primary.value[index]
  if (!item) return
  mutate(() => {
    item.transition = transition
  })
}
function onTransitionKindChange(index: number, value: string) {
  if (value === 'default') return setItemTransition(index, null)
  const current = primary.value[index]?.transition ?? defaultTransition.value
  setItemTransition(index, { kind: value as TimelineTransitionKind, duration_ms: current?.duration_ms ?? 500 })
}
function onTransitionDurationChange(index: number, seconds: number) {
  const item = primary.value[index]
  const base = item?.transition ?? defaultTransition.value
  if (!base || base.kind === 'cut') return
  setItemTransition(index, { kind: base.kind, duration_ms: Math.round(Math.max(0.1, Math.min(2, seconds)) * 1000) })
}
watch([selectedId, () => primary.value.length], () => { transitionPopoverIndex.value = null })
/** Preview stand-in for the rendered blend: the picture dips towards the cut and recovers after it. */
const transitionPreviewStyle = computed((): Record<string, string> => {
  const index = activeIndex.value
  const item = primary.value[index]
  if (!item) return {}
  const local = activeLocalMs.value
  const into = effectiveTransition(index)
  const out = effectiveTransition(index + 1)
  let nearest: { kind: TimelineTransitionKind; progress: number } | null = null
  if (into && local < into.duration_ms / 2) nearest = { kind: into.kind, progress: local / (into.duration_ms / 2) }
  const toEnd = item.duration_ms - local
  if (out && toEnd < out.duration_ms / 2) {
    const progress = toEnd / (out.duration_ms / 2)
    if (!nearest || progress < nearest.progress) nearest = { kind: out.kind, progress }
  }
  if (!nearest) return {}
  const p = Math.max(0, Math.min(1, nearest.progress))
  if (nearest.kind === 'fadewhite') return { filter: `brightness(${(1 + (1 - p) * 3).toFixed(3)})` }
  const floor = nearest.kind === 'fadeblack' ? 0 : 0.35
  return { opacity: (floor + (1 - floor) * p).toFixed(3) }
})
const releaseFrameSegments = computed<ReleaseFrameSegment[]>(() =>
  primary.value.flatMap((item, index) => {
    if (!item.source || item.source.kind === 'shot_audio') return []
    const media = resolveSource(item.source)
    if (!media?.url) return []
    return [{
      id: item.id,
      start_ms: starts.value[index] ?? 0,
      duration_ms: item.duration_ms,
      source: item.source,
      source_in_ms: item.source_in_ms,
      label: item.label,
      url: media.url,
      mime_type: media.mime_type || '',
    }]
  }),
)

const isMultiSelect = computed(() => selectedEntries.value.length > 1)

function isSelected(kind: SelectedTrackKind, id: string): boolean {
  if (selectedEntries.value.length > 1) {
    return selectedEntries.value.some((entry) => entry.kind === kind && entry.id === id)
  }
  return selectedKind.value === kind && selectedId.value === id
}

function getItemLabel(entry: { kind: string; id: string }): string {
  if (entry.kind === 'primary') {
    return primary.value.find((p) => p.id === entry.id)?.label || 'Primary Clip'
  }
  if (entry.kind === 'video') {
    return videoOverlays.value.find((v) => v.id === entry.id)?.label || 'Video Layer'
  }
  if (entry.kind === 'audio') {
    return audioOverlays.value.find((a) => a.id === entry.id)?.label || 'Audio Layer'
  }
  if (entry.kind === 'image') {
    const img = timeline.value?.document.image_overlays.find((i) => i.id === entry.id)
    return img ? (resolveSource(img.source)?.label || 'Image Layer') : 'Image Layer'
  }
  if (entry.kind === 'text') {
    return timeline.value?.document.text_overlays.find((t) => t.id === entry.id)?.text || 'Text Layer'
  }
  return 'Item'
}

const multiSelectSummary = computed(() => {
  const pCount = selectedEntries.value.filter((e) => e.kind === 'primary').length
  const vCount = selectedEntries.value.filter((e) => e.kind === 'video').length
  const aCount = selectedEntries.value.filter((e) => e.kind === 'audio').length
  const iCount = selectedEntries.value.filter((e) => e.kind === 'image').length
  const tCount = selectedEntries.value.filter((e) => e.kind === 'text').length

  const parts: string[] = []
  if (pCount) parts.push(`${pCount} Primary`)
  if (vCount) parts.push(`${vCount} Video`)
  if (aCount) parts.push(`${aCount} Audio`)
  if (iCount) parts.push(`${iCount} Image`)
  if (tCount) parts.push(`${tCount} Text`)
  return parts.join(', ') || `${selectedEntries.value.length} items`
})

const eligibleSplitCount = computed(() => {
  const curPlayhead = Math.round(playheadMs.value)
  const entries = selectedEntries.value.length > 0 ? selectedEntries.value : [{ kind: 'primary' as SelectedTrackKind, id: activeItem.value?.id ?? '' }]
  let count = 0
  for (const entry of entries) {
    if (entry.kind === 'primary') {
      const pIdx = primary.value.findIndex((p, idx) => {
        const s = starts.value[idx] ?? 0
        return p.id === entry.id || (entries.length === 1 && curPlayhead >= s && curPlayhead < s + p.duration_ms)
      })
      if (pIdx >= 0) {
        const item = primary.value[pIdx]
        const s = starts.value[pIdx] ?? 0
        const local = curPlayhead - s
        if (item && item.kind === 'video' && local >= 100 && item.duration_ms - local >= 100) count++
      }
    } else if (entry.kind === 'video') {
      const item = videoOverlays.value.find((v) => v.id === entry.id)
      if (item && curPlayhead >= item.start_ms + 100 && curPlayhead <= item.start_ms + item.duration_ms - 100) count++
    } else if (entry.kind === 'audio') {
      const item = audioOverlays.value.find((a) => a.id === entry.id)
      if (item && curPlayhead >= item.start_ms + 100 && curPlayhead <= item.start_ms + item.duration_ms - 100) count++
    }
  }
  return count
})

const canSplitAtPlayhead = computed(() => eligibleSplitCount.value > 0)

function toggleSelectEntry(kind: SelectedTrackKind, id: string) {
  const existingIdx = selectedEntries.value.findIndex((e) => e.kind === kind && e.id === id)
  if (existingIdx >= 0) {
    selectedEntries.value.splice(existingIdx, 1)
    if (selectedEntries.value.length === 1) {
      const first = selectedEntries.value[0]
      if (first) {
        selectedKind.value = first.kind
        selectedId.value = first.id
      }
    } else if (selectedEntries.value.length === 0) {
      selectedKind.value = 'primary'
      selectedId.value = primary.value[0]?.id ?? ''
      selectedEntries.value = [{ kind: 'primary', id: selectedId.value }]
    }
  } else {
    selectedEntries.value.push({ kind, id })
    selectedKind.value = kind
    selectedId.value = id
  }
}

function clearMultiSelection() {
  const first = selectedEntries.value[0]
  if (first) {
    selectedEntries.value = [first]
    selectedKind.value = first.kind
    selectedId.value = first.id
  }
}

watch(
  [selectedKind, selectedId],
  ([k, id]) => {
    if (selectedEntries.value.length <= 1) {
      if (k && id) {
        selectedEntries.value = [{ kind: k, id }]
      }
    }
  },
  { immediate: true },
)

function ensureCropFields(geom: TimelineGeometry): TimelineGeometry {
  if (typeof geom.crop_top !== 'number') geom.crop_top = 0
  if (typeof geom.crop_bottom !== 'number') geom.crop_bottom = 0
  if (typeof geom.crop_left !== 'number') geom.crop_left = 0
  if (typeof geom.crop_right !== 'number') geom.crop_right = 0
  if (typeof geom.offset_x !== 'number') geom.offset_x = 0
  if (typeof geom.offset_y !== 'number') geom.offset_y = 0
  if (typeof geom.focal_x !== 'number') geom.focal_x = 0.5
  if (typeof geom.focal_y !== 'number') geom.focal_y = 0.5
  if (typeof geom.lut !== 'string') geom.lut = 'none'
  if (typeof geom.lut_strength !== 'number') geom.lut_strength = 1
  return geom
}

const lutPresets = ref<LUTPreset[]>(DEFAULT_LUT_PRESETS)
const layoutCatalog = ref<{ shorts: LayoutTemplatePreset[]; landscape: LayoutTemplatePreset[] }>({
  shorts: [],
  landscape: [],
})

function defaultCanvasLayout(): TimelineCanvasLayout {
  return {
    template: 'full_bleed',
    matte: 'solid',
    matte_color: '#0B0D12',
    matte_color_b: '#1A2233',
    top_text: '',
    bottom_text: '',
    brand_text: '',
    accent_bar: false,
    accent_color: '#39D0FF',
  }
}

function ensureCanvasLayout(): TimelineCanvasLayout {
  if (!timeline.value) return defaultCanvasLayout()
  const current = timeline.value.document.layout
  if (!current || typeof current !== 'object') {
    timeline.value.document.layout = defaultCanvasLayout()
  } else {
    timeline.value.document.layout = { ...defaultCanvasLayout(), ...current }
  }
  return timeline.value.document.layout!
}

const canvasLayout = computed(() => {
  const current = timeline.value?.document?.layout
  return current ? { ...defaultCanvasLayout(), ...current } : defaultCanvasLayout()
})
const layoutFamily = computed(() => (variant.value === 'shorts' ? 'shorts' : 'landscape'))
const layoutPresets = computed<LayoutTemplatePreset[]>(() => {
  const fromApi = layoutCatalog.value[layoutFamily.value] || []
  if (fromApi.length) return fromApi
  // Offline fallback so the picker is never empty before /layouts loads.
  const family: 'shorts' | 'landscape' = layoutFamily.value
  return family === 'shorts'
    ? [
        { id: 'full_bleed', label: 'Full bleed', blurb: 'Edge-to-edge', family, video: { x: 0, y: 0, w: 1, h: 1 }, default_matte: 'solid' as const, slots: {} },
        { id: 'boxed', label: 'Boxed', blurb: '16:9 video boxed in center · top & bottom space', family, video: { x: 0, y: 0.3418, w: 1, h: 0.3164 }, default_matte: 'solid' as const, slots: { top: 'hook', bottom: 'caption' } },
        { id: 'center_stage', label: 'Center stage', blurb: 'Hook + caption bands', family, video: { x: 0, y: 0.16, w: 1, h: 0.68 }, default_matte: 'blur' as const, slots: { top: 'hook', bottom: 'caption' } },
        { id: 'top_hero', label: 'Top hero', blurb: 'CTA band below', family, video: { x: 0, y: 0, w: 1, h: 0.7 }, default_matte: 'gradient' as const, slots: { bottom: 'caption' } },
        { id: 'bottom_anchor', label: 'Bottom anchor', blurb: 'Hook band on top', family, video: { x: 0, y: 0.3, w: 1, h: 0.7 }, default_matte: 'gradient' as const, slots: { top: 'hook' } },
        { id: 'caption_stack', label: 'Caption stack', blurb: 'Tall caption band', family, video: { x: 0, y: 0, w: 1, h: 0.58 }, default_matte: 'solid' as const, slots: { bottom: 'caption' } },
        { id: 'split_billboard', label: 'Split billboard', blurb: 'Title-card energy', family, video: { x: 0, y: 0.22, w: 1, h: 0.56 }, default_matte: 'duotone' as const, slots: { top: 'hook', bottom: 'brand' }, accent_bar: true },
      ]
    : [
        { id: 'full_bleed', label: 'Full bleed', blurb: 'Edge-to-edge', family, video: { x: 0, y: 0, w: 1, h: 1 }, default_matte: 'solid' as const, slots: {} },
        { id: 'cinematic_bars', label: 'Cinematic bars', blurb: 'Letterbox prestige', family, video: { x: 0, y: 0.12, w: 1, h: 0.76 }, default_matte: 'solid' as const, slots: { top: 'hook', bottom: 'brand' }, accent_bar: true },
        { id: 'brand_footer', label: 'Brand footer', blurb: 'Logo strip', family, video: { x: 0, y: 0, w: 1, h: 0.88 }, default_matte: 'gradient' as const, slots: { bottom: 'brand' } },
        { id: 'lower_third_room', label: 'Lower-third room', blurb: 'Nameplate space', family, video: { x: 0, y: 0, w: 1, h: 0.78 }, default_matte: 'blur' as const, slots: { bottom: 'caption' } },
      ]
})
const activeLayoutPreset = computed(
  () => layoutPresets.value.find((p) => p.id === canvasLayout.value.template) || layoutPresets.value[0] || null,
)
const layoutVideoRect = computed(() => activeLayoutPreset.value?.video || { x: 0, y: 0, w: 1, h: 1 })
const isFullBleedLayout = computed(
  () =>
    Math.abs(layoutVideoRect.value.x) < 1e-6 &&
    Math.abs(layoutVideoRect.value.y) < 1e-6 &&
    Math.abs(layoutVideoRect.value.w - 1) < 1e-6 &&
    Math.abs(layoutVideoRect.value.h - 1) < 1e-6,
)
const layoutSectionEl = ref<HTMLDetailsElement | null>(null)

function openLayoutSection() {
  if (layoutSectionEl.value) layoutSectionEl.value.open = true
}

watch(
  isFullBleedLayout,
  (fullBleed) => {
    if (!fullBleed) nextTick(openLayoutSection)
  },
  { immediate: true },
)

function applyLayoutTemplate(preset: LayoutTemplatePreset) {
  if (!timeline.value) return
  mutate(() => {
    const layout = ensureCanvasLayout()
    layout.template = preset.id
    layout.matte = preset.default_matte || layout.matte
    layout.accent_bar = Boolean(preset.accent_bar)
  })
  const fullBleed =
    Math.abs(preset.video.x) < 1e-6 &&
    Math.abs(preset.video.y) < 1e-6 &&
    Math.abs(preset.video.w - 1) < 1e-6 &&
    Math.abs(preset.video.h - 1) < 1e-6
  if (!fullBleed) nextTick(openLayoutSection)
}

function updateCanvasLayout<K extends keyof TimelineCanvasLayout>(key: K, value: TimelineCanvasLayout[K]) {
  if (!timeline.value) return
  mutate(() => {
    ensureCanvasLayout()[key] = value
  })
}

function layoutMatteStyle(): Record<string, string> {
  const layout = canvasLayout.value
  if (layout.matte === 'gradient' || layout.matte === 'duotone') {
    return {
      background: `linear-gradient(180deg, ${layout.matte_color} 0%, ${layout.matte_color_b} 100%)`,
    }
  }
  if (layout.matte === 'blur' && activeMedia.value?.url) {
    return {
      backgroundImage: `url(${activeMedia.value.url})`,
      backgroundSize: 'cover',
      backgroundPosition: 'center',
      filter: 'blur(18px) brightness(0.55) saturate(0.7)',
      transform: 'scale(1.08)',
    }
  }
  return { background: layout.matte_color || '#0B0D12' }
}

function resetLut(geom: TimelineGeometry) {
  mutate(() => {
    geom.lut = 'none'
    geom.lut_strength = 1
  })
}

function assignLut(geom: TimelineGeometry, lutId: string) {
  geom.lut = lutId
}

function assignLutStrength(geom: TimelineGeometry, strength: number) {
  geom.lut_strength = Math.max(0, Math.min(1, strength))
}

/** Frame aspect (height / width) of the current output, used to express Y offsets in container-width units. */
const frameHeightOverWidth = computed(() => (targetAspect.value === '9:16' ? 16 / 9 : 9 / 16))

/**
 * CSS `translate` value shifting media by a fraction of the output frame.
 * Uses container-query width units (the program monitor is an inline-size container)
 * so the shift matches the FFmpeg export regardless of the element's own size.
 */
function geometryOffsetTranslate(geom: TimelineGeometry): string | undefined {
  const ox = Number(geom.offset_x ?? 0) || 0
  const oy = Number(geom.offset_y ?? 0) || 0
  if (Math.abs(ox) < 1e-4 && Math.abs(oy) < 1e-4) return undefined
  // Use container width units for X and container height (via cqw * aspect) for Y so
  // offset 1.0 always equals one full frame edge, matching FFmpeg's width/height pads.
  return `${(ox * 100).toFixed(2)}cqw ${(oy * 100 * frameHeightOverWidth.value).toFixed(2)}cqw`
}

/** Track fill style for framing ranges. `center` = bipolar fill from range midpoint (neutral). */
function framingRangeStyle(
  value: number | null | undefined,
  min: number,
  max: number,
  mode: 'start' | 'center' = 'start',
): Record<string, string> {
  const span = max - min || 1
  const raw = Number(value)
  const v = Number.isFinite(raw) ? Math.max(min, Math.min(max, raw)) : min
  const pct = ((v - min) / span) * 100
  if (mode === 'center') {
    // Neutral is the geometric midpoint: 0 for −1…1 offsets, 0.5 for Focus X/Y.
    const mid = (((min + max) / 2 - min) / span) * 100
    const from = Math.min(mid, pct)
    const to = Math.max(mid, pct)
    return {
      '--range-pct': `${pct}%`,
      '--range-mid': `${mid}%`,
      '--range-fill-start': `${from}%`,
      '--range-fill-end': `${to}%`,
    }
  }
  return {
    '--range-pct': `${pct}%`,
    '--range-mid': '0%',
    '--range-fill-start': '0%',
    '--range-fill-end': `${pct}%`,
  }
}

function formatSignedPercent(value: number | null | undefined): string {
  const pct = Math.round((Number(value) || 0) * 100)
  if (pct > 0) return `+${pct}%`
  return `${pct}%`
}

function nudgeGeometryOffset(geom: TimelineGeometry, dx: number, dy: number) {
  mutate(() => {
    const clamp = (v: number) => Math.max(-1, Math.min(1, Math.round(v * 100) / 100))
    geom.offset_x = clamp((geom.offset_x ?? 0) + dx)
    geom.offset_y = clamp((geom.offset_y ?? 0) + dy)
  })
}

function centerGeometryOffset(geom: TimelineGeometry) {
  mutate(() => {
    geom.offset_x = 0
    geom.offset_y = 0
  })
}

function defaultGeometry(item?: { source?: TimelineSource | null }): TimelineGeometry {
  return {
    source_aspect: 'auto',
    fit: 'cover',
    focal_x: 0.5,
    focal_y: 0.5,
    zoom: 1,
    position: 'center',
    scale: 1,
    crop_top: 0,
    crop_bottom: 0,
    crop_left: 0,
    crop_right: 0,
    offset_x: 0,
    offset_y: 0,
    lut: 'none',
    lut_strength: 1,
  }
}
function geometryFor(item: { source?: TimelineSource | null; variant: Partial<Record<OutputVariant, TimelineGeometry>> }) {
  if (!item.variant[variant.value]) item.variant[variant.value] = defaultGeometry(item)
  return ensureCropFields(item.variant[variant.value]!)
}

function defaultVideoOverlayGeometry(item?: { source?: TimelineSource | null }): TimelineGeometry {
  return {
    source_aspect: 'auto',
    fit: 'cover',
    focal_x: 0.5,
    focal_y: 0.5,
    zoom: 1,
    position: 'full-frame',
    scale: 1,
    crop_top: 0,
    crop_bottom: 0,
    crop_left: 0,
    crop_right: 0,
    offset_x: 0,
    offset_y: 0,
    lut: 'none',
    lut_strength: 1,
  }
}
function videoOverlayGeometryFor(item: TimelineVideoOverlay) {
  if (!item.variant[variant.value]) item.variant[variant.value] = defaultVideoOverlayGeometry(item)
  return ensureCropFields(item.variant[variant.value]!)
}

/** Visible window of a cropped/zoomed element as fractions of its own (untransformed) box. */
function geometryCropInsets(
  geom: TimelineGeometry,
  forceClipForZoom = false,
): { top: number; right: number; bottom: number; left: number } | null {
  let cTop = Math.max(0, Math.min(0.85, Number(geom.crop_top ?? 0) || 0))
  let cBottom = Math.max(0, Math.min(0.85, Number(geom.crop_bottom ?? 0) || 0))
  let cLeft = Math.max(0, Math.min(0.85, Number(geom.crop_left ?? 0) || 0))
  let cRight = Math.max(0, Math.min(0.85, Number(geom.crop_right ?? 0) || 0))
  const zoom = Math.max(1, Number(geom.zoom ?? 1) || 1)
  const hasCrop = cTop > 1e-4 || cBottom > 1e-4 || cLeft > 1e-4 || cRight > 1e-4
  if (!hasCrop && !(forceClipForZoom && zoom > 1.001)) {
    return null
  }
  if (cTop + cBottom > 0.9) {
    const scaleV = 0.9 / (cTop + cBottom)
    cTop *= scaleV
    cBottom *= scaleV
  }
  if (cLeft + cRight > 0.9) {
    const scaleH = 0.9 / (cLeft + cRight)
    cLeft *= scaleH
    cRight *= scaleH
  }
  const fx = Math.max(0, Math.min(1, Number(geom.focal_x ?? 0.5)))
  const fy = Math.max(0, Math.min(1, Number(geom.focal_y ?? 0.5)))
  const localLeft = Math.max(0, Math.min(0.95, fx + (cLeft - fx) / zoom))
  const localRight = Math.max(0, Math.min(0.95, 1 - (fx + (1 - cRight - fx) / zoom)))
  const localTop = Math.max(0, Math.min(0.95, fy + (cTop - fy) / zoom))
  const localBottom = Math.max(0, Math.min(0.95, 1 - (fy + (1 - cBottom - fy) / zoom)))
  return { top: localTop, right: localRight, bottom: localBottom, left: localLeft }
}

function geometryCropClipPath(geom: TimelineGeometry, forceClipForZoom = false, roundCqw = 0): string | undefined {
  const insets = geometryCropInsets(geom, forceClipForZoom)
  const round = roundCqw > 1e-3 ? ` round ${roundCqw.toFixed(2)}cqw` : ''
  if (!insets) return round ? `inset(0${round})` : undefined
  return `inset(${(insets.top * 100).toFixed(2)}% ${(insets.right * 100).toFixed(2)}% ${(insets.bottom * 100).toFixed(2)}% ${(insets.left * 100).toFixed(2)}%${round})`
}

function frameRadiusCqw(geom: TimelineGeometry): number {
  return Math.max(0, Math.min(0.25, Number(geom.frame_radius ?? 0) || 0)) * 100
}

/** Border ring + glow for an element laid exactly over a media window (sizes in frame-width units, like the export). */
function mediaBorderStyle(geom: TimelineGeometry | undefined): Record<string, string> | null {
  const style = geom?.frame_style || 'none'
  if (!geom || style === 'none') return null
  const color = /^#[0-9a-f]{6}$/i.test(geom.frame_color || '') ? geom.frame_color! : '#FFFFFF'
  const w = Math.max(0.002, Math.min(0.04, Number(geom.frame_width ?? 0.008) || 0.008)) * 100
  const cq = (v: number) => `${v.toFixed(3)}cqw`
  const borderRadius = cq(frameRadiusCqw(geom))
  if (style === 'glow') {
    const blur = w * 2 + 0.6
    return {
      borderRadius,
      border: `${cq(Math.max(0.1, w / 2))} solid ${color}`,
      boxShadow: `0 0 ${cq(blur * 1.5)} ${cq(w * 0.6)} ${color}, inset 0 0 ${cq(blur)} ${color}`,
    }
  }
  if (style === 'neon') {
    const blur = w * 2 + 0.6
    return {
      borderRadius,
      border: `${cq(w)} solid ${color}`,
      boxShadow: `0 0 ${cq(blur * 2)} ${cq(w)} ${color}, inset 0 0 ${cq(blur)} ${color}, inset 0 0 0 ${cq(w / 3)} rgba(255, 255, 255, 0.65)`,
    }
  }
  return { borderRadius, border: `${cq(w)} ${style} ${color}` }
}

function assignFrame(geom: TimelineGeometry, patch: Partial<TimelineGeometry>) {
  Object.assign(geom, patch)
}

function resetFrame(geom: TimelineGeometry) {
  mutate(() => {
    geom.frame_style = 'none'
    geom.frame_radius = 0
    geom.frame_width = 0.008
    geom.frame_color = '#FFFFFF'
  })
}

function hasFrameDecoration(geom: TimelineGeometry): boolean {
  return (geom.frame_style ?? 'none') !== 'none' || frameRadiusCqw(geom) > 1e-3
}

function hasCropOrZoom(geom: TimelineGeometry): boolean {
  return (
    Math.abs((geom.zoom ?? 1) - 1) > 1e-3 ||
    Math.abs((geom.focal_x ?? 0.5) - 0.5) > 1e-3 ||
    Math.abs((geom.focal_y ?? 0.5) - 0.5) > 1e-3 ||
    (geom.crop_top ?? 0) > 1e-3 ||
    (geom.crop_bottom ?? 0) > 1e-3 ||
    (geom.crop_left ?? 0) > 1e-3 ||
    (geom.crop_right ?? 0) > 1e-3 ||
    Math.abs(geom.offset_x ?? 0) > 1e-3 ||
    Math.abs(geom.offset_y ?? 0) > 1e-3
  )
}

function isCropPresetActive(
  geom: TimelineGeometry,
  preset: 'full' | 'center-box' | 'left-half' | 'right-half' | 'top-half' | 'bottom-half',
): boolean {
  const t = geom.crop_top ?? 0
  const b = geom.crop_bottom ?? 0
  const l = geom.crop_left ?? 0
  const r = geom.crop_right ?? 0
  const eq = (a: number, v: number) => Math.abs(a - v) < 0.015
  if (preset === 'full') return eq(t, 0) && eq(b, 0) && eq(l, 0) && eq(r, 0)
  if (preset === 'center-box') return eq(t, 0.2) && eq(b, 0.2) && eq(l, 0.2) && eq(r, 0.2)
  if (preset === 'left-half') return eq(t, 0) && eq(b, 0) && eq(l, 0) && eq(r, 0.5)
  if (preset === 'right-half') return eq(t, 0) && eq(b, 0) && eq(l, 0.5) && eq(r, 0)
  if (preset === 'top-half') return eq(t, 0) && eq(b, 0.5) && eq(l, 0) && eq(r, 0)
  return eq(t, 0.5) && eq(b, 0) && eq(l, 0) && eq(r, 0)
}

function resetGeometryCropAndZoom(geom: TimelineGeometry) {
  mutate(() => {
    geom.crop_top = 0
    geom.crop_bottom = 0
    geom.crop_left = 0
    geom.crop_right = 0
    geom.zoom = 1
    geom.focal_x = 0.5
    geom.focal_y = 0.5
    geom.offset_x = 0
    geom.offset_y = 0
  })
}

function applyCropPreset(geom: TimelineGeometry, preset: 'full' | 'center-box' | 'left-half' | 'right-half' | 'top-half' | 'bottom-half') {
  mutate(() => {
    if (preset === 'full') {
      geom.crop_top = 0
      geom.crop_bottom = 0
      geom.crop_left = 0
      geom.crop_right = 0
    } else if (preset === 'center-box') {
      geom.crop_top = 0.2
      geom.crop_bottom = 0.2
      geom.crop_left = 0.2
      geom.crop_right = 0.2
    } else if (preset === 'left-half') {
      geom.crop_top = 0
      geom.crop_bottom = 0
      geom.crop_left = 0
      geom.crop_right = 0.5
    } else if (preset === 'right-half') {
      geom.crop_top = 0
      geom.crop_bottom = 0
      geom.crop_left = 0.5
      geom.crop_right = 0
    } else if (preset === 'top-half') {
      geom.crop_top = 0
      geom.crop_bottom = 0.5
      geom.crop_left = 0
      geom.crop_right = 0
    } else if (preset === 'bottom-half') {
      geom.crop_top = 0.5
      geom.crop_bottom = 0
      geom.crop_left = 0
      geom.crop_right = 0
    }
  })
}

function applyFocalPreset(geom: TimelineGeometry, focalX: number) {
  mutate(() => {
    geom.focal_x = Math.max(0, Math.min(1, Math.round(focalX * 100) / 100))
  })
}

function setPrimaryFit(fit: 'cover' | 'contain') {
  if (!selectedPrimary.value) return
  mutate(() => {
    const geom = geometryFor(selectedPrimary.value!)
    geom.fit = fit
    if (fit === 'contain') {
      if ((geom.zoom ?? 1) > 1.01) geom.zoom = 1
      geom.focal_x = 0.5
      geom.focal_y = 0.5
    }
  })
  commitInspectorEdit()
}

function setVideoOverlayFit(fit: 'cover' | 'contain') {
  if (!selectedVideo.value) return
  mutate(() => {
    const geom = videoOverlayGeometryFor(selectedVideo.value!)
    geom.fit = fit
    if (fit === 'contain') {
      if ((geom.zoom ?? 1) > 1.01) geom.zoom = 1
      geom.focal_x = 0.5
      geom.focal_y = 0.5
    }
  })
  commitInspectorEdit()
}

function applyFitToAllClips(fit: 'cover' | 'contain') {
  const primary = timeline.value?.document?.primary
  if (!primary || !primary.length) return
  mutate(() => {
    for (const item of primary) {
      if (!item.variant[variant.value]) {
        item.variant[variant.value] = defaultGeometry(item)
      }
      const geom = ensureCropFields(item.variant[variant.value]!)
      geom.fit = fit
      if (fit === 'contain') {
        if ((geom.zoom ?? 1) > 1.01) geom.zoom = 1
        geom.focal_x = 0.5
        geom.focal_y = 0.5
      }
    }
  })
  commitInspectorEdit()
}

const allClipsFitState = computed<'cover' | 'contain' | 'mixed'>(() => {
  const primary = timeline.value?.document?.primary
  if (!primary || !primary.length) return 'cover'
  const v = variant.value
  let hasCover = false
  let hasContain = false
  for (const item of primary) {
    const geom = item.variant?.[v]
    const fit = geom?.fit || 'cover'
    if (fit === 'contain') hasContain = true
    else hasCover = true
  }
  if (hasCover && hasContain) return 'mixed'
  return hasContain ? 'contain' : 'cover'
})

const programMonitorStyle = computed(() => {
  if (variant.value === 'shorts' && canvasLayout.value?.matte_color) {
    return { backgroundColor: canvasLayout.value.matte_color }
  }
  return {}
})

const isReframingActivePrimary = computed(() => {
  if (!activeItem.value || !activeMedia.value) return false
  if (activeGeometry.value.fit === 'contain') return false
  return targetAspect.value === '9:16' || (activeGeometry.value.zoom ?? 1) > 1.01 || !activeMedia.value.native
})

const isDraggingPrimaryReframe = ref(false)
let reframeStartX = 0
let reframeStartY = 0
let reframeStartFocalX = 0.5
let reframeStartFocalY = 0.5
let reframeWindowWidth = 1
let reframeWindowHeight = 1

function onPrimaryReframePointerDown(event: PointerEvent) {
  if (!isReframingActivePrimary.value || !activeItem.value) return
  if (event.button !== 0) return // only primary mouse button

  // Prevent native browser dragging of video/image and text selection
  event.preventDefault()

  const target = (event.currentTarget as HTMLElement | null) ?? programMonitor.value
  const rect = target?.getBoundingClientRect()
  if (!rect || rect.width <= 0 || rect.height <= 0) return

  // Select the active primary clip if not already selected so inspector coordinates stay in sync
  if (selectedKind.value !== 'primary' || selectedId.value !== activeItem.value.id) {
    selectedKind.value = 'primary'
    selectedId.value = activeItem.value.id
    selectedEntries.value = [{ kind: 'primary', id: activeItem.value.id }]
  }

  // Ensure activeItem.variant[variant.value] exists and is attached to document
  const geom = geometryFor(activeItem.value)
  captureInspectorSnapshot()

  isDraggingPrimaryReframe.value = true
  reframeStartX = event.clientX
  reframeStartY = event.clientY
  reframeWindowWidth = rect.width
  reframeWindowHeight = rect.height
  reframeStartFocalX = Number.isFinite(geom.focal_x) ? (geom.focal_x ?? 0.5) : 0.5
  reframeStartFocalY = Number.isFinite(geom.focal_y) ? (geom.focal_y ?? 0.5) : 0.5

  const onWindowPointerMove = (moveEv: PointerEvent) => {
    if (!isDraggingPrimaryReframe.value || !activeItem.value) return
    moveEv.preventDefault()

    const currentGeom = geometryFor(activeItem.value)
    const deltaX = moveEv.clientX - reframeStartX
    const deltaY = moveEv.clientY - reframeStartY

    // In 9:16 frame with 16:9 media:
    // Video width is 3.16x window width, overflow is 2.16x.
    // Scale factor 1 / 2.16 ≈ 0.463 gives exact 1:1 cursor tracking.
    const normalizedDeltaX = (deltaX / reframeWindowWidth) * 0.463
    const nextFocalX = Math.max(0, Math.min(1, Math.round((reframeStartFocalX - normalizedDeltaX) * 1000) / 1000))
    currentGeom.focal_x = nextFocalX

    const zoom = Math.max(1, Number(currentGeom.zoom ?? 1) || 1)
    if (zoom > 1.01 || targetAspect.value === '16:9') {
      const normalizedDeltaY = (deltaY / reframeWindowHeight) * (targetAspect.value === '16:9' ? 0.463 : 0.463 / zoom)
      const nextFocalY = Math.max(0, Math.min(1, Math.round((reframeStartFocalY - normalizedDeltaY) * 1000) / 1000))
      currentGeom.focal_y = nextFocalY
    }
  }

  const onWindowPointerUp = () => {
    window.removeEventListener('pointermove', onWindowPointerMove)
    window.removeEventListener('pointerup', onWindowPointerUp)
    window.removeEventListener('pointercancel', onWindowPointerUp)
    if (isDraggingPrimaryReframe.value) {
      isDraggingPrimaryReframe.value = false
      commitInspectorEdit()
    }
  }

  window.addEventListener('pointermove', onWindowPointerMove, { passive: false })
  window.addEventListener('pointerup', onWindowPointerUp)
  window.addEventListener('pointercancel', onWindowPointerUp)
}

function shotMedia(shot: Shot, mediaType: 'image' | 'clip') {
  const nativeVariant = shot.media_variants.find((item) => item.media_type === mediaType && item.aspect_ratio === targetAspect.value)
  const legacy = mediaType === 'clip' ? shot.clip : shot.image
  const hasNativeSlot = Boolean(nativeVariant || (props.project.aspect_ratio === targetAspect.value && legacy))
  const selectedId = mediaType === 'clip' ? shot.selected_clip_take_id : shot.selected_image_take_id
  const activeTake = hasNativeSlot
    ? shot.takes?.find(
        (t) =>
          t.media_type === mediaType &&
          t.aspect_ratio === targetAspect.value &&
          (t.selected || (selectedId && t.id === selectedId))
      )
    : null
  const boostStrength = nativeVariant?.color_boost_strength ?? legacy?.color_boost_strength ?? 0
  if (activeTake) return { media: { ...activeTake, color_boost_strength: boostStrength }, native: true }
  if (nativeVariant) return { media: nativeVariant, native: true }
  if (legacy) return { media: legacy, native: props.project.aspect_ratio === targetAspect.value }
  const fallback = shot.media_variants.find((item) => item.media_type === mediaType)
  return fallback ? { media: fallback, native: false } : null
}

function resolveSource(source: TimelineSource | null) {
  if (!source) return null
  if (source.kind === 'asset') {
    const asset = timeline.value?.assets.find((item) => item.id === source.ref_id)
    return asset ? { url: asset.url, mime_type: asset.mime_type, native: true, label: asset.label, color_boost_strength: 0 } : null
  }
  const shot = props.shots.find((item) => item.id === source.ref_id)
  if (!shot) return null
  if (source.kind === 'shot_audio') {
    if (shot.dialogue_audio_filename) {
      return {
        url: `/media/${shot.dialogue_audio_filename}`,
        mime_type: shot.dialogue_audio_mime || 'audio/wav',
        native: true,
        label: `${shot.title} dialogue`,
        color_boost_strength: 0,
      }
    }
    const clipResolved = shotMedia(shot, 'clip')
    if (clipResolved) {
      return { ...clipResolved.media, native: clipResolved.native, label: `${shot.title} audio` }
    }
    return null
  }
  let resolved = shotMedia(shot, source.kind === 'shot_clip' ? 'clip' : 'image')
  if (!resolved && source.kind === 'shot_clip') resolved = shotMedia(shot, 'image')
  if (!resolved) return null
  return { ...resolved.media, native: resolved.native, label: shot.title }
}

function blockThumbnailUrl(item: TimelinePrimaryItem): string | null {
  if (!item.source) return null
  if (item.source.kind === 'asset') {
    const asset = timeline.value?.assets.find((a) => a.id === item.source?.ref_id)
    return asset && asset.mime_type.startsWith('image') ? asset.url : null
  }
  const shot = props.shots.find((s) => s.id === item.source?.ref_id)
  if (!shot) return null
  const still = shotMedia(shot, 'image')
  if (still?.media.url) return still.media.url
  if (shot.image_references?.[0]?.url) return shot.image_references[0].url
  return null
}

const activeMedia = computed(() => resolveSource(activeItem.value?.source ?? null))
const activeGeometry = computed(() => ensureCropFields(activeItem.value?.variant[variant.value] ?? defaultGeometry(activeItem.value ?? undefined)))
const activeMediaLayoutStyle = computed(() => {
  const geom = activeGeometry.value
  const clipPath = geometryCropClipPath(geom)
  const fx = (geom.focal_x ?? 0.5) * 100
  const fy = (geom.focal_y ?? 0.5) * 100
  const zoom = Math.max(1, Number(geom.zoom ?? 1) || 1)
  const translate = geometryOffsetTranslate(geom)
  return {
    objectFit: geom.fit,
    objectPosition: `${fx}% ${fy}%`,
    transformOrigin: `${fx}% ${fy}%`,
    // Keep scale (with focus origin) separate from pan so offsets stay in frame units.
    transform: `scale(${zoom})`,
    ...(translate ? { translate } : {}),
    ...(clipPath ? { clipPath } : {}),
  }
})
/** Where the primary video sits on the canvas: the whole frame, or the layout template's video window. */
const primaryWindowRect = computed(() => {
  const rect = isFullBleedLayout.value ? { x: 0, y: 0, w: 1, h: 1 } : layoutVideoRect.value
  return {
    left: `${rect.x * 100}%`,
    top: `${rect.y * 100}%`,
    width: `${rect.w * 100}%`,
    height: `${rect.h * 100}%`,
  }
})
const primaryWindowStyle = computed(() => {
  const radius = frameRadiusCqw(activeGeometry.value)
  return {
    ...primaryWindowRect.value,
    ...(radius > 1e-3 ? { borderRadius: `${radius.toFixed(3)}cqw` } : {}),
    ...transitionPreviewStyle.value,
  }
})
const primaryBorderStyle = computed(() => mediaBorderStyle(activeGeometry.value))
const activeMediaStyle = computed(() => {
  const geom = activeGeometry.value
  const boostFilter = videoColorBoostFilter(activeMedia.value?.color_boost_strength)
  // When WebGL LUT preview is active, grade runs in the shader (same .cube as FFmpeg).
  const lutFilter = lutGlActive.value ? '' : lutCssFilter(geom.lut, geom.lut_strength)
  const filter = lutGlActive.value
    ? ''
    : [boostFilter, lutFilter].filter(Boolean).join(' ')
  return {
    ...activeMediaLayoutStyle.value,
    ...(filter ? { filter } : {}),
  }
})
const lutCanvasStyle = computed(() => activeMediaLayoutStyle.value)

async function syncLutGlPreview() {
  const geom = activeGeometry.value
  const lutId = geom.lut || 'none'
  const strength = geom.lut_strength ?? 1
  const wantsLut = lutGlSupported && lutId !== 'none' && strength >= 0.02 && Boolean(activeMedia.value)

  if (!wantsLut) {
    lutGl.value?.stop()
    lutGlActive.value = false
    return
  }

  // Show canvas before measuring / binding so clientWidth is non-zero.
  lutGlActive.value = true
  await nextTick()
  const canvas = lutCanvas.value
  const isVideo = Boolean(activeMedia.value?.mime_type.startsWith('video'))
  const source = isVideo ? previewVideo.value : previewImage.value
  if (!canvas || !source) {
    lutGlActive.value = false
    return
  }
  if (!lutGl.value || lutGl.value.canvas !== canvas) {
    lutGl.value?.dispose()
    lutGl.value = new LutGlRenderer(canvas)
  }
  if (!lutGl.value.ok) {
    lutGlActive.value = false
    return
  }

  try {
    const cube = await fetchLutCube(lutId, strength)
    // Bail if selection changed while fetching.
    const gNow = activeGeometry.value
    if ((gNow.lut || 'none') !== lutId) return
    if (Math.round((gNow.lut_strength ?? 1) * 20) !== Math.round(strength * 20)) return
    lutGl.value.setLutCube(cube)
    lutGl.value.setSource(source)
    lutGlActive.value = true
    lutGl.value.start(() => {
      const g = activeGeometry.value
      return {
        fit: (g.fit === 'contain' ? 'contain' : 'cover') as 'cover' | 'contain',
        focalX: g.focal_x ?? 0.5,
        focalY: g.focal_y ?? 0.5,
        boost: Number(activeMedia.value?.color_boost_strength ?? 0) || 0,
        applyLut: true,
      }
    })
  } catch {
    lutGlActive.value = false
    lutGl.value?.stop()
  }
}

watch(
  () => [
    activeGeometry.value.lut,
    activeGeometry.value.lut_strength,
    activeMedia.value?.url,
    activeMedia.value?.mime_type,
    activeMedia.value?.color_boost_strength,
  ],
  () => {
    void syncLutGlPreview()
  },
)
const activeBadge = computed(() => {
  if (!activeMedia.value) return activeItem.value?.kind === 'title' ? 'TITLE' : 'MISSING'
  if (!activeMedia.value.mime_type.startsWith('video')) return 'STILL FALLBACK'
  return activeMedia.value.native ? `NATIVE ${targetAspect.value}` : `REFRAMED ${targetAspect.value === '9:16' ? '16:9' : '9:16'}`
})

function imageOverlayPreviewStyle(overlay: TimelineImageOverlay) {
  const geom = geometryFor(overlay)
  const isFullFrame = geom.position === 'full-frame'
  const lutFilter = lutCssFilter(geom.lut, geom.lut_strength)
  if (isFullFrame) {
    return {
      opacity: overlay.opacity ?? 1,
      objectFit: (geom.fit || 'cover') as 'cover' | 'contain',
      ...(lutFilter ? { filter: lutFilter } : {}),
    }
  }
  const scale = geom.scale ?? 1
  return {
    opacity: overlay.opacity ?? 1,
    width: `${Math.round(45 * scale)}%`,
    maxHeight: `${Math.round(60 * scale)}%`,
    objectFit: 'contain' as const,
    ...(lutFilter ? { filter: lutFilter } : {}),
  }
}

function videoOverlayPreviewStyle(overlay: TimelineVideoOverlay) {
  const geom = videoOverlayGeometryFor(overlay)
  const isFullFrame = geom.position === 'full-frame'
  const resolved = resolveSource(overlay.source)
  const boostFilter = videoColorBoostFilter(resolved?.color_boost_strength)
  const lutFilter = lutCssFilter(geom.lut, geom.lut_strength)
  const filter = [boostFilter, lutFilter].filter(Boolean).join(' ')
  const fx = (geom.focal_x ?? 0.5) * 100
  const fy = (geom.focal_y ?? 0.5) * 100
  const zoom = Math.max(1, Number(geom.zoom ?? 1) || 1)
  // Clip-path is in the element's local box, which scale(zoom) then magnifies.
  const clipPath = geometryCropClipPath(geom, !isFullFrame, frameRadiusCqw(geom) / zoom)
  const translate = geometryOffsetTranslate(geom)
  if (isFullFrame) {
    const objectFit = geom.fit === 'contain' ? 'contain' : (targetAspect.value === '9:16' ? 'fill' : (geom.fit || 'cover'))
    return {
      opacity: overlay.opacity ?? 1,
      objectFit: objectFit as 'contain' | 'cover' | 'fill',
      objectPosition: `${fx}% ${fy}%`,
      transformOrigin: `${fx}% ${fy}%`,
      transform: `scale(${zoom})`,
      ...(translate ? { translate } : {}),
      ...(clipPath ? { clipPath } : {}),
      ...(filter ? { filter } : {}),
    }
  }
  const scale = geom.scale ?? 1
  const pos = geom.position || 'center'
  let baseTransform = ''
  if (pos === 'center') baseTransform = 'translate(-50%, -50%) '
  else if (pos === 'top' || pos === 'bottom') baseTransform = 'translateX(-50%) '
  return {
    opacity: overlay.opacity ?? 1,
    width: `${Math.round(45 * scale)}%`,
    maxHeight: `${Math.round(60 * scale)}%`,
    objectFit: 'contain' as const,
    objectPosition: `${fx}% ${fy}%`,
    transformOrigin: `${fx}% ${fy}%`,
    transform: `${baseTransform}scale(${geom.zoom ?? 1})`,
    ...(translate ? { translate } : {}),
    ...(clipPath ? { clipPath } : {}),
    ...(filter ? { filter } : {}),
  }
}

type LaneItem = { id: string; start_ms: number; duration_ms: number; lane?: number | null }

/** Clip being dragged on an overlay track; it is placed last so no other clip moves rows under it. */
const editingOverlayId = ref<string | null>(null)

/**
 * Rows for an overlay track. Clips keep their saved row; a clip only moves (down, to the nearest
 * free row) when it would sit on top of another clip. Clips without a row fill the first free one.
 */
function packOverlayLanes<T extends LaneItem>(items: T[], lastId: string | null = null) {
  const placed: { item: T; lane: number }[] = []
  const laneById: Record<string, number> = {}
  const isFree = (item: T, lane: number) => !placed.some((other) =>
    other.lane === lane
    && item.start_ms < other.item.start_ms + other.item.duration_ms
    && other.item.start_ms < item.start_ms + item.duration_ms)
  const place = (item: T, from: number) => {
    let lane = Math.max(0, from)
    while (!isFree(item, lane)) lane++
    placed.push({ item, lane })
    laneById[item.id] = lane
  }
  const byStart = (a: T, b: T) => a.start_ms - b.start_ms
  const hasLane = (item: T) => typeof item.lane === 'number'
  const others = items.filter((item) => item.id !== lastId)
  for (const item of others.filter(hasLane).sort(byStart)) place(item, item.lane!)
  for (const item of others.filter((item) => !hasLane(item)).sort(byStart)) place(item, 0)
  const last = items.find((item) => item.id === lastId)
  if (last) place(last, last.lane ?? 0)
  return {
    laneById,
    laneCount: Math.max(1, ...placed.map((entry) => entry.lane + 1)),
  }
}

const videoOverlayPacking = computed(() => packOverlayLanes(videoOverlays.value, editingOverlayId.value))
const imageOverlayPacking = computed(() => packOverlayLanes(timeline.value?.document.image_overlays ?? [], editingOverlayId.value))
const audioOverlayPacking = computed(() => packOverlayLanes(audioOverlays.value, editingOverlayId.value))

// Save each clip's displayed row so later edits elsewhere never reshuffle it.
watch([videoOverlayPacking, imageOverlayPacking, audioOverlayPacking], () => {
  const persist = (items: LaneItem[], laneById: Record<string, number>) => {
    for (const item of items) {
      if (item.id === editingOverlayId.value) continue
      const lane = laneById[item.id]
      if (lane !== undefined && item.lane !== lane) item.lane = lane
    }
  }
  persist(videoOverlays.value, videoOverlayPacking.value.laneById)
  persist(timeline.value?.document.image_overlays ?? [], imageOverlayPacking.value.laneById)
  persist(audioOverlays.value, audioOverlayPacking.value.laneById)
})

let loadedProjectId = props.project.id
let loadedEpisodeId: string | undefined = store.currentEpisodeId || undefined
let timelineLoadSequence = 0

async function loadTimeline() {
  const loadSeq = ++timelineLoadSequence
  loading.value = true
  localError.value = ''
  const reqProjectId = props.project.id
  const reqEpisodeId = store.currentEpisodeId || undefined
  const reqVariant = variant.value
  const persisted = readPersistedUiState(reqProjectId, reqEpisodeId || null)
  if (typeof persisted.isMuted === 'boolean') {
    isMuted.value = persisted.isMuted
  }
  if (typeof persisted.zoomLevel === 'number' && persisted.zoomLevel >= 1 && persisted.zoomLevel <= 10) {
    zoomLevel.value = persisted.zoomLevel
  }
  try {
    let fetched: ProjectTimeline
    if (props.compilationId) {
      fetched = await api.compilationTimeline(props.compilationId, reqVariant)
      try {
        compilationExports.value = await api.compilationExports(props.compilationId)
      } catch {}
    } else {
      fetched = await api.timeline(reqProjectId, reqVariant, reqEpisodeId)
    }
    if (loadSeq !== timelineLoadSequence) return
    fetched.document.video_overlays ??= []
    fetched.document.audio_overlays ??= []
    fetched.document.layout = { ...defaultCanvasLayout(), ...(fetched.document.layout || {}) }
    timeline.value = fetched
    loadedProjectId = reqProjectId
    loadedEpisodeId = reqEpisodeId
    const trimShot = requestedShotId()
    const requestedPrimary = timeline.value.document.primary
    const requestedIndex = !trimShot ? -1 : Math.max(
      requestedPrimary.findIndex((item) => item.source?.ref_id === trimShot && item.kind === 'video'),
      requestedPrimary.findIndex((item) => item.source?.ref_id === trimShot),
    )
    if (requestedIndex >= 0) {
      selectedKind.value = 'primary'
      selectedId.value = timeline.value.document.primary[requestedIndex]?.id ?? ''
      playheadMs.value = timeline.value.document.primary.slice(0, requestedIndex).reduce((sum, item) => sum + item.duration_ms, 0)
    } else {
      const doc = timeline.value.document
      if (persisted.selectedKind === 'video' && persisted.selectedId && (doc.video_overlays ?? []).some((item) => item.id === persisted.selectedId)) {
        selectedKind.value = 'video'
        selectedId.value = persisted.selectedId
      } else if (persisted.selectedKind === 'image' && persisted.selectedId && doc.image_overlays.some((item) => item.id === persisted.selectedId)) {
        selectedKind.value = 'image'
        selectedId.value = persisted.selectedId
      } else if (persisted.selectedKind === 'text' && persisted.selectedId && doc.text_overlays.some((item) => item.id === persisted.selectedId)) {
        selectedKind.value = 'text'
        selectedId.value = persisted.selectedId
      } else if (persisted.selectedKind === 'audio' && persisted.selectedId && (doc.audio_overlays ?? []).some((item) => item.id === persisted.selectedId)) {
        selectedKind.value = 'audio'
        selectedId.value = persisted.selectedId
      } else if (persisted.selectedId && doc.primary.some((item) => item.id === persisted.selectedId)) {
        selectedKind.value = 'primary'
        selectedId.value = persisted.selectedId
      } else {
        selectedKind.value = 'primary'
        selectedId.value = doc.primary[0]?.id ?? ''
      }
      if (typeof persisted.playheadMs === 'number' && Number.isFinite(persisted.playheadMs)) {
        playheadMs.value = Math.max(0, Math.min(totalMs.value, persisted.playheadMs))
      } else {
        playheadMs.value = 0
      }
    }
    history.value = []
    future.value = []
  } catch (cause) {
    if (loadSeq !== timelineLoadSequence) return
    localError.value = cause instanceof Error ? cause.message : 'Could not load timeline'
  } finally {
    if (loadSeq === timelineLoadSequence) {
      loading.value = false
    }
  }
  if (loadSeq !== timelineLoadSequence) return
  await nextTick()
  updateContainerWidth()
  if (timelineScrollRef.value) {
    const trimShot = requestedShotId()
    if (!trimShot && typeof persisted.scrollLeft === 'number' && persisted.scrollLeft > 0) {
      timelineScrollRef.value.scrollLeft = persisted.scrollLeft
      currentScrollLeft.value = persisted.scrollLeft
    } else if (zoomLevel.value > 1) {
      scrollPlayheadIntoView()
    }
  }
  syncVideo()
  persistUiState()
  if (typeof route.query.shot === 'string') {
    const { shot: _consumed, ...query } = route.query
    void router.replace({ query })
  }
}

function requestedShotId(): string | null {
  const value = route.query.trimShot ?? route.query.shot
  return typeof value === 'string' && value ? value : null
}

function shotRefOf(source: TimelineSource | null | undefined): string | null {
  return source && source.kind !== 'asset' ? source.ref_id : null
}

watch(
  () => shotRefOf(selectedPrimary.value?.source)
    ?? shotRefOf(selectedVideo.value?.source)
    ?? shotRefOf(selectedAudio.value?.source)
    ?? shotRefOf(activeItem.value?.source),
  (shotId) => {
    if (shotId) store.focusShotId = shotId
  },
)

function snapshot() {
  return timeline.value ? JSON.stringify(timeline.value.document) : ''
}

let inspectorBeforeSnapshot = ''

function captureInspectorSnapshot() {
  if (!timeline.value) return
  inspectorBeforeSnapshot = snapshot()
}

function commitInspectorEdit() {
  if (!timeline.value) return
  if (selectedKind.value === 'video' && selectedVideo.value) {
    if (selectedVideo.value.duration_ms < 100) selectedVideo.value.duration_ms = 100
    if (selectedVideo.value.source_in_ms < 0) selectedVideo.value.source_in_ms = 0
    if (totalMs.value > 0 && selectedVideo.value.start_ms + selectedVideo.value.duration_ms > totalMs.value) {
      selectedVideo.value.start_ms = Math.max(0, totalMs.value - selectedVideo.value.duration_ms)
    }
  }
  if (selectedKind.value === 'image' && selectedImage.value) {
    if (selectedImage.value.duration_ms < 100) selectedImage.value.duration_ms = 100
    if (totalMs.value > 0 && selectedImage.value.start_ms + selectedImage.value.duration_ms > totalMs.value) {
      selectedImage.value.start_ms = Math.max(0, totalMs.value - selectedImage.value.duration_ms)
    }
  }
  const current = snapshot()
  if (inspectorBeforeSnapshot && inspectorBeforeSnapshot !== current) {
    history.value.push(inspectorBeforeSnapshot)
    if (history.value.length > 50) history.value.shift()
    future.value = []
  }
  inspectorBeforeSnapshot = current
  syncVideoOverlays()
  syncAudioOverlays()
  scheduleSave()
}

function setVideoOverlayDuration(durationMs: number) {
  if (!selectedVideo.value) return
  mutate(() => {
    const clamped = Math.max(100, Math.min(durationMs, totalMs.value || durationMs))
    selectedVideo.value!.duration_ms = clamped
    if (totalMs.value > 0 && selectedVideo.value!.start_ms + clamped > totalMs.value) {
      selectedVideo.value!.start_ms = Math.max(0, totalMs.value - clamped)
    }
  })
}

function moveSelectedVideoLayer(direction: -1 | 1) {
  if (!timeline.value?.document.video_overlays) return
  const index = selectedVideoIndex.value
  const target = index + direction
  if (index < 0 || target < 0 || target >= timeline.value.document.video_overlays.length) return
  mutate(() => {
    const list = timeline.value!.document.video_overlays!
    const [moved] = list.splice(index, 1)
    if (!moved) return
    list.splice(target, 0, moved)
  })
}

function setImageOverlayDuration(durationMs: number) {
  if (!selectedImage.value) return
  mutate(() => {
    selectedImage.value!.duration_ms = durationMs
    if (totalMs.value > 0 && selectedImage.value!.start_ms + durationMs > totalMs.value) {
      selectedImage.value!.start_ms = Math.max(0, totalMs.value - durationMs)
    }
  })
}

function ensureValidSelection() {
  if (!timeline.value) return
  timeline.value.document.video_overlays ??= []
  timeline.value.document.audio_overlays ??= []
  if (selectedKind.value === 'primary' && selectedPrimary.value) return
  if (selectedKind.value === 'video' && selectedVideo.value) return
  if (selectedKind.value === 'text' && selectedText.value) return
  if (selectedKind.value === 'image' && selectedImage.value) return
  if (selectedKind.value === 'audio' && selectedAudio.value) return
  selectedKind.value = 'primary'
  selectedId.value = primary.value[0]?.id ?? ''
}

function mutate(change: () => void) {
  if (!timeline.value) return
  history.value.push(snapshot())
  if (history.value.length > 50) history.value.shift()
  future.value = []
  change()
  inspectorBeforeSnapshot = ''
  nextTick(scheduleSave)
}

function undo() {
  const previous = history.value.pop()
  if (!previous || !timeline.value) return
  future.value.push(snapshot())
  timeline.value.document = JSON.parse(previous)
  ensureValidSelection()
  inspectorBeforeSnapshot = ''
  syncVideo()
  scheduleSave()
}

function redo() {
  const next = future.value.pop()
  if (!next || !timeline.value) return
  history.value.push(snapshot())
  timeline.value.document = JSON.parse(next)
  ensureValidSelection()
  inspectorBeforeSnapshot = ''
  syncVideo()
  scheduleSave()
}

function scheduleSave() {
  if (!timeline.value) return
  dirtyDocument = true
  saveState.value = 'Unsaved'
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(() => {
    saveTimer = undefined
    void saveTimeline()
  }, 650)
}

async function saveTimeline() {
  if (!timeline.value) return
  if (saving.value) {
    pendingSave = true
    return
  }
  window.clearTimeout(saveTimer)
  saveTimer = undefined
  dirtyDocument = false
  saving.value = true
  saveState.value = 'Saving...'
  const targetProjectId = loadedProjectId
  const targetVariant = timeline.value.edit || variant.value
  const targetEpisodeId = loadedEpisodeId
  try {
    for (const item of timeline.value.document.primary) {
      item.duration_ms = Math.round(item.duration_ms)
      item.source_in_ms = Math.round(item.source_in_ms || 0)
      if (item.source_out_ms != null) {
        item.source_out_ms = Math.round(item.source_out_ms)
      }
    }
    timeline.value.document.video_overlays ??= []
    for (const item of timeline.value.document.video_overlays) {
      item.start_ms = Math.round(item.start_ms)
      item.duration_ms = Math.round(item.duration_ms)
      item.source_in_ms = Math.round(item.source_in_ms || 0)
    }
    for (const item of timeline.value.document.image_overlays) {
      item.start_ms = Math.round(item.start_ms)
      item.duration_ms = Math.round(item.duration_ms)
    }
    for (const item of timeline.value.document.text_overlays) {
      item.start_ms = Math.round(item.start_ms)
      item.duration_ms = Math.round(item.duration_ms)
    }
    timeline.value.document.audio_overlays ??= []
    for (const item of timeline.value.document.audio_overlays) {
      item.start_ms = Math.round(item.start_ms)
      item.duration_ms = Math.round(item.duration_ms)
      item.source_in_ms = Math.round(item.source_in_ms || 0)
    }
    const saved = props.compilationId
      ? await api.saveCompilationTimeline(props.compilationId, timeline.value, targetVariant)
      : await api.saveTimeline(targetProjectId, timeline.value, targetVariant, targetEpisodeId)
    saved.document.video_overlays ??= []
    saved.document.audio_overlays ??= []
    if (props.compilationId) {
      if (!pendingSave && !dirtyDocument) {
        timeline.value = saved
      } else if (timeline.value) {
        timeline.value.revision = saved.revision
      }
    } else if (props.project.id === targetProjectId && (timeline.value?.edit || variant.value) === targetVariant && (store.currentEpisodeId || undefined) === targetEpisodeId) {
      if (!pendingSave && !dirtyDocument) {
        timeline.value = saved
      } else if (timeline.value) {
        timeline.value.revision = saved.revision
      }
    }
    if (!pendingSave && !dirtyDocument) {
      saveState.value = 'Saved'
    }
    localError.value = ''
    if (!props.compilationId) {
      await store.loadProject(targetProjectId, { episodeId: targetEpisodeId, resumeJobs: false })
    }
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Could not save timeline'
    saveState.value = 'Save failed'
  } finally {
    saving.value = false
    if (pendingSave || dirtyDocument) {
      pendingSave = false
      void saveTimeline()
    }
  }
}

async function flushPendingSave() {
  if (saveTimer !== undefined || dirtyDocument) {
    window.clearTimeout(saveTimer)
    saveTimer = undefined
    await saveTimeline()
  }
}

function removeInterval(start: number, duration: number) {
  if (!timeline.value) return
  const end = start + duration
  const adjust = <T extends { start_ms: number; duration_ms: number }>(items: T[]) => items.flatMap((item) => {
    const itemEnd = item.start_ms + item.duration_ms
    if (item.start_ms >= end) return [{ ...item, start_ms: item.start_ms - duration }]
    if (itemEnd <= start) return [item]
    if (item.start_ms >= start && itemEnd <= end) return []
    const overlap = Math.min(itemEnd, end) - Math.max(item.start_ms, start)
    const nextDuration = item.duration_ms - overlap
    if (nextDuration < 100) return []
    return [{ ...item, start_ms: Math.min(item.start_ms, start), duration_ms: nextDuration }]
  })
  timeline.value.document.video_overlays = adjust(timeline.value.document.video_overlays ?? [])
  timeline.value.document.image_overlays = adjust(timeline.value.document.image_overlays)
  timeline.value.document.text_overlays = adjust(timeline.value.document.text_overlays)
  timeline.value.document.audio_overlays = adjust(timeline.value.document.audio_overlays ?? [])
}

function insertInterval(start: number, duration: number) {
  if (!timeline.value) return
  for (const item of [
    ...(timeline.value.document.video_overlays ?? []),
    ...timeline.value.document.image_overlays,
    ...timeline.value.document.text_overlays,
    ...(timeline.value.document.audio_overlays ?? []),
  ]) {
    if (item.start_ms >= start) item.start_ms += duration
  }
}

function resizeSelected(event: Event) {
  const item = selectedPrimary.value
  const index = selectedPrimaryIndex.value
  if (!item || index < 0) return
  const nextDuration = Math.max(100, Math.round(Number((event.target as HTMLInputElement).value) * 1000))
  const previousDuration = item.duration_ms
  if (nextDuration === previousDuration) return
  const start = starts.value[index] ?? 0
  mutate(() => {
    if (nextDuration < previousDuration) removeInterval(start + nextDuration, previousDuration - nextDuration)
    else insertInterval(start + previousDuration, nextDuration - previousDuration)
    item.duration_ms = nextDuration
    if (item.kind === 'video') setVideoLength(item, nextDuration, primarySpanMs(item))
  })
}

function primarySpanMs(item: TimelinePrimaryItem): number {
  return item.source_out_ms != null ? item.source_out_ms - item.source_in_ms : item.duration_ms
}

function primarySourceMs(item: TimelinePrimaryItem, localMs: number): number {
  const span = primarySpanMs(item)
  return item.source_in_ms + (item.loop && span > 0 ? localMs % span : localMs)
}

function setVideoLength(item: TimelinePrimaryItem, duration: number, span: number) {
  if (duration > span) {
    item.loop = true
    item.source_out_ms = item.source_in_ms + span
  } else {
    item.loop = false
    item.source_out_ms = item.source_in_ms + duration
  }
}

function applyTrim(sourceInMs: number, sourceOutMs: number) {
  const item = selectedPrimary.value
  const index = selectedPrimaryIndex.value
  if (!item || item.kind !== 'video' || index < 0) return
  const maximum = trimMaximumMs.value
  const nextIn = Math.max(0, Math.min(Math.round(sourceInMs), maximum - 100))
  const nextOut = Math.max(nextIn + 100, Math.min(Math.round(sourceOutMs), maximum))
  const nextDuration = nextOut - nextIn
  const previousDuration = item.duration_ms
  const start = starts.value[index] ?? 0
  mutate(() => {
    if (nextDuration < previousDuration) removeInterval(start + nextDuration, previousDuration - nextDuration)
    else if (nextDuration > previousDuration) insertInterval(start + previousDuration, nextDuration - previousDuration)
    item.source_in_ms = nextIn
    item.source_out_ms = nextOut
    item.duration_ms = nextDuration
    item.loop = false
    playheadMs.value = Math.min(start + nextDuration, Math.max(start, playheadMs.value))
  })
}

function changeTrimIn(event: Event) {
  applyTrim(Number((event.target as HTMLInputElement).value), selectedTrimOutMs.value)
}

function changeTrimOut(event: Event) {
  applyTrim(selectedPrimary.value?.source_in_ms ?? 0, Number((event.target as HTMLInputElement).value))
}

function setTrimAtPlayhead(edge: 'in' | 'out') {
  const item = selectedPrimary.value
  const index = selectedPrimaryIndex.value
  if (!item || item.kind !== 'video' || index < 0) return
  const itemStart = starts.value[index] ?? 0
  const local = Math.max(0, Math.min(item.duration_ms, Math.round(playheadMs.value - itemStart)))
  const sourceTime = Math.round(primarySourceMs(item, local))
  if (edge === 'in') applyTrim(sourceTime, selectedTrimOutMs.value)
  else applyTrim(item.source_in_ms, sourceTime)
}

function resetTrim() {
  const item = selectedPrimary.value
  if (!item || item.kind !== 'video') return
  applyTrim(0, sourceDurationMs.value || selectedTrimOutMs.value)
}

function splitAtPlayhead() {
  if (!timeline.value) return
  const curPlayhead = Math.round(playheadMs.value)
  const toSplitEntries = selectedEntries.value.length > 0
    ? [...selectedEntries.value]
    : [{ kind: 'primary' as SelectedTrackKind, id: activeItem.value?.id ?? '' }]

  let didSplitAny = false
  const newSelectionEntries: SelectedEntry[] = []

  mutate(() => {
    for (const entry of toSplitEntries) {
      if (entry.kind === 'primary') {
        const pIdx = primary.value.findIndex((p, idx) => {
          const s = starts.value[idx] ?? 0
          return p.id === entry.id || (toSplitEntries.length === 1 && curPlayhead >= s && curPlayhead < s + p.duration_ms)
        })
        if (pIdx >= 0) {
          const item = primary.value[pIdx]
          if (item && item.kind === 'video') {
            const start = starts.value[pIdx] ?? 0
            const local = curPlayhead - start
            if (local >= 100 && item.duration_ms - local >= 100) {
              const inMs = Math.round(item.source_in_ms || 0)
              const totalDuration = Math.round(item.duration_ms)
              const leftDuration = local
              const leftSourceOut = inMs + leftDuration
              const rightDuration = totalDuration - leftDuration
              const rightSourceIn = leftSourceOut
              const rightSourceOut = item.source_out_ms != null ? Math.round(item.source_out_ms) : rightSourceIn + rightDuration

              let left: TimelinePrimaryItem = { ...item, duration_ms: leftDuration, source_in_ms: inMs, source_out_ms: leftSourceOut }
              let right: TimelinePrimaryItem = { ...item, id: crypto.randomUUID(), label: `${item.label} B`, source_in_ms: rightSourceIn, duration_ms: rightDuration, source_out_ms: rightSourceOut, transition: { kind: 'cut', duration_ms: 500 } }
              const span = primarySpanMs(item)
              if (item.loop && span > 0) {
                const phase = leftDuration % span
                left = { ...item, duration_ms: leftDuration }
                setVideoLength(left, leftDuration, span)
                right = { ...item, id: crypto.randomUUID(), label: `${item.label} B`, duration_ms: rightDuration, transition: { kind: 'cut', duration_ms: 500 } }
                if (rightDuration <= span - phase) {
                  right.source_in_ms = inMs + phase
                  right.source_out_ms = inMs + phase + rightDuration
                  right.loop = false
                }
              }
              timeline.value!.document.primary.splice(pIdx, 1, left, right)
              newSelectionEntries.push({ kind: 'primary', id: right.id })
              didSplitAny = true
              continue
            }
          }
        }
        newSelectionEntries.push(entry)
      } else if (entry.kind === 'video') {
        const vList = timeline.value!.document.video_overlays ?? []
        const vIdx = vList.findIndex((v) => v.id === entry.id)
        if (vIdx >= 0) {
          const item = vList[vIdx]
          if (item) {
            const start = item.start_ms
            const end = item.start_ms + item.duration_ms
            if (curPlayhead >= start + 100 && curPlayhead <= end - 100) {
              const leftDuration = curPlayhead - start
              const rightDuration = end - curPlayhead
              const leftSourceIn = Math.round(item.source_in_ms ?? 0)
              const rightSourceIn = leftSourceIn + leftDuration

              const left: TimelineVideoOverlay = {
                ...item,
                duration_ms: leftDuration,
                source_in_ms: leftSourceIn,
              }
              const right: TimelineVideoOverlay = {
                ...item,
                id: crypto.randomUUID(),
                label: `${item.label} B`,
                start_ms: curPlayhead,
                duration_ms: rightDuration,
                source_in_ms: rightSourceIn,
                variant: JSON.parse(JSON.stringify(item.variant || {})),
              }
              vList.splice(vIdx, 1, left, right)
              newSelectionEntries.push({ kind: 'video', id: right.id })
              didSplitAny = true
              continue
            }
          }
        }
        newSelectionEntries.push(entry)
      } else if (entry.kind === 'audio') {
        const aList = timeline.value!.document.audio_overlays ?? []
        const aIdx = aList.findIndex((a) => a.id === entry.id)
        if (aIdx >= 0) {
          const item = aList[aIdx]
          if (item) {
            const start = item.start_ms
            const end = item.start_ms + item.duration_ms
            if (curPlayhead >= start + 100 && curPlayhead <= end - 100) {
              const leftDuration = curPlayhead - start
              const rightDuration = end - curPlayhead
              const leftSourceIn = Math.round(item.source_in_ms ?? 0)
              let rightSourceIn = leftSourceIn + leftDuration

              const url = resolveSource(item.source)?.url
              const fileTotal = url ? audioFileDurations.value[url] : undefined
              if (fileTotal && fileTotal > 0 && item.loop !== false) {
                rightSourceIn = (leftSourceIn + leftDuration) % fileTotal
              }

              const left: TimelineAudioOverlay = {
                ...item,
                duration_ms: leftDuration,
                source_in_ms: leftSourceIn,
              }
              const right: TimelineAudioOverlay = {
                ...item,
                id: crypto.randomUUID(),
                label: `${item.label} B`,
                start_ms: curPlayhead,
                duration_ms: rightDuration,
                source_in_ms: rightSourceIn,
              }
              aList.splice(aIdx, 1, left, right)
              newSelectionEntries.push({ kind: 'audio', id: right.id })
              didSplitAny = true
              continue
            }
          }
        }
        newSelectionEntries.push(entry)
      } else {
        newSelectionEntries.push(entry)
      }
    }

    if (didSplitAny) {
      selectedEntries.value = newSelectionEntries
      const first = newSelectionEntries[0]
      if (first) {
        selectedKind.value = first.kind
        selectedId.value = first.id
      }
      syncVideoOverlays()
      syncAudioOverlays()
    }
  })
}

function deleteSelection() {
  if (!timeline.value) return
  if (selectedEntries.value.length > 1) {
    mutate(() => {
      const videoIds = new Set(selectedEntries.value.filter((e) => e.kind === 'video').map((e) => e.id))
      const imageIds = new Set(selectedEntries.value.filter((e) => e.kind === 'image').map((e) => e.id))
      const audioIds = new Set(selectedEntries.value.filter((e) => e.kind === 'audio').map((e) => e.id))
      const textIds = new Set(selectedEntries.value.filter((e) => e.kind === 'text').map((e) => e.id))

      if (videoIds.size) timeline.value!.document.video_overlays = (timeline.value!.document.video_overlays ?? []).filter((v) => !videoIds.has(v.id))
      if (imageIds.size) timeline.value!.document.image_overlays = (timeline.value!.document.image_overlays ?? []).filter((img) => !imageIds.has(img.id))
      if (audioIds.size) timeline.value!.document.audio_overlays = (timeline.value!.document.audio_overlays ?? []).filter((a) => !audioIds.has(a.id))
      if (textIds.size) timeline.value!.document.text_overlays = (timeline.value!.document.text_overlays ?? []).filter((t) => !textIds.has(t.id))

      const primaryIds = new Set(selectedEntries.value.filter((e) => e.kind === 'primary').map((e) => e.id))
      if (primaryIds.size && primary.value.length > primaryIds.size) {
        const indicesToDelete = primary.value
          .map((p, idx) => ({ id: p.id, idx }))
          .filter((p) => primaryIds.has(p.id))
          .map((p) => p.idx)
          .sort((a, b) => b - a)

        for (const idx of indicesToDelete) {
          const start = starts.value[idx] ?? 0
          const duration = primary.value[idx]?.duration_ms ?? 0
          timeline.value!.document.primary.splice(idx, 1)
          removeInterval(start, duration)
        }
      }

      selectedEntries.value = []
      selectedId.value = primary.value[0]?.id ?? ''
      selectedKind.value = 'primary'
      syncVideoOverlays()
      syncAudioOverlays()
    })
    return
  }

  mutate(() => {
    if (selectedKind.value === 'primary') {
      const index = selectedPrimaryIndex.value
      if (index < 0 || primary.value.length === 1) return
      const start = starts.value[index] ?? 0
      const duration = primary.value[index]?.duration_ms ?? 0
      timeline.value!.document.primary.splice(index, 1)
      removeInterval(start, duration)
      selectedId.value = timeline.value!.document.primary[Math.max(0, index - 1)]?.id ?? ''
    } else if (selectedKind.value === 'video') {
      timeline.value!.document.video_overlays = (timeline.value!.document.video_overlays ?? []).filter((item) => item.id !== selectedId.value)
      selectedId.value = primary.value[0]?.id ?? ''
      selectedKind.value = 'primary'
      syncVideoOverlays()
    } else if (selectedKind.value === 'image') {
      timeline.value!.document.image_overlays = timeline.value!.document.image_overlays.filter((item) => item.id !== selectedId.value)
      selectedId.value = primary.value[0]?.id ?? ''
      selectedKind.value = 'primary'
    } else if (selectedKind.value === 'audio') {
      timeline.value!.document.audio_overlays = (timeline.value!.document.audio_overlays ?? []).filter((item) => item.id !== selectedId.value)
      selectedId.value = primary.value[0]?.id ?? ''
      selectedKind.value = 'primary'
      syncAudioOverlays()
    } else {
      timeline.value!.document.text_overlays = timeline.value!.document.text_overlays.filter((item) => item.id !== selectedId.value)
      selectedId.value = primary.value[0]?.id ?? ''
      selectedKind.value = 'primary'
    }
    selectedEntries.value = selectedId.value ? [{ kind: selectedKind.value, id: selectedId.value }] : []
  })
}

function moveSelected(direction: -1 | 1) {
  const index = selectedPrimaryIndex.value
  const target = index + direction
  if (!timeline.value || index < 0 || target < 0 || target >= primary.value.length) return
  mutate(() => {
    const [item] = timeline.value!.document.primary.splice(index, 1)
    if (!item) return
    timeline.value!.document.primary.splice(target, 0, item)
  })
}

function isDuplicateDragEvent(event: DragEvent | PointerEvent | MouseEvent): boolean {
  return Boolean(event.ctrlKey || event.shiftKey || event.altKey || event.metaKey || dragDuplicateModifier.value)
}

function getDraggedPrimaryIndex(event: DragEvent): number | null {
  if (dragSourceIndex.value !== null && dragSourceIndex.value >= 0 && dragSourceIndex.value < primary.value.length) {
    return dragSourceIndex.value
  }
  const raw = event.dataTransfer?.getData('application/x-cutline-primary-index') || event.dataTransfer?.getData('text/plain')
  if (raw != null && raw.trim() !== '') {
    const parsed = Number(raw)
    if (Number.isInteger(parsed) && parsed >= 0 && parsed < primary.value.length) {
      return parsed
    }
  }
  return null
}

function clampOverlaysToTotal() {
  if (!timeline.value) return
  const total = timeline.value.document.primary.reduce((sum, item) => sum + item.duration_ms, 0)
  if (total <= 0) return
  const clampList = <T extends { start_ms: number; duration_ms: number }>(items?: T[]) => {
    if (!items) return
    for (const item of items) {
      item.duration_ms = Math.max(100, Math.min(Math.round(item.duration_ms), total))
      item.start_ms = Math.max(0, Math.min(Math.round(item.start_ms), Math.max(0, total - item.duration_ms)))
    }
  }
  clampList(timeline.value.document.video_overlays)
  clampList(timeline.value.document.image_overlays)
  clampList(timeline.value.document.text_overlays)
  clampList(timeline.value.document.audio_overlays)
}

function onPrimaryDragStart(event: DragEvent, index: number) {
  if (primaryResize) {
    event.preventDefault()
    return
  }
  dragSourceIndex.value = index
  dragDuplicateModifier.value = Boolean(event.ctrlKey || event.shiftKey || event.altKey || event.metaKey)
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'copyMove'
    event.dataTransfer.setData('text/plain', String(index))
    event.dataTransfer.setData('application/x-cutline-primary-index', String(index))
  }
}

function onPrimaryDragOver(event: DragEvent, index: number) {
  const fromIndex = getDraggedPrimaryIndex(event)
  const isDuplicate = isDuplicateDragEvent(event)
  if (fromIndex === null || (!isDuplicate && fromIndex === index)) return
  if (event.dataTransfer) event.dataTransfer.dropEffect = isDuplicate ? 'copy' : 'move'
  dragOverIndex.value = index
}

function onPrimaryDragLeave(index: number) {
  if (dragOverIndex.value === index) {
    dragOverIndex.value = null
  }
}

function onPrimaryDrop(event: DragEvent, targetIndex: number) {
  const fromIndex = getDraggedPrimaryIndex(event)
  const isDuplicate = isDuplicateDragEvent(event)
  dragSourceIndex.value = null
  dragOverIndex.value = null
  dragDuplicateModifier.value = false
  if (!timeline.value || fromIndex === null) return
  if (fromIndex < 0 || fromIndex >= primary.value.length || targetIndex < 0 || targetIndex >= primary.value.length) return
  if (!isDuplicate && fromIndex === targetIndex) return
  mutate(() => {
    if (isDuplicate) {
      const sourceItem = timeline.value!.document.primary[fromIndex]
      if (!sourceItem) return
      const duplicate: TimelinePrimaryItem = {
        ...JSON.parse(JSON.stringify(sourceItem)),
        id: crypto.randomUUID(),
        label: `${sourceItem.label} (copy)`,
      }
      const insertAt = fromIndex === targetIndex ? targetIndex + 1 : targetIndex
      const insertionTime = starts.value[insertAt] ?? totalMs.value
      timeline.value!.document.primary.splice(insertAt, 0, duplicate)
      insertInterval(insertionTime, duplicate.duration_ms)
      selectedKind.value = 'primary'
      selectedId.value = duplicate.id
      return
    }
    const [moved] = timeline.value!.document.primary.splice(fromIndex, 1)
    if (!moved) return
    timeline.value!.document.primary.splice(targetIndex, 0, moved)
    selectedKind.value = 'primary'
    selectedId.value = moved.id
  })
  nextTick(() => {
    const newIndex = primary.value.findIndex((item) => item.id === selectedId.value)
    if (newIndex >= 0) {
      playheadMs.value = startAt(newIndex)
      syncVideo()
    }
  })
}

function onPrimaryDragEnd() {
  dragSourceIndex.value = null
  dragOverIndex.value = null
  dragDuplicateModifier.value = false
}

function duplicateSelected() {
  if (!timeline.value) return
  if (selectedKind.value === 'primary') {
    const selected = selectedPrimary.value
    const index = selectedPrimaryIndex.value
    if (!selected || index < 0) return
    mutate(() => {
      const duplicate: TimelinePrimaryItem = {
        ...JSON.parse(JSON.stringify(selected)),
        id: crypto.randomUUID(),
        label: `${selected.label} (copy)`,
      }
      const insertionIndex = index + 1
      const insertionTime = (starts.value[index] ?? 0) + selected.duration_ms
      timeline.value!.document.primary.splice(insertionIndex, 0, duplicate)
      insertInterval(insertionTime, duplicate.duration_ms)
      selectedId.value = duplicate.id
    })
  } else if (selectedKind.value === 'video') {
    const item = selectedVideo.value
    if (!item) return
    mutate(() => {
      const duplicate: TimelineVideoOverlay = {
        ...JSON.parse(JSON.stringify(item)),
        id: crypto.randomUUID(),
        label: `${item.label} (copy)`,
        start_ms: Math.max(0, Math.min(totalMs.value - item.duration_ms, item.start_ms + item.duration_ms)),
      }
      timeline.value!.document.video_overlays ??= []
      timeline.value!.document.video_overlays.push(duplicate)
      selectedId.value = duplicate.id
    })
  } else if (selectedKind.value === 'image') {
    const item = selectedImage.value
    if (!item) return
    mutate(() => {
      const duplicate = {
        ...JSON.parse(JSON.stringify(item)),
        id: crypto.randomUUID(),
        start_ms: Math.max(0, Math.min(totalMs.value - item.duration_ms, item.start_ms + item.duration_ms)),
      }
      timeline.value!.document.image_overlays.push(duplicate)
      selectedId.value = duplicate.id
    })
  } else if (selectedKind.value === 'text') {
    const item = selectedText.value
    if (!item) return
    mutate(() => {
      const duplicate = {
        ...JSON.parse(JSON.stringify(item)),
        id: crypto.randomUUID(),
        start_ms: Math.max(0, Math.min(totalMs.value - item.duration_ms, item.start_ms + item.duration_ms)),
      }
      timeline.value!.document.text_overlays.push(duplicate)
      selectedId.value = duplicate.id
    })
  } else if (selectedKind.value === 'audio') {
    const item = selectedAudio.value
    if (!item) return
    mutate(() => {
      const duplicate: TimelineAudioOverlay = {
        ...JSON.parse(JSON.stringify(item)),
        id: crypto.randomUUID(),
        label: `${item.label} (copy)`,
        start_ms: Math.max(0, Math.min(totalMs.value - item.duration_ms, item.start_ms + item.duration_ms)),
      }
      timeline.value!.document.audio_overlays ??= []
      timeline.value!.document.audio_overlays.push(duplicate)
      selectedId.value = duplicate.id
    })
  }
}

const showAddShotModal = ref(false)
const shotFilterQuery = ref('')

const availableShots = computed(() => {
  const query = shotFilterQuery.value.trim().toLowerCase()
  if (!query) return props.shots
  return props.shots.filter((shot) => (shot.title || '').toLowerCase().includes(query) || (shot.action || '').toLowerCase().includes(query))
})

function addShotToTimeline(shot: Shot) {
  if (!timeline.value) return
  mutate(() => {
    const kind = shot.clip ? 'video' : 'image'
    const source: TimelineSource = {
      kind: shot.clip ? 'shot_clip' : 'shot_image',
      ref_id: shot.id,
    }
    const item: TimelinePrimaryItem = {
      id: crypto.randomUUID(),
      kind,
      label: shot.title || 'Shot',
      source,
      duration_ms: Math.round(shot.duration_ms || 5000),
      source_in_ms: 0,
      source_out_ms: kind === 'video' ? Math.round(shot.duration_ms || 5000) : null,
      text: '',
      mute_audio: false,
      variant: {},
    }
    const index = selectedPrimaryIndex.value >= 0 ? selectedPrimaryIndex.value + 1 : primary.value.length
    const insertionTime = index >= primary.value.length ? totalMs.value : (starts.value[index] ?? 0)
    timeline.value!.document.primary.splice(index, 0, item)
    insertInterval(insertionTime, item.duration_ms)
    selectedKind.value = 'primary'
    selectedId.value = item.id
    showAddShotModal.value = false
  })
}

function appendStill() {
  closeAddMenu()
  const selected = selectedPrimary.value
  if (!selected?.source || !timeline.value) return
  const source = selected.source.kind === 'asset' ? selected.source : { kind: 'shot_image' as const, ref_id: selected.source.ref_id }
  mutate(() => {
    const item: TimelinePrimaryItem = { id: crypto.randomUUID(), kind: 'image', label: `${selected.label} still`, source, duration_ms: 2000, source_in_ms: 0, source_out_ms: null, text: '', variant: {} }
    const insertionIndex = selectedPrimaryIndex.value + 1
    const insertionTime = (starts.value[selectedPrimaryIndex.value] ?? 0) + selected.duration_ms
    timeline.value!.document.primary.splice(insertionIndex, 0, item)
    insertInterval(insertionTime, item.duration_ms)
    selectedId.value = item.id
  })
}

function addTitle() {
  closeAddMenu()
  if (!timeline.value) return
  mutate(() => {
    const item: TimelinePrimaryItem = { id: crypto.randomUUID(), kind: 'title', label: 'Title card', source: null, duration_ms: 2000, source_in_ms: 0, source_out_ms: null, text: 'TITLE', variant: {} }
    const index = Math.max(0, selectedPrimaryIndex.value + 1)
    const insertionTime = index >= primary.value.length ? totalMs.value : (starts.value[index] ?? 0)
    timeline.value!.document.primary.splice(index, 0, item)
    insertInterval(insertionTime, item.duration_ms)
    selectedKind.value = 'primary'
    selectedId.value = item.id
  })
}

function addText() {
  closeAddMenu()
  if (!timeline.value) return
  mutate(() => {
    const defaultDur = Math.max(500, Math.min(2500, totalMs.value || 2500))
    const playhead = Math.round(playheadMs.value)
    const start = Math.max(0, Math.min(playhead, Math.max(0, totalMs.value - defaultDur)))
    const duration = totalMs.value > 0 ? Math.min(defaultDur, Math.max(500, totalMs.value - start)) : defaultDur
    const item = { id: crypto.randomUUID(), text: 'New text', start_ms: start, duration_ms: duration, presentation: 'overlay' as const, size: 'medium' as const, color: 'white' as const, background: 'shadow' as const, variant: {} }
    timeline.value!.document.text_overlays.push(item)
    selectedKind.value = 'text'
    selectedId.value = item.id
  })
}

const showAddVideoOverlayModal = ref(false)
const videoFilterQuery = ref('')
const isDraggingOverVideoTrack = ref(false)

function shotHasVideo(shot?: Shot | null): boolean {
  if (!shot) return false
  return Boolean(
    shot.clip ||
    shot.media_variants?.some((v) => v.media_type === 'clip') ||
    shot.takes?.some((t) => t.media_type === 'clip')
  )
}

function shotClipUrl(shot: Shot): string {
  const resolved = shotMedia(shot, 'clip')
  if (resolved?.media.url) return resolved.media.url
  if (shot.clip?.url) return shot.clip.url
  return ''
}

const availableVideoShots = computed(() => {
  const query = videoFilterQuery.value.trim().toLowerCase()
  return props.shots.filter((shot) => {
    if (!shotHasVideo(shot)) return false
    if (!query) return true
    return (shot.title || '').toLowerCase().includes(query) || (shot.action || '').toLowerCase().includes(query)
  })
})

const availableVideoAssets = computed(() => {
  return (timeline.value?.assets || []).filter((a) => a.mime_type.startsWith('video/'))
})

const selectedShotWithVideo = computed(() => {
  const refId = selectedPrimary.value?.source?.ref_id || activeItem.value?.source?.ref_id
  const currentShot = props.shots.find((s) => s.id === refId)
  if (shotHasVideo(currentShot)) return currentShot!
  return props.shots.find((s) => shotHasVideo(s)) ?? null
})

function openAddVideoOverlayModal() {
  closeAddMenu()
  showAddVideoOverlayModal.value = true
}

function primaryHasActiveAudioInRange(startMs: number, durationMs: number): boolean {
  if (!timeline.value) return false
  const endMs = startMs + durationMs
  let elapsed = 0
  for (const item of timeline.value.document.primary) {
    const itemStart = elapsed
    const itemEnd = elapsed + item.duration_ms
    elapsed = itemEnd
    if (itemEnd <= startMs || itemStart >= endMs) continue
    if (item.kind === 'video' && !item.mute_audio) {
      return true
    }
  }
  return false
}

function addVideoOverlay(source?: TimelineSource, label?: string, atMs?: number, durationMs?: number) {
  closeAddMenu()
  if (!timeline.value) return
  if (!source) {
    showAddVideoOverlayModal.value = true
    return
  }
  mutate(() => {
    const resolved = resolveSource(source)
    const fallbackDur = durationMs ?? 5000
    const defaultDur = Math.max(500, Math.min(fallbackDur, totalMs.value || fallbackDur))
    const playhead = atMs !== undefined ? Math.round(atMs) : Math.round(playheadMs.value)
    const start = Math.max(0, Math.min(playhead, Math.max(0, totalMs.value - defaultDur)))
    const duration = totalMs.value > 0 ? Math.min(defaultDur, Math.max(500, totalMs.value - start)) : defaultDur
    const defaultGeom = defaultVideoOverlayGeometry({ source })
    const autoMute = primaryHasActiveAudioInRange(start, duration)
    const item: TimelineVideoOverlay = {
      id: crypto.randomUUID(),
      label: label || resolved?.label || 'Video layer',
      source,
      start_ms: start,
      duration_ms: duration,
      source_in_ms: 0,
      opacity: 1,
      volume: 1,
      mute_audio: autoMute,
      variant: {
        [variant.value]: defaultGeom,
      },
    }
    timeline.value!.document.video_overlays ??= []
    timeline.value!.document.video_overlays.push(item)
    selectedKind.value = 'video'
    selectedId.value = item.id
  })
  nextTick(() => syncVideoOverlays())
}

function addShotVideoToTimeline(shot?: Shot | null) {
  closeAddMenu()
  const target = shot ?? selectedShotWithVideo.value
  if (!target) {
    showAddVideoOverlayModal.value = true
    return
  }
  const source: TimelineSource = { kind: 'shot_clip', ref_id: target.id }
  addVideoOverlay(source, target.title || 'Shot video', playheadMs.value, target.duration_ms || 5000)
  showAddShotModal.value = false
  showAddVideoOverlayModal.value = false
}

function addAssetVideoToTimeline(asset: TimelineAsset) {
  closeAddMenu()
  const source: TimelineSource = { kind: 'asset', ref_id: asset.id }
  addVideoOverlay(source, asset.label || 'Video layer', playheadMs.value, 5000)
  showAddVideoOverlayModal.value = false
}

function probeVideoFileDurationMs(file: File): Promise<number> {
  return new Promise((resolve) => {
    if (typeof document === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
      resolve(5000)
      return
    }
    let settled = false
    const objectUrl = URL.createObjectURL(file)
    const finish = (ms: number) => {
      if (settled) return
      settled = true
      try {
        URL.revokeObjectURL(objectUrl)
      } catch {}
      resolve(ms)
    }
    const timer = window.setTimeout(() => finish(5000), 2000)
    try {
      const video = document.createElement('video')
      video.preload = 'metadata'
      video.onloadedmetadata = () => {
        window.clearTimeout(timer)
        const dur = Number.isFinite(video.duration) && video.duration > 0 ? Math.round(video.duration * 1000) : 5000
        finish(dur)
      }
      video.onerror = () => {
        window.clearTimeout(timer)
        finish(5000)
      }
      video.src = objectUrl
    } catch {
      window.clearTimeout(timer)
      finish(5000)
    }
  })
}

async function uploadVideoOverlayAt(file: File, atMs?: number) {
  if (!timeline.value) return
  try {
    const probedMs = await probeVideoFileDurationMs(file)
    const cleanLabel = file.name.replace(/\.[^.]+$/, '') || file.name
    await flushPendingSave()
    timeline.value = await api.uploadTimelineAsset(
      props.project.id,
      file,
      cleanLabel,
      variant.value,
      props.compilationId ? undefined : store.currentEpisodeId || undefined,
      'video',
      props.compilationId,
    )
    timeline.value.document.video_overlays ??= []
    timeline.value.document.audio_overlays ??= []
    const asset = timeline.value.assets[timeline.value.assets.length - 1]
    if (asset) {
      const source: TimelineSource = { kind: 'asset', ref_id: asset.id }
      addVideoOverlay(source, asset.label || cleanLabel, atMs, probedMs)
    }
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Could not upload video layer'
  }
}

async function uploadVideoOverlayDirect(event: Event) {
  closeAddMenu()
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  await uploadVideoOverlayAt(file, playheadMs.value)
  input.value = ''
  showAddVideoOverlayModal.value = false
}

function handleVideoTrackDragOver(event: DragEvent) {
  event.preventDefault()
  if (event.dataTransfer) {
    const fromIndex = getDraggedPrimaryIndex(event)
    const isDuplicate = isDuplicateDragEvent(event)
    event.dataTransfer.dropEffect = fromIndex !== null && !isDuplicate && primary.value.length > 1 ? 'move' : 'copy'
  }
  isDraggingOverVideoTrack.value = true
}

function handleVideoTrackDragLeave(event: DragEvent) {
  const currentTarget = event.currentTarget as HTMLElement | null
  const relatedTarget = event.relatedTarget as Node | null
  if (currentTarget && relatedTarget && currentTarget.contains(relatedTarget)) {
    return
  }
  isDraggingOverVideoTrack.value = false
}

async function handleVideoTrackDrop(event: DragEvent) {
  event.preventDefault()
  event.stopPropagation()
  isDraggingOverVideoTrack.value = false

  const target = event.currentTarget as HTMLElement | null
  const overlayTrackEl = (target?.querySelector('.overlay-track') ?? (target?.classList.contains('overlay-track') ? target : null)) as HTMLElement | null

  const fromIndex = getDraggedPrimaryIndex(event)
  if (fromIndex !== null) {
    const isDuplicate = isDuplicateDragEvent(event)
    dragSourceIndex.value = null
    dragOverIndex.value = null
    dragDuplicateModifier.value = false
    const draggedItem = primary.value[fromIndex]
    if (!timeline.value || !draggedItem || !draggedItem.source) return

    const originalStartMs = startAt(fromIndex)
    const shouldRemovePrimary = !isDuplicate && timeline.value.document.primary.length > 1
    let dropMs = shouldRemovePrimary ? Math.min(originalStartMs, Math.max(0, totalMs.value - draggedItem.duration_ms)) : originalStartMs
    if (overlayTrackEl && totalMs.value > 0) {
      const rect = overlayTrackEl.getBoundingClientRect()
      if (rect.width > 0 && (event.clientX > 0 || event.clientY > 0)) {
        const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left))
        dropMs = Math.round((relativeX / rect.width) * totalMs.value)
      }
    }

    mutate(() => {
      const sourceItem = timeline.value!.document.primary[fromIndex]
      if (!sourceItem || !sourceItem.source) return
      const rawSource: TimelineSource = JSON.parse(JSON.stringify(sourceItem.source))
      const overlaySource: TimelineSource =
        rawSource.kind === 'shot_image' && shotHasVideo(props.shots.find((s) => s.id === rawSource.ref_id))
          ? { kind: 'shot_clip', ref_id: rawSource.ref_id }
          : rawSource

      if (shouldRemovePrimary) {
        timeline.value!.document.primary.splice(fromIndex, 1)
      }

      const nextTotalMs = timeline.value!.document.primary.reduce((sum, item) => sum + item.duration_ms, 0)
      const baseDur = Math.max(500, Math.round(sourceItem.duration_ms || 3000))
      const duration = nextTotalMs > 0 ? Math.max(100, Math.min(baseDur, nextTotalMs)) : baseDur
      const start = nextTotalMs > 0 ? Math.max(0, Math.min(Math.round(dropMs), Math.max(0, nextTotalMs - duration))) : 0

      const baseVariant = sourceItem.variant ? JSON.parse(JSON.stringify(sourceItem.variant)) : {}
      const activeGeom = baseVariant[variant.value] ?? defaultVideoOverlayGeometry({ source: overlaySource })
      baseVariant[variant.value] = {
        ...defaultVideoOverlayGeometry({ source: overlaySource }),
        ...activeGeom,
        position: 'full-frame',
      }

      const autoMute = Boolean(sourceItem.mute_audio) || primaryHasActiveAudioInRange(start, duration)
      const item: TimelineVideoOverlay = {
        id: crypto.randomUUID(),
        label: isDuplicate ? `${sourceItem.label} (copy)` : sourceItem.label,
        source: overlaySource,
        start_ms: start,
        duration_ms: duration,
        source_in_ms: Math.round(sourceItem.source_in_ms || 0),
        opacity: 1,
        volume: 1,
        mute_audio: autoMute,
        variant: baseVariant,
      }
      timeline.value!.document.video_overlays ??= []
      timeline.value!.document.video_overlays.push(item)
      clampOverlaysToTotal()
      selectedKind.value = 'video'
      selectedId.value = item.id
      playheadMs.value = item.start_ms
    })
    nextTick(() => {
      syncVideo()
    })
    return
  }

  const files = event.dataTransfer?.files
  if (!files || files.length === 0) return
  const file = files[0]
  const isVideoFile = file && (file.type.startsWith('video/') || /\.(mp4|webm|mov)$/i.test(file.name))
  if (!isVideoFile) return

  let dropMs = playheadMs.value
  if (overlayTrackEl && totalMs.value > 0) {
    const rect = overlayTrackEl.getBoundingClientRect()
    if (rect.width > 0) {
      const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left))
      dropMs = Math.round((relativeX / rect.width) * totalMs.value)
    }
  }

  await uploadVideoOverlayAt(file, dropMs)
}

function handleVideoTrackClick(event: MouseEvent) {
  const target = event.currentTarget as HTMLElement | null
  if (!target || totalMs.value <= 0) return
  const rect = target.getBoundingClientRect()
  const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left))
  playheadMs.value = Math.round((relativeX / rect.width) * totalMs.value)
  syncVideo()
}

const showAddImageOverlayModal = ref(false)
const imageFilterQuery = ref('')
const isDraggingOverImageTrack = ref(false)

function shotHasImage(shot?: Shot | null): boolean {
  if (!shot) return false
  return Boolean(
    shot.image ||
    shot.image_references?.length ||
    shot.media_variants?.some((v) => v.media_type === 'image') ||
    shot.takes?.some((t) => t.media_type === 'image')
  )
}

function shotStillUrl(shot: Shot): string {
  const still = shotMedia(shot, 'image')
  if (still?.media.url) return still.media.url
  if (shot.image?.url) return shot.image.url
  if (shot.image_references?.[0]?.url) return shot.image_references[0].url
  return ''
}

const availableImageShots = computed(() => {
  const query = imageFilterQuery.value.trim().toLowerCase()
  return props.shots.filter((shot) => {
    if (!shotHasImage(shot)) return false
    if (!query) return true
    return (shot.title || '').toLowerCase().includes(query) || (shot.action || '').toLowerCase().includes(query)
  })
})

const availableImageAssets = computed(() => {
  return (timeline.value?.assets || []).filter((a) => a.mime_type.startsWith('image/'))
})

const selectedShotWithImage = computed(() => {
  const refId = selectedPrimary.value?.source?.ref_id || activeItem.value?.source?.ref_id
  const currentShot = props.shots.find((s) => s.id === refId)
  if (shotHasImage(currentShot)) return currentShot!
  return props.shots.find((s) => shotHasImage(s)) ?? null
})

function openAddImageOverlayModal() {
  closeAddMenu()
  showAddImageOverlayModal.value = true
}

function addImageOverlay(source?: TimelineSource, atMs?: number, durationMs?: number) {
  closeAddMenu()
  if (!timeline.value) return
  const fallback = selectedPrimary.value?.source
  const chosen = source ?? (fallback?.kind === 'asset' ? fallback : fallback ? { kind: 'shot_image' as const, ref_id: fallback.ref_id } : null)
  if (!chosen) {
    showAddImageOverlayModal.value = true
    return
  }
  mutate(() => {
    const defaultDur = Math.max(500, Math.min(durationMs ?? 3000, totalMs.value || 3000))
    const playhead = atMs !== undefined ? Math.round(atMs) : Math.round(playheadMs.value)
    const start = Math.max(0, Math.min(playhead, Math.max(0, totalMs.value - defaultDur)))
    const duration = totalMs.value > 0 ? Math.min(defaultDur, Math.max(500, totalMs.value - start)) : defaultDur
    const item: TimelineImageOverlay = {
      id: crypto.randomUUID(),
      source: chosen,
      start_ms: start,
      duration_ms: duration,
      opacity: 1,
      variant: {},
    }
    timeline.value!.document.image_overlays ??= []
    timeline.value!.document.image_overlays.push(item)
    selectedKind.value = 'image'
    selectedId.value = item.id
  })
}

function addShotImageToTimeline(shot?: Shot | null) {
  closeAddMenu()
  const target = shot ?? selectedShotWithImage.value
  if (!target) {
    showAddImageOverlayModal.value = true
    return
  }
  const source: TimelineSource = { kind: 'shot_image', ref_id: target.id }
  addImageOverlay(source)
  showAddShotModal.value = false
  showAddImageOverlayModal.value = false
}

function addAssetImageToTimeline(asset: TimelineAsset) {
  closeAddMenu()
  const source: TimelineSource = { kind: 'asset', ref_id: asset.id }
  addImageOverlay(source)
  showAddImageOverlayModal.value = false
}

async function uploadImageOverlayAt(file: File, atMs?: number) {
  if (!timeline.value) return
  await flushPendingSave()
  timeline.value = await api.uploadTimelineAsset(
    props.project.id,
    file,
    file.name,
    variant.value,
    props.compilationId ? undefined : store.currentEpisodeId || undefined,
    'image',
    props.compilationId,
  )
  timeline.value.document.video_overlays ??= []
  timeline.value.document.image_overlays ??= []
  const asset = timeline.value.assets[timeline.value.assets.length - 1]
  if (asset) {
    const source: TimelineSource = { kind: 'asset', ref_id: asset.id }
    addImageOverlay(source, atMs)
  }
}

async function uploadImageOverlayDirect(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  await uploadImageOverlayAt(file, playheadMs.value)
  input.value = ''
  showAddImageOverlayModal.value = false
}

function handleImageTrackDragOver(event: DragEvent) {
  event.preventDefault()
  if (event.dataTransfer) {
    const fromIndex = getDraggedPrimaryIndex(event)
    const isDuplicate = isDuplicateDragEvent(event)
    event.dataTransfer.dropEffect = fromIndex !== null && !isDuplicate && primary.value.length > 1 ? 'move' : 'copy'
  }
  isDraggingOverImageTrack.value = true
}

function handleImageTrackDragLeave(event: DragEvent) {
  const currentTarget = event.currentTarget as HTMLElement | null
  const relatedTarget = event.relatedTarget as Node | null
  if (currentTarget && relatedTarget && currentTarget.contains(relatedTarget)) {
    return
  }
  isDraggingOverImageTrack.value = false
}

async function handleImageTrackDrop(event: DragEvent) {
  event.preventDefault()
  event.stopPropagation()
  isDraggingOverImageTrack.value = false

  const target = event.currentTarget as HTMLElement | null
  const overlayTrackEl = (target?.querySelector('.overlay-track') ?? (target?.classList.contains('overlay-track') ? target : null)) as HTMLElement | null

  const fromIndex = getDraggedPrimaryIndex(event)
  if (fromIndex !== null) {
    const isDuplicate = isDuplicateDragEvent(event)
    dragSourceIndex.value = null
    dragOverIndex.value = null
    dragDuplicateModifier.value = false
    const draggedItem = primary.value[fromIndex]
    if (!timeline.value || !draggedItem || !draggedItem.source) return

    const originalStartMs = startAt(fromIndex)
    const shouldRemovePrimary = !isDuplicate && timeline.value.document.primary.length > 1
    let dropMs = shouldRemovePrimary ? Math.min(originalStartMs, Math.max(0, totalMs.value - draggedItem.duration_ms)) : originalStartMs
    if (overlayTrackEl && totalMs.value > 0) {
      const rect = overlayTrackEl.getBoundingClientRect()
      if (rect.width > 0 && (event.clientX > 0 || event.clientY > 0)) {
        const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left))
        dropMs = Math.round((relativeX / rect.width) * totalMs.value)
      }
    }

    mutate(() => {
      const sourceItem = timeline.value!.document.primary[fromIndex]
      if (!sourceItem || !sourceItem.source) return
      const rawSource: TimelineSource = JSON.parse(JSON.stringify(sourceItem.source))
      const overlaySource: TimelineSource =
        rawSource.kind === 'shot_clip' ? { kind: 'shot_image', ref_id: rawSource.ref_id } : rawSource

      if (shouldRemovePrimary) {
        timeline.value!.document.primary.splice(fromIndex, 1)
      }

      const nextTotalMs = timeline.value!.document.primary.reduce((sum, item) => sum + item.duration_ms, 0)
      const baseDur = Math.max(500, Math.round(sourceItem.duration_ms || 3000))
      const duration = nextTotalMs > 0 ? Math.max(100, Math.min(baseDur, nextTotalMs)) : baseDur
      const start = nextTotalMs > 0 ? Math.max(0, Math.min(Math.round(dropMs), Math.max(0, nextTotalMs - duration))) : 0

      const item: TimelineImageOverlay = {
        id: crypto.randomUUID(),
        source: overlaySource,
        start_ms: start,
        duration_ms: duration,
        opacity: 1,
        variant: {},
      }
      timeline.value!.document.image_overlays ??= []
      timeline.value!.document.image_overlays.push(item)
      clampOverlaysToTotal()
      selectedKind.value = 'image'
      selectedId.value = item.id
      playheadMs.value = item.start_ms
    })
    nextTick(() => {
      syncVideo()
    })
    return
  }

  const files = event.dataTransfer?.files
  if (!files || files.length === 0) return
  const file = files[0]
  if (!file || !file.type.startsWith('image/')) return

  let dropMs = playheadMs.value
  if (overlayTrackEl && totalMs.value > 0) {
    const rect = overlayTrackEl.getBoundingClientRect()
    if (rect.width > 0) {
      const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left))
      dropMs = Math.round((relativeX / rect.width) * totalMs.value)
    }
  }

  await uploadImageOverlayAt(file, dropMs)
}

function handleImageTrackClick(event: MouseEvent) {
  const target = event.currentTarget as HTMLElement | null
  if (!target || totalMs.value <= 0) return
  const rect = target.getBoundingClientRect()
  const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left))
  playheadMs.value = Math.round((relativeX / rect.width) * totalMs.value)
}

function handleAudioTrackClick(event: MouseEvent) {
  const target = event.currentTarget as HTMLElement | null
  if (!target || totalMs.value <= 0) return
  const rect = target.getBoundingClientRect()
  const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left))
  playheadMs.value = Math.round((relativeX / rect.width) * totalMs.value)
  syncVideo()
}

function addAudioOverlay(source: TimelineSource, label = 'Audio clip', preferredDurationMs = 3000) {
  closeAddMenu()
  if (!timeline.value) return
  mutate(() => {
    const defaultDur = Math.max(500, Math.min(preferredDurationMs, totalMs.value || preferredDurationMs))
    const playhead = Math.round(playheadMs.value)
    const start = Math.max(0, Math.min(playhead, Math.max(0, totalMs.value - defaultDur)))
    const duration = totalMs.value > 0 ? Math.min(defaultDur, Math.max(500, totalMs.value - start)) : defaultDur
    const item: TimelineAudioOverlay = {
      id: crypto.randomUUID(),
      label,
      source,
      start_ms: start,
      duration_ms: duration,
      source_in_ms: 0,
      volume: 1,
      mute_audio: false,
    }
    timeline.value!.document.audio_overlays ??= []
    timeline.value!.document.audio_overlays.push(item)
    selectedKind.value = 'audio'
    selectedId.value = item.id
  })
}

interface StemTarget {
  kind: 'primary' | 'video' | 'audio'
  id: string
  label: string
  source: TimelineSource
  sourceInMs: number
  durationMs: number
  startMs: number
}

const stemSeparators = ref<StemSeparatorStatus[]>([])
const stemProvider = ref<StemSeparatorId | ''>('')
const stemIncludeVoice = ref(false)
const stemRuns = ref<Record<string, { busy: boolean; status: string; error: string }>>({})
let stemSeparatorsRequest: Promise<void> | null = null

function hasSeparableAudio(source: TimelineSource | null | undefined): source is TimelineSource {
  if (!source || source.kind === 'shot_image') return false
  if (source.kind !== 'asset') return true
  const mime = timeline.value?.assets.find((asset) => asset.id === source.ref_id)?.mime_type ?? ''
  return mime.startsWith('video/') || mime.startsWith('audio/')
}

function stemTargetFor(kind: StemTarget['kind'], id: string): StemTarget | null {
  const doc = timeline.value?.document
  if (!doc) return null
  if (kind === 'primary') {
    const index = doc.primary.findIndex((item) => item.id === id)
    const item = doc.primary[index]
    if (!item || item.kind !== 'video' || !hasSeparableAudio(item.source)) return null
    return { kind, id, label: item.label, source: item.source, sourceInMs: item.source_in_ms, durationMs: Math.min(item.duration_ms, primarySpanMs(item)), startMs: starts.value[index] ?? 0 }
  }
  const overlay = kind === 'video'
    ? (doc.video_overlays ?? []).find((item) => item.id === id)
    : (doc.audio_overlays ?? []).find((item) => item.id === id)
  if (!overlay || !hasSeparableAudio(overlay.source)) return null
  return { kind, id, label: overlay.label, source: overlay.source, sourceInMs: overlay.source_in_ms ?? 0, durationMs: overlay.duration_ms, startMs: overlay.start_ms }
}

const stemTarget = computed<StemTarget | null>(() => {
  if (selectedKind.value === 'primary' && selectedPrimary.value) return stemTargetFor('primary', selectedPrimary.value.id)
  if (selectedKind.value === 'video' && selectedVideo.value) return stemTargetFor('video', selectedVideo.value.id)
  if (selectedKind.value === 'audio' && selectedAudio.value) return stemTargetFor('audio', selectedAudio.value.id)
  return null
})
const stemRun = computed(() => (stemTarget.value && stemRuns.value[stemTarget.value.id]) || { busy: false, status: '', error: '' })

function loadStemSeparators() {
  stemSeparatorsRequest ??= api.stemSeparators().then((list) => {
    stemSeparators.value = list
    if (!stemProvider.value) {
      stemProvider.value = (list.find((item) => item.selected) ?? list.find((item) => item.available))?.id ?? ''
    }
  }).catch(() => {
    stemSeparatorsRequest = null
  })
  return stemSeparatorsRequest
}

watch(stemTarget, (target) => {
  if (target) void loadStemSeparators()
})

async function refreshTimelineAssets() {
  const fetched = props.compilationId
    ? await api.compilationTimeline(props.compilationId, variant.value)
    : await api.timeline(props.project.id, variant.value, store.currentEpisodeId || undefined)
  if (timeline.value) timeline.value.assets = fetched.assets
}

function applyStemSeparation(target: StemTarget, music: TimelineAsset, voice: TimelineAsset | null): string {
  const current = stemTargetFor(target.kind, target.id) ?? target
  let musicOverlayId = ''
  mutate(() => {
    const doc = timeline.value!.document
    doc.audio_overlays ??= []
    const added = [music, ...(voice ? [voice] : [])].map((asset): TimelineAudioOverlay => ({
      id: crypto.randomUUID(),
      label: asset.label,
      source: { kind: 'asset', ref_id: asset.id },
      start_ms: current.startMs,
      duration_ms: current.durationMs,
      source_in_ms: 0,
      volume: 1,
      mute_audio: false,
    }))
    doc.audio_overlays.push(...added)
    const original = target.kind === 'primary'
      ? doc.primary.find((item) => item.id === target.id)
      : target.kind === 'video'
        ? (doc.video_overlays ?? []).find((item) => item.id === target.id)
        : doc.audio_overlays.find((item) => item.id === target.id)
    if (original) original.mute_audio = true
    musicOverlayId = added[0]!.id
    selectedKind.value = 'audio'
    selectedId.value = musicOverlayId
  })
  return musicOverlayId
}

async function separateMusic() {
  const target = stemTarget.value
  const provider = stemProvider.value
  if (!target || !provider || stemRuns.value[target.id]?.busy) return
  const includeVoice = stemIncludeVoice.value
  const providerLabel = stemSeparators.value.find((item) => item.id === provider)?.label ?? provider
  const setRun = (busy: boolean, status: string, error = '', ids = [target.id]) => {
    stemRuns.value = { ...stemRuns.value, ...Object.fromEntries(ids.map((id) => [id, { busy, status, error }])) }
  }
  setRun(true, `Separating with ${providerLabel}…`)
  try {
    const { separation, job } = await api.startStemSeparation(props.project.id, {
      source: target.source,
      source_in_ms: Math.round(target.sourceInMs),
      duration_ms: Math.max(500, Math.round(target.durationMs)),
      provider,
      label: target.label,
      episode_id: props.compilationId ? null : store.currentEpisodeId || null,
    })
    const finished = await store.watchJob(job.id, (update) => {
      if (update.status === 'queued' || update.status === 'running') setRun(true, `Separating with ${providerLabel}… ${update.message}`)
    }, { reloadOnComplete: false })
    if (!finished || finished.status !== 'completed') {
      throw new Error(finished?.error || finished?.message || `${providerLabel} separation failed`)
    }
    const result = await api.stemSeparation(separation.id)
    if (!result.music_asset) throw new Error(`${providerLabel} returned no music track`)
    await refreshTimelineAssets()
    const musicOverlayId = applyStemSeparation(target, result.music_asset, includeVoice ? result.voice_asset : null)
    setRun(false, `Music from ${providerLabel} added to the AUDIO track; original audio muted.`, '', [target.id, musicOverlayId])
  } catch (cause) {
    setRun(false, '', cause instanceof Error ? cause.message : `${providerLabel} separation failed`)
  }
}

function shotHasAudio(shot?: Shot | null): boolean {
  if (!shot) return false
  return Boolean(shot.dialogue_audio_filename || shot.clip || shot.media_variants?.some((v) => v.media_type === 'clip'))
}

const activeShotWithAudio = computed(() => {
  const refId = selectedPrimary.value?.source?.ref_id || activeItem.value?.source?.ref_id
  const currentShot = props.shots.find((s) => s.id === refId)
  if (shotHasAudio(currentShot)) return currentShot!
  return props.shots.find((s) => shotHasAudio(s)) ?? null
})

function addShotAudioToTimeline(shot?: Shot | null) {
  closeAddMenu()
  const target = shot ?? activeShotWithAudio.value
  if (!target) return
  const label = target.dialogue_audio_filename ? `${target.title || 'Shot'} dialogue` : `${target.title || 'Shot'} audio`
  addAudioOverlay({ kind: 'shot_audio', ref_id: target.id }, label, target.duration_ms || 3000)
  showAddShotModal.value = false
}

function probeFileDurationMs(file: File): Promise<number> {
  return new Promise((resolve) => {
    if (typeof Audio === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
      resolve(3000)
      return
    }
    let settled = false
    const objectUrl = URL.createObjectURL(file)
    const finish = (ms: number) => {
      if (settled) return
      settled = true
      try {
        URL.revokeObjectURL(objectUrl)
      } catch {}
      resolve(ms)
    }
    const timer = window.setTimeout(() => finish(3000), 1200)
    try {
      const audio = new Audio()
      audio.preload = 'metadata'
      audio.onloadedmetadata = () => {
        window.clearTimeout(timer)
        const dur = Number.isFinite(audio.duration) && audio.duration > 0 ? Math.round(audio.duration * 1000) : 3000
        finish(dur)
      }
      audio.onerror = () => {
        window.clearTimeout(timer)
        finish(3000)
      }
      audio.src = objectUrl
    } catch {
      window.clearTimeout(timer)
      finish(3000)
    }
  })
}

async function addAudioFile(file: File, label: string, durationMs?: number) {
  if (!timeline.value) return
  await flushPendingSave()
  timeline.value = await api.uploadTimelineAsset(
    props.project.id,
    file,
    label,
    variant.value,
    props.compilationId ? undefined : store.currentEpisodeId || undefined,
    'audio',
    props.compilationId,
  )
  timeline.value.document.video_overlays ??= []
  timeline.value.document.audio_overlays ??= []
  const asset = timeline.value.assets[timeline.value.assets.length - 1]
  if (asset) {
    addAudioOverlay({ kind: 'asset', ref_id: asset.id }, asset.label || label, durationMs)
  }
}

async function uploadAudioAsset(event: Event) {
  closeAddMenu()
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !timeline.value) return
  try {
    const probedMs = await probeFileDurationMs(file)
    await addAudioFile(file, file.name.replace(/\.[^.]+$/, '') || file.name, probedMs)
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Could not upload audio'
  } finally {
    input.value = ''
  }
}

const audioLibraryOpen = ref(false)
const audioLibraryPanel = ref<InstanceType<typeof AudioLibraryPanel> | null>(null)
const librarySaves = ref<Record<string, { busy: boolean; status: string; error: string }>>({})
const librarySave = computed(() => (stemTarget.value && librarySaves.value[stemTarget.value.id]) || { busy: false, status: '', error: '' })

function openAudioLibrary() {
  closeAddMenu()
  audioLibraryOpen.value = true
}

async function addFromLibrary(item: LibraryAudio) {
  try {
    const response = await fetch(item.url)
    if (!response.ok) throw new Error(`Could not load “${item.label}” from the library`)
    const blob = await response.blob()
    const file = new File([blob], `${item.label}.m4a`, { type: item.mime_type || 'audio/mp4' })
    await addAudioFile(file, item.label, item.duration_ms)
    audioLibraryOpen.value = false
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Could not add audio from the library'
  } finally {
    audioLibraryPanel.value?.done()
  }
}

async function saveToLibrary(label: string) {
  const target = stemTarget.value
  if (!target) return
  librarySaves.value = { ...librarySaves.value, [target.id]: { busy: true, status: '', error: '' } }
  try {
    const saved = await api.saveLibraryAudio(props.project.id, {
      source: target.source,
      source_in_ms: Math.round(target.sourceInMs),
      duration_ms: Math.round(target.durationMs),
      label,
    })
    librarySaves.value = { ...librarySaves.value, [target.id]: { busy: false, status: `Saved “${saved.label}” to the audio library`, error: '' } }
  } catch (cause) {
    librarySaves.value = { ...librarySaves.value, [target.id]: { busy: false, status: '', error: cause instanceof Error ? cause.message : 'Could not save to the library' } }
  }
}

async function uploadAsset(event: Event, append: boolean) {
  closeAddMenu()
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !timeline.value) return
  await flushPendingSave()
  timeline.value = await api.uploadTimelineAsset(
    props.project.id,
    file,
    file.name,
    variant.value,
    props.compilationId ? undefined : store.currentEpisodeId || undefined,
    'image',
    props.compilationId,
  )
  timeline.value.document.video_overlays ??= []
  timeline.value.document.audio_overlays ??= []
  const asset = timeline.value.assets[timeline.value.assets.length - 1]
  if (asset) {
    selectedKind.value = 'primary'
    const source: TimelineSource = { kind: 'asset', ref_id: asset.id }
    if (append) {
      const item: TimelinePrimaryItem = { id: crypto.randomUUID(), kind: 'image', label: asset.label, source, duration_ms: 2000, source_in_ms: 0, source_out_ms: null, text: '', variant: {} }
      const index = Math.max(0, selectedPrimaryIndex.value + 1)
      const insertionTime = index >= primary.value.length ? totalMs.value : (starts.value[index] ?? 0)
      timeline.value.document.primary.splice(index, 0, item)
      insertInterval(insertionTime, item.duration_ms)
      selectedId.value = item.id
      scheduleSave()
    } else addImageOverlay(source)
  }
  input.value = ''
}

let lastModifierToggleTime = 0
let lastModifierToggledId = ''

function selectItem(kind: 'primary' | 'video' | 'image' | 'text' | 'audio', id: string, start: number, event?: MouseEvent) {
  if (event && (event.shiftKey || event.ctrlKey || event.metaKey)) {
    if (Date.now() - lastModifierToggleTime > 300 || lastModifierToggledId !== id) {
      lastModifierToggleTime = Date.now()
      lastModifierToggledId = id
      toggleSelectEntry(kind, id)
    }
    return
  }
  // Select the layer. If the playhead is outside its interval, pause and seek into it
  // so the layer is immediately visible on the monitor.
  selectedKind.value = kind
  selectedId.value = id
  selectedEntries.value = [{ kind, id }]
  const item = kind === 'video'
    ? videoOverlays.value.find((entry) => entry.id === id)
    : kind === 'image'
      ? timeline.value?.document.image_overlays.find((entry) => entry.id === id)
      : kind === 'text'
        ? timeline.value?.document.text_overlays.find((entry) => entry.id === id)
        : kind === 'audio'
          ? timeline.value?.document.audio_overlays?.find((entry) => entry.id === id)
          : undefined
  if (item) {
    const itemStart = item.start_ms
    const itemEnd = itemStart + (item.duration_ms || 0)
    if (playheadMs.value < itemStart || playheadMs.value >= itemEnd) {
      stopPlayback()
      const duration = item.duration_ms || 0
      playheadMs.value = Math.min(totalMs.value, Math.max(0, itemStart + Math.min(1000, Math.floor(duration / 2))))
      void nextTick(() => {
        syncVideo()
        syncVideoOverlays()
      })
    }
  }
}

function seekToOverlay(item: { start_ms: number; duration_ms: number }) {
  stopPlayback()
  playheadMs.value = Math.min(totalMs.value, Math.max(0, item.start_ms + Math.min(1000, Math.floor(item.duration_ms / 2))))
  void nextTick(() => {
    syncVideo()
    syncVideoOverlays()
  })
}

function handlePrimaryBlockClick(event: MouseEvent, item: TimelinePrimaryItem, index: number) {
  stopPlayback()
  if (event.shiftKey || event.ctrlKey || event.metaKey) {
    toggleSelectEntry('primary', item.id)
    return
  }
  selectedKind.value = 'primary'
  selectedId.value = item.id
  selectedEntries.value = [{ kind: 'primary', id: item.id }]
  const start = startAt(index)
  const target = event.currentTarget as HTMLElement | null
  if (target) {
    const rect = target.getBoundingClientRect()
    if (rect.width > 0 && event.clientX > rect.left && event.clientX <= rect.right) {
      const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
      playheadMs.value = Math.round(start + ratio * item.duration_ms)
      syncVideo()
      return
    }
  }
  playheadMs.value = start
  syncVideo()
}

function stepFrame(direction: -1 | 1) {
  stopPlayback()
  playheadMs.value = Math.max(0, Math.min(totalMs.value, Math.round(playheadMs.value + direction * 50)))
  syncVideo()
  scrollPlayheadIntoView()
}

function jumpClip(direction: -1 | 1) {
  stopPlayback()
  if (!primary.value.length) return
  const currentIdx = activeIndex.value
  const currentStart = starts.value[currentIdx] ?? 0
  let targetIdx = currentIdx
  if (direction < 0) {
    if (playheadMs.value - currentStart > 250) {
      targetIdx = currentIdx
    } else {
      targetIdx = Math.max(0, currentIdx - 1)
    }
  } else {
    targetIdx = Math.min(primary.value.length - 1, currentIdx + 1)
  }
  const targetItem = primary.value[targetIdx]
  if (targetItem) {
    selectedKind.value = 'primary'
    selectedId.value = targetItem.id
    playheadMs.value = starts.value[targetIdx] ?? 0
    syncVideo()
    scrollPlayheadIntoView()
  }
}

function beginOverlayEdit(event: PointerEvent, kind: 'video' | 'image' | 'text' | 'audio', item: OverlayItem, mode: OverlayEditMode) {
  if (event.button !== 0 || !timeline.value || totalMs.value <= 0) return
  if (mode === 'move' && (event.shiftKey || event.ctrlKey || event.metaKey)) {
    lastModifierToggleTime = Date.now()
    lastModifierToggledId = item.id
    toggleSelectEntry(kind, item.id)
    return
  }
  const track = (event.currentTarget as HTMLElement).closest('.overlay-track')
  if (!track) return
  event.preventDefault()
  selectedKind.value = kind
  selectedId.value = item.id
  selectedEntries.value = [{ kind, id: item.id }]
  // Resize handles may jump the playhead to the edge being edited.
  // Move/select on secondary clips must not reset the current seek position.
  if (mode !== 'move') {
    stopPlayback()
    playheadMs.value = mode === 'resize-end' ? item.start_ms + item.duration_ms : item.start_ms
  }
  const trackRect = track.getBoundingClientRect()
  const packing = kind === 'video' ? videoOverlayPacking.value : kind === 'image' ? imageOverlayPacking.value : kind === 'audio' ? audioOverlayPacking.value : null
  const initialLane = packing?.laneById[item.id] ?? 0
  overlayEdit = {
    pointerId: event.pointerId,
    kind,
    item,
    mode,
    startX: event.clientX,
    startY: event.clientY,
    trackTop: trackRect.top,
    trackWidth: trackRect.width,
    initialStart: item.start_ms,
    initialDuration: item.duration_ms,
    initialLane,
    laneCount: packing?.laneCount ?? 1,
    before: snapshot(),
    moved: false,
  }
  if (packing) {
    item.lane = initialLane
    editingOverlayId.value = item.id
  }
  window.addEventListener('pointermove', updateOverlayEdit, { passive: false })
  window.addEventListener('pointerup', finishOverlayEdit)
  window.addEventListener('pointercancel', finishOverlayEdit)
}

function updateOverlayEdit(event: PointerEvent) {
  const edit = overlayEdit
  if (!edit || event.pointerId !== edit.pointerId) return
  event.preventDefault()
  if ((edit.kind === 'image' || edit.kind === 'video') && edit.mode === 'move') {
    overlayToPrimaryDropMs.value = primaryTrackMsAt(event.clientX, event.clientY)
    if (overlayToPrimaryDropMs.value !== null) return
  }
  const deltaMs = Math.round(((event.clientX - edit.startX) / edit.trackWidth * totalMs.value) / 20) * 20
  let nextStart = edit.initialStart
  let nextDuration = edit.initialDuration
  if (edit.mode === 'move') {
    nextStart = Math.max(0, Math.min(totalMs.value - edit.initialDuration, edit.initialStart + deltaMs))
  } else if (edit.mode === 'resize-start') {
    const end = edit.initialStart + edit.initialDuration
    nextStart = Math.max(0, Math.min(end - 100, edit.initialStart + deltaMs))
    nextDuration = end - nextStart
  } else {
    nextDuration = Math.max(100, Math.min(totalMs.value - edit.initialStart, edit.initialDuration + deltaMs))
  }
  let nextLane = edit.item.lane
  if (edit.mode === 'move' && edit.kind !== 'text') {
    // Rows only change on a deliberate vertical drag (more than half a row).
    const pointerLane = Math.floor((event.clientY - edit.trackTop - 5) / 32)
    nextLane = Math.abs(event.clientY - edit.startY) < 16
      ? edit.initialLane
      : Math.max(0, Math.min(edit.laneCount, pointerLane))
  }
  if (nextStart === edit.item.start_ms && nextDuration === edit.item.duration_ms && nextLane === edit.item.lane) return
  if (!edit.moved) {
    history.value.push(edit.before)
    if (history.value.length > 50) history.value.shift()
    future.value = []
    edit.moved = true
  }
  edit.item.start_ms = nextStart
  edit.item.duration_ms = nextDuration
  if (nextLane !== undefined) edit.item.lane = nextLane
  playheadMs.value = edit.mode === 'resize-end' ? nextStart + nextDuration : nextStart
  syncVideo()
}

function finishOverlayEdit(event?: PointerEvent) {
  if (event && overlayEdit && event.pointerId !== overlayEdit.pointerId) return
  const edit = overlayEdit
  const changed = edit?.moved ?? false
  const dropMs = event?.type === 'pointerup' ? overlayToPrimaryDropMs.value : null
  if (edit && edit.kind !== 'text') {
    const packing = edit.kind === 'video' ? videoOverlayPacking.value : edit.kind === 'image' ? imageOverlayPacking.value : audioOverlayPacking.value
    const shown = packing.laneById[edit.item.id]
    if (shown !== undefined) edit.item.lane = shown
  }
  overlayEdit = null
  editingOverlayId.value = null
  overlayToPrimaryDropMs.value = null
  window.removeEventListener('pointermove', updateOverlayEdit)
  window.removeEventListener('pointerup', finishOverlayEdit)
  window.removeEventListener('pointercancel', finishOverlayEdit)
  if (edit && (edit.kind === 'image' || edit.kind === 'video') && dropMs !== null) {
    moveOverlayToPrimary(edit.kind, edit.item as TimelineImageOverlay | TimelineVideoOverlay, dropMs, edit.moved ? null : edit.before)
    return
  }
  if (edit && !edit.moved && (event?.type === 'pointerup' || !event)) {
    selectItem(edit.kind, edit.item.id, edit.item.start_ms)
  }
  if (changed) scheduleSave()
}

function primaryTrackMsAt(clientX: number, clientY: number): number | null {
  const rect = primaryTrackRef.value?.getBoundingClientRect()
  if (!rect || clientY < rect.top || clientY > rect.bottom || clientX < rect.left || clientX > rect.right) return null
  return Math.max(0, Math.min(totalMs.value, ((clientX - rect.left) / (rect.width || 1)) * totalMs.value))
}

function primaryInsertIndexAt(ms: number): number {
  const index = primary.value.findIndex((item, i) => ms < startAt(i) + item.duration_ms / 2)
  return index < 0 ? primary.value.length : index
}

const overlayToPrimaryMarkerMs = computed(() => {
  if (overlayToPrimaryDropMs.value === null) return null
  const index = primaryInsertIndexAt(overlayToPrimaryDropMs.value)
  return index >= primary.value.length ? totalMs.value : startAt(index)
})

function moveOverlayToPrimary(
  kind: 'image' | 'video',
  overlay: TimelineImageOverlay | TimelineVideoOverlay,
  dropMs: number,
  historyBefore: string | null,
) {
  if (!timeline.value) return
  const doc = timeline.value.document
  if (historyBefore !== null) {
    history.value.push(historyBefore)
    if (history.value.length > 50) history.value.shift()
    future.value = []
  }
  const index = primaryInsertIndexAt(dropMs)
  const insertionTime = index >= primary.value.length ? totalMs.value : startAt(index)
  const duration = Math.max(100, overlay.duration_ms)
  let item: TimelinePrimaryItem
  if (kind === 'video' && 'source_in_ms' in overlay) {
    item = {
      id: crypto.randomUUID(),
      kind: 'video',
      label: overlay.label || 'Video',
      source: overlay.source,
      duration_ms: duration,
      source_in_ms: overlay.source_in_ms,
      source_out_ms: overlay.source_in_ms + duration,
      text: '',
      mute_audio: overlay.mute_audio ?? false,
      variant: {},
    }
    doc.video_overlays = (doc.video_overlays ?? []).filter((entry) => entry.id !== overlay.id)
  } else {
    const asset = overlay.source.kind === 'asset' ? timeline.value.assets.find((entry) => entry.id === overlay.source.ref_id) : undefined
    item = {
      id: crypto.randomUUID(),
      kind: 'image',
      label: asset?.label || 'Image',
      source: overlay.source,
      duration_ms: duration,
      source_in_ms: 0,
      source_out_ms: null,
      text: '',
      variant: {},
    }
    doc.image_overlays = doc.image_overlays.filter((entry) => entry.id !== overlay.id)
  }
  doc.primary.splice(index, 0, item)
  insertInterval(insertionTime, item.duration_ms)
  selectedKind.value = 'primary'
  selectedId.value = item.id
  selectedEntries.value = [{ kind: 'primary', id: item.id }]
  playheadMs.value = insertionTime
  syncVideo()
  scheduleSave()
}

let primaryResize: {
  pointerId: number
  item: TimelinePrimaryItem
  startX: number
  msPerPx: number
  initialDuration: number
  initialSpan: number
  before: string
  moved: boolean
} | null = null

function beginPrimaryResize(event: PointerEvent, item: TimelinePrimaryItem) {
  if (event.button !== 0 || !timeline.value || !primaryTrackRef.value) return
  stopPlayback()
  selectedKind.value = 'primary'
  selectedId.value = item.id
  selectedEntries.value = [{ kind: 'primary', id: item.id }]
  primaryResize = {
    pointerId: event.pointerId,
    item,
    startX: event.clientX,
    msPerPx: totalMs.value / (primaryTrackRef.value.getBoundingClientRect().width || 1),
    initialDuration: item.duration_ms,
    initialSpan: primarySpanMs(item),
    before: snapshot(),
    moved: false,
  }
  window.addEventListener('pointermove', updatePrimaryResize, { passive: false })
  window.addEventListener('pointerup', finishPrimaryResize)
  window.addEventListener('pointercancel', finishPrimaryResize)
}

function updatePrimaryResize(event: PointerEvent) {
  const edit = primaryResize
  if (!edit || event.pointerId !== edit.pointerId || !timeline.value) return
  event.preventDefault()
  const index = primary.value.findIndex((entry) => entry.id === edit.item.id)
  if (index < 0) return
  const deltaMs = Math.round(((event.clientX - edit.startX) * edit.msPerPx) / 100) * 100
  const nextDuration = Math.max(edit.item.kind === 'video' ? 200 : 500, edit.initialDuration + deltaMs)
  const previousDuration = edit.item.duration_ms
  if (nextDuration === previousDuration) return
  if (!edit.moved) {
    history.value.push(edit.before)
    if (history.value.length > 50) history.value.shift()
    future.value = []
    edit.moved = true
  }
  const start = startAt(index)
  if (nextDuration < previousDuration) removeInterval(start + nextDuration, previousDuration - nextDuration)
  else insertInterval(start + previousDuration, nextDuration - previousDuration)
  edit.item.duration_ms = nextDuration
  if (edit.item.kind === 'video') setVideoLength(edit.item, nextDuration, edit.initialSpan)
  playheadMs.value = start + nextDuration
  syncVideo()
}

function finishPrimaryResize(event?: PointerEvent) {
  if (event && primaryResize && event.pointerId !== primaryResize.pointerId) return
  const changed = primaryResize?.moved ?? false
  primaryResize = null
  window.removeEventListener('pointermove', updatePrimaryResize)
  window.removeEventListener('pointerup', finishPrimaryResize)
  window.removeEventListener('pointercancel', finishPrimaryResize)
  if (changed) scheduleSave()
}

function startAt(index: number) {
  return starts.value[index] ?? 0
}

function seek(event: Event) {
  const keepPlaying = playing.value
  playheadMs.value = Number((event.target as HTMLInputElement).value)
  if (!selectedId.value && activeItem.value) {
    selectedKind.value = 'primary'
    selectedId.value = activeItem.value.id
  }
  if (keepPlaying) {
    // Scrubbing the TIMELINE seek bar should not pause — retarget media and keep playing.
    void startActiveVideo()
  } else {
    syncVideo()
    persistUiState()
  }
}

const videoOverlayEls = new Map<string, HTMLVideoElement>()

function setVideoOverlayRef(id: string, el: HTMLVideoElement | null) {
  if (el) {
    videoOverlayEls.set(id, el)
  } else {
    videoOverlayEls.delete(id)
  }
}

const programMonitor = ref<HTMLElement | null>(null)
type BorderRect = { left: string; top: string; width: string; height: string }
const overlayBorderRects = ref<Record<string, BorderRect>>({})
const anyOverlayBorder = computed(() => videoOverlays.value.some((overlay) => Boolean(mediaBorderStyle(overlay.variant[variant.value]))))
let overlayBorderRaf = 0

/** Track each bordered overlay's visible (post-crop, post-transform) box so its border sits on the video edge. */
function measureOverlayBorders() {
  const monitor = programMonitor.value
  const next: Record<string, BorderRect> = {}
  if (monitor) {
    const m = monitor.getBoundingClientRect()
    for (const overlay of videoOverlays.value) {
      const geom = overlay.variant[variant.value]
      if (!mediaBorderStyle(geom)) continue
      const el = videoOverlayEls.get(overlay.id)
      if (!el || !el.isConnected) continue
      const r = el.getBoundingClientRect()
      if (r.width < 1 || r.height < 1) continue
      const insets = geometryCropInsets(geom!, geom!.position !== 'full-frame') ?? { top: 0, right: 0, bottom: 0, left: 0 }
      next[overlay.id] = {
        left: `${(r.left - m.left - monitor.clientLeft + insets.left * r.width).toFixed(1)}px`,
        top: `${(r.top - m.top - monitor.clientTop + insets.top * r.height).toFixed(1)}px`,
        width: `${(r.width * (1 - insets.left - insets.right)).toFixed(1)}px`,
        height: `${(r.height * (1 - insets.top - insets.bottom)).toFixed(1)}px`,
      }
    }
  }
  if (JSON.stringify(next) !== JSON.stringify(overlayBorderRects.value)) overlayBorderRects.value = next
}

function stopOverlayBorderLoop() {
  if (overlayBorderRaf) cancelAnimationFrame(overlayBorderRaf)
  overlayBorderRaf = 0
}

function startOverlayBorderLoop() {
  if (overlayBorderRaf || typeof requestAnimationFrame !== 'function') return
  const tick = () => {
    measureOverlayBorders()
    overlayBorderRaf = requestAnimationFrame(tick)
  }
  overlayBorderRaf = requestAnimationFrame(tick)
}

watch(
  anyOverlayBorder,
  (active) => {
    if (active) {
      startOverlayBorderLoop()
    } else {
      stopOverlayBorderLoop()
      overlayBorderRects.value = {}
    }
  },
  { immediate: true },
)

function pauseAllVideoOverlays() {
  for (const el of videoOverlayEls.values()) {
    try {
      el.pause()
    } catch {}
  }
}

function syncVideoOverlays() {
  const items = videoOverlays.value
  const activeIds = new Set(items.map((item) => item.id))
  for (const [id, el] of videoOverlayEls.entries()) {
    if (!activeIds.has(id)) {
      try {
        el.pause()
      } catch {}
      videoOverlayEls.delete(id)
    }
  }
  for (const overlay of items) {
    const el = videoOverlayEls.get(overlay.id)
    if (!el) continue
    const inWindow = playheadMs.value >= overlay.start_ms && playheadMs.value < overlay.start_ms + overlay.duration_ms
    const muted = isMuted.value || Boolean(overlay.mute_audio) || (overlay.volume ?? 1) <= 0
    el.muted = muted
    el.volume = Math.max(0, Math.min(1, overlay.volume ?? 1))
    const targetSec = Math.max(0, ((overlay.source_in_ms || 0) + (playheadMs.value - overlay.start_ms)) / 1000)
    if (playing.value && inWindow) {
      try {
        if (Math.abs((el.currentTime || 0) - targetSec) > 0.25) {
          el.currentTime = Number.isFinite(el.duration) ? Math.min(targetSec, el.duration) : targetSec
        }
        if (el.paused) {
          void el.play()?.catch?.(() => {
            el.muted = true
            void el.play()?.catch?.(() => undefined)
          })
        }
      } catch {}
    } else {
      try {
        if (!el.paused) el.pause()
        if (inWindow && Math.abs((el.currentTime || 0) - targetSec) > 0.04) {
          el.currentTime = Number.isFinite(el.duration) ? Math.min(targetSec, el.duration) : targetSec
        }
      } catch {}
    }
  }
}

const audioOverlayPlayers = new Map<string, HTMLAudioElement>()
/** Audio file lengths in ms, keyed by URL, learned from the preview players' metadata. */
const audioFileDurations = ref<Record<string, number>>({})

/** Length of the portion that repeats, or null when the clip doesn't outlast its file. */
function audioLoopSpanMs(overlay: TimelineAudioOverlay): number | null {
  if (overlay.loop === false) return null
  const url = resolveSource(overlay.source)?.url
  const total = url ? audioFileDurations.value[url] : undefined
  if (!total) return null
  const inMs = (overlay.source_in_ms || 0) % total
  const span = total - inMs
  return span > 50 && (span < overlay.duration_ms || (overlay.source_in_ms || 0) >= total) ? span : null
}

function toggleSelectedAudioLoop() {
  const item = selectedAudio.value
  if (!item) return
  mutate(() => {
    item.loop = item.loop === false
  })
}

function pauseAllAudioOverlays() {
  for (const el of audioOverlayPlayers.values()) {
    try {
      el.pause()
    } catch {}
  }
}

function syncAudioOverlays() {
  if (typeof Audio === 'undefined') return
  const items = audioOverlays.value
  const activeIds = new Set(items.map((item) => item.id))
  for (const [id, el] of audioOverlayPlayers.entries()) {
    if (!activeIds.has(id)) {
      try {
        el.pause()
      } catch {}
      audioOverlayPlayers.delete(id)
    }
  }
  for (const overlay of items) {
    const resolved = resolveSource(overlay.source)
    if (!resolved?.url) {
      const existing = audioOverlayPlayers.get(overlay.id)
      if (existing) {
        try {
          existing.pause()
        } catch {}
        audioOverlayPlayers.delete(overlay.id)
      }
      continue
    }
    let player = audioOverlayPlayers.get(overlay.id)
    if (!player) {
      try {
        player = new Audio()
        player.preload = 'auto'
        const el = player
        el.addEventListener('loadedmetadata', () => {
          const src = el.dataset.src
          if (src && Number.isFinite(el.duration) && el.duration > 0) {
            audioFileDurations.value = { ...audioFileDurations.value, [src]: Math.round(el.duration * 1000) }
          }
        })
        audioOverlayPlayers.set(overlay.id, player)
      } catch {
        continue
      }
    }
    if (player.dataset.src !== resolved.url) {
      player.dataset.src = resolved.url
      player.src = resolved.url
    }
    const inWindow = playheadMs.value >= overlay.start_ms && playheadMs.value < overlay.start_ms + overlay.duration_ms
    const muted = isMuted.value || Boolean(overlay.mute_audio) || (overlay.volume ?? 1) <= 0
    player.muted = muted
    player.volume = Math.max(0, Math.min(1, overlay.volume ?? 1))
    const elapsed = Math.max(0, playheadMs.value - overlay.start_ms)
    const url = resolveSource(overlay.source)?.url
    const total = url ? audioFileDurations.value[url] : undefined
    const span = audioLoopSpanMs(overlay)
    let targetSec = 0
    if (span && span > 0 && total && total > 0) {
      const inMs = (overlay.source_in_ms || 0) % total
      targetSec = ((inMs + (elapsed % span)) % total) / 1000
    } else if (total && total > 0 && overlay.loop !== false) {
      targetSec = (((overlay.source_in_ms || 0) + elapsed) % total) / 1000
    } else {
      targetSec = ((overlay.source_in_ms || 0) + (span ? elapsed % span : elapsed)) / 1000
    }
    if (overlay.loop !== false && Number.isFinite(player.duration) && player.duration > 0 && targetSec >= player.duration) {
      targetSec = targetSec % player.duration
    }
    if (playing.value && inWindow && !muted) {
      try {
        if (Math.abs((player.currentTime || 0) - targetSec) > 0.25) {
          player.currentTime = targetSec
        }
        if (player.paused) {
          void player.play()?.catch?.(() => undefined)
        }
      } catch {}
    } else {
      try {
        if (!player.paused) player.pause()
        if (inWindow && Math.abs((player.currentTime || 0) - targetSec) > 0.05) {
          player.currentTime = targetSec
        }
      } catch {}
    }
  }
}

function toggleMute() {
  isMuted.value = !isMuted.value
  if (previewVideo.value) {
    previewVideo.value.muted = isMuted.value || Boolean(activeItem.value?.mute_audio)
  }
  syncVideoOverlays()
  syncAudioOverlays()
  persistUiState()
}

function syncVideo() {
  syncVideoOverlays()
  syncAudioOverlays()
  const video = previewVideo.value
  const item = activeItem.value
  if (!video || !item || !activeMedia.value?.mime_type.startsWith('video')) return
  const isItemMuted = Boolean(item.mute_audio)
  video.muted = isMuted.value || isItemMuted
  const target = Math.max(0, primarySourceMs(item, activeLocalMs.value) / 1000)
  if (Number.isFinite(video.duration)) video.currentTime = Math.min(target, video.duration)
  else video.currentTime = target
}

function wrapLoopingPreview() {
  const video = previewVideo.value
  const item = activeItem.value
  if (!video || !item?.loop || item.source_out_ms == null) return
  if (video.currentTime * 1000 < item.source_out_ms - 40 && !video.ended) return
  syncVideo()
  if (playing.value && video.paused) void video.play().catch(() => undefined)
}

function toggleSelectedAudio() {
  if (selectedKind.value === 'video' && selectedVideo.value) {
    const videoItem = selectedVideo.value
    mutate(() => {
      videoItem.mute_audio = !videoItem.mute_audio
    })
    syncVideoOverlays()
    return
  }
  if (selectedKind.value === 'audio' && selectedAudio.value) {
    const audioItem = selectedAudio.value
    mutate(() => {
      audioItem.mute_audio = !audioItem.mute_audio
    })
    syncAudioOverlays()
    return
  }
  const item = selectedPrimary.value
  if (!item || item.kind !== 'video') return
  mutate(() => {
    item.mute_audio = !item.mute_audio
  })
  syncVideo()
}

function toggleBlockAudio(kind: 'primary' | 'video' | 'audio', id: string) {
  if (!timeline.value) return
  if (kind === 'primary') {
    const target = timeline.value.document.primary.find((item) => item.id === id)
    if (!target || target.kind !== 'video') return
    mutate(() => {
      target.mute_audio = !target.mute_audio
    })
    syncVideo()
    return
  }
  if (kind === 'video') {
    const target = (timeline.value.document.video_overlays ?? []).find((item) => item.id === id)
    if (!target) return
    mutate(() => {
      target.mute_audio = !target.mute_audio
    })
    syncVideoOverlays()
    return
  }
  const target = (timeline.value.document.audio_overlays ?? []).find((item) => item.id === id)
  if (!target) return
  mutate(() => {
    target.mute_audio = !target.mute_audio
  })
  syncAudioOverlays()
}

function detachSelectedVideoAudio() {
  if (!timeline.value) return
  if (selectedKind.value === 'video' && selectedVideo.value) {
    const videoItem = selectedVideo.value
    mutate(() => {
      const audioSource: TimelineSource =
        videoItem.source.kind === 'shot_image'
          ? { kind: 'shot_audio', ref_id: videoItem.source.ref_id }
          : JSON.parse(JSON.stringify(videoItem.source))
      const audioItem: TimelineAudioOverlay = {
        id: crypto.randomUUID(),
        label: `${videoItem.label.replace(/ \(copy\)$/i, '')} audio`,
        source: audioSource,
        start_ms: Math.round(videoItem.start_ms),
        duration_ms: Math.round(videoItem.duration_ms),
        source_in_ms: Math.round(videoItem.source_in_ms || 0),
        volume: videoItem.volume ?? 1,
        mute_audio: false,
      }
      videoItem.mute_audio = true
      timeline.value!.document.audio_overlays ??= []
      timeline.value!.document.audio_overlays.push(audioItem)
      clampOverlaysToTotal()
    })
    syncVideo()
    return
  }
  const primaryItem = selectedPrimary.value
  const primaryIndex = selectedPrimaryIndex.value
  if (!primaryItem || primaryItem.kind !== 'video' || !primaryItem.source || primaryIndex < 0) return
  const clipStartMs = startAt(primaryIndex)
  mutate(() => {
    const rawSource: TimelineSource = JSON.parse(JSON.stringify(primaryItem.source!))
    const audioSource: TimelineSource =
      rawSource.kind === 'shot_image' ? { kind: 'shot_audio', ref_id: rawSource.ref_id } : rawSource
    const audioItem: TimelineAudioOverlay = {
      id: crypto.randomUUID(),
      label: `${primaryItem.label.replace(/ \(copy\)$/i, '')} audio`,
      source: audioSource,
      start_ms: Math.round(clipStartMs),
      duration_ms: Math.round(primaryItem.duration_ms),
      source_in_ms: Math.round(primaryItem.source_in_ms || 0),
      volume: 1,
      mute_audio: false,
    }
    primaryItem.mute_audio = true
    timeline.value!.document.audio_overlays ??= []
    timeline.value!.document.audio_overlays.push(audioItem)
    clampOverlaysToTotal()
  })
  syncVideo()
}

function soloVideoLayerAudio() {
  if (!timeline.value || !selectedVideo.value) return
  const videoItem = selectedVideo.value
  mutate(() => {
    videoItem.mute_audio = false
    const startMs = videoItem.start_ms
    const endMs = videoItem.start_ms + videoItem.duration_ms
    let elapsed = 0
    for (const item of timeline.value!.document.primary) {
      const itemStart = elapsed
      const itemEnd = elapsed + item.duration_ms
      elapsed = itemEnd
      if (itemEnd <= startMs || itemStart >= endMs) continue
      if (item.kind === 'video') {
        item.mute_audio = true
      }
    }
  })
  syncVideo()
}

const isDraggingOverAudioTrack = ref(false)

function handleAudioTrackDragOver(event: DragEvent) {
  event.preventDefault()
  if (event.dataTransfer) {
    const fromIndex = getDraggedPrimaryIndex(event)
    const isDuplicate = isDuplicateDragEvent(event)
    event.dataTransfer.dropEffect = fromIndex !== null && !isDuplicate ? 'move' : 'copy'
  }
  isDraggingOverAudioTrack.value = true
}

function handleAudioTrackDragLeave(event: DragEvent) {
  const currentTarget = event.currentTarget as HTMLElement | null
  const relatedTarget = event.relatedTarget as Node | null
  if (currentTarget && relatedTarget && currentTarget.contains(relatedTarget)) {
    return
  }
  isDraggingOverAudioTrack.value = false
}

async function handleAudioTrackDrop(event: DragEvent) {
  event.preventDefault()
  event.stopPropagation()
  isDraggingOverAudioTrack.value = false

  const target = event.currentTarget as HTMLElement | null
  const overlayTrackEl = (target?.querySelector('.overlay-track') ?? (target?.classList.contains('overlay-track') ? target : null)) as HTMLElement | null

  const fromIndex = getDraggedPrimaryIndex(event)
  if (fromIndex !== null) {
    const isDuplicate = isDuplicateDragEvent(event)
    dragSourceIndex.value = null
    dragOverIndex.value = null
    dragDuplicateModifier.value = false
    const draggedItem = primary.value[fromIndex]
    if (!timeline.value || !draggedItem || !draggedItem.source) return

    let dropMs = startAt(fromIndex)
    if (overlayTrackEl && totalMs.value > 0) {
      const rect = overlayTrackEl.getBoundingClientRect()
      if (rect.width > 0 && (event.clientX > 0 || event.clientY > 0)) {
        const relativeX = Math.max(0, Math.min(rect.width, event.clientX - rect.left))
        dropMs = Math.round((relativeX / rect.width) * totalMs.value)
      }
    }

    mutate(() => {
      const sourceItem = timeline.value!.document.primary[fromIndex]
      if (!sourceItem || !sourceItem.source) return
      const rawSource: TimelineSource = JSON.parse(JSON.stringify(sourceItem.source))
      const audioSource: TimelineSource =
        rawSource.kind === 'shot_image' ? { kind: 'shot_audio', ref_id: rawSource.ref_id } : rawSource
      const nextTotalMs = timeline.value!.document.primary.reduce((sum, item) => sum + item.duration_ms, 0)
      const baseDur = Math.max(500, Math.round(sourceItem.duration_ms || 3000))
      const duration = nextTotalMs > 0 ? Math.max(100, Math.min(baseDur, nextTotalMs)) : baseDur
      const start = nextTotalMs > 0 ? Math.max(0, Math.min(Math.round(dropMs), Math.max(0, nextTotalMs - duration))) : 0

      if (!isDuplicate) {
        sourceItem.mute_audio = true
      }

      const audioItem: TimelineAudioOverlay = {
        id: crypto.randomUUID(),
        label: `${sourceItem.label.replace(/ \(copy\)$/i, '')} audio`,
        source: audioSource,
        start_ms: start,
        duration_ms: duration,
        source_in_ms: Math.round(sourceItem.source_in_ms || 0),
        volume: 1,
        mute_audio: false,
      }
      timeline.value!.document.audio_overlays ??= []
      timeline.value!.document.audio_overlays.push(audioItem)
      clampOverlaysToTotal()
      selectedKind.value = 'audio'
      selectedId.value = audioItem.id
      playheadMs.value = audioItem.start_ms
    })
    nextTick(() => {
      syncVideo()
    })
  }
}

function handleLoadedMetadata() {
  const video = previewVideo.value
  if (video) video.muted = isMuted.value || Boolean(activeItem.value?.mute_audio)
  sourceDurationMs.value = video && Number.isFinite(video.duration) ? Math.round(video.duration * 1000) : selectedTrimOutMs.value
  syncVideo()
  void syncLutGlPreview()
}

async function startActiveVideo() {
  await nextTick()
  syncVideo()
  const video = previewVideo.value
  if (playing.value && video && activeMedia.value?.mime_type.startsWith('video')) {
    video.muted = isMuted.value || Boolean(activeItem.value?.mute_audio)
    try {
      await video.play()
    } catch {
      // Fall back to muted playback on the element if autoplay restrictions require user interaction
      video.muted = true
      await video.play().catch(() => undefined)
    }
  }
}

function stopPlayback() {
  const wasPlaying = playing.value
  playing.value = false
  if (playbackTimer !== undefined) cancelAnimationFrame(playbackTimer)
  playbackTimer = undefined
  previewVideo.value?.pause()
  pauseAllVideoOverlays()
  pauseAllAudioOverlays()
  if (wasPlaying && !loading.value) persistUiState()
}

function togglePlayback() {
  if (playing.value) {
    stopPlayback()
    return
  }
  if (playheadMs.value >= totalMs.value) playheadMs.value = 0
  playing.value = true
  void startActiveVideo()
  let previous = performance.now()
  const tick = (now: number) => {
    if (!playing.value) return
    playheadMs.value = Math.min(totalMs.value, playheadMs.value + now - previous)
    previous = now
    wrapLoopingPreview()
    syncVideoOverlays()
    syncAudioOverlays()
    scrollPlayheadIntoView()
    if (playheadMs.value >= totalMs.value) {
      if (loopPlayback.value && totalMs.value > 0) {
        playheadMs.value = 0
        void startActiveVideo()
        playbackTimer = requestAnimationFrame(tick)
      } else {
        stopPlayback()
      }
    } else {
      playbackTimer = requestAnimationFrame(tick)
    }
  }
  playbackTimer = requestAnimationFrame(tick)
}

const unresolvedTimelineShots = computed(() => {
  const byId = new Map(props.shots.map((shot) => [shot.id, shot]))
  const orderedIds = timeline.value?.document.primary
    .map((item) => item.source?.kind.startsWith('shot_') ? item.source.ref_id : null)
    .filter((id): id is string => Boolean(id)) || []
  return [...new Set(orderedIds)]
    .map((id) => byId.get(id))
    .filter((shot): shot is Shot => Boolean(shot))
    .filter((shot) => (shot.unresolved_note_count ?? 0) > 0 || (shot.notes || []).some((note) => !note.resolved))
})

function hasUnresolvedNote(item: TimelinePrimaryItem): boolean {
  if (!item.source?.ref_id) return false
  const shot = props.shots.find((s) => s.id === item.source?.ref_id)
  if (!shot) return false
  return (shot.unresolved_note_count ?? 0) > 0 || (shot.notes || []).some((n) => !n.resolved)
}

async function performRenderVariant() {
  await flushPendingSave()
  if (props.compilationId) {
    await store.renderCompilationExport(props.compilationId, variant.value)
    try {
      compilationExports.value = await api.compilationExports(props.compilationId)
    } catch {}
  } else {
    await store.renderFinalCut(variant.value)
  }
}

async function renderVariant() {
  if (unresolvedTimelineShots.value.length) {
    showUnresolvedNotesModal.value = true
    return
  }
  await performRenderVariant()
}

async function continueExportWithNotes() {
  showUnresolvedNotesModal.value = false
  await performRenderVariant()
}

async function reviewShot(shotId: string) {
  showUnresolvedNotesModal.value = false
  await router.push({
    name: 'project-stage',
    params: { projectId: props.project.id, stage: 'shots', shotId },
    query: store.currentEpisodeId ? { episode: store.currentEpisodeId } : {},
  })
}

async function reviewFirstUnresolvedNote() {
  const shot = unresolvedTimelineShots.value[0]
  if (!shot) return
  await reviewShot(shot.id)
}

async function resetTimeline() {
  if (!confirm('Reset the timeline to the current shot plan? All timeline edits and overlays will be removed.')) return
  window.clearTimeout(saveTimer)
  saveTimer = undefined
  dirtyDocument = false
  pendingSave = false
  if (props.compilationId) {
    timeline.value = await api.resetCompilationTimeline(props.compilationId, variant.value)
  } else {
    timeline.value = await api.resetTimeline(props.project.id, store.currentEpisodeId || undefined)
  }
  timeline.value.document.video_overlays ??= []
  timeline.value.document.audio_overlays ??= []
  selectedKind.value = 'primary'
  selectedId.value = timeline.value.document.primary[0]?.id ?? ''
  playheadMs.value = 0
  persistUiState()
  if (!props.compilationId) {
    await store.loadProject(props.project.id, { episodeId: store.currentEpisodeId || undefined, resumeJobs: false })
  }
}

watch([isMuted, zoomLevel, selectedKind, selectedId, variant], () => {
  if (!loading.value) persistUiState()
})
watch(playheadMs, () => {
  if (!loading.value && !playing.value) persistUiState()
})
watch([() => props.project.id, () => store.currentEpisodeId, () => props.compilationId], async ([newProjectId, newEpisodeId, newCompId], [oldProjectId, oldEpisodeId, oldCompId]) => {
  if (newProjectId === oldProjectId && newEpisodeId === oldEpisodeId && newCompId === oldCompId) return
  await flushPendingSave()
  await loadTimeline()
})
watch(variant, async () => {
  stopPlayback()
  persistUiState()
  emit('variantChange', variant.value)
  await flushPendingSave()
  await loadTimeline()
  await startActiveVideo()
})
watch(activeIndex, () => void startActiveVideo())
watch(
  () => activeMedia.value?.url,
  async (newUrl, oldUrl) => {
    if (!newUrl || newUrl === oldUrl) return
    await nextTick()
    const video = previewVideo.value
    if (video && activeMedia.value?.mime_type.startsWith('video')) {
      try {
        video.load?.()
      } catch {
        // Ignore in non-browser/jsdom environments
      }
      if (playing.value) {
        void startActiveVideo()
      } else {
        syncVideo()
      }
    }
  }
)
function isTypingTarget(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName.toLowerCase()
  if (tag === 'textarea' || tag === 'select') return true
  if (tag === 'input') {
    const type = ((target as HTMLInputElement).type || 'text').toLowerCase()
    return type !== 'range' && type !== 'file' && type !== 'checkbox' && type !== 'radio' && type !== 'button'
  }
  return false
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && transitionPopoverIndex.value !== null) {
    transitionPopoverIndex.value = null
    event.preventDefault()
    return
  }
  if (event.target instanceof Element && event.target.closest('.transition-popover')) return
  if (isTypingTarget(event.target)) return
  if (showShortcutsModal.value || showAddShotModal.value || showAddVideoOverlayModal.value || showAddImageOverlayModal.value || showPublishModal.value || showReleaseKit.value) {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeModalsFromRoute()
      showShortcutsModal.value = false
      showAddShotModal.value = false
      showAddVideoOverlayModal.value = false
      showAddImageOverlayModal.value = false
    }
    return
  }
  if (event.key === 'Escape' && addMenuRef.value?.open) {
    event.preventDefault()
    closeAddMenu()
    return
  }
  if (event.key === '?') {
    event.preventDefault()
    showShortcutsModal.value = true
    return
  }
  if (event.code === 'Space' && event.target instanceof HTMLElement && event.target.closest('summary, a')) return

  const isCmdOrCtrl = event.metaKey || event.ctrlKey

  // Ctrl+Z or Cmd+Z (Undo / Redo)
  if (isCmdOrCtrl && (event.key === 'z' || event.key === 'Z')) {
    event.preventDefault()
    if (event.shiftKey) redo()
    else undo()
    return
  }

  // Ctrl+Y or Cmd+Y (Redo)
  if (isCmdOrCtrl && (event.key === 'y' || event.key === 'Y')) {
    event.preventDefault()
    redo()
    return
  }

  // Ctrl+D or Cmd+D (Duplicate)
  if (isCmdOrCtrl && (event.key === 'd' || event.key === 'D')) {
    event.preventDefault()
    duplicateSelected()
    return
  }

  // Space (Play / Pause)
  if (event.code === 'Space') {
    event.preventDefault()
    togglePlayback()
    return
  }

  // S or C or Ctrl+K (Split clip at playhead)
  if (!isCmdOrCtrl && (event.key === 's' || event.key === 'S' || event.key === 'c' || event.key === 'C')) {
    event.preventDefault()
    splitAtPlayhead()
    return
  }
  if (isCmdOrCtrl && (event.key === 'k' || event.key === 'K')) {
    event.preventDefault()
    splitAtPlayhead()
    return
  }

  // Delete or Backspace (Delete selected item)
  if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault()
    deleteSelection()
    return
  }

  // Left Arrow / Right Arrow (Frame / playhead stepping)
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    stopPlayback()
    const step = event.shiftKey ? 1000 : 50
    playheadMs.value = Math.max(0, Math.round(playheadMs.value - step))
    syncVideo()
    scrollPlayheadIntoView()
    return
  }
  if (event.key === 'ArrowRight') {
    event.preventDefault()
    stopPlayback()
    const step = event.shiftKey ? 1000 : 50
    playheadMs.value = Math.min(totalMs.value, Math.round(playheadMs.value + step))
    syncVideo()
    scrollPlayheadIntoView()
    return
  }

  // Home / End
  if (event.key === 'Home') {
    event.preventDefault()
    stopPlayback()
    playheadMs.value = 0
    syncVideo()
    scrollPlayheadIntoView()
    return
  }
  if (event.key === 'End') {
    event.preventDefault()
    stopPlayback()
    playheadMs.value = totalMs.value
    syncVideo()
    scrollPlayheadIntoView()
    return
  }

  // Zoom: + / = to zoom in, - to zoom out, 0 to reset
  if ((isCmdOrCtrl && (event.key === '=' || event.key === '+')) || (!isCmdOrCtrl && (event.key === '=' || event.key === '+'))) {
    event.preventDefault()
    zoomIn()
    return
  }
  if ((isCmdOrCtrl && (event.key === '-' || event.key === '_')) || (!isCmdOrCtrl && (event.key === '-' || event.key === '_'))) {
    event.preventDefault()
    zoomOut()
    return
  }
  if (isCmdOrCtrl && event.key === '0') {
    event.preventDefault()
    resetZoom()
    return
  }

  // M (Mute audio on selected clip)
  if (!isCmdOrCtrl && (event.key === 'm' || event.key === 'M')) {
    event.preventDefault()
    toggleSelectedAudio()
    return
  }

  // I / O (Set trim in / out)
  if (!isCmdOrCtrl && (event.key === 'i' || event.key === 'I')) {
    event.preventDefault()
    setTrimAtPlayhead('in')
    return
  }
  if (!isCmdOrCtrl && (event.key === 'o' || event.key === 'O')) {
    event.preventDefault()
    setTrimAtPlayhead('out')
    return
  }

  // Escape: Close shortcuts modal or deselect
  if (event.key === 'Escape') {
    if (showShortcutsModal.value || showAddShotModal.value || showAddVideoOverlayModal.value || showAddImageOverlayModal.value || showPublishModal.value || showReleaseKit.value) {
      closeModalsFromRoute()
      event.preventDefault()
      showShortcutsModal.value = false
      showAddShotModal.value = false
      showAddVideoOverlayModal.value = false
      showAddImageOverlayModal.value = false
    }
  }
}

let resizeObserver: ResizeObserver | null = null

function handleBeforeUnload() {
  persistUiState()
  void flushPendingSave()
}

onMounted(() => {
  emit('variantChange', variant.value)
  updateContainerWidth()
  if (typeof ResizeObserver !== 'undefined' && timelineScrollRef.value) {
    resizeObserver = new ResizeObserver(() => updateContainerWidth())
    resizeObserver.observe(timelineScrollRef.value)
  }
  window.addEventListener('resize', updateContainerWidth)
  window.addEventListener('keydown', handleKeydown)
  window.addEventListener('pointerdown', handleDocumentPointerDown, true)
  window.addEventListener('beforeunload', handleBeforeUnload)
  void loadTimeline()
  void api.getTimelineLuts().then((list) => {
    if (Array.isArray(list) && list.length > 0) {
      lutPresets.value = list
      registerLutPresets(list)
    }
  }).catch(() => {})
  void api.getTimelineLayouts().then((catalog) => {
    if (catalog?.shorts && catalog?.landscape) layoutCatalog.value = catalog
  }).catch(() => {})
})
onBeforeUnmount(() => {
  if (resizeObserver) resizeObserver.disconnect()
  window.removeEventListener('resize', updateContainerWidth)
  window.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('pointerdown', handleDocumentPointerDown, true)
  window.removeEventListener('beforeunload', handleBeforeUnload)
  lutGl.value?.dispose()
  lutGl.value = null
  lutGlActive.value = false
  stopOverlayBorderLoop()
  stopPlayback()
  finishOverlayEdit()
  persistUiState()
  void flushPendingSave()
})
</script>

<template>
  <section class="timeline-editor">
    <header class="editor-header">
      <div class="editor-title-block">
        <div class="timeline-title-row">
          <h2>Timeline</h2>
          <div v-if="props.compilationId && store.activeCompilation" class="compilation-badge-pill">
            <Boxes :size="13" />
            <span>{{ store.activeCompilation.episode_id || store.activeCompilation.shot_ids?.length ? 'Short Cut' : 'Box' }}: {{ store.activeCompilation.title }}</span>
            <button
              type="button"
              class="edit-compilation-btn"
              title="Edit compilation details and shots"
              @click="emit('openCompilationModal', props.compilationId)"
            >
              <Pencil :size="11" />
            </button>
            <button
              type="button"
              class="exit-compilation-btn"
              title="Exit compilation mode and return to episode timeline"
              @click="store.selectCompilation(null)"
            >
              <X :size="12" />
            </button>
          </div>
        </div>
        <p>
          <span>{{ primary.length }} {{ primary.length === 1 ? 'clip' : 'clips' }}</span>
          <span class="meta-dot">·</span>
          <span>{{ (totalMs / 1000).toFixed(1) }} seconds</span>
          <span class="save-status" :class="{ unsaved: saveState === 'Unsaved', saving: saveState === 'Saving...', failed: saveState === 'Save failed' }" role="status">{{ saveState }}</span>
        </p>
      </div>
      <div class="editor-actions">
        <div class="variant-toggle" role="group" aria-label="Output format">
          <button :class="{ active: variant === 'landscape' }" @click="variant = 'landscape'">Landscape 16:9</button>
          <button :class="{ active: variant === 'shorts' }" @click="variant = 'shorts'">Shorts 9:16</button>
        </div>
        <button class="secondary-button" :disabled="saving" @click="saveTimeline"><Save :size="15" />Save</button>
        <button class="primary-button" :disabled="store.renderingFinalCut || !timeline" @click="renderVariant"><LoaderCircle v-if="store.renderingFinalCut" class="spin" :size="15" /><RefreshCw v-else :size="15" />{{ store.renderingFinalCut ? (store.finalCutJob?.progress ? `Rendering… ${Math.round(store.finalCutJob.progress * 100)}%` : 'Rendering…') : variantExport ? 'Render again' : 'Export video' }}</button>
        <button
          v-if="store.renderingFinalCut && store.finalCutJob && (store.finalCutJob.status === 'queued' || store.finalCutJob.status === 'running')"
          class="secondary-button"
          :disabled="store.isCancellingJob(store.finalCutJob.id)"
          @click="store.cancelJob(store.finalCutJob.id)"
        >
          <X :size="14" />{{ store.isCancellingJob(store.finalCutJob.id) ? 'Cancelling…' : 'Cancel' }}
        </button>
        <a v-if="variantExport" class="secondary-button" :href="variantExport.url" :download="downloadFilename"><Download :size="15" />MP4</a>
        <button
          class="secondary-button"
          title="Frames, AI titles, description, tags and covers for this video"
          @click="openReleaseKit()"
        >
          <Package :size="14" />
          <span>Release kit</span>
        </button>
        <button
          class="secondary-button publish-header-btn"
          title="Publish to YouTube and social channels"
          @click="openPublishModal()"
        >
          <Share2 :size="14" />
          <span>Publish</span>
        </button>
      </div>
    </header>

    <div v-if="store.renderingFinalCut && store.finalCutJob" class="render-progress-banner">
      <div class="render-progress-info">
        <div class="render-progress-label">
          <LoaderCircle class="spin" :size="14" />
          <span>{{ store.finalCutJob.message || 'Exporting video cut…' }}</span>
        </div>
        <span class="render-progress-pct">{{ Math.round((store.finalCutJob.progress || 0) * 100) }}%</span>
      </div>
      <div class="render-progress-track">
        <div
          class="render-progress-fill"
          :style="{ width: `${Math.min(100, Math.max(0, Math.round((store.finalCutJob.progress || 0) * 100)))}%` }"
        ></div>
      </div>
    </div>

    <div v-if="localError || store.error" class="form-error timeline-error-banner">
      <span>{{ localError || store.error }}</span>
      <button type="button" class="form-error-dismiss" aria-label="Dismiss error" @click="localError = ''; store.error = ''"><X :size="13" /></button>
    </div>
    <div v-if="loading" class="timeline-loading"><LoaderCircle class="spin" :size="22" />Loading timeline</div>
    <template v-else-if="timeline">
      <div class="editor-main">
        <div class="preview-pane">
          <section ref="programMonitor" class="program-monitor" :class="[variant, { 'has-layout': !isFullBleedLayout }]" :style="programMonitorStyle">
            <div
              v-if="!isFullBleedLayout"
              class="layout-matte"
              :class="[`matte-${canvasLayout.matte}`]"
              :style="layoutMatteStyle()"
              aria-hidden="true"
            ></div>
            <div
              class="preview-primary-window"
              :class="{ 'can-reframe': isReframingActivePrimary, 'is-dragging': isDraggingPrimaryReframe }"
              :style="primaryWindowStyle"
              :title="isReframingActivePrimary ? 'Drag horizontally to adjust 9:16 crop framing' : undefined"
              @pointerdown="onPrimaryReframePointerDown"
              @dragstart.prevent
            >
              <video
                v-if="activeMedia?.mime_type.startsWith('video')"
                ref="previewVideo"
                :src="activeMedia.url"
                class="preview-primary-media"
                :class="{ 'lut-source-hidden': lutGlActive }"
                :style="activeMediaStyle"
                :muted="isMuted || Boolean(activeItem?.mute_audio)"
                playsinline
                preload="auto"
                draggable="false"
                @loadedmetadata="handleLoadedMetadata"
              ></video>
              <img
                v-else-if="activeMedia"
                ref="previewImage"
                class="preview-primary-media"
                :class="{ 'lut-source-hidden': lutGlActive }"
                :src="activeMedia.url"
                :style="activeMediaStyle"
                alt=""
                draggable="false"
                @load="syncLutGlPreview"
              />
              <div v-else-if="activeItem?.kind === 'title'" class="preview-title" :style="activeMediaStyle">{{ activeItem.text }}</div>
              <div v-else class="preview-missing" :style="activeMediaStyle">Missing media</div>
              <canvas
                v-show="lutGlActive && activeMedia"
                ref="lutCanvas"
                class="preview-primary-media lut-gl-canvas"
                :style="lutCanvasStyle"
                aria-hidden="true"
              ></canvas>
              <div
                v-if="isReframingActivePrimary"
                class="preview-reframe-overlay"
                aria-hidden="true"
              ></div>
            </div>
            <div
              v-if="primaryBorderStyle && activeItem"
              class="preview-media-border primary-border"
              :style="[primaryWindowRect, primaryBorderStyle]"
              aria-hidden="true"
            ></div>
            <template v-if="!isFullBleedLayout">
              <div
                v-if="layoutVideoRect.y > 0.02"
                class="layout-band top"
                :style="{ height: `${layoutVideoRect.y * 100}%` }"
              >
                <span v-if="canvasLayout.top_text || canvasLayout.brand_text">{{ canvasLayout.top_text || canvasLayout.brand_text }}</span>
              </div>
              <div
                v-if="layoutVideoRect.y + layoutVideoRect.h < 0.98"
                class="layout-band bottom"
                :style="{ height: `${(1 - layoutVideoRect.y - layoutVideoRect.h) * 100}%` }"
              >
                <span v-if="canvasLayout.bottom_text || (!canvasLayout.top_text && canvasLayout.brand_text)">{{ canvasLayout.bottom_text || canvasLayout.brand_text }}</span>
              </div>
              <i
                v-if="canvasLayout.accent_bar && layoutVideoRect.y > 0"
                class="layout-accent top"
                :style="{ top: `calc(${layoutVideoRect.y * 100}% - 2px)`, background: canvasLayout.accent_color }"
              ></i>
              <i
                v-if="canvasLayout.accent_bar && layoutVideoRect.y + layoutVideoRect.h < 1"
                class="layout-accent bottom"
                :style="{ top: `${(layoutVideoRect.y + layoutVideoRect.h) * 100}%`, background: canvasLayout.accent_color }"
              ></i>
            </template>
            <template v-for="overlay in videoOverlays" :key="overlay.id">
              <video
                v-if="playheadMs >= overlay.start_ms && playheadMs < overlay.start_ms + overlay.duration_ms && resolveSource(overlay.source)?.mime_type.startsWith('video')"
                :ref="(el) => setVideoOverlayRef(overlay.id, el as HTMLVideoElement | null)"
                class="preview-video-overlay"
                :class="[videoOverlayGeometryFor(overlay).position, { 'is-full-frame': videoOverlayGeometryFor(overlay).position === 'full-frame', 'is-selected': isSelected('video', overlay.id) }]"
                :style="videoOverlayPreviewStyle(overlay)"
                :src="resolveSource(overlay.source)!.url"
                :muted="isMuted || Boolean(overlay.mute_audio) || (overlay.volume ?? 1) <= 0"
                playsinline
                preload="auto"
                @loadedmetadata="syncVideoOverlays"
              ></video>
              <div
                v-if="overlayBorderRects[overlay.id] && playheadMs >= overlay.start_ms && playheadMs < overlay.start_ms + overlay.duration_ms"
                class="preview-media-border overlay-border"
                :style="[overlayBorderRects[overlay.id]!, mediaBorderStyle(overlay.variant[variant]) ?? {}, { opacity: overlay.opacity ?? 1 }]"
                aria-hidden="true"
              ></div>
            </template>
            <template v-for="overlay in timeline.document.image_overlays" :key="overlay.id">
              <img
                v-if="playheadMs >= overlay.start_ms && playheadMs < overlay.start_ms + overlay.duration_ms && resolveSource(overlay.source)"
                class="preview-image-overlay"
                :class="[geometryFor(overlay).position, { 'is-full-frame': geometryFor(overlay).position === 'full-frame' }]"
                :style="imageOverlayPreviewStyle(overlay)"
                :src="resolveSource(overlay.source)!.url"
                alt=""
              />
            </template>
            <template v-for="overlay in timeline.document.text_overlays" :key="overlay.id">
              <div v-if="playheadMs >= overlay.start_ms && playheadMs < overlay.start_ms + overlay.duration_ms" class="preview-text-overlay" :class="[geometryFor(overlay).position, overlay.presentation, overlay.background, overlay.size, overlay.color]">{{ overlay.text }}</div>
            </template>
            <span class="source-badge">{{ activeBadge }}</span>
            <div v-if="variant === 'shorts'" class="shorts-safe-area" title="Shorts safe zone"></div>
            <div v-if="isDraggingPrimaryReframe && activeItem" class="reframe-hud-badge" role="status">
              <span>Pan X</span>
              <strong>{{ Math.round((geometryFor(activeItem).focal_x ?? 0.5) * 100) }}%</strong>
            </div>
          </section>
          <div class="preview-controls">
            <div class="transport-cluster">
              <button type="button" class="transport-btn" title="Previous clip" aria-label="Previous clip" :disabled="!primary.length" @click="jumpClip(-1)">
                <SkipBack :size="14" />
              </button>
              <button type="button" class="transport-btn" title="Step back 50ms (←)" aria-label="Step backward" @click="stepFrame(-1)">
                <ChevronLeft :size="15" />
              </button>
              <button type="button" class="transport-btn play-btn" :title="playing ? 'Pause timeline (Space)' : 'Play timeline (Space)'" :aria-label="playing ? 'Pause timeline' : 'Play timeline'" @click="togglePlayback">
                <Pause v-if="playing" :size="16" />
                <Play v-else :size="16" />
              </button>
              <button type="button" class="transport-btn" title="Step forward 50ms (→)" aria-label="Step forward" @click="stepFrame(1)">
                <ChevronRight :size="15" />
              </button>
              <button type="button" class="transport-btn" title="Next clip" aria-label="Next clip" :disabled="!primary.length" @click="jumpClip(1)">
                <SkipForward :size="14" />
              </button>
            </div>
            <div class="transport-secondary">
              <button
                type="button"
                class="transport-btn"
                title="Add the frame at the playhead to the release kit"
                aria-label="Add frame to release kit"
                :disabled="capturingFrame || !activeItem"
                @click="captureReleaseFrame"
              >
                <LoaderCircle v-if="capturingFrame" class="spin" :size="14" />
                <Camera v-else :size="14" />
              </button>
              <button type="button" class="transport-btn" :class="{ active: loopPlayback }" title="Loop playback" aria-label="Toggle loop playback" @click="loopPlayback = !loopPlayback">
                <Repeat :size="14" />
              </button>
              <button type="button" class="transport-btn" :class="{ muted: isMuted }" :title="isMuted ? 'Unmute preview audio' : 'Mute preview audio'" :aria-label="isMuted ? 'Unmute preview audio' : 'Mute preview audio'" @click="toggleMute">
                <VolumeX v-if="isMuted" :size="15" />
                <Volume2 v-else :size="15" />
              </button>
            </div>
            <output class="preview-time" aria-label="Playback position">
              <strong>{{ formatTimecode(playheadMs) }}</strong>
              <span>/ {{ formatTimecode(totalMs) }}</span>
            </output>
            <span v-if="activeItem" class="preview-active-clip" :title="activeItem.label">
              <em>#{{ activeIndex + 1 }}</em> {{ activeItem.label }}
            </span>
            <span v-if="frameNotice" class="preview-frame-notice" role="status">{{ frameNotice }} · <button type="button" @click="openReleaseKit()">Open kit</button></span>
            <span v-else class="preview-shortcut">{{ isReframingActivePrimary ? 'Drag preview to reframe · Space play' : 'Space play · ←/→ step · S split' }}</span>
          </div>
        </div>
        <aside class="timeline-inspector" @focusin="captureInspectorSnapshot" @pointerdown="captureInspectorSnapshot">
          <header class="inspector-header">
            <span class="inspector-eyebrow">Inspector · {{ isMultiSelect ? 'MULTI-SELECTION' : selectedKind.toUpperCase() }}</span>
            <strong>{{ isMultiSelect ? `${selectedEntries.length} items selected` : (selectedPrimary?.label || selectedVideo?.label || selectedText?.text || selectedAudio?.label || (selectedImage ? 'Image overlay' : 'Nothing selected')) }}</strong>
          </header>

          <details ref="layoutSectionEl" class="inspector-section layout-section">
            <summary class="inspector-section-summary layout-section-summary">
              <span>{{ variant === 'shorts' ? 'Shorts layout' : 'Landscape layout' }}</span>
              <span class="layout-summary-chip" :title="activeLayoutPreset?.blurb || ''">
                <span class="layout-summary-thumb" :class="variant" aria-hidden="true">
                  <span
                    class="layout-summary-video"
                    :style="{
                      left: `${(activeLayoutPreset?.video.x ?? 0) * 100}%`,
                      top: `${(activeLayoutPreset?.video.y ?? 0) * 100}%`,
                      width: `${(activeLayoutPreset?.video.w ?? 1) * 100}%`,
                      height: `${(activeLayoutPreset?.video.h ?? 1) * 100}%`,
                    }"
                  ></span>
                </span>
                {{ activeLayoutPreset?.label || 'Full bleed' }}
              </span>
            </summary>
            <div class="framing-card layout-template-card">
              <p class="layout-card-blurb">{{ activeLayoutPreset?.blurb || 'Pick a canvas template — empty bands are for hooks, captions, and branding.' }}</p>
              <div class="layout-template-grid" role="listbox" :aria-label="`${variant} layout templates`">
                <button
                  v-for="preset in layoutPresets"
                  :key="preset.id"
                  type="button"
                  class="layout-template-thumb"
                  :class="{ active: canvasLayout.template === preset.id }"
                  :title="preset.blurb"
                  role="option"
                  :aria-selected="canvasLayout.template === preset.id"
                  @click="applyLayoutTemplate(preset)"
                >
                  <span class="layout-thumb-canvas" :class="variant">
                    <i
                      class="layout-thumb-video"
                      :style="{
                        left: `${preset.video.x * 100}%`,
                        top: `${preset.video.y * 100}%`,
                        width: `${preset.video.w * 100}%`,
                        height: `${preset.video.h * 100}%`,
                      }"
                    ></i>
                  </span>
                  <em>{{ preset.label }}</em>
                </button>
              </div>
              <div class="layout-fit-global">
                <div class="layout-fit-global-header">
                  <span class="quick-dur-label">Clips framing</span>
                  <span class="layout-fit-status-badge" :class="allClipsFitState">
                    {{ allClipsFitState === 'contain' ? 'All Boxed' : allClipsFitState === 'cover' ? 'All Fill' : 'Mixed' }}
                  </span>
                </div>
                <div class="framing-fit-segmented mini">
                  <button
                    type="button"
                    class="framing-fit-btn"
                    :class="{ active: allClipsFitState === 'cover' }"
                    title="Crop all primary clips to fill the portrait frame"
                    @click="applyFitToAllClips('cover')"
                  >
                    <span class="fit-btn-glyph fill"><i></i></span>
                    <span>Fill all (Crop)</span>
                  </button>
                  <button
                    type="button"
                    class="framing-fit-btn"
                    :class="{ active: allClipsFitState === 'contain' }"
                    title="Box all primary clips so the entire 16:9 video is visible in the center"
                    @click="applyFitToAllClips('contain')"
                  >
                    <span class="fit-btn-glyph boxed"><i></i></span>
                    <span>Box all (Letterbox)</span>
                  </button>
                </div>
              </div>
              <template v-if="!isFullBleedLayout">
                <label class="layout-field">
                  Band fill
                  <select :value="canvasLayout.matte" @change="updateCanvasLayout('matte', ($event.target as HTMLSelectElement).value as TimelineCanvasLayout['matte'])">
                    <option value="solid">Solid color</option>
                    <option value="gradient">Gradient wash</option>
                    <option value="blur">Blurred video bed</option>
                    <option value="duotone">Duotone glow</option>
                  </select>
                </label>
                <div class="layout-color-row">
                  <label>Matte A<input type="color" :value="canvasLayout.matte_color" @input="updateCanvasLayout('matte_color', ($event.target as HTMLInputElement).value)" /></label>
                  <label>Matte B<input type="color" :value="canvasLayout.matte_color_b" @input="updateCanvasLayout('matte_color_b', ($event.target as HTMLInputElement).value)" /></label>
                  <label>Accent<input type="color" :value="canvasLayout.accent_color" @input="updateCanvasLayout('accent_color', ($event.target as HTMLInputElement).value)" /></label>
                </div>
                <label class="layout-field">
                  Top band text
                  <input
                    type="text"
                    :value="canvasLayout.top_text"
                    maxlength="280"
                    placeholder="Hook / title…"
                    @change="updateCanvasLayout('top_text', ($event.target as HTMLInputElement).value)"
                  />
                </label>
                <label class="layout-field">
                  Bottom band text
                  <input
                    type="text"
                    :value="canvasLayout.bottom_text"
                    maxlength="280"
                    placeholder="Caption / CTA…"
                    @change="updateCanvasLayout('bottom_text', ($event.target as HTMLInputElement).value)"
                  />
                </label>
                <label class="layout-field">
                  Brand lockup
                  <input
                    type="text"
                    :value="canvasLayout.brand_text"
                    maxlength="80"
                    placeholder="Channel / series name…"
                    @change="updateCanvasLayout('brand_text', ($event.target as HTMLInputElement).value)"
                  />
                </label>
                <label class="layout-check">
                  <input
                    type="checkbox"
                    :checked="canvasLayout.accent_bar"
                    @change="updateCanvasLayout('accent_bar', ($event.target as HTMLInputElement).checked)"
                  />
                  Neon seam bars
                </label>
              </template>
            </div>
          </details>

          <details class="inspector-section transition-section">
            <summary class="inspector-section-summary">
              <span>Transitions</span>
              <span class="layout-summary-chip">{{ defaultTransition ? `${transitionLabel(defaultTransition.kind)} · ${transitionDuration('default', defaultTransition.duration_ms).toFixed(1)}s` : 'No transition' }}</span>
            </summary>
            <div class="framing-card transition-card">
              <label class="layout-field">
                Default between shots
                <select
                  aria-label="Default transition"
                  :value="defaultTransition?.kind ?? 'cut'"
                  @change="setDefaultTransition(($event.target as HTMLSelectElement).value as TimelineTransitionKind)"
                >
                  <option v-for="option in TRANSITION_OPTIONS" :key="option.kind" :value="option.kind">{{ option.label }}</option>
                </select>
              </label>
              <label v-if="defaultTransition" class="layout-field">
                Length · {{ transitionDuration('default', defaultTransition.duration_ms).toFixed(1) }}s
                <input
                  aria-label="Default transition length"
                  type="range"
                  min="0.1"
                  max="2"
                  step="0.1"
                  :value="transitionDuration('default', defaultTransition.duration_ms)"
                  @input="transitionDurationDrafts.default = Number(($event.target as HTMLInputElement).value)"
                  @change="setDefaultTransition(defaultTransition.kind, Math.round(Number(($event.target as HTMLInputElement).value) * 1000))"
                />
              </label>
              <p class="layout-card-blurb">Click a ◆ between shots on the PRIMARY track to override one cut.</p>
            </div>
          </details>

          <template v-if="isMultiSelect">
            <div class="multi-select-inspector">
              <div class="multi-select-summary-badge">{{ multiSelectSummary }}</div>
              <div class="multi-select-list">
                <div v-for="entry in selectedEntries" :key="entry.kind + '-' + entry.id" class="multi-select-item-chip">
                  <span class="chip-kind-badge">{{ entry.kind }}</span>
                  <span class="chip-name">{{ getItemLabel(entry) }}</span>
                  <button type="button" class="chip-remove" title="Remove from selection" @click="toggleSelectEntry(entry.kind, entry.id)">×</button>
                </div>
              </div>
              <div class="multi-select-actions">
                <button
                  type="button"
                  class="inspector-action-btn primary-action-btn"
                  :disabled="!canSplitAtPlayhead"
                  @click="splitAtPlayhead"
                  title="Split all intersecting selected clips at playhead (S / C)"
                >
                  <Split :size="13" />
                  <span>Split at playhead{{ eligibleSplitCount > 0 ? ` (${eligibleSplitCount})` : '' }}</span>
                </button>
                <button
                  type="button"
                  class="inspector-action-btn danger-action-btn"
                  @click="deleteSelection"
                  title="Delete all selected items (Delete / Backspace)"
                >
                  <Trash2 :size="13" />
                  <span>Delete selected ({{ selectedEntries.length }})</span>
                </button>
                <button
                  type="button"
                  class="inspector-action-btn"
                  @click="clearMultiSelection"
                  title="Clear multi-selection"
                >
                  <X :size="13" />
                  <span>Deselect all</span>
                </button>
              </div>
            </div>
          </template>
          <template v-else-if="selectedPrimary">
            <label>Name<input v-model="selectedPrimary.label" @change="commitInspectorEdit" /></label>
            <label v-if="selectedPrimary.kind === 'title'">Text<textarea v-model="selectedPrimary.text" rows="3" @change="commitInspectorEdit"></textarea></label>
            <label>Duration (seconds)<input type="number" min="0.1" step="0.1" :value="selectedPrimary.duration_ms / 1000" @change="resizeSelected" /></label>
            <div v-if="selectedPrimaryIndex > 0" class="transition-in-row">
              <label>Transition in
                <select
                  aria-label="Transition in"
                  :value="selectedPrimary.transition?.kind ?? 'default'"
                  @change="onTransitionKindChange(selectedPrimaryIndex, ($event.target as HTMLSelectElement).value)"
                >
                  <option value="default">Use default — {{ transitionLabel(defaultTransition?.kind ?? 'cut') }}</option>
                  <option v-for="option in TRANSITION_OPTIONS" :key="option.kind" :value="option.kind">{{ option.label }}</option>
                </select>
              </label>
              <label v-if="selectedPrimary.transition && selectedPrimary.transition.kind !== 'cut'">Length · {{ transitionDuration(selectedPrimaryIndex, selectedPrimary.transition.duration_ms).toFixed(1) }}s
                <input type="range" min="0.1" max="2" step="0.1" :value="transitionDuration(selectedPrimaryIndex, selectedPrimary.transition.duration_ms)" @input="transitionDurationDrafts[selectedPrimaryIndex] = Number(($event.target as HTMLInputElement).value)" @change="onTransitionDurationChange(selectedPrimaryIndex, Number(($event.target as HTMLInputElement).value))" />
              </label>
            </div>
            <template v-if="selectedPrimary.kind === 'video'">
              <div class="trim-heading">
                <span><Scissors :size="13" />Trim clip</span>
                <button type="button" @click="resetTrim">Reset</button>
              </div>
              <div class="trim-range-visualizer" aria-hidden="true">
                <div class="trim-range-track">
                  <div class="trim-range-fill" :style="trimRangeStyle">
                    <span class="trim-edge-cap start"></span>
                    <span class="trim-edge-cap end"></span>
                  </div>
                </div>
                <div class="trim-range-meta">
                  <span>In {{ (selectedPrimary.source_in_ms / 1000).toFixed(2) }}s</span>
                  <strong>{{ (selectedPrimary.duration_ms / 1000).toFixed(2) }}s active</strong>
                  <span>Out {{ (selectedTrimOutMs / 1000).toFixed(2) }}s</span>
                </div>
              </div>
              <label>In · {{ (selectedPrimary.source_in_ms / 1000).toFixed(2) }}s<input type="range" min="0" :max="trimMaximumMs" step="50" :value="selectedPrimary.source_in_ms" @input="changeTrimIn" /></label>
              <label>Out · {{ (selectedTrimOutMs / 1000).toFixed(2) }}s<input type="range" min="100" :max="trimMaximumMs" step="50" :value="selectedTrimOutMs" @input="changeTrimOut" /></label>
              <div class="trim-actions"><button type="button" title="Set In point at playhead (I)" @click="setTrimAtPlayhead('in')">Set in (I)</button><button type="button" title="Set Out point at playhead (O)" @click="setTrimAtPlayhead('out')">Set out (O)</button></div>
              <StemSeparationControls v-if="stemTarget" v-model:provider="stemProvider" v-model:include-voice="stemIncludeVoice" :separators="stemSeparators" :busy="stemRun.busy" :status="stemRun.status" :error="stemRun.error" @separate="separateMusic" />
              <SaveToLibraryControl v-if="stemTarget" :default-label="stemTarget.label" :busy="librarySave.busy" :status="librarySave.status" :error="librarySave.error" @save="saveToLibrary" />

              <button type="button" class="inspector-action-btn" @click="duplicateSelected" title="Duplicate this clip so it plays twice (Ctrl+D)">
                <CopyPlus :size="13" />Duplicate clip (play twice)
              </button>

              <div class="inspector-audio-row">
                <span class="inspector-label"><Volume2 :size="13" />Audio</span>
                <button
                  type="button"
                  class="audio-toggle-action"
                  :class="{ muted: selectedPrimary.mute_audio }"
                  @click="toggleSelectedAudio"
                  :title="selectedPrimary.mute_audio ? 'Unmute clip audio' : 'Remove/mute audio from this clip'"
                >
                  <VolumeX v-if="selectedPrimary.mute_audio" :size="13" />
                  <Volume2 v-else :size="13" />
                  <span>{{ selectedPrimary.mute_audio ? 'Video only (Muted)' : 'Audio active' }}</span>
                </button>
              </div>
              <button
                type="button"
                class="inspector-action-btn"
                title="Extract this clip's audio onto the AUDIO track and mute the video clip"
                @click="detachSelectedVideoAudio"
              >
                <Music :size="13" />Detach audio to AUDIO track
              </button>
            </template>
            <details class="inspector-section framing-section" open>
              <summary class="inspector-section-summary">
                <span>Framing, zoom &amp; crop</span>
                <button
                  v-if="hasCropOrZoom(geometryFor(selectedPrimary))"
                  type="button"
                  class="framing-inline-reset"
                  title="Reset crop & zoom"
                  @click.stop.prevent="resetGeometryCropAndZoom(geometryFor(selectedPrimary))"
                >
                  Reset
                </button>
              </summary>
              <div class="framing-panel">
                <div class="framing-fit-control">
                  <div class="framing-fit-header">
                    <span class="framing-fit-title">Framing</span>
                    <button
                      v-if="timeline?.document?.primary && timeline.document.primary.length > 1"
                      type="button"
                      class="framing-apply-all-btn"
                      :title="`Apply '${(geometryFor(selectedPrimary).fit || 'cover') === 'contain' ? 'Boxed' : 'Fill frame'}' to all clips in this Shorts timeline`"
                      @click="applyFitToAllClips(geometryFor(selectedPrimary).fit || 'cover')"
                    >
                      Apply to all clips
                    </button>
                  </div>
                  <div class="framing-fit-segmented">
                    <button
                      type="button"
                      class="framing-fit-btn"
                      :class="{ active: (geometryFor(selectedPrimary).fit || 'cover') === 'cover' }"
                      title="Fill frame: crops widescreen video to fill 9:16 portrait"
                      @click="setPrimaryFit('cover')"
                    >
                      <span class="fit-btn-glyph fill"><i></i></span>
                      <span>Fill frame (Crop)</span>
                    </button>
                    <button
                      type="button"
                      class="framing-fit-btn"
                      :class="{ active: geometryFor(selectedPrimary).fit === 'contain' }"
                      title="Boxed / Letterbox: shows the full 16:9 video centered with space top and bottom"
                      @click="setPrimaryFit('contain')"
                    >
                      <span class="fit-btn-glyph boxed"><i></i></span>
                      <span>Boxed (Letterbox)</span>
                    </button>
                  </div>
                  <div class="framing-fit-subtext">
                    <span v-if="(geometryFor(selectedPrimary).fit || 'cover') === 'contain'">
                      Full 16:9 widescreen video centered with top &amp; bottom space.
                    </span>
                    <span v-else>
                      Cropped to fill 9:16 frame. Drag preview to reframe focal point.
                    </span>
                  </div>
                </div>

                <div class="framing-card position-card">
                  <div class="framing-card-header">
                    <span class="quick-dur-label">Position</span>
                    <span class="framing-crop-readout">
                      X {{ formatSignedPercent(geometryFor(selectedPrimary).offset_x) }} · Y {{ formatSignedPercent(geometryFor(selectedPrimary).offset_y) }}
                    </span>
                  </div>
                  <div class="position-body">
                    <div class="nudge-pad" role="group" aria-label="Shift video position">
                      <button type="button" class="nudge-btn nudge-up" title="Shift up" aria-label="Shift up" @click="nudgeGeometryOffset(geometryFor(selectedPrimary), 0, -0.02)"><ChevronUp :size="13" /></button>
                      <button type="button" class="nudge-btn nudge-left" title="Shift left" aria-label="Shift left" @click="nudgeGeometryOffset(geometryFor(selectedPrimary), -0.02, 0)"><ChevronLeft :size="13" /></button>
                      <button type="button" class="nudge-btn nudge-center" title="Re-center position" aria-label="Re-center position" @click="centerGeometryOffset(geometryFor(selectedPrimary))"><Crosshair :size="12" /></button>
                      <button type="button" class="nudge-btn nudge-right" title="Shift right" aria-label="Shift right" @click="nudgeGeometryOffset(geometryFor(selectedPrimary), 0.02, 0)"><ChevronRight :size="13" /></button>
                      <button type="button" class="nudge-btn nudge-down" title="Shift down" aria-label="Shift down" @click="nudgeGeometryOffset(geometryFor(selectedPrimary), 0, 0.02)"><ChevronDown :size="13" /></button>
                    </div>
                    <div class="position-sliders">
                      <label class="framing-slider-field bipolar">
                        <span class="framing-field-head"><span>X · left ↔ right</span><b>{{ formatSignedPercent(geometryFor(selectedPrimary).offset_x) }}</b></span>
                        <input
                          v-model.number="geometryFor(selectedPrimary).offset_x"
                          type="range"
                          min="-1"
                          max="1"
                          step="0.01"
                          aria-label="Horizontal position"
                          :style="framingRangeStyle(geometryFor(selectedPrimary).offset_x, -1, 1, 'center')"
                          @change="commitInspectorEdit"
                        />
                      </label>
                      <label class="framing-slider-field bipolar">
                        <span class="framing-field-head"><span>Y · up ↕ down</span><b>{{ formatSignedPercent(geometryFor(selectedPrimary).offset_y) }}</b></span>
                        <input
                          v-model.number="geometryFor(selectedPrimary).offset_y"
                          type="range"
                          min="-1"
                          max="1"
                          step="0.01"
                          aria-label="Vertical position"
                          :style="framingRangeStyle(geometryFor(selectedPrimary).offset_y, -1, 1, 'center')"
                          @change="commitInspectorEdit"
                        />
                      </label>
                    </div>
                  </div>
                </div>

                <div class="framing-card">
                  <label class="framing-slider-field">
                    <span class="framing-field-head">
                      <span>Zoom</span>
                      <b>{{ (geometryFor(selectedPrimary).zoom ?? 1).toFixed(2) }}×</b>
                    </span>
                    <input
                      v-model.number="geometryFor(selectedPrimary).zoom"
                      type="range"
                      min="1"
                      max="3"
                      step="0.05"
                      aria-label="Zoom"
                      :style="framingRangeStyle(geometryFor(selectedPrimary).zoom, 1, 3)"
                      @change="commitInspectorEdit"
                    />
                  </label>
                  <div class="framing-grid-2col">
                    <label class="framing-slider-field bipolar">
                      <span class="framing-field-head">
                        <span>Focus X (Pan)</span>
                        <b>{{ Math.round((geometryFor(selectedPrimary).focal_x ?? 0.5) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="geometryFor(selectedPrimary).focal_x"
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        aria-label="Focus X"
                        :style="framingRangeStyle(geometryFor(selectedPrimary).focal_x ?? 0.5, 0, 1, 'center')"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field bipolar">
                      <span class="framing-field-head">
                        <span>Focus Y</span>
                        <b>{{ Math.round((geometryFor(selectedPrimary).focal_y ?? 0.5) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="geometryFor(selectedPrimary).focal_y"
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        aria-label="Focus Y"
                        :style="framingRangeStyle(geometryFor(selectedPrimary).focal_y ?? 0.5, 0, 1, 'center')"
                        @change="commitInspectorEdit"
                      />
                    </label>
                  </div>
                  <div class="quick-dur-buttons focal-preset-grid">
                    <button type="button" class="quick-dur-btn focal-preset-btn" :class="{ active: Math.abs((geometryFor(selectedPrimary).focal_x ?? 0.5) - 0.15) < 0.05 }" @click="applyFocalPreset(geometryFor(selectedPrimary), 0.15)"><span>Left</span></button>
                    <button type="button" class="quick-dur-btn focal-preset-btn" :class="{ active: Math.abs((geometryFor(selectedPrimary).focal_x ?? 0.5) - 0.5) < 0.05 }" @click="applyFocalPreset(geometryFor(selectedPrimary), 0.5)"><span>Center</span></button>
                    <button type="button" class="quick-dur-btn focal-preset-btn" :class="{ active: Math.abs((geometryFor(selectedPrimary).focal_x ?? 0.5) - 0.85) < 0.05 }" @click="applyFocalPreset(geometryFor(selectedPrimary), 0.85)"><span>Right</span></button>
                  </div>
                </div>

                <div class="framing-card">
                  <div class="framing-card-header">
                    <span class="quick-dur-label">Crop region</span>
                    <span class="framing-crop-readout">
                      {{ Math.round(Math.max(10, (1 - (geometryFor(selectedPrimary).crop_left ?? 0) - (geometryFor(selectedPrimary).crop_right ?? 0)) * 100)) }}% × {{ Math.round(Math.max(10, (1 - (geometryFor(selectedPrimary).crop_top ?? 0) - (geometryFor(selectedPrimary).crop_bottom ?? 0)) * 100)) }}%
                    </span>
                  </div>
                  <div class="quick-dur-buttons crop-preset-grid">
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(geometryFor(selectedPrimary), 'full') }" @click="applyCropPreset(geometryFor(selectedPrimary), 'full')"><span class="crop-glyph full"><i></i></span><span>Full</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(geometryFor(selectedPrimary), 'center-box') }" @click="applyCropPreset(geometryFor(selectedPrimary), 'center-box')"><span class="crop-glyph center"><i></i></span><span>Center</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(geometryFor(selectedPrimary), 'left-half') }" @click="applyCropPreset(geometryFor(selectedPrimary), 'left-half')"><span class="crop-glyph left"><i></i></span><span>Left ½</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(geometryFor(selectedPrimary), 'right-half') }" @click="applyCropPreset(geometryFor(selectedPrimary), 'right-half')"><span class="crop-glyph right"><i></i></span><span>Right ½</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(geometryFor(selectedPrimary), 'top-half') }" @click="applyCropPreset(geometryFor(selectedPrimary), 'top-half')"><span class="crop-glyph top"><i></i></span><span>Top ½</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(geometryFor(selectedPrimary), 'bottom-half') }" @click="applyCropPreset(geometryFor(selectedPrimary), 'bottom-half')"><span class="crop-glyph bottom"><i></i></span><span>Bottom ½</span></button>
                  </div>
                  <div class="crop-edges-grid">
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Top</span>
                        <b>{{ Math.round((geometryFor(selectedPrimary).crop_top ?? 0) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="geometryFor(selectedPrimary).crop_top"
                        type="range"
                        min="0"
                        max="0.85"
                        step="0.01"
                        aria-label="Crop top"
                        :style="framingRangeStyle(geometryFor(selectedPrimary).crop_top, 0, 0.85)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Bottom</span>
                        <b>{{ Math.round((geometryFor(selectedPrimary).crop_bottom ?? 0) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="geometryFor(selectedPrimary).crop_bottom"
                        type="range"
                        min="0"
                        max="0.85"
                        step="0.01"
                        aria-label="Crop bottom"
                        :style="framingRangeStyle(geometryFor(selectedPrimary).crop_bottom, 0, 0.85)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Left</span>
                        <b>{{ Math.round((geometryFor(selectedPrimary).crop_left ?? 0) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="geometryFor(selectedPrimary).crop_left"
                        type="range"
                        min="0"
                        max="0.85"
                        step="0.01"
                        aria-label="Crop left"
                        :style="framingRangeStyle(geometryFor(selectedPrimary).crop_left, 0, 0.85)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Right</span>
                        <b>{{ Math.round((geometryFor(selectedPrimary).crop_right ?? 0) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="geometryFor(selectedPrimary).crop_right"
                        type="range"
                        min="0"
                        max="0.85"
                        step="0.01"
                        aria-label="Crop right"
                        :style="framingRangeStyle(geometryFor(selectedPrimary).crop_right, 0, 0.85)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                  </div>
                </div>

                <button
                  type="button"
                  class="framing-reset-btn"
                  :disabled="!hasCropOrZoom(geometryFor(selectedPrimary))"
                  @click="resetGeometryCropAndZoom(geometryFor(selectedPrimary))"
                >
                  Reset crop &amp; zoom
                </button>
              </div>
            </details>
            <details class="inspector-section lut-section" open>
              <summary class="inspector-section-summary">
                <span class="inspector-section-title"><Palette :size="13" />Color grade &amp; LUT</span>
                <button
                  v-if="geometryFor(selectedPrimary).lut && geometryFor(selectedPrimary).lut !== 'none'"
                  type="button"
                  class="framing-inline-reset"
                  title="Reset color grade"
                  @click.stop.prevent="resetLut(geometryFor(selectedPrimary))"
                >
                  Reset
                </button>
              </summary>
              <div class="lut-panel">
                <LutPicker
                  :lut="geometryFor(selectedPrimary).lut || 'none'"
                  :strength="geometryFor(selectedPrimary).lut_strength ?? 1"
                  :presets="lutPresets"
                  @update:lut="(id) => assignLut(geometryFor(selectedPrimary!), id)"
                  @update:strength="(s) => assignLutStrength(geometryFor(selectedPrimary!), s)"
                  @change="commitInspectorEdit"
                />
              </div>
            </details>
            <details class="inspector-section frame-section" open>
              <summary class="inspector-section-summary">
                <span class="inspector-section-title"><Frame :size="13" />Border &amp; corners</span>
                <button
                  v-if="hasFrameDecoration(geometryFor(selectedPrimary))"
                  type="button"
                  class="framing-inline-reset"
                  title="Remove border and rounded corners"
                  @click.stop.prevent="resetFrame(geometryFor(selectedPrimary))"
                >
                  Reset
                </button>
              </summary>
              <FrameStylePicker
                :geometry="geometryFor(selectedPrimary)"
                @update="(patch) => assignFrame(geometryFor(selectedPrimary!), patch)"
                @change="commitInspectorEdit"
              />
            </details>
          </template>
          <template v-else-if="selectedVideo">
            <div class="inspector-layer-order">
              <span class="quick-dur-label">Layer {{ selectedVideoIndex + 1 }} of {{ videoOverlays.length }}</span>
              <button type="button" class="quick-dur-btn" title="Add another secondary video layer onto the VIDEO track" @click="openAddVideoOverlayModal">
                + Add video layer
              </button>
            </div>
            <div
              v-if="playheadMs < selectedVideo.start_ms || playheadMs >= selectedVideo.start_ms + selectedVideo.duration_ms"
              class="inspector-outside-notice"
            >
              <span>Playhead is outside layer window ({{ (selectedVideo.start_ms / 1000).toFixed(1) }}s – {{ ((selectedVideo.start_ms + selectedVideo.duration_ms) / 1000).toFixed(1) }}s)</span>
              <button
                type="button"
                class="jump-to-layer-btn"
                @click="seekToOverlay(selectedVideo)"
              >
                Jump playhead to layer
              </button>
            </div>
            <label>Label<input v-model="selectedVideo.label" @change="commitInspectorEdit" /></label>
            <label>Start (ms)<input v-model.number="selectedVideo.start_ms" type="number" min="0" step="100" @change="commitInspectorEdit" /></label>
            <label>Duration (ms)<input v-model.number="selectedVideo.duration_ms" type="number" min="100" step="100" @change="commitInspectorEdit" /></label>
            <div class="inspector-quick-durations">
              <span class="quick-dur-label">Quick duration:</span>
              <div class="quick-dur-buttons">
                <button type="button" class="quick-dur-btn" @click="setVideoOverlayDuration(3000)">3s</button>
                <button type="button" class="quick-dur-btn" @click="setVideoOverlayDuration(5000)">5s</button>
                <button type="button" class="quick-dur-btn" @click="setVideoOverlayDuration(10000)">10s</button>
              </div>
            </div>
            <label>Trim in (ms)<input v-model.number="selectedVideo.source_in_ms" type="number" min="0" step="100" @change="commitInspectorEdit" /></label>
            <label>Layout / Scale
              <select v-model="videoOverlayGeometryFor(selectedVideo).position" @change="commitInspectorEdit">
                <option value="full-frame">Full frame (Same as primary)</option>
                <option value="center">Picture-in-Picture · Center</option>
                <option value="top-left">Picture-in-Picture · Top left</option>
                <option value="top-right">Picture-in-Picture · Top right</option>
                <option value="bottom-left">Picture-in-Picture · Bottom left</option>
                <option value="bottom-right">Picture-in-Picture · Bottom right</option>
                <option value="top">Picture-in-Picture · Top center</option>
                <option value="bottom">Picture-in-Picture · Bottom center</option>
              </select>
            </label>
            <div v-if="videoOverlayGeometryFor(selectedVideo).position === 'full-frame'" class="framing-fit-control">
              <div class="framing-fit-header">
                <span class="framing-fit-title">Framing</span>
              </div>
              <div class="framing-fit-segmented">
                <button
                  type="button"
                  class="framing-fit-btn"
                  :class="{ active: (videoOverlayGeometryFor(selectedVideo).fit || 'cover') === 'cover' }"
                  title="Fill frame: crops layer to fill 9:16 portrait"
                  @click="setVideoOverlayFit('cover')"
                >
                  <span class="fit-btn-glyph fill"><i></i></span>
                  <span>Fill frame (Crop)</span>
                </button>
                <button
                  type="button"
                  class="framing-fit-btn"
                  :class="{ active: videoOverlayGeometryFor(selectedVideo).fit === 'contain' }"
                  title="Boxed / Letterbox: shows full layer centered with space top & bottom"
                  @click="setVideoOverlayFit('contain')"
                >
                  <span class="fit-btn-glyph boxed"><i></i></span>
                  <span>Boxed (Letterbox)</span>
                </button>
              </div>
            </div>
            <label v-else class="framing-slider-field">
              <span class="framing-field-head">
                <span>Scale</span>
                <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).scale ?? 1) * 100) }}%</b>
              </span>
              <input v-model.number="videoOverlayGeometryFor(selectedVideo).scale" type="range" min="0.1" max="2" step="0.05" @change="commitInspectorEdit" />
              <small class="inspector-help">Scale changes the layer's visible size. Zoom below changes the crop inside the layer.</small>
            </label>
            <details class="inspector-section framing-section" open>
              <summary class="inspector-section-summary">
                <span>Framing, zoom &amp; crop</span>
                <button
                  v-if="hasCropOrZoom(videoOverlayGeometryFor(selectedVideo))"
                  type="button"
                  class="framing-inline-reset"
                  title="Reset crop & zoom"
                  @click.stop.prevent="resetGeometryCropAndZoom(videoOverlayGeometryFor(selectedVideo))"
                >
                  Reset
                </button>
              </summary>
              <div class="framing-panel">
                <div class="framing-card position-card">
                  <div class="framing-card-header">
                    <span class="quick-dur-label">Position</span>
                    <span class="framing-crop-readout">
                      X {{ formatSignedPercent(videoOverlayGeometryFor(selectedVideo).offset_x) }} · Y {{ formatSignedPercent(videoOverlayGeometryFor(selectedVideo).offset_y) }}
                    </span>
                  </div>
                  <div class="position-body">
                    <div class="nudge-pad" role="group" aria-label="Shift layer position">
                      <button type="button" class="nudge-btn nudge-up" title="Shift up" aria-label="Shift up" @click="nudgeGeometryOffset(videoOverlayGeometryFor(selectedVideo), 0, -0.02)"><ChevronUp :size="13" /></button>
                      <button type="button" class="nudge-btn nudge-left" title="Shift left" aria-label="Shift left" @click="nudgeGeometryOffset(videoOverlayGeometryFor(selectedVideo), -0.02, 0)"><ChevronLeft :size="13" /></button>
                      <button type="button" class="nudge-btn nudge-center" title="Re-center position" aria-label="Re-center position" @click="centerGeometryOffset(videoOverlayGeometryFor(selectedVideo))"><Crosshair :size="12" /></button>
                      <button type="button" class="nudge-btn nudge-right" title="Shift right" aria-label="Shift right" @click="nudgeGeometryOffset(videoOverlayGeometryFor(selectedVideo), 0.02, 0)"><ChevronRight :size="13" /></button>
                      <button type="button" class="nudge-btn nudge-down" title="Shift down" aria-label="Shift down" @click="nudgeGeometryOffset(videoOverlayGeometryFor(selectedVideo), 0, 0.02)"><ChevronDown :size="13" /></button>
                    </div>
                    <div class="position-sliders">
                      <label class="framing-slider-field bipolar">
                        <span class="framing-field-head"><span>X · left ↔ right</span><b>{{ formatSignedPercent(videoOverlayGeometryFor(selectedVideo).offset_x) }}</b></span>
                        <input
                          v-model.number="videoOverlayGeometryFor(selectedVideo).offset_x"
                          type="range"
                          min="-1"
                          max="1"
                          step="0.01"
                          aria-label="Horizontal position"
                          :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).offset_x, -1, 1, 'center')"
                          @change="commitInspectorEdit"
                        />
                      </label>
                      <label class="framing-slider-field bipolar">
                        <span class="framing-field-head"><span>Y · up ↕ down</span><b>{{ formatSignedPercent(videoOverlayGeometryFor(selectedVideo).offset_y) }}</b></span>
                        <input
                          v-model.number="videoOverlayGeometryFor(selectedVideo).offset_y"
                          type="range"
                          min="-1"
                          max="1"
                          step="0.01"
                          aria-label="Vertical position"
                          :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).offset_y, -1, 1, 'center')"
                          @change="commitInspectorEdit"
                        />
                      </label>
                    </div>
                  </div>
                </div>

                <div class="framing-card">
                  <label class="framing-slider-field">
                    <span class="framing-field-head">
                      <span>Zoom</span>
                      <b>{{ (videoOverlayGeometryFor(selectedVideo).zoom ?? 1).toFixed(2) }}×</b>
                    </span>
                    <input
                      v-model.number="videoOverlayGeometryFor(selectedVideo).zoom"
                      type="range"
                      min="1"
                      max="3"
                      step="0.05"
                      aria-label="Zoom"
                      :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).zoom, 1, 3)"
                      @change="commitInspectorEdit"
                    />
                  </label>
                  <div class="framing-grid-2col">
                    <label class="framing-slider-field bipolar">
                      <span class="framing-field-head">
                        <span>Focus X (Pan)</span>
                        <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).focal_x ?? 0.5) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="videoOverlayGeometryFor(selectedVideo).focal_x"
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        aria-label="Focus X"
                        :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).focal_x ?? 0.5, 0, 1, 'center')"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field bipolar">
                      <span class="framing-field-head">
                        <span>Focus Y</span>
                        <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).focal_y ?? 0.5) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="videoOverlayGeometryFor(selectedVideo).focal_y"
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        aria-label="Focus Y"
                        :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).focal_y ?? 0.5, 0, 1, 'center')"
                        @change="commitInspectorEdit"
                      />
                    </label>
                  </div>
                  <div class="quick-dur-buttons focal-preset-grid">
                    <button type="button" class="quick-dur-btn focal-preset-btn" :class="{ active: Math.abs((videoOverlayGeometryFor(selectedVideo).focal_x ?? 0.5) - 0.15) < 0.05 }" @click="applyFocalPreset(videoOverlayGeometryFor(selectedVideo), 0.15)"><span>Left</span></button>
                    <button type="button" class="quick-dur-btn focal-preset-btn" :class="{ active: Math.abs((videoOverlayGeometryFor(selectedVideo).focal_x ?? 0.5) - 0.5) < 0.05 }" @click="applyFocalPreset(videoOverlayGeometryFor(selectedVideo), 0.5)"><span>Center</span></button>
                    <button type="button" class="quick-dur-btn focal-preset-btn" :class="{ active: Math.abs((videoOverlayGeometryFor(selectedVideo).focal_x ?? 0.5) - 0.85) < 0.05 }" @click="applyFocalPreset(videoOverlayGeometryFor(selectedVideo), 0.85)"><span>Right</span></button>
                  </div>
                </div>

                <div class="framing-card">
                  <div class="framing-card-header">
                    <span class="quick-dur-label">Crop region</span>
                    <span class="framing-crop-readout">
                      {{ Math.round(Math.max(10, (1 - (videoOverlayGeometryFor(selectedVideo).crop_left ?? 0) - (videoOverlayGeometryFor(selectedVideo).crop_right ?? 0)) * 100)) }}% × {{ Math.round(Math.max(10, (1 - (videoOverlayGeometryFor(selectedVideo).crop_top ?? 0) - (videoOverlayGeometryFor(selectedVideo).crop_bottom ?? 0)) * 100)) }}%
                    </span>
                  </div>
                  <div class="quick-dur-buttons crop-preset-grid">
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(videoOverlayGeometryFor(selectedVideo), 'full') }" @click="applyCropPreset(videoOverlayGeometryFor(selectedVideo), 'full')"><span class="crop-glyph full"><i></i></span><span>Full</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(videoOverlayGeometryFor(selectedVideo), 'center-box') }" @click="applyCropPreset(videoOverlayGeometryFor(selectedVideo), 'center-box')"><span class="crop-glyph center"><i></i></span><span>Center</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(videoOverlayGeometryFor(selectedVideo), 'left-half') }" @click="applyCropPreset(videoOverlayGeometryFor(selectedVideo), 'left-half')"><span class="crop-glyph left"><i></i></span><span>Left ½</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(videoOverlayGeometryFor(selectedVideo), 'right-half') }" @click="applyCropPreset(videoOverlayGeometryFor(selectedVideo), 'right-half')"><span class="crop-glyph right"><i></i></span><span>Right ½</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(videoOverlayGeometryFor(selectedVideo), 'top-half') }" @click="applyCropPreset(videoOverlayGeometryFor(selectedVideo), 'top-half')"><span class="crop-glyph top"><i></i></span><span>Top ½</span></button>
                    <button type="button" class="quick-dur-btn crop-preset-btn" :class="{ active: isCropPresetActive(videoOverlayGeometryFor(selectedVideo), 'bottom-half') }" @click="applyCropPreset(videoOverlayGeometryFor(selectedVideo), 'bottom-half')"><span class="crop-glyph bottom"><i></i></span><span>Bottom ½</span></button>
                  </div>
                  <div class="crop-edges-grid">
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Top</span>
                        <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).crop_top ?? 0) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="videoOverlayGeometryFor(selectedVideo).crop_top"
                        type="range"
                        min="0"
                        max="0.85"
                        step="0.01"
                        aria-label="Crop top"
                        :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).crop_top, 0, 0.85)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Bottom</span>
                        <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).crop_bottom ?? 0) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="videoOverlayGeometryFor(selectedVideo).crop_bottom"
                        type="range"
                        min="0"
                        max="0.85"
                        step="0.01"
                        aria-label="Crop bottom"
                        :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).crop_bottom, 0, 0.85)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Left</span>
                        <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).crop_left ?? 0) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="videoOverlayGeometryFor(selectedVideo).crop_left"
                        type="range"
                        min="0"
                        max="0.85"
                        step="0.01"
                        aria-label="Crop left"
                        :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).crop_left, 0, 0.85)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Right</span>
                        <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).crop_right ?? 0) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="videoOverlayGeometryFor(selectedVideo).crop_right"
                        type="range"
                        min="0"
                        max="0.85"
                        step="0.01"
                        aria-label="Crop right"
                        :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).crop_right, 0, 0.85)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                  </div>
                </div>

                <button
                  type="button"
                  class="framing-reset-btn"
                  :disabled="!hasCropOrZoom(videoOverlayGeometryFor(selectedVideo))"
                  @click="resetGeometryCropAndZoom(videoOverlayGeometryFor(selectedVideo))"
                >
                  Reset crop &amp; zoom
                </button>
              </div>
            </details>
            <details class="inspector-section lut-section" open>
              <summary class="inspector-section-summary">
                <span class="inspector-section-title"><Palette :size="13" />Color grade &amp; LUT</span>
                <button
                  v-if="videoOverlayGeometryFor(selectedVideo).lut && videoOverlayGeometryFor(selectedVideo).lut !== 'none'"
                  type="button"
                  class="framing-inline-reset"
                  title="Reset color grade"
                  @click.stop.prevent="resetLut(videoOverlayGeometryFor(selectedVideo))"
                >
                  Reset
                </button>
              </summary>
              <div class="lut-panel">
                <LutPicker
                  :lut="videoOverlayGeometryFor(selectedVideo).lut || 'none'"
                  :strength="videoOverlayGeometryFor(selectedVideo).lut_strength ?? 1"
                  :presets="lutPresets"
                  @update:lut="(id) => assignLut(videoOverlayGeometryFor(selectedVideo!), id)"
                  @update:strength="(s) => assignLutStrength(videoOverlayGeometryFor(selectedVideo!), s)"
                  @change="commitInspectorEdit"
                />
              </div>
            </details>
            <details class="inspector-section frame-section" open>
              <summary class="inspector-section-summary">
                <span class="inspector-section-title"><Frame :size="13" />Border &amp; corners</span>
                <button
                  v-if="hasFrameDecoration(videoOverlayGeometryFor(selectedVideo))"
                  type="button"
                  class="framing-inline-reset"
                  title="Remove border and rounded corners"
                  @click.stop.prevent="resetFrame(videoOverlayGeometryFor(selectedVideo))"
                >
                  Reset
                </button>
              </summary>
              <FrameStylePicker
                :geometry="videoOverlayGeometryFor(selectedVideo)"
                @update="(patch) => assignFrame(videoOverlayGeometryFor(selectedVideo!), patch)"
                @change="commitInspectorEdit"
              />
            </details>
            <label>Opacity · {{ Math.round((selectedVideo.opacity ?? 1) * 100) }}%<input v-model.number="selectedVideo.opacity" type="range" min="0" max="1" step="0.05" @change="commitInspectorEdit" /></label>
            <label>Volume · {{ Math.round((selectedVideo.volume ?? 1) * 100) }}%<input v-model.number="selectedVideo.volume" type="range" min="0" max="2" step="0.05" @input="syncVideoOverlays" @change="commitInspectorEdit" /></label>
            <div v-if="videoOverlays.length > 1" class="trim-actions">
              <button type="button" :disabled="selectedVideoIndex <= 0" @click="moveSelectedVideoLayer(-1)">Send backward</button>
              <button type="button" :disabled="selectedVideoIndex >= videoOverlays.length - 1" @click="moveSelectedVideoLayer(1)">Bring forward</button>
            </div>
            <button type="button" class="inspector-action-btn" @click="duplicateSelected" title="Duplicate this video layer (Ctrl+D)">
              <CopyPlus :size="13" />Duplicate video layer
            </button>
            <div class="inspector-audio-row">
              <span class="inspector-label"><Volume2 :size="13" />Layer audio</span>
              <button
                type="button"
                class="audio-toggle-action"
                :class="{ muted: selectedVideo.mute_audio }"
                @click="toggleSelectedAudio"
                :title="selectedVideo.mute_audio ? 'Unmute video layer audio' : 'Mute video layer audio (video only)'"
              >
                <VolumeX v-if="selectedVideo.mute_audio" :size="13" />
                <Volume2 v-else :size="13" />
                <span>{{ selectedVideo.mute_audio ? 'Video only (Muted)' : 'Video + Audio' }}</span>
              </button>
            </div>
            <div class="trim-actions">
              <button
                type="button"
                title="Unmute this video layer and mute overlapping primary clips underneath"
                @click="soloVideoLayerAudio"
              >
                Use layer audio (mute primary)
              </button>
              <button
                type="button"
                title="Extract this layer's audio onto the AUDIO track and make this layer video-only"
                @click="detachSelectedVideoAudio"
              >
                Detach audio
              </button>
            </div>
            <StemSeparationControls v-if="stemTarget" v-model:provider="stemProvider" v-model:include-voice="stemIncludeVoice" :separators="stemSeparators" :busy="stemRun.busy" :status="stemRun.status" :error="stemRun.error" @separate="separateMusic" />
            <SaveToLibraryControl v-if="stemTarget" :default-label="stemTarget.label" :busy="librarySave.busy" :status="librarySave.status" :error="librarySave.error" @save="saveToLibrary" />
          </template>
          <template v-else-if="selectedText">
            <label>Text<textarea v-model="selectedText.text" rows="3" @change="commitInspectorEdit"></textarea></label>
            <label>Start (ms)<input v-model.number="selectedText.start_ms" type="number" min="0" step="100" @change="commitInspectorEdit" /></label>
            <label>Duration (ms)<input v-model.number="selectedText.duration_ms" type="number" min="100" step="100" @change="commitInspectorEdit" /></label>
            <label>Presentation<select v-model="selectedText.presentation" @change="commitInspectorEdit"><option value="overlay">Overlay</option><option value="full-frame">Full frame</option></select></label>
            <label>Position<select v-model="geometryFor(selectedText).position" @change="commitInspectorEdit"><option value="top">Top</option><option value="center">Center</option><option value="bottom">Bottom</option><option value="bottom-left">Lower left</option><option value="bottom-right">Lower right</option></select></label>
            <label>Size<select v-model="selectedText.size" @change="commitInspectorEdit"><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></label>
            <label>Background<select v-model="selectedText.background" @change="commitInspectorEdit"><option value="none">None</option><option value="shadow">Shadow</option><option value="translucent">Translucent</option><option value="solid">Solid</option></select></label>
          </template>
          <template v-else-if="selectedImage">
            <div v-if="resolveSource(selectedImage.source)?.url" class="inspector-image-preview">
              <img :src="resolveSource(selectedImage.source)!.url" alt="Selected image overlay" />
              <div class="inspector-image-meta">
                <strong>{{ resolveSource(selectedImage.source)?.label || 'Image overlay' }}</strong>
                <span>{{ (selectedImage.duration_ms / 1000).toFixed(1) }}s</span>
              </div>
            </div>
            <label>Start (ms)<input v-model.number="selectedImage.start_ms" type="number" min="0" step="100" @change="commitInspectorEdit" /></label>
            <label>Duration (ms)<input v-model.number="selectedImage.duration_ms" type="number" min="100" step="100" @change="commitInspectorEdit" /></label>
            <div class="inspector-quick-durations">
              <span class="quick-dur-label">Quick duration:</span>
              <div class="quick-dur-buttons">
                <button type="button" class="quick-dur-btn" @click="setImageOverlayDuration(1000)">1s</button>
                <button type="button" class="quick-dur-btn" @click="setImageOverlayDuration(3000)">3s</button>
                <button type="button" class="quick-dur-btn" @click="setImageOverlayDuration(5000)">5s</button>
              </div>
            </div>
            <label>Position
              <select v-model="geometryFor(selectedImage).position" @change="commitInspectorEdit">
                <option value="center">Center</option>
                <option value="full-frame">Full frame (Cover)</option>
                <option value="top-left">Top left</option>
                <option value="top-right">Top right</option>
                <option value="bottom-left">Bottom left</option>
                <option value="bottom-right">Bottom right</option>
                <option value="top">Top center</option>
                <option value="bottom">Bottom center</option>
              </select>
            </label>
            <label v-if="geometryFor(selectedImage).position === 'full-frame'">
              Fit
              <select v-model="geometryFor(selectedImage).fit" @change="commitInspectorEdit">
                <option value="cover">Fill frame (Cover / Crop)</option>
                <option value="contain">Fit frame (Contain / No crop)</option>
              </select>
            </label>
            <label v-else>
              Scale · {{ Math.round((geometryFor(selectedImage).scale ?? 1) * 100) }}%
              <input v-model.number="geometryFor(selectedImage).scale" type="range" min="0.1" max="2" step="0.05" @change="commitInspectorEdit" />
            </label>
            <label>Opacity · {{ Math.round((selectedImage.opacity ?? 1) * 100) }}%<input v-model.number="selectedImage.opacity" type="range" min="0" max="1" step="0.05" @change="commitInspectorEdit" /></label>
            <details class="inspector-section lut-section" open>
              <summary class="inspector-section-summary">
                <span class="inspector-section-title"><Palette :size="13" />Color grade &amp; LUT</span>
                <button
                  v-if="geometryFor(selectedImage).lut && geometryFor(selectedImage).lut !== 'none'"
                  type="button"
                  class="framing-inline-reset"
                  title="Reset color grade"
                  @click.stop.prevent="resetLut(geometryFor(selectedImage))"
                >
                  Reset
                </button>
              </summary>
              <div class="lut-panel">
                <LutPicker
                  :lut="geometryFor(selectedImage).lut || 'none'"
                  :strength="geometryFor(selectedImage).lut_strength ?? 1"
                  :presets="lutPresets"
                  @update:lut="(id) => assignLut(geometryFor(selectedImage!), id)"
                  @update:strength="(s) => assignLutStrength(geometryFor(selectedImage!), s)"
                  @change="commitInspectorEdit"
                />
              </div>
            </details>
            <button type="button" class="inspector-action-btn" @click="duplicateSelected" title="Duplicate this image overlay (Ctrl+D)">
              <CopyPlus :size="13" />Duplicate image overlay
            </button>
          </template>
          <template v-else-if="selectedAudio">
            <label>Label<input v-model="selectedAudio.label" @change="commitInspectorEdit" /></label>
            <label>Start (ms)<input v-model.number="selectedAudio.start_ms" type="number" min="0" step="100" @change="commitInspectorEdit" /></label>
            <label>Duration (ms)<input v-model.number="selectedAudio.duration_ms" type="number" min="100" step="100" @change="commitInspectorEdit" /></label>
            <label>Trim in (ms)<input v-model.number="selectedAudio.source_in_ms" type="number" min="0" step="100" @change="commitInspectorEdit" /></label>
            <label>Volume · {{ Math.round((selectedAudio.volume ?? 1) * 100) }}%<input v-model.number="selectedAudio.volume" type="range" min="0" max="2" step="0.05" @input="syncAudioOverlays" @change="commitInspectorEdit" /></label>
            <label class="layout-check">
              <input type="checkbox" aria-label="Loop audio" :checked="selectedAudio.loop !== false" @change="toggleSelectedAudioLoop" />
              Loop when longer than the audio
            </label>
            <small v-if="audioLoopSpanMs(selectedAudio)" class="audio-loop-note">Repeats every {{ (audioLoopSpanMs(selectedAudio)! / 1000).toFixed(1) }}s</small>
            <button type="button" class="inspector-action-btn" @click="duplicateSelected" title="Duplicate this audio clip (Ctrl+D)">
              <CopyPlus :size="13" />Duplicate audio clip
            </button>
            <div class="inspector-audio-row">
              <span class="inspector-label"><Music :size="13" />Track audio</span>
              <button
                type="button"
                class="audio-toggle-action"
                :class="{ muted: selectedAudio.mute_audio }"
                @click="toggleSelectedAudio"
                :title="selectedAudio.mute_audio ? 'Unmute audio clip' : 'Mute audio clip'"
              >
                <VolumeX v-if="selectedAudio.mute_audio" :size="13" />
                <Volume2 v-else :size="13" />
                <span>{{ selectedAudio.mute_audio ? 'Muted' : 'Active' }}</span>
              </button>
            </div>
            <StemSeparationControls v-if="stemTarget" v-model:provider="stemProvider" v-model:include-voice="stemIncludeVoice" :separators="stemSeparators" :busy="stemRun.busy" :status="stemRun.status" :error="stemRun.error" @separate="separateMusic" />
            <SaveToLibraryControl v-if="stemTarget" :default-label="stemTarget.label" :busy="librarySave.busy" :status="librarySave.status" :error="librarySave.error" @save="saveToLibrary" />
          </template>
        </aside>
      </div>

      <div class="timeline-toolbar">
        <button title="Undo (Ctrl+Z)" :disabled="!history.length" @click="undo"><Undo2 :size="16" /></button>
        <button title="Redo (Ctrl+Shift+Z / Ctrl+Y)" :disabled="!future.length" @click="redo"><Redo2 :size="16" /></button>
        <span></span>
        <button
          :title="isMultiSelect ? `Split ${eligibleSplitCount} selected clip(s) at playhead (S / C)` : 'Split at playhead (S / C)'"
          :disabled="!canSplitAtPlayhead"
          @click="splitAtPlayhead"
        >
          <Split :size="16" />
          <span>Split{{ eligibleSplitCount > 1 ? ` (${eligibleSplitCount})` : '' }}</span>
        </button>
        <button title="Duplicate selected item (Ctrl+D)" :disabled="!selectedPrimary && !selectedVideo && !selectedImage && !selectedText && !selectedAudio" @click="duplicateSelected"><CopyPlus :size="16" /><span>Duplicate</span></button>
        <button class="add-shot-toolbar-btn" title="Add/insert shot from project into timeline" @click="showAddShotModal = true"><Plus :size="15" /><span>Add shot</span></button>
        <button
          type="button"
          :title="selectedAudio ? (selectedAudio.mute_audio ? 'Unmute audio clip (M)' : 'Mute audio clip (M)') : selectedVideo ? (selectedVideo.mute_audio ? 'Unmute video layer audio (M)' : 'Mute video layer audio (M)') : selectedPrimary?.kind === 'video' ? (selectedPrimary.mute_audio ? 'Unmute clip audio (M)' : 'Mute/remove audio from clip (M)') : 'Mute clip audio (select video or audio)'"
          :disabled="selectedPrimary?.kind !== 'video' && !selectedVideo && !selectedAudio"
          :class="{ 'item-muted-active': selectedAudio ? selectedAudio.mute_audio : selectedVideo ? selectedVideo.mute_audio : selectedPrimary?.mute_audio }"
          @click="toggleSelectedAudio"
        >
          <VolumeX v-if="selectedAudio ? selectedAudio.mute_audio : selectedVideo ? selectedVideo.mute_audio : selectedPrimary?.mute_audio" :size="16" />
          <Volume2 v-else :size="16" />
        </button>
        <button :disabled="!selectedPrimary || selectedPrimaryIndex <= 0" title="Move left" @click="moveSelected(-1)"><MoveLeft :size="16" /></button>
        <button :disabled="!selectedPrimary || selectedPrimaryIndex >= primary.length - 1" title="Move right" @click="moveSelected(1)"><MoveRight :size="16" /></button>
        <details ref="addMenuRef" class="add-menu"><summary><Plus :size="15" />Add media &amp; text</summary><div class="add-menu-content">
        <button title="Append current storyboard frame" @click="appendStill"><ImagePlus :size="16" /><span>Append storyboard frame</span></button>
        <label class="tool-upload" title="Upload and append image"><Upload :size="16" /><span>Upload image</span><input type="file" aria-label="Upload and append image" accept="image/png,image/jpeg,image/webp" @change="uploadAsset($event, true)" /></label>
        <button title="Add title card" @click="addTitle"><AlignCenter :size="16" /><span>Title card</span></button>
        <button title="Add secondary video layer" @click="openAddVideoOverlayModal"><Layers :size="16" /><span>Video layer</span></button>
        <label class="tool-upload" title="Upload secondary video onto VIDEO track"><Upload :size="16" /><span>Upload video layer</span><input type="file" aria-label="Upload video overlay" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov" @change="uploadVideoOverlayDirect" /></label>
        <button title="Add text overlay" @click="addText"><Type :size="16" /><span>Text overlay</span></button>
        <button title="Add image overlay" @click="addImageOverlay()"><ImagePlus :size="16" /><span>Image overlay</span></button>
        <label class="tool-upload" title="Upload image overlay"><Upload :size="16" /><span>Upload image</span><input type="file" aria-label="Upload image overlay" accept="image/png,image/jpeg,image/webp" @change="uploadAsset($event, false)" /></label>
        <button title="Add saved music or sounds from the audio library" @click="openAudioLibrary"><Library :size="16" /><span>From audio library</span></button>
        <label class="tool-upload" title="Upload audio onto AUDIO track"><Music :size="16" /><span>Upload audio</span><input type="file" aria-label="Upload audio overlay" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac,.webm" @change="uploadAudioAsset($event)" /></label>
        <button v-if="activeShotWithAudio" title="Add shot audio onto AUDIO track" @click="addShotAudioToTimeline()"><Volume2 :size="16" /><span>Shot audio</span></button>
        </div></details>
        <button
          class="danger-tool"
          :title="isMultiSelect ? `Delete ${selectedEntries.length} selected items (Delete / Backspace)` : 'Delete selected item (Delete / Backspace)'"
          @click="deleteSelection"
        >
          <Trash2 :size="16" />
        </button>
        <div v-if="isMultiSelect" class="multi-select-pill" :title="`Selected: ${multiSelectSummary}`">
          <span class="multi-select-pill-count">{{ selectedEntries.length }} selected</span>
          <span class="multi-select-pill-summary">({{ multiSelectSummary }})</span>
          <button type="button" class="multi-select-pill-clear" title="Clear multi-selection" @click="clearMultiSelection">
            <X :size="11" />
          </button>
        </div>
        <span></span>
        <div class="zoom-toolbar-group">
          <button type="button" :title="`Zoom out (-) · Current: ${zoomLevel === 1 ? 'Fit' : `${zoomLevel.toFixed(1)}x`}`" :disabled="zoomLevel <= 1" @click="zoomOut"><ZoomOut :size="14" /></button>
          <input class="zoom-slider-input" type="range" min="1" max="10" step="0.25" v-model.number="zoomLevel" title="Timeline zoom level" />
          <button type="button" class="zoom-badge-button" :class="{ 'is-fit': zoomLevel === 1 }" @click="resetZoom" title="Click to fit timeline (Ctrl+0)">
            {{ zoomLevel === 1 ? 'Fit' : `${zoomLevel.toFixed(1)}x` }}
          </button>
          <button type="button" :title="`Zoom in (+) · Current: ${zoomLevel === 1 ? 'Fit' : `${zoomLevel.toFixed(1)}x`}`" :disabled="zoomLevel >= 10" @click="zoomIn"><ZoomIn :size="14" /></button>
        </div>
        <button type="button" title="Keyboard shortcuts (?)" :class="{ active: showShortcutsModal }" @click="showShortcutsModal = !showShortcutsModal"><Keyboard :size="15" /></button>
        <button class="reset-tool" @click="resetTimeline"><RefreshCw :size="15" />Reset</button>
      </div>

      <div
        ref="timelineScrollRef"
        class="timeline-scroll"
        :style="{ '--track-width': `${trackWidthPx}px`, '--full-track-width': `${trackWidthPx + 110}px` }"
        @wheel="handleTimelineWheel"
        @scroll="handleTimelineScroll"
      >
        <div class="playhead-scrubber">
          <strong>TIMELINE</strong>
          <div class="timeline-range">
            <input class="timeline-input" type="range" min="0" :max="totalMs" step="20" :value="playheadMs" :style="{ '--playhead': `${playheadPercent}%` }" aria-label="Project timeline position" @input="seek" />
            <span class="playhead-time" :style="{ left: `${playheadPercent}%` }">{{ (playheadMs / 1000).toFixed(2) }}s</span>
            <div class="timeline-ticks" aria-hidden="true"><span v-for="tick in timelineTicks" :key="tick.percent" :style="{ left: `${tick.percent}%` }"><i></i><em>{{ tick.label }}</em></span></div>
          </div>
        </div>

        <section class="tracks">
        <div class="track-row primary-track"><strong>PRIMARY</strong><div ref="primaryTrackRef" class="track-content" :class="{ 'image-drop-active': overlayToPrimaryMarkerMs !== null }"><i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i>
          <i v-if="overlayToPrimaryMarkerMs !== null" class="image-drop-marker" :style="{ left: `${(overlayToPrimaryMarkerMs / (totalMs || 1)) * 100}%` }"></i>
          <button
            v-for="(item, index) in primary"
            :key="item.id"
            class="timeline-block"
            draggable="true"
            :title="`${item.label} · ${(item.duration_ms / 1000).toFixed(1)}s (drag to reorder, drop onto VIDEO for B-roll, or drop onto AUDIO to detach audio · Ctrl/Shift+drag to duplicate)`"
            :aria-pressed="isSelected('primary', item.id)"
            :class="[
              item.kind,
              {
                selected: isSelected('primary', item.id),
                'is-muted': item.mute_audio,
                'is-dragging': dragSourceIndex === index,
                'is-drop-target': dragOverIndex === index,
              },
            ]"
            :style="{
              left: `${(startAt(index) / (totalMs || 1)) * 100}%`,
              width: `${(item.duration_ms / (totalMs || 1)) * 100}%`,
            }"
            @click="handlePrimaryBlockClick($event, item, index)"
            @contextmenu="(e) => { if (e.ctrlKey) e.preventDefault() }"
            @dragstart="onPrimaryDragStart($event, index)"
            @dragover.prevent="onPrimaryDragOver($event, index)"
            @dragleave="onPrimaryDragLeave(index)"
            @drop.prevent="onPrimaryDrop($event, index)"
            @dragend="onPrimaryDragEnd"
          >
            <img v-if="blockThumbnailUrl(item)" class="block-thumb-bg" :src="blockThumbnailUrl(item)!" alt="" loading="lazy" />
            <span class="block-top">
              <span class="block-kind-pill">
                <GripVertical class="block-grip" :size="10" />
                <em>#{{ index + 1 }}</em>
                <span class="block-kind-label">{{ item.kind.toUpperCase() }}</span>
              </span>
              <span class="block-status-icons">
                <span v-if="hasUnresolvedNote(item)" class="block-note-icon" title="This shot has unresolved notes"><MessageSquare :size="10" /></span>
                <Scissors v-if="item.kind === 'video' && (item.source_in_ms > 0)" class="block-trim-icon" :size="10" title="Trimmed clip" />
                <Repeat v-if="item.kind === 'video' && item.loop" class="block-loop-icon" :size="10" title="Loops to fill its length" />
                <span
                  v-if="item.kind === 'video'"
                  role="button"
                  tabindex="-1"
                  class="block-audio-toggle"
                  :class="{ muted: item.mute_audio }"
                  :title="item.mute_audio ? 'Video only · Audio muted (click to unmute)' : 'Audio active (click for video only)'"
                  @pointerdown.stop
                  @click.stop="toggleBlockAudio('primary', item.id)"
                >
                  <VolumeX v-if="item.mute_audio" class="block-muted-icon" :size="11" />
                  <Volume2 v-else class="block-active-audio-icon" :size="11" />
                </span>
              </span>
            </span>
            <span
              class="resize-handle end primary-resize-handle"
              :title="item.kind === 'video' ? 'Drag to trim, or past the clip end to loop it' : 'Drag to change duration'"
              draggable="false"
              @pointerdown.stop.prevent="beginPrimaryResize($event, item)"
              @click.stop
              @dragstart.stop.prevent
            ></span>
            <span class="block-bottom">
              <b>{{ item.label }}</b>
              <small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small>
            </span>
          </button>
          <template v-for="(item, index) in primary" :key="`join-${item.id}`">
            <button
              v-if="index > 0"
              type="button"
              class="transition-marker"
              :class="{ active: !!effectiveTransition(index), overridden: !!item.transition, open: transitionPopoverIndex === index }"
              :style="{ left: `${(startAt(index) / (totalMs || 1)) * 100}%` }"
              :title="effectiveTransition(index) ? `${transitionLabel(effectiveTransition(index)!.kind)} · ${(effectiveTransition(index)!.duration_ms / 1000).toFixed(1)}s${item.transition ? '' : ' (default)'}` : 'Cut (no transition) — click to add one'"
              :aria-label="`Transition into clip ${index + 1}`"
              :data-join-index="index"
              @click.stop="transitionPopoverIndex = transitionPopoverIndex === index ? null : index"
              @pointerdown.stop
            >◆</button>
            <div
              v-if="index > 0 && transitionPopoverIndex === index"
              class="transition-popover"
              :style="{ left: `${(startAt(index) / (totalMs || 1)) * 100}%` }"
              @click.stop
              @pointerdown.stop
            >
              <header>
                <span>Into #{{ index + 1 }}</span>
                <button type="button" aria-label="Close" @click="transitionPopoverIndex = null"><X :size="12" /></button>
              </header>
              <select
                aria-label="Transition for this cut"
                :value="item.transition?.kind ?? 'default'"
                @change="onTransitionKindChange(index, ($event.target as HTMLSelectElement).value)"
              >
                <option value="default">Use default — {{ transitionLabel(defaultTransition?.kind ?? 'cut') }}</option>
                <option v-for="option in TRANSITION_OPTIONS" :key="option.kind" :value="option.kind">{{ option.label }}</option>
              </select>
              <label v-if="(item.transition ?? defaultTransition) && (item.transition ?? defaultTransition)!.kind !== 'cut'">
                Length · {{ transitionDuration(index, (item.transition ?? defaultTransition)!.duration_ms).toFixed(1) }}s
                <input
                  type="range"
                  min="0.1"
                  max="2"
                  step="0.1"
                  :value="transitionDuration(index, (item.transition ?? defaultTransition)!.duration_ms)"
                  @input="transitionDurationDrafts[index] = Number(($event.target as HTMLInputElement).value)"
                  @change="onTransitionDurationChange(index, Number(($event.target as HTMLInputElement).value))"
                />
              </label>
            </div>
          </template>
        </div></div>
        <div
          class="track-row video-track-row"
          :class="{ 'drag-over': isDraggingOverVideoTrack }"
          :style="{ minHeight: `${Math.max(38, videoOverlayPacking.laneCount * 32 + 6)}px` }"
          @dragover.prevent="handleVideoTrackDragOver"
          @dragleave="handleVideoTrackDragLeave"
          @drop.prevent="handleVideoTrackDrop"
        >
          <strong class="track-header-cell">
            <span>VIDEO</span>
            <span class="track-header-actions">
              <button
                type="button"
                class="track-action-btn"
                title="Add secondary video layer (+)"
                @click="openAddVideoOverlayModal"
              >
                <Plus :size="11" />
              </button>
              <label class="track-action-upload" title="Upload video file to VIDEO track">
                <Upload :size="10" />
                <input type="file" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov" @change="uploadVideoOverlayDirect" />
              </label>
            </span>
          </strong>
          <div
            class="overlay-track"
            :style="{ minHeight: `${Math.max(38, videoOverlayPacking.laneCount * 32 + 6)}px` }"
            @click="handleVideoTrackClick"
          >
            <i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i>
            <div
              v-if="!videoOverlays.length"
              class="empty-track-cta"
              @click.stop="openAddVideoOverlayModal"
            >
              <Plus :size="11" />
              <span>Click or drop secondary video here</span>
            </div>
            <button
              v-for="item in videoOverlays"
              :key="item.id"
              class="overlay-block video"
              :class="{ selected: isSelected('video', item.id), 'is-muted': item.mute_audio }"
              :style="{
                left: `${item.start_ms / (totalMs || 1) * 100}%`,
                width: `${(item.duration_ms / (totalMs || 1)) * 100}%`,
                top: `${5 + (videoOverlayPacking.laneById[item.id] ?? 0) * 32}px`,
              }"
              :title="`${item.label} · ${(item.duration_ms / 1000).toFixed(1)}s (${item.mute_audio ? 'Video only' : 'Video + Audio'} · drag to move)`"
              @pointerdown="beginOverlayEdit($event, 'video', item, 'move')"
              @click.stop="selectItem('video', item.id, item.start_ms, $event)"
            >
              <span class="resize-handle start" title="Adjust start" @pointerdown.stop="beginOverlayEdit($event, 'video', item, 'resize-start')"></span>
              <Film :size="11" class="overlay-icon" />
              <span
                role="button"
                tabindex="-1"
                class="overlay-audio-toggle"
                :class="{ muted: item.mute_audio }"
                :title="item.mute_audio ? 'Video only · B-roll (click to enable layer audio)' : 'Layer audio active (click for video only)'"
                @pointerdown.stop
                @click.stop="toggleBlockAudio('video', item.id)"
              >
                <VolumeX v-if="item.mute_audio" class="overlay-audio-icon muted" :size="10" />
                <Volume2 v-else class="overlay-audio-icon" :size="10" />
              </span>
              <span class="overlay-label">{{ item.label || resolveSource(item.source)?.label || 'VIDEO' }}</span>
              <small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small>
              <span class="resize-handle end" title="Adjust end" @pointerdown.stop="beginOverlayEdit($event, 'video', item, 'resize-end')"></span>
            </button>
          </div>
        </div>
        <div
          class="track-row image-track-row"
          :class="{ 'drag-over': isDraggingOverImageTrack }"
          :style="{ minHeight: `${Math.max(38, imageOverlayPacking.laneCount * 32 + 6)}px` }"
          @dragover.prevent="handleImageTrackDragOver"
          @dragleave="handleImageTrackDragLeave"
          @drop.prevent="handleImageTrackDrop"
        >
          <strong class="track-header-cell">
            <span>IMAGE</span>
            <span class="track-header-actions">
              <button
                type="button"
                class="track-action-btn"
                title="Add image overlay (+)"
                @click="openAddImageOverlayModal"
              >
                <Plus :size="11" />
              </button>
              <label class="track-action-upload" title="Upload image file to IMAGE track">
                <Upload :size="10" />
                <input type="file" accept="image/png,image/jpeg,image/webp" @change="uploadImageOverlayDirect" />
              </label>
            </span>
          </strong>
          <div
            class="overlay-track"
            :style="{ minHeight: `${Math.max(38, imageOverlayPacking.laneCount * 32 + 6)}px` }"
            @click="handleImageTrackClick"
          >
            <i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i>
            <div
              v-if="!timeline.document.image_overlays?.length"
              class="empty-track-cta"
              @click.stop="openAddImageOverlayModal"
            >
              <Plus :size="11" />
              <span>Click or drop image here</span>
            </div>
            <button
              v-for="item in timeline.document.image_overlays"
              :key="item.id"
              class="overlay-block image"
              :class="{ selected: isSelected('image', item.id) }"
              :style="{
                left: `${item.start_ms / (totalMs || 1) * 100}%`,
                width: `${(item.duration_ms / (totalMs || 1)) * 100}%`,
                top: `${5 + (imageOverlayPacking.laneById[item.id] ?? 0) * 32}px`,
              }"
              title="Drag to move image overlay"
              @pointerdown="beginOverlayEdit($event, 'image', item, 'move')"
              @click.stop="selectItem('image', item.id, item.start_ms, $event)"
            >
              <span class="resize-handle start" title="Adjust start" @pointerdown.stop="beginOverlayEdit($event, 'image', item, 'resize-start')"></span>
              <img v-if="resolveSource(item.source)?.url" :src="resolveSource(item.source)!.url" class="overlay-thumb" alt="" />
              <ImagePlus v-else :size="11" class="overlay-icon" />
              <span class="overlay-label">{{ resolveSource(item.source)?.label || 'IMAGE' }}</span>
              <small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small>
              <span class="resize-handle end" title="Adjust end" @pointerdown.stop="beginOverlayEdit($event, 'image', item, 'resize-end')"></span>
            </button>
          </div>
        </div>
        <div class="track-row"><strong>TEXT</strong><div class="overlay-track"><i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i><button v-for="item in timeline.document.text_overlays" :key="item.id" class="overlay-block text" :class="{ selected: isSelected('text', item.id) }" :style="{ left: `${item.start_ms / (totalMs || 1) * 100}%`, width: `${(item.duration_ms / (totalMs || 1)) * 100}%` }" title="Drag to move text overlay" @pointerdown="beginOverlayEdit($event, 'text', item, 'move')" @click.stop="selectItem('text', item.id, item.start_ms, $event)"><span class="resize-handle start" title="Adjust start" @pointerdown.stop="beginOverlayEdit($event, 'text', item, 'resize-start')"></span><span class="overlay-label">{{ item.text }}</span><small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small><span class="resize-handle end" title="Adjust end" @pointerdown.stop="beginOverlayEdit($event, 'text', item, 'resize-end')"></span></button></div></div>
        <div
          class="track-row audio-track-row"
          :class="{ 'drag-over': isDraggingOverAudioTrack }"
          :style="{ minHeight: `${Math.max(38, audioOverlayPacking.laneCount * 32 + 6)}px` }"
          @dragover.prevent="handleAudioTrackDragOver"
          @dragleave="handleAudioTrackDragLeave"
          @drop.prevent="handleAudioTrackDrop"
        >
          <strong class="track-header-cell">
            <span>AUDIO</span>
            <span class="track-header-actions">
              <button
                type="button"
                class="track-action-btn"
                :disabled="!activeShotWithAudio"
                :title="activeShotWithAudio ? 'Add secondary audio from the current shot (+)' : 'No shot audio available'"
                @click="addShotAudioToTimeline()"
              >
                <Plus :size="11" />
              </button>
              <label class="track-action-upload" title="Upload audio file to AUDIO track">
                <Upload :size="10" />
                <input type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac,.webm" @change="uploadAudioAsset($event)" />
              </label>
              <span class="audio-library-anchor">
                <button type="button" class="track-action-btn" :class="{ active: audioLibraryOpen }" aria-label="Open audio library" title="Audio library: saved music and sounds from any project" @click.stop="audioLibraryOpen = !audioLibraryOpen">
                  <Library :size="11" />
                </button>
                <AudioLibraryPanel v-if="audioLibraryOpen" ref="audioLibraryPanel" @add="addFromLibrary" @close="audioLibraryOpen = false" />
              </span>
            </span>
          </strong>
          <div
            class="overlay-track"
            :style="{ minHeight: `${Math.max(38, audioOverlayPacking.laneCount * 32 + 6)}px` }"
            @click="handleAudioTrackClick"
          >
            <i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i>
            <label v-if="!audioOverlays.length" class="empty-track-cta" @click.stop>
              <Plus :size="11" />
              <span>Click or drop secondary audio here</span>
              <input type="file" accept="audio/*,.mp3,.wav,.m4a,.aac,.ogg,.flac,.webm" @change="uploadAudioAsset($event)" />
            </label>
            <button
              v-for="item in audioOverlays"
              :key="item.id"
              class="overlay-block audio"
              :class="{ selected: isSelected('audio', item.id), 'is-muted': item.mute_audio }"
              :style="{
                left: `${item.start_ms / (totalMs || 1) * 100}%`,
                width: `${(item.duration_ms / (totalMs || 1)) * 100}%`,
                top: `${5 + (audioOverlayPacking.laneById[item.id] ?? 0) * 32}px`,
              }"
              :title="`${item.label} · ${(item.duration_ms / 1000).toFixed(1)}s (drag to move)`"
              @pointerdown="beginOverlayEdit($event, 'audio', item, 'move')"
              @click.stop="selectItem('audio', item.id, item.start_ms, $event)"
            >
              <span class="resize-handle start" title="Adjust start" @pointerdown.stop="beginOverlayEdit($event, 'audio', item, 'resize-start')"></span>
              <span
                role="button"
                tabindex="-1"
                class="overlay-audio-toggle"
                :class="{ muted: item.mute_audio }"
                :title="item.mute_audio ? 'Audio muted (click to unmute)' : 'Audio active (click to mute)'"
                @pointerdown.stop
                @click.stop="toggleBlockAudio('audio', item.id)"
              >
                <VolumeX v-if="item.mute_audio" class="overlay-audio-icon muted" :size="11" />
                <Music v-else class="overlay-audio-icon" :size="11" />
              </span>
              <span class="overlay-label">{{ item.label }}</span>
              <Repeat v-if="audioLoopSpanMs(item)" class="overlay-loop-icon" :size="10" :title="`Repeats every ${(audioLoopSpanMs(item)! / 1000).toFixed(1)}s to fill the clip`" />
              <small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small>
              <span class="resize-handle end" title="Adjust end" @pointerdown.stop="beginOverlayEdit($event, 'audio', item, 'resize-end')"></span>
            </button>
          </div>
        </div>
        </section>
      </div>

      <!-- ADD SHOT MODAL -->
      <div v-if="showAddShotModal" class="add-shot-backdrop" @click.self="showAddShotModal = false">
        <div class="add-shot-card">
          <header>
            <div class="add-shot-title"><Film :size="16" /><span>Add shot to timeline</span></div>
            <button type="button" class="mini-icon" aria-label="Close" @click="showAddShotModal = false"><X :size="15" /></button>
          </header>
          <div class="add-shot-search">
            <input v-model="shotFilterQuery" type="text" placeholder="Filter shots by title or action..." />
          </div>
          <div class="add-shot-list">
            <div v-for="shot in availableShots" :key="shot.id" class="add-shot-row">
              <div class="add-shot-thumb">
                <video v-if="shot.clip" :src="shot.clip.url" muted preload="metadata"></video>
                <img v-else-if="shot.image || shot.image_references?.[0]" :src="(shot.image || shot.image_references?.[0])?.url" alt="" />
                <Film v-else :size="18" />
              </div>
              <div class="add-shot-info">
                <strong>{{ shot.title || 'Untitled shot' }}</strong>
                <span>{{ shot.clip ? 'Video clip' : 'Storyboard still' }} · {{ (shot.duration_ms / 1000).toFixed(1) }}s</span>
              </div>
              <button v-if="shotHasVideo(shot)" type="button" class="add-shot-insert-btn secondary" title="Add shot clip onto VIDEO overlay track" @click="addShotVideoToTimeline(shot)">
                <Layers :size="13" />Video layer
              </button>
              <button v-if="shotHasImage(shot)" type="button" class="add-shot-insert-btn secondary" title="Add shot frame onto IMAGE track" @click="addShotImageToTimeline(shot)">
                <ImagePlus :size="13" />Image
              </button>
              <button v-if="shotHasAudio(shot)" type="button" class="add-shot-insert-btn secondary" title="Add shot audio onto AUDIO track" @click="addShotAudioToTimeline(shot)">
                <Music :size="13" />Audio
              </button>
              <button type="button" class="add-shot-insert-btn" @click="addShotToTimeline(shot)">
                <Plus :size="13" />Insert
              </button>
            </div>
            <div v-if="!availableShots.length" class="empty-shot-state">No shots found matching your search.</div>
          </div>
        </div>
      </div>

      <!-- ADD VIDEO OVERLAY MODAL -->
      <div v-if="showAddVideoOverlayModal" class="add-shot-backdrop" @click.self="showAddVideoOverlayModal = false">
        <div class="add-shot-card add-image-card">
          <header>
            <div class="add-shot-title"><Layers :size="16" /><span>Add secondary video layer to timeline</span></div>
            <button type="button" class="mini-icon" aria-label="Close" @click="showAddVideoOverlayModal = false"><X :size="15" /></button>
          </header>
          <div class="add-image-modal-toolbar">
            <div class="add-shot-search">
              <input v-model="videoFilterQuery" type="text" placeholder="Filter video clips by title or description..." />
            </div>
            <label class="add-image-upload-btn" title="Upload new video from your computer">
              <Upload :size="13" />
              <span>Upload video</span>
              <input type="file" accept="video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov" @change="uploadVideoOverlayDirect" />
            </label>
          </div>
          <div class="add-shot-list">
            <div v-if="availableVideoAssets.length" class="modal-section-title">Uploaded video layers</div>
            <div v-for="asset in availableVideoAssets" :key="asset.id" class="add-shot-row">
              <div class="add-shot-thumb">
                <video :src="asset.url" muted preload="metadata"></video>
              </div>
              <div class="add-shot-info">
                <strong>{{ asset.label }}</strong>
                <span>Uploaded video asset · Full-frame scale by default</span>
              </div>
              <button type="button" class="add-shot-insert-btn" @click="addAssetVideoToTimeline(asset)">
                <Plus :size="13" />Insert at playhead
              </button>
            </div>

            <div class="modal-section-title">Project shot video clips</div>
            <div v-for="shot in availableVideoShots" :key="shot.id" class="add-shot-row">
              <div class="add-shot-thumb">
                <video v-if="shotClipUrl(shot)" :src="shotClipUrl(shot)" muted preload="metadata"></video>
                <Film v-else :size="18" />
              </div>
              <div class="add-shot-info">
                <strong>{{ shot.title || 'Untitled shot' }}</strong>
                <span>Video clip · {{ (shot.duration_ms / 1000).toFixed(1) }}s · Full-frame scale by default</span>
              </div>
              <button type="button" class="add-shot-insert-btn" @click="addShotVideoToTimeline(shot)">
                <Plus :size="13" />Insert at playhead
              </button>
            </div>
            <div v-if="!availableVideoShots.length && !availableVideoAssets.length" class="empty-shot-state">
              No video clips found matching your search. You can upload a video (.mp4, .webm, .mov) using the button above.
            </div>
          </div>
        </div>
      </div>

      <!-- ADD IMAGE OVERLAY MODAL -->
      <div v-if="showAddImageOverlayModal" class="add-shot-backdrop" @click.self="showAddImageOverlayModal = false">
        <div class="add-shot-card add-image-card">
          <header>
            <div class="add-shot-title"><ImagePlus :size="16" /><span>Add image to timeline</span></div>
            <button type="button" class="mini-icon" aria-label="Close" @click="showAddImageOverlayModal = false"><X :size="15" /></button>
          </header>
          <div class="add-image-modal-toolbar">
            <div class="add-shot-search">
              <input v-model="imageFilterQuery" type="text" placeholder="Filter shots by title or description..." />
            </div>
            <label class="add-image-upload-btn" title="Upload new image from your computer">
              <Upload :size="13" />
              <span>Upload image</span>
              <input type="file" accept="image/png,image/jpeg,image/webp" @change="uploadImageOverlayDirect" />
            </label>
          </div>
          <div class="add-shot-list">
            <div v-if="availableImageAssets.length" class="modal-section-title">Uploaded images</div>
            <div v-for="asset in availableImageAssets" :key="asset.id" class="add-shot-row">
              <div class="add-shot-thumb">
                <img :src="asset.url" alt="" />
              </div>
              <div class="add-shot-info">
                <strong>{{ asset.label }}</strong>
                <span>Uploaded image asset</span>
              </div>
              <button type="button" class="add-shot-insert-btn" @click="addAssetImageToTimeline(asset)">
                <Plus :size="13" />Insert at playhead
              </button>
            </div>

            <div class="modal-section-title">Project shots &amp; storyboard frames</div>
            <div v-for="shot in availableImageShots" :key="shot.id" class="add-shot-row">
              <div class="add-shot-thumb">
                <img :src="shotStillUrl(shot)" alt="" />
              </div>
              <div class="add-shot-info">
                <strong>{{ shot.title || 'Untitled shot' }}</strong>
                <span>Storyboard still · {{ (shot.duration_ms / 1000).toFixed(1) }}s</span>
              </div>
              <button type="button" class="add-shot-insert-btn" @click="addShotImageToTimeline(shot)">
                <Plus :size="13" />Insert at playhead
              </button>
            </div>
            <div v-if="!availableImageShots.length && !availableImageAssets.length" class="empty-shot-state">
              No images found matching your search. You can upload an image using the button above.
            </div>
          </div>
        </div>
      </div>

      <!-- KEYBOARD SHORTCUTS MODAL -->
      <div v-if="showShortcutsModal" class="shortcuts-modal-backdrop" @click.self="showShortcutsModal = false">
        <div class="shortcuts-card">
          <header>
            <div class="shortcuts-title"><Keyboard :size="16" /><span>Keyboard shortcuts</span></div>
            <button type="button" class="mini-icon" aria-label="Close" @click="showShortcutsModal = false"><X :size="15" /></button>
          </header>
          <div class="shortcuts-grid">
            <div class="shortcut-row"><kbd>Space</kbd><span>Play / Pause</span></div>
            <div class="shortcut-row"><kbd>Ctrl / ⌘</kbd> + <kbd>Z</kbd><span>Undo</span></div>
            <div class="shortcut-row"><kbd>Ctrl / ⌘</kbd> + <kbd>⇧</kbd> + <kbd>Z</kbd> / <kbd>Ctrl+Y</kbd><span>Redo</span></div>
            <div class="shortcut-row"><kbd>Ctrl / ⌘</kbd> + <kbd>D</kbd><span>Duplicate clip (play twice)</span></div>
            <div class="shortcut-row"><kbd>S</kbd> / <kbd>C</kbd><span>Split clip at playhead</span></div>
            <div class="shortcut-row"><kbd>Delete</kbd> / <kbd>⌫</kbd><span>Delete selected item</span></div>
            <div class="shortcut-row"><kbd>←</kbd> / <kbd>→</kbd><span>Step 50ms (Shift: 1s)</span></div>
            <div class="shortcut-row"><kbd>+</kbd> / <kbd>-</kbd><span>Zoom in / out</span></div>
            <div class="shortcut-row"><kbd>Ctrl / ⌘</kbd> + <kbd>0</kbd><span>Fit timeline</span></div>
            <div class="shortcut-row"><kbd>M</kbd><span>Mute / unmute clip audio</span></div>
            <div class="shortcut-row"><kbd>I</kbd> / <kbd>O</kbd><span>Set trim in / out</span></div>
            <div class="shortcut-row"><kbd>Home</kbd> / <kbd>End</kbd><span>Jump to start / end</span></div>
          </div>
        </div>
      </div>

      <div v-if="variantExport" class="export-status" :class="{ stale: variantExport.is_stale }"><strong>{{ variant === 'shorts' ? 'Shorts' : 'Landscape' }} export</strong><span>{{ variantExport.width }} × {{ variantExport.height }} · {{ (variantExport.duration_ms / 1000).toFixed(1) }}s</span><em>{{ variantExport.is_stale ? 'Outdated — render again' : 'Up to date' }}</em></div>
      <!-- UNRESOLVED NOTES INTERRUPT MODAL -->
      <div v-if="showUnresolvedNotesModal" class="publish-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="unresolved-notes-title" @click.self="showUnresolvedNotesModal = false">
        <section class="unresolved-notes-card">
          <header>
            <div class="unresolved-notes-icon"><CircleAlert :size="22" /></div>
            <div>
              <h2 id="unresolved-notes-title">Unresolved shot notes</h2>
              <p>{{ unresolvedTimelineShots.length }} shot{{ unresolvedTimelineShots.length === 1 ? '' : 's' }} in this timeline still need review before exporting.</p>
            </div>
            <button type="button" class="mini-icon unresolved-close-btn" aria-label="Close" @click="showUnresolvedNotesModal = false">
              <X :size="16" />
            </button>
          </header>
          <ul class="unresolved-notes-list">
            <li
              v-for="shot in unresolvedTimelineShots.slice(0, 5)"
              :key="shot.id"
              class="unresolved-shot-item"
              title="Click to jump directly to this shot"
              @click="reviewShot(shot.id)"
            >
              <div class="unresolved-shot-item-main">
                <MessageSquare :size="14" class="unresolved-item-icon" />
                <strong>{{ shot.title || `Shot ${shot.order_index + 1}` }}</strong>
              </div>
              <span class="unresolved-count-pill">
                {{ ((shot.notes || []).filter((note) => !note.resolved).length) || shot.unresolved_note_count || 1 }} unresolved
                <ChevronRight :size="13" />
              </span>
            </li>
          </ul>
          <p v-if="unresolvedTimelineShots.length > 5" class="unresolved-more">And {{ unresolvedTimelineShots.length - 5 }} more shots with unresolved notes.</p>
          <footer>
            <button type="button" class="secondary-button" @click="continueExportWithNotes">Continue without resolving</button>
            <button type="button" class="primary-button" @click="reviewFirstUnresolvedNote">Go back to resolve</button>
          </footer>
        </section>
      </div>

      <div v-if="showPublishModal" class="publish-modal-backdrop" @click.self="closePublishModal()">
        <div class="publish-modal-card">
          <PublishPanel
            :project="project"
            :variant="variant"
            :episode-id="props.compilationId ? undefined : store.currentEpisodeId || undefined"
            :compilation-id="props.compilationId || undefined"
            as-modal
            @close="closePublishModal()"
            @open-kit="openReleaseKit()"
            @published="store.loadProject(project.id); closePublishModal()"
          />
        </div>
      </div>
      <div v-if="showReleaseKit" class="publish-modal-backdrop" @click.self="closeReleaseKit()">
        <div class="publish-modal-card release-kit-card">
          <ReleaseKitPanel
            :project="project"
            :variant="variant"
            :episode-id="releaseKitScope.episodeId"
            :compilation-id="releaseKitScope.compilationId"
            :refresh-key="releaseKitRefresh"
            :segments="releaseFrameSegments"
            :initial-ms="playheadMs"
            as-modal
            @close="closeReleaseKit()"
          />
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* ==========================================================================
   TIMELINE EDITOR SHELL & HEADER
   ========================================================================== */
.timeline-editor {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: auto;
  overflow: hidden;
  background: var(--workspace-bg);
  color: var(--text-primary);
  border-top: 1px solid var(--line);
}

.editor-header {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  min-height: 60px;
  padding: 10px 18px;
  border-bottom: 1px solid var(--line);
  background: var(--chrome-bg);
  backdrop-filter: blur(18px);
}

.editor-title-block h2 {
  margin: 0;
  color: var(--text-primary);
  font-size: 17px;
  font-weight: 650;
  letter-spacing: -0.02em;
}

.timeline-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 2px;
}

.compilation-badge-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px;
  border-radius: 6px;
  background: rgba(99, 102, 241, 0.16);
  border: 1px solid rgba(99, 102, 241, 0.35);
  color: #a5b4fc;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.edit-compilation-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
  border: none;
  color: #c7d2fe;
  cursor: pointer;
  padding: 0;
  transition: all 0.15s ease;
}

.edit-compilation-btn:hover {
  background: rgba(99, 102, 241, 0.35);
  color: #ffffff;
}

.exit-compilation-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
  border: none;
  color: #c7d2fe;
  cursor: pointer;
  padding: 0;
  transition: all 0.15s ease;
}

.exit-compilation-btn:hover {
  background: rgba(239, 68, 68, 0.25);
  color: #fca5a5;
}

.editor-title-block p {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  color: var(--text-tertiary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.meta-dot {
  opacity: 0.5;
}

.save-status {
  display: inline-flex;
  align-items: center;
  margin-left: 6px;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--fill-muted);
  border: 1px solid var(--line-subtle);
  color: var(--muted);
  font-size: 10px;
  font-weight: 600;
}

.save-status.unsaved,
.save-status.saving {
  color: var(--warning);
  border-color: rgba(217, 155, 74, 0.3);
  background: rgba(217, 155, 74, 0.1);
}

.save-status.failed {
  color: var(--danger);
  border-color: rgba(239, 106, 106, 0.35);
  background: var(--danger-soft);
}

.editor-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.variant-toggle {
  display: flex;
  align-items: center;
  gap: 3px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--fill-muted);
}

.variant-toggle button {
  min-height: 28px;
  padding: 0 11px;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--muted);
  font-size: 11px;
  font-weight: 650;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}

.variant-toggle button:hover {
  color: var(--text-primary);
}

.variant-toggle button.active {
  color: var(--text-primary);
  background: var(--surface-active);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08), var(--shadow);
}

.publish-header-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

/* ==========================================================================
   BANNERS & LOADING
   ========================================================================== */
.render-progress-banner {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 9px 16px;
  background: rgba(14, 21, 33, 0.95);
  border-bottom: 1px solid rgba(56, 220, 255, 0.3);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.4);
}

.render-progress-info {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
}

.render-progress-label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #e2e8f0;
  font-weight: 500;
}

.render-progress-pct {
  font-weight: 700;
  color: #38dcff;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.05em;
}

.render-progress-track {
  width: 100%;
  height: 4px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 9999px;
  overflow: hidden;
}

.render-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #38dcff 0%, #a3e635 100%);
  border-radius: 9999px;
  transition: width 0.3s ease;
  box-shadow: 0 0 8px rgba(56, 220, 255, 0.6);
}

.timeline-error-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 7px 14px;
  background: var(--danger-soft);
  border-bottom: 1px solid rgba(239, 106, 106, 0.3);
  color: var(--danger);
  font-size: 12px;
}

.form-error-dismiss {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  opacity: 0.8;
}

.form-error-dismiss:hover {
  opacity: 1;
  background: rgba(255, 255, 255, 0.08);
}

.timeline-loading {
  display: flex;
  gap: 10px;
  align-items: center;
  justify-content: center;
  min-height: 420px;
  color: var(--muted);
  font-size: 13px;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* ==========================================================================
   MAIN WORKSPACE: PROGRAM MONITOR + INSPECTOR
   ========================================================================== */
.editor-main {
  display: grid;
  flex: 1;
  grid-template-columns: minmax(0, 1fr) 296px;
  grid-template-rows: minmax(0, 1fr);
  min-height: 280px;
  overflow: hidden;
  border-bottom: 1px solid var(--line);
  background: var(--workspace-bg);
}

.preview-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 14px 20px 12px;
  border-right: 1px solid var(--line);
  background:
    radial-gradient(circle at 50% 10%, var(--fill-muted), transparent 42%),
    var(--preview-pane);
}

.program-monitor {
  position: relative;
  display: grid;
  place-items: center;
  flex: 0 1 auto;
  min-height: 0;
  min-width: 0;
  max-height: calc(100% - 46px);
  max-width: 100%;
  width: auto;
  height: 100%;
  margin: auto;
  aspect-ratio: 16 / 9;
  container-type: inline-size;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--preview-well);
  box-shadow: var(--shadow-panel), inset 0 1px 0 var(--fill-muted);
  overflow: hidden;
}

.program-monitor.shorts {
  width: auto;
  height: 100%;
  max-width: 100%;
  aspect-ratio: 9 / 16;
}

.program-monitor video:not(.preview-video-overlay),
.program-monitor .preview-primary-media {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  user-select: none;
  -webkit-user-drag: none;
}

.program-monitor .preview-primary-window {
  position: absolute;
  z-index: 2;
  display: grid;
  place-items: center;
  overflow: hidden;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}

.program-monitor .preview-primary-window.can-reframe {
  cursor: grab;
}

.program-monitor .preview-primary-window.can-reframe:active,
.program-monitor .preview-primary-window.is-dragging {
  cursor: grabbing !important;
}

.program-monitor .preview-primary-window .preview-reframe-overlay {
  position: absolute;
  inset: 0;
  z-index: 4;
  cursor: grab;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}

.program-monitor .preview-primary-window.is-dragging .preview-reframe-overlay {
  cursor: grabbing !important;
}

.program-monitor .preview-media-border {
  position: absolute;
  z-index: 2;
  box-sizing: border-box;
  pointer-events: none;
}

.program-monitor .preview-media-border.primary-border {
  z-index: 3;
}

.program-monitor .lut-source-hidden {
  opacity: 0 !important;
  filter: none !important;
  pointer-events: none;
}

.program-monitor .lut-gl-canvas {
  z-index: 1;
  pointer-events: none;
}

.preview-title,
.preview-missing {
  padding: 30px;
  text-align: center;
  font-size: 26px;
  font-weight: 800;
  color: var(--text-secondary);
}

.preview-missing {
  color: var(--text-tertiary);
  font-size: 16px;
  font-weight: 600;
}

.source-badge {
  position: absolute;
  left: 10px;
  top: 10px;
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(10, 12, 14, 0.82);
  backdrop-filter: blur(8px);
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  pointer-events: none;
  user-select: none;
}

.program-monitor .reframe-hud-badge {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 6;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(10, 12, 14, 0.9);
  border: 1px solid rgba(57, 208, 255, 0.45);
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45);
  color: #f8fafc;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  pointer-events: none;
  user-select: none;
}

.program-monitor .reframe-hud-badge strong {
  color: #38bdf8;
  font-variant-numeric: tabular-nums;
}

.program-monitor.has-layout {
  overflow: hidden;
  background: #050607;
}

.program-monitor .layout-matte {
  position: absolute;
  inset: -8%;
  z-index: 0;
  pointer-events: none;
}

.program-monitor.has-layout > video,
.program-monitor.has-layout > .preview-primary-media,
.program-monitor.has-layout > .preview-title,
.program-monitor.has-layout > .preview-missing {
  z-index: 2;
}

.layout-band {
  position: absolute;
  left: 0;
  right: 0;
  z-index: 3;
  display: grid;
  place-items: center;
  padding: 0 8%;
  pointer-events: none;
  text-align: center;
}

.layout-band.top { top: 0; }
.layout-band.bottom { bottom: 0; }

.layout-band span {
  color: #f4f7fb;
  font-weight: 800;
  font-size: clamp(0.72rem, 2.4vw, 1.05rem);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.85), 0 0 18px rgba(0, 0, 0, 0.35);
  line-height: 1.25;
}

.layout-accent {
  position: absolute;
  left: 0;
  right: 0;
  height: 2px;
  z-index: 4;
  pointer-events: none;
  box-shadow: 0 0 10px currentColor;
}

.layout-section {
  margin: 0 0 8px;
  margin-top: 0;
  padding-top: 0;
  padding-bottom: 10px;
  border-top: none;
  border-bottom: 1px solid var(--line);
}

.layout-section-summary {
  padding: 2px 0 6px;
}

.layout-summary-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 58%;
  padding: 2px 7px 2px 3px;
  border-radius: 6px;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(15, 23, 42, 0.55);
  color: #e2e8f0;
  font-size: 10.5px;
  font-weight: 650;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.layout-summary-thumb {
  position: relative;
  display: block;
  flex: 0 0 auto;
  width: 14px;
  height: 18px;
  border-radius: 2px;
  background: #0a0c10;
  border: 1px solid rgba(255, 255, 255, 0.1);
  overflow: hidden;
}

.layout-summary-thumb.landscape {
  width: 22px;
  height: 12px;
}

.layout-summary-video {
  position: absolute;
  display: block;
  border-radius: 1px;
  background: linear-gradient(135deg, #5b7cfa, #39d0ff 55%, #7dffb3);
}

.layout-template-card {
  margin: 0;
  border: none;
  background: transparent;
  padding: 0;
}

.layout-card-blurb {
  margin: 0 0 8px;
  font-size: 0.72rem;
  line-height: 1.35;
  color: var(--muted);
}

.layout-template-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 8px;
}

.layout-template-thumb {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px;
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(15, 23, 42, 0.55);
  color: #cbd5e1;
  cursor: pointer;
  text-align: left;
}

.layout-template-thumb:hover {
  border-color: rgba(148, 163, 184, 0.4);
}

.layout-template-thumb.active {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 45%, transparent);
}

.layout-thumb-canvas {
  position: relative;
  width: 100%;
  border-radius: 4px;
  background: #0a0c10;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.layout-thumb-canvas.shorts { aspect-ratio: 9 / 16; }
.layout-thumb-canvas.landscape { aspect-ratio: 16 / 9; }

.layout-thumb-video {
  position: absolute;
  display: block;
  border-radius: 2px;
  background: linear-gradient(135deg, #5b7cfa, #39d0ff 55%, #7dffb3);
  opacity: 0.92;
}

.layout-template-thumb em {
  font-style: normal;
  font-size: 0.68rem;
  font-weight: 650;
  color: #e2e8f0;
}

.layout-field {
  display: grid;
  gap: 3px;
  margin: 0 0 6px !important;
  font-size: 0.72rem;
  color: var(--muted);
}

.layout-field input[type='text'],
.layout-field select {
  width: 100%;
}

.layout-color-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 6px;
}

.layout-color-row label {
  display: grid;
  gap: 2px;
  font-size: 0.68rem;
  color: var(--muted);
  margin: 0 !important;
}

.layout-color-row input[type='color'] {
  width: 100%;
  height: 28px;
  padding: 0;
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 6px;
  background: transparent;
}

.layout-check {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0 0 !important;
  font-size: 0.75rem;
  color: #e2e8f0;
}

.shorts-safe-area {
  position: absolute;
  inset: 8% 7% 16%;
  border: 1px dashed rgba(255, 255, 255, 0.38);
  border-radius: 6px;
  pointer-events: none;
}

.preview-video-overlay,
.preview-image-overlay {
  position: absolute;
  z-index: 2;
  height: auto;
  pointer-events: none;
  border-radius: 4px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.45);
}

.preview-video-overlay.is-selected {
  outline: 2px solid #fb6b5b;
  outline-offset: -2px;
  box-shadow: 0 0 0 1px rgba(8, 12, 16, 0.9), 0 4px 18px rgba(0, 0, 0, 0.6);
}

.inspector-outside-notice {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  background: rgba(251, 107, 91, 0.12);
  border: 1px solid rgba(251, 107, 91, 0.35);
  border-radius: 8px;
  font-size: 0.74rem;
  color: #ff9d92;
}

.jump-to-layer-btn {
  align-self: flex-start;
  padding: 4px 10px;
  border-radius: 6px;
  background: #fb6b5b;
  color: #0b0d12;
  font-weight: 600;
  font-size: 0.72rem;
  border: none;
  cursor: pointer;
  transition: opacity 0.15s ease;
}

.jump-to-layer-btn:hover {
  opacity: 0.9;
}

.inspector-help {
  display: block;
  margin-top: 5px;
  color: var(--muted);
  font-size: 0.72rem;
  line-height: 1.35;
}

.preview-video-overlay.is-full-frame,
.preview-video-overlay.full-frame,
.preview-image-overlay.is-full-frame,
.preview-image-overlay.full-frame {
  inset: 0 !important;
  top: 0 !important;
  left: 0 !important;
  right: 0 !important;
  bottom: 0 !important;
  width: 100% !important;
  height: 100% !important;
  max-height: none !important;
  border-radius: 0 !important;
  box-shadow: none !important;
}

.preview-image-overlay.is-full-frame,
.preview-image-overlay.full-frame {
  transform: none !important;
}

.preview-video-overlay.center,
.preview-image-overlay.center {
  top: 50%;
  left: 50%;
  right: auto;
  bottom: auto;
  transform: translate(-50%, -50%);
}

.preview-video-overlay.top-left,
.preview-image-overlay.top-left {
  top: 8%;
  left: 8%;
  right: auto;
  bottom: auto;
  transform: none;
}

.preview-video-overlay.top-right,
.preview-image-overlay.top-right {
  top: 8%;
  right: 8%;
  left: auto;
  bottom: auto;
  transform: none;
}

.preview-video-overlay.bottom-left,
.preview-image-overlay.bottom-left {
  bottom: 8%;
  left: 8%;
  right: auto;
  top: auto;
  transform: none;
}

.preview-video-overlay.bottom-right,
.preview-image-overlay.bottom-right {
  bottom: 8%;
  right: 8%;
  left: auto;
  top: auto;
  transform: none;
}

.preview-video-overlay.top,
.preview-image-overlay.top {
  top: 8%;
  left: 50%;
  right: auto;
  bottom: auto;
  transform: translateX(-50%);
}

.preview-video-overlay.bottom,
.preview-image-overlay.bottom {
  bottom: 8%;
  left: 50%;
  right: auto;
  top: auto;
  transform: translateX(-50%);
}

.preview-text-overlay {
  position: absolute;
  z-index: 3;
  max-width: 82%;
  padding: 7px 11px;
  color: #fff;
  font-weight: 800;
  text-align: center;
  border-radius: 4px;
}

.preview-text-overlay.black { color: #111; }
.preview-text-overlay.small { font-size: 15px; }
.preview-text-overlay.medium { font-size: 22px; }
.preview-text-overlay.large { font-size: 31px; }
.preview-text-overlay.shadow { text-shadow: 0 2px 6px rgba(0, 0, 0, 0.85); }
.preview-text-overlay.translucent { background: rgba(0, 0, 0, 0.65); }
.preview-text-overlay.solid,
.preview-text-overlay.full-frame { background: #000; }
.preview-text-overlay.full-frame {
  inset: 0;
  max-width: none;
  display: grid;
  place-items: center;
}

.preview-text-overlay.top, .preview-text-overlay.top-left, .preview-text-overlay.top-right { top: 8%; }
.preview-text-overlay.bottom, .preview-text-overlay.bottom-left, .preview-text-overlay.bottom-right { bottom: 10%; }
.preview-text-overlay.center { top: 50%; left: 50%; transform: translate(-50%, -50%); }
.preview-text-overlay.top, .preview-text-overlay.bottom { left: 50%; transform: translateX(-50%); }
.preview-text-overlay.top-left, .preview-text-overlay.bottom-left { left: 7%; }
.preview-text-overlay.top-right, .preview-text-overlay.bottom-right { right: 7%; }

/* ==========================================================================
   PROGRAM MONITOR TRANSPORT CONTROLS
   ========================================================================== */
.preview-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: none;
  margin-top: auto;
  padding: 12px 0 0;
  border-top: 0;
  background: transparent;
  flex-wrap: wrap;
}

.transport-cluster,
.transport-secondary {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--chrome-bg);
}

.transport-btn {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border: 1px solid transparent;
  border-radius: 7px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.14s ease;
}

.transport-btn:hover:not(:disabled) {
  background: var(--fill-muted-hover);
  color: var(--text-primary);
}

.transport-btn:disabled {
  opacity: 0.35;
  cursor: default;
}

.transport-btn.play-btn {
  width: 34px;
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
  box-shadow: 0 2px 8px rgba(235, 101, 84, 0.3);
}

.transport-btn.play-btn:hover:not(:disabled) {
  background: var(--accent-hover);
  border-color: var(--accent-hover);
  color: #fff;
}

.transport-btn.active {
  background: var(--accent-soft);
  border-color: rgba(235, 101, 84, 0.35);
  color: var(--accent);
}

.transport-btn.muted {
  color: var(--danger);
  background: var(--danger-soft);
}

.preview-time {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 5px 10px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--chrome-bg);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.preview-time strong {
  color: var(--text-primary);
  font-weight: 700;
}

.preview-time span {
  color: var(--muted);
  font-size: 11px;
}

.preview-active-clip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  max-width: 200px;
  padding: 4px 9px;
  border-radius: 999px;
  background: var(--fill-muted);
  border: 1px solid var(--line-subtle);
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.preview-active-clip em {
  font-style: normal;
  font-weight: 700;
  color: var(--accent);
}

.preview-shortcut {
  margin-left: auto;
  color: var(--muted);
  font-size: 11px;
  white-space: nowrap;
}

/* ==========================================================================
   INSPECTOR PANEL & VISUAL TRIM RANGE BAR
   ========================================================================== */
.timeline-inspector {
  min-width: 0;
  min-height: 0;
  box-sizing: border-box;
  padding: 14px 16px;
  background: var(--chrome-bg);
  color: var(--ink);
  overflow-x: hidden;
  overflow-y: auto;
}

.inspector-header {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-bottom: 14px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}

.inspector-eyebrow {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
}

.inspector-header strong {
  font-size: 14px;
  line-height: 1.35;
  color: var(--text-primary);
  word-break: break-word;
}

.timeline-inspector label {
  display: grid;
  min-width: 0;
  gap: 5px;
  margin: 10px 0;
  font-size: 11px;
  font-weight: 650;
  color: var(--muted);
}

.timeline-inspector input:not([type="range"]),
.timeline-inspector select,
.timeline-inspector textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 9px;
  background: var(--input-bg);
  color: var(--ink);
  font-size: 12px;
  transition: border-color 0.14s ease;
}

.timeline-inspector input:not([type="range"]):focus,
.timeline-inspector select:focus,
.timeline-inspector textarea:focus {
  outline: none;
  border-color: var(--accent);
}

.timeline-inspector input[type="range"] {
  width: 100%;
  padding: 0;
  min-height: 22px;
  border: 0;
  background: transparent;
  accent-color: var(--accent);
  cursor: pointer;
}

.trim-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  color: var(--text-secondary);
}

.trim-heading span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.trim-heading button,
.trim-actions button {
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--fill-muted);
  color: var(--ink);
  padding: 5px 9px;
  font-size: 11px;
  font-weight: 650;
  cursor: pointer;
  transition: all 0.14s ease;
}

.trim-heading button:hover,
.trim-actions button:hover {
  background: var(--fill-muted-hover);
  border-color: var(--line-emphasis);
}

.trim-range-visualizer {
  margin: 10px 0 8px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--input-bg);
}

.trim-range-track {
  position: relative;
  height: 16px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.06);
  overflow: hidden;
}

.trim-range-fill {
  position: absolute;
  top: 0;
  bottom: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-radius: 4px;
  background: rgba(235, 101, 84, 0.28);
  border: 1px solid var(--accent);
  box-shadow: inset 0 0 8px rgba(235, 101, 84, 0.2);
}

.trim-edge-cap {
  width: 3px;
  height: 10px;
  border-radius: 2px;
  background: #fff;
  margin: 0 2px;
  opacity: 0.85;
}

.trim-range-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
  font-size: 10px;
  font-variant-numeric: tabular-nums;
  color: var(--muted);
}

.trim-range-meta strong {
  color: var(--accent);
  font-weight: 700;
}

.trim-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin-top: 6px;
}

.inspector-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  margin-top: 10px;
  padding: 7px 10px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--fill-muted);
  color: var(--ink);
  font-size: 11px;
  font-weight: 650;
  cursor: pointer;
  transition: all 0.15s ease;
}

.inspector-action-btn:hover {
  background: var(--accent-soft);
  border-color: rgba(235, 101, 84, 0.4);
  color: var(--accent);
}

.inspector-action-btn.primary-action-btn {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
  font-weight: 700;
}

.inspector-action-btn.primary-action-btn:hover:not(:disabled) {
  filter: brightness(1.1);
  color: #fff;
}

.inspector-action-btn.danger-action-btn {
  background: rgba(220, 38, 38, 0.1);
  border-color: rgba(220, 38, 38, 0.35);
  color: #ef4444;
}

.inspector-action-btn.danger-action-btn:hover {
  background: rgba(220, 38, 38, 0.18);
  border-color: #ef4444;
  color: #ef4444;
}

.multi-select-inspector {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 8px;
}

.multi-select-summary-badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: 6px;
  background: var(--accent-soft);
  border: 1px solid rgba(235, 101, 84, 0.25);
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  width: fit-content;
}

.multi-select-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow-y: auto;
  padding-right: 2px;
}

.multi-select-item-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 6px;
  background: var(--input-bg);
  border: 1px solid var(--line);
  font-size: 12px;
}

.multi-select-item-chip .chip-kind-badge {
  font-size: 9px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--fill-muted);
  color: var(--text-secondary);
}

.multi-select-item-chip .chip-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
  color: var(--ink);
}

.multi-select-item-chip .chip-remove {
  background: transparent;
  border: 0;
  color: var(--muted);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  padding: 2px 4px;
  border-radius: 3px;
  transition: all 0.12s ease;
}

.multi-select-item-chip .chip-remove:hover {
  background: var(--fill-muted-hover);
  color: var(--text-primary);
}

.multi-select-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 4px;
}

.multi-select-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  border-radius: 6px;
  background: var(--accent-soft);
  border: 1px solid rgba(235, 101, 84, 0.35);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
  animation: fadeIn 0.15s ease-out;
}

.multi-select-pill-count {
  font-weight: 750;
}

.multi-select-pill-summary {
  opacity: 0.85;
  font-size: 10px;
}

.multi-select-pill-clear {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 2px;
  border: none;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  border-radius: 3px;
  opacity: 0.8;
  min-height: auto;
  min-width: auto;
}

.multi-select-pill-clear:hover {
  opacity: 1;
  background: rgba(235, 101, 84, 0.2);
}

.inspector-audio-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.inspector-audio-row .inspector-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 700;
  color: var(--muted);
}

.audio-toggle-action {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--fill-muted);
  color: var(--ink);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.audio-toggle-action:hover {
  background: var(--fill-muted-hover);
  border-color: var(--line-emphasis);
}

.audio-toggle-action.muted {
  background: var(--danger-soft);
  border-color: rgba(239, 106, 106, 0.4);
  color: var(--danger);
}

.inspector-section {
  min-width: 0;
  margin-top: 14px;
  border-top: 1px solid var(--line);
  padding-top: 12px;
}

.inspector-section summary {
  cursor: pointer;
  font-size: 12px;
  font-weight: 650;
  color: var(--text-secondary);
}

.inspector-section-summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  user-select: none;
  list-style: none;
}

.inspector-section-summary::-webkit-details-marker {
  display: none;
}

.inspector-section-summary > span::before {
  content: '▾';
  display: inline-block;
  margin-right: 5px;
  font-size: 10px;
  color: var(--muted);
  transition: transform 0.14s ease;
}

details.inspector-section:not([open]) .inspector-section-summary > span::before {
  transform: rotate(-90deg);
}

.framing-inline-reset {
  padding: 2px 7px;
  border-radius: 5px;
  border: 1px solid rgba(235, 101, 84, 0.35);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 10px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.14s ease;
}

.framing-inline-reset:hover {
  background: var(--accent);
  color: #fff;
}

.framing-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
  min-width: 0;
}

.inspector-section-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.lut-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 8px;
  min-width: 0;
}

.lut-description {
  font-size: 11px;
  line-height: 1.35;
  color: var(--muted);
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid var(--line-subtle);
}

.framing-fit-label {
  margin: 0 !important;
}

.framing-fit-control {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.framing-fit-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.framing-fit-title {
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}

.framing-apply-all-btn {
  background: transparent;
  border: 1px solid var(--line);
  border-radius: 4px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  cursor: pointer;
  transition: all 0.14s ease;
}

.framing-apply-all-btn:hover {
  color: var(--text);
  border-color: var(--accent);
  background: var(--accent-soft);
}

.framing-fit-segmented {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.framing-fit-segmented.mini {
  margin-top: 5px;
}

.framing-fit-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 7px 10px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.025);
  color: var(--text-secondary);
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.14s ease;
  user-select: none;
}

.framing-fit-btn:hover {
  border-color: var(--line-strong, #3a4150);
  color: var(--text);
}

.framing-fit-btn.active {
  border-color: var(--accent) !important;
  background: var(--accent-soft) !important;
  color: var(--accent) !important;
}

.fit-btn-glyph {
  position: relative;
  display: inline-block;
  width: 13px;
  height: 17px;
  border-radius: 2px;
  border: 1px solid rgba(255, 255, 255, 0.4);
  flex-shrink: 0;
  overflow: hidden;
}

.framing-fit-btn.active .fit-btn-glyph {
  border-color: var(--accent);
}

.fit-btn-glyph.fill i {
  position: absolute;
  inset: 0;
  background: currentColor;
  opacity: 0.85;
}

.fit-btn-glyph.boxed i {
  position: absolute;
  left: 1px;
  right: 1px;
  top: 4px;
  bottom: 4px;
  background: currentColor;
  opacity: 0.85;
  border-radius: 1px;
}

.framing-fit-subtext {
  font-size: 10.5px;
  color: var(--muted);
  line-height: 1.35;
}

.layout-fit-global {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}

.layout-fit-global-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.layout-fit-status-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--fill-muted);
  color: var(--muted);
}

.layout-fit-status-badge.contain {
  background: var(--accent-soft);
  color: var(--accent);
}

.layout-fit-status-badge.cover {
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-secondary);
}

.layout-fit-status-badge.mixed {
  background: rgba(234, 179, 8, 0.15);
  color: #eab308;
}

.framing-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.025);
  min-width: 0;
}

.framing-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
}

.framing-crop-readout {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--fill-muted);
  border: 1px solid var(--line-subtle);
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.framing-slider-field {
  margin: 0 !important;
  gap: 2px !important;
  min-width: 0;
}

.framing-field-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  font-size: 10.5px;
  font-weight: 650;
  color: var(--muted);
}

.framing-field-head b {
  font-size: 10.5px;
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.framing-slider-field input[type="range"] {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  min-height: 18px;
  height: 18px;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  outline: none;
  cursor: pointer;
  /* Disable native left-fill so bipolar (center=0) tracks aren't misleading. */
  accent-color: transparent;
}

.framing-slider-field input[type="range"]::-webkit-slider-runnable-track {
  height: 4px;
  border-radius: 999px;
  background: linear-gradient(
    to right,
    rgba(255, 255, 255, 0.12) 0%,
    rgba(255, 255, 255, 0.12) var(--range-fill-start, 0%),
    var(--accent) var(--range-fill-start, 0%),
    var(--accent) var(--range-fill-end, 0%),
    rgba(255, 255, 255, 0.12) var(--range-fill-end, 0%),
    rgba(255, 255, 255, 0.12) 100%
  );
}

.framing-slider-field.bipolar input[type="range"]::-webkit-slider-runnable-track {
  background:
    linear-gradient(
      to right,
      transparent calc(var(--range-mid, 50%) - 0.5px),
      rgba(255, 255, 255, 0.45) calc(var(--range-mid, 50%) - 0.5px),
      rgba(255, 255, 255, 0.45) calc(var(--range-mid, 50%) + 0.5px),
      transparent calc(var(--range-mid, 50%) + 0.5px)
    ),
    linear-gradient(
      to right,
      rgba(255, 255, 255, 0.12) 0%,
      rgba(255, 255, 255, 0.12) var(--range-fill-start, 50%),
      var(--accent) var(--range-fill-start, 50%),
      var(--accent) var(--range-fill-end, 50%),
      rgba(255, 255, 255, 0.12) var(--range-fill-end, 50%),
      rgba(255, 255, 255, 0.12) 100%
    );
}

.framing-slider-field input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 14px;
  height: 14px;
  margin-top: -5px;
  border: 2px solid var(--chrome-bg, #1a1d1b);
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.45);
}

.framing-slider-field input[type="range"]::-moz-range-track {
  height: 4px;
  border: 0;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
}

.framing-slider-field input[type="range"]::-moz-range-progress {
  height: 4px;
  border: 0;
  border-radius: 999px;
  background: var(--accent);
}

.framing-slider-field.bipolar input[type="range"]::-moz-range-progress {
  background: transparent;
}

.framing-slider-field input[type="range"]::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border: 2px solid var(--chrome-bg, #1a1d1b);
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.45);
}

.framing-grid-2col,
.crop-edges-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 10px;
  min-width: 0;
}

.crop-edges-grid {
  padding-top: 6px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.crop-preset-grid {
  display: grid !important;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px !important;
  min-width: 0;
}

.focal-preset-grid {
  display: grid !important;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 5px !important;
  margin-top: 6px;
  min-width: 0;
}

.focal-preset-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 6px !important;
  font-size: 11px !important;
  font-weight: 600;
  border-radius: 5px;
  border: 1px solid var(--line);
  background: var(--fill-muted);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.12s ease;
}

.focal-preset-btn:hover {
  background: var(--fill-muted-hover);
  color: var(--text-primary);
}

.focal-preset-btn.active {
  border-color: var(--accent) !important;
  background: var(--accent-soft) !important;
  color: var(--accent) !important;
}

.crop-preset-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-width: 0;
  padding: 5px 6px !important;
  font-size: 10.5px !important;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.crop-preset-btn.active {
  border-color: var(--accent) !important;
  background: var(--accent-soft) !important;
  color: var(--accent) !important;
}

.crop-glyph {
  position: relative;
  display: inline-block;
  width: 12px;
  height: 9px;
  border-radius: 2px;
  border: 1px solid rgba(255, 255, 255, 0.35);
  flex-shrink: 0;
  overflow: hidden;
}

.crop-preset-btn.active .crop-glyph {
  border-color: var(--accent);
}

.crop-glyph i {
  position: absolute;
  background: currentColor;
  opacity: 0.75;
}

.crop-glyph.full i {
  inset: 1px;
}

.crop-glyph.center i {
  inset: 2px 3px;
}

.crop-glyph.left i {
  inset: 1px 50% 1px 1px;
}

.crop-glyph.right i {
  inset: 1px 1px 1px 50%;
}

.crop-glyph.top i {
  inset: 1px 1px 50% 1px;
}

.crop-glyph.bottom i {
  inset: 50% 1px 1px 1px;
}

.framing-reset-btn {
  width: 100%;
  padding: 6px 10px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--fill-muted);
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 650;
  cursor: pointer;
  transition: all 0.14s ease;
}

.framing-reset-btn:hover:not(:disabled) {
  background: var(--fill-muted-hover);
  border-color: var(--line-emphasis);
  color: var(--text-primary);
}

.framing-reset-btn:disabled {
  opacity: 0.4;
  cursor: default;
}

.position-body {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.nudge-pad {
  display: grid;
  grid-template-columns: repeat(3, 26px);
  grid-template-rows: repeat(3, 26px);
  gap: 3px;
  width: 84px;
  height: 84px;
  flex-shrink: 0;
}

.nudge-btn {
  width: 26px;
  height: 26px;
  min-width: 26px !important;
  min-height: 26px !important;
  max-width: 26px;
  max-height: 26px;
  box-sizing: border-box;
  position: static !important;
  top: auto !important;
  left: auto !important;
  right: auto !important;
  bottom: auto !important;
  transform: none !important;
  margin: 0;
  display: grid;
  place-items: center;
  padding: 0 !important;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: var(--fill-muted);
  color: var(--text-secondary);
  cursor: pointer;
  transition: background 0.12s ease, border-color 0.12s ease, color 0.12s ease;
}

.nudge-btn:hover {
  background: var(--fill-muted-hover);
  border-color: var(--line-emphasis);
  color: var(--text-primary);
}

.nudge-btn:active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

.nudge-btn.nudge-up { grid-column: 2; grid-row: 1; }
.nudge-btn.nudge-left { grid-column: 1; grid-row: 2; }
.nudge-btn.nudge-center { grid-column: 2; grid-row: 2; color: var(--muted); }
.nudge-btn.nudge-right { grid-column: 3; grid-row: 2; }
.nudge-btn.nudge-down { grid-column: 2; grid-row: 3; }

.position-sliders {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

/* ==========================================================================
   TIMELINE TOOLBAR & ADD MENU POPOVER
   ========================================================================== */
.timeline-toolbar {
  display: flex;
  flex: none;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--chrome-bg);
  overflow: visible;
}

.timeline-toolbar > span {
  width: 1px;
  height: 22px;
  margin: 0 2px;
  background: var(--line);
}

.timeline-toolbar button,
.tool-upload {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 30px;
  min-width: 30px;
  padding: 0 9px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--fill-muted);
  color: var(--ink);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  cursor: pointer;
  transition: all 0.14s ease;
}

.timeline-toolbar button:hover:not(:disabled),
.tool-upload:hover {
  border-color: var(--line-emphasis);
  background: var(--fill-muted-hover);
  color: var(--text-primary);
}

.timeline-toolbar button:disabled {
  opacity: 0.35;
  cursor: default;
}

.timeline-toolbar button.active {
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent);
}

.add-shot-toolbar-btn {
  background: var(--accent-soft);
  border-color: rgba(235, 101, 84, 0.35);
  color: var(--accent);
  font-weight: 700;
}

.add-shot-toolbar-btn:hover:not(:disabled) {
  background: rgba(235, 101, 84, 0.22);
  border-color: var(--accent);
  color: #fff;
}

.item-muted-active {
  background: var(--danger-soft);
  color: var(--danger);
  border-color: rgba(239, 106, 106, 0.45);
}

.danger-tool {
  color: var(--danger);
}

.danger-tool:hover:not(:disabled) {
  background: var(--danger-soft);
  border-color: rgba(239, 106, 106, 0.45);
  color: var(--danger);
}

.add-menu {
  position: relative;
}

.add-menu summary {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 30px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--fill-muted);
  color: var(--ink);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  list-style: none;
  transition: all 0.14s ease;
}

.add-menu summary:hover {
  border-color: var(--line-emphasis);
  background: var(--fill-muted-hover);
}

.add-menu summary::-webkit-details-marker {
  display: none;
}

.add-menu-content {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  z-index: 30;
  display: grid;
  gap: 4px;
  min-width: 230px;
  padding: 8px;
  border: 1px solid var(--line-emphasis);
  border-radius: 12px;
  background: var(--chrome-bg);
  box-shadow: var(--shadow-float);
}

.add-menu-content button,
.add-menu-content .tool-upload {
  justify-content: flex-start;
  width: 100%;
  padding: 0 10px;
  font-size: 12px;
  border-color: transparent;
  background: transparent;
}

.add-menu-content button:hover,
.add-menu-content .tool-upload:hover {
  background: var(--fill-muted-hover);
  border-color: var(--line);
}

.tool-upload {
  position: relative;
}

.tool-upload:focus-within {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.tool-upload input {
  display: block;
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

/* ZOOM CONTROLS */
.zoom-toolbar-group {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 2px 6px;
  background: var(--fill-muted);
  border-radius: 7px;
  border: 1px solid var(--line);
}

.zoom-toolbar-group button {
  min-height: 24px;
  min-width: 24px;
  padding: 2px 5px;
  background: transparent;
  border: none;
}

.zoom-slider-input {
  width: 72px;
  height: 4px;
  cursor: pointer;
  accent-color: var(--accent);
  margin: 0 2px;
}

.zoom-badge-button {
  min-height: 22px;
  padding: 0 7px;
  border: 1px solid var(--line);
  border-radius: 5px;
  background: var(--surface-2);
  color: var(--text-primary);
  font-size: 10px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.zoom-badge-button.is-fit {
  color: var(--muted);
}

.reset-tool {
  margin-left: 0;
}

/* ==========================================================================
   TIMELINE RULER, PLAYHEAD & MULTI-TRACK LANES
   ========================================================================== */
.timeline-scroll {
  flex: 0 0 auto;
  max-height: 42vh;
  overflow: auto;
  overscroll-behavior: contain;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.015), transparent 42px),
    var(--preview-well);
}

.timeline-scroll .playhead-scrubber {
  display: grid;
  grid-template-columns: 70px var(--track-width, 1000px);
  align-items: center;
  gap: 0;
  min-width: var(--full-track-width, 1106px);
  padding: 28px 18px 6px;
  background: var(--chrome-bg);
  border-bottom: 1px solid var(--line);
  color: var(--ink);
  font-size: 11px;
  overflow: visible;
}

.playhead-scrubber > strong {
  position: sticky;
  left: 0;
  z-index: 6;
  display: flex;
  align-items: center;
  align-self: start;
  padding: 2px 8px 0;
  background: var(--chrome-bg);
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.05em;
  white-space: nowrap;
}

.timeline-range {
  position: relative;
  width: var(--track-width, 1000px);
  min-width: var(--track-width, 1000px);
  height: 44px;
}

.timeline-editor input[type="range"],
.timeline-editor input[type="range"]:hover,
.timeline-editor input[type="range"]:focus,
.timeline-editor input[type="range"]:focus-visible {
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  outline: none;
}

.timeline-input {
  -webkit-appearance: none;
  appearance: none;
  position: absolute;
  inset: 0 0 auto;
  width: 100%;
  height: 20px;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
  outline: none;
  cursor: ew-resize;
  z-index: 3;
}

.timeline-input::-webkit-slider-runnable-track {
  height: 6px;
  border-radius: 3px;
  background: linear-gradient(
    to right,
    var(--accent) 0 var(--playhead),
    var(--line-emphasis) var(--playhead) 100%
  );
}

.timeline-input::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 14px;
  height: 20px;
  margin-top: -7px;
  border: 2px solid var(--chrome-bg);
  border-radius: 4px;
  background: var(--accent);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.45);
  transition: transform 0.12s ease, box-shadow 0.12s ease;
}

.timeline-input:hover::-webkit-slider-thumb {
  transform: scale(1.1);
}

.timeline-input:focus-visible::-webkit-slider-thumb {
  box-shadow: 0 0 0 2px rgba(235, 101, 84, 0.35), 0 2px 6px rgba(0, 0, 0, 0.45);
}

.timeline-input::-moz-range-track {
  height: 6px;
  border-radius: 3px;
  background: var(--line-emphasis);
}

.timeline-input::-moz-range-progress {
  height: 6px;
  border-radius: 3px;
  background: var(--accent);
}

.timeline-input::-moz-range-thumb {
  width: 14px;
  height: 20px;
  border: 2px solid var(--chrome-bg);
  border-radius: 4px;
  background: var(--accent);
}

.playhead-time {
  position: absolute;
  top: -8px;
  z-index: 4;
  transform: translate(-50%, -100%);
  padding: 3px 7px;
  border-radius: 6px;
  background: var(--accent);
  color: #fff;
  font-size: 10px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  line-height: 1.2;
  white-space: nowrap;
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.35);
  pointer-events: none;
}

.playhead-time::after {
  position: absolute;
  left: 50%;
  bottom: -3px;
  width: 6px;
  height: 6px;
  transform: translateX(-50%) rotate(45deg);
  background: var(--accent);
  content: "";
}

.timeline-ticks {
  position: absolute;
  top: 22px;
  left: 0;
  width: 100%;
  height: 22px;
  pointer-events: none;
}

.timeline-ticks span {
  position: absolute;
  top: 0;
  transform: translateX(-50%);
  color: var(--muted);
  font-size: 9px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.timeline-ticks span:first-child {
  transform: none;
}

.timeline-ticks span:last-child {
  transform: translateX(-100%);
}

.timeline-ticks i {
  display: block;
  width: 1px;
  height: 6px;
  margin: 0 auto 2px;
  background: var(--line-emphasis);
}

.timeline-ticks em {
  font-style: normal;
}

.timeline-scroll .tracks {
  min-width: var(--full-track-width, 1106px);
  padding: 8px 18px 18px;
  background: var(--preview-well);
  overflow: visible;
}

.track-row {
  display: grid;
  grid-template-columns: 82px var(--track-width, 1000px);
  min-height: 38px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.primary-track {
  min-height: 74px;
}

.track-row > strong,
.track-header {
  position: sticky;
  left: 0;
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 8px;
  background: var(--preview-well);
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.05em;
}

.track-header strong {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: var(--muted);
}

.track-header-actions {
  display: flex;
  align-items: center;
  gap: 3px;
}

.track-action-btn,
.track-action-upload {
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: var(--text-secondary);
  cursor: pointer;
  padding: 0;
  transition: all 0.15s ease;
}

.track-action-upload input,
.empty-track-cta input {
  display: none;
}

.track-action-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.track-action-btn:hover,
.track-action-btn.active,
.track-action-upload:hover {
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  border-color: rgba(255, 255, 255, 0.25);
}

.audio-library-anchor {
  position: relative;
  display: inline-flex;
}

.track-row.video-track-row.drag-over .overlay-track,
.track-row.image-track-row.drag-over .overlay-track,
.track-row.audio-track-row.drag-over .overlay-track {
  background-color: rgba(56, 189, 248, 0.15) !important;
  outline: 1.5px dashed #38bdf8;
  outline-offset: -1px;
}

.block-audio-toggle,
.overlay-audio-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 2px 3px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.28);
  color: rgba(255, 255, 255, 0.85);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease, transform 0.12s ease;
}

.block-audio-toggle:hover,
.overlay-audio-toggle:hover {
  background: rgba(255, 255, 255, 0.2);
  color: #fff;
  transform: scale(1.1);
}

.block-audio-toggle.muted,
.overlay-audio-toggle.muted {
  background: rgba(239, 106, 106, 0.22);
  color: #fca5a5;
}

.empty-track-cta {
  position: absolute;
  top: 7px;
  left: 10px;
  z-index: 2;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 9px;
  border-radius: 5px;
  border: 1px dashed rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.02);
  color: var(--muted);
  font-size: 10px;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}

.empty-track-cta:hover {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.26);
  color: var(--text-primary);
}

.overlay-thumb {
  width: 20px;
  height: 20px;
  border-radius: 3px;
  object-fit: cover;
  flex-shrink: 0;
  margin-right: 2px;
  border: 1px solid rgba(255, 255, 255, 0.18);
}

.inspector-image-preview {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  margin-bottom: 8px;
}

.inspector-image-preview img,
.inspector-image-preview video {
  width: 44px;
  height: 44px;
  border-radius: 5px;
  object-fit: cover;
  border: 1px solid rgba(255, 255, 255, 0.15);
}

.inspector-layer-order {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin: 8px 0 6px;
}

.inspector-layer-buttons {
  display: flex;
  gap: 6px;
}

.inspector-image-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.inspector-image-meta strong {
  font-size: 12px;
  color: var(--text-primary);
}

.inspector-image-meta span {
  font-size: 10px;
  color: var(--text-tertiary);
}

.inspector-quick-durations {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: -2px;
  margin-bottom: 6px;
}

.quick-dur-label {
  font-size: 11px;
  color: var(--text-tertiary);
  font-weight: 600;
}

.quick-dur-buttons {
  display: flex;
  gap: 6px;
}

.quick-dur-btn {
  flex: 1;
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 5px;
  border: 1px solid var(--line);
  background: var(--surface-secondary);
  color: var(--text-primary);
  cursor: pointer;
  transition: all 0.15s ease;
}

.quick-dur-btn:hover {
  background: var(--surface-hover);
  border-color: var(--line-hover);
}

.add-image-card {
  max-width: 580px;
}

.add-image-modal-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.add-image-modal-toolbar .add-shot-search {
  flex: 1;
}

.add-image-upload-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.14);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}

.add-image-upload-btn:hover {
  background: rgba(255, 255, 255, 0.14);
  border-color: rgba(255, 255, 255, 0.25);
}

.add-image-upload-btn input {
  display: none;
}

.modal-section-title {
  padding: 8px 12px 4px;
  font-size: 10.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-tertiary);
}

.track-content,
.overlay-track {
  position: relative;
  width: var(--track-width, 1000px);
  min-width: var(--track-width, 1000px);
  background-image: linear-gradient(to right, rgba(255, 255, 255, 0.025) 1px, transparent 1px);
  background-size: 48px 100%;
}

.track-content {
  position: relative;
  min-height: 74px;
  height: 74px;
  overflow: visible;
}

.overlay-track {
  display: block;
  background-color: rgba(255, 255, 255, 0.018);
}

.track-playhead {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 5;
  width: 2px;
  transform: translateX(-1px);
  background: var(--accent);
  box-shadow: 0 0 0 1px rgba(235, 101, 84, 0.15), 0 0 12px rgba(235, 101, 84, 0.35);
  pointer-events: none;
}

/* TRANSITIONS */
.transition-marker {
  position: absolute;
  top: 50%;
  z-index: 5;
  width: 16px;
  height: 16px;
  padding: 0;
  transform: translate(-50%, -50%);
  border: 1px solid rgba(255, 255, 255, 0.35);
  border-radius: 4px;
  background: rgba(10, 12, 18, 0.85);
  color: rgba(255, 255, 255, 0.4);
  font-size: 9px;
  line-height: 14px;
  cursor: pointer;
}
.transition-marker:hover,
.transition-marker.open {
  border-color: #fff;
  color: #fff;
}
.transition-marker.active {
  border-color: #f5c84b;
  background: #f5c84b;
  color: #1a1404;
}
.transition-marker.active.overridden {
  box-shadow: 0 0 0 2px rgba(245, 200, 75, 0.35);
}
.transition-popover {
  position: absolute;
  top: calc(50% + 14px);
  z-index: 30;
  display: grid;
  gap: 6px;
  width: 190px;
  padding: 8px;
  transform: translateX(-50%);
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 8px;
  background: #151821;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
  color: #eef0f4;
  font-size: 11px;
}
.transition-popover select {
  width: 100%;
  padding: 4px 6px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 5px;
  background: #0d0f15;
  color: inherit;
  font-size: 11px;
}
.transition-popover header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
}
.transition-popover header button {
  padding: 2px;
  border: 0;
  background: none;
  color: inherit;
  cursor: pointer;
}
.transition-popover label {
  display: grid;
  gap: 4px;
}
.transition-in-row {
  display: grid;
  gap: 6px;
}

/* PRIMARY FILMSTRIP BLOCKS */
.timeline-block {
  position: absolute;
  top: 4px;
  bottom: 4px;
  box-sizing: border-box;
  container-type: inline-size;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 5px 6px;
  border: 1px solid var(--line-default);
  border-radius: 6px;
  background: linear-gradient(180deg, #212a31 0%, #171e24 100%);
  color: #f4f6f8;
  text-align: left;
  overflow: hidden;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.06);
  transition: border-color 0.14s ease, filter 0.14s ease, transform 0.12s ease, opacity 0.14s ease;
}

@container (max-width: 68px) {
  .block-kind-label {
    display: none;
  }
}

@container (max-width: 46px) {
  .block-grip {
    display: none;
  }
  .timeline-block small {
    display: none;
  }
}

.timeline-block.image {
  background: linear-gradient(180deg, #332c21 0%, #241f17 100%);
}

.timeline-block.title {
  background: linear-gradient(180deg, #312334 0%, #221824 100%);
}

.timeline-block:hover {
  border-color: var(--line-emphasis);
  filter: brightness(1.08);
}

.timeline-block.selected,
.overlay-block.selected {
  z-index: 4;
  border-color: var(--accent);
  outline: 2px solid var(--accent);
  outline-offset: -2px;
  box-shadow: 0 0 0 2px rgba(235, 101, 84, 0.16), 0 6px 16px rgba(0, 0, 0, 0.32);
}

.timeline-block.is-muted {
  border-bottom: 2px dashed var(--danger);
}

.timeline-block.is-dragging {
  opacity: 0.45;
  transform: scale(0.98);
  z-index: 10;
}

.timeline-block.is-drop-target {
  z-index: 4;
  outline: 2px solid var(--accent);
  outline-offset: -1px;
  box-shadow: 0 0 12px rgba(235, 101, 84, 0.45);
}

.block-thumb-bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  opacity: 0.28;
  pointer-events: none;
  filter: saturate(0.9) contrast(1.05);
}

.block-top,
.block-bottom {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  width: 100%;
  min-width: 0;
}

.block-kind-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(9, 12, 15, 0.65);
  backdrop-filter: blur(4px);
  font-size: 8px;
  font-weight: 800;
  letter-spacing: 0.04em;
  color: rgba(255, 255, 255, 0.85);
  overflow: hidden;
  white-space: nowrap;
}

.block-kind-pill em {
  font-style: normal;
  color: var(--accent);
}

.block-grip {
  opacity: 0.55;
  flex-shrink: 0;
}

.block-status-icons {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.block-trim-icon {
  color: #efc04f;
}

.block-muted-icon {
  color: var(--danger);
}

.block-bottom {
  margin-top: 8px;
  align-items: baseline;
}

.timeline-block b {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.8);
}

.timeline-block small {
  flex: none;
  font-size: 9px;
  font-variant-numeric: tabular-nums;
  opacity: 0.78;
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.8);
}

/* OVERLAY BLOCKS */
.overlay-block {
  position: absolute;
  top: 5px;
  height: 28px;
  min-width: 24px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 9px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 6px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: 9px;
  font-weight: 800;
  touch-action: none;
  cursor: grab;
  user-select: none;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.28);
}

.overlay-block:active {
  cursor: grabbing;
}

.overlay-block.video {
  background: linear-gradient(180deg, #2b5a7d 0%, #1c3d56 100%);
  color: #fff;
}

.overlay-block.video.is-muted {
  border-bottom: 2px dashed var(--danger);
}

.overlay-layer-badge {
  flex: none;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(9, 12, 15, 0.55);
  color: #7dd3fc;
  font-size: 8px;
  font-weight: 800;
  letter-spacing: 0.03em;
}

.overlay-block.image {
  background: linear-gradient(180deg, #326857 0%, #244d40 100%);
  color: #fff;
}

.overlay-block.text {
  background: linear-gradient(180deg, #8b6537 0%, #684b27 100%);
  color: #fff;
}

.overlay-block.audio {
  background: linear-gradient(180deg, #4b3d73 0%, #352a54 100%);
  color: #fff;
}

.overlay-block.audio.is-muted {
  opacity: 0.65;
  border-bottom: 2px dashed var(--danger);
}

.overlay-loop-icon {
  flex: 0 0 auto;
  opacity: 0.8;
}
.audio-loop-note {
  margin-top: -4px;
  color: rgba(255, 255, 255, 0.55);
  font-size: 11px;
}
.overlay-audio-icon {
  flex: none;
  opacity: 0.88;
}

.overlay-audio-icon.muted {
  color: var(--danger);
}

.overlay-block.selected {
  z-index: 4;
}

.overlay-block .overlay-label {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: left;
  color: #fff;
}

.overlay-block small {
  flex: none;
  font-size: 8px;
  font-weight: 700;
  opacity: 0.8;
}

.resize-handle {
  position: absolute;
  top: 3px;
  bottom: 3px;
  width: 6px;
  border-radius: 2px;
  cursor: ew-resize;
  opacity: 0;
  transition: opacity 0.12s ease, background 0.12s ease;
}

.resize-handle.start { left: 2px; }
.resize-handle.end { right: 2px; }

.overlay-block:hover .resize-handle,
.overlay-block.selected .resize-handle {
  opacity: 1;
  background: rgba(255, 255, 255, 0.72);
}

.overlay-block.selected .resize-handle:hover {
  background: #fff;
}

.primary-resize-handle {
  z-index: 3;
  width: 8px;
}

.timeline-block:hover .primary-resize-handle,
.timeline-block.selected .primary-resize-handle {
  opacity: 1;
  background: rgba(255, 255, 255, 0.72);
}

.track-content.image-drop-active {
  outline: 1px dashed rgba(255, 255, 255, 0.45);
  outline-offset: -2px;
}

.image-drop-marker {
  position: absolute;
  top: 0;
  bottom: 0;
  z-index: 5;
  width: 3px;
  margin-left: -1px;
  border-radius: 2px;
  background: var(--accent, #39d0ff);
  pointer-events: none;
}

/* ==========================================================================
   EXPORT STATUS FOOTER & MODALS
   ========================================================================== */
.export-status {
  display: flex;
  flex: none;
  align-items: center;
  gap: 16px;
  padding: 8px 20px;
  border-top: 1px solid var(--line);
  background: var(--success-soft);
  color: var(--success);
  font-size: 12px;
}

.export-status em {
  margin-left: auto;
  font-style: normal;
  font-size: 11px;
  font-weight: 600;
}

.export-status.stale {
  background: var(--accent-soft);
  color: #ffb2a7;
}

.add-shot-backdrop,
.shortcuts-modal-backdrop,
.publish-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(0, 0, 0, 0.7);
  backdrop-filter: blur(5px);
  display: grid;
  place-items: center;
  padding: 20px;
}

.add-shot-card,
.shortcuts-card {
  width: 100%;
  max-width: 520px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  background: var(--surface-2);
  border: 1px solid var(--line-emphasis);
  border-radius: 12px;
  box-shadow: var(--shadow-float);
  padding: 18px;
  color: var(--text-primary);
}

.shortcuts-card {
  max-width: 440px;
}

.add-shot-card header,
.shortcuts-card header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--line);
}

.add-shot-title,
.shortcuts-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
  font-weight: 700;
}

.add-shot-search input {
  width: 100%;
  padding: 8px 11px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--input-bg);
  color: var(--text-primary);
  font-size: 12px;
  outline: none;
  margin-bottom: 12px;
}

.add-shot-search input:focus {
  border-color: var(--accent);
}

.add-shot-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  max-height: 420px;
  padding-right: 4px;
}

.add-shot-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--fill-muted);
  transition: all 0.12s ease;
}

.add-shot-row:hover {
  background: var(--fill-muted-hover);
  border-color: var(--line-emphasis);
}

.add-shot-thumb {
  width: 52px;
  height: 34px;
  border-radius: 5px;
  overflow: hidden;
  background: var(--preview-well);
  display: grid;
  place-items: center;
  color: var(--muted);
  flex-shrink: 0;
}

.add-shot-thumb video,
.add-shot-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.add-shot-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.add-shot-info strong {
  font-size: 12px;
  font-weight: 700;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.add-shot-info span {
  font-size: 10px;
  color: var(--muted);
}

.add-shot-insert-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid rgba(235, 101, 84, 0.35);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
}

.add-shot-insert-btn:hover {
  background: var(--accent);
  color: #fff;
}

.add-shot-insert-btn.secondary {
  border-color: var(--line);
  background: var(--fill-muted);
  color: var(--text-secondary);
}

.add-shot-insert-btn.secondary:hover {
  border-color: rgba(139, 108, 217, 0.55);
  background: rgba(75, 61, 115, 0.45);
  color: #fff;
}

.empty-shot-state {
  text-align: center;
  padding: 30px;
  color: var(--muted);
  font-size: 12px;
}

.shortcuts-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
}

.shortcut-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-secondary);
}

.shortcut-row kbd {
  display: inline-block;
  padding: 2px 6px;
  background: var(--surface-3);
  border: 1px solid var(--line-emphasis);
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10px;
  font-weight: 700;
  color: var(--accent);
}

.publish-modal-card {
  width: 100%;
  max-width: 720px;
  max-height: 90vh;
  overflow-y: auto;
  border-radius: 16px;
}

.publish-modal-card.release-kit-card {
  max-width: 980px;
}

.unresolved-notes-card {
  width: min(540px, calc(100vw - 32px));
  border: 1px solid var(--line-emphasis);
  border-radius: 16px;
  background: var(--surface-2);
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.5);
  padding: 22px;
  display: grid;
  gap: 18px;
}
.unresolved-notes-card header {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.unresolved-notes-card h2,
.unresolved-notes-card p {
  margin: 0;
}
.unresolved-notes-card header p {
  color: var(--muted);
  margin-top: 4px;
}
.unresolved-notes-icon {
  color: #f6c453;
  padding: 8px;
  border-radius: 10px;
  background: rgba(246, 196, 83, 0.12);
  display: flex;
  align-items: center;
  justify-content: center;
}
.unresolved-close-btn {
  margin-left: auto;
  border: none;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
}
.unresolved-close-btn:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.08);
}
.unresolved-notes-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}
.unresolved-shot-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--surface-3);
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.15s ease;
}
.unresolved-shot-item:hover {
  background: var(--surface-hover, rgba(255, 255, 255, 0.08));
  border-color: rgba(246, 196, 83, 0.25);
  transform: translateX(2px);
}
.unresolved-shot-item-main {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.unresolved-shot-item-main strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.unresolved-item-icon {
  color: #f6c453;
  flex-shrink: 0;
}
.unresolved-count-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #f6c453;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}
.unresolved-more {
  color: var(--muted);
  font-size: 12px;
  margin: 0;
}
.unresolved-notes-card footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.block-note-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #f6c453;
  margin-right: 3px;
}

.preview-frame-notice {
  margin-left: auto;
  font-size: 12px;
  color: var(--workspace-accent, #d9ff63);
}

.preview-frame-notice button {
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}

.timeline-editor button:focus-visible,
.timeline-editor summary:focus-visible,
.timeline-editor a:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

/* ==========================================================================
   RESPONSIVE BREAKPOINTS
   ========================================================================== */
@media (max-width: 1100px) {
  .editor-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .editor-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .preview-shortcut {
    display: none;
  }
}

@media (max-width: 900px) {
  .editor-main {
    grid-template-columns: minmax(0, 1fr) 250px;
    min-height: 340px;
  }
}

@media (max-width: 640px) {
  .timeline-editor {
    flex: none;
    overflow: visible;
  }

  .editor-main {
    display: flex;
    flex-direction: column;
    flex: none;
    min-height: 0;
  }

  .preview-pane {
    min-height: 320px;
    border-right: 0;
  }

  .preview-pane .program-monitor {
    flex: none;
    height: 240px;
    width: 100%;
  }

  .preview-pane .program-monitor.shorts {
    width: 135px;
    height: 240px;
  }

  .timeline-inspector {
    flex: none;
    max-height: 240px;
    border-left: 0;
    border-top: 1px solid var(--line);
  }

  .editor-header {
    padding: 12px;
  }

  .editor-actions {
    gap: 6px;
  }

  .variant-toggle {
    width: 100%;
  }

  .variant-toggle button {
    flex: 1;
  }

  .zoom-toolbar-group {
    margin-left: 0;
  }

  .timeline-scroll {
    flex: none;
  }
}
</style>
