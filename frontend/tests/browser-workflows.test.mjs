import test from 'node:test';
import assert from 'node:assert/strict';
import { access, readFile, unlink, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
let url;
const chromePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ??
  path.join(process.env.ProgramFiles ?? 'C:/Program Files', 'Google/Chrome/Application/chrome.exe');
let server;
let browser;
const tempFiles = [];

const sources = [
  { id: 'source-1', name: 'Linear Algebra Primer', locatorType: 'section', status: 'indexed', chunkCount: 3, indexRevision: 2, canReindex: true },
  { id: 'source-2', name: 'Lab Safety Manual', locatorType: 'page', status: 'indexed', chunkCount: 2, indexRevision: 5, canReindex: false },
];

const workbookRecord = {
  artifactId: 'artifact-wb', revisionId: 'wb-r1', parentRevisionId: null, kind: 'workbook', title: 'Metrics',
  original: { filename: 'metrics.xlsx', sha256: 'abc123', preserved: true }, status: 'ready', message: null,
  spec: { kind: 'workbook', title: 'Metrics', sheets: [{ id: 'sheet-main', name: 'Data', cells: [
    { id: 'cell-label', row: 1, column: 1, value: 'Visits', formula: null, style: { bold: true } },
    { id: 'cell-value', row: 1, column: 2, value: 10, formula: null, style: {} },
    { id: 'cell-date', row: 2, column: 2, value: '2026-01-01', value_type: 'date', formula: null, style: { number_format: 'yyyy-mm-dd' } },
  ] }, { id: 'sheet-summary', name: 'Summary', cells: [
    { id: 'cell-value', row: 1, column: 2, value: 20, formula: null, style: { bold: true } },
  ] }], charts: [] },
};
const documentRecord = {
  artifactId: 'artifact-doc', revisionId: 'doc-r1', parentRevisionId: null, kind: 'document', title: 'Notes', status: 'ready',
  spec: { kind: 'document', title: 'Notes', blocks: [
    { id: 'code-42', type: 'code', language: 'text', text: 'first line\n\nthird line' },
    { id: 'table-77', type: 'table', headers: ['Symbol', 'Notes'], rows: [['A|B', 'top line\nbottom line']] },
    { id: 'paragraph-9', type: 'paragraph', text: 'Unchanged passage.' },
  ], page_layout: { margin_top: 1 }, evidence: {
    'code-42': [{ chunk_id: 'chunk-code', source_id: 'source-1', source_name: 'Linear Algebra Primer', locator_label: 'page 4', text_snapshot: 'first line' }],
    'paragraph-9': [{ chunk_id: 'chunk-para', source_id: 'source-1', source_name: 'Linear Algebra Primer', locator_label: 'page 5', text_snapshot: 'Unchanged passage.' }],
  }, provenance: { authority: 'grounded' } },
};
const profileFixture = {
  profile_id: 'profile-1', name: 'Local OpenAI', provider: 'openai', model_id: 'gpt-test', base_url: null,
  account_id: null, secret_configured: true, provider_options: {}, roles: ['standard'], capabilities: ['structured_output'],
  data_scopes: ['institutional_corpus'], enabled: true, priority: 10, revision: 1,
  created_at: '2026-10-08T00:00:00Z', updated_at: '2026-10-08T00:00:00Z', eval_passed: false,
};

function trace() {
  return {
    traceId: 'trace-browser', query: 'basis', normalizedQuery: 'basis', seams: [], contributions: [],
    contextBlock: [{ chunkId: 'chunk-la-1', sourceName: 'Linear Algebra Primer', locatorLabel: 'section 2.1', text: 'A basis spans the vector space and is linearly independent.' }],
    prompt: { system: 'grounded', user: 'basis' },
    modelCall: { provider: 'test', modelId: 'test-model', promptTokens: null, completionTokens: null, latencyMs: 1 }, warnings: [], errors: [],
  };
}

test.before(async () => {
  try { await access(chromePath); } catch { throw new Error(`Chrome not found at ${chromePath}; set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to use another local browser.`); }
  const dist = path.join(frontend, 'dist');
  server = createServer(async (request, response) => {
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const relative = pathname === '/' ? 'index.html' : pathname.slice(1);
    const file = path.resolve(dist, relative);
    if (!file.startsWith(dist + path.sep)) { response.writeHead(400).end(); return; }
    try {
      const body = await readFile(file);
      const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
      response.writeHead(200, { 'Content-Type': type }).end(body);
    } catch { response.writeHead(404).end('Not found'); }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  url = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ executablePath: chromePath, headless: true, args: ['--no-sandbox'] });
});

test.after(async () => {
  await browser?.close();
  if (server?.listening) await new Promise((resolve) => server.close(resolve));
  await Promise.all(tempFiles.map((file) => unlink(file).catch(() => {})));
});

async function setupPage({ failFirstRunPoll = false, importedRecord = null, connectionAdmin = false, initialSession = null, failFirstSessionCheck = false, initialArtifacts = [], initialConversations = [], initialConversationRecords = [], resumeStreams = [], initialBatches = [], initialBatchRecords = [], failBatchSubmitOnce = false, unauthenticatedOnSources = false, initialGaps = [], gapConflictOnce = false, runConflictOnce = false, pinnedRunRevision = false, saveDelayMs = 0, conversationCreateDelayMs = 0 } = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('pageerror', (error) => console.error('Browser page error:', error.message));
  page.on('requestfailed', (request) => console.error('Browser request failed:', request.url(), request.failure()?.errorText));
  const capturedQueries = [];
  const capturedRevisions = [];
  const capturedRuns = [];
  let runPollFailures = failFirstRunPoll ? 1 : 0;
  const capturedConnectionWrites = [];
  const capturedReindexes = [];
  let sourceRows = structuredClone(sources);
  let connectionProfiles = [];
  let currentSession = initialSession ?? { mode: 'development', authenticated: true, workspaceKey: 'browser-test', displayName: 'Local development' };
  let sessionFailures = failFirstSessionCheck ? 1 : 0;
  let sourcesRequested = 0;
  const artifactSummaries = [...initialArtifacts];
  let workbookHead = structuredClone(workbookRecord);
  const batchSummaries = [...initialBatches];
  const batchRecords = new Map(initialBatchRecords.map((batch) => [batch.batchId, structuredClone(batch)]));
  const capturedBatchSubmissions = [];
  const capturedBatchPolls = [];
  const capturedBatchCancels = [];
  const openedArtifactRevisions = [];
  const openedRunRevisions = [];
  const downloadedArtifacts = [];
  let failBatchSubmit = failBatchSubmitOnce;
  const conversationSummaries = [...initialConversations];
  const conversationRecords = new Map(initialConversationRecords.map((record) => [record.conversationId, structuredClone(record)]));
  const capturedConversationTurns = [];
  const capturedConversationStreams = [];
  let notifyConversationCreate;
  const conversationCreateStarted = new Promise((resolve) => { notifyConversationCreate = resolve; });
  const conversationStreams = new Map(resumeStreams.map((stream) => [`${stream.conversationId}:${stream.requestId}`, { ...stream, polls: 0 }]));
  let conversationTurnFailures = 0;
  let conversationProviderCalls = 0;
  const gapRows = structuredClone(initialGaps);
  const capturedGapResolutions = [];
  let failGapConflict = gapConflictOnce;
  let failRunConflict = runConflictOnce;
  await page.route('**/api/**', async (route) => {
    const req = route.request(); const apiUrl = new URL(req.url());
    if (apiUrl.pathname === '/api/session' && req.method() === 'GET') { if (sessionFailures > 0) { sessionFailures--; return route.fulfill({ status: 503, json: { detail: 'session temporarily unavailable' } }); } return route.fulfill({ json: currentSession, headers: { 'Cache-Control': 'no-store' } }); }
    if (apiUrl.pathname === '/api/auth/logout' && req.method() === 'POST') { currentSession = { mode: 'oidc', authenticated: false }; return route.fulfill({ status: 204 }); }
    if (apiUrl.pathname === '/api/auth/login' && req.method() === 'GET') return route.fulfill({ status: 200, contentType: 'text/plain', body: 'login redirect test stub' });
    if (apiUrl.pathname === '/api/collections' && req.method() === 'GET') return route.fulfill({ json: [{ id: 'collection-1', name: 'default' }] });
    if (apiUrl.pathname === '/api/sources' && req.method() === 'GET') { sourcesRequested++; if (unauthenticatedOnSources) { currentSession = { mode: 'oidc', authenticated: false }; return route.fulfill({ status: 401, json: { detail: 'authentication required' } }); } return route.fulfill({ json: sourceRows }); }
    if (apiUrl.pathname === '/api/sources/source-1/reindex' && req.method() === 'POST') { const body = req.postDataJSON(); capturedReindexes.push(body); await new Promise((resolve) => setTimeout(resolve, 250)); sourceRows = sourceRows.map((item) => item.id === 'source-1' ? { ...item, indexRevision: item.indexRevision + 1, errorMessage: 'RuntimeError: test replacement failed' } : item); return route.fulfill({ status: 422, json: { detail: 'source reindex failed' } }); }
    if (apiUrl.pathname === '/api/sources' && req.method() === 'POST') {
      if (req.postData()?.includes('oversize.pdf')) return route.fulfill({ status: 413, json: { detail: 'upload exceeds configured size limit' } });
      return route.fulfill({ json: { id: 'bad-source', name: 'broken.pdf', locatorType: 'page', status: 'failed', chunkCount: 0, errorMessage: 'No readable pages were found.' } });
    }
    if (apiUrl.pathname === '/api/gaps' && req.method() === 'GET') return route.fulfill({ json: { gaps: gapRows, metrics: { repeatedGapObservations: 3, successfulRepeatedObservations: 1, recurrenceRate: 1 / 3 } } });
    const resolveGapMatch = /^\/api\/gaps\/([^/]+)\/resolve$/.exec(apiUrl.pathname);
    if (resolveGapMatch && req.method() === 'POST') {
      const body = req.postDataJSON(); const gap = gapRows.find((item) => item.gapId === resolveGapMatch[1]);
      capturedGapResolutions.push({ id: resolveGapMatch[1], body });
      if (!gap) return route.fulfill({ status: 404, json: { detail: 'Gap or eligible source not found.' } });
      if (failGapConflict) { failGapConflict = false; gap.revision += 1; return route.fulfill({ status: 409, json: { detail: 'Gap changed; refresh before resolving it.' } }); }
      if (body.expectedRevision !== gap.revision) return route.fulfill({ status: 409, json: { detail: 'Gap changed; refresh before resolving it.' } });
      gap.state = 'resolved'; gap.resolution = { sourceIds: body.sourceIds, note: body.note, resolvedAt: '2026-10-08T00:01:00Z' };
      return route.fulfill({ json: gap });
    }
    if (apiUrl.pathname === '/api/conversations' && req.method() === 'GET') return route.fulfill({ json: conversationSummaries });
    if (apiUrl.pathname === '/api/conversations' && req.method() === 'POST') {
      const body = req.postDataJSON(); const createdAt = '2026-10-08T00:00:00Z';
      notifyConversationCreate();
      if (conversationCreateDelayMs) await new Promise((resolve) => setTimeout(resolve, conversationCreateDelayMs));
      const item = { conversationId: 'conversation-' + (conversationSummaries.length + 1), title: body.title, lastSequence: 0, pendingRequestId: null, createdAt, updatedAt: createdAt };
      conversationSummaries.unshift(item); conversationRecords.set(item.conversationId, { ...item, turns: [] });
      return route.fulfill({ status: 201, json: item });
    }
    const streamSubmitMatch = /^\/api\/conversations\/([^/]+)\/turns\/stream$/.exec(apiUrl.pathname);
    if (streamSubmitMatch && req.method() === 'POST') {
      const id = streamSubmitMatch[1]; const body = req.postDataJSON();
      capturedConversationTurns.push({ id, body }); capturedConversationStreams.push({ id, body });
      const record = conversationRecords.get(id);
      if (!record) return route.fulfill({ status: 404, json: { detail: 'conversation not found' } });
      const priorUsers = record.turns.filter((turn) => turn.role === 'user');
      const inherited = priorUsers.length ? priorUsers.at(-1).evidence.effective.sourceIds : null;
      const sourceIds = Object.hasOwn(body, 'sourceIds') ? body.sourceIds : inherited;
      const userTurn = { sequence: record.lastSequence + 1, requestId: body.requestId, role: 'user', text: body.query, status: 'input', evidence: { request: body, query: body.query, effective: { collectionId: 'browser-test', sourceIds, retrievalQuery: body.query, artifactReferences: [] } }, createdAt: '2026-10-08T00:01:00Z' };
      record.turns.push(userTurn); record.lastSequence = userTurn.sequence; record.pendingRequestId = body.requestId;
      conversationSummaries[0] = { ...record };
      conversationStreams.set(`${id}:${body.requestId}`, { conversationId: id, requestId: body.requestId, body, polls: 0 });
      return route.fulfill({ status: 202, json: { conversationId: id, requestId: body.requestId, jobId: 'job-' + body.requestId, state: 'queued' } });
    }
    const streamEventsMatch = /^\/api\/conversations\/([^/]+)\/turns\/([^/]+)\/events$/.exec(apiUrl.pathname);
    if (streamEventsMatch && req.method() === 'GET') {
      const id = streamEventsMatch[1]; const requestId = streamEventsMatch[2];
      const key = `${id}:${requestId}`; const stream = conversationStreams.get(key);
      const afterEventId = Number(apiUrl.searchParams.get('afterEventId') ?? 0);
      if (!stream) return route.fulfill({ json: { events: [], nextEventId: afterEventId, requestState: 'pending', jobState: 'unknown', done: false } });
      const record = conversationRecords.get(id);
      if (stream.polls++ === 0) return route.fulfill({ json: { events: [{ eventId: 1, type: 'provisional_delta', text: 'Unverified provisional text.' }, { eventId: 2, type: 'stage', stage: 'generating' }].filter((event) => event.eventId > afterEventId), nextEventId: 2, requestState: 'pending', jobState: 'running', done: false } });
      const body = stream.body ?? { query: 'Resumed request' };
      const failed = conversationTurnFailures > 0;
      if (failed) conversationTurnFailures--;
      const refusal = body.sourceIds?.length === 0;
      const status = failed ? 'failed' : refusal ? 'refusal' : 'answer';
      const answer = failed ? 'Conversation request failed. Saved turns were retained.' : refusal ? 'No sources were selected for this question.' : 'A basis spans the vector space.';
      record.turns.push({ sequence: ++record.lastSequence, requestId, role: 'assistant', text: answer, status, evidence: { type: status, citations: failed || refusal ? [] : [{ sourceName: 'Linear Algebra Primer', locatorLabel: 'section 2.1', chunkId: 'chunk-la-1' }], contextBlock: failed || refusal ? [] : trace().contextBlock, traceId: 'trace-browser', turnReferences: [] }, createdAt: '2026-10-08T00:01:01Z' });
      record.pendingRequestId = null; record.updatedAt = '2026-10-08T00:01:01Z'; conversationSummaries[0] = { ...record };
      return route.fulfill({ json: { events: [{ eventId: 3, type: 'state', state: failed ? 'failed' : 'succeeded' }].filter((event) => event.eventId > afterEventId), nextEventId: 3, requestState: failed ? 'failed' : 'completed', jobState: failed ? 'failed' : 'succeeded', done: true } });
    }
    const conversationMatch = /^\/api\/conversations\/([^/]+)$/.exec(apiUrl.pathname);
    if (conversationMatch && req.method() === 'GET') {
      const record = conversationRecords.get(conversationMatch[1]);
      if (!record) return route.fulfill({ json: { detail: 'not found' }, status: 404 });
      const after = Number(apiUrl.searchParams.get('after') ?? 0);
      const limit = Number(apiUrl.searchParams.get('limit') ?? 200);
      return route.fulfill({ json: { ...record, turns: record.turns.filter((turn) => turn.sequence > after).slice(0, limit) } });
    }
    const conversationTurnMatch = /^\/api\/conversations\/([^/]+)\/turns$/.exec(apiUrl.pathname);
    if (conversationTurnMatch && req.method() === 'POST') {
      const id = conversationTurnMatch[1]; const body = req.postDataJSON(); capturedConversationTurns.push({ id, body });
      const record = conversationRecords.get(id);
      if (!record) return route.fulfill({ status: 404, json: { detail: 'conversation not found' } });
      const priorUsers = record.turns.filter((turn) => turn.role === 'user');
      const inherited = priorUsers.length ? priorUsers.at(-1).evidence.effective.sourceIds : null;
      const sourceIds = Object.hasOwn(body, 'sourceIds') ? body.sourceIds : inherited;
      const userTurn = { sequence: record.lastSequence + 1, requestId: body.requestId, role: 'user', text: body.query, status: 'input', evidence: { request: body, query: body.query, effective: { collectionId: 'browser-test', sourceIds, retrievalQuery: body.query, artifactReferences: [] } }, createdAt: '2026-10-08T00:01:00Z' };
      record.turns.push(userTurn); record.lastSequence = userTurn.sequence;
      if (conversationTurnFailures > 0) {
        conversationTurnFailures--;
        record.turns.push({ sequence: ++record.lastSequence, requestId: body.requestId, role: 'assistant', text: 'Conversation request failed. Saved turns were retained.', status: 'failed', evidence: { type: 'failed', citations: [], contextBlock: [], traceId: null, turnReferences: [] }, createdAt: '2026-10-08T00:01:01Z' });
        record.updatedAt = '2026-10-08T00:01:01Z'; record.pendingRequestId = null; conversationSummaries[0] = { ...record };
        return route.fulfill({ status: 503, json: { detail: 'conversation request failed; saved turns were retained' } });
      }
      if (body.mode === 'history') {
        const previous = record.turns.filter((turn) => turn.sequence < userTurn.sequence);
        const references = previous.map((turn) => turn.sequence);
        const answer = previous.map((turn) => `Turn ${turn.sequence} (${turn.role}):\n${turn.text}`).join('\n\n') || 'No prior turns are saved in this conversation.';
        record.turns.push({ sequence: ++record.lastSequence, requestId: body.requestId, role: 'assistant', text: answer, status: 'history', evidence: { type: 'history', citations: [], contextBlock: [], traceId: null, turnReferences: references }, createdAt: '2026-10-08T00:01:01Z' });
        record.updatedAt = '2026-10-08T00:01:01Z'; conversationSummaries[0] = { ...record };
        return route.fulfill({ json: { type: 'history', answer, turnReferences: references, conversationId: id, sequence: record.lastSequence } });
      }
      if (sourceIds?.length === 0) {
        const reason = 'No sources were selected for this question.';
        record.turns.push({ sequence: ++record.lastSequence, requestId: body.requestId, role: 'assistant', text: reason, status: 'refusal', evidence: { type: 'refusal', citations: [], contextBlock: [], traceId: null, turnReferences: [] }, createdAt: '2026-10-08T00:01:01Z' });
        conversationSummaries[0] = { ...record };
        return route.fulfill({ json: { type: 'refusal', reason, conversationId: id, sequence: record.lastSequence } });
      }
      conversationProviderCalls++;
      const contextBlock = trace().contextBlock;
      const citations = [{ sourceName: 'Linear Algebra Primer', locatorLabel: 'section 2.1', chunkId: 'chunk-la-1' }];
      const answer = 'A basis spans the vector space.';
      record.turns.push({ sequence: ++record.lastSequence, requestId: body.requestId, role: 'assistant', text: answer, status: 'answer', evidence: { type: 'answer', citations, contextBlock, traceId: 'trace-browser', turnReferences: [] }, createdAt: '2026-10-08T00:01:01Z' });
      record.updatedAt = '2026-10-08T00:01:01Z'; conversationSummaries[0] = { ...record };
      return route.fulfill({ json: { type: 'answer', answer, citations, trace: trace(), conversationId: id, sequence: record.lastSequence } });
    }
    const abandonMatch = /^\/api\/conversations\/([^/]+)\/turns\/([^/]+)\/abandon$/.exec(apiUrl.pathname);
    if (abandonMatch && req.method() === 'POST') {
      const record = conversationRecords.get(abandonMatch[1]);
      if (!record) return route.fulfill({ status: 404, json: { detail: 'conversation not found' } });
      const requestId = abandonMatch[2]; const reason = 'Request abandoned; late completion is fenced.';
      record.turns.push({ sequence: ++record.lastSequence, requestId, role: 'assistant', text: reason, status: 'abandoned', evidence: { type: 'abandoned', citations: [], contextBlock: [], traceId: null, turnReferences: [] }, createdAt: '2026-10-08T00:01:02Z' });
      record.pendingRequestId = null; conversationSummaries[0] = { ...record };
      return route.fulfill({ json: { type: 'abandoned', reason, conversationId: record.conversationId, sequence: record.lastSequence } });
    }
    if (apiUrl.pathname === '/api/query' && req.method() === 'POST') {
      const body = req.postDataJSON(); capturedQueries.push(body);
      if (body.sourceIds?.length === 0) return route.fulfill({ json: { type: 'refusal', reason: 'No sources were selected for this question.', trace: trace() } });
      return route.fulfill({ json: { type: 'answer', answer: 'A basis spans the vector space.', citations: [{ sourceName: 'Linear Algebra Primer', locatorLabel: 'section 2.1', chunkId: 'chunk-la-1' }], trace: trace() } });
    }
    if (apiUrl.pathname === '/api/recommendations') return route.fulfill({ json: [{ clusterId: 'linear-foundations', path: ['Mathematics', 'Linear Algebra'], courses: [{ courseId: 'math-221', code: 'MATH 221', title: 'Linear Algebra' }] }] });
    if (apiUrl.pathname === '/api/admin/connections' && req.method() === 'GET') return connectionAdmin ? route.fulfill({ json: connectionProfiles }) : route.fulfill({ status: 404, json: { detail: 'not found' } });
    if (apiUrl.pathname === '/api/admin/connections' && req.method() === 'POST') { const body = req.postDataJSON(); capturedConnectionWrites.push(body); connectionProfiles = [{ ...profileFixture, name: body.name, model_id: body.model_id, provider: body.provider }]; return route.fulfill({ status: 201, json: connectionProfiles[0] }); }
    if (apiUrl.pathname === '/api/admin/connections/profile-1/secret' && req.method() === 'PUT') { const body = req.postDataJSON(); capturedConnectionWrites.push(body); connectionProfiles = [{ ...profileFixture, revision: profileFixture.revision + 1 }]; return route.fulfill({ json: connectionProfiles[0] }); }
    if (apiUrl.pathname === '/api/artifacts' && req.method() === 'GET') return route.fulfill({ json: artifactSummaries });
    if (apiUrl.pathname === '/api/artifacts' && req.method() === 'POST') {
      const body = req.postDataJSON();
      artifactSummaries.push({ artifactId: 'artifact-1', revisionId: 'rev-1', kind: body.kind, title: body.spec.title, status: 'ready' });
      return route.fulfill({ json: { artifactId: 'artifact-1', revisionId: 'rev-1', parentRevisionId: null, kind: body.kind, title: body.spec.title, spec: body.spec } });
    }
    if (apiUrl.pathname === '/api/artifacts/artifact-wb' && req.method() === 'GET') return route.fulfill({ json: workbookRecord });
    if (apiUrl.pathname === '/api/artifact-batches' && req.method() === 'GET') return route.fulfill({ json: batchSummaries });
    if (apiUrl.pathname === '/api/artifact-batches' && req.method() === 'POST') {
      const body = req.postDataJSON(); capturedBatchSubmissions.push(body);
      const createdAt = '2026-10-08T00:00:00Z';
      const record = {
        batchId: body.batchId, state: failBatchSubmit ? 'unknown' : 'in_progress', cancelRequested: false,
        itemCount: body.items.length, totalCount: body.items.length, completedCount: 0, failedCount: 0, createdAt, updatedAt: createdAt,
        items: body.items.map((item) => ({ itemId: item.itemId, state: 'pending', publicationState: 'pending', artifactId: null, revisionId: null, errorCode: null })),
      };
      failBatchSubmit = false;
      batchRecords.set(record.batchId, record);
      batchSummaries.unshift({ ...record, items: undefined });
      if (capturedBatchSubmissions.length === 1 && record.state === 'unknown') return route.fulfill({ status: 503, json: { detail: 'submission outcome unknown' } });
      return route.fulfill({ json: record });
    }
    const batchMatch = /^\/api\/artifact-batches\/([^/]+)$/.exec(apiUrl.pathname);
    if (batchMatch && req.method() === 'GET') {
      const record = batchRecords.get(batchMatch[1]);
      return route.fulfill({ status: record ? 200 : 404, json: record ?? { detail: 'batch not found' } });
    }
    const batchPollMatch = /^\/api\/artifact-batches\/([^/]+)\/poll$/.exec(apiUrl.pathname);
    if (batchPollMatch && req.method() === 'POST') {
      capturedBatchPolls.push(batchPollMatch[1]);
      const record = batchRecords.get(batchPollMatch[1]);
      if (!record) return route.fulfill({ status: 404, json: { detail: 'batch not found' } });
      record.state = 'completed'; record.completedCount = record.itemCount; record.failedCount = 0; record.totalCount = record.itemCount;
      record.items = record.items.map((item, index) => ({ ...item, state: 'completed', publicationState: 'published', artifactId: `batch-artifact-${index + 1}`, revisionId: `revision-published-${index + 1}` }));
      batchSummaries[batchSummaries.findIndex((item) => item.batchId === record.batchId)] = { ...record, items: undefined };
      return route.fulfill({ json: record });
    }
    const batchCancelMatch = /^\/api\/artifact-batches\/([^/]+)\/cancel$/.exec(apiUrl.pathname);
    if (batchCancelMatch && req.method() === 'POST') {
      capturedBatchCancels.push(batchCancelMatch[1]);
      const record = batchRecords.get(batchCancelMatch[1]);
      if (!record) return route.fulfill({ status: 404, json: { detail: 'batch not found' } });
      record.state = 'cancelled'; record.cancelRequested = true;
      batchSummaries[batchSummaries.findIndex((item) => item.batchId === record.batchId)] = { ...record, items: undefined };
      return route.fulfill({ json: record });
    }
    if (apiUrl.pathname === '/api/artifacts/batch-artifact-1' && req.method() === 'GET') {
      openedArtifactRevisions.push(apiUrl.searchParams.get('revisionId'));
      return route.fulfill({ json: { artifactId: 'batch-artifact-1', revisionId: 'revision-published-1', parentRevisionId: null, kind: 'document', title: 'Published batch document', spec: { kind: 'document', title: 'Published batch document', blocks: [{ id: 'batch-block', type: 'paragraph', text: 'Published from batch.' }] } } });
    }
    if (apiUrl.pathname === '/api/runs/limits' && req.method() === 'GET') return route.fulfill({ json: { maxCalls: 4, totalTokenBudget: 6000, perCallOutputCap: 2048 } });
    if (apiUrl.pathname === '/api/runs' && req.method() === 'POST') { capturedRuns.push(req.postDataJSON()); if (failRunConflict) { failRunConflict = false; return route.fulfill({ status: 409, json: { detail: 'artifact head changed' } }); } return route.fulfill({ json: { runId: 'run-1', state: 'queued' } }); }
    if (apiUrl.pathname === '/api/runs/run-1' && req.method() === 'GET') {
      if (runPollFailures > 0) { runPollFailures--; return route.fulfill({ status: 503, json: { detail: 'temporary job status outage' } }); }
      return route.fulfill({ json: { runId: 'run-1', state: 'succeeded', artifactIds: ['artifact-1'], ...(pinnedRunRevision ? { artifactRevisions: [{ artifactId: 'artifact-1', revisionId: 'published-r1' }] } : {}) } });
    }
    if (apiUrl.pathname === '/api/runs/run-1/events') return route.fulfill({ json: [{ eventId: 1, runId: 'run-1', state: 'succeeded', stage: 'rendering', artifactIds: ['artifact-1'], ...(pinnedRunRevision ? { artifactRevisions: [{ artifactId: 'artifact-1', revisionId: 'published-r1' }] } : {}) }] });
    if (apiUrl.pathname === '/api/runs/run-1/cancel' && req.method() === 'POST') return route.fulfill({ json: { runId: 'run-1', state: 'cancelled' } });
    if (apiUrl.pathname === '/api/artifacts/artifact-1' && req.method() === 'GET') { const revisionId = apiUrl.searchParams.get('revisionId'); if (revisionId) openedRunRevisions.push(revisionId); return route.fulfill({ json: { artifactId: 'artifact-1', revisionId: revisionId ?? (pinnedRunRevision ? 'newer-head' : 'rev-1'), parentRevisionId: null, kind: 'document', title: 'Generated draft', spec: { kind: 'document', title: 'Generated draft', blocks: [], provenance: { authority: 'generated_draft' } } } }); }
    if (apiUrl.pathname === '/api/artifacts/import' && req.method() === 'POST') return route.fulfill({ json: importedRecord ?? workbookRecord });
    if (apiUrl.pathname === '/api/artifacts/artifact-wb/original') return route.fulfill({ body: 'original xlsx bytes', headers: { 'Content-Disposition': "attachment; filename*=UTF-8''metrics.xlsx" } });
    if (apiUrl.pathname === '/api/artifacts/artifact-wb/revisions') {
      const body = req.postDataJSON(); capturedRevisions.push(body);
      if (saveDelayMs) await new Promise((resolve) => setTimeout(resolve, saveDelayMs));
      const updated = structuredClone(workbookHead);
      updated.revisionId = `wb-r${capturedRevisions.length + 1}`; updated.parentRevisionId = workbookHead.revisionId;
      if (body.spec) updated.spec = body.spec;
      for (const operation of body.operations ?? []) {
        const sheet = updated.spec.sheets.find((item) => item.id === operation.sheet_id);
        const cellIndex = sheet.cells.findIndex((item) => item.id === operation.cell_id);
        sheet.cells[cellIndex] = operation.cell;
      }
      updated.title = updated.spec.title;
      workbookHead = updated;
      return route.fulfill({ json: updated });
    }
    if (apiUrl.pathname === '/api/artifacts/artifact-1/revisions') return route.fulfill({ status: 409, json: { detail: 'Stale parent revision.' } });
    if (apiUrl.pathname === '/api/artifacts/artifact-doc/revisions') {
      const body = req.postDataJSON(); capturedRevisions.push(body);
      if (saveDelayMs) await new Promise((resolve) => setTimeout(resolve, saveDelayMs));
      return route.fulfill({ json: { ...documentRecord, revisionId: 'doc-r2', parentRevisionId: 'doc-r1', spec: body.spec } });
    }
    if (apiUrl.pathname === '/api/artifacts/artifact-1/preview') return route.fulfill({ json: { kind: 'document', title: 'Browser draft', html: '<p>Safe preview</p>', sandboxRequired: false } });
    if (apiUrl.pathname === '/api/artifacts/artifact-1/download') return route.fulfill({ body: '<p>Safe preview</p>', headers: { 'Content-Disposition': 'attachment; filename="browser-draft.html"' } });
    if (apiUrl.pathname === '/api/artifacts/artifact-doc/download') {
      const format = apiUrl.searchParams.get('format');
      downloadedArtifacts.push({ format, revisionId: apiUrl.searchParams.get('revisionId') });
      return route.fulfill({ body: `rendered ${format}`, headers: { 'Content-Disposition': `attachment; filename="notes-r1.${format}"` } });
    }
    if (apiUrl.pathname === '/api/artifacts/artifact-wb/download') {
      const format = apiUrl.searchParams.get('format');
      downloadedArtifacts.push({ format, revisionId: apiUrl.searchParams.get('revisionId') });
      return route.fulfill({ body: `rendered ${format}`, headers: { 'Content-Disposition': `attachment; filename="metrics-wb-r1.${format}"` } });
    }
    return route.fulfill({ status: 404, json: { detail: `Unexpected test route ${req.method()} ${apiUrl.pathname}` } });
  });
  await page.goto(url);
  try { await page.getByRole('heading', { name: 'Emory STEM Advisor' }).waitFor({ timeout: 5000 }); }
  catch { throw new Error(`Frontend did not render. Body: ${await page.locator('body').innerText()}`); }
  return { context, page, capturedQueries, capturedRevisions, capturedRuns, capturedConnectionWrites, capturedReindexes, capturedConversationTurns, capturedConversationStreams, conversationRecords, conversationSummaries, capturedBatchSubmissions, capturedBatchPolls, capturedBatchCancels, openedArtifactRevisions, openedRunRevisions, downloadedArtifacts, batchRecords, capturedGapResolutions, get conversationProviderCalls() { return conversationProviderCalls; }, waitForConversationCreate: () => conversationCreateStarted, failNextConversationTurn: () => { conversationTurnFailures++; }, sourcesRequested };
}

const message = (page) => page.getByLabel('Message', { exact: true });

async function send(page, text) {
  await message(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
}

/** Open "Search in" and limit questions to exactly `included` source names. */
async function searchOnly(page, included) {
  await page.getByRole('button', { name: /^Search in:/ }).click();
  await page.getByRole('radio', { name: /Only these sources/ }).check();
  for (const name of ['Linear Algebra Primer', 'Lab Safety Manual']) {
    const box = page.getByRole('checkbox', { name: `Include ${name} in questions` });
    if ((await box.isChecked()) !== included.includes(name)) await box.click();
  }
  await page.keyboard.press('Escape');
}

async function openSidebar(page, name) {
  await page.getByRole('navigation', { name: 'Chats and library' }).getByRole('button', { name }).click();
}

async function openTool(page, tab) {
  await openSidebar(page, 'Tools');
  await page.getByRole('tab', { name: tab }).click();
}

async function createMenu(page, item) {
  await page.getByRole('button', { name: 'Create or import' }).click();
  await page.getByRole('menuitem', { name: item }).click();
}

async function importArtifact(page, file) {
  await createMenu(page, /^Import a Word/);
  await page.locator('input[type=file][aria-label="Choose a Markdown, DOCX, or XLSX file"]').setInputFiles(file);
  await page.getByRole('region', { name: 'Artifact editor' }).waitFor();
}

async function download(page, format) {
  await page.getByRole('button', { name: 'Download', exact: true }).click();
  const event = page.waitForEvent('download');
  await page.getByRole('menuitem', { name: `Download .${format}` }).click();
  return (await event).suggestedFilename();
}

const revisionShown = (page, revisionId) => page.locator(`.canvas-footer [title$="revision ${revisionId}"]`).waitFor();

test('browser keeps empty source scope explicit, cites the displayed passage, and renders recommendations', async () => {
  const { context, page, capturedConversationStreams } = await setupPage();
  try {
    await openSidebar(page, 'Tools');
    await page.getByRole('tab', { name: 'Developer' }).waitFor();
    assert.equal(await page.getByRole('tab', { name: 'Model connections' }).count(), 0, 'local settings stay hidden when the endpoint is disabled');
    await page.keyboard.press('Escape');

    await searchOnly(page, []);
    await page.getByRole('button', { name: 'Search in: No sources' }).waitFor();
    await send(page, 'What is a basis?');
    await page.getByText('I couldn’t answer that from your sources.').waitFor();
    assert.deepEqual(capturedConversationStreams.at(-1)?.body.sourceIds, []);

    await searchOnly(page, ['Linear Algebra Primer']);
    await send(page, 'What is a basis?');
    await page.getByText('A basis spans the vector space.', { exact: true }).waitFor();
    assert.deepEqual(capturedConversationStreams.at(-1)?.body.sourceIds, ['source-1']);
    await page.getByRole('button', { name: 'Linear Algebra Primer section 2.1' }).click();
    assert.equal(await page.getByRole('figure', { name: 'Cited passage' }).locator('blockquote').innerText(),
      'A basis spans the vector space and is linearly independent.');

    await openSidebar(page, 'Explore courses');
    await page.getByLabel('Interest or career goal').fill('linear algebra');
    await page.getByRole('button', { name: 'Find' }).click();
    await page.getByText('MATH 221').waitFor();
    await page.getByText('Mathematics › Linear Algebra').waitFor();
    await page.getByRole('button', { name: 'Ask about MATH 221' }).click();
    assert.match(await message(page).inputValue(), /MATH 221 \(Linear Algebra\)/);
  } finally { await context.close(); }
});

test('failed source replacement refreshes revision diagnostics and keeps citations available', async () => {
  const { context, page, capturedReindexes } = await setupPage();
  try {
    await openSidebar(page, /^Sources/);
    const row = page.locator('.source-row').filter({ hasText: 'Linear Algebra Primer' });
    await row.getByText('Details').click();
    const button = page.getByRole('button', { name: 'Reindex Linear Algebra Primer' });
    await button.click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('.source-reindex-button')).some((item) => item.textContent === 'Reindexing…'));
    assert.equal(await button.isDisabled(), true, 'duplicate clicks are disabled while the request is active');
    try { await page.getByRole('alert').getByText(/Reindex failed: API request failed \(422\): source reindex failed/).waitFor({ timeout: 5000 }); }
    catch { throw new Error(`Expected reindex failure was not rendered. Calls=${JSON.stringify(capturedReindexes)} Body=${await page.locator('body').innerText()}`); }
    assert.deepEqual(capturedReindexes, [{ expectedIndexRevision: 2 }]);
    await row.getByText('Index revision 3', { exact: true }).waitFor();
    await row.getByText('RuntimeError: test replacement failed', { exact: true }).waitFor();
    await page.getByRole('alert').getByText(/previously ready index remains available/).waitFor();
    assert.equal(await button.isDisabled(), false, 'the button is re-enabled after failure');
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    await send(page, 'What does a basis do?');
    await page.getByText('A basis spans the vector space.', { exact: true }).waitFor();
    await page.locator('.source-chip').filter({ hasText: 'Linear Algebra Primer' }).click();
    assert.match(await page.getByRole('figure', { name: 'Cited passage' }).locator('blockquote').innerText(), /basis spans the vector space/);
  } finally { await context.close(); }
});

