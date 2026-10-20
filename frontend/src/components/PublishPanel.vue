<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  CheckCircle2,
  Clock3,
  ExternalLink,
  LoaderCircle,
  PlaySquare,
  Send,
  Share2,
  X,
  Radio,
  Sparkles,
  Calendar,
  Eye,
  Tag,
  FileText,
  Image as ImageIcon,
  Package,
} from '@lucide/vue'
import {
  api,
  type ReleaseKit,
  type Job,
  type OutputVariant,
  type ProjectDetail,
  type PublishAsset,
  type PublishPlan,
  type PublishRecord,
  type SocialAuthStatus,
} from '@/lib/api'

const props = defineProps<{
  project: ProjectDetail
  variant: OutputVariant
  episodeId?: string
  compilationId?: string
  asModal?: boolean
}>()

const emit = defineEmits<{
  published: []
  close: []
  'open-kit': []
}>()

const kit = ref<ReleaseKit | null>(null)
const coverId = ref<string | null>(null)
const kitCovers = computed(() => (kit.value?.covers || []).filter((item) => item.status === 'ready'))
const selectedCover = computed(() => kitCovers.value.find((item) => item.id === coverId.value) || null)

const plan = ref<PublishPlan | null>(null)
const loading = ref(false)
const publishing = ref(false)
const error = ref('')
const job = ref<Job | null>(null)
const platforms = ref<Array<'youtube' | 'facebook'>>(['youtube'])
const scope = ref<'final' | 'scenes' | 'shots'>('final')
const selected = ref<string[]>([])
const title = ref(props.project.title)
const description = ref('')
const tagsText = ref('')
const madeForKids = ref(false)
const scheduledAt = ref('')
const scheduleInterval = ref(24)
const privacy = ref({ youtube: 'private', facebook: 'self' })

const records = computed<PublishRecord[]>(() => props.project.publishes || [])

const publishedAssetMap = computed(() => {
  const map = new Map<string, { url: string; label: string }>()
  for (const rec of records.value) {
    for (const r of rec.results || []) {
      if (r.status === 'completed' && r.external_url && r.asset_id) {
        map.set(String(r.asset_id), { url: String(r.external_url), label: String(r.asset_label || '') })
      }
    }
  }
  return map
})

const unuploadedAssets = computed(() =>
  readyAssets.value.filter((item) => !publishedAssetMap.value.has(item.id))
)

function selectOnlyUnpublished() {
  selected.value = unuploadedAssets.value.map((item) => item.id)
}
const activeAssets = computed(() => {
  if (!plan.value) return []
  return scope.value === 'final'
    ? plan.value.final
    : scope.value === 'scenes'
      ? plan.value.scenes
      : plan.value.shots
})
const readyAssets = computed(() => activeAssets.value.filter((item) => item.ready))
const selectedAssets = computed(() =>
  readyAssets.value.filter((item) => selected.value.includes(item.id)),
)
const providerMap = computed(
  () => new Map((plan.value?.providers || []).map((item) => [item.id, item]))
)
const canPublish = computed(
  () =>
    platforms.value.length > 0 &&
    platforms.value.every((platform) => isConnected(platform)) &&
    title.value.trim() &&
    selectedAssets.value.length > 0 &&
    !publishing.value
)

function formatDuration(ms: number) {
  const seconds = Math.round(ms / 1000)
  const minutes = Math.floor(seconds / 60)
  const rem = seconds % 60
  return minutes ? `${minutes}:${String(rem).padStart(2, '0')}` : `${seconds}s`
}


function formatDateTime(iso: string) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function formatEndDateTime(iso: string, intervalHours: number, count: number) {
  try {
    const d = new Date(iso)
    d.setHours(d.getHours() + intervalHours * (count - 1))
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return ''
  }
}

function platformLabel(id: 'youtube' | 'facebook') {
  return id === 'youtube' ? 'YouTube' : 'Facebook'
}

function togglePlatform(id: 'youtube' | 'facebook') {
  platforms.value = platforms.value.includes(id)
    ? platforms.value.filter((item) => item !== id)
    : [...platforms.value, id]
}

function toggleAsset(id: string) {
  selected.value = selected.value.includes(id)
    ? selected.value.filter((item) => item !== id)
    : [...selected.value, id]
}

function selectAll() {
  selected.value = readyAssets.value.map((item) => item.id)
}

function clearSelection() {
  selected.value = []
}

async function loadPlan() {
  loading.value = true
  error.value = ''
  try {
    plan.value = await api.publishPlan(props.project.id, props.variant, props.episodeId, props.compilationId)
    selected.value = readyAssets.value.map((item) => item.id)
    await loadKit()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not load publish plan'
  } finally {
    loading.value = false
  }
}

