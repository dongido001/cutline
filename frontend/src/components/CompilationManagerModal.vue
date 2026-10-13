<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  Boxes,
  Check,
  ChevronDown,
  Clock3,
  Film,
  GripVertical,
  Plus,
  Sparkles,
  Trash2,
  X,
  Play,
  ArrowRight,
  Layers,
} from '@lucide/vue'
import { useStudioStore } from '@/stores/studio'
import type { CompilationSummary } from '@/lib/api'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ close: []; 'open-compilation': [compilationId: string] }>()

const store = useStudioStore()
const dialog = ref<HTMLDialogElement>()

const activeTab = ref<'create' | 'list'>('create')
const newTitle = ref('')
const newDescription = ref('')
const selectedEpisodeIds = ref<string[]>([])
const isSubmitting = ref(false)
const localError = ref('')
const deletingId = ref<string | null>(null)
const confirmingDeleteId = ref<string | null>(null)

const projectEpisodes = computed(() => store.episodes || [])
const compilations = computed(() => store.compilations || [])

// Default title helper
watch(
  [selectedEpisodeIds, () => projectEpisodes.value],
  () => {
    if (newTitle.value && !newTitle.value.startsWith('Episodes ')) return
    if (!selectedEpisodeIds.value.length) {
      newTitle.value = ''
      return
    }
    const nums = selectedEpisodeIds.value
      .map((id) => projectEpisodes.value.find((e) => e.id === id)?.episode_number)
      .filter((n): n is number => n !== undefined)
    if (nums.length === 1) {
      newTitle.value = `Episode ${nums[0]} Cut`
    } else if (nums.length > 1) {
      const min = Math.min(...nums)
      const max = Math.max(...nums)
      if (max - min + 1 === nums.length) {
        newTitle.value = `Episodes ${min}–${max} Box`
      } else {
        newTitle.value = `Episodes ${nums.join(', ')} Mix`
      }
    }
  },
  { deep: true },
)

watch(
  () => props.show,
  async (open) => {
    if (open) {
      localError.value = ''
      confirmingDeleteId.value = null
      if (compilations.value.length > 0) {
        activeTab.value = 'list'
      } else {
        activeTab.value = 'create'
      }
      resetForm()
      await nextTick()
      dialog.value?.showModal()
    } else {
      confirmingDeleteId.value = null
      dialog.value?.close()
    }
  },
)

function resetForm() {
  selectedEpisodeIds.value = projectEpisodes.value.slice(0, Math.min(4, projectEpisodes.value.length)).map((e) => e.id)
  newTitle.value = ''
  newDescription.value = ''
  localError.value = ''
}

function handleClose() {
  emit('close')
}

function applyPreset(count: number | 'all') {
  if (count === 'all') {
    selectedEpisodeIds.value = projectEpisodes.value.map((e) => e.id)
  } else {
    selectedEpisodeIds.value = projectEpisodes.value.slice(0, count).map((e) => e.id)
  }
}

function toggleEpisode(epId: string) {
  const idx = selectedEpisodeIds.value.indexOf(epId)
  if (idx >= 0) {
    selectedEpisodeIds.value.splice(idx, 1)
  } else {
    selectedEpisodeIds.value.push(epId)
  }
}

function isEpisodeSelected(epId: string): boolean {
  return selectedEpisodeIds.value.includes(epId)
}

// Move episode up/down in the custom compilation order
function moveEpisode(epId: string, direction: 'up' | 'down') {
  const idx = selectedEpisodeIds.value.indexOf(epId)
  if (idx < 0) return
  const targetIdx = direction === 'up' ? idx - 1 : idx + 1
  if (targetIdx < 0 || targetIdx >= selectedEpisodeIds.value.length) return
  const [item] = selectedEpisodeIds.value.splice(idx, 1)
  if (item) {
    selectedEpisodeIds.value.splice(targetIdx, 0, item)
  }
}

