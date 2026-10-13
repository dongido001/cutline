<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  Check,
  ChevronDown,
  CircleAlert,
  Clock3,
  Copy,
  FileText,
  Film,
  History,
  RefreshCw,
  RotateCcw,
  X,
} from '@lucide/vue'
import { api, type ScriptHistory, type ScriptVersion } from '@/lib/api'

const props = defineProps<{ show: boolean; versionId: string; episodeTitle: string }>()
const emit = defineEmits<{ close: []; restored: [] }>()

const dialog = ref<HTMLDialogElement>()
const history = ref<ScriptHistory | null>(null)
const preview = ref<ScriptVersion | null>(null)
const selected = ref('')
const view = ref<'shots' | 'source'>('shots')
const loading = ref(false)
const restoring = ref(false)
const error = ref('')
const copiedSource = ref(false)
let generation = 0

const shots = computed(() => preview.value?.scenes.flatMap((scene) => scene.shots) ?? [])
const totalDurationSeconds = computed(() =>
  Math.round(shots.value.reduce((sum, shot) => sum + shot.duration_ms, 0) / 1000),
)
const isCurrentSelected = computed(
  () => Boolean(history.value && selected.value === history.value.current_version_id),
)
const selectedVersionMeta = computed(() =>
  history.value?.versions.find((v) => v.id === selected.value) ?? null,
)
const canRestore = computed(
  () =>
    Boolean(
      preview.value &&
        history.value &&
        preview.value.id === selected.value &&
        selected.value !== history.value.current_version_id &&
        !loading.value &&
        !restoring.value,
    ),
)

const message = (cause: unknown) =>
  cause instanceof Error ? cause.message : 'Could not load version history. Please retry.'

function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatShotDuration(ms: number): string {
  const secs = ms / 1000
  return `${Number.isInteger(secs) ? secs : secs.toFixed(1)}s`
}

function formatStatusLabel(status: string): string {
  if (!status) return 'Draft'
  return status.replace(/_/g, ' ')
}

function cleanPromptText(text: string | undefined | null): string {
  if (!text) return '—'
  const cleaned = text
    .replace(/\n?\s*[=\-*_]{4,}[\s\S]*$/g, '')
    .trim()
  return cleaned || text.trim() || '—'
}

function onDialogClick(event: MouseEvent) {
  if (event.target === dialog.value && !restoring.value) {
    emit('close')
  }
}

async function copySourceText() {
  if (!preview.value?.source_text) return
  await navigator.clipboard.writeText(preview.value.source_text)
  copiedSource.value = true
  setTimeout(() => {
    copiedSource.value = false
  }, 2000)
}

async function selectVersion(id: string) {
  if (id === selected.value || restoring.value) return
  selected.value = id
  await loadPreview(id)
}

async function loadPreview(id: string) {
  const epoch = ++generation
  loading.value = true
  preview.value = null
  error.value = ''
  try {
    const result = await api.scriptVersion(id)
    if (epoch === generation) preview.value = result
  } catch (cause) {
    if (epoch === generation) error.value = message(cause)
  } finally {
    if (epoch === generation) loading.value = false
  }
}

async function load() {
  const epoch = ++generation
  loading.value = true
  history.value = null
  preview.value = null
  error.value = ''
  try {
    const result = await api.scriptHistory(props.versionId)
    if (epoch !== generation) return
    history.value = result
    selected.value = result.current_version_id
    await loadPreview(selected.value)
  } catch (cause) {
    if (epoch === generation) {
      error.value = message(cause)
      loading.value = false
    }
  }
}

async function restore() {
  if (!canRestore.value || !history.value) return
  restoring.value = true
  error.value = ''
  try {
    await api.restoreScriptVersion(selected.value, history.value)
    emit('restored')
    emit('close')
  } catch (cause) {
    error.value = message(cause)
  } finally {
    restoring.value = false
  }
}

watch(
  () => [props.show, props.versionId] as const,
  async ([show]) => {
    await nextTick()
    if (show) {
      if (!dialog.value?.open) dialog.value?.showModal()
      await load()
    } else {
      generation++
      if (dialog.value?.open) dialog.value.close()
    }
  },
  { immediate: true },
)
</script>

