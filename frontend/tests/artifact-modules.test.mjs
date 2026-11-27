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
const { address, evaluateFormula, sheetGrid } = await import(toDataUrl(await read('workbook')));
const { readableStage } = await import(toDataUrl(await read('labels')));

test('cell addresses use spreadsheet column letters', () => {
  assert.equal(address(1, 1), 'A1');
  assert.equal(address(7, 26), 'Z7');
  assert.equal(address(2, 27), 'AA2');
  assert.equal(address(3, 52), 'AZ3');
  assert.equal(address(1, 703), 'AAA1');
});

test('a sheet preview shows totals it can work out exactly, and formulas otherwise', () => {
  const cells = [
    { row: 1, column: 1, value: 'Course' },
    { row: 1, column: 2, value: 'Credits' },
    { row: 2, column: 1, value: 'MATH 111' },
    { row: 2, column: 2, value: 4 },
    { row: 3, column: 1, value: 'CS 170' },
    { row: 3, column: 2, value: '4' },
    { row: 4, column: 1, value: 'Total' },
    { row: 4, column: 2, formula: '=SUM(B2:B3)' },
    { row: 5, column: 2, formula: '=AVERAGE(B2:B3)' },
    { row: 6, column: 2, formula: '=SUM(B4,1)' },
  ];
  const grid = sheetGrid(cells);
  assert.equal(grid.columns, 2);
  assert.deepEqual(grid.rows[0].shown, ['Course', 'Credits']);
  assert.equal(grid.rows[3].shown[1], '8');
  // Only SUM is worked out; anything else stays a formula rather than a guess.
  assert.equal(grid.rows[4].shown[1], '=AVERAGE(B2:B3)');
  assert.equal(grid.rows[5].shown[1], '9');
  assert.equal(grid.unresolved, 1);
  const at = (row, column) => cells.find((cell) => cell.row === row && cell.column === column);
  assert.equal(evaluateFormula('=SUM(B2:B3, B9)', at), 8);
});

test('stage labels are readable and absent stages have none', () => {
  assert.equal(readableStage('planning'), 'Planning the output');
  assert.equal(readableStage('tool_replayed'), 'Recovered a saved tool result');
  assert.equal(readableStage(undefined), null);
});
