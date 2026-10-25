<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { Crop, Image as ImageIcon, Lock, Maximize2, Minus, Palette, Plus, Redo2, RefreshCw, RotateCcw, Sparkles, Undo2, X } from '@lucide/vue'
import { api, type AspectRatio, type ImageReferenceSource, type ImageReferenceSourceShot, type ReferenceColorRetouchAction, type Shot } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import Dialog from '@/components/ui/Dialog.vue'
import VideoFramePicker from '@/components/VideoFramePicker.vue'
import OpticalPunchModal from '@/components/OpticalPunchModal.vue'

const props = defineProps<{
  shot: Shot
  aspect: AspectRatio
  previousShot?: Shot
  projectAspect?: AspectRatio
  canReferenceEarlier?: boolean
}>()
const emit = defineEmits<{
  (e: 'generate-locked-blocking'): void
}>()
const skipDefault = defineModel<boolean>('skipDefault', { default: false })
const lockBlocking = defineModel<boolean>('lockBlocking', { default: false })
const focalCharacterId = defineModel<string | null>('focalCharacterId', { default: null })
const store = useStudioStore()
const previousShotId = computed(() => props.previousShot?.id)
const hasEarlierEpisodeShots = computed(() => {
  const curEp = store.currentEpisode
  if (!curEp) return false
  return store.episodes.some(
    (ep) => ep.episode_number < curEp.episode_number && (ep.shot_count ?? 1) > 0,
  )
})
const canBrowse = computed(() => Boolean(props.canReferenceEarlier || previousShotId.value || hasEarlierEpisodeShots.value))
const localColorOverride = ref<{
  shotId: string
  aspect: AspectRatio
  url: string
  color_boost_strength: number
  can_undo_color: boolean
  can_redo_color: boolean
} | null>(null)
const currentOverride = computed(() => {
  const ov = localColorOverride.value
  if (!ov || ov.shotId !== props.shot.id || ov.aspect !== props.aspect) return null
  return ov
})
const active = computed(() => {
  const base = props.shot.image_references?.find((item) => item.aspect_ratio === props.aspect)
  if (!base) return undefined
  const ov = currentOverride.value
  return ov ? { ...base, ...ov } : base
})
const continuityFrame = computed(() => {
  const base = props.shot.continuity_frames?.find((item) => item.aspect_ratio === props.aspect)
  if (!base) return undefined
  const ov = currentOverride.value
  return ov ? { ...base, ...ov } : base
})
const retouchTarget = computed(() => active.value ?? continuityFrame.value)
const canRetouchReference = computed(() => Boolean(retouchTarget.value))
const currentBoostStrength = computed(() => Number(retouchTarget.value?.color_boost_strength ?? 0))
const currentBoostPercent = computed(() => Math.round(currentBoostStrength.value * 100))
const canUndoColor = computed(() => Boolean(retouchTarget.value?.can_undo_color))
const canRedoColor = computed(() => Boolean(retouchTarget.value?.can_redo_color))
const boostBadgeLabel = computed(() => {
  const pct = currentBoostPercent.value
  if (pct === 0) return ''
  return pct > 0 ? `+${pct}%` : `${pct}%`
})
const boostButtonTitle = computed(() => {
  if (currentBoostPercent.value === 100) {
    return 'Standard +100% color boost is active — use - / + to step by 25%, or Reset to restore 0%'
  }
  if (currentBoostPercent.value === 0) {
    return 'Apply standard +100% color & contrast boost preset in-place'
  }
  return `Return to standard +100% color boost preset (currently ${boostBadgeLabel.value})`
})
const isProcessingShot = computed(() => store.isProcessingShot(props.shot.id, props.aspect))
const retouchingColor = ref(false)
const repaintingAi = ref(false)
const defaultPreview = computed(() => {
  const frame = continuityFrame.value
  if (frame) {
    return {
      url: frame.url,
      title: frame.source_shot_title || 'Continuity frame',
      detail: `Video frame at ${(frame.source_time_ms / 1000).toFixed(3)} s · ${frame.aspect_ratio}`,
    }
  }
  const otherAspect: AspectRatio = props.aspect === '9:16' ? '16:9' : '9:16'
  const otherVariant = props.shot.media_variants?.find(
    (item) => item.media_type === 'image' && item.aspect_ratio === otherAspect,
  )
  const otherStill = otherVariant ?? (props.projectAspect === otherAspect ? props.shot.image : null)
  if (otherStill) {
    return {
      url: otherStill.url,
      title: `${props.shot.title || 'This shot'} (${otherAspect} → ${props.aspect})`,
      detail: `Approved ${otherAspect} shot frame · Will reframe/outpaint to ${props.aspect} automatically`,
    }
  }
  const otherRef = props.shot.image_references?.find((item) => item.aspect_ratio === otherAspect)
  if (otherRef) {
    return {
      url: otherRef.url,
      title: otherRef.source_shot_title || 'Cross-aspect reference',
      detail: `${describe(otherRef)} · Reused from ${otherRef.source_aspect_ratio}`,
    }
  }
  const otherFrame = props.shot.continuity_frames?.find((item) => item.aspect_ratio === otherAspect)
  if (otherFrame) {
    return {
      url: otherFrame.url,
      title: otherFrame.source_shot_title || 'Cross-aspect continuity frame',
      detail: `Video frame at ${(otherFrame.source_time_ms / 1000).toFixed(3)} s · Reused from ${otherFrame.aspect_ratio}`,
    }
  }
  return null
})
const history = computed(() => props.shot.takes?.filter((take) => take.media_type === 'image' && take.aspect_ratio === props.aspect && take.settings.image_reference) || [])
const sources = ref<ImageReferenceSourceShot[]>([])
const sourceShotId = ref('')
const sourceShot = computed(() => sources.value.find((item) => item.id === sourceShotId.value))
const showBrowser = ref(false)
const selectedVideo = ref<ImageReferenceSource | null>(null)
const busy = ref(false)
const error = ref('')
const loading = ref(false)