// Prefill from the release kit only when the user has not already typed their own metadata.
async function loadKit() {
  try {
    const next = await api.releaseKit(props.project.id, {
      variant: props.variant,
      episodeId: props.episodeId,
      compilationId: props.compilationId,
    })
    const previous = kit.value
    kit.value = next
    const selectedTitle = next.title_options.find((item) => item.id === next.selected_title_id)
    const untouchedTitle = !title.value.trim() || title.value === props.project.title
      || previous?.title_options.some((item) => item.text === title.value)
    if (selectedTitle && untouchedTitle) title.value = selectedTitle.text
    if (next.description && (!description.value.trim() || description.value === previous?.description)) description.value = next.description
    if (next.tags.length && (!tagsText.value.trim() || tagsText.value === previous?.tags.join(', '))) tagsText.value = next.tags.join(', ')
    if (!coverId.value || !next.covers.some((item) => item.id === coverId.value && item.status === 'ready')) {
      coverId.value = next.selected_cover_id
    }
  } catch {
    kit.value = null
  }
}

function useTitle(text: string) {
  title.value = text
}

async function watchPublish(jobId: string) {
  for (;;) {
    await new Promise((resolve) => window.setTimeout(resolve, 900))
    const next = await api.job(jobId)
    job.value = next
    if (!['queued', 'running'].includes(next.status)) break
  }
}

async function publish() {
  if (!canPublish.value) return
  publishing.value = true
  error.value = ''
  try {
    const tagList = tagsText.value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    job.value = await api.publishProject(props.project.id, {
      platforms: platforms.value,
      scope: scope.value,
      variant: props.variant,
      title: title.value.trim(),
      description: description.value.trim(),
      tags: tagList,
      privacy: privacy.value,
      selected_asset_ids: selected.value,
      scheduled_at: scheduledAt.value ? new Date(scheduledAt.value).toISOString() : null,
      schedule_interval_hours: scheduledAt.value && selectedAssets.value.length > 1 ? Number(scheduleInterval.value) : 0,
      made_for_kids: madeForKids.value,
      platform_options: {},
      release_kit_id: selectedCover.value ? kit.value?.id : null,
      cover_id: selectedCover.value?.id || null,
    }, props.episodeId, props.compilationId)
    await watchPublish(job.value.id)
    emit('published')
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Publish failed'
  } finally {
    publishing.value = false
  }
}

function resultLinks(record: PublishRecord) {
  return (record.results || []).filter((item) => item.external_url).slice(0, 4)
}

watch([() => props.variant, () => props.episodeId, () => props.compilationId], loadPlan)
watch(scope, () => {
  selected.value = []
})

// ── Social OAuth ──────────────────────────────────────────────────────────────

const socialAuth = ref<Record<string, SocialAuthStatus>>({})
const connectingPlatform = ref<'youtube' | 'facebook' | null>(null)

async function loadSocialStatus() {
  try {
    const statuses = await api.socialAuthStatus()
    socialAuth.value = Object.fromEntries(statuses.map((s) => [s.platform, s]))
  } catch {
    // Non-fatal — just show as disconnected
  }
}

function isConnected(platform: 'youtube' | 'facebook') {
  const s = socialAuth.value[platform]
  return s?.connected && !s?.is_expired
}

function accountName(platform: 'youtube' | 'facebook') {
  return socialAuth.value[platform]?.account_name ?? null
}

function accountPicture(platform: 'youtube' | 'facebook') {
  return socialAuth.value[platform]?.account_picture ?? null
}

async function connectPlatform(platform: 'youtube' | 'facebook') {
  connectingPlatform.value = platform
  try {
    const { url } = await api.socialAuthStart(platform)
    const authUrl = new URL(url)
    const redirectUri = authUrl.searchParams.get('redirect_uri')
    const oauthBackendOrigin = redirectUri ? new URL(redirectUri).origin : window.location.origin
    const popup = window.open(url, `connect_${platform}`, 'width=520,height=640,resizable,scrollbars')
    if (!popup) {
      error.value = 'Popup was blocked. Please allow popups for this site and try again.'
      connectingPlatform.value = null
      return
    }

    // postMessage listener — fires when callback page sends success/error
    const listener = async (event: MessageEvent) => {
      if (event.origin !== oauthBackendOrigin) return
      if (event.source !== popup) return
      if (event.data?.platform !== platform) return
      window.removeEventListener('message', listener)
      window.clearInterval(interval)
      if (event.data?.type === 'oauth_success') {
        await loadSocialStatus()
        await loadPlan()
      } else if (event.data?.type === 'oauth_error') {
        error.value = event.data.error || 'Connection failed'
      }
      connectingPlatform.value = null
    }
    window.addEventListener('message', listener)

    // Fallback poll: runs every 600ms, refreshes status when popup closes
    // This catches cases where postMessage doesn't fire (e.g. cross-origin quirks)
    const interval = window.setInterval(async () => {
      if (!popup.closed) return
      window.clearInterval(interval)
      window.removeEventListener('message', listener)
      // Small delay so the backend has time to finish writing the token
      await new Promise((r) => window.setTimeout(r, 500))
      await loadSocialStatus()
      await loadPlan()
      connectingPlatform.value = null
    }, 600)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not start connection'
    connectingPlatform.value = null
  }
}

