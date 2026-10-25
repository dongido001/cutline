<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Crop, RefreshCw, Sparkles, Check, Move } from '@lucide/vue'
import Dialog from '@/components/ui/Dialog.vue'
import { useStudioStore } from '@/stores/studio'
import type { AspectRatio } from '@/lib/api'

const props = withDefaults(
  defineProps<{
    open: boolean
    shotId: string
    src: string
    aspect: AspectRatio
    sourceType?: 'reference' | 'take' | 'continuity_frame' | 'image'
    sourceId?: string | null
    sourceTitle?: string
    characters?: Array<{ id: string; name: string }>
  }>(),
  {
    sourceType: 'reference',
    sourceId: null,
    sourceTitle: 'Reference frame',
    characters: () => [],
  },
)

const emit = defineEmits<{
  'update:open': [value: boolean]
  created: []
}>()

const store = useStudioStore()

// Normalized crop box (0.0 to 1.0)
const cropX = ref(0.2)
const cropY = ref(0.1)
const cropWidth = ref(0.6)
const cropHeight = ref(0.6)
const label = ref('')

const busy = ref(false)
const error = ref('')
const imgElement = ref<HTMLImageElement | null>(null)
const naturalWidth = ref(1920)
const naturalHeight = ref(1080)

const targetRatio = computed(() => (props.aspect === '16:9' ? 16 / 9 : 9 / 16))
const imageRatio = computed(() => naturalWidth.value / (naturalHeight.value || 1))

// Initialize / reset crop to a healthy center frame
function resetToPreset(preset: 'left' | 'center' | 'right' | 'tight' | 'full') {
  const imgR = imageRatio.value
  const tgtR = targetRatio.value

  let w = 0.55
  let h = (w * imgR) / tgtR

  if (h > 0.9) {
    h = 0.9
    w = (h * tgtR) / imgR
  }

  if (preset === 'tight') {
    w = 0.35
    h = (w * imgR) / tgtR
    if (h > 0.9) {
      h = 0.9
      w = (h * tgtR) / imgR
    }
    cropX.value = Math.max(0, Math.min(1 - w, 0.5 - w / 2))
    cropY.value = Math.max(0, Math.min(1 - h, 0.5 - h / 2))
  } else if (preset === 'full') {
    if (imgR > tgtR) {
      // Image is wider than target: full height, center width
      h = 1.0
      w = Math.min(1.0, (h * tgtR) / imgR)
      cropX.value = (1.0 - w) / 2
      cropY.value = 0
    } else {
      // Image is taller than target: full width, center height
      w = 1.0
      h = Math.min(1.0, (w * imgR) / tgtR)
      cropX.value = 0
      cropY.value = (1.0 - h) / 2
    }
  } else if (preset === 'left') {
    cropX.value = 0.05
    cropY.value = Math.max(0, Math.min(1 - h, 0.15))
  } else if (preset === 'right') {
    cropX.value = Math.max(0, 1.0 - w - 0.05)
    cropY.value = Math.max(0, Math.min(1 - h, 0.15))
  } else {
    // center
    cropX.value = Math.max(0, (1.0 - w) / 2)
    cropY.value = Math.max(0, (1.0 - h) / 2)
  }

  cropWidth.value = Math.min(1.0, w)
  cropHeight.value = Math.min(1.0, h)
}

function handleImageLoaded(e: Event) {
  const target = e.target as HTMLImageElement
  naturalWidth.value = target.naturalWidth || 1920
  naturalHeight.value = target.naturalHeight || 1080
  resetToPreset('center')
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      error.value = ''
      label.value = ''
      if (imgElement.value?.complete) {
        naturalWidth.value = imgElement.value.naturalWidth || 1920
        naturalHeight.value = imgElement.value.naturalHeight || 1080
        resetToPreset('center')
      }
    }
  },
)

// Interactive drag logic
const isDragging = ref(false)
const dragStartX = ref(0)
const dragStartY = ref(0)
const initialCropX = ref(0)
const initialCropY = ref(0)

