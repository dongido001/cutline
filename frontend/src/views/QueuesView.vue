<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Clock,
  Copy,
  ExternalLink,
  FileText,
  Film,
  Filter,
  Image as ImageIcon,
  Layers,
  Loader2,
  Maximize2,
  Mic,
  MoreVertical,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Server,
  Sparkles,
  Square,
  StopCircle,
  Terminal,
  Trash2,
  Video,
  X,
  Zap,
} from '@lucide/vue'
import { api, type QueueJobItem, type QueueStat, type QueueSystemStatus, type BridgeStatus, type SystemService } from '@/lib/api'
import { cutlineWs } from '@/lib/ws'
import { UiBadge, UiButton, UiCard, UiDialog, UiIconButton, UiInput, UiSelect, UiTooltip } from '@/components/ui'

const router = useRouter()

// State
const currentTab = ref<'jobs' | 'services'>('jobs')
const loading = ref(true)
const autoRefresh = ref(true)
const autoRefreshInterval = ref(3000)
let pollTimer: number | undefined = undefined

const systemStatus = ref<QueueSystemStatus | null>(null)
const jobs = ref<QueueJobItem[]>([])
const selectedJob = ref<QueueJobItem | null>(null)
const inspectOpen = ref(false)

// Services State
const services = ref<SystemService[]>([])
const servicesLoading = ref(false)
const serviceActionBusyId = ref<string | null>(null)
const restartingAll = ref(false)

// Logs Modal State
const logsModalOpen = ref(false)
const activeServiceForLogs = ref<SystemService | null>(null)
const activeServiceLogs = ref<string>('')
const logsLoading = ref(false)
const logsAutoRefresh = ref(true)
let logsPollTimer: number | undefined = undefined

// Action states
const actionBusyId = ref<string | null>(null)
const cancellingAll = ref(false)
const cleaningUp = ref(false)
const flushingQueueId = ref<string | null>(null)
const actionNotification = ref<{ message: string; type: 'success' | 'error' } | null>(null)

// Filters
const statusFilter = ref<'all' | 'active' | 'failed' | 'completed' | 'cancelled'>('all')
const kindFilter = ref<string>('all')
const searchQuery = ref('')

const availableKinds = [
  { value: 'all', label: 'All Task Types' },
  { value: 'shot_image', label: 'Storyboard Frames (shot_image)' },
  { value: 'shot_clip', label: 'Video Clips (shot_clip)' },
  { value: 'create_draft', label: 'Draft Batches (create_draft)' },
  { value: 'script_analysis', label: 'Script Analysis' },
  { value: 'continuation', label: 'Continuations' },
  { value: 'cast_reference', label: 'Cast Faces' },
  { value: 'final_cut', label: 'Final Cut' },
  { value: 'audio', label: 'Audio & Narration' },
]

// Computed
const filteredJobs = computed(() => {
  let list = jobs.value
  if (statusFilter.value === 'active') {
    list = list.filter((j) => j.status === 'running' || j.status === 'queued')
  } else if (statusFilter.value !== 'all') {
    list = list.filter((j) => j.status === statusFilter.value)
  }

  if (kindFilter.value !== 'all') {
    list = list.filter((j) => j.kind === kindFilter.value)
  }

  if (searchQuery.value.trim()) {
    const q = searchQuery.value.trim().toLowerCase()
    list = list.filter(
      (j) =>
        j.id.toLowerCase().includes(q) ||
        (j.project_title && j.project_title.toLowerCase().includes(q)) ||
        (j.message && j.message.toLowerCase().includes(q)) ||
        (j.error && j.error.toLowerCase().includes(q)) ||
        (j.provider && j.provider.toLowerCase().includes(q)),
    )
  }
  return list
})

// Pagination State
const currentPage = ref(1)
const pageSize = ref(15)
const pageSizeOptions = [
  { value: '10', label: '10 / page' },
  { value: '15', label: '15 / page' },
  { value: '25', label: '25 / page' },
  { value: '50', label: '50 / page' },
  { value: '100', label: '100 / page' },
]

const totalPages = computed(() => Math.max(1, Math.ceil(filteredJobs.value.length / pageSize.value)))

const paginatedJobs = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredJobs.value.slice(start, start + pageSize.value)
})

const paginationRangeStart = computed(() => {
  if (filteredJobs.value.length === 0) return 0
  return (currentPage.value - 1) * pageSize.value + 1
})

const paginationRangeEnd = computed(() => {
  return Math.min(filteredJobs.value.length, currentPage.value * pageSize.value)
})

const displayedPages = computed(() => {
  const total = totalPages.value
  const current = currentPage.value
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }
  const pages: (number | 'ellipsis')[] = []
  pages.push(1)
  if (current > 3) {
    pages.push('ellipsis')
  }
  const start = Math.max(2, current - 1)
  const end = Math.min(total - 1, current + 1)
  for (let i = start; i <= end; i++) {
    pages.push(i)
  }
  if (current < total - 2) {
    pages.push('ellipsis')
  }
  pages.push(total)
  return pages
})

function setPage(page: number) {
  if (page >= 1 && page <= totalPages.value) {
    currentPage.value = page
  }
}

watch([statusFilter, kindFilter, searchQuery, pageSize], () => {
  currentPage.value = 1
})

watch(totalPages, (newTotal) => {
  if (currentPage.value > newTotal) {
    currentPage.value = newTotal
  }
})


const activeJobsCount = computed(() => systemStatus.value?.active_jobs ?? jobs.value.filter((j) => j.status === 'running' || j.status === 'queued').length)
const failedJobsCount = computed(() => systemStatus.value?.failed_jobs ?? jobs.value.filter((j) => j.status === 'failed').length)
const completedJobsCount = computed(() => systemStatus.value?.completed_jobs ?? jobs.value.filter((j) => j.status === 'completed').length)
const totalJobsCount = computed(() => systemStatus.value?.total_jobs ?? jobs.value.length)

const runningServicesCount = computed(() => services.value.filter((s) => s.status === 'running').length)
const servicesWithWarnings = computed(() => services.value.filter((s) => s.status === 'warning').length)

function notify(message: string, type: 'success' | 'error' = 'success') {
  actionNotification.value = { message, type }
  setTimeout(() => {
    if (actionNotification.value?.message === message) actionNotification.value = null
  }, 4000)
}

async function loadServices(silent = false) {
  if (!silent) servicesLoading.value = true
  try {
    services.value = await api.systemServices()
  } catch (err) {
    if (!silent) notify(err instanceof Error ? err.message : 'Could not fetch services', 'error')
  } finally {
    if (!silent) servicesLoading.value = false
  }
}

async function handleServiceAction(serviceId: string, action: 'start' | 'stop' | 'restart') {
  serviceActionBusyId.value = `${serviceId}:${action}`
  try {
    let res: { ok: boolean; message?: string }
    if (action === 'start') {
      res = await api.startService(serviceId)
    } else if (action === 'stop') {
      res = await api.stopService(serviceId)
    } else {
      res = await api.restartService(serviceId)
    }
    notify(res.message || `Service ${action} succeeded`, res.ok ? 'success' : 'error')
    await Promise.all([loadServices(true), loadData(true)])
  } catch (err) {
    notify(err instanceof Error ? err.message : `Failed to ${action} service`, 'error')
  } finally {
    serviceActionBusyId.value = null
  }
}

async function restartAllServices() {
  restartingAll.value = true
  try {
    for (const svc of services.value) {
      if (svc.status !== 'stopped') {
        await api.restartService(svc.id).catch(() => {})
      }
    }
    notify('Restarted all active services', 'success')
    await Promise.all([loadServices(true), loadData(true)])
  } catch (err) {
    notify(err instanceof Error ? err.message : 'Failed to restart services', 'error')
  } finally {
    restartingAll.value = false
  }
}

async function openLogs(service: SystemService) {
  activeServiceForLogs.value = service
  logsModalOpen.value = true
  await fetchLogs()
  startLogsPolling()
}

async function fetchLogs() {
  if (!activeServiceForLogs.value) return
  logsLoading.value = true
  try {
    const res = await api.serviceLogs(activeServiceForLogs.value.id, 100)
    activeServiceLogs.value = res.logs
  } catch (err) {
    activeServiceLogs.value = `Failed to load logs: ${err instanceof Error ? err.message : String(err)}`
  } finally {
    logsLoading.value = false
  }
}