async function disconnectPlatform(platform: 'youtube' | 'facebook') {
  try {
    await api.socialDisconnect(platform)
    await loadSocialStatus()
    // Remove this platform from selection if it was selected
    platforms.value = platforms.value.filter((p) => p !== platform)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : 'Could not disconnect'
  }
}

onMounted(async () => {
  await Promise.all([loadPlan(), loadSocialStatus()])
  try {
    const activeJobs = await api.projectJobs(props.project.id, true)
    const activePublish = activeJobs.find((j) => j.kind === 'publish')
    if (activePublish) {
      job.value = activePublish
      publishing.value = true
      watchPublish(activePublish.id).finally(() => {
        publishing.value = false
      })
    }
  } catch {
    // Ignore error restoring active job
  }
})
</script>

<template>
  <section class="publish-panel" :class="{ 'is-modal': props.asModal }" aria-label="Publish to Social Channels">
    <!-- Header -->
    <header class="panel-header">
      <div class="header-text">
        <div class="header-badge">
          <Radio :size="13" class="badge-icon pulse" />
          <span>RELEASE & DISTRIBUTION</span>
        </div>
        <div class="title-row">
          <h3>Publish to YouTube & Socials</h3>
          <span class="variant-tag">{{ props.variant === 'shorts' ? 'Shorts 9:16' : 'Landscape 16:9' }}</span>
        </div>
        <p>Release the full final cut or publish multi-part clips directly to your channel.</p>
      </div>
      <div class="header-actions">
        <button class="icon-refresh" type="button" :disabled="loading" title="Refresh ready assets" @click="loadPlan">
          <LoaderCircle v-if="loading" class="spin" :size="15" />
          <Sparkles v-else :size="15" />
          <span>Sync</span>
        </button>
        <button v-if="props.asModal" type="button" class="close-btn" aria-label="Close modal" @click="emit('close')">
          <X :size="18" />
        </button>
      </div>
    </header>

    <!-- Loading State -->
    <div v-if="loading" class="publish-loading">
      <LoaderCircle class="spin" :size="20" />
      <span>Checking ready media assets and provider connections…</span>
    </div>

    <!-- Main Content -->
    <template v-else>
      <!-- Channel Destination Selector -->
      <div class="section-block">
        <span class="block-label">Select Destination Channels</span>
        <div class="destination-grid">
          <!-- YouTube Card -->
          <div class="destination-card youtube-card" :class="{ active: platforms.includes('youtube'), offline: !providerMap.get('youtube')?.available && !isConnected('youtube') }">
            <button type="button" class="card-main-area" @click="isConnected('youtube') ? togglePlatform('youtube') : connectPlatform('youtube')">
              <div class="card-icon-wrap yt-wrap">
                <img v-if="accountPicture('youtube')" :src="accountPicture('youtube') ?? undefined" class="account-avatar" alt="YouTube channel" />
                <PlaySquare v-else :size="22" class="yt-icon" />
              </div>
              <div class="card-info">
                <div class="card-title-row">
                  <strong>YouTube</strong>
                  <span v-if="platforms.includes('youtube') && isConnected('youtube')" class="status-pill active-pill">Selected</span>
                  <span v-else-if="isConnected('youtube')" class="status-pill connected-pill">Connected</span>
                  <span v-else class="status-pill disconnected-pill">Not connected</span>
                </div>
                <span v-if="isConnected('youtube')" class="provider-detail connected-detail">
                  <CheckCircle2 :size="11" class="check-inline" />
                  {{ accountName('youtube') || 'YouTube Channel' }}
                </span>
                <span v-else class="provider-detail connect-cta">
                  <LoaderCircle v-if="connectingPlatform === 'youtube'" :size="11" class="spin" />
                  {{ connectingPlatform === 'youtube' ? 'Opening Google login…' : 'Click to connect your YouTube channel' }}
                </span>
              </div>
            </button>
            <button v-if="isConnected('youtube')" type="button" class="disconnect-btn" title="Disconnect YouTube" @click.stop="disconnectPlatform('youtube')">
              <X :size="12" />
            </button>
          </div>

          <!-- Facebook Card -->
          <div class="destination-card fb-card" :class="{ active: platforms.includes('facebook'), offline: !providerMap.get('facebook')?.available && !isConnected('facebook') }">
            <button type="button" class="card-main-area" @click="isConnected('facebook') ? togglePlatform('facebook') : connectPlatform('facebook')">
              <div class="card-icon-wrap fb-wrap">
                <img v-if="accountPicture('facebook')" :src="accountPicture('facebook') ?? undefined" class="account-avatar" alt="Facebook page" />
                <Share2 v-else :size="20" class="fb-icon" />
              </div>
              <div class="card-info">
                <div class="card-title-row">
                  <strong>Facebook</strong>
                  <span v-if="platforms.includes('facebook') && isConnected('facebook')" class="status-pill active-pill">Selected</span>
                  <span v-else-if="isConnected('facebook')" class="status-pill connected-pill">Connected</span>
                  <span v-else class="status-pill disconnected-pill">Not connected</span>
                </div>
                <span v-if="isConnected('facebook')" class="provider-detail connected-detail">
                  <CheckCircle2 :size="11" class="check-inline" />
                  {{ accountName('facebook') || 'Facebook Page' }}
                </span>
                <span v-else class="provider-detail connect-cta">
                  <LoaderCircle v-if="connectingPlatform === 'facebook'" :size="11" class="spin" />
                  {{ connectingPlatform === 'facebook' ? 'Opening Facebook login…' : 'Click to connect your Facebook Page' }}
                </span>
              </div>
            </button>
            <button v-if="isConnected('facebook')" type="button" class="disconnect-btn" title="Disconnect Facebook" @click.stop="disconnectPlatform('facebook')">
              <X :size="12" />
            </button>
          </div>
        </div>
      </div>

      <!-- Scope Selector (Final Cut vs Scene vs Shot) -->
      <div class="section-block">
        <span class="block-label">Publish Scope</span>
        <div class="publish-scope" role="group" aria-label="Publish source">
          <button type="button" :class="{ active: scope === 'final' }" @click="scope = 'final'">
            <span>Final Cut</span>
            <small class="count-badge">{{ plan?.final.length || 0 }}</small>
          </button>
          <button v-if="!props.compilationId" type="button" :class="{ active: scope === 'scenes' }" @click="scope = 'scenes'">
            <span>Scene by Scene</span>
            <small class="count-badge">{{ plan?.scenes.filter((item) => item.ready).length || 0 }}/{{ plan?.scenes.length || 0 }}</small>
          </button>
          <button v-if="!props.compilationId" type="button" :class="{ active: scope === 'shots' }" @click="scope = 'shots'">
            <span>Shot by Shot</span>
            <small class="count-badge">{{ plan?.shots.length || 0 }}</small>
          </button>
        </div>
      </div>

      <!-- Asset Picker -->
      <div class="section-block asset-picker">
        <div class="asset-picker-head">
          <div class="picker-summary">
            <strong>{{ selectedAssets.length }} video{{ selectedAssets.length === 1 ? '' : 's' }} selected</strong>
            <span class="picker-hint">Ready for release</span>
          </div>
          <div class="picker-actions">
            <button
              v-if="unuploadedAssets.length > 0 && publishedAssetMap.size > 0"
              type="button"
              class="btn-text highlight"
              @click="selectOnlyUnpublished"
            >
              Select Unuploaded Only ({{ unuploadedAssets.length }})
            </button>
            <span v-if="unuploadedAssets.length > 0 && publishedAssetMap.size > 0" class="divider-dot">·</span>
            <button type="button" class="btn-text" @click="selectAll">Select All</button>
            <span class="divider-dot">·</span>
            <button type="button" class="btn-text" @click="clearSelection">Reset</button>
          </div>
        </div>

        <div v-if="!activeAssets.length" class="empty-assets">
          <p>No {{ scope }} videos are ready yet.</p>
          <small>Render the final cut or generate clips first to enable direct export.</small>
        </div>

        <div v-else class="asset-list">
          <button
            v-for="asset in activeAssets"
            :key="asset.id"
            type="button"
            class="asset-row"
            :class="{
              selected: !selected.length || selected.includes(asset.id),
              disabled: !asset.ready,
            }"
            :disabled="!asset.ready"
            @click="toggleAsset(asset.id)"
          >
            <div class="asset-checkbox">
              <CheckCircle2 v-if="asset.ready" :size="16" class="check-icon" />
              <Clock3 v-else :size="16" class="clock-icon" />
            </div>
            <div class="asset-title-block">
              <strong>{{ asset.label }}</strong>
              <a
                v-if="publishedAssetMap.has(asset.id)"
                :href="publishedAssetMap.get(asset.id)?.url"
                target="_blank"
                rel="noopener"
                class="uploaded-pill"
                title="View on YouTube"
                @click.stop
              >
                <CheckCircle2 :size="11" /> Uploaded
                <ExternalLink :size="10" />
              </a>
              <span v-else-if="asset.ready" class="pending-pill">Ready to Upload</span>
            </div>
            <div class="asset-meta">
              <span v-if="asset.ready" class="duration-chip">{{ formatDuration(asset.duration_ms) }}</span>
              <span v-else class="missing-chip">{{ asset.missing_count }} missing clip{{ asset.missing_count === 1 ? '' : 's' }}</span>
            </div>
          </button>
        </div>
      </div>

      <!-- Release kit: cover + suggested titles -->
      <div class="section-block kit-block">
        <div class="kit-block-head">
          <span class="block-label"><ImageIcon :size="12" /> Cover</span>
          <button type="button" class="btn-text highlight" @click="emit('open-kit')">
            <Package :size="12" /> {{ kit?.covers.length || kit?.title_options.length ? 'Edit release kit' : 'Create covers & titles with AI' }}
          </button>
        </div>
        <div class="kit-cover-row" :class="{ shorts: props.variant === 'shorts' }" role="radiogroup" aria-label="Cover">
          <button type="button" role="radio" class="kit-cover none" :class="{ active: !coverId }" :aria-checked="!coverId" @click="coverId = null">
            <span>Platform default</span>
          </button>
          <button
            v-for="cover in kitCovers"
            :key="cover.id"
            type="button"
            role="radio"
            class="kit-cover"
            :class="{ active: coverId === cover.id }"
            :aria-checked="coverId === cover.id"
            :aria-label="`Cover ${cover.headline || cover.concept}`"
            @click="coverId = cover.id"
          >
            <img :src="cover.url || ''" :alt="cover.headline || cover.concept" />
            <CheckCircle2 v-if="coverId === cover.id" :size="16" class="kit-cover-check" />
          </button>
        </div>
        <small v-if="selectedCover && platforms.includes('youtube')" class="kit-note">YouTube applies custom thumbnails only on channels with phone verification; if it refuses, the video still publishes.</small>
      </div>

      <!-- Metadata Grid -->
      <div class="section-block metadata-block">
        <span class="block-label">Video Metadata & SEO</span>
        <div class="metadata-grid">
          <label class="form-field title-field">
            <span class="field-label"><FileText :size="12" /> Video Title</span>
            <input v-model="title" maxlength="180" placeholder="Give your release a compelling title…" />
          </label>

          <label class="form-field tags-field">
            <span class="field-label"><Tag :size="12" /> Tags (comma separated)</span>
            <input v-model="tagsText" placeholder="fitness, workout, routine, health" />
          </label>

          <div v-if="kit?.title_options.length" class="title-suggestions" aria-label="Suggested titles">
            <button
              v-for="option in kit.title_options"
              :key="option.id"
              type="button"
              class="title-chip"
              :class="{ active: title === option.text }"
              @click="useTitle(option.text)"
            >{{ option.text }}</button>
          </div>
          <label class="form-field desc-field">
            <span class="field-label"><FileText :size="12" /> Description</span>
            <textarea
              v-model="description"
              rows="3"
              placeholder="Write the description and credits that should accompany the video release…"
            ></textarea>
          </label>
        </div>
      </div>

      <!-- Release Options (Privacy & Schedule) -->
      <div class="section-block options-block">
        <span class="block-label">Release Visibility & Timing</span>
        <div class="publish-options">
          <label v-if="platforms.includes('youtube')" class="form-field">
            <span class="field-label"><Eye :size="12" /> YouTube Visibility</span>
            <select v-model="privacy.youtube">
              <option value="private">Private (Only you)</option>
              <option value="unlisted">Unlisted (Anyone with link)</option>
              <option value="public">Public (Everyone)</option>
            </select>
          </label>

          <label v-if="platforms.includes('facebook')" class="form-field">
            <span class="field-label"><Eye :size="12" /> Facebook Audience</span>
            <select v-model="privacy.facebook">
              <option value="self">Only me</option>
              <option value="friends">Friends</option>
              <option value="public">Public</option>
            </select>
          </label>

          <label class="form-field">
            <span class="field-label"><Calendar :size="12" /> Schedule (Optional)</span>
            <input v-model="scheduledAt" type="datetime-local" />
          </label>

          <label v-if="scheduledAt && selectedAssets.length > 1" class="form-field">
            <span class="field-label"><Clock3 :size="12" /> Stagger Interval</span>
            <select v-model="scheduleInterval">
              <option :value="0">Release all at once (same time)</option>
              <option :value="24">1 video per day (Every 24 hours)</option>
              <option :value="48">Every 2 days (48 hours)</option>
              <option :value="72">Every 3 days (72 hours)</option>
              <option :value="168">1 video per week (7 days)</option>
              <option :value="12">Twice daily (Every 12 hours)</option>
            </select>
          </label>

          <label class="form-field checkbox-field">
            <input v-model="madeForKids" type="checkbox" />
            <span>Made for kids</span>
          </label>
        </div>
        <div
          v-if="scheduledAt && Number(scheduleInterval) > 0 && selectedAssets.length > 1"
          class="stagger-preview-banner"
        >
          <span>
            📅 <strong>{{ selectedAssets.length }} videos</strong> will be scheduled 1-by-1 starting
            <strong>{{ formatDateTime(scheduledAt) }}</strong> through
            <strong>{{ formatEndDateTime(scheduledAt, Number(scheduleInterval), selectedAssets.length) }}</strong>
            ({{ Number(scheduleInterval) === 24 ? '1 video per day' : 'every ' + scheduleInterval + ' hours' }}).
          </span>
        </div>
      </div>

      <!-- Live Job Status -->
      <div v-if="job" class="publish-job" :class="job.status">
        <LoaderCircle v-if="job.status === 'running' || job.status === 'queued'" class="spin" :size="16" />
        <CheckCircle2 v-else-if="job.status === 'completed'" :size="16" class="job-success-icon" />
        <div class="job-details">
          <strong>{{ job.message || 'Processing publish job…' }}</strong>
          <div class="job-bar-wrap">
            <div class="job-bar" :style="{ width: `${Math.round(job.progress * 100)}%` }"></div>
          </div>
        </div>
        <span class="job-pct">{{ Math.round(job.progress * 100) }}%</span>
      </div>

      <!-- Error Alert -->
      <div v-if="error" class="form-error">
        <X :size="14" />
        <span>{{ error }}</span>
      </div>

      <!-- Footer Action Bar -->
      <footer class="panel-footer">
        <div class="footer-summary">
          <span class="summary-badge">
            {{ selectedAssets.length }} video{{ selectedAssets.length === 1 ? '' : 's' }} ×
            {{ platforms.length }} channel{{ platforms.length === 1 ? '' : 's' }}
          </span>
        </div>

        <div class="footer-buttons">
          <button v-if="props.asModal" type="button" class="btn-cancel" @click="emit('close')">
            Cancel
          </button>
          <button class="btn-publish" type="button" :disabled="!canPublish" @click="publish">
            <LoaderCircle v-if="publishing" class="spin" :size="16" />
            <Send v-else :size="16" />
            <span>{{ publishing ? 'Publishing…' : `Publish to ${platforms.map(platformLabel).join(' & ')}` }}</span>
          </button>
        </div>
      </footer>
    </template>

    <!-- Past Release History -->
    <details v-if="records.length" class="publish-history">
      <summary class="history-toggle">
        <span>Recent Publish History ({{ records.length }})</span>
      </summary>
      <div class="history-list">
        <article v-for="record in records.slice(0, 5)" :key="record.id" class="history-item">
          <div class="history-item-info">
            <strong>{{ record.title }}</strong>
            <span class="history-item-meta">
              {{ record.platforms.map(platformLabel).join(' + ') }} · {{ record.scope }} · {{ record.status }}
            </span>
          </div>
          <div class="history-links">
            <a
              v-for="result in resultLinks(record)"
              :key="String(result.external_url)"
              :href="String(result.external_url)"
              target="_blank"
              rel="noopener"
              class="history-ext-link"
            >
              <ExternalLink :size="12" />
              <span>{{ result.platform }}</span>
            </a>
          </div>
        </article>
      </div>
    </details>
  </section>
