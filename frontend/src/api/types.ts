// API contract types mirroring docs/architecture.md.
// These are the contract the HttpClient (frontend/src/api/http.ts) and the
// backend FastAPI schemas (backend/app/api/schemas.py) both satisfy; the mock
// client satisfies them too.

export type SeamName = 'keyword' | 'embed' | 'prereq';

export interface SeamStatus {
  name: SeamName;
  enabled: boolean;
  /** active = produced candidates; dormant = enabled but no usable data;
   *  disabled = off in config; failed = errored during the query. */
  state: 'active' | 'dormant' | 'disabled' | 'failed';
  reason?: string;
  candidateCount: number;
}

export interface Contribution {
  chunkId: string;
  /** Every seam that surfaced this chunk. Multiplicity is signal, per fusion step 1. */
  seams: SeamName[];
  normalizedScore: number;
  /** Slot this chunk's source was allocated in relevance allocation. */
  sourceSlot: number;
}

export interface ContextItem {
  chunkId: string;
  sourceId: string;
  sourceName: string;
  locatorLabel: string;
  text: string;
}

export interface PromptInfo {
  system: string;
  user: string;
}

export interface ModelCallInfo {
  provider: string;
  modelId: string;
  promptTokens: number | null;
  completionTokens: number | null;
  latencyMs: number;
  usageSource?: 'provider' | 'estimated' | 'unknown';
}

export interface RetrievalTrace {
  traceId: string;
  query: string;
  normalizedQuery: string;
  seams: SeamStatus[];
  contributions: Contribution[];
  /** Exactly the chunk texts assembled as the model's context block. */
  contextBlock: ContextItem[];
  prompt: PromptInfo;
  modelCall: ModelCallInfo;
  warnings: string[];
  errors: string[];
  /** Retrieval-stage wall times in milliseconds; diagnostic only. */
  timings: Record<string, number>;
}

export interface Citation {
  sourceId: string;
  sourceName: string;
  locatorLabel: string;
  chunkId: string;
}

export interface QueryRequest {
  query: string;
  collectionId?: string;
  /** Omitted/null searches the collection; [] means explicitly search no sources. */
  sourceIds?: string[] | null;
  studentCohort?: string | null;
}