test('saved conversation reloads exact citation context and inherits its scope', async () => {
  const state = await setupPage();
  const { context, page, capturedConversationTurns } = state;
  try {
    await send(page, 'What is a basis?');
    await page.getByText('A basis spans the vector space.', { exact: true }).waitFor();
    assert.equal(capturedConversationTurns.length, 1);
    assert.equal(capturedConversationTurns[0].body.expectedSequence, 0);
    assert.match(capturedConversationTurns[0].body.requestId, /^[0-9a-f-]{36}$/i);
    assert.equal(Object.hasOwn(capturedConversationTurns[0].body, 'sourceIds'), false, 'first turn uses the default all-source scope');
    await page.getByRole('button', { name: 'Linear Algebra Primer section 2.1' }).click();
    const passage = page.getByRole('figure', { name: 'Cited passage' }).locator('blockquote');
    assert.equal(await passage.innerText(), 'A basis spans the vector space and is linearly independent.');

    await page.reload();
    const chat = page.getByRole('main', { name: 'Chat' });
    await chat.getByText('What is a basis?', { exact: true }).first().waitFor();
    await chat.getByText('A basis spans the vector space.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Linear Algebra Primer section 2.1' }).click();
    assert.equal(await passage.innerText(), 'A basis spans the vector space and is linearly independent.');

    await send(page, 'Why must basis vectors be independent?');
    await chat.getByText('Why must basis vectors be independent?', { exact: true }).first().waitFor();
    assert.equal(capturedConversationTurns[1].body.expectedSequence, 2);
    assert.equal(Object.hasOwn(capturedConversationTurns[1].body, 'sourceIds'), false, 'follow-up inherits the saved scope');
    assert.notEqual(capturedConversationTurns[0].body.requestId, capturedConversationTurns[1].body.requestId);
  } finally { await context.close(); }
});

