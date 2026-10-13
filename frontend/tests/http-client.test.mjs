import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';

const source = await (await import('node:fs/promises')).readFile(new URL('../src/api/http.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source.replace('import.meta.env.VITE_API_BASE_URL', "''"), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText;
const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(js).toString('base64');
const { HttpClient, ApiError } = await import(moduleUrl);

async function withStubbedFetch(handler, body) {
  const oldFetch = globalThis.fetch;
  globalThis.fetch = handler;
  try {
    return await body();
  } finally {
    globalThis.fetch = oldFetch;
  }
}

test('query preserves explicit empty source scope', async () => {
  let captured;
  const handler = async (input, init) => {
    captured = { input: String(input), init };
    return Response.json({ type: 'refusal', reason: 'No selected sources.' });
  };
  await withStubbedFetch(handler, async () => {
    const response = await new HttpClient().query({ query: 'requirements', sourceIds: [] });
    assert.equal(captured.input, '/api/query');
    assert.deepEqual(JSON.parse(captured.init.body), { query: 'requirements', sourceIds: [] });
    assert.equal(response.type, 'refusal');
  });
});

test('upload sends multipart data without overriding its boundary', async () => {
  let captured;
  const handler = async (input, init) => {
    captured = { input: String(input), init };
    return Response.json({ id: 'source-1', name: 'guide.pdf', locatorType: 'page', status: 'indexed', chunkCount: 2 });
  };
  await withStubbedFetch(handler, async () => {
    const file = new File(['PDF bytes'], 'guide.pdf', { type: 'application/pdf' });
    const source = await new HttpClient().uploadSource(file);
    assert.equal(captured.input, '/api/sources');
    assert.ok(captured.init.body instanceof FormData);
    assert.equal(captured.init.body.get('file').name, 'guide.pdf');
    assert.equal(captured.init.headers, undefined);
    assert.equal(source.status, 'indexed');
  });
});

test('recommendations encode the topic and API failures expose FastAPI detail', async () => {
  let url;
  const handler = async (input) => { url = String(input); return Response.json([]); };
  await withStubbedFetch(handler, async () => {
    assert.deepEqual(await new HttpClient().recommendations('linear algebra & calculus'), []);
    assert.equal(url, '/api/recommendations?query=linear+algebra+%26+calculus');
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'collection not found' }), { status: 404, headers: { 'content-type': 'application/json' } });
    await assert.rejects(() => new HttpClient().listSources(), /collection not found/);
  });
});