</template>

<style scoped>
.publish-panel {
  display: grid;
  gap: 18px;
  padding: 24px;
  background: #171b20;
  color: #e5edf4;
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.5);
  font-family: inherit;
}

.publish-panel.is-modal {
  max-height: 88vh;
  overflow-y: auto;
  border: none;
  box-shadow: none;
}

/* Header */
.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.header-text {
  display: grid;
  gap: 6px;
}

.header-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1.2px;
  color: #38bdf8;
}

.badge-icon {
  color: #38bdf8;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.title-row h3 {
  margin: 0;
  font-size: 19px;
  font-weight: 700;
  color: #ffffff;
  letter-spacing: -0.3px;
}

.variant-tag {
  font-size: 11px;
  font-weight: 700;
  padding: 3px 8px;
  background: rgba(255, 255, 255, 0.08);
  border-radius: 6px;
  color: #94a3b8;
}

.header-text p {
  margin: 0;
  color: #8da2b5;
  font-size: 12px;
  line-height: 17px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.icon-refresh {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.05);
  color: #cbd5e1;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.icon-refresh:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}

.close-btn {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.05);
  color: #94a3b8;
  display: grid;
  place-items: center;
  cursor: pointer;
  transition: all 0.15s ease;
}

.close-btn:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #ffffff;
}