// Native authoring artifacts are immutable revisions.
export type NativeArtifactKind = 'document' | 'workbook' | 'html';
export type WorkbookCellStyle = {
  bold?: boolean;
  italic?: boolean;
  font_color?: string | null;
  fill_color?: string | null;
  number_format?: string | null;
  wrap_text?: boolean | null;
};
export type NativeArtifactSpec = DocumentSpec | WorkbookSpec | HtmlArtifactSpec;
export interface DocumentBlock {
  id: string;
  type: 'paragraph' | 'heading' | 'list' | 'table' | 'code' | 'quote';
  text?: string;
  level?: number;
  ordered?: boolean;
  items?: string[];
  headers?: string[];
  rows?: string[][];
  language?: string | null;
}
export interface DocumentSpec {
  kind: 'document';
  title: string;
  blocks: DocumentBlock[];
  page_layout?: Record<string, number>;
  evidence?: Record<string, ArtifactEvidenceReference[]>;
  provenance?: Record<string, string | number | boolean | null>;
}
export interface ArtifactEvidenceReference {
  chunk_id: string;
  source_id: string;
  source_name: string;
  locator_label: string;
  text_snapshot: string;
}
export interface WorkbookSpec {
  kind: 'workbook';
  title: string;
  sheets: {
    id: string;
    name: string;
    cells: {
      id: string;
      row: number;
      column: number;
      value?: string | number | boolean | null;
      value_type?: 'date' | 'datetime' | null;
      formula?: string | null;
      style?: WorkbookCellStyle;
    }[];
    column_widths?: Record<string, number>;
    row_heights?: Record<number, number>;
    freeze_panes?: string | null;
  }[];
  charts?: {
    id: string;
    type: 'bar' | 'line' | 'pie';
    title: string;
    sheet_id: string;
    data_range: string;
    categories_range?: string | null;
    titles_from_data?: boolean;
  }[];
  evidence?: Record<string, ArtifactEvidenceReference[]>;
  provenance?: Record<string, string | number | boolean | null>;
}
export interface HtmlArtifactSpec {
  kind: 'html';
  title: string;
  html: string;
  provenance?: Record<string, string | number | boolean | null>;
}
export interface NativeArtifactRecord {
  artifactId: string;
  revisionId: string;
  parentRevisionId: string | null;
  kind: NativeArtifactKind;
  title: string;
  spec: NativeArtifactSpec;
  status?: 'ready' | 'needs_recalculation';
  message?: string | null;
}
export interface ArtifactRevisionSummary {
  revisionId: string;
  parentRevisionId: string | null;
  title: string;
  createdAt: string;
}
export interface ArtifactPreview {
  kind: NativeArtifactKind;
  title?: string;
  html?: string;
  status?: 'ready' | 'needs_recalculation';
  sheets?: {
    id: string;
    name: string;
    cells: {
      id: string;
      row: number;
      column: number;
      value?: string | number | boolean | null;
      formula?: string | null;
      calculatedValue?: string | number | boolean | null;
    }[];
  }[];
  formulas?: unknown[];
  sandboxRequired?: boolean;
}
export interface ArtifactRun {
  runId: string;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  artifactIds?: string[];
  artifactRevisions?: { artifactId: string; revisionId: string }[];
  error?: string | null;
}
export interface ArtifactRunEvent extends Pick<ArtifactRun, 'artifactIds' | 'artifactRevisions' | 'error'> {
  eventId: number;
  runId: string;
  state: ArtifactRun['state'];
  stage?:
    | 'planning'
    | 'retrieving'
    | 'routing'
    | 'generating'
    | 'repairing'
    | 'assembling'
    | 'validating'
    | 'validated'
    | 'rendering'
    | 'recalculating'
    | 'publishing'
    | 'outlining'
    | 'writing'
    | 'checking'
    | 'tool_started'
    | 'tool_completed'
    | 'tool_reused'
    | 'tool_replayed'
    | 'completed'
    | 'reused'
    | 'replayed';
  /** The plan unit (section or worksheet key) a dependent run is working on. */
  unit?: string;
  requestedOutputTokenLimit?: number;
  outputTokenLimit?: number;
}

export type ConnectionRole = 'standard' | 'complex' | 'fast_generation';
export type ConnectionCapability = 'structured_output' | 'tools' | 'streaming' | 'vision';
export type ConnectionDataScope = 'institutional_corpus' | 'user_supplied_data' | 'private_workspace';
export type ConnectionProvider = 'openai' | 'anthropic' | 'google' | 'openai-compatible' | 'hf-local';
export interface ConnectionProfile {
  profile_id: string;
  name: string;
  provider: ConnectionProvider;
  model_id: string;
  base_url: string | null;
  account_id: string | null;
  secret_configured: boolean;
  provider_options: Record<string, unknown>;
  roles: ConnectionRole[];
  capabilities: ConnectionCapability[];
  data_scopes: ConnectionDataScope[];
  enabled: boolean;
  priority: number;
  revision: number;
  created_at: string;
  updated_at: string;
  eval_passed: boolean;
}
export interface ConnectionProfileWrite {
  name: string;
  provider: ConnectionProvider;
  model_id: string;
  roles: ConnectionRole[];
  capabilities: ConnectionCapability[];
  data_scopes: ConnectionDataScope[];
  base_url: string | null;
  account_id: string | null;
  provider_options: Record<string, unknown>;
  enabled: boolean;
  priority: number;
}
export interface ConnectionDiagnostic {
  status: 'not_required' | 'configured' | 'missing' | 'unreadable';
  message: string;
}
export interface AuthSession {
  mode: 'development' | 'oidc';
  authenticated: boolean;
  workspaceKey?: string;
  displayName?: string | null;
  email?: string | null;
  role?: 'student' | 'admin' | null;
}
export interface NativeArtifactSummary {
  artifactId: string;
  revisionId: string;
  kind: NativeArtifactKind;
  title: string;
  status?: 'ready' | 'needs_recalculation';
}

