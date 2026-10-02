<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type AspectRatio, type ImageReferenceSource, type ImageReferenceSourceShot, type Shot } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import Dialog from '@/components/ui/Dialog.vue'
import VideoFramePicker from '@/components/VideoFramePicker.vue'

const props = defineProps<{ shot: Shot; aspect: AspectRatio; previousShotId?: string }>()
const store = useStudioStore()
const active = computed(() => props.shot.image_references?.find((item) => item.aspect_ratio === props.aspect))
const history = computed(() => props.shot.takes?.filter((take) => take.media_type === 'image' && take.aspect_ratio === props.aspect && take.settings.image_reference) || [])
const sources = ref<ImageReferenceSourceShot[]>([])
const sourceShotId = ref('')
const sourceShot = computed(() => sources.value.find((item) => item.id === sourceShotId.value))
const showBrowser = ref(false)
const selectedVideo = ref<ImageReferenceSource | null>(null)
const busy = ref(false)
const error = ref('')
const loading = ref(false)
function describe(ref: { source_media_type: string; source_time_ms?: number | null }) {
  return ref.source_media_type === 'clip' ? `Video frame at ${((ref.source_time_ms || 0) / 1000).toFixed(3)} s` : 'Still image'
}
async function browse(previous = false) {
  error.value = ''
  showBrowser.value = true
  loading.value = true
  try {
    sources.value = await api.imageReferenceSources(props.shot.id)
    sourceShotId.value = (previous ? props.previousShotId : active.value?.source_shot_id) || sources.value[sources.value.length - 1]?.id || ''
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
  try { await api.removeImageReference(props.shot.id, props.aspect); await refresh() }
  catch (cause) { error.value = cause instanceof Error ? cause.message : 'Could not remove reference' }
  finally { busy.value = false }
}
</script>

<template>
  <section class="image-reference-panel" aria-label="Image reference">
    <div>
      <strong>Image reference</strong>
      <p>Visual continuity · Keep identity, wardrobe, setting, and lighting while following this shot's framing and action.</p>
    </div>
    <div v-if="active" class="reference-preview">
      <img :src="active.url" alt="Selected image reference" />
      <div><strong>{{ active.source_shot_title }}</strong><p>{{ describe(active) }} · {{ active.source_aspect_ratio }}</p></div>
    </div>
    <p v-else>None — generate using this shot's prompt and selected cast.</p>
    <div class="reference-actions">
      <button v-if="previousShotId" class="secondary-button" :disabled="busy" @click="browse(true)">Previous shot</button>
      <button class="secondary-button" :disabled="busy || !previousShotId" @click="browse()">{{ active ? 'Change' : 'Choose another shot' }}</button>
      <button v-if="active" class="ghost-link" :disabled="busy" @click="remove">Remove</button>
    </div>
    <p v-if="!previousShotId">References become available after the first shot.</p>
    <p v-if="error && !showBrowser && !selectedVideo" role="alert">{{ error }}</p>
    <details v-if="history.length" class="reference-history">
      <summary>References used by image takes ({{ history.length }})</summary>
      <div v-for="take in history" :key="take.id" class="reference-preview">
        <img :src="take.settings.image_reference!.url" alt="Reference used by this image take" />
        <div>
          <a :href="take.url" target="_blank" rel="noopener">{{ take.selected ? 'Selected take' : 'Image take' }} · {{ new Date(take.created_at).toLocaleString() }}</a>
          <p>{{ take.settings.image_reference!.source_shot_title }} · {{ describe(take.settings.image_reference!) }} · Visual continuity</p>
        </div>
      </div>
    </details>
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
.image-reference-panel { display: grid; gap: 12px; padding: 16px; border: 1px solid var(--line-default); border-radius: 12px; }
p { margin: 4px 0; color: var(--text-secondary); font-size: 12px; line-height: 1.6; }
.reference-preview { display: flex; align-items: center; gap: 12px; padding: 8px 0; }
.reference-preview img { width: 88px; height: 66px; object-fit: contain; border-radius: 7px; background: #111; }
.reference-preview strong, .reference-preview a { font-size: 13px; }
.reference-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.source-select { display: grid; gap: 8px; margin-bottom: 16px; }
.source-select select { padding: 10px; border: 1px solid var(--line-default); border-radius: 8px; background: var(--surface-2); }
.source-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
.source-card { padding: 8px; border: 1px solid var(--line-default); border-radius: 10px; text-align: left; }
.source-card img, .source-card video { width: 100%; height: 140px; object-fit: contain; background: #111; }
.source-card span { display: block; font-size: 12px; margin-top: 8px; }
.reference-history { font-size: 12px; }
[role=alert] { color: var(--status-danger, #ef8585); }
</style>
