<script setup lang="ts">
import { AudioLines, LoaderCircle } from '@lucide/vue'
import type { StemSeparatorId, StemSeparatorStatus } from '@/lib/api'

defineProps<{
  separators: StemSeparatorStatus[]
  busy: boolean
  status: string
  error: string
}>()
const provider = defineModel<StemSeparatorId | ''>('provider', { required: true })
const includeVoice = defineModel<boolean>('includeVoice', { required: true })
const emit = defineEmits<{ separate: [] }>()
</script>

<template>
  <div class="stem-separation-controls">
    <div class="trim-heading"><span><AudioLines :size="13" />Separate music</span></div>
    <label>Separator
      <select v-model="provider" :disabled="busy" aria-label="Music separator">
        <option v-for="separator in separators" :key="separator.id" :value="separator.id" :disabled="!separator.available">
          {{ separator.label }}{{ separator.available ? '' : ' (not set up)' }}
        </option>
      </select>
    </label>
    <label class="stem-voice-toggle"><input v-model="includeVoice" type="checkbox" :disabled="busy" />Also add voice</label>
    <button type="button" class="inspector-action-btn primary-action-btn" :disabled="busy || !provider" @click="emit('separate')">
      <LoaderCircle v-if="busy" :size="13" class="spin" /><AudioLines v-else :size="13" />
      <span>{{ busy ? 'Separating…' : 'Separate music' }}</span>
    </button>
    <p v-if="status" class="stem-separation-status">{{ status }}</p>
    <p v-if="error" class="stem-separation-status error">{{ error }}</p>
  </div>
</template>

<style scoped>
.trim-heading {
  display: flex;
  align-items: center;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  color: var(--text-secondary);
}

.trim-heading span {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

label {
  display: grid;
  gap: 5px;
  margin: 10px 0;
  font-size: 11px;
  font-weight: 650;
  color: var(--muted);
}

select {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 9px;
  background: var(--input-bg);
  color: var(--ink);
  font-size: 12px;
}

select:focus {
  outline: none;
  border-color: var(--accent);
}

.stem-voice-toggle {
  display: flex;
  align-items: center;
  gap: 7px;
  cursor: pointer;
}

.stem-voice-toggle input {
  accent-color: var(--accent);
}

.inspector-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  border-radius: 6px;
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
}

.inspector-action-btn:hover:not(:disabled) {
  filter: brightness(1.1);
}

.inspector-action-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.stem-separation-status {
  margin: 8px 0 0;
  font-size: 11px;
  line-height: 1.4;
  color: var(--muted);
}

.stem-separation-status.error {
  color: #b4554d;
}

.spin {
  animation: stem-spin 0.9s linear infinite;
}

@keyframes stem-spin {
  to { transform: rotate(360deg); }
}
</style>
