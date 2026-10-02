<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = withDefaults(defineProps<{
  tone?: 'neutral' | 'accent' | 'success' | 'danger'
  class?: string
}>(), {
  tone: 'neutral',
  class: '',
})

const classes = computed(() => cn(
  'inline-flex h-6 items-center rounded-full border px-2 text-[11px] font-semibold',
  props.tone === 'neutral' && 'border-[var(--line-default)] bg-white/[.045] text-[var(--text-secondary)]',
  props.tone === 'accent' && 'border-[rgba(235,101,84,.25)] bg-[var(--accent-soft)] text-[var(--accent)]',
  props.tone === 'success' && 'border-emerald-400/20 bg-emerald-400/10 text-[var(--success)]',
  props.tone === 'danger' && 'border-red-400/20 bg-red-400/10 text-[var(--danger)]',
  props.class,
))
</script>

<template><span :class="classes"><slot /></span></template>
