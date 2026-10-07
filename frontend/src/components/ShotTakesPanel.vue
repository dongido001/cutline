<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import type { AspectRatio, Shot, ShotTake } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import {
  Check,
  Clapperboard,
  Clock,
  ExternalLink,
  Film,
  Image as ImageIcon,
  ImagePlus,
  MessageSquare,
  Pencil,
  RefreshCw,
  Sparkles,
  Upload,
  Video,
  X,
} from '@lucide/vue'

const props = defineProps<{
  shot: Shot
  aspect: AspectRatio
}>()

const emit = defineEmits<{
  (e: 'play-take', take: ShotTake): void
}>()

interface PendingAttachment {
  id: string
  file: File
  previewUrl: string
}

const store = useStudioStore()
const activeTab = ref<'clip' | 'image'>('clip')
const selectingTakeId = ref<string | null>(null)
const previewHoverTakeId = ref<string | null>(null)
const brokenTakeIds = ref<Set<string>>(new Set())

// Follow-up edit state
const targetEditTakeId = ref<string | null>(null)
const editPrompt = ref('')
const editAttachments = ref<PendingAttachment[]>([])
const editError = ref('')
const submittingEdit = ref(false)
const uploadingTake = ref(false)
const editTextareaRef = ref<HTMLTextAreaElement | null>(null)
const uploadTakeInputRef = ref<HTMLInputElement | null>(null)
const attachInputRef = ref<HTMLInputElement | null>(null)

function clearAttachments() {
  for (const item of editAttachments.value) {
    URL.revokeObjectURL(item.previewUrl)
  }
  editAttachments.value = []
}

onBeforeUnmount(() => {
  clearAttachments()
})

function markBrokenTake(takeId: string) {
  const next = new Set(brokenTakeIds.value)
  next.add(takeId)
  brokenTakeIds.value = next
}

// Sort takes chronologically ascending to assign stable "Take 1, Take 2..." numbers
const matchingTakesAscending = computed(() => {
  const takes = props.shot.takes?.filter((t) => t.aspect_ratio === props.aspect) || []
  return [...takes].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
  )
})

const videoTakes = computed(() => {
  const list = matchingTakesAscending.value.filter((t) => t.media_type === 'clip')
  // Return descending for display (newest first)
  return [...list].reverse()
})

const imageTakes = computed(() => {
  const list = matchingTakesAscending.value.filter((t) => t.media_type === 'image')
  // Return descending for display (newest first)
  return [...list].reverse()
})

const targetEditTake = computed<ShotTake | null>(() => {
  if (!imageTakes.value.length) return null
  if (targetEditTakeId.value) {
    const found = imageTakes.value.find((t) => t.id === targetEditTakeId.value)
    if (found) return found
  }
  return imageTakes.value.find((t) => t.selected) || imageTakes.value[0] || null
})

const isProcessingImage = computed(() => store.isProcessingShot(props.shot.id))
const activeImageJob = computed(() => store.imageJobs[props.shot.id] || null)

// Auto-select tab based on available takes
watch(
  () => [props.shot.id, props.aspect],
  () => {
    brokenTakeIds.value = new Set()
    targetEditTakeId.value = null
    editPrompt.value = ''
    editError.value = ''
    clearAttachments()
    if (videoTakes.value.length > 0) {
      activeTab.value = 'clip'
    } else if (imageTakes.value.length > 0) {
      activeTab.value = 'image'
    } else {
      activeTab.value = 'image'
    }
  },
  { immediate: true }
)

// Keep targetEditTakeId synced when a new selected image take arrives
watch(
  () => imageTakes.value.map((t) => `${t.id}:${t.selected}`).join(','),
  () => {
    const selected = imageTakes.value.find((t) => t.selected)
    if (selected) {
      targetEditTakeId.value = selected.id
    } else if (!targetEditTakeId.value && imageTakes.value[0]) {
      targetEditTakeId.value = imageTakes.value[0].id
    }
  },
  { immediate: true }
)

const activeTakesList = computed(() => {
  return activeTab.value === 'clip' ? videoTakes.value : imageTakes.value
})

function getTakeNumber(take: ShotTake): number {
  const sameMediaAsc = matchingTakesAscending.value.filter((t) => t.media_type === take.media_type)
  const idx = sameMediaAsc.findIndex((t) => t.id === take.id)
  return idx !== -1 ? idx + 1 : 1
}

