import type {
  AdvisorClient,
  EffectiveFlagsResponse,
  QueryRequest,
  QueryResponse,
  SourceDoc,
  CollectionDoc,
  Recommendation,
  RetiredCourse,
  ArtifactPreview,
  ArtifactRevisionRequest,
  NativeArtifactRecord,
  NativeArtifactSpec,
  ExportSummaryRequest,
  ArtifactRun,
  ArtifactRunEvent,
  ArtifactRunRequest,
  ArtifactRunLimits,
  ArtifactBatchRequest,
  ArtifactBatchRecord,
  ArtifactBatchSummary,
  ConnectionDiagnostic,
  ConnectionProfile,
  ConnectionProfileWrite,
  AuthSession,
  NativeArtifactSummary,
  ConversationSummary,
  ConversationRecord,
  CreateConversationRequest,
  ConversationTurnRequest,
  ConversationStreamStartResponse,
  ConversationStreamEventsResponse,
  ConversationTerminalResponse,
  ConversationMemoryInspection,
  ConversationResetResponse,
  GapListResponse,
  ResolveGapRequest,
  RetrievalGap,
  AcademicRecordAttemptItem,
  AcademicRecordAttemptUpsertRequest,
  AcademicRecordDetailResponse,
  AcademicRecordImportBatchResponse,
  AcademicRecordImportCommitRequest,
  AcademicRecordImportCommitResponse,
  AcademicRecordImportPreviewRequest,
  AcademicRecordImportUpdateRequest,
  AcademicRecordRevisionResponse,
  AuditableProgramItem,
  DegreeAuditDetailResponse,
  DegreeAuditRecordItem,
  DegreeAuditRunRequest,
  ProgramComparisonCreateRequest,
  ProgramComparisonResponse,
  ProgramComparisonSummary,
  PrivacyExportDownloadResponse,
  PrivacyExportResponse,
  ResponseFeedbackRequest,
  ResponseFeedbackResponse,
} from './types';

/** Base URL of the backend. Defaults to the Vite dev proxy path. */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

// Set only by the Debug bundle. The Main bundle never sets it, so ordinary
// users never send an override header even though they share this module.
let debugSessionToken: string | null = null;