test('artifact feature errors surface the unsupported feature list', async () => {
  const handler = async () => new Response(JSON.stringify({
    detail: 'This file uses features that cannot be preserved safely',
    code: 'unsupported_feature',
    features: ['hyperlinks', 'drawing objects'],
  }), { status: 422, headers: { 'content-type': 'application/json' } });
  await withStubbedFetch(handler, async () => {
    const file = new File(['x'], 'bad.docx', { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
    await assert.rejects(
      () => new HttpClient().importArtifact(file),
      (error) => {
        assert.ok(error instanceof ApiError);
        assert.deepEqual(error.features, ['hyperlinks', 'drawing objects']);
        assert.match(error.message, /Unsupported: hyperlinks, drawing objects\./);
        return true;
      },
    );
  });
});

test('source diagnostics from the API remain available to the UI', async () => {
  const handler = async () => Response.json([{ id: 'source-2', name: 'bad.pdf', locatorType: 'page', status: 'failed', chunkCount: 0, errorMessage: 'No readable pages.', warnings: ['Page 2 OCR quality was low.'] }]);
  await withStubbedFetch(handler, async () => {
    const [source] = await new HttpClient().listSources();
    assert.equal(source.status, 'failed');
    assert.equal(source.errorMessage, 'No readable pages.');
    assert.deepEqual(source.warnings, ['Page 2 OCR quality was low.']);
  });
});

test('reindex posts the expected index revision and decodes the authoritative refreshed source', async () => {
  let captured;
  const handler = async (input, init) => {
    captured = { url: String(input), init };
    return Response.json({ id: 'source-2', name: 'guide.pdf', locatorType: 'page', status: 'indexed', chunkCount: 7, indexRevision: 4, errorMessage: null, warnings: [] });
  };
  await withStubbedFetch(handler, async () => {
    const result = await new HttpClient().reindexSource('source/id', 3);
    assert.equal(captured.url, '/api/sources/source%2Fid/reindex');
    assert.equal(captured.init.method, 'POST');
    assert.deepEqual(JSON.parse(captured.init.body), { expectedIndexRevision: 3 });
    assert.equal(result.indexRevision, 4);
    assert.equal(result.status, 'indexed');
  });
});

test('conversation requests preserve source-scope omission, CAS sequence, and fixed request identity', async () => {
  const calls = [];
  const summary = { conversationId: 'conv-1', title: 'Basis', lastSequence: 0, pendingRequestId: null, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' };
  const handler = async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    if (url === '/api/conversations' && init?.method === 'POST') return Response.json(summary, { status: 201 });
    if (url === '/api/conversations') return Response.json([summary]);
    if (url.includes('/abandon')) return Response.json({ type: 'abandoned', reason: 'Abandoned', conversationId: 'conv-1', sequence: 3 });
    if (url.includes('/turns')) return Response.json({ type: 'history', answer: 'Turn 1 (user):\nBasis?', turnReferences: [1], conversationId: 'conv-1', sequence: 2 });
    return Response.json({ ...summary, lastSequence: 1, turns: [] });
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    assert.deepEqual(await client.listConversations(), [summary]);
    await client.createConversation({ title: 'Basis' });
    await client.getConversation('conv-1', 4, 25);
    const result = await client.sendConversationTurn('conv-1', { query: 'Follow up', requestId: 'request-uuid', expectedSequence: 1, mode: 'advising' });
    await client.abandonConversationTurn('conv-1', 'request-uuid');
    assert.equal(calls[0].url, '/api/conversations');
    assert.deepEqual(JSON.parse(calls[1].init.body), { title: 'Basis' });
    assert.equal(calls[2].url, '/api/conversations/conv-1?after=4&limit=25');
    assert.equal(calls[3].url, '/api/conversations/conv-1/turns');
    assert.deepEqual(JSON.parse(calls[3].init.body), { query: 'Follow up', requestId: 'request-uuid', expectedSequence: 1, mode: 'advising' });
    assert.equal(calls[3].init.credentials, 'same-origin');
    assert.deepEqual(result.turnReferences, [1]);
    assert.equal(calls[4].url, '/api/conversations/conv-1/turns/request-uuid/abandon');
  });
});

test('summary listing is a storage-only owner-scoped GET', async () => {
  let captured;
  const checkpoint = { summaryId: 'summary-1', conversationId: 'conv/1', state: 'completed',
    expectedSequence: 8, candidateCoveredThrough: 4, coveredThrough: 4, fingerprint: 'a'.repeat(64),
    text: 'Saved checkpoint', referenceMetadata: [], failureCode: null, profileId: 'profile-1',
    profileRevision: 2, createdAt: '2026-10-01T00:00:00Z', updatedAt: '2026-10-01T00:00:00Z' };
  const handler = async (input, init) => {
    captured = { url: String(input), init };
    return Response.json([checkpoint]);
  };
  await withStubbedFetch(handler, async () => {
    assert.deepEqual(await new HttpClient().listConversationSummaries('conv/1'), [checkpoint]);
    assert.equal(captured.url, '/api/conversations/conv%2F1/summaries');
    assert.equal(captured.init.method, undefined);
    assert.equal(captured.init.credentials, 'same-origin');
  });
});

test('conversation stream submission and event reads are separate storage requests', async () => {
  const calls = [];
  const handler = async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.endsWith('/turns/stream')) return Response.json({ conversationId: 'conv-1', requestId: 'req-1', jobId: 'job-1', state: 'queued' }, { status: 202 });
    return Response.json({ events: [{ eventId: 2, type: 'provisional_delta', text: 'Unverified' }], nextEventId: 2, requestState: 'pending', jobState: 'running', done: false });
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    const request = { query: 'Follow up', requestId: 'req-1', expectedSequence: 2, mode: 'advising' };
    assert.deepEqual(await client.startConversationTurnStream('conv/1', request), { conversationId: 'conv-1', requestId: 'req-1', jobId: 'job-1', state: 'queued' });
    const page = await client.getConversationTurnEvents('conv/1', 'req-1', 1, 20);
    assert.equal(page.events[0].text, 'Unverified');
    assert.equal(calls[0].url, '/api/conversations/conv%2F1/turns/stream');
    assert.deepEqual(JSON.parse(calls[0].init.body), request);
    assert.equal(calls[1].url, '/api/conversations/conv%2F1/turns/req-1/events?afterEventId=1&limit=20');
    assert.equal(calls[1].init.method, undefined);
    assert.equal(calls[1].init.credentials, 'same-origin');
  });
});

