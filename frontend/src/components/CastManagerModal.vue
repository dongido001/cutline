<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, Sparkles, Trash2, Upload, X } from '@lucide/vue'
import type { Character } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'

const props = defineProps<{
  character: Character
  imageGenerationAvailable: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'deleted-character', characterId: string): void
}>()

const store = useStudioStore()
const fileInput = ref<HTMLInputElement | null>(null)
const notesDraft = ref(props.character.notes || '')
const savingNotes = ref(false)
const notesSaved = ref(false)
const busy = ref(false)

watch(
  () => props.character.notes,
  (newNotes) => {
    notesDraft.value = newNotes || ''
  },
)

const notesChanged = computed(() => {
  return notesDraft.value.trim() !== (props.character.notes || '').trim()
})

const isGenerating = computed(() => {
  return store.isGeneratingCast(props.character.id)
})

async function saveNotes() {
  if (!notesChanged.value || savingNotes.value) return
  savingNotes.value = true
  try {
    await store.updateCharacter(props.character.id, { notes: notesDraft.value })
    notesSaved.value = true
    setTimeout(() => {
      notesSaved.value = false
    }, 2000)
  } catch {
    // Store error surface
  } finally {
    savingNotes.value = false
  }
}

async function handleGenerate() {
  if (notesChanged.value) {
    await saveNotes()
  }
  try {
    await store.generateCharacterReference(props.character.id)
  } catch {
    // Store error surface
  }
}

function triggerUpload() {
  fileInput.value?.click()
}

async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  busy.value = true
  try {
    await store.uploadCharacterReference(props.character.id, file)
  } catch {
    // Store error surface
  } finally {
    busy.value = false
    input.value = ''
  }
}

async function handleDeleteReference(referenceId: string) {
  busy.value = true
  try {
    await store.deleteCharacterReference(referenceId)
  } catch {
    // Store error surface
  } finally {
    busy.value = false
  }
}

async function handleDeleteCharacter() {
  if (!confirm(`Are you sure you want to remove "${props.character.name}" from the cast?`)) return
  busy.value = true
  try {
    await store.deleteCharacter(props.character.id)
    emit('deleted-character', props.character.id)
    emit('close')
  } catch {
    // Store error surface
  } finally {
    busy.value = false
  }
}

const backdropPressed = ref(false)

function onBackdropMouseDown(e: MouseEvent) {
  backdropPressed.value = e.target === e.currentTarget
}

function onBackdropClick(e: MouseEvent) {
  if (backdropPressed.value && e.target === e.currentTarget) {
    emit('close')
  }
  backdropPressed.value = false
}
</script>

<template>
  <div
    class="modal-backdrop"
    @mousedown="onBackdropMouseDown"
    @click="onBackdropClick"
  >
    <section class="project-modal cast-manager-modal" role="dialog" aria-modal="true" :aria-labelledby="'cast-manager-' + character.id">
      <header>
        <div>
          <span class="section-label">Cast Character</span>
          <h2 :id="'cast-manager-' + character.id">{{ character.name }}</h2>
        </div>
        <button type="button" class="mini-icon" aria-label="Close" @click="emit('close')">
          <X :size="17" />
        </button>
      </header>

      <div class="cast-manager-content">
        <p class="cast-manager-sub">
          Manage face references for <strong>{{ character.name }}</strong>. Shots tagged with this character inherit these references for visual consistency.
        </p>

        <!-- Face Prompt / Appearance Notes Editor -->
        <div class="cast-prompt-section">
          <div class="cast-prompt-header">
            <label :for="'cast-prompt-' + character.id">
              <strong>Face Appearance & Generation Prompt</strong>
              <small>Edit this prompt to adjust facial features, hair, skin tone, or age before generating.</small>
            </label>
            <button
              v-if="notesChanged || notesSaved"
              type="button"
              class="secondary-button mini-btn"
              :disabled="savingNotes || notesSaved"
              @click="saveNotes"
            >
              <Check v-if="notesSaved" :size="13" />
              <span>{{ notesSaved ? 'Saved' : savingNotes ? 'Saving…' : 'Save prompt' }}</span>
            </button>
          </div>
          <textarea
            :id="'cast-prompt-' + character.id"
            v-model="notesDraft"
            class="cast-prompt-textarea"
            rows="3"
            maxlength="2000"
            placeholder="Describe facial features, skin tone, eye color, hair style, age, and distinguishing traits..."
            @blur="saveNotes"
          ></textarea>
        </div>

        <div class="cast-manager-section-header">
          <strong>Face References ({{ character.references.length }})</strong>
          <small v-if="character.references.length > 1">Delete duplicate or unwanted face references below.</small>
        </div>

        <div v-if="!character.references.length" class="cast-no-refs">
          <p>No face references yet. Generate one with AI or upload a photo.</p>
        </div>

        <div v-else class="cast-refs-grid">
          <div
            v-for="(refItem, idx) in character.references"
            :key="refItem.id"
            class="cast-ref-card"
          >
            <div class="cast-ref-thumb">
              <img :src="refItem.url" :alt="`${character.name} face ${idx + 1}`" />
            </div>
            <div class="cast-ref-footer">
              <span class="ref-tag">Ref {{ idx + 1 }}</span>
              <button
                type="button"
                class="ref-delete-btn"
                title="Delete this reference"
                :disabled="busy"
                @click="handleDeleteReference(refItem.id)"
              >
                <Trash2 :size="13" />
                <span>Delete</span>
              </button>
            </div>
          </div>
        </div>

        <div class="cast-manager-toolbar">
          <button
            type="button"
            class="primary-button"
            :disabled="busy || isGenerating || !imageGenerationAvailable"
            @click="handleGenerate"
          >
            <Sparkles :size="15" />
            <span>{{ isGenerating ? 'Generating…' : 'Generate face with AI' }}</span>
          </button>
          <button
            type="button"
            class="secondary-button"
            :disabled="busy"
            @click="triggerUpload"
          >
            <Upload :size="15" />
            <span>Upload photo</span>
          </button>
          <button
            type="button"
            class="danger-link-btn"
            :disabled="busy"
            @click="handleDeleteCharacter"
          >
            <Trash2 :size="14" />
            <span>Delete character</span>
          </button>
        </div>
      </div>

      <input
        ref="fileInput"
        type="file"
        class="visually-hidden"
        accept="image/png,image/jpeg,image/webp"
        @change="handleUpload"
      />

      <footer>
        <button type="button" class="primary-button" @click="emit('close')">
          <span>Done</span>
        </button>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.cast-manager-modal {
  max-width: 580px;
  width: 92vw;
}

