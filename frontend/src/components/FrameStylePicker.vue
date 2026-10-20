<script setup lang="ts">
import { computed } from 'vue'
import type { FrameStyle, TimelineGeometry } from '@/lib/api'

const props = defineProps<{
  geometry: TimelineGeometry
}>()

const emit = defineEmits<{
  update: [patch: Partial<TimelineGeometry>]
  change: []
}>()

const STYLES: { id: FrameStyle; label: string }[] = [
  { id: 'none', label: 'None' },
  { id: 'solid', label: 'Solid' },
  { id: 'double', label: 'Double' },
  { id: 'dashed', label: 'Dashed' },
  { id: 'glow', label: 'Glow' },
  { id: 'neon', label: 'Neon' },
]

const CORNERS: { label: string; radius: number }[] = [
  { label: 'Square', radius: 0 },
  { label: 'Soft', radius: 0.015 },
  { label: 'Rounded', radius: 0.04 },
  { label: 'Pill', radius: 0.25 },
]

const SWATCHES = ['#FFFFFF', '#000000', '#39D0FF', '#FF3B6B', '#FFC93C', '#7CFF6B', '#B86BFF']

const style = computed<FrameStyle>(() => props.geometry.frame_style || 'none')
const radius = computed(() => Number(props.geometry.frame_radius ?? 0) || 0)
const width = computed(() => Number(props.geometry.frame_width ?? 0.008) || 0)
const color = computed(() => (props.geometry.frame_color || '#FFFFFF').toUpperCase())

function apply(patch: Partial<TimelineGeometry>) {
  emit('update', patch)
  emit('change')
}

function onRange(key: 'frame_radius' | 'frame_width', event: Event) {
  emit('update', { [key]: Number((event.target as HTMLInputElement).value) })
}

function onColorInput(event: Event) {
  emit('update', { frame_color: (event.target as HTMLInputElement).value.toUpperCase() })
}

function rangeStyle(value: number, max: number) {
  const pct = `${Math.max(0, Math.min(100, (value / max) * 100))}%`
  return { '--range-fill-end': pct }
}

function previewStyle(id: FrameStyle) {
  const c = color.value
  const r = `${Math.min(10, radius.value * 160)}px`
  const base = { borderRadius: r }
  if (id === 'none') return { ...base, border: '1px dashed rgba(148, 163, 184, 0.35)' }
  if (id === 'solid') return { ...base, border: `2px solid ${c}` }
  if (id === 'double') return { ...base, border: `4px double ${c}` }
  if (id === 'dashed') return { ...base, border: `2px dashed ${c}` }
  if (id === 'glow') return { ...base, border: `1px solid ${c}`, boxShadow: `0 0 6px ${c}, inset 0 0 4px ${c}` }
  return { ...base, border: `2px solid ${c}`, boxShadow: `0 0 8px ${c}, 0 0 2px #fff, inset 0 0 6px ${c}` }
}
</script>