function startLogsPolling() {
  stopLogsPolling()
  logsPollTimer = window.setInterval(() => {
    if (logsModalOpen.value && logsAutoRefresh.value && activeServiceForLogs.value) {
      void fetchLogs()
    }
  }, 2500)
}

function stopLogsPolling() {
  if (logsPollTimer !== undefined) {
    clearInterval(logsPollTimer)
    logsPollTimer = undefined
  }
}

function copyLogs() {
  if (!activeServiceLogs.value) return
  void navigator.clipboard.writeText(activeServiceLogs.value)
  notify('Logs copied to clipboard', 'success')
}

async function loadData(silent = false) {
  if (!silent) loading.value = true
  try {
    const [sys, all] = await Promise.all([
      api.systemQueues(),
      api.allJobs({ limit: 120 }),
      loadServices(true),
    ])
    systemStatus.value = sys
    jobs.value = all
    if (selectedJob.value) {
      const updated = all.find((j) => j.id === selectedJob.value?.id)
      if (updated) selectedJob.value = updated
    }
  } catch (err) {
    if (!silent) notify(err instanceof Error ? err.message : 'Could not fetch queues', 'error')
  } finally {
    if (!silent) loading.value = false
  }
}

function startPolling() {
  stopPolling()
  if (autoRefresh.value) {
    pollTimer = window.setInterval(() => {
      void loadData(true)
    }, autoRefreshInterval.value)
  }
}

