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
    <label class="frame-time">Selected time: {{ time.toFixed(3) }} s
      <input aria-label="Video frame time" type="range" min="0" :max="Math.max(0, Math.floor(duration * 1000) - 1)" step="1"
        :value="Math.round(time * 1000)" :disabled="!duration || busy || capturing" @input="seek" />
    </label>
    <p v-if="error || captureError" role="alert">{{ error || captureError }}</p>
    <footer>
      <button class="secondary-button" :disabled="busy || capturing" @click="emit('close')">Cancel</button>
      <button class="primary-button" :disabled="busy || capturing || seeking || !duration" @click="capture">{{ busy ? 'Saving frame…' : 'Use this frame' }}</button>
    </footer>
  </Dialog>
</template>

<style scoped>
.frame-video { width: 100%; max-height: 55vh; background: #000; border-radius: 10px; }
.frame-time { display: grid; gap: 8px; margin: 16px 0; font-size: 13px; }
.frame-time input { width: 100%; }
footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }
[role=alert] { color: var(--status-danger, #ef8585); }
</style>
