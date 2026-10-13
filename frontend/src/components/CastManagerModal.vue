<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Box, Check, Sparkles, Trash2, Upload, X } from '@lucide/vue'
import type { Character, ReferenceViewType } from '@/lib/api'
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
const pendingUploadViewType = ref<ReferenceViewType>('main')
const notesDraft = ref(props.character.notes || '')
const voiceDescriptionDraft = ref(props.character.voice_description || '')
const savingNotes = ref(false)
const notesSaved = ref(false)
const savingVoice = ref(false)
const voiceSaved = ref(false)
const busy = ref(false)

const elementKind = computed(() => props.character.kind || 'character')

const elementKindLabel = computed(() => {
  if (elementKind.value === 'scene') return 'Scene / Set Reference'
  if (elementKind.value === 'prop') return 'Prop Element'
  return 'Cast Character'
})

const elementTag = computed(() => {
  const slug = (props.character.name || 'ELEMENT')
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
  return `@${slug || 'ELEMENT'}`
})

const mainReferences = computed(() =>
  (props.character.references || []).filter((r) => (r.view_type || 'main') !== '3d_view'),
)

const turnaroundReferences = computed(() =>
  (props.character.references || []).filter((r) => r.view_type === '3d_view'),
)

const primaryMainRef = computed(() =>
  mainReferences.value.length ? mainReferences.value[mainReferences.value.length - 1] : null,
)

const primary3dRef = computed(() =>
  turnaroundReferences.value.length ? turnaroundReferences.value[turnaroundReferences.value.length - 1] : null,
)

watch(
  () => props.character.notes,
  (newNotes) => {
    notesDraft.value = newNotes || ''
  },
)

watch(
  () => props.character.voice_description,
  (newVoice) => {
    voiceDescriptionDraft.value = newVoice || ''
  },
)

const notesChanged = computed(() => {
  return notesDraft.value.trim() !== (props.character.notes || '').trim()
})

