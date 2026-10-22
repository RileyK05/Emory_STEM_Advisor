// API contract types mirroring docs/architecture.md.
// These are the contract the HttpClient (frontend/src/api/http.ts) and the
// backend FastAPI schemas (backend/app/api/schemas.py) both satisfy; the mock
// client satisfies them too.

/** plan = evidence a validated query plan chose (opt-in query planning). */
export type SeamName = 'keyword' | 'embed' | 'prereq' | 'plan';

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
}

export interface Citation {
  sourceId: string;
  sourceName: string;
  locatorLabel: string;
  chunkId: string;
}

// --- Answer artifacts (generation layer; see docs/architecture.md) ---
// The model emits a validated structured spec per kind; the frontend renders
// each kind with a fixed component. Kinds are a trust ladder: the model's
// output is always data, never executable code.

export type ArtifactKind = 'markdown' | 'table' | 'chart' | 'diagram' | 'html';

/** Grounding carried by content-bearing artifact elements. Elements without
 *  backing chunks are rejected at the contract check, same as text claims. */
export interface ArtifactElementRef {
  chunkIds: string[];
  conceptId?: string;
  evidenceLevel: 'direct' | 'derived' | 'hypothesis';
}

export interface TableSpec {
  columns: { key: string; label: string }[];
  rows: Record<string, string | number>[];
}

/** Declarative chart spec (Vega-Lite or equivalent): data + mark + encoding. No code. */
export interface ChartSpec {
  mark: string;
  data: Record<string, string | number>[];
  encoding: Record<string, unknown>;
}

export interface DiagramSpec {
  nodes: ({ id: string; label: string } & ArtifactElementRef)[];
  edges: ({ from: string; to: string; relation: string } & ArtifactElementRef)[];
}

export interface Artifact {
  artifactId: string;
  kind: ArtifactKind;
  title: string;
  /** Per-kind validated spec. Rendered by one fixed component per kind. */
  spec: string | TableSpec | ChartSpec | DiagramSpec;
  provenance: { traceId: string; modelId: string; promptVersion: string };
}

export interface QueryRequest {
  query: string;
  collectionId?: string;
  /** Omitted/null searches the collection; [] means explicitly search no sources. */
  sourceIds?: string[] | null;
  studentCohort?: string | null;
}

export interface Recommendation {
  clusterId: string;
  path: string[];
  courses: {
    courseId: string;
    code: string;
    title: string;
    lifecycle: 'current' | 'retired';
  }[];
}

export interface RetiredCourse {
  courseId: string;
  code: string;
  title: string;
  lifecycle: 'retired';
}

export interface TopicGraphNode {
  id: string;
  kind: 'root' | 'department' | 'topic' | 'course';
  label: string;
  departmentKey: string | null;
  courseId?: string | null;
  code?: string | null;
  title?: string | null;
}

export interface TopicGraphEdge {
  parentId: string;
  childId: string;
  kind: 'contains';
}

export interface SubgraphView {
  nodes: TopicGraphNode[];
  edges: TopicGraphEdge[];
}

