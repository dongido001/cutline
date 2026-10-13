<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Activity, Bot, Clapperboard, Command, Film, MessageSquareText, Moon, RefreshCw, Search, Settings, Sun } from '@lucide/vue'
import { UiBadge, UiButton, UiCard, UiDialog, UiIconButton, UiInput, UiTooltip } from '@/components/ui'
import { useStudioStore } from '@/stores/studio'
import { initTheme, setTheme, type ThemeMode } from '@/lib/theme'
import { api, DEFAULT_FLOW_PRESETS } from '@/lib/api'

const router = useRouter()
const store = useStudioStore()
const showCommands = ref(false)
const showSettings = ref(false)
const commandQuery = ref('')
const refreshing = ref(false)
const theme = ref<ThemeMode>(initTheme())
const activeQueueJobs = ref(0)
let queuePollTimer: number | undefined

function chooseTheme(next: ThemeMode) {
  theme.value = next
  setTheme(next)
}

interface CommandItem {
  label: string
  to?: string
  stage?: string
  icon: any
}

const commands = computed(() => {
  const project = store.currentProject
  const items: CommandItem[] = [
    { label: 'Background Queues & Tasks', to: '/queues', icon: Activity },
    ...(project ? [
      { label: 'Script', stage: 'script', icon: MessageSquareText },
      { label: 'Produce', stage: 'shots', icon: Clapperboard },
      { label: 'Assemble', stage: 'final-cut', icon: Film },
    ] : []),
  ]
  const query = commandQuery.value.trim().toLowerCase()
  return query ? items.filter((item) => item.label.toLowerCase().includes(query)) : items
})

function openCommands() {
  showSettings.value = false
  showCommands.value = true
  commandQuery.value = ''
}

function openSettings() {
  showCommands.value = false
  showSettings.value = true
}

async function runCommand(item: CommandItem) {
  showCommands.value = false
  if (item.to) {
    await router.push(item.to)
    return
  }
  const project = store.currentProject
  if (!project || !item.stage) return
  await router.push({ name: 'project-stage', params: { projectId: project.id, stage: item.stage } })
}

async function refreshProviders() {
  refreshing.value = true
  try {
    await store.initialize(store.currentProject?.id)
  } finally {
    refreshing.value = false
  }
}

const resettingFlow = ref(false)
const switchingFlowModel = ref(false)
const flowSyncMessage = ref('')

async function resetFlowSession() {
  resettingFlow.value = true
  flowSyncMessage.value = ''
  try {
    const res = await api.resetFlowSession()
    flowSyncMessage.value = res.message || 'Flow account synced!'
    await store.initialize(store.currentProject?.id)
  } catch (err: any) {
    flowSyncMessage.value = err?.message || 'Failed to sync Flow account'
  } finally {
    resettingFlow.value = false
  }
}

async function selectFlowModel(presetId: string) {
  if (!presetId || switchingFlowModel.value) return
  switchingFlowModel.value = true
  flowSyncMessage.value = ''
  try {
    store.videoProvider = await api.setFlowVideoModel(presetId)
    flowSyncMessage.value = `Switched video model to ${store.videoProvider.model}`
  } catch (err: any) {
    flowSyncMessage.value = err?.message || 'Failed to switch video model'
  } finally {
    switchingFlowModel.value = false
  }
}

async function checkQueueStats() {
  try {
    const status = await api.systemQueues()
    activeQueueJobs.value = status.active_jobs
    const videoBridge = status.bridges?.find((b) => b.id === 'video_provider')
    if (videoBridge && (!store.videoProvider || store.videoProvider.available !== videoBridge.available)) {
      store.videoProvider = await api.videoProvider()
    }
  } catch {
    // ignore background errors
  }
}