function getTakeNumberById(takeId: string | undefined | null): number | null {
  if (!takeId) return null
  const found = matchingTakesAscending.value.find((t) => t.id === takeId)
  return found ? getTakeNumber(found) : null
}

function getTakeEditPrompt(take: ShotTake): string | null {
  const settings = take.settings as Record<string, unknown> | undefined
  if (settings && typeof settings.edit_prompt === 'string' && settings.edit_prompt.trim()) {
    return settings.edit_prompt.trim()
  }
  return null
}

function getTakeParentNumber(take: ShotTake): number | null {
  const settings = take.settings as Record<string, unknown> | undefined
  if (settings && typeof settings.parent_take_id === 'string') {
    return getTakeNumberById(settings.parent_take_id)
  }
  return null
}

function formatTakeTime(isoDate: string): string {
  try {
    const d = new Date(isoDate)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}

function getTakeChatUrl(take: ShotTake): string | null {
  if (take.conversation_url) return take.conversation_url
  const settings = take.settings as Record<string, unknown> | undefined
  if (settings && typeof settings.conversation_url === 'string') {
    return settings.conversation_url
  }
  return null
}

function providerLabel(provider: string): string {
  const map: Record<string, string> = {
    flowkit: 'Google Flow',
    google_flow: 'Google Flow',
    chatgpt_web: 'ChatGPT Web',
    gemini_web: 'Gemini Web',
    comfyui: 'ComfyUI',
    uploaded: 'Uploaded',
    'manual-upload': 'Uploaded',
  }
  return map[provider] || provider || 'AI Generated'
}

function handleThumbMouseEnter(event: MouseEvent, takeId: string) {
  previewHoverTakeId.value = takeId
  const video = event.currentTarget as HTMLVideoElement | null
  if (video) {
    video.currentTime = 0
    void video.play().catch(() => undefined)
  }
}

function handleThumbMouseLeave(event: MouseEvent) {
  previewHoverTakeId.value = null
  const video = event.currentTarget as HTMLVideoElement | null
  if (video) {
    video.pause()
    video.currentTime = 0
  }
}

function applyOptimisticTakeSelection(take: ShotTake) {
  if (props.shot.takes) {
    for (const t of props.shot.takes) {
      if (t.media_type === take.media_type && t.aspect_ratio === take.aspect_ratio) {
        t.selected = t.id === take.id
      }
    }
  }
  const normalizedSource: 'generated' | 'uploaded' = take.source === 'uploaded' ? 'uploaded' : 'generated'
  const isLegacyAspect = store.currentProject?.aspect_ratio === take.aspect_ratio
  if (take.media_type === 'clip') {
    if (isLegacyAspect) {
      props.shot.selected_clip_take_id = take.id
      if (props.shot.clip) {
        props.shot.clip.url = take.url
        props.shot.clip.mime_type = take.mime_type
        props.shot.clip.source = normalizedSource
        props.shot.clip.provider = take.provider
        props.shot.clip.prompt = take.prompt
        props.shot.clip.created_at = take.created_at
      } else {
        props.shot.clip = {
          id: take.id,
          url: take.url,
          mime_type: take.mime_type,
          source: normalizedSource,
          provider: take.provider,
          prompt: take.prompt,
          created_at: take.created_at,
        }
      }
    }
  } else if (take.media_type === 'image') {
    if (isLegacyAspect) {
      props.shot.selected_image_take_id = take.id
      if (props.shot.image) {
        props.shot.image.url = take.url
        props.shot.image.mime_type = take.mime_type
        props.shot.image.source = normalizedSource
        props.shot.image.provider = take.provider
        props.shot.image.prompt = take.prompt
        props.shot.image.created_at = take.created_at
      } else {
        props.shot.image = {
          id: take.id,
          url: take.url,
          mime_type: take.mime_type,
          source: normalizedSource,
          provider: take.provider,
          prompt: take.prompt,
          created_at: take.created_at,
        }
      }
    }
  }
  if (take.media_type === 'clip' || take.media_type === 'image') {
    const existingVariant = props.shot.media_variants?.find(
      (item) => item.media_type === take.media_type && item.aspect_ratio === take.aspect_ratio
    )
    if (existingVariant) {
      existingVariant.url = take.url
      existingVariant.mime_type = take.mime_type
      existingVariant.source = normalizedSource
      existingVariant.provider = take.provider
      existingVariant.prompt = take.prompt
      existingVariant.created_at = take.created_at
    } else if (props.shot.media_variants) {
      props.shot.media_variants.push({
        id: take.id,
        media_type: take.media_type,
        aspect_ratio: take.aspect_ratio as AspectRatio,
        url: take.url,
        mime_type: take.mime_type,
        source: normalizedSource,
        provider: take.provider,
        prompt: take.prompt,
        created_at: take.created_at,
      })
    }
  }
}

async function handleSelectTake(take: ShotTake) {
  if (selectingTakeId.value) return
  if (take.media_type === 'image') {
    targetEditTakeId.value = take.id
  }
  if (take.selected) {
    if (take.media_type === 'clip') {
      emit('play-take', take)
    }
    return
  }
  selectingTakeId.value = take.id
  try {
    applyOptimisticTakeSelection(take)
    if (take.media_type === 'clip') {
      emit('play-take', take)
    }
    await store.selectShotTake(props.shot.id, take.id)
    if (take.media_type === 'clip') {
      emit('play-take', take)
    }
  } catch (err) {
    console.error('Failed to select take:', err)
  } finally {
    selectingTakeId.value = null
  }
}

function handleStartEditTake(take: ShotTake) {
  activeTab.value = 'image'
  targetEditTakeId.value = take.id
  editError.value = ''
  void nextTick(() => {
    editTextareaRef.value?.focus()
  })
}

function triggerUploadTakePicker() {
  uploadTakeInputRef.value?.click()
}

async function handleUploadImageTake(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  uploadingTake.value = true
  editError.value = ''
  try {
    activeTab.value = 'image'
    await store.uploadShotImage(props.shot.id, file, props.aspect)
  } catch (err) {
    editError.value = err instanceof Error ? err.message : 'Failed to upload image take'
  } finally {
    uploadingTake.value = false
  }
}

function triggerAttachPicker() {
  attachInputRef.value?.click()
}

function addFilesToAttachments(files: FileList | File[]) {
  const allowed = new Set(['image/jpeg', 'image/png', 'image/webp'])
  for (const file of Array.from(files)) {
    if (!allowed.has(file.type)) {
      editError.value = 'Only JPG, PNG, or WebP images can be attached'
      continue
    }
    if (editAttachments.value.length >= 4) {
      editError.value = 'Up to 4 reference images can be attached per edit'
      break
    }
    editAttachments.value.push({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      file,
      previewUrl: URL.createObjectURL(file),
    })
  }
}

function handleAttachFiles(event: Event) {
  const input = event.target as HTMLInputElement
  if (input.files?.length) {
    editError.value = ''
    addFilesToAttachments(input.files)
  }
  input.value = ''
}

function handleComposerPaste(event: ClipboardEvent) {
  const items = event.clipboardData?.items
  if (!items) return
  const files: File[] = []
  for (const item of Array.from(items)) {
    if (item.kind === 'file' && item.type.startsWith('image/')) {
      const f = item.getAsFile()
      if (f) files.push(f)
    }
  }
  if (files.length > 0) {
    editError.value = ''
    addFilesToAttachments(files)
  }
}

function removeAttachment(id: string) {
  const idx = editAttachments.value.findIndex((item) => item.id === id)
  if (idx !== -1) {
    const [removed] = editAttachments.value.splice(idx, 1)
    if (removed) URL.revokeObjectURL(removed.previewUrl)
  }
}

async function handleSubmitFollowupEdit() {
  const take = targetEditTake.value
  const promptText = editPrompt.value.trim()
  if (!take || !promptText || submittingEdit.value || isProcessingImage.value) return
  submittingEdit.value = true
  editError.value = ''
  const filesToSend = editAttachments.value.map((item) => item.file)
  try {
    editPrompt.value = ''
    clearAttachments()
    await store.editShotImageTake(props.shot.id, take.id, promptText, filesToSend)
  } catch (err) {
    editError.value = err instanceof Error ? err.message : 'Follow-up image edit failed'
    if (!editPrompt.value) {
      editPrompt.value = promptText
    }
  } finally {
    submittingEdit.value = false
  }
}

function handleComposerKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
    event.preventDefault()
    void handleSubmitFollowupEdit()
  }
}
</script>