function onPointerDown(e: MouseEvent | TouchEvent) {
  isDragging.value = true
  const clientX = 'touches' in e ? (e.touches[0]?.clientX ?? 0) : e.clientX
  const clientY = 'touches' in e ? (e.touches[0]?.clientY ?? 0) : e.clientY
  dragStartX.value = clientX
  dragStartY.value = clientY
  initialCropX.value = cropX.value
  initialCropY.value = cropY.value

  window.addEventListener('mousemove', onPointerMove)
  window.addEventListener('mouseup', onPointerUp)
  window.addEventListener('touchmove', onPointerMove)
  window.addEventListener('touchend', onPointerUp)
}

function onPointerMove(e: MouseEvent | TouchEvent) {
  if (!isDragging.value || !imgElement.value) return
  const clientX = 'touches' in e ? (e.touches[0]?.clientX ?? 0) : e.clientX
  const clientY = 'touches' in e ? (e.touches[0]?.clientY ?? 0) : e.clientY

  const rect = imgElement.value.getBoundingClientRect()
  if (!rect.width || !rect.height) return

  const deltaX = (clientX - dragStartX.value) / rect.width
  const deltaY = (clientY - dragStartY.value) / rect.height

  let nextX = initialCropX.value + deltaX
  let nextY = initialCropY.value + deltaY

  nextX = Math.max(0, Math.min(1 - cropWidth.value, nextX))
  nextY = Math.max(0, Math.min(1 - cropHeight.value, nextY))

  cropX.value = nextX
  cropY.value = nextY
}

function onPointerUp() {
  isDragging.value = false
  window.removeEventListener('mousemove', onPointerMove)
  window.removeEventListener('mouseup', onPointerUp)
  window.removeEventListener('touchmove', onPointerMove)
  window.removeEventListener('touchend', onPointerUp)
}

// Adjust scale / zoom slider
const zoomPercent = computed({
  get: () => Math.round((1.05 - cropWidth.value) * 100),
  set: (val: number) => {
    const fraction = Math.max(0.15, Math.min(1.0, 1.05 - val / 100))
    const imgR = imageRatio.value
    const tgtR = targetRatio.value

    let w = fraction
    let h = (w * imgR) / tgtR
    if (h > 1.0) {
      h = 1.0
      w = (h * tgtR) / imgR
    }

    // Keep center anchored
    const centerX = cropX.value + cropWidth.value / 2
    const centerY = cropY.value + cropHeight.value / 2

    cropWidth.value = w
    cropHeight.value = h
    cropX.value = Math.max(0, Math.min(1 - w, centerX - w / 2))
    cropY.value = Math.max(0, Math.min(1 - h, centerY - h / 2))
  },
})

function applyCharacterName(name: string) {
  label.value = `Close-up on ${name}`
}

