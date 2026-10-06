<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ArrowUpRight, Check, LoaderCircle, RefreshCw, Undo2, X } from '@lucide/vue'
import { api } from '@/lib/api'
import { deepReviewApi, type FindingKind, type ReviewEvidence, type ReviewFinding, type ReviewPatch, type ReviewRun } from '@/lib/deepReview'

const props = defineProps<{ versionId: string; episodeId: string | null; refreshKey?: string }>()
const emit = defineEmits<{
  applied: []
  navigate: [shotId: string]
  jump: [episodeId: string, shotId?: string]
}>()
const latest = ref<ReviewRun | null>(null)
const previous = ref<ReviewRun | null>(null)
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const filter = ref<FindingKind | 'all'>('all')
const preview = ref<{ finding: ReviewFinding; editable: boolean; values: string[] } | null>(null)
const previewElement = ref<HTMLElement | HTMLElement[] | null>(null)
const review = computed(() => latest.value?.report ? latest.value : previous.value)
const active = computed(() => [latest.value?.status, latest.value?.draft_job?.status].some(isActive))
const stale = computed(() => Boolean(review.value?.stale || (review.value && review.value.script_version_id !== props.versionId)))
const findings = computed(() => (review.value?.report?.findings ?? []).filter(finding => filter.value === 'all' || finding.kind === filter.value))
const canUndo = computed(() => review.value?.applied_version_id === props.versionId)
const kinds: Array<{ value: FindingKind | 'all'; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'confirmed_inconsistency', label: 'Confirmed' },
  { value: 'possible_gap', label: 'Gaps' },
  { value: 'editorial_suggestion', label: 'Editorial' },
]
let timer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let disposed = false

function isActive(status?: string) { return status === 'queued' || status === 'running' }
function display(value: ReviewPatch['after']) { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) }
function message(cause: unknown) { return cause instanceof Error ? cause.message : 'The request failed. Try again.' }
function kindLabel(kind: FindingKind) { return kinds.find(item => item.value === kind)?.label ?? kind }
function evidenceContext(evidence: ReviewEvidence) { return review.value?.context.find(item => item.version_id === evidence.version_id) }
function currentEvidence(evidence: ReviewEvidence) {
  const context = evidenceContext(evidence)
  return context ? context.episode_id === props.episodeId : evidence.version_id === review.value?.script_version_id
}
function navigate(evidence: ReviewEvidence) {
  if (currentEvidence(evidence) && evidence.shot_id) emit('navigate', evidence.shot_id)
  else {
    const context = evidenceContext(evidence)
    if (context) emit('jump', context.episode_id, evidence.shot_id)
  }
}
function schedule() {
  clearTimeout(timer)
  if (active.value && !disposed) timer = setTimeout(() => void load(), 3000)
}
async function load() {
  const epoch = ++generation
  const versionId = props.versionId
  try {
    const result = await deepReviewApi.get(versionId)
    if (disposed || epoch !== generation || versionId !== props.versionId) return
    latest.value = result.latest
    previous.value = result.previous_success
    if (stale.value) preview.value = null
    schedule()
  } catch (cause) {
    if (epoch === generation && !disposed) error.value = message(cause)
  } finally {
    if (epoch === generation && !disposed) loading.value = false
  }
}
async function start() {
  busy.value = true
  error.value = ''
  preview.value = null
  try {
    latest.value = await deepReviewApi.start(props.versionId)
    schedule()
  } catch (cause) { error.value = message(cause) }
  finally { busy.value = false }
}
async function cancel() {
  const jobId = isActive(latest.value?.draft_job?.status) ? undefined : latest.value?.job_id
  if (!jobId) return
  busy.value = true
  error.value = ''
  try { await api.cancelJob(jobId); await load() }
  catch (cause) { error.value = message(cause) }
  finally { busy.value = false }
}
async function draft(finding: ReviewFinding) {
  if (!review.value) return
  busy.value = true
  error.value = ''
  try { await deepReviewApi.draft(review.value.id, finding.id); await load() }
  catch (cause) { error.value = message(cause) }
  finally { busy.value = false }
}
async function reject(finding: ReviewFinding) {
  if (!review.value) return
  busy.value = true
  error.value = ''
  try {
    const result = await deepReviewApi.decide(review.value.id, finding.id, { decision: 'rejected' })
    latest.value = result.run
  } catch (cause) { error.value = message(cause) }
  finally { busy.value = false }
}
async function openPreview(finding: ReviewFinding, editable: boolean) {
  error.value = ''
  preview.value = { finding, editable, values: finding.patches.map(patch => display(patch.after)) }
  await nextTick()
  const target = Array.isArray(previewElement.value) ? previewElement.value[0] : previewElement.value
  target?.focus?.()
}
async function apply() {
  if (!review.value || !preview.value || stale.value) return
  error.value = ''
  try {
    const proposal = preview.value
    const patches = proposal.finding.patches.map((patch, index) => {
      const value = proposal.values[index] ?? ''
      let after: ReviewPatch['after'] = value
      if (patch.field === 'dialogue') {
        const parsed: unknown = JSON.parse(value)
        if (!Array.isArray(parsed) || !parsed.every(line => typeof line?.character === 'string' && typeof line?.text === 'string')) {
          throw new Error('Dialogue must be a JSON array of character and text entries.')
        }
        after = parsed
      }
      return { ...patch, after }
    })
    busy.value = true
    const result = await deepReviewApi.decide(review.value.id, proposal.finding.id, {
      decision: 'accepted', expected_version_id: props.versionId, patches,
    })
    latest.value = result.run
    preview.value = null
    emit('applied')
  } catch (cause) { error.value = message(cause) }
  finally { busy.value = false }
}
async function undo() {
  if (!review.value || !canUndo.value) return
  busy.value = true
  error.value = ''
  try { await deepReviewApi.undo(review.value.id, props.versionId); emit('applied') }
  catch (cause) { error.value = message(cause) }
  finally { busy.value = false }
}
watch(() => [props.versionId, props.episodeId, props.refreshKey], (value, oldValue) => {
  if (oldValue && value[1] !== oldValue[1]) { latest.value = null; previous.value = null; filter.value = 'all' }
  preview.value = null
  clearTimeout(timer)
  void load()
}, { immediate: true })
onBeforeUnmount(() => { disposed = true; generation += 1; clearTimeout(timer) })
</script>