test('draft edits made during conversation creation are transferred and survive the sent turn', async () => {
  const state = await setupPage({ conversationCreateDelayMs: 350 });
  try {
    await send(state.page, 'What is a basis?');
    await state.waitForConversationCreate();
    await message(state.page).fill('A newer draft typed during creation.');
    await state.page.getByText('A basis spans the vector space.', { exact: true }).waitFor();
    assert.equal(state.capturedConversationTurns.length, 1);
    assert.equal(state.capturedConversationTurns[0].body.query, 'What is a basis?');
    assert.equal(await message(state.page).inputValue(), 'A newer draft typed during creation.');
  } finally { await state.context.close(); }
});

test('saved research answers retain partial notices and exact citation navigation after reload', async () => {
  const createdAt = '2026-10-13T00:00:00Z';
  const summary = { conversationId: 'research-1', title: 'Research options', lastSequence: 2, pendingRequestId: null, createdAt, updatedAt: createdAt };
  const record = { ...summary, turns: [
    { sequence: 1, requestId: 'research-request', role: 'user', text: 'Which path should I explore?', status: 'input', evidence: { request: { query: 'Which path should I explore?' }, query: 'Which path should I explore?', effective: { collectionId: 'browser-test', sourceIds: null, retrievalQuery: 'Which path should I explore?', artifactReferences: [] } }, createdAt },
    { sequence: 2, requestId: 'research-request', role: 'assistant', text: 'I would explore this path [1].', status: 'answer', evidence: { type: 'answer', citations: [{ sourceName: 'Linear Algebra Primer', locatorLabel: 'section 2.1', chunkId: 'chunk-la-1' }], contextBlock: trace().contextBlock, traceId: 'research-trace', turnReferences: [], research: { runId: 'run-1', outcome: 'recommendation', completeness: 'partial', blocks: [], unresolvedQuestions: ['The double-counting policy remains unknown.'] } }, createdAt },
  ] };
  const { context, page } = await setupPage({ initialConversations: [summary], initialConversationRecords: [record] });
  try {
    await page.getByRole('note').getByText('Some questions remain open.').waitFor();
    await page.getByText('The double-counting policy remains unknown.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Linear Algebra Primer section 2.1', exact: true }).click();
    assert.equal(await page.getByRole('figure', { name: 'Cited passage' }).locator('blockquote').innerText(), trace().contextBlock[0].text);
    await page.reload();
    await page.getByRole('note').getByText('Some questions remain open.').waitFor();
  } finally { await context.close(); }
});

