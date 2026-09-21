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
} from './types';
import { chunk, MOCK_SOURCES } from './corpus';

const SYSTEM_PROMPT =
  'You are the Emory STEM Advisor. Answer only from the context block assembled ' +
  'from the program\'s internal documents. Every claim must be grounded in a cited ' +
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
  return { chunkId: c.chunkId, sourceName: c.sourceName, locatorLabel: c.locatorLabel, text: c.text };
}

function contribution(chunkId: string, seams: Contribution['seams'], score: number, slot: number): Contribution {
  return { chunkId, seams, normalizedScore: score, sourceSlot: slot };
}

function citation(chunkId: string): Citation {
  const c = chunk(chunkId);
  return { sourceName: c.sourceName, locatorLabel: c.locatorLabel, chunkId: c.chunkId };
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

const GRAPH_DORMANT: SeamStatus = {
  name: 'graph',
  enabled: false,
  state: 'dormant',
  reason: 'seams.graph.enabled = false; extracted edges await manual review',
  candidateCount: 0,
};

export class MockClient implements AdvisorClient {
  async query(req: QueryRequest): Promise<QueryResponse> {
    const wait = latency();
    const q = normalize(req.query);

    let response: QueryResponse;
    if (q.includes('quantum')) {
      response = refusalResponse(req.query, wait);
    } else if (q.includes('gradient') || q.includes('semantic') || q.includes('embed')) {
      response = embeddingResponse(req.query, wait);
    } else if (q.includes('prereq') || q.includes('depend')) {
      response = graphResponse(req.query, wait);
    } else if (q.includes('vector') || q.includes('basis') || q.includes('eigen')) {
      response = groundedResponse(req.query, wait);
    } else {
      response = defaultResponse(req.query, wait);
    }

    await sleep(wait);
    return response;
  }

  async listSources(): Promise<SourceDoc[]> {
    return MOCK_SOURCES;
  }

  async uploadSource(_file: File): Promise<SourceDoc> {
    await sleep(300);
    throw new Error('Upload requires the backend ingest pipeline, which is not connected yet.');
  }
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
      { name: 'toc', enabled: true, state: 'active', candidateCount: 4 },
      GRAPH_DORMANT,
      { name: 'embed', enabled: false, state: 'disabled', reason: 'provider not configured', candidateCount: 0 },
    ],
    contributions: [
      contribution('chk-la-03', ['keyword', 'toc'], 1.0, 1),
      contribution('chk-la-02', ['keyword', 'toc'], 0.82, 2),
      contribution('chk-la-05', ['toc'], 0.61, 3),
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
    'The semantic angle here is eigenstructure: an eigenvector\'s direction is unchanged by the ' +
    'linear map, and spectral decomposition factors a diagonalizable matrix into PDP⁻¹.\n\n' +
    'Note that several of these chunks were surfaced by embedding similarity rather than keyword ' +
    'or TOC match, so the phrasing in the source may not mirror your question.';
  const trace = buildTrace({
    query,
    seams: [
      { name: 'keyword', enabled: true, state: 'active', candidateCount: 1 },
      { name: 'toc', enabled: true, state: 'active', candidateCount: 0 },
      GRAPH_DORMANT,
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
      'embedding_only_quota applied: 6 embedding-only candidates survived, quota is 8',
      'toc seam: 0 entries above toc_match_min (0.3); seam contributed nothing this query',
    ],
  });
  return {
    type: 'answer',
    answer,
    citations: [
      citation('chk-la-07'),
      citation('chk-la-05'),
      citation('chk-ob-04'),
      citation('chk-safe-02'),
    ],
    trace,
  };
}

function graphResponse(query: string, wait: number): QueryResponse {
  const answer =
    'Before the material on inner products you will want the vector-space fundamentals: closure ' +
    'under addition and scalar multiplication, the span/independence distinction, and what a basis is. ' +
    'Spectral decomposition builds directly on eigenvectors, so treat that as downstream of this topic.';
  const trace = buildTrace({
    query,
    seams: [
      { name: 'keyword', enabled: true, state: 'active', candidateCount: 3 },
      { name: 'toc', enabled: true, state: 'active', candidateCount: 2 },
      {
        name: 'graph',
        enabled: true,
        state: 'active',
        candidateCount: 2,
      },
      { name: 'embed', enabled: false, state: 'disabled', reason: 'provider not configured', candidateCount: 0 },
    ],
    contributions: [
      contribution('chk-la-02', ['keyword', 'toc'], 0.91, 1),
      contribution('chk-la-03', ['toc'], 0.73, 2),
      contribution('chk-la-05', ['graph'], 0.58, 3),
      contribution('chk-la-07', ['graph'], 0.46, 4),
    ],
    contextChunkIds: ['chk-la-02', 'chk-la-03', 'chk-la-05', 'chk-la-07'],
    answer,
    latencyMs: wait,
    warnings: [
      'graph seam: 2 edges skipped, evidence_level = hypothesis (inert until review)',
      'graph_decay = 0.8 applied: expansion candidates cannot outrank direct matches',
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
      { name: 'toc', enabled: true, state: 'active', candidateCount: 1 },
      GRAPH_DORMANT,
      { name: 'embed', enabled: false, state: 'disabled', reason: 'provider not configured', candidateCount: 0 },
    ],
    contributions: [
      contribution('chk-ob-03', ['keyword', 'toc'], 0.87, 1),
      contribution('chk-ob-04', ['keyword'], 0.66, 2),
      contribution('chk-safe-01', ['toc'], 0.49, 3),
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
      { name: 'toc', enabled: true, state: 'active', candidateCount: 0 },
      GRAPH_DORMANT,
      { name: 'embed', enabled: false, state: 'disabled', reason: 'provider not configured', candidateCount: 0 },
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
