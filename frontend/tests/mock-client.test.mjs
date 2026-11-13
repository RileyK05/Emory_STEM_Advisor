import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const toDataUrl = (text) => 'data:text/javascript;base64,' + Buffer.from(ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText).toString('base64');
const source = await readFile(new URL('../src/api/mock.ts', import.meta.url), 'utf8');
const corpusSource = await readFile(new URL('../src/api/corpus.ts', import.meta.url), 'utf8');
const mock = await import(toDataUrl(source.replace("from './corpus'", `from '${toDataUrl(corpusSource)}'`)));

test('mock answers an explicit empty source scope without building a context', async () => {
  const result = await new mock.MockClient().query({ query: 'What is a vector basis?', sourceIds: [] });
  assert.equal(result.research?.outcome, 'insufficient_evidence');
  assert.match(result.answer, /No sources were selected/);
  assert.deepEqual(result.trace.contextBlock, []);
});

test('mock starts an artifact run when the box is ticked, or the wording asks without one', async () => {
  for (const [request, expected] of [
    [{ query: 'make a plan for next semester' }, 'demo-run-1'],
    [{ query: 'What is a vector basis?', makeArtifact: true }, 'demo-run-1'],
    [{ query: 'make a plan for next semester', makeArtifact: false }, undefined],
  ]) {
    const result = await new mock.MockClient().query(request);
    assert.equal(result.type, 'answer');
    if (result.type === 'answer') assert.equal(result.artifactRun?.runId, expected, JSON.stringify(request));
  }
});

test('mock scoped answer never includes passages from deselected sources', async () => {
  const client = new mock.MockClient();
  const selected = await client.query({ query: 'What is a vector basis?', sourceIds: ['src-linalg'] });
  assert.equal(selected.type, 'answer');
  if (selected.type === 'answer') assert.ok(selected.trace.contextBlock.every((item) => item.sourceName === 'Linear Algebra Primer'));
  const excluded = await client.query({ query: 'What is a vector basis?', sourceIds: ['src-safety'] });
  assert.equal(excluded.research?.outcome, 'insufficient_evidence');
});