function handleKeyboard(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    if (showCommands.value) {
      showCommands.value = false
    } else {
      openCommands()
    }
  } else if (event.key === 'Escape') {
    showCommands.value = false
    showSettings.value = false
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeyboard)
  void checkQueueStats()
  queuePollTimer = window.setInterval(() => {
    void checkQueueStats()
  }, 8000)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeyboard)
  if (queuePollTimer !== undefined) {
    window.clearInterval(queuePollTimer)
  }
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink class="brand" to="/">
        <span class="brand-mark"><Clapperboard :size="18" stroke-width="2.2" /></span>
        <span>Cutline</span>
      </RouterLink>
      <div class="topbar-meta">
        <RouterLink
          to="/queues"
          class="topbar-nav-button"
          :class="{ active: router.currentRoute.value.path === '/queues' }"
          title="Manage background task queues & bridges"
        >
          <Activity :size="14" />
          <span>Queues</span>
          <span v-if="activeQueueJobs > 0" class="topbar-queue-badge">{{ activeQueueJobs }}</span>
        </RouterLink>

        <UiBadge tone="success"><span class="status-dot"></span> Local workspace</UiBadge>
        <UiTooltip text="Command menu (⌘K)">
          <UiIconButton label="Open command menu" :active="showCommands" :aria-expanded="showCommands" @click="openCommands"><Command :size="17" /></UiIconButton>
        </UiTooltip>
        <UiTooltip text="Settings">
          <UiIconButton label="Open settings" :active="showSettings" :aria-expanded="showSettings" @click="openSettings"><Settings :size="17" /></UiIconButton>
        </UiTooltip>
      </div>
    </header>
    <RouterView />

    <div v-if="showCommands" class="utility-backdrop" @click.self="showCommands = false">
      <section class="command-palette" role="dialog" aria-modal="true" aria-labelledby="command-title">
        <header><Search :size="17" /><UiInput v-model="commandQuery" autofocus placeholder="Jump to page or project stage..." aria-label="Search commands" class="command-input" /><kbd>ESC</kbd></header>
        <div class="command-heading"><span id="command-title">{{ store.currentProject?.title || 'Global & Navigation' }}</span><small>Quick Navigation</small></div>
        <button v-for="item in commands" :key="item.label" @click="runCommand(item)"><component :is="item.icon" :size="16" /><span>{{ item.label }}</span><kbd>↵</kbd></button>
        <p v-if="!commands.length">No matching command</p>
      </section>
    </div>

    <UiDialog v-model:open="showSettings" title="Provider settings" description="Local workspace connections used by Cutline generation workflows." width-class="max-w-2xl">
      <div class="settings-section-heading"><span>Appearance</span></div>
      <div class="theme-toggle" role="group" aria-label="Color theme">
        <button type="button" :class="{ active: theme === 'dark' }" :aria-pressed="theme === 'dark'" @click="chooseTheme('dark')">
          <Moon :size="14" /> Dark
        </button>
        <button type="button" :class="{ active: theme === 'light' }" :aria-pressed="theme === 'light'" @click="chooseTheme('light')">
          <Sun :size="14" /> Light
        </button>
      </div>
      <div class="settings-section-heading"><span>Text providers</span><UiButton size="sm" variant="ghost" :disabled="refreshing" @click="refreshProviders"><RefreshCw :class="{ spin: refreshing }" :size="14" />Refresh</UiButton></div>
      <div class="provider-settings-list">
        <UiCard v-for="provider in store.providers" :key="provider.id" class="provider-setting-card"><span class="provider-status-dot" :class="{ ready: provider.available }"></span><div><strong>{{ provider.label }}</strong><small>{{ provider.model }} · {{ provider.detail }}</small></div><UiBadge :tone="provider.available ? 'success' : provider.configured ? 'danger' : 'neutral'">{{ provider.available ? 'Connected' : provider.configured ? 'Offline' : 'Not configured' }}</UiBadge></UiCard>
      </div>
      <div class="settings-section-heading"><span>Image providers</span></div>
      <div class="provider-settings-list">
        <UiCard v-for="provider in store.imageProviders" :key="`image-${provider.id}`" class="provider-setting-card"><span class="provider-status-dot" :class="{ ready: provider.verified, connected: provider.connected }"></span><div><strong>{{ provider.priority }}. {{ provider.label }}</strong><small>{{ provider.model }} · {{ provider.detail }}</small></div><UiBadge :tone="provider.verified ? 'success' : provider.connected ? 'accent' : provider.configured ? 'danger' : 'neutral'">{{ provider.verified ? 'Verified' : provider.connected ? 'Connected' : provider.configured ? 'Offline' : 'Not configured' }}</UiBadge></UiCard>
      </div>
      <div class="settings-section-heading">
        <span>Video provider</span>
        <UiButton v-if="store.videoProvider" size="sm" variant="ghost" :disabled="resettingFlow" @click="resetFlowSession">
          <RefreshCw :class="{ spin: resettingFlow }" :size="14" />
          {{ resettingFlow ? 'Syncing Flow…' : 'Switch Account / Sync Flow' }}
        </UiButton>
      </div>
      <UiCard v-if="store.videoProvider" class="video-provider-setting provider-setting-card"><Bot :size="17" /><div><strong>{{ store.videoProvider.label }}</strong><small>{{ flowSyncMessage || store.videoProvider.detail }}</small></div><UiBadge :tone="store.videoProvider.available ? 'success' : store.videoProvider.configured ? 'danger' : 'neutral'">{{ store.videoProvider.available ? 'Connected' : store.videoProvider.configured ? 'Offline' : 'Not configured' }}</UiBadge></UiCard>
      <div v-if="store.videoProvider" class="flow-model-presets">
        <button
          v-for="preset in (store.videoProvider.presets?.length ? store.videoProvider.presets : DEFAULT_FLOW_PRESETS)"
          :key="preset.id"
          type="button"
          class="flow-model-preset-btn"
          :class="{ active: (store.videoProvider.active_preset || 'veo_lite') === preset.id }"
          :disabled="switchingFlowModel"
          @click="selectFlowModel(preset.id)"
        >
          <strong>{{ preset.label }}</strong>
          <small>{{ preset.credit_note }}</small>
        </button>
      </div>
      <p v-else class="settings-empty">Video provider status is unavailable.</p>
    </UiDialog>
  </div>
