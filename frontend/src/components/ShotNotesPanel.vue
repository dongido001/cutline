<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  Check,
  CircleAlert,
  File,
  Image as ImageIcon,
  MessageSquareText,
  Paperclip,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  X,
} from '@lucide/vue'
import { api, type Shot, type ShotNote } from '@/lib/api'

const props = withDefaults(
  defineProps<{
    shot: Shot
    inDrawer?: boolean
    hideHeader?: boolean
  }>(),
  {
    inDrawer: false,
    hideHeader: false,
  },
)
const emit = defineEmits<{ changed: [notes: ShotNote[]] }>()

const notes = ref<ShotNote[]>([])
const body = ref('')
const files = ref<File[]>([])
const editingId = ref<string | null>(null)
const editingBody = ref('')
const busy = ref(false)
const error = ref('')
const filter = ref<'all' | 'unresolved' | 'resolved'>('all')
const previewModalUrl = ref<string | null>(null)
const isDraggingOver = ref(false)

const unresolvedCount = computed(() => notes.value.filter((note) => !note.resolved).length)
const resolvedCount = computed(() => notes.value.filter((note) => note.resolved).length)

const displayedNotes = computed(() => {
  if (filter.value === 'unresolved') return notes.value.filter((note) => !note.resolved)
  if (filter.value === 'resolved') return notes.value.filter((note) => note.resolved)
  return [...notes.value].sort((a, b) => {
    if (a.resolved === b.resolved) {
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    }
    return a.resolved ? 1 : -1
  })
})

watch(() => props.shot.id, load, { immediate: true })

async function load() {
  error.value = ''
  try {
    notes.value = await api.shotNotes(props.shot.id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load notes.'
  }
}

function chooseFiles(event: Event) {
  const chosen = Array.from((event.target as HTMLInputElement).files || [])
  files.value.push(...chosen)
}

function handlePaste(event: ClipboardEvent) {
  const items = event.clipboardData?.items
  if (!items) return
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      const file = item.getAsFile()
      if (file) {
        const ext = item.type.split('/')[1] || 'png'
        const screenshotFile = new (window as any).File([file], `screenshot-${Date.now()}.${ext}`, {
          type: file.type,
        }) as File
        files.value.push(screenshotFile)
      }
    }
  }
}

function handleDrop(event: DragEvent) {
  isDraggingOver.value = false
  const dropped = Array.from(event.dataTransfer?.files || [])
  if (dropped.length) {
    files.value.push(...dropped)
  }
}

function removeStagedFile(index: number) {
  files.value.splice(index, 1)
}

function publish() {
  emit('changed', notes.value)
}

async function addNote() {
  if (!body.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const note = await api.createShotNote(props.shot.id, body.value.trim(), files.value)
    notes.value.unshift(note)
    body.value = ''
    files.value = []
    publish()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not add note.'
  } finally {
    busy.value = false
  }
}

async function toggleResolved(note: ShotNote) {
  try {
    const updated = await api.updateShotNote(note.id, { resolved: !note.resolved })
    notes.value = notes.value.map((item) => (item.id === note.id ? updated : item))
    publish()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not update note.'
  }
}

async function saveEdit(note: ShotNote) {
  if (!editingBody.value.trim()) return
  try {
    const updated = await api.updateShotNote(note.id, { body: editingBody.value.trim() })
    notes.value = notes.value.map((item) => (item.id === note.id ? updated : item))
    editingId.value = null
    publish()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not save note.'
  }
}

async function removeNote(note: ShotNote) {
  if (!window.confirm('Delete this note and its attachments?')) return
  try {
    await api.deleteShotNote(note.id)
    notes.value = notes.value.filter((item) => item.id !== note.id)
    publish()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not delete note.'
  }
}

async function addAttachments(note: ShotNote, event: Event) {
  const selected = Array.from((event.target as HTMLInputElement).files || [])
  if (!selected.length) return
  try {
    const updated = await api.addShotNoteAttachments(note.id, selected)
    notes.value = notes.value.map((item) => (item.id === note.id ? updated : item))
    publish()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not upload attachment.'
  }
}

async function removeAttachment(note: ShotNote, attachmentId: string) {
  try {
    await api.deleteShotNoteAttachment(attachmentId)
    note.attachments = note.attachments.filter((item) => item.id !== attachmentId)
    publish()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not remove attachment.'
  }
}