const voiceChanged = computed(() => {
  return voiceDescriptionDraft.value.trim() !== (props.character.voice_description || '').trim()
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

async function saveVoiceDescription() {
  if (!voiceChanged.value || savingVoice.value) return
  savingVoice.value = true
  try {
    await store.updateCharacter(props.character.id, { voice_description: voiceDescriptionDraft.value })
    voiceSaved.value = true
    setTimeout(() => {
      voiceSaved.value = false
    }, 2000)
  } catch {
    // Store error surface
  } finally {
    savingVoice.value = false
  }
}

async function handleGenerate(look?: string) {
  if (notesChanged.value) {
    await saveNotes()
  }
  if (elementKind.value === 'character' && voiceChanged.value) {
    await saveVoiceDescription()
  }
  try {
    const effectiveLook =
      look || (elementKind.value === 'scene' ? 'scene' : elementKind.value === 'prop' ? 'prop' : undefined)
    await store.generateCharacterReference(props.character.id, effectiveLook)
  } catch {
    // Store error surface
  }
}

function triggerUpload(viewType?: ReferenceViewType) {
  const defaultType: ReferenceViewType =
    elementKind.value === 'scene' ? 'scene' : elementKind.value === 'prop' ? 'prop' : 'main'
  pendingUploadViewType.value = viewType || defaultType
  fileInput.value?.click()
}

async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  busy.value = true
  try {
    await store.uploadCharacterReference(props.character.id, file, pendingUploadViewType.value)
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
  const noun = elementKind.value === 'scene' ? 'scene' : elementKind.value === 'prop' ? 'prop' : 'character'
  if (!confirm(`Are you sure you want to remove "${props.character.name}" from ${noun}s?`)) return
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
          <div class="cast-header-meta">
            <span class="section-label">{{ elementKindLabel }}</span>
            <span class="element-mention-pill" :class="`kind-${elementKind}`" title="Type this @mention in any shot prompt to attach this reference">{{ elementTag }}</span>
          </div>
          <h2 :id="'cast-manager-' + character.id">{{ character.name }}</h2>
        </div>
        <button type="button" class="mini-icon" aria-label="Close" @click="emit('close')">
          <X :size="17" />
        </button>
      </header>

      <div class="cast-manager-content">
        <p v-if="elementKind === 'character'" class="cast-manager-sub">
          Build <strong>① Character Main View</strong> and <strong>② Character 3D View (Turnaround Sheet)</strong> for <strong>{{ character.name }}</strong>. Reference-to-Video (R2V) uses the 3D turnaround sheet so the character stays consistent from front, side, and back angles.
        </p>
        <p v-else-if="elementKind === 'scene'" class="cast-manager-sub">
          Manage the canonical set &amp; environment reference for <strong>{{ character.name }}</strong>. Mention <code>{{ elementTag }}</code> in a shot prompt or attach it in R2V mode to lock room layout and lighting.
        </p>
        <p v-else class="cast-manager-sub">
          Manage the isolated studio prop reference for <strong>{{ character.name }}</strong>. Mention <code>{{ elementTag }}</code> in a shot prompt to keep this object's design and materials consistent.
        </p>

        <!-- 2-STEP CHARACTER MAIN VIEW + 3D TURNAROUND VIEW -->
        <div v-if="elementKind === 'character'" class="character-two-step-grid">
          <!-- Step 1: Character Main View -->
          <div class="step-view-card">
            <div class="step-view-header">
              <span class="step-badge">①</span>
              <div>
                <strong>Character Main View</strong>
                <small>Front-facing identity &amp; outfit lock</small>
              </div>
            </div>
            <div class="step-view-stage aspect-portrait">
              <img
                v-if="primaryMainRef"
                :src="primaryMainRef.url"
                :alt="`${character.name} Main View`"
              />
              <div v-else class="step-view-empty">
                <span>No Main View yet</span>
                <small>Generate or upload a front portrait first</small>
              </div>
            </div>
            <div class="step-view-actions">
              <button
                type="button"
                class="primary-button mini-btn"
                :disabled="busy || isGenerating || !imageGenerationAvailable"
                @click="handleGenerate()"
              >
                <Sparkles :size="13" />
                <span>{{ isGenerating ? 'Generating…' : primaryMainRef ? 'Regenerate Main' : 'Generate Main' }}</span>
              </button>
              <button
                type="button"
                class="secondary-button mini-btn"
                :disabled="busy"
                @click="triggerUpload('main')"
              >
                <Upload :size="13" />
                <span>Upload</span>
              </button>
              <button
                v-if="primaryMainRef"
                type="button"
                class="ref-delete-btn"
                title="Delete Main View"
                :disabled="busy"
                @click="handleDeleteReference(primaryMainRef.id)"
              >
                <Trash2 :size="13" />
              </button>
            </div>
          </div>

          <!-- Step 2: Character 3D View (16:9 Turnaround Sheet) -->
          <div class="step-view-card step-3d-card">
            <div class="step-view-header">
              <span class="step-badge step-badge-3d">②</span>
              <div>
                <strong>Character 3D View</strong>
                <small>Close-up + Front / Side / Back turnaround sheet (R2V)</small>
              </div>
              <span v-if="primary3dRef" class="r2v-ready-pill">3D Ready</span>
            </div>
            <div class="step-view-stage aspect-landscape">
              <img
                v-if="primary3dRef"
                :src="primary3dRef.url"
                :alt="`${character.name} 3D View Turnaround`"
              />
              <div v-else class="step-view-empty">
                <Box :size="22" />
                <span>No 3D Turnaround Sheet yet</span>
                <small>Generates close-up + front, side &amp; back full-body views on white studio backdrop using Step ①</small>
              </div>
            </div>
            <div class="step-view-actions">
              <button
                type="button"
                class="primary-button mini-btn btn-3d-accent"
                :disabled="busy || isGenerating || !imageGenerationAvailable"
                @click="handleGenerate('3d_view')"
              >
                <Box :size="13" />
                <span>{{ isGenerating ? 'Generating…' : primary3dRef ? 'Regenerate 3D View' : 'Generate 3D View' }}</span>
              </button>
              <button
                type="button"
                class="secondary-button mini-btn"
                :disabled="busy"
                @click="triggerUpload('3d_view')"
              >
                <Upload :size="13" />
                <span>Upload 3D Sheet</span>
              </button>
              <button
                v-if="primary3dRef"
                type="button"
                class="ref-delete-btn"
                title="Delete 3D View"
                :disabled="busy"
                @click="handleDeleteReference(primary3dRef.id)"
              >
                <Trash2 :size="13" />
              </button>
            </div>
          </div>
        </div>

        <!-- Prompt / Description Editor -->
        <div class="cast-prompt-section">
          <div class="cast-prompt-header">
            <label :for="'cast-prompt-' + character.id">
              <strong v-if="elementKind === 'character'">Character Appearance &amp; Wardrobe Prompt</strong>
              <strong v-else-if="elementKind === 'scene'">Scene Architecture, Layout &amp; Lighting Prompt</strong>
              <strong v-else>Prop Design, Material &amp; Scale Prompt</strong>
              <small v-if="elementKind === 'character'">Describe facial features, hair, skin tone, age, and signature full-body outfit for both Main and 3D views.</small>
              <small v-else-if="elementKind === 'scene'">Describe room layout, key furniture, architectural style, and lighting atmosphere.</small>
              <small v-else>Describe the object's shape, materials, colors, wear, and proportions.</small>
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
            :placeholder="
              elementKind === 'scene'
                ? 'Describe the set environment, architecture, furniture, and lighting...'
                : elementKind === 'prop'
                  ? 'Describe the prop object, materials, texture, and colors on a white background...'
                  : 'Describe facial features, skin tone, eye color, hair style, age, and full-body outfit...'
            "
            @blur="saveNotes"
          ></textarea>
        </div>

        <!-- Canonical Voice Prompt (only for Characters) -->
        <div v-if="elementKind === 'character'" class="cast-prompt-section">
          <div class="cast-prompt-header">
            <label :for="'cast-voice-' + character.id">
              <strong>Canonical Voice Prompt (Google Flow / Veo)</strong>
              <small>Injected as "Character voices: {{ character.name }}: …" in every video prompt where this character speaks or appears.</small>
            </label>
            <button
              v-if="voiceChanged || voiceSaved"
              type="button"
              class="secondary-button mini-btn"
              :disabled="savingVoice || voiceSaved"
              @click="saveVoiceDescription"
            >
              <Check v-if="voiceSaved" :size="13" />
              <span>{{ voiceSaved ? 'Saved' : savingVoice ? 'Saving…' : 'Save voice' }}</span>
            </button>
          </div>
          <textarea
            :id="'cast-voice-' + character.id"
            v-model="voiceDescriptionDraft"
            class="cast-prompt-textarea"
            rows="2"
            maxlength="1000"
            placeholder="e.g., Steady, resonant 29-year-old baritone, composed, controlled, unhurried delivery"
            @blur="saveVoiceDescription"
          ></textarea>
        </div>

        <!-- All References Gallery -->
        <div class="cast-manager-section-header">
          <strong>All Saved Reference Images ({{ character.references.length }})</strong>
          <small v-if="character.references.length > 1">Manage or delete reference takes below.</small>
        </div>

        <div v-if="!character.references.length" class="cast-no-refs">
          <p>No reference images yet. Generate one with AI or upload a photo.</p>
        </div>

        <div v-else class="cast-refs-grid">
          <div
            v-for="(refItem, idx) in character.references"
            :key="refItem.id"
            class="cast-ref-card"
          >
            <div class="cast-ref-thumb" :class="{ 'thumb-landscape': refItem.view_type === '3d_view' || elementKind !== 'character' }">
              <img :src="refItem.url" :alt="`${character.name} reference ${idx + 1}`" />
            </div>
            <div class="cast-ref-footer">
              <span class="ref-tag">
                {{ refItem.view_type === '3d_view' ? '3D View' : refItem.view_type === 'scene' ? 'Scene' : refItem.view_type === 'prop' ? 'Prop' : `Main ${idx + 1}` }}
              </span>
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
            @click="handleGenerate()"
          >
            <Sparkles :size="15" />
            <span>
              {{
                isGenerating
                  ? 'Generating…'
                  : elementKind === 'scene'
                    ? 'Generate Scene with AI'
                    : elementKind === 'prop'
                      ? 'Generate Prop with AI'
                      : 'Generate Main View'
              }}
            </span>
          </button>
          <button
            v-if="elementKind === 'character'"
            type="button"
            class="secondary-button"
            :disabled="busy || isGenerating || !imageGenerationAvailable"
            @click="handleGenerate('3d_view')"
          >
            <Box :size="15" />
            <span>{{ isGenerating ? 'Generating…' : 'Generate 3D Turnaround' }}</span>
          </button>
          <button
            type="button"
            class="secondary-button"
            :disabled="busy"
            @click="triggerUpload()"
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
            <span>Delete {{ elementKind }}</span>
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
  max-width: 720px;
  width: 94vw;
}

.cast-header-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.element-mention-pill {
  font-size: 11px;
  font-weight: 700;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(168, 85, 247, 0.18);
  color: #d8b4fe;
  border: 1px solid rgba(168, 85, 247, 0.35);
}

.element-mention-pill.kind-scene {
  background: rgba(34, 197, 94, 0.16);
  color: #86efac;
  border-color: rgba(34, 197, 94, 0.35);
}

.element-mention-pill.kind-prop {
  background: rgba(56, 189, 248, 0.16);
  color: #7dd3fc;
  border-color: rgba(56, 189, 248, 0.35);
}

.character-two-step-grid {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: 12px;
}

@media (max-width: 640px) {
  .character-two-step-grid {
    grid-template-columns: 1fr;
  }
}

.step-view-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid var(--line-subtle);
  border-radius: 10px;
  padding: 10px 12px;
}

.step-3d-card {
  border-color: rgba(168, 85, 247, 0.28);
  background: rgba(168, 85, 247, 0.04);
}

.step-view-header {
  display: flex;
  align-items: center;
  gap: 8px;
}

.step-view-header div {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.step-view-header strong {
  font-size: 12.5px;
  color: var(--text-primary);
}

.step-view-header small {
  font-size: 11px;
  color: var(--text-tertiary);
}

.step-badge {
  width: 22px;
  height: 22px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.step-badge-3d {
  background: rgba(168, 85, 247, 0.25);
  color: #e9d5ff;
}

.r2v-ready-pill {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(34, 197, 94, 0.18);
  color: #86efac;
  border: 1px solid rgba(34, 197, 94, 0.35);
}

.step-view-stage {
  width: 100%;
  height: 170px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.07);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.step-view-stage img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  background: #111;
}

.step-view-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 14px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 12px;
}

.step-view-empty small {
  font-size: 11px;
  color: var(--text-tertiary);
  max-width: 280px;
}

.step-view-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.btn-3d-accent {
  background: linear-gradient(135deg, #9333ea, #6366f1);
  border-color: rgba(168, 85, 247, 0.5);
  color: #fff;
}

.cast-manager-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
  margin: 8px 0;
  max-height: 75vh;
  overflow-y: auto;
  padding-right: 4px;
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
  padding: 20px;
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
  max-height: 32vh;
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

.cast-ref-thumb.thumb-landscape {
  aspect-ratio: 16 / 9;
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
