<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = withDefaults(defineProps<{
  label: string
  size?: 'sm' | 'md'
  active?: boolean
  disabled?: boolean
  class?: string
}>(), {
  size: 'md',
  active: false,
  disabled: false,
  class: '',
})

const classes = computed(() => cn(
  'inline-flex shrink-0 items-center justify-center rounded-[var(--radius-sm)] border transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(235,101,84,.28)] disabled:pointer-events-none disabled:opacity-45 active:translate-y-px',
  props.active
    ? 'border-[var(--line-default)] bg-white/[.075] text-[var(--text-primary)]'
    : 'border-transparent bg-transparent text-[var(--text-secondary)] hover:border-[var(--line-default)] hover:bg-white/[.055] hover:text-[var(--text-primary)]',
  props.size === 'sm' ? 'h-8 w-8' : 'h-[var(--control-h)] w-[var(--control-h)]',
  props.class,
))
</script>

<template>
  <button type="button" :aria-label="label" :title="label" :aria-pressed="active || undefined" :disabled="disabled" :class="classes">
    <slot />
  </button>
</template>
