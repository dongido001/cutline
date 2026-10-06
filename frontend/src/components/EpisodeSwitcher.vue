<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { Check, ChevronDown, Clapperboard, Copy, Pencil, Plus, Settings, X } from '@lucide/vue'
import { useStudioStore } from '@/stores/studio'

const emit = defineEmits<{
  (e: 'open-settings'): void
  (e: 'open-new-episode'): void
}>()

const store = useStudioStore()
const isOpen = ref(false)
const dropdownRef = ref<HTMLElement | null>(null)

const editingEpisodeId = ref<string | null>(null)
const editingTitle = ref('')
const savingId = ref<string | null>(null)
const renameInputRef = ref<HTMLInputElement | null>(null)
const copiedEpisodeId = ref<string | null>(null)
let copiedTimer: ReturnType<typeof setTimeout> | null = null

const episodeLabel = computed(() => store.episodeLabel || 'Episode')

const currentEpisodeDisplay = computed(() => {
  if (!store.currentProject) return 'No project'
  const ep = store.currentEpisode
  if (!ep) return `${episodeLabel.value} 1`
  return `${episodeLabel.value} ${ep.episode_number}${ep.title && ep.title !== `Episode ${ep.episode_number}` ? ` · ${ep.title}` : ''}`
})

function toggleDropdown() {
  isOpen.value = !isOpen.value
  if (!isOpen.value) {
    cancelEditing()
  }
}

function closeDropdown() {
  isOpen.value = false
  cancelEditing()
}

async function handleSelectEpisode(episodeId: string) {
  closeDropdown()
  if (episodeId === store.currentEpisodeId) return
  await store.selectEpisode(episodeId)
}

function handleOpenSettings() {
  closeDropdown()
  emit('open-settings')
}

function handleOpenNewEpisode() {
  closeDropdown()
  emit('open-new-episode')
}

async function copyEpisodeTitle(ep: { id: string; title: string | null; episode_number: number }) {
  const title = (ep.title || `${episodeLabel.value} ${ep.episode_number}`).trim()
  if (!title) return
  await navigator.clipboard.writeText(title)
  copiedEpisodeId.value = ep.id
  if (copiedTimer) clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => {
    if (copiedEpisodeId.value === ep.id) {
      copiedEpisodeId.value = null
    }
  }, 1800)
}

function startEditing(ep: { id: string; title: string | null; episode_number: number }) {
  editingEpisodeId.value = ep.id
  editingTitle.value = ep.title || `${episodeLabel.value} ${ep.episode_number}`
  nextTick(() => {
    renameInputRef.value?.focus()
    renameInputRef.value?.select()
  })
}

function cancelEditing() {
  editingEpisodeId.value = null
  editingTitle.value = ''
}

async function saveEditing(ep: { id: string; episode_number: number }) {
  if (!editingEpisodeId.value || savingId.value) return
  const trimmed = editingTitle.value.trim()
  savingId.value = ep.id
  try {
    await store.updateEpisode(ep.id, {
      title: trimmed || `${episodeLabel.value} ${ep.episode_number}`,
    })
  } catch (err) {
    console.error('Failed to update episode title', err)
  } finally {
    savingId.value = null
    editingEpisodeId.value = null
    editingTitle.value = ''
  }
}