export interface ResearchBlock {
  kind: 'fact' | 'judgment' | 'assumption' | 'limitation' | 'question';
  text: string;
  evidenceIds: string[];
  citationNumbers: number[];
  candidateIds: string[];
}

export interface ArtifactRequestInfo {
  kind: NativeArtifactKind;
  title: string;
  brief: string;
  purpose?: 'create' | 'revise' | 'summary';
  revises?: { artifactId: string; revisionId: string } | null;
}

export interface ResearchDecisionInfo {
  runId: string;
  outcome: 'recommendation' | 'information' | 'clarification' | 'insufficient_evidence' | 'declined';
  completeness: 'complete' | 'partial';
  blocks: ResearchBlock[];
  artifact?: ArtifactRequestInfo | null;
  unresolvedQuestions: string[];
}

export interface SourceApplicabilityItem {
  sourceId: string;
  sourceName: string;
  sourceCategory: string;
  edition?: string | null;
  publishedAt?: string | null;
  isCurrent: boolean;
  isSuperseded: boolean;
  freshness: 'current' | 'aging' | 'stale' | 'unknown';
  applicabilityStatus: 'reviewed_default' | 'historical' | 'unknown';
  cohortCompatibility: 'compatible' | 'mismatch' | 'unknown';
  summary: string;
}

export interface ApplicabilityNotice {
  level: 'info' | 'warning' | 'conflict';
  headline: string;
  hasConflicts: boolean;
  conflicts: string[];
  sources: SourceApplicabilityItem[];
  declaredCohort?: string | null;
}

export type QueryResponse = {
  type: 'answer';
  answer: string;
  citations: Citation[];
  trace: RetrievalTrace;
  claims?: ParagraphClaimReviewUnit[];
  research?: ResearchDecisionInfo | null;
  applicability?: ApplicabilityNotice | null;
  artifactRun?: ArtifactRunInfo | null;
  artifactNotice?: string | null;
};

export interface ParagraphClaimReviewUnit {
  text: string;
  citationChunkIds: string[];
  reviewStatus: 'unreviewed';
}

export interface ArtifactRevisionReference {
  artifactId: string;
  revisionId: string;
}

export interface EffectiveArtifactReference extends ArtifactRevisionReference {
  spec: { title?: string | null; name?: string | null; kind?: string | null };
}
export interface EffectiveConversationInputs {
  collectionId: string | null;
  sourceIds: string[] | null;
  retrievalQuery?: string;
  artifactReferences: EffectiveArtifactReference[];
}
export interface ConversationUserTurnEvidence {
  request: ConversationTurnRequest;
  query: string;
  effective: EffectiveConversationInputs;
}
export interface ConversationAssistantTurnEvidence {
  type: 'answer' | 'refusal' | 'history' | 'failed' | 'abandoned';
  citations: Citation[];
  contextBlock: ContextItem[];
  traceId: string | null;
  turnReferences: number[];
  research?: ResearchDecisionInfo;
  applicability?: ApplicabilityNotice;
  artifactRun?: ArtifactRunInfo | null;
  artifactNotice?: string | null;
}
export type ConversationTurnEvidence = ConversationUserTurnEvidence | ConversationAssistantTurnEvidence;

export interface ArtifactRunInfo {
  runId: string;
  kind: NativeArtifactKind;
  title: string;
  purpose: 'create' | 'revise' | 'summary';
}

export interface ConversationTurn {
  sequence: number;
  requestId: string;
  role: 'user' | 'assistant';
  text: string;
  status: 'input' | 'answer' | 'refusal' | 'history' | 'failed' | 'abandoned';
  evidence: ConversationTurnEvidence;
  artifactRun?: ArtifactRunInfo | null;
  artifactNotice?: string | null;
  createdAt: string;
}

export interface ConversationSummary {
  conversationId: string;
  title: string;
  lastSequence: number;
  pendingRequestId: string | null;
  contextEpoch?: number;
  contextBoundarySequence?: number;
  contextResetAt?: string | null;
  archivedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}
export interface ConversationRecord extends ConversationSummary {
  turns: ConversationTurn[];
}