function stopPolling() {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

watch(autoRefresh, (enabled) => {
  if (enabled) startPolling()
  else stopPolling()
})

async function cancelJob(job: QueueJobItem) {
  actionBusyId.value = job.id
  try {
    await api.cancelJob(job.id)
    notify(`Job ${job.id.slice(0, 8)} cancelled`)
    await loadData(true)
  } catch (err) {
    notify(err instanceof Error ? err.message : 'Could not cancel job', 'error')
  } finally {
    actionBusyId.value = null
  }
}

async function retryJob(job: QueueJobItem) {
  actionBusyId.value = job.id
  try {
    await api.retryJob(job.id)
    notify(`Job ${job.id.slice(0, 8)} re-queued for execution`)
    await loadData(true)
  } catch (err) {
    notify(err instanceof Error ? err.message : 'Could not retry job', 'error')
  } finally {
    actionBusyId.value = null
  }
}

async function deleteJob(job: QueueJobItem) {
  if (!confirm(`Delete record for job ${job.id.slice(0, 8)}?`)) return
  actionBusyId.value = job.id
  try {
    await api.deleteJob(job.id)
    notify(`Job ${job.id.slice(0, 8)} deleted`)
    if (selectedJob.value?.id === job.id) inspectOpen.value = false
    await loadData(true)
  } catch (err) {
    notify(err instanceof Error ? err.message : 'Could not delete job', 'error')
  } finally {
    actionBusyId.value = null
  }
}

async function cancelAllActive() {
  if (!confirm(`Are you sure you want to cancel all ${activeJobsCount.value} active and queued jobs?`)) return
  cancellingAll.value = true
  try {
    const res = await api.cancelAllJobs()
    notify(`Cancelled ${res.cancelled} active jobs`)
    await loadData(true)
  } catch (err) {
    notify(err instanceof Error ? err.message : 'Could not cancel all jobs', 'error')
  } finally {
    cancellingAll.value = false
  }
}

async function cleanupFinished(statusType?: string) {
  const label = statusType ? `${statusType} jobs` : 'finished (completed & cancelled) jobs'
  if (!confirm(`Remove all ${label} from database history?`)) return
  cleaningUp.value = true
  try {
    const res = await api.cleanupJobs(statusType)
    notify(`Cleaned up ${res.deleted} records`)
    await loadData(true)
  } catch (err) {
    notify(err instanceof Error ? err.message : 'Could not clean up jobs', 'error')
  } finally {
    cleaningUp.value = false
  }
}

async function flushQueue(queueId: string) {
  if (!confirm(`Flush queue '${queueId}'? This clears any unscheduled tasks in this queue.`)) return
  flushingQueueId.value = queueId
  try {
    await api.flushQueue(queueId)
    notify(`Queue '${queueId}' flushed successfully`)
    await loadData(true)
  } catch (err) {
    notify(err instanceof Error ? err.message : 'Could not flush queue', 'error')
  } finally {
    flushingQueueId.value = null
  }
}

function openInspect(job: QueueJobItem) {
  selectedJob.value = job
  inspectOpen.value = true
}

function openProject(projectId: string) {
  void router.push(`/projects/${projectId}/shots`)
}

function formatKind(kind: string) {
  switch (kind) {
    case 'shot_image':
      return 'Storyboard Frame'
    case 'shot_clip':
      return 'Shot Video Clip'
    case 'create_draft':
      return 'Full Draft Generation'
    case 'script_analysis':
      return 'Script Scene Analysis'
    case 'script_continuation':
      return 'Story Continuation'
    case 'cast_reference':
      return 'Cast Face Reference'
    case 'final_cut':
      return 'Final Cut Render'
    default:
      return kind.replace(/_/g, ' ')
  }
}

function kindIcon(kind: string) {
  switch (kind) {
    case 'shot_image':
      return ImageIcon
    case 'shot_clip':
      return Video
    case 'create_draft':
      return Sparkles
    case 'script_analysis':
    case 'script_continuation':
      return Film
    case 'cast_reference':
      return Zap
    case 'final_cut':
      return Layers
    default:
      return Activity
  }
}

function formatTimestamp(value?: string | null) {
  if (!value) return '—'
  const hasTz = value.endsWith('Z') || /[+-]\d{2}:\d{2}$/.test(value)
  const normalized = hasTz ? value : value + 'Z'
  const date = new Date(normalized)
  return isNaN(date.getTime()) ? value : date.toLocaleTimeString()
}

function formatDuration(seconds?: number) {
  if (seconds === undefined || seconds === null) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const mins = Math.floor(seconds / 60)
  const rem = Math.round(seconds % 60)
  return `${mins}m ${rem}s`
}

function statusTone(status: string): 'success' | 'danger' | 'accent' | 'neutral' {
  switch (status) {
    case 'running':
    case 'queued':
      return 'accent'
    case 'completed':
      return 'success'
    case 'failed':
      return 'danger'
    default:
      return 'neutral'
  }
}

let unsubscribeWs: (() => void) | null = null

onMounted(() => {
  void loadData()
  startPolling()
  unsubscribeWs = cutlineWs.subscribe((event) => {
    if (!autoRefresh.value) return
    if (event.type === 'job.updated') {
      const idx = jobs.value.findIndex((j) => j.id === event.job.id)
      if (idx !== -1) {
        jobs.value[idx] = { ...jobs.value[idx], ...event.job }
        if (selectedJob.value?.id === event.job.id) {
          selectedJob.value = jobs.value[idx]
        }
      } else {
        void loadData(true)
      }
    }
  }, null)
})

onUnmounted(() => {
  if (unsubscribeWs) {
    unsubscribeWs()
    unsubscribeWs = null
  }
  stopPolling()
  stopLogsPolling()
})
</script>

<template>
  <main class="queues-dashboard">
    <!-- Header -->
    <header class="dashboard-header">
      <div class="header-left">
        <RouterLink to="/" class="back-link" title="Back to workspace">
          <ArrowLeft :size="16" />
          <span>Workspace</span>
        </RouterLink>
        <div class="title-wrap">
          <div class="title-row">
            <span class="pulse-indicator" :class="{ active: activeJobsCount > 0 }"></span>
            <h1>Queues & Workers</h1>
            <UiBadge v-if="activeJobsCount > 0" tone="accent">{{ activeJobsCount }} running / queued</UiBadge>
          </div>
          <p class="subtitle">Live monitor for background workers, video/image bridges, generation tasks, and Huey pipelines.</p>
        </div>
      </div>

      <div class="header-actions">
        <label class="auto-refresh-switch" title="Toggle 3s live polling">
          <input v-model="autoRefresh" type="checkbox" />
          <span class="refresh-indicator" :class="{ spinning: autoRefresh && loading }"></span>
          <span>Live updates (3s)</span>
        </label>

        <UiButton size="sm" variant="secondary" :disabled="loading" @click="loadData(false)">
          <RefreshCw :class="{ spin: loading }" :size="14" />
          <span>Refresh</span>
        </UiButton>

        <UiButton
          v-if="activeJobsCount > 0"
          size="sm"
          variant="danger"
          :disabled="cancellingAll"
          @click="cancelAllActive"
        >
          <StopCircle :size="14" />
          <span>{{ cancellingAll ? 'Cancelling…' : 'Cancel all active' }}</span>
        </UiButton>

        <div class="cleanup-dropdown">
          <UiButton size="sm" variant="ghost" :disabled="cleaningUp" @click="cleanupFinished()">
            <Trash2 :size="14" />
            <span>Clean history</span>
          </UiButton>
        </div>
      </div>
    </header>

    <!-- Notification Toast -->
    <div v-if="actionNotification" class="action-toast" :class="actionNotification.type" role="status">
      <CheckCircle2 v-if="actionNotification.type === 'success'" :size="16" />
      <AlertCircle v-else :size="16" />
      <span>{{ actionNotification.message }}</span>
      <button type="button" class="toast-close" @click="actionNotification = null"><X :size="13" /></button>
    </div>

    <!-- Main Navigation Tabs -->
    <nav class="main-view-tabs" role="tablist">
      <button
        type="button"
        class="main-tab-btn"
        :class="{ active: currentTab === 'jobs' }"
        role="tab"
        :aria-selected="currentTab === 'jobs'"
        @click="currentTab = 'jobs'"
      >
        <Activity :size="16" />
        <span>Tasks & Queue</span>
        <span v-if="activeJobsCount > 0" class="nav-tab-badge active">{{ activeJobsCount }} running</span>
        <span v-else class="nav-tab-badge">{{ jobs.length }}</span>
      </button>

      <button
        type="button"
        class="main-tab-btn"
        :class="{ active: currentTab === 'services' }"
        role="tab"
        :aria-selected="currentTab === 'services'"
        @click="currentTab = 'services'"
      >
        <Server :size="16" />
        <span>Services & Daemons</span>
        <span v-if="servicesWithWarnings > 0" class="nav-tab-badge warning">
          {{ servicesWithWarnings }} attention needed
        </span>
        <span v-else class="nav-tab-badge ready">
          {{ runningServicesCount }} active
        </span>
      </button>
    </nav>

    <!-- TAB 1: Tasks & Queue -->
    <template v-if="currentTab === 'jobs'">
      <!-- Stat Hero Cards -->
      <section class="metrics-grid">
      <div class="metric-card active-card" :class="{ has_active: activeJobsCount > 0 }">
        <div class="metric-icon"><Loader2 v-if="activeJobsCount > 0" class="spin" :size="20" /><Activity v-else :size="20" /></div>
        <div class="metric-data">
          <span class="metric-label">Active In-Flight</span>
          <strong class="metric-value">{{ activeJobsCount }}</strong>
          <small class="metric-hint">{{ activeJobsCount > 0 ? 'Worker execution in progress' : 'All worker queues idle' }}</small>
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-icon queued-icon"><Clock :size="20" /></div>
        <div class="metric-data">
          <span class="metric-label">Queued Jobs</span>
          <strong class="metric-value">{{ jobs.filter((j) => j.status === 'queued').length }}</strong>
          <small class="metric-hint">Awaiting available worker slot</small>
        </div>
      </div>

      <div class="metric-card" :class="{ has_failed: failedJobsCount > 0 }">
        <div class="metric-icon failed-icon"><AlertTriangle :size="20" /></div>
        <div class="metric-data">
          <span class="metric-label">Failed Jobs</span>
          <strong class="metric-value">{{ failedJobsCount }}</strong>
          <small class="metric-hint">{{ failedJobsCount > 0 ? 'Require inspection or retry' : 'No errors logged' }}</small>
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-icon completed-icon"><CheckCircle2 :size="20" /></div>
        <div class="metric-data">
          <span class="metric-label">Completed</span>
          <strong class="metric-value">{{ completedJobsCount }}</strong>
          <small class="metric-hint">Out of {{ totalJobsCount }} recorded jobs</small>
        </div>
      </div>
    </section>

    <!-- System Services & Bridges Bar -->
    <section class="system-infra-section">
      <div class="infra-panel">
        <div class="infra-header">
          <div class="infra-title"><Server :size="16" /><strong>Huey Task Queues</strong></div>
          <small>Internal SQLite work pipelines</small>
        </div>
        <div class="queues-pills">
          <div v-for="q in systemStatus?.queues" :key="q.id" class="queue-pill">
            <span class="status-dot ready"></span>
            <div class="queue-info">
              <span class="queue-name">{{ q.name }}</span>
              <small>{{ q.pending }} pending · {{ q.scheduled }} sched</small>
            </div>
            <button
              v-if="q.pending > 0"
              class="queue-flush-btn"
              title="Flush stuck tasks from this queue"
              :disabled="flushingQueueId === q.id"
              @click="flushQueue(q.id)"
            >
              {{ flushingQueueId === q.id ? '…' : 'Flush' }}
            </button>
          </div>
        </div>
      </div>

      <div class="infra-panel">
        <div class="infra-header">
          <div class="infra-title"><Zap :size="16" /><strong>External AI Bridges</strong></div>
          <button type="button" class="infra-action-btn" @click="currentTab = 'services'">
            Manage & Restart Daemons →
          </button>
        </div>
        <div class="bridges-list">
          <div v-for="b in systemStatus?.bridges" :key="b.id" class="bridge-card" :class="{ online: b.available }">
            <span class="status-dot" :class="{ ready: b.available, offline: !b.available }"></span>
            <div class="bridge-info">
              <strong>{{ b.name }}</strong>
              <small>{{ b.detail }}</small>
            </div>
            <UiBadge :tone="b.available ? 'success' : 'neutral'" size="sm">{{ b.available ? 'Ready' : 'Offline' }}</UiBadge>
          </div>
        </div>
      </div>
    </section>

    <!-- Main Jobs Table Section -->
    <section class="jobs-section">
      <div class="section-toolbar">
        <div class="status-tabs" role="tablist">
          <button
            type="button"
            class="tab-btn"
            :class="{ active: statusFilter === 'all' }"
            @click="statusFilter = 'all'"
          >
            All <span class="tab-count">{{ jobs.length }}</span>
          </button>
          <button
            type="button"
            class="tab-btn"
            :class="{ active: statusFilter === 'active' }"
            @click="statusFilter = 'active'"
          >
            Active <span class="tab-count badge-accent">{{ activeJobsCount }}</span>
          </button>
          <button
            type="button"
            class="tab-btn"
            :class="{ active: statusFilter === 'failed' }"
            @click="statusFilter = 'failed'"
          >
            Failed <span class="tab-count" :class="{ 'badge-danger': failedJobsCount > 0 }">{{ failedJobsCount }}</span>
          </button>
          <button
            type="button"
            class="tab-btn"
            :class="{ active: statusFilter === 'completed' }"
            @click="statusFilter = 'completed'"
          >
            Completed <span class="tab-count">{{ completedJobsCount }}</span>
          </button>
          <button
            type="button"
            class="tab-btn"
            :class="{ active: statusFilter === 'cancelled' }"
            @click="statusFilter = 'cancelled'"
          >
            Cancelled <span class="tab-count">{{ jobs.filter((j) => j.status === 'cancelled').length }}</span>
          </button>
        </div>

        <div class="search-filter-group">
          <div class="kind-select-wrapper">
            <select v-model="kindFilter" class="kind-select" aria-label="Filter by task type">
              <option v-for="k in availableKinds" :key="k.value" :value="k.value">{{ k.label }}</option>
            </select>
          </div>

          <div class="search-box">
            <Search :size="15" />
            <input v-model="searchQuery" type="text" placeholder="Search tasks, errors, IDs…" aria-label="Search jobs" />
            <button v-if="searchQuery" type="button" class="clear-search" @click="searchQuery = ''"><X :size="12" /></button>
          </div>
        </div>
      </div>

      <!-- Jobs Table -->
      <div class="jobs-table-card">
        <table class="jobs-table">
          <thead>
            <tr>
              <th style="width: 220px">Task / Kind</th>
              <th style="width: 170px">Project</th>
              <th style="width: 110px">Status</th>
              <th>Progress & Provider State</th>
              <th style="width: 110px">Elapsed</th>
              <th style="width: 140px">Created</th>
              <th style="width: 130px; text-align: right">Actions</th>
            </tr>
          </thead>
          <tbody v-if="filteredJobs.length">
            <tr
              v-for="job in paginatedJobs"
              :key="job.id"
              class="job-row"
              :class="{
                'row-running': job.status === 'running',
                'row-failed': job.status === 'failed',
                'row-queued': job.status === 'queued',
              }"
              @click="openInspect(job)"
            >
              <!-- Kind & Icon -->
              <td>
                <div class="job-kind-cell">
                  <span class="kind-icon-badge" :class="job.kind">
                    <component :is="kindIcon(job.kind)" :size="15" />
                  </span>
                  <div class="kind-texts">
                    <strong>{{ formatKind(job.kind) }}</strong>
                    <span class="job-id-mono" :title="job.id">{{ job.id.slice(0, 8) }}…</span>
                  </div>
                </div>
              </td>

              <!-- Project Title -->
              <td>
                <button
                  type="button"
                  class="project-link-btn"
                  :title="`Open ${job.project_title || 'project'}`"
                  @click.stop="job.project_id && openProject(job.project_id)"
                >
                  <span class="truncate">{{ job.project_title || 'Untitled' }}</span>
                  <ExternalLink :size="12" />
                </button>
              </td>

              <!-- Status Badge -->
              <td>
                <UiBadge :tone="statusTone(job.status)">
                  <span v-if="job.status === 'running'" class="spinner-dot"></span>
                  <span v-else-if="job.status === 'queued'" class="pulse-dot"></span>
                  {{ job.status }}
                </UiBadge>
              </td>

              <!-- Progress & State Message -->
              <td>
                <div class="job-progress-cell">
                  <div class="message-row">
                    <span class="phase-badge" v-if="job.provider_phase || job.provider">
                      {{ job.provider ? `${job.provider} · ` : '' }}{{ job.provider_phase || '' }}
                    </span>
                    <span class="job-message truncate" :title="job.message">{{ job.message || 'Processing' }}</span>
                  </div>

                  <div v-if="job.status === 'running' || job.status === 'queued'" class="progress-bar-track">
                    <div
                      class="progress-bar-fill"
                      :class="{ indeterminate: job.status === 'queued' || (job.status === 'running' && job.progress <= 0.05) }"
                      :style="{ width: `${Math.max(5, Math.min(100, (job.progress || 0) * 100))}%` }"
                    ></div>
                  </div>

                  <!-- Error snippet if failed -->
                  <div v-if="job.status === 'failed' && job.error" class="error-snippet" :title="job.error">
                    <AlertCircle :size="12" />
                    <span class="truncate">{{ job.error }}</span>
                  </div>
                </div>
              </td>

              <!-- Elapsed Time -->
              <td>
                <span class="elapsed-time">{{ formatDuration(job.elapsed_seconds) }}</span>
              </td>

              <!-- Created Timestamp -->
              <td>
                <span class="timestamp-text" :title="job.created_at">{{ formatTimestamp(job.created_at) }}</span>
              </td>

              <!-- Actions -->
              <td class="actions-cell" @click.stop>
                <div class="row-actions">
                  <!-- Retry if failed or cancelled -->
                  <button
                    v-if="job.status === 'failed' || job.status === 'cancelled'"
                    type="button"
                    class="action-icon-btn retry"
                    title="Retry this job"
                    :disabled="actionBusyId === job.id"
                    @click="retryJob(job)"
                  >
                    <RotateCcw :size="14" />
                  </button>

                  <!-- Cancel if queued or running -->
                  <button
                    v-if="job.status === 'running' || job.status === 'queued'"
                    type="button"
                    class="action-icon-btn cancel"
                    title="Cancel job execution"
                    :disabled="actionBusyId === job.id"
                    @click="cancelJob(job)"
                  >
                    <StopCircle :size="14" />
                  </button>

                  <!-- Inspect details -->
                  <button
                    type="button"
                    class="action-icon-btn inspect"
                    title="Inspect full job payload and attempts"
                    @click="openInspect(job)"
                  >
                    <Maximize2 :size="14" />
                  </button>

                  <!-- Delete -->
                  <button
                    v-if="job.status !== 'running'"
                    type="button"
                    class="action-icon-btn delete"
                    title="Delete record"
                    :disabled="actionBusyId === job.id"
                    @click="deleteJob(job)"
                  >
                    <Trash2 :size="14" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
          <tbody v-else>
            <tr>
              <td colspan="7" class="empty-state-cell">
                <div class="empty-state">
                  <Activity :size="32" />
                  <strong>No jobs match the current filters</strong>
                  <p>Background tasks triggered by screenplay analysis, storyboard generation, and rendering will appear here.</p>
                  <UiButton size="sm" variant="secondary" @click="statusFilter = 'all'; kindFilter = 'all'; searchQuery = ''">
                    Reset filters
                  </UiButton>
                </div>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- Table Pagination Bar -->
        <div v-if="filteredJobs.length > 0" class="table-pagination">
          <div class="pagination-meta">
            <span>
              Showing <strong>{{ paginationRangeStart }}</strong>–<strong>{{ paginationRangeEnd }}</strong> of <strong>{{ filteredJobs.length }}</strong> tasks
            </span>
            <div class="page-size-selector">
              <label for="page-size-select">Per page:</label>
              <select
                id="page-size-select"
                :value="String(pageSize)"
                @change="pageSize = Number(($event.target as HTMLSelectElement).value)"
                class="size-select"
              >
                <option v-for="opt in pageSizeOptions" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </option>
              </select>
            </div>
          </div>

          <div class="pagination-controls" v-if="totalPages > 1">
            <button
              type="button"
              class="page-nav-btn"
              title="First Page"
              :disabled="currentPage === 1"
              @click="setPage(1)"
            >
              <ChevronsLeft :size="15" />
            </button>
            <button
              type="button"
              class="page-nav-btn"
              title="Previous Page"
              :disabled="currentPage === 1"
              @click="setPage(currentPage - 1)"
            >
              <ChevronLeft :size="15" />
            </button>

            <div class="page-pills">
              <template v-for="(p, idx) in displayedPages" :key="idx">
                <span v-if="p === 'ellipsis'" class="page-ellipsis">…</span>
                <button
                  v-else
                  type="button"
                  class="page-pill-btn"
                  :class="{ active: currentPage === p }"
                  @click="setPage(p)"
                >
                  {{ p }}
                </button>
              </template>
            </div>

            <button
              type="button"
              class="page-nav-btn"
              title="Next Page"
              :disabled="currentPage === totalPages"
              @click="setPage(currentPage + 1)"
            >
              <ChevronRight :size="15" />
            </button>
            <button
              type="button"
              class="page-nav-btn"
              title="Last Page"
              :disabled="currentPage === totalPages"
              @click="setPage(totalPages)"
            >
              <ChevronsRight :size="15" />
            </button>
          </div>
        </div>
      </div>
    </section>

    </template>

    <!-- TAB 2: Services & Daemons -->
    <template v-else-if="currentTab === 'services'">
      <section class="services-mgmt-section">
        <div class="services-mgmt-header">
          <div class="mgmt-header-text">
            <h2>Background Daemons & Generation Bridges</h2>
            <p>Start, stop, restart, and inspect log outputs for local AI bridges and worker pipelines.</p>
          </div>
          <div class="mgmt-header-actions">
            <UiButton size="sm" variant="secondary" :disabled="restartingAll" @click="restartAllServices">
              <RotateCcw :size="14" :class="{ spin: restartingAll }" />
              <span>{{ restartingAll ? 'Restarting all…' : 'Restart all services' }}</span>
            </UiButton>
            <UiButton size="sm" variant="ghost" :disabled="servicesLoading" @click="loadServices(false)">
              <RefreshCw :size="14" :class="{ spin: servicesLoading }" />
              <span>Refresh status</span>
            </UiButton>
          </div>
        </div>

        <!-- Services Cards Grid -->
        <div class="services-cards-grid">
          <div
            v-for="svc in services"
            :key="svc.id"
            class="service-card"
            :class="[svc.status, { is_busy: serviceActionBusyId?.startsWith(svc.id) }]"
          >
            <!-- Card Header -->
            <div class="svc-card-header">
              <div class="svc-title-group">
                <div class="svc-icon-badge" :class="svc.id">
                  <Video v-if="svc.id === 'flowkit'" :size="20" />
                  <Sparkles v-else-if="svc.id === 'chatgpt_web'" :size="20" />
                  <Server v-else-if="svc.id === 'movie_worker'" :size="20" />
                  <Layers v-else :size="20" />
                </div>
                <div>
                  <div class="svc-name-row">
                    <h3>{{ svc.name }}</h3>
                    <span class="svc-category-tag">{{ svc.category }}</span>
                  </div>
                  <p class="svc-description">{{ svc.description }}</p>
                </div>
              </div>

              <!-- Status Badge -->
              <div class="svc-status-pill" :class="svc.status">
                <span class="status-pulse-dot"></span>
                <span>{{ svc.status === 'running' ? 'Running' : svc.status === 'warning' ? 'Attention' : 'Stopped' }}</span>
              </div>
            </div>

            <!-- Warning Alert Box if any -->
            <div v-if="svc.warning" class="svc-warning-box">
              <AlertTriangle :size="16" class="warning-icon" />
              <div class="warning-text-group">
                <strong>Attention Required</strong>
                <p>{{ svc.warning }}</p>
                <div v-if="svc.id === 'flowkit'" class="warning-fix-tip">
                  <span>💡 Fix: Open Google Chrome → go to <code>https://flow.google.com/</code> → ensure you are signed in and complete any captcha verification.</span>
                </div>
              </div>
            </div>

            <!-- Metadata & Technical Details -->
            <div class="svc-details-grid">
              <div class="svc-detail-item">
                <span class="detail-label">Endpoint / Port</span>
                <span v-if="svc.port" class="detail-val mono">:{{ svc.port }}</span>
                <span v-else class="detail-val muted">Local process</span>
              </div>
              <div class="svc-detail-item">
                <span class="detail-label">Process PID</span>
                <span v-if="svc.pid" class="detail-val mono">{{ svc.pid }}</span>
                <span v-else class="detail-val muted">—</span>
              </div>
              <div class="svc-detail-item">
                <span class="detail-label">Health & State</span>
                <span v-if="svc.status === 'running'" class="detail-val text-success">Healthy & Online</span>
                <span v-else-if="svc.status === 'warning'" class="detail-val text-warning">Warning active</span>
                <span v-else class="detail-val muted">Offline</span>
              </div>

              <!-- Specific service diagnostic info -->
              <div v-if="svc.id === 'flowkit' && svc.details.ws" class="svc-detail-item col-span-full">
                <span class="detail-label">FlowKit Status</span>
                <span class="detail-val">
                  v{{ svc.details.version }} · 
                  Extension: {{ svc.details.extension_connected ? 'Connected' : 'Disconnected' }} · 
                  Authenticated Sessions: 
                  <strong :class="svc.details.ws?.authenticated_connections > 0 ? 'text-success' : 'text-danger'">
                    {{ svc.details.ws?.authenticated_connections || 0 }}
                  </strong>
                </span>
              </div>

              <div v-if="svc.id === 'chatgpt_web' && svc.details.model" class="svc-detail-item col-span-full">
                <span class="detail-label">Model & Auth</span>
                <span class="detail-val">
                  {{ svc.details.model }} · Signed in: 
                  <strong :class="svc.details.logged_in_hint ? 'text-success' : 'text-danger'">
                    {{ svc.details.logged_in_hint ? 'Yes' : 'No' }}
                  </strong>
                </span>
              </div>

              <div v-if="svc.id === 'movie_worker'" class="svc-detail-item col-span-full">
                <span class="detail-label">Huey Worker Queues</span>
                <span class="detail-val mono">video, image, chatgpt, gemini, general</span>
              </div>
            </div>

            <!-- Card Actions Toolbar -->
            <div class="svc-card-actions">
              <UiButton size="sm" variant="ghost" @click="openLogs(svc)">
                <Terminal :size="13" />
                <span>Console Logs</span>
              </UiButton>

              <div class="svc-action-buttons">
                <!-- If Stopped -->
                <UiButton
                  v-if="svc.status === 'stopped'"
                  size="sm"
                  variant="primary"
                  :disabled="serviceActionBusyId === `${svc.id}:start`"
                  @click="handleServiceAction(svc.id, 'start')"
                >
                  <Play :size="13" />
                  <span>{{ serviceActionBusyId === `${svc.id}:start` ? 'Starting…' : 'Start Service' }}</span>
                </UiButton>

                <!-- If Running or Warning -->
                <template v-else>
                  <UiButton
                    size="sm"
                    variant="secondary"
                    :disabled="serviceActionBusyId === `${svc.id}:restart`"
                    @click="handleServiceAction(svc.id, 'restart')"
                  >
                    <RotateCcw :size="13" :class="{ spin: serviceActionBusyId === `${svc.id}:restart` }" />
                    <span>{{ serviceActionBusyId === `${svc.id}:restart` ? 'Restarting…' : 'Restart' }}</span>
                  </UiButton>

                  <UiButton
                    size="sm"
                    variant="danger"
                    :disabled="serviceActionBusyId === `${svc.id}:stop`"
                    @click="handleServiceAction(svc.id, 'stop')"
                  >
                    <Square :size="13" />
                    <span>{{ serviceActionBusyId === `${svc.id}:stop` ? 'Stopping…' : 'Stop' }}</span>
                  </UiButton>
                </template>
              </div>
            </div>
          </div>
        </div>

        <!-- Architecture & Troubleshooting Banner -->
        <div class="troubleshooting-guide-card">
          <div class="guide-header">
            <Zap :size="16" />
            <strong>Bridge Quick Reference & Troubleshooting</strong>
          </div>
          <div class="guide-grid">
            <div class="guide-col">
              <h4>🎬 Google Flow Video Bridge (FlowKit)</h4>
              <p>FlowKit bridges Cutline's video generation queue directly into Google Flow (Veo). It communicates via WebSockets with the Flow Kit extension running in <strong>Google Chrome</strong>.</p>
              <ul>
                <li>Ensure <strong>Google Chrome</strong> is running with <code>https://flow.google.com/</code> open.</li>
                <li>If jobs fail with <code>CAPTCHA_TIMEOUT</code>, switch to the Flow tab in Chrome, ensure you are signed in, and reload the tab.</li>
              </ul>
            </div>
            <div class="guide-col">
              <h4>🎨 ChatGPT Web Bridge</h4>
              <p>Generates storyboard frames and character concept art. Automates an isolated Playwright browser session on <code>chatgpt.com</code>.</p>
              <ul>
                <li>If jobs time out or fail, click <strong>Restart</strong> on the ChatGPT Web Bridge.</li>
                <li>Check Console Logs to see real-time generation status and image URLs.</li>
              </ul>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- Service Logs Viewer Modal -->
    <UiDialog
      v-model:open="logsModalOpen"
      :title="activeServiceForLogs ? `Logs: ${activeServiceForLogs.name}` : 'Service Logs'"
      description="Live console and runtime log output from local daemon."
      width-class="max-w-4xl"
    >
      <div v-if="activeServiceForLogs" class="logs-modal-body">
        <div class="logs-toolbar">
          <div class="logs-meta-group">
            <span class="logs-path-tag">{{ activeServiceForLogs.log_path }}</span>
            <span class="logs-status-indicator" :class="activeServiceForLogs.status">
              {{ activeServiceForLogs.status }}
            </span>
          </div>
          <div class="logs-controls">
            <label class="logs-auto-poll" title="Poll every 2.5s">
              <input v-model="logsAutoRefresh" type="checkbox" />
              <span>Live stream</span>
            </label>
            <UiButton size="sm" variant="secondary" :disabled="logsLoading" @click="fetchLogs">
              <RefreshCw :size="12" :class="{ spin: logsLoading }" />
              <span>Refresh</span>
            </UiButton>
            <UiButton size="sm" variant="ghost" @click="copyLogs">
              <Copy :size="12" />
              <span>Copy</span>
            </UiButton>
          </div>
        </div>

        <div class="terminal-wrapper">
          <pre class="terminal-text">{{ activeServiceLogs }}</pre>
        </div>

        <footer class="logs-modal-footer">
          <UiButton variant="secondary" @click="logsModalOpen = false">Close</UiButton>
          <UiButton
            v-if="activeServiceForLogs.status !== 'stopped'"
            variant="secondary"
            :disabled="serviceActionBusyId === `${activeServiceForLogs.id}:restart`"
            @click="handleServiceAction(activeServiceForLogs.id, 'restart')"
          >
            <RotateCcw :size="13" />
            <span>Restart Service</span>
          </UiButton>
        </footer>
      </div>
    </UiDialog>

    <!-- Job Inspector Modal / Drawer -->
    <UiDialog
      v-model:open="inspectOpen"
      :title="selectedJob ? `${formatKind(selectedJob.kind)} (${selectedJob.id.slice(0, 8)})` : 'Job Inspector'"
      description="Complete runtime payload, prompt snapshot, provider failover logs, and attempts."
      width-class="max-w-3xl"
    >
      <div v-if="selectedJob" class="inspect-body">
        <!-- Quick Summary Header -->
        <div class="inspect-summary-grid">
          <div><label>Job ID</label><code>{{ selectedJob.id }}</code></div>
          <div><label>Project</label><strong>{{ selectedJob.project_title || selectedJob.project_id }}</strong></div>
          <div><label>Status</label><UiBadge :tone="statusTone(selectedJob.status)">{{ selectedJob.status }}</UiBadge></div>
          <div><label>Created</label><span>{{ formatTimestamp(selectedJob.created_at) }}</span></div>
          <div><label>Elapsed Time</label><span>{{ formatDuration(selectedJob.elapsed_seconds) }}</span></div>
          <div><label>Active Provider</label><span>{{ selectedJob.provider || '—' }}</span></div>
          <div><label>Provider Phase</label><span>{{ selectedJob.provider_phase || '—' }}</span></div>
          <div v-if="selectedJob.conversation_url" class="col-span-2">
            <label>Provider Conversation</label>
            <a :href="selectedJob.conversation_url" target="_blank" rel="noopener noreferrer" class="conversation-link">
              <span>{{ selectedJob.conversation_url }}</span>
              <ExternalLink :size="13" />
            </a>
          </div>
        </div>

        <!-- Failure Details Banner -->
        <div v-if="selectedJob.error" class="inspect-error-box">
          <div class="error-title"><AlertCircle :size="16" /><strong>Failure Reason</strong></div>
          <p class="error-text">{{ selectedJob.error }}</p>
          <div class="error-actions">
            <UiButton size="sm" variant="secondary" :disabled="actionBusyId === selectedJob.id" @click="retryJob(selectedJob)">
              <RotateCcw :size="14" /> Retry this job
            </UiButton>
          </div>
        </div>

        <!-- Attempts & Snapshot Details -->
        <div class="attempts-container">
          <div class="attempts-heading">
            <strong>Attempts & Pipeline Metadata ({{ selectedJob.attempts?.length || 0 }})</strong>
          </div>

          <div v-for="(attempt, index) in selectedJob.attempts" :key="index" class="attempt-card">
            <div class="attempt-header">
              <span class="attempt-badge">Step {{ index + 1 }}</span>
              <span v-if="attempt.provider" class="attempt-provider">{{ attempt.provider }} · {{ attempt.phase || 'attempt' }}</span>
              <span v-else-if="attempt.shot_id" class="attempt-provider">Shot: {{ String(attempt.shot_id).slice(0, 8) }}… ({{ attempt.aspect_ratio || '16:9' }})</span>
            </div>

            <!-- If this attempt has a continuity snapshot or prompt -->
            <div v-if="attempt.continuity_snapshot" class="snapshot-details">
              <div v-if="attempt.continuity_snapshot.effective_image_prompt" class="detail-block">
                <label>Effective Prompt Used</label>
                <pre>{{ attempt.continuity_snapshot.effective_image_prompt }}</pre>
              </div>
            </div>

            <!-- Image reference details if present -->
            <div v-if="attempt.image_reference" class="reference-snapshot-box">
              <label>Attached Visual Continuity Reference</label>
              <div class="ref-row">
                <span class="ref-file">{{ attempt.image_reference.filename }}</span>
                <span class="ref-shot">{{ attempt.image_reference.source_shot_title }}</span>
                <span class="ref-aspect">{{ attempt.image_reference.aspect_ratio }}</span>
              </div>
            </div>

            <!-- Raw Attempt JSON viewer -->
            <details class="raw-json-details">
              <summary>View raw JSON</summary>
              <pre>{{ JSON.stringify(attempt, null, 2) }}</pre>
            </details>
          </div>
        </div>

        <footer class="inspect-footer">
          <UiButton variant="secondary" @click="inspectOpen = false">Close</UiButton>
          <UiButton
            v-if="selectedJob.status === 'failed' || selectedJob.status === 'cancelled'"
            variant="primary"
            :disabled="actionBusyId === selectedJob.id"
            @click="retryJob(selectedJob)"
          >
            <RotateCcw :size="14" /> Retry Task
          </UiButton>
          <UiButton
            v-if="selectedJob.status === 'running' || selectedJob.status === 'queued'"
            variant="danger"
            :disabled="actionBusyId === selectedJob.id"
            @click="cancelJob(selectedJob)"
          >
            <StopCircle :size="14" /> Cancel Task
          </UiButton>
        </footer>
      </div>
    </UiDialog>
  </main>