test('reopened pending conversation resumes stored stream events without posting or retrying', async () => {
  const createdAt = '2026-10-08T00:00:00Z';
  const summary = { conversationId: 'resume-1', title: 'Resume answer', lastSequence: 1, pendingRequestId: 'resume-request', createdAt, updatedAt: createdAt };
  const record = { ...summary, turns: [{ sequence: 1, requestId: 'resume-request', role: 'user', text: 'What is a basis?', status: 'input', evidence: { request: { query: 'What is a basis?' }, query: 'What is a basis?', effective: { collectionId: 'browser-test', sourceIds: null, retrievalQuery: 'What is a basis?', artifactReferences: [] } }, createdAt }] };
  const state = await setupPage({ initialConversations: [summary], initialConversationRecords: [record], resumeStreams: [{ conversationId: 'resume-1', requestId: 'resume-request', body: { query: 'What is a basis?' } }] });
  try {
    await state.page.getByLabel('Unverified provisional answer').getByText('Unverified provisional text.').waitFor();
    assert.equal(await state.page.locator('.message-pending .source-chip, .message-pending .cite-mark').count(), 0, 'provisional output carries no active citations');
    await state.page.locator('.message-assistant').getByText('A basis spans the vector space.', { exact: true }).waitFor();
    assert.equal(state.capturedConversationStreams.length, 0, 'reopen inspects events and never resubmits the request');
    assert.equal(state.conversationProviderCalls, 0, 'event reconnection does not call a model');
  } finally { await state.context.close(); }
});

