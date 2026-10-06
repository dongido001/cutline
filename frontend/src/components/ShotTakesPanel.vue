<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { AspectRatio, Shot, ShotTake } from '@/lib/api'
import { useStudioStore } from '@/stores/studio'
import {
  Check,
  Clapperboard,
  Clock,
  ExternalLink,
  Film,
  Image as ImageIcon,
  MessageSquare,
  RefreshCw,
  Video,
} from '@lucide/vue'

const props = defineProps<{
  shot: Shot
  aspect: AspectRatio
}>()

const store = useStudioStore()
const activeTab = ref<'clip' | 'image'>('clip')
const selectingTakeId = ref<string | null>(null)
const previewHoverTakeId = ref<string | null>(null)
const brokenTakeIds = ref<Set<string>>(new Set())

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

// Auto-select tab based on available takes
watch(
  () => [props.shot.id, props.aspect],
  () => {
    brokenTakeIds.value = new Set()
    if (videoTakes.value.length > 0) {
      activeTab.value = 'clip'
    } else if (imageTakes.value.length > 0) {
      activeTab.value = 'image'
    } else {
      activeTab.value = 'clip'
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
  }
  return map[provider] || provider || 'AI Generated'
}

async function handleSelectTake(take: ShotTake) {
  if (take.selected || selectingTakeId.value) return
  selectingTakeId.value = take.id
  try {
    if (props.shot.takes) {
      for (const t of props.shot.takes) {
        if (t.media_type === take.media_type && t.aspect_ratio === take.aspect_ratio) {
          t.selected = (t.id === take.id)
        }
      }
    }
    await store.selectShotTake(props.shot.id, take.id)
  } catch (err) {
    console.error('Failed to select take:', err)
  } finally {
    selectingTakeId.value = null
  }
}
</script>

<template>
  <section v-if="videoTakes.length || imageTakes.length" class="shot-takes-panel" aria-label="Shot Takes History">
    <header class="takes-panel-header">
      <div class="takes-heading">
        <Clapperboard :size="16" class="takes-icon" />
        <strong>Takes & History</strong>
        <span class="aspect-badge">{{ aspect }}</span>
      </div>

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
    </header>

    <div v-if="activeTakesList.length" class="takes-grid">
      <article
        v-for="take in activeTakesList"
        :key="take.id"
        class="take-card"
        :class="{
          selected: take.selected,
          processing: selectingTakeId === take.id,
        }"
        @click="handleSelectTake(take)"
      >
        <div class="take-thumb-wrapper">
          <video
            v-if="take.media_type === 'clip'"
            :src="take.url"
            muted
            playsinline
            preload="metadata"
            class="take-media"
            @mouseenter="previewHoverTakeId = take.id"
            @mouseleave="previewHoverTakeId = null"
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

          <button
            type="button"
            class="take-select-btn"
            :class="{ active: take.selected }"
            :disabled="take.selected || selectingTakeId === take.id"
            @click.stop="handleSelectTake(take)"
          >
            <Check v-if="take.selected" :size="13" />
            <span>{{ take.selected ? 'Current take' : 'Select take' }}</span>
          </button>
        </div>
      </article>
    </div>

    <div v-else class="empty-takes-state">
      <Film :size="22" />
      <span>No {{ activeTab === 'clip' ? 'video' : 'image' }} takes yet for {{ aspect }}.</span>
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
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
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
  gap: 8px;
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

.take-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
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

.take-select-btn {
  width: 100%;
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

.empty-takes-state {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: var(--text-secondary, #94a3b8);
  font-size: 12px;
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