<template>
  <section v-if="videoTakes.length || imageTakes.length" class="shot-takes-panel" aria-label="Shot Takes History">
    <input
      ref="uploadTakeInputRef"
      type="file"
      accept="image/jpeg,image/png,image/webp"
      hidden
      @change="handleUploadImageTake"
    />
    <input
      ref="attachInputRef"
      type="file"
      accept="image/jpeg,image/png,image/webp"
      multiple
      hidden
      @change="handleAttachFiles"
    />

    <header class="takes-panel-header">
      <div class="takes-heading">
        <Clapperboard :size="16" class="takes-icon" />
        <strong>Takes & History</strong>
        <span class="aspect-badge">{{ aspect }}</span>
      </div>

      <div class="takes-header-controls">
        <button
          type="button"
          class="upload-take-btn"
          :disabled="uploadingTake || isProcessingImage"
          title="Upload an image from disk as a new Image Take"
          @click="triggerUploadTakePicker"
        >
          <Upload :size="13" />
          <span>{{ uploadingTake ? 'Uploading…' : 'Upload image take' }}</span>
        </button>

        <div class="takes-tab-switch" role="tablist">
          <button
            type="button"
            class="takes-tab-btn"
            :class="{ active: activeTab === 'clip' }"
            role="tab"
            :aria-selected="activeTab === 'clip'"
            @click="activeTab = 'clip'"
          >
            <Video :size="13" />
            <span>Video takes</span>
            <span class="count-pill">{{ videoTakes.length }}</span>
          </button>
          <button
            type="button"
            class="takes-tab-btn"
            :class="{ active: activeTab === 'image' }"
            role="tab"
            :aria-selected="activeTab === 'image'"
            @click="activeTab = 'image'"
          >
            <ImageIcon :size="13" />
            <span>Image takes</span>
            <span class="count-pill">{{ imageTakes.length }}</span>
          </button>
        </div>
      </div>
    </header>

    <div v-if="activeTakesList.length" class="takes-grid">
      <article
        v-for="take in activeTakesList"
        :key="take.id"
        class="take-card"
        :class="{
          selected: take.selected,
          'edit-target': activeTab === 'image' && targetEditTake?.id === take.id,
          processing: selectingTakeId === take.id,
        }"
        @click="handleSelectTake(take)"
      >
        <div class="take-thumb-wrapper">
          <video
            v-if="take.media_type === 'clip'"
            :src="take.url"
            muted
            loop
            playsinline
            preload="metadata"
            class="take-media"
            @mouseenter="handleThumbMouseEnter($event, take.id)"
            @mouseleave="handleThumbMouseLeave($event)"
          ></video>
          <img
            v-else
            :src="take.url"
            alt="Take thumbnail"
            class="take-media"
            @error="markBrokenTake(take.id)"
          />
          <div v-if="brokenTakeIds.has(take.id)" class="take-missing-thumb">Preview unavailable</div>

          <span v-if="take.selected" class="take-active-badge">
            <Check :size="11" /> Active
          </span>
          <span v-else class="take-num-badge">
            Take {{ getTakeNumber(take) }}
          </span>

          <span
            v-if="take.media_type === 'image' && getTakeParentNumber(take)"
            class="take-lineage-badge"
            :title="getTakeEditPrompt(take) || 'Follow-up edit'"
          >
            Edited from T{{ getTakeParentNumber(take) }}
          </span>

          <div v-if="selectingTakeId === take.id" class="take-loading-overlay">
            <RefreshCw class="spinning" :size="20" />
            <span>Switching…</span>
          </div>
        </div>

        <div class="take-card-details">
          <div class="take-details-top">
            <strong class="take-title">Take {{ getTakeNumber(take) }}</strong>
            <span class="take-time"><Clock :size="11" /> {{ formatTakeTime(take.created_at) }}</span>
          </div>

          <p
            v-if="take.media_type === 'image' && getTakeEditPrompt(take)"
            class="take-edit-note"
            :title="getTakeEditPrompt(take)!"
          >
            “{{ getTakeEditPrompt(take) }}”
          </p>

          <div class="take-meta-row">
            <span class="take-provider-tag">{{ providerLabel(take.provider) }}</span>
            <a
              v-if="getTakeChatUrl(take)"
              :href="getTakeChatUrl(take)!"
              target="_blank"
              rel="noopener noreferrer"
              class="take-chat-link"
              title="Open AI chat session for this take"
              @click.stop
            >
              <MessageSquare :size="11" />
              <span>Chat</span>
              <ExternalLink :size="9" />
            </a>
          </div>

          <div class="take-card-actions">
            <button
              type="button"
              class="take-select-btn"
              :class="{ active: take.selected }"
              :disabled="take.selected || selectingTakeId === take.id"
              @click.stop="handleSelectTake(take)"
            >
              <Check v-if="take.selected" :size="13" />
              <span>{{ take.selected ? 'Current' : 'Select' }}</span>
            </button>

            <button
              v-if="take.media_type === 'image'"
              type="button"
              class="take-reply-btn"
              :class="{ active: targetEditTake?.id === take.id }"
              title="Reply with follow-up edit changes for this image take"
              @click.stop="handleStartEditTake(take)"
            >
              <Pencil :size="12" />
              <span>Edit</span>
            </button>
          </div>
        </div>
      </article>
    </div>

    <div v-else class="empty-takes-state">
      <Film :size="22" />
      <span>No {{ activeTab === 'clip' ? 'video' : 'image' }} takes yet for {{ aspect }}.</span>
      <button
        v-if="activeTab === 'image'"
        type="button"
        class="upload-take-btn"
        :disabled="uploadingTake || isProcessingImage"
        @click="triggerUploadTakePicker"
      >
        <Upload :size="13" />
        <span>Upload image take</span>
      </button>
    </div>

    <!-- Follow-up Edit / Chat Reply Composer for Image Takes -->
    <div v-if="activeTab === 'image' && targetEditTake" class="take-followup-composer">
      <div class="composer-header">
        <div class="composer-target-info">
          <img
            :src="targetEditTake.url"
            alt="Target take"
            class="composer-target-thumb"
          />
          <div class="composer-target-text">
            <div class="composer-target-title">
              <Sparkles :size="13" class="sparkles-icon" />
              <strong>Follow-up edit on Take {{ getTakeNumber(targetEditTake) }}</strong>
              <span
                v-if="getTakeChatUrl(targetEditTake)"
                class="composer-mode-badge chat-mode"
                title="Replies directly inside the ChatGPT conversation that created this take"
              >
                <MessageSquare :size="11" />
                Replies in ChatGPT chat
              </span>
              <span
                v-else
                class="composer-mode-badge frame-mode"
                title="Sends this take's image frame to the AI provider to apply your changes"
              >
                Edits Take {{ getTakeNumber(targetEditTake) }} frame
              </span>
            </div>
            <span class="composer-subtitle">
              Describe what to change, fix, or add. You can also attach or paste reference images.
            </span>
          </div>
        </div>

        <button
          type="button"
          class="attach-ref-btn"
          :disabled="submittingEdit || isProcessingImage || editAttachments.length >= 4"
          title="Attach reference image(s) to include with your edit reply"
          @click="triggerAttachPicker"
        >
          <ImagePlus :size="14" />
          <span>Attach image</span>
        </button>
      </div>

      <textarea
        ref="editTextareaRef"
        v-model="editPrompt"
        rows="2"
        class="composer-textarea"
        :disabled="submittingEdit || isProcessingImage"
        :placeholder="`Reply with changes for Take ${getTakeNumber(targetEditTake)} (e.g. 'change her dress to emerald green', 'remove the extra cup on the table')…`"
        @keydown="handleComposerKeydown"
        @paste="handleComposerPaste"
      ></textarea>

      <div v-if="editAttachments.length" class="composer-attachments">
        <div
          v-for="item in editAttachments"
          :key="item.id"
          class="attachment-chip"
        >
          <img :src="item.previewUrl" :alt="item.file.name" class="attachment-thumb" />
          <span class="attachment-name">{{ item.file.name }}</span>
          <button
            type="button"
            class="attachment-remove"
            title="Remove attached image"
            @click="removeAttachment(item.id)"
          >
            <X :size="12" />
          </button>
        </div>
      </div>

      <p v-if="editError" class="composer-error">{{ editError }}</p>

      <div class="composer-footer">
        <div class="composer-status">
          <template v-if="isProcessingImage">
            <RefreshCw :size="13" class="spinning status-spinner" />
            <span>{{ activeImageJob?.message || 'Applying follow-up image edit…' }}</span>
          </template>
          <span v-else class="composer-hint">Press Cmd/Ctrl + Enter to send · Creates a new Image Take</span>
        </div>

        <button
          type="button"
          class="send-edit-btn"
          :disabled="!editPrompt.trim() || submittingEdit || isProcessingImage"
          @click="handleSubmitFollowupEdit"
        >
          <RefreshCw v-if="submittingEdit || isProcessingImage" :size="13" class="spinning" />
          <Sparkles v-else :size="13" />
          <span>{{ submittingEdit || isProcessingImage ? 'Editing take…' : 'Send Edit' }}</span>
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.shot-takes-panel {
  margin: 16px 0;
  padding: 16px;
  background: var(--surface-1, #131514);
  border: 1px solid var(--line-default, #2a2e2b);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.takes-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line-soft, #212522);
}

.takes-heading {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  color: var(--text-primary, #e2e8f0);
}

.takes-icon {
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

.takes-header-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.upload-take-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 11px;
  border-radius: 7px;
  border: 1px solid var(--line-default, #2a2e2b);
  background: var(--surface-2, #181a19);
  color: var(--text-primary, #e2e8f0);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.upload-take-btn:hover:not(:disabled) {
  border-color: rgba(56, 189, 248, 0.45);
  color: #38bdf8;
  background: rgba(56, 189, 248, 0.08);
}

.upload-take-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.takes-tab-switch {
  display: flex;
  background: rgba(0, 0, 0, 0.3);
  padding: 3px;
  border-radius: 7px;
  border: 1px solid var(--line-soft, #212522);
  gap: 4px;
}

.takes-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  border-radius: 5px;
  border: none;
  background: transparent;
  color: var(--text-secondary, #94a3b8);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.takes-tab-btn:hover {
  color: var(--text-primary, #e2e8f0);
}

.takes-tab-btn.active {
  background: var(--surface-2, #1f2320);
  color: #38bdf8;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
}

.count-pill {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.1);
  color: inherit;
}

.takes-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(185px, 1fr));
  gap: 12px;
}

.take-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line-default, #2a2e2b);
  border-radius: 10px;
  background: var(--surface-2, #181a19);
  overflow: hidden;
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
}

.take-card:hover {
  border-color: rgba(56, 189, 248, 0.5);
  transform: translateY(-2px);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}

.take-card.selected {
  border-color: #10b981;
  background: rgba(16, 185, 129, 0.04);
  box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.3);
}

.take-card.edit-target:not(.selected) {
  border-color: rgba(56, 189, 248, 0.65);
  box-shadow: 0 0 0 1px rgba(56, 189, 248, 0.25);
}

.take-thumb-wrapper {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #111;
  overflow: hidden;
}

.take-media {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  display: block;
  object-fit: contain;
  background: #111;
  transition: transform 0.2s ease;
}

.take-missing-thumb {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: 11px;
  color: #94a3b8;
  background: #181a19;
}

.take-card:hover .take-media {
  transform: scale(1.03);
}

.take-active-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 7px;
  border-radius: 4px;
  background: rgba(16, 185, 129, 0.9);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  backdrop-filter: blur(4px);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
}

.take-num-badge {
  position: absolute;
  top: 6px;
  left: 6px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.7);
  color: #e2e8f0;
  font-size: 10px;
  font-weight: 600;
  backdrop-filter: blur(4px);
}

.take-lineage-badge {
  position: absolute;
  bottom: 6px;
  left: 6px;
  padding: 2px 6px;
  border-radius: 4px;
  background: rgba(14, 165, 233, 0.85);
  color: #fff;
  font-size: 9px;
  font-weight: 700;
  backdrop-filter: blur(4px);
}

.take-loading-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  font-size: 11px;
  color: #38bdf8;
  backdrop-filter: blur(2px);
}

.take-card-details {
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 7px;
  flex: 1;
}

.take-details-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.take-title {
  font-size: 12px;
  color: var(--text-primary, #e2e8f0);
}

.take-time {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  color: var(--text-secondary, #94a3b8);
}

.take-edit-note {
  margin: 0;
  font-size: 10px;
  color: #7dd3fc;
  line-height: 1.3;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.take-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  margin-top: auto;
}

.take-provider-tag {
  font-size: 10px;
  color: #94a3b8;
  background: rgba(255, 255, 255, 0.05);
  padding: 2px 6px;
  border-radius: 4px;
}

.take-chat-link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  color: #38bdf8;
  text-decoration: none;
  padding: 2px 5px;
  border-radius: 4px;
  background: rgba(56, 189, 248, 0.08);
}

.take-chat-link:hover {
  background: rgba(56, 189, 248, 0.18);
  text-decoration: underline;
}

.take-card-actions {
  display: flex;
  gap: 6px;
}

.take-select-btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 6px;
  border-radius: 6px;
  border: 1px solid var(--line-default, #2a2e2b);
  background: var(--surface-1, #131514);
  color: var(--text-primary, #e2e8f0);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.take-select-btn:hover:not(:disabled) {
  background: rgba(56, 189, 248, 0.15);
  border-color: rgba(56, 189, 248, 0.4);
  color: #38bdf8;
}

.take-select-btn.active {
  background: rgba(16, 185, 129, 0.15);
  border-color: rgba(16, 185, 129, 0.4);
  color: #10b981;
  cursor: default;
}

.take-reply-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 6px 8px;
  border-radius: 6px;
  border: 1px solid var(--line-default, #2a2e2b);
  background: var(--surface-1, #131514);
  color: var(--text-secondary, #94a3b8);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.take-reply-btn:hover,
.take-reply-btn.active {
  background: rgba(56, 189, 248, 0.14);
  border-color: rgba(56, 189, 248, 0.45);
  color: #38bdf8;
}

.empty-takes-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 24px;
  color: var(--text-secondary, #94a3b8);
  font-size: 12px;
}

/* Follow-up Edit Composer */
.take-followup-composer {
  margin-top: 2px;
  padding: 12px;
  border-radius: 10px;
  background: rgba(15, 23, 42, 0.45);
  border: 1px solid rgba(56, 189, 248, 0.25);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.composer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.composer-target-info {
  display: flex;
  align-items: center;
  gap: 10px;
}

.composer-target-thumb {
  width: 48px;
  height: 32px;
  object-fit: cover;
  border-radius: 5px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: #0f1115;
}

.composer-target-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.composer-target-title {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--text-primary, #e2e8f0);
}

.sparkles-icon {
  color: #38bdf8;
}

.composer-mode-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 999px;
}

.composer-mode-badge.chat-mode {
  background: rgba(16, 185, 129, 0.14);
  color: #34d399;
  border: 1px solid rgba(16, 185, 129, 0.3);
}

.composer-mode-badge.frame-mode {
  background: rgba(56, 189, 248, 0.12);
  color: #7dd3fc;
  border: 1px solid rgba(56, 189, 248, 0.28);
}

.composer-subtitle {
  font-size: 11px;
  color: var(--text-secondary, #94a3b8);
}

.attach-ref-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 7px;
  border: 1px solid rgba(56, 189, 248, 0.35);
  background: rgba(56, 189, 248, 0.08);
  color: #7dd3fc;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.attach-ref-btn:hover:not(:disabled) {
  background: rgba(56, 189, 248, 0.18);
  border-color: rgba(56, 189, 248, 0.6);
  color: #e0f2fe;
}

.attach-ref-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.composer-textarea {
  width: 100%;
  box-sizing: border-box;
  border-radius: 8px;
  border: 1px solid var(--line-default, #2a2e2b);
  background: var(--surface-1, #111312);
  color: var(--text-primary, #e2e8f0);
  padding: 9px 11px;
  font-size: 12px;
  line-height: 1.45;
  resize: vertical;
  min-height: 54px;
}

.composer-textarea:focus {
  outline: none;
  border-color: #38bdf8;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.15);
}

.composer-attachments {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.attachment-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px 4px 4px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.12);
  font-size: 11px;
  color: var(--text-primary, #e2e8f0);
}

.attachment-thumb {
  width: 26px;
  height: 26px;
  object-fit: cover;
  border-radius: 4px;
}

.attachment-name {
  max-width: 130px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
}

.attachment-remove:hover {
  color: #f87171;
  background: rgba(248, 113, 113, 0.12);
}

.composer-error {
  margin: 0;
  font-size: 11px;
  color: #f87171;
}

.composer-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.composer-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: #38bdf8;
}

.composer-hint {
  color: var(--text-secondary, #94a3b8);
}

.send-edit-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 7px;
  border: 1px solid rgba(56, 189, 248, 0.5);
  background: linear-gradient(135deg, #0284c7, #0ea5e9);
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.send-edit-btn:hover:not(:disabled) {
  filter: brightness(1.08);
  transform: translateY(-1px);
}

.send-edit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