test('saved conversation preserves explicit empty scope and exposes pending requests for explicit abandonment', async () => {
  const pendingSummary = { conversationId: 'pending-1', title: 'Pending conversation', lastSequence: 1, pendingRequestId: 'pending-request', createdAt: '2026-10-08T00:00:00Z', updatedAt: '2026-10-08T00:00:00Z' };
  const pendingRecord = { ...pendingSummary, turns: [{ sequence: 1, requestId: 'pending-request', role: 'user', text: 'Unfinished question', status: 'input', evidence: { request: { query: 'Unfinished question' }, query: 'Unfinished question', effective: { collectionId: 'browser-test', sourceIds: null, retrievalQuery: 'Unfinished question', artifactReferences: [] } }, createdAt: '2026-10-08T00:00:01Z' }] };
  const { context, page, capturedConversationTurns, conversationRecords } = await setupPage({ initialConversations: [pendingSummary], initialConversationRecords: [pendingRecord] });
  const stop = page.getByRole('button', { name: 'Stop answering' });
  try {
    await page.getByText('Unfinished question', { exact: true }).waitFor();
    await stop.click();
    await page.getByText(/^Stopped\./).waitFor();
    assert.equal(conversationRecords.get('pending-1').pendingRequestId, null);
    assert.equal(capturedConversationTurns.length, 0, 'opening and abandoning pending work never replay its model call');

    let notifyLateRead, releaseLateRead;
    const lateReadStarted = new Promise(resolve => { notifyLateRead = resolve; });
    const release = new Promise(resolve => { releaseLateRead = resolve; });
    let heldRequestId;
    await page.route('**/api/conversations/*/turns/*/events**', async route => {
      const requestUrl = new URL(route.request().url());
      const requestId = requestUrl.pathname.split('/').at(-2);
      heldRequestId ??= requestId;
      const after = Number(requestUrl.searchParams.get('afterEventId'));
      if (requestId === heldRequestId && after > 0) {
        notifyLateRead();
        await release;
        return route.fulfill({ json: { events: [{ eventId: 2, type: 'provisional_delta', text: 'Late stale text' }], nextEventId: 2, requestState: 'pending', jobState: 'running', done: false } });
      }
      return route.fulfill({ json: { events: [{ eventId: 1, type: 'provisional_delta', text: 'Still generating' }].filter(event => event.eventId > after), nextEventId: 1, requestState: 'pending', jobState: 'running', done: false } });
    });
    await send(page, 'Cancel this newly submitted turn.');
    await lateReadStarted;
    assert.equal(await stop.isEnabled(), true, 'polling does not disable cancellation');
    await stop.click();
    await page.getByText(/^Stopped\./).waitFor();
    await page.waitForFunction(() => !document.querySelector('.composer button[type=submit]').disabled);
    assert.equal(await message(page).inputValue(), 'Cancel this newly submitted turn.');

    await send(page, 'A different new request.');
    await page.getByLabel('Unverified provisional answer').getByText('Still generating').waitFor();
    const lateResponse = page.waitForResponse(response => response.url().includes(heldRequestId) && response.url().includes('afterEventId=1'));
    releaseLateRead();
    await (await lateResponse).finished();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    assert.equal(await page.getByText('Late stale text', { exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: 'Send', exact: true }).isDisabled(), true, 'old poll cleanup cannot unlock the new pending request');
    assert.equal(capturedConversationTurns.length, 2);
    await stop.click();
    await page.getByText(/^Stopped\./).waitFor();
  } finally { await context.close(); }

  const state = await setupPage();
  try {
    await searchOnly(state.page, ['Linear Algebra Primer']);
    await send(state.page, 'Use only the primer.');
    await state.page.locator('.message-assistant').getByText('A basis spans the vector space.', { exact: true }).waitFor();
    await state.page.waitForFunction(() => document.querySelector('.composer textarea').value === '');
    assert.deepEqual(state.capturedConversationTurns[0].body.sourceIds, ['source-1']);
    await state.page.getByRole('button', { name: 'Search in: 1 source' }).waitFor();
    await searchOnly(state.page, []);
    await send(state.page, 'Do not use any source.');
    await state.page.getByText(/No sources were selected for this question/).waitFor();
    assert.deepEqual(state.capturedConversationTurns[1].body.sourceIds, [], 'empty selection stays distinct from inherited or all-source scope');
    assert.equal(state.conversationProviderCalls, 0, 'an explicit empty source scope never reaches generation');
  } finally { await state.context.close(); }
});

test('saved conversation opens on the latest bounded page and explicitly loads older turns', async () => {
  const createdAt = '2026-10-08T00:00:00Z';
  const pendingSummary = { conversationId: 'long-1', title: 'Long saved discussion', lastSequence: 401, pendingRequestId: 'pending-401', createdAt, updatedAt: createdAt };
  const turns = Array.from({ length: 401 }, (_, index) => {
    const sequence = index + 1;
    const requestId = sequence % 2 === 1 ? `request-${Math.ceil(sequence / 2)}` : `request-${sequence / 2}`;
    if (sequence % 2 === 1) return {
      sequence, requestId, role: 'user', text: `Historical turn sequence ${sequence}`, status: 'input',
      evidence: { request: {}, query: `Historical turn sequence ${sequence}`, effective: { collectionId: 'browser-test', sourceIds: null, retrievalQuery: '', artifactReferences: [] } }, createdAt,
    };
    return {
      sequence, requestId, role: 'assistant', text: `Saved answer sequence ${sequence}`, status: 'answer',
      evidence: { type: 'answer', citations: [], contextBlock: [], traceId: null, turnReferences: [] }, createdAt,
    };
  });
  const pendingRecord = { ...pendingSummary, turns };
  const { context, page, failNextConversationTurn } = await setupPage({ initialConversations: [pendingSummary], initialConversationRecords: [pendingRecord] });
  try {
    const transcript = page.getByRole('main', { name: 'Chat' });
    await transcript.getByText('Historical turn sequence 401', { exact: true }).waitFor();
    await transcript.getByText('Saved answer sequence 202', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Stop answering' }).waitFor();
    assert.equal(await transcript.getByText('Saved answer sequence 200', { exact: true }).count(), 0, 'older page is bounded and not fetched automatically');

    await page.getByRole('button', { name: 'Load earlier messages' }).click();
    await transcript.getByText('Historical turn sequence 201', { exact: true }).waitFor();
    assert.equal(await transcript.getByText('Historical turn sequence 1', { exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Load earlier messages' }).click();
    await transcript.getByText('Historical turn sequence 1', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Load earlier messages' }).count(), 0);

    await page.getByRole('button', { name: 'Stop answering' }).click();
    await page.getByText(/^Stopped\./).waitFor();
    await send(page, 'New question after 400 saved turns');
    await transcript.getByText('A basis spans the vector space.', { exact: true }).waitFor();
    await page.waitForFunction(() => document.querySelector('.composer textarea').value === '');
    assert.equal(await transcript.getByText('New question after 400 saved turns', { exact: true }).count(), 1);

    failNextConversationTurn();
    await send(page, 'Keep this failed long-conversation draft');
    await transcript.getByRole('alert').getByText('Conversation request failed. Saved turns were retained.', { exact: true }).waitFor();
    assert.equal(await message(page).inputValue(), 'Keep this failed long-conversation draft');
  } finally { await context.close(); }
});

test('the mascot face switches between the Swoop and Dooley themes and the choice persists', async () => {
  const { context, page } = await setupPage();
  try {
    const startTheme = await page.evaluate(() => document.documentElement.dataset.theme);
    const toDark = page.getByRole('button', { name: 'Switch to dark mode (Dooley)' });
    const toLight = page.getByRole('button', { name: 'Switch to light mode (Swoop)' });
    if (startTheme === 'dark') await toLight.click();
    await toDark.click();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'dark');
    assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.topbar')).backgroundColor), 'rgb(0, 0, 0)');
    await page.reload();
    await toLight.waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'dark');
    await toLight.click();
    assert.equal(await page.evaluate(() => getComputedStyle(document.querySelector('.topbar')).backgroundColor), 'rgb(1, 33, 105)', 'light mode is Emory blue');
  } finally { await context.close(); }
});

test('Enter during an IME composition does not send the message', async () => {
  const { context, page, capturedConversationStreams } = await setupPage();
  try {
    await message(page).fill('What is a basis?');
    await message(page).evaluate((el) => {
      el.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }));
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, isComposing: true }));
    });
    await page.waitForTimeout(150);
    assert.equal(capturedConversationStreams.length, 0, 'composing Enter must not submit');
    await message(page).press('Enter');
    await page.getByText('A basis spans the vector space.', { exact: true }).waitFor();
    assert.equal(capturedConversationStreams.length, 1, 'a committed Enter still submits');
  } finally { await context.close(); }
});

