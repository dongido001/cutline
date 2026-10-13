import { request, type DialogueLine, type Job } from './api'

export type FindingKind = 'confirmed_inconsistency' | 'possible_gap' | 'editorial_suggestion'
export interface ReviewPatch {
  shot_id: string
  field: 'action' | 'dialogue' | 'sound' | 'framing' | 'image_prompt' | 'video_prompt' | 'title'
  before: string | DialogueLine[]
  after: string | DialogueLine[]
}
export interface ReviewEvidence {
  version_id: string
  character_id?: string
  episode_id?: string | null
  shot_id?: string
  quote: string
  field?: string
  label: string
}
export interface ReviewFinding {
  id: string
  kind: FindingKind
  category: string
  title: string
  reason: string
  recommendation: string
  evidence: ReviewEvidence[]
  patches: ReviewPatch[]
  status: 'open' | 'rejected' | 'accepted'
  modified?: boolean
}
export interface ReviewRun {
  id: string
  script_version_id: string
  provider?: string
  status: string
  job_id?: string | null
  error?: string | null
  stale: boolean
  context: Array<{ episode_id: string; episode_number: number; title: string; version_id: string; draft: boolean }>
  report: { assessment: string; strengths: string[]; priorities: string[]; findings: ReviewFinding[] } | null
  applied_version_id?: string | null
  undo_version_id?: string | null
  draft_job?: { status: string; error?: string | null; finding_id: string } | null
}
export interface ReviewResponse { latest: ReviewRun | null; previous_success: ReviewRun | null }

export const deepReviewApi = {
  get: (versionId: string) => request<ReviewResponse>(`/api/script-versions/${versionId}/deep-review`),
  start: (versionId: string) => request<ReviewRun>(`/api/script-versions/${versionId}/deep-review`, { method: 'POST' }),
  draft: (runId: string, findingId: string) => request<Job>(`/api/deep-reviews/${runId}/findings/${findingId}/draft`, { method: 'POST' }),
  decide: (runId: string, findingId: string, input: { decision: 'rejected' | 'accepted'; expected_version_id?: string; patches?: ReviewPatch[] }) =>
    request<{ version_id?: string; run: ReviewRun }>(`/api/deep-reviews/${runId}/findings/${findingId}`, { method: 'PATCH', body: JSON.stringify(input) }),
  undo: (runId: string, versionId: string) => request<{ version_id: string }>(`/api/deep-reviews/${runId}/undo`, { method: 'POST', body: JSON.stringify({ expected_version_id: versionId }) }),
}
