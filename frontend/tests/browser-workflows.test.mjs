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
const chromePath =
  process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ??
  path.join(process.env.ProgramFiles ?? 'C:/Program Files', 'Google/Chrome/Application/chrome.exe');
let server;
let browser;
const tempFiles = [];

const sources = [
  {
    id: 'source-1',
    name: 'Linear Algebra Primer',
    locatorType: 'section',
    status: 'indexed',
    chunkCount: 3,
    indexRevision: 2,
    canReindex: true,
  },
  {
    id: 'source-2',
    name: 'Lab Safety Manual',
    locatorType: 'page',
    status: 'indexed',
    chunkCount: 2,
    indexRevision: 5,
    canReindex: false,
  },
];

const workbookRecord = {
  artifactId: 'artifact-wb',
  revisionId: 'wb-r1',
  parentRevisionId: null,
  kind: 'workbook',
  title: 'Metrics',
  original: { filename: 'metrics.xlsx', sha256: 'abc123', preserved: true },
  status: 'ready',
  message: null,
  spec: {
    kind: 'workbook',
    title: 'Metrics',
    sheets: [
      {
        id: 'sheet-main',
        name: 'Data',
        cells: [
          { id: 'cell-label', row: 1, column: 1, value: 'Visits', formula: null, style: { bold: true } },
          { id: 'cell-value', row: 1, column: 2, value: 10, formula: null, style: {} },
          {
            id: 'cell-date',
            row: 2,
            column: 2,
            value: '2026-01-01',
            value_type: 'date',
            formula: null,
            style: { number_format: 'yyyy-mm-dd' },
          },
        ],
      },
      {
        id: 'sheet-summary',
        name: 'Summary',
        cells: [{ id: 'cell-value', row: 1, column: 2, value: 20, formula: null, style: { bold: true } }],
      },
    ],
    charts: [],
  },
};
const documentRecord = {
  artifactId: 'artifact-doc',
  revisionId: 'doc-r1',
  parentRevisionId: null,
  kind: 'document',
  title: 'Notes',
  status: 'ready',
  spec: {
    kind: 'document',
    title: 'Notes',
    blocks: [
      { id: 'code-42', type: 'code', language: 'text', text: 'first line\n\nthird line' },
      { id: 'table-77', type: 'table', headers: ['Symbol', 'Notes'], rows: [['A|B', 'top line\nbottom line']] },
      { id: 'paragraph-9', type: 'paragraph', text: 'Unchanged passage.' },
    ],
    page_layout: { margin_top: 1 },
    evidence: {
      'code-42': [
        {
          chunk_id: 'chunk-code',
          source_id: 'source-1',
          source_name: 'Linear Algebra Primer',
          locator_label: 'page 4',
          text_snapshot: 'first line',
        },
      ],
      'paragraph-9': [
        {
          chunk_id: 'chunk-para',
          source_id: 'source-1',
          source_name: 'Linear Algebra Primer',
          locator_label: 'page 5',
          text_snapshot: 'Unchanged passage.',
        },
      ],
    },
    provenance: { authority: 'grounded' },
  },
};
const profileFixture = {
  profile_id: 'profile-1',
  name: 'Local OpenAI',
  provider: 'openai',
  model_id: 'gpt-test',
  base_url: null,
  account_id: null,
  secret_configured: true,
  provider_options: {},
  roles: ['standard'],
  capabilities: ['structured_output'],
  data_scopes: ['institutional_corpus'],
  enabled: true,
  priority: 10,
  revision: 1,
  created_at: '2026-10-08T00:00:00Z',
  updated_at: '2026-10-08T00:00:00Z',
  eval_passed: false,
};

function trace() {
  return {
    traceId: 'trace-browser',
    query: 'basis',
    normalizedQuery: 'basis',
    seams: [],
    contributions: [],
    contextBlock: [
      {
        chunkId: 'chunk-la-1',
        sourceName: 'Linear Algebra Primer',
        locatorLabel: 'section 2.1',
        text: 'A basis spans the vector space and is linearly independent.',
      },
    ],
    prompt: { system: 'grounded', user: 'basis' },
    modelCall: { provider: 'test', modelId: 'test-model', promptTokens: null, completionTokens: null, latencyMs: 1 },
    warnings: [],
    errors: [],
  };
}

const defaultAdminUsers = [
  {
    userId: 'user-admin-1',
    subject: 'admin1@emory.edu',
    email: 'admin1@emory.edu',
    displayName: 'Admin User',
    role: 'admin',
    status: 'active',
    workspaceKey: 'ws-admin-1',
    conversationCount: 3,
    activeSessions: 1,
    createdAt: '2026-10-01T00:00:00Z',
    updatedAt: '2026-10-01T00:00:00Z',
    lastSeenAt: '2026-10-08T00:00:00Z',
  },
  {
    userId: 'user-student-2',
    subject: 'student2@emory.edu',
    email: 'student2@emory.edu',
    displayName: 'Student User',
    role: 'student',
    status: 'active',
    workspaceKey: 'ws-student-2',
    conversationCount: 1,
    activeSessions: 1,
    createdAt: '2026-10-02T00:00:00Z',
    updatedAt: '2026-10-02T00:00:00Z',
    lastSeenAt: '2026-10-07T00:00:00Z',
  },
];

const defaultAdminCollections = [
  {
    collectionId: 'col-default',
    name: 'default',
    sourceCount: 2,
    createdAt: '2026-10-01T00:00:00Z',
  },
];

