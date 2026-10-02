<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown } from '@lucide/vue'
import { cn } from '@/lib/utils'

export interface SelectOption { label: string; value: string }

const props = withDefaults(defineProps<{
  modelValue?: string
  options: SelectOption[]
  disabled?: boolean
  class?: string
}>(), {
  modelValue: '',
  disabled: false,
  class: '',
})

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const classes = computed(() => cn(
  'h-[var(--control-h)] w-full appearance-none rounded-[var(--radius-sm)] border border-[var(--line-default)] bg-black/25 px-3 pr-9 text-sm text-[var(--text-primary)] outline-none transition hover:border-[var(--line-emphasis)] focus:border-[rgba(235,101,84,.72)] focus:ring-2 focus:ring-[rgba(235,101,84,.12)] disabled:opacity-50',
  props.class,
))
</script>

<template>
  <label class="relative block">
    <select :value="modelValue" :disabled="disabled" :class="classes" @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)">
      <option v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</option>
    </select>
    <ChevronDown :size="14" class="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-tertiary)]" />
  </label>
</template>
