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
const ids = toDataUrl(await read('ids'));
const withIds = (source) => source.replace("from './ids'", `from '${ids}'`);

const { parseMarkdown } = await import(toDataUrl(withIds(await read('markdown'))));
const { address, workbookEditOperations, emptyWorkbook } = await import(toDataUrl(withIds(await read('workbook'))));
const plan = await import(toDataUrl(await read('outputPlan')));
const { readableStage } = await import(toDataUrl(await read('labels')));

const types = (spec) => spec.blocks.map((block) => block.type);

// ---------------------------------------------------------------- markdown

test('markdown parses each block kind and keeps ordering', () => {
  const spec = parseMarkdown(
    'Guide',
    ['# Title', '', 'First para', 'still first', '', '> quoted', '> twice', '', '- a', '- b', '', '1. one', '2. two'].join(
      '\n',
    ),
  );
  assert.equal(spec.title, 'Guide');
  assert.deepEqual(types(spec), ['heading', 'paragraph', 'quote', 'list', 'list']);
  assert.equal(spec.blocks[0].level, 1);
  assert.equal(spec.blocks[1].text, 'First para\nstill first');
  assert.equal(spec.blocks[2].text, 'quoted\ntwice');
  assert.deepEqual([spec.blocks[3].ordered, spec.blocks[3].items], [false, ['a', 'b']]);
  assert.deepEqual([spec.blocks[4].ordered, spec.blocks[4].items], [true, ['one', 'two']]);
  assert.equal(new Set(spec.blocks.map((block) => block.id)).size, spec.blocks.length);
});

test('markdown fences keep their content and language, including an unterminated fence', () => {
  const closed = parseMarkdown('t', '```python\nprint(1)\n```\nafter');
  assert.deepEqual(types(closed), ['code', 'paragraph']);
  assert.deepEqual([closed.blocks[0].language, closed.blocks[0].text], ['python', 'print(1)']);
  const open = parseMarkdown('t', '~~~\nnever closed\nstill code');
  assert.deepEqual([open.blocks[0].type, open.blocks[0].language, open.blocks[0].text], ['code', null, 'never closed\nstill code']);
});

test('markdown tables split on unescaped pipes only', () => {
  const spec = parseMarkdown('t', '| Name | Note |\n| --- | --- |\n| a\\|b | plain |\n| c | d |');
  assert.deepEqual(types(spec), ['table']);
  assert.deepEqual(spec.blocks[0].headers, ['Name', 'Note']);
  assert.deepEqual(spec.blocks[0].rows, [
    ['a|b', 'plain'],
    ['c', 'd'],
  ]);
});

test('markdown falls back to a default title and tolerates CRLF and empty input', () => {
  assert.equal(parseMarkdown('', '# x').title, 'Untitled document');
  assert.deepEqual(types(parseMarkdown('t', '# a\r\n\r\npara\r\n')), ['heading', 'paragraph']);
  assert.deepEqual(parseMarkdown('t', '').blocks, []);
});

// --------------------------------------------------------------- workbooks

test('cell addresses use spreadsheet column letters', () => {
  assert.equal(address(1, 1), 'A1');
  assert.equal(address(7, 26), 'Z7');
  assert.equal(address(2, 27), 'AA2');
  assert.equal(address(3, 52), 'AZ3');
  assert.equal(address(1, 703), 'AAA1');
});

test('a new workbook has one starter cell with unique ids', () => {
  const book = emptyWorkbook('Budget');
  assert.equal(book.title, 'Budget');
  assert.equal(book.sheets.length, 1);
  assert.deepEqual([book.sheets[0].cells[0].row, book.sheets[0].cells[0].column], [1, 1]);
});

const sheet = {
  kind: 'workbook',
  title: 'w',
  provenance: {},
  sheets: [
    {
      id: 's1',
      name: 'Sheet1',
      cells: [
        { id: 'c-num', row: 1, column: 1, value: 3, style: {} },
        { id: 'c-bool', row: 1, column: 2, value: true, style: {} },
        { id: 'c-text', row: 1, column: 3, value: 'hi', style: {} },
        { id: 'c-formula', row: 1, column: 4, value: null, formula: '=A1*2', style: {} },
      ],
    },
  ],
};
const edit = (drafts) => workbookEditOperations(sheet, drafts);

