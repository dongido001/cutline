<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Check, ChevronDown, Clapperboard, Plus, Settings } from '@lucide/vue'
import { useStudioStore } from '@/stores/studio'

const emit = defineEmits<{
  (e: 'open-settings'): void
  (e: 'open-new-episode'): void
}>()

const store = useStudioStore()
const isOpen = ref(false)
const dropdownRef = ref<HTMLElement | null>(null)

const episodeLabel = computed(() => store.episodeLabel || 'Episode')

const currentEpisodeDisplay = computed(() => {
  if (!store.currentProject) return 'No project'
  const ep = store.currentEpisode
  if (!ep) return `${episodeLabel.value} 1`
  return `${episodeLabel.value} ${ep.episode_number}${ep.title && ep.title !== `Episode ${ep.episode_number}` ? ` · ${ep.title}` : ''}`
})

function toggleDropdown() {
  isOpen.value = !isOpen.value
}

function closeDropdown() {
  isOpen.value = false
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
})
</script>

<template>
  <div ref="dropdownRef" class="episode-switcher">
    <button
      type="button"
      class="episode-switcher-trigger"
      :class="{ 'is-open': isOpen }"
      :title="'Switch ' + episodeLabel"
      @click="toggleDropdown"
    >
      <Clapperboard :size="13" />
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
        <button
          v-for="ep in store.episodes"
          :key="ep.id"
          type="button"
          class="episode-menu-item"
          :class="{ 'is-active': ep.id === store.currentEpisodeId }"
          @click="handleSelectEpisode(ep.id)"
        >
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
          <Check v-if="ep.id === store.currentEpisodeId" :size="15" class="episode-check-icon" />
        </button>
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
}

.episode-switcher-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #fff;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.episode-switcher-trigger:hover,
.episode-switcher-trigger.is-open {
  background: rgba(255, 255, 255, 0.09);
  border-color: rgba(255, 255, 255, 0.22);
}

.episode-switcher-label {
  max-width: 220px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.switcher-arrow {
  color: var(--muted);
  transition: transform 0.15s ease;
}

.episode-switcher-trigger.is-open .switcher-arrow {
  transform: rotate(180deg);
}

.episode-dropdown-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  min-width: 280px;
  max-width: 360px;
  background: #141715;
  border: 1px solid rgba(255, 255, 255, 0.13);
  border-radius: 12px;
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.55);
  z-index: 200;
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

.episode-check-icon {
  color: #7fd1a5;
  margin-left: 8px;
  flex-shrink: 0;
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
