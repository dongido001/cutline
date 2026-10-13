<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PublishPanel from '@/components/PublishPanel.vue'
import { api, DEFAULT_LUT_PRESETS, lutCssFilter, videoColorBoostFilter, type AspectRatio, type LUTPreset, type OutputVariant, type ProjectDetail, type ProjectTimeline, type ProjectVariantExport, type Shot, type TimelineAsset, type TimelineAudioOverlay, type TimelineGeometry, type TimelineImageOverlay, type TimelinePrimaryItem, type TimelineSource, type TimelineVideoOverlay } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import { AlignCenter, Boxes, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, CopyPlus, Crosshair, Download, Film, GripVertical, ImagePlus, Keyboard, Layers, LoaderCircle, MoveLeft, MoveRight, Music, Palette, Pause, Play, Plus, Redo2, RefreshCw, Repeat, Save, Scissors, Share2, SkipBack, SkipForward, Split, Trash2, Type, Undo2, Upload, Volume2, VolumeX, X, ZoomIn, ZoomOut } from '@lucide/vue'

const props = defineProps<{ project: ProjectDetail; shots: Shot[]; compilationId?: string }>()
const emit = defineEmits<{ variantChange: [variant: OutputVariant] }>()
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
const variant = ref<OutputVariant>(
  requestedVariant === 'shorts' || requestedVariant === 'landscape'
    ? requestedVariant
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
const dragSourceIndex = ref<number | null>(null)
const dragOverIndex = ref<number | null>(null)
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
type OverlayItem = { id: string; start_ms: number; duration_ms: number }
type OverlayEditMode = 'move' | 'resize-start' | 'resize-end'
let overlayEdit: {
  pointerId: number
  item: OverlayItem
  mode: OverlayEditMode
  startX: number
  trackWidth: number
  initialStart: number
  initialDuration: number
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
  if (typeof geom.lut !== 'string') geom.lut = 'none'
  if (typeof geom.lut_strength !== 'number') geom.lut_strength = 1
  return geom
}

const lutPresets = ref<LUTPreset[]>(DEFAULT_LUT_PRESETS)

const lutGroups = computed(() => {
  const groups: Record<string, LUTPreset[]> = {}
  for (const preset of lutPresets.value) {
    const cat = preset.category || 'General'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(preset)
  }
  return Object.entries(groups).map(([category, presets]) => ({ category, presets }))
})

function selectedLutPreset(lutId?: string): LUTPreset | undefined {
  if (!lutId || lutId === 'none') return undefined
  return lutPresets.value.find((p) => p.id === lutId)
}

function resetLut(geom: TimelineGeometry) {
  mutate(() => {
    geom.lut = 'none'
    geom.lut_strength = 1
  })
}

function setLutStrength(geom: TimelineGeometry, strength: number) {
  mutate(() => {
    geom.lut_strength = Math.max(0, Math.min(1, strength))
  })
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

/** Track fill style for framing ranges. `center` = bipolar fill from midpoint (position). */
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
    const mid = ((0 - min) / span) * 100
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

function geometryCropClipPath(geom: TimelineGeometry, forceClipForZoom = false): string | undefined {
  let cTop = Math.max(0, Math.min(0.85, Number(geom.crop_top ?? 0) || 0))
  let cBottom = Math.max(0, Math.min(0.85, Number(geom.crop_bottom ?? 0) || 0))
  let cLeft = Math.max(0, Math.min(0.85, Number(geom.crop_left ?? 0) || 0))
  let cRight = Math.max(0, Math.min(0.85, Number(geom.crop_right ?? 0) || 0))
  const zoom = Math.max(1, Number(geom.zoom ?? 1) || 1)
  const hasCrop = cTop > 1e-4 || cBottom > 1e-4 || cLeft > 1e-4 || cRight > 1e-4
  if (!hasCrop && !(forceClipForZoom && zoom > 1.001)) {
    return undefined
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
  return `inset(${(localTop * 100).toFixed(2)}% ${(localRight * 100).toFixed(2)}% ${(localBottom * 100).toFixed(2)}% ${(localLeft * 100).toFixed(2)}%)`
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
const activeMediaStyle = computed(() => {
  const geom = activeGeometry.value
  const boostFilter = videoColorBoostFilter(activeMedia.value?.color_boost_strength)
  const lutFilter = lutCssFilter(geom.lut, geom.lut_strength)
  const filter = [boostFilter, lutFilter].filter(Boolean).join(' ')
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
    ...(filter ? { filter } : {}),
  }
})
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
  const clipPath = geometryCropClipPath(geom, !isFullFrame)
  const translate = geometryOffsetTranslate(geom)
  const zoom = Math.max(1, Number(geom.zoom ?? 1) || 1)
  if (isFullFrame) {
    return {
      opacity: overlay.opacity ?? 1,
      objectFit: (geom.fit || 'cover') as 'cover' | 'contain',
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

function packOverlayLanes<T extends { id: string; start_ms: number; duration_ms: number }>(items: T[]) {
  const laneEnds: number[] = []
  const laneById: Record<string, number> = {}
  for (const item of items) {
    let assigned = -1
    for (let i = 0; i < laneEnds.length; i++) {
      if ((laneEnds[i] ?? 0) <= item.start_ms) {
        assigned = i
        laneEnds[i] = item.start_ms + item.duration_ms
        break
      }
    }
    if (assigned < 0) {
      assigned = laneEnds.length
      laneEnds.push(item.start_ms + item.duration_ms)
    }
    laneById[item.id] = assigned
  }
  return {
    laneById,
    laneCount: Math.max(1, laneEnds.length),
  }
}

const videoOverlayPacking = computed(() => packOverlayLanes(videoOverlays.value))
const imageOverlayPacking = computed(() => packOverlayLanes(timeline.value?.document.image_overlays ?? []))

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
    timeline.value = fetched
    loadedProjectId = reqProjectId
    loadedEpisodeId = reqEpisodeId
    const trimShot = typeof route.query.trimShot === 'string' ? route.query.trimShot : null
    const requestedIndex = trimShot ? timeline.value.document.primary.findIndex((item) => item.source?.ref_id === trimShot && item.kind === 'video') : -1
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
    const trimShot = typeof route.query.trimShot === 'string' ? route.query.trimShot : null
    if (!trimShot && typeof persisted.scrollLeft === 'number' && persisted.scrollLeft > 0) {
      timelineScrollRef.value.scrollLeft = persisted.scrollLeft
      currentScrollLeft.value = persisted.scrollLeft
    } else if (zoomLevel.value > 1) {
      scrollPlayheadIntoView()
    }
  }
  syncVideo()
  persistUiState()
}

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
    if (item.kind === 'video') item.source_out_ms = item.source_in_ms + nextDuration
  })
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
  const sourceTime = Math.round(item.source_in_ms + local)
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

              const left = { ...item, duration_ms: leftDuration, source_in_ms: inMs, source_out_ms: leftSourceOut }
              const right = { ...item, id: crypto.randomUUID(), label: `${item.label} B`, source_in_ms: rightSourceIn, duration_ms: rightDuration, source_out_ms: rightSourceOut }
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
              const rightSourceIn = leftSourceIn + leftDuration

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

async function uploadAudioAsset(event: Event) {
  closeAddMenu()
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !timeline.value) return
  try {
    const probedMs = await probeFileDurationMs(file)
    const cleanLabel = file.name.replace(/\.[^.]+$/, '') || file.name
    await flushPendingSave()
    timeline.value = await api.uploadTimelineAsset(
      props.project.id,
      file,
      cleanLabel,
      variant.value,
      props.compilationId ? undefined : store.currentEpisodeId || undefined,
      'audio',
      props.compilationId,
    )
    timeline.value.document.video_overlays ??= []
    timeline.value.document.audio_overlays ??= []
    const asset = timeline.value.assets[timeline.value.assets.length - 1]
    if (asset) {
      addAudioOverlay({ kind: 'asset', ref_id: asset.id }, asset.label || cleanLabel, probedMs)
    }
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Could not upload audio'
  } finally {
    input.value = ''
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
  stopPlayback()
  if (event && (event.shiftKey || event.ctrlKey || event.metaKey)) {
    if (Date.now() - lastModifierToggleTime > 300 || lastModifierToggledId !== id) {
      lastModifierToggleTime = Date.now()
      lastModifierToggledId = id
      toggleSelectEntry(kind, id)
    }
    return
  }
  selectedKind.value = kind
  selectedId.value = id
  selectedEntries.value = [{ kind, id }]
  playheadMs.value = start
  syncVideo()
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
  stopPlayback()
  selectedKind.value = kind
  selectedId.value = item.id
  selectedEntries.value = [{ kind, id: item.id }]
  playheadMs.value = mode === 'resize-end' ? item.start_ms + item.duration_ms : item.start_ms
  overlayEdit = {
    pointerId: event.pointerId,
    item,
    mode,
    startX: event.clientX,
    trackWidth: track.getBoundingClientRect().width,
    initialStart: item.start_ms,
    initialDuration: item.duration_ms,
    before: snapshot(),
    moved: false,
  }
  window.addEventListener('pointermove', updateOverlayEdit, { passive: false })
  window.addEventListener('pointerup', finishOverlayEdit)
  window.addEventListener('pointercancel', finishOverlayEdit)
}

function updateOverlayEdit(event: PointerEvent) {
  const edit = overlayEdit
  if (!edit || event.pointerId !== edit.pointerId) return
  event.preventDefault()
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
  if (nextStart === edit.item.start_ms && nextDuration === edit.item.duration_ms) return
  if (!edit.moved) {
    history.value.push(edit.before)
    if (history.value.length > 50) history.value.shift()
    future.value = []
    edit.moved = true
  }
  edit.item.start_ms = nextStart
  edit.item.duration_ms = nextDuration
  playheadMs.value = edit.mode === 'resize-end' ? nextStart + nextDuration : nextStart
  syncVideo()
}

function finishOverlayEdit(event?: PointerEvent) {
  if (event && overlayEdit && event.pointerId !== overlayEdit.pointerId) return
  const changed = overlayEdit?.moved ?? false
  overlayEdit = null
  window.removeEventListener('pointermove', updateOverlayEdit)
  window.removeEventListener('pointerup', finishOverlayEdit)
  window.removeEventListener('pointercancel', finishOverlayEdit)
  if (changed) scheduleSave()
}

function startAt(index: number) {
  return starts.value[index] ?? 0
}

function seek(event: Event) {
  stopPlayback()
  playheadMs.value = Number((event.target as HTMLInputElement).value)
  if (!selectedId.value && activeItem.value) {
    selectedKind.value = 'primary'
    selectedId.value = activeItem.value.id
  }
  syncVideo()
  persistUiState()
}

const videoOverlayEls = new Map<string, HTMLVideoElement>()

function setVideoOverlayRef(id: string, el: HTMLVideoElement | null) {
  if (el) {
    videoOverlayEls.set(id, el)
  } else {
    videoOverlayEls.delete(id)
  }
}

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
    const targetSec = Math.max(0, ((overlay.source_in_ms || 0) + (playheadMs.value - overlay.start_ms)) / 1000)
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
  const target = Math.max(0, (item.source_in_ms + activeLocalMs.value) / 1000)
  if (Number.isFinite(video.duration)) video.currentTime = Math.min(target, video.duration)
  else video.currentTime = target
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

async function renderVariant() {
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
  if (isTypingTarget(event.target)) return
  if (showShortcutsModal.value || showAddShotModal.value || showAddVideoOverlayModal.value || showAddImageOverlayModal.value || showPublishModal.value) {
    if (event.key === 'Escape') {
      event.preventDefault()
      if (showPublishModal.value) closePublishModal()
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
    if (showShortcutsModal.value || showAddShotModal.value || showAddVideoOverlayModal.value || showAddImageOverlayModal.value || showPublishModal.value) {
      closePublishModal()
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
  window.addEventListener('pointerdown', handleDocumentPointerDown)
  window.addEventListener('beforeunload', handleBeforeUnload)
  void loadTimeline()
  void api.getTimelineLuts().then((list) => {
    if (Array.isArray(list) && list.length > 0) {
      lutPresets.value = list
    }
  }).catch(() => {})
})
onBeforeUnmount(() => {
  if (resizeObserver) resizeObserver.disconnect()
  window.removeEventListener('resize', updateContainerWidth)
  window.removeEventListener('keydown', handleKeydown)
  window.removeEventListener('pointerdown', handleDocumentPointerDown)
  window.removeEventListener('beforeunload', handleBeforeUnload)
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
            <span>Box: {{ store.activeCompilation.title }}</span>
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
        <button class="primary-button" :disabled="store.renderingFinalCut || !timeline" @click="renderVariant"><LoaderCircle v-if="store.renderingFinalCut" class="spin" :size="15" /><RefreshCw v-else :size="15" />{{ store.renderingFinalCut ? 'Rendering…' : variantExport ? 'Render again' : 'Export video' }}</button>
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
          v-if="!props.compilationId"
          class="secondary-button publish-header-btn"
          title="Publish to YouTube and social channels"
          @click="openPublishModal()"
        >
          <Share2 :size="14" />
          <span>Publish</span>
        </button>
      </div>
    </header>

    <div v-if="localError || store.error" class="form-error timeline-error-banner">
      <span>{{ localError || store.error }}</span>
      <button type="button" class="form-error-dismiss" aria-label="Dismiss error" @click="localError = ''; store.error = ''"><X :size="13" /></button>
    </div>
    <div v-if="loading" class="timeline-loading"><LoaderCircle class="spin" :size="22" />Loading timeline</div>
    <template v-else-if="timeline">
      <div class="editor-main">
        <div class="preview-pane">
          <section class="program-monitor" :class="variant">
            <video v-if="activeMedia?.mime_type.startsWith('video')" ref="previewVideo" :src="activeMedia.url" :style="activeMediaStyle" :muted="isMuted || Boolean(activeItem?.mute_audio)" playsinline preload="auto" @loadedmetadata="handleLoadedMetadata"></video>
            <img v-else-if="activeMedia" class="preview-primary-media" :src="activeMedia.url" :style="activeMediaStyle" alt="" />
            <div v-else-if="activeItem?.kind === 'title'" class="preview-title">{{ activeItem.text }}</div>
            <div v-else class="preview-missing">Missing media</div>
            <template v-for="overlay in videoOverlays" :key="overlay.id">
              <video
                v-if="playheadMs >= overlay.start_ms && playheadMs < overlay.start_ms + overlay.duration_ms && resolveSource(overlay.source)?.mime_type.startsWith('video')"
                :ref="(el) => setVideoOverlayRef(overlay.id, el as HTMLVideoElement | null)"
                class="preview-video-overlay"
                :class="[videoOverlayGeometryFor(overlay).position, { 'is-full-frame': videoOverlayGeometryFor(overlay).position === 'full-frame' }]"
                :style="videoOverlayPreviewStyle(overlay)"
                :src="resolveSource(overlay.source)!.url"
                :muted="isMuted || Boolean(overlay.mute_audio) || (overlay.volume ?? 1) <= 0"
                playsinline
                preload="auto"
                @loadedmetadata="syncVideoOverlays"
              ></video>
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
            <span class="preview-shortcut">Space play · ←/→ step · S split</span>
          </div>
        </div>
        <aside class="timeline-inspector" @focusin="captureInspectorSnapshot" @pointerdown="captureInspectorSnapshot">
          <header class="inspector-header">
            <span class="inspector-eyebrow">Inspector · {{ isMultiSelect ? 'MULTI-SELECTION' : selectedKind.toUpperCase() }}</span>
            <strong>{{ isMultiSelect ? `${selectedEntries.length} items selected` : (selectedPrimary?.label || selectedVideo?.label || selectedText?.text || selectedAudio?.label || (selectedImage ? 'Image overlay' : 'Nothing selected')) }}</strong>
          </header>
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
                <label class="framing-fit-label">
                  <span>Fit</span>
                  <select v-model="geometryFor(selectedPrimary).fit" @change="commitInspectorEdit">
                    <option value="cover">Fill frame (crop)</option>
                    <option value="contain">Full frame (no crop)</option>
                  </select>
                </label>

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
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Focus X</span>
                        <b>{{ Math.round((geometryFor(selectedPrimary).focal_x ?? 0.5) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="geometryFor(selectedPrimary).focal_x"
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        aria-label="Focus X"
                        :style="framingRangeStyle(geometryFor(selectedPrimary).focal_x ?? 0.5, 0, 1)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field">
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
                        :style="framingRangeStyle(geometryFor(selectedPrimary).focal_y ?? 0.5, 0, 1)"
                        @change="commitInspectorEdit"
                      />
                    </label>
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
                <label>
                  Look / Preset
                  <select v-model="geometryFor(selectedPrimary).lut" @change="commitInspectorEdit">
                    <option value="none">None (Original colors)</option>
                    <optgroup v-for="group in lutGroups" :key="group.category" :label="group.category">
                      <option v-for="preset in group.presets" :key="preset.id" :value="preset.id">
                        {{ preset.label }}
                      </option>
                    </optgroup>
                  </select>
                </label>
                <div v-if="selectedLutPreset(geometryFor(selectedPrimary).lut)" class="lut-description">
                  {{ selectedLutPreset(geometryFor(selectedPrimary).lut)?.description }}
                </div>
                <template v-if="geometryFor(selectedPrimary).lut && geometryFor(selectedPrimary).lut !== 'none'">
                  <label class="framing-slider-field">
                    <span class="framing-field-head">
                      <span>Strength</span>
                      <b>{{ Math.round((geometryFor(selectedPrimary).lut_strength ?? 1) * 100) }}%</b>
                    </span>
                    <input
                      v-model.number="geometryFor(selectedPrimary).lut_strength"
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      aria-label="LUT strength"
                      :style="framingRangeStyle(geometryFor(selectedPrimary).lut_strength ?? 1, 0, 1)"
                      @change="commitInspectorEdit"
                    />
                  </label>
                  <div class="quick-dur-buttons">
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((geometryFor(selectedPrimary).lut_strength ?? 1) * 100) === 25 }"
                      @click="setLutStrength(geometryFor(selectedPrimary), 0.25)"
                    >
                      25%
                    </button>
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((geometryFor(selectedPrimary).lut_strength ?? 1) * 100) === 50 }"
                      @click="setLutStrength(geometryFor(selectedPrimary), 0.5)"
                    >
                      50%
                    </button>
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((geometryFor(selectedPrimary).lut_strength ?? 1) * 100) === 75 }"
                      @click="setLutStrength(geometryFor(selectedPrimary), 0.75)"
                    >
                      75%
                    </button>
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((geometryFor(selectedPrimary).lut_strength ?? 1) * 100) === 100 }"
                      @click="setLutStrength(geometryFor(selectedPrimary), 1)"
                    >
                      100%
                    </button>
                  </div>
                </template>
              </div>
            </details>
          </template>
          <template v-else-if="selectedVideo">
            <div class="inspector-layer-order">
              <span class="quick-dur-label">Layer {{ selectedVideoIndex + 1 }} of {{ videoOverlays.length }}</span>
              <button type="button" class="quick-dur-btn" title="Add another secondary video layer onto the VIDEO track" @click="openAddVideoOverlayModal">
                + Add video layer
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
            <label v-if="videoOverlayGeometryFor(selectedVideo).position === 'full-frame'">
              Fit
              <select v-model="videoOverlayGeometryFor(selectedVideo).fit" @change="commitInspectorEdit">
                <option value="cover">Fill frame (Cover / Crop)</option>
                <option value="contain">Fit frame (Contain / No crop)</option>
              </select>
            </label>
            <label v-else class="framing-slider-field">
              <span class="framing-field-head">
                <span>Scale</span>
                <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).scale ?? 1) * 100) }}%</b>
              </span>
              <input v-model.number="videoOverlayGeometryFor(selectedVideo).scale" type="range" min="0.1" max="2" step="0.05" @change="commitInspectorEdit" />
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
                    <label class="framing-slider-field">
                      <span class="framing-field-head">
                        <span>Focus X</span>
                        <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).focal_x ?? 0.5) * 100) }}%</b>
                      </span>
                      <input
                        v-model.number="videoOverlayGeometryFor(selectedVideo).focal_x"
                        type="range"
                        min="0"
                        max="1"
                        step="0.01"
                        aria-label="Focus X"
                        :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).focal_x ?? 0.5, 0, 1)"
                        @change="commitInspectorEdit"
                      />
                    </label>
                    <label class="framing-slider-field">
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
                        :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).focal_y ?? 0.5, 0, 1)"
                        @change="commitInspectorEdit"
                      />
                    </label>
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
                <label>
                  Look / Preset
                  <select v-model="videoOverlayGeometryFor(selectedVideo).lut" @change="commitInspectorEdit">
                    <option value="none">None (Original colors)</option>
                    <optgroup v-for="group in lutGroups" :key="group.category" :label="group.category">
                      <option v-for="preset in group.presets" :key="preset.id" :value="preset.id">
                        {{ preset.label }}
                      </option>
                    </optgroup>
                  </select>
                </label>
                <div v-if="selectedLutPreset(videoOverlayGeometryFor(selectedVideo).lut)" class="lut-description">
                  {{ selectedLutPreset(videoOverlayGeometryFor(selectedVideo).lut)?.description }}
                </div>
                <template v-if="videoOverlayGeometryFor(selectedVideo).lut && videoOverlayGeometryFor(selectedVideo).lut !== 'none'">
                  <label class="framing-slider-field">
                    <span class="framing-field-head">
                      <span>Strength</span>
                      <b>{{ Math.round((videoOverlayGeometryFor(selectedVideo).lut_strength ?? 1) * 100) }}%</b>
                    </span>
                    <input
                      v-model.number="videoOverlayGeometryFor(selectedVideo).lut_strength"
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      aria-label="LUT strength"
                      :style="framingRangeStyle(videoOverlayGeometryFor(selectedVideo).lut_strength ?? 1, 0, 1)"
                      @change="commitInspectorEdit"
                    />
                  </label>
                  <div class="quick-dur-buttons">
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((videoOverlayGeometryFor(selectedVideo).lut_strength ?? 1) * 100) === 25 }"
                      @click="setLutStrength(videoOverlayGeometryFor(selectedVideo), 0.25)"
                    >
                      25%
                    </button>
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((videoOverlayGeometryFor(selectedVideo).lut_strength ?? 1) * 100) === 50 }"
                      @click="setLutStrength(videoOverlayGeometryFor(selectedVideo), 0.5)"
                    >
                      50%
                    </button>
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((videoOverlayGeometryFor(selectedVideo).lut_strength ?? 1) * 100) === 75 }"
                      @click="setLutStrength(videoOverlayGeometryFor(selectedVideo), 0.75)"
                    >
                      75%
                    </button>
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((videoOverlayGeometryFor(selectedVideo).lut_strength ?? 1) * 100) === 100 }"
                      @click="setLutStrength(videoOverlayGeometryFor(selectedVideo), 1)"
                    >
                      100%
                    </button>
                  </div>
                </template>
              </div>
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
                <label>
                  Look / Preset
                  <select v-model="geometryFor(selectedImage).lut" @change="commitInspectorEdit">
                    <option value="none">None (Original colors)</option>
                    <optgroup v-for="group in lutGroups" :key="group.category" :label="group.category">
                      <option v-for="preset in group.presets" :key="preset.id" :value="preset.id">
                        {{ preset.label }}
                      </option>
                    </optgroup>
                  </select>
                </label>
                <div v-if="selectedLutPreset(geometryFor(selectedImage).lut)" class="lut-description">
                  {{ selectedLutPreset(geometryFor(selectedImage).lut)?.description }}
                </div>
                <template v-if="geometryFor(selectedImage).lut && geometryFor(selectedImage).lut !== 'none'">
                  <label class="framing-slider-field">
                    <span class="framing-field-head">
                      <span>Strength</span>
                      <b>{{ Math.round((geometryFor(selectedImage).lut_strength ?? 1) * 100) }}%</b>
                    </span>
                    <input
                      v-model.number="geometryFor(selectedImage).lut_strength"
                      type="range"
                      min="0"
                      max="1"
                      step="0.05"
                      aria-label="LUT strength"
                      :style="framingRangeStyle(geometryFor(selectedImage).lut_strength ?? 1, 0, 1)"
                      @change="commitInspectorEdit"
                    />
                  </label>
                  <div class="quick-dur-buttons">
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((geometryFor(selectedImage).lut_strength ?? 1) * 100) === 25 }"
                      @click="setLutStrength(geometryFor(selectedImage), 0.25)"
                    >
                      25%
                    </button>
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((geometryFor(selectedImage).lut_strength ?? 1) * 100) === 50 }"
                      @click="setLutStrength(geometryFor(selectedImage), 0.5)"
                    >
                      50%
                    </button>
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((geometryFor(selectedImage).lut_strength ?? 1) * 100) === 75 }"
                      @click="setLutStrength(geometryFor(selectedImage), 0.75)"
                    >
                      75%
                    </button>
                    <button
                      type="button"
                      class="quick-dur-btn"
                      :class="{ active: Math.round((geometryFor(selectedImage).lut_strength ?? 1) * 100) === 100 }"
                      @click="setLutStrength(geometryFor(selectedImage), 1)"
                    >
                      100%
                    </button>
                  </div>
                </template>
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
        <div class="track-row primary-track"><strong>PRIMARY</strong><div class="track-content"><i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i>
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
                <Scissors v-if="item.kind === 'video' && (item.source_in_ms > 0)" class="block-trim-icon" :size="10" title="Trimmed clip" />
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
            <span class="block-bottom">
              <b>{{ item.label }}</b>
              <small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small>
            </span>
          </button>
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
          @dragover.prevent="handleAudioTrackDragOver"
          @dragleave="handleAudioTrackDragLeave"
          @drop.prevent="handleAudioTrackDrop"
        >
          <strong>AUDIO</strong>
          <div class="overlay-track">
            <i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i>
            <button
              v-for="item in audioOverlays"
              :key="item.id"
              class="overlay-block audio"
              :class="{ selected: isSelected('audio', item.id), 'is-muted': item.mute_audio }"
              :style="{ left: `${item.start_ms / (totalMs || 1) * 100}%`, width: `${(item.duration_ms / (totalMs || 1)) * 100}%` }"
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
      <!-- PUBLISH MODAL -->
      <div v-if="showPublishModal" class="publish-modal-backdrop" @click.self="closePublishModal()">
        <div class="publish-modal-card">
          <PublishPanel
            :project="project"
            :variant="variant"
            :episode-id="store.currentEpisodeId || undefined"
            as-modal
            @close="closePublishModal()"
            @published="store.loadProject(project.id); closePublishModal()"
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
  overflow-x: auto;
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

.track-action-upload input {
  display: none;
}

.track-action-btn:hover,
.track-action-upload:hover {
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  border-color: rgba(255, 255, 255, 0.25);
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
  z-index: 120;
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
