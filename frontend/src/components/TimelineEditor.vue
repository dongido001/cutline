<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, type AspectRatio, type OutputVariant, type ProjectDetail, type ProjectTimeline, type Shot, type TimelineGeometry, type TimelinePrimaryItem, type TimelineSource } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import { AlignCenter, Download, ImagePlus, LoaderCircle, MoveLeft, MoveRight, Pause, Play, Redo2, RefreshCw, Save, Scissors, Split, Trash2, Type, Undo2, Upload } from '@lucide/vue'

const props = defineProps<{ project: ProjectDetail; shots: Shot[] }>()
const emit = defineEmits<{ variantChange: [variant: OutputVariant] }>()
const store = useStudioStore()
const route = useRoute()
const timeline = ref<ProjectTimeline | null>(null)
const requestedVariant = route.query.variant
const variant = ref<OutputVariant>(
  requestedVariant === 'shorts' || requestedVariant === 'landscape'
    ? requestedVariant
    : props.project.aspect_ratio === '9:16' ? 'shorts' : 'landscape',
)
const selectedKind = ref<'primary' | 'image' | 'text'>('primary')
const selectedId = ref('')
const playheadMs = ref(0)
const playing = ref(false)
const previewVideo = ref<HTMLVideoElement | null>(null)
const sourceDurationMs = ref(0)
const loading = ref(true)
const saving = ref(false)
const saveState = ref('Saved')
const localError = ref('')
const history = ref<string[]>([])
const future = ref<string[]>([])
let saveTimer: number | undefined
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
const primary = computed(() => timeline.value?.document.primary ?? [])
const totalMs = computed(() => primary.value.reduce((sum, item) => sum + item.duration_ms, 0))
const playheadPercent = computed(() => totalMs.value ? Math.min(100, Math.max(0, playheadMs.value / totalMs.value * 100)) : 0)
const timelineTicks = computed(() => Array.from({ length: 9 }, (_, index) => {
  const percent = index / 8 * 100
  const seconds = totalMs.value / 1000 * index / 8
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return { percent, label: `${minutes}:${String(remainder).padStart(2, '0')}` }
}))
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

async function loadTimeline() {
  loading.value = true
  localError.value = ''
  try {
    timeline.value = await api.timeline(props.project.id)
    const trimShot = typeof route.query.trimShot === 'string' ? route.query.trimShot : null
    const requestedIndex = trimShot ? timeline.value.document.primary.findIndex((item) => item.source?.ref_id === trimShot && item.kind === 'video') : -1
    const initialIndex = requestedIndex >= 0 ? requestedIndex : 0
    selectedId.value = timeline.value.document.primary[initialIndex]?.id ?? ''
    playheadMs.value = timeline.value.document.primary.slice(0, initialIndex).reduce((sum, item) => sum + item.duration_ms, 0)
    if (route.query.variant === 'shorts' || route.query.variant === 'landscape') variant.value = route.query.variant
    history.value = []
    future.value = []
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Could not load timeline'
  } finally {
    loading.value = false
  }
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
  saveState.value = 'Unsaved'
  window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(saveTimeline, 650)
}