</template>

<style scoped>
/* Main View Tabs */
.main-view-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--surface-1);
  padding: 4px;
  border-radius: var(--radius-md);
  border: 1px solid var(--line);
  width: fit-content;
}

.main-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: none;
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.main-tab-btn:hover {
  color: var(--ink);
  background: var(--surface-2);
}

.main-tab-btn.active {
  background: var(--surface-2);
  color: var(--ink);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}

.nav-tab-badge {
  font-size: 11px;
  padding: 2px 7px;
  border-radius: 9999px;
  background: var(--surface-3);
  color: var(--muted);
}

.nav-tab-badge.active {
  background: var(--accent);
  color: #fff;
  font-weight: 600;
}

.nav-tab-badge.warning {
  background: rgba(240, 164, 66, 0.18);
  color: #f0a442;
  font-weight: 600;
}

.nav-tab-badge.ready {
  background: rgba(72, 187, 120, 0.15);
  color: #48bb78;
}

.infra-action-btn {
  background: transparent;
  border: none;
  color: var(--accent);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: background 0.15s ease;
}

.infra-action-btn:hover {
  background: var(--accent-soft);
}

/* Services Management Section */
.services-mgmt-section {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.services-mgmt-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 16px;
}

.mgmt-header-text h2 {
  font-size: 18px;
  font-weight: 600;
  margin: 0 0 4px;
}

