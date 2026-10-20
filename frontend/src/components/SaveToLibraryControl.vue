<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { BookmarkPlus, LoaderCircle } from '@lucide/vue'

const props = defineProps<{
  defaultLabel: string
  busy: boolean
  status: string
  error: string
}>()
const emit = defineEmits<{ save: [label: string] }>()

const naming = ref(false)
const label = ref('')
const input = ref<HTMLInputElement | null>(null)

async function start() {
  label.value = props.defaultLabel
  naming.value = true
  await nextTick()
  input.value?.select()
}

function save() {
  const name = label.value.trim()
  if (!name) return
  emit('save', name)
  naming.value = false
}
</script>

<template>
  <div class="save-to-library">
    <button v-if="!naming" type="button" class="library-btn" :disabled="busy" title="Save the part of this audio you're using to the library, for any project" @click="start">
      <LoaderCircle v-if="busy" :size="13" class="spin" /><BookmarkPlus v-else :size="13" />
      <span>{{ busy ? 'Saving…' : 'Save audio to library' }}</span>
    </button>
    <form v-else class="library-name-form" @submit.prevent="save">
      <label>Name in library
        <input ref="input" v-model="label" aria-label="Library name" maxlength="160" @keydown.esc.prevent="naming = false" />
      </label>
      <div class="library-name-actions">
        <button type="submit" class="library-btn primary" :disabled="!label.trim()">Save</button>
        <button type="button" class="library-btn" @click="naming = false">Cancel</button>
      </div>
    </form>
    <p v-if="status" class="library-status">{{ status }}</p>
    <p v-if="error" class="library-status error">{{ error }}</p>
  </div>
</template>

<style scoped>
.save-to-library {
  margin-top: 8px;
}

.library-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: 100%;
  padding: 7px 10px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink);
  font-size: 11px;
  font-weight: 700;
  cursor: pointer;
}

.library-btn.primary {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
}

.library-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

.library-name-form label {
  display: grid;
  gap: 5px;
  font-size: 11px;
  font-weight: 650;
  color: var(--muted);
}

.library-name-form input {
  width: 100%;
  box-sizing: border-box;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 9px;
  background: var(--input-bg);
  color: var(--ink);
  font-size: 12px;
}

.library-name-actions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin-top: 8px;
}

.library-status {
  margin: 8px 0 0;
  font-size: 11px;
  line-height: 1.4;
  color: var(--muted);
}

.library-status.error {
  color: #b4554d;
}

.spin {
  animation: library-spin 0.9s linear infinite;
}

@keyframes library-spin {
  to { transform: rotate(360deg); }
}
</style>
