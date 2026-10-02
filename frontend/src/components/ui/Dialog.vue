<script setup lang="ts">
import { DialogClose, DialogContent, DialogDescription, DialogOverlay, DialogPortal, DialogRoot, DialogTitle } from 'reka-ui'
import { X } from '@lucide/vue'

withDefaults(defineProps<{
  open: boolean
  title: string
  description?: string
  widthClass?: string
}>(), {
  description: '',
  widthClass: 'max-w-xl',
})

const emit = defineEmits<{ 'update:open': [value: boolean] }>()
</script>

<template>
  <DialogRoot :open="open" @update:open="emit('update:open', $event)">
    <DialogPortal>
      <DialogOverlay class="fixed inset-0 z-50 bg-black/65 backdrop-blur-[2px]" />
      <DialogContent :class="['fixed left-1/2 top-1/2 z-50 max-h-[calc(100dvh-32px)] overflow-y-auto w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-[var(--line-default)] bg-[var(--surface-1)] p-5 text-[var(--text-primary)] shadow-[var(--shadow-float)] outline-none', widthClass]">
        <div class="mb-4 pr-10">
          <DialogTitle class="text-base font-semibold tracking-[-.02em]">{{ title }}</DialogTitle>
          <DialogDescription v-if="description" class="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{{ description }}</DialogDescription>
        </div>
        <slot />
        <DialogClose class="absolute right-4 top-4 inline-flex h-8 w-8 items-center justify-center rounded-lg border border-transparent text-[var(--text-secondary)] transition hover:border-[var(--line-default)] hover:bg-white/[.055] hover:text-[var(--text-primary)]" aria-label="Close dialog">
          <X :size="16" />
        </DialogClose>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>