test('gap queue uses an explicit scoped GET and resolution posts exact revision, sources, and note', async () => {
  const calls = [];
  const gap = { gapId: 'gap-1', collectionId: 'collection-1', query: 'How do I register?',
    sourceIds: ['source-1'], state: 'open', revision: 3, occurrenceCount: 4, recurrenceCount: 2,
    successfulRecurrenceCount: 1, firstSeenAt: '2026-10-01T00:00:00Z', lastSeenAt: '2026-10-02T00:00:00Z', resolution: null };
  const handler = async (input, init) => {
    calls.push({ url: String(input), init });
    return Response.json(calls.length === 1 ? { gaps: [gap], metrics: { repeatedGapObservations: 2, successfulRepeatedObservations: 1, recurrenceRate: 0.5 } } : { ...gap, state: 'resolved' });
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    assert.deepEqual((await client.listGaps('collection/id')).gaps, [gap]);
    await client.resolveGap('gap/1', { expectedRevision: 3, sourceIds: ['source-1'], note: 'Relevant chapter.' });
    assert.equal(calls[0].url, '/api/gaps?collectionId=collection%2Fid');
    assert.equal(calls[0].init.method, undefined);
    assert.equal(calls[1].url, '/api/gaps/gap%2F1/resolve');
    assert.equal(calls[1].init.method, 'POST');
    assert.deepEqual(JSON.parse(calls[1].init.body), { expectedRevision: 3, sourceIds: ['source-1'], note: 'Relevant chapter.' });
  });
});

test('source listings can be pinned to the exact collection displayed in gap review', async () => {
  let url;
  const handler = async (input) => { url = String(input); return Response.json([]); };
  await withStubbedFetch(handler, async () => {
    await new HttpClient().listSources('collection/id');
    assert.equal(url, '/api/sources?collection=collection%2Fid');
  });
});

test('native artifact revisions use compare-and-swap and original files remain separately downloadable', async () => {
  const calls = [];
  const handler = async (input, init) => {
    calls.push({ url: String(input), init });
    if (String(input).endsWith('/original')) return new Response('original bytes', { headers: { 'Content-Disposition': 'attachment; filename="source.docx"' } });
    if (String(input).includes('/events?')) return Response.json([]);
    return Response.json({ artifactId: 'a1', revisionId: 'r2', parentRevisionId: 'r1', kind: 'document', title: 'T', spec: { kind: 'document', title: 'T', blocks: [] } });
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    await client.saveArtifactRevision('a1', { expectedParentRevisionId: 'r1', draftId: 'draft-1', spec: { kind: 'document', title: 'T', blocks: [] } });
    await client.listArtifactRunEvents('job/1');
    const original = await client.downloadOriginalArtifact('a1');
    assert.equal(calls[0].url, '/api/artifacts/a1/revisions');
    assert.deepEqual(JSON.parse(calls[0].init.body), { expectedParentRevisionId: 'r1', draftId: 'draft-1', spec: { kind: 'document', title: 'T', blocks: [] } });
    assert.equal(calls[1].url, '/api/runs/job%2F1/events?afterEventId=0');
    assert.equal(calls[2].url, '/api/artifacts/a1/original');
    assert.equal(original.filename, 'source.docx');
    assert.equal(await original.blob.text(), 'original bytes');
  });
});

