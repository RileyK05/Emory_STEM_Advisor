import { request as apiRequest, requestWithHeaders } from './http';
import type {
  AdminAuditEntry,
  AdminCollection,
  AdminJob,
  AdminOverview,
  AdminSource,
  AdminUser,
  AdminUserSession,
  AdminUserUpdate,
  SourceReviewDetailResponse,
  SourceReviewProposal,
  SourceReviewProposalCreate,
  SourceLifecycleChangeRequest,
  SourceLifecycleChangeResponse,
  SourceRevisionEvidenceResponse,
  ProgramInspectionResponse,
  ProgramRuleReleaseDetail,
  ModelPriceSchedule,
  TelemetryUsageResponse,
  FeedbackTriageListItem,
  FeedbackTriageRequest,
  FeedbackTriageResponse,
  EvalCandidatePromotionRequest,
  EvalCandidatePromotionResponse,
  AdminCourseChange,
  AdminCourseChangeCreate,
  AdminCourseConflict,
  AdminCourseDetail,
  AdminCourseListResponse,
  AdminCoursePreviewResponse,
  AdminCoursePublishResponse,
} from './types';

// Backoffice calls send a debug session only when one is passed explicitly, never the
// token the Debug bundle set for its test chat.
function request<T>(path: string, init: RequestInit = {}, debugToken: string | null = null): Promise<T> {
  return apiRequest<T>(path, init, debugToken);
}

export function getAdminOverview(): Promise<AdminOverview> {
  return request<AdminOverview>('/api/admin/overview');
}

export function listAdminUsers(
  params: {
    query?: string;
    role?: string;
    status?: string;
    limit?: number;
    offset?: number;
  } = {},
): Promise<AdminUser[]> {
  const search = new URLSearchParams();
  if (params.query) search.set('query', params.query);
  if (params.role) search.set('role', params.role);
  if (params.status) search.set('status', params.status);
  search.set('limit', String(params.limit ?? 50));
  search.set('offset', String(params.offset ?? 0));
  return request<AdminUser[]>(`/api/admin/users?${search}`);
}