test('unchanged or untouched cells produce no operations', () => {
  assert.deepEqual(edit({}), []);
  assert.deepEqual(edit({ 's1:c-num': '3', 's1:c-text': 'hi', 's1:c-formula': '=A1*2' }), []);
});

test('numbers and booleans keep their type; other text stays text', () => {
  const ops = edit({ 's1:c-num': '4.5', 's1:c-bool': 'FALSE', 's1:c-text': 'bye' });
  const byCell = Object.fromEntries(ops.map((op) => [op.cell_id, op.cell]));
  assert.equal(byCell['c-num'].value, 4.5);
  assert.equal(byCell['c-bool'].value, false);
  assert.equal(byCell['c-text'].value, 'bye');
  assert.ok(ops.every((op) => op.type === 'set_cell' && op.sheet_id === 's1'));
});

test('a number cell edited to non-numeric text becomes text, and to empty becomes null', () => {
  const ops = edit({ 's1:c-num': 'n/a', 's1:c-text': '' });
  const byCell = Object.fromEntries(ops.map((op) => [op.cell_id, op.cell]));
  assert.equal(byCell['c-num'].value, 'n/a');
  assert.equal(byCell['c-text'].value, null);
});

test('text starting with = is a formula and clears the value', () => {
  const [op] = edit({ 's1:c-text': '  =SUM(A1:A3) ' });
  assert.equal(op.cell.formula, '=SUM(A1:A3)');
  assert.equal(op.cell.value, null);
});

// ------------------------------------------------------------ output plan

const limits = { maxCalls: 10, totalTokenBudget: 100000, perCallOutputCap: 4096 };
const section = (over = {}) => ({ ...plan.DEFAULT_SECTION, ...over });
const docDraft = (over = {}) => ({
  kind: 'document',
  title: 'My doc',
  sections: [section()],
  documentBlockQuota: 6,
  sheets: [plan.DEFAULT_SHEET],
  cellQuota: 39,
  unitTokens: 1200,
  unitRepairs: 1,
  checkpointBytes: 4000000,
  ...over,
});
const sheetDraft = (over = {}) => ({ ...plan.DEFAULT_SHEET, ...over });
const bookDraft = (over = {}) => docDraft({ kind: 'workbook', title: 'My book', ...over });
const build = (draft, l = limits, e = null) => plan.buildOutputPlan(draft, l, e);

test('the default document plan builds with the server field names and numbered keys', () => {
  const built = build(docDraft({ sections: [section(), section({ title: ' Second ' })], documentBlockQuota: 12 }));
  assert.equal(built.kind, 'document');
  assert.deepEqual(built.sections.map((s) => [s.key, s.title]), [
    ['section-1', 'Overview'],
    ['section-2', 'Second'],
  ]);
  assert.deepEqual(
    [built.max_total_blocks, built.max_output_tokens_per_unit, built.max_repairs_per_unit, built.max_checkpoint_bytes],
    [12, 1200, 1, 4000000],
  );
  assert.equal(built.sections[0].target_words, 500);
});

test('section bounds must be ordered and in range', () => {
  for (const bad of [
    { title: '  ' },
    { minWords: 600 },
    { targetWords: 700 },
    { maxWords: 20001 },
    { minBlocks: 5 },
    { targetBlocks: 65 },
  ])
    assert.throws(() => build(docDraft({ sections: [section(bad)] })), /Check section 1: targets must sit between/);
});

test('fractional quotas are rejected', () => {
  assert.throws(() => build(docDraft({ sections: [section({ targetWords: 500.5 })] })), /section 1/);
});

test('the document block quota must cover each heading and its maximum blocks', () => {
  assert.throws(() => build(docDraft({ documentBlockQuota: 5 })), /Total document blocks must include each section heading/);
  assert.doesNotThrow(() => build(docDraft({ documentBlockQuota: 6 })));
  assert.throws(() => build(docDraft({ sections: [] })), /Total document blocks/);
});

