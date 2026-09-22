<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Bot, Clapperboard, Command, Film, MessageSquareText, RefreshCw, Search, Settings, X } from '@lucide/vue'
import { useStudioStore } from '@/stores/studio'

const router = useRouter()
const store = useStudioStore()
const showCommands = ref(false)
const showSettings = ref(false)
const commandQuery = ref('')
const refreshing = ref(false)

const commands = computed(() => {
  const project = store.currentProject
  if (!project) return []
  const items = [
    { label: 'Script', stage: 'script', icon: MessageSquareText },
    { label: 'Produce', stage: 'shots', icon: Clapperboard },
    { label: 'Assemble', stage: 'final-cut', icon: Film },
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

async function runCommand(stage: string) {
  const project = store.currentProject
  if (!project) return
  showCommands.value = false
  await router.push({ name: 'project-stage', params: { projectId: project.id, stage } })
}

async function refreshProviders() {
  refreshing.value = true
  try {
    await store.initialize(store.currentProject?.id)
  } finally {
    refreshing.value = false
  }
}

function handleKeyboard(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    showCommands.value ? showCommands.value = false : openCommands()
  } else if (event.key === 'Escape') {
    showCommands.value = false
    showSettings.value = false
  }
}

onMounted(() => window.addEventListener('keydown', handleKeyboard))
onUnmounted(() => window.removeEventListener('keydown', handleKeyboard))
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink class="brand" to="/">
        <span class="brand-mark"><Clapperboard :size="18" stroke-width="2.2" /></span>
        <span>Cutline</span>
      </RouterLink>
      <div class="topbar-meta">
        <span class="local-state"><span class="status-dot"></span> Local workspace</span>
        <button class="icon-button" title="Command menu (⌘K)" aria-label="Open command menu" :aria-expanded="showCommands" @click="openCommands"><Command :size="17" /></button>
        <button class="icon-button" title="Settings" aria-label="Open settings" :aria-expanded="showSettings" @click="openSettings"><Settings :size="17" /></button>
      </div>
    </header>
    <RouterView />

    <div v-if="showCommands" class="utility-backdrop" @click.self="showCommands = false">
      <section class="command-palette" role="dialog" aria-modal="true" aria-labelledby="command-title">
        <header><Search :size="17" /><input v-model="commandQuery" autofocus placeholder="Go to a project stage" aria-label="Search commands" /><kbd>ESC</kbd></header>
        <div class="command-heading"><span id="command-title">{{ store.currentProject?.title || 'No project selected' }}</span><small>Navigation</small></div>
        <button v-for="item in commands" :key="item.stage" @click="runCommand(item.stage)"><component :is="item.icon" :size="16" /><span>{{ item.label }}</span><kbd>↵</kbd></button>
        <p v-if="store.currentProject && !commands.length">No matching command</p>
        <p v-else-if="!store.currentProject">Open a project to use quick navigation.</p>
      </section>
    </div>

    <div v-if="showSettings" class="utility-backdrop settings-backdrop" @click.self="showSettings = false">
      <section class="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title">
        <header><div><span>Local workspace</span><h2 id="settings-title">Provider settings</h2></div><button class="mini-icon" aria-label="Close settings" @click="showSettings = false"><X :size="17" /></button></header>
        <div class="settings-section-heading"><span>Text providers</span><button :disabled="refreshing" @click="refreshProviders"><RefreshCw :class="{ spin: refreshing }" :size="14" />Refresh</button></div>
        <div class="provider-settings-list">
          <article v-for="provider in store.providers" :key="provider.id"><span class="provider-status-dot" :class="{ ready: provider.available }"></span><div><strong>{{ provider.label }}</strong><small>{{ provider.model }} · {{ provider.detail }}</small></div><em>{{ provider.available ? 'Connected' : provider.configured ? 'Offline' : 'Not configured' }}</em></article>
        </div>
        <div class="settings-section-heading"><span>Image providers</span></div>
        <div class="provider-settings-list">
          <article v-for="provider in store.imageProviders" :key="`image-${provider.id}`"><span class="provider-status-dot" :class="{ ready: provider.verified, connected: provider.connected }"></span><div><strong>{{ provider.priority }}. {{ provider.label }}</strong><small>{{ provider.model }} · {{ provider.detail }}</small></div><em>{{ provider.verified ? 'Verified' : provider.connected ? 'Connected' : provider.configured ? 'Offline' : 'Not configured' }}</em></article>
        </div>
        <div class="settings-section-heading"><span>Video provider</span></div>
        <article v-if="store.videoProvider" class="video-provider-setting"><Bot :size="17" /><div><strong>{{ store.videoProvider.label }}</strong><small>{{ store.videoProvider.detail }}</small></div><em>{{ store.videoProvider.available ? 'Connected' : store.videoProvider.configured ? 'Offline' : 'Not configured' }}</em></article>
        <p v-else class="settings-empty">Video provider status is unavailable.</p>
      </section>
    </div>
  </div>
</template>