test('dependent run requests preserve strict plan fields and use explicit read-only operation limits', async () => {
  const calls = [];
  const handler = async (input, init = {}) => {
    calls.push({ path: String(input), method: init.method ?? 'GET', body: init.body });
    return Response.json(calls.at(-1).path === '/api/runs/limits'
      ? { maxCalls: 4, totalTokenBudget: 6000, perCallOutputCap: 2048 }
      : { runId: 'run-planned', state: 'queued' });
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    assert.deepEqual(await client.getArtifactRunLimits(), { maxCalls: 4, totalTokenBudget: 6000, perCallOutputCap: 2048 });
    const request = { kind: 'document', idempotencyKey: 'request-1', inputs: { prompt: 'write', sourceIds: ['source-1'], mode: 'grounded', artifactId: 'artifact-1', expectedRevisionId: 'revision-4', outputPlan: { kind: 'document', title: 'Plan', sections: [{ key: 'intro', title: 'Introduction', target_words: 300, min_words: 250, max_words: 350, target_blocks: 2, min_blocks: 1, max_blocks: 3 }], max_total_blocks: 4, max_output_tokens_per_unit: 1000, max_repairs_per_unit: 1, max_checkpoint_bytes: 4096 } } };
    await client.createArtifactRun(request);
    assert.deepEqual(calls.map(({ path, method }) => [path, method]), [['/api/runs/limits', 'GET'], ['/api/runs', 'POST']]);
    assert.deepEqual(JSON.parse(calls[1].body), request);
  });
});

test('native artifact batches use stable typed submission and explicit read, poll, and cancel routes', async () => {
  const calls = [];
  const handler = async (input, init) => {
    calls.push({ url: String(input), init });
    if (String(input) === '/api/artifact-batches') return Response.json([{ batchId: 'batch-1', state: 'in_progress', itemCount: 1, totalCount: 1, completedCount: 0, failedCount: 0, cancelRequested: false }]);
    return Response.json({ batchId: 'batch-1', state: 'in_progress', itemCount: 1, totalCount: 1, completedCount: 0, failedCount: 0, cancelRequested: false, items: [] });
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    const request = { batchId: 'batch-1', kind: 'document', items: [{ itemId: 'item-1', inputs: { prompt: 'Draft a guide', sourceIds: [], mode: 'authoring' } }] };
    await client.createArtifactBatch(request);
    await client.listArtifactBatches();
    await client.getArtifactBatch('batch-1');
    await client.pollArtifactBatch('batch-1');
    await client.cancelArtifactBatch('batch-1');
    assert.deepEqual(calls.map((call) => call.url), ['/api/artifact-batches', '/api/artifact-batches', '/api/artifact-batches/batch-1', '/api/artifact-batches/batch-1/poll', '/api/artifact-batches/batch-1/cancel']);
    assert.deepEqual(JSON.parse(calls[0].init.body), request);
    assert.equal(calls[1].init.method, undefined, 'list is a read-only GET');
    assert.equal(calls[1].init.body, undefined);
    assert.equal(calls[2].init.method, undefined, 'detail is a read-only GET');
    assert.equal(calls[2].init.body, undefined);
    assert.equal(calls[3].init.method, 'POST');
    assert.equal(calls[4].init.method, 'POST');
  });
});

test('session, logout, and persistent artifact requests use same-origin cookies', async () => {
  const calls = [];
  const handler = async (input, init) => {
    calls.push({ url: String(input), init });
    if (String(input) === '/api/auth/logout') return new Response(null, { status: 204 });
    if (String(input) === '/api/session') return Response.json({ mode: 'oidc', authenticated: true, workspaceKey: 'workspace-1', displayName: 'Ada' });
    return Response.json([{ artifactId: 'artifact-1', revisionId: 'rev-1', kind: 'document', title: 'Study guide' }]);
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    assert.deepEqual(await client.getSession(), { mode: 'oidc', authenticated: true, workspaceKey: 'workspace-1', displayName: 'Ada' });
    await client.logout();
    assert.equal((await client.listArtifacts())[0].title, 'Study guide');
    assert.deepEqual(calls.map(({ url, init }) => [url, init.credentials]), [
      ['/api/session', 'same-origin'], ['/api/auth/logout', 'same-origin'], ['/api/artifacts', 'same-origin'],
    ]);
    assert.equal(calls[1].init.method, 'POST');
  });
});