<template>
  <div class="frame-picker">
    <div class="frame-style-grid" role="listbox" aria-label="Border style">
      <button
        v-for="item in STYLES"
        :key="item.id"
        type="button"
        class="frame-style-btn"
        :class="{ active: style === item.id }"
        role="option"
        :aria-selected="style === item.id"
        @click="apply({ frame_style: item.id })"
      >
        <span class="frame-style-preview" :style="previewStyle(item.id)" aria-hidden="true"></span>
        <em>{{ item.label }}</em>
      </button>
    </div>

    <div class="frame-field">
      <span class="frame-field-head"><span>Corners</span><b>{{ Math.round(radius * 1000) / 10 }}%</b></span>
      <div class="frame-corner-presets">
        <button
          v-for="corner in CORNERS"
          :key="corner.label"
          type="button"
          class="frame-chip"
          :class="{ active: Math.abs(radius - corner.radius) < 1e-3 }"
          @click="apply({ frame_radius: corner.radius })"
        >
          {{ corner.label }}
        </button>
      </div>
      <input
        type="range"
        min="0"
        max="0.25"
        step="0.005"
        aria-label="Corner radius"
        :value="radius"
        :style="rangeStyle(radius, 0.25)"
        @input="onRange('frame_radius', $event)"
        @change="emit('change')"
      />
    </div>

    <template v-if="style !== 'none'">
      <label class="frame-field">
        <span class="frame-field-head"><span>Thickness</span><b>{{ (width * 100).toFixed(1) }}%</b></span>
        <input
          type="range"
          min="0.002"
          max="0.04"
          step="0.001"
          aria-label="Border thickness"
          :value="width"
          :style="rangeStyle(width, 0.04)"
          @input="onRange('frame_width', $event)"
          @change="emit('change')"
        />
      </label>
      <div class="frame-field">
        <span class="frame-field-head"><span>Color</span><b>{{ color }}</b></span>
        <div class="frame-swatches">
          <button
            v-for="swatch in SWATCHES"
            :key="swatch"
            type="button"
            class="frame-swatch"
            :class="{ active: color === swatch }"
            :style="{ background: swatch }"
            :title="swatch"
            :aria-label="`Border color ${swatch}`"
            @click="apply({ frame_color: swatch })"
          ></button>
          <input
            type="color"
            class="frame-color-input"
            aria-label="Custom border color"
            :value="color.toLowerCase()"
            @input="onColorInput"
            @change="emit('change')"
          />
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.frame-picker {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.frame-style-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px;
}

.frame-style-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 7px 5px 5px;
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(15, 23, 42, 0.55);
  color: #cbd5e1;
  cursor: pointer;
  min-width: 0;
}

.frame-style-btn:hover {
  border-color: rgba(148, 163, 184, 0.4);
}

.frame-style-btn.active {
  border-color: rgba(235, 101, 84, 0.85);
  box-shadow: 0 0 0 1px rgba(235, 101, 84, 0.35);
}

.frame-style-preview {
  display: block;
  width: 100%;
  max-width: 54px;
  height: 30px;
  box-sizing: border-box;
  background: linear-gradient(135deg, #1e3a5f, #0f172a);
}

.frame-style-btn em {
  font-style: normal;
  font-size: 10.5px;
  font-weight: 650;
}

.frame-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.frame-field-head {
  display: flex;
  justify-content: space-between;
  font-size: 10.5px;
  font-weight: 650;
  color: var(--muted);
}

.frame-field-head b {
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.frame-corner-presets {
  display: flex;
  gap: 4px;
}

.frame-chip {
  flex: 1;
  padding: 4px 6px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 5px;
  border: 1px solid var(--line);
  background: var(--surface-secondary);
  color: var(--text-primary);
  cursor: pointer;
}

.frame-chip:hover {
  background: var(--surface-hover);
}

.frame-chip.active {
  border-color: rgba(235, 101, 84, 0.7);
  color: var(--accent);
}

.frame-field input[type='range'] {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 18px;
  margin: 0;
  background: transparent;
  cursor: pointer;
}

.frame-field input[type='range']::-webkit-slider-runnable-track {
  height: 4px;
  border-radius: 999px;
  background: linear-gradient(
    to right,
    var(--accent) 0%,
    var(--accent) var(--range-fill-end, 0%),
    rgba(255, 255, 255, 0.12) var(--range-fill-end, 0%),
    rgba(255, 255, 255, 0.12) 100%
  );
}

.frame-field input[type='range']::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 12px;
  height: 12px;
  margin-top: -4px;
  border-radius: 50%;
  background: var(--text-primary);
  border: 2px solid var(--accent);
}

.frame-swatches {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
}

.frame-swatch {
  width: 18px;
  height: 18px;
  padding: 0;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.25);
  cursor: pointer;
}

.frame-swatch.active {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.frame-color-input {
  width: 26px;
  height: 20px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
}
</style>