<template>
  <section class="deep-review" aria-label="Deep script review" :aria-busy="loading || active">
    <header class="deep-review-heading">
      <div><h2>Deep review</h2><p>Story, continuity, and production details.</p></div>
      <button type="button" class="deep-primary" :disabled="loading || busy || active" @click="start">
        <RefreshCw v-if="review" :size="14" />{{ review ? 'Review again' : 'Run review' }}
      </button>
    </header>
    <p v-if="error" class="deep-notice error" role="alert">{{ error }}</p>
    <p v-if="loading && !latest && !previous" class="deep-notice" role="status">Loading review…</p>
    <div v-if="active" class="deep-notice deep-progress" role="status">
      <LoaderCircle class="deep-spinner" :size="17" />
      <span>{{ isActive(latest?.draft_job?.status) ? 'Drafting a revision…' : latest?.status === 'queued' ? 'Review queued…' : 'Reviewing the script and earlier episodes…' }}</span>
      <button v-if="latest?.job_id && !isActive(latest?.draft_job?.status)" type="button" :disabled="busy" @click="cancel">Cancel</button>
    </div>
    <p v-if="latest?.error" class="deep-notice error" role="alert">Review failed: {{ latest.error }} Use Review again to retry.</p>
    <p v-if="latest?.status === 'cancelled'" class="deep-notice">Review cancelled. You can run it again.</p>
    <p v-if="latest?.draft_job?.error" class="deep-notice error" role="alert">Revision failed: {{ latest.draft_job.error }}</p>
    <p v-if="review && review.id !== latest?.id" class="deep-notice">Showing the last completed review.</p>
    <div v-if="review && stale" class="deep-notice stale" role="status">
      <strong>This review is outdated.</strong> The script or its context has changed. Review again before applying another correction.
    </div>
    <button v-if="canUndo" type="button" class="deep-undo" :disabled="busy || active" @click="undo"><Undo2 :size="14" /> Undo last review change</button>
    <div v-if="!loading && !review && !active" class="deep-empty">
      <h3>A closer look at your story</h3>
      <p>Find contradictions, unexplained choices, and opportunities to strengthen the episode. Each finding includes evidence and a recommendation.</p>
      <p>Running a review leaves your script unchanged.</p>
    </div>
    <template v-if="review?.report">
      <details class="deep-context"><summary>Context · {{ review.context.length }} {{ review.context.length === 1 ? 'episode' : 'episodes' }}</summary>
        <ul><li v-for="context in review.context" :key="context.version_id">Episode {{ context.episode_number }} · {{ context.title }} <strong v-if="context.draft">(draft)</strong></li></ul>
        <p v-if="review.context.some(context => context.draft)">Draft context may change as earlier scripts are revised.</p>
      </details>
      <section class="deep-overview"><h3>Assessment</h3><p>{{ review.report.assessment }}</p></section>
      <section v-if="review.report.strengths.length" class="deep-overview"><h3>What works</h3><ul><li v-for="strength in review.report.strengths" :key="strength">{{ strength }}</li></ul></section>
      <section v-if="review.report.priorities.length" class="deep-overview"><h3>Revise first</h3><ol><li v-for="priority in review.report.priorities" :key="priority">{{ priority }}</li></ol></section>
      <div class="deep-findings-header"><h3>Findings <span>{{ review.report.findings.length }}</span></h3>
        <div class="deep-filters" role="group" aria-label="Filter findings"><button v-for="kind in kinds" :key="kind.value" type="button" :aria-pressed="filter === kind.value" @click="filter = kind.value">{{ kind.label }}</button></div>
      </div>
      <p v-if="!findings.length" class="deep-muted">{{ review.report.findings.length ? 'No findings in this category.' : 'No issues were identified. A review cannot guarantee a script is free of issues.' }}</p>
      <article v-for="finding in findings" :key="finding.id" class="deep-finding" :class="finding.status">
        <div class="deep-finding-kind">{{ kindLabel(finding.kind) }}<span v-if="finding.status !== 'open'">{{ finding.status }}{{ finding.modified ? ' with modification' : '' }}</span></div>
        <h4>{{ finding.title }}</h4><p>{{ finding.reason }}</p>
        <details class="deep-evidence"><summary>Evidence · {{ finding.evidence.length }} {{ finding.evidence.length === 1 ? 'reference' : 'references' }}</summary>
          <div v-for="(evidence, index) in finding.evidence" :key="index">
            <strong>{{ evidence.label }}</strong><blockquote>{{ evidence.quote }}</blockquote>
            <button v-if="evidence.shot_id && currentEvidence(evidence)" type="button" @click="navigate(evidence)">Show in script <ArrowUpRight :size="13" /></button>
            <button v-else-if="evidenceContext(evidence) && !currentEvidence(evidence)" type="button" @click="navigate(evidence)">Jump to {{ evidence.label }} <ArrowUpRight :size="13" /></button>
          </div>
        </details>
        <p class="deep-recommendation"><strong>Recommendation</strong>{{ finding.recommendation }}</p>
        <div v-if="finding.status === 'open'" class="deep-decisions">
          <template v-if="finding.patches.length">
            <button type="button" class="deep-primary" :disabled="busy || active || stale" @click="openPreview(finding, false)"><Check :size="14" /> Accept</button>
            <button type="button" :disabled="busy || active || stale" @click="openPreview(finding, true)">Accept with modification</button>
          </template>
          <button v-else type="button" class="deep-primary" :disabled="busy || active || stale" @click="draft(finding)">Draft a revision</button>
          <button type="button" :disabled="busy || active" @click="reject(finding)"><X :size="14" /> Reject</button>
        </div>
        <section v-if="preview?.finding.id === finding.id" ref="previewElement" class="deep-preview" tabindex="-1" aria-label="Revision preview">
          <h4>{{ preview.editable ? 'Modify the correction' : 'Preview the correction' }}</h4>
          <p>Applying creates a new script version. Existing media is retained and may need review.</p>
          <div v-for="(patch, index) in finding.patches" :key="`${patch.shot_id}-${patch.field}`" class="deep-patch">
            <strong>{{ patch.field.replace(/_/g, ' ') }}</strong>
            <span>Before</span><pre>{{ display(patch.before) }}</pre>
            <label :for="`deep-after-${finding.id}-${index}`">After{{ patch.field === 'dialogue' ? ' (JSON)' : '' }}</label>
            <textarea v-if="preview.editable" :id="`deep-after-${finding.id}-${index}`" v-model="preview.values[index]" rows="5" :disabled="busy" />
            <pre v-else>{{ preview.values[index] }}</pre>
          </div>
          <div class="deep-decisions"><button type="button" class="deep-primary" :disabled="busy || stale" @click="apply">{{ busy ? 'Applying…' : 'Apply revision' }}</button><button type="button" :disabled="busy" @click="preview = null">Cancel</button></div>
        </section>
      </article>
    </template>
  </section>
