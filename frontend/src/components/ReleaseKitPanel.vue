<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  Camera,
  Check,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Download,
  Eye,
  Image as ImageIcon,
  ImagePlus,
  Layers,
  LoaderCircle,
  Palette,
  Play,
  Plus,
  RefreshCw,
  Sparkles,
  Trash2,
  Type,
  Upload,
  Wand2,
  X,
  Zap,
} from '@lucide/vue'
import {
  api,
  type Job,
  type OutputVariant,
  type ProjectDetail,
  type ReleaseCopyProvider,
  type ReleaseCoverProvider,
  type ReleaseFrameSegment,
  type ReleaseKit,
  type ReleaseKitCover,
  type ReleaseKitCoverIdea,
  type ReleaseKitUpdate,
} from '@/lib/api'

const props = defineProps<{
  project: ProjectDetail
  variant: OutputVariant
  episodeId?: string | null
  compilationId?: string | null
  asModal?: boolean
  /** Bumped by the timeline after it captures a frame so the panel refetches. */
  refreshKey?: number
  /** Visual clips on the primary track; enables the in-panel frame scrubber. */
  segments?: ReleaseFrameSegment[]
  /** Where the scrubber starts, normally the timeline playhead. */
  initialMs?: number
}>()

const emit = defineEmits<{
  close: []
  changed: [kit: ReleaseKit]
}>()

const MAX_FRAMES = 8

// ── Tab Navigation ────────────────────────────────────────────────────────────
type PanelTab = 'studio' | 'metadata' | 'frames'
const activeTab = ref<PanelTab>('studio')

const kit = ref<ReleaseKit | null>(null)
const loading = ref(false)
const error = ref('')
const saving = ref(false)
const brief = ref('')
const description = ref('')
const tags = ref<string[]>([])
const tagDraft = ref('')
const newTitle = ref('')
const titleDrafts = ref<Record<string, string>>({})
const headlineDrafts = ref<Record<string, string>>({})
const copyProvider = ref<'' | ReleaseCopyProvider>('')
const coverProvider = ref<'' | ReleaseCoverProvider>('')
const suggestJob = ref<Job | null>(null)
const customPrompt = ref('')
const customHeadline = ref('')
const startingCover = ref<string | null>(null)
const uploadInput = ref<HTMLInputElement | null>(null)
let pollTimer: number | null = null
let disposed = false

// ── Thumbnail Studio State ───────────────────────────────────────────────────
type MockupMode = 'art' | 'youtube' | 'shorts'
const mockupMode = ref<MockupMode>(props.variant === 'shorts' ? 'shorts' : 'youtube')
const showSafeZone = ref(false)
const promotingFrameId = ref<string | null>(null)
const frameStyle = ref<'punch' | 'vivid' | 'drama' | 'warm' | 'natural'>('punch')
const autoImporting = ref(false)

// ── Text / Headline Overlay Tool ─────────────────────────────────────────────
const showTextOverlayModal = ref(false)
const overlayText = ref('')
const overlayTopText = ref('')
const overlayBottomText = ref('')
const isDualHook = ref(false)
const overlayStyle = ref<'dual_combo' | 'gamer_red' | 'neon_clutch' | 'impact' | 'yellow' | 'red_alert' | 'gold'>('dual_combo')
const overlayPosition = ref<'top_center' | 'top_left' | 'center' | 'bottom_left'>('bottom_left')
const bakingOverlay = ref(false)
const overlayCanvas = ref<HTMLCanvasElement | null>(null)

interface ThumbnailHookSuggestion {
  headline: string
  secondary: string
  full: string
  tag: string
}