test('desktop, phone, and tablet viewports keep long chat lists separate from reachable navigation', async () => {
  const initialConversations = Array.from({ length: 40 }, (_, index) => ({
    conversationId: `layout-chat-${index}`, title: `Saved chat ${index + 1}`, lastSequence: 0,
    pendingRequestId: null, createdAt: '2026-10-08T00:00:00Z', updatedAt: '2026-10-08T00:00:00Z',
  }));
  const { context, page } = await setupPage({
    initialConversations,
    initialConversationRecords: initialConversations.map((item) => ({ ...item, turns: [] })),
  });
  try {
    const navigation = page.getByRole('navigation', { name: 'Chats and library' });
    const chats = navigation.getByRole('list', { name: 'Recent chats' });
    await chats.getByRole('button').last().waitFor({ state: 'attached' });
    for (const size of [{ width: 1280, height: 738 }, { width: 1024, height: 480 }, { width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 320, height: 568 }]) {
      await page.setViewportSize(size);
      const scope = page.getByRole('button', { name: /^Search in:/ });
      await scope.focus();
      await scope.press('Enter');
      await page.getByRole('checkbox', { name: 'Include Lab Safety Manual in questions' }).waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await page.getByRole('button', { name: 'Send', exact: true }).isVisible(), true);
      const viewport = await page.evaluate(() => ({ width: window.innerWidth, scrollWidth: document.documentElement.scrollWidth }));
      assert.ok(viewport.scrollWidth <= viewport.width, `overflow at ${size.width}px: ${JSON.stringify(viewport)}`);

      if (size.width < 900) await page.getByRole('button', { name: 'Open menu' }).click();
      await navigation.getByRole('button', { name: 'New chat', exact: true }).hover();
      const links = navigation.locator('.sidebar-links');
      const linksBefore = await links.boundingBox();
      const listBounds = await chats.boundingBox();
      assert.ok(listBounds.y + listBounds.height <= linksBefore.y, `chats overlap navigation at ${size.width}px`);
      assert.ok(linksBefore.y + linksBefore.height <= size.height, `navigation is offscreen at ${size.width}px`);
      await chats.getByRole('button').last().scrollIntoViewIfNeeded();
      assert.equal((await links.boundingBox()).y, linksBefore.y, 'scrolling chats keeps navigation in place');
      for (const button of await links.getByRole('button').all()) {
        assert.equal(await button.evaluate((el) => {
          const rect = el.getBoundingClientRect();
          return el.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
        }), true, `${await button.innerText()} is covered at ${size.width}px`);
      }
      await chats.getByRole('button').last().click();
      if (size.width < 900) await page.getByRole('button', { name: 'Open menu' }).click();
      await openSidebar(page, /^Sources/);
      await page.getByRole('heading', { name: 'Your sources' }).waitFor();
      await page.keyboard.press('Escape');
    }
  } finally { await context.close(); }
});

test('current-revision document exports use the open revision id and suggested filename', async () => {
  const { context, page, downloadedArtifacts } = await setupPage({ importedRecord: documentRecord });
  try {
    const tempFile = path.join(os.tmpdir(), `notes-export-${Date.now()}.md`); tempFiles.push(tempFile);
    await writeFile(tempFile, '# fixture');
    await importArtifact(page, tempFile);
    assert.equal(await download(page, 'md'), 'notes-r1.md');
    assert.equal(await download(page, 'docx'), 'notes-r1.docx');
    assert.deepEqual(downloadedArtifacts, [
      { format: 'md', revisionId: 'doc-r1' },
      { format: 'docx', revisionId: 'doc-r1' },
    ]);
  } finally { await context.close(); }
});

test('browser edits a supported imported workbook cell by stable ID and keeps the original available', async () => {
  const { context, page, capturedRevisions } = await setupPage({ saveDelayMs: 450 });
  try {
    const tempFile = path.join(os.tmpdir(), `metrics-${Date.now()}.xlsx`); tempFiles.push(tempFile);
    await writeFile(tempFile, 'mock xlsx bytes');
    await importArtifact(page, tempFile);
    await page.getByLabel('Cell B1').fill('450');
    await page.getByLabel('Cell B2').fill('2026-01-05');
    await page.getByRole('tab', { name: 'Summary' }).click();
    await page.getByLabel('Cell B1').fill('900');
    await page.getByRole('button', { name: 'Save changes' }).click();
    assert.equal(await page.getByLabel('Cell B1').isDisabled(), true, 'workbook cells are frozen while the save is pending');
    assert.equal(await page.getByLabel('Title', { exact: true }).isDisabled(), true, 'workbook title is frozen while the save is pending');
    await revisionShown(page, 'wb-r2');
    assert.deepEqual(capturedRevisions[0].operations, [{
      type: 'set_cell', sheet_id: 'sheet-main', cell_id: 'cell-value',
      cell: { id: 'cell-value', row: 1, column: 2, value: 450, formula: null, style: {} },
    }, {
      type: 'set_cell', sheet_id: 'sheet-main', cell_id: 'cell-date',
      cell: { id: 'cell-date', row: 2, column: 2, value: '2026-01-05', value_type: 'date', formula: null, style: { number_format: 'yyyy-mm-dd' } },
    }, {
      type: 'set_cell', sheet_id: 'sheet-summary', cell_id: 'cell-value',
      cell: { id: 'cell-value', row: 1, column: 2, value: 900, formula: null, style: { bold: true } },
    }]);
    assert.equal(await page.getByLabel('Cell A1').inputValue(), 'Visits');
    assert.equal(await page.getByLabel('Cell B1').inputValue(), '450');
    assert.equal(await page.getByLabel('Cell B2').inputValue(), '2026-01-05');
    await page.getByRole('tab', { name: 'Summary' }).click();
    assert.equal(await page.getByLabel('Cell B1').inputValue(), '900');
    await page.getByRole('button', { name: 'Download', exact: true }).click();
    const originalDownload = page.waitForEvent('download');
    await page.getByRole('menuitem', { name: /Original file/ }).click();
    assert.equal((await originalDownload).suggestedFilename(), 'metrics.xlsx');
  } finally { await context.close(); }
});

test('imported document block edits preserve IDs, code blank lines, table pipes, multiline cells, and evidence', async () => {
  const { context, page, capturedRevisions } = await setupPage({ importedRecord: documentRecord, saveDelayMs: 450 });
  try {
    const tempFile = path.join(os.tmpdir(), `notes-${Date.now()}.md`); tempFiles.push(tempFile);
    await writeFile(tempFile, '# fixture');
    await importArtifact(page, tempFile);
    const code = page.getByLabel('Block code-42');
    assert.equal(await code.inputValue(), 'first line\n\nthird line');
    assert.equal(await page.getByLabel('Rows table-77').inputValue(), 'A|B\ttop line\nbottom line');
    await code.fill('revised code\n\nwith blank line');
    await page.getByText(/Edited · unverified — its source link will be removed/).waitFor();
    await page.getByRole('button', { name: 'Save changes' }).click();
    assert.equal(await code.isDisabled(), true, 'document blocks are frozen while the save is pending');
    assert.equal(await page.getByLabel('Title', { exact: true }).isDisabled(), true, 'document title is frozen while the save is pending');
    await revisionShown(page, 'doc-r2');
    const saved = capturedRevisions.at(-1).spec;
    assert.deepEqual(saved.blocks.map((block) => block.id), ['code-42', 'table-77', 'paragraph-9']);
    assert.equal(saved.blocks[0].text, 'revised code\n\nwith blank line');
    assert.equal(saved.blocks[1].rows[0][0], 'A|B');
    assert.equal(saved.blocks[1].rows[0][1], 'top line\nbottom line');
    assert.deepEqual(Object.keys(saved.evidence), ['paragraph-9']);
    assert.deepEqual(JSON.parse(saved.provenance.unverifiedElements), ['code-42']);
    assert.equal(saved.page_layout.margin_top, 1);
    await page.getByText(/Edited · unverified — no longer tied to a source/).waitFor();
    await page.getByText('Source: Linear Algebra Primer · page 5').waitFor();
  } finally { await context.close(); }
});

test('workbook title and cell edits publish together, and title-only changes create a revision', async () => {
  const { context, page, capturedRevisions } = await setupPage();
  try {
    const tempFile = path.join(os.tmpdir(), `metrics-title-${Date.now()}.xlsx`); tempFiles.push(tempFile);
    await writeFile(tempFile, 'mock xlsx bytes');
    await importArtifact(page, tempFile);
    await page.getByLabel('Title', { exact: true }).fill('Visits by term');
    await page.getByLabel('Cell B1').fill('450');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await revisionShown(page, 'wb-r2');
    const combined = capturedRevisions[0];
    assert.ok(combined.spec, 'title changes use a full spec revision with cell edits applied');
    assert.equal(combined.spec.title, 'Visits by term');
    assert.equal(combined.spec.sheets[0].cells.find((cell) => cell.id === 'cell-value').value, 450);
    assert.deepEqual(combined.spec.sheets.map((sheet) => sheet.id), ['sheet-main', 'sheet-summary']);
    assert.equal(combined.spec.sheets[0].cells[0].style.bold, true);
    assert.equal(combined.spec.sheets[1].cells[0].value, 20);

    await page.getByLabel('Title', { exact: true }).fill('Visits by semester');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await revisionShown(page, 'wb-r3');
    const titleOnly = capturedRevisions[1];
    assert.ok(titleOnly.spec, 'a title-only change creates a revision');
    assert.equal(titleOnly.spec.title, 'Visits by semester');
    assert.equal(titleOnly.spec.sheets[0].cells.find((cell) => cell.id === 'cell-value').value, 450);
    assert.equal(titleOnly.spec.sheets[0].cells[0].style.bold, true);
  } finally { await context.close(); }
});

