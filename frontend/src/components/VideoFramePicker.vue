<script setup lang="ts">
import { ref } from 'vue'
import Dialog from '@/components/ui/Dialog.vue'

withDefaults(defineProps<{ src: string; title: string; description: string; busy?: boolean; error?: string }>(), {
  busy: false, error: '',
})
const emit = defineEmits<{ close: []; save: [frame: { file: File; timeMs: number }] }>()
const video = ref<HTMLVideoElement | null>(null)
const duration = ref(0)
const time = ref(0)
const seeking = ref(false)
const capturing = ref(false)
const captureError = ref('')
function sync() { time.value = video.value?.currentTime || 0 }
function seek(event: Event) {
  if (!video.value) return
  seeking.value = true
  video.value.currentTime = Number((event.target as HTMLInputElement).value) / 1000
}
function jumpTo(fraction: number) {
  if (!video.value || !duration.value) return
  seeking.value = true
  // For the last frame, offset by 0.06s to guarantee the video decoder renders the final valid frame
  const target = fraction === 1 ? Math.max(0, duration.value - 0.06) : duration.value * fraction
  video.value.currentTime = target
}
async function capture() {
  const player = video.value
  if (!player || player.seeking || player.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
    captureError.value = 'Wait for the selected frame to load.'
    return
  }
  capturing.value = true
  captureError.value = ''
  player.pause()
  const timeMs = Math.round(player.currentTime * 1000)
  try {
    const canvas = document.createElement('canvas')
    canvas.width = player.videoWidth
    canvas.height = player.videoHeight
    const context = canvas.getContext('2d')
    if (!context || !canvas.width || !canvas.height) throw new Error('Could not read this frame')
    context.drawImage(player, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(
      (value) => value ? resolve(value) : reject(new Error('Could not capture this frame')), 'image/png',
    ))
    emit('save', { file: new File([blob], 'reference-frame.png', { type: 'image/png' }), timeMs })
  } catch (cause) {
    captureError.value = cause instanceof Error ? cause.message : 'Could not capture this frame'
  } finally { capturing.value = false }
}
</script>

<template>
  <Dialog :open="true" :title="title" :description="description" width-class="max-w-3xl" @update:open="!busy && !capturing && emit('close')">
    <video ref="video" :src="src" controls playsinline preload="metadata" class="frame-video"
      @loadedmetadata="duration = Number.isFinite(video?.duration) ? video!.duration : 0"
      @seeking="seeking = true" @seeked="seeking = false; sync()" @timeupdate="sync"
      @error="captureError = 'This video could not be loaded. Choose another source.'"></video>
    <div class="frame-scrubber-controls">
      <div class="frame-time-row">
        <span class="frame-time-display">Time: <strong>{{ time.toFixed(3) }} s</strong> / {{ duration.toFixed(2) }} s</span>
        <div class="frame-jump-buttons">
          <button type="button" class="quick-jump-btn" :disabled="!duration || busy || capturing" @click="jumpTo(0)">First frame</button>
          <button type="button" class="quick-jump-btn" :disabled="!duration || busy || capturing" @click="jumpTo(0.5)">Mid frame</button>
          <button type="button" class="quick-jump-btn highlight" :disabled="!duration || busy || capturing" @click="jumpTo(1)">⏭ Last frame (End)</button>
        </div>
      </div>
      <input aria-label="Video frame time" type="range" min="0" :max="Math.max(0, Math.floor(duration * 1000) - 1)" step="1"
        :value="Math.round(time * 1000)" :disabled="!duration || busy || capturing" @input="seek" />
    </div>
    <p v-if="error || captureError" role="alert">{{ error || captureError }}</p>
    <footer>
      <button class="secondary-button" :disabled="busy || capturing" @click="emit('close')">Cancel</button>
      <button class="primary-button" :disabled="busy || capturing || seeking || !duration" @click="capture">{{ busy ? 'Saving frame…' : 'Use this frame' }}</button>
    </footer>
  </Dialog>
</template>

<style scoped>
.frame-video { width: 100%; max-height: 55vh; background: #000; border-radius: 10px; }
.frame-scrubber-controls { display: grid; gap: 10px; margin: 16px 0; }
.frame-time-row { display: flex; align-items: center; justify-content: space-between; font-size: 13px; }
.frame-jump-buttons { display: flex; gap: 6px; }
.quick-jump-btn { padding: 4px 10px; border: 1px solid var(--line-default, #383e3a); border-radius: 6px; background: var(--surface-2, #1f2421); color: var(--text-secondary, #ccc); font-size: 11px; cursor: pointer; }
.quick-jump-btn:hover:not(:disabled) { border-color: var(--line-emphasis, #555); color: #fff; background: var(--surface-3, #2a302c); }
.quick-jump-btn.highlight { border-color: #3b82f6; color: #60a5fa; background: rgba(59, 130, 246, 0.1); }
.quick-jump-btn.highlight:hover:not(:disabled) { background: rgba(59, 130, 246, 0.2); }
.frame-scrubber-controls input[type=range] { width: 100%; cursor: pointer; }
footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }
[role=alert] { color: var(--status-danger, #ef8585); }
</style>
