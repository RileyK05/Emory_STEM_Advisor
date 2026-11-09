import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import { fileURLToPath } from 'node:url';

const source = await (
  await import('node:fs/promises')
).readFile(new URL('../src/api/http.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source.replace('import.meta.env.VITE_API_BASE_URL', "''"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const moduleUrl = 'data:text/javascript;base64,' + Buffer.from(js).toString('base64');
const { HttpClient, ApiError, setDebugSessionToken } = await import(moduleUrl);

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
    return Response.json({ type: 'answer', answer: 'No selected sources.', citations: [] });
  };
  await withStubbedFetch(handler, async () => {
    const response = await new HttpClient().query({ query: 'requirements', sourceIds: [] });
    assert.equal(captured.input, '/api/query');
    assert.deepEqual(JSON.parse(captured.init.body), { query: 'requirements', sourceIds: [] });
    assert.equal(response.type, 'answer');
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

test('a privacy export download carries its own token beside the session cookie', async () => {
  const sent = [];
  const handler = async (_input, init) => {
    sent.push(new Headers(init.headers));
    return Response.json({});
  };
  await withStubbedFetch(handler, async () => {
    await new HttpClient().downloadPrivacyExport('export-1', 'once-token');
  });
  assert.equal(sent[0].get('X-Export-Token'), 'once-token');
  assert.equal(sent[0].get('Content-Type'), 'application/json');
});

test('API failures expose FastAPI detail', async () => {
  await withStubbedFetch(
    async () =>
      new Response(JSON.stringify({ detail: 'collection not found' }), {
        status: 404,
        headers: { 'content-type': 'application/json' },
      }),
    async () => {
      await assert.rejects(() => new HttpClient().listSources(), /collection not found/);
    },
  );
});

test('a proxy 5xx with the backend stopped reports the unreachable message', async () => {
  const handler = async () => new Response('', { status: 500, statusText: 'Internal Server Error' });
  await withStubbedFetch(handler, async () => {
    await assert.rejects(() => new HttpClient().getSession(), /Could not reach the backend API/);
  });
});

test('an application 5xx with a body keeps the backend detail', async () => {
  const handler = async () => Response.json({ detail: 'boom' }, { status: 500 });
  await withStubbedFetch(handler, async () => {
    await assert.rejects(() => new HttpClient().getSession(), /boom/);
  });
});

test('source diagnostics from the API remain available to the UI', async () => {
  const handler = async () =>
    Response.json([
      {
        id: 'source-2',
        name: 'bad.pdf',
        locatorType: 'page',
        status: 'failed',
        chunkCount: 0,
        errorMessage: 'No readable pages.',
        warnings: ['Page 2 OCR quality was low.'],
      },
    ]);
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
    return Response.json({
      id: 'source-2',
      name: 'guide.pdf',
      locatorType: 'page',
      status: 'indexed',
      chunkCount: 7,
      indexRevision: 4,
      errorMessage: null,
      warnings: [],
    });
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

test('conversation requests use owner-scoped routes and an explicit abandon', async () => {
  const calls = [];
  const summary = {
    conversationId: 'conv-1',
    title: 'Basis',
    lastSequence: 0,
    pendingRequestId: null,
    createdAt: '2026-01-01T00:00:00Z',
    updatedAt: '2026-01-01T00:00:00Z',
  };
  const handler = async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    if (url === '/api/conversations' && init?.method === 'POST') return Response.json(summary, { status: 201 });
    if (url === '/api/conversations') return Response.json([summary]);
    if (url.includes('/abandon'))
      return Response.json({ type: 'abandoned', reason: 'Abandoned', conversationId: 'conv-1', sequence: 3 });
    if (url.endsWith('/archive') || url.endsWith('/restore')) return Response.json(summary);
    return Response.json({ ...summary, lastSequence: 1, turns: [] });
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    assert.deepEqual(await client.listConversations(), [summary]);
    await client.createConversation({ title: 'Basis' });
    await client.getConversation('conv-1', 4, 25);
    await client.abandonConversationTurn('conv-1', 'request-uuid');
    await client.archiveConversation('conv-1');
    await client.restoreConversation('conv-1');
    assert.equal(calls[0].url, '/api/conversations');
    assert.deepEqual(JSON.parse(calls[1].init.body), { title: 'Basis' });
    assert.equal(calls[2].url, '/api/conversations/conv-1?after=4&limit=25');
    assert.equal(calls[3].url, '/api/conversations/conv-1/turns/request-uuid/abandon');
    assert.equal(calls[3].init.credentials, 'same-origin');
    assert.equal(calls[4].url, '/api/conversations/conv-1/archive');
    assert.equal(calls[4].init.method, 'POST');
    assert.equal(calls[5].url, '/api/conversations/conv-1/restore');
  });
});

test('conversation stream submission and event reads are separate storage requests', async () => {
  const calls = [];
  const handler = async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    if (url.endsWith('/turns/stream'))
      return Response.json(
        { conversationId: 'conv-1', requestId: 'req-1', jobId: 'job-1', state: 'queued' },
        { status: 202 },
      );
    return Response.json({
      events: [{ eventId: 2, type: 'stage', stage: 'researching' }],
      nextEventId: 2,
      requestState: 'pending',
      jobState: 'running',
      done: false,
    });
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    const request = { query: 'Follow up', requestId: 'req-1', expectedSequence: 2 };
    assert.deepEqual(await client.startConversationTurnStream('conv/1', request), {
      conversationId: 'conv-1',
      requestId: 'req-1',
      jobId: 'job-1',
      state: 'queued',
    });
    const page = await client.getConversationTurnEvents('conv/1', 'req-1', 1, 20);
    assert.equal(page.events[0].stage, 'researching');
    assert.equal(calls[0].url, '/api/conversations/conv%2F1/turns/stream');
    assert.deepEqual(JSON.parse(calls[0].init.body), request);
    assert.equal(calls[1].url, '/api/conversations/conv%2F1/turns/req-1/events?afterEventId=1&limit=20');
    assert.equal(calls[1].init.method, undefined);
    assert.equal(calls[1].init.credentials, 'same-origin');
  });
});

test('gap queue uses an explicit scoped GET and resolution posts exact revision, sources, and note', async () => {
  const calls = [];
  const gap = {
    gapId: 'gap-1',
    collectionId: 'collection-1',
    query: 'How do I register?',
    sourceIds: ['source-1'],
    state: 'open',
    revision: 3,
    occurrenceCount: 4,
    recurrenceCount: 2,
    successfulRecurrenceCount: 1,
    firstSeenAt: '2026-10-01T00:00:00Z',
    lastSeenAt: '2026-10-02T00:00:00Z',
    resolution: null,
  };
  const handler = async (input, init) => {
    calls.push({ url: String(input), init });
    return Response.json(
      calls.length === 1
        ? {
            gaps: [gap],
            metrics: { repeatedGapObservations: 2, successfulRepeatedObservations: 1, recurrenceRate: 0.5 },
          }
        : { ...gap, state: 'resolved' },
    );
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    assert.deepEqual((await client.listGaps('collection/id')).gaps, [gap]);
    await client.resolveGap('gap/1', { expectedRevision: 3, sourceIds: ['source-1'], note: 'Relevant chapter.' });
    assert.equal(calls[0].url, '/api/gaps?collectionId=collection%2Fid');
    assert.equal(calls[0].init.method, undefined);
    assert.equal(calls[1].url, '/api/gaps/gap%2F1/resolve');
    assert.equal(calls[1].init.method, 'POST');
    assert.deepEqual(JSON.parse(calls[1].init.body), {
      expectedRevision: 3,
      sourceIds: ['source-1'],
      note: 'Relevant chapter.',
    });
  });
});

