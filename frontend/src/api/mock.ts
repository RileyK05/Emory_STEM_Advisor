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
  Recommendation,
  RetiredCourse,
  AuthSession,
  NativeArtifactSummary,
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

function seam(
  name: SeamStatus['name'],
  state: SeamStatus['state'],
  candidateCount: number,
  reason?: string,
): SeamStatus {
  return { name, enabled: true, state, ...(reason ? { reason } : {}), candidateCount };
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
    timings: { keyword_ms: 5, embed_ms: 8, prereq_ms: 1, fusion_ms: 1, total_ms: 15 },
  };
}

/** A canned answer: the context block and citations are both drawn from the mock corpus. */
function answerResponse(
  query: string,
  wait: number,
  seed: Omit<TraceSeed, 'query' | 'latencyMs'> & { citationChunkIds: string[] },
): QueryResponse {
  return {
    type: 'answer',
    answer: seed.answer,
    citations: seed.citationChunkIds.map(citation),
    trace: buildTrace({ ...seed, query, latencyMs: wait }),
  };
}

/** A canned refusal: no context was assembled and the model was not called. */
function refusal(query: string, wait: number, reason: string, seams: SeamStatus[], warnings: string[]): QueryResponse {
  return {
    type: 'refusal',
    reason,
    trace: buildTrace({
      query,
      seams,
      contributions: [],
      contextChunkIds: [],
      answer: '',
      latencyMs: Math.round(wait / 2),
      warnings,
    }),
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
const PREREQ_DORMANT = seam('prereq', 'dormant', 0, 'no course codes in query');

/** A client method the mock cannot honor without the HTTP backend. */
function unavailable(message: string, status?: number): () => Promise<never> {
  return async () => {
    throw Object.assign(new Error(message), status === undefined ? {} : { status });
  };
}

const ARTIFACTS =
  'Native artifact storage and export require the backend API. Switch VITE_API_MODE=http after the artifact routes are available.';
const CONVERSATIONS = 'Saved conversations require the HTTP backend; the mock client does not persist transcripts.';
const GAPS =
  'Human retrieval-gap review requires the private HTTP backend; the mock client does not store gap observations.';
const CONNECTIONS = 'Local connection settings are not enabled by this mock backend.';
const RECORDS = 'Academic records require the HTTP backend.';
const AUDITS = 'Program audits require the HTTP backend.';
const PRIVACY = 'Privacy export requires the HTTP backend.';

export class MockClient implements AdvisorClient {
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

  async uploadSource(_file: File): Promise<SourceDoc> {
    await sleep(300);
    throw new Error('Upload requires the backend ingest pipeline, which is not connected yet.');
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
  beginLogin(): void {
    /* The mock deliberately stays in local development mode. */
  }
  async listArtifacts(): Promise<NativeArtifactSummary[]> {
    return [];
  }

  listCollections = unavailable('Collections are unavailable in mock mode.');
  reindexSource = unavailable(
    'Source reindexing requires the HTTP backend; the mock client does not change stored indexes.',
  );

  listConversations = unavailable(CONVERSATIONS);
  createConversation = unavailable(CONVERSATIONS);
  archiveConversation = unavailable(CONVERSATIONS);
  restoreConversation = unavailable(CONVERSATIONS);
  getConversation = unavailable(CONVERSATIONS);
  startConversationTurnStream = unavailable(CONVERSATIONS);
  getConversationTurnEvents = unavailable(CONVERSATIONS);
  abandonConversationTurn = unavailable(CONVERSATIONS);
  inspectConversationMemory = unavailable(CONVERSATIONS);
  resetConversationMemory = unavailable(CONVERSATIONS);
  exportSummary = unavailable(CONVERSATIONS);

  listGaps = unavailable(GAPS);
  resolveGap = unavailable(GAPS);

  createArtifact = unavailable(ARTIFACTS);
  importArtifact = unavailable(ARTIFACTS);
  getArtifact = unavailable(ARTIFACTS);
  saveArtifactRevision = unavailable(ARTIFACTS);
  previewArtifact = unavailable(ARTIFACTS);
  downloadArtifact = unavailable(ARTIFACTS);
  downloadOriginalArtifact = unavailable(ARTIFACTS);
  createArtifactRun = unavailable(ARTIFACTS);
  getArtifactRunLimits = unavailable(ARTIFACTS);
  getArtifactRun = unavailable(ARTIFACTS);
  listArtifactRunEvents = unavailable(ARTIFACTS);
  cancelArtifactRun = unavailable(ARTIFACTS);
  listArtifactBatches = unavailable(ARTIFACTS);
  createArtifactBatch = unavailable(ARTIFACTS);
  getArtifactBatch = unavailable(ARTIFACTS);
  pollArtifactBatch = unavailable(ARTIFACTS);
  cancelArtifactBatch = unavailable(ARTIFACTS);

  listConnectionProfiles = unavailable(CONNECTIONS, 404);
  createConnectionProfile = unavailable(CONNECTIONS, 404);
  updateConnectionProfile = unavailable(CONNECTIONS, 404);
  replaceConnectionSecret = unavailable(CONNECTIONS, 404);
  clearConnectionSecret = unavailable(CONNECTIONS, 404);
  deleteConnectionProfile = unavailable(CONNECTIONS, 404);
  diagnoseConnection = unavailable(CONNECTIONS, 404);

  getAcademicRecord = unavailable(RECORDS);
  upsertAcademicAttempt = unavailable(RECORDS);
  previewAcademicImport = unavailable(RECORDS);
  updateAcademicImport = unavailable(RECORDS);
  commitAcademicImport = unavailable(RECORDS);
  getAcademicRecordRevision = unavailable(RECORDS);

  listAuditablePrograms = unavailable(AUDITS);
  listProgramAudits = unavailable(AUDITS);
  runProgramAudit = unavailable(AUDITS);
  getProgramAudit = unavailable(AUDITS);
  exportProgramAudit = unavailable(AUDITS);
  listProgramComparisons = unavailable(AUDITS);
  createProgramComparison = unavailable(AUDITS);
  submitResponseFeedback = unavailable(AUDITS);

  listPrivacyExports = unavailable(PRIVACY);
  createPrivacyExport = unavailable(PRIVACY);
  downloadPrivacyExport = unavailable(PRIVACY);
}

function noSelectedSourceResponse(query: string, wait: number): QueryResponse {
  return refusal(
    query,
    wait,
    'No sources were selected for this question. Select one or more indexed sources and try again.',
    [
      seam('keyword', 'dormant', 0, 'no sources selected'),
      seam('embed', 'dormant', 0, 'no sources selected'),
      seam('prereq', 'dormant', 0, 'no sources selected'),
    ],
    ['No sources were selected; retrieval and generation were skipped.'],
  );
}

function refusalResponse(query: string, wait: number): QueryResponse {
  return refusal(
    query,
    wait,
    'No relevant material found in the uploaded docs for this question.',
    [seam('keyword', 'active', 0), PREREQ_DORMANT, seam('embed', 'active', 0)],
    [
      'fusion: zero grounded candidates after union; refusal rule applied (invariant 4)',
      'model was not called; generative stage skipped for zero-candidate queries',
    ],
  );
}

function groundedResponse(query: string, wait: number): QueryResponse {
  return answerResponse(query, wait, {
    answer:
      'A **basis** is a set of vectors that is both *linearly independent* and *spanning*: ' +
      'no vector in the set is a linear combination of the others, and every vector in the ' +
      'space can be written as a combination of the basis vectors.\n\n' +
      'An inner product gives you a notion of angle and length, which is what makes orthogonality ' +
      'and the Gram–Schmidt procedure possible.',
    seams: [seam('keyword', 'active', 6), PREREQ_DORMANT, seam('embed', 'active', 5)],
    contributions: [
      contribution('chk-la-03', ['keyword', 'embed'], 1.0, 1),
      contribution('chk-la-02', ['keyword', 'embed'], 0.82, 2),
      contribution('chk-la-05', ['embed'], 0.61, 3),
      contribution('chk-ob-03', ['keyword'], 0.44, 4),
    ],
    contextChunkIds: ['chk-la-03', 'chk-la-02', 'chk-la-05'],
    citationChunkIds: ['chk-la-03', 'chk-la-02', 'chk-la-05'],
  });
}

function embeddingResponse(query: string, wait: number): QueryResponse {
  return answerResponse(query, wait, {
    answer:
      "The semantic angle here is eigenstructure: an eigenvector's direction is unchanged by the " +
      'linear map, and spectral decomposition factors a diagonalizable matrix into PDP⁻¹.\n\n' +
      'Note that several of these chunks were surfaced by embedding similarity rather than keyword ' +
      'match, so the phrasing in the source may not mirror your question.',
    seams: [seam('keyword', 'active', 1), PREREQ_DORMANT, seam('embed', 'active', 12)],
    contributions: [
      contribution('chk-la-07', ['embed', 'keyword'], 0.94, 1),
      contribution('chk-la-05', ['embed'], 0.88, 2),
      contribution('chk-la-03', ['embed'], 0.71, 3),
      contribution('chk-la-02', ['embed'], 0.65, 4),
      contribution('chk-ob-04', ['embed'], 0.52, 5),
      contribution('chk-safe-02', ['embed'], 0.41, 6),
    ],
    contextChunkIds: ['chk-la-07', 'chk-la-05', 'chk-la-03', 'chk-la-02', 'chk-ob-04', 'chk-safe-02'],
    citationChunkIds: ['chk-la-07', 'chk-la-05', 'chk-ob-04', 'chk-safe-02'],
    warnings: [
      'embedding_only_quota applied: 6 embedding-only candidates survived, quota is 4',
      'keyword seam matched 1 chunk; embedding expansion bounded by quota',
    ],
  });
}

function prereqResponse(query: string, wait: number): QueryResponse {
  return answerResponse(query, wait, {
    answer:
      'Before the material on inner products you will want the vector-space fundamentals: closure ' +
      'under addition and scalar multiplication, the span/independence distinction, and what a basis is. ' +
      'Spectral decomposition builds directly on eigenvectors, so treat that as downstream of this topic.',
    seams: [seam('keyword', 'active', 3), seam('prereq', 'active', 2), seam('embed', 'active', 3)],
    contributions: [
      contribution('chk-la-02', ['keyword', 'prereq'], 0.91, 1),
      contribution('chk-la-03', ['prereq'], 0.73, 2),
      contribution('chk-la-05', ['prereq', 'embed'], 0.58, 3),
      contribution('chk-la-07', ['embed'], 0.46, 4),
    ],
    contextChunkIds: ['chk-la-02', 'chk-la-03', 'chk-la-05', 'chk-la-07'],
    citationChunkIds: ['chk-la-02', 'chk-la-03', 'chk-la-05', 'chk-la-07'],
    warnings: [
      'prereq seam: matched 1 course; upstream expansion returned 2 chunks',
      'prereq decay = 0.8 applied: prerequisite chunks cannot outrank the course itself',
    ],
  });
}

function defaultResponse(query: string, wait: number): QueryResponse {
  return answerResponse(query, wait, {
    answer:
      'Based on the program documents: you are assigned a faculty advisor in the first week, and the ' +
      'matching form is due Friday of week two. Lab access requires completing the safety orientation ' +
      'and passing the checkout quiz at 90% or higher.',
    seams: [seam('keyword', 'active', 2), PREREQ_DORMANT, seam('embed', 'active', 3)],
    contributions: [
      contribution('chk-ob-03', ['keyword', 'embed'], 0.87, 1),
      contribution('chk-ob-04', ['keyword'], 0.66, 2),
      contribution('chk-safe-01', ['embed'], 0.49, 3),
    ],
    contextChunkIds: ['chk-ob-03', 'chk-ob-04', 'chk-safe-01'],
    citationChunkIds: ['chk-ob-03', 'chk-ob-04', 'chk-safe-01'],
  });
}