function fileSize(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <section class="shot-notes-panel" :class="{ 'in-drawer': inDrawer }">
    <header v-if="!hideHeader" class="notes-header">
      <div class="notes-header-title">
        <MessageSquareText :size="18" />
        <div>
          <strong>Shot notes</strong>
          <span>Reminders, fixes, and review files for this shot.</span>
        </div>
      </div>
      <div class="notes-header-badges">
        <span v-if="unresolvedCount" class="note-count">{{ unresolvedCount }} unresolved</span>
        <span v-else-if="notes.length" class="note-clear"><Check :size="13" /> All resolved</span>
      </div>
    </header>

    <div v-if="notes.length > 0" class="notes-filter-tabs">
      <button
        type="button"
        class="filter-tab"
        :class="{ active: filter === 'all' }"
        @click="filter = 'all'"
      >
        All ({{ notes.length }})
      </button>
      <button
        type="button"
        class="filter-tab"
        :class="{ active: filter === 'unresolved' }"
        @click="filter = 'unresolved'"
      >
        Unresolved ({{ unresolvedCount }})
      </button>
      <button
        type="button"
        class="filter-tab"
        :class="{ active: filter === 'resolved' }"
        @click="filter = 'resolved'"
      >
        Resolved ({{ resolvedCount }})
      </button>
    </div>

    <form
      class="note-composer"
      :class="{ 'is-dragging': isDraggingOver }"
      @submit.prevent="addNote"
      @dragover.prevent="isDraggingOver = true"
      @dragleave.prevent="isDraggingOver = false"
      @drop.prevent="handleDrop"
    >
      <textarea
        v-model="body"
        rows="3"
        maxlength="10000"
        placeholder="Add a reminder or fix for this shot… (paste screenshots with ⌘V)"
        aria-label="New shot note"
        @paste="handlePaste"
      ></textarea>

      <!-- Staged attachments preview -->
      <div v-if="files.length" class="staged-attachments-list">
        <div v-for="(file, idx) in files" :key="`${file.name}-${idx}`" class="staged-file-pill">
          <ImageIcon v-if="file.type.startsWith('image/')" :size="12" />
          <File v-else :size="12" />
          <span class="staged-file-name">{{ file.name }}</span>
          <small>{{ fileSize(file.size) }}</small>
          <button type="button" aria-label="Remove file" @click="removeStagedFile(idx)">
            <X :size="11" />
          </button>
        </div>
      </div>

      <div class="note-composer-actions">
        <label class="note-file-button" title="Attach screenshots or review documents">
          <Paperclip :size="14" />
          <span>Attach files / screenshot</span>
          <input type="file" multiple @change="chooseFiles" />
        </label>
        <span class="paste-hint">Tip: Paste screenshot with ⌘V</span>
        <button class="primary-button" :disabled="!body.trim() || busy">
          <Plus :size="14" />
          {{ busy ? 'Adding…' : 'Add note' }}
        </button>
      </div>
    </form>

    <p v-if="error" class="note-error"><CircleAlert :size="14" /> {{ error }}</p>

    <div v-if="displayedNotes.length" class="shot-note-list">
      <article
        v-for="note in displayedNotes"
        :key="note.id"
        class="shot-note"
        :class="{ resolved: note.resolved }"
      >
        <div class="shot-note-top">
          <span class="note-status-indicator" :class="{ resolved: note.resolved }">
            <Check v-if="note.resolved" :size="12" />
            <span>{{ note.resolved ? 'Resolved' : 'Needs attention' }}</span>
          </span>
          <small class="note-time">{{ new Date(note.updated_at).toLocaleString() }}</small>
        </div>

        <div class="shot-note-body">
          <textarea
            v-if="editingId === note.id"
            v-model="editingBody"
            rows="3"
            aria-label="Edit note"
          ></textarea>
          <p v-else>{{ note.body }}</p>
        </div>

        <!-- Attachments on saved note -->
        <div v-if="note.attachments.length" class="note-attachments">
          <div v-for="attachment in note.attachments" :key="attachment.id" class="attachment-chip">
            <template v-if="attachment.mime_type.startsWith('image/')">
              <img
                :src="attachment.url"
                :alt="attachment.original_name"
                class="attachment-thumb"
                title="Click to view full size"
                @click="previewModalUrl = attachment.url"
              />
              <span class="attachment-name" @click="previewModalUrl = attachment.url">
                {{ attachment.original_name }}
              </span>
            </template>
            <template v-else>
              <a :href="attachment.url" target="_blank" rel="noopener" class="attachment-link">
                <File :size="13" />
                <span class="attachment-name">{{ attachment.original_name }}</span>
                <small>{{ fileSize(attachment.size_bytes) }}</small>
              </a>
            </template>
            <button
              type="button"
              class="attachment-remove-btn"
              :aria-label="`Remove ${attachment.original_name}`"
              @click="removeAttachment(note, attachment.id)"
            >
              <X :size="12" />
            </button>
          </div>
        </div>

        <footer>
          <button
            type="button"
            class="resolve-toggle-btn"
            :class="{ is_resolved: note.resolved }"
            @click="toggleResolved(note)"
          >
            <RotateCcw v-if="note.resolved" :size="13" />
            <Check v-else :size="13" />
            {{ note.resolved ? 'Reopen note' : 'Mark as resolved' }}
          </button>

          <template v-if="editingId === note.id">
            <button type="button" class="action-btn" @click="saveEdit(note)">
              <Check :size="13" /> Save
            </button>
            <button type="button" class="action-btn" @click="editingId = null">
              <X :size="13" /> Cancel
            </button>
          </template>
          <button v-else type="button" class="action-btn" @click="editingId = note.id; editingBody = note.body">
            <Pencil :size="13" /> Edit
          </button>

          <label class="note-attach-more" title="Add another attachment">
            <Paperclip :size="13" />
            <span>Add file</span>
            <input type="file" multiple @change="addAttachments(note, $event)" />
          </label>

          <button type="button" class="action-btn danger" @click="removeNote(note)">
            <Trash2 :size="13" /> Delete
          </button>
        </footer>
      </article>
    </div>

    <p v-else-if="notes.length && !displayedNotes.length" class="note-empty">
      No notes matching this filter.
    </p>
    <p v-else class="note-empty">
      No notes on this shot yet. Add reminders or review screenshots above.
    </p>

    <!-- Image Lightbox Modal for Screenshots -->
    <div
      v-if="previewModalUrl"
      class="screenshot-lightbox-backdrop"
      @click="previewModalUrl = null"
    >
      <div class="screenshot-lightbox-card" @click.stop>
        <header>
          <span>Screenshot Preview</span>
          <button type="button" aria-label="Close" @click="previewModalUrl = null">
            <X :size="16" />
          </button>
        </header>
        <div class="screenshot-lightbox-body">
          <img :src="previewModalUrl" alt="Screenshot preview" />
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.shot-notes-panel {
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface-raised);
  padding: 16px;
  display: grid;
  gap: 14px;
}
.shot-notes-panel.in-drawer {
  border: none;
  border-radius: 0;
  background: transparent;
  padding: 0;
  box-shadow: none;
}
.notes-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.notes-header-title {
  display: flex;
  align-items: center;
  gap: 10px;
}
.notes-header-title div {
  display: grid;
}
.notes-header-title span {
  font-size: 12px;
  color: var(--muted);
}
.notes-header-badges {
  margin-left: auto;
}
.note-count,
.note-clear {
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 700;
}
.note-count {
  background: rgba(246, 196, 83, 0.15);
  color: #f6c453 !important;
  border: 1px solid rgba(246, 196, 83, 0.35);
}
.note-clear {
  display: flex;
  align-items: center;
  gap: 4px;
  background: color-mix(in srgb, #4ade80 13%, transparent);
  color: #4ade80 !important;
}

.notes-filter-tabs {
  display: flex;
  gap: 6px;
  border-bottom: 1px solid var(--border);
  padding-bottom: 8px;
}
.filter-tab {
  background: transparent;
  border: none;
  color: var(--muted);
  font-size: 12px;
  padding: 4px 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s ease;
}
.filter-tab:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.05);
}
.filter-tab.active {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.1);
  font-weight: 700;
}