<template>
  <dialog
    ref="dialog"
    class="project-modal history-modal"
    aria-labelledby="history-title"
    @cancel.prevent="!restoring && emit('close')"
    @click="onDialogClick"
  >
    <div class="history-modal-shell">
      <header class="history-header">
        <div class="history-header-copy">
          <span class="section-label">
            <History :size="12" />
            <span>Revision History</span>
          </span>
          <div class="history-title-row">
            <h2 id="history-title">Version history</h2>
            <span v-if="history" class="history-count-badge">
              {{ history.versions.length }} {{ history.versions.length === 1 ? 'revision' : 'revisions' }}
            </span>
          </div>
          <p class="history-episode-subtitle">{{ episodeTitle }}</p>
        </div>
        <button
          type="button"
          class="mini-icon history-close-btn"
          aria-label="Close version history"
          :disabled="restoring"
          @click="emit('close')"
        >
          <X :size="17" />
        </button>
      </header>

      <div class="history-controls">
        <label class="history-control-field version-field">
          <span class="control-label">Saved version</span>
          <div class="select-wrapper">
            <select
              v-model="selected"
              :disabled="!history || restoring"
              @change="loadPreview(selected)"
            >
              <option
                v-for="version in history?.versions"
                :key="version.id"
                :value="version.id"
              >
                Version {{ version.version_number }}{{ version.id === history?.current_version_id ? ' · Current' : '' }} · {{ formatStatusLabel(version.status) }} · {{ formatTimestamp(version.created_at) }}
              </option>
            </select>
            <ChevronDown :size="14" class="select-chevron" />
          </div>
        </label>

        <label class="history-control-field view-field">
          <span class="control-label">Preview mode</span>
          <div class="select-wrapper">
            <select v-model="view">
              <option value="shots">Saved shots</option>
              <option value="source">Source screenplay</option>
            </select>
            <ChevronDown :size="14" class="select-chevron" />
          </div>
        </label>
      </div>

      <div v-if="history && history.versions.length > 1" class="version-pills-bar" role="tablist" aria-label="Quick version switcher">
        <button
          v-for="version in history.versions"
          :key="version.id"
          type="button"
          class="version-pill"
          :class="{
            active: selected === version.id,
            current: version.id === history.current_version_id,
          }"
          :disabled="restoring"
          @click="selectVersion(version.id)"
        >
          <span class="version-pill-num">v{{ version.version_number }}</span>
          <span v-if="version.id === history.current_version_id" class="version-pill-tag">Current</span>
          <span v-else class="version-pill-date">{{ formatTimestamp(version.created_at) }}</span>
        </button>
      </div>

      <div v-if="error" class="history-alert" role="alert">
        <div class="history-alert-copy">
          <CircleAlert :size="15" />
          <span>{{ error }}</span>
        </div>
        <button type="button" class="secondary-button" :disabled="restoring" @click="load">
          <RefreshCw :size="13" />
          <span>Reload history</span>
        </button>
      </div>

      <div v-if="loading" class="history-loading" role="status">
        <RefreshCw :size="18" class="spinning" />
        <span>Loading version…</span>
      </div>

      <div
        v-else-if="preview"
        class="history-preview"
        tabindex="0"
        aria-label="Read-only version preview"
      >
        <div class="history-summary-bar">
          <div class="history-summary-badges">
            <span class="version-badge">Version {{ preview.version_number }}</span>
            <span v-if="isCurrentSelected" class="status-badge current">
              <Check :size="11" />
              <span>Active version</span>
            </span>
            <span v-else class="status-badge snapshot">Historical snapshot</span>
            <span class="status-badge workflow">{{ formatStatusLabel(preview.status) }}</span>
            <span v-if="selectedVersionMeta" class="summary-timestamp">
              {{ formatTimestamp(selectedVersionMeta.created_at) }}
            </span>
          </div>

          <div class="history-summary-right">
            <span class="summary-metrics">
              {{ shots.length }} shots · {{ totalDurationSeconds }} seconds · Read-only preview
            </span>
            <div class="view-segmented" role="group" aria-label="Switch preview mode">
              <button
                type="button"
                :class="{ active: view === 'shots' }"
                @click="view = 'shots'"
              >
                <Film :size="12" />
                <span>Shots</span>
              </button>
              <button
                type="button"
                :class="{ active: view === 'source' }"
                @click="view = 'source'"
              >
                <FileText :size="12" />
                <span>Source</span>
              </button>
            </div>
            <button
              v-if="view === 'source' && preview.source_text"
              type="button"
              class="copy-source-button"
              @click="copySourceText"
            >
              <Check v-if="copiedSource" :size="12" />
              <Copy v-else :size="12" />
              <span>{{ copiedSource ? 'Copied' : 'Copy' }}</span>
            </button>
          </div>
        </div>

        <div v-if="view === 'source'" class="history-source-well">
          <pre>{{ preview.source_text }}</pre>
        </div>

        <div v-else class="history-shots-list">
          <div v-if="!shots.length" class="history-empty-shots">
            <Film :size="20" />
            <strong>No parsed shots in this revision</strong>
            <span>Switch to Source screenplay above to inspect the raw script text for this version.</span>
          </div>

          <section
            v-for="(shot, index) in shots"
            :key="shot.id"
            class="history-shot"
          >
            <div class="history-shot-header">
              <div class="history-shot-title">
                <span class="shot-index-pill">#{{ String(index + 1).padStart(2, '0') }}</span>
                <h3>Shot {{ index + 1 }} · {{ shot.title }}</h3>
              </div>
              <div class="history-shot-badges">
                <span v-if="shot.framing" class="shot-chip">{{ shot.framing }}</span>
                <span class="shot-chip duration">
                  <Clock3 :size="11" />
                  <span>{{ formatShotDuration(shot.duration_ms) }}</span>
                </span>
              </div>
            </div>

            <p class="history-shot-action">{{ shot.action || 'No visual action specified for this shot.' }}</p>

            <div v-if="shot.dialogue?.length" class="history-dialogue-group">
              <p
                v-for="(line, lineIndex) in shot.dialogue"
                :key="lineIndex"
                class="history-dialogue-line"
              >
                <strong>{{ line.character }}</strong>: {{ line.text }}
              </p>
            </div>

            <p v-if="shot.sound" class="history-shot-sound">
              <strong>Sound</strong>
              <span>{{ shot.sound }}</span>
            </p>

            <details class="history-prompts">
              <summary>
                <span>Production prompts</span>
                <ChevronDown :size="13" class="prompts-chevron" />
              </summary>
              <div class="history-prompts-grid">
                <p><strong>Image</strong>: {{ cleanPromptText(shot.image_prompt) }}</p>
                <p><strong>Video</strong>: {{ cleanPromptText(shot.video_prompt) }}</p>
              </div>
            </details>
          </section>
        </div>
      </div>

      <footer class="history-footer">
        <div class="history-footer-note">
          <span v-if="isCurrentSelected" class="footer-status-hint">
            Viewing current active version — select an earlier revision above to restore it.
          </span>
          <p>
            Restore creates a new version for review, including saved shots and media. Your current script and timeline are kept in history. A version without a saved timeline starts with an empty timeline.
          </p>
        </div>
        <button
          type="button"
          class="primary-button history-restore-button"
          :disabled="!canRestore"
          @click="restore"
        >
          <RotateCcw :size="14" :class="{ spinning: restoring }" />
          <span>{{ restoring ? 'Restoring…' : `Restore version ${preview?.version_number ?? ''}` }}</span>
        </button>
      </footer>
    </div>
  </dialog>