.cast-manager-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 8px 0;
}

.cast-manager-sub {
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-secondary);
  margin: 0;
}

.cast-prompt-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--line-subtle);
  border-radius: 10px;
  padding: 12px 14px;
}

.cast-prompt-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.cast-prompt-header label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  cursor: pointer;
}

.cast-prompt-header strong {
  font-size: 13px;
  color: var(--text-primary);
}

.cast-prompt-header small {
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: 1.4;
}

.cast-prompt-textarea {
  width: 100%;
  border: 1px solid var(--line-subtle);
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.35);
  color: var(--text-primary);
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.5;
  resize: vertical;
  min-height: 72px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.cast-prompt-textarea:focus {
  border-color: var(--line-default);
  outline: none;
  box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.06);
}

.mini-btn {
  padding: 3px 9px;
  font-size: 11px;
  height: auto;
  min-height: 26px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.cast-manager-section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.cast-manager-section-header strong {
  font-size: 13px;
  color: var(--text-primary);
}

.cast-manager-section-header small {
  color: var(--text-tertiary);
  font-size: 11px;
}

.cast-no-refs {
  padding: 24px;
  text-align: center;
  background: rgba(255, 255, 255, 0.02);
  border: 1px dashed var(--line-subtle);
  border-radius: 10px;
  color: var(--text-secondary);
  font-size: 13px;
}

.cast-refs-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 12px;
  max-height: 48vh;
  overflow-y: auto;
  padding: 2px;
}

.cast-ref-card {
  display: flex;
  flex-direction: column;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--line-subtle);
  border-radius: 10px;
  overflow: hidden;
  transition: border-color 0.15s ease, transform 0.15s ease;
}

.cast-ref-card:hover {
  border-color: var(--line-default);
  transform: translateY(-1px);
}

.cast-ref-thumb {
  width: 100%;
  aspect-ratio: 1 / 1;
  background: #000;
  overflow: hidden;
}

.cast-ref-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.cast-ref-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  background: rgba(0, 0, 0, 0.28);
}

.ref-tag {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

.ref-delete-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: transparent;
  border: 0;
  color: #ff7878;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  padding: 3px 6px;
  border-radius: 5px;
  transition: background 0.12s ease;
}

.ref-delete-btn:hover {
  background: rgba(238, 92, 92, 0.18);
  color: #ff9e9e;
}

.ref-delete-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.cast-manager-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding-top: 10px;
  border-top: 1px solid var(--line-subtle);
}

.danger-link-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: auto;
  background: transparent;
  border: 0;
  color: #ff6b6b;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: 6px;
  transition: background 0.12s ease;
}

.danger-link-btn:hover {
  background: rgba(238, 92, 92, 0.12);
}

.danger-link-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  border: 0;
}
</style>