function deriveSuggestedHooks(text: string): ThumbnailHookSuggestion[] {
  const clean = (text || '').replace(/[^\w\s']/g, ' ').trim()
  const words = clean.split(/\s+/).filter(Boolean)
  const lower = clean.toLowerCase()
  const list: ThumbnailHookSuggestion[] = []
  const seen = new Set<string>()

  function add(headline: string, secondary = '', tag = 'Viral Hook') {
    headline = headline.trim().toUpperCase().slice(0, 40)
    secondary = secondary.trim().toUpperCase().slice(0, 30)
    if (!headline) return
    const full = secondary ? `${headline} // ${secondary}` : headline
    if (seen.has(full.toLowerCase())) return
    seen.add(full.toLowerCase())
    list.push({ headline, secondary, full, tag })
  }

  if (words.length >= 1 && words.length <= 5) {
    const direct = words.join(' ').toUpperCase()
    const cleanedDirect = direct.replace(/^(THEY|HE|SHE|WE|I)\s+/, '')
    if (cleanedDirect && cleanedDirect !== direct) {
      const clutch = /push|rush|fight|game|clutch|early/.test(lower) ? 'I CLUTCHED' : 'BIG MISTAKE'
      add(cleanedDirect, clutch, 'Viral Dual Hook')
      add(direct, '', 'Direct Punch')
    } else {
      add(direct, /push|rush|fight|clutch|early/.test(lower) ? 'I CLUTCHED' : '', 'Direct Punch')
    }
  }

  if (/push|rush|early|clutch|flank|attack|kill|shot/.test(lower)) {
    add('PUSHED TOO EARLY', 'I CLUTCHED', 'Gamer / Action')
    add('THEY PUSHED TOO EARLY', '', 'High CTR')
    add('I CLUTCHED', '', 'Punchline')
    add('BIG MISTAKE', 'NO MERCY', 'Stakes')
    add('INSTANT REGRET', '', 'Emotion')
  }

  if (/lie|lied|truth|secret|hide|cheated|fake|betray/.test(lower)) {
    add('WHO LIED?', 'THE TRUTH', 'Suspense')
    add('CAUGHT RED HANDED', '', 'Drama')
    add('BETRAYED', 'NO ESCAPE', 'Stakes')
    add('THE REAL TRUTH', '', 'Curiosity')
  }

  if (/argue|fight|confront|break|broke|end|leave|crying/.test(lower)) {
    add("IT'S OVER", 'NO WAY BACK', 'High Stakes')
    add('HE SAID WHAT?!', '', 'Shock')
    add('POINT OF NO RETURN', '', 'Drama')
  }

  const fallbacks: [string, string, string][] = [
    ['PUSHED TOO EARLY', 'I CLUTCHED', 'Action Hook'],
    ["IT'S OVER", 'NO WAY BACK', 'High Stakes'],
    ['BIG MISTAKE', 'THEY REGRETTED IT', 'Stakes'],
    ['WHO LIED?', '', 'Suspense'],
    ['NO ESCAPE', '', 'Urgency'],
    ['THE TRUTH', '', 'Mystery'],
  ]
  for (const [h, s, t] of fallbacks) {
    if (list.length >= 6) break
    add(h, s, t)
  }
  return list.slice(0, 6)
}

const remoteHooks = ref<ThumbnailHookSuggestion[]>([])
const loadingHooks = ref(false)

const suggestedHooks = computed(() => {
  if (remoteHooks.value.length) return remoteHooks.value
  return deriveSuggestedHooks(customPrompt.value || kit.value?.brief || '')
})

async function fetchRemoteHooks() {
  if (!kit.value || loadingHooks.value) return
  loadingHooks.value = true
  try {
    const res = await api.suggestReleaseKitHooks(kit.value.id, customPrompt.value || kit.value.brief)
    if (res.hooks && res.hooks.length) {
      remoteHooks.value = res.hooks
    }
  } catch {
    // Fall back to local
  } finally {
    loadingHooks.value = false
  }
}

function autoFillTopHook() {
  const top = suggestedHooks.value[0]?.full
  if (top) customHeadline.value = top
}

const THUMBNAIL_PRESETS = [
  { id: 'clutch', label: '⚡ Action / Clutch', prompt: 'Intense first-person gaming combat scene, extreme urgency, dynamic motion blur, high contrast action lighting', headline: 'PUSHED TOO EARLY // I CLUTCHED' },
  { id: 'stakes', label: '🔥 High Stakes Drama', prompt: 'Extreme dramatic tension, high stakes confrontation, dramatic rim lighting, intense facial expression, cinematic movie still', headline: "IT'S OVER" },
  { id: 'shock', label: '😱 Shock & Discovery', prompt: 'Extreme emotional reaction, wide eyes, breathless discovery, shallow depth of field, high-contrast lighting', headline: 'SHE KNEW' },
  { id: 'poster', label: '🎬 Cinematic Poster', prompt: 'Iconic film poster composition, heroic framing, deep rich shadows, atmospheric color grading, cinematic haze', headline: 'THE TRUTH' },
  { id: 'mystery', label: '❓ Moody Mystery', prompt: 'Moody silhouette against dark rainy backdrop, enigmatic expression, cool vs warm light contrast, shadowy intrigue', headline: 'NO ESCAPE' },
]

function applyPreset(preset: typeof THUMBNAIL_PRESETS[0]) {
  customPrompt.value = preset.prompt
  customHeadline.value = preset.headline
}

// ── Frame scrubber ────────────────────────────────────────────────────────────
const FRAME_MS = 1000 / 30
const seekMs = ref(Math.max(0, Math.round(props.initialMs ?? 0)))
const previewVideo = ref<HTMLVideoElement | null>(null)
const addingFrame = ref(false)
const segments = computed(() => props.segments || [])
const scrubTotalMs = computed(() => segments.value.reduce((end, item) => Math.max(end, item.start_ms + item.duration_ms), 0))
const seekSegment = computed(() =>
  segments.value.find((item) => seekMs.value >= item.start_ms && seekMs.value < item.start_ms + item.duration_ms) || null,
)
const seekSourceMs = computed(() =>
  seekSegment.value ? Math.max(0, Math.round(seekSegment.value.source_in_ms + seekMs.value - seekSegment.value.start_ms)) : 0,
)
const seekIsVideo = computed(() => !!seekSegment.value?.mime_type.startsWith('video'))

function clampSeek(ms: number) {
  const last = Math.max(0, scrubTotalMs.value - 1)
  seekMs.value = Math.min(last, Math.max(0, Math.round(ms)))
}

function stepSeek(frames: number) {
  clampSeek(seekMs.value + frames * FRAME_MS)
}

function jumpSegment(direction: -1 | 1) {
  const list = segments.value
  if (!list.length) return
  const index = seekSegment.value ? list.indexOf(seekSegment.value) : -1
  if (direction < 0 && seekSegment.value && seekMs.value - seekSegment.value.start_ms > 250) {
    clampSeek(seekSegment.value.start_ms)
    return
  }
  const target = list[Math.min(list.length - 1, Math.max(0, index + direction))]
  if (target) clampSeek(target.start_ms)
}

function applyVideoSeek() {
  const video = previewVideo.value
  if (!video || !seekIsVideo.value || video.readyState < 1) return
  const target = seekSourceMs.value / 1000
  if (Math.abs(video.currentTime - target) > 0.01) video.currentTime = Math.min(target, Math.max(0, (video.duration || target) - 0.05))
}

async function addSeekFrame() {
  const segment = seekSegment.value
  if (!kit.value || !segment || addingFrame.value) return
  addingFrame.value = true
  error.value = ''
  try {
    adopt(await api.addReleaseKitFrame(kit.value.id, {
      source: segment.source,
      source_ms: seekSourceMs.value,
      timeline_ms: Math.round(seekMs.value),
      label: segment.label,
    }))
  } catch (cause) {
    error.value = message(cause, 'Could not add the frame')
  } finally {
    addingFrame.value = false
  }
}

function seekToFrame(timelineMs: number) {
  clampSeek(timelineMs)
  activeTab.value = 'frames'
}

function formatPrecise(ms: number) {
  const total = Math.max(0, ms) / 1000
  const minutes = Math.floor(total / 60)
  return `${minutes}:${(total - minutes * 60).toFixed(2).padStart(5, '0')}`
}

watch([seekSourceMs, () => seekSegment.value?.url], applyVideoSeek, { flush: 'post' })

const suggesting = computed(() => !!suggestJob.value && ['queued', 'running'].includes(suggestJob.value.status))
const generatingCovers = computed(() => (kit.value?.covers || []).some((item) => item.status === 'generating'))
const readyCovers = computed(() => (kit.value?.covers || []).filter((item) => item.status === 'ready'))
const variantLabel = computed(() => (props.variant === 'shorts' ? 'Shorts 9:16' : 'Landscape 16:9'))

const selectedCover = computed<ReleaseKitCover | null>(() => {
  if (!kit.value?.covers.length) return null
  return kit.value.covers.find((c) => c.id === kit.value?.selected_cover_id) || kit.value.covers[0] || null
})

const activeDisplayTitle = computed(() => {
  if (!kit.value) return props.project.title
  const selected = kit.value.title_options.find((t) => t.id === kit.value?.selected_title_id)
  return selected?.text || kit.value.title_options[0]?.text || props.project.title
})

function message(cause: unknown, fallback: string) {
  return cause instanceof Error ? cause.message : fallback
}

function syncLocal(next: ReleaseKit) {
  kit.value = next
  brief.value = next.brief
  description.value = next.description
  tags.value = [...next.tags]
  titleDrafts.value = Object.fromEntries(next.title_options.map((item) => [item.id, item.text]))
  headlineDrafts.value = Object.fromEntries(next.cover_ideas.map((item) => [item.id, item.headline]))
}

function adopt(next: ReleaseKit) {
  syncLocal(next)
  emit('changed', next)
}

async function load() {
  loading.value = !kit.value
  error.value = ''
  try {
    adopt(await api.releaseKit(props.project.id, {
      variant: props.variant,
      episodeId: props.episodeId,
      compilationId: props.compilationId,
    }))
    const active = await api.projectJobs(props.project.id, true).catch(() => [] as Job[])
    const running = active.find((job) => job.kind === 'release_copy' && job.attempts?.[0]?.release_kit_id === kit.value?.id)
    if (running) watchSuggest(running)
    schedulePoll()
  } catch (cause) {
    error.value = message(cause, 'Could not load the release kit')
  } finally {
    loading.value = false
  }
}

async function save(patch: ReleaseKitUpdate) {
  if (!kit.value) return
  saving.value = true
  error.value = ''
  try {
    adopt(await api.updateReleaseKit(kit.value.id, patch))
  } catch (cause) {
    error.value = message(cause, 'Could not save the release kit')
  } finally {
    saving.value = false
  }
}

function schedulePoll() {
  if (pollTimer !== null || disposed || !generatingCovers.value) return
  pollTimer = window.setTimeout(async () => {
    pollTimer = null
    if (!kit.value || disposed) return
    try {
      const next = await api.releaseKit(props.project.id, {
        variant: props.variant,
        episodeId: props.episodeId,
        compilationId: props.compilationId,
      })
      if (kit.value && next.id === kit.value.id) {
        kit.value = { ...kit.value, covers: next.covers, selected_cover_id: next.selected_cover_id }
        emit('changed', kit.value)
      }
    } catch {
      // Keep polling
    }
    schedulePoll()
  }, 2500)
}

async function watchSuggest(job: Job) {
  suggestJob.value = job
  while (!disposed && suggestJob.value && ['queued', 'running'].includes(suggestJob.value.status)) {
    await new Promise((resolve) => window.setTimeout(resolve, 1200))
    try {
      suggestJob.value = await api.job(job.id)
    } catch (cause) {
      error.value = message(cause, 'Lost track of the suggestion job')
      return
    }
  }
  if (disposed || !suggestJob.value) return
  if (suggestJob.value.status === 'completed') {
    await load()
  } else if (suggestJob.value.status === 'failed') {
    error.value = suggestJob.value.error || 'The AI could not write suggestions'
  }
}

async function suggest() {
  if (!kit.value || suggesting.value) return
  error.value = ''
  if (brief.value !== kit.value.brief) await save({ brief: brief.value })
  try {
    await watchSuggest(await api.suggestReleaseCopy(kit.value.id, copyProvider.value || null))
  } catch (cause) {
    error.value = message(cause, 'Could not start suggestions')
  }
}

function saveBrief() {
  if (kit.value && brief.value !== kit.value.brief) save({ brief: brief.value })
}

function saveDescription() {
  if (kit.value && description.value !== kit.value.description) save({ description: description.value })
}

function selectTitle(id: string) {
  if (kit.value?.selected_title_id !== id) save({ selected_title_id: id })
}

function saveTitles(options = kit.value?.title_options || []) {
  save({ title_options: options.map((item) => ({ ...item, text: titleDrafts.value[item.id] ?? item.text })) })
}

function commitTitle(id: string) {
  const original = kit.value?.title_options.find((item) => item.id === id)
  const draft = (titleDrafts.value[id] || '').trim()
  if (!original || draft === original.text) return
  if (!draft) {
    titleDrafts.value[id] = original.text
    return
  }
  saveTitles()
}

function removeTitle(id: string) {
  saveTitles((kit.value?.title_options || []).filter((item) => item.id !== id))
}

async function addTitle() {
  const text = newTitle.value.trim()
  if (!text || !kit.value) return
  await save({
    title_options: [...kit.value.title_options, { text, angle: 'custom', custom: true }],
  })
  const added = kit.value?.title_options[kit.value.title_options.length - 1]
  if (added) await save({ selected_title_id: added.id })
  newTitle.value = ''
}

function addTags(raw: string) {
  const additions = raw.split(',').map((item) => item.trim().replace(/^#/, '')).filter(Boolean)
  if (!additions.length) return
  const seen = new Set(tags.value.map((item) => item.toLowerCase()))
  const merged = [...tags.value]
  for (const tag of additions) {
    if (!seen.has(tag.toLowerCase())) {
      seen.add(tag.toLowerCase())
      merged.push(tag)
    }
  }
  tagDraft.value = ''
  save({ tags: merged })
}

function onTagKey(event: KeyboardEvent) {
  if (event.key === 'Enter' || event.key === ',') {
    event.preventDefault()
    addTags(tagDraft.value)
  } else if (event.key === 'Backspace' && !tagDraft.value && tags.value.length) {
    save({ tags: tags.value.slice(0, -1) })
  }
}

function removeTag(tag: string) {
  save({ tags: tags.value.filter((item) => item !== tag) })
}

function commitHeadline(idea: ReleaseKitCoverIdea) {
  const draft = (headlineDrafts.value[idea.id] ?? idea.headline).trim()
  if (draft === idea.headline || !kit.value) return
  save({ cover_ideas: kit.value.cover_ideas.map((item) => (item.id === idea.id ? { ...item, headline: draft } : item)) })
}

async function startCover(key: string, input: { idea_id?: string | null; headline?: string | null; prompt?: string | null }) {
  if (!kit.value) return
  startingCover.value = key
  error.value = ''
  try {
    await api.generateReleaseCover(kit.value.id, { ...input, provider: coverProvider.value || null })
    syncLocal(await api.releaseKit(props.project.id, {
      variant: props.variant,
      episodeId: props.episodeId,
      compilationId: props.compilationId,
    }))
    schedulePoll()
  } catch (cause) {
    error.value = message(cause, 'Could not start the cover')
  } finally {
    startingCover.value = null
  }
}

function generateFromIdea(idea: ReleaseKitCoverIdea) {
  startCover(idea.id, { idea_id: idea.id, headline: (headlineDrafts.value[idea.id] ?? idea.headline).trim() })
}

function generateCustom() {
  const prompt = customPrompt.value.trim()
  if (!prompt) return
  let headline = customHeadline.value.trim()
  if (!headline) {
    const top = suggestedHooks.value[0]?.full
    if (top) {
      headline = top
      customHeadline.value = top
    }
  }
  startCover('custom', { prompt, headline })
}

function regenerate(cover: ReleaseKitCover) {
  startCover(cover.id, { idea_id: cover.idea_id, headline: cover.headline, prompt: cover.idea_id ? null : cover.prompt || cover.concept })
}

function selectCover(cover: ReleaseKitCover) {
  if (cover.status === 'ready' && kit.value?.selected_cover_id !== cover.id) save({ selected_cover_id: cover.id })
}

async function removeCover(cover: ReleaseKitCover) {
  if (!kit.value) return
  try {
    adopt(await api.deleteReleaseCover(kit.value.id, cover.id))
  } catch (cause) {
    error.value = message(cause, 'Could not delete the cover')
  }
}

async function removeFrame(frameId: string) {
  if (!kit.value) return
  try {
    adopt(await api.deleteReleaseKitFrame(kit.value.id, frameId))
  } catch (cause) {
    error.value = message(cause, 'Could not remove the frame')
  }
}

async function uploadCover(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !kit.value) return
  try {
    adopt(await api.uploadReleaseCover(kit.value.id, file))
  } catch (cause) {
    error.value = message(cause, 'Could not upload the cover')
  }
}

// ── ⚡ 1-Click Frame to Thumbnail Promotion ──────────────────────────────────
async function handlePromoteFrame(frameId: string, style?: 'natural' | 'punch' | 'vivid' | 'drama' | 'warm') {
  if (!kit.value || promotingFrameId.value) return
  promotingFrameId.value = frameId
  error.value = ''
  try {
    adopt(await api.promoteReleaseKitFrame(kit.value.id, frameId, {
      style: style || frameStyle.value,
      headline: customHeadline.value || null,
    }))
  } catch (cause) {
    error.value = message(cause, 'Could not promote frame to thumbnail')
  } finally {
    promotingFrameId.value = null
  }
}

async function handleAutoImportFrames() {
  if (!kit.value || autoImporting.value) return
  autoImporting.value = true
  error.value = ''
  try {
    adopt(await api.autoImportReleaseKitFrames(kit.value.id))
  } catch (cause) {
    error.value = message(cause, 'Could not auto-import shot frames')
  } finally {
    autoImporting.value = false
  }
}

// ── 🎨 Text / Headline Canvas Compositing Tool ───────────────────────────────
function openTextOverlayTool() {
  if (!selectedCover.value?.url) return
  const initial = (selectedCover.value.headline || customHeadline.value || activeDisplayTitle.value.slice(0, 30)).trim()
  if (initial.includes(' // ')) {
    const parts = initial.split(' // ')
    overlayTopText.value = (parts[0] || '').trim()
    overlayBottomText.value = (parts[1] || '').trim()
    overlayText.value = initial
    isDualHook.value = true
    overlayStyle.value = 'dual_combo'
  } else {
    overlayText.value = initial
    overlayTopText.value = initial
    overlayBottomText.value = 'I CLUTCHED'
    isDualHook.value = false
    overlayStyle.value = 'gamer_red'
  }
  showTextOverlayModal.value = true
  nextTick(() => renderOverlayPreview())
}

function renderOverlayPreview() {
  const canvas = overlayCanvas.value
  if (!canvas || !selectedCover.value?.url) return
  const img = new Image()
  img.crossOrigin = 'anonymous'
  img.onload = () => {
    const isShorts = props.variant === 'shorts'
    canvas.width = isShorts ? 1080 : 1280
    canvas.height = isShorts ? 1920 : 720
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const context: CanvasRenderingContext2D = ctx

    context.drawImage(img, 0, 0, canvas.width, canvas.height)

    function drawStyledText(
      text: string,
      x: number,
      y: number,
      fontSize: number,
      fillColor: string,
      strokeColor: string,
      glowColor?: string,
      underlineColor?: string,
    ) {
      if (!text) return
      context.save()
      context.font = `900 ${fontSize}px "Impact", "Arial Black", -apple-system, sans-serif`
      context.textBaseline = 'middle'
      context.textAlign = 'center'
      context.lineJoin = 'miter'
      context.miterLimit = 2

      const strokeWidth = Math.round(fontSize * 0.22)
      const textMetrics = context.measureText(text)

      // Outer glow / splatter pass
      if (glowColor) {
        context.save()
        context.shadowColor = glowColor
        context.shadowBlur = 36
        context.lineWidth = strokeWidth + 6
        context.strokeStyle = glowColor
        context.strokeText(text, x, y)
        context.restore()
      }

      // Drop shadow pass
      context.save()
      context.shadowColor = 'rgba(0,0,0,0.92)'
      context.shadowBlur = 18
      context.shadowOffsetX = 4
      context.shadowOffsetY = 6
      context.lineWidth = strokeWidth
      context.strokeStyle = strokeColor
      context.strokeText(text, x, y)
      context.restore()

      // Primary stroke
      context.lineWidth = strokeWidth
      context.strokeStyle = strokeColor
      context.strokeText(text, x, y)

      // Primary fill
      context.fillStyle = fillColor
      context.fillText(text, x, y)

      // Underline paint bar
      if (underlineColor) {
        const barHeight = Math.max(7, Math.round(fontSize * 0.16))
        const startX = x - textMetrics.width * 0.54
        const barWidth = textMetrics.width * 1.08
        context.save()
        context.fillStyle = underlineColor
        context.shadowColor = 'rgba(0,0,0,0.85)'
        context.shadowBlur = 12
        context.shadowOffsetY = 4
        context.beginPath()
        context.moveTo(startX, y + fontSize * 0.58)
        context.lineTo(startX + barWidth * 0.96, y + fontSize * 0.54)
        context.lineTo(startX + barWidth, y + fontSize * 0.58 + barHeight)
        context.lineTo(startX + barWidth * 0.04, y + fontSize * 0.58 + barHeight * 1.1)
        context.closePath()
        context.fill()
        context.restore()
      }

      context.restore()
    }

    if (isDualHook.value || overlayStyle.value === 'dual_combo') {
      const top = (overlayTopText.value || 'PUSHED TOO EARLY').toUpperCase().trim()
      const bottom = (overlayBottomText.value || 'I CLUTCHED').toUpperCase().trim()
      const topSize = Math.round(canvas.height * (isShorts ? 0.065 : 0.13))
      const bottomSize = Math.round(canvas.height * (isShorts ? 0.055 : 0.11))

      const topY = canvas.height * (isShorts ? 0.18 : 0.18)
      const bottomY = canvas.height * (isShorts ? 0.80 : 0.82)
      const centerX = canvas.width * 0.5

      // Top: White text + blood-red splatter glow (#FF1E27)
      drawStyledText(top, centerX, topY, topSize, '#FFFFFF', '#0B0B0E', '#FF1E27')

      // Bottom: Neon yellow text (#FFE600) + paint underline
      drawStyledText(bottom, centerX, bottomY, bottomSize, '#FFE600', '#0B0B0E', undefined, '#FFE600')
    } else {
      const rawText = overlayText.value.trim()
      if (!rawText) return

      const lines = rawText.toUpperCase().split('\n').map((l) => l.trim()).filter(Boolean)
      if (!lines.length) return

      const fontSize = Math.round(canvas.height * (isShorts ? 0.058 : 0.115))
      const lineHeight = fontSize * 1.15
      const totalBlockHeight = lines.length * lineHeight

      let startY = canvas.height * 0.5
      let startX = canvas.width * 0.5

      if (overlayPosition.value === 'top_center') {
        startY = canvas.height * (isShorts ? 0.18 : 0.16)
        startX = canvas.width * 0.5
      } else if (overlayPosition.value === 'top_left') {
        startY = canvas.height * (isShorts ? 0.18 : 0.16)
        startX = canvas.width * 0.5
      } else if (overlayPosition.value === 'bottom_left') {
        startY = canvas.height * (isShorts ? 0.76 : 0.78)
        startX = canvas.width * 0.5
      }

      let fill = '#FFFFFF'
      let stroke = '#000000'
      let glow: string | undefined = undefined
      let underline: string | undefined = undefined

      if (overlayStyle.value === 'gamer_red') {
        fill = '#FFFFFF'
        stroke = '#0B0B0E'
        glow = '#FF1E27'
      } else if (overlayStyle.value === 'neon_clutch') {
        fill = '#FFE600'
        stroke = '#0B0B0E'
        underline = '#FFE600'
      } else if (overlayStyle.value === 'yellow') {
        fill = '#FFE600'
        stroke = '#000000'
      } else if (overlayStyle.value === 'red_alert') {
        fill = '#FF2A2A'
        stroke = '#FFFFFF'
      } else if (overlayStyle.value === 'gold') {
        fill = '#FAD961'
        stroke = '#1A1404'
      }

      lines.forEach((line, index) => {
        const y = startY + index * lineHeight - totalBlockHeight / 2 + lineHeight / 2
        drawStyledText(line, startX, y, fontSize, fill, stroke, glow, index === lines.length - 1 ? underline : undefined)
      })
    }
  }
  img.src = selectedCover.value.url
}

watch([overlayText, overlayTopText, overlayBottomText, isDualHook, overlayStyle, overlayPosition], () => {
  if (showTextOverlayModal.value) renderOverlayPreview()
})

async function applyBakedOverlay() {
  const canvas = overlayCanvas.value
  if (!canvas || !kit.value || bakingOverlay.value) return
  bakingOverlay.value = true
  error.value = ''
  try {
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.94))
    if (!blob) throw new Error('Could not render image canvas')
    const file = new File([blob], `cover-headline-${Date.now()}.jpg`, { type: 'image/jpeg' })
    adopt(await api.uploadReleaseCover(kit.value.id, file))
    showTextOverlayModal.value = false
  } catch (cause) {
    error.value = message(cause, 'Could not save the overlay cover')
  } finally {
    bakingOverlay.value = false
  }
}

function coverDownloadName(cover: ReleaseKitCover) {
  const base = (kit.value?.scope_label || props.project.title).replace(/[^\w-]+/g, '-').replace(/^-|-$/g, '')
  return `${base || 'cover'}-${props.variant}-${cover.id}.jpg`
}

function formatTimecode(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

watch(() => [props.variant, props.episodeId, props.compilationId], () => {
  kit.value = null
  suggestJob.value = null
  load()
})
watch(() => props.refreshKey, () => load())
watch(generatingCovers, (value) => value && schedulePoll())

onMounted(load)
onBeforeUnmount(() => {
  disposed = true
  if (pollTimer !== null) window.clearTimeout(pollTimer)
})
</script>

<template>
  <section class="release-kit" :class="{ 'is-modal': props.asModal, shorts: props.variant === 'shorts' }" aria-label="Release kit">
    <!-- Main Header -->
    <header class="kit-header">
      <div class="header-left">
        <span class="kit-eyebrow">Release studio · {{ variantLabel }}</span>
        <h3>{{ kit?.scope_label || 'Loading…' }}</h3>
        <p>Package your film for release: generate high-CTR thumbnails, select video frames, and craft viral titles & metadata.</p>
      </div>
      <div class="kit-header-actions">
        <LoaderCircle v-if="saving" class="spin muted" :size="15" aria-label="Saving" />
        <button v-if="props.asModal" type="button" class="icon-btn" aria-label="Close release kit" @click="emit('close')"><X :size="18" /></button>
      </div>
    </header>

    <!-- Top Mode Switcher Tabs -->
    <nav class="studio-tabs" aria-label="Release kit tabs">
      <button
        type="button"
        class="tab-btn"
        :class="{ active: activeTab === 'studio' }"
        @click="activeTab = 'studio'"
      >
        <ImageIcon :size="15" />
        <span>Thumbnail Studio</span>
        <span v-if="kit?.covers.length" class="badge-count">{{ kit.covers.length }}</span>
      </button>
      <button
        type="button"
        class="tab-btn"
        :class="{ active: activeTab === 'metadata' }"
        @click="activeTab = 'metadata'"
      >
        <Sparkles :size="15" />
        <span>Titles & Description</span>
        <span v-if="kit?.title_options.length" class="badge-count">{{ kit.title_options.length }}</span>
      </button>
      <button
        type="button"
        class="tab-btn"
        :class="{ active: activeTab === 'frames' }"
        @click="activeTab = 'frames'"
      >
        <Camera :size="15" />
        <span>Video Frames</span>
        <span v-if="kit?.frames.length" class="badge-count">{{ kit.frames.length }}</span>
      </button>
    </nav>

    <div v-if="loading" class="kit-loading"><LoaderCircle class="spin" :size="18" /> Loading release kit…</div>

    <template v-else-if="kit">
      <div v-if="error" class="kit-error" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="icon-btn" aria-label="Dismiss" @click="error = ''"><X :size="13" /></button>
      </div>

      <!-- ================================================================= -->
      <!-- TAB 1: THUMBNAIL STUDIO (PRIMARY HERO WORKSPACE)                  -->
      <!-- ================================================================= -->
      <section v-show="activeTab === 'studio'" class="thumbnail-studio">
        <!-- Hero Visual Preview & Platform Mockup -->
        <div class="hero-stage">
          <div class="stage-toolbar">
            <div class="mockup-selector" role="radiogroup" aria-label="Mockup view">
              <button
                type="button"
                class="mockup-btn"
                :class="{ active: mockupMode === 'art' }"
                @click="mockupMode = 'art'"
              >
                Artwork
              </button>
              <button
                v-if="props.variant === 'landscape'"
                type="button"
                class="mockup-btn"
                :class="{ active: mockupMode === 'youtube' }"
                @click="mockupMode = 'youtube'"
              >
                YouTube Card
              </button>
              <button
                v-if="props.variant === 'shorts'"
                type="button"
                class="mockup-btn"
                :class="{ active: mockupMode === 'shorts' }"
                @click="mockupMode = 'shorts'"
              >
                Shorts Feed
              </button>
            </div>

            <div class="stage-tools">
              <button
                type="button"
                class="ghost small"
                :class="{ active: showSafeZone }"
                title="Toggle platform safe zones overlay"
                @click="showSafeZone = !showSafeZone"
              >
                <Layers :size="13" /> Safe Zone
              </button>
              <button
                type="button"
                class="ghost small"
                :disabled="!selectedCover?.url"
                title="Bake custom bold headline text directly onto the thumbnail"
                @click="openTextOverlayTool"
              >
                <Type :size="13" /> Add Headline Text
              </button>
              <button type="button" class="ghost small" @click="uploadInput?.click()">
                <Upload :size="13" /> Upload Cover
              </button>
              <input ref="uploadInput" type="file" accept="image/*" hidden @change="uploadCover" />
              <a
                v-if="selectedCover?.url"
                class="primary small btn-download"
                :href="selectedCover.url"
                :download="coverDownloadName(selectedCover)"
                title="Download full-resolution thumbnail"
              >
                <Download :size="13" /> Download High-Res
              </a>
            </div>
          </div>

          <!-- Canvas / Artwork Container -->
          <div class="thumbnail-canvas-wrapper" :class="[mockupMode, { shorts: props.variant === 'shorts' }]">
            <!-- Normal Artwork View -->
            <div v-if="mockupMode === 'art'" class="art-frame">
              <img v-if="selectedCover?.url" :src="selectedCover.url" :alt="selectedCover.concept" />
              <div v-else class="art-placeholder">
                <ImageIcon :size="48" class="muted" />
                <p>No thumbnail selected yet. Pick a frame below or generate one with AI.</p>
              </div>

              <!-- Safe Zone Guide Overlay -->
              <div v-if="showSafeZone && selectedCover?.url" class="safe-zone-overlay">
                <div v-if="props.variant === 'landscape'" class="yt-badge-danger-zone">
                  <span>YouTube 03:45 Pill</span>
                </div>
                <div v-if="props.variant === 'shorts'" class="shorts-ui-danger-zone">
                  <div class="shorts-right-bar">Actions Safe Area</div>
                  <div class="shorts-bottom-bar">Title & Sound Bar Safe Area</div>
                </div>
              </div>
            </div>

            <!-- YouTube Mockup View -->
            <div v-else-if="mockupMode === 'youtube'" class="yt-mockup-card">
              <div class="yt-thumb-box">
                <img v-if="selectedCover?.url" :src="selectedCover.url" :alt="selectedCover.concept" />
                <span class="yt-duration-badge">04:12</span>
                <div v-if="showSafeZone" class="yt-badge-danger-zone">
                  <span>UI Pill Zone</span>
                </div>
              </div>
              <div class="yt-meta-row">
                <div class="yt-avatar"></div>
                <div class="yt-meta-content">
                  <div class="yt-title">{{ activeDisplayTitle }}</div>
                  <div class="yt-channel">{{ props.project.title }} · 48K views · 2 hours ago</div>
                </div>
              </div>
            </div>

            <!-- Shorts Mockup View -->
            <div v-else-if="mockupMode === 'shorts'" class="shorts-mockup-card">
              <img v-if="selectedCover?.url" :src="selectedCover.url" :alt="selectedCover.concept" />
              <div class="shorts-overlay-ui">
                <div class="shorts-right-actions">
                  <div class="shorts-action-pill">♥ 14K</div>
                  <div class="shorts-action-pill">💬 280</div>
                  <div class="shorts-action-pill">↗ Share</div>
                </div>
                <div class="shorts-bottom-info">
                  <div class="shorts-handle">@{{ props.project.title.toLowerCase().replace(/\s+/g, '') }}</div>
                  <div class="shorts-caption">{{ activeDisplayTitle }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- ⚡ Quick Frame-to-Thumbnail Section -->
        <div class="quick-frame-bar">
          <div class="quick-frame-head">
            <div>
              <h4>⚡ Turn Video Frame into Thumbnail</h4>
              <p>Promote any frame from the video into a crisp YouTube thumbnail with one click.</p>
            </div>
            <div class="frame-bar-actions">
              <label class="style-select-label">
                <span>Preset:</span>
                <select v-model="frameStyle" class="compact-select">
                  <option value="punch">Vivid Punch (YouTube pop)</option>
                  <option value="cinematic">Cinematic Drama</option>
                  <option value="warm">Warm Glow</option>
                  <option value="natural">Natural Still</option>
                </select>
              </label>
              <button
                type="button"
                class="ghost small"
                :disabled="autoImporting"
                title="Automatically scan episode shots for hero frames"
                @click="handleAutoImportFrames"
              >
                <LoaderCircle v-if="autoImporting" class="spin" :size="13" />
                <Zap v-else :size="13" /> Auto-Import Shot Frames
              </button>
            </div>
          </div>

          <div v-if="kit.frames.length" class="frames-mini-strip">
            <div v-for="frame in kit.frames" :key="frame.id" class="mini-frame-card">
              <div class="mini-frame-media" @click="seekToFrame(frame.timeline_ms)">
                <img :src="frame.url" :alt="frame.label" />
                <span class="mini-frame-time">{{ formatTimecode(frame.timeline_ms) }}</span>
              </div>
              <div class="mini-frame-footer">
                <span class="mini-frame-label" :title="frame.label">{{ frame.label || 'Frame' }}</span>
                <button
                  type="button"
                  class="primary mini"
                  :disabled="promotingFrameId === frame.id"
                  title="Make this frame your active thumbnail"
                  @click="handlePromoteFrame(frame.id)"
                >
                  <LoaderCircle v-if="promotingFrameId === frame.id" class="spin" :size="11" />
                  <Zap v-else :size="11" /> Use as Thumbnail
                </button>
              </div>
            </div>
          </div>
          <div v-else class="empty-frames-prompt">
            <p>No video frames in the kit yet. Click <strong>Auto-Import Shot Frames</strong> to pull hero frames directly from your episode storyboard!</p>
          </div>
        </div>

        <!-- AI Thumbnail Generator & Creative Presets -->
        <div class="ai-thumbnail-creator">
          <div class="section-head">
            <div>
              <h4>AI Thumbnail Generator</h4>
              <p>Generate high-CTR concept artwork using your characters, key moments, and viral presets.</p>
            </div>
            <div class="cover-tools">
              <select v-model="coverProvider" aria-label="Image AI">
                <option value="">Auto (first live bridge)</option>
                <option value="chatgpt_web">ChatGPT bridge</option>
                <option value="gemini_web">Gemini bridge</option>
              </select>
            </div>
          </div>

          <!-- Quick Creative Presets -->
          <div class="preset-chips">
            <span class="preset-label">Styles:</span>
            <button
              v-for="preset in THUMBNAIL_PRESETS"
              :key="preset.id"
              type="button"
              class="preset-chip"
              @click="applyPreset(preset)"
            >
              {{ preset.label }}
            </button>
          </div>

          <form class="custom-cover" @submit.prevent="generateCustom">
            <textarea
              v-model="customPrompt"
              rows="2"
              maxlength="2000"
              placeholder="Describe your thumbnail concept: lighting, facial expression, emotional tension, hero focal point…"
              aria-label="Custom cover prompt"
            ></textarea>

            <!-- ⚡ Real-Time Suggested Headline Hooks -->
            <div v-if="suggestedHooks.length" class="suggested-hooks-box">
              <div class="hooks-header">
                <span class="hooks-title"><Sparkles :size="12" /> Suggested Titles (click to use):</span>
                <button
                  type="button"
                  class="hooks-refresh-btn"
                  title="Generate more title hooks"
                  :disabled="loadingHooks"
                  @click="fetchRemoteHooks"
                >
                  <LoaderCircle v-if="loadingHooks" class="spin" :size="11" />
                  <RefreshCw v-else :size="11" /> Refresh
                </button>
              </div>
              <div class="hooks-chips-wrap">
                <button
                  v-for="(hook, idx) in suggestedHooks"
                  :key="idx"
                  type="button"
                  class="hook-pill"
                  :class="{ selected: customHeadline === hook.full || customHeadline === hook.headline }"
                  @click="customHeadline = hook.full"
                >
                  <span class="hook-text">{{ hook.full }}</span>
                  <span v-if="hook.tag" class="hook-badge">{{ hook.tag }}</span>
                </button>
              </div>
            </div>

            <div class="headline-field-row">
              <input
                v-model="customHeadline"
                maxlength="60"
                placeholder="Text on thumbnail (auto-suggested from prompt if left empty)"
                aria-label="Custom cover headline"
              />
              <button
                type="button"
                class="ghost small auto-suggest-action"
                title="Auto-fill with top viral title hook"
                @click="autoFillTopHook"
              >
                <Sparkles :size="13" /> Auto Title
              </button>
            </div>

            <button type="submit" class="primary generate-btn" :disabled="!customPrompt.trim() || startingCover === 'custom'">
              <LoaderCircle v-if="startingCover === 'custom'" class="spin" :size="14" />
              <Wand2 v-else :size="14" /> Generate Thumbnail
            </button>
          </form>

          <!-- Suggested Cover Ideas from Script -->
          <div v-if="kit.cover_ideas.length" class="idea-grid">
            <article v-for="idea in kit.cover_ideas" :key="idea.id" class="idea-card">
              <p>{{ idea.concept }}</p>
              <label class="headline">
                <span>Text on cover</span>
                <input v-model="headlineDrafts[idea.id]" maxlength="60" placeholder="No text" @blur="commitHeadline(idea)" />
              </label>
              <button type="button" class="primary small" :disabled="startingCover === idea.id" @click="generateFromIdea(idea)">
                <LoaderCircle v-if="startingCover === idea.id" class="spin" :size="13" />
                <Wand2 v-else :size="13" /> Generate cover
              </button>
            </article>
          </div>
        </div>

        <!-- Thumbnail Takes Gallery -->
        <div class="cover-gallery-section">
          <div class="section-head">
            <h4>Generated & Uploaded Covers <small>({{ kit.covers.length }})</small></h4>
            <span class="hint">Click any card to make it your active publishing thumbnail.</span>
          </div>

          <div v-if="kit.covers.length" class="cover-grid">
            <figure
              v-for="cover in kit.covers"
              :key="cover.id"
              class="cover-card"
              :class="[cover.status, { selected: kit.selected_cover_id === cover.id }]"
            >
              <button
                type="button"
                class="cover-frame"
                :disabled="cover.status !== 'ready'"
                :aria-pressed="kit.selected_cover_id === cover.id"
                :aria-label="cover.status === 'ready' ? `Use cover ${cover.headline || cover.concept}` : 'Cover not ready'"
                @click="selectCover(cover)"
              >
                <img v-if="cover.url" :src="cover.url" :alt="cover.headline || cover.concept" />
                <span v-else-if="cover.status === 'generating'" class="cover-status"><LoaderCircle class="spin" :size="18" /> Painting…</span>
                <span v-else class="cover-status failed">{{ cover.error || 'Failed' }}</span>
                <span v-if="kit.selected_cover_id === cover.id" class="selected-badge"><Check :size="11" /> Active</span>
              </button>
              <figcaption>
                <span class="cover-caption" :title="cover.concept">{{ cover.headline || cover.concept || 'Cover' }}</span>
                <span class="cover-actions">
                  <a v-if="cover.url" class="icon-btn" :href="cover.url" :download="coverDownloadName(cover)" aria-label="Download cover"><Download :size="13" /></a>
                  <button v-if="cover.provider !== 'upload' && cover.provider !== 'frame_promote'" type="button" class="icon-btn" aria-label="Generate another like this" :disabled="!!startingCover" @click="regenerate(cover)"><RefreshCw :size="13" /></button>
                  <button type="button" class="icon-btn" aria-label="Delete cover" @click="removeCover(cover)"><Trash2 :size="13" /></button>
                </span>
              </figcaption>
            </figure>
          </div>
          <p v-else class="empty">No covers yet. Pick a video frame above or generate one with AI.</p>
        </div>
      </section>

      <!-- ================================================================= -->
      <!-- TAB 2: TITLES, DESCRIPTION & TAGS METADATA                        -->
      <!-- ================================================================= -->
      <section v-show="activeTab === 'metadata'" class="metadata-studio">
        <!-- Brief & Suggest CTA -->
        <section class="kit-section">
          <label class="field">
            <span class="field-label">Creative Brief / Story Hook</span>
            <textarea
              v-model="brief"
              rows="2"
              maxlength="4000"
              placeholder="What happens in this video, key dramatic twist, the question you want viewers asking…"
              @blur="saveBrief"
            ></textarea>
          </label>
          <div class="suggest-row">
            <select v-model="copyProvider" aria-label="Text AI">
              <option value="">Auto (project AI)</option>
              <option value="chatgpt_web">ChatGPT bridge</option>
              <option value="gemini_web">Gemini bridge</option>
            </select>
            <button type="button" class="primary" :disabled="suggesting" @click="suggest">
              <LoaderCircle v-if="suggesting" class="spin" :size="14" />
              <Sparkles v-else :size="14" />
              {{ suggesting ? (suggestJob?.message || 'Writing viral copy…') : kit.title_options.length ? 'Suggest new angles' : 'Suggest titles, description & covers' }}
            </button>
            <small v-if="kit.copy_provider && !suggesting" class="muted">Last suggested by {{ kit.copy_provider }}.</small>
          </div>
        </section>

        <!-- Titles Section -->
        <section class="kit-section">
          <div class="section-head">
            <h4>Titles & Angles</h4>
            <span class="hint">The selected title will be automatically published.</span>
          </div>
          <div class="title-list" role="radiogroup" aria-label="Title options">
            <div
              v-for="option in kit.title_options"
              :key="option.id"
              class="title-option"
              :class="{ selected: kit.selected_title_id === option.id }"
            >
              <button
                type="button"
                role="radio"
                class="radio"
                :aria-checked="kit.selected_title_id === option.id"
                :aria-label="`Use title ${option.text}`"
                @click="selectTitle(option.id)"
              >
                <Check v-if="kit.selected_title_id === option.id" :size="12" />
              </button>
              <input
                v-model="titleDrafts[option.id]"
                maxlength="100"
                :aria-label="`Edit title ${option.text}`"
                @focus="selectTitle(option.id)"
                @keydown.enter="($event.target as HTMLInputElement).blur()"
                @blur="commitTitle(option.id)"
              />
              <span class="angle">{{ option.angle }}</span>
              <span class="count" :class="{ long: (titleDrafts[option.id] || '').length > 70 }">
                {{ (titleDrafts[option.id] || '').length }}/70
              </span>
              <button type="button" class="icon-btn" aria-label="Remove title" @click="removeTitle(option.id)"><Trash2 :size="13" /></button>
            </div>
            <form class="title-option add" @submit.prevent="addTitle">
              <Plus :size="14" class="muted" />
              <input v-model="newTitle" maxlength="100" placeholder="Write your own custom title" aria-label="New title" />
              <button type="submit" class="ghost" :disabled="!newTitle.trim()">Add</button>
            </form>
          </div>
        </section>

        <!-- Description & Tags Section -->
        <section class="kit-section two-col">
          <label class="field">
            <span class="field-label">Description</span>
            <textarea
              v-model="description"
              rows="6"
              maxlength="5000"
              placeholder="Hook line, synopsis, call to subscribe, links and hashtags…"
              @blur="saveDescription"
            ></textarea>
            <small class="muted">{{ description.length }}/5000</small>
          </label>
          <div class="field">
            <span class="field-label">Tags & Keywords</span>
            <div class="tag-box" @click="($event.currentTarget as HTMLElement).querySelector('input')?.focus()">
              <span v-for="tag in tags" :key="tag" class="tag-chip">
                {{ tag }}
                <button type="button" :aria-label="`Remove tag ${tag}`" @click.stop="removeTag(tag)"><X :size="10" /></button>
              </span>
              <input v-model="tagDraft" placeholder="Add tag, press Enter" aria-label="Add tag" @keydown="onTagKey" @blur="addTags(tagDraft)" />
            </div>
            <small class="muted">{{ tags.length }} tags. Tags help YouTube recommend your video to the right audience.</small>
          </div>
        </section>
      </section>

      <!-- ================================================================= -->
      <!-- TAB 3: VIDEO FRAMES & TIMELINE SCRUBBER                           -->
      <!-- ================================================================= -->
      <section v-show="activeTab === 'frames'" class="frames-studio">
        <section class="kit-section">
          <div class="section-head">
            <h4>Timeline Frame Scrubber</h4>
            <span class="hint">Scrub to any moment and capture a frame for thumbnails.</span>
          </div>
          <div v-if="segments.length" class="scrubber" :class="{ shorts: props.variant === 'shorts' }">
            <div class="scrub-preview">
              <video
                v-if="seekIsVideo && seekSegment"
                ref="previewVideo"
                :src="seekSegment.url"
                muted
                playsinline
                preload="auto"
                @loadedmetadata="applyVideoSeek"
              ></video>
              <img v-else-if="seekSegment" :src="seekSegment.url" :alt="seekSegment.label" />
              <span v-else class="scrub-empty">No picture at this moment</span>
            </div>
            <div class="scrub-controls">
              <input
                class="scrub-range"
                type="range"
                min="0"
                :max="Math.max(0, scrubTotalMs - 1)"
                step="1"
                :value="seekMs"
                aria-label="Seek frame"
                @input="clampSeek(Number(($event.target as HTMLInputElement).value))"
              />
              <div class="scrub-clips" aria-hidden="true">
                <button
                  v-for="segment in segments"
                  :key="segment.id"
                  type="button"
                  tabindex="-1"
                  class="scrub-clip"
                  :class="{ active: seekSegment?.id === segment.id }"
                  :style="{ left: `${(segment.start_ms / Math.max(1, scrubTotalMs)) * 100}%`, width: `${(segment.duration_ms / Math.max(1, scrubTotalMs)) * 100}%` }"
                  :title="segment.label"
                  @click="clampSeek(segment.start_ms)"
                ></button>
                <span
                  v-for="frame in kit.frames"
                  :key="frame.id"
                  class="scrub-mark"
                  :style="{ left: `${(frame.timeline_ms / Math.max(1, scrubTotalMs)) * 100}%` }"
                ></span>
              </div>
              <div class="scrub-row">
                <div class="scrub-buttons">
                  <button type="button" class="icon-btn" aria-label="Previous clip" @click="jumpSegment(-1)"><ChevronsLeft :size="15" /></button>
                  <button type="button" class="icon-btn" aria-label="Back one frame" @click="stepSeek(-1)"><ChevronLeft :size="15" /></button>
                  <button type="button" class="icon-btn" aria-label="Forward one frame" @click="stepSeek(1)"><ChevronRight :size="15" /></button>
                  <button type="button" class="icon-btn" aria-label="Next clip" @click="jumpSegment(1)"><ChevronsRight :size="15" /></button>
                </div>
                <output class="scrub-time">{{ formatPrecise(seekMs) }} <span>/ {{ formatPrecise(scrubTotalMs) }}</span></output>
                <span class="scrub-label" :title="seekSegment?.label">{{ seekSegment?.label || '—' }}</span>
                <button
                  type="button"
                  class="primary small"
                  :disabled="!seekSegment || addingFrame || kit.frames.length >= MAX_FRAMES"
                  @click="addSeekFrame"
                >
                  <LoaderCircle v-if="addingFrame" class="spin" :size="13" />
                  <Camera v-else :size="13" />
                  {{ kit.frames.length >= MAX_FRAMES ? 'Kit is full' : 'Add this frame' }}
                </button>
              </div>
            </div>
          </div>

          <!-- Captured Frames Strip -->
          <div class="section-head" style="margin-top: 14px;">
            <h4>Captured Frames <small>({{ kit.frames.length }}/{{ MAX_FRAMES }})</small></h4>
          </div>
          <div v-if="kit.frames.length" class="frame-strip">
            <figure v-for="frame in kit.frames" :key="frame.id" class="frame-card">
              <button type="button" class="frame-seek" :aria-label="`Seek to ${formatTimecode(frame.timeline_ms)}`" @click="seekToFrame(frame.timeline_ms)">
                <img :src="frame.url" :alt="frame.label || `Frame at ${formatTimecode(frame.timeline_ms)}`" />
              </button>
              <figcaption>{{ formatTimecode(frame.timeline_ms) }}<span v-if="frame.label"> · {{ frame.label }}</span></figcaption>
              <div class="frame-card-actions">
                <button
                  type="button"
                  class="primary mini"
                  :disabled="promotingFrameId === frame.id"
                  @click="handlePromoteFrame(frame.id)"
                >
                  <Zap :size="11" /> To Thumbnail
                </button>
                <button type="button" class="frame-remove" aria-label="Remove frame" @click="removeFrame(frame.id)"><X :size="12" /></button>
              </div>
            </figure>
          </div>
          <p v-else class="empty">No frames captured yet. Use the scrubber above to add frames, or use Auto-Import.</p>
        </section>
      </section>

      <!-- ================================================================= -->
      <!-- MODAL: TEXT / HEADLINE OVERLAY CANVAS TOOL                        -->
      <!-- ================================================================= -->
      <div v-if="showTextOverlayModal" class="overlay-modal-backdrop" @click.self="showTextOverlayModal = false">
        <div class="overlay-modal" role="dialog" aria-label="Add headline overlay">
          <header class="overlay-modal-header">
            <h4><Type :size="16" /> Add Headline to Thumbnail</h4>
            <button type="button" class="icon-btn" aria-label="Close" @click="showTextOverlayModal = false"><X :size="16" /></button>
          </header>

          <div class="overlay-modal-body">
            <!-- Canvas Preview -->
            <div class="overlay-preview-container">
              <canvas ref="overlayCanvas" class="overlay-canvas"></canvas>
            </div>

            <!-- Controls -->
            <div class="overlay-controls">
              <!-- Mode Toggle: Dual Hook vs Single Hook -->
              <div class="hook-mode-toggle">
                <button
                  type="button"
                  class="mode-toggle-btn"
                  :class="{ active: isDualHook || overlayStyle === 'dual_combo' }"
                  @click="isDualHook = true; overlayStyle = 'dual_combo'"
                >
                  Dual Hook (Top + Bottom)
                </button>
                <button
                  type="button"
                  class="mode-toggle-btn"
                  :class="{ active: !isDualHook && overlayStyle !== 'dual_combo' }"
                  @click="isDualHook = false; if (overlayStyle === 'dual_combo') overlayStyle = 'gamer_red'"
                >
                  Single Hook
                </button>
              </div>

              <!-- Dual Hook Inputs -->
              <template v-if="isDualHook || overlayStyle === 'dual_combo'">
                <label class="field">
                  <span class="field-label">Top Hook (Headline)</span>
                  <input
                    v-model="overlayTopText"
                    maxlength="50"
                    placeholder="e.g. PUSHED TOO EARLY"
                  />
                </label>
                <label class="field">
                  <span class="field-label">Bottom Punchline (Action Callout)</span>
                  <input
                    v-model="overlayBottomText"
                    maxlength="40"
                    placeholder="e.g. I CLUTCHED"
                  />
                </label>
              </template>

              <!-- Single Hook Input -->
              <template v-else>
                <label class="field">
                  <span class="field-label">Headline Text</span>
                  <textarea
                    v-model="overlayText"
                    rows="2"
                    maxlength="60"
                    placeholder="e.g. SHE KNEW, NO WAY BACK"
                  ></textarea>
                </label>
              </template>

              <div class="control-row">
                <label class="field">
                  <span class="field-label">Style Preset</span>
                  <select v-model="overlayStyle">
                    <option value="dual_combo">⚡ Dual Combo (Red Splatter + Yellow Clutch)</option>
                    <option value="gamer_red">🩸 Gamer Red Glow (Distressed Splatter)</option>
                    <option value="neon_clutch">⚡ Neon Yellow (Paint Underline)</option>
                    <option value="impact">Impact White (Heavy Shadow)</option>
                    <option value="yellow">Yellow Viral (MrBeast style)</option>
                    <option value="red_alert">Red Alert (Urgent Drama)</option>
                    <option value="gold">Cinematic Gold</option>
                  </select>
                </label>

                <label v-if="!isDualHook && overlayStyle !== 'dual_combo'" class="field">
                  <span class="field-label">Position</span>
                  <select v-model="overlayPosition">
                    <option value="bottom_left">Bottom (Safe from UI)</option>
                    <option value="top_center">Top Center</option>
                    <option value="top_left">Top Left</option>
                    <option value="center">Center Punch</option>
                  </select>
                </label>
              </div>

              <div class="overlay-actions">
                <button type="button" class="ghost" @click="showTextOverlayModal = false">Cancel</button>
                <button
                  type="button"
                  class="primary"
                  :disabled="bakingOverlay || ((isDualHook || overlayStyle === 'dual_combo') ? (!overlayTopText.trim() && !overlayBottomText.trim()) : !overlayText.trim())"
                  @click="applyBakedOverlay"
                >
                  <LoaderCircle v-if="bakingOverlay" class="spin" :size="14" />
                  <Check v-else :size="14" /> Save as New Thumbnail
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.release-kit {
  --input-bg: var(--workspace-elevated, #1a1d20);
  --text-primary: var(--workspace-text, #f5f7f8);
  --line-default: var(--workspace-border-strong, rgba(255, 255, 255, 0.12));
  display: grid;
  gap: 16px;
  padding: 20px 24px 24px;
  background: var(--workspace-panel-strong, #141619);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
  border-radius: 18px;
  color: var(--workspace-text, #f5f7f8);
  font-size: 13px;
}

/* ── Header ────────────────────────────────────────────────────────────── */
.kit-header {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.kit-header h3 {
  margin: 3px 0 4px;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: -0.01em;
}

.kit-header p,
.hint,
.muted,
.empty {
  color: var(--workspace-muted, #92989f);
}

.kit-header p {
  margin: 0;
  max-width: 65ch;
  line-height: 1.45;
}

.kit-eyebrow {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--workspace-accent, #d9ff63);
}

.kit-header-actions {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}

/* ── Studio Tabs Navigation ────────────────────────────────────────────── */
.studio-tabs {
  display: flex;
  gap: 6px;
  padding: 4px;
  border-radius: 10px;
  background: var(--workspace-bg, #0d0f11);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.06));
}

.tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 8px 14px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--workspace-muted, #92989f);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.tab-btn:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.04);
}

.tab-btn.active {
  background: var(--workspace-elevated, #1c2024);
  color: var(--workspace-accent, #d9ff63);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
}

.badge-count {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  font-size: 10px;
  font-weight: 700;
  color: #fff;
}

.tab-btn.active .badge-count {
  background: var(--workspace-accent, #d9ff63);
  color: #000;
}

/* ── Thumbnail Studio Layout ───────────────────────────────────────────── */
.thumbnail-studio {
  display: grid;
  gap: 20px;
}

.hero-stage {
  display: grid;
  gap: 12px;
  padding: 16px;
  background: var(--workspace-bg, #0c0e10);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
  border-radius: 14px;
}

.stage-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.mockup-selector {
  display: inline-flex;
  padding: 2px;
  border-radius: 8px;
  background: var(--workspace-elevated, #1a1d20);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
}

.mockup-btn {
  padding: 4px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--workspace-muted, #92989f);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.mockup-btn.active {
  background: var(--workspace-hover, #24292e);
  color: #fff;
}

.stage-tools {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.btn-download {
  text-decoration: none;
}

/* ── Thumbnail Canvas & Mockups ────────────────────────────────────────── */
.thumbnail-canvas-wrapper {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 280px;
  padding: 16px;
  background: #050607;
  border-radius: 12px;
  overflow: hidden;
}

.art-frame {
  position: relative;
  width: 100%;
  max-width: 640px;
  aspect-ratio: 16 / 9;
  border-radius: 10px;
  overflow: hidden;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
  background: #000;
}

.thumbnail-canvas-wrapper.shorts .art-frame {
  max-width: 280px;
  aspect-ratio: 9 / 16;
}

.art-frame img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.art-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 24px;
  text-align: center;
  gap: 8px;
}

.art-placeholder p {
  margin: 0;
  font-size: 13px;
  color: var(--workspace-muted, #92989f);
}

/* Safe Zone Guides */
.safe-zone-overlay {
  position: absolute;
  inset: 0;
  pointer-events: none;
  border: 2px dashed rgba(217, 255, 99, 0.6);
}

.yt-badge-danger-zone {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 90px;
  height: 28px;
  background: rgba(255, 59, 48, 0.4);
  border: 1px solid rgba(255, 59, 48, 0.8);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  color: #fff;
  font-weight: 700;
}

.shorts-ui-danger-zone {
  position: absolute;
  inset: 0;
}

.shorts-right-bar {
  position: absolute;
  right: 0;
  bottom: 40px;
  width: 55px;
  height: 180px;
  background: rgba(255, 59, 48, 0.35);
  font-size: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: #fff;
}

.shorts-bottom-bar {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 70px;
  background: rgba(255, 59, 48, 0.35);
  font-size: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}

/* YouTube Mockup Card */
.yt-mockup-card {
  width: 100%;
  max-width: 480px;
  background: #0f0f0f;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
  padding-bottom: 12px;
}

.yt-thumb-box {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
}

.yt-thumb-box img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.yt-duration-badge {
  position: absolute;
  right: 8px;
  bottom: 8px;
  padding: 2px 5px;
  background: rgba(0, 0, 0, 0.85);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  border-radius: 4px;
}

.yt-meta-row {
  display: flex;
  gap: 12px;
  padding: 12px 12px 0;
}

.yt-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  background: #444;
  flex: 0 0 36px;
}

.yt-meta-content {
  flex: 1;
  min-width: 0;
}

.yt-title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.3;
  color: #f1f1f1;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.yt-channel {
  font-size: 12px;
  color: #aaa;
  margin-top: 4px;
}

/* Shorts Mockup Card */
.shorts-mockup-card {
  position: relative;
  width: 250px;
  aspect-ratio: 9 / 16;
  border-radius: 18px;
  overflow: hidden;
  background: #000;
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.7);
  border: 2px solid rgba(255, 255, 255, 0.15);
}

.shorts-mockup-card img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.shorts-overlay-ui {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  padding: 12px;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.8) 0%, transparent 40%);
}

.shorts-right-actions {
  position: absolute;
  right: 10px;
  bottom: 60px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.shorts-action-pill {
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  text-shadow: 0 1px 3px rgba(0,0,0,0.8);
}

.shorts-bottom-info {
  margin-right: 40px;
}

.shorts-handle {
  font-size: 11px;
  font-weight: 700;
  color: #fff;
}

.shorts-caption {
  font-size: 11px;
  color: #eee;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Quick Frame-to-Thumbnail Bar ──────────────────────────────────────── */
.quick-frame-bar {
  display: grid;
  gap: 12px;
  padding: 16px;
  border-radius: 14px;
  background: var(--workspace-elevated, #1a1d20);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
}

.quick-frame-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.quick-frame-head h4 {
  margin: 0 0 2px;
  font-size: 14px;
  font-weight: 700;
}

.quick-frame-head p {
  margin: 0;
  font-size: 12px;
  color: var(--workspace-muted, #92989f);
}

.frame-bar-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.style-select-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--workspace-muted, #92989f);
}

.compact-select {
  padding: 4px 8px;
  font-size: 12px;
  background: var(--workspace-bg, #0d0f11);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.1));
  border-radius: 6px;
  color: #fff;
}

.frames-mini-strip {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
}

.mini-frame-card {
  display: flex;
  flex-direction: column;
  border-radius: 8px;
  overflow: hidden;
  background: var(--workspace-bg, #0d0f11);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.06));
}

.mini-frame-media {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
  cursor: pointer;
}

.shorts .mini-frame-media {
  aspect-ratio: 9 / 16;
}

.mini-frame-media img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.mini-frame-time {
  position: absolute;
  left: 4px;
  bottom: 4px;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(0, 0, 0, 0.75);
  font-size: 9px;
  font-weight: 700;
}

.mini-frame-footer {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 8px;
}

.mini-frame-label {
  font-size: 11px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--workspace-muted, #92989f);
}

button.mini {
  padding: 3px 6px;
  font-size: 11px;
  gap: 4px;
}

.empty-frames-prompt {
  padding: 16px;
  text-align: center;
  border: 1px dashed var(--workspace-border, rgba(255, 255, 255, 0.1));
  border-radius: 8px;
  color: var(--workspace-muted, #92989f);
}

/* ── AI Thumbnail Creator & Presets ────────────────────────────────────── */
.ai-thumbnail-creator {
  display: grid;
  gap: 12px;
  padding: 16px;
  border-radius: 14px;
  background: var(--workspace-panel-strong, #141619);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
}

.preset-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.preset-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--workspace-muted, #92989f);
}

.preset-chip {
  padding: 4px 10px;
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.1));
  border-radius: 999px;
  background: var(--workspace-bg, #0c0e10);
  color: #ddd;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.preset-chip:hover {
  background: var(--workspace-hover, #202428);
  border-color: var(--workspace-accent, #d9ff63);
  color: #fff;
}

.custom-cover {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.custom-cover textarea {
  width: 100%;
  resize: vertical;
  background: var(--input-bg, #1a1d20);
  border: 1px solid var(--line-default, rgba(255, 255, 255, 0.12));
  color: var(--text-primary, #f5f7f8);
  border-radius: 8px;
  padding: 10px 12px;
  font-family: inherit;
  font-size: 13px;
}

.suggested-hooks-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
}

.hooks-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.hooks-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--workspace-accent, #d9ff63);
}

.hooks-refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: none;
  color: var(--workspace-muted, #92989f);
  font-size: 11px;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
}

.hooks-refresh-btn:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.06);
}

.hooks-chips-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.hook-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  background: var(--workspace-bg, #0c0e10);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.12));
  border-radius: 6px;
  color: #eee;
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
}

.hook-pill:hover {
  background: var(--workspace-hover, #202428);
  border-color: rgba(255, 255, 255, 0.3);
  color: #fff;
  transform: translateY(-1px);
}

.hook-pill.selected {
  background: rgba(217, 255, 99, 0.14);
  border-color: var(--workspace-accent, #d9ff63);
  color: var(--workspace-accent, #d9ff63);
}

.hook-badge {
  font-size: 9px;
  font-weight: 600;
  text-transform: uppercase;
  padding: 1px 4px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.08);
  color: var(--workspace-muted, #92989f);
}

.hook-pill.selected .hook-badge {
  background: rgba(217, 255, 99, 0.25);
  color: #fff;
}

.headline-field-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.headline-field-row input {
  flex: 1;
  background: var(--input-bg, #1a1d20);
  border: 1px solid var(--line-default, rgba(255, 255, 255, 0.12));
  color: var(--text-primary, #f5f7f8);
  border-radius: 8px;
  padding: 8px 12px;
  font-family: inherit;
  font-size: 13px;
}

.auto-suggest-action {
  white-space: nowrap;
  flex: 0 0 auto;
}

.generate-btn {
  align-self: flex-start;
  padding: 8px 18px;
}

.idea-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
  margin-top: 6px;
}

.idea-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--workspace-bg, #0d0f11);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.06));
}

.idea-card p {
  margin: 0;
  font-size: 12px;
  line-height: 1.4;
  color: #ccc;
}

.idea-card .headline {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 11px;
  color: var(--workspace-muted, #92989f);
}

.idea-card .headline input {
  padding: 4px 8px;
  border-radius: 5px;
  background: var(--workspace-elevated, #1a1d20);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.1));
  color: #fff;
  font-size: 12px;
}

/* ── Covers Grid ───────────────────────────────────────────────────────── */
.cover-gallery-section {
  display: grid;
  gap: 12px;
}

.cover-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px;
}

.shorts .cover-grid {
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
}

.cover-card {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.cover-frame {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  padding: 0;
  border: 2px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
  border-radius: 10px;
  background: #000;
  overflow: hidden;
  cursor: pointer;
  transition: all 0.15s ease;
}

.shorts .cover-frame {
  aspect-ratio: 9 / 16;
}

.cover-card.selected .cover-frame {
  border-color: var(--workspace-accent, #d9ff63);
  box-shadow: 0 0 0 1px var(--workspace-accent, #d9ff63);
}

.cover-frame img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-status {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 6px;
  font-size: 12px;
  color: var(--workspace-muted, #92989f);
}

.cover-status.failed {
  color: #ff8080;
}

.selected-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--workspace-accent, #d9ff63);
  color: #000;
  font-size: 10px;
  font-weight: 800;
}

.cover-card figcaption {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 4px;
}

.cover-caption {
  font-size: 12px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--workspace-muted, #92989f);
}

.cover-actions {
  display: inline-flex;
}

/* ── Metadata Tab ──────────────────────────────────────────────────────── */
.metadata-studio {
  display: grid;
  gap: 16px;
}

.field {
  display: grid;
  gap: 5px;
}

.field-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--workspace-muted, #92989f);
}

textarea, input {
  font-family: inherit;
}

.field textarea {
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--workspace-elevated, #1a1d20);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
  color: #fff;
  font-size: 13px;
}

.suggest-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.title-list {
  display: grid;
  gap: 6px;
}

.title-option {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--workspace-elevated, #1a1d20);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.06));
}

.title-option.selected {
  border-color: var(--workspace-accent, #d9ff63);
  background: rgba(217, 255, 99, 0.03);
}

.title-option .radio {
  width: 18px;
  height: 18px;
  padding: 0;
  border-radius: 50%;
  border: 1px solid var(--workspace-border-strong, rgba(255, 255, 255, 0.2));
  background: transparent;
  display: grid;
  place-items: center;
  cursor: pointer;
  color: #000;
}

.title-option.selected .radio {
  background: var(--workspace-accent, #d9ff63);
  border-color: var(--workspace-accent, #d9ff63);
}

.title-option input {
  flex: 1;
  background: transparent;
  border: none;
  color: #fff;
  font-size: 13px;
  outline: none;
}

.angle {
  font-size: 10px;
  text-transform: uppercase;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.06);
  color: var(--workspace-muted, #92989f);
  font-weight: 700;
}

.count {
  font-size: 11px;
  color: var(--workspace-muted, #92989f);
}

.count.long {
  color: #ff9900;
}

.title-option.add input {
  padding: 4px 0;
}

.tag-box {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--workspace-elevated, #1a1d20);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
  min-height: 40px;
}

.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  font-size: 11px;
}

.tag-chip button {
  background: transparent;
  border: none;
  padding: 0;
  cursor: pointer;
  color: inherit;
  display: grid;
  place-items: center;
}

.tag-box input {
  flex: 1;
  min-width: 120px;
  background: transparent;
  border: none;
  color: #fff;
  outline: none;
  font-size: 12px;
}

.kit-section {
  display: grid;
  gap: 12px;
  padding-top: 14px;
  border-top: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.06));
}

.kit-section.two-col {
  grid-template-columns: 1.3fr 1fr;
  gap: 14px;
}

.section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.section-head h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 700;
}

/* ── Frames Scrubber Tab ───────────────────────────────────────────────── */
.frames-studio {
  display: grid;
  gap: 16px;
}

.scrubber {
  display: grid;
  grid-template-columns: minmax(220px, 340px) 1fr;
  gap: 14px;
  align-items: center;
  padding: 12px;
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
  border-radius: 12px;
  background: var(--workspace-elevated, #1a1d20);
}

.scrubber.shorts {
  grid-template-columns: 150px 1fr;
}

.scrub-preview {
  display: grid;
  place-items: center;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  border-radius: 8px;
  background: #000;
}

.shorts .scrub-preview {
  aspect-ratio: 9 / 16;
}

.scrub-preview video,
.scrub-preview img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.scrub-empty {
  font-size: 12px;
  color: var(--workspace-muted, #92989f);
}

.scrub-controls {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.scrub-range {
  width: 100%;
  accent-color: var(--workspace-accent, #d9ff63);
}

.scrub-clips {
  position: relative;
  height: 14px;
}

.scrub-clip {
  position: absolute;
  top: 0;
  height: 100%;
  padding: 0;
  border: 1px solid var(--workspace-panel-strong, #141619);
  border-radius: 3px;
  background: var(--workspace-hover, #202428);
  cursor: pointer;
}

.scrub-clip.active {
  background: var(--workspace-accent-soft, rgba(217, 255, 99, 0.12));
  border-color: var(--workspace-accent, #d9ff63);
}

.scrub-mark {
  position: absolute;
  top: -3px;
  width: 2px;
  height: 20px;
  margin-left: -1px;
  border-radius: 1px;
  background: var(--workspace-accent, #d9ff63);
  pointer-events: none;
}

.scrub-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.scrub-buttons {
  display: inline-flex;
}

.scrub-time {
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.scrub-time span {
  color: var(--workspace-muted, #92989f);
}

.scrub-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--workspace-muted, #92989f);
}

.frame-strip {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 10px;
}

.frame-card {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.frame-seek {
  width: 100%;
  aspect-ratio: 16 / 9;
  padding: 0;
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.08));
  border-radius: 8px;
  background: #000;
  overflow: hidden;
  cursor: pointer;
}

.shorts .frame-seek {
  aspect-ratio: 9 / 16;
}

.frame-seek img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.frame-card figcaption {
  font-size: 11px;
  color: var(--workspace-muted, #92989f);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.frame-card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.frame-remove {
  background: transparent;
  border: none;
  color: var(--workspace-muted, #92989f);
  cursor: pointer;
  padding: 2px;
}

/* ── Overlay Modal ─────────────────────────────────────────────────────── */
.overlay-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(4px);
  display: grid;
  place-items: center;
  z-index: 1000;
  padding: 20px;
}

.overlay-modal {
  width: 100%;
  max-width: 720px;
  background: var(--workspace-panel-strong, #141619);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.12));
  border-radius: 16px;
  overflow: hidden;
  display: grid;
  gap: 16px;
  padding: 20px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.8);
}

.overlay-modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.overlay-modal-header h4 {
  margin: 0;
  font-size: 16px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.overlay-modal-body {
  display: grid;
  grid-template-columns: 1fr 280px;
  gap: 16px;
}

.overlay-preview-container {
  display: flex;
  align-items: center;
  justify-content: center;
  background: #000;
  border-radius: 10px;
  overflow: hidden;
}

.overlay-canvas {
  width: 100%;
  height: auto;
  aspect-ratio: 16 / 9;
  object-fit: contain;
}

.shorts .overlay-canvas {
  aspect-ratio: 9 / 16;
}

.overlay-controls {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.control-row {
  display: grid;
  gap: 10px;
}

.hook-mode-toggle {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;
}

.mode-toggle-btn {
  flex: 1;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 600;
  border-radius: 6px;
  background: var(--workspace-bg, #0c0e10);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.1));
  color: var(--workspace-muted, #92989f);
  cursor: pointer;
  transition: all 0.15s ease;
}

.mode-toggle-btn:hover {
  color: #fff;
  border-color: rgba(255, 255, 255, 0.25);
}

.mode-toggle-btn.active {
  background: rgba(217, 255, 99, 0.12);
  border-color: var(--workspace-accent, #d9ff63);
  color: var(--workspace-accent, #d9ff63);
}

.overlay-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: auto;
}

/* ── Utilities ─────────────────────────────────────────────────────────── */
.spin {
  animation: kit-spin 0.9s linear infinite;
}

@keyframes kit-spin {
  to {
    transform: rotate(360deg);
  }
}

.icon-btn {
  background: transparent;
  border: none;
  color: var(--workspace-muted, #92989f);
  cursor: pointer;
  display: grid;
  place-items: center;
  padding: 4px;
  border-radius: 4px;
}

.icon-btn:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.06);
}

button.primary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 8px;
  background: var(--workspace-accent, #d9ff63);
  color: #101204;
  border: none;
  font-weight: 700;
  font-size: 12px;
  cursor: pointer;
  transition: opacity 0.15s ease;
}

button.primary:hover:not(:disabled) {
  opacity: 0.9;
}

button.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

button.ghost {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 8px;
  background: transparent;
  color: #fff;
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.12));
  font-weight: 600;
  font-size: 12px;
  cursor: pointer;
}

button.ghost:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.05);
}

button.ghost:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

button.small,
a.small {
  padding: 5px 10px;
  font-size: 11px;
}

select {
  padding: 6px 10px;
  border-radius: 6px;
  background: var(--workspace-elevated, #1a1d20);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.1));
  color: #fff;
  font-size: 12px;
}

.kit-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--workspace-muted, #92989f);
  padding: 24px 0;
}

.kit-error {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: rgba(255, 80, 80, 0.12);
  color: #ffb4b4;
  font-size: 12px;
}

@media (max-width: 800px) {
  .scrubber,
  .kit-section.two-col,
  .custom-cover,
  .overlay-modal-body {
    grid-template-columns: 1fr;
  }
}
</style>