.mgmt-header-text p {
  font-size: 13px;
  color: var(--muted);
  margin: 0;
}

.mgmt-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Service Cards Grid */
.services-cards-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 20px;
}

.service-card {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  transition: all 0.2s ease;
}

.service-card:hover {
  border-color: var(--line-strong);
}

.service-card.warning {
  border-color: rgba(240, 164, 66, 0.4);
}

.service-card.stopped {
  opacity: 0.85;
}

.svc-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}

.svc-title-group {
  display: flex;
  align-items: flex-start;
  gap: 14px;
}

.svc-icon-badge {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: var(--surface-2);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.svc-icon-badge.flowkit { color: #5aa4f3; background: rgba(90, 164, 243, 0.12); }
.svc-icon-badge.chatgpt_web { color: #10a37f; background: rgba(16, 163, 127, 0.12); }
.svc-icon-badge.movie_worker { color: #f0a442; background: rgba(240, 164, 66, 0.12); }
.svc-icon-badge.gemini_web { color: #9a76f2; background: rgba(154, 118, 242, 0.12); }

.svc-name-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.svc-name-row h3 {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
  color: var(--ink);
}

.svc-category-tag {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--surface-3);
  color: var(--muted);
  font-weight: 600;
}

.svc-description {
  font-size: 12px;
  color: var(--muted);
  margin: 0;
  line-height: 1.4;
}

.svc-status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 9999px;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
}

.svc-status-pill.running {
  background: rgba(72, 187, 120, 0.12);
  color: #48bb78;
}

.svc-status-pill.warning {
  background: rgba(240, 164, 66, 0.15);
  color: #f0a442;
}

.svc-status-pill.stopped {
  background: var(--surface-3);
  color: var(--muted);
}

.status-pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.svc-status-pill.running .status-pulse-dot {
  animation: pulse-ring 1.8s infinite;
}

/* Warning Box */
.svc-warning-box {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  background: rgba(240, 164, 66, 0.08);
  border: 1px solid rgba(240, 164, 66, 0.25);
  border-radius: var(--radius-md);
  color: var(--ink);
}

.warning-icon {
  color: #f0a442;
  flex-shrink: 0;
  margin-top: 2px;
}

.warning-text-group strong {
  display: block;
  font-size: 12px;
  color: #f0a442;
  margin-bottom: 2px;
}

.warning-text-group p {
  margin: 0 0 6px;
  font-size: 12px;
  line-height: 1.4;
}

.warning-fix-tip {
  font-size: 11px;
  color: var(--muted);
  background: rgba(0, 0, 0, 0.04);
  padding: 6px 8px;
  border-radius: 4px;
}

.warning-fix-tip code {
  font-family: monospace;
  color: var(--accent);
}

/* Details Grid */
.svc-details-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
  padding: 12px 14px;
  background: var(--surface-2);
  border-radius: var(--radius-md);
  border: 1px solid var(--line);
}

.svc-detail-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.svc-detail-item.col-span-full {
  grid-column: 1 / -1;
  border-top: 1px solid var(--line);
  padding-top: 8px;
  margin-top: 2px;
}

.detail-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
}

.detail-val {
  font-size: 12px;
  color: var(--ink);
}

.detail-val.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.detail-val.text-success { color: #48bb78; font-weight: 500; }
.detail-val.text-warning { color: #f0a442; font-weight: 500; }
.detail-val.muted { color: var(--muted); }

/* Card Actions */
.svc-card-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}

.svc-action-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* Troubleshooting Card */
.troubleshooting-guide-card {
  padding: 20px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.guide-header {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--accent);
  font-size: 13px;
}

.guide-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
  gap: 20px;
}

.guide-col h4 {
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 6px;
  color: var(--ink);
}

.guide-col p {
  font-size: 12px;
  color: var(--muted);
  margin: 0 0 8px;
  line-height: 1.4;
}

.guide-col ul {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.5;
}

.guide-col code {
  color: var(--accent);
  font-family: monospace;
}

/* Logs Modal */
.logs-modal-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.logs-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.logs-meta-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logs-path-tag {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  color: var(--muted);
  background: var(--surface-2);
  padding: 3px 8px;
  border-radius: 4px;
}

.logs-status-indicator {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
}

.logs-status-indicator.running { color: #48bb78; }
.logs-status-indicator.warning { color: #f0a442; }
.logs-status-indicator.stopped { color: var(--muted); }

.logs-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logs-auto-poll {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--muted);
  cursor: pointer;
}

.terminal-wrapper {
  background: #090b0e;
  border: 1px solid #1e2229;
  border-radius: var(--radius-md);
  padding: 14px;
  max-height: 480px;
  overflow: auto;
}

.terminal-text {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: #c9d1d9;
  white-space: pre-wrap;
  word-break: break-all;
}

.logs-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.queues-dashboard {
  max-width: 1440px;
  margin: 0 auto;
  padding: 32px 28px 80px;
  display: flex;
  flex-direction: column;
  gap: 28px;
  color: var(--ink);
}

/* Header */
.dashboard-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  flex-wrap: wrap;
  gap: 20px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--line);
}