</template>

<style scoped>
.history-modal:not([open]) {
  display: none;
}

.history-modal {
  width: min(920px, calc(100vw - 32px));
  max-height: calc(100dvh - 36px);
  margin: auto;
  padding: 0;
  color: var(--text-primary, var(--ink));
  background: var(--panel, #141816);
  border: 1px solid var(--line-emphasis, rgba(255, 255, 255, 0.16));
  border-radius: 16px;
  box-shadow: 0 28px 80px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(255, 255, 255, 0.05);
  overflow: hidden;
}

.history-modal::backdrop {
  background: rgba(8, 10, 12, 0.72);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
}

.history-modal-shell {
  display: flex;
  flex-direction: column;
  max-height: calc(100dvh - 36px);
  overflow: hidden;
}

/* Header */
.history-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 18px 22px 15px;
  border-bottom: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.07));
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.025), transparent);
}

.history-header-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.history-header .section-label {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--accent, #eb6554);
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.history-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.history-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.015em;
  color: var(--text-primary, #f4f6f8);
}

.history-count-badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.08));
  color: var(--text-secondary, #a9b0ba);
  font-size: 11px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.history-episode-subtitle {
  margin: 0;
  font-size: 12px;
  color: var(--text-secondary, #a9b0ba);
}

.history-close-btn {
  flex-shrink: 0;
  width: 30px;
  height: 30px;
  border-radius: 7px;
}

/* Controls Bar */
.history-controls {
  display: grid;
  grid-template-columns: minmax(240px, 1.8fr) minmax(160px, 1fr);
  gap: 12px;
  padding: 14px 22px 12px;
  background: var(--surface-1, #111419);
  border-bottom: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.065));
}

.history-control-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.control-label {
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-tertiary, #737c88);
}

.select-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}