const showPunchModal = ref(false)
const punchSource = computed(() => {
  const target = retouchTarget.value
  if (target) {
    return {
      url: target.url,
      title: ('source_shot_title' in target ? target.source_shot_title : 'Reference frame') || 'Reference frame',
      sourceType: ('source_time_ms' in target ? 'continuity_frame' : 'reference') as 'reference' | 'continuity_frame',
      sourceId: target.id,
    }
  }
  if (defaultPreview.value) {
    return {
      url: defaultPreview.value.url,
      title: defaultPreview.value.title,
      sourceType: 'image' as const,
      sourceId: null,
    }
  }
  return null
})
const canPunchIn = computed(() => Boolean(punchSource.value))
const shotCharacters = computed(() => {
  const ids = new Set(props.shot.character_ids || [])
  return (store.currentProject?.characters || []).filter(
    (c) => ids.has(c.id) && (c.kind || 'character') === 'character',
  )
})
const focalTargetLabel = computed(() => {
  if (focalCharacterId.value) {
    const found = shotCharacters.value.find((c) => c.id === focalCharacterId.value)
    if (found) return found.name
  }
  if (shotCharacters.value.length > 0) {
    return shotCharacters.value.map((c) => c.name).join(' & ')
  }
  return 'Selected Cast'
})

function handleGenerateLockedBlocking() {
  lockBlocking.value = true
  emit('generate-locked-blocking')
}

// Lightbox state for viewing reference images full size
const lightboxDialogRef = ref<HTMLDialogElement | null>(null)
const lightboxPreview = ref<{ url: string; title: string; detail: string } | null>(null)

function openReferenceLightbox(url: string, title: string, detail: string) {
  lightboxPreview.value = { url, title, detail }
  void nextTick(() => {
    const dlg = lightboxDialogRef.value
    if (dlg && !dlg.open) {
      dlg.showModal()
    }
  })
}

function closeReferenceLightbox() {
  const dlg = lightboxDialogRef.value
  if (dlg && dlg.open) {
    dlg.close()
  }
  lightboxPreview.value = null
}

function handleLightboxBackdropClick(event: MouseEvent) {
  const dlg = lightboxDialogRef.value
  if (!dlg || event.target !== dlg) return
  const rect = dlg.getBoundingClientRect()
  const inside =
    rect.top <= event.clientY &&
    event.clientY <= rect.top + rect.height &&
    rect.left <= event.clientX &&
    event.clientX <= rect.left + rect.width
  if (!inside) {
    closeReferenceLightbox()
  }
}