async function handleCreateTake() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    await store.deriveTakeFromCrop(props.shotId, {
      aspect_ratio: props.aspect,
      x: cropX.value,
      y: cropY.value,
      width: cropWidth.value,
      height: cropHeight.value,
      source_type: props.sourceType,
      source_id: props.sourceId,
      label: label.value.trim() || undefined,
    })
    emit('created')
    emit('update:open', false)
  } catch (err: any) {
    error.value = err?.message || 'Failed to derive take from crop'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <Dialog
    :open="open"
    title="Optical Punch-In (Direct Frame Crop)"
    description="Derive a clean take by zooming into this frame with 0% AI hallucination. Preserves exact actor positions, costumes, background, and lighting."
    width-class="max-w-4xl"
    @update:open="!busy && emit('update:open', false)"
  >
    <div class="optical-punch-container">
      <!-- Top controls & Presets -->
      <div class="preset-bar">
        <span class="preset-label">Framing Presets:</span>
        <button type="button" class="preset-btn" :disabled="busy" @click="resetToPreset('left')">
          Stage Left (Left 1/3)
        </button>
        <button type="button" class="preset-btn" :disabled="busy" @click="resetToPreset('center')">
          Center
        </button>
        <button type="button" class="preset-btn" :disabled="busy" @click="resetToPreset('right')">
          Stage Right (Right 1/3)
        </button>
        <button type="button" class="preset-btn" :disabled="busy" @click="resetToPreset('tight')">
          Tight Close-Up
        </button>
        <button type="button" class="preset-btn" :disabled="busy" @click="resetToPreset('full')">
          Reset Full Frame
        </button>
      </div>

      <!-- Main Cropping Workspace -->
      <div class="crop-workspace-wrapper">
        <div class="crop-stage">
          <img
            ref="imgElement"
            :src="src"
            alt="Source reference for optical punch-in"
            class="crop-source-image"
            @load="handleImageLoaded"
          />

          <!-- Darkened Overlay outside crop box -->
          <div
            class="crop-box"
            :style="{
              left: `${cropX * 100}%`,
              top: `${cropY * 100}%`,
              width: `${cropWidth * 100}%`,
              height: `${cropHeight * 100}%`,
            }"
            @mousedown="onPointerDown"
            @touchstart.passive="onPointerDown"
          >
            <!-- Rule of Thirds Grid -->
            <div class="grid-line grid-v-1"></div>
            <div class="grid-line grid-v-2"></div>
            <div class="grid-line grid-h-1"></div>
            <div class="grid-line grid-h-2"></div>

            <div class="crop-badge">
              <Move :size="12" />
              <span>{{ aspect }} Punch-In</span>
            </div>
          </div>
        </div>

        <!-- Zoom Slider -->
        <div class="zoom-slider-row">
          <label for="punch-zoom-range">Punch-In Depth (Zoom):</label>
          <input
            id="punch-zoom-range"
            v-model.number="zoomPercent"
            type="range"
            min="5"
            max="80"
            step="1"
            :disabled="busy"
          />
          <span class="zoom-value">{{ zoomPercent }}% zoom</span>
        </div>
      </div>

      <!-- Take Meta & Label -->
      <div class="take-meta-row">
        <div class="label-input-group">
          <label for="take-label-input">Take Label (optional):</label>
          <input
            id="take-label-input"
            v-model="label"
            type="text"
            placeholder="e.g. Close-up on Tobi"
            :disabled="busy"
          />
        </div>
        <div v-if="characters.length" class="character-quick-tags">
          <span class="tag-title">Tag character:</span>
          <button
            v-for="char in characters"
            :key="char.id"
            type="button"
            class="char-chip"
            :disabled="busy"
            @click="applyCharacterName(char.name)"
          >
            + {{ char.name }}
          </button>
        </div>
      </div>

      <p v-if="error" class="error-banner" role="alert">{{ error }}</p>

      <!-- Footer Actions -->
      <div class="modal-footer">
        <button
          type="button"
          class="secondary-button"
          :disabled="busy"
          @click="emit('update:open', false)"
        >
          Cancel
        </button>
        <button
          type="button"
          class="primary-button create-take-btn"
          :disabled="busy"
          @click="handleCreateTake"
        >
          <RefreshCw v-if="busy" :size="14" class="spinning" />
          <Crop v-else :size="14" />
          <span>{{ busy ? 'Cropping & Enhancing…' : 'Create Derived Take' }}</span>
        </button>
      </div>
    </div>
  </Dialog>
</template>

<style scoped>
.optical-punch-container {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.preset-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem;
  background: var(--color-surface-soft, rgba(255, 255, 255, 0.03));
  padding: 0.5rem 0.75rem;
  border-radius: 6px;
  font-size: 0.8125rem;
}