.select-wrapper select {
  width: 100%;
  height: 36px;
  padding: 0 32px 0 11px;
  appearance: none;
  -webkit-appearance: none;
  color: var(--text-primary, #f4f6f8);
  background: var(--panel-2, #191e1b);
  border: 1px solid var(--line-default, rgba(255, 255, 255, 0.11));
  border-radius: 8px;
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.select-wrapper select:hover:not(:disabled) {
  border-color: var(--line-emphasis, rgba(255, 255, 255, 0.2));
  background: var(--panel-3, #202622);
}

.select-wrapper select:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.select-chevron {
  position: absolute;
  right: 10px;
  pointer-events: none;
  color: var(--text-tertiary, #737c88);
}

/* Quick Version Switcher Pills */
.version-pills-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 22px;
  overflow-x: auto;
  background: rgba(0, 0, 0, 0.2);
  border-bottom: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.065));
}

.version-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  border: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.08));
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-secondary, #a9b0ba);
  font-size: 11.5px;
  font-weight: 500;
  white-space: nowrap;
  cursor: pointer;
  transition: all 0.14s ease;
}

.version-pill:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.07);
  color: var(--text-primary, #f4f6f8);
  border-color: var(--line-default, rgba(255, 255, 255, 0.14));
}

.version-pill.active {
  background: rgba(235, 101, 84, 0.14);
  border-color: rgba(235, 101, 84, 0.45);
  color: #ffffff;
}

.version-pill-num {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.version-pill-tag {
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(73, 169, 120, 0.18);
  color: #7fd1a5;
  font-size: 10px;
  font-weight: 650;
}

.version-pill-date {
  font-size: 10.5px;
  color: var(--text-tertiary, #737c88);
}

.version-pill.active .version-pill-date {
  color: rgba(255, 255, 255, 0.78);
}

/* Error & Loading States */
.history-alert {
  margin: 12px 22px 0;
  padding: 10px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-radius: 10px;
  border: 1px solid rgba(238, 92, 92, 0.32);
  background: var(--danger-soft, rgba(238, 92, 92, 0.12));
  color: #ffb7b7;
  font-size: 12px;
}

.history-alert-copy {
  display: flex;
  align-items: center;
  gap: 8px;
}

.history-loading {
  min-height: 220px;
  display: flex;
   flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--text-secondary, #a9b0ba);
  font-size: 13px;
}

/* Preview Area */
.history-preview {
  flex: 1 1 auto;
  min-height: 240px;
  max-height: 52dvh;
  overflow-y: auto;
  margin: 14px 22px;
  border: 1px solid var(--line-default, rgba(255, 255, 255, 0.1));
  border-radius: 12px;
  background: rgba(7, 9, 11, 0.55);
  line-height: 1.6;
}

.history-summary-bar {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
  padding: 10px 14px;
  background: rgba(18, 22, 20, 0.94);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.07));
}

.history-summary-badges {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.version-badge {
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.08);
  color: var(--text-primary, #f4f6f8);
  font-size: 11px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 10.5px;
  font-weight: 650;
  text-transform: capitalize;
}

.status-badge.current {
  background: rgba(73, 169, 120, 0.16);
  border: 1px solid rgba(73, 169, 120, 0.3);
  color: #7fd1a5;
}

.status-badge.snapshot {
  background: rgba(235, 101, 84, 0.13);
  border: 1px solid rgba(235, 101, 84, 0.28);
  color: #ff9c90;
}

.status-badge.workflow {
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.08));
  color: var(--text-secondary, #a9b0ba);
}

.summary-timestamp {
  font-size: 11px;
  color: var(--text-tertiary, #737c88);
}

.history-summary-right {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.summary-metrics {
  font-size: 11.5px;
  color: var(--text-secondary, #a9b0ba);
  font-variant-numeric: tabular-nums;
}

.view-segmented {
  display: inline-flex;
  padding: 2px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.08));
}

.view-segmented button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 8px;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--text-secondary, #a9b0ba);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.12s ease;
}