// Native authoring artifacts are immutable revisions; this contract is separate
// from the grounded answer-artifact union above.
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
  original?: { filename: string; sha256: string; preserved: boolean } | null;
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
export interface ArtifactRevisionRequest {
  expectedParentRevisionId: string;
  operations?: unknown[];
  spec?: NativeArtifactSpec;
  draftId?: string;
}
export interface ArtifactRun {
  runId: string;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  artifactIds?: string[];
  artifactRevisions?: { artifactId: string; revisionId: string }[];
  error?: string | null;
}
export interface ArtifactRunLimits {
  maxCalls: number;
  totalTokenBudget: number;
  perCallOutputCap: number;
}
export interface ArtifactRunRequest {
  kind: NativeArtifactKind;
  inputs: ArtifactGenerationInput;
  idempotencyKey: string;
}
export interface DocumentOutputSection {
  key: string;
  title: string;
  target_words: number;
  target_blocks: number;
  min_words: number;
  max_words: number;
  min_blocks: number;
  max_blocks: number;
}
export interface DocumentOutputPlan {
  kind: 'document';
  title: string;
  sections: DocumentOutputSection[];
  max_total_blocks: number;
  max_output_tokens_per_unit: number;
  max_repairs_per_unit: number;
  max_checkpoint_bytes: number;
}
export interface WorkbookOutputColumn {
  key: string;
  label: string;
  value_type: 'text' | 'integer' | 'number' | 'date' | 'formula';
}
export interface WorkbookOutputSheet {
  key: string;
  name: string;
  columns: WorkbookOutputColumn[];
  min_rows: number;
  target_rows: number;
  max_rows: number;
}
export interface WorkbookOutputPlan {
  kind: 'workbook';
  title: string;
  sheets: WorkbookOutputSheet[];
  max_total_cells: number;
  max_output_tokens_per_unit: number;
  max_repairs_per_unit: number;
  max_checkpoint_bytes: number;
}
export interface ArtifactGenerationInput {
  prompt: string;
  sourceIds: string[] | null;
  collectionId?: string;
  mode: 'grounded' | 'authoring';
  artifactId?: string;
  expectedRevisionId?: string;
  outputPlan?: DocumentOutputPlan | WorkbookOutputPlan;
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

export type ArtifactBatchState =
  | 'prepared'
  | 'submitting'
  | 'pending'
  | 'validating'
  | 'in_progress'
  | 'finalizing'
  | 'completed'
  | 'failed'
  | 'expired'
  | 'cancel_requested'
  | 'cancelling'
  | 'cancelled'
  | 'unknown';
export interface ArtifactBatchItemInput {
  itemId: string;
  inputs: Omit<ArtifactGenerationInput, 'outputPlan'>;
}
export interface ArtifactBatchRequest {
  batchId: string;
  kind: NativeArtifactKind;
  items: ArtifactBatchItemInput[];
}
export interface ArtifactBatchItem {
  itemId: string;
  state: string;
  publicationState: string;
  artifactId: string | null;
  revisionId: string | null;
  errorCode: string | null;
}
export interface ArtifactBatchSummary {
  batchId: string;
  state: ArtifactBatchState;
  cancelRequested: boolean;
  itemCount: number;
  totalCount: number | null;
  completedCount: number | null;
  failedCount: number | null;
  createdAt: string;
  updatedAt: string;
}
export interface ArtifactBatchRecord extends Omit<ArtifactBatchSummary, 'createdAt' | 'updatedAt'> {
  items: ArtifactBatchItem[];
  createdAt?: string;
  updatedAt?: string;
}

export type ConnectionRole = 'standard' | 'complex' | 'fast_generation' | 'decision';
export type ConnectionCapability = 'structured_output' | 'tools' | 'streaming' | 'vision' | 'batch' | 'decision';
export type ConnectionDataScope = 'institutional_corpus' | 'user_supplied_data' | 'private_workspace';
export type ConnectionProvider =
  | 'openai'
  | 'anthropic'
  | 'google'
  | 'openai-compatible'
  | 'hf-local'
  | 'openai-decisions'
  | 'cloudflare-clef';
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
  role?: 'student' | 'advisor' | 'admin' | 'auditor' | null;
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

export interface ResearchDecisionInfo {
  runId: string;
  outcome: 'recommendation' | 'information' | 'clarification' | 'insufficient_evidence';
  completeness: 'complete' | 'partial';
  blocks: ResearchBlock[];
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

export type QueryResponse =
  | {
      type: 'answer';
      answer: string;
      citations: Citation[];
      artifacts?: Artifact[];
      trace: RetrievalTrace;
      claims?: ParagraphClaimReviewUnit[];
      research?: ResearchDecisionInfo | null;
      applicability?: ApplicabilityNotice | null;
    }
  | { type: 'refusal'; reason: string; trace?: RetrievalTrace };

export interface ParagraphClaimReviewUnit {
  text: string;
  citationChunkIds: string[];
  reviewStatus: 'unreviewed';
}

export interface ArtifactRevisionReference {
  artifactId: string;
  revisionId: string;
}

export interface ExportSummaryRequest {
  title?: string | null;
  throughSequence?: number | null;
  artifactReferences?: ArtifactRevisionReference[];
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
}
export type ConversationTurnEvidence = ConversationUserTurnEvidence | ConversationAssistantTurnEvidence;

export interface ConversationTurn {
  sequence: number;
  requestId: string;
  role: 'user' | 'assistant';
  text: string;
  status: 'input' | 'answer' | 'refusal' | 'history' | 'failed' | 'abandoned';
  evidence: ConversationTurnEvidence;
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
  createdAt: string;
  updatedAt: string;
}
export interface ConversationRecord extends ConversationSummary {
  turns: ConversationTurn[];
}

export interface ConversationSummaryReference {
  sequence: number;
  role: 'user' | 'assistant';
  status: ConversationTurn['status'];
  effective?: EffectiveConversationInputs | null;
  citations?: Citation[];
  contextBlock?: ContextItem[];
}
export interface ConversationSummaryCheckpoint {
  summaryId: string;
  conversationId: string;
  state: 'pending' | 'completed' | 'refused' | 'failed' | 'stale';
  expectedSequence: number;
  candidateCoveredThrough: number;
  coveredThrough: number | null;
  fingerprint: string;
  text: string | null;
  referenceMetadata: ConversationSummaryReference[];
  failureCode: string | null;
  profileId: string | null;
  profileRevision: number | null;
  contextEpoch?: number;
  createdAt: string;
  updatedAt: string;
}

export interface ConversationMemoryInspection {
  conversationId: string;
  contextEpoch: number;
  contextBoundarySequence: number;
  contextResetAt?: string | null;
  checkpoints: ConversationSummaryCheckpoint[];
  researchNotebook?: Record<string, unknown> | null;
}

export interface ConversationResetResponse {
  conversationId: string;
  contextEpoch: number;
  contextBoundarySequence: number;
  contextResetAt: string;
  message?: string;
}

export type NativeDecisionState = 'pending' | 'completed' | 'refused' | 'invalid' | 'failed' | 'unknown' | 'abandoned';
export interface NativeDecisionRequest {
  decisionId: string;
  task: string;
  predicate: string;
  input: string | Record<string, unknown>;
}
export interface NativeDecision {
  decisionId: string;
  state: NativeDecisionState;
  prediction: boolean | null;
  probability: number | null;
  confidence: number | null;
  refused: boolean;
  usage: {
    source: 'provider' | 'estimated' | 'unknown';
    promptTokens: number | null;
    completionTokens: number | null;
  };
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
  mode?: 'advising' | 'history';
  artifactReferences?: ArtifactRevisionReference[];
  studentCohort?: string | null;
}

export interface ConversationHistoryResponse {
  type: 'history';
  answer: string;
  turnReferences: number[];
  conversationId: string;
  sequence: number;
}
export interface ConversationTerminalResponse {
  type: 'failed' | 'abandoned';
  reason: string;
  statusCode?: number;
  conversationId: string;
  sequence: number;
}
export type ConversationTurnResponse =
  | (QueryResponse & { conversationId: string; sequence: number })
  | ConversationHistoryResponse
  | ConversationTerminalResponse;

export interface ConversationStreamStartResponse {
  conversationId: string;
  requestId: string;
  jobId: string | null;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'unknown';
}
export type ConversationStreamEvent =
  | { eventId: number; type: 'provisional_delta'; text: string }
  | { eventId: number; type: 'provisional_reset' }
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

export interface AdvisorClient {
  getEffectiveFlags(): Promise<EffectiveFlagsResponse>;
  listCollections(): Promise<CollectionDoc[]>;
  query(req: QueryRequest): Promise<QueryResponse>;
  listConversations(): Promise<ConversationSummary[]>;
  createConversation(request: CreateConversationRequest): Promise<ConversationSummary>;
  getConversation(id: string, after?: number, limit?: number): Promise<ConversationRecord>;
  sendConversationTurn(id: string, request: ConversationTurnRequest): Promise<ConversationTurnResponse>;
  startConversationTurnStream(id: string, request: ConversationTurnRequest): Promise<ConversationStreamStartResponse>;
  getConversationTurnEvents(
    id: string,
    requestId: string,
    afterEventId?: number,
    limit?: number,
  ): Promise<ConversationStreamEventsResponse>;
  abandonConversationTurn(id: string, requestId: string): Promise<ConversationTerminalResponse>;
  listConversationSummaries(id: string): Promise<ConversationSummaryCheckpoint[]>;
  inspectConversationMemory(id: string): Promise<ConversationMemoryInspection>;
  resetConversationMemory(id: string): Promise<ConversationResetResponse>;
  exportSummary(id: string, request?: ExportSummaryRequest): Promise<NativeArtifactRecord>;
  submitDecision(request: NativeDecisionRequest): Promise<NativeDecision>;
  getDecision(id: string): Promise<NativeDecision>;
  abandonDecision(id: string): Promise<NativeDecision>;
  listGaps(collectionId?: string): Promise<GapListResponse>;
  resolveGap(id: string, request: ResolveGapRequest): Promise<RetrievalGap>;
  listSources(collection?: string): Promise<SourceDoc[]>;
  reindexSource(id: string, expectedIndexRevision: number): Promise<SourceDoc>;
  /** Ingest a file into the backend. The mock rejects; the HttpClient posts. */
  uploadSource(file: File): Promise<SourceDoc>;
  recommendations(query: string): Promise<Recommendation[]>;
  retiredCourses(query: string): Promise<RetiredCourse[]>;
  createArtifact(spec: NativeArtifactSpec): Promise<NativeArtifactRecord>;
  importArtifact(file: File): Promise<NativeArtifactRecord>;
  getArtifact(id: string, revisionId?: string): Promise<NativeArtifactRecord>;
  saveArtifactRevision(id: string, request: ArtifactRevisionRequest): Promise<NativeArtifactRecord>;
  previewArtifact(id: string, revisionId?: string): Promise<ArtifactPreview>;
  downloadArtifact(
    id: string,
    revisionId: string,
    format: 'md' | 'docx' | 'xlsx' | 'html',
  ): Promise<{ blob: Blob; filename: string }>;
  downloadOriginalArtifact(id: string): Promise<{ blob: Blob; filename: string }>;
  createArtifactRun(request: ArtifactRunRequest): Promise<ArtifactRun>;
  getArtifactRunLimits(): Promise<ArtifactRunLimits>;
  getArtifactRun(id: string): Promise<ArtifactRun>;
  listArtifactRunEvents(id: string, afterEventId?: number): Promise<ArtifactRunEvent[]>;
  cancelArtifactRun(id: string): Promise<ArtifactRun>;
  listArtifactBatches(): Promise<ArtifactBatchSummary[]>;
  createArtifactBatch(request: ArtifactBatchRequest): Promise<ArtifactBatchRecord>;
  getArtifactBatch(id: string): Promise<ArtifactBatchRecord>;
  pollArtifactBatch(id: string): Promise<ArtifactBatchRecord>;
  cancelArtifactBatch(id: string): Promise<ArtifactBatchRecord>;
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
  getAcademicRecord(): Promise<AcademicRecordDetailResponse>;
  upsertAcademicAttempt(body: AcademicRecordAttemptUpsertRequest): Promise<AcademicRecordAttemptItem>;
  previewAcademicImport(body: AcademicRecordImportPreviewRequest): Promise<AcademicRecordImportBatchResponse>;
  updateAcademicImport(
    batchId: string,
    body: AcademicRecordImportUpdateRequest,
  ): Promise<AcademicRecordImportBatchResponse>;
  commitAcademicImport(
    batchId: string,
    body: AcademicRecordImportCommitRequest,
  ): Promise<AcademicRecordImportCommitResponse>;
  getAcademicRecordRevision(revision: number): Promise<AcademicRecordRevisionResponse>;
  listAuditablePrograms(): Promise<AuditableProgramItem[]>;
  listProgramAudits(): Promise<DegreeAuditRecordItem[]>;
  runProgramAudit(body: DegreeAuditRunRequest): Promise<DegreeAuditDetailResponse>;
  getProgramAudit(auditId: string): Promise<DegreeAuditDetailResponse>;
  exportProgramAudit(auditId: string): Promise<DegreeAuditDetailResponse>;
  listProgramComparisons(): Promise<ProgramComparisonSummary[]>;
  createProgramComparison(body: ProgramComparisonCreateRequest): Promise<ProgramComparisonResponse>;
  getProgramComparison(comparisonId: string): Promise<ProgramComparisonResponse>;
  submitResponseFeedback(body: ResponseFeedbackRequest): Promise<ResponseFeedbackResponse>;
  listPrivacyExports(): Promise<PrivacyExportResponse[]>;
  createPrivacyExport(): Promise<PrivacyExportResponse>;
  downloadPrivacyExport(exportId: string, token: string): Promise<PrivacyExportDownloadResponse>;
}

export interface AdminOverview {
  users: number;
  admins: number;
  disabledUsers: number;
  activeSessions: number;
  conversations: number;
  turns: number;
  summaries: number;
  queuedJobs: number;
  runningJobs: number;
  failedJobs: number;
  sources: number;
  artifacts: number;
  traces: number;
}

export type UserRole = 'student' | 'advisor' | 'admin' | 'auditor';
export type GrantType = 'advisor_access' | 'support_access';
export type GrantResourceType = 'conversation' | 'plan' | 'all_records';

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

export interface ResourceGrantCreate {
  grantType: GrantType;
  actorUserId: string;
  targetWorkspaceKey: string;
  resourceType: GrantResourceType;
  resourceId?: string | null;
  reason: string;
  expiresAt: string;
  allowedActions?: string[];
}

export interface ResourceGrant {
  grantId: string;
  grantType: GrantType;
  actorUserId: string;
  targetWorkspaceKey: string;
  resourceType: GrantResourceType;
  resourceId?: string | null;
  allowedActions: string[];
  reason: string;
  approvedByUserId: string;
  expiresAt: string;
  createdAt: string;
  revokedAt?: string | null;
  revokedByUserId?: string | null;
  actorEmail?: string | null;
  actorDisplayName?: string | null;
  approvedByEmail?: string | null;
  approvedByDisplayName?: string | null;
}

export interface SupportGrantRequestCreate {
  targetWorkspaceKey: string;
  resourceType: GrantResourceType;
  resourceId?: string | null;
  reason: string;
  expiresAt: string;
}

export interface SupportGrantRequestUpdate extends SupportGrantRequestCreate {
  expectedRevision: number;
}

export interface SupportGrantRequestDecision {
  expectedRevision: number;
  note?: string | null;
}

export interface SupportGrantRequest {
  requestId: string;
  requesterUserId: string;
  actorUserId: string;
  targetWorkspaceKey: string;
  resourceType: GrantResourceType;
  resourceId?: string | null;
  allowedActions: string[];
  reason: string;
  expiresAt: string;
  status: 'pending' | 'approved' | 'rejected';
  revision: number;
  decisionNote?: string | null;
  decidedByUserId?: string | null;
  decidedAt?: string | null;
  grantId?: string | null;
  createdAt: string;
  updatedAt: string;
  actorEmail?: string | null;
  actorDisplayName?: string | null;
}

export interface GrantedResultStaging {
  stagingId: string;
  grantId: string;
  status: 'staged' | 'published' | 'withheld';
  resourceId: string;
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

export interface AdminConversation {
  conversationId: string;
  workspaceKey: string;
  title: string;
  lastSequence: number;
  pendingRequestId?: string | null;
  turnCount: number;
  summaryCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface AdminConversationTurn {
  sequence: number;
  requestId: string;
  role: 'user' | 'assistant';
  text: string;
  status: string;
  evidence: Record<string, unknown>;
  createdAt: string;
}

export interface AdminTranscript {
  conversation: AdminConversation;
  turns: AdminConversationTurn[];
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

export interface DebugSetting {
  name: string;
  label: string;
  group: string;
  type: 'int' | 'float' | 'bool' | 'str';
  value: unknown;
  description: string;
  minimum?: number | null;
  maximum?: number | null;
  choices?: string[] | null;
}

export interface DebugSettingsResponse {
  settings: DebugSetting[];
  retrieval: DebugSetting[];
  systemPrompt: string;
  defaultSystemPrompt: string;
}

export interface DebugSession {
  debugSessionId: string;
  expiresAt: string;
  overrides: {
    settings?: Record<string, unknown>;
    retrieval?: Record<string, unknown>;
  };
  systemPrompt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DebugSessionCreated extends DebugSession {
  token: string;
}

export interface AdminUserSession {
  createdAt: string;
  expiresAt: string;
  revokedAt?: string | null;
}

export interface AdminArtifact {
  artifactId: string;
  workspaceKey: string;
  kind: string;
  headRevisionId: string;
  originalFilename?: string | null;
  revisionCount: number;
  createdAt: string;
}

export interface AdminCollection {
  collectionId: string;
  name: string;
  sourceCount: number;
  createdAt: string;
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

export interface ModelPriceScheduleCreate {
  billingMode?: 'standard' | 'batch';
  provider: string;
  modelId: string;
  currency?: string;
  inputRatePerMillion: number;
  cachedReadRatePerMillion?: number;
  outputRatePerMillion: number;
  effectiveFrom: string;
  effectiveTo?: string | null;
  provenance: string;
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

export interface FeatureFlagSummary {
  flagName: string;
  description: string;
  envCeilingKey?: string | null;
  defaultEnabled: boolean;
  killSwitchActive: boolean;
  killReason?: string | null;
  overridesCount: number;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface FeatureFlagOverride {
  overrideId: string;
  flagName: string;
  scopeType: 'global' | 'cohort' | 'workspace';
  scopeId: string;
  enabled: boolean;
  reason?: string | null;
  actorUserId?: string | null;
  updatedAt?: string | null;
}

export interface FeatureFlagOverrideRequest {
  expectedUpdatedAt?: string | null;
  requireAbsent?: boolean;
  scopeType: 'global' | 'cohort' | 'workspace';
  scopeId: string;
  enabled: boolean;
  reason?: string | null;
}

export interface FeatureFlagKillSwitchRequest {
  expectedUpdatedAt?: string | null;
  killSwitchActive: boolean;
  reason?: string | null;
}

export interface CohortSummary {
  cohortName: string;
  memberCount: number;
}

export interface CohortAssignmentRequest {
  userId: string;
}

export interface CohortMember {
  userId: string;
  displayName?: string | null;
  email?: string | null;
  role: 'student' | 'advisor' | 'admin' | 'auditor';
  assignedAt: string;
}

export interface CohortAssignmentResponse {
  status: 'assigned';
  cohortName: string;
  userId: string;
}

export interface EffectiveFlagsResponse {
  flags: Record<string, boolean>;
}

export interface EffectiveQuotaResponse {
  remainingQueriesMinute: number;
  remainingQueriesDay: number;
  minuteQueryLimit: number;
  dailyQueryLimit: number;
  activeJobsLimit: number;
  resetDaySeconds: number;
}

export interface QuotaPolicySummary {
  policyId: string;
  scopeType: 'global' | 'cohort' | 'workspace' | 'user';
  scopeId: string;
  queriesPerMinute?: number | null;
  queriesPerDay?: number | null;
  tokensPerDay?: number | null;
  spendUsdPerDay?: number | null;
  maxActiveJobs?: number | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface QuotaPolicyUpsertRequest {
  scopeType: 'global' | 'cohort' | 'workspace' | 'user';
  scopeId: string;
  queriesPerMinute?: number | null;
  queriesPerDay?: number | null;
  tokensPerDay?: number | null;
  spendUsdPerDay?: number | null;
  maxActiveJobs?: number | null;
}

export interface AlertStatusResponse {
  alertName: string;
  status: 'firing' | 'resolved' | 'unknown';
  severity: 'critical' | 'warning' | 'info';
  currentValue: number | null;
  threshold: number;
  owner: string;
  runbook: string;
  description: string;
  evaluatedAt: string;
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

export interface SourceImpactPreviewResponse {
  sourceId: string;
  filename: string;
  programCount: number;
  courseCount: number;
  gapCount: number;
  researchRunCount: number;
  programs?: Array<Record<string, any>>;
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

export interface SourceReviewProposalReject {
  reason: string;
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

export interface ProgramRuleDraftCreate {
  rulesTree?: Record<string, any> | null;
  notes?: string;
}

export interface ProgramRuleReviewAction {
  notes?: string;
}

export interface ProgramRuleRejectAction {
  reason: string;
}

export interface ProgramRuleDiffResponse {
  programId: string;
  releaseIdA: string;
  versionA: number;
  releaseIdB: string;
  versionB: number;
  addedNodeIds?: string[];
  removedNodeIds?: string[];
  modifiedNodes?: Array<Record<string, any>>;
  digestMatch: boolean;
}

export interface AcademicRecordSummary {
  recordId: string;
  workspaceKey: string;
  recordRevision: number;
  createdAt: string;
  updatedAt: string;
}

export interface AcademicRecordAttemptItem {
  attemptId: string;
  recordId: string;
  workspaceKey: string;
  institution: string;
  submittedCourseCode: string;
  resolvedCourseCode?: string | null;
  courseTitle?: string | null;
  term: string;
  attemptedCredits: number;
  earnedCredits: number;
  grade?: string | null;
  status: string;
  provenance: string;
  isVerified: boolean;
  verificationMethod?: string | null;
  verifiedByUserId?: string | null;
  verifiedAt?: string | null;
  verificationNotes?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AcademicRecordVerificationEventItem {
  eventId: string;
  recordId: string;
  attemptId?: string | null;
  eventType: string;
  actorUserId?: string | null;
  actorRole: string;
  details?: Record<string, any>;
  createdAt: string;
}

export interface AcademicRecordDetailResponse {
  record: AcademicRecordSummary;
  attempts?: AcademicRecordAttemptItem[];
  recentEvents?: AcademicRecordVerificationEventItem[];
}

export interface AcademicRecordAttemptUpsertRequest {
  attemptId?: string | null;
  institution?: string;
  submittedCourseCode: string;
  resolvedCourseCode?: string | null;
  courseTitle?: string | null;
  term: string;
  attemptedCredits?: number;
  earnedCredits?: number | null;
  grade?: string | null;
  status?: string | null;
  provenance?: string;
  workspaceKey?: string | null;
}

export interface AcademicRecordVerifyAttemptRequest {
  method?: 'manual_staff' | 'official_transcript' | 'sis_feed';
  notes?: string | null;
  workspaceKey?: string | null;
}

export interface AcademicRecordImportPreviewRequest {
  filename: string;
  rawContent: string;
  expectedRecordRevision: number;
  workspaceKey?: string | null;
}

export interface AcademicRecordImportBatchResponse {
  batchId: string;
  recordId: string;
  workspaceKey: string;
  expectedRecordRevision: number;
  status: string;
  filename: string;
  parsedRows?: Array<Record<string, any>>;
  rowErrors?: Array<Record<string, any>>;
  duplicateCandidates?: Array<Record<string, any>>;
  previewDigest?: string | null;
  rowDecisions?: Array<'select' | 'reject'>;
  createdAt: string;
  committedAt?: string | null;
}

export interface AcademicRecordImportCommitRequest {
  expectedRecordRevision: number;
  previewDigest: string;
  workspaceKey?: string | null;
}

export interface AcademicRecordImportUpdateRequest {
  expectedRecordRevision: number;
  decisions?: Array<'select' | 'reject'>;
  corrections?: Array<{ rowIndex: number; grade?: string; submittedCourseCode?: string; term?: string }>;
  workspaceKey?: string | null;
}

export interface AcademicRecordRevisionResponse {
  recordId: string;
  revision: number;
  attempts?: Array<Record<string, any>>;
}

export interface AcademicRecordImportCommitResponse {
  batchId: string;
  status: string;
  attemptsImported: number;
  newRecordRevision: number;
}

export interface DegreeAuditRuleEvaluationItem {
  evaluationId: string;
  auditId: string;
  nodeId: string;
  parentNodeId?: string | null;
  nodeType: string;
  title: string;
  status: string;
  allocatedAttemptIds?: string[];
  allocatedCredits: number;
  requiredCount?: number | null;
  satisfiedCount?: number | null;
  unresolvedReason?: string | null;
  rawPosition?: number | null;
  createdAt: string;
}

export interface DegreeAuditRecordItem {
  auditId: string;
  workspaceKey: string;
  programId: string;
  ruleReleaseId: string;
  recordId: string;
  recordRevision: number;
  rulesDigest: string;
  overallStatus: string;
  isStale: boolean;
  completedCredits: number;
  inProgressCredits: number;
  unverifiedCredits: number;
  resultsSummary?: Record<string, any>;
  blockers?: string[];
  assumptions?: string[];
  programTitle?: string | null;
  programKind?: string | null;
  createdAt: string;
}

export interface DegreeAuditDetailResponse {
  audit: DegreeAuditRecordItem;
  evaluations?: DegreeAuditRuleEvaluationItem[];
  programTitle: string;
  programKind: string;
  coverageScope: 'program_audit';
}

export interface AuditableProgramItem {
  programId: string;
  title: string;
  kind: string;
  ruleReleaseId: string;
}

export interface DegreeAuditRunRequest {
  programId: string;
  workspaceKey?: string | null;
}

export interface ProgramComparisonMember {
  position: number;
  programId: string;
  programTitle: string;
  ruleReleaseId: string;
  auditId: string;
  savedStatus: string;
  savedCompletedCredits: number;
  overlayStatus: string;
  unresolvedCount: number;
}

export interface ProgramComparisonResponse {
  comparisonId: string;
  workspaceKey: string;
  recordId: string;
  recordRevision: number;
  sharingPolicy: 'unknown' | 'evidenced';
  mentionKind: string;
  sharedMentions?: string[];
  creditSavings?: number | null;
  coverageComparable: boolean;
  rankingBlockedReason?: string | null;
  members?: ProgramComparisonMember[];
  createdAt: string;
}

export interface ProgramComparisonSummary {
  comparisonId: string;
  recordRevision: number;
  sharingPolicy: 'unknown' | 'evidenced';
  mentionKind: string;
  sharedMentions?: string[];
  creditSavings?: number | null;
  coverageComparable: boolean;
  rankingBlockedReason?: string | null;
  createdAt: string;
}

export interface ProgramComparisonCreateRequest {
  programIds: string[];
  addCourseCodes?: string[];
  removeCourseCodes?: string[];
  workspaceKey?: string | null;
}

export interface CatalogEditionItem {
  editionId: string;
  revision: number;
  editionKey: string;
  isCurrent: boolean;
  topicBuildId?: string | null;
  sourceIds?: string[];
}

export interface CatalogRefreshBatchResponse {
  batchId: string;
  collectionId: string;
  editionKey: string;
  status: 'staging' | 'cancelled' | 'published';
  baseRevision: number;
  stagedRevision: number;
  compatible: boolean;
  currentEdition: CatalogEditionItem;
  stagedEdition: CatalogEditionItem;
}

export interface CatalogRefreshStartRequest {
  collectionId: string;
  editionKey: string;
  compatible?: boolean;
}

export interface CatalogRefreshPublishRequest {
  expectedRevision: number;
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

export interface EvalCandidateCreateRequest {
  redactedPrompt: string;
  redactedExpected: string;
  sourceFeedbackId?: string | null;
}

export interface EvalCandidateResponse {
  candidateId: string;
  redactedPrompt: string;
  redactedExpected: string;
  sourceFeedbackId?: string | null;
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

export interface DebugExperimentCreateRequest {
  conversationId: string;
  requestId: string;
  snapshot: Record<string, unknown>;
}

export interface DebugExperimentResponse {
  experimentId: string;
  sourceConversationId: string;
  sourceRequestId: string;
  state: 'recorded';
  createdAt: string;
}

export interface PromptVersionCreateRequest {
  name: string;
  templateText: string;
  versionId?: string | null;
}

export interface PromptVersionResponse {
  versionId: string;
  name: string;
  templateText: string;
  contentDigest: string;
  reviewState: 'draft' | 'approved';
  createdAt: string;
}

export interface PromptFixtureCreateRequest {
  suiteName: string;
  fixtureText: string;
}

export interface PromptFixtureResponse {
  fixtureId: string;
  suiteName: string;
  fixtureDigest: string;
  present: boolean;
}

export interface PromptPromotionRequest {
  suiteName: string;
}

export interface PromptPromotionResponse {
  promotionId: string;
  versionId: string;
  suiteName: string;
  fixtureDigest: string;
  status: 'published';
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
  staleAuditCount: number;
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

export interface AdminCourseChangeReject {
  reason: string;
}
