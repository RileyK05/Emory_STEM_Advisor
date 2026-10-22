import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const toDataUrl = (text) => 'data:text/javascript;base64,' + Buffer.from(ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText).toString('base64');
const source = await readFile(new URL('../src/api/mock.ts', import.meta.url), 'utf8');
const corpusSource = await readFile(new URL('../src/api/corpus.ts', import.meta.url), 'utf8');
const mock = await import(toDataUrl(source.replace("from './corpus'", `from '${toDataUrl(corpusSource)}'`)));

test('mock refuses explicit empty source scope without building a context', async () => {
  const result = await new mock.MockClient().query({ query: 'What is a vector basis?', sourceIds: [] });
  assert.equal(result.type, 'refusal');
  if (result.type === 'refusal') assert.match(result.reason, /No sources were selected/);
  assert.deepEqual(result.trace?.contextBlock, []);
});

test('mock scoped answer never includes passages from deselected sources', async () => {
  const client = new mock.MockClient();
  const selected = await client.query({ query: 'What is a vector basis?', sourceIds: ['src-linalg'] });
  assert.equal(selected.type, 'answer');
  if (selected.type === 'answer') assert.ok(selected.trace.contextBlock.every((item) => item.sourceName === 'Linear Algebra Primer'));
  const excluded = await client.query({ query: 'What is a vector basis?', sourceIds: ['src-safety'] });
  assert.equal(excluded.type, 'refusal');
});