.view-segmented button:hover {
  color: var(--text-primary, #ffffff);
}

.view-segmented button.active {
  background: var(--panel-3, #252c35);
  color: #ffffff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
}

.copy-source-button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 9px;
  border-radius: 6px;
  border: 1px solid var(--line-default, rgba(255, 255, 255, 0.12));
  background: rgba(255, 255, 255, 0.05);
  color: var(--text-primary, #f4f6f8);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}

.copy-source-button:hover {
  background: rgba(255, 255, 255, 0.1);
}

/* Source Screenplay View */
.history-source-well {
  padding: 18px 20px;
}

.history-source-well pre {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-primary, #f4f6f8);
}

/* Saved Shots View */
.history-shots-list {
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.history-empty-shots {
  padding: 40px 20px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 6px;
  color: var(--text-tertiary, #737c88);
}

.history-empty-shots strong {
  color: var(--text-primary, #f4f6f8);
  font-size: 13px;
}

.history-empty-shots span {
  font-size: 12px;
  max-width: 360px;
}

.history-shot {
  padding: 14px 16px;
  border-radius: 10px;
  border: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.075));
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.015));
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.history-shot-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.history-shot-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.shot-index-pill {
  padding: 2px 6px;
  border-radius: 5px;
  background: rgba(235, 101, 84, 0.14);
  color: var(--accent, #eb6554);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 10.5px;
  font-weight: 700;
}

.history-shot h3 {
  margin: 0;
  font-size: 13.5px;
  font-weight: 650;
  color: var(--text-primary, #f4f6f8);
}

.history-shot-badges {
  display: flex;
  align-items: center;
  gap: 6px;
}

.shot-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.08));
  color: var(--text-secondary, #a9b0ba);
  font-size: 10.5px;
  font-weight: 500;
}

.shot-chip.duration {
  font-variant-numeric: tabular-nums;
}

.history-shot-action {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--text-primary, #eff2f0);
  overflow-wrap: anywhere;
  word-break: break-word;
}

.history-dialogue-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 12px;
  border-left: 2px solid rgba(235, 101, 84, 0.5);
  border-radius: 0 7px 7px 0;
  background: rgba(255, 255, 255, 0.025);
}

.history-dialogue-line {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--text-primary, #f4f6f8);
  overflow-wrap: anywhere;
  word-break: break-word;
}

.history-dialogue-line strong {
  color: #ffb4a8;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
  letter-spacing: 0.03em;
  text-transform: uppercase;
}

.history-shot-sound {
  margin: 0;
  padding: 6px 10px;
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-secondary, #a9b0ba);
  font-size: 11.5px;
  overflow-wrap: anywhere;
  word-break: break-word;
}

.history-shot-sound strong {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-tertiary, #737c88);
}

/* Collapsible Production Prompts */
.history-prompts {
  border-top: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.065));
  padding-top: 8px;
}

.history-prompts summary {
  list-style: none;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  margin-left: -8px;
  border-radius: 6px;
  color: var(--text-secondary, #a9b0ba);
  font-size: 11.5px;
  font-weight: 600;
  cursor: pointer;
  user-select: none;
  outline: none;
  transition: color 0.15s ease, background-color 0.15s ease;
}

.history-prompts summary::-webkit-details-marker {
  display: none;
}

.history-prompts summary:hover {
  color: var(--text-primary, #ffffff);
  background: rgba(255, 255, 255, 0.05);
}

.history-prompts summary:focus-visible {
  color: var(--text-primary, #ffffff);
  background: rgba(255, 255, 255, 0.07);
  box-shadow: 0 0 0 1px var(--line-emphasis, rgba(255, 255, 255, 0.22));
}

.prompts-chevron {
  transition: transform 0.15s ease;
}

.history-prompts[open] .prompts-chevron {
  transform: rotate(180deg);
}

.history-prompts-grid {
  margin-top: 8px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 8px;
}

.history-prompts-grid p {
  min-width: 0;
  margin: 0;
  padding: 8px 10px;
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.32);
  border: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.06));
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--text-secondary, #a9b0ba);
  overflow-wrap: anywhere;
  word-break: break-word;
}

.history-prompts-grid strong {
  color: var(--text-primary, #f4f6f8);
  font-size: 10.5px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

/* Footer */
.history-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
  padding: 14px 22px 18px;
  border-top: 1px solid var(--line-subtle, rgba(255, 255, 255, 0.07));
  background: var(--surface-1, #111419);
}

.history-footer-note {
  flex: 1;
  min-width: 240px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.footer-status-hint {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-secondary, #a9b0ba);
}

.history-footer-note p {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.45;
  color: var(--text-tertiary, #737c88);
}

.history-restore-button {
  flex-shrink: 0;
  height: 36px;
  padding: 0 14px;
  border-radius: 8px;
}

.spinning {
  animation: history-spin 1.1s linear infinite;
}

@keyframes history-spin {
  to {
    transform: rotate(360deg);
  }
}

button:focus-visible,
select:focus-visible,
.history-preview:focus-visible {
  outline: 2px solid var(--accent, #eb6554);
  outline-offset: 2px;
}

@media (max-width: 640px) {
  .history-controls {
    grid-template-columns: 1fr;
  }
  .history-header,
  .history-controls,
  .version-pills-bar,
  .history-footer {
    padding-left: 16px;
    padding-right: 16px;
  }
  .history-preview {
    margin: 10px 16px;
  }
}
</style>