.header-left {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  transition: color 0.15s ease;
}

.back-link:hover {
  color: var(--ink);
}

.title-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.title-row h1 {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--ink);
}

.subtitle {
  margin: 0;
  color: var(--muted);
  font-size: 13px;
}

.pulse-indicator {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--muted);
  display: inline-block;
}

.pulse-indicator.active {
  background: var(--accent);
  box-shadow: 0 0 10px var(--accent);
  animation: pulse-ring 1.8s infinite;
}

@keyframes pulse-ring {
  0% { transform: scale(0.95); opacity: 0.9; }
  50% { transform: scale(1.3); opacity: 0.4; }
  100% { transform: scale(0.95); opacity: 0.9; }
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.auto-refresh-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--muted);
  cursor: pointer;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  border: 1px solid var(--line);
  user-select: none;
}

.auto-refresh-switch input {
  cursor: pointer;
}

.refresh-indicator {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--success);
}

.refresh-indicator.spinning {
  background: var(--accent);
  box-shadow: 0 0 6px var(--accent);
}

/* Toast */
.action-toast {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 18px;
  border-radius: var(--radius-md);
  font-size: 13px;
  animation: slide-in 0.2s ease;
}

.action-toast.success {
  background: var(--success-soft);
  color: #79e2a8;
  border: 1px solid rgba(76, 190, 126, 0.3);
}