function handleClickOutside(event: MouseEvent) {
  if (dropdownRef.value && !dropdownRef.value.contains(event.target as Node)) {
    closeDropdown()
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

onUnmounted(() => {
  document.removeEventListener('click', handleClickOutside)
  if (copiedTimer) clearTimeout(copiedTimer)
})
</script>

<template>
  <div ref="dropdownRef" class="episode-switcher" :class="{ 'is-open': isOpen }">
    <button
      type="button"
      class="episode-switcher-trigger"
      :class="{ 'is-open': isOpen }"
      :title="'Switch ' + episodeLabel"
      @click="toggleDropdown"
    >
      <Clapperboard :size="13" class="episode-trigger-icon" />
      <span class="episode-switcher-label">{{ currentEpisodeDisplay }}</span>
      <ChevronDown :size="13" class="switcher-arrow" />
    </button>

    <div v-if="isOpen" class="episode-dropdown-menu">
      <div class="episode-menu-header">
        <span>{{ episodeLabel }}s ({{ store.episodes.length }})</span>
        <button
          type="button"
          class="episode-settings-icon-btn"
          title="Project settings & label"
          @click="handleOpenSettings"
        >
          <Settings :size="13" />
        </button>
      </div>

      <div class="episode-menu-list">
        <div
          v-for="ep in store.episodes"
          :key="ep.id"
          class="episode-menu-item"
          :class="{ 'is-active': ep.id === store.currentEpisodeId, 'is-editing': editingEpisodeId === ep.id }"
          @click="editingEpisodeId !== ep.id && handleSelectEpisode(ep.id)"
        >
          <!-- Editing Mode -->
          <div v-if="editingEpisodeId === ep.id" class="episode-rename-row" @click.stop>
            <span class="episode-badge">{{ episodeLabel }} {{ ep.episode_number }}</span>
            <input
              ref="renameInputRef"
              v-model="editingTitle"
              type="text"
              class="episode-rename-input"
              :placeholder="`${episodeLabel} ${ep.episode_number} title...`"
              :disabled="savingId === ep.id"
              @keydown.enter.prevent="saveEditing(ep)"
              @keydown.esc.prevent="cancelEditing"
            />
            <button
              type="button"
              class="episode-rename-btn save-btn"
              title="Save title (Enter)"
              :disabled="savingId === ep.id"
              @click.stop="saveEditing(ep)"
            >
              <Check :size="13" />
            </button>
            <button
              type="button"
              class="episode-rename-btn cancel-btn"
              title="Cancel (Esc)"
              :disabled="savingId === ep.id"
              @click.stop="cancelEditing"
            >
              <X :size="13" />
            </button>
          </div>

          <!-- Normal Display Mode -->
          <template v-else>
            <div class="episode-item-main">
              <div class="episode-item-headline">
                <span class="episode-badge">{{ episodeLabel }} {{ ep.episode_number }}</span>
                <strong class="episode-item-title">{{ ep.title || `${episodeLabel} ${ep.episode_number}` }}</strong>
              </div>
              <div class="episode-item-meta">
                <span>{{ ep.shot_count }} shots</span>
                <span>·</span>
                <span class="episode-status-tag" :class="ep.status">{{ ep.status.replace('_', ' ') }}</span>
              </div>
            </div>
            <div class="episode-item-actions">
              <button
                type="button"
                class="episode-edit-icon-btn"
                :class="{ 'is-copied': copiedEpisodeId === ep.id }"
                :title="copiedEpisodeId === ep.id ? 'Title copied' : 'Copy episode title'"
                :aria-label="copiedEpisodeId === ep.id ? 'Title copied' : `Copy ${ep.title || `${episodeLabel} ${ep.episode_number}`} title`"
                @click.stop="copyEpisodeTitle(ep)"
              >
                <Check v-if="copiedEpisodeId === ep.id" :size="12" />
                <Copy v-else :size="12" />
              </button>
              <button
                type="button"
                class="episode-edit-icon-btn"
                title="Rename episode"
                :aria-label="`Rename ${ep.title || `${episodeLabel} ${ep.episode_number}`}`"
                @click.stop="startEditing(ep)"
              >
                <Pencil :size="12" />
              </button>
              <Check v-if="ep.id === store.currentEpisodeId" :size="15" class="episode-check-icon" />
            </div>
          </template>
        </div>
      </div>

      <div class="episode-menu-footer">
        <button type="button" class="add-episode-btn" @click="handleOpenNewEpisode">
          <Plus :size="14" />
          <span>New {{ episodeLabel }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.episode-switcher {
  position: relative;
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  z-index: 10;
}

.episode-switcher.is-open {
  z-index: 1000;
}

.episode-switcher-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #eff2f0;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
  line-height: 1;
  flex-shrink: 0;
}

.episode-switcher-trigger:hover,
.episode-switcher-trigger.is-open {
  background: rgba(255, 255, 255, 0.09);
  border-color: rgba(255, 255, 255, 0.22);
  color: #fff;
}

.episode-trigger-icon {
  color: #9ba39e;
  flex-shrink: 0;
}

.episode-switcher-label {
  max-width: 240px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.switcher-arrow {
  color: var(--muted);
  transition: transform 0.15s ease;
  flex-shrink: 0;
}

.episode-switcher-trigger.is-open .switcher-arrow {
  transform: rotate(180deg);
}

.episode-dropdown-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  min-width: 300px;
  max-width: 380px;
  background: #141715;
  border: 1px solid rgba(255, 255, 255, 0.13);
  border-radius: 12px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.55);
  z-index: 1000;
  overflow: hidden;
  animation: dropIn 0.15s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes dropIn {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.episode-menu-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
}

.episode-settings-icon-btn {
  background: transparent;
  border: 0;
  color: var(--muted);
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
}

.episode-settings-icon-btn:hover {
  color: #fff;
  background: rgba(255, 255, 255, 0.08);
}

.episode-menu-list {
  max-height: 280px;
  overflow-y: auto;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.episode-menu-item {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  border-radius: 8px;
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s ease;
}

.episode-menu-item:hover {
  background: rgba(255, 255, 255, 0.05);
}

.episode-menu-item.is-active {
  background: rgba(73, 169, 120, 0.12);
  border-color: rgba(73, 169, 120, 0.28);
}

.episode-menu-item.is-editing {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.15);
  cursor: default;
}

.episode-item-main {
  flex: 1;
  min-width: 0;
}

.episode-item-headline {
  display: flex;
  align-items: center;
  gap: 6px;
}

.episode-badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
  color: #c5ccc7;
  font-weight: 600;
  white-space: nowrap;
}

.episode-item-title {
  font-size: 12px;
  color: #eff2f0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.episode-item-meta {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}

.episode-status-tag {
  text-transform: capitalize;
}

.episode-status-tag.ready,
.episode-status-tag.shot_plan {
  color: #7fd1a5;
}

.episode-item-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.episode-edit-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  background: transparent;
  border: 1px solid transparent;
  color: var(--muted);
  cursor: pointer;
  transition: all 0.15s ease;
  opacity: 0.6;
}

.episode-menu-item:hover .episode-edit-icon-btn {
  opacity: 1;
}

.episode-edit-icon-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.18);
  color: #fff;
}