export interface GapResolution {
  sourceIds: string[];
  note: string;
  resolvedAt: string;
  evidenceState?: 'current' | 'ineligible';
}
export interface RetrievalGap {
  gapId: string;
  collectionId: string;
  query: string;
  sourceIds: string[] | null;
  state: 'open' | 'resolved';
  revision: number;
  occurrenceCount: number;
  recurrenceCount: number;
  successfulRecurrenceCount: number;
  firstSeenAt: string;
  lastSeenAt: string;
  resolution: GapResolution | null;
}
export interface GapMetrics {
  repeatedGapObservations: number;
  successfulRepeatedObservations: number;
  recurrenceRate: number | null;
}
export interface GapListResponse {
  gaps: RetrievalGap[];
  metrics: GapMetrics;
}
export interface ResolveGapRequest {
  expectedRevision: number;
  sourceIds: string[];
  note: string;
}

export interface CreateConversationRequest {
  title: string;
}
export interface ConversationTurnRequest {
  query: string;
  collectionId?: string;
  sourceIds?: string[] | null;
  requestId: string;
  expectedSequence: number;
  artifactReferences?: ArtifactRevisionReference[];
  studentCohort?: string | null;
}

export interface ConversationTerminalResponse {
  type: 'failed' | 'abandoned';
  reason: string;
  statusCode?: number;
  conversationId: string;
  sequence: number;
}