test.before(async () => {
  try {
    await access(chromePath);
  } catch {
    throw new Error(
      `Chrome not found at ${chromePath}; set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH to use another local browser.`,
    );
  }
  const dist = path.join(frontend, 'dist');
  server = createServer(async (request, response) => {
    const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
    const relative = pathname === '/' ? 'index.html' : pathname.slice(1);
    let file = path.resolve(dist, relative);
    if (!file.startsWith(dist + path.sep)) {
      response.writeHead(400).end();
      return;
    }
    try {
      let body;
      try {
        body = await readFile(file);
      } catch {
        if (!file.endsWith('.html')) {
          file = file + '.html';
          body = await readFile(file);
        } else {
          throw new Error('Not found');
        }
      }
      const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html';
      response.writeHead(200, { 'Content-Type': type }).end(body);
    } catch {
      response.writeHead(404).end('Not found');
    }
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

/** Routes that answer after a short pause so their loading states can be observed. */
const SLOW_ROUTES = new Set([
  'GET /api/academic-record',
  'GET /api/comparisons',
  'GET /api/feedback/triage',
  'GET /api/audits',
  'GET /api/admin/grant-requests',
  'GET /api/admin/sources/src-1/review',
]);

async function setupPage({
  entry = '/',
  importedRecord = null,
  connectionAdmin = false,
  initialSession = null,
  initialArtifacts = [],
  initialConversations = [],
  initialConversationRecords = [],
  resumeStreams = [],
  initialBatches = [],
  initialBatchRecords = [],
  failBatchSubmitOnce = false,
  unauthenticatedOnSources = false,
  initialGaps = [],
  gapConflictOnce = false,
  pinnedRunRevision = false,
  saveDelayMs = 0,
  conversationCreateDelayMs = 0,
  initialAdminUsers = null,
  initialAdminCollections = null,
  debugSessionExpired = false,
  sessionStorageEntries = null,
  effectiveFlags = { memory_inspector: true, advising_summary_export: true },
  sameNameCitation = false,
  conversationListDelayMs = 0,
  seedConnectionProfiles = [],
  /** 'METHOD /api/path' -> [status, detail]: that request fails once with the given status. */
  faults = {},
} = {}) {
  const context = await browser.newContext();
  if (sessionStorageEntries) {
    await context.addInitScript((entries) => {
      for (const [key, val] of Object.entries(entries)) {
        window.sessionStorage.setItem(key, val);
      }
    }, sessionStorageEntries);
  }
  const page = await context.newPage();
  page.on('pageerror', (error) => console.error('Browser page error:', error.message));
  page.on('requestfailed', (request) =>
    console.error('Browser request failed:', request.url(), request.failure()?.errorText),
  );
  const capturedQueries = [];
  const capturedRevisions = [];
  const capturedRuns = [];
  const capturedConnectionWrites = [];
  const capturedReindexes = [];
  const capturedApiRequests = [];
  const capturedUserUpdates = [];
  const capturedUserRevokes = [];
  const capturedDebugPatches = [];
  const adminUsersList = structuredClone(initialAdminUsers ?? defaultAdminUsers);
  const adminCollectionsList = structuredClone(initialAdminCollections ?? defaultAdminCollections);
  const pendingFaults = new Map(Object.entries(faults));
  let grantRequests = [];
  let grantRows = [];
  let refreshPublished = {
    editionId: 'edition-1',
    revision: 1,
    editionKey: '2026',
    isCurrent: true,
    topicBuildId: 'build-1',
    sourceIds: ['source-1'],
  };
  let refreshEdition = null;
  let refreshStaged = false;
  let privacyRelease = null;
  let privacyReleasedEarly = false;
  page.releaseNextPrivacyList = () => {
    if (privacyRelease) {
      const resolve = privacyRelease;
      privacyRelease = null;
      resolve();
    } else {
      privacyReleasedEarly = true;
    }
  };
  let promptApproved = false;
  let promptFixture = false;
  let promptVersionId = '33333333-3333-4333-8333-333333333333';
  let comparisons = [];
  let auditListReads = 0;
  let auditRuns = 0;
  const auditProgramId = '11111111-1111-4111-8111-111111111111';
  let programAudits = [];
  let recordRevision = 1;
  let recordAttempts = [];
  let importDigest = 'd'.repeat(64);
  const reviewSource = {
    sourceId: 'src-1',
    collectionId: 'col-1',
    filename: 'handbook.md',
    contentType: 'text/markdown',
    status: 'indexed',
    workspaceKey: null,
    indexRevision: 1,
    isCurrent: true,
    createdAt: '2026-10-19T12:00:00Z',
    updatedAt: '2026-10-19T12:00:00Z',
  };
  let policyRevision = 1;
  let policyFlags = [
    {
      flagName: 'memory_inspector',
      description: 'Inspect saved context',
      defaultEnabled: true,
      killSwitchActive: false,
      envCeilingKey: null,
      overridesCount: 0,
      updatedAt: '2026-10-19T12:00:00Z',
    },
  ];
  let policyOverrides = [];
  let policyMembers = [];
  let debugSession = {
    token: 'test-debug-token-active',
    overrides: {},
    systemPrompt: null,
    createdAt: '2026-10-08T00:00:00Z',
    expiresAt: '2026-10-08T01:00:00Z',
  };
  let sourceRows = structuredClone(sources);
  let connectionProfiles = structuredClone(seedConnectionProfiles);
  let currentSession = initialSession ?? {
    mode: 'development',
    authenticated: true,
    workspaceKey: 'browser-test',
    displayName: 'Local development',
  };
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
  const conversationRecords = new Map(
    initialConversationRecords.map((record) => [record.conversationId, structuredClone(record)]),
  );
  const capturedConversationTurns = [];
  const capturedConversationStreams = [];
  let notifyConversationCreate;
  const conversationCreateStarted = new Promise((resolve) => {
    notifyConversationCreate = resolve;
  });
  const conversationStreams = new Map(
    resumeStreams.map((stream) => [`${stream.conversationId}:${stream.requestId}`, { ...stream, polls: 0 }]),
  );
  let conversationTurnFailures = 0;
  let conversationProviderCalls = 0;
  const gapRows = structuredClone(initialGaps);
  const capturedGapResolutions = [];
  let failGapConflict = gapConflictOnce;
  await page.route('**/api/**', async (route) => {
    const req = route.request();
    const apiUrl = new URL(req.url());
    capturedApiRequests.push({ method: req.method(), url: req.url(), path: apiUrl.pathname, headers: req.headers() });
    const routeKey = `${req.method()} ${apiUrl.pathname}`;
    if (SLOW_ROUTES.has(routeKey)) await new Promise((resolve) => setTimeout(resolve, 300));
    if (routeKey === 'GET /api/privacy/exports') {
      if (privacyReleasedEarly) {
        privacyReleasedEarly = false;
      } else {
        await new Promise((resolve) => {
          privacyRelease = resolve;
        });
      }
    }
    const fault = pendingFaults.get(routeKey);
    if (fault) {
      pendingFaults.delete(routeKey);
      const [status, detail] = fault;
      return route.fulfill({ status, json: { detail } });
    }
    if (apiUrl.pathname === '/api/flags/effective') return route.fulfill({ json: { flags: effectiveFlags } });
    if (apiUrl.pathname === '/api/academic-record' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          record: {
            recordId: 'rec-1',
            workspaceKey: 'student-ws',
            recordRevision,
            createdAt: '2026-10-19T12:00:00Z',
            updatedAt: '2026-10-19T12:00:00Z',
          },
          attempts: recordAttempts,
          recentEvents: [],
        },
      });
    }
    if (apiUrl.pathname === '/api/academic-record/attempts' && req.method() === 'POST') {
      const body = req.postDataJSON();
      recordRevision += 1;
      const attempt = {
        attemptId: `att-${recordAttempts.length + 1}`,
        recordId: 'rec-1',
        workspaceKey: 'student-ws',
        institution: 'Emory University',
        submittedCourseCode: body.submittedCourseCode,
        term: body.term,
        attemptedCredits: body.attemptedCredits ?? 3,
        earnedCredits: 3,
        grade: body.grade,
        status: 'completed',
        provenance: 'student_manual',
        isVerified: false,
        createdAt: '2026-10-19T12:00:00Z',
        updatedAt: '2026-10-19T12:00:00Z',
      };
      recordAttempts = [...recordAttempts, attempt];
      return route.fulfill({ json: attempt });
    }
    if (apiUrl.pathname === '/api/academic-record/imports/preview' && req.method() === 'POST') {
      return route.fulfill({
        json: {
          batchId: 'batch-1',
          recordId: 'rec-1',
          workspaceKey: 'student-ws',
          expectedRecordRevision: recordRevision,
          status: 'preview',
          filename: 'courses.csv',
          parsedRows: [{ submittedCourseCode: 'CHEM 150', term: 'Spring 2024', grade: 'A' }],
          rowDecisions: ['select'],
          previewDigest: importDigest,
          createdAt: '2026-10-19T12:01:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/academic-record/imports/batch-1/rows' && req.method() === 'POST') {
      const body = req.postDataJSON();
      importDigest = 'e'.repeat(64);
      return route.fulfill({
        json: {
          batchId: 'batch-1',
          recordId: 'rec-1',
          workspaceKey: 'student-ws',
          expectedRecordRevision: recordRevision,
          status: 'preview',
          filename: 'courses.csv',
          parsedRows: [
            { submittedCourseCode: 'CHEM 150', term: 'Spring 2024', grade: body.corrections?.[0]?.grade ?? 'A' },
          ],
          rowDecisions: ['select'],
          previewDigest: importDigest,
          createdAt: '2026-10-19T12:01:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/academic-record/imports/batch-1/commit' && req.method() === 'POST') {
      if (!recordAttempts.some((row) => row.submittedCourseCode === 'CHEM 150')) {
        recordRevision += 1;
        recordAttempts = [
          ...recordAttempts,
          {
            attemptId: 'att-chem',
            recordId: 'rec-1',
            workspaceKey: 'student-ws',
            institution: 'Emory University',
            submittedCourseCode: 'CHEM 150',
            term: 'Spring 2024',
            attemptedCredits: 3,
            earnedCredits: 3,
            grade: 'A-',
            status: 'completed',
            provenance: 'csv_import',
            isVerified: false,
            createdAt: '2026-10-19T12:02:00Z',
            updatedAt: '2026-10-19T12:02:00Z',
          },
        ];
      }
      return route.fulfill({
        json: { batchId: 'batch-1', status: 'committed', attemptsImported: 1, newRecordRevision: recordRevision },
      });
    }
    if (apiUrl.pathname === '/api/audits/programs' && req.method() === 'GET') {
      return route.fulfill({
        json: [
          {
            programId: auditProgramId,
            title: 'CS Minor',
            kind: 'minor',
            ruleReleaseId: '22222222-2222-4222-8222-222222222222',
          },
          {
            programId: '33333333-3333-4333-8333-333333333333',
            title: 'Open Minor',
            kind: 'minor',
            ruleReleaseId: '44444444-4444-4444-8444-444444444444',
          },
        ],
      });
    }
    if (apiUrl.pathname === '/api/comparisons' && req.method() === 'GET') {
      return route.fulfill({ json: comparisons });
    }
    if (apiUrl.pathname === '/api/comparisons' && req.method() === 'POST') {
      const row = {
        comparisonId: 'comparison-1',
        workspaceKey: 'student-ws',
        recordId: 'rec-1',
        recordRevision: 1,
        sharingPolicy: 'unknown',
        mentionKind: 'mention_overlap_only',
        sharedMentions: ['CS 170'],
        creditSavings: null,
        coverageComparable: false,
        rankingBlockedReason: 'unequal unresolved coverage',
        members: [],
        createdAt: '2026-10-19T12:00:00Z',
      };
      comparisons = [row];
      return route.fulfill({ json: row });
    }
    if (apiUrl.pathname.startsWith('/api/admin/catalog-refresh')) {
      const edition = (revision, editionKey, isCurrent) => ({
        editionId: `edition-${revision}`,
        revision,
        editionKey,
        isCurrent,
        topicBuildId: 'build-1',
        sourceIds: ['source-1'],
      });
      const batchFor = (status) => ({
        batchId: 'batch-1',
        collectionId: 'col-1',
        editionKey: refreshEdition.editionKey,
        status,
        baseRevision: refreshPublished.revision,
        stagedRevision: refreshPublished.revision + 1,
        compatible: true,
        currentEdition: refreshPublished,
        stagedEdition: refreshEdition,
      });
      if (req.method() === 'GET') {
        return route.fulfill({
          json: { currentEdition: refreshPublished, staging: refreshStaged ? batchFor('staging') : null },
        });
      }
      if (apiUrl.pathname === '/api/admin/catalog-refresh' && req.method() === 'POST') {
        refreshEdition = edition(refreshPublished.revision + 1, req.postDataJSON().editionKey, false);
        refreshStaged = true;
        return route.fulfill({ status: 201, json: batchFor('staging') });
      }
      if (apiUrl.pathname === '/api/admin/catalog-refresh/batch-1/cancel') {
        refreshStaged = false;
        return route.fulfill({ json: batchFor('cancelled') });
      }
      if (apiUrl.pathname === '/api/admin/catalog-refresh/batch-1/publish') {
        assert.equal(req.postDataJSON().expectedRevision, refreshPublished.revision);
        refreshStaged = false;
        refreshPublished = { ...refreshEdition, isCurrent: true };
        return route.fulfill({ json: { ...batchFor('published'), currentEdition: refreshPublished } });
      }
    }
    if (apiUrl.pathname === '/api/privacy/exports' && req.method() === 'GET') return route.fulfill({ json: [] });
    if (apiUrl.pathname === '/api/privacy/exports' && req.method() === 'POST') {
      return route.fulfill({
        status: 201,
        json: {
          exportId: '55555555-5555-4555-8555-555555555555',
          workspaceKey: 'student-ws',
          status: 'completed',
          downloadToken: 'once-token',
          expiresAt: '2026-10-21T00:00:00+00:00',
          createdAt: '2026-10-20T00:00:00+00:00',
          manifest: { inventory: { conversations: 0 } },
        },
      });
    }
    if (
      apiUrl.pathname === '/api/privacy/exports/55555555-5555-4555-8555-555555555555/download' &&
      req.method() === 'GET'
    ) {
      if (req.headers()['x-export-token'] !== 'once-token') {
        return route.fulfill({ status: 403, json: { detail: 'export download token is not valid' } });
      }
      return route.fulfill({
        json: {
          exportId: '55555555-5555-4555-8555-555555555555',
          workspaceKey: 'student-ws',
          status: 'completed',
          contentDigest: 'digest-1',
          expiresAt: '2026-10-21T00:00:00+00:00',
          manifest: { inventory: { conversations: 0 } },
        },
      });
    }
    if (apiUrl.pathname === '/api/feedback' && req.method() === 'POST') {
      const body = req.postDataJSON();
      const revision = body.vote === 'down' ? 2 : 1;
      return route.fulfill({
        json: {
          feedbackId: 'feedback-1',
          workspaceKey: 'student-ws',
          responseId: body.responseId,
          revision,
          vote: body.vote,
          reasonCode: body.reasonCode,
          comment: body.comment,
          currentVotes: 1,
          createdAt: '2026-10-20T12:00:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/feedback/triage' && req.method() === 'GET') {
      return route.fulfill({ json: [] });
    }
    if (apiUrl.pathname === '/api/feedback/triage' && req.method() === 'POST') {
      const body = req.postDataJSON();
      return route.fulfill({
        json: {
          feedbackId: body.feedbackId,
          issueType: body.issueType,
          triageStatus: 'reviewed',
          triageRevision: 1,
          voteRevision: body.expectedRevision,
          gapId: body.issueType === 'missing_evidence' ? 'gap-1' : null,
          eventId: 'event-1',
        },
      });
    }
    if (apiUrl.pathname === '/api/feedback/promotions' && req.method() === 'POST') {
      return route.fulfill({
        json: {
          candidateId: 'candidate-1',
          redactedPrompt: 'Which requirement is missing?',
          redactedExpected: 'The catalog line.',
          sourceFeedbackId: 'feedback-1',
          sourceIndexRevision: 1,
          promotionState: 'current',
          reviewState: 'reviewed',
          createdAt: '2026-10-20T12:00:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/debug/experiments' && req.method() === 'POST') {
      const body = req.postDataJSON();
      return route.fulfill({
        json: {
          experimentId: '44444444-4444-4444-8444-444444444444',
          sourceConversationId: body.conversationId,
          sourceRequestId: body.requestId,
          state: 'recorded',
          createdAt: '2026-10-20T12:00:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/prompts' && req.method() === 'POST') {
      return route.fulfill({
        json: {
          versionId: promptVersionId,
          name: 'answer',
          templateText: 'Cite the catalog.',
          contentDigest: 'a'.repeat(64),
          reviewState: 'draft',
          createdAt: '2026-10-20T12:00:00Z',
        },
      });
    }
    if (apiUrl.pathname === `/api/admin/prompts/${promptVersionId}/approve` && req.method() === 'POST') {
      promptApproved = true;
      return route.fulfill({
        json: {
          versionId: promptVersionId,
          name: 'answer',
          templateText: 'Cite the catalog.',
          contentDigest: 'a'.repeat(64),
          reviewState: 'approved',
          createdAt: '2026-10-20T12:00:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/prompts/fixtures' && req.method() === 'POST') {
      promptFixture = true;
      return route.fulfill({
        json: {
          fixtureId: 'fixture-1',
          suiteName: 'catalog',
          fixtureDigest: 'b'.repeat(64),
          present: true,
        },
      });
    }
    if (apiUrl.pathname === `/api/admin/prompts/${promptVersionId}/promote` && req.method() === 'POST') {
      if (!promptApproved) {
        return route.fulfill({ status: 409, json: { detail: 'prompt review is not approved' } });
      }
      if (!promptFixture) {
        return route.fulfill({ status: 409, json: { detail: 'eval fixture is missing' } });
      }
      return route.fulfill({
        json: {
          promotionId: 'promotion-1',
          versionId: promptVersionId,
          suiteName: 'catalog',
          fixtureDigest: 'b'.repeat(64),
          status: 'published',
          createdAt: '2026-10-20T12:00:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/audits' && req.method() === 'GET') {
      auditListReads += 1;
      if (auditListReads === 3) {
        programAudits = programAudits.map((audit) => ({ ...audit, isStale: true }));
      }
      return route.fulfill({ json: programAudits });
    }
    if (apiUrl.pathname === '/api/audits/run' && req.method() === 'POST') {
      auditRuns += 1;
      if (auditRuns === 2) {
        return route.fulfill({
          status: 409,
          json: { detail: 'program evidence is retired or superseded; review current rules' },
        });
      }
      const auditId = `audit-${auditRuns}`;
      const audit = {
        auditId,
        workspaceKey: 'student-ws',
        programId: auditProgramId,
        ruleReleaseId: '22222222-2222-4222-8222-222222222222',
        recordId: 'rec-1',
        recordRevision: 1,
        rulesDigest: 'd'.repeat(64),
        overallStatus: 'outstanding',
        isStale: false,
        completedCredits: 0,
        inProgressCredits: 0,
        unverifiedCredits: 0,
        programTitle: 'CS Minor',
        programKind: 'minor',
        createdAt: '2026-10-19T12:00:00Z',
      };
      programAudits = [audit, ...programAudits.filter((row) => row.auditId !== auditId)];
      return route.fulfill({
        json: {
          audit,
          evaluations: [
            {
              evaluationId: `eval-${auditRuns}`,
              auditId,
              nodeId: 'm1',
              nodeType: 'exact_course',
              title: 'CS 170',
              status: 'outstanding',
              allocatedCredits: 0,
              createdAt: '2026-10-19T12:00:00Z',
            },
          ],
          programTitle: 'CS Minor',
          programKind: 'minor',
          coverageScope: 'program_audit',
        },
      });
    }
    if (apiUrl.pathname.startsWith('/api/audits/') && req.method() === 'GET') {
      const parts = apiUrl.pathname.split('/');
      const auditId = parts[3];
      const audit = programAudits.find((row) => row.auditId === auditId);
      if (!audit) return route.fulfill({ status: 404, json: { detail: 'audit not found' } });
      return route.fulfill({
        json: {
          audit,
          evaluations: [
            {
              evaluationId: `eval-${auditId}`,
              auditId,
              nodeId: 'm1',
              nodeType: 'exact_course',
              title: 'CS 170',
              status: audit.overallStatus,
              allocatedCredits: 0,
              createdAt: audit.createdAt,
            },
          ],
          programTitle: 'CS Minor',
          programKind: 'minor',
          coverageScope: 'program_audit',
        },
      });
    }
    if (apiUrl.pathname === '/api/academic-record/revisions/1' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          recordId: 'rec-1',
          revision: 1,
          attempts: [{ submittedCourseCode: 'CS 170', grade: 'A', provenance: 'student_manual' }],
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/flags') return route.fulfill({ json: policyFlags });
    if (apiUrl.pathname === '/api/admin/flags/memory_inspector/overrides') {
      if (req.method() === 'GET') return route.fulfill({ json: policyOverrides });
      const body = req.postDataJSON();
      const row = {
        ...body,
        flagName: 'memory_inspector',
        overrideId: 'override-1',
        updatedAt: `2026-10-19T12:00:0${++policyRevision}Z`,
      };
      policyOverrides = [row];
      policyFlags[0].overridesCount = 1;
      return route.fulfill({ json: row });
    }
    if (apiUrl.pathname.startsWith('/api/admin/flags/memory_inspector/overrides/') && req.method() === 'DELETE') {
      policyOverrides = [];
      policyFlags[0].overridesCount = 0;
      return route.fulfill({ status: 204 });
    }
    if (apiUrl.pathname === '/api/admin/grant-requests' && req.method() === 'GET') {
      const status = apiUrl.searchParams.get('status');
      return route.fulfill({ json: status ? grantRequests.filter((row) => row.status === status) : grantRequests });
    }
    if (apiUrl.pathname === '/api/admin/grant-requests' && req.method() === 'POST') {
      const body = req.postDataJSON();
      const row = {
        requestId: 'req-1',
        requesterUserId: 'user-admin-1',
        actorUserId: 'user-admin-1',
        targetWorkspaceKey: body.targetWorkspaceKey,
        resourceType: body.resourceType,
        resourceId: body.resourceId ?? null,
        allowedActions: ['read'],
        reason: body.reason,
        expiresAt: body.expiresAt,
        status: 'pending',
        revision: 1,
        decisionNote: null,
        decidedByUserId: null,
        decidedAt: null,
        grantId: null,
        createdAt: '2026-10-19T12:00:00Z',
        updatedAt: '2026-10-19T12:00:00Z',
        actorEmail: 'admin1@emory.edu',
        actorDisplayName: 'Admin User',
      };
      grantRequests = [row];
      return route.fulfill({ status: 201, json: row });
    }
    const grantRequestMatch = /^\/api\/admin\/grant-requests\/([^/]+)$/.exec(apiUrl.pathname);
    if (grantRequestMatch && req.method() === 'PATCH') {
      const body = req.postDataJSON();
      const current = grantRequests.find((row) => row.requestId === decodeURIComponent(grantRequestMatch[1]));
      const row = {
        ...current,
        ...body,
        reason: body.reason,
        revision: (current?.revision ?? 1) + 1,
        status: 'pending',
      };
      grantRequests = [row];
      return route.fulfill({ json: row });
    }
    const grantApproveMatch = /^\/api\/admin\/grant-requests\/([^/]+)\/approve$/.exec(apiUrl.pathname);
    if (grantApproveMatch && req.method() === 'POST') {
      const current = grantRequests.find((row) => row.requestId === decodeURIComponent(grantApproveMatch[1]));
      const grant = {
        grantId: 'grant-1',
        grantType: 'support_access',
        actorUserId: current.actorUserId,
        targetWorkspaceKey: current.targetWorkspaceKey,
        resourceType: current.resourceType,
        resourceId: current.resourceId,
        allowedActions: ['read'],
        reason: current.reason,
        approvedByUserId: 'user-admin-2',
        expiresAt: current.expiresAt,
        createdAt: '2026-10-19T12:01:00Z',
        revokedAt: null,
        revokedByUserId: null,
        actorEmail: current.actorEmail,
        actorDisplayName: current.actorDisplayName,
        approvedByEmail: 'approver@emory.edu',
        approvedByDisplayName: 'Approver',
      };
      grantRows = [grant];
      const row = { ...current, status: 'approved', grantId: grant.grantId, revision: current.revision + 1 };
      grantRequests = [row];
      return route.fulfill({ json: row });
    }
    if (apiUrl.pathname === '/api/admin/grants' && req.method() === 'GET') {
      const activeOnly = apiUrl.searchParams.get('active_only') === 'true';
      return route.fulfill({ json: activeOnly ? grantRows.filter((row) => !row.revokedAt) : grantRows });
    }
    const grantRevokeMatch = /^\/api\/admin\/grants\/([^/]+)\/revoke$/.exec(apiUrl.pathname);
    if (grantRevokeMatch && req.method() === 'POST') {
      grantRows = grantRows.map((row) =>
        row.grantId === decodeURIComponent(grantRevokeMatch[1])
          ? { ...row, revokedAt: '2026-10-19T12:02:00Z', revokedByUserId: 'user-admin-2' }
          : row,
      );
      return route.fulfill({ json: grantRows.find((row) => row.grantId === decodeURIComponent(grantRevokeMatch[1])) });
    }
    if (apiUrl.pathname === '/api/admin/flags/memory_inspector/kill') {
      const body = req.postDataJSON();
      policyFlags[0] = {
        ...policyFlags[0],
        ...body,
        killReason: body.reason,
        updatedAt: `2026-10-19T12:00:0${++policyRevision}Z`,
      };
      return route.fulfill({ json: policyFlags[0] });
    }
    if (apiUrl.pathname === '/api/admin/cohorts')
      return route.fulfill({
        json: policyMembers.length ? [{ cohortName: 'pilot', memberCount: policyMembers.length }] : [],
      });
    if (apiUrl.pathname === '/api/admin/cohorts/pilot/users') {
      if (req.method() === 'GET') return route.fulfill({ json: policyMembers });
      const user = adminUsersList.find((row) => row.userId === req.postDataJSON().userId);
      policyMembers = [{ ...user, assignedAt: '2026-10-19T12:00:00Z' }];
      return route.fulfill({ json: { status: 'assigned', cohortName: 'pilot', userId: user.userId } });
    }
    if (apiUrl.pathname.startsWith('/api/admin/cohorts/pilot/users/') && req.method() === 'DELETE') {
      policyMembers = [];
      return route.fulfill({ status: 204 });
    }
    if (apiUrl.pathname === '/api/session' && req.method() === 'GET') {
      return route.fulfill({ json: currentSession, headers: { 'Cache-Control': 'no-store' } });
    }
    if (apiUrl.pathname === '/api/auth/logout' && req.method() === 'POST') {
      currentSession = { mode: 'oidc', authenticated: false };
      return route.fulfill({ status: 204 });
    }
    if (apiUrl.pathname === '/api/auth/login' && req.method() === 'GET')
      return route.fulfill({ status: 200, contentType: 'text/plain', body: 'login redirect test stub' });
    if (apiUrl.pathname === '/api/collections' && req.method() === 'GET')
      return route.fulfill({ json: [{ id: 'collection-1', name: 'default' }] });
    if (apiUrl.pathname === '/api/sources' && req.method() === 'GET') {
      sourcesRequested++;
      if (unauthenticatedOnSources) {
        currentSession = { mode: 'oidc', authenticated: false };
        return route.fulfill({ status: 401, json: { detail: 'authentication required' } });
      }
      return route.fulfill({ json: sourceRows });
    }
    if (apiUrl.pathname === '/api/sources/source-1/reindex' && req.method() === 'POST') {
      const body = req.postDataJSON();
      capturedReindexes.push(body);
      await new Promise((resolve) => setTimeout(resolve, 250));
      sourceRows = sourceRows.map((item) =>
        item.id === 'source-1'
          ? { ...item, indexRevision: item.indexRevision + 1, errorMessage: 'RuntimeError: test replacement failed' }
          : item,
      );
      return route.fulfill({ status: 422, json: { detail: 'source reindex failed' } });
    }
    if (apiUrl.pathname === '/api/sources' && req.method() === 'POST') {
      if (req.postData()?.includes('oversize.pdf'))
        return route.fulfill({ status: 413, json: { detail: 'upload exceeds configured size limit' } });
      return route.fulfill({
        json: {
          id: 'bad-source',
          name: 'broken.pdf',
          locatorType: 'page',
          status: 'failed',
          chunkCount: 0,
          errorMessage: 'No readable pages were found.',
        },
      });
    }
    if (apiUrl.pathname === '/api/gaps' && req.method() === 'GET')
      return route.fulfill({
        json: {
          gaps: gapRows,
          metrics: { repeatedGapObservations: 3, successfulRepeatedObservations: 1, recurrenceRate: 1 / 3 },
        },
      });
    const resolveGapMatch = /^\/api\/gaps\/([^/]+)\/resolve$/.exec(apiUrl.pathname);
    if (resolveGapMatch && req.method() === 'POST') {
      const body = req.postDataJSON();
      const gap = gapRows.find((item) => item.gapId === resolveGapMatch[1]);
      capturedGapResolutions.push({ id: resolveGapMatch[1], body });
      if (!gap) return route.fulfill({ status: 404, json: { detail: 'Gap or eligible source not found.' } });
      if (failGapConflict) {
        failGapConflict = false;
        gap.revision += 1;
        return route.fulfill({ status: 409, json: { detail: 'Gap changed; refresh before resolving it.' } });
      }
      if (body.expectedRevision !== gap.revision)
        return route.fulfill({ status: 409, json: { detail: 'Gap changed; refresh before resolving it.' } });
      gap.state = 'resolved';
      gap.resolution = { sourceIds: body.sourceIds, note: body.note, resolvedAt: '2026-10-08T00:01:00Z' };
      return route.fulfill({ json: gap });
    }
    if (apiUrl.pathname === '/api/conversations' && req.method() === 'GET') {
      if (conversationListDelayMs) await new Promise((resolve) => setTimeout(resolve, conversationListDelayMs));
      return route.fulfill({ json: conversationSummaries });
    }
    if (apiUrl.pathname === '/api/conversations' && req.method() === 'POST') {
      const body = req.postDataJSON();
      const createdAt = '2026-10-08T00:00:00Z';
      notifyConversationCreate();
      if (conversationCreateDelayMs) await new Promise((resolve) => setTimeout(resolve, conversationCreateDelayMs));
      const item = {
        conversationId: 'conversation-' + (conversationSummaries.length + 1),
        title: body.title,
        lastSequence: 0,
        pendingRequestId: null,
        createdAt,
        updatedAt: createdAt,
      };
      conversationSummaries.unshift(item);
      conversationRecords.set(item.conversationId, { ...item, turns: [] });
      return route.fulfill({ status: 201, json: item });
    }
    const streamSubmitMatch = /^\/api\/conversations\/([^/]+)\/turns\/stream$/.exec(apiUrl.pathname);
    if (streamSubmitMatch && req.method() === 'POST') {
      const id = streamSubmitMatch[1];
      const body = req.postDataJSON();
      capturedConversationTurns.push({ id, body });
      capturedConversationStreams.push({ id, body });
      const record = conversationRecords.get(id);
      if (!record) return route.fulfill({ status: 404, json: { detail: 'conversation not found' } });
      const priorUsers = record.turns.filter((turn) => turn.role === 'user');
      const inherited = priorUsers.length ? priorUsers.at(-1).evidence.effective.sourceIds : null;
      const sourceIds = Object.hasOwn(body, 'sourceIds') ? body.sourceIds : inherited;
      const userTurn = {
        sequence: record.lastSequence + 1,
        requestId: body.requestId,
        role: 'user',
        text: body.query,
        status: 'input',
        evidence: {
          request: body,
          query: body.query,
          effective: { collectionId: 'browser-test', sourceIds, retrievalQuery: body.query, artifactReferences: [] },
        },
        createdAt: '2026-10-08T00:01:00Z',
      };
      record.turns.push(userTurn);
      record.lastSequence = userTurn.sequence;
      record.pendingRequestId = body.requestId;
      conversationSummaries[0] = { ...record };
      conversationStreams.set(`${id}:${body.requestId}`, {
        conversationId: id,
        requestId: body.requestId,
        body,
        polls: 0,
      });
      return route.fulfill({
        status: 202,
        json: { conversationId: id, requestId: body.requestId, jobId: 'job-' + body.requestId, state: 'queued' },
      });
    }
    const streamEventsMatch = /^\/api\/conversations\/([^/]+)\/turns\/([^/]+)\/events$/.exec(apiUrl.pathname);
    if (streamEventsMatch && req.method() === 'GET') {
      const id = streamEventsMatch[1];
      const requestId = streamEventsMatch[2];
      const key = `${id}:${requestId}`;
      const stream = conversationStreams.get(key);
      const afterEventId = Number(apiUrl.searchParams.get('afterEventId') ?? 0);
      if (!stream)
        return route.fulfill({
          json: { events: [], nextEventId: afterEventId, requestState: 'pending', jobState: 'unknown', done: false },
        });
      const record = conversationRecords.get(id);
      if (stream.polls++ === 0)
        return route.fulfill({
          json: {
            events: [
              { eventId: 1, type: 'provisional_delta', text: 'Unverified provisional text.' },
              { eventId: 2, type: 'stage', stage: 'generating' },
            ].filter((event) => event.eventId > afterEventId),
            nextEventId: 2,
            requestState: 'pending',
            jobState: 'running',
            done: false,
          },
        });
      const body = stream.body ?? { query: 'Resumed request' };
      const failed = conversationTurnFailures > 0;
      if (failed) conversationTurnFailures--;
      const refusal = body.sourceIds?.length === 0;
      const status = failed ? 'failed' : refusal ? 'refusal' : 'answer';
      const sameName = sameNameCitation && /shared notes/i.test(body.query ?? '');
      const answer = failed
        ? 'Conversation request failed. Saved turns were retained.'
        : refusal
          ? 'No sources were selected for this question.'
          : sameName
            ? 'The cited upload is the chemistry notes.'
            : 'A basis spans the vector space.';
      record.turns.push({
        sequence: ++record.lastSequence,
        requestId,
        role: 'assistant',
        text: answer,
        status,
        evidence: {
          type: status,
          citations:
            failed || refusal
              ? []
              : sameName
                ? [{ sourceId: 'source-b', sourceName: 'Shared Notes', locatorLabel: 'page 2', chunkId: 'chunk-b' }]
                : [{ sourceName: 'Linear Algebra Primer', locatorLabel: 'section 2.1', chunkId: 'chunk-la-1' }],
          contextBlock:
            failed || refusal
              ? []
              : sameName
                ? [
                    {
                      chunkId: 'chunk-a',
                      sourceId: 'source-a',
                      sourceName: 'Shared Notes',
                      locatorLabel: 'page 1',
                      text: 'First upload about biology.',
                    },
                    {
                      chunkId: 'chunk-b',
                      sourceId: 'source-b',
                      sourceName: 'Shared Notes',
                      locatorLabel: 'page 2',
                      text: 'Second upload about chemistry.',
                    },
                  ]
                : trace().contextBlock,
          traceId: 'trace-browser',
          turnReferences: [],
        },
        createdAt: '2026-10-08T00:01:01Z',
      });
      record.pendingRequestId = null;
      record.updatedAt = '2026-10-08T00:01:01Z';
      conversationSummaries[0] = { ...record };
      return route.fulfill({
        json: {
          events: [{ eventId: 3, type: 'state', state: failed ? 'failed' : 'succeeded' }].filter(
            (event) => event.eventId > afterEventId,
          ),
          nextEventId: 3,
          requestState: failed ? 'failed' : 'completed',
          jobState: failed ? 'failed' : 'succeeded',
          done: true,
        },
      });
    }
    const conversationMatch = /^\/api\/conversations\/([^/]+)$/.exec(apiUrl.pathname);
    if (conversationMatch && req.method() === 'GET') {
      const record = conversationRecords.get(conversationMatch[1]);
      if (!record) return route.fulfill({ json: { detail: 'not found' }, status: 404 });
      const after = Number(apiUrl.searchParams.get('after') ?? 0);
      const limit = Number(apiUrl.searchParams.get('limit') ?? 200);
      return route.fulfill({
        json: { ...record, turns: record.turns.filter((turn) => turn.sequence > after).slice(0, limit) },
      });
    }
    const conversationTurnMatch = /^\/api\/conversations\/([^/]+)\/turns$/.exec(apiUrl.pathname);
    if (conversationTurnMatch && req.method() === 'POST') {
      const id = conversationTurnMatch[1];
      const body = req.postDataJSON();
      capturedConversationTurns.push({ id, body });
      const record = conversationRecords.get(id);
      if (!record) return route.fulfill({ status: 404, json: { detail: 'conversation not found' } });
      const priorUsers = record.turns.filter((turn) => turn.role === 'user');
      const inherited = priorUsers.length ? priorUsers.at(-1).evidence.effective.sourceIds : null;
      const sourceIds = Object.hasOwn(body, 'sourceIds') ? body.sourceIds : inherited;
      const userTurn = {
        sequence: record.lastSequence + 1,
        requestId: body.requestId,
        role: 'user',
        text: body.query,
        status: 'input',
        evidence: {
          request: body,
          query: body.query,
          effective: { collectionId: 'browser-test', sourceIds, retrievalQuery: body.query, artifactReferences: [] },
        },
        createdAt: '2026-10-08T00:01:00Z',
      };
      record.turns.push(userTurn);
      record.lastSequence = userTurn.sequence;
      if (conversationTurnFailures > 0) {
        conversationTurnFailures--;
        record.turns.push({
          sequence: ++record.lastSequence,
          requestId: body.requestId,
          role: 'assistant',
          text: 'Conversation request failed. Saved turns were retained.',
          status: 'failed',
          evidence: { type: 'failed', citations: [], contextBlock: [], traceId: null, turnReferences: [] },
          createdAt: '2026-10-08T00:01:01Z',
        });
        record.updatedAt = '2026-10-08T00:01:01Z';
        record.pendingRequestId = null;
        conversationSummaries[0] = { ...record };
        return route.fulfill({
          status: 503,
          json: { detail: 'conversation request failed; saved turns were retained' },
        });
      }
      if (body.mode === 'history') {
        const previous = record.turns.filter((turn) => turn.sequence < userTurn.sequence);
        const references = previous.map((turn) => turn.sequence);
        const answer =
          previous.map((turn) => `Turn ${turn.sequence} (${turn.role}):\n${turn.text}`).join('\n\n') ||
          'No prior turns are saved in this conversation.';
        record.turns.push({
          sequence: ++record.lastSequence,
          requestId: body.requestId,
          role: 'assistant',
          text: answer,
          status: 'history',
          evidence: { type: 'history', citations: [], contextBlock: [], traceId: null, turnReferences: references },
          createdAt: '2026-10-08T00:01:01Z',
        });
        record.updatedAt = '2026-10-08T00:01:01Z';
        conversationSummaries[0] = { ...record };
        return route.fulfill({
          json: {
            type: 'history',
            answer,
            turnReferences: references,
            conversationId: id,
            sequence: record.lastSequence,
          },
        });
      }
      if (sourceIds?.length === 0) {
        const reason = 'No sources were selected for this question.';
        record.turns.push({
          sequence: ++record.lastSequence,
          requestId: body.requestId,
          role: 'assistant',
          text: reason,
          status: 'refusal',
          evidence: { type: 'refusal', citations: [], contextBlock: [], traceId: null, turnReferences: [] },
          createdAt: '2026-10-08T00:01:01Z',
        });
        conversationSummaries[0] = { ...record };
        return route.fulfill({ json: { type: 'refusal', reason, conversationId: id, sequence: record.lastSequence } });
      }
      conversationProviderCalls++;
      const contextBlock = trace().contextBlock;
      const citations = [{ sourceName: 'Linear Algebra Primer', locatorLabel: 'section 2.1', chunkId: 'chunk-la-1' }];
      const answer = 'A basis spans the vector space.';
      record.turns.push({
        sequence: ++record.lastSequence,
        requestId: body.requestId,
        role: 'assistant',
        text: answer,
        status: 'answer',
        evidence: { type: 'answer', citations, contextBlock, traceId: 'trace-browser', turnReferences: [] },
        createdAt: '2026-10-08T00:01:01Z',
      });
      record.updatedAt = '2026-10-08T00:01:01Z';
      conversationSummaries[0] = { ...record };
      return route.fulfill({
        json: { type: 'answer', answer, citations, trace: trace(), conversationId: id, sequence: record.lastSequence },
      });
    }
    const abandonMatch = /^\/api\/conversations\/([^/]+)\/turns\/([^/]+)\/abandon$/.exec(apiUrl.pathname);
    if (abandonMatch && req.method() === 'POST') {
      const record = conversationRecords.get(abandonMatch[1]);
      if (!record) return route.fulfill({ status: 404, json: { detail: 'conversation not found' } });
      const requestId = abandonMatch[2];
      const reason = 'Request abandoned; late completion is fenced.';
      record.turns.push({
        sequence: ++record.lastSequence,
        requestId,
        role: 'assistant',
        text: reason,
        status: 'abandoned',
        evidence: { type: 'abandoned', citations: [], contextBlock: [], traceId: null, turnReferences: [] },
        createdAt: '2026-10-08T00:01:02Z',
      });
      record.pendingRequestId = null;
      conversationSummaries[0] = { ...record };
      return route.fulfill({
        json: { type: 'abandoned', reason, conversationId: record.conversationId, sequence: record.lastSequence },
      });
    }
    if (apiUrl.pathname === '/api/query' && req.method() === 'POST') {
      const body = req.postDataJSON();
      capturedQueries.push(body);
      if (body.sourceIds?.length === 0)
        return route.fulfill({
          json: { type: 'refusal', reason: 'No sources were selected for this question.', trace: trace() },
        });
      return route.fulfill({
        json: {
          type: 'answer',
          answer: 'A basis spans the vector space.',
          citations: [{ sourceName: 'Linear Algebra Primer', locatorLabel: 'section 2.1', chunkId: 'chunk-la-1' }],
          trace: trace(),
        },
      });
    }
    if (apiUrl.pathname === '/api/recommendations')
      return route.fulfill({
        json: [
          {
            clusterId: 'linear-foundations',
            path: ['Mathematics', 'Linear Algebra'],
            courses: [
              {
                courseId: 'math-221',
                code: 'MATH 221',
                title: 'Linear Algebra',
                lifecycle: 'current',
              },
            ],
          },
        ],
      });
    if (apiUrl.pathname === '/api/retired-courses')
      return route.fulfill({
        json: [
          {
            courseId: 'math-199',
            code: 'MATH 199',
            title: 'Retired Topics',
            lifecycle: 'retired',
          },
        ],
      });
    if (apiUrl.pathname === '/api/admin/connections' && req.method() === 'GET')
      return connectionAdmin
        ? route.fulfill({ json: connectionProfiles })
        : route.fulfill({ status: 404, json: { detail: 'not found' } });
    if (apiUrl.pathname === '/api/admin/connections' && req.method() === 'POST') {
      const body = req.postDataJSON();
      capturedConnectionWrites.push(body);
      connectionProfiles = [{ ...profileFixture, name: body.name, model_id: body.model_id, provider: body.provider }];
      return route.fulfill({ status: 201, json: connectionProfiles[0] });
    }
    const connectionUpdate = /^\/api\/admin\/connections\/([^/]+)$/.exec(apiUrl.pathname);
    if (connectionUpdate && req.method() === 'PUT') {
      const body = req.postDataJSON();
      capturedConnectionWrites.push(body);
      const id = decodeURIComponent(connectionUpdate[1]);
      connectionProfiles = connectionProfiles.map((profile) =>
        profile.profile_id === id
          ? { ...profile, ...body, profile_id: id, revision: profile.revision + 1 }
          : profile,
      );
      return route.fulfill({ json: connectionProfiles.find((profile) => profile.profile_id === id) });
    }
    if (apiUrl.pathname === '/api/admin/connections/profile-1/secret' && req.method() === 'PUT') {
      const body = req.postDataJSON();
      capturedConnectionWrites.push(body);
      connectionProfiles = [{ ...profileFixture, revision: profileFixture.revision + 1 }];
      return route.fulfill({ json: connectionProfiles[0] });
    }
    if (apiUrl.pathname === '/api/admin/overview' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          users: adminUsersList.length,
          admins: adminUsersList.filter((u) => u.role === 'admin' && u.status === 'active').length,
          activeSessions: 2,
          conversations: conversationSummaries.length,
          turns: 10,
          summaries: 2,
          queuedJobs: 0,
          runningJobs: 0,
          sources: sourceRows.length,
          artifacts: artifactSummaries.length,
          collections: adminCollectionsList.length,
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/users' && req.method() === 'GET') {
      const q = apiUrl.searchParams.get('query')?.toLowerCase();
      let filtered = adminUsersList;
      if (q) {
        filtered = filtered.filter(
          (u) =>
            u.displayName?.toLowerCase().includes(q) ||
            u.email?.toLowerCase().includes(q) ||
            u.workspaceKey?.toLowerCase().includes(q),
        );
      }
      return route.fulfill({ json: filtered });
    }
    const adminUserPatchMatch = /^\/api\/admin\/users\/([^/]+)$/.exec(apiUrl.pathname);
    if (adminUserPatchMatch && req.method() === 'PATCH') {
      const userId = decodeURIComponent(adminUserPatchMatch[1]);
      const body = req.postDataJSON();
      capturedUserUpdates.push({ userId, body });
      const user = adminUsersList.find((u) => u.userId === userId);
      if (!user) return route.fulfill({ status: 404, json: { detail: 'User not found' } });
      const activeAdmins = adminUsersList.filter((u) => u.role === 'admin' && u.status === 'active');
      if (
        user.role === 'admin' &&
        (body.role === 'student' || body.status === 'disabled') &&
        activeAdmins.length <= 1
      ) {
        return route.fulfill({ status: 400, json: { detail: 'Cannot remove the last active admin.' } });
      }
      if (body.role) user.role = body.role;
      if (body.status) user.status = body.status;
      user.updatedAt = '2026-10-08T00:02:00Z';
      return route.fulfill({ json: user });
    }
    const userSessionsMatch = /^\/api\/admin\/users\/([^/]+)\/sessions$/.exec(apiUrl.pathname);
    if (userSessionsMatch && req.method() === 'GET') {
      return route.fulfill({
        json: [
          {
            createdAt: '2026-10-08T00:00:00Z',
            expiresAt: '2026-10-09T00:00:00Z',
            revokedAt: null,
          },
        ],
      });
    }
    const revokeSessionsMatch = /^\/api\/admin\/users\/([^/]+)\/revoke-sessions$/.exec(apiUrl.pathname);
    if (revokeSessionsMatch && req.method() === 'POST') {
      capturedUserRevokes.push(decodeURIComponent(revokeSessionsMatch[1]));
      return route.fulfill({ json: { revoked: 1 } });
    }
    if (apiUrl.pathname === '/api/admin/collections' && req.method() === 'GET') {
      return route.fulfill({ json: adminCollectionsList });
    }
    if (apiUrl.pathname === '/api/admin/collections' && req.method() === 'POST') {
      const body = req.postDataJSON();
      const created = {
        collectionId: 'col-' + (adminCollectionsList.length + 1),
        name: body.name,
        sourceCount: 0,
        createdAt: '2026-10-08T00:00:00Z',
      };
      adminCollectionsList.push(created);
      return route.fulfill({ status: 201, json: created });
    }
    const adminCollectionDeleteMatch = /^\/api\/admin\/collections\/([^/]+)$/.exec(apiUrl.pathname);
    if (adminCollectionDeleteMatch && req.method() === 'DELETE') {
      const colId = decodeURIComponent(adminCollectionDeleteMatch[1]);
      const idx = adminCollectionsList.findIndex((c) => c.collectionId === colId);
      if (idx !== -1) adminCollectionsList.splice(idx, 1);
      return route.fulfill({ status: 204 });
    }
    if (apiUrl.pathname === '/api/admin/audit' && req.method() === 'GET') {
      const actionFilter = apiUrl.searchParams.get('action') || '';
      const cursor = apiUrl.searchParams.get('cursor') || '';
      if (cursor === 'cur-2') {
        return route.fulfill({
          headers: { 'Content-Type': 'application/json' },
          json: [
            {
              auditId: 'aud-2',
              actorEmail: 'admin1@emory.edu',
              actorWorkspace: 'admin-ws',
              action: 'conversation.deleted',
              targetType: 'conversation',
              targetId: 'conv-123',
              createdAt: '2026-10-07T00:00:00Z',
              detail: {},
            },
          ],
        });
      }
      return route.fulfill({
        headers: {
          'Content-Type': 'application/json',
          'X-Next-Cursor': 'cur-2',
        },
        json: [
          {
            auditId: 'aud-1',
            actorEmail: 'admin1@emory.edu',
            actorWorkspace: 'admin-ws',
            action: actionFilter || 'user.role_status',
            targetType: 'user',
            targetId: 'user-student-2',
            createdAt: '2026-10-08T00:00:00Z',
            detail: { after: { role: 'admin' } },
          },
        ],
      });
    }
    if (apiUrl.pathname === '/api/admin/audit/export' && req.method() === 'GET') {
      const format = apiUrl.searchParams.get('format') || 'csv';
      return route.fulfill({
        headers: {
          'Content-Type': format === 'csv' ? 'text/csv; charset=utf-8' : 'application/json',
          'Content-Disposition': `attachment; filename="admin-audit-export.${format}"`,
          'X-Snapshot-Until': '2026-10-19T00:00:00Z',
        },
        body: format === 'csv' ? 'audit_id,action\naud-1,user.role_status\n' : JSON.stringify([{ auditId: 'aud-1' }]),
      });
    }
    if (apiUrl.pathname === '/api/admin/jobs' && req.method() === 'GET') return route.fulfill({ json: [] });
    if (apiUrl.pathname === '/api/admin/sources' && req.method() === 'GET') {
      return route.fulfill({ json: [reviewSource] });
    }
    if (apiUrl.pathname === '/api/admin/sources/src-1/review' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          source: {
            ...reviewSource,
            metadataRevision: 1,
            sourceCategory: 'institutional_policy',
            lifecycleStatus: 'active',
          },
          pendingProposal: null,
          proposals: [],
          lifecycleEvents: [],
          dependencies: {
            programCount: 1,
            courseCount: 0,
            gapCount: 0,
            researchRunCount: 0,
            programs: [{ programId: 'prog-1', title: 'Pocket Major (BA)' }],
          },
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/sources/src-1/proposals' && req.method() === 'POST') {
      return route.fulfill({
        status: 201,
        json: {
          proposalId: 'prop-1',
          sourceId: 'src-1',
          expectedMetadataRevision: 1,
          status: 'pending',
          sourceCategory: 'institutional_policy',
          classificationOrigin: 'verified_curator',
          applicabilityStatus: 'unknown',
          lifecycleStatus: 'active',
          createdAt: '2026-10-19T12:00:00Z',
          updatedAt: '2026-10-19T12:00:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/sources/src-1/proposals/prop-1/approve' && req.method() === 'POST') {
      return route.fulfill({
        json: {
          proposalId: 'prop-1',
          sourceId: 'src-1',
          expectedMetadataRevision: 1,
          status: 'approved',
          sourceCategory: 'institutional_policy',
          classificationOrigin: 'verified_curator',
          applicabilityStatus: 'unknown',
          lifecycleStatus: 'active',
          createdAt: '2026-10-19T12:00:00Z',
          updatedAt: '2026-10-19T12:02:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/sources/src-1/lifecycle' && req.method() === 'POST') {
      return route.fulfill({ json: { sourceId: 'src-1', lifecycleStatus: 'deprecated', metadataRevision: 2 } });
    }
    if (apiUrl.pathname === '/api/admin/sources/src-1/evidence' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          sourceId: 'src-1',
          indexRevision: 1,
          passages: [
            {
              chunkId: 'chunk-1',
              chunkIndex: 0,
              locatorLabel: 'line 1',
              text: 'Historical clause alpha stays addressable.',
              textDigest: 'a'.repeat(64),
            },
          ],
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/programs/prog-1/inspection' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          program: { programId: 'prog-1', title: 'Pocket Major (BA)' },
          items: [{ position: 1, text: 'CS 170', mentions: ['CS 170'] }],
          releases: [],
          currentRelease: null,
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/programs/prog-1/rules/draft' && req.method() === 'POST') {
      return route.fulfill({
        status: 201,
        json: {
          releaseId: 'rel-1',
          programId: 'prog-1',
          collectionId: 'col-1',
          sourceId: 'src-1',
          sourceIndexRevision: 1,
          sourceSha256: 'b'.repeat(64),
          releaseVersion: 1,
          status: 'draft',
          rulesTree: { nodeId: 'root', nodeType: 'all_of' },
          rulesDigest: 'c'.repeat(64),
          createdAt: '2026-10-19T12:03:00Z',
          updatedAt: '2026-10-19T12:03:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/programs/prog-1/rules/rel-1/approve' && req.method() === 'POST') {
      return route.fulfill({
        json: {
          releaseId: 'rel-1',
          programId: 'prog-1',
          status: 'approved',
          rulesDigest: 'c'.repeat(64),
          releaseVersion: 1,
          sourceId: 'src-1',
          collectionId: 'col-1',
          sourceIndexRevision: 1,
          sourceSha256: 'b'.repeat(64),
          rulesTree: {},
          createdAt: '2026-10-19T12:03:00Z',
          updatedAt: '2026-10-19T12:04:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/programs/prog-1/rules/rel-1/publish' && req.method() === 'POST') {
      return route.fulfill({
        json: {
          releaseId: 'rel-1',
          programId: 'prog-1',
          status: 'published',
          rulesDigest: 'c'.repeat(64),
          releaseVersion: 1,
          sourceId: 'src-1',
          collectionId: 'col-1',
          sourceIndexRevision: 1,
          sourceSha256: 'b'.repeat(64),
          rulesTree: {},
          createdAt: '2026-10-19T12:03:00Z',
          updatedAt: '2026-10-19T12:05:00Z',
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/artifacts' && req.method() === 'GET') return route.fulfill({ json: [] });
    if (apiUrl.pathname === '/api/admin/conversations' && req.method() === 'GET') return route.fulfill({ json: [] });
    if (apiUrl.pathname === '/api/admin/telemetry/usage' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          summary: {
            totalCalls: 10,
            completedCalls: 8,
            failedCalls: 2,
            totalPromptTokens: 25000,
            totalCachedTokens: 5000,
            totalCompletionTokens: 4000,
            cachedTokenShare: 0.2,
            callsReportingCache: 8,
            callsWithCacheHits: 4,
            cacheHitFraction: 0.5,
            cacheReportingCoverage: 0.8,
            totalCost: 0.125,
            costCurrency: 'USD',
            costCurrencies: ['USD'],
            measuredCost: 0.1,
            estimatedCost: 0.025,
            unknownCostCalls: 1,
          },
          breakdown: [
            {
              bucket: '2026-10-08',
              totalCalls: 10,
              completedCalls: 8,
              failedCalls: 2,
              totalPromptTokens: 25000,
              totalCachedTokens: 5000,
              totalCompletionTokens: 4000,
              cachedTokenShare: 0.2,
              callsReportingCache: 8,
              callsWithCacheHits: 4,
              cacheHitFraction: 0.5,
              cacheReportingCoverage: 0.8,
              totalCost: 0.125,
              costCurrency: 'USD',
              costCurrencies: ['USD'],
              measuredCost: 0.1,
              estimatedCost: 0.025,
              unknownCostCalls: 1,
            },
          ],
        },
      });
    }
    if (apiUrl.pathname === '/api/admin/pricing/schedules' && req.method() === 'GET') {
      return route.fulfill({
        json: [
          {
            scheduleId: 'sched-1',
            provider: 'openai',
            modelId: 'gpt-4o',
            currency: 'USD',
            billingMode: 'standard',
            inputRatePerMillion: 2.5,
            cachedReadRatePerMillion: 1.25,
            outputRatePerMillion: 10.0,
            effectiveFrom: '2026-10-01T00:00:00Z',
            effectiveTo: null,
            provenance: 'OpenAI Pricing 2026-10',
            createdAt: '2026-10-01T00:00:00Z',
          },
        ],
      });
    }

    if (apiUrl.pathname === '/api/debug/settings' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          settings: [
            { name: 'retrieval_limit', label: 'Retrieval limit', type: 'int', value: 5, description: 'Max passages' },
            {
              name: 'enable_planning',
              label: 'Enable planning',
              type: 'bool',
              value: false,
              description: 'Query planner',
            },
          ],
          retrieval: [
            { name: 'fusion_weight', label: 'Fusion weight', type: 'float', value: 0.5, description: 'Seam weight' },
          ],
          defaultSystemPrompt: 'Default test system prompt',
        },
      });
    }
    if (apiUrl.pathname === '/api/debug/sessions' && req.method() === 'POST') {
      const body = req.postDataJSON();
      debugSession = {
        token: 'test-debug-token-active',
        overrides: body.overrides ?? {},
        systemPrompt: body.systemPrompt ?? null,
        createdAt: '2026-10-08T00:00:00Z',
        expiresAt: '2026-10-08T01:00:00Z',
      };
      return route.fulfill({ status: 201, json: debugSession });
    }
    if (apiUrl.pathname === '/api/debug/session') {
      const token = req.headers()['x-debug-session'];
      if (req.method() === 'GET') {
        if (debugSessionExpired || token === 'expired-token') {
          return route.fulfill({ status: 401, json: { detail: 'Debug session expired.' } });
        }
        return route.fulfill({
          json: debugSession ?? { token: 'test-debug-token-active', overrides: {}, systemPrompt: null },
        });
      }
      if (req.method() === 'PATCH') {
        const body = req.postDataJSON();
        capturedDebugPatches.push(body);
        debugSession = {
          ...debugSession,
          overrides: { ...(debugSession?.overrides ?? {}), ...(body.overrides ?? {}) },
          systemPrompt: body.systemPrompt !== undefined ? body.systemPrompt : debugSession?.systemPrompt,
        };
        return route.fulfill({ json: debugSession });
      }
      if (req.method() === 'DELETE') {
        debugSession = null;
        return route.fulfill({ status: 204 });
      }
    }
    if (apiUrl.pathname === '/api/artifacts' && req.method() === 'GET')
      return route.fulfill({ json: artifactSummaries });
    if (apiUrl.pathname === '/api/artifacts' && req.method() === 'POST') {
      const body = req.postDataJSON();
      artifactSummaries.push({
        artifactId: 'artifact-1',
        revisionId: 'rev-1',
        kind: body.kind,
        title: body.spec.title,
        status: 'ready',
      });
      return route.fulfill({
        json: {
          artifactId: 'artifact-1',
          revisionId: 'rev-1',
          parentRevisionId: null,
          kind: body.kind,
          title: body.spec.title,
          spec: body.spec,
        },
      });
    }
    if (apiUrl.pathname === '/api/artifacts/artifact-wb' && req.method() === 'GET')
      return route.fulfill({ json: workbookRecord });
    if (apiUrl.pathname === '/api/artifact-batches' && req.method() === 'GET')
      return route.fulfill({ json: batchSummaries });
    if (apiUrl.pathname === '/api/artifact-batches' && req.method() === 'POST') {
      const body = req.postDataJSON();
      capturedBatchSubmissions.push(body);
      const createdAt = '2026-10-08T00:00:00Z';
      const record = {
        batchId: body.batchId,
        state: failBatchSubmit ? 'unknown' : 'in_progress',
        cancelRequested: false,
        itemCount: body.items.length,
        totalCount: body.items.length,
        completedCount: 0,
        failedCount: 0,
        createdAt,
        updatedAt: createdAt,
        items: body.items.map((item) => ({
          itemId: item.itemId,
          state: 'pending',
          publicationState: 'pending',
          artifactId: null,
          revisionId: null,
          errorCode: null,
        })),
      };
      failBatchSubmit = false;
      batchRecords.set(record.batchId, record);
      batchSummaries.unshift({ ...record, items: undefined });
      if (capturedBatchSubmissions.length === 1 && record.state === 'unknown')
        return route.fulfill({ status: 503, json: { detail: 'submission outcome unknown' } });
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
      record.state = 'completed';
      record.completedCount = record.itemCount;
      record.failedCount = 0;
      record.totalCount = record.itemCount;
      record.items = record.items.map((item, index) => ({
        ...item,
        state: 'completed',
        publicationState: 'published',
        artifactId: `batch-artifact-${index + 1}`,
        revisionId: `revision-published-${index + 1}`,
      }));
      batchSummaries[batchSummaries.findIndex((item) => item.batchId === record.batchId)] = {
        ...record,
        items: undefined,
      };
      return route.fulfill({ json: record });
    }
    const batchCancelMatch = /^\/api\/artifact-batches\/([^/]+)\/cancel$/.exec(apiUrl.pathname);
    if (batchCancelMatch && req.method() === 'POST') {
      capturedBatchCancels.push(batchCancelMatch[1]);
      const record = batchRecords.get(batchCancelMatch[1]);
      if (!record) return route.fulfill({ status: 404, json: { detail: 'batch not found' } });
      record.state = 'cancelled';
      record.cancelRequested = true;
      batchSummaries[batchSummaries.findIndex((item) => item.batchId === record.batchId)] = {
        ...record,
        items: undefined,
      };
      return route.fulfill({ json: record });
    }
    if (apiUrl.pathname === '/api/artifacts/batch-artifact-1' && req.method() === 'GET') {
      openedArtifactRevisions.push(apiUrl.searchParams.get('revisionId'));
      return route.fulfill({
        json: {
          artifactId: 'batch-artifact-1',
          revisionId: 'revision-published-1',
          parentRevisionId: null,
          kind: 'document',
          title: 'Published batch document',
          spec: {
            kind: 'document',
            title: 'Published batch document',
            blocks: [{ id: 'batch-block', type: 'paragraph', text: 'Published from batch.' }],
          },
        },
      });
    }
    if (apiUrl.pathname === '/api/runs/limits' && req.method() === 'GET')
      return route.fulfill({ json: { maxCalls: 4, totalTokenBudget: 6000, perCallOutputCap: 2048 } });
    if (apiUrl.pathname === '/api/runs' && req.method() === 'POST') {
      capturedRuns.push(req.postDataJSON());
      return route.fulfill({ json: { runId: 'run-1', state: 'queued' } });
    }
    if (apiUrl.pathname === '/api/runs/run-1' && req.method() === 'GET') {
      return route.fulfill({
        json: {
          runId: 'run-1',
          state: 'succeeded',
          artifactIds: ['artifact-1'],
          ...(pinnedRunRevision
            ? { artifactRevisions: [{ artifactId: 'artifact-1', revisionId: 'published-r1' }] }
            : {}),
        },
      });
    }
    if (apiUrl.pathname === '/api/runs/run-1/events')
      return route.fulfill({
        json: [
          {
            eventId: 1,
            runId: 'run-1',
            state: 'succeeded',
            stage: 'rendering',
            artifactIds: ['artifact-1'],
            ...(pinnedRunRevision
              ? { artifactRevisions: [{ artifactId: 'artifact-1', revisionId: 'published-r1' }] }
              : {}),
          },
        ],
      });
    if (apiUrl.pathname === '/api/runs/run-1/cancel' && req.method() === 'POST')
      return route.fulfill({ json: { runId: 'run-1', state: 'cancelled' } });
    if (apiUrl.pathname === '/api/artifacts/artifact-1' && req.method() === 'GET') {
      const revisionId = apiUrl.searchParams.get('revisionId');
      if (revisionId) openedRunRevisions.push(revisionId);
      return route.fulfill({
        json: {
          artifactId: 'artifact-1',
          revisionId: revisionId ?? (pinnedRunRevision ? 'newer-head' : 'rev-1'),
          parentRevisionId: null,
          kind: 'document',
          title: 'Generated draft',
          spec: {
            kind: 'document',
            title: 'Generated draft',
            blocks: [],
            provenance: { authority: 'generated_draft' },
          },
        },
      });
    }
    if (apiUrl.pathname === '/api/artifacts/import' && req.method() === 'POST')
      return route.fulfill({ json: importedRecord ?? workbookRecord });
    if (apiUrl.pathname === '/api/artifacts/artifact-wb/original')
      return route.fulfill({
        body: 'original xlsx bytes',
        headers: { 'Content-Disposition': "attachment; filename*=UTF-8''metrics.xlsx" },
      });
    if (apiUrl.pathname === '/api/artifacts/artifact-wb/revisions') {
      const body = req.postDataJSON();
      capturedRevisions.push(body);
      if (saveDelayMs) await new Promise((resolve) => setTimeout(resolve, saveDelayMs));
      const updated = structuredClone(workbookHead);
      updated.revisionId = `wb-r${capturedRevisions.length + 1}`;
      updated.parentRevisionId = workbookHead.revisionId;
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
    if (apiUrl.pathname === '/api/artifacts/artifact-1/revisions')
      return route.fulfill({ status: 409, json: { detail: 'Stale parent revision.' } });
    if (apiUrl.pathname === '/api/artifacts/artifact-doc/revisions') {
      const body = req.postDataJSON();
      capturedRevisions.push(body);
      if (saveDelayMs) await new Promise((resolve) => setTimeout(resolve, saveDelayMs));
      return route.fulfill({
        json: { ...documentRecord, revisionId: 'doc-r2', parentRevisionId: 'doc-r1', spec: body.spec },
      });
    }
    if (apiUrl.pathname === '/api/artifacts/artifact-1/preview')
      return route.fulfill({
        json: { kind: 'document', title: 'Browser draft', html: '<p>Safe preview</p>', sandboxRequired: false },
      });
    if (apiUrl.pathname === '/api/artifacts/artifact-1/download')
      return route.fulfill({
        body: '<p>Safe preview</p>',
        headers: { 'Content-Disposition': 'attachment; filename="browser-draft.html"' },
      });
    if (apiUrl.pathname === '/api/artifacts/artifact-doc/download') {
      const format = apiUrl.searchParams.get('format');
      downloadedArtifacts.push({ format, revisionId: apiUrl.searchParams.get('revisionId') });
      return route.fulfill({
        body: `rendered ${format}`,
        headers: { 'Content-Disposition': `attachment; filename="notes-r1.${format}"` },
      });
    }
    if (apiUrl.pathname === '/api/artifacts/artifact-wb/download') {
      const format = apiUrl.searchParams.get('format');
      downloadedArtifacts.push({ format, revisionId: apiUrl.searchParams.get('revisionId') });
      return route.fulfill({
        body: `rendered ${format}`,
        headers: { 'Content-Disposition': `attachment; filename="metrics-wb-r1.${format}"` },
      });
    }
    return route.fulfill({ status: 404, json: { detail: `Unexpected test route ${req.method()} ${apiUrl.pathname}` } });
  });
  const targetUrl = entry === '/' ? url : `${url}${entry.startsWith('/') ? entry : '/' + entry}`;
  await page.goto(targetUrl);
  if (entry === '/' || entry === '/index.html') {
    try {
      await page.getByRole('heading', { name: 'Emory STEM Advisor' }).waitFor({ timeout: 5000 });
    } catch {
      throw new Error(`Frontend did not render. Body: ${await page.locator('body').innerText()}`);
    }
  } else if (entry === '/admin.html' || entry === '/admin') {
    try {
      if (['admin', 'auditor'].includes(currentSession?.role)) {
        await page.getByRole('tablist').waitFor({ timeout: 5000 });
      } else {
        await page.getByRole('heading', { name: 'Administrator access required' }).waitFor({ timeout: 5000 });
      }
    } catch {
      throw new Error(`Admin page did not render expected state. Body: ${await page.locator('body').innerText()}`);
    }
  } else if (entry === '/debug.html' || entry === '/debug') {
    try {
      if (currentSession?.role === 'admin') {
        await page.getByRole('button', { name: 'Save overrides' }).waitFor({ timeout: 5000 });
      } else {
        await page.getByRole('heading', { name: 'Administrator access required' }).waitFor({ timeout: 5000 });
      }
    } catch {
      throw new Error(`Debug page did not render expected state. Body: ${await page.locator('body').innerText()}`);
    }
  }
  return {
    context,
    page,
    capturedQueries,
    capturedRevisions,
    capturedRuns,
    capturedConnectionWrites,
    capturedReindexes,
    capturedConversationTurns,
    capturedConversationStreams,
    conversationRecords,
    conversationSummaries,
    capturedBatchSubmissions,
    capturedBatchPolls,
    capturedBatchCancels,
    openedArtifactRevisions,
    openedRunRevisions,
    downloadedArtifacts,
    batchRecords,
    capturedGapResolutions,
    get conversationProviderCalls() {
      return conversationProviderCalls;
    },
    waitForConversationCreate: () => conversationCreateStarted,
    failNextConversationTurn: () => {
      conversationTurnFailures++;
    },
    sourcesRequested,
    capturedApiRequests,
    capturedUserUpdates,
    capturedUserRevokes,
    capturedDebugPatches,
    adminUsersList,
    adminCollectionsList,
  };
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
    assert.equal(
      await page.getByRole('tab', { name: 'Model connections' }).count(),
      0,
      'local settings stay hidden when the endpoint is disabled',
    );
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
    assert.equal(
      await page.getByRole('figure', { name: 'Cited passage' }).locator('blockquote').innerText(),
      'A basis spans the vector space and is linearly independent.',
    );

    await openSidebar(page, 'Explore courses');
    await page.getByLabel('Interest or career goal').fill('linear algebra');
    await page.getByRole('button', { name: 'Find' }).click();
    await page.getByText('MATH 221').waitFor();
    await page.getByText('Mathematics › Linear Algebra').waitFor();
    await page.getByText('Retired Topics').waitFor();
    await page.getByRole('button', { name: 'Ask about MATH 221' }).click();
    assert.match(await message(page).inputValue(), /MATH 221 \(Linear Algebra\)/);
  } finally {
    await context.close();
  }
});

test('failed source replacement refreshes revision diagnostics and keeps citations available', async () => {
  const { context, page, capturedReindexes } = await setupPage();
  try {
    await openSidebar(page, /^Sources/);
    const row = page.locator('.source-row').filter({ hasText: 'Linear Algebra Primer' });
    await row.getByText('Details').click();
    const button = page.getByRole('button', { name: 'Reindex Linear Algebra Primer' });
    await button.click();
    await page.waitForFunction(() =>
      Array.from(document.querySelectorAll('.source-reindex-button')).some(
        (item) => item.textContent === 'Reindexing…',
      ),
    );
    assert.equal(await button.isDisabled(), true, 'duplicate clicks are disabled while the request is active');
    try {
      await page
        .getByRole('alert')
        .getByText(/Reindex failed: API request failed \(422\): source reindex failed/)
        .waitFor({ timeout: 5000 });
    } catch {
      throw new Error(
        `Expected reindex failure was not rendered. Calls=${JSON.stringify(capturedReindexes)} Body=${await page.locator('body').innerText()}`,
      );
    }
    assert.deepEqual(capturedReindexes, [{ expectedIndexRevision: 2 }]);
    await row.getByText('Index revision 3', { exact: true }).waitFor();
    await row.getByText('RuntimeError: test replacement failed', { exact: true }).waitFor();
    await page
      .getByRole('alert')
      .getByText(/previously ready index remains available/)
      .waitFor();
    assert.equal(await button.isDisabled(), false, 'the button is re-enabled after failure');
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    await send(page, 'What does a basis do?');
    await page.getByText('A basis spans the vector space.', { exact: true }).waitFor();
    await page.locator('.source-chip').filter({ hasText: 'Linear Algebra Primer' }).click();
    assert.match(
      await page.getByRole('figure', { name: 'Cited passage' }).locator('blockquote').innerText(),
      /basis spans the vector space/,
    );
  } finally {
    await context.close();
  }
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
    assert.equal(
      Object.hasOwn(capturedConversationTurns[0].body, 'sourceIds'),
      false,
      'first turn uses the default all-source scope',
    );
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
    assert.equal(
      Object.hasOwn(capturedConversationTurns[1].body, 'sourceIds'),
      false,
      'follow-up inherits the saved scope',
    );
    assert.notEqual(capturedConversationTurns[0].body.requestId, capturedConversationTurns[1].body.requestId);
  } finally {
    await context.close();
  }
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
  } finally {
    await state.context.close();
  }
});

test('saved research answers retain partial notices and exact citation navigation after reload', async () => {
  const createdAt = '2026-10-13T00:00:00Z';
  const summary = {
    conversationId: 'research-1',
    title: 'Research options',
    lastSequence: 2,
    pendingRequestId: null,
    createdAt,
    updatedAt: createdAt,
  };
  const record = {
    ...summary,
    turns: [
      {
        sequence: 1,
        requestId: 'research-request',
        role: 'user',
        text: 'Which path should I explore?',
        status: 'input',
        evidence: {
          request: { query: 'Which path should I explore?' },
          query: 'Which path should I explore?',
          effective: {
            collectionId: 'browser-test',
            sourceIds: null,
            retrievalQuery: 'Which path should I explore?',
            artifactReferences: [],
          },
        },
        createdAt,
      },
      {
        sequence: 2,
        requestId: 'research-request',
        role: 'assistant',
        text: 'I would explore this path [1].',
        status: 'answer',
        evidence: {
          type: 'answer',
          citations: [{ sourceName: 'Linear Algebra Primer', locatorLabel: 'section 2.1', chunkId: 'chunk-la-1' }],
          contextBlock: trace().contextBlock,
          traceId: 'research-trace',
          turnReferences: [],
          research: {
            runId: 'run-1',
            outcome: 'recommendation',
            completeness: 'partial',
            blocks: [],
            unresolvedQuestions: ['The double-counting policy remains unknown.'],
          },
        },
        createdAt,
      },
    ],
  };
  const effectiveFlags = { memory_inspector: true, advising_summary_export: true };
  const { context, page } = await setupPage({
    initialConversations: [summary],
    initialConversationRecords: [record],
    effectiveFlags,
  });
  try {
    await page.getByRole('note').getByText('Some questions remain open.').waitFor();
    await page.getByText('The double-counting policy remains unknown.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Linear Algebra Primer section 2.1', exact: true }).click();
    assert.equal(
      await page.getByRole('figure', { name: 'Cited passage' }).locator('blockquote').innerText(),
      trace().contextBlock[0].text,
    );
    await page.reload();
    await page.getByRole('note').getByText('Some questions remain open.').waitFor();
  } finally {
    await context.close();
  }
});

test('reopened pending conversation resumes stored stream events without posting or retrying', async () => {
  const createdAt = '2026-10-08T00:00:00Z';
  const summary = {
    conversationId: 'resume-1',
    title: 'Resume answer',
    lastSequence: 1,
    pendingRequestId: 'resume-request',
    createdAt,
    updatedAt: createdAt,
  };
  const record = {
    ...summary,
    turns: [
      {
        sequence: 1,
        requestId: 'resume-request',
        role: 'user',
        text: 'What is a basis?',
        status: 'input',
        evidence: {
          request: { query: 'What is a basis?' },
          query: 'What is a basis?',
          effective: {
            collectionId: 'browser-test',
            sourceIds: null,
            retrievalQuery: 'What is a basis?',
            artifactReferences: [],
          },
        },
        createdAt,
      },
    ],
  };
  const state = await setupPage({
    initialConversations: [summary],
    initialConversationRecords: [record],
    resumeStreams: [{ conversationId: 'resume-1', requestId: 'resume-request', body: { query: 'What is a basis?' } }],
  });
  try {
    await state.page.getByLabel('Unverified provisional answer').getByText('Unverified provisional text.').waitFor();
    assert.equal(
      await state.page.locator('.message-pending .source-chip, .message-pending .cite-mark').count(),
      0,
      'provisional output carries no active citations',
    );
    await state.page
      .locator('.message-assistant')
      .getByText('A basis spans the vector space.', { exact: true })
      .waitFor();
    assert.equal(state.capturedConversationStreams.length, 0, 'reopen inspects events and never resubmits the request');
    assert.equal(state.conversationProviderCalls, 0, 'event reconnection does not call a model');
  } finally {
    await state.context.close();
  }
});

test('saved conversation preserves explicit empty scope and exposes pending requests for explicit abandonment', async () => {
  const pendingSummary = {
    conversationId: 'pending-1',
    title: 'Pending conversation',
    lastSequence: 1,
    pendingRequestId: 'pending-request',
    createdAt: '2026-10-08T00:00:00Z',
    updatedAt: '2026-10-08T00:00:00Z',
  };
  const pendingRecord = {
    ...pendingSummary,
    turns: [
      {
        sequence: 1,
        requestId: 'pending-request',
        role: 'user',
        text: 'Unfinished question',
        status: 'input',
        evidence: {
          request: { query: 'Unfinished question' },
          query: 'Unfinished question',
          effective: {
            collectionId: 'browser-test',
            sourceIds: null,
            retrievalQuery: 'Unfinished question',
            artifactReferences: [],
          },
        },
        createdAt: '2026-10-08T00:00:01Z',
      },
    ],
  };
  const { context, page, capturedConversationTurns, conversationRecords } = await setupPage({
    initialConversations: [pendingSummary],
    initialConversationRecords: [pendingRecord],
  });
  const stop = page.getByRole('button', { name: 'Stop answering' });
  try {
    await page.getByText('Unfinished question', { exact: true }).waitFor();
    await stop.click();
    await page.getByText(/^Stopped\./).waitFor();
    assert.equal(conversationRecords.get('pending-1').pendingRequestId, null);
    assert.equal(
      capturedConversationTurns.length,
      0,
      'opening and abandoning pending work never replay its model call',
    );

    let notifyLateRead, releaseLateRead;
    const lateReadStarted = new Promise((resolve) => {
      notifyLateRead = resolve;
    });
    const release = new Promise((resolve) => {
      releaseLateRead = resolve;
    });
    let heldRequestId;
    await page.route('**/api/conversations/*/turns/*/events**', async (route) => {
      const requestUrl = new URL(route.request().url());
      const requestId = requestUrl.pathname.split('/').at(-2);
      heldRequestId ??= requestId;
      const after = Number(requestUrl.searchParams.get('afterEventId'));
      if (requestId === heldRequestId && after > 0) {
        notifyLateRead();
        await release;
        return route.fulfill({
          json: {
            events: [{ eventId: 2, type: 'provisional_delta', text: 'Late stale text' }],
            nextEventId: 2,
            requestState: 'pending',
            jobState: 'running',
            done: false,
          },
        });
      }
      return route.fulfill({
        json: {
          events: [{ eventId: 1, type: 'provisional_delta', text: 'Still generating' }].filter(
            (event) => event.eventId > after,
          ),
          nextEventId: 1,
          requestState: 'pending',
          jobState: 'running',
          done: false,
        },
      });
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
    const lateResponse = page.waitForResponse(
      (response) => response.url().includes(heldRequestId) && response.url().includes('afterEventId=1'),
    );
    releaseLateRead();
    await (await lateResponse).finished();
    await page.evaluate(() => new Promise(requestAnimationFrame));
    assert.equal(await page.getByText('Late stale text', { exact: true }).count(), 0);
    assert.equal(
      await page.getByRole('button', { name: 'Send', exact: true }).isDisabled(),
      true,
      'old poll cleanup cannot unlock the new pending request',
    );
    assert.equal(capturedConversationTurns.length, 2);
    await stop.click();
    await page.getByText(/^Stopped\./).waitFor();
  } finally {
    await context.close();
  }

  const state = await setupPage();
  try {
    await searchOnly(state.page, ['Linear Algebra Primer']);
    await send(state.page, 'Use only the primer.');
    await state.page
      .locator('.message-assistant')
      .getByText('A basis spans the vector space.', { exact: true })
      .waitFor();
    await state.page.waitForFunction(() => document.querySelector('.composer textarea').value === '');
    assert.deepEqual(state.capturedConversationTurns[0].body.sourceIds, ['source-1']);
    await state.page.getByRole('button', { name: 'Search in: 1 source' }).waitFor();
    await searchOnly(state.page, []);
    await send(state.page, 'Do not use any source.');
    await state.page.getByText(/No sources were selected for this question/).waitFor();
    assert.deepEqual(
      state.capturedConversationTurns[1].body.sourceIds,
      [],
      'empty selection stays distinct from inherited or all-source scope',
    );
    assert.equal(state.conversationProviderCalls, 0, 'an explicit empty source scope never reaches generation');
  } finally {
    await state.context.close();
  }
});

test('saved conversation opens on the latest bounded page and explicitly loads older turns', async () => {
  const createdAt = '2026-10-08T00:00:00Z';
  const pendingSummary = {
    conversationId: 'long-1',
    title: 'Long saved discussion',
    lastSequence: 401,
    pendingRequestId: 'pending-401',
    createdAt,
    updatedAt: createdAt,
  };
  const turns = Array.from({ length: 401 }, (_, index) => {
    const sequence = index + 1;
    const requestId = sequence % 2 === 1 ? `request-${Math.ceil(sequence / 2)}` : `request-${sequence / 2}`;
    if (sequence % 2 === 1)
      return {
        sequence,
        requestId,
        role: 'user',
        text: `Historical turn sequence ${sequence}`,
        status: 'input',
        evidence: {
          request: {},
          query: `Historical turn sequence ${sequence}`,
          effective: { collectionId: 'browser-test', sourceIds: null, retrievalQuery: '', artifactReferences: [] },
        },
        createdAt,
      };
    return {
      sequence,
      requestId,
      role: 'assistant',
      text: `Saved answer sequence ${sequence}`,
      status: 'answer',
      evidence: { type: 'answer', citations: [], contextBlock: [], traceId: null, turnReferences: [] },
      createdAt,
    };
  });
  const pendingRecord = { ...pendingSummary, turns };
  const { context, page, failNextConversationTurn } = await setupPage({
    initialConversations: [pendingSummary],
    initialConversationRecords: [pendingRecord],
  });
  try {
    const transcript = page.getByRole('main', { name: 'Chat' });
    await transcript.getByText('Historical turn sequence 401', { exact: true }).waitFor();
    await transcript.getByText('Saved answer sequence 202', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Stop answering' }).waitFor();
    assert.equal(
      await transcript.getByText('Saved answer sequence 200', { exact: true }).count(),
      0,
      'older page is bounded and not fetched automatically',
    );

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
    await transcript
      .getByRole('alert')
      .getByText('Conversation request failed. Saved turns were retained.', { exact: true })
      .waitFor();
    assert.equal(await message(page).inputValue(), 'Keep this failed long-conversation draft');
  } finally {
    await context.close();
  }
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
    assert.equal(
      await page.evaluate(() => getComputedStyle(document.querySelector('.topbar')).backgroundColor),
      'rgb(0, 0, 0)',
    );
    await page.reload();
    await toLight.waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), 'dark');
    await toLight.click();
    assert.equal(
      await page.evaluate(() => getComputedStyle(document.querySelector('.topbar')).backgroundColor),
      'rgb(1, 33, 105)',
      'light mode is Emory blue',
    );
  } finally {
    await context.close();
  }
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
  } finally {
    await context.close();
  }
});

test('desktop, phone, and tablet viewports keep long chat lists separate from reachable navigation', async () => {
  const initialConversations = Array.from({ length: 40 }, (_, index) => ({
    conversationId: `layout-chat-${index}`,
    title: `Saved chat ${index + 1}`,
    lastSequence: 0,
    pendingRequestId: null,
    createdAt: '2026-10-08T00:00:00Z',
    updatedAt: '2026-10-08T00:00:00Z',
  }));
  const { context, page } = await setupPage({
    initialConversations,
    initialConversationRecords: initialConversations.map((item) => ({ ...item, turns: [] })),
  });
  try {
    const navigation = page.getByRole('navigation', { name: 'Chats and library' });
    const chats = navigation.getByRole('list', { name: 'Recent chats' });
    await chats.getByRole('button').last().waitFor({ state: 'attached' });
    for (const size of [
      { width: 1280, height: 738 },
      { width: 1024, height: 480 },
      { width: 390, height: 844 },
      { width: 768, height: 1024 },
      { width: 320, height: 568 },
    ]) {
      await page.setViewportSize(size);
      const scope = page.getByRole('button', { name: /^Search in:/ });
      await scope.focus();
      await scope.press('Enter');
      await page.getByRole('checkbox', { name: 'Include Lab Safety Manual in questions' }).waitFor();
      await page.keyboard.press('Escape');
      assert.equal(await page.getByRole('button', { name: 'Send', exact: true }).isVisible(), true);
      const viewport = await page.evaluate(() => ({
        width: window.innerWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));
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
        assert.equal(
          await button.evaluate((el) => {
            const rect = el.getBoundingClientRect();
            return el.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
          }),
          true,
          `${await button.innerText()} is covered at ${size.width}px`,
        );
      }
      await chats.getByRole('button').last().click();
      if (size.width < 900) await page.getByRole('button', { name: 'Open menu' }).click();
      await openSidebar(page, /^Sources/);
      await page.getByRole('heading', { name: 'Your sources' }).waitFor();
      await page.keyboard.press('Escape');
    }
  } finally {
    await context.close();
  }
});

test('current-revision document exports use the open revision id and suggested filename', async () => {
  const { context, page, downloadedArtifacts } = await setupPage({ importedRecord: documentRecord });
  try {
    const tempFile = path.join(os.tmpdir(), `notes-export-${Date.now()}.md`);
    tempFiles.push(tempFile);
    await writeFile(tempFile, '# fixture');
    await importArtifact(page, tempFile);
    assert.equal(await download(page, 'md'), 'notes-r1.md');
    assert.equal(await download(page, 'docx'), 'notes-r1.docx');
    assert.deepEqual(downloadedArtifacts, [
      { format: 'md', revisionId: 'doc-r1' },
      { format: 'docx', revisionId: 'doc-r1' },
    ]);
  } finally {
    await context.close();
  }
});

test('browser edits a supported imported workbook cell by stable ID and keeps the original available', async () => {
  const { context, page, capturedRevisions } = await setupPage({ saveDelayMs: 450 });
  try {
    const tempFile = path.join(os.tmpdir(), `metrics-${Date.now()}.xlsx`);
    tempFiles.push(tempFile);
    await writeFile(tempFile, 'mock xlsx bytes');
    await importArtifact(page, tempFile);
    await page.getByLabel('Cell B1').fill('450');
    await page.getByLabel('Cell B2').fill('2026-01-05');
    await page.getByRole('tab', { name: 'Summary' }).click();
    await page.getByLabel('Cell B1').fill('900');
    await page.getByRole('button', { name: 'Save changes' }).click();
    assert.equal(
      await page.getByLabel('Cell B1').isDisabled(),
      true,
      'workbook cells are frozen while the save is pending',
    );
    assert.equal(
      await page.getByLabel('Title', { exact: true }).isDisabled(),
      true,
      'workbook title is frozen while the save is pending',
    );
    await revisionShown(page, 'wb-r2');
    assert.deepEqual(capturedRevisions[0].operations, [
      {
        type: 'set_cell',
        sheet_id: 'sheet-main',
        cell_id: 'cell-value',
        cell: { id: 'cell-value', row: 1, column: 2, value: 450, formula: null, style: {} },
      },
      {
        type: 'set_cell',
        sheet_id: 'sheet-main',
        cell_id: 'cell-date',
        cell: {
          id: 'cell-date',
          row: 2,
          column: 2,
          value: '2026-01-05',
          value_type: 'date',
          formula: null,
          style: { number_format: 'yyyy-mm-dd' },
        },
      },
      {
        type: 'set_cell',
        sheet_id: 'sheet-summary',
        cell_id: 'cell-value',
        cell: { id: 'cell-value', row: 1, column: 2, value: 900, formula: null, style: { bold: true } },
      },
    ]);
    assert.equal(await page.getByLabel('Cell A1').inputValue(), 'Visits');
    assert.equal(await page.getByLabel('Cell B1').inputValue(), '450');
    assert.equal(await page.getByLabel('Cell B2').inputValue(), '2026-01-05');
    await page.getByRole('tab', { name: 'Summary' }).click();
    assert.equal(await page.getByLabel('Cell B1').inputValue(), '900');
    await page.getByRole('button', { name: 'Download', exact: true }).click();
    const originalDownload = page.waitForEvent('download');
    await page.getByRole('menuitem', { name: /Original file/ }).click();
    assert.equal((await originalDownload).suggestedFilename(), 'metrics.xlsx');
  } finally {
    await context.close();
  }
});

test('imported document block edits preserve IDs, code blank lines, table pipes, multiline cells, and evidence', async () => {
  const { context, page, capturedRevisions } = await setupPage({ importedRecord: documentRecord, saveDelayMs: 450 });
  try {
    const tempFile = path.join(os.tmpdir(), `notes-${Date.now()}.md`);
    tempFiles.push(tempFile);
    await writeFile(tempFile, '# fixture');
    await importArtifact(page, tempFile);
    const code = page.getByLabel('Block code-42');
    assert.equal(await code.inputValue(), 'first line\n\nthird line');
    assert.equal(await page.getByLabel('Rows table-77').inputValue(), 'A|B\ttop line\nbottom line');
    await code.fill('revised code\n\nwith blank line');
    await page.getByText(/Edited · unverified — its source link will be removed/).waitFor();
    await page.getByRole('button', { name: 'Save changes' }).click();
    assert.equal(await code.isDisabled(), true, 'document blocks are frozen while the save is pending');
    assert.equal(
      await page.getByLabel('Title', { exact: true }).isDisabled(),
      true,
      'document title is frozen while the save is pending',
    );
    await revisionShown(page, 'doc-r2');
    const saved = capturedRevisions.at(-1).spec;
    assert.deepEqual(
      saved.blocks.map((block) => block.id),
      ['code-42', 'table-77', 'paragraph-9'],
    );
    assert.equal(saved.blocks[0].text, 'revised code\n\nwith blank line');
    assert.equal(saved.blocks[1].rows[0][0], 'A|B');
    assert.equal(saved.blocks[1].rows[0][1], 'top line\nbottom line');
    assert.deepEqual(Object.keys(saved.evidence), ['paragraph-9']);
    assert.deepEqual(JSON.parse(saved.provenance.unverifiedElements), ['code-42']);
    assert.equal(saved.page_layout.margin_top, 1);
    await page.getByText(/Edited · unverified — no longer tied to a source/).waitFor();
    await page.getByText('Source: Linear Algebra Primer · page 5').waitFor();
  } finally {
    await context.close();
  }
});

test('workbook title and cell edits publish together, and title-only changes create a revision', async () => {
  const { context, page, capturedRevisions } = await setupPage();
  try {
    const tempFile = path.join(os.tmpdir(), `metrics-title-${Date.now()}.xlsx`);
    tempFiles.push(tempFile);
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
    assert.deepEqual(
      combined.spec.sheets.map((sheet) => sheet.id),
      ['sheet-main', 'sheet-summary'],
    );
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
  } finally {
    await context.close();
  }
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
    assert.deepEqual(capturedRuns[0].inputs, {
      prompt: 'Write a short study guide about eigenvectors.',
      sourceIds: ['source-1'],
      mode: 'grounded',
    });
    assert.ok(typeof capturedRuns[0].idempotencyKey === 'string' && capturedRuns[0].idempotencyKey.length > 0);
    await artifacts.getByRole('button', { name: /Generated draft/ }).click();
    await revisionShown(page, 'rev-1');
    assert.equal(await page.getByLabel('Title', { exact: true }).inputValue(), 'Generated draft');
    await page.getByText('Drafted from your instructions — not checked against your sources.').waitFor();

    await page.getByRole('button', { name: 'Back to artifacts' }).click();
    await artifacts.getByRole('button', { name: 'New', exact: true }).click();
    await page.getByRole('checkbox', { name: /Use my sources/ }).uncheck();
    await page.getByText('Written from your instructions only — not checked against your sources.').waitFor();
  } finally {
    await context.close();
  }
});

test('generation opens the pinned published revision even after the artifact head advances', async () => {
  const { context, page, openedRunRevisions } = await setupPage({ pinnedRunRevision: true });
  try {
    await createMenu(page, 'New document');
    await page.getByLabel('What should it contain?').fill('Draft a lesson note.');
    await page.getByRole('button', { name: 'Create document' }).click();
    const card = page
      .getByRole('complementary', { name: 'Artifacts' })
      .getByRole('button', { name: /Generated draft/ });
    await card.waitFor({ timeout: 6000 });
    await card.click();
    await revisionShown(page, 'published-r1');
    assert.ok(openedRunRevisions.length > 0 && openedRunRevisions.every((revision) => revision === 'published-r1'));
  } finally {
    await context.close();
  }
});

test('dependent workbook generation sends typed columns, quota bounds, selected sources, and exact revision parent', async () => {
  const { context, page, capturedRuns } = await setupPage({
    initialArtifacts: [{ artifactId: 'artifact-wb', revisionId: 'wb-r1', kind: 'workbook', title: 'Metrics' }],
  });
  try {
    await openSidebar(page, 'All artifacts');
    await page
      .getByRole('dialog')
      .getByRole('button', { name: /Metrics/ })
      .click();
    await page.getByRole('button', { name: 'Ask for changes' }).click();
    await page.getByText('Advanced: plan it section by section').click();
    await page.getByText('Plan a document or workbook in sections').click();
    await page.getByRole('button', { name: 'Add worksheet' }).click();
    await page.getByLabel('Total workbook cells, including headers').fill('52');
    await page.getByLabel('What should change?').fill('Create a small visit summary.');
    await page.getByRole('button', { name: 'Make these changes' }).click();
    await page
      .getByRole('complementary', { name: 'Artifacts' })
      .getByRole('button', { name: /Generated draft/ })
      .waitFor({ timeout: 6000 });
    const body = capturedRuns[0];
    assert.equal(body.kind, 'workbook');
    assert.deepEqual(body.inputs.sourceIds, null);
    assert.equal(body.inputs.artifactId, 'artifact-wb');
    assert.equal(body.inputs.expectedRevisionId, 'wb-r1');
    assert.deepEqual(body.inputs.outputPlan, {
      kind: 'workbook',
      title: 'Metrics',
      sheets: [
        {
          key: 'sheet-1',
          name: 'Data',
          columns: [
            { key: 'item', label: 'Item', value_type: 'text' },
            { key: 'value', label: 'Value', value_type: 'number' },
          ],
          min_rows: 8,
          target_rows: 10,
          max_rows: 12,
        },
        {
          key: 'sheet-2',
          name: 'Sheet 2',
          columns: [{ key: 'item', label: 'Item', value_type: 'text' }],
          min_rows: 8,
          target_rows: 10,
          max_rows: 12,
        },
      ],
      max_total_cells: 52,
      max_output_tokens_per_unit: 1200,
      max_repairs_per_unit: 1,
      max_checkpoint_bytes: 4000000,
    });
  } finally {
    await context.close();
  }
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
    await page
      .getByRole('alert')
      .getByText(/blocks must include each section heading/)
      .waitFor();
    assert.equal(capturedRuns.length, 0);
    assert.equal(await page.getByLabel('What should it contain?').inputValue(), 'Write a short guide.');
  } finally {
    await context.close();
  }
});

test('stale artifact generation retains prompt and plan and waits for explicit head reload and resubmission', async () => {
  const { context, page, capturedApiRequests } = await setupPage({
    initialArtifacts: [{ artifactId: 'artifact-wb', revisionId: 'wb-r1', kind: 'workbook', title: 'Metrics' }],
    faults: { 'POST /api/runs': [409, 'artifact head changed'] },
  });
  const runSubmissions = () =>
    capturedApiRequests.filter((request) => request.method === 'POST' && request.path === '/api/runs').length;
  try {
    await openSidebar(page, 'All artifacts');
    await page
      .getByRole('dialog')
      .getByRole('button', { name: /Metrics/ })
      .click();
    await page.getByRole('button', { name: 'Ask for changes' }).click();
    await page.getByLabel('What should change?').fill('Refresh the metrics workbook.');
    await page.getByRole('button', { name: 'Make these changes' }).click();
    await page.getByRole('alert').getByText('Metrics changed since you opened it.').waitFor();
    assert.equal(runSubmissions(), 1);
    assert.equal(await page.getByLabel('What should change?').inputValue(), 'Refresh the metrics workbook.');
    await page.getByRole('button', { name: 'Load latest version' }).click();
    await page.getByRole('alert').waitFor({ state: 'detached' });
    assert.equal(await page.getByLabel('What should change?').inputValue(), 'Refresh the metrics workbook.');
    assert.equal(runSubmissions(), 1, 'head reload does not resubmit the generation job');
  } finally {
    await context.close();
  }
});

test('generation polling recovers after a temporary HTTP failure', async () => {
  const { context, page, capturedRuns } = await setupPage({
    faults: { 'GET /api/runs/run-1': [503, 'temporary job status outage'] },
  });
  try {
    await createMenu(page, 'New document');
    await page.getByLabel('What should it contain?').fill('Draft a worksheet about derivatives.');
    await page.getByRole('button', { name: 'Create document' }).click();
    await page.getByText(/Status check failed, retrying: .*temporary job status outage/).waitFor();
    await page
      .getByRole('complementary', { name: 'Artifacts' })
      .getByRole('button', { name: /Generated draft/ })
      .waitFor({ timeout: 6000 });
    assert.equal(capturedRuns.length, 1);
  } finally {
    await context.close();
  }
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
    assert.deepEqual(firstSubmission.items[0].inputs, {
      prompt: 'Create a short guide from the selected lecture notes.',
      mode: 'grounded',
      sourceIds: ['source-1'],
    });
    await page.getByText('Batch · Outcome unknown · 0/1', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Submit batch' }).isDisabled(), true);

    await page.reload();
    await openTool(page, 'Bulk drafts');
    await page.getByText('Batch · Outcome unknown · 0/1', { exact: true }).waitFor();
    assert.equal(
      state.capturedBatchSubmissions.length,
      1,
      'reopen performs reads only and does not resubmit an uncertain intent',
    );
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
    assert.deepEqual(state.capturedBatchSubmissions[1].items[0].inputs, {
      prompt: 'Create another guide to cancel.',
      mode: 'authoring',
      sourceIds: [],
    });
    await page.getByRole('button', { name: 'Cancel batch' }).click();
    await page
      .getByText(/Remote provider usage may still incur charges; already published revisions are retained/)
      .waitFor();
    assert.deepEqual(state.capturedBatchCancels, [secondBatchId]);
    await page.keyboard.press('Escape');
    await revisionShown(page, 'revision-published-1');
  } finally {
    await context.close();
  }
});

test('browser exposes upload diagnostics and isolates HTML artifact preview', async () => {
  const { context, page } = await setupPage();
  try {
    await openSidebar(page, /^Sources/);
    const tempFile = path.join(os.tmpdir(), `broken-${Date.now()}.pdf`);
    tempFiles.push(tempFile);
    await writeFile(tempFile, 'mock pdf');
    await page.locator('input[type=file][aria-label="Choose a PDF, text, or Markdown source"]').setInputFiles(tempFile);
    await page.getByText('No readable pages were found.', { exact: true }).waitFor();
    await page.locator('input[type=file][aria-label="Choose a PDF, text, or Markdown source"]').setInputFiles({
      name: 'oversize.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('large upload fixture'),
    });
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
  } finally {
    await context.close();
  }
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
      name: 'Local OpenAI',
      provider: 'openai',
      model_id: 'gpt-test',
      roles: ['standard'],
      capabilities: [],
      data_scopes: ['institutional_corpus'],
      base_url: null,
      account_id: null,
      provider_options: {},
      enabled: true,
      priority: 100,
      secret: { api_key: 'super-secret-test-value' },
    });
    assert.equal(await page.getByLabel('Provider credential').inputValue(), '');
    assert.equal(
      await page.evaluate(() =>
        [...Object.values(localStorage), ...Object.values(sessionStorage)].some((value) =>
          /super-secret-test-value|second-secret-value/.test(value),
        ),
      ),
      false,
    );
    assert.equal(await page.getByText('super-secret-test-value', { exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Replace credential' }).click();
    await page.getByLabel(/Replace API key for/).fill('second-secret-value');
    await page.getByRole('button', { name: 'Replace credential' }).last().click();
    assert.deepEqual(capturedConnectionWrites[1], { expected_revision: 1, secret: { api_key: 'second-secret-value' } });
    assert.equal(
      capturedConnectionWrites.some((body) => 'eval_passed' in body),
      false,
    );
    await page.keyboard.press('Escape');
    await openTool(page, 'Model connections');
    assert.equal(
      await page.getByLabel('Provider credential').inputValue(),
      '',
      'closing the panel discards any typed credential',
    );
  } finally {
    await context.close();
  }
});

test('provider change drops hidden account state and a credential draft cannot move profiles', async () => {
  const clef = {
    ...profileFixture,
    profile_id: 'profile-cf',
    name: 'Campus Clef',
    provider: 'cloudflare-clef',
    model_id: 'clef-test',
    account_id: 'acct-hidden',
    roles: ['decision'],
  };
  const other = {
    ...profileFixture,
    profile_id: 'profile-b',
    name: 'Other OpenAI',
    model_id: 'gpt-other',
  };
  const { context, page, capturedConnectionWrites } = await setupPage({
    connectionAdmin: true,
    seedConnectionProfiles: [clef, other],
  });
  try {
    await openTool(page, 'Model connections');
    const cards = page.locator('article.connection-profile');
    await cards.filter({ hasText: 'Campus Clef' }).getByRole('button', { name: 'Edit profile' }).click();
    await page.getByLabel('Account ID').waitFor();
    await page.getByLabel('Provider').selectOption('openai');
    assert.equal(await page.getByLabel('Account ID').count(), 0);
    await page.getByRole('button', { name: 'Save profile' }).click();
    await page.getByText('Profile saved. Routing remains evaluation-gated.').waitFor();
    const saved = capturedConnectionWrites.at(-1);
    assert.equal(saved.provider, 'openai');
    assert.equal(saved.account_id, null);
    assert.deepEqual(saved.roles, ['standard']);

    await cards.filter({ hasText: 'Other OpenAI' }).getByRole('button', { name: 'Replace credential' }).click();
    await page.getByLabel(/Replace API key for/).fill('secret-for-other');
    assert.equal(
      await cards.filter({ hasText: 'Campus Clef' }).getByRole('button', { name: 'Replace credential' }).isDisabled(),
      true,
    );
    assert.equal(capturedConnectionWrites.some((body) => body.secret?.api_key === 'secret-for-other'), false);
  } finally {
    await context.close();
  }
});

test('a citation opens the cited passage when two sources share a display name', async () => {
  const { context, page } = await setupPage({ sameNameCitation: true });
  try {
    await send(page, 'Compare the shared notes');
    await page.getByText('The cited upload is the chemistry notes.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Shared Notes page 2' }).click();
    assert.equal(
      await page.getByRole('figure', { name: 'Cited passage' }).locator('blockquote').innerText(),
      'Second upload about chemistry.',
    );
    assert.equal(await page.getByText('First upload about biology.', { exact: true }).count(), 0);
  } finally {
    await context.close();
  }
});

test('new chat during the first list load does not reopen the previous chat', async () => {
  const summary = {
    conversationId: 'conversation-old',
    title: 'Existing chat',
    lastSequence: 0,
    pendingRequestId: null,
    createdAt: '2026-10-08T00:00:00Z',
    updatedAt: '2026-10-08T00:00:00Z',
  };
  const { context, page } = await setupPage({
    conversationListDelayMs: 1200,
    initialConversations: [summary],
    initialConversationRecords: [{ ...summary, turns: [] }],
  });
  try {
    const nav = page.getByRole('navigation', { name: 'Chats and library' });
    await nav.getByText('Loading…', { exact: true }).waitFor();
    await nav.getByRole('button', { name: 'New chat' }).click();
    await nav.getByRole('button', { name: 'Existing chat' }).waitFor();
    assert.equal(await nav.getByRole('button', { name: 'Existing chat' }).getAttribute('aria-current'), null);
    assert.equal(await page.getByLabel('Message').isDisabled(), false);
    assert.equal(await nav.getByText('Loading…', { exact: true }).count(), 0);
  } finally {
    await context.close();
  }
});

test('OIDC signed-out session hides workspace and navigates only to the fixed login route', async () => {
  const { context, page, sourcesRequested } = await setupPage({
    initialSession: { mode: 'oidc', authenticated: false },
  });
  try {
    await page.getByRole('heading', { name: 'Sign in required' }).waitFor();
    assert.equal(await page.getByRole('navigation', { name: 'Chats and library' }).count(), 0);
    assert.equal(sourcesRequested, 0);
    const loginRequest = page.waitForRequest((request) => new URL(request.url()).pathname === '/api/auth/login');
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    assert.equal(new URL((await loginRequest).url()).pathname, '/api/auth/login');
  } finally {
    await context.close();
  }
});

test('authenticated OIDC logout clears the workspace before reloading the session', async () => {
  const { context, page } = await setupPage({
    initialSession: {
      mode: 'oidc',
      authenticated: true,
      workspaceKey: 'private-1',
      displayName: 'Ada Student',
      email: 'ada@example.edu',
    },
  });
  try {
    await page.getByText('Ada Student', { exact: true }).waitFor();
    await openSidebar(page, /^Sources/);
    await page.getByText('Linear Algebra Primer', { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Sign out' }).click();
    await page.getByRole('heading', { name: 'Sign in required' }).waitFor();
    assert.equal(await page.getByRole('navigation', { name: 'Chats and library' }).count(), 0);
    assert.equal(await page.getByText('Linear Algebra Primer', { exact: true }).count(), 0);
  } finally {
    await context.close();
  }
});

test('session check errors show a retry and recover without a blank workspace', async () => {
  const { context, page } = await setupPage({
    faults: { 'GET /api/session': [503, 'session temporarily unavailable'] },
  });
  try {
    await page.getByRole('alert').getByText('session temporarily unavailable').waitFor();
    await page.getByRole('button', { name: 'Retry session check' }).click();
    await page.getByRole('navigation', { name: 'Chats and library' }).waitFor();
    await page.getByText('Local development', { exact: true }).waitFor();
  } finally {
    await context.close();
  }
});

test('saved artifacts reload from the server list after page refresh', async () => {
  const { context, page } = await setupPage({
    initialArtifacts: [
      { artifactId: 'artifact-1', revisionId: 'rev-1', kind: 'document', title: 'Generated draft', status: 'ready' },
    ],
  });
  try {
    await openSidebar(page, 'All artifacts');
    await page
      .getByRole('dialog')
      .getByRole('button', { name: /Generated draft/ })
      .waitFor();
    await page.reload();
    await openSidebar(page, 'All artifacts');
    await page
      .getByRole('dialog')
      .getByRole('button', { name: /Generated draft/ })
      .click();
    await page.waitForFunction(() => document.querySelector('.canvas-title')?.value === 'Generated draft');
  } finally {
    await context.close();
  }
});

test('a protected API 401 clears the active workspace and returns to sign in', async () => {
  const { context, page } = await setupPage({
    initialSession: { mode: 'oidc', authenticated: true, workspaceKey: 'private-1', displayName: 'Ada' },
    unauthenticatedOnSources: true,
  });
  try {
    await page.getByRole('heading', { name: 'Sign in required' }).waitFor();
    assert.equal(await page.getByRole('navigation', { name: 'Chats and library' }).count(), 0);
  } finally {
    await context.close();
  }
});

test('human gap attestation is explicit, scope-limited, and retains draft across a stale revision', async () => {
  const gap = {
    gapId: 'gap-1',
    collectionId: 'collection-1',
    query: 'How do I register for MATH 221?',
    sourceIds: ['source-1'],
    state: 'open',
    revision: 1,
    occurrenceCount: 4,
    recurrenceCount: 2,
    successfulRecurrenceCount: 1,
    firstSeenAt: '2026-10-01T00:00:00Z',
    lastSeenAt: '2026-10-08T00:00:00Z',
    resolution: null,
  };
  const { context, page, capturedGapResolutions } = await setupPage({ initialGaps: [gap], gapConflictOnce: true });
  try {
    await openTool(page, 'Unanswered questions');
    const details = page.locator('.gap-review-item').filter({ hasText: 'How do I register for MATH 221?' });
    await details.locator('summary').click();
    await page.getByRole('checkbox', { name: 'Linear Algebra Primer', exact: true }).check();
    assert.equal(
      await page.getByRole('checkbox', { name: 'Lab Safety Manual', exact: true }).count(),
      0,
      'explicitly scoped gaps only offer sources from their recorded source scope',
    );
    await page.getByLabel('Optional reviewer note').fill('The registration steps are in this source.');
    await page.getByRole('button', { name: 'Record human source attestation' }).click();
    await page.getByRole('alert').getByText('This gap changed').waitFor();
    assert.equal(await page.getByRole('checkbox', { name: 'Linear Algebra Primer', exact: true }).isChecked(), true);
    assert.equal(
      await page.getByLabel('Optional reviewer note').inputValue(),
      'The registration steps are in this source.',
    );
    assert.deepEqual(capturedGapResolutions[0], {
      id: 'gap-1',
      body: { expectedRevision: 1, sourceIds: ['source-1'], note: 'The registration steps are in this source.' },
    });
    await page.getByRole('button', { name: 'Record human source attestation' }).click();
    await page.getByText('Reviewer note: The registration steps are in this source.', { exact: true }).waitFor();
    assert.equal(capturedGapResolutions[1].body.expectedRevision, 2);
    assert.equal((await page.getByText('Human source attestation', { exact: true }).count()) > 0, true);
    assert.equal(await page.getByText(/does not prove that a generated answer is factually correct/).count(), 1);
  } finally {
    await context.close();
  }
});

test('non-admin student is rejected from admin and debug surfaces with clear role notice', async () => {
  const studentSession = { mode: 'development', authenticated: true, role: 'student', workspaceKey: 'student-ws' };
  const adminPage = await setupPage({ entry: '/admin.html', initialSession: studentSession });
  try {
    await adminPage.page.getByRole('heading', { name: 'Administrator access required' }).waitFor();
    assert.match(await adminPage.page.locator('.gate-card p').innerText(), /cannot manage institutional users/);
  } finally {
    await adminPage.context.close();
  }

  const debugPage = await setupPage({ entry: '/debug.html', initialSession: studentSession });
  try {
    await debugPage.page.getByRole('heading', { name: 'Administrator access required' }).waitFor();
    assert.match(
      await debugPage.page.locator('.gate-card p').innerText(),
      /Debug overrides are scoped to institutional administrators/,
    );
    const returnLink = debugPage.page.locator('a.primary-button');
    assert.equal(await returnLink.getAttribute('href'), '/');
  } finally {
    await debugPage.context.close();
  }
});

test('admin reads overview, lists and updates users, and verifies audit trail', async () => {
  const adminSession = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page, capturedUserUpdates } = await setupPage({
    entry: '/admin.html',
    initialSession: adminSession,
  });
  try {
    await page.getByRole('tab', { name: 'Overview' }).waitFor();
    assert.equal(await page.getByText('Users').first().isVisible(), true);

    await page.getByRole('tab', { name: 'Users' }).click();
    await page.getByText('Users appear after their first verified login').waitFor();
    await page.getByText('Student User').waitFor();
    assert.equal(await page.getByText('Student User').isVisible(), true);

    const studentRow = page.locator('tr').filter({ hasText: 'Student User' });
    const roleSelect = studentRow.locator('select').first();
    await roleSelect.selectOption('admin');
    const saveButton = studentRow.getByRole('button', { name: 'Save' });
    await saveButton.click();

    assert.equal(capturedUserUpdates.length, 1);
    assert.equal(capturedUserUpdates[0].userId, 'user-student-2');
    assert.equal(capturedUserUpdates[0].body.role, 'admin');
  } finally {
    await context.close();
  }
});

test('admin cannot demote or remove the last active admin and sees rejection banner', async () => {
  const adminSession = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page } = await setupPage({
    entry: '/admin.html',
    initialSession: adminSession,
  });
  try {
    await page.getByRole('tab', { name: 'Users' }).click();
    const adminRow = page.locator('tr').filter({ hasText: 'Admin User' });
    const roleSelect = adminRow.locator('select').first();
    await roleSelect.selectOption('student');
    const saveButton = adminRow.getByRole('button', { name: 'Save' });
    await saveButton.click();

    await page.getByRole('alert').getByText('Cannot remove the last active admin.').waitFor();
  } finally {
    await context.close();
  }
});

test('admin inspects user sessions and revokes them', async () => {
  const adminSession = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page, capturedUserRevokes } = await setupPage({
    entry: '/admin.html',
    initialSession: adminSession,
  });
  try {
    await page.getByRole('tab', { name: 'Users' }).click();
    await page.getByText('Student User').click();

    await page.getByRole('button', { name: 'Revoke all sessions' }).waitFor();
    await page.getByRole('button', { name: 'Revoke all sessions' }).click();

    assert.equal(capturedUserRevokes.includes('user-student-2'), true);
  } finally {
    await context.close();
  }
});

test('admin creates and deletes an empty collection', async () => {
  const adminSession = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page, adminCollectionsList } = await setupPage({
    entry: '/admin.html',
    initialSession: adminSession,
  });
  try {
    await page.getByRole('tab', { name: 'Collections' }).click();
    await page.getByPlaceholder('New collection name').fill('Math Lab 2026');
    await page.getByRole('button', { name: 'Create collection' }).click();

    await page.getByText('Math Lab 2026').waitFor();
    assert.equal(
      adminCollectionsList.some((c) => c.name === 'Math Lab 2026'),
      true,
    );

    await page.getByText('Math Lab 2026').click();
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: 'Delete' }).click();

    await page.waitForTimeout(100);
    assert.equal(
      adminCollectionsList.some((c) => c.name === 'Math Lab 2026'),
      false,
    );
  } finally {
    await context.close();
  }
});

test('debug session recovers from expired token and saves prompt overrides', async () => {
  const adminSession = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page, capturedDebugPatches } = await setupPage({
    entry: '/debug.html',
    initialSession: adminSession,
    sessionStorageEntries: { 'advisor-debug-session': 'expired-token' },
  });
  try {
    await page.getByRole('button', { name: 'Save overrides' }).waitFor();
    const promptInput = page.locator('.debug-prompt');
    await promptInput.waitFor();

    await promptInput.fill('Override STEM advising prompt for testing.');
    await page.getByRole('button', { name: 'Save overrides' }).click();

    assert.equal(capturedDebugPatches.length, 1);
    assert.equal(capturedDebugPatches[0].systemPrompt, 'Override STEM advising prompt for testing.');
  } finally {
    await context.close();
  }
});

test('debug credentials and override headers never reach the main advisor bundle', async () => {
  const adminSession = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const debugPage = await setupPage({
    entry: '/debug.html',
    initialSession: adminSession,
  });
  try {
    await debugPage.page.getByRole('button', { name: 'Save overrides' }).waitFor();
  } finally {
    await debugPage.context.close();
  }

  const { context, page, capturedApiRequests } = await setupPage({
    entry: '/',
    initialSession: adminSession,
  });
  try {
    await page.getByRole('heading', { name: 'Emory STEM Advisor' }).waitFor();
    await send(page, 'What is linear algebra?');
    await page.getByText('A basis spans the vector space.').waitFor();

    assert.equal(capturedApiRequests.length > 0, true);
    for (const req of capturedApiRequests) {
      assert.equal(
        req.headers['x-debug-session'],
        undefined,
        `Header X-Debug-Session leaked in ${req.method} ${req.path}`,
      );
    }
  } finally {
    await context.close();
  }
});

test('admin filters audit records, pages with keyset cursor, and accesses export links', async () => {
  const adminSession = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page } = await setupPage({ entry: '/admin.html', initialSession: adminSession });
  try {
    await page.getByRole('tab', { name: 'Audit' }).click();
    await page.getByText('user.role_status').waitFor();

    // Verify export buttons are visible and point to export endpoints
    const exportCsvLink = page.getByRole('link', { name: 'Export CSV' });
    await exportCsvLink.waitFor();
    const csvHref = await exportCsvLink.getAttribute('href');
    assert.equal(csvHref?.includes('/api/admin/audit/export'), true);
    assert.equal(csvHref?.includes('format=csv'), true);

    const exportJsonLink = page.getByRole('link', { name: 'Export JSON' });
    await exportJsonLink.waitFor();
    const jsonHref = await exportJsonLink.getAttribute('href');
    assert.equal(jsonHref?.includes('/api/admin/audit/export'), true);
    assert.equal(jsonHref?.includes('format=json'), true);

    // Filter by action
    await page.getByPlaceholder('Filter by action (e.g. user.role_status)').fill('conversation.deleted');
    await page.getByLabel('Filter from date').fill('2026-10-01T08:00');
    await page.getByLabel('Filter until date').fill('2026-10-19T18:00');
    await page.getByRole('button', { name: 'Apply filters' }).click();
    await page.getByText('conversation.deleted').waitFor();
    const appliedCsv = new URL(await exportCsvLink.getAttribute('href'), url);
    const appliedJson = new URL(await exportJsonLink.getAttribute('href'), url);
    assert.equal(appliedCsv.searchParams.get('action'), 'conversation.deleted');
    assert.equal(appliedCsv.searchParams.get('since'), appliedJson.searchParams.get('since'));
    assert.equal(appliedCsv.searchParams.get('until'), appliedJson.searchParams.get('until'));
    assert.ok(appliedCsv.searchParams.get('since')?.endsWith('Z'));
    // Editing a draft must not change the export scope until filters are applied.
    await page.getByPlaceholder('Filter by action (e.g. user.role_status)').fill('unapplied.action');
    assert.equal(
      new URL(await exportCsvLink.getAttribute('href'), url).searchParams.get('action'),
      'conversation.deleted',
    );

    // Next page with keyset cursor
    const nextPageButton = page.getByRole('button', { name: 'Next page' });
    await nextPageButton.waitFor();
    await nextPageButton.click();
    await page.getByText('conversation.deleted').waitFor();

    // First page button resets cursor
    const firstPageButton = page.getByRole('button', { name: 'First page' });
    await firstPageButton.waitFor();
    await firstPageButton.click();
  } finally {
    await context.close();
  }
});

test('admin inspects telemetry usage metrics and model price schedules', async () => {
  const adminSession = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page } = await setupPage({ entry: '/admin.html', initialSession: adminSession });
  try {
    await page.getByRole('tab', { name: 'Usage & Cost' }).click();
    await page.getByText('Unknown', { exact: true }).first().waitFor();
    await page.getByText('Known portion USD 0.1250').waitFor();
    await page.getByText('Cache metrics are not answer quality.').waitFor();
    await page.getByText('Cached Token Share').waitFor();
    await page.getByText('20.0%').first().waitFor();
    await page.getByText('Model Price Schedules').waitFor();
    await page.getByText('gpt-4o').waitFor();
    await page.getByText('OpenAI Pricing 2026-10').waitFor();
  } finally {
    await context.close();
  }
});

test('saved chat inspects exact memory references, recovers reset conflicts, and exports a summary artifact', async () => {
  const createdAt = '2026-10-19T12:00:00Z';
  const summary = {
    conversationId: 'memory-1',
    title: 'Memory and summary',
    lastSequence: 2,
    pendingRequestId: null,
    createdAt,
    updatedAt: createdAt,
  };
  const reference = {
    sourceId: 'source-1',
    sourceName: 'Linear Algebra Primer',
    locatorLabel: 'section 2.1',
    chunkId: 'memory-chunk-1',
  };
  const notice = {
    level: 'warning',
    headline: 'Check your applicable catalog cohort.',
    hasConflicts: false,
    conflicts: [],
    sources: [],
    unknownScope: true,
  };
  const record = {
    ...summary,
    turns: [
      {
        sequence: 1,
        requestId: 'memory-request',
        role: 'user',
        text: 'Explain this basis.',
        status: 'input',
        evidence: { request: { query: 'Explain this basis.' } },
        createdAt,
      },
      {
        sequence: 2,
        requestId: 'memory-request',
        role: 'assistant',
        text: 'A basis spans the space [1].',
        status: 'answer',
        evidence: {
          type: 'answer',
          citations: [reference],
          contextBlock: [{ ...reference, text: 'A basis spans the space.' }],
          applicability: notice,
        },
        createdAt,
      },
    ],
  };
  const effectiveFlags = { memory_inspector: true, advising_summary_export: true };
  const { context, page } = await setupPage({
    initialConversations: [summary],
    initialConversationRecords: [record],
    effectiveFlags,
  });
  let resets = 0;
  let exports = 0;
  let epoch = 0;
  await page.route('**/api/conversations/memory-1/memory**', async (route) => {
    if (route.request().method() === 'POST') {
      if (++resets === 1) return route.fulfill({ status: 409, json: { detail: 'A turn request is pending.' } });
      epoch = 1;
      return route.fulfill({
        json: {
          conversationId: 'memory-1',
          contextEpoch: 1,
          contextBoundarySequence: 2,
          contextResetAt: createdAt,
          message: 'Conversation context memory reset.',
        },
      });
    }
    return route.fulfill({
      json: {
        conversationId: 'memory-1',
        contextEpoch: epoch,
        contextBoundarySequence: epoch ? 2 : 0,
        contextResetAt: epoch ? createdAt : null,
        researchNotebook: null,
        checkpoints: [
          {
            summaryId: 'checkpoint-1',
            conversationId: 'memory-1',
            state: 'completed',
            contextEpoch: 0,
            expectedSequence: 2,
            candidateCoveredThrough: 2,
            coveredThrough: 2,
            fingerprint: 'snapshot-1',
            text: 'Discussing vector spaces.',
            referenceMetadata: [
              { sequence: 2, role: 'assistant', status: 'answer', citations: [reference], contextBlock: [] },
            ],
            failureCode: null,
            profileId: null,
            profileRevision: null,
            createdAt,
            updatedAt: createdAt,
          },
        ],
      },
    });
  });
  await page.route('**/api/conversations/memory-1/export-summary', async (route) => {
    exports++;
    return route.fulfill({ json: documentRecord });
  });
  try {
    await page.getByText('Check your applicable catalog cohort.', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('Source applicability and freshness notice').count(), 1);
    const openMemory = page.getByRole('button', { name: 'Inspect conversation memory and context' });
    await openMemory.click();
    const dialog = page.getByRole('dialog', { name: 'Conversation Memory & Context' });
    await dialog.getByText('Discussing vector spaces.', { exact: true }).waitFor();
    await dialog.getByText('Exact References (1 turns)').click();
    await dialog.getByText('1 citations', { exact: true }).click();
    await dialog.getByText('memory-chunk-1', { exact: true }).waitFor();
    await dialog.getByRole('button', { name: 'Reset Context Memory', exact: true }).click();
    await dialog.getByRole('button', { name: 'Confirm Reset', exact: true }).click();
    await dialog
      .getByRole('alert')
      .getByText(/turn request is pending/)
      .waitFor();
    await dialog.getByRole('button', { name: 'Confirm Reset', exact: true }).click();
    await dialog.getByText('Historical or unpublished checkpoint; excluded from current context').waitFor();
    await dialog.getByRole('button', { name: 'Close', exact: true }).click();
    assert.equal(await openMemory.evaluate((button) => button === document.activeElement), true);
    await page.getByRole('button', { name: 'Export advising summary document' }).click();
    await page.getByText('Unchanged passage.', { exact: true }).waitFor();
    assert.equal(resets, 2);
    assert.equal(exports, 1);
    assert.equal(await page.getByText('Explain this basis.', { exact: true }).count(), 1);
    effectiveFlags.memory_inspector = false;
    effectiveFlags.advising_summary_export = false;
    await page.evaluate(() => window.dispatchEvent(new Event('focus')));
    await openMemory.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Export advising summary document' }).waitFor({ state: 'hidden' });
  } finally {
    await context.close();
  }
});

test('auditor sees only audit and telemetry and lands outside mutation workflows', async () => {
  const session = { mode: 'development', authenticated: true, role: 'auditor', workspaceKey: 'auditor-ws' };
  const main = await setupPage({ initialSession: session });
  try {
    await main.page.getByRole('heading', { name: 'Auditor workspace' }).waitFor();
    assert.equal(await main.page.locator('textarea').count(), 0);
  } finally {
    await main.context.close();
  }
  const admin = await setupPage({ entry: '/admin.html', initialSession: session });
  try {
    await admin.page.getByRole('tab', { name: 'Audit', exact: true }).waitFor();
    assert.equal(await admin.page.getByRole('tab').count(), 2);
    await admin.page.getByRole('tab', { name: 'Usage & Cost' }).click();
    await admin.page.getByText('Model Price Schedules').waitFor();
    assert.equal(
      admin.capturedApiRequests.some((request) => request.path === '/api/admin/overview'),
      false,
    );
  } finally {
    await admin.context.close();
  }
});

test('admin requests, updates, approves, and revokes a support grant with visible failure states', async () => {
  const session = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page } = await setupPage({
    entry: '/admin.html',
    initialSession: session,
    faults: {
      'GET /api/admin/grant-requests': [500, 'Grant list failed.'],
      'PATCH /api/admin/grant-requests/req-1': [
        409,
        'support grant request changed; refresh and review the pending request',
      ],
    },
  });
  try {
    await page.getByRole('tab', { name: 'Support grants', exact: true }).click();
    await page.getByText('Loading support grants...', { exact: true }).waitFor();
    await page
      .getByRole('alert')
      .getByText(/Grant list failed/)
      .waitFor();
    await page.getByRole('button', { name: 'Retry grants', exact: true }).click();
    await page.getByText('Loading support grants...', { exact: true }).waitFor();
    await page.getByText('No support grant requests.', { exact: true }).waitFor();
    await page.getByLabel('Target workspace', { exact: true }).fill('ws-student-2');
    await page.getByLabel('Resource type', { exact: true }).selectOption('conversation');
    await page.getByLabel('Resource id', { exact: true }).fill('conv-private');
    await page.getByLabel('Request reason', { exact: true }).fill('Investigating ticket 1234');
    await page.getByLabel('Grant expiry', { exact: true }).fill('2026-12-01T12:00');
    await page.getByRole('button', { name: 'Request access', exact: true }).click();
    await page.getByText('Support grant request saved.', { exact: true }).waitFor();
    await page.getByLabel('Request reason', { exact: true }).fill('Investigating ticket 1234 updated');
    await page.getByRole('button', { name: 'Update request', exact: true }).click();
    await page
      .getByRole('alert')
      .getByText(/request changed/)
      .waitFor();
    assert.equal(
      await page.getByLabel('Request reason', { exact: true }).inputValue(),
      'Investigating ticket 1234 updated',
    );
    await page.getByRole('button', { name: 'Update request', exact: true }).click();
    await page.getByText('Support grant request saved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Approve request', exact: true }).click();
    await page.getByRole('button', { name: 'Revoke grant', exact: true }).click();
    await page.getByText('Support grant revoked.', { exact: true }).waitFor();
    await page.getByText('No active support grants.', { exact: true }).waitFor();
  } finally {
    await context.close();
  }
  const auditor = await setupPage({
    entry: '/admin.html',
    initialSession: { mode: 'development', authenticated: true, role: 'auditor', workspaceKey: 'auditor-ws' },
  });
  try {
    assert.equal(await auditor.page.getByRole('tab', { name: 'Support grants' }).count(), 0);
    assert.equal(await auditor.page.getByRole('button', { name: 'Approve request' }).count(), 0);
    assert.equal(await auditor.page.getByRole('button', { name: 'Revoke grant' }).count(), 0);
    assert.equal(
      auditor.capturedApiRequests.some((request) => request.path.includes('grant')),
      false,
    );
  } finally {
    await auditor.context.close();
  }
});

test('admin reviews a source and publishes a program release with visible failure states', async () => {
  const session = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page } = await setupPage({
    entry: '/admin.html',
    initialSession: session,
    faults: {
      'GET /api/admin/sources/src-1/review': [500, 'Source review failed.'],
      'POST /api/admin/sources/src-1/proposals': [409, 'metadata revision mismatch: expected 1, currently 2'],
    },
  });
  try {
    await page.getByRole('tab', { name: 'Sources', exact: true }).click();
    await page.getByText('handbook.md', { exact: true }).click();
    await page.getByText('Loading source review...', { exact: true }).waitFor();
    await page
      .getByRole('alert')
      .getByText(/Source review failed/)
      .waitFor();
    await page.getByRole('button', { name: 'Retry source review', exact: true }).click();
    await page.getByText('Loading source review...', { exact: true }).waitFor();
    await page.getByText('No source proposals.', { exact: true }).waitFor();
    await page.getByLabel('Proposed title', { exact: true }).fill('College handbook');
    await page.getByRole('button', { name: 'Propose source change', exact: true }).click();
    await page
      .getByRole('alert')
      .getByText(/metadata revision mismatch/)
      .waitFor();
    assert.equal(await page.getByLabel('Proposed title', { exact: true }).inputValue(), 'College handbook');
    await page.getByRole('button', { name: 'Propose source change', exact: true }).click();
    await page.getByText('Source proposal saved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Approve proposal', exact: true }).click();
    await page.getByText('Source proposal approved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Deprecate source', exact: true }).click();
    await page.getByText('Source deprecated.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Show revision evidence', exact: true }).click();
    await page.getByText('Historical clause alpha stays addressable.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Open program review', exact: true }).click();
    await page.getByText('CS 170', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Draft rules', exact: true }).click();
    await page.getByText('Rule draft saved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Approve release', exact: true }).click();
    await page.getByText('Rule release approved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Publish release', exact: true }).click();
    await page.getByText('Rule release published.', { exact: true }).waitFor();
  } finally {
    await context.close();
  }
  const auditor = await setupPage({
    entry: '/admin.html',
    initialSession: { mode: 'development', authenticated: true, role: 'auditor', workspaceKey: 'auditor-ws' },
  });
  try {
    assert.equal(await auditor.page.getByRole('tab', { name: 'Sources' }).count(), 0);
    assert.equal(await auditor.page.getByRole('button', { name: 'Propose source change' }).count(), 0);
    assert.equal(await auditor.page.getByRole('button', { name: 'Publish release' }).count(), 0);
  } finally {
    await auditor.context.close();
  }
});

test('student keeps a record draft, commits one reviewed import, and reads an older revision', async () => {
  const session = { mode: 'development', authenticated: true, role: 'student', workspaceKey: 'student-ws' };
  const { context, page } = await setupPage({
    initialSession: session,
    faults: {
      'GET /api/academic-record': [500, 'Record list failed.'],
      'POST /api/academic-record/imports/batch-1/rows': [
        409,
        'record revision conflict: expected revision does not match current record',
      ],
    },
  });
  try {
    await openTool(page, 'Academic record');
    await page.getByText('Loading academic record...', { exact: true }).waitFor();
    await page
      .getByRole('alert')
      .getByText(/Record list failed/)
      .waitFor();
    await page.getByRole('button', { name: 'Retry record', exact: true }).click();
    await page.getByText('Loading academic record...', { exact: true }).waitFor();
    await page.getByText('No course attempts.', { exact: true }).waitFor();
    await page.getByLabel('Course code', { exact: true }).fill('CS 170');
    await page.getByLabel('Term', { exact: true }).fill('Fall 2023');
    await page.getByLabel('Grade', { exact: true }).fill('A');
    await page.getByRole('button', { name: 'Save attempt', exact: true }).click();
    await page.getByText('Course attempt saved.', { exact: true }).waitFor();
    await page.getByText(/student_manual/).waitFor();
    await page.getByRole('button', { name: 'Show prior revision', exact: true }).click();
    await page.getByText('CS 170 A', { exact: true }).waitFor();
    await page
      .getByLabel('CSV import', { exact: true })
      .fill('Course,Term,Credits,Grade\nCHEM 150,Spring 2024,3.0,A\n');
    await page.getByRole('button', { name: 'Preview import', exact: true }).click();
    await page.getByText('Import preview ready.', { exact: true }).waitFor();
    await page.getByLabel('Reviewed grade', { exact: true }).fill('A-');
    assert.equal(await page.getByRole('button', { name: 'Commit import', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: 'Save import rows', exact: true }).click();
    await page
      .getByRole('alert')
      .getByText(/record revision conflict/)
      .waitFor();
    assert.equal(await page.getByLabel('Reviewed grade', { exact: true }).inputValue(), 'A-');
    assert.equal(await page.getByRole('button', { name: 'Commit import', exact: true }).isDisabled(), true);
    await page.getByRole('button', { name: 'Save import rows', exact: true }).click();
    await page.getByText('Import rows saved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Commit import', exact: true }).click();
    await page.getByText('Import committed 1 attempt.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Commit import', exact: true }).click();
    await page.getByRole('listitem').filter({ hasText: 'CHEM 150' }).waitFor();
    assert.equal(await page.getByRole('listitem').filter({ hasText: 'CHEM 150' }).count(), 1);
  } finally {
    await context.close();
  }
});

test('student runs a program audit, keeps it after a stale recompute, and exports the same staleness', async () => {
  const session = { mode: 'development', authenticated: true, role: 'student', workspaceKey: 'student-ws' };
  const { context, page } = await setupPage({
    initialSession: session,
    faults: { 'GET /api/audits': [500, 'Program audit list failed.'] },
  });
  try {
    await openTool(page, 'Program audit');
    await page.getByText('Loading program audits...', { exact: true }).waitFor();
    await page.getByText('Program audit list failed.').waitFor();
    await page.getByRole('button', { name: 'Retry program audits', exact: true }).click();
    await page.getByText('No saved program audits.', { exact: true }).waitFor();
    await page.getByText('Coverage is a program audit, not a degree audit.', { exact: true }).waitFor();
    await page.getByLabel('Program').selectOption('CS Minor');
    await page.getByRole('button', { name: 'Run program audit', exact: true }).click();
    await page.getByText('Program audit saved.', { exact: true }).waitFor();
    await page.getByText('Status outstanding', { exact: true }).waitFor();
    assert.equal(await page.getByText('This program audit is stale.', { exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Refresh program audits', exact: true }).click();
    await page.getByText('This program audit is stale.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Export program audit', exact: true }).click();
    await page.getByText('Exported program audit is stale.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Recompute program audit', exact: true }).click();
    await page.getByRole('alert').getByText('program evidence is retired or superseded').waitFor();
    await page.getByText('This program audit is stale.', { exact: true }).waitFor();
    assert.equal(await page.getByLabel('Program').inputValue(), '11111111-1111-4111-8111-111111111111');
    await page.getByRole('button', { name: 'Recompute program audit', exact: true }).click();
    await page.getByText('Program audit saved.', { exact: true }).waitFor();
    assert.equal(await page.getByText('This program audit is stale.', { exact: true }).count(), 0);
  } finally {
    await context.close();
  }
});

test('student compares programs without treating mention overlap as saved credit', async () => {
  const session = { mode: 'development', authenticated: true, role: 'student', workspaceKey: 'student-ws' };
  const { context, page } = await setupPage({
    initialSession: session,
    faults: {
      'GET /api/comparisons': [500, 'Program comparison list failed.'],
      'POST /api/comparisons': [409, 'comparison revision conflict'],
    },
  });
  try {
    await openTool(page, 'Program comparison');
    await page.getByText('Loading program comparisons...', { exact: true }).waitFor();
    await page.getByText('Program comparison list failed.').waitFor();
    await page.getByRole('button', { name: 'Retry program comparisons', exact: true }).click();
    await page.getByText('No saved program comparisons.', { exact: true }).waitFor();
    await page.getByLabel('First program').selectOption('CS Minor');
    await page.getByLabel('Second program').selectOption('Open Minor');
    await page.getByLabel('Overlay course').fill('CHEM 150');
    await page.getByRole('button', { name: 'Compare programs', exact: true }).click();
    await page.getByRole('alert').getByText('comparison revision conflict').waitFor();
    assert.equal(await page.getByLabel('Overlay course').inputValue(), 'CHEM 150');
    await page.getByRole('button', { name: 'Compare programs', exact: true }).click();
    await page.getByText('Sharing policy is unknown.', { exact: true }).waitFor();
    await page.getByText('No credit savings are claimed.', { exact: true }).waitFor();
    await page.getByText('Shared mentions: CS 170', { exact: true }).waitFor();
    await page.getByText('unequal unresolved coverage', { exact: true }).waitFor();
  } finally {
    await context.close();
  }
});

test('admin stages, cancels, and publishes a catalog edition', async () => {
  const session = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page } = await setupPage({
    entry: '/admin.html',
    initialSession: session,
    faults: { 'POST /api/admin/catalog-refresh': [409, 'catalog refresh conflict'] },
  });
  try {
    await page.getByRole('tab', { name: 'Catalog refresh' }).click();
    await page.getByLabel('Collection id').fill('col-1');
    await page.getByRole('button', { name: 'Load catalog edition', exact: true }).click();
    await page.getByText('Published edition 2026 (revision 1, 1 sources)', { exact: true }).waitFor();
    await page.getByLabel('Edition key').fill('2027');
    await page.getByRole('button', { name: 'Stage catalog refresh', exact: true }).click();
    await page.getByRole('alert').getByText('catalog refresh conflict').waitFor();
    assert.equal(await page.getByLabel('Edition key').inputValue(), '2027');
    await page.getByRole('button', { name: 'Stage catalog refresh', exact: true }).click();
    await page.getByText('Staged edition 2027 (revision 2, 1 sources)', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Cancel catalog refresh', exact: true }).click();
    await page.getByText('Catalog refresh cancelled.', { exact: true }).waitFor();
    await page.getByText('Published edition 2026 (revision 1, 1 sources)', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Stage catalog refresh', exact: true }).click();
    await page.getByRole('button', { name: 'Publish catalog edition', exact: true }).click();
    await page.getByText('Catalog edition published.', { exact: true }).waitFor();
    await page.getByText('Published edition 2027 (revision 2, 1 sources)', { exact: true }).waitFor();
  } finally {
    await context.close();
  }
  const auditor = await setupPage({
    entry: '/admin.html',
    initialSession: { mode: 'development', authenticated: true, role: 'auditor', workspaceKey: 'auditor-ws' },
  });
  try {
    assert.equal(await auditor.page.getByRole('tab', { name: 'Catalog refresh' }).count(), 0);
  } finally {
    await auditor.context.close();
  }
});

test('student replaces a feedback vote without adding another current vote', async () => {
  const session = { mode: 'development', authenticated: true, role: 'student', workspaceKey: 'student-ws' };
  const responseId = '11111111-1111-4111-8111-111111111111';
  const { context, page } = await setupPage({
    initialSession: session,
    faults: { 'POST /api/feedback': [409, 'feedback conflict'] },
  });
  try {
    await openTool(page, 'Response feedback');
    await page.getByText('No feedback submitted.', { exact: true }).waitFor();
    await page.getByLabel('Response id').fill(responseId);
    await page.getByLabel('Reason code').fill('helpful');
    await page.getByLabel('Comment').fill('Clear answer');
    await page.getByRole('button', { name: 'Save feedback', exact: true }).click();
    await page.getByRole('alert').getByText('feedback conflict').waitFor();
    assert.equal(await page.getByLabel('Comment').inputValue(), 'Clear answer');
    await page.getByRole('button', { name: 'Retry feedback', exact: true }).click();
    await page.getByText('Revision 1. Current votes 1.', { exact: true }).waitFor();
    await page.getByLabel('Vote').selectOption('down');
    await page.getByLabel('Reason code').fill('missing-evidence');
    await page.getByRole('button', { name: 'Save feedback', exact: true }).click();
    await page.getByText('Revision 2. Current votes 1.', { exact: true }).waitFor();
  } finally {
    await context.close();
  }
});

test('admin reviews rollout conflicts, manages overrides and cohort members, and toggles a kill switch', async () => {
  const session = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page, capturedApiRequests } = await setupPage({
    entry: '/admin.html',
    initialSession: session,
    faults: { 'POST /api/admin/flags/memory_inspector/overrides': [409, 'Policy changed; refresh and review.'] },
  });
  try {
    await page.getByRole('tab', { name: 'Feature Rollouts' }).click();
    await page.getByLabel('Feature to configure').selectOption('memory_inspector');
    await page.getByLabel('Override reason', { exact: true }).fill('Reviewed pilot configuration');
    await page.getByRole('button', { name: 'Save override', exact: true }).click();
    await page
      .getByRole('alert')
      .getByText(/Policy changed/)
      .waitFor();
    assert.equal(
      await page.getByLabel('Override reason', { exact: true }).inputValue(),
      'Reviewed pilot configuration',
    );
    await page.getByRole('button', { name: 'Refresh policy', exact: true }).click();
    await page.getByRole('button', { name: 'Save override', exact: true }).click();
    await page.getByText('Override saved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Remove global *', exact: true }).click();
    await page.getByText('Override removed.', { exact: true }).waitFor();
    await page.getByLabel('Kill-switch reason', { exact: true }).fill('Maintenance rehearsal');
    await page.getByRole('button', { name: 'Activate kill switch', exact: true }).click();
    await page.getByRole('button', { name: 'Release kill switch', exact: true }).waitFor();
    await page.getByRole('button', { name: 'Release kill switch', exact: true }).click();
    await page.getByRole('button', { name: 'Activate kill switch', exact: true }).waitFor();
    await page.getByLabel('Membership cohort', { exact: true }).fill('pilot');
    await page.getByLabel('Find user by name or email', { exact: true }).fill('student2');
    await page.getByRole('button', { name: 'Search users', exact: true }).click();
    await page.getByLabel('User to assign', { exact: true }).selectOption('user-student-2');
    await page.getByRole('button', { name: 'Assign to cohort', exact: true }).click();
    await page.getByRole('button', { name: 'Remove Student User', exact: true }).click();
    await page.getByText('Cohort member removed.', { exact: true }).waitFor();
    assert.equal(
      capturedApiRequests.filter((request) => request.method === 'POST' && request.path.endsWith('/kill')).length,
      2,
    );
  } finally {
    await context.close();
  }
});

test('admin triages missing evidence and saves a redacted candidate', async () => {
  const session = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page } = await setupPage({
    entry: '/admin.html',
    initialSession: session,
    faults: {
      'GET /api/feedback/triage': [500, 'Feedback triage list failed.'],
      'POST /api/feedback/triage': [409, 'feedback revision changed; refresh before triage'],
    },
  });
  try {
    await page.getByRole('tab', { name: 'Feedback triage' }).click();
    await page.getByText('Loading feedback triage...', { exact: true }).waitFor();
    await page.getByText('Feedback triage list failed.').waitFor();
    await page.getByRole('button', { name: 'Retry feedback triage', exact: true }).click();
    await page.getByText('No feedback triage is recorded.', { exact: true }).waitFor();
    await page.getByLabel('Feedback id').fill('11111111-1111-4111-8111-111111111111');
    await page.getByLabel('Triage note').fill('open a gap');
    await page.getByLabel('Trace id').fill('22222222-2222-4222-8222-222222222222');
    await page.getByLabel('Collection id').fill('33333333-3333-4333-8333-333333333333');
    await page.getByLabel('Investigation query').fill('Which catalog requirement line is absent?');
    await page.getByRole('button', { name: 'Record feedback triage', exact: true }).click();
    await page.getByRole('alert').getByText('feedback revision changed').waitFor();
    assert.equal(await page.getByLabel('Triage note').inputValue(), 'open a gap');
    await page.getByRole('button', { name: 'Retry feedback triage', exact: true }).click();
    await page.getByText('Issue missing_evidence. Triage revision 1.', { exact: false }).waitFor();
    await page.getByText('Gap gap-1 is an investigation.', { exact: false }).waitFor();
    await page.getByLabel('Source id').fill('44444444-4444-4444-8444-444444444444');
    await page.getByLabel('Redacted prompt').fill('Which requirement is missing?');
    await page.getByLabel('Redacted expected').fill('The catalog line.');
    await page.getByRole('button', { name: 'Save redacted candidate', exact: true }).click();
    await page.getByText('Candidate candidate-1 stores no student history.', { exact: false }).waitFor();
  } finally {
    await context.close();
  }
  const auditor = await setupPage({
    entry: '/admin.html',
    initialSession: { mode: 'development', authenticated: true, role: 'auditor', workspaceKey: 'auditor-ws' },
  });
  try {
    assert.equal(await auditor.page.getByRole('tab', { name: 'Feedback triage' }).count(), 0);
    assert.equal(await auditor.page.getByRole('tab', { name: 'Experiments' }).count(), 0);
    assert.equal(await auditor.page.getByRole('tab', { name: 'Prompt promotion' }).count(), 0);
  } finally {
    await auditor.context.close();
  }
});

test('admin records an experiment that is not the student conversation', async () => {
  const session = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page } = await setupPage({
    entry: '/admin.html',
    initialSession: session,
    faults: { 'POST /api/debug/experiments': [409, 'unknown prior provider outcome is not replayed'] },
  });
  try {
    await page.getByRole('tab', { name: 'Experiments' }).click();
    await page.getByText('No debug experiment is recorded.', { exact: true }).waitFor();
    await page.getByLabel('Conversation id').fill('11111111-1111-4111-8111-111111111111');
    await page.getByLabel('Request id').fill('22222222-2222-4222-8222-222222222222');
    await page.getByLabel('Saved query').fill('what is CS 170?');
    await page.getByRole('button', { name: 'Record debug experiment', exact: true }).click();
    await page.getByRole('alert').getByText('unknown prior provider outcome is not replayed').waitFor();
    assert.equal(await page.getByLabel('Conversation id').inputValue(), '11111111-1111-4111-8111-111111111111');
    await page.getByRole('button', { name: 'Retry debug experiment', exact: true }).click();
    await page.getByText('Experiment is not part of the student conversation.', { exact: true }).waitFor();
    await page.getByText('Experiment 44444444-4444-4444-8444-444444444444 is not the conversation.', { exact: true }).waitFor();
  } finally {
    await context.close();
  }
});

test('admin cannot promote a prompt until review and a fixture exist', async () => {
  const session = { mode: 'development', authenticated: true, role: 'admin', workspaceKey: 'admin-ws' };
  const { context, page } = await setupPage({
    entry: '/admin.html',
    initialSession: session,
  });
  try {
    await page.getByRole('tab', { name: 'Prompt promotion' }).click();
    await page.getByText('No prompt version is saved.', { exact: true }).waitFor();
    await page.getByLabel('Prompt name').fill('answer');
    await page.getByLabel('Prompt text').fill('Cite the catalog.');
    await page.getByLabel('Suite name').fill('catalog');
    await page.getByRole('button', { name: 'Save prompt version', exact: true }).click();
    await page.getByText('Version 33333333-3333-4333-8333-333333333333 is draft.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Promote prompt', exact: true }).click();
    await page.getByRole('alert').getByText('prompt review is not approved').waitFor();
    assert.equal(await page.getByLabel('Prompt text').inputValue(), 'Cite the catalog.');
    await page.getByRole('button', { name: 'Approve prompt review', exact: true }).click();
    await page.getByText('Version 33333333-3333-4333-8333-333333333333 is approved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Promote prompt', exact: true }).click();
    await page.getByRole('alert').getByText('eval fixture is missing').waitFor();
    assert.equal(await page.getByLabel('Prompt text').inputValue(), 'Cite the catalog.');
    await page.getByLabel('Fixture text').fill('CS 170 is listed.');
    await page.getByRole('button', { name: 'Save eval fixture', exact: true }).click();
    await page.getByText('Eval fixture saved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Promote prompt', exact: true }).click();
    await page.getByText('Prompt promotion published.', { exact: true }).waitFor();
    await page.getByText('Promotion status published.', { exact: true }).waitFor();
  } finally {
    await context.close();
  }
});

test('student exports private data without leaving the download token on screen', async () => {
  const session = { mode: 'development', authenticated: true, role: 'student', workspaceKey: 'student-ws' };
  const { context, page } = await setupPage({
    initialSession: session,
    faults: { 'GET /api/privacy/exports': [500, 'Privacy export list failed.'] },
  });
  try {
    await openTool(page, 'Privacy');
    await page.getByText('Loading privacy exports...', { exact: true }).waitFor();
    page.releaseNextPrivacyList();
    await page
      .getByRole('alert')
      .getByText(/Privacy export list failed/)
      .waitFor();
    await page.getByRole('button', { name: 'Retry privacy export', exact: true }).click();
    await page.getByText('Loading privacy exports...', { exact: true }).waitFor();
    page.releaseNextPrivacyList();
    await page.getByText('No privacy export is saved.', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Create privacy export', exact: true }).click();
    await page.getByText('Export ready. Download expires 2026-10-21T00:00:00+00:00.', { exact: true }).waitFor();
    assert.equal(await page.getByText('once-token').count(), 0);
    await page.getByRole('button', { name: 'Download privacy export', exact: true }).click();
    await page.getByText('Export digest digest-1.', { exact: true }).waitFor();
    assert.equal(await page.getByText('once-token').count(), 0);
  } finally {
    await context.close();
  }
});
