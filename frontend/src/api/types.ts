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
}

export interface Recommendation {
  clusterId: string;
  path: string[];
  courses: { courseId: string; code: string; title: string }[];
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

export type QueryResponse =
  | {
      type: 'answer';
      answer: string;
      citations: Citation[];
      artifacts?: Artifact[];
      trace: RetrievalTrace;
      claims?: ParagraphClaimReviewUnit[];
      research?: ResearchDecisionInfo | null;
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

export interface EffectiveConversationInputs {
  collectionId: string | null;
  sourceIds: string[] | null;
  retrievalQuery: string;
  artifactReferences: (ArtifactRevisionReference & { spec: NativeArtifactSpec })[];
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
  createdAt: string;
  updatedAt: string;
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
}
