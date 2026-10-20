<script setup lang="ts">
import { computed } from 'vue'
import type { LUTPreset } from '@/lib/api'

const props = defineProps<{
  lut: string
  strength: number
  presets: LUTPreset[]
}>()

const emit = defineEmits<{
  'update:lut': [value: string]
  'update:strength': [value: number]
  change: []
}>()

const groups = computed(() => {
  const map: Record<string, LUTPreset[]> = {}
  for (const preset of props.presets) {
    const cat = preset.category || 'General'
    if (!map[cat]) map[cat] = []
    map[cat].push(preset)
  }
  return Object.entries(map).map(([category, items]) => ({ category, presets: items }))
})

const active = computed(() => props.presets.find((p) => p.id === props.lut))

const strengthStyle = computed(() => {
  const v = Math.max(0, Math.min(1, Number(props.strength) || 0))
  const pct = v * 100
  return {
    '--range-pct': `${pct}%`,
    '--range-mid': '0%',
    '--range-fill-start': '0%',
    '--range-fill-end': `${pct}%`,
  }
})

function selectLut(id: string) {
  emit('update:lut', id)
  emit('change')
}

function setStrength(value: number) {
  emit('update:strength', Math.max(0, Math.min(1, value)))
  emit('change')
}

function onStrengthInput(event: Event) {
  const v = Number((event.target as HTMLInputElement).value)
  emit('update:strength', Math.max(0, Math.min(1, v)))
}

function onStrengthChange() {
  emit('change')
}
</script>

<template>
  <div class="lut-picker">
    <div class="lut-swatch-grid" role="listbox" aria-label="Color grade presets">
      <button
        type="button"
        class="lut-swatch"
        :class="{ active: !lut || lut === 'none' }"
        role="option"
        :aria-selected="!lut || lut === 'none'"
        title="Original colors"
        @click="selectLut('none')"
      >
        <span class="lut-swatch-preview none" aria-hidden="true"></span>
        <em>None</em>
      </button>
      <template v-for="group in groups" :key="group.category">
        <div class="lut-swatch-cat" aria-hidden="true">{{ group.category }}</div>
        <button
          v-for="preset in group.presets"
          :key="preset.id"
          type="button"
          class="lut-swatch"
          :class="{ active: lut === preset.id }"
          role="option"
          :aria-selected="lut === preset.id"
          :title="preset.description"
          @click="selectLut(preset.id)"
        >
          <span
            class="lut-swatch-preview"
            aria-hidden="true"
            :style="{ filter: preset.css_filter }"
          ></span>
          <em>{{ preset.label }}</em>
        </button>
      </template>
    </div>

    <p v-if="active" class="lut-description">{{ active.description }}</p>

    <template v-if="lut && lut !== 'none'">
      <label class="lut-strength-field">
        <span class="lut-strength-head">
          <span>Strength</span>
          <b>{{ Math.round((strength ?? 1) * 100) }}%</b>
        </span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.05"
          aria-label="LUT strength"
          :value="strength ?? 1"
          :style="strengthStyle"
          @input="onStrengthInput"
          @change="onStrengthChange"
        />
      </label>
      <div class="lut-strength-presets">
        <button
          v-for="pct in [25, 50, 75, 100]"
          :key="pct"
          type="button"
          class="lut-strength-btn"
          :class="{ active: Math.round((strength ?? 1) * 100) === pct }"
          @click="setStrength(pct / 100)"
        >
          {{ pct }}%
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.lut-picker {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}

.lut-swatch-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
  max-height: 220px;
  overflow: auto;
  padding: 2px;
  margin: 0 -2px;
}

.lut-swatch-cat {
  grid-column: 1 / -1;
  margin-top: 4px;
  padding: 2px 2px 0;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--muted);
}

.lut-swatch-cat:first-of-type {
  margin-top: 0;
}

.lut-swatch {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 5px;
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(15, 23, 42, 0.55);
  color: #cbd5e1;
  cursor: pointer;
  text-align: left;
  min-width: 0;
}

.lut-swatch:hover {
  border-color: rgba(148, 163, 184, 0.4);
}

.lut-swatch.active {
  border-color: rgba(235, 101, 84, 0.85);
  box-shadow: 0 0 0 1px rgba(235, 101, 84, 0.35);
}

.lut-swatch-preview {
  display: block;
  width: 100%;
  height: 28px;
  border-radius: 4px;
  background:
    linear-gradient(
      90deg,
      #1a1a1a 0%,
      #c45c26 18%,
      #2a6b4f 36%,
      #d8c4a0 54%,
      #3b6ea5 72%,
      #f2f2f2 100%
    ),
    linear-gradient(180deg, #fff 0%, #888 50%, #111 100%);
  background-blend-mode: multiply;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.lut-swatch-preview.none {
  filter: none;
  background: repeating-linear-gradient(
    -45deg,
    #1e293b,
    #1e293b 4px,
    #0f172a 4px,
    #0f172a 8px
  );
}

.lut-swatch em {
  font-style: normal;
  font-size: 10.5px;
  font-weight: 650;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.lut-description {
  margin: 0;
  font-size: 11px;
  line-height: 1.35;
  color: var(--muted);
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.025);
  border: 1px solid var(--line-subtle);
}

.lut-strength-field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.lut-strength-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
  font-size: 10.5px;
  font-weight: 650;
  color: var(--muted);
}

.lut-strength-head b {
  font-weight: 700;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}

.lut-strength-field input[type='range'] {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 18px;
  margin: 0;
  background: transparent;
  cursor: pointer;
}

.lut-strength-field input[type='range']::-webkit-slider-runnable-track {
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

.lut-strength-field input[type='range']::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 12px;
  height: 12px;
  margin-top: -4px;
  border-radius: 50%;
  background: var(--text-primary);
  border: 2px solid var(--accent);
}

.lut-strength-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.lut-strength-btn {
  flex: 1;
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 5px;
  border: 1px solid var(--line);
  background: var(--surface-secondary);
  color: var(--text-primary);
  cursor: pointer;
}

.lut-strength-btn:hover {
  background: var(--surface-hover);
}

.lut-strength-btn.active {
  border-color: rgba(235, 101, 84, 0.7);
  color: var(--accent);
}
</style>