/* Loading */
.publish-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 40px 20px;
  color: #94a3b8;
  font-size: 13px;
}

/* Section Blocks */
.section-block {
  display: grid;
  gap: 8px;
}

.block-label {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.8px;
  color: #94a3b8;
  text-transform: uppercase;
}

/* Destination Channels */
.destination-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.destination-card {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  background: #1f252d;
  color: #ffffff;
  text-align: left;
  transition: all 0.18s ease;
  overflow: hidden;
  position: relative;
}

.destination-card:hover {
  border-color: rgba(255, 255, 255, 0.2);
  background: #252d36;
}

.destination-card.active.youtube-card {
  border-color: #ef4444;
  background: rgba(239, 68, 68, 0.08);
  box-shadow: 0 0 0 1px #ef4444, 0 4px 16px rgba(239, 68, 68, 0.12);
}

.destination-card.active.fb-card {
  border-color: #3b82f6;
  background: rgba(59, 130, 246, 0.08);
  box-shadow: 0 0 0 1px #3b82f6, 0 4px 16px rgba(59, 130, 246, 0.12);
}

.card-main-area {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 14px 16px;
  flex: 1;
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.disconnect-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 100%;
  padding: 0;
  background: rgba(239, 68, 68, 0.07);
  border: none;
  border-left: 1px solid rgba(255, 255, 255, 0.06);
  color: #94a3b8;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.disconnect-btn:hover {
  background: rgba(239, 68, 68, 0.2);
  color: #ef4444;
}

