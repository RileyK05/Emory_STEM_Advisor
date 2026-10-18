import type {
  AdvisorClient,
  QueryRequest,
  QueryResponse,
  SourceDoc,
  CollectionDoc,
  Recommendation,
  ArtifactPreview,
  ArtifactRevisionRequest,
  NativeArtifactRecord,
  NativeArtifactSpec,
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
  ConversationTurnResponse,
  ConversationStreamStartResponse,
  ConversationStreamEventsResponse,
  ConversationTerminalResponse,
  ConversationSummaryCheckpoint,
  NativeDecisionRequest,
  NativeDecision,
  GapListResponse,
  ResolveGapRequest,
  RetrievalGap,
} from './types';

/** Base URL of the backend. Defaults to the Vite dev proxy path. */
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

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
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, init);
  } catch {
    throw new Error(unreachableMessage);
  }
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith('/api/auth/'))
      window.dispatchEvent(new Event('advisor-session-invalid'));
    // A 5xx with an empty body is the dev proxy failing to reach a stopped API,
    // not an application error. Report it as unreachable rather than a
    // misleading "500 Internal Server Error".
    if (response.status >= 500 && (await response.clone().text()).length === 0) throw new Error(unreachableMessage);
    throw await errorDetail(response, errorPrefix);
  }
  return response;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetchOrThrow(
    path,
    {
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
      credentials: 'same-origin',
      ...init,
    },
    'Could not reach the backend API. Is it running, and is VITE_API_MODE=http?',
    'API request failed',
  );
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
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

  async listConversations(): Promise<ConversationSummary[]> {
    return request<ConversationSummary[]>('/api/conversations');
  }

  async createConversation(body: CreateConversationRequest): Promise<ConversationSummary> {
    return request<ConversationSummary>('/api/conversations', { method: 'POST', body: JSON.stringify(body) });
  }

  async getConversation(id: string, after = 0, limit = 200): Promise<ConversationRecord> {
    const params = new URLSearchParams({ after: String(after), limit: String(limit) });
    return request<ConversationRecord>(`/api/conversations/${encodeURIComponent(id)}?${params}`);
  }

  async sendConversationTurn(id: string, body: ConversationTurnRequest): Promise<ConversationTurnResponse> {
    return request<ConversationTurnResponse>(`/api/conversations/${encodeURIComponent(id)}/turns`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
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

  async listConversationSummaries(id: string): Promise<ConversationSummaryCheckpoint[]> {
    return request<ConversationSummaryCheckpoint[]>(`/api/conversations/${encodeURIComponent(id)}/summaries`);
  }

  async submitDecision(body: NativeDecisionRequest): Promise<NativeDecision> {
    return request<NativeDecision>('/api/decisions', { method: 'POST', body: JSON.stringify(body) });
  }

  async getDecision(id: string): Promise<NativeDecision> {
    return request<NativeDecision>(`/api/decisions/${encodeURIComponent(id)}`);
  }

  async abandonDecision(id: string): Promise<NativeDecision> {
    return request<NativeDecision>(`/api/decisions/${encodeURIComponent(id)}/abandon`, { method: 'POST' });
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
    return request<NativeArtifactRecord>('/api/artifacts/import', { method: 'POST', body: form, headers: {} });
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
  async logout(): Promise<void> {
    await request<void>('/api/auth/logout', { method: 'POST' });
  }
  beginLogin(): void {
    window.location.assign('/api/auth/login');
  }
  async listArtifacts(): Promise<NativeArtifactSummary[]> {
    return request<NativeArtifactSummary[]>('/api/artifacts');
  }
}