async function saveTimeline() {
  if (!timeline.value || saving.value) return
  saving.value = true
  saveState.value = 'Saving...'
  try {
    timeline.value = await api.saveTimeline(props.project.id, timeline.value)
    saveState.value = 'Saved'
    await store.loadProject(props.project.id)
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Could not save timeline'
    saveState.value = 'Save failed'
  } finally {
    saving.value = false
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
  const local = Math.max(0, Math.min(item.duration_ms, playheadMs.value - itemStart))
  const sourceTime = item.source_in_ms + local
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
  const local = activeLocalMs.value
  if (local < 100 || item.duration_ms - local < 100) return
  mutate(() => {
    const index = activeIndex.value
    const left = { ...item, duration_ms: local, source_out_ms: item.source_in_ms + local }
    const right = { ...item, id: crypto.randomUUID(), label: `${item.label} B`, source_in_ms: item.source_in_ms + local, duration_ms: item.duration_ms - local, source_out_ms: item.source_out_ms }
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

function appendStill() {
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
  if (!timeline.value) return
  mutate(() => {
    const duration = Math.min(2500, Math.max(100, totalMs.value - playheadMs.value))
    const item = { id: crypto.randomUUID(), text: 'New text', start_ms: playheadMs.value, duration_ms: duration, presentation: 'overlay' as const, size: 'medium' as const, color: 'white' as const, background: 'shadow' as const, variant: {} }
    timeline.value!.document.text_overlays.push(item)
    selectedKind.value = 'text'
    selectedId.value = item.id
  })
}

function addImageOverlay(source?: TimelineSource) {
  if (!timeline.value) return
  const fallback = selectedPrimary.value?.source
  const chosen = source ?? (fallback?.kind === 'asset' ? fallback : fallback ? { kind: 'shot_image' as const, ref_id: fallback.ref_id } : null)
  if (!chosen) return
  mutate(() => {
    const duration = Math.min(2500, Math.max(100, totalMs.value - playheadMs.value))
    const item = { id: crypto.randomUUID(), source: chosen, start_ms: playheadMs.value, duration_ms: duration, opacity: 1, variant: {} }
    timeline.value!.document.image_overlays.push(item)
    selectedKind.value = 'image'
    selectedId.value = item.id
  })
}

async function uploadAsset(event: Event, append: boolean) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !timeline.value) return
  timeline.value = await api.uploadTimelineAsset(props.project.id, file)
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
}

function syncVideo() {
  const video = previewVideo.value
  const item = activeItem.value
  if (!video || !item || !activeMedia.value?.mime_type.startsWith('video')) return
  const target = Math.max(0, (item.source_in_ms + activeLocalMs.value) / 1000)
  if (Number.isFinite(video.duration)) video.currentTime = Math.min(target, video.duration)
  else video.currentTime = target
}

function handleLoadedMetadata() {
  const video = previewVideo.value
  sourceDurationMs.value = video && Number.isFinite(video.duration) ? Math.round(video.duration * 1000) : selectedTrimOutMs.value
  syncVideo()
}

async function startActiveVideo() {
  await nextTick()
  syncVideo()
  const video = previewVideo.value
  if (playing.value && video && activeMedia.value?.mime_type.startsWith('video')) {
    await video.play().catch(() => undefined)
  }
}

function stopPlayback() {
  playing.value = false
  if (playbackTimer !== undefined) cancelAnimationFrame(playbackTimer)
  playbackTimer = undefined
  previewVideo.value?.pause()
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
    if (playheadMs.value >= totalMs.value) stopPlayback()
    else playbackTimer = requestAnimationFrame(tick)
  }
  playbackTimer = requestAnimationFrame(tick)
}

async function renderVariant() {
  await store.renderFinalCut(variant.value)
}

async function resetTimeline() {
  if (!confirm('Reset the timeline to the current shot plan? All timeline edits and overlays will be removed.')) return
  timeline.value = await api.resetTimeline(props.project.id)
  selectedId.value = timeline.value.document.primary[0]?.id ?? ''
  await store.loadProject(props.project.id)
}

watch(() => props.project.id, loadTimeline)
watch(variant, () => {
  stopPlayback()
  emit('variantChange', variant.value)
  void startActiveVideo()
}, { immediate: true })
watch(activeIndex, () => void startActiveVideo())
onMounted(loadTimeline)
onBeforeUnmount(() => {
  stopPlayback()
  finishOverlayEdit()
})
</script>

<template>
  <section class="timeline-editor">
    <header class="editor-header">
      <div><span class="eyebrow">Master edit</span><h2>{{ project.title }}</h2><p>{{ primary.length }} items · {{ (totalMs / 1000).toFixed(1) }}s · {{ saveState }}</p></div>
      <div class="editor-actions">
        <div class="variant-toggle" role="group" aria-label="Output format">
          <button :class="{ active: variant === 'landscape' }" @click="variant = 'landscape'">Landscape 16:9</button>
          <button :class="{ active: variant === 'shorts' }" @click="variant = 'shorts'">Shorts 9:16</button>
        </div>
        <button class="secondary-button" :disabled="saving" @click="saveTimeline"><Save :size="15" />Save</button>
        <button class="primary-button" :disabled="store.renderingFinalCut || !timeline" @click="renderVariant"><LoaderCircle v-if="store.renderingFinalCut" class="spin" :size="15" /><RefreshCw v-else :size="15" />{{ variantExport ? 'Render again' : 'Render' }}</button>
        <a v-if="variantExport" class="secondary-button" :href="variantExport.url" :download="`${project.title}-${variant}.mp4`"><Download :size="15" />MP4</a>
      </div>
    </header>

    <p v-if="localError || store.error" class="form-error">{{ localError || store.error }}</p>
    <div v-if="loading" class="timeline-loading"><LoaderCircle class="spin" :size="22" />Loading timeline</div>
    <template v-else-if="timeline">
      <div class="editor-main">
        <section class="program-monitor" :class="variant">
          <video v-if="activeMedia?.mime_type.startsWith('video')" ref="previewVideo" :key="`${activeMedia.url}-${activeItem?.id}`" :src="activeMedia.url" :style="activeMediaStyle" muted playsinline preload="metadata" @loadedmetadata="handleLoadedMetadata"></video>
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
          <div v-if="variant === 'shorts'" class="shorts-safe-area"></div>
        </section>

        <aside class="timeline-inspector">
          <header><span>Inspector</span><strong>{{ selectedPrimary?.label || selectedText?.text || (selectedImage ? 'Image overlay' : 'Nothing selected') }}</strong></header>
          <template v-if="selectedPrimary">
            <label>Name<input v-model="selectedPrimary.label" @change="scheduleSave" /></label>
            <label v-if="selectedPrimary.kind === 'title'">Text<textarea v-model="selectedPrimary.text" rows="3" @change="scheduleSave"></textarea></label>
            <label>Duration (seconds)<input type="number" min="0.1" step="0.1" :value="selectedPrimary.duration_ms / 1000" @change="resizeSelected" /></label>
            <template v-if="selectedPrimary.kind === 'video'">
              <div class="trim-heading"><span><Scissors :size="13" />Trim</span><button type="button" @click="resetTrim">Reset</button></div>
              <label>In · {{ (selectedPrimary.source_in_ms / 1000).toFixed(2) }}s<input type="range" min="0" :max="trimMaximumMs" step="50" :value="selectedPrimary.source_in_ms" @input="changeTrimIn" /></label>
              <label>Out · {{ (selectedTrimOutMs / 1000).toFixed(2) }}s<input type="range" min="100" :max="trimMaximumMs" step="50" :value="selectedTrimOutMs" @input="changeTrimOut" /></label>
              <div class="trim-actions"><button type="button" @click="setTrimAtPlayhead('in')">Set in</button><button type="button" @click="setTrimAtPlayhead('out')">Set out</button></div>
            </template>
            <label>Fit<select v-model="geometryFor(selectedPrimary).fit" @change="scheduleSave"><option value="cover">Fill frame (crop)</option><option value="contain">Full frame (no crop)</option></select></label>
            <label>Horizontal focus<input v-model.number="geometryFor(selectedPrimary).focal_x" type="range" min="0" max="1" step="0.01" @change="scheduleSave" /></label>
            <label>Vertical focus<input v-model.number="geometryFor(selectedPrimary).focal_y" type="range" min="0" max="1" step="0.01" @change="scheduleSave" /></label>
            <label>Crop zoom<input v-model.number="geometryFor(selectedPrimary).zoom" type="range" min="1" max="3" step="0.05" @change="scheduleSave" /></label>
          </template>
          <template v-else-if="selectedText">
            <label>Text<textarea v-model="selectedText.text" rows="3" @change="scheduleSave"></textarea></label>
            <label>Start (seconds)<input v-model.number="selectedText.start_ms" type="number" min="0" step="100" @change="scheduleSave" /></label>
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
        <button :title="playing ? 'Pause timeline' : 'Play timeline'" :aria-label="playing ? 'Pause timeline' : 'Play timeline'" @click="togglePlayback"><Pause v-if="playing" :size="16" /><Play v-else :size="16" /></button>
        <button title="Undo" :disabled="!history.length" @click="undo"><Undo2 :size="16" /></button>
        <button title="Redo" :disabled="!future.length" @click="redo"><Redo2 :size="16" /></button>
        <span></span>
        <button title="Split at playhead" :disabled="activeItem?.kind !== 'video'" @click="splitAtPlayhead"><Split :size="16" /></button>
        <button title="Move left" @click="moveSelected(-1)"><MoveLeft :size="16" /></button>
        <button title="Move right" @click="moveSelected(1)"><MoveRight :size="16" /></button>
        <button title="Append current storyboard frame" @click="appendStill"><ImagePlus :size="16" /></button>
        <label class="tool-upload" title="Upload and append image"><Upload :size="16" /><input type="file" accept="image/png,image/jpeg,image/webp" @change="uploadAsset($event, true)" /></label>
        <button title="Add title card" @click="addTitle"><AlignCenter :size="16" /></button>
        <button title="Add text overlay" @click="addText"><Type :size="16" /></button>
        <button title="Add image overlay" @click="addImageOverlay()"><ImagePlus :size="16" /></button>
        <label class="tool-upload" title="Upload image overlay"><Upload :size="16" /><input type="file" accept="image/png,image/jpeg,image/webp" @change="uploadAsset($event, false)" /></label>
        <button class="danger-tool" title="Delete selected item" @click="deleteSelection"><Trash2 :size="16" /></button>
        <button class="reset-tool" @click="resetTimeline"><RefreshCw :size="15" />Reset</button>
      </div>

      <div class="timeline-scroll">
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
          <button v-for="(item, index) in primary" :key="item.id" class="timeline-block" :class="[item.kind, { selected: selectedKind === 'primary' && selectedId === item.id }]" :style="{ width: `${item.duration_ms / totalMs * 100}%` }" @click="selectItem('primary', item.id, startAt(index))"><span>{{ item.kind.toUpperCase() }}</span><b>{{ item.label }}</b><small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small></button>
        </div></div>
        <div class="track-row"><strong>IMAGE</strong><div class="overlay-track"><i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i><button v-for="item in timeline.document.image_overlays" :key="item.id" class="overlay-block image" :class="{ selected: selectedKind === 'image' && selectedId === item.id }" :style="{ left: `${item.start_ms / totalMs * 100}%`, width: `${Math.max(2, item.duration_ms / totalMs * 100)}%` }" title="Drag to move image overlay" @pointerdown="beginOverlayEdit($event, 'image', item, 'move')" @click="selectItem('image', item.id, item.start_ms)"><span class="resize-handle start" title="Adjust start" @pointerdown.stop="beginOverlayEdit($event, 'image', item, 'resize-start')"></span><span class="overlay-label">IMAGE</span><small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small><span class="resize-handle end" title="Adjust end" @pointerdown.stop="beginOverlayEdit($event, 'image', item, 'resize-end')"></span></button></div></div>
        <div class="track-row"><strong>TEXT</strong><div class="overlay-track"><i class="track-playhead" :style="{ left: `${playheadPercent}%` }"></i><button v-for="item in timeline.document.text_overlays" :key="item.id" class="overlay-block text" :class="{ selected: selectedKind === 'text' && selectedId === item.id }" :style="{ left: `${item.start_ms / totalMs * 100}%`, width: `${Math.max(2, item.duration_ms / totalMs * 100)}%` }" title="Drag to move text overlay" @pointerdown="beginOverlayEdit($event, 'text', item, 'move')" @click="selectItem('text', item.id, item.start_ms)"><span class="resize-handle start" title="Adjust start" @pointerdown.stop="beginOverlayEdit($event, 'text', item, 'resize-start')"></span><span class="overlay-label">{{ item.text }}</span><small>{{ (item.duration_ms / 1000).toFixed(1) }}s</small><span class="resize-handle end" title="Adjust end" @pointerdown.stop="beginOverlayEdit($event, 'text', item, 'resize-end')"></span></button></div></div>
        </section>
      </div>

      <div v-if="variantExport" class="export-status" :class="{ stale: variantExport.is_stale }"><strong>{{ variant === 'shorts' ? 'Shorts' : 'Landscape' }} export</strong><span>{{ variantExport.width }} × {{ variantExport.height }} · {{ (variantExport.duration_ms / 1000).toFixed(1) }}s</span><em>{{ variantExport.is_stale ? 'Outdated — render again' : 'Up to date' }}</em></div>
    </template>
  </section>
</template>

<style scoped>
.timeline-editor{display:flex;flex:1;flex-direction:column;min-width:0;min-height:0;height:auto;overflow:hidden;background:#f7f8f6;color:#181b19}.editor-header{display:flex;flex:none;align-items:center;justify-content:space-between;gap:18px;padding:12px 20px;border-bottom:1px solid #d8ddd9;background:#fff}.editor-header h2{margin:3px 0;font-size:20px;letter-spacing:0}.editor-header p{margin:0;color:#727a75}.editor-actions,.variant-toggle,.timeline-toolbar{display:flex;align-items:center;gap:8px}.variant-toggle{padding:3px;background:#e9ece9;border-radius:6px}.variant-toggle button{border:0;background:transparent;padding:8px 11px;border-radius:4px;font-weight:700}.variant-toggle button.active{background:#fff;box-shadow:0 1px 3px #0002}.editor-main{display:grid;flex:1;grid-template-columns:minmax(420px,1fr) 290px;min-height:0;border-bottom:1px solid #d8ddd9}.program-monitor{position:relative;display:grid;place-items:center;height:calc(100% - 24px);width:auto;max-width:90%;max-height:480px;margin:12px;background:#111;overflow:hidden;aspect-ratio:16/9;align-self:center;justify-self:center}.program-monitor.shorts{aspect-ratio:9/16;width:auto;max-width:45%;max-height:calc(100% - 24px)}.program-monitor video,.program-monitor>img{width:100%;height:100%;object-fit:cover}.preview-title,.preview-missing{color:#fff;font-weight:800;font-size:30px;text-align:center;padding:30px}.source-badge{position:absolute;left:10px;top:10px;background:#111d;color:#fff;padding:5px 7px;font-size:10px;font-weight:800}.shorts-safe-area{position:absolute;inset:8% 7% 16%;border:1px dashed #fff8;pointer-events:none}.preview-image-overlay{position:absolute;max-height:50%;object-fit:contain}.preview-text-overlay{position:absolute;z-index:3;max-width:82%;padding:7px 11px;color:#fff;font-weight:800;text-align:center}.preview-text-overlay.black{color:#111}.preview-text-overlay.small{font-size:15px}.preview-text-overlay.medium{font-size:22px}.preview-text-overlay.large{font-size:31px}.preview-text-overlay.shadow{text-shadow:0 2px 4px #000}.preview-text-overlay.translucent{background:#000a}.preview-text-overlay.solid,.preview-text-overlay.full-frame{background:#000}.preview-text-overlay.full-frame{inset:0;max-width:none;display:grid;place-items:center}.top,.top-left,.top-right{top:8%}.bottom,.bottom-left,.bottom-right{bottom:10%}.center{top:50%;left:50%;transform:translate(-50%,-50%)}.top,.bottom{left:50%;transform:translateX(-50%)}.top-left,.bottom-left{left:7%}.top-right,.bottom-right{right:7%}.timeline-inspector{min-height:0;padding:14px 18px;border-left:1px solid #d8ddd9;background:#fff;overflow:auto}.timeline-inspector header{display:flex;flex-direction:column;margin-bottom:14px}.timeline-inspector header span{font-size:11px;color:#8a928d;font-weight:800}.timeline-inspector label{display:grid;gap:5px;margin:11px 0;font-size:12px;font-weight:700;color:#59605c}.timeline-inspector input,.timeline-inspector select,.timeline-inspector textarea{width:100%;box-sizing:border-box;border:1px solid #cbd1cd;border-radius:4px;padding:8px;background:#fff;color:#181b19}.timeline-toolbar{flex:none;padding:7px 18px;border-bottom:1px solid #d8ddd9;background:#fff;overflow-x:auto}.timeline-toolbar>span{width:1px;height:24px;background:#d8ddd9}.timeline-toolbar button,.tool-upload{display:flex;align-items:center;justify-content:center;gap:5px;height:30px;min-width:30px;border:1px solid #ccd2ce;background:#fff;border-radius:4px;cursor:pointer}.timeline-toolbar button:disabled{opacity:.35}.tool-upload input{display:none}.danger-tool{color:#b7332c!important}.reset-tool{margin-left:auto;padding:0 9px}.playhead-scrubber{display:grid;grid-template-columns:55px 1fr 55px;align-items:center;gap:8px;padding:8px 18px;background:#edf0ed;font-size:11px}.playhead-scrubber input{width:100%}.tracks{padding:10px 18px 18px;overflow-x:auto;background:#242a26}.track-row{display:grid;grid-template-columns:70px minmax(1000px,1fr);min-height:36px;border-bottom:1px solid #3e4641}.track-row>strong{padding:12px 8px;color:#9fa8a2;font-size:10px}.track-content{display:flex}.timeline-block{flex:none;min-width:70px;border:0;border-right:2px solid #242a26;background:#526159;color:#fff;text-align:left;padding:5px 8px;overflow:hidden}.timeline-block.image{background:#655d4b}.timeline-block.title{background:#5f4d58}.timeline-block.selected,.overlay-block.selected{outline:2px solid #efc04f;outline-offset:-2px}.timeline-block span{display:block;font-size:8px;opacity:.7}.timeline-block b,.timeline-block small{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.timeline-block b{font-size:11px}.timeline-block small{font-size:9px;opacity:.7}.overlay-track{position:relative;background:#303732}.overlay-block{position:absolute;top:5px;height:26px;min-width:20px;border:0;border-radius:3px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:9px;font-weight:800}.overlay-block.image{background:#477c69;color:#fff}.overlay-block.text{background:#9b7545;color:#fff}.export-status{display:flex;flex:none;align-items:center;gap:16px;padding:8px 20px;background:#e4f0e9;color:#225d43}.export-status em{margin-left:auto;font-style:normal;font-size:12px}.export-status.stale{background:#fff0d9;color:#8a4c0f}.timeline-loading{display:flex;gap:8px;align-items:center;justify-content:center;min-height:420px}.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:900px){.timeline-editor{overflow:auto}.editor-header{align-items:flex-start;flex-direction:column}.editor-actions{flex-wrap:wrap}.editor-main{grid-template-columns:1fr;min-height:480px}.timeline-inspector{border-left:0;border-top:1px solid #d8ddd9}.program-monitor.shorts{width:auto;max-width:70%}}
.trim-heading{display:flex;align-items:center;justify-content:space-between;margin-top:14px;padding-top:12px;border-top:1px solid #e1e5e2}.trim-heading span{display:flex;align-items:center;gap:5px;font-size:11px;font-weight:800;text-transform:uppercase}.trim-heading button,.trim-actions button{border:1px solid #cbd1cd;border-radius:4px;background:#fff;padding:6px 9px;font-size:11px;font-weight:700}.trim-actions{display:grid;grid-template-columns:1fr 1fr;gap:6px}.playhead-scrubber{grid-template-columns:150px minmax(180px,1fr) 80px}.playhead-scrubber span,.playhead-scrubber strong{white-space:nowrap}
.timeline-scroll{overflow-x:auto;background:#242a26}.timeline-scroll .playhead-scrubber{grid-template-columns:70px minmax(1000px,1fr);gap:0;min-width:1106px;padding:30px 18px 8px;overflow:visible;background:#e9edea;border-bottom:1px solid #cfd5d1}.playhead-scrubber>strong{align-self:start;padding:1px 8px 0;color:#59625c;font-size:10px}.timeline-range{position:relative;min-width:1000px;height:48px}.timeline-input{-webkit-appearance:none;appearance:none;position:absolute;inset:0 0 auto;width:100%!important;height:20px;margin:0!important;background:transparent;cursor:ew-resize;z-index:3}.timeline-input::-webkit-slider-runnable-track{height:6px;border-radius:3px;background:linear-gradient(to right,#d7a72e 0 var(--playhead),#aab2ad var(--playhead) 100%);box-shadow:inset 0 0 0 1px #7e8882}.timeline-input::-webkit-slider-thumb{-webkit-appearance:none;appearance:none;width:16px;height:22px;margin-top:-8px;border:2px solid #242a26;border-radius:4px;background:#efc04f;box-shadow:0 1px 3px #0005;transition:transform .12s ease,box-shadow .12s ease}.timeline-input:hover::-webkit-slider-thumb{transform:scale(1.08);box-shadow:0 2px 6px #0006}.timeline-input:focus-visible{outline:none}.timeline-input:focus-visible::-webkit-slider-thumb{box-shadow:0 0 0 3px #fff,0 0 0 5px #b88917}.timeline-input::-moz-range-track{height:6px;border:1px solid #7e8882;border-radius:3px;background:#aab2ad}.timeline-input::-moz-range-progress{height:6px;border-radius:3px;background:#d7a72e}.timeline-input::-moz-range-thumb{width:14px;height:20px;border:2px solid #242a26;border-radius:4px;background:#efc04f;box-shadow:0 1px 3px #0005}.playhead-time{position:absolute;top:-11px;z-index:4;transform:translate(-50%,-100%);padding:4px 7px;border-radius:4px;background:#202622;color:#fff;font-size:10px;font-weight:800;line-height:1.2;pointer-events:none}.playhead-time:after{position:absolute;left:50%;bottom:-4px;width:8px;height:8px;transform:translateX(-50%) rotate(45deg);background:#202622;content:""}.timeline-ticks{position:absolute;top:24px;left:0;width:100%;height:24px;pointer-events:none}.timeline-ticks span{position:absolute;top:0;transform:translateX(-50%);color:#657069;font-size:9px;white-space:nowrap}.timeline-ticks span:first-child{transform:none}.timeline-ticks span:last-child{transform:translateX(-100%)}.timeline-ticks i{display:block;width:1px;height:6px;margin:0 auto 2px;background:#8e9892}.timeline-ticks em{font-style:normal}.timeline-scroll .tracks{min-width:1106px;padding:10px 18px 18px;overflow:visible}.track-content,.overlay-track{position:relative;width:100%;min-width:1000px}.track-content{display:flex}.overlay-track{display:block}.timeline-block{min-width:0;transition:filter .12s ease}.timeline-block:hover{filter:brightness(1.12)}.track-playhead{position:absolute;top:0;bottom:0;z-index:5;width:2px;transform:translateX(-1px);background:#efc04f;box-shadow:0 0 0 1px #1c211e55;pointer-events:none}
.overlay-block{display:flex;align-items:center;gap:4px;padding:0 9px;touch-action:none;cursor:grab;user-select:none}.overlay-block:active{cursor:grabbing}.overlay-block .overlay-label{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;text-align:left}.overlay-block small{flex:none;font-size:8px;font-weight:700;opacity:.72}.resize-handle{position:absolute;top:3px;bottom:3px;width:7px;border-radius:2px;cursor:ew-resize;opacity:0;transition:opacity .12s ease,background .12s ease}.resize-handle.start{left:2px}.resize-handle.end{right:2px}.overlay-block:hover .resize-handle,.overlay-block.selected .resize-handle{opacity:1;background:#ffffffb8}.overlay-block.selected .resize-handle:hover{background:#fff}.overlay-block.selected{z-index:4}
</style>