.episode-edit-icon-btn.is-copied {
  opacity: 1;
  color: #7fd1a5;
  background: rgba(73, 169, 120, 0.15);
  border-color: rgba(73, 169, 120, 0.35);
}

.episode-check-icon {
  color: #7fd1a5;
  flex-shrink: 0;
}

.episode-rename-row {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
}

.episode-rename-input {
  flex: 1;
  min-width: 0;
  height: 26px;
  padding: 0 8px;
  font-size: 12px;
  background: rgba(0, 0, 0, 0.4);
  border: 1px solid var(--accent);
  border-radius: 4px;
  color: #fff;
  outline: none;
}

.episode-rename-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 4px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.08);
  color: var(--muted);
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.12s ease;
}

.episode-rename-btn.save-btn {
  color: #7fd1a5;
  background: rgba(73, 169, 120, 0.15);
  border-color: rgba(73, 169, 120, 0.35);
}

.episode-rename-btn.save-btn:hover:not(:disabled) {
  background: rgba(73, 169, 120, 0.3);
  color: #fff;
}

.episode-rename-btn.cancel-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.15);
  color: #fff;
}

.episode-rename-btn:disabled {
  opacity: 0.5;
  cursor: wait;
}

.episode-menu-footer {
  padding: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(0, 0, 0, 0.25);
}

.add-episode-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 7px 12px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: #eff2f0;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.add-episode-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.2);
}
</style>
