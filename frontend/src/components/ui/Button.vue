<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  size?: 'sm' | 'md' | 'lg'
  type?: 'button' | 'submit' | 'reset'
  disabled?: boolean
  class?: string
}>(), {
  variant: 'secondary',
  size: 'md',
  type: 'button',
  disabled: false,
  class: '',
})

const classes = computed(() => cn(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-sm)] border text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(235,101,84,.28)] disabled:pointer-events-none disabled:opacity-45 active:translate-y-px',
  props.variant === 'primary' && 'border-white/10 bg-[var(--accent)] text-white shadow-[0_5px_16px_rgba(235,101,84,.18)] hover:bg-[var(--accent-hover)]',
  props.variant === 'secondary' && 'border-[var(--line-default)] bg-white/[.035] text-[var(--text-primary)] hover:border-[var(--line-emphasis)] hover:bg-white/[.065]',
  props.variant === 'ghost' && 'border-transparent bg-transparent text-[var(--text-secondary)] hover:border-[var(--line-default)] hover:bg-white/[.055] hover:text-[var(--text-primary)]',
  props.variant === 'danger' && 'border-red-400/20 bg-red-400/10 text-red-200 hover:bg-red-400/15',
  props.size === 'sm' && 'h-8 px-2.5 text-xs',
  props.size === 'md' && 'h-[var(--control-h)] px-3',
  props.size === 'lg' && 'h-10 px-4',
  props.class,
))
</script>

<template>
  <button :type="type" :disabled="disabled" :class="classes">
    <slot />
  </button>
</template>