.account-avatar {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  object-fit: cover;
}

.card-icon-wrap {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.yt-wrap {
  background: rgba(239, 68, 68, 0.15);
  color: #ef4444;
}

.fb-wrap {
  background: rgba(59, 130, 246, 0.15);
  color: #3b82f6;
}

.card-info {
  display: grid;
  gap: 2px;
  flex: 1;
}

.card-title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.card-title-row strong {
  font-size: 14px;
  font-weight: 700;
}

.status-pill {
  font-size: 10px;
  font-weight: 800;
  padding: 2px 7px;
  border-radius: 99px;
}

.active-pill {
  background: #38bdf8;
  color: #0c2336;
}

.connected-pill {
  background: rgba(74, 222, 128, 0.2);
  color: #4ade80;
}

.disconnected-pill {
  background: rgba(148, 163, 184, 0.12);
  color: #64748b;
}

.provider-detail {
  font-size: 11px;
  color: #8da2b5;
  display: flex;
  align-items: center;
  gap: 4px;
}

.connected-detail {
  color: #4ade80;
}

.connect-cta {
  color: #60a5fa;
  display: flex;
  align-items: center;
  gap: 4px;
}

.check-inline {
  color: #4ade80;
}

/* Scope Selector */
.publish-scope {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  background: #121519;
  padding: 4px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.publish-scope button {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 10px 14px;
  border-radius: 8px;
  border: none;
  background: transparent;
  color: #94a3b8;
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.publish-scope button:hover {
  color: #ffffff;
}

.publish-scope button.active {
  background: #252d36;
  color: #ffffff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}

.count-badge {
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.12);
  color: #e2e8f0;
}

/* Asset Picker */
.asset-picker-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 2px;
}

.picker-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.picker-summary strong {
  color: #ffffff;
}

.picker-hint {
  color: #8da2b5;
  font-size: 11px;
}

.picker-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.btn-text {
  border: none;
  background: none;
  color: #38bdf8;
  font-weight: 700;
  cursor: pointer;
  font-size: 12px;
  padding: 0;
}

.btn-text:hover {
  text-decoration: underline;
}

.divider-dot {
  color: #475569;
}

.empty-assets {
  padding: 24px;
  border: 1px dashed rgba(255, 255, 255, 0.15);
  border-radius: 12px;
  text-align: center;
  color: #8da2b5;
  background: #14171c;
}

.empty-assets p {
  margin: 0 0 4px;
  font-weight: 600;
  color: #ffffff;
}

.empty-assets small {
  font-size: 12px;
}

.asset-list {
  display: grid;
  gap: 6px;
  max-height: 220px;
  overflow-y: auto;
  padding-right: 4px;
}

.asset-row {
  display: grid;
  grid-template-columns: 24px 1fr auto;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: #1f252d;
  color: #e5edf5;
  cursor: pointer;
  text-align: left;
  transition: all 0.12s ease;
}

.asset-row:hover:not(:disabled) {
  background: #252d36;
  border-color: rgba(255, 255, 255, 0.15);
}

.asset-row.selected {
  border-color: #38bdf8;
  background: rgba(56, 189, 248, 0.08);
}

.asset-row.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.check-icon {
  color: #38bdf8;
}

.clock-icon {
  color: #94a3b8;
}

.asset-title-block strong {
  font-size: 13px;
  font-weight: 600;
}

.duration-chip {
  font-size: 11px;
  font-weight: 700;
  padding: 2px 7px;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.08);
  color: #94a3b8;
}

.missing-chip {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 7px;
  border-radius: 6px;
  background: rgba(239, 68, 68, 0.15);
  color: #f87171;
}

/* Metadata */
.metadata-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.desc-field {
  grid-column: 1 / -1;
}

.form-field {
  display: grid;
  gap: 6px;
}

.field-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 700;
  color: #94a3b8;
}

