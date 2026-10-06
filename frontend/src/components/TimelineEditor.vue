<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PublishPanel from '@/components/PublishPanel.vue'
import { api, type AspectRatio, type OutputVariant, type ProjectDetail, type ProjectTimeline, type Shot, type TimelineGeometry, type TimelinePrimaryItem, type TimelineSource } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import { AlignCenter, ChevronLeft, ChevronRight, CopyPlus, Download, Film, GripVertical, ImagePlus, Keyboard, LoaderCircle, MoveLeft, MoveRight, Pause, Play, Plus, Redo2, RefreshCw, Repeat, Save, Scissors, Share2, SkipBack, SkipForward, Split, Trash2, Type, Undo2, Upload, Volume2, VolumeX, X, ZoomIn, ZoomOut } from '@lucide/vue'

const props = defineProps<{ project: ProjectDetail; shots: Shot[] }>()
const emit = defineEmits<{ variantChange: [variant: OutputVariant] }>()
const store = useStudioStore()
const route = useRoute()
const router = useRouter()
const timeline = ref<ProjectTimeline | null>(null)

interface PersistedTimelineUiState {
  isMuted?: boolean
  zoomLevel?: number
  playheadMs?: number
  selectedKind?: 'primary' | 'image' | 'text'
  selectedId?: string
  variant?: OutputVariant
  scrollLeft?: number
}

const GLOBAL_PREFS_KEY = 'cutline:timeline-prefs'

function uiStorageKey(projectId = props.project.id, episodeId = store.currentEpisodeId): string {
  return `cutline:timeline-ui:${projectId}:${episodeId || 'default'}`
}