test('an answer becomes a grounded artifact job in its chat, which opens the returned artifact', async () => {
  const { context, page, capturedRuns } = await setupPage();
  try {
    await searchOnly(page, ['Linear Algebra Primer']);
    await send(page, 'What is a basis?');
    await page.getByText('A basis spans the vector space.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Make a spreadsheet from this answer' }).click();
    const request = page.getByLabel('What should it contain?');
    assert.match(await request.inputValue(), /What is a basis\?/);
    await request.fill('Write a short study guide about eigenvectors.');
    await page.getByText('Grounded in 1 source, with each claim tied to a passage.').waitFor();
    await page.getByRole('button', { name: 'Create spreadsheet' }).click();
    const artifacts = page.getByRole('complementary', { name: 'Artifacts' });
    await artifacts.getByRole('button', { name: /Generated draft/ }).waitFor({ timeout: 6000 });
    assert.equal(capturedRuns[0].kind, 'workbook');
    assert.deepEqual(capturedRuns[0].inputs, { prompt: 'Write a short study guide about eigenvectors.', sourceIds: ['source-1'], mode: 'grounded' });
    assert.ok(typeof capturedRuns[0].idempotencyKey === 'string' && capturedRuns[0].idempotencyKey.length > 0);
    await artifacts.getByRole('button', { name: /Generated draft/ }).click();
    await revisionShown(page, 'rev-1');
    assert.equal(await page.getByLabel('Title', { exact: true }).inputValue(), 'Generated draft');
    await page.getByText('Drafted from your instructions — not checked against your sources.').waitFor();

    await page.getByRole('button', { name: 'Back to artifacts' }).click();
    await artifacts.getByRole('button', { name: 'New', exact: true }).click();
    await page.getByRole('checkbox', { name: /Use my sources/ }).uncheck();
    await page.getByText('Written from your instructions only — not checked against your sources.').waitFor();
  } finally { await context.close(); }
});

test('generation opens the pinned published revision even after the artifact head advances', async () => {
  const { context, page, openedRunRevisions } = await setupPage({ pinnedRunRevision: true });
  try {
    await createMenu(page, 'New document');
    await page.getByLabel('What should it contain?').fill('Draft a lesson note.');
    await page.getByRole('button', { name: 'Create document' }).click();
    const card = page.getByRole('complementary', { name: 'Artifacts' }).getByRole('button', { name: /Generated draft/ });
    await card.waitFor({ timeout: 6000 });
    await card.click();
    await revisionShown(page, 'published-r1');
    assert.ok(openedRunRevisions.length > 0 && openedRunRevisions.every((revision) => revision === 'published-r1'));
  } finally { await context.close(); }
});

test('dependent workbook generation sends typed columns, quota bounds, selected sources, and exact revision parent', async () => {
  const { context, page, capturedRuns } = await setupPage({ initialArtifacts: [{ artifactId: 'artifact-wb', revisionId: 'wb-r1', kind: 'workbook', title: 'Metrics' }] });
  try {
    await openSidebar(page, 'All artifacts');
    await page.getByRole('dialog').getByRole('button', { name: /Metrics/ }).click();
    await page.getByRole('button', { name: 'Ask for changes' }).click();
    await page.getByText('Advanced: plan it section by section').click();
    await page.getByText('Plan a document or workbook in sections').click();
    await page.getByRole('button', { name: 'Add worksheet' }).click();
    await page.getByLabel('Total workbook cells, including headers').fill('52');
    await page.getByLabel('What should change?').fill('Create a small visit summary.');
    await page.getByRole('button', { name: 'Make these changes' }).click();
    await page.getByRole('complementary', { name: 'Artifacts' }).getByRole('button', { name: /Generated draft/ }).waitFor({ timeout: 6000 });
    const body = capturedRuns[0];
    assert.equal(body.kind, 'workbook');
    assert.deepEqual(body.inputs.sourceIds, null);
    assert.equal(body.inputs.artifactId, 'artifact-wb');
    assert.equal(body.inputs.expectedRevisionId, 'wb-r1');
    assert.deepEqual(body.inputs.outputPlan, {
      kind: 'workbook', title: 'Metrics',
      sheets: [{ key: 'sheet-1', name: 'Data', columns: [{ key: 'item', label: 'Item', value_type: 'text' }, { key: 'value', label: 'Value', value_type: 'number' }], min_rows: 8, target_rows: 10, max_rows: 12 }, { key: 'sheet-2', name: 'Sheet 2', columns: [{ key: 'item', label: 'Item', value_type: 'text' }], min_rows: 8, target_rows: 10, max_rows: 12 }],
      max_total_cells: 52, max_output_tokens_per_unit: 1200, max_repairs_per_unit: 1, max_checkpoint_bytes: 4000000,
    });
  } finally { await context.close(); }
});

test('impossible dependent quotas are rejected locally and preserve the generation draft', async () => {
  const { context, page, capturedRuns } = await setupPage();
  try {
    await createMenu(page, 'New document');
    await page.getByText('Advanced: plan it section by section').click();
    await page.getByLabel('Plan a document or workbook in sections').check();
    await page.getByText(/Current allowance:/).waitFor();
    await page.getByLabel('Total document blocks, including heading').fill('2');
    await page.getByLabel('What should it contain?').fill('Write a short guide.');
    await page.getByRole('button', { name: 'Create document' }).click();
    await page.getByRole('alert').getByText(/blocks must include each section heading/).waitFor();
    assert.equal(capturedRuns.length, 0);
    assert.equal(await page.getByLabel('What should it contain?').inputValue(), 'Write a short guide.');
  } finally { await context.close(); }
});

test('stale artifact generation retains prompt and plan and waits for explicit head reload and resubmission', async () => {
  const { context, page, capturedRuns } = await setupPage({ initialArtifacts: [{ artifactId: 'artifact-wb', revisionId: 'wb-r1', kind: 'workbook', title: 'Metrics' }], runConflictOnce: true });
  try {
    await openSidebar(page, 'All artifacts');
    await page.getByRole('dialog').getByRole('button', { name: /Metrics/ }).click();
    await page.getByRole('button', { name: 'Ask for changes' }).click();
    await page.getByLabel('What should change?').fill('Refresh the metrics workbook.');
    await page.getByRole('button', { name: 'Make these changes' }).click();
    await page.getByRole('alert').getByText('Metrics changed since you opened it.').waitFor();
    assert.equal(capturedRuns.length, 1);
    assert.equal(await page.getByLabel('What should change?').inputValue(), 'Refresh the metrics workbook.');
    await page.getByRole('button', { name: 'Load latest version' }).click();
    await page.getByRole('alert').waitFor({ state: 'detached' });
    assert.equal(await page.getByLabel('What should change?').inputValue(), 'Refresh the metrics workbook.');
    assert.equal(capturedRuns.length, 1, 'head reload does not resubmit the generation job');
  } finally { await context.close(); }
});

test('generation polling recovers after a temporary HTTP failure', async () => {
  const { context, page, capturedRuns } = await setupPage({ failFirstRunPoll: true });
  try {
    await createMenu(page, 'New document');
    await page.getByLabel('What should it contain?').fill('Draft a worksheet about derivatives.');
    await page.getByRole('button', { name: 'Create document' }).click();
    await page.getByText(/Status check failed, retrying: .*temporary job status outage/).waitFor();
    await page.getByRole('complementary', { name: 'Artifacts' }).getByRole('button', { name: /Generated draft/ }).waitFor({ timeout: 6000 });
    assert.equal(capturedRuns.length, 1);
  } finally { await context.close(); }
});