export function setDebugSessionToken(token: string | null): void {
  debugSessionToken = token;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly features: string[] = [],
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

async function errorDetail(response: Response, prefix: string): Promise<ApiError> {
  const body = await response.text();
  let detail = body || response.statusText;
  let features: string[] = [];
  try {
    const parsed = JSON.parse(body) as { detail?: unknown; message?: unknown; features?: unknown };
    const value = parsed.detail ?? parsed.message;
    if (typeof value === 'string') detail = value;
    else if (Array.isArray(value))
      detail = value
        .map((item) =>
          typeof item === 'object' && item !== null && 'msg' in item
            ? String((item as { msg: unknown }).msg)
            : String(item),
        )
        .join('; ');
    else if (value != null) detail = JSON.stringify(value);
    if (Array.isArray(parsed.features)) features = parsed.features.map((item) => String(item));
  } catch {
    /* FastAPI and proxies may return plain text. */
  }
  const suffix = features.length ? ' Unsupported: ' + features.join(', ') + '.' : '';
  return new ApiError(
    prefix + ' (' + response.status + '): ' + (detail || response.statusText) + suffix,
    response.status,
    features,
  );
}

async function fetchOrThrow(
  path: string,
  init: RequestInit,
  unreachableMessage: string,
  errorPrefix: string,
  debugToken: string | null = debugSessionToken,
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch {
    throw new Error(unreachableMessage);
  }
  if (!response.ok) {
    if (response.status === 401 && !debugToken && !path.startsWith('/api/auth/'))
      window.dispatchEvent(new Event('advisor-session-invalid'));
    // A 5xx with an empty body is the dev proxy failing to reach a stopped API,
    // not an application error. Report it as unreachable rather than a
    // misleading "500 Internal Server Error".
    if (response.status >= 500 && (await response.clone().text()).length === 0) throw new Error(unreachableMessage);
    throw await errorDetail(response, errorPrefix);
  }
  return response;
}

/** Sends a same-origin API request. Caller headers are merged with the JSON
 *  content type (omitted for FormData so the browser sets the boundary) and
 *  the debug session header. `debugToken` defaults to the token the Debug
 *  bundle set; pass one explicitly (or null for none) to override it. */
async function send(path: string, init: RequestInit, debugToken: string | null): Promise<Response> {
  const headers = new Headers(init.headers);
  if (!(init.body instanceof FormData) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  if (debugToken && !headers.has('X-Debug-Session')) headers.set('X-Debug-Session', debugToken);
  return fetchOrThrow(
    path,
    { credentials: 'same-origin', ...init, headers },
    'Could not reach the backend API. Is it running, and is VITE_API_MODE=http?',
    'API request failed',
    debugToken,
  );
}

async function body<T>(response: Response): Promise<T> {
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
  debugToken: string | null = debugSessionToken,
): Promise<T> {
  return body<T>(await send(path, init, debugToken));
}

/** Like `request`, but also returns the response headers (e.g. a paging cursor). */
export async function requestWithHeaders<T>(
  path: string,
  init: RequestInit = {},
  debugToken: string | null = debugSessionToken,
): Promise<{ data: T; headers: Headers }> {
  const response = await send(path, init, debugToken);
  return { data: await body<T>(response), headers: response.headers };
}

export class HttpClient implements AdvisorClient {
  async listCollections(): Promise<CollectionDoc[]> {
    return request<CollectionDoc[]>('/api/collections');
  }
  async query(req: QueryRequest): Promise<QueryResponse> {
    return request<QueryResponse>('/api/query', {
      method: 'POST',
      body: JSON.stringify(req),
    });
  }

  async recommendations(query: string): Promise<Recommendation[]> {
    const params = new URLSearchParams({ query });
    return request<Recommendation[]>(`/api/recommendations?${params.toString()}`);
  }

  async retiredCourses(query: string): Promise<RetiredCourse[]> {
    const params = new URLSearchParams({ query });
    return request<RetiredCourse[]>(`/api/retired-courses?${params.toString()}`);
  }

  async listConversations(archived = false, limit?: number, offset?: number): Promise<ConversationSummary[]> {
    const params = new URLSearchParams();
    if (archived) params.set('archived', 'true');
    if (limit !== undefined) params.set('limit', String(limit));
    if (offset !== undefined) params.set('offset', String(offset));
    const suffix = params.size ? `?${params.toString()}` : '';
    return request<ConversationSummary[]>(`/api/conversations${suffix}`);
  }

  async createConversation(body: CreateConversationRequest): Promise<ConversationSummary> {
    return request<ConversationSummary>('/api/conversations', { method: 'POST', body: JSON.stringify(body) });
  }

  async archiveConversation(id: string): Promise<ConversationSummary> {
    return request<ConversationSummary>(`/api/conversations/${encodeURIComponent(id)}/archive`, {
      method: 'POST',
    });
  }

  async restoreConversation(id: string): Promise<ConversationSummary> {
    return request<ConversationSummary>(`/api/conversations/${encodeURIComponent(id)}/restore`, {
      method: 'POST',
    });
  }

  async getConversation(id: string, after = 0, limit = 200): Promise<ConversationRecord> {
    const params = new URLSearchParams({ after: String(after), limit: String(limit) });
    return request<ConversationRecord>(`/api/conversations/${encodeURIComponent(id)}?${params}`);
  }

  async startConversationTurnStream(
    id: string,
    body: ConversationTurnRequest,
  ): Promise<ConversationStreamStartResponse> {
    return request<ConversationStreamStartResponse>(`/api/conversations/${encodeURIComponent(id)}/turns/stream`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async getConversationTurnEvents(
    id: string,
    requestId: string,
    afterEventId = 0,
    limit = 100,
  ): Promise<ConversationStreamEventsResponse> {
    const params = new URLSearchParams({ afterEventId: String(afterEventId), limit: String(limit) });
    return request<ConversationStreamEventsResponse>(
      `/api/conversations/${encodeURIComponent(id)}/turns/${encodeURIComponent(requestId)}/events?${params}`,
    );
  }

  async abandonConversationTurn(id: string, requestId: string): Promise<ConversationTerminalResponse> {
    return request<ConversationTerminalResponse>(
      `/api/conversations/${encodeURIComponent(id)}/turns/${encodeURIComponent(requestId)}/abandon`,
      { method: 'POST' },
    );
  }

  async inspectConversationMemory(id: string): Promise<ConversationMemoryInspection> {
    return request<ConversationMemoryInspection>(`/api/conversations/${encodeURIComponent(id)}/memory`);
  }

  async resetConversationMemory(id: string): Promise<ConversationResetResponse> {
    return request<ConversationResetResponse>(`/api/conversations/${encodeURIComponent(id)}/memory/reset`, {
      method: 'POST',
    });
  }

  async exportSummary(id: string, body?: ExportSummaryRequest): Promise<NativeArtifactRecord> {
    return request<NativeArtifactRecord>(`/api/conversations/${encodeURIComponent(id)}/export-summary`, {
      method: 'POST',
      body: JSON.stringify(body ?? {}),
    });
  }

  async listGaps(collectionId?: string): Promise<GapListResponse> {
    const query = collectionId ? `?${new URLSearchParams({ collectionId })}` : '';
    return request<GapListResponse>(`/api/gaps${query}`);
  }

  async resolveGap(id: string, body: ResolveGapRequest): Promise<RetrievalGap> {
    return request<RetrievalGap>(`/api/gaps/${encodeURIComponent(id)}/resolve`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async listSources(collection?: string): Promise<SourceDoc[]> {
    const query = collection ? `?${new URLSearchParams({ collection })}` : '';
    return request<SourceDoc[]>(`/api/sources${query}`);
  }

  async reindexSource(id: string, expectedIndexRevision: number): Promise<SourceDoc> {
    return request<SourceDoc>(`/api/sources/${encodeURIComponent(id)}/reindex`, {
      method: 'POST',
      body: JSON.stringify({ expectedIndexRevision }),
    });
  }

  async uploadSource(file: File): Promise<SourceDoc> {
    const form = new FormData();
    form.append('file', file);
    const response = await fetchOrThrow(
      '/api/sources',
      { method: 'POST', body: form, credentials: 'same-origin' },
      'Could not reach the backend API to upload the file.',
      'Upload failed',
    );
    return (await response.json()) as SourceDoc;
  }

  async createArtifact(spec: NativeArtifactSpec): Promise<NativeArtifactRecord> {
    return request<NativeArtifactRecord>('/api/artifacts', {
      method: 'POST',
      body: JSON.stringify({ kind: spec.kind, spec }),
    });
  }

  async importArtifact(file: File): Promise<NativeArtifactRecord> {
    const form = new FormData();
    form.append('file', file);
    return request<NativeArtifactRecord>('/api/artifacts/import', { method: 'POST', body: form });
  }

  async getArtifact(id: string, revisionId?: string): Promise<NativeArtifactRecord> {
    const query = revisionId ? `?revisionId=${encodeURIComponent(revisionId)}` : '';
    return request<NativeArtifactRecord>(`/api/artifacts/${encodeURIComponent(id)}${query}`);
  }

  async saveArtifactRevision(id: string, body: ArtifactRevisionRequest): Promise<NativeArtifactRecord> {
    return request<NativeArtifactRecord>(`/api/artifacts/${encodeURIComponent(id)}/revisions`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async previewArtifact(id: string, revisionId?: string): Promise<ArtifactPreview> {
    const query = revisionId ? `?revisionId=${encodeURIComponent(revisionId)}` : '';
    return request<ArtifactPreview>(`/api/artifacts/${encodeURIComponent(id)}/preview${query}`);
  }

  async downloadArtifact(
    id: string,
    revisionId: string,
    format: 'md' | 'docx' | 'xlsx' | 'html',
  ): Promise<{ blob: Blob; filename: string }> {
    const params = new URLSearchParams({ revisionId, format });
    return this.binaryDownload(`/api/artifacts/${encodeURIComponent(id)}/download?${params}`, 'artifact.' + format);
  }

  async downloadOriginalArtifact(id: string): Promise<{ blob: Blob; filename: string }> {
    return this.binaryDownload(`/api/artifacts/${encodeURIComponent(id)}/original`, 'original');
  }

  private async binaryDownload(path: string, fallbackName: string): Promise<{ blob: Blob; filename: string }> {
    const response = await fetchOrThrow(
      path,
      { credentials: 'same-origin' },
      'Could not reach the backend API to download the artifact.',
      'Download failed',
    );
    const disposition = response.headers.get('Content-Disposition') ?? '';
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
    const quoted = disposition.match(/filename="?([^";]+)"?/i)?.[1];
    return { blob: await response.blob(), filename: encoded ? decodeURIComponent(encoded) : (quoted ?? fallbackName) };
  }

  async createArtifactRun(body: ArtifactRunRequest): Promise<ArtifactRun> {
    return request<ArtifactRun>('/api/runs', { method: 'POST', body: JSON.stringify(body) });
  }

  async getArtifactRunLimits(): Promise<ArtifactRunLimits> {
    return request<ArtifactRunLimits>('/api/runs/limits');
  }

  async getArtifactRun(id: string): Promise<ArtifactRun> {
    return request<ArtifactRun>(`/api/runs/${encodeURIComponent(id)}`);
  }

  async listArtifactRunEvents(id: string, afterEventId = 0): Promise<ArtifactRunEvent[]> {
    const params = new URLSearchParams({ afterEventId: String(afterEventId) });
    return request<ArtifactRunEvent[]>(`/api/runs/${encodeURIComponent(id)}/events?${params}`);
  }

  async cancelArtifactRun(id: string): Promise<ArtifactRun> {
    return request<ArtifactRun>(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
  }

  async listArtifactBatches(): Promise<ArtifactBatchSummary[]> {
    return request<ArtifactBatchSummary[]>('/api/artifact-batches');
  }

  async createArtifactBatch(body: ArtifactBatchRequest): Promise<ArtifactBatchRecord> {
    return request<ArtifactBatchRecord>('/api/artifact-batches', { method: 'POST', body: JSON.stringify(body) });
  }

  async getArtifactBatch(id: string): Promise<ArtifactBatchRecord> {
    return request<ArtifactBatchRecord>(`/api/artifact-batches/${encodeURIComponent(id)}`);
  }

  async pollArtifactBatch(id: string): Promise<ArtifactBatchRecord> {
    return request<ArtifactBatchRecord>(`/api/artifact-batches/${encodeURIComponent(id)}/poll`, { method: 'POST' });
  }

  async cancelArtifactBatch(id: string): Promise<ArtifactBatchRecord> {
    return request<ArtifactBatchRecord>(`/api/artifact-batches/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
  }

  async listConnectionProfiles(): Promise<ConnectionProfile[]> {
    return request<ConnectionProfile[]>('/api/admin/connections');
  }
  async createConnectionProfile(
    body: ConnectionProfileWrite & { secret?: Record<string, string> },
  ): Promise<ConnectionProfile> {
    return request<ConnectionProfile>('/api/admin/connections', { method: 'POST', body: JSON.stringify(body) });
  }
  async updateConnectionProfile(
    id: string,
    body: ConnectionProfileWrite & { expected_revision: number },
  ): Promise<ConnectionProfile> {
    return request<ConnectionProfile>(`/api/admin/connections/${encodeURIComponent(id)}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }
  async replaceConnectionSecret(
    id: string,
    body: { expected_revision: number; secret: Record<string, string> },
  ): Promise<ConnectionProfile> {
    return request<ConnectionProfile>(`/api/admin/connections/${encodeURIComponent(id)}/secret`, {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }
  async clearConnectionSecret(id: string, body: { expected_revision: number }): Promise<ConnectionProfile> {
    return request<ConnectionProfile>(`/api/admin/connections/${encodeURIComponent(id)}/secret`, {
      method: 'DELETE',
      body: JSON.stringify(body),
    });
  }
  async deleteConnectionProfile(id: string, body: { expected_revision: number }): Promise<{ deleted: boolean }> {
    return request<{ deleted: boolean }>(`/api/admin/connections/${encodeURIComponent(id)}`, {
      method: 'DELETE',
      body: JSON.stringify(body),
    });
  }
  async diagnoseConnection(id: string): Promise<ConnectionDiagnostic> {
    return request<ConnectionDiagnostic>(`/api/admin/connections/${encodeURIComponent(id)}/diagnose`);
  }
  async getSession(): Promise<AuthSession> {
    return request<AuthSession>('/api/session', { cache: 'no-store' });
  }
  async getEffectiveFlags(): Promise<EffectiveFlagsResponse> {
    return request<EffectiveFlagsResponse>('/api/flags/effective', { cache: 'no-store' });
  }
  async logout(): Promise<void> {
    await request<void>('/api/auth/logout', { method: 'POST' });
  }
  beginLogin(): void {
    window.location.assign('/api/auth/login');
  }
  async listArtifacts(): Promise<NativeArtifactSummary[]> {
    return request<NativeArtifactSummary[]>('/api/artifacts');
  }
  async getAcademicRecord(): Promise<AcademicRecordDetailResponse> {
    return request<AcademicRecordDetailResponse>('/api/academic-record');
  }
  async upsertAcademicAttempt(body: AcademicRecordAttemptUpsertRequest): Promise<AcademicRecordAttemptItem> {
    return request<AcademicRecordAttemptItem>('/api/academic-record/attempts', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }
  async previewAcademicImport(body: AcademicRecordImportPreviewRequest): Promise<AcademicRecordImportBatchResponse> {
    return request<AcademicRecordImportBatchResponse>('/api/academic-record/imports/preview', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }
  async updateAcademicImport(
    batchId: string,
    body: AcademicRecordImportUpdateRequest,
  ): Promise<AcademicRecordImportBatchResponse> {
    return request<AcademicRecordImportBatchResponse>(
      `/api/academic-record/imports/${encodeURIComponent(batchId)}/rows`,
      {
        method: 'POST',
        body: JSON.stringify(body),
      },
    );
  }
  async commitAcademicImport(
    batchId: string,
    body: AcademicRecordImportCommitRequest,
  ): Promise<AcademicRecordImportCommitResponse> {
    return request<AcademicRecordImportCommitResponse>(
      `/api/academic-record/imports/${encodeURIComponent(batchId)}/commit`,
      { method: 'POST', body: JSON.stringify(body) },
    );
  }
  async getAcademicRecordRevision(revision: number): Promise<AcademicRecordRevisionResponse> {
    return request<AcademicRecordRevisionResponse>(`/api/academic-record/revisions/${revision}`);
  }
  async listAuditablePrograms(): Promise<AuditableProgramItem[]> {
    return request<AuditableProgramItem[]>('/api/audits/programs');
  }
  async listProgramAudits(): Promise<DegreeAuditRecordItem[]> {
    return request<DegreeAuditRecordItem[]>('/api/audits');
  }
  async runProgramAudit(body: DegreeAuditRunRequest): Promise<DegreeAuditDetailResponse> {
    return request<DegreeAuditDetailResponse>('/api/audits/run', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }
  async getProgramAudit(auditId: string): Promise<DegreeAuditDetailResponse> {
    return request<DegreeAuditDetailResponse>(`/api/audits/${encodeURIComponent(auditId)}`);
  }
  async exportProgramAudit(auditId: string): Promise<DegreeAuditDetailResponse> {
    return request<DegreeAuditDetailResponse>(`/api/audits/${encodeURIComponent(auditId)}/export`);
  }
  async listProgramComparisons(): Promise<ProgramComparisonSummary[]> {
    return request<ProgramComparisonSummary[]>('/api/comparisons');
  }
  async createProgramComparison(body: ProgramComparisonCreateRequest): Promise<ProgramComparisonResponse> {
    return request<ProgramComparisonResponse>('/api/comparisons', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }
  async submitResponseFeedback(body: ResponseFeedbackRequest): Promise<ResponseFeedbackResponse> {
    return request<ResponseFeedbackResponse>('/api/feedback', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }
  async listPrivacyExports(): Promise<PrivacyExportResponse[]> {
    return request<PrivacyExportResponse[]>('/api/privacy/exports');
  }
  async createPrivacyExport(): Promise<PrivacyExportResponse> {
    return request<PrivacyExportResponse>('/api/privacy/exports', { method: 'POST' });
  }
  async downloadPrivacyExport(exportId: string, token: string): Promise<PrivacyExportDownloadResponse> {
    return request<PrivacyExportDownloadResponse>(`/api/privacy/exports/${encodeURIComponent(exportId)}/download`, {
      headers: { 'X-Export-Token': token },
    });
  }
}