const selectedSummary = computed(() => {
  let shotCount = 0
  let durationMs = 0
  for (const id of selectedEpisodeIds.value) {
    const ep = projectEpisodes.value.find((e) => e.id === id)
    if (ep) {
      shotCount += ep.shot_count || 0
      durationMs += ep.duration_ms || 0
    }
  }
  const minutes = Math.floor(durationMs / 60000)
  const seconds = Math.round((durationMs % 60000) / 1000)
  return {
    count: selectedEpisodeIds.value.length,
    shots: shotCount,
    durationFormatted: minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`,
  }
})

async function handleCreateCompilation() {
  if (!selectedEpisodeIds.value.length) {
    localError.value = 'Please select at least one episode for the compilation box.'
    return
  }
  const title = (newTitle.value || 'Custom Compilation').trim()
  isSubmitting.value = true
  localError.value = ''
  try {
    const created = await store.createCompilation({
      title,
      description: newDescription.value.trim() || undefined,
      episode_ids: [...selectedEpisodeIds.value],
    })
    emit('open-compilation', created.id)
    emit('close')
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Failed to create compilation'
  } finally {
    isSubmitting.value = false
  }
}

function handleSelectExisting(compilation: CompilationSummary) {
  store.selectCompilation(compilation.id)
  emit('open-compilation', compilation.id)
  emit('close')
}

function requestDeleteCompilation(compilationId: string) {
  confirmingDeleteId.value = compilationId
  localError.value = ''
}

function cancelDeleteCompilation() {
  if (deletingId.value) return
  confirmingDeleteId.value = null
}

async function confirmDeleteCompilation(compilationId: string) {
  deletingId.value = compilationId
  localError.value = ''
  try {
    await store.deleteCompilation(compilationId)
    confirmingDeleteId.value = null
  } catch (cause) {
    localError.value = cause instanceof Error ? cause.message : 'Failed to delete compilation'
  } finally {
    deletingId.value = null
  }
}

function formatDuration(ms?: number): string {
  if (!ms) return '0s'
  const minutes = Math.floor(ms / 60000)
  const seconds = Math.round((ms % 60000) / 1000)
  return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`
}

function getEpisodeNumbers(epIds: string[]): string {
  const nums = epIds
    .map((id) => projectEpisodes.value.find((e) => e.id === id)?.episode_number)
    .filter((n): n is number => n !== undefined)
  if (!nums.length) return ''
  return nums.map((n) => `Ep ${n}`).join(', ')
}
</script>

<template>
  <dialog
    ref="dialog"
    class="compilation-dialog"
    aria-labelledby="compilation-modal-title"
    @cancel.prevent="handleClose"
  >
    <div class="compilation-modal-content">
      <header class="modal-header">
        <div class="modal-title-group">
          <div class="icon-bubble">
            <Boxes :size="20" />
          </div>
          <div>
            <h2 id="compilation-modal-title">Multi-Episode Compilation Boxes</h2>
            <p class="modal-subtitle">
              Group and rearrange episodes into custom compilation cuts with independent timelines and master exports.
            </p>
          </div>
        </div>
        <button type="button" class="close-button" aria-label="Close" @click="handleClose">
          <X :size="18" />
        </button>
      </header>

      <nav class="compilation-tabs" role="tablist">
        <button
          type="button"
          class="tab-btn"
          :class="{ active: activeTab === 'create' }"
          @click="activeTab = 'create'"
        >
          <Plus :size="15" />
          <span>New Compilation Box</span>
        </button>
        <button
          type="button"
          class="tab-btn"
          :class="{ active: activeTab === 'list' }"
          @click="activeTab = 'list'"
        >
          <Layers :size="15" />
          <span>Saved Compilations</span>
          <span v-if="compilations.length" class="badge">{{ compilations.length }}</span>
        </button>
      </nav>

      <div v-if="localError" class="modal-error-banner">
        {{ localError }}
      </div>

      <!-- CREATE VIEW -->
      <section v-if="activeTab === 'create'" class="tab-panel create-panel">
        <div class="form-row">
          <div class="form-group flex-2">
            <label for="comp-title">Compilation Title</label>
            <input
              id="comp-title"
              v-model="newTitle"
              type="text"
              placeholder="e.g. Episodes 1–4 Recap"
              class="text-input"
              maxlength="200"
            />
          </div>
          <div class="form-group flex-3">
            <label for="comp-desc">Description (Optional)</label>
            <input
              id="comp-desc"
              v-model="newDescription"
              type="text"
              placeholder="e.g. Feature-length recap for social media"
              class="text-input"
            />
          </div>
        </div>

        <div class="presets-row">
          <span class="presets-label">Quick Presets:</span>
          <button
            v-if="projectEpisodes.length >= 4"
            type="button"
            class="preset-btn"
            @click="applyPreset(4)"
          >
            Episodes 1–4
          </button>
          <button
            v-if="projectEpisodes.length >= 7"
            type="button"
            class="preset-btn"
            @click="applyPreset(7)"
          >
            Episodes 1–7
          </button>
          <button
            type="button"
            class="preset-btn"
            @click="applyPreset('all')"
          >
            All Episodes (1–{{ projectEpisodes.length }})
          </button>
          <button
            v-if="selectedEpisodeIds.length"
            type="button"
            class="preset-btn ghost"
            @click="selectedEpisodeIds = []"
          >
            Clear Selection
          </button>
        </div>

        <div class="episodes-selector-area">
          <div class="selector-header">
            <h3>Select &amp; Order Episodes in this Box</h3>
            <span class="selection-pill">
              {{ selectedSummary.count }} of {{ projectEpisodes.length }} episodes selected
              &middot; {{ selectedSummary.shots }} shots &middot; {{ selectedSummary.durationFormatted }}
            </span>
          </div>

          <div class="episodes-cards-grid">
            <div
              v-for="ep in projectEpisodes"
              :key="ep.id"
              class="episode-card"
              :class="{ selected: isEpisodeSelected(ep.id) }"
              @click="toggleEpisode(ep.id)"
            >
              <div class="ep-check-indicator">
                <Check v-if="isEpisodeSelected(ep.id)" :size="14" />
              </div>
              <div class="ep-card-info">
                <div class="ep-card-top">
                  <span class="ep-number">Episode {{ ep.episode_number }}</span>
                  <span class="ep-duration">{{ formatDuration(ep.duration_ms) }}</span>
                </div>
                <strong class="ep-title">{{ ep.title || 'Untitled Episode' }}</strong>
                <span class="ep-shots-count">{{ ep.shot_count || 0 }} shots</span>
              </div>
            </div>
          </div>

          <!-- Order arrangement pill strip if episodes are selected -->
          <div v-if="selectedEpisodeIds.length > 1" class="order-arrangement-section">
            <span class="order-label">Sequence Arrangement (Drag/Shift order):</span>
            <div class="ordered-pills-row">
              <div
                v-for="(epId, idx) in selectedEpisodeIds"
                :key="epId"
                class="ordered-ep-pill"
              >
                <span class="order-idx">{{ idx + 1 }}</span>
                <span class="order-name">
                  Ep {{ projectEpisodes.find((e) => e.id === epId)?.episode_number }}
                </span>
                <div class="reorder-actions">
                  <button
                    type="button"
                    class="arrow-btn"
                    :disabled="idx === 0"
                    title="Move earlier"
                    @click.stop="moveEpisode(epId, 'up')"
                  >
                    &lsaquo;
                  </button>
                  <button
                    type="button"
                    class="arrow-btn"
                    :disabled="idx === selectedEpisodeIds.length - 1"
                    title="Move later"
                    @click.stop="moveEpisode(epId, 'down')"
                  >
                    &rsaquo;
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <footer class="modal-footer">
          <div class="footer-summary">
            <strong>Estimated Cut:</strong>
            <span>{{ selectedSummary.durationFormatted }} ({{ selectedSummary.shots }} total shots)</span>
          </div>
          <div class="footer-actions">
            <button type="button" class="secondary-btn" @click="handleClose">Cancel</button>
            <button
              type="button"
              class="primary-btn"
              :disabled="!selectedEpisodeIds.length || isSubmitting"
              @click="handleCreateCompilation"
            >
              <Sparkles :size="15" />
              <span>{{ isSubmitting ? 'Creating compilation…' : 'Create & Open Box' }}</span>
            </button>
          </div>
        </footer>
      </section>

      <!-- SAVED COMPILATIONS VIEW -->
      <section v-else class="tab-panel list-panel">
        <div v-if="!compilations.length" class="empty-state">
          <Boxes :size="36" class="empty-icon" />
          <h3>No compilation boxes yet</h3>
          <p>Create your first compilation box to combine and edit multiple episodes together.</p>
          <button type="button" class="primary-btn" @click="activeTab = 'create'">
            <Plus :size="15" />
            <span>Create Compilation Box</span>
          </button>
        </div>

        <div v-else class="compilations-list">
          <article
            v-for="comp in compilations"
            :key="comp.id"
            class="compilation-item-card"
            :class="{ active: store.activeCompilationId === comp.id }"
          >
            <div class="comp-card-main">
              <div class="comp-title-row">
                <span class="comp-icon"><Boxes :size="16" /></span>
                <strong>{{ comp.title }}</strong>
                <span v-if="store.activeCompilationId === comp.id" class="active-badge">Active in Assemble</span>
              </div>
              <p v-if="comp.description" class="comp-desc">{{ comp.description }}</p>
              <div class="comp-meta-row">
                <span class="meta-tag episodes-tag">
                  {{ getEpisodeNumbers(comp.episode_ids) }}
                </span>
                <span class="meta-tag">
                  {{ comp.clip_count ?? 0 }} shots
                </span>
                <span class="meta-tag">
                  {{ formatDuration(comp.duration_ms) }}
                </span>
              </div>
            </div>

            <div v-if="confirmingDeleteId === comp.id" class="comp-delete-confirm">
              <span class="comp-delete-prompt">
                Delete <strong>{{ comp.title }}</strong>? This cannot be undone.
              </span>
              <div class="comp-delete-confirm-actions">
                <button
                  type="button"
                  class="action-btn cancel-delete-btn"
                  :disabled="deletingId === comp.id"
                  @click="cancelDeleteCompilation"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  class="action-btn confirm-delete-btn"
                  :disabled="deletingId === comp.id"
                  @click="confirmDeleteCompilation(comp.id)"
                >
                  <Trash2 :size="14" />
                  <span>{{ deletingId === comp.id ? 'Deleting…' : 'Delete' }}</span>
                </button>
              </div>
            </div>
            <div v-else class="comp-card-actions">
              <button
                type="button"
                class="action-btn open-btn"
                @click="handleSelectExisting(comp)"
              >
                <Play :size="14" fill="currentColor" />
                <span>Open in Assemble</span>
              </button>
              <button
                type="button"
                class="action-btn delete-btn"
                title="Delete compilation"
                @click="requestDeleteCompilation(comp.id)"
              >
                <Trash2 :size="14" />
              </button>
            </div>
          </article>
        </div>

        <footer class="modal-footer list-footer">
          <button type="button" class="secondary-btn" @click="activeTab = 'create'">
            <Plus :size="15" />
            <span>Create Another Compilation Box</span>
          </button>
          <button type="button" class="secondary-btn" @click="handleClose">Close</button>
        </footer>
      </section>
    </div>
  </dialog>
</template>

<style scoped>
.compilation-dialog:not([open]) {
  display: none;
}

.compilation-dialog {
  position: fixed;
  inset: 0;
  margin: auto;
  border: none;
  background: transparent;
  padding: 0;
  max-width: 900px;
  width: min(900px, calc(100vw - 32px));
  max-height: calc(100dvh - 36px);
  color: #f1f5f9;
  outline: none;
}

.compilation-dialog::backdrop {
  background: rgba(10, 15, 29, 0.75);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
}

.compilation-modal-content {
  background: #0f172a;
  border: 1px solid #334155;
  border-radius: 16px;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
  display: flex;
  flex-direction: column;
  max-height: 88vh;
  overflow: hidden;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid #1e293b;
}

.modal-title-group {
  display: flex;
  align-items: center;
  gap: 14px;
}

.icon-bubble {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: rgba(147, 51, 234, 0.15);
  color: #c084fc;
  display: flex;
  align-items: center;
  justify-content: center;
}

.modal-header h2 {
  font-size: 1.15rem;
  font-weight: 600;
  margin: 0;
  color: #f8fafc;
}

.modal-subtitle {
  font-size: 0.85rem;
  color: #94a3b8;
  margin: 2px 0 0 0;
}

.close-button {
  background: transparent;
  border: none;
  color: #94a3b8;
  cursor: pointer;
  padding: 8px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s ease;
}

.close-button:hover {
  background: #1e293b;
  color: #f8fafc;
}

.compilation-tabs {
  display: flex;
  gap: 8px;
  padding: 12px 24px;
  background: #090d16;
  border-bottom: 1px solid #1e293b;
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
  color: #94a3b8;
  font-size: 0.88rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.tab-btn:hover {
  color: #f8fafc;
  background: #1e293b;
}

.tab-btn.active {
  color: #f8fafc;
  background: #1e293b;
  border-color: #334155;
}

.tab-btn .badge {
  background: #7c3aed;
  color: white;
  padding: 2px 7px;
  border-radius: 10px;
  font-size: 0.72rem;
  font-weight: 600;
}

.modal-error-banner {
  background: rgba(239, 68, 68, 0.15);
  border-left: 3px solid #ef4444;
  color: #fca5a5;
  padding: 10px 16px;
  margin: 12px 24px 0 24px;
  border-radius: 6px;
  font-size: 0.85rem;
}

.tab-panel {
  padding: 20px 24px;
  overflow-y: auto;
  flex: 1;
}

.form-row {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}

.flex-2 {
  flex: 2;
}

.flex-3 {
  flex: 3;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-group label {
  font-size: 0.82rem;
  font-weight: 500;
  color: #cbd5e1;
}

.text-input {
  background: #090d16;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 14px;
  color: #f8fafc;
  font-size: 0.9rem;
  outline: none;
  transition: border-color 0.15s ease;
}

.text-input:focus {
  border-color: #a855f7;
}

.presets-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}

.presets-label {
  font-size: 0.82rem;
  color: #94a3b8;
  margin-right: 4px;
}

.preset-btn {
  background: #1e293b;
  border: 1px solid #334155;
  color: #e2e8f0;
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.preset-btn:hover {
  background: #334155;
  border-color: #475569;
}

.preset-btn.ghost {
  background: transparent;
  border-color: transparent;
  color: #94a3b8;
}

.preset-btn.ghost:hover {
  color: #ef4444;
}

.episodes-selector-area {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.selector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.selector-header h3 {
  font-size: 0.92rem;
  font-weight: 600;
  color: #f1f5f9;
  margin: 0;
}

.selection-pill {
  font-size: 0.78rem;
  color: #c084fc;
  background: rgba(147, 51, 234, 0.12);
  padding: 3px 10px;
  border-radius: 12px;
}

.episodes-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 12px;
  max-height: 240px;
  overflow-y: auto;
  padding: 2px;
}

.episode-card {
  background: #090d16;
  border: 1px solid #1e293b;
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  gap: 10px;
  cursor: pointer;
  transition: all 0.15s ease;
  user-select: none;
}

.episode-card:hover {
  border-color: #475569;
}

.episode-card.selected {
  background: rgba(147, 51, 234, 0.08);
  border-color: #9333ea;
}

.ep-check-indicator {
  width: 20px;
  height: 20px;
  border-radius: 6px;
  border: 1px solid #475569;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  margin-top: 2px;
  background: #0f172a;
}

.episode-card.selected .ep-check-indicator {
  background: #9333ea;
  border-color: #9333ea;
  color: white;
}

.ep-card-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
  overflow: hidden;
  flex: 1;
}

.ep-card-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.ep-number {
  font-size: 0.72rem;
  text-transform: uppercase;
  font-weight: 700;
  color: #a855f7;
  letter-spacing: 0.5px;
}

.ep-duration {
  font-size: 0.72rem;
  color: #64748b;
}

.ep-title {
  font-size: 0.85rem;
  color: #f1f5f9;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ep-shots-count {
  font-size: 0.72rem;
  color: #64748b;
}

.order-arrangement-section {
  background: #090d16;
  border: 1px solid #1e293b;
  border-radius: 10px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.order-label {
  font-size: 0.8rem;
  font-weight: 500;
  color: #94a3b8;
}

.ordered-pills-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.ordered-ep-pill {
  display: flex;
  align-items: center;
  gap: 6px;
  background: #1e293b;
  border: 1px solid #334155;
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 0.8rem;
  color: #e2e8f0;
}

.order-idx {
  background: #9333ea;
  color: white;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  font-size: 0.7rem;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}

.reorder-actions {
  display: flex;
  gap: 2px;
  margin-left: 4px;
}

.arrow-btn {
  background: transparent;
  border: none;
  color: #94a3b8;
  cursor: pointer;
  padding: 0 4px;
  font-size: 1rem;
  line-height: 1;
}

.arrow-btn:hover:not(:disabled) {
  color: #c084fc;
}

.arrow-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-top: 20px;
  border-top: 1px solid #1e293b;
  margin-top: 16px;
}

.footer-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.82rem;
  color: #94a3b8;
}

.footer-summary strong {
  color: #e2e8f0;
}

.footer-actions {
  display: flex;
  gap: 12px;
}

.secondary-btn {
  background: #1e293b;
  border: 1px solid #334155;
  color: #f1f5f9;
  border-radius: 8px;
  padding: 9px 18px;
  font-size: 0.85rem;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.15s ease;
}

.secondary-btn:hover {
  background: #334155;
}

.primary-btn {
  background: linear-gradient(135deg, #9333ea, #7c3aed);
  border: none;
  color: white;
  border-radius: 8px;
  padding: 9px 20px;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 8px;
  box-shadow: 0 4px 12px rgba(147, 51, 234, 0.35);
  transition: all 0.15s ease;
}

.primary-btn:hover:not(:disabled) {
  opacity: 0.95;
  transform: translateY(-1px);
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

/* SAVED LIST */
.compilations-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  max-height: 400px;
  overflow-y: auto;
}

.compilation-item-card {
  background: #090d16;
  border: 1px solid #1e293b;
  border-radius: 12px;
  padding: 16px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  transition: all 0.15s ease;
}

.compilation-item-card:hover {
  border-color: #334155;
}

.compilation-item-card.active {
  border-color: #9333ea;
  background: rgba(147, 51, 234, 0.05);
}

.comp-card-main {
  display: flex;
  flex-direction: column;
  gap: 6px;
  flex: 1;
}

.comp-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.comp-icon {
  color: #a855f7;
  display: flex;
  align-items: center;
}

.comp-title-row strong {
  font-size: 0.98rem;
  color: #f8fafc;
}

.active-badge {
  background: rgba(34, 197, 94, 0.15);
  color: #4ade80;
  border: 1px solid rgba(34, 197, 94, 0.3);
  font-size: 0.72rem;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
}

.comp-desc {
  font-size: 0.82rem;
  color: #94a3b8;
  margin: 0;
}

.comp-meta-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 2px;
}

.meta-tag {
  background: #1e293b;
  border-radius: 6px;
  padding: 3px 8px;
  font-size: 0.75rem;
  color: #cbd5e1;
}

.episodes-tag {
  color: #c084fc;
  background: rgba(147, 51, 234, 0.12);
}

.comp-card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.comp-delete-confirm {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
  min-width: min(280px, 100%);
}

.comp-delete-prompt {
  font-size: 0.8rem;
  color: #fca5a5;
  text-align: right;
  line-height: 1.35;
}

.comp-delete-prompt strong {
  color: #fecaca;
  font-weight: 600;
}

.comp-delete-confirm-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.cancel-delete-btn {
  background: transparent;
  border: 1px solid #334155;
  color: #cbd5e1;
  padding: 8px 12px;
}

.cancel-delete-btn:hover:not(:disabled) {
  background: rgba(148, 163, 184, 0.12);
  border-color: #475569;
  color: #f8fafc;
}

.confirm-delete-btn {
  background: rgba(239, 68, 68, 0.18);
  border: 1px solid rgba(239, 68, 68, 0.45);
  color: #fecaca;
  padding: 8px 12px;
}

.confirm-delete-btn:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.3);
  border-color: rgba(248, 113, 113, 0.7);
  color: #fff;
}

.confirm-delete-btn:disabled,
.cancel-delete-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.action-btn {
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.82rem;
  font-weight: 500;
  padding: 8px 14px;
  transition: all 0.15s ease;
}

.open-btn {
  background: #1e293b;
  border: 1px solid #334155;
  color: #f8fafc;
}

.open-btn:hover {
  background: #9333ea;
  border-color: #9333ea;
  color: white;
}

.delete-btn {
  background: transparent;
  border: 1px solid transparent;
  color: #94a3b8;
  padding: 8px 10px;
}

.delete-btn:hover:not(:disabled) {
  background: rgba(239, 68, 68, 0.15);
  color: #f87171;
  border-color: rgba(239, 68, 68, 0.3);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 40px 20px;
  gap: 12px;
}

.empty-icon {
  color: #475569;
}

.empty-state h3 {
  font-size: 1.05rem;
  font-weight: 600;
  color: #f1f5f9;
  margin: 0;
}

.empty-state p {
  font-size: 0.85rem;
  color: #94a3b8;
  max-width: 360px;
  margin: 0 0 12px 0;
}
</style>