.note-composer {
  display: grid;
  gap: 8px;
  padding: 10px;
  border-radius: 10px;
  background: var(--surface-2, rgba(0, 0, 0, 0.2));
  border: 1px dashed var(--border);
  transition: border-color 0.15s ease;
}
.note-composer.is-dragging {
  border-color: #f6c453;
  background: rgba(246, 196, 83, 0.05);
}
.note-composer textarea,
.shot-note textarea {
  width: 100%;
  resize: vertical;
  border-radius: 8px;
  background: var(--surface-raised);
  border: 1px solid var(--border);
  padding: 8px;
  color: inherit;
  font-family: inherit;
  font-size: 13px;
}
.staged-attachments-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.staged-file-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 8px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 11px;
}
.staged-file-name {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.staged-file-pill small {
  color: var(--muted);
}
.staged-file-pill button {
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
}
.staged-file-pill button:hover {
  color: #ff5555;
}

.note-composer-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.paste-hint {
  font-size: 11px;
  color: var(--muted);
}
.note-composer-actions .primary-button {
  margin-left: auto;
}
.note-file-button,
.note-attach-more {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  cursor: pointer;
  font-size: 12px;
  color: var(--muted);
  padding: 4px 6px;
  border-radius: 6px;
  transition: all 0.15s ease;
}
.note-file-button:hover,
.note-attach-more:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.06);
}
.note-file-button input,
.note-attach-more input {
  display: none;
}