</template>

<style scoped>
.topbar-nav-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 11px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 550;
  color: #cbd1ce;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.09));
  text-decoration: none;
  transition: all 0.16s ease;
}

.topbar-nav-button:hover {
  color: #ffffff;
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.18);
}

.topbar-nav-button.active {
  color: #ffffff;
  background: rgba(99, 102, 241, 0.14);
  border-color: rgba(99, 102, 241, 0.4);
}

.topbar-queue-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 17px;
  height: 17px;
  padding: 0 4px;
  font-size: 10px;
  font-weight: 700;
  border-radius: 9999px;
  background: #6366f1;
  color: #ffffff;
  box-shadow: 0 0 10px rgba(99, 102, 241, 0.6);
  animation: pulse-glow 2s infinite ease-in-out;
}

@keyframes pulse-glow {
  0%, 100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.08);
    opacity: 0.85;
  }
}

.flow-model-presets {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  margin-top: 8px;
}

.flow-model-preset-btn {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid var(--workspace-border, rgba(255, 255, 255, 0.1));
  background: rgba(255, 255, 255, 0.03);
  color: inherit;
  text-align: left;
  cursor: pointer;
  transition: all 0.15s ease;
}

.flow-model-preset-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.22);
}

.flow-model-preset-btn.active {
  background: rgba(99, 102, 241, 0.15);
  border-color: rgba(99, 102, 241, 0.55);
}

.flow-model-preset-btn strong {
  font-size: 12.5px;
  font-weight: 600;
}

.flow-model-preset-btn small {
  font-size: 11px;
  opacity: 0.72;
}
</style>