test('an over-long generated title is rejected and a blank one falls back', () => {
  assert.throws(() => build(docDraft({ title: 'x'.repeat(501) })), /500 characters or fewer/);
  assert.equal(build(docDraft({ title: '   ' })).title, 'Generated document');
  assert.equal(build(bookDraft({ title: '   ' })).title, 'Generated workbook');
});

test('workbook columns must be key|label|type with a known type', () => {
  assert.throws(() => build(bookDraft({ sheets: [sheetDraft({ columnText: 'a|b' })] })), /exactly key\|label\|type/);
  assert.throws(
    () => build(bookDraft({ sheets: [sheetDraft({ columnText: 'a|A|blob' })] })),
    /needs key\|label\|type; type may be/,
  );
  const built = build(bookDraft());
  assert.deepEqual(built.sheets[0].columns, [
    { key: 'item', label: 'Item', value_type: 'text' },
    { key: 'value', label: 'Value', value_type: 'number' },
  ]);
  assert.equal(built.max_total_cells, 39);
});

test('worksheet names and column keys are validated', () => {
  const bad = (over) => () => build(bookDraft({ sheets: [sheetDraft(over)] }));
  assert.throws(bad({ name: 'a:b' }), /Check worksheet 1/);
  assert.throws(bad({ name: "'quoted" }), /Check worksheet 1/);
  assert.throws(bad({ name: 'x'.repeat(32) }), /Check worksheet 1/);
  assert.throws(bad({ columnText: 'a|A|text\na|B|text' }), /Check worksheet 1/);
  assert.throws(bad({ columnText: '1bad|A|text' }), /Check worksheet 1/);
  assert.throws(bad({ minRows: 11 }), /Check worksheet 1/);
});

test('the workbook cell quota must cover headers and maximum rows; sheet names must be unique', () => {
  // 2 columns * (12 max rows + 1 header row) = 26 cells needed.
  assert.doesNotThrow(() => build(bookDraft({ cellQuota: 26 })));
  assert.throws(() => build(bookDraft({ cellQuota: 25 })), /total workbook cells must cover every header/);
  const two = [sheetDraft({ name: 'Same' }), sheetDraft({ name: 'same' })];
  assert.throws(() => build(bookDraft({ sheets: two, cellQuota: 100 })), /names must be unique/);
});

test('per-unit output limits are range checked', () => {
  for (const bad of [{ unitTokens: 63 }, { unitTokens: 8193 }, { unitRepairs: 3 }, { checkpointBytes: 1023 }])
    assert.throws(() => build(docDraft(bad)), /Output limits: tokens 64–8,192/);
});

test('the server allowance is required and enforced', () => {
  assert.throws(() => build(docDraft(), null, null), /Loading generation limits/);
  assert.throws(() => build(docDraft(), null, 'offline'), /Generation limits are unavailable: offline/);
  // 1 section * (1 + 1 repair) = 2 calls * 1200 tokens = 2400
  assert.doesNotThrow(() => build(docDraft(), { ...limits, maxCalls: 2, totalTokenBudget: 2400 }));
  assert.throws(
    () => build(docDraft(), { ...limits, maxCalls: 1 }),
    /exceeds the current generation allowance \(2 calls and up to 2400 output tokens\)/,
  );
  assert.throws(() => build(docDraft(), { ...limits, totalTokenBudget: 2399 }), /exceeds the current generation allowance/);
  assert.throws(() => build(docDraft(), { ...limits, perCallOutputCap: 1199 }), /exceeds the current generation allowance/);
});

test('new units get numbered defaults', () => {
  assert.equal(plan.newSection(1).title, 'Section 2');
  assert.equal(plan.newSheet(2).name, 'Sheet 3');
});

// ------------------------------------------------------------------ labels

test('stage labels are readable and absent stages have none', () => {
  assert.equal(readableStage('planning'), 'Planning the output');
  assert.equal(readableStage('tool_replayed'), 'Recovered a saved tool result');
  assert.equal(readableStage(undefined), null);
});
