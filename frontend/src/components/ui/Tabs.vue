<script setup lang="ts">
import { TabsContent, TabsList, TabsRoot, TabsTrigger } from 'reka-ui'

export interface TabItem { label: string; value: string }

withDefaults(defineProps<{ modelValue?: string; items: TabItem[] }>(), { modelValue: '' })
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
</script>

<template>
  <TabsRoot :model-value="modelValue || items[0]?.value" @update:model-value="emit('update:modelValue', String($event))">
    <TabsList class="inline-flex gap-0.5 rounded-[9px] border border-[var(--line-subtle)] bg-black/20 p-[3px]">
      <TabsTrigger v-for="item in items" :key="item.value" :value="item.value" class="h-7 rounded-md px-2.5 text-xs font-semibold text-[var(--text-secondary)] transition hover:bg-white/[.045] hover:text-[var(--text-primary)] data-[state=active]:bg-[var(--surface-active)] data-[state=active]:text-white">
        {{ item.label }}
      </TabsTrigger>
    </TabsList>
    <TabsContent v-for="item in items" :key="`content-${item.value}`" :value="item.value" class="outline-none">
      <slot :name="item.value" />
    </TabsContent>
  </TabsRoot>
</template>
