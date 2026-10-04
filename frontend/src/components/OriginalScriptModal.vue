<script setup lang="ts">
import { ref } from 'vue'
import { Check, Copy, X } from '@lucide/vue'

const props = defineProps<{
  show: boolean
  sourceText: string
  episodeTitle: string
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const copied = ref(false)

async function copyScript() {
  if (!props.sourceText) return
  await navigator.clipboard.writeText(props.sourceText)
  copied.value = true
  setTimeout(() => {
    copied.value = false
  }, 2000)
}
</script>

<template>
  <div v-if="show" class="modal-backdrop" @click.self="emit('close')">
    <div class="project-modal original-script-modal" role="dialog" aria-modal="true" aria-labelledby="original-script-title">
      <header>
        <div>
          <span class="section-label">Source Screenplay</span>
          <h2 id="original-script-title">Original Script · {{ episodeTitle }}</h2>
        </div>
        <button type="button" class="mini-icon" aria-label="Close" @click="emit('close')">
          <X :size="17" />
        </button>
      </header>

      <div class="original-script-content">
        <pre class="original-script-pre">{{ sourceText }}</pre>
      </div>

      <footer>
        <button type="button" class="secondary-button" @click="copyScript">
          <Check v-if="copied" :size="14" />
          <Copy v-else :size="14" />
          <span>{{ copied ? 'Copied to Clipboard' : 'Copy Script' }}</span>
        </button>
        <button type="button" class="primary-button" @click="emit('close')">
          <span>Close</span>
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.original-script-modal {
  max-width: 760px;
  width: 90vw;
}

.original-script-content {
  margin: 16px 0;
  max-height: 60vh;
  overflow-y: auto;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.45);
  border: 1px solid var(--line-subtle);
  padding: 16px;
}

.original-script-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
}
</style>