.preset-label {
  font-weight: 500;
  color: var(--color-text-muted, #9ca3af);
  margin-right: 0.25rem;
}

.preset-btn {
  background: var(--color-surface, rgba(255, 255, 255, 0.06));
  border: 1px solid var(--color-border, rgba(255, 255, 255, 0.1));
  color: var(--color-text, #e5e7eb);
  padding: 0.25rem 0.6rem;
  border-radius: 4px;
  font-size: 0.75rem;
  cursor: pointer;
  transition: all 0.15s ease;
}

.preset-btn:hover:not(:disabled) {
  background: var(--color-primary-soft, rgba(59, 130, 246, 0.2));
  border-color: var(--color-primary, #3b82f6);
  color: #fff;
}

.crop-workspace-wrapper {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.crop-stage {
  position: relative;
  width: 100%;
  max-height: 52vh;
  display: flex;
  justify-content: center;
  align-items: center;
  background: #000;
  border-radius: 6px;
  overflow: hidden;
  user-select: none;
}

.crop-source-image {
  max-width: 100%;
  max-height: 52vh;
  object-fit: contain;
  display: block;
}

.crop-box {
  position: absolute;
  cursor: grab;
  border: 2px solid #3b82f6;
  box-shadow: 0 0 0 9999px rgba(0, 0, 0, 0.65), 0 0 10px rgba(59, 130, 246, 0.5);
  box-sizing: border-box;
  touch-action: none;
}

.crop-box:active {
  cursor: grabbing;
}

/* Rule of Thirds */
.grid-line {
  position: absolute;
  background: rgba(255, 255, 255, 0.25);
  pointer-events: none;
}

.grid-v-1 {
  left: 33.33%;
  top: 0;
  bottom: 0;
  width: 1px;
}

.grid-v-2 {
  left: 66.66%;
  top: 0;
  bottom: 0;
  width: 1px;
}

.grid-h-1 {
  top: 33.33%;
  left: 0;
  right: 0;
  height: 1px;
}

.grid-h-2 {
  top: 66.66%;
  left: 0;
  right: 0;
  height: 1px;
}

.crop-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  background: rgba(0, 0, 0, 0.75);
  backdrop-filter: blur(4px);
  color: #60a5fa;
  border: 1px solid rgba(59, 130, 246, 0.4);
  border-radius: 3px;
  padding: 2px 6px;
  font-size: 0.6875rem;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 4px;
  pointer-events: none;
}

.zoom-slider-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.8125rem;
  color: var(--color-text-muted, #9ca3af);
}

.zoom-slider-row input[type='range'] {
  flex: 1;
}

.zoom-value {
  font-variant-numeric: tabular-nums;
  min-width: 4.5rem;
  text-align: right;
  font-weight: 500;
  color: var(--color-text, #e5e7eb);
}

.take-meta-row {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.label-input-group {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.label-input-group label {
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--color-text-muted, #9ca3af);
}

.label-input-group input {
  padding: 0.4rem 0.6rem;
  font-size: 0.8125rem;
  background: var(--color-surface-soft, rgba(255, 255, 255, 0.05));
  border: 1px solid var(--color-border, rgba(255, 255, 255, 0.12));
  border-radius: 4px;
  color: var(--color-text, #fff);
}

.character-quick-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.35rem;
  font-size: 0.75rem;
}

.tag-title {
  color: var(--color-text-muted, #9ca3af);
}

.char-chip {
  background: var(--color-surface, rgba(255, 255, 255, 0.06));
  border: 1px solid var(--color-border, rgba(255, 255, 255, 0.1));
  color: var(--color-text, #e5e7eb);
  padding: 0.15rem 0.5rem;
  border-radius: 12px;
  font-size: 0.6875rem;
  cursor: pointer;
  transition: all 0.15s ease;
}

.char-chip:hover {
  background: var(--color-primary-soft, rgba(59, 130, 246, 0.2));
  border-color: var(--color-primary, #3b82f6);
  color: #fff;
}

.error-banner {
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.4);
  color: #f87171;
  padding: 0.5rem 0.75rem;
  border-radius: 4px;
  font-size: 0.8125rem;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
  margin-top: 0.5rem;
}

.create-take-btn {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
