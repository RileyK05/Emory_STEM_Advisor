import type {
  AdvisorClient,
  Contribution,
  Citation,
  ContextItem,
  ModelCallInfo,
  QueryRequest,
  QueryResponse,
  RetrievalTrace,
  SeamStatus,
  SourceDoc,
  CollectionDoc,
  Recommendation,
  RetiredCourse,
  AuthSession,
  NativeArtifactSummary,
  NativeDecisionRequest,
  ResolveGapRequest,
} from './types';
import { chunk, MOCK_SOURCES } from './corpus';

const SYSTEM_PROMPT =
  'You are the Emory STEM Advisor. Answer only from the context block assembled ' +
  "from the program's internal documents. Every claim must be grounded in a cited " +
  'chunk. If the context contains no relevant material, refuse rather than guess.';

let traceCounter = 0;

function normalize(q: string): string {
  return q
    .toLowerCase()
    .replace(/[^\w\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function contextItem(chunkId: string): ContextItem {
  const c = chunk(chunkId);
  return {
    chunkId: c.chunkId,
    sourceId: c.sourceId,
    sourceName: c.sourceName,
    locatorLabel: c.locatorLabel,
    text: c.text,
  };
}

function contribution(chunkId: string, seams: Contribution['seams'], score: number, slot: number): Contribution {
  return { chunkId, seams, normalizedScore: score, sourceSlot: slot };
}

function citation(chunkId: string): Citation {
  const c = chunk(chunkId);
  return { sourceId: c.sourceId, sourceName: c.sourceName, locatorLabel: c.locatorLabel, chunkId: c.chunkId };
}

interface TraceSeed {
  query: string;
  seams: SeamStatus[];
  contributions: Contribution[];
  contextChunkIds: string[];
  answer: string;
  latencyMs: number;
  warnings?: string[];
  errors?: string[];
}

function buildTrace(seed: TraceSeed): RetrievalTrace {
  const context = seed.contextChunkIds.map(contextItem);
  const userMessage =
    `Context (${context.length} chunks):\n` +
    context.map((c, i) => `[${i + 1}] ${c.sourceName} · ${c.locatorLabel}\n${c.text}`).join('\n\n') +
    `\n\nQuestion: ${seed.query}\nAnswer with inline citations to the chunk numbers.`;

  const modelCall: ModelCallInfo = {
    provider: 'huggingface-local',
    modelId: 'openbmb/MiniCPM-2B-128k',
    promptTokens: context.reduce((n, c) => n + c.text.split(/\s+/).length, 0) + 40,
    completionTokens: seed.answer.split(/\s+/).length,
    latencyMs: seed.latencyMs,
  };

  return {
    traceId: `trace-${String(++traceCounter).padStart(4, '0')}`,
    query: seed.query,
    normalizedQuery: normalize(seed.query),
    seams: seed.seams,
    contributions: seed.contributions,
    contextBlock: context,
    prompt: { system: SYSTEM_PROMPT, user: userMessage },
    modelCall,
    warnings: seed.warnings ?? [],
    errors: seed.errors ?? [],
  };
}

function latency(min = 500, max = 1200): number {
  return Math.round(min + Math.random() * (max - min));
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// The prerequisite graph is parsed deterministically from the course atlas, so
// it is authoritative (never review-gated). It is dormant when the query names
// no course code.
const PREREQ_DORMANT: SeamStatus = {
  name: 'prereq',
  enabled: true,
  state: 'dormant',
  reason: 'no course codes in query',
  candidateCount: 0,
};

export class MockClient implements AdvisorClient {
  async listCollections(): Promise<CollectionDoc[]> {
    throw new Error('Collections are unavailable in mock mode.');
  }
  private artifactUnavailable(): never {
    throw new Error(
      'Native artifact storage and export require the backend API. Switch VITE_API_MODE=http after the artifact routes are available.',
    );
  }
  private conversationUnavailable(): never {
    throw new Error('Saved conversations require the HTTP backend; the mock client does not persist transcripts.');
  }
  async query(req: QueryRequest): Promise<QueryResponse> {
    const wait = latency();
    const q = normalize(req.query);

    let response: QueryResponse;
    if (req.sourceIds?.length === 0) {
      response = noSelectedSourceResponse(req.query, wait);
    } else if (q.includes('quantum')) {
      response = refusalResponse(req.query, wait);
    } else if (q.includes('gradient') || q.includes('semantic') || q.includes('embed')) {
      response = embeddingResponse(req.query, wait);
    } else if (q.includes('prereq') || q.includes('depend')) {
      response = prereqResponse(req.query, wait);
    } else if (q.includes('vector') || q.includes('basis') || q.includes('eigen')) {
      response = groundedResponse(req.query, wait);
    } else {
      response = defaultResponse(req.query, wait);
    }

    // The fixture answers contain passages from known mock sources. When the
    // caller scopes sources, refuse if any assembled passage falls outside it.
    if (req.sourceIds?.length && response.type === 'answer') {
      const allowedNames = new Set(
        MOCK_SOURCES.filter((source) => req.sourceIds?.includes(source.id)).map((source) => source.name),
      );
      const traceContainsUnselectedSource = response.trace.contextBlock.some(
        (item) => !allowedNames.has(item.sourceName),
      );
      const citationFromUnselectedSource = response.citations.some((item) => !allowedNames.has(item.sourceName));
      if (traceContainsUnselectedSource || citationFromUnselectedSource) {
        response = noSelectedSourceResponse(req.query, wait);
      }
    }

    await sleep(wait);
    return response;
  }

  async recommendations(_query: string): Promise<Recommendation[]> {
    return [];
  }

  async retiredCourses(_query: string): Promise<RetiredCourse[]> {
    return [];
  }

  async listSources(): Promise<SourceDoc[]> {
    return MOCK_SOURCES;
  }

  async reindexSource(): Promise<SourceDoc> {
    throw new Error('Source reindexing requires the HTTP backend; the mock client does not change stored indexes.');
  }

  async listConversations() {
    return this.conversationUnavailable();
  }
  async createConversation() {
    return this.conversationUnavailable();
  }
  async getConversation() {
    return this.conversationUnavailable();
  }
  async sendConversationTurn() {
    return this.conversationUnavailable();
  }
  async startConversationTurnStream() {
    return this.conversationUnavailable();
  }
  async getConversationTurnEvents() {
    return this.conversationUnavailable();
  }
  async abandonConversationTurn() {
    return this.conversationUnavailable();
  }
  async listConversationSummaries() {
    return this.conversationUnavailable();
  }
  async inspectConversationMemory() {
    return this.conversationUnavailable();
  }
  async resetConversationMemory() {
    return this.conversationUnavailable();
  }
  async exportSummary() {
    return this.conversationUnavailable();
  }
  private decisionUnavailable(): never {
    throw new Error(
      'Native decisions require an evaluated backend connection; the mock client does not submit or persist decisions.',
    );
  }
  async submitDecision(_request: NativeDecisionRequest) {
    return this.decisionUnavailable();
  }
  async getDecision() {
    return this.decisionUnavailable();
  }
  async abandonDecision() {
    return this.decisionUnavailable();
  }
  private gapUnavailable(): never {
    throw new Error(
      'Human retrieval-gap review requires the private HTTP backend; the mock client does not store gap observations.',
    );
  }
  async listGaps() {
    return this.gapUnavailable();
  }
  async resolveGap(_id: string, _request: ResolveGapRequest) {
    return this.gapUnavailable();
  }

  async uploadSource(_file: File): Promise<SourceDoc> {
    await sleep(300);
    throw new Error('Upload requires the backend ingest pipeline, which is not connected yet.');
  }

  async createArtifact() {
    return this.artifactUnavailable();
  }
  async importArtifact() {
    return this.artifactUnavailable();
  }
  async getArtifact() {
    return this.artifactUnavailable();
  }
  async saveArtifactRevision() {
    return this.artifactUnavailable();
  }
  async previewArtifact() {
    return this.artifactUnavailable();
  }
  async downloadArtifact() {
    return this.artifactUnavailable();
  }
  async downloadOriginalArtifact() {
    return this.artifactUnavailable();
  }
  async createArtifactRun() {
    return this.artifactUnavailable();
  }
  async getArtifactRunLimits() {
    return this.artifactUnavailable();
  }
  async getArtifactRun() {
    return this.artifactUnavailable();
  }
  async listArtifactRunEvents() {
    return this.artifactUnavailable();
  }
  async cancelArtifactRun() {
    return this.artifactUnavailable();
  }
  async listArtifactBatches() {
    return this.artifactUnavailable();
  }
  async createArtifactBatch() {
    return this.artifactUnavailable();
  }
  async getArtifactBatch() {
    return this.artifactUnavailable();
  }
  async pollArtifactBatch() {
    return this.artifactUnavailable();
  }
  async cancelArtifactBatch() {
    return this.artifactUnavailable();
  }
  private localAdminUnavailable(): never {
    const error = new Error('Local connection settings are not enabled by this mock backend.') as Error & {
      status: number;
    };
    error.status = 404;
    throw error;
  }
  async listConnectionProfiles() {
    return this.localAdminUnavailable();
  }
  async createConnectionProfile() {
    return this.localAdminUnavailable();
  }
  async updateConnectionProfile() {
    return this.localAdminUnavailable();
  }
  async replaceConnectionSecret() {
    return this.localAdminUnavailable();
  }
  async clearConnectionSecret() {
    return this.localAdminUnavailable();
  }
  async deleteConnectionProfile() {
    return this.localAdminUnavailable();
  }
  async diagnoseConnection() {
    return this.localAdminUnavailable();
  }
  async getSession(): Promise<AuthSession> {
    return { mode: 'development', authenticated: true, workspaceKey: 'mock-local', displayName: 'Local development' };
  }
  async getEffectiveFlags() {
    return { flags: { memory_inspector: false, advising_summary_export: false } };
  }
  async logout(): Promise<void> {
    /* The mock has no authenticated identity or cookie. */
  }
  async listArtifacts(): Promise<NativeArtifactSummary[]> {
    return [];
  }
  async getAcademicRecord() {
    return this.recordUnavailable();
  }
  async upsertAcademicAttempt() {
    return this.recordUnavailable();
  }
  async previewAcademicImport() {
    return this.recordUnavailable();
  }
  async updateAcademicImport() {
    return this.recordUnavailable();
  }
  async commitAcademicImport() {
    return this.recordUnavailable();
  }
  async getAcademicRecordRevision() {
    return this.recordUnavailable();
  }
  async listAuditablePrograms() {
    return this.auditUnavailable();
  }
  async listProgramAudits() {
    return this.auditUnavailable();
  }
  async runProgramAudit() {
    return this.auditUnavailable();
  }
  async getProgramAudit() {
    return this.auditUnavailable();
  }
  async exportProgramAudit() {
    return this.auditUnavailable();
  }
  async listProgramComparisons() {
    return this.auditUnavailable();
  }
  async createProgramComparison() {
    return this.auditUnavailable();
  }
  async getProgramComparison() {
    return this.auditUnavailable();
  }
  async submitResponseFeedback() {
    return this.auditUnavailable();
  }
  async listPrivacyExports() {
    return this.privacyUnavailable();
  }
  async createPrivacyExport() {
    return this.privacyUnavailable();
  }
  async downloadPrivacyExport() {
    return this.privacyUnavailable();
  }
  private privacyUnavailable(): never {
    throw new Error('Privacy export requires the HTTP backend.');
  }
  private recordUnavailable(): never {
    throw new Error('Academic records require the HTTP backend.');
  }
  private auditUnavailable(): never {
    throw new Error('Program audits require the HTTP backend.');
  }
  beginLogin(): void {
    /* The mock deliberately stays in local development mode. */
  }
}

function noSelectedSourceResponse(query: string, wait: number): QueryResponse {
  const trace = buildTrace({
    query,
    seams: [
      { name: 'keyword', enabled: true, state: 'dormant', reason: 'no sources selected', candidateCount: 0 },
      { name: 'embed', enabled: true, state: 'dormant', reason: 'no sources selected', candidateCount: 0 },
      { name: 'prereq', enabled: true, state: 'dormant', reason: 'no sources selected', candidateCount: 0 },
    ],
    contributions: [],
    contextChunkIds: [],
    answer: '',
    latencyMs: Math.round(wait / 2),
    warnings: ['No sources were selected; retrieval and generation were skipped.'],
  });
  return {
    type: 'refusal',
    reason: 'No sources were selected for this question. Select one or more indexed sources and try again.',
    trace,
  };
}

function groundedResponse(query: string, wait: number): QueryResponse {
  const answer =
    'A **basis** is a set of vectors that is both *linearly independent* and *spanning*: ' +
    'no vector in the set is a linear combination of the others, and every vector in the ' +
    'space can be written as a combination of the basis vectors.\n\n' +
    'An inner product gives you a notion of angle and length, which is what makes orthogonality ' +
    'and the Gram–Schmidt procedure possible.';
  const trace = buildTrace({
    query,
    seams: [
      { name: 'keyword', enabled: true, state: 'active', candidateCount: 6 },
      PREREQ_DORMANT,
      { name: 'embed', enabled: true, state: 'active', candidateCount: 5 },
    ],
    contributions: [
      contribution('chk-la-03', ['keyword', 'embed'], 1.0, 1),
      contribution('chk-la-02', ['keyword', 'embed'], 0.82, 2),
      contribution('chk-la-05', ['embed'], 0.61, 3),
      contribution('chk-ob-03', ['keyword'], 0.44, 4),
    ],
    contextChunkIds: ['chk-la-03', 'chk-la-02', 'chk-la-05'],
    answer,
    latencyMs: wait,
  });
  return {
    type: 'answer',
    answer,
    citations: [citation('chk-la-03'), citation('chk-la-02'), citation('chk-la-05')],
    trace,
  };
}

function embeddingResponse(query: string, wait: number): QueryResponse {
  const answer =
    "The semantic angle here is eigenstructure: an eigenvector's direction is unchanged by the " +
    'linear map, and spectral decomposition factors a diagonalizable matrix into PDP⁻¹.\n\n' +
    'Note that several of these chunks were surfaced by embedding similarity rather than keyword ' +
    'match, so the phrasing in the source may not mirror your question.';
  const trace = buildTrace({
    query,
    seams: [
      { name: 'keyword', enabled: true, state: 'active', candidateCount: 1 },
      PREREQ_DORMANT,
      {
        name: 'embed',
        enabled: true,
        state: 'active',
        candidateCount: 12,
      },
    ],
    contributions: [
      contribution('chk-la-07', ['embed', 'keyword'], 0.94, 1),
      contribution('chk-la-05', ['embed'], 0.88, 2),
      contribution('chk-la-03', ['embed'], 0.71, 3),
      contribution('chk-la-02', ['embed'], 0.65, 4),
      contribution('chk-ob-04', ['embed'], 0.52, 5),
      contribution('chk-safe-02', ['embed'], 0.41, 6),
    ],
    contextChunkIds: ['chk-la-07', 'chk-la-05', 'chk-la-03', 'chk-la-02', 'chk-ob-04', 'chk-safe-02'],
    answer,
    latencyMs: wait,
    warnings: [
      'embedding_only_quota applied: 6 embedding-only candidates survived, quota is 4',
      'keyword seam matched 1 chunk; embedding expansion bounded by quota',
    ],
  });
  return {
    type: 'answer',
    answer,
    citations: [citation('chk-la-07'), citation('chk-la-05'), citation('chk-ob-04'), citation('chk-safe-02')],
    trace,
  };
}

function prereqResponse(query: string, wait: number): QueryResponse {
  const answer =
    'Before the material on inner products you will want the vector-space fundamentals: closure ' +
    'under addition and scalar multiplication, the span/independence distinction, and what a basis is. ' +
    'Spectral decomposition builds directly on eigenvectors, so treat that as downstream of this topic.';
  const trace = buildTrace({
    query,
    seams: [
      { name: 'keyword', enabled: true, state: 'active', candidateCount: 3 },
      {
        name: 'prereq',
        enabled: true,
        state: 'active',
        candidateCount: 2,
      },
      { name: 'embed', enabled: true, state: 'active', candidateCount: 3 },
    ],
    contributions: [
      contribution('chk-la-02', ['keyword', 'prereq'], 0.91, 1),
      contribution('chk-la-03', ['prereq'], 0.73, 2),
      contribution('chk-la-05', ['prereq', 'embed'], 0.58, 3),
      contribution('chk-la-07', ['embed'], 0.46, 4),
    ],
    contextChunkIds: ['chk-la-02', 'chk-la-03', 'chk-la-05', 'chk-la-07'],
    answer,
    latencyMs: wait,
    warnings: [
      'prereq seam: matched 1 course; upstream expansion returned 2 chunks',
      'prereq decay = 0.8 applied: prerequisite chunks cannot outrank the course itself',
    ],
  });
  return {
    type: 'answer',
    answer,
    citations: [citation('chk-la-02'), citation('chk-la-03'), citation('chk-la-05'), citation('chk-la-07')],
    trace,
  };
}

function defaultResponse(query: string, wait: number): QueryResponse {
  const answer =
    'Based on the program documents: you are assigned a faculty advisor in the first week, and the ' +
    'matching form is due Friday of week two. Lab access requires completing the safety orientation ' +
    'and passing the checkout quiz at 90% or higher.';
  const trace = buildTrace({
    query,
    seams: [
      { name: 'keyword', enabled: true, state: 'active', candidateCount: 2 },
      PREREQ_DORMANT,
      { name: 'embed', enabled: true, state: 'active', candidateCount: 3 },
    ],
    contributions: [
      contribution('chk-ob-03', ['keyword', 'embed'], 0.87, 1),
      contribution('chk-ob-04', ['keyword'], 0.66, 2),
      contribution('chk-safe-01', ['embed'], 0.49, 3),
    ],
    contextChunkIds: ['chk-ob-03', 'chk-ob-04', 'chk-safe-01'],
    answer,
    latencyMs: wait,
  });
  return {
    type: 'answer',
    answer,
    citations: [citation('chk-ob-03'), citation('chk-ob-04'), citation('chk-safe-01')],
    trace,
  };
}

function refusalResponse(query: string, wait: number): QueryResponse {
  const trace = buildTrace({
    query,
    seams: [
      { name: 'keyword', enabled: true, state: 'active', candidateCount: 0 },
      PREREQ_DORMANT,
      { name: 'embed', enabled: true, state: 'active', candidateCount: 0 },
    ],
    contributions: [],
    contextChunkIds: [],
    answer: '',
    latencyMs: Math.round(wait / 2),
    warnings: [
      'fusion: zero grounded candidates after union; refusal rule applied (invariant 4)',
      'model was not called; generative stage skipped for zero-candidate queries',
    ],
  });
  return {
    type: 'refusal',
    reason: 'No relevant material found in the uploaded docs for this question.',
    trace,
  };
}