export function updateAdminUser(id: string, body: AdminUserUpdate): Promise<AdminUser> {
  return request<AdminUser>(`/api/admin/users/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  });
}

export function revokeAdminUserSessions(id: string): Promise<{ revoked: number }> {
  return request<{ revoked: number }>(`/api/admin/users/${encodeURIComponent(id)}/revoke-sessions`, { method: 'POST' });
}

export interface AdminAuditParams {
  action?: string;
  targetType?: string;
  targetId?: string;
  actor?: string;
  since?: string;
  until?: string;
  cursor?: string;
  limit?: number;
}

export async function listAdminAudit(
  params: number | AdminAuditParams = 100,
): Promise<{ entries: AdminAuditEntry[]; nextCursor: string | null }> {
  const p: AdminAuditParams = typeof params === 'number' ? { limit: params } : params;
  const search = new URLSearchParams();
  if (p.action) search.set('action', p.action);
  if (p.targetType) search.set('target_type', p.targetType);
  if (p.targetId) search.set('target_id', p.targetId);
  if (p.actor) search.set('actor', p.actor);
  if (p.since) search.set('since', p.since);
  if (p.until) search.set('until', p.until);
  if (p.cursor) search.set('cursor', p.cursor);
  search.set('limit', String(p.limit ?? 100));

  const { data, headers } = await requestWithHeaders<AdminAuditEntry[]>(`/api/admin/audit?${search}`, {}, null);
  return {
    entries: Array.isArray(data) ? data : [],
    nextCursor: headers.get('X-Next-Cursor'),
  };
}

export function getSourceReview(sourceId: string): Promise<SourceReviewDetailResponse> {
  return request<SourceReviewDetailResponse>(`/api/admin/sources/${encodeURIComponent(sourceId)}/review`);
}

export function createSourceProposal(
  sourceId: string,
  body: SourceReviewProposalCreate,
): Promise<SourceReviewProposal> {
  return request<SourceReviewProposal>(`/api/admin/sources/${encodeURIComponent(sourceId)}/proposals`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function approveSourceProposal(sourceId: string, proposalId: string): Promise<SourceReviewProposal> {
  return request<SourceReviewProposal>(
    `/api/admin/sources/${encodeURIComponent(sourceId)}/proposals/${encodeURIComponent(proposalId)}/approve`,
    { method: 'POST' },
  );
}

export function rejectSourceProposal(
  sourceId: string,
  proposalId: string,
  reason: string,
): Promise<SourceReviewProposal> {
  return request<SourceReviewProposal>(
    `/api/admin/sources/${encodeURIComponent(sourceId)}/proposals/${encodeURIComponent(proposalId)}/reject`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  );
}

export function changeSourceLifecycle(
  sourceId: string,
  body: SourceLifecycleChangeRequest,
): Promise<SourceLifecycleChangeResponse> {
  return request<SourceLifecycleChangeResponse>(`/api/admin/sources/${encodeURIComponent(sourceId)}/lifecycle`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function getSourceRevisionEvidence(
  sourceId: string,
  indexRevision: number,
): Promise<SourceRevisionEvidenceResponse> {
  return request<SourceRevisionEvidenceResponse>(
    `/api/admin/sources/${encodeURIComponent(sourceId)}/evidence?index_revision=${indexRevision}`,
  );
}

export function getProgramInspection(programId: string): Promise<ProgramInspectionResponse> {
  return request<ProgramInspectionResponse>(`/api/admin/programs/${encodeURIComponent(programId)}/inspection`);
}

export function draftProgramRules(programId: string, notes: string): Promise<ProgramRuleReleaseDetail> {
  return request<ProgramRuleReleaseDetail>(`/api/admin/programs/${encodeURIComponent(programId)}/rules/draft`, {
    method: 'POST',
    body: JSON.stringify({ notes }),
  });
}

export function approveProgramRelease(programId: string, releaseId: string): Promise<ProgramRuleReleaseDetail> {
  return request<ProgramRuleReleaseDetail>(
    `/api/admin/programs/${encodeURIComponent(programId)}/rules/${encodeURIComponent(releaseId)}/approve`,
    { method: 'POST', body: JSON.stringify({}) },
  );
}

export function publishProgramRelease(programId: string, releaseId: string): Promise<ProgramRuleReleaseDetail> {
  return request<ProgramRuleReleaseDetail>(
    `/api/admin/programs/${encodeURIComponent(programId)}/rules/${encodeURIComponent(releaseId)}/publish`,
    { method: 'POST', body: JSON.stringify({}) },
  );
}

export function rejectProgramRelease(
  programId: string,
  releaseId: string,
  reason: string,
): Promise<ProgramRuleReleaseDetail> {
  return request<ProgramRuleReleaseDetail>(
    `/api/admin/programs/${encodeURIComponent(programId)}/rules/${encodeURIComponent(releaseId)}/reject`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  );
}

export function listAdminUserSessions(id: string): Promise<AdminUserSession[]> {
  return request<AdminUserSession[]>(`/api/admin/users/${encodeURIComponent(id)}/sessions`);
}

export function listAdminJobs(status = '', limit = 100, offset = 0): Promise<AdminJob[]> {
  const search = new URLSearchParams({
    status,
    limit: String(limit),
    offset: String(offset),
  });
  return request<AdminJob[]>(`/api/admin/jobs?${search}`);
}

export function cancelAdminJob(id: string): Promise<AdminJob> {
  return request<AdminJob>(`/api/admin/jobs/${encodeURIComponent(id)}/cancel`, {
    method: 'POST',
  });
}

export function listAdminSources(workspace = '', limit = 100, offset = 0): Promise<AdminSource[]> {
  const search = new URLSearchParams({
    workspace,
    limit: String(limit),
    offset: String(offset),
  });
  return request<AdminSource[]>(`/api/admin/sources?${search}`);
}

export function listAdminCollections(limit = 100, offset = 0): Promise<AdminCollection[]> {
  const search = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return request<AdminCollection[]>(`/api/admin/collections?${search}`);
}

export function getAdminTelemetryUsage(
  params: {
    since?: string;
    until?: string;
    provider?: string;
    modelId?: string;
    groupBy?: 'date' | 'model' | 'operation';
    limit?: number;
  } = {},
): Promise<TelemetryUsageResponse> {
  const search = new URLSearchParams();
  if (params.since) search.set('since', params.since);
  if (params.until) search.set('until', params.until);
  if (params.provider) search.set('provider', params.provider);
  if (params.modelId) search.set('model_id', params.modelId);
  if (params.groupBy) search.set('group_by', params.groupBy);
  if (params.limit) search.set('limit', String(params.limit));
  const query = search.toString();
  return request<TelemetryUsageResponse>(`/api/admin/telemetry/usage${query ? `?${query}` : ''}`);
}

export function listAdminPriceSchedules(): Promise<ModelPriceSchedule[]> {
  return request<ModelPriceSchedule[]>('/api/admin/pricing/schedules');
}

export function listFeedbackTriage(): Promise<FeedbackTriageListItem[]> {
  return request('/api/feedback/triage');
}

export function recordFeedbackTriage(body: FeedbackTriageRequest): Promise<FeedbackTriageResponse> {
  return request('/api/feedback/triage', { method: 'POST', body: JSON.stringify(body) });
}

export function promoteEvalCandidate(body: EvalCandidatePromotionRequest): Promise<EvalCandidatePromotionResponse> {
  return request('/api/feedback/promotions', { method: 'POST', body: JSON.stringify(body) });
}

// ---- Reviewed course maintenance (phase B) ---------------------------------

export function listAdminCourses(
  params: {
    collection_id?: string;
    query?: string;
    subject?: string;
    lifecycle?: string;
    authority?: string;
    campus?: string;
    limit?: number;
    offset?: number;
  } = {},
): Promise<AdminCourseListResponse> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const suffix = search.toString();
  return request<AdminCourseListResponse>(`/api/admin/courses${suffix ? `?${suffix}` : ''}`);
}

export function getAdminCourse(code: string, collectionId?: string): Promise<AdminCourseDetail> {
  const suffix = collectionId ? `?collection_id=${encodeURIComponent(collectionId)}` : '';
  return request<AdminCourseDetail>(`/api/admin/courses/${encodeURIComponent(code)}${suffix}`);
}

export function listCourseConflicts(collectionId?: string): Promise<AdminCourseConflict[]> {
  const suffix = collectionId ? `?collection_id=${encodeURIComponent(collectionId)}` : '';
  return request<AdminCourseConflict[]>(`/api/admin/course-changes/conflicts${suffix}`);
}

export function createCourseChange(body: AdminCourseChangeCreate, collectionId?: string): Promise<AdminCourseChange> {
  const suffix = collectionId ? `?collection_id=${encodeURIComponent(collectionId)}` : '';
  return request<AdminCourseChange>(`/api/admin/course-changes${suffix}`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export function previewCourseChange(changeId: string): Promise<AdminCoursePreviewResponse> {
  return request<AdminCoursePreviewResponse>(`/api/admin/course-changes/${encodeURIComponent(changeId)}/preview`);
}

export function publishCourseChange(changeId: string): Promise<AdminCoursePublishResponse> {
  return request<AdminCoursePublishResponse>(`/api/admin/course-changes/${encodeURIComponent(changeId)}/publish`, {
    method: 'POST',
  });
}

export function rejectCourseChange(changeId: string, reason: string): Promise<AdminCourseChange> {
  return request<AdminCourseChange>(`/api/admin/course-changes/${encodeURIComponent(changeId)}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}
