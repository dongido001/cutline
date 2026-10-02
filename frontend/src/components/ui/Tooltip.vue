<script setup lang="ts">
import { TooltipContent, TooltipPortal, TooltipProvider, TooltipRoot, TooltipTrigger } from 'reka-ui'

withDefaults(defineProps<{ text: string; side?: 'top' | 'right' | 'bottom' | 'left' }>(), { side: 'bottom' })
</script>

<template>
  <TooltipProvider :delay-duration="350">
    <TooltipRoot>
      <TooltipTrigger as-child><slot /></TooltipTrigger>
      <TooltipPortal>
        <TooltipContent :side="side" :side-offset="6" class="z-[100] rounded-md border border-[var(--line-default)] bg-[var(--surface-raised)] px-2 py-1 text-[11px] font-medium text-[var(--text-primary)] shadow-xl">
          {{ text }}
        </TooltipContent>
      </TooltipPortal>
    </TooltipRoot>
  </TooltipProvider>
</template>