</template>

<style scoped>
.deep-review { padding: 20px 18px 36px; color: var(--ink); font-size: 13px; line-height: 1.65; overflow-wrap: anywhere; }
.deep-review-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 20px; }
.deep-review h2 { font-size: 18px; margin: 0; line-height: 1.3; }
.deep-review h3 { font-size: 14px; margin: 0 0 8px; line-height: 1.4; }
.deep-review h4 { font-size: 14px; margin: 5px 0 9px; line-height: 1.5; }
.deep-review p { margin: 0 0 12px; white-space: pre-line; }
.deep-review-heading p, .deep-muted { color: var(--text-secondary); font-size: 12px; }
.deep-review button { min-height: 34px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px; background: transparent; color: var(--ink); font-size: 12px; font-weight: 600; cursor: pointer; line-height: 1.4; }
.deep-review button:hover:not(:disabled) { background: var(--panel-2); border-color: var(--text-tertiary); }
.deep-review button:disabled { opacity: .45; cursor: not-allowed; }
.deep-review button:focus-visible, .deep-review summary:focus-visible, .deep-review textarea:focus-visible, .deep-preview:focus { outline: 2px solid var(--accent); outline-offset: 3px; }
.deep-review button.deep-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.deep-review-heading button { flex-shrink: 0; }
.deep-notice { background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px; padding: 11px 12px; margin-bottom: 14px; font-size: 12px; }
.deep-notice.error { color: var(--danger, #ffb7b7); }
.deep-notice.stale { color: var(--ink); }
.deep-notice strong { display: block; }
.deep-progress { display: flex; align-items: center; gap: 9px; }
.deep-progress span { flex: 1; }
.deep-progress svg { flex-shrink: 0; }
.deep-spinner { animation: deep-spin 1.2s linear infinite; }
@keyframes deep-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .deep-spinner { animation: none; } }
.deep-empty { padding: 20px 0; }
.deep-empty p { color: var(--text-secondary); }
.deep-overview { margin: 24px 0; }
.deep-review ul, .deep-review ol { padding-left: 20px; margin: 0; }
.deep-review li + li { margin-top: 8px; }
.deep-context { padding-bottom: 14px; border-bottom: 1px solid var(--line); font-size: 12px; color: var(--text-secondary); }
.deep-review summary { cursor: pointer; font-weight: 600; color: var(--ink); }
.deep-context ul { margin-top: 12px; }
.deep-context p { margin-top: 10px; }
.deep-findings-header { margin-top: 28px; }
.deep-findings-header h3 span { margin-left: 5px; color: var(--text-secondary); font-weight: 400; }
.deep-filters { display: flex; gap: 5px; flex-wrap: wrap; }
.deep-filters button { font-size: 11px; padding: 5px 9px; min-height: 30px; }
.deep-filters button[aria-pressed='true'] { background: var(--panel-3); border-color: var(--text-secondary); }
.deep-finding { padding: 20px 0; border-bottom: 1px solid var(--line); }
.deep-finding:last-child { border-bottom: 0; }
.deep-finding-kind { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 6px; color: var(--text-secondary); font-size: 11px; font-weight: 600; }
.deep-finding-kind span { text-transform: capitalize; }
.deep-evidence { margin: 14px 0; font-size: 12px; }
.deep-evidence > div { padding: 12px 0 0; }
.deep-evidence strong { font-size: 11px; color: var(--text-secondary); }
.deep-evidence blockquote { margin: 6px 0; padding: 10px 12px; background: var(--panel-2); white-space: pre-wrap; }
.deep-evidence button { border: 0; padding: 4px 0; text-align: left; color: var(--text-secondary); }
.deep-recommendation strong { display: block; font-size: 11px; margin-bottom: 4px; }
.deep-decisions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 12px; }
.deep-preview { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line); }
.deep-preview > p { color: var(--text-secondary); font-size: 12px; }
.deep-patch { display: flex; flex-direction: column; gap: 5px; margin: 18px 0; }
.deep-patch > strong { text-transform: capitalize; }
.deep-patch > span, .deep-patch > label { color: var(--text-secondary); font-size: 11px; }
.deep-patch pre { font-family: inherit; white-space: pre-wrap; overflow-wrap: anywhere; background: var(--panel-2); padding: 10px 12px; margin: 0; font-size: 12px; }
.deep-patch textarea { width: 100%; min-height: 110px; padding: 10px 12px; resize: vertical; border: 1px solid var(--line); border-radius: 5px; background: var(--panel); color: var(--ink); font: inherit; caret-color: var(--accent); }
.deep-undo { margin-bottom: 14px; }
</style>