.action-toast.error {
  background: var(--danger-soft);
  color: #ffa1a1;
  border: 1px solid rgba(238, 92, 92, 0.3);
}

.toast-close {
  margin-left: auto;
  background: none;
  border: none;
  color: inherit;
  cursor: pointer;
  padding: 2px;
  opacity: 0.7;
}

.toast-close:hover {
  opacity: 1;
}

/* Metrics Grid */
.metrics-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
}

.metric-card {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 20px 22px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  transition: transform 0.2s ease, border-color 0.2s ease;
}

.metric-card:hover {
  border-color: var(--line-strong);
}

.metric-card.active-card.has_active {
  border-color: rgba(235, 101, 84, 0.4);
  background: linear-gradient(135deg, var(--panel) 0%, rgba(235, 101, 84, 0.08) 100%);
}

.metric-card.has_failed {
  border-color: rgba(239, 106, 106, 0.3);
}

.metric-icon {
  width: 46px;
  height: 46px;
  border-radius: 14px;
  background: var(--surface-2);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent);
}

.queued-icon { color: var(--warning); background: rgba(217, 155, 74, 0.1); }
.failed-icon { color: var(--danger); background: var(--danger-soft); }
.completed-icon { color: var(--success); background: var(--success-soft); }

.metric-data {
  display: flex;
  flex-direction: column;
}

.metric-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.metric-value {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.15;
  color: var(--ink);
}

.metric-hint {
  font-size: 12px;
  color: var(--muted);
  margin-top: 3px;
}

/* System Infra Section */
.system-infra-section {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

@media (max-width: 960px) {
  .system-infra-section {
    grid-template-columns: 1fr;
  }
}

.infra-panel {
  padding: 18px 20px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.infra-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.infra-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.infra-header small {
  color: var(--muted);
  font-size: 12px;
}

.queues-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.queue-pill {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 14px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  font-size: 12px;
}

.queue-info {
  display: flex;
  flex-direction: column;
}

.queue-name {
  font-weight: 600;
  color: var(--ink);
}

.queue-info small {
  color: var(--muted);
}

.queue-flush-btn {
  margin-left: 6px;
  padding: 2px 7px;
  font-size: 11px;
  border-radius: 4px;
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid rgba(238, 92, 92, 0.3);
  cursor: pointer;
}

.queue-flush-btn:hover {
  background: var(--danger);
  color: white;
}

.bridges-list {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

@media (max-width: 600px) {
  .bridges-list {
    grid-template-columns: 1fr;
  }
}

.bridge-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
}

.bridge-card.online {
  border-color: rgba(76, 190, 126, 0.25);
}

.bridge-info {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  margin-right: auto;
}

.bridge-info strong {
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bridge-info small {
  font-size: 11px;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--muted);
  flex-shrink: 0;
}

.status-dot.ready { background: var(--success); box-shadow: 0 0 6px rgba(76, 190, 126, 0.5); }
.status-dot.offline { background: var(--danger); }

/* Jobs Section */
.jobs-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 14px;
}

.status-tabs {
  display: flex;
  gap: 6px;
  padding: 4px;
  background: var(--surface-2);
  border-radius: var(--radius-md);
  border: 1px solid var(--line);
}

.tab-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: none;
  background: none;
  color: var(--muted);
  font-size: 13px;
  font-weight: 500;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all 0.15s ease;
}

