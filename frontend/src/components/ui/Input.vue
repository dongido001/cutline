<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = withDefaults(defineProps<{
  modelValue?: string
  type?: string
  placeholder?: string
  disabled?: boolean
  class?: string
}>(), {
  modelValue: '',
  type: 'text',
  placeholder: '',
  disabled: false,
  class: '',
})

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const classes = computed(() => cn(
  'h-[var(--control-h)] w-full rounded-[var(--radius-sm)] border border-[var(--line-default)] bg-black/25 px-3 text-sm text-[var(--text-primary)] outline-none transition placeholder:text-[var(--text-tertiary)] hover:border-[var(--line-emphasis)] focus:border-[rgba(235,101,84,.72)] focus:ring-2 focus:ring-[rgba(235,101,84,.12)] disabled:opacity-50',
  props.class,
))
</script>

<template>
  <input
    :value="modelValue"
    :type="type"
    :placeholder="placeholder"
    :disabled="disabled"
    :class="classes"
    @input="emit('update:modelValue', ($event.target as HTMLInputElement).value)"
  />
</template>