.form-field input,
.form-field textarea,
.form-field select {
  width: 100%;
  padding: 10px 14px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: #121519;
  color: #ffffff;
  font-family: inherit;
  font-size: 13px;
  transition: all 0.15s ease;
  box-sizing: border-box;
}

.form-field input:focus,
.form-field textarea:focus,
.form-field select:focus {
  outline: none;
  border-color: #38bdf8;
  box-shadow: 0 0 0 2px rgba(56, 189, 248, 0.2);
}

.form-field textarea {
  resize: vertical;
  min-height: 70px;
}

/* Options */
.publish-options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  align-items: end;
}

.checkbox-field {
  display: flex !important;
  flex-direction: row;
  align-items: center;
  gap: 10px;
  padding-bottom: 10px;
  cursor: pointer;
  color: #e2e8f0;
  font-size: 13px;
  font-weight: 600;
}

.checkbox-field input {
  width: 18px;
  height: 18px;
  accent-color: #38bdf8;
  cursor: pointer;
}

/* Job progress */
.publish-job {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 12px;
  background: #1f252d;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.job-details {
  flex: 1;
  display: grid;
  gap: 6px;
}

.job-details strong {
  font-size: 13px;
  color: #ffffff;
}

.job-bar-wrap {
  height: 5px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.1);
  overflow: hidden;
}