.tab-btn:hover {
  color: var(--ink);
}

.tab-btn.active {
  background: var(--panel-2);
  color: var(--ink);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
}

.tab-count {
  padding: 1px 6px;
  border-radius: 10px;
  background: var(--surface-3);
  font-size: 11px;
}

.tab-count.badge-accent {
  background: var(--accent);
  color: white;
}

.tab-count.badge-danger {
  background: var(--danger);
  color: white;
}

.search-filter-group {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.kind-select-wrapper select {
  height: var(--control-h);
  padding: 0 12px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  border: 1px solid var(--line);
  color: var(--ink);
  font-size: 13px;
  outline: none;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 8px;
  height: var(--control-h);
  padding: 0 12px;
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  border: 1px solid var(--line);
  color: var(--muted);
}

.search-box input {
  background: none;
  border: none;
  color: var(--ink);
  font-size: 13px;
  outline: none;
  width: 180px;
}

.clear-search {
  background: none;
  border: none;
  color: var(--muted);
  cursor: pointer;
}

/* Table Card */
.jobs-table-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  overflow: hidden;
  box-shadow: var(--shadow-panel);
}

.jobs-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  text-align: left;
}

.jobs-table th {
  padding: 12px 16px;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid var(--line);
  background: var(--surface-1);
}

.jobs-table td {
  padding: 14px 16px;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
}

.job-row {
  cursor: pointer;
  transition: background 0.15s ease;
}

.job-row:hover {
  background: var(--panel-2);
}

.job-row.row-running {
  background: rgba(235, 101, 84, 0.04);
}

.job-row.row-failed {
  background: rgba(239, 106, 106, 0.03);
}

.job-kind-cell {
  display: flex;
  align-items: center;
  gap: 12px;
}

.kind-icon-badge {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--surface-2);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent);
  flex-shrink: 0;
}

.kind-icon-badge.shot_clip { color: #5aa4f3; background: rgba(90, 164, 243, 0.12); }
.kind-icon-badge.create_draft { color: #f0a442; background: rgba(240, 164, 66, 0.12); }
.kind-icon-badge.final_cut { color: #9a76f2; background: rgba(154, 118, 242, 0.12); }

.kind-texts {
  display: flex;
  flex-direction: column;
}

.kind-texts strong {
  font-weight: 600;
  color: var(--ink);
  font-size: 13px;
}

.job-id-mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  color: var(--muted);
}

.project-link-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-radius: var(--radius-xs);
  background: var(--surface-2);
  border: 1px solid var(--line);
  color: var(--ink);
  font-size: 12px;
  cursor: pointer;
  max-width: 160px;
}

.project-link-btn:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.truncate {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.job-progress-cell {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 400px;
}

.message-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.phase-badge {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--surface-3);
  color: var(--muted);
  font-weight: 600;
  flex-shrink: 0;
}

.job-message {
  font-size: 12px;
  color: var(--muted);
}

.progress-bar-track {
  width: 100%;
  height: 4px;
  border-radius: 2px;
  background: var(--surface-3);
  overflow: hidden;
}

.progress-bar-fill {
  height: 100%;
  border-radius: 2px;
  background: var(--accent);
  transition: width 0.3s ease;
}

.progress-bar-fill.indeterminate {
  width: 40% !important;
  animation: progress-slide 1.5s infinite ease-in-out;
}

@keyframes progress-slide {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(300%); }
}

.error-snippet {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--danger);
  font-size: 12px;
}

.elapsed-time, .timestamp-text {
  font-size: 12px;
  color: var(--muted);
}

/* Row Actions */
.actions-cell {
  text-align: right;
}

.row-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.action-icon-btn {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  color: var(--muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.15s ease;
}

.action-icon-btn:hover {
  color: var(--ink);
  border-color: var(--line-strong);
}

.action-icon-btn.retry:hover {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-soft);
}

.action-icon-btn.cancel:hover {
  color: var(--danger);
  border-color: var(--danger);
  background: var(--danger-soft);
}

.action-icon-btn.delete:hover {
  color: var(--danger);
  border-color: var(--danger);
}

/* Empty State */
.empty-state-cell {
  padding: 60px 20px !important;
  text-align: center;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  color: var(--muted);
}

.empty-state strong {
  font-size: 15px;
  color: var(--ink);
}

.empty-state p {
  margin: 0 0 12px;
  font-size: 13px;
  max-width: 400px;
}

/* Spinner & Pulse in table */
.spinner-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-top-color: transparent;
  display: inline-block;
  animation: spin 0.8s infinite linear;
  margin-right: 4px;
}

.pulse-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  display: inline-block;
  margin-right: 4px;
  animation: pulse-ring 1.5s infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* Inspector Drawer / Modal */
.inspect-body {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.inspect-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 14px;
  padding: 16px;
  background: var(--surface-2);
  border-radius: var(--radius-md);
  border: 1px solid var(--line);
}

.inspect-summary-grid > div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.inspect-summary-grid label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--muted);
}

.inspect-summary-grid strong, .inspect-summary-grid span {
  font-size: 13px;
}

.conversation-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--accent);
  font-size: 12px;
  text-decoration: none;
}

.conversation-link:hover {
  text-decoration: underline;
}

.inspect-error-box {
  padding: 14px 16px;
  background: var(--danger-soft);
  border: 1px solid rgba(238, 92, 92, 0.3);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.error-title {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--danger);
  font-size: 13px;
}

.error-text {
  margin: 0;
  font-size: 13px;
  color: var(--ink);
  line-height: 1.5;
  white-space: pre-wrap;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.attempts-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.attempts-heading {
  font-size: 13px;
  color: var(--muted);
}

.attempt-card {
  padding: 14px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.attempt-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.attempt-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  background: var(--surface-3);
  border-radius: 4px;
  color: var(--muted);
}

.attempt-provider {
  font-size: 12px;
  font-weight: 500;
  color: var(--ink);
}

.snapshot-details pre {
  margin: 4px 0 0;
  padding: 10px;
  background: var(--surface-2);
  border-radius: 6px;
  font-size: 12px;
  color: var(--ink);
  white-space: pre-wrap;
  max-height: 140px;
  overflow-y: auto;
}

.reference-snapshot-box {
  padding: 10px;
  background: var(--surface-2);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
}

.ref-row {
  display: flex;
  gap: 10px;
  color: var(--muted);
}

.ref-file { color: var(--accent); font-family: monospace; }
.ref-shot { font-weight: 500; color: var(--ink); }

.raw-json-details summary {
  font-size: 11px;
  color: var(--muted);
  cursor: pointer;
}

.raw-json-details pre {
  margin: 6px 0 0;
  padding: 8px;
  background: #0d0f11;
  border-radius: 6px;
  font-size: 11px;
  color: #adb5bd;
  max-height: 180px;
  overflow: auto;
}

.inspect-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding-top: 14px;
  border-top: 1px solid var(--line);
}

/* Table Pagination */
.table-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  background: var(--surface-1);
  border-top: 1px solid var(--line);
  font-size: 13px;
  color: var(--muted);
  flex-wrap: wrap;
  gap: 12px;
}

.pagination-meta {
  display: flex;
  align-items: center;
  gap: 16px;
}

.pagination-meta strong {
  color: var(--text);
}

.page-size-selector {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
}

.size-select {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 12px;
  padding: 4px 8px;
  outline: none;
  cursor: pointer;
}

.size-select:focus {
  border-color: var(--accent);
}

.pagination-controls {
  display: flex;
  align-items: center;
  gap: 6px;
}

.page-nav-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm);
  background: var(--panel-2);
  border: 1px solid var(--line);
  color: var(--text);
  cursor: pointer;
  transition: all 0.15s ease;
}

.page-nav-btn:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--accent);
  color: var(--accent);
}

.page-nav-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.page-pills {
  display: flex;
  align-items: center;
  gap: 4px;
}

.page-pill-btn {
  min-width: 30px;
  height: 30px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: transparent;
  border: 1px solid transparent;
  color: var(--text);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

.page-pill-btn:hover:not(.active) {
  background: var(--panel-2);
  border-color: var(--line);
}

.page-pill-btn.active {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
  font-weight: 700;
}

.page-ellipsis {
  padding: 0 4px;
  color: var(--muted);
  user-select: none;
}

</style>
