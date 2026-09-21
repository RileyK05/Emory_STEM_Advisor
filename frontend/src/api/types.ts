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
  nodes: { id: string; label: string } & ArtifactElementRef[];
  edges: { from: string; to: string; relation: string } & ArtifactElementRef[];
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
}

export type QueryResponse =
  | {
      type: 'answer';
      answer: string;
      citations: Citation[];
      artifacts?: Artifact[];
      trace: RetrievalTrace;
    }
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
