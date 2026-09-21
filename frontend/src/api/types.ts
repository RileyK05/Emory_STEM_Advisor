// API contract types mirroring docs/architecture.md.
// The backend does not exist yet; these types are the contract the future
// HTTP client must satisfy, and the mock client already satisfies.

export type SeamName = 'keyword' | 'toc' | 'graph' | 'embed';

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
  promptTokens: number;
  completionTokens: number;
  latencyMs: number;
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
  sourceName: string;
  locatorLabel: string;
  chunkId: string;
}

export interface QueryRequest {
  query: string;
  collectionId?: string;
}

export type QueryResponse =
  | { type: 'answer'; answer: string; citations: Citation[]; trace: RetrievalTrace }
  | { type: 'refusal'; reason: string; trace?: RetrievalTrace };

export interface SourceDoc {
  id: string;
  name: string;
  locatorType: string;
  status: 'uploaded' | 'parsed' | 'chunked' | 'indexed' | 'failed';
  chunkCount: number;
}

export interface AdvisorClient {
  query(req: QueryRequest): Promise<QueryResponse>;
  listSources(): Promise<SourceDoc[]>;
  /** Placeholder until the backend exists; the mock rejects with a
   *  "backend not connected" error and the UI surfaces that state. */
  uploadSource(file: File): Promise<SourceDoc>;
}