.shot-note-list {
  display: grid;
  gap: 10px;
}
.shot-note {
  border: 1px solid var(--border);
  border-radius: 11px;
  padding: 12px;
  display: grid;
  gap: 10px;
  background: var(--surface-2, rgba(0, 0, 0, 0.15));
  transition: all 0.2s ease;
}
.shot-note.resolved {
  opacity: 0.6;
  border-color: rgba(255, 255, 255, 0.08);
}
.shot-note.resolved:hover {
  opacity: 0.9;
}
.shot-note-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.note-status-indicator {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(246, 196, 83, 0.15);
  color: #f6c453;
}
.note-status-indicator.resolved {
  background: rgba(74, 222, 128, 0.15);
  color: #4ade80;
}
.note-time {
  color: var(--muted);
  font-size: 11px;
}
.shot-note-body p {
  white-space: pre-wrap;
  margin: 0;
  font-size: 13px;
  line-height: 1.45;
}

.note-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.attachment-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px 8px;
  background: var(--surface-raised);
}
.attachment-thumb {
  width: 32px;
  height: 32px;
  object-fit: cover;
  border-radius: 4px;
  cursor: pointer;
  transition: opacity 0.15s ease;
}
.attachment-thumb:hover {
  opacity: 0.8;
}
.attachment-name {
  font-size: 12px;
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}
.attachment-link {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: inherit;
  text-decoration: none;
}
.attachment-remove-btn {
  border: none;
  background: none;
  color: var(--muted);
  cursor: pointer;
  padding: 2px;
  display: flex;
  align-items: center;
}
.attachment-remove-btn:hover {
  color: #ff5555;
}

.shot-note footer {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  border-top: 1px solid rgba(255, 255, 255, 0.05);
  padding-top: 8px;
}
.resolve-toggle-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  border: 1px solid rgba(246, 196, 83, 0.3);
  background: rgba(246, 196, 83, 0.12);
  color: #f6c453;
  transition: all 0.15s ease;
}
.resolve-toggle-btn.is_resolved {
  border-color: rgba(255, 255, 255, 0.15);
  background: rgba(255, 255, 255, 0.05);
  color: var(--muted);
}
.resolve-toggle-btn:hover {
  transform: translateY(-1px);
}
.action-btn {
  border: 0;
  background: none;
  color: var(--muted);
  font-size: 12px;
  padding: 4px 6px;
  display: inline-flex;
  gap: 5px;
  align-items: center;
  border-radius: 4px;
  cursor: pointer;
}
.action-btn:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.06);
}
.action-btn.danger {
  margin-left: auto;
  color: var(--danger, #ff6b6b);
}
.action-btn.danger:hover {
  background: rgba(255, 85, 85, 0.1);
}

.note-empty,
.note-error {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}
.note-error {
  color: var(--danger, #ff6b6b);
  display: flex;
  align-items: center;
  gap: 6px;
}

.screenshot-lightbox-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.85);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 24px;
}
.screenshot-lightbox-card {
  max-width: 90vw;
  max-height: 90vh;
  display: grid;
  grid-template-rows: auto 1fr;
  background: var(--surface-2, #1a1a1a);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.screenshot-lightbox-card header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
  font-weight: 600;
}
.screenshot-lightbox-card header button {
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
}
.screenshot-lightbox-body {
  overflow: auto;
  padding: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.screenshot-lightbox-body img {
  max-width: 100%;
  max-height: 75vh;
  object-fit: contain;
  border-radius: 6px;
}
</style>
