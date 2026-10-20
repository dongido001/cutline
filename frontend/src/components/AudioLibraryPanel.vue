<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { LoaderCircle, Plus, Search, Trash2, X } from '@lucide/vue'
import { api, type LibraryAudio } from '@/lib/api'

const emit = defineEmits<{ add: [item: LibraryAudio]; close: [] }>()

const items = ref<LibraryAudio[]>([])
const loading = ref(true)
const error = ref('')
const query = ref('')
const adding = ref<string | null>(null)

const visible = computed(() => {
  const needle = query.value.trim().toLowerCase()
  return needle ? items.value.filter((item) => item.label.toLowerCase().includes(needle)) : items.value
})

const seconds = (ms: number) => {
  const total = Math.round(ms / 1000)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    items.value = await api.libraryAudio()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load the library'
  } finally {
    loading.value = false
  }
}

async function remove(item: LibraryAudio) {
  if (!window.confirm(`Delete “${item.label}” from the library? Timelines already using it keep their copy.`)) return
  try {
    await api.deleteLibraryAudio(item.id)
    items.value = items.value.filter((entry) => entry.id !== item.id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not delete'
  }
}

function add(item: LibraryAudio) {
  adding.value = item.id
  emit('add', item)
}

defineExpose({ done: () => { adding.value = null } })
onMounted(load)
</script>

<template>
  <div class="audio-library-panel" role="dialog" aria-label="Audio library" @pointerdown.stop @click.stop>
    <header>
      <strong>Audio library</strong>
      <button type="button" class="icon-btn" aria-label="Close library" @click="emit('close')"><X :size="13" /></button>
    </header>
    <label class="library-search"><Search :size="12" /><input v-model="query" placeholder="Search saved audio…" aria-label="Search library" /></label>
    <p v-if="loading" class="library-note"><LoaderCircle :size="12" class="spin" /> Loading…</p>
    <p v-else-if="error" class="library-note error">{{ error }}</p>
    <p v-else-if="!items.length" class="library-note">Nothing saved yet. Select a clip with sound and use “Save audio to library” in the inspector.</p>
    <ul v-else class="library-list">
      <li v-for="item in visible" :key="item.id">
        <div class="library-row">
          <span class="library-label" :title="item.label">{{ item.label }}</span>
          <small>{{ seconds(item.duration_ms) }}</small>
        </div>
        <audio :src="item.url" controls preload="none"></audio>
        <div class="library-actions">
          <button type="button" class="add-btn" :disabled="adding !== null" @click="add(item)">
            <LoaderCircle v-if="adding === item.id" :size="12" class="spin" /><Plus v-else :size="12" />Add at playhead
          </button>
          <button type="button" class="icon-btn" :aria-label="`Delete ${item.label}`" title="Delete from library" @click="remove(item)"><Trash2 :size="12" /></button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.audio-library-panel {
  position: absolute;
  z-index: 40;
  top: 100%;
  left: 0;
  display: grid;
  gap: 8px;
  width: 300px;
  max-height: 380px;
  padding: 10px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 8px;
  background: #151821;
  box-shadow: 0 14px 32px rgba(0, 0, 0, 0.5);
  color: #eef0f4;
  font-size: 11px;
  text-transform: none;
  letter-spacing: normal;
  font-weight: 500;
}

header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
}

.icon-btn {
  display: inline-flex;
  padding: 4px;
  border: 0;
  border-radius: 4px;
  background: none;
  color: inherit;
  cursor: pointer;
}

.icon-btn:hover {
  background: rgba(255, 255, 255, 0.08);
}

.library-search {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 6px;
  background: #0d0f15;
}

.library-search input {
  flex: 1;
  border: 0;
  outline: none;
  background: none;
  color: inherit;
  font-size: 11px;
}

.library-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  list-style: none;
}

.library-list li {
  display: grid;
  gap: 5px;
  padding: 7px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 6px;
}

.library-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.library-label {
  overflow: hidden;
  font-weight: 650;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.library-row small {
  color: rgba(255, 255, 255, 0.55);
}

audio {
  width: 100%;
  height: 28px;
}

.library-actions {
  display: flex;
  gap: 6px;
}

.add-btn {
  display: inline-flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 5px 8px;
  border: 1px solid var(--accent, #e8624f);
  border-radius: 5px;
  background: var(--accent, #e8624f);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
}

.add-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.library-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  color: rgba(255, 255, 255, 0.6);
  line-height: 1.4;
}

.library-note.error {
  color: #e08a80;
}

.spin {
  animation: library-spin 0.9s linear infinite;
}

@keyframes library-spin {
  to { transform: rotate(360deg); }
}
</style>
