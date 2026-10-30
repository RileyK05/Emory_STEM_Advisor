import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// Load the pure artifact modules straight from source (type-stripped), resolving their relative imports.
const toDataUrl = (text) =>
  'data:text/javascript;base64,' +
  Buffer.from(
    ts.transpileModule(text, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } })
      .outputText,
  ).toString('base64');
const read = (name) => readFile(new URL(`../src/components/artifact/${name}.ts`, import.meta.url), 'utf8');
const { address } = await import(toDataUrl(await read('workbook')));
const { readableStage } = await import(toDataUrl(await read('labels')));

test('cell addresses use spreadsheet column letters', () => {
  assert.equal(address(1, 1), 'A1');
  assert.equal(address(7, 26), 'Z7');
  assert.equal(address(2, 27), 'AA2');
  assert.equal(address(3, 52), 'AZ3');
  assert.equal(address(1, 703), 'AAA1');
});

test('stage labels are readable and absent stages have none', () => {
  assert.equal(readableStage('planning'), 'Planning the output');
  assert.equal(readableStage('tool_replayed'), 'Recovered a saved tool result');
  assert.equal(readableStage(undefined), null);
});