function describe(ref: { source_media_type: string; source_time_ms?: number | null }) {
  return ref.source_media_type === 'clip' ? `Video frame at ${((ref.source_time_ms || 0) / 1000).toFixed(3)} s` : 'Still image'
}
async function browse(previous = false) {
  error.value = ''
  showBrowser.value = true
  loading.value = true
  try {
    sources.value = await api.imageReferenceSources(props.shot.id)
    const latestWithMedia = [...sources.value].reverse().find((item) => item.media.length > 0)?.id
    sourceShotId.value =
      (previous && previousShotId.value ? previousShotId.value : active.value?.source_shot_id) ||
      latestWithMedia ||
      sources.value[sources.value.length - 1]?.id ||
      ''
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not load sources' }
  finally { loading.value = false }
}
async function refresh() {
  if (store.currentProject) await store.loadProject(store.currentProject.id)
}
async function save(source: ImageReferenceSource, frame?: { file: File; timeMs: number }) {
  busy.value = true
  error.value = ''
  try {
    localColorOverride.value = null
    await api.saveImageReference(props.shot.id, source, props.aspect, frame)
    await refresh()
    selectedVideo.value = null
    showBrowser.value = false
  } catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not save reference' }
  finally { busy.value = false }
}
function choose(source: ImageReferenceSource) {
  if (source.source_media_type === 'clip') {
    selectedVideo.value = source
    showBrowser.value = false
  } else void save(source)
}
async function remove() {
  busy.value = true
  error.value = ''
  try {
    localColorOverride.value = null
    await api.removeImageReference(props.shot.id, props.aspect)
    await refresh()
  }
  catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not remove reference' }
  finally { busy.value = false }
}
async function runReferenceColorAction(action?: ReferenceColorRetouchAction, strength?: number) {
  if (!canRetouchReference.value || retouchingColor.value || busy.value) return
  retouchingColor.value = true
  error.value = ''
  try {
    const isMockedStoreAction = typeof store.retouchShotReferenceColor === 'function' && 'mock' in store.retouchShotReferenceColor
    const updated = isMockedStoreAction
      ? (action
        ? await store.retouchShotReferenceColor(props.shot.id, props.aspect, { action, strength })
        : await store.retouchShotReferenceColor(props.shot.id, props.aspect))
      : await api.retouchShotReferenceColor(props.shot.id, props.aspect, action ? { action, strength } : undefined)
    const nextRef = updated?.image_references?.find((item) => item.aspect_ratio === props.aspect)
      ?? updated?.continuity_frames?.find((item) => item.aspect_ratio === props.aspect)
    if (nextRef) {
      localColorOverride.value = {
        shotId: props.shot.id,
        aspect: props.aspect,
        url: nextRef.url,
        color_boost_strength: Number(nextRef.color_boost_strength ?? 0),
        can_undo_color: Boolean(nextRef.can_undo_color),
        can_redo_color: Boolean(nextRef.can_redo_color),
      }
      if (lightboxPreview.value) {
        lightboxPreview.value = {
          ...lightboxPreview.value,
          url: nextRef.url,
        }
      }
    }
    if (!isMockedStoreAction && typeof store.refreshShot === 'function') {
      await store.refreshShot(props.shot.id)
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not adjust reference frame color'
  } finally {
    retouchingColor.value = false
  }
}
async function handleBoostReferenceColor() {
  await runReferenceColorAction()
}
async function handleAiRepaintReference() {
  if (!canRetouchReference.value || repaintingAi.value || isProcessingShot.value || busy.value) return
  repaintingAi.value = true
  error.value = ''
  try {
    localColorOverride.value = null
    await store.repaintShotReference(props.shot.id, props.aspect, 'repaint this image')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not repaint reference frame'
  } finally {
    repaintingAi.value = false
  }
}
</script>

<template>
  <section class="image-reference-panel" aria-label="Image reference">
    <div class="reference-header">
      <div class="reference-heading">
        <ImageIcon :size="16" class="reference-icon" />
        <strong class="reference-title">Visual & Video Frame Reference</strong>
        <span class="aspect-badge">{{ aspect }}</span>
      </div>
      <p class="reference-subhead">Visual continuity · Grab a frame from an earlier video clip or select a still image to preserve identity, wardrobe, setting, and lighting.</p>
    </div>
    <div v-if="active" class="reference-preview">
      <button
        type="button"
        class="reference-thumb-btn"
        title="Click to view reference image full size"
        @click="openReferenceLightbox(active.url, active.source_shot_title || 'Selected image reference', `${describe(active)} · ${active.source_aspect_ratio}`)"
      >
        <img :src="active.url" alt="Selected image reference" />
        <span class="reference-zoom-badge" aria-hidden="true"><Maximize2 :size="11" /></span>
      </button>
      <div><strong>{{ active.source_shot_title }}</strong><p>{{ describe(active) }} · {{ active.source_aspect_ratio }}</p></div>
    </div>
    <template v-else-if="defaultPreview">
      <div class="reference-preview">
        <button
          type="button"
          class="reference-thumb-btn"
          title="Click to view reference image full size"
          @click="openReferenceLightbox(defaultPreview.url, defaultPreview.title, defaultPreview.detail)"
        >
          <img :src="defaultPreview.url" alt="Automatic reference preview" />
          <span class="reference-zoom-badge" aria-hidden="true"><Maximize2 :size="11" /></span>
        </button>
        <div>
          <strong>{{ defaultPreview.title }}</strong>
          <p>{{ defaultPreview.detail }}</p>
        </div>
      </div>
      <label class="skip-default">
        <input v-model="skipDefault" type="checkbox" />
        <span>Skip automatic reference and generate from prompt only</span>
      </label>
    </template>
    <p v-else>No reference selected. Generate using this shot's prompt and selected cast, or click "Previous shot" or "Choose reference shot" to manually attach a reference.</p>
    <div class="reference-actions">
      <button v-if="previousShotId" class="secondary-button" :disabled="busy" @click="browse(true)">Previous shot</button>
      <button class="secondary-button" :disabled="busy || !canBrowse" @click="browse()">{{ active ? 'Change' : 'Choose reference shot' }}</button>
      <div v-if="canRetouchReference" class="reference-color-controls" role="group" aria-label="Reference color controls">
        <button
          type="button"
          class="secondary-button reference-retouch-btn"
          :disabled="busy || retouchingColor || isProcessingShot || currentBoostPercent === 100"
          :title="boostButtonTitle"
          @click="handleBoostReferenceColor"
        >
          <RefreshCw v-if="retouchingColor" :size="13" class="spinning" />
          <Palette v-else :size="13" />
          <span>{{ retouchingColor ? 'Adjusting…' : 'Boost Color' }}</span>
          <span v-if="boostBadgeLabel" class="boost-level-pill">{{ boostBadgeLabel }}</span>
        </button>
        <button
          type="button"
          class="secondary-button reference-color-step-btn"
          :disabled="busy || retouchingColor || isProcessingShot || currentBoostStrength <= -1"
          aria-label="Reduce color boost"
          title="Reduce color boost (-25%)"
          @click="runReferenceColorAction('decrease')"
        >
          <Minus :size="13" />
        </button>
        <button
          type="button"
          class="secondary-button reference-color-step-btn"
          :disabled="busy || retouchingColor || isProcessingShot || currentBoostStrength >= 3"
          aria-label="Increase color boost"
          title="Increase color boost (+25%)"
          @click="runReferenceColorAction('increase')"
        >
          <Plus :size="13" />
        </button>
        <button
          type="button"
          class="secondary-button reference-color-step-btn"
          :disabled="busy || retouchingColor || isProcessingShot || !canUndoColor"
          aria-label="Undo color change"
          title="Undo color change"
          @click="runReferenceColorAction('undo')"
        >
          <Undo2 :size="13" />
        </button>
        <button
          type="button"
          class="secondary-button reference-color-step-btn"
          :disabled="busy || retouchingColor || isProcessingShot || !canRedoColor"
          aria-label="Redo color change"
          title="Redo color change"
          @click="runReferenceColorAction('redo')"
        >
          <Redo2 :size="13" />
        </button>
        <button
          v-if="currentBoostPercent !== 0"
          type="button"
          class="secondary-button reference-color-step-btn reset"
          :disabled="busy || retouchingColor || isProcessingShot"
          aria-label="Reset color boost"
          title="Reset to original un-boosted frame (0%)"
          @click="runReferenceColorAction('reset')"
        >
          <RotateCcw :size="12" />
          <span>Reset</span>
        </button>
      </div>
      <button
        v-if="canRetouchReference"
        type="button"
        class="secondary-button reference-repaint-btn"
        :disabled="busy || retouchingColor || repaintingAi || isProcessingShot"
        title="Ask AI to repaint and revive the color of this faint frame while preserving exact composition, faces, and details"
        @click="handleAiRepaintReference"
      >
        <RefreshCw v-if="repaintingAi || isProcessingShot" :size="13" class="spinning" />
        <Sparkles v-else :size="13" />
        <span>{{ repaintingAi ? 'Repainting…' : 'AI Repaint' }}</span>
      </button>
      <button
        v-if="canPunchIn"
        type="button"
        class="secondary-button reference-punch-btn"
        :disabled="busy || isProcessingShot"
        title="Optical punch-in: Crop directly from this reference frame to focus on a character with 0% AI hallucination"
        @click="showPunchModal = true"
      >
        <Crop :size="13" />
        <span>Optical Punch-In</span>
      </button>
      <button v-if="active" class="ghost-link" :disabled="busy" @click="remove">Remove</button>
    </div>
    <div v-if="retouchTarget || defaultPreview" class="reference-blocking-options">
      <div class="blocking-header-row">
        <label class="blocking-checkbox" title="Turn and zoom the camera toward selected character(s) directly from this reference image without changing anything else in the image">
          <input v-model="lockBlocking" type="checkbox" :disabled="busy || isProcessingShot" />
          <span class="blocking-checkbox-text">
            <Lock :size="12" />
            <span>Lock scene blocking (camera zoom on reference without changing positions)</span>
          </span>
        </label>
      </div>
      <div class="focal-cast-row">
        <template v-if="shotCharacters.length">
          <label for="focal-char-select">Focus on:</label>
          <select id="focal-char-select" v-model="focalCharacterId" :disabled="busy || isProcessingShot">
            <option :value="null">Selected cast ({{ shotCharacters.map((c) => c.name).join(' & ') }})</option>
            <option v-for="char in shotCharacters" :key="char.id" :value="char.id">
              Only {{ char.name }}
            </option>
          </select>
        </template>
        <button
          type="button"
          class="primary-button blocking-zoom-generate-btn"
          :disabled="busy || isProcessingShot"
          title="Dedicated Lock Scene Blocking generator: turns and zooms the camera toward the selected character(s) directly from this reference image without running normal continuity prompts"
          @click="handleGenerateLockedBlocking"
        >
          <RefreshCw v-if="isProcessingShot" :size="13" class="spinning" />
          <Lock v-else :size="13" />
          <span>{{ isProcessingShot ? 'Zooming camera…' : `Zoom Camera on ${focalTargetLabel}` }}</span>
        </button>
      </div>
    </div>
    <p v-if="!canBrowse">References become available after the first shot.</p>
    <p v-if="error && !showBrowser && !selectedVideo" role="alert">{{ error }}</p>
    <details v-if="history.length" class="reference-history">
      <summary>References used by image takes ({{ history.length }})</summary>
      <div v-for="take in history" :key="take.id" class="reference-preview">
        <button
          type="button"
          class="reference-thumb-btn"
          title="Click to view reference image full size"
          @click="openReferenceLightbox(take.settings.image_reference!.url, take.settings.image_reference!.source_shot_title || 'Reference image', `${describe(take.settings.image_reference!)} · Visual continuity`)"
        >
          <img :src="take.settings.image_reference!.url" alt="Reference used by this image take" />
          <span class="reference-zoom-badge" aria-hidden="true"><Maximize2 :size="11" /></span>
        </button>
        <div>
          <a :href="take.url" target="_blank" rel="noopener">{{ take.selected ? 'Selected take' : 'Image take' }} · {{ new Date(take.created_at).toLocaleString() }}</a>
          <p>{{ take.settings.image_reference!.source_shot_title }} · {{ describe(take.settings.image_reference!) }} · Visual continuity</p>
        </div>
      </div>
    </details>

    <dialog
      ref="lightboxDialogRef"
      class="reference-lightbox-dialog"
      closedby="any"
      :aria-label="lightboxPreview?.title || 'Reference image preview'"
      @close="closeReferenceLightbox"
      @click="handleLightboxBackdropClick"
    >
      <div v-if="lightboxPreview" class="reference-lightbox-shell" @click.stop>
        <header class="reference-lightbox-header">
          <div class="reference-lightbox-title">
            <strong>{{ lightboxPreview.title }}</strong>
            <span>{{ lightboxPreview.detail }}</span>
          </div>
          <button
            type="button"
            class="reference-lightbox-close"
            aria-label="Close full-size reference image"
            title="Close (Esc)"
            @click="closeReferenceLightbox"
          >
            <X :size="16" />
          </button>
        </header>
        <div class="reference-lightbox-stage">
          <img :src="lightboxPreview.url" :alt="lightboxPreview.title" class="reference-lightbox-img" />
        </div>
      </div>
    </dialog>

    <OpticalPunchModal
      v-if="punchSource"
      v-model:open="showPunchModal"
      :shot-id="shot.id"
      :src="punchSource.url"
      :aspect="aspect"
      :source-type="punchSource.sourceType"
      :source-id="punchSource.sourceId"
      :source-title="punchSource.title"
      :characters="shotCharacters"
    />
  </section>
  <Dialog :open="showBrowser" title="Choose an image reference" description="Browse earlier shots. Select a still image or choose a frame from a video." width-class="max-w-3xl" @update:open="!busy && (showBrowser = $event)">
    <p v-if="loading">Loading available media…</p>
    <template v-else>
      <label class="source-select">Source shot
        <select v-model="sourceShotId" :disabled="busy"><option v-for="item in sources" :key="item.id" :value="item.id">{{ item.title || 'Untitled shot' }}</option></select>
      </label>
      <div class="source-grid">
        <button v-for="item in sourceShot?.media" :key="item.source_media_kind + item.source_media_id" class="source-card" :disabled="busy" @click="choose(item)">
          <img v-if="item.source_media_type === 'image'" :src="item.url" alt="Source still" />
          <video v-else :src="item.url" preload="metadata" muted playsinline></video>
          <span>{{ item.source_media_type === 'clip' ? 'Choose video frame' : 'Use still image' }} · {{ item.source_aspect_ratio }}</span>
        </button>
      </div>
      <p v-if="!sourceShot?.media.length">This shot has no available still images or videos.</p>
    </template>
    <p v-if="error" role="alert">{{ error }}</p>
  </Dialog>
  <VideoFramePicker v-if="selectedVideo" :key="selectedVideo.source_media_id" :src="selectedVideo.url" title="Choose a reference frame"
    :description="`Select a frame from ${selectedVideo.source_shot_title} for visual guidance.`"
    :busy="busy" :error="error" @close="selectedVideo = null; showBrowser = true; error = ''" @save="save(selectedVideo!, $event)" />
</template>

<style scoped>
.image-reference-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  margin: 16px 0;
  background: var(--surface-1, #131514);
  border: 1px solid var(--line-default, #2a2e2b);
  border-radius: 12px;
}
.reference-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.reference-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--text-primary, #e2e8f0);
}
.reference-icon {
  color: #38bdf8;
}
.aspect-badge {
  font-size: 10px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
  color: var(--text-secondary, #94a3b8);
}
.reference-subhead {
  margin: 0;
  color: var(--text-secondary, #94a3b8);
  font-size: 12px;
  line-height: 1.5;
}
p { margin: 4px 0; color: var(--text-secondary); font-size: 12px; line-height: 1.6; }
.reference-preview { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
.reference-thumb-btn {
  position: relative;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 7px;
  background: #111;
  cursor: zoom-in;
  overflow: hidden;
  flex-shrink: 0;
  transition: border-color 0.15s ease, transform 0.15s ease;
}
.reference-thumb-btn:hover {
  border-color: #38bdf8;
  transform: scale(1.03);
}
.reference-thumb-btn img {
  display: block;
  width: 88px;
  height: 66px;
  object-fit: contain;
  background: #111;
}
.reference-zoom-badge {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 22px;
  height: 22px;
  border-radius: 5px;
  background: rgba(15, 17, 21, 0.8);
  border: 1px solid rgba(255, 255, 255, 0.18);
  color: #e2e8f0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.15s ease, color 0.15s ease;
}
.reference-thumb-btn:hover .reference-zoom-badge {
  opacity: 1;
  color: #38bdf8;
}
.reference-preview strong, .reference-preview a { font-size: 13px; }
.reference-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.reference-color-controls {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.reference-retouch-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-color: rgba(245, 158, 11, 0.35);
  color: #fbbf24;
  background: rgba(245, 158, 11, 0.1);
}
.reference-retouch-btn:hover:not(:disabled) {
  border-color: #f59e0b;
  background: rgba(245, 158, 11, 0.18);
  color: #fde68a;
}
.boost-level-pill {
  display: inline-flex;
  align-items: center;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  background: rgba(245, 158, 11, 0.22);
  color: #fde68a;
}
.reference-color-step-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-width: 30px;
  padding-inline: 8px;
  border-color: rgba(245, 158, 11, 0.28);
  color: #fbbf24;
  background: rgba(245, 158, 11, 0.07);
}
.reference-color-step-btn:hover:not(:disabled) {
  border-color: #f59e0b;
  background: rgba(245, 158, 11, 0.16);
  color: #fde68a;
}
.reference-color-step-btn:disabled {
  opacity: 0.38;
  cursor: not-allowed;
}
.reference-color-step-btn.reset {
  font-size: 11px;
  padding-inline: 9px;
}
.reference-repaint-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-color: rgba(168, 85, 247, 0.35);
  color: #c084fc;
  background: rgba(168, 85, 247, 0.1);
}
.reference-repaint-btn:hover:not(:disabled) {
  border-color: #a855f7;
  background: rgba(168, 85, 247, 0.18);
  color: #e9d5ff;
}
.spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.skip-default { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--text-secondary); }
.source-select { display: grid; gap: 8px; margin-bottom: 16px; }
.source-select select { padding: 10px; border: 1px solid var(--line-default); border-radius: 8px; background: var(--surface-2); }
.source-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.source-card { padding: 8px; border: 1px solid var(--line-default); border-radius: 10px; text-align: left; }
.source-card img, .source-card video { width: 100%; height: 140px; object-fit: contain; background: #111; }
.source-card span { display: block; font-size: 12px; margin-top: 8px; }
.reference-history { font-size: 12px; }
[role=alert] { color: var(--status-danger, #ef8585); }

.reference-lightbox-dialog {
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 14px;
  background: #0f1114;
  color: #e2e8f0;
  padding: 0;
  max-width: min(94vw, 1360px);
  max-height: 92vh;
  width: fit-content;
  margin: auto;
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.75);
  overflow: hidden;
}
.reference-lightbox-dialog::backdrop {
  background: rgba(5, 7, 10, 0.84);
  backdrop-filter: blur(6px);
}
.reference-lightbox-shell {
  display: flex;
  flex-direction: column;
  max-height: 92vh;
  min-width: min(76vw, 680px);
}
.reference-lightbox-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.09);
  background: #14171a;
}
.reference-lightbox-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.reference-lightbox-title strong {
  font-size: 14px;
  color: #f8fafc;
}
.reference-lightbox-title span {
  font-size: 12px;
  color: #94a3b8;
}
.reference-lightbox-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 7px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.05);
  color: #cbd5e1;
  cursor: pointer;
}
.reference-lightbox-close:hover {
  background: rgba(248, 113, 113, 0.16);
  border-color: rgba(248, 113, 113, 0.4);
  color: #f87171;
}
.reference-lightbox-stage {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: #090b0d;
  overflow: auto;
}
.reference-lightbox-img {
  display: block;
  max-width: min(90vw, 1280px);
  max-height: calc(92vh - 96px);
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: 8px;
}
.reference-punch-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border-color: rgba(56, 189, 248, 0.35);
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.1);
}
.reference-punch-btn:hover:not(:disabled) {
  border-color: #38bdf8;
  background: rgba(56, 189, 248, 0.2);
  color: #bae6fd;
}
.reference-blocking-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  margin-top: 4px;
}
.blocking-checkbox {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-primary, #e2e8f0);
  cursor: pointer;
  user-select: none;
}
.blocking-checkbox-text {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.blocking-checkbox-text svg {
  color: #38bdf8;
}
.focal-cast-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary, #94a3b8);
  margin-left: 24px;
}
.focal-cast-row select {
  padding: 5px 8px;
  font-size: 12px;
  background: rgba(255, 255, 255, 0.07);
  border: 1px solid rgba(255, 255, 255, 0.15);
  border-radius: 5px;
  color: #e2e8f0;
}
.blocking-zoom-generate-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  font-size: 12px;
  font-weight: 600;
  border-radius: 6px;
  border: 1px solid rgba(56, 189, 248, 0.45);
  background: rgba(56, 189, 248, 0.18);
  color: #e0f2fe;
  cursor: pointer;
  transition: background 0.15s ease, border-color 0.15s ease;
}
.blocking-zoom-generate-btn:hover:not(:disabled) {
  background: rgba(56, 189, 248, 0.28);
  border-color: #38bdf8;
  color: #ffffff;
}
.blocking-zoom-generate-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