.job-bar {
  height: 100%;
  border-radius: 99px;
  background: #38bdf8;
  transition: width 0.3s ease;
}

.job-pct {
  font-size: 13px;
  font-weight: 800;
  color: #38bdf8;
}

.job-success-icon {
  color: #10b981;
}

/* Error */
.form-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-radius: 10px;
  background: rgba(239, 68, 68, 0.12);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #f87171;
  font-size: 12px;
  font-weight: 600;
}

/* Footer */
.panel-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-top: 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.summary-badge {
  font-size: 12px;
  color: #94a3b8;
  font-weight: 600;
}

.footer-buttons {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-cancel {
  padding: 10px 18px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.05);
  color: #cbd5e1;
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
  transition: all 0.15s ease;
}

.btn-cancel:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #ffffff;
}

.btn-publish {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 11px 22px;
  border-radius: 10px;
  border: none;
  background: #ef4444;
  color: #ffffff;
  font-size: 13px;
  font-weight: 800;
  cursor: pointer;
  box-shadow: 0 4px 16px rgba(239, 68, 68, 0.35);
  transition: all 0.18s ease;
}

.btn-publish:hover:not(:disabled) {
  background: #dc2626;
  box-shadow: 0 6px 20px rgba(239, 68, 68, 0.45);
  transform: translateY(-1px);
}

.btn-publish:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  box-shadow: none;
}

/* History */
.publish-history {
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 12px;
}

.history-toggle {
  cursor: pointer;
  font-size: 12px;
  font-weight: 700;
  color: #94a3b8;
  transition: color 0.15s ease;
}

.history-toggle:hover {
  color: #ffffff;
}

.history-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-radius: 10px;
  background: #14171c;
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.history-item-info {
  display: grid;
  gap: 2px;
}

.history-item-info strong {
  font-size: 13px;
  color: #ffffff;
}

.history-item-meta {
  font-size: 11px;
  color: #8da2b5;
}

.history-links {
  display: flex;
  gap: 8px;
}

.history-ext-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(56, 189, 248, 0.1);
  color: #38bdf8;
  font-size: 11px;
  font-weight: 700;
  text-decoration: none;
  transition: all 0.15s ease;
}

.history-ext-link:hover {
  background: rgba(56, 189, 248, 0.2);
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 768px) {
  .destination-grid,
  .publish-scope,
  .metadata-grid,
  .publish-options {
    grid-template-columns: 1fr;
  }
}

.stagger-preview-banner {
  margin-top: 14px;
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(56, 189, 248, 0.1);
  border: 1px solid rgba(56, 189, 248, 0.25);
  color: #bae6fd;
  font-size: 12px;
  line-height: 1.5;
}
.stagger-preview-banner strong {
  color: #38bdf8;
}


.asset-title-block {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.uploaded-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(34, 197, 94, 0.15);
  border: 1px solid rgba(34, 197, 94, 0.35);
  color: #4ade80;
  font-size: 11px;
  font-weight: 500;
  text-decoration: none;
  transition: all 0.15s ease;
}
.uploaded-pill:hover {
  background: rgba(34, 197, 94, 0.25);
  color: #86efac;
}
.pending-pill {
  display: inline-flex;
  align-items: center;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.25);
  color: #fbbf24;
  font-size: 11px;
  font-weight: 500;
}
.btn-text.highlight {
  color: #38bdf8 !important;
  font-weight: 600;
}

.kit-block-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.kit-block-head .block-label,
.kit-block-head .btn-text {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.kit-cover-row {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  padding: 8px 0 4px;
}

.kit-cover {
  position: relative;
  flex: 0 0 auto;
  width: 136px;
  aspect-ratio: 16 / 9;
  padding: 0;
  overflow: hidden;
  border: 2px solid rgba(148, 163, 184, 0.25);
  border-radius: 10px;
  background: #0b0f14;
  color: #94a3b8;
  font-size: 11px;
  cursor: pointer;
}

.kit-cover-row.shorts .kit-cover {
  width: 76px;
  aspect-ratio: 9 / 16;
}

.kit-cover.none {
  display: grid;
  place-items: center;
  border-style: dashed;
}

.kit-cover.active {
  border-color: #38bdf8;
  box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.2);
}

.kit-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.kit-cover-check {
  position: absolute;
  top: 5px;
  right: 5px;
  color: #38bdf8;
  background: #0b0f14;
  border-radius: 50%;
}

.kit-note {
  display: block;
  margin-top: 4px;
  color: #94a3b8;
  font-size: 11px;
}

.title-suggestions {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: -4px;
}

.title-chip {
  max-width: 100%;
  padding: 5px 10px;
  border: 1px solid rgba(148, 163, 184, 0.25);
  border-radius: 999px;
  background: transparent;
  color: #cbd5e1;
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.title-chip:hover,
.title-chip.active {
  border-color: #38bdf8;
  color: #e0f2fe;
  background: rgba(56, 189, 248, 0.12);
}

</style>