test('source listings can be pinned to the exact collection displayed in gap review', async () => {
  let url;
  const handler = async (input) => {
    url = String(input);
    return Response.json([]);
  };
  await withStubbedFetch(handler, async () => {
    await new HttpClient().listSources('collection/id');
    assert.equal(url, '/api/sources?collection=collection%2Fid');
  });
});

test('run events read through the run route', async () => {
  const calls = [];
  const handler = async (input, init) => {
    calls.push({ url: String(input), init });
    if (String(input).includes('/events?')) return Response.json([]);
    return Response.json({});
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    await client.listArtifactRunEvents('job/1');
    assert.equal(calls[0].url, '/api/runs/job%2F1/events?afterEventId=0');
  });
});

test('session, logout, and persistent artifact requests use same-origin cookies', async () => {
  const calls = [];
  const handler = async (input, init) => {
    calls.push({ url: String(input), init });
    if (String(input) === '/api/auth/logout') return new Response(null, { status: 204 });
    if (String(input) === '/api/session')
      return Response.json({ mode: 'oidc', authenticated: true, workspaceKey: 'workspace-1', displayName: 'Ada' });
    return Response.json([{ artifactId: 'artifact-1', revisionId: 'rev-1', kind: 'document', title: 'Study guide' }]);
  };
  await withStubbedFetch(handler, async () => {
    const client = new HttpClient();
    assert.deepEqual(await client.getSession(), {
      mode: 'oidc',
      authenticated: true,
      workspaceKey: 'workspace-1',
      displayName: 'Ada',
    });
    await client.logout();
    assert.equal((await client.listArtifacts())[0].title, 'Study guide');
    assert.deepEqual(
      calls.map(({ url, init }) => [url, init.credentials]),
      [
        ['/api/session', 'same-origin'],
        ['/api/auth/logout', 'same-origin'],
        ['/api/artifacts', 'same-origin'],
      ],
    );
    assert.equal(calls[1].init.method, 'POST');
  });
});