test('native batches inspect unknown submission without retry, poll explicitly, reopen exact revision, and cancel explicitly', async () => {
  const state = await setupPage({ failBatchSubmitOnce: true });
  const { context, page } = state;
  try {
    await searchOnly(page, ['Linear Algebra Primer']);
    await openTool(page, 'Bulk drafts');
    await page.getByLabel('Batch prompt 1').fill('Create a short guide from the selected lecture notes.');
    await page.getByRole('button', { name: 'Submit batch' }).click();
    await page.getByText(/Submission outcome may be unknown/).waitFor();
    assert.equal(state.capturedBatchSubmissions.length, 1);
    const firstSubmission = state.capturedBatchSubmissions[0];
    assert.match(firstSubmission.batchId, /^[0-9a-f-]{36}$/i);
    assert.equal(firstSubmission.kind, 'document');
    assert.equal(firstSubmission.items.length, 1);
    assert.match(firstSubmission.items[0].itemId, /^[0-9a-f-]{36}$/i);
    assert.deepEqual(firstSubmission.items[0].inputs, { prompt: 'Create a short guide from the selected lecture notes.', mode: 'grounded', sourceIds: ['source-1'] });
    await page.getByText('Batch · Outcome unknown · 0/1', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Submit batch' }).isDisabled(), true);

    await page.reload();
    await openTool(page, 'Bulk drafts');
    await page.getByText('Batch · Outcome unknown · 0/1', { exact: true }).waitFor();
    assert.equal(state.capturedBatchSubmissions.length, 1, 'reopen performs reads only and does not resubmit an uncertain intent');
    await page.getByRole('button', { name: 'Poll provider status' }).click();
    await page.getByText('Selected batch · Completed · 1/1 complete · 0 failed', { exact: true }).waitFor();
    assert.deepEqual(state.capturedBatchPolls, [firstSubmission.batchId]);
    await page.getByRole('button', { name: 'Open published revision' }).click();
    await revisionShown(page, 'revision-published-1');
    assert.deepEqual(state.openedArtifactRevisions, ['revision-published-1']);

    await openTool(page, 'Bulk drafts');
    await page.getByLabel('Batch prompt 1').fill('Create another guide to cancel.');
    await page.getByLabel('Batch request mode').selectOption('authoring');
    await page.getByRole('button', { name: 'Submit batch' }).click();
    await page.getByText('Selected batch · In progress · 0/1 complete · 0 failed', { exact: true }).waitFor();
    const secondBatchId = state.capturedBatchSubmissions[1].batchId;
    assert.deepEqual(state.capturedBatchSubmissions[1].items[0].inputs, { prompt: 'Create another guide to cancel.', mode: 'authoring', sourceIds: [] });
    await page.getByRole('button', { name: 'Cancel batch' }).click();
    await page.getByText(/Remote provider usage may still incur charges; already published revisions are retained/).waitFor();
    assert.deepEqual(state.capturedBatchCancels, [secondBatchId]);
    await page.keyboard.press('Escape');
    await revisionShown(page, 'revision-published-1');
  } finally { await context.close(); }
});

test('browser exposes upload diagnostics and isolates HTML artifact preview', async () => {
  const { context, page } = await setupPage();
  try {
    await openSidebar(page, /^Sources/);
    const tempFile = path.join(os.tmpdir(), `broken-${Date.now()}.pdf`); tempFiles.push(tempFile);
    await writeFile(tempFile, 'mock pdf');
    await page.locator('input[type=file][aria-label="Choose a PDF, text, or Markdown source"]').setInputFiles(tempFile);
    await page.getByText('No readable pages were found.', { exact: true }).waitFor();
    await page.locator('input[type=file][aria-label="Choose a PDF, text, or Markdown source"]').setInputFiles({ name: 'oversize.pdf', mimeType: 'application/pdf', buffer: Buffer.from('large upload fixture') });
    await page.getByRole('alert').getByText('upload exceeds configured size limit').waitFor();
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    await createMenu(page, 'New web page');
    await page.getByRole('tab', { name: 'Start blank' }).click();
    await page.getByLabel('Title').fill('Browser draft');
    await page.getByLabel('HTML content').fill('<p>Safe preview</p>');
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await revisionShown(page, 'rev-1');
    await page.getByLabel('HTML content').fill('<p>Safe preview, edited</p>');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await page.getByRole('alert').getByText('Stale parent revision.').waitFor();
    assert.equal(await page.getByLabel('HTML content').inputValue(), '<p>Safe preview, edited</p>');
    await page.getByRole('button', { name: 'Preview' }).click();
    const frame = page.locator('iframe[title="Isolated artifact preview"]');
    await frame.waitFor();
    assert.equal(await frame.getAttribute('sandbox'), '');
    const csp = await frame.evaluate((el) => el.getAttribute('srcdoc') ?? '', undefined);
    assert.match(csp, /Content-Security-Policy/);
    assert.match(csp, /connect-src 'none'/);
    assert.match(csp, /script-src 'none'/);
    assert.match(csp, /worker-src 'none'/);
    assert.match(csp, /form-action 'none'/);
    assert.equal(await download(page, 'html'), 'browser-draft.html');

    await createMenu(page, 'New spreadsheet');
    await page.getByRole('tab', { name: 'Start blank' }).click();
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await page.getByRole('region', { name: 'Workbook editor' }).waitFor();
  } finally { await context.close(); }
});

test('local connection panel sends exact DTOs and keeps credentials transient and masked', async () => {
  const { context, page, capturedConnectionWrites } = await setupPage({ connectionAdmin: true });
  try {
    await openTool(page, 'Model connections');
    await page.getByLabel('Name', { exact: true }).fill('Local OpenAI');
    await page.locator('.connection-settings-panel form select').selectOption('openai');
    await page.getByLabel('Model ID').fill('gpt-test');
    await page.getByLabel('Provider credential').fill('super-secret-test-value');
    await page.getByRole('checkbox', { name: 'standard' }).check();
    await page.getByRole('checkbox', { name: 'institutional_corpus' }).check();
    await page.getByRole('button', { name: 'Create profile' }).click();
    await page.getByText(/current evaluation: not passed/).waitFor();
    assert.deepEqual(capturedConnectionWrites[0], {
      name: 'Local OpenAI', provider: 'openai', model_id: 'gpt-test', roles: ['standard'], capabilities: [],
      data_scopes: ['institutional_corpus'], base_url: null, account_id: null, provider_options: {}, enabled: true,
      priority: 100, secret: { api_key: 'super-secret-test-value' },
    });
    assert.equal(await page.getByLabel('Provider credential').inputValue(), '');
    assert.equal(await page.evaluate(() => [...Object.values(localStorage), ...Object.values(sessionStorage)].some((value) => /super-secret-test-value|second-secret-value/.test(value))), false);
    assert.equal(await page.getByText('super-secret-test-value', { exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Replace credential' }).click();
    await page.getByLabel(/Replace API key for/).fill('second-secret-value');
    await page.getByRole('button', { name: 'Replace credential' }).last().click();
    assert.deepEqual(capturedConnectionWrites[1], { expected_revision: 1, secret: { api_key: 'second-secret-value' } });
    assert.equal(capturedConnectionWrites.some((body) => 'eval_passed' in body), false);
    await page.keyboard.press('Escape');
    await openTool(page, 'Model connections');
    assert.equal(await page.getByLabel('Provider credential').inputValue(), '', 'closing the panel discards any typed credential');
  } finally { await context.close(); }
});

test('OIDC signed-out session hides workspace and navigates only to the fixed login route', async () => {
  const { context, page, sourcesRequested } = await setupPage({ initialSession: { mode: 'oidc', authenticated: false } });
  try {
    await page.getByRole('heading', { name: 'Sign in required' }).waitFor();
    assert.equal(await page.getByRole('navigation', { name: 'Chats and library' }).count(), 0);
    assert.equal(sourcesRequested, 0);
    const loginRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/auth/login');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    assert.equal(new URL((await loginRequest).url()).pathname, '/api/auth/login');
  } finally { await context.close(); }
});

test('authenticated OIDC logout clears the workspace before reloading the session', async () => {
  const { context, page } = await setupPage({ initialSession: { mode: 'oidc', authenticated: true, workspaceKey: 'private-1', displayName: 'Ada Student', email: 'ada@example.edu' } });
  try {
    await page.getByText('Ada Student', { exact: true }).waitFor();
    await openSidebar(page, /^Sources/);
    await page.getByText('Linear Algebra Primer', { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Sign out' }).click();
    await page.getByRole('heading', { name: 'Sign in required' }).waitFor();
    assert.equal(await page.getByRole('navigation', { name: 'Chats and library' }).count(), 0);
    assert.equal(await page.getByText('Linear Algebra Primer', { exact: true }).count(), 0);
  } finally { await context.close(); }
});

test('session check errors show a retry and recover without a blank workspace', async () => {
  const { context, page } = await setupPage({ failFirstSessionCheck: true });
  try {
    await page.getByRole('alert').getByText('session temporarily unavailable').waitFor();
    await page.getByRole('button', { name: 'Retry session check' }).click();
    await page.getByRole('navigation', { name: 'Chats and library' }).waitFor();
    await page.getByText('Local development', { exact: true }).waitFor();
  } finally { await context.close(); }
});

test('saved artifacts reload from the server list after page refresh', async () => {
  const { context, page } = await setupPage({ initialArtifacts: [{ artifactId: 'artifact-1', revisionId: 'rev-1', kind: 'document', title: 'Generated draft', status: 'ready' }] });
  try {
    await openSidebar(page, 'All artifacts');
    await page.getByRole('dialog').getByRole('button', { name: /Generated draft/ }).waitFor();
    await page.reload();
    await openSidebar(page, 'All artifacts');
    await page.getByRole('dialog').getByRole('button', { name: /Generated draft/ }).click();
    await page.waitForFunction(() => document.querySelector('.canvas-title')?.value === 'Generated draft');
  } finally { await context.close(); }
});

test('a protected API 401 clears the active workspace and returns to sign in', async () => {
  const { context, page } = await setupPage({ initialSession: { mode: 'oidc', authenticated: true, workspaceKey: 'private-1', displayName: 'Ada' }, unauthenticatedOnSources: true });
  try {
    await page.getByRole('heading', { name: 'Sign in required' }).waitFor();
    assert.equal(await page.getByRole('navigation', { name: 'Chats and library' }).count(), 0);
  } finally { await context.close(); }
});

test('human gap attestation is explicit, scope-limited, and retains draft across a stale revision', async () => {
  const gap = { gapId: 'gap-1', collectionId: 'collection-1', query: 'How do I register for MATH 221?',
    sourceIds: ['source-1'], state: 'open', revision: 1, occurrenceCount: 4, recurrenceCount: 2,
    successfulRecurrenceCount: 1, firstSeenAt: '2026-10-01T00:00:00Z', lastSeenAt: '2026-10-08T00:00:00Z', resolution: null };
  const { context, page, capturedGapResolutions } = await setupPage({ initialGaps: [gap], gapConflictOnce: true });
  try {
    await openTool(page, 'Unanswered questions');
    const details = page.locator('.gap-review-item').filter({ hasText: 'How do I register for MATH 221?' });
    await details.locator('summary').click();
    await page.getByRole('checkbox', { name: 'Linear Algebra Primer', exact: true }).check();
    assert.equal(await page.getByRole('checkbox', { name: 'Lab Safety Manual', exact: true }).count(), 0,
      'explicitly scoped gaps only offer sources from their recorded source scope');
    await page.getByLabel('Optional reviewer note').fill('The registration steps are in this source.');
    await page.getByRole('button', { name: 'Record human source attestation' }).click();
    await page.getByRole('alert').getByText('This gap changed').waitFor();
    assert.equal(await page.getByRole('checkbox', { name: 'Linear Algebra Primer', exact: true }).isChecked(), true);
    assert.equal(await page.getByLabel('Optional reviewer note').inputValue(), 'The registration steps are in this source.');
    assert.deepEqual(capturedGapResolutions[0], { id: 'gap-1', body: { expectedRevision: 1, sourceIds: ['source-1'], note: 'The registration steps are in this source.' } });
    await page.getByRole('button', { name: 'Record human source attestation' }).click();
    await page.getByText('Reviewer note: The registration steps are in this source.', { exact: true }).waitFor();
    assert.equal(capturedGapResolutions[1].body.expectedRevision, 2);
    assert.equal(await page.getByText('Human source attestation', { exact: true }).count() > 0, true);
    assert.equal(await page.getByText(/does not prove that a generated answer is factually correct/).count(), 1);
  } finally { await context.close(); }
});
