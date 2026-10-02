<script setup lang="ts">
import { computed } from 'vue'
import { cn } from '@/lib/utils'

const props = withDefaults(defineProps<{ class?: string; interactive?: boolean }>(), {
  class: '',
  interactive: false,
})

const classes = computed(() => cn(
  'rounded-[var(--radius-md)] border border-[var(--line-subtle)] bg-[linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,.012))]',
  props.interactive && 'transition hover:-translate-y-px hover:border-[var(--line-default)] hover:bg-white/[.035]',
  props.class,
))
</script>

<template>
  <div :class="classes"><slot /></div>
</template>