function readPersistedUiState(projectId = props.project.id, episodeId = store.currentEpisodeId): PersistedTimelineUiState {
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
      localStorage.getItem(uiStorageKey(projectId, episodeId)) ||
      (episodeId ? localStorage.getItem(uiStorageKey(projectId, null)) : null)
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
const selectedKind = ref<'primary' | 'image' | 'text'>(
  initialUiState.selectedKind === 'image' || initialUiState.selectedKind === 'text'
    ? initialUiState.selectedKind
    : 'primary',
)
const selectedId = ref(typeof initialUiState.selectedId === 'string' ? initialUiState.selectedId : '')
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
const variantExport = computed(() => props.project.variant_exports.find((item) => item.variant === variant.value) ?? null)
const downloadFilename = computed(() => {
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
const selectedImage = computed(() => timeline.value?.document.image_overlays.find((item) => item.id === selectedId.value) ?? null)
const selectedText = computed(() => timeline.value?.document.text_overlays.find((item) => item.id === selectedId.value) ?? null)
const activeIndex = computed(() => {
  const found = starts.value.findIndex((start, index) => playheadMs.value >= start && playheadMs.value < start + (primary.value[index]?.duration_ms ?? 0))
  return found < 0 ? Math.max(0, primary.value.length - 1) : found
})
const activeItem = computed(() => primary.value[activeIndex.value] ?? null)
const activeLocalMs = computed(() => playheadMs.value - (starts.value[activeIndex.value] ?? 0))

function defaultGeometry(item?: { source?: TimelineSource | null }): TimelineGeometry {
  const reframed = item?.source ? resolveSource(item.source)?.native === false : false
  return { source_aspect: 'auto', fit: reframed ? 'contain' : 'cover', focal_x: 0.5, focal_y: 0.5, zoom: 1, position: 'center', scale: 1 }
}
function geometryFor(item: { source?: TimelineSource | null; variant: Partial<Record<OutputVariant, TimelineGeometry>> }) {
  if (!item.variant[variant.value]) item.variant[variant.value] = defaultGeometry(item)
  return item.variant[variant.value]!
}

function shotMedia(shot: Shot, mediaType: 'image' | 'clip') {
  const native = shot.media_variants.find((item) => item.media_type === mediaType && item.aspect_ratio === targetAspect.value)
  if (native) return { media: native, native: true }
  const legacy = mediaType === 'clip' ? shot.clip : shot.image
  if (legacy) return { media: legacy, native: props.project.aspect_ratio === targetAspect.value }
  const fallback = shot.media_variants.find((item) => item.media_type === mediaType)
  return fallback ? { media: fallback, native: false } : null
}

function resolveSource(source: TimelineSource | null) {
  if (!source) return null
  if (source.kind === 'asset') {
    const asset = timeline.value?.assets.find((item) => item.id === source.ref_id)
    return asset ? { url: asset.url, mime_type: asset.mime_type, native: true, label: asset.label } : null
  }
  const shot = props.shots.find((item) => item.id === source.ref_id)
  if (!shot) return null
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
const activeGeometry = computed(() => activeItem.value?.variant[variant.value] ?? defaultGeometry(activeItem.value ?? undefined))
const activeMediaStyle = computed(() => ({
  objectFit: activeGeometry.value.fit,
  objectPosition: `${activeGeometry.value.focal_x * 100}% ${activeGeometry.value.focal_y * 100}%`,
  transform: `scale(${activeGeometry.value.zoom})`,
}))
const activeBadge = computed(() => {
  if (!activeMedia.value) return activeItem.value?.kind === 'title' ? 'TITLE' : 'MISSING'
  if (!activeMedia.value.mime_type.startsWith('video')) return 'STILL FALLBACK'
  return activeMedia.value.native ? `NATIVE ${targetAspect.value}` : `REFRAMED ${targetAspect.value === '9:16' ? '16:9' : '9:16'}`
})

let loadedProjectId = props.project.id
let loadedEpisodeId: string | undefined = store.currentEpisodeId || undefined

async function loadTimeline() {
  loading.value = true
  localError.value = ''
  const reqProjectId = props.project.id
  const reqEpisodeId = store.currentEpisodeId || undefined
  const persisted = readPersistedUiState(reqProjectId, reqEpisodeId || null)
  if (typeof persisted.isMuted === 'boolean') {
    isMuted.value = persisted.isMuted
  }
  if (typeof persisted.zoomLevel === 'number' && persisted.zoomLevel >= 1 && persisted.zoomLevel <= 10) {
    zoomLevel.value = persisted.zoomLevel
  }
  try {
    timeline.value = await api.timeline(reqProjectId, variant.value, reqEpisodeId)
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
      if (persisted.selectedKind === 'image' && persisted.selectedId && doc.image_overlays.some((item) => item.id === persisted.selectedId)) {
        selectedKind.value = 'image'
        selectedId.value = persisted.selectedId
      } else if (persisted.selectedKind === 'text' && persisted.selectedId && doc.text_overlays.some((item) => item.id === persisted.selectedId)) {
        selectedKind.value = 'text'
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
    if (route.query.variant === 'shorts' || route.query.variant === 'landscape') variant.value = route.query.variant
    history.value = []
    future.value = []
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Could not load timeline'
  } finally {
    loading.value = false
  }
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

function mutate(change: () => void) {
  if (!timeline.value) return
  history.value.push(snapshot())
  if (history.value.length > 50) history.value.shift()
  future.value = []
  change()
  nextTick(scheduleSave)
}

function undo() {
  const previous = history.value.pop()
  if (!previous || !timeline.value) return
  future.value.push(snapshot())
  timeline.value.document = JSON.parse(previous)
  scheduleSave()
}

function redo() {
  const next = future.value.pop()
  if (!next || !timeline.value) return
  history.value.push(snapshot())
  timeline.value.document = JSON.parse(next)
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
    for (const item of timeline.value.document.image_overlays) {
      item.start_ms = Math.round(item.start_ms)
      item.duration_ms = Math.round(item.duration_ms)
    }
    for (const item of timeline.value.document.text_overlays) {
      item.start_ms = Math.round(item.start_ms)
      item.duration_ms = Math.round(item.duration_ms)
    }
    const saved = await api.saveTimeline(targetProjectId, timeline.value, targetVariant, targetEpisodeId)
    if (props.project.id === targetProjectId && (timeline.value?.edit || variant.value) === targetVariant && (store.currentEpisodeId || undefined) === targetEpisodeId) {
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
    await store.loadProject(targetProjectId)
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
  timeline.value.document.image_overlays = adjust(timeline.value.document.image_overlays)
  timeline.value.document.text_overlays = adjust(timeline.value.document.text_overlays)
}

function insertInterval(start: number, duration: number) {
  if (!timeline.value) return
  for (const item of [...timeline.value.document.image_overlays, ...timeline.value.document.text_overlays]) {
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
  const item = activeItem.value
  if (!item || item.kind !== 'video' || !timeline.value) return
  const local = Math.round(activeLocalMs.value)
  if (local < 100 || item.duration_ms - local < 100) return
  mutate(() => {
    const index = activeIndex.value
    const inMs = Math.round(item.source_in_ms || 0)
    const totalDuration = Math.round(item.duration_ms)
    const leftDuration = local
    const leftSourceOut = inMs + leftDuration
    const rightDuration = totalDuration - leftDuration
    const rightSourceIn = leftSourceOut
    const rightSourceOut = item.source_out_ms != null ? Math.round(item.source_out_ms) : rightSourceIn + rightDuration

    const left = { ...item, duration_ms: leftDuration, source_in_ms: inMs, source_out_ms: leftSourceOut }
    const right = { ...item, id: crypto.randomUUID(), label: `${item.label} B`, source_in_ms: rightSourceIn, duration_ms: rightDuration, source_out_ms: rightSourceOut }
    timeline.value!.document.primary.splice(index, 1, left, right)
    selectedId.value = right.id
  })
}

function deleteSelection() {
  if (!timeline.value) return
  mutate(() => {
    if (selectedKind.value === 'primary') {
      const index = selectedPrimaryIndex.value
      if (index < 0 || primary.value.length === 1) return
      const start = starts.value[index] ?? 0
      const duration = primary.value[index]?.duration_ms ?? 0
      timeline.value!.document.primary.splice(index, 1)
      removeInterval(start, duration)
      selectedId.value = timeline.value!.document.primary[Math.max(0, index - 1)]?.id ?? ''
    } else if (selectedKind.value === 'image') {
      timeline.value!.document.image_overlays = timeline.value!.document.image_overlays.filter((item) => item.id !== selectedId.value)
      selectedId.value = primary.value[0]?.id ?? ''
      selectedKind.value = 'primary'
    } else {
      timeline.value!.document.text_overlays = timeline.value!.document.text_overlays.filter((item) => item.id !== selectedId.value)
      selectedId.value = primary.value[0]?.id ?? ''
      selectedKind.value = 'primary'
    }
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

function onPrimaryDragStart(event: DragEvent, index: number) {
  dragSourceIndex.value = index
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(index))
  }
}

function onPrimaryDragOver(event: DragEvent, index: number) {
  if (dragSourceIndex.value === null || dragSourceIndex.value === index) return
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  dragOverIndex.value = index
}

function onPrimaryDragLeave(index: number) {
  if (dragOverIndex.value === index) {
    dragOverIndex.value = null
  }
}

function onPrimaryDrop(_event: DragEvent, targetIndex: number) {
  const fromIndex = dragSourceIndex.value
  dragSourceIndex.value = null
  dragOverIndex.value = null
  if (!timeline.value || fromIndex === null || fromIndex === targetIndex) return
  if (fromIndex < 0 || fromIndex >= primary.value.length || targetIndex < 0 || targetIndex >= primary.value.length) return
  mutate(() => {
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
    const playhead = Math.round(playheadMs.value)
    const duration = Math.round(Math.min(2500, Math.max(100, totalMs.value - playhead)))
    const item = { id: crypto.randomUUID(), text: 'New text', start_ms: playhead, duration_ms: duration, presentation: 'overlay' as const, size: 'medium' as const, color: 'white' as const, background: 'shadow' as const, variant: {} }
    timeline.value!.document.text_overlays.push(item)
    selectedKind.value = 'text'
    selectedId.value = item.id
  })
}

function addImageOverlay(source?: TimelineSource) {
  closeAddMenu()
  if (!timeline.value) return
  const fallback = selectedPrimary.value?.source
  const chosen = source ?? (fallback?.kind === 'asset' ? fallback : fallback ? { kind: 'shot_image' as const, ref_id: fallback.ref_id } : null)
  if (!chosen) return
  mutate(() => {
    const playhead = Math.round(playheadMs.value)
    const duration = Math.round(Math.min(2500, Math.max(100, totalMs.value - playhead)))
    const item = { id: crypto.randomUUID(), source: chosen, start_ms: playhead, duration_ms: duration, opacity: 1, variant: {} }
    timeline.value!.document.image_overlays.push(item)
    selectedKind.value = 'image'
    selectedId.value = item.id
  })
}

async function uploadAsset(event: Event, append: boolean) {
  closeAddMenu()
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !timeline.value) return
  await flushPendingSave()
  timeline.value = await api.uploadTimelineAsset(props.project.id, file, file.name, variant.value, store.currentEpisodeId || undefined)
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

function selectItem(kind: 'primary' | 'image' | 'text', id: string, start: number) {
  stopPlayback()
  selectedKind.value = kind
  selectedId.value = id
  playheadMs.value = start
}

function handlePrimaryBlockClick(event: MouseEvent, item: TimelinePrimaryItem, index: number) {
  stopPlayback()
  selectedKind.value = 'primary'
  selectedId.value = item.id
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

function beginOverlayEdit(event: PointerEvent, kind: 'image' | 'text', item: OverlayItem, mode: OverlayEditMode) {
  if (event.button !== 0 || !timeline.value || totalMs.value <= 0) return
  const track = (event.currentTarget as HTMLElement).closest('.overlay-track')
  if (!track) return
  event.preventDefault()
  stopPlayback()
  selectedKind.value = kind
  selectedId.value = item.id
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
  const item = activeItem.value
  if (item) {
    selectedKind.value = 'primary'
    selectedId.value = item.id
  }
  syncVideo()
  persistUiState()
}

function toggleMute() {
  isMuted.value = !isMuted.value
  if (previewVideo.value) {
    previewVideo.value.muted = isMuted.value || Boolean(activeItem.value?.mute_audio)
  }
  persistUiState()
}

function syncVideo() {
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
  const item = selectedPrimary.value
  if (!item || item.kind !== 'video') return
  mutate(() => {
    item.mute_audio = !item.mute_audio
  })
  syncVideo()
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
  await store.renderFinalCut(variant.value)
}

async function resetTimeline() {
  if (!confirm('Reset the timeline to the current shot plan? All timeline edits and overlays will be removed.')) return
  window.clearTimeout(saveTimer)
  saveTimer = undefined
  dirtyDocument = false
  pendingSave = false
  timeline.value = await api.resetTimeline(props.project.id, store.currentEpisodeId || undefined)
  selectedKind.value = 'primary'
  selectedId.value = timeline.value.document.primary[0]?.id ?? ''
  playheadMs.value = 0
  persistUiState()
  await store.loadProject(props.project.id)
}

watch([isMuted, zoomLevel, selectedKind, selectedId, variant], () => {
  if (!loading.value) persistUiState()
})
watch(playheadMs, () => {
  if (!loading.value && !playing.value) persistUiState()
})
watch(() => [props.project.id, store.currentEpisodeId], async () => {
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
  if (showShortcutsModal.value || showAddShotModal.value || showPublishModal.value) {
    if (event.key === 'Escape') {
      event.preventDefault()
      if (showPublishModal.value) closePublishModal()
      showShortcutsModal.value = false
      showAddShotModal.value = false
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
    if (showShortcutsModal.value || showAddShotModal.value || showPublishModal.value) {
      closePublishModal()
      event.preventDefault()
      showShortcutsModal.value = false
      showAddShotModal.value = false
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
        <h2>Timeline</h2>
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
            <img v-else-if="activeMedia" :src="activeMedia.url" :style="activeMediaStyle" alt="" />
            <div v-else-if="activeItem?.kind === 'title'" class="preview-title">{{ activeItem.text }}</div>
            <div v-else class="preview-missing">Missing media</div>
            <template v-for="overlay in timeline.document.image_overlays" :key="overlay.id">
              <img v-if="playheadMs >= overlay.start_ms && playheadMs < overlay.start_ms + overlay.duration_ms && resolveSource(overlay.source)" class="preview-image-overlay" :class="geometryFor(overlay).position" :style="{ opacity: overlay.opacity, width: `${30 * geometryFor(overlay).scale}%` }" :src="resolveSource(overlay.source)!.url" alt="" />
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
        <aside class="timeline-inspector">
          <header class="inspector-header">
            <span class="inspector-eyebrow">Inspector · {{ selectedKind.toUpperCase() }}</span>
            <strong>{{ selectedPrimary?.label || selectedText?.text || (selectedImage ? 'Image overlay' : 'Nothing selected') }}</strong>
          </header>
          <template v-if="selectedPrimary">
            <label>Name<input v-model="selectedPrimary.label" @change="scheduleSave" /></label>
            <label v-if="selectedPrimary.kind === 'title'">Text<textarea v-model="selectedPrimary.text" rows="3" @change="scheduleSave"></textarea></label>
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
                  <span>{{ selectedPrimary.mute_audio ? 'Audio removed' : 'Audio active' }}</span>
                </button>
              </div>
            </template>
            <details class="inspector-section"><summary>Framing &amp; crop</summary>
            <label>Fit<select v-model="geometryFor(selectedPrimary).fit" @change="scheduleSave"><option value="cover">Fill frame (crop)</option><option value="contain">Full frame (no crop)</option></select></label>
            <label>Horizontal focus<input v-model.number="geometryFor(selectedPrimary).focal_x" type="range" min="0" max="1" step="0.01" @change="scheduleSave" /></label>
            <label>Vertical focus<input v-model.number="geometryFor(selectedPrimary).focal_y" type="range" min="0" max="1" step="0.01" @change="scheduleSave" /></label>
            <label>Crop zoom<input v-model.number="geometryFor(selectedPrimary).zoom" type="range" min="1" max="3" step="0.05" @change="scheduleSave" /></label>
            </details>
          </template>
          <template v-else-if="selectedText">
            <label>Text<textarea v-model="selectedText.text" rows="3" @change="scheduleSave"></textarea></label>
            <label>Start (ms)<input v-model.number="selectedText.start_ms" type="number" min="0" step="100" @change="scheduleSave" /></label>
            <label>Duration (ms)<input v-model.number="selectedText.duration_ms" type="number" min="100" step="100" @change="scheduleSave" /></label>
            <label>Presentation<select v-model="selectedText.presentation" @change="scheduleSave"><option value="overlay">Overlay</option><option value="full-frame">Full frame</option></select></label>
            <label>Position<select v-model="geometryFor(selectedText).position" @change="scheduleSave"><option value="top">Top</option><option value="center">Center</option><option value="bottom">Bottom</option><option value="bottom-left">Lower left</option><option value="bottom-right">Lower right</option></select></label>
            <label>Size<select v-model="selectedText.size" @change="scheduleSave"><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></label>
            <label>Background<select v-model="selectedText.background" @change="scheduleSave"><option value="none">None</option><option value="shadow">Shadow</option><option value="translucent">Translucent</option><option value="solid">Solid</option></select></label>
          </template>
          <template v-else-if="selectedImage">
            <label>Start (ms)<input v-model.number="selectedImage.start_ms" type="number" min="0" step="100" @change="scheduleSave" /></label>
            <label>Duration (ms)<input v-model.number="selectedImage.duration_ms" type="number" min="100" step="100" @change="scheduleSave" /></label>
            <label>Position<select v-model="geometryFor(selectedImage).position" @change="scheduleSave"><option value="top-left">Top left</option><option value="top-right">Top right</option><option value="center">Center</option><option value="bottom-left">Bottom left</option><option value="bottom-right">Bottom right</option></select></label>
            <label>Scale<input v-model.number="geometryFor(selectedImage).scale" type="range" min="0.1" max="2" step="0.05" @change="scheduleSave" /></label>
            <label>Opacity<input v-model.number="selectedImage.opacity" type="range" min="0" max="1" step="0.05" @change="scheduleSave" /></label>
          </template>
        </aside>
      </div>

      <div class="timeline-toolbar">
        <button title="Undo (Ctrl+Z)" :disabled="!history.length" @click="undo"><Undo2 :size="16" /></button>
        <button title="Redo (Ctrl+Shift+Z / Ctrl+Y)" :disabled="!future.length" @click="redo"><Redo2 :size="16" /></button>
        <span></span>
        <button title="Split at playhead (S / C)" :disabled="activeItem?.kind !== 'video'" @click="splitAtPlayhead"><Split :size="16" /><span>Split</span></button>
        <button title="Duplicate selected item (Ctrl+D)" :disabled="!selectedPrimary && !selectedImage && !selectedText" @click="duplicateSelected"><CopyPlus :size="16" /><span>Duplicate</span></button>
        <button class="add-shot-toolbar-btn" title="Add/insert shot from project into timeline" @click="showAddShotModal = true"><Plus :size="15" /><span>Add shot</span></button>
        <button
          type="button"
          :title="selectedPrimary?.kind === 'video' ? (selectedPrimary.mute_audio ? 'Unmute clip audio (M)' : 'Mute/remove audio from clip (M)') : 'Mute clip audio (select video)'"
          :disabled="selectedPrimary?.kind !== 'video'"
          :class="{ 'item-muted-active': selectedPrimary?.mute_audio }"
          @click="toggleSelectedAudio"
        >
          <VolumeX v-if="selectedPrimary?.mute_audio" :size="16" />
          <Volume2 v-else :size="16" />
        </button>
        <button :disabled="!selectedPrimary || selectedPrimaryIndex <= 0" title="Move left" @click="moveSelected(-1)"><MoveLeft :size="16" /></button>
        <button :disabled="!selectedPrimary || selectedPrimaryIndex >= primary.length - 1" title="Move right" @click="moveSelected(1)"><MoveRight :size="16" /></button>
        <details ref="addMenuRef" class="add-menu"><summary><Plus :size="15" />Add media &amp; text</summary><div class="add-menu-content">
        <button title="Append current storyboard frame" @click="appendStill"><ImagePlus :size="16" /><span>Append storyboard frame</span></button>
        <label class="tool-upload" title="Upload and append image"><Upload :size="16" /><span>Upload image</span><input type="file" aria-label="Upload and append image" accept="image/png,image/jpeg,image/webp" @change="uploadAsset($event, true)" /></label>
        <button title="Add title card" @click="addTitle"><AlignCenter :size="16" /><span>Title card</span></button>
        <button title="Add text overlay" @click="addText"><Type :size="16" /><span>Text overlay</span></button>
        <button title="Add image overlay" @click="addImageOverlay()"><ImagePlus :size="16" /><span>Image overlay</span></button>
        <label class="tool-upload" title="Upload image overlay"><Upload :size="16" /><span>Upload image</span><input type="file" aria-label="Upload image overlay" accept="image/png,image/jpeg,image/webp" @change="uploadAsset($event, false)" /></label>
        </div></details>
        <button class="danger-tool" title="Delete selected item (Delete / Backspace)" @click="deleteSelection"><Trash2 :size="16" /></button>
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
            :title="`${item.label} · ${(item.duration_ms / 1000).toFixed(1)}s (drag to reorder)`"
            :aria-pressed="selectedKind === 'primary' && selectedId === item.id"
            :class="[
              item.kind,
              {
                selected: selectedKind === 'primary' && selectedId === item.id,
                'is-muted': item.mute_audio,
                'is-dragging': dragSourceIndex === index,
                'is-drop-target': dragOverIndex === index,
              },
            ]"
            :style="{ width: `${item.duration_ms / totalMs * 100}%` }"
            @click="handlePrimaryBlockClick($event, item, index)"
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
                <span>{{ item.kind.toUpperCase() }}</span>
              </span>
              <span class="block-status-icons">
                <Scissors v-if="item.kind === 'video' && (item.source_in_ms > 0)" class="block-trim-icon" :size="10" title="Trimmed clip" />
                <VolumeX v-if="item.mute_audio" class="block-muted-icon" :size="11" title="Audio muted" />
              </span>
            </span>
            <span class="block-bottom">
              <b>{{ item.label }}</b>
              <small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small>
            </span>
          </button>
        </div></div>
        <div class="track-row"><strong>IMAGE</strong><div class="overlay-track"><i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i><button v-for="item in timeline.document.image_overlays" :key="item.id" class="overlay-block image" :class="{ selected: selectedKind === 'image' && selectedId === item.id }" :style="{ left: `${item.start_ms / totalMs * 100}%`, width: `${Math.max(2, item.duration_ms / totalMs * 100)}%` }" title="Drag to move image overlay" @pointerdown="beginOverlayEdit($event, 'image', item, 'move')" @click="selectItem('image', item.id, item.start_ms)"><span class="resize-handle start" title="Adjust start" @pointerdown.stop="beginOverlayEdit($event, 'image', item, 'resize-start')"></span><span class="overlay-label">IMAGE</span><small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small><span class="resize-handle end" title="Adjust end" @pointerdown.stop="beginOverlayEdit($event, 'image', item, 'resize-end')"></span></button></div></div>
        <div class="track-row"><strong>TEXT</strong><div class="overlay-track"><i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i><button v-for="item in timeline.document.text_overlays" :key="item.id" class="overlay-block text" :class="{ selected: selectedKind === 'text' && selectedId === item.id }" :style="{ left: `${item.start_ms / totalMs * 100}%`, width: `${Math.max(2, item.duration_ms / totalMs * 100)}%` }" title="Drag to move text overlay" @pointerdown="beginOverlayEdit($event, 'text', item, 'move')" @click="selectItem('text', item.id, item.start_ms)"><span class="resize-handle start" title="Adjust start" @pointerdown.stop="beginOverlayEdit($event, 'text', item, 'resize-start')"></span><span class="overlay-label">{{ item.text }}</span><small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small><span class="resize-handle end" title="Adjust end" @pointerdown.stop="beginOverlayEdit($event, 'text', item, 'resize-end')"></span></button></div></div>
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
              <button type="button" class="add-shot-insert-btn" @click="addShotToTimeline(shot)">
                <Plus :size="13" />Insert
              </button>
            </div>
            <div v-if="!availableShots.length" class="empty-shot-state">No shots found matching your search.</div>
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
  margin: 0 0 2px;
  color: var(--text-primary);
  font-size: 17px;
  font-weight: 650;
  letter-spacing: -0.02em;
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
  flex: 1;
  min-height: 0;
  max-height: 100%;
  max-width: 100%;
  width: 100%;
  height: auto;
  margin: auto;
  aspect-ratio: 16 / 9;
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

.program-monitor video,
.program-monitor > img {
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

.preview-image-overlay {
  position: absolute;
  max-height: 50%;
  object-fit: contain;
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

.top, .top-left, .top-right { top: 8%; }
.bottom, .bottom-left, .bottom-right { bottom: 10%; }
.center { top: 50%; left: 50%; transform: translate(-50%, -50%); }
.top, .bottom { left: 50%; transform: translateX(-50%); }
.top-left, .bottom-left { left: 7%; }
.top-right, .bottom-right { right: 7%; }

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
  min-height: 0;
  padding: 14px 16px;
  background: var(--chrome-bg);
  color: var(--ink);
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
  grid-template-columns: 70px minmax(var(--track-width, 1000px), 1fr);
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
  align-self: start;
  padding: 2px 8px 0;
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
  grid-template-columns: 70px var(--track-width, 1000px);
  min-height: 38px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.primary-track {
  min-height: 74px;
}

.track-row > strong {
  position: sticky;
  left: 0;
  z-index: 6;
  display: flex;
  align-items: center;
  padding: 0 8px;
  background: var(--preview-well);
  color: var(--muted);
  font-size: 10px;
  letter-spacing: 0.05em;
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
  display: flex;
  gap: 2px;
  padding: 4px 0;
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
  position: relative;
  flex: none;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 6px 8px;
  border: 1px solid var(--line-default);
  border-radius: 7px;
  background: linear-gradient(180deg, #212a31 0%, #171e24 100%);
  color: #f4f6f8;
  text-align: left;
  overflow: hidden;
  cursor: pointer;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.22), inset 0 1px 0 rgba(255, 255, 255, 0.06);
  transition: border-color 0.14s ease, filter 0.14s ease, transform 0.12s ease, opacity 0.14s ease;
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
}

.timeline-block.is-drop-target {
  border-left: 3px solid var(--accent);
  box-shadow: -3px 0 12px rgba(235, 101, 84, 0.45);
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

.overlay-block.image {
  background: linear-gradient(180deg, #326857 0%, #244d40 100%);
  color: #fff;
}

.overlay-block.text {
  background: linear-gradient(180deg, #8b6537 0%, #684b27 100%);
  color: #fff;
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