export interface ConversationStreamStartResponse {
  conversationId: string;
  requestId: string;
  jobId: string | null;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'unknown';
}
export type ConversationStreamEvent =
  | {
      eventId: number;
      type: 'stage';
      stage:
        | 'planning'
        | 'retrieving'
        | 'routing'
        | 'generating'
        | 'using_tools'
        | 'validating'
        | 'validated'
        | 'researching'
        | 'comparing'
        | 'finalizing';
    }
  | { eventId: number; type: 'state'; state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' };
export interface ConversationStreamEventsResponse {
  events: ConversationStreamEvent[];
  nextEventId: number;
  requestState: 'pending' | 'completed' | 'failed' | 'abandoned';
  jobState: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'unknown';
  done: boolean;
}

export interface SourceDoc {
  id: string;
  name: string;
  locatorType: string;
  status: 'uploaded' | 'parsed' | 'chunked' | 'indexed' | 'failed';
  chunkCount: number;
  errorMessage?: string | null;
  warnings?: string[];
  indexRevision?: number;
  canReindex?: boolean;
}

export interface CollectionDoc {
  id: string;
  name: string;
}

export interface ReadinessStatus {
  status: 'ready' | 'unavailable';
  reasons?: string[];
}

export interface AdvisorClient {
  getReadiness(): Promise<ReadinessStatus>;
  listCollections(): Promise<CollectionDoc[]>;
  query(req: QueryRequest): Promise<QueryResponse>;
  listConversations(archived?: boolean, limit?: number, offset?: number): Promise<ConversationSummary[]>;
  createConversation(request: CreateConversationRequest): Promise<ConversationSummary>;
  archiveConversation(id: string): Promise<ConversationSummary>;
  restoreConversation(id: string): Promise<ConversationSummary>;
  getConversation(id: string, after?: number, limit?: number): Promise<ConversationRecord>;
  startConversationTurnStream(id: string, request: ConversationTurnRequest): Promise<ConversationStreamStartResponse>;
  getConversationTurnEvents(
    id: string,
    requestId: string,
    afterEventId?: number,
    limit?: number,
  ): Promise<ConversationStreamEventsResponse>;
  abandonConversationTurn(id: string, requestId: string): Promise<ConversationTerminalResponse>;
  listGaps(collectionId?: string): Promise<GapListResponse>;
  resolveGap(id: string, request: ResolveGapRequest): Promise<RetrievalGap>;
  listSources(collection?: string): Promise<SourceDoc[]>;
  reindexSource(id: string, expectedIndexRevision: number): Promise<SourceDoc>;
  /** Ingest a file into the backend. The mock rejects; the HttpClient posts. */
  uploadSource(file: File): Promise<SourceDoc>;
  getArtifact(id: string, revisionId?: string): Promise<NativeArtifactRecord>;
  listArtifactRevisions(id: string): Promise<ArtifactRevisionSummary[]>;
  previewArtifact(id: string, revisionId?: string): Promise<ArtifactPreview>;
  downloadArtifact(
    id: string,
    revisionId: string,
    format: 'md' | 'docx' | 'xlsx' | 'html',
  ): Promise<{ blob: Blob; filename: string }>;
  getArtifactRun(id: string): Promise<ArtifactRun>;
  listArtifactRunEvents(id: string, afterEventId?: number): Promise<ArtifactRunEvent[]>;
  cancelArtifactRun(id: string): Promise<ArtifactRun>;
  retryArtifactRun(id: string): Promise<ArtifactRun>;
  listConnectionProfiles(): Promise<ConnectionProfile[]>;
  createConnectionProfile(
    body: ConnectionProfileWrite & { secret?: Record<string, string> },
  ): Promise<ConnectionProfile>;
  updateConnectionProfile(
    id: string,
    body: ConnectionProfileWrite & { expected_revision: number },
  ): Promise<ConnectionProfile>;
  replaceConnectionSecret(
    id: string,
    body: { expected_revision: number; secret: Record<string, string> },
  ): Promise<ConnectionProfile>;
  clearConnectionSecret(id: string, body: { expected_revision: number }): Promise<ConnectionProfile>;
  deleteConnectionProfile(id: string, body: { expected_revision: number }): Promise<{ deleted: boolean }>;
  diagnoseConnection(id: string): Promise<ConnectionDiagnostic>;
  getSession(): Promise<AuthSession>;
  logout(): Promise<void>;
  listArtifacts(): Promise<NativeArtifactSummary[]>;
  beginLogin(): void;
  submitResponseFeedback(body: ResponseFeedbackRequest): Promise<ResponseFeedbackResponse>;
  createPrivacyExport(): Promise<PrivacyExportResponse>;
  downloadPrivacyExport(exportId: string, token: string): Promise<PrivacyExportDownloadResponse>;
  deleteMyData(body: PrivacyDeletionRequest): Promise<PrivacyDeletionResponse>;
}

export interface AdminOverview {
  users: number;
  admins: number;
  disabledUsers: number;
  activeSessions: number;
  conversations: number;
  turns: number;
  queuedJobs: number;
  runningJobs: number;
  failedJobs: number;
  sources: number;
  artifacts: number;
  traces: number;
}

export type UserRole = 'student' | 'admin';

export interface AdminUser {
  userId: string;
  issuer: string;
  subject: string;
  workspaceKey: string;
  displayName?: string | null;
  email?: string | null;
  role: UserRole;
  status: 'active' | 'disabled';
  conversationCount: number;
  activeSessions: number;
  createdAt: string;
  updatedAt: string;
  lastSeenAt?: string | null;
}

export interface AdminUserUpdate {
  role: UserRole;
  status: 'active' | 'disabled';
}

export type PrivacyStatus = 'queued' | 'running' | 'completed' | 'failed';

export interface PrivacyExportResponse {
  exportId: string;
  workspaceKey: string;
  status: PrivacyStatus;
  manifest?: Record<string, unknown> | null;
  downloadToken?: string | null;
  expiresAt: string;
  createdAt: string;
  finishedAt?: string | null;
  error?: string | null;
}

export interface PrivacyExportDownloadResponse {
  exportId: string;
  workspaceKey: string;
  status: PrivacyStatus;
  manifest: Record<string, unknown>;
  contentDigest: string;
  expiresAt: string;
}

export interface PrivacyDeletionRequest {
  confirmation: string;
  reason?: string;
}

export interface PrivacyDeletionResponse {
  deletionId: string;
  workspaceKey: string;
  status: PrivacyStatus;
  deletedCounts: Record<string, unknown>;
  createdAt: string;
  finishedAt?: string | null;
  error?: string | null;
  message?: string;
}

export interface AdminAuditEntry {
  auditId: string;
  actorUserId?: string | null;
  actorWorkspace: string;
  action: string;
  targetType: string;
  targetId?: string | null;
  detail: Record<string, unknown>;
  createdAt: string;
  actorEmail?: string | null;
  actorDisplayName?: string | null;
}

export interface AdminJob {
  jobId: string;
  kind: string;
  scopeKey: string;
  status: string;
  attemptCount: number;
  maxAttempts: number;
  cancelRequested: boolean;
  error?: Record<string, unknown> | null;
  createdAt: string;
  updatedAt: string;
  finishedAt?: string | null;
}

export interface AdminSource {
  sourceId: string;
  collectionId: string;
  filename: string;
  contentType: string;
  status: string;
  workspaceKey?: string | null;
  indexRevision: number;
  isCurrent: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface AdminUserSession {
  createdAt: string;
  expiresAt: string;
  revokedAt?: string | null;
}

export interface TelemetryUsageSummary {
  costCurrency?: string | null;
  costCurrencies?: string[];
  totalCalls: number;
  completedCalls: number;
  failedCalls: number;
  totalPromptTokens?: number | null;
  totalCompletionTokens?: number | null;
  totalCachedTokens?: number | null;
  cachedTokenShare?: number | null;
  callsReportingCache: number;
  callsWithCacheHits: number;
  cacheHitFraction?: number | null;
  cacheReportingCoverage?: number | null;
  totalCost?: number | null;
  measuredCost?: number | null;
  estimatedCost?: number | null;
  unknownCostCalls: number;
  costCompleteness: 'complete' | 'partial' | 'unknown';
}

export interface TelemetryUsageBreakdownRow {
  costCurrency?: string | null;
  costCurrencies?: string[];
  bucket: string;
  provider?: string | null;
  modelId?: string | null;
  totalCalls: number;
  completedCalls: number;
  failedCalls: number;
  totalPromptTokens?: number | null;
  totalCompletionTokens?: number | null;
  totalCachedTokens?: number | null;
  cachedTokenShare?: number | null;
  callsReportingCache: number;
  callsWithCacheHits: number;
  cacheHitFraction?: number | null;
  cacheReportingCoverage?: number | null;
  totalCost?: number | null;
  measuredCost?: number | null;
  estimatedCost?: number | null;
  unknownCostCalls: number;
  costCompleteness: 'complete' | 'partial' | 'unknown';
}

export interface TelemetryUsageResponse {
  summary: TelemetryUsageSummary;
  breakdown: TelemetryUsageBreakdownRow[];
}

export interface ModelPriceSchedule {
  billingMode: 'standard' | 'batch';
  scheduleId: string;
  provider: string;
  modelId: string;
  currency: string;
  inputRatePerMillion: number;
  cachedReadRatePerMillion: number;
  outputRatePerMillion: number;
  effectiveFrom: string;
  effectiveTo?: string | null;
  provenance: string;
  createdAt: string;
}

export interface SourceReviewProposal {
  proposalId: string;
  sourceId: string;
  expectedMetadataRevision: number;
  proposedByUserId?: string | null;
  status: string;
  sourceCategory: string;
  classificationOrigin: string;
  publisher?: string | null;
  sourceUrl?: string | null;
  edition?: string | null;
  geography?: string | null;
  occupationId?: string | null;
  publishedAt?: string | null;
  acquiredAt?: string | null;
  attribution?: string | null;
  title?: string | null;
  institution?: string | null;
  issuingUnit?: string | null;
  documentRoles?: string[];
  applicability?: Record<string, string[]>;
  applicabilityStatus: string;
  lifecycleStatus: string;
  replacementIntent?: string | null;
  reviewNotes?: string | null;
  rejectionReason?: string | null;
  reviewedByUserId?: string | null;
  reviewedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface SourceLifecycleEvent {
  eventId: string;
  sourceId: string;
  eventType: string;
  proposalId?: string | null;
  metadataRevision: number;
  actorUserId?: string | null;
  details?: Record<string, any>;
  createdAt: string;
}

export interface SourceDependencies {
  programCount: number;
  courseCount: number;
  gapCount: number;
  researchRunCount: number;
  programs?: Array<Record<string, any>>;
}

export interface SourceReviewDetailResponse {
  source: Record<string, any>;
  pendingProposal?: SourceReviewProposal | null;
  proposals?: SourceReviewProposal[];
  lifecycleEvents?: SourceLifecycleEvent[];
  dependencies: SourceDependencies;
}

export interface SourceReviewProposalCreate {
  expectedMetadataRevision: number;
  sourceCategory: string;
  publisher?: string | null;
  sourceUrl?: string | null;
  edition?: string | null;
  geography?: string | null;
  occupationId?: string | null;
  publishedAt?: string | null;
  acquiredAt?: string | null;
  attribution?: string | null;
  title?: string | null;
  institution?: string | null;
  issuingUnit?: string | null;
  documentRoles?: string[];
  applicability?: Record<string, string[]>;
  applicabilityStatus?: string;
  lifecycleStatus?: string;
  replacementIntent?: string | null;
  reviewNotes?: string | null;
}

export interface SourceLifecycleChangeRequest {
  action: 'activate' | 'deprecate' | 'archive';
  expectedMetadataRevision: number;
  notes?: string;
}

export interface SourceRevisionPassage {
  chunkId: string;
  chunkIndex: number;
  locatorLabel: string;
  text: string;
  textDigest: string;
}

export interface SourceRevisionEvidenceResponse {
  sourceId: string;
  indexRevision: number;
  passages?: SourceRevisionPassage[];
}

export interface SourceLifecycleChangeResponse {
  sourceId: string;
  lifecycleStatus: string;
  metadataRevision: number;
}

export interface ProgramRawItem {
  position: number;
  section?: string | null;
  text: string;
  choose?: number | null;
  mentions?: string[];
  evidence?: Array<Record<string, any>>;
}

export interface ProgramRuleReleaseSummary {
  releaseId: string;
  programId: string;
  collectionId: string;
  sourceId: string;
  sourceIndexRevision: number;
  sourceSha256: string;
  releaseVersion: number;
  status: string;
  rulesDigest: string;
  coverageSummary?: Record<string, any>;
  createdByUserId?: string | null;
  approvedByUserId?: string | null;
  approvedAt?: string | null;
  notes?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ProgramRuleReleaseDetail {
  releaseId: string;
  programId: string;
  collectionId: string;
  sourceId: string;
  sourceIndexRevision: number;
  sourceSha256: string;
  releaseVersion: number;
  status: string;
  rulesTree: Record<string, any>;
  rulesDigest: string;
  coverageSummary?: Record<string, any>;
  createdByUserId?: string | null;
  approvedByUserId?: string | null;
  approvedAt?: string | null;
  notes?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ProgramInspectionResponse {
  program: Record<string, any>;
  items?: ProgramRawItem[];
  releases?: ProgramRuleReleaseSummary[];
  currentRelease?: ProgramRuleReleaseDetail | null;
}

export interface ResponseFeedbackRequest {
  responseId: string;
  vote: 'up' | 'down';
  reasonCode: string;
  comment?: string | null;
  workspaceKey?: string | null;
}

export interface ResponseFeedbackResponse {
  feedbackId: string;
  workspaceKey: string;
  responseId: string;
  revision: number;
  vote: 'up' | 'down';
  reasonCode: string;
  comment?: string | null;
  currentVotes: number;
  createdAt: string;
}

export interface FeedbackTriageRequest {
  feedbackId: string;
  issueType: 'missing_evidence' | 'provider' | 'explanation' | 'privacy' | 'ui';
  toStatus: 'open' | 'reviewed' | 'withdrawn';
  expectedRevision: number;
  expectedTriageRevision: number;
  note?: string | null;
  traceId?: string | null;
  collectionId?: string | null;
  investigationQuery?: string | null;
}

export interface FeedbackTriageResponse {
  feedbackId: string;
  issueType: string;
  triageStatus: string;
  triageRevision: number;
  voteRevision: number;
  gapId?: string | null;
  eventId?: string | null;
}

export interface FeedbackTriageListItem {
  eventId: string;
  feedbackId: string;
  issueType: string;
  triageStatus: string;
  triageRevision: number;
  voteRevision: number;
  vote: 'up' | 'down';
  gapId?: string | null;
  createdAt: string;
}

export interface EvalCandidatePromotionRequest {
  feedbackId: string;
  redactedPrompt: string;
  redactedExpected: string;
  sourceId: string;
  traceId: string;
  collectionId: string;
  reReview?: boolean;
}

export interface EvalCandidatePromotionResponse {
  candidateId: string;
  redactedPrompt: string;
  redactedExpected: string;
  sourceFeedbackId?: string | null;
  sourceIndexRevision: number;
  promotionState: 'current' | 'stale';
  reviewState: 'reviewed' | 're_reviewed';
  createdAt: string;
}

// ---- Reviewed course maintenance (phase B) ---------------------------------

export interface AdminCourse {
  courseId: string;
  code: string;
  subject: string;
  title: string;
  canonicalCode?: string | null;
  parseStatus: string;
  lifecycle: 'current' | 'retired';
  authority: 'atlas' | 'catalog' | 'admin_correction' | 'admin_created';
  adminRevision: number;
  requisitesRaw?: string | null;
  oxford?: boolean;
  mentionCount?: number;
  unresolvedCount?: number;
  programCount?: number;
  filename?: string | null;
  contentType?: string | null;
  originKey?: string | null;
  indexRevision?: number | null;
  sourceStatus?: string | null;
  sourceIsCurrent?: boolean | null;
  publisher?: string | null;
  sourceUrl?: string | null;
  sourceTitle?: string | null;
  institution?: string | null;
  issuingUnit?: string | null;
  documentRoles?: string[];
  applicability?: Record<string, string[]>;
  applicabilityStatus?: string | null;
}

export interface AdminCourseListResponse {
  courses: AdminCourse[];
  total: number;
  subjects: string[];
}

export interface AdminCourseMention {
  kind: string;
  targetCode: string;
  resolved: boolean;
  position?: number | null;
}

export interface AdminCourseNode {
  nodeId: string;
  kind: string;
  parentNodeId?: string | null;
  position: number;
  nodeType: string;
  targetCode?: string | null;
  conditionText?: string | null;
}

export interface AdminCourseDependent {
  courseId: string;
  code: string;
  title: string;
  kind: string;
  targetCode: string;
  resolved: boolean;
}

export interface AdminCourseProgramReference {
  programId: string;
  title: string;
  kind: string;
}

export interface AdminCourseChange {
  changeId: string;
  code: string;
  changeKind: 'create' | 'update' | 'retire' | 'restore' | 'baseline_conflict';
  status: 'draft' | 'published' | 'rejected' | 'superseded' | 'conflicted';
  expectedRevision: number;
  publishedRevision?: number | null;
  baseAuthority?: string | null;
  baseValue?: Record<string, any>;
  proposedValue?: Record<string, any>;
  reason: string;
  actorUserId?: string | null;
  publishedByUserId?: string | null;
  conflictNote?: string | null;
  createdAt: string;
  updatedAt: string;
  publishedAt?: string | null;
}

export interface AdminCourseDetail {
  course: AdminCourse;
  mentions: AdminCourseMention[];
  nodes: AdminCourseNode[];
  programs: AdminCourseProgramReference[];
  dependents: AdminCourseDependent[];
  changes: AdminCourseChange[];
  currentCodes: string[];
}

export interface AdminCourseParsedMention {
  kind: string;
  targetCode: string;
  resolved: boolean;
}

export interface AdminCourseParsedRequisites {
  status: string;
  parseStatus: string;
  mentions: AdminCourseParsedMention[];
  unresolved: string[];
  leftover: string[];
}

export interface AdminCoursePreviewResponse {
  change: AdminCourseChange;
  current?: Record<string, any> | null;
  changedFields: string[];
  parsed?: AdminCourseParsedRequisites | null;
  programs: AdminCourseProgramReference[];
  dependents: AdminCourseDependent[];
  activeTopicBuilds: number;
  warnings: string[];
}

export interface AdminCoursePublishResponse {
  change: AdminCourseChange;
}

export interface AdminCourseConflict {
  changeId: string;
  code: string;
  changeKind: string;
  status: string;
  conflictNote?: string | null;
  createdAt: string;
}

export interface AdminCourseChangeCreate {
  code: string;
  changeKind?: 'create' | 'update' | 'retire' | 'restore' | null;
  title?: string | null;
  requisitesRaw?: string | null;
  sourceId?: string | null;
  reason: string;
  expectedRevision?: number;
}
