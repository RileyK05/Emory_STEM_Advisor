import type { ArtifactRunLimits, DocumentOutputPlan, WorkbookOutputColumn, WorkbookOutputPlan } from '../../api/types';

/** A document section as edited in the form (camelCase, all numbers editable). */
export interface DocSectionDraft {
  /** Stable identity for React keys; never sent to the server. */
  id: string;
  title: string;
  targetWords: number;
  minWords: number;
  maxWords: number;
  targetBlocks: number;
  minBlocks: number;
  maxBlocks: number;
}

/** A worksheet as edited in the form; columns are one `key|label|type` line each. */
export interface SheetDraft {
  /** Stable identity for React keys; never sent to the server. */
  id: string;
  name: string;
  columnText: string;
  targetRows: number;
  minRows: number;
  maxRows: number;
}

/** Everything the plan form collects. */
interface PlanDraft {
  kind: 'document' | 'workbook';
  title: string;
  sections: DocSectionDraft[];
  documentBlockQuota: number;
  sheets: SheetDraft[];
  cellQuota: number;
  unitTokens: number;
  unitRepairs: number;
  checkpointBytes: number;
}

export const DEFAULT_SECTION: DocSectionDraft = {
  id: 'section-default',
  title: 'Overview',
  targetWords: 500,
  minWords: 400,
  maxWords: 600,
  targetBlocks: 4,
  minBlocks: 3,
  maxBlocks: 5,
};
export const DEFAULT_SHEET: SheetDraft = {
  id: 'sheet-default',
  name: 'Data',
  columnText: 'item|Item|text\nvalue|Value|number',
  targetRows: 10,
  minRows: 8,
  maxRows: 12,
};

export const MAX_UNITS = 40;

let draftSerial = 0;

export function newSection(count: number): DocSectionDraft {
  draftSerial += 1;
  return {
    id: `section-${draftSerial}`,
    title: `Section ${count + 1}`,
    targetWords: 300,
    minWords: 200,
    maxWords: 400,
    targetBlocks: 3,
    minBlocks: 2,
    maxBlocks: 4,
  };
}
export function newSheet(count: number): SheetDraft {
  draftSerial += 1;
  return {
    id: `sheet-${draftSerial}`,
    name: `Sheet ${count + 1}`,
    columnText: 'item|Item|text',
    targetRows: 10,
    minRows: 8,
    maxRows: 12,
  };
}

const COLUMN_TYPES = ['text', 'integer', 'number', 'date', 'formula'];
const COLUMN_HELP = 'type may be text, integer, number, date, or formula.';

function inIntRange(value: number, min: number, max: number) {
  return Number.isSafeInteger(value) && value >= min && value <= max;
}
function codePoints(value: string) {
  return Array.from(value).length;
}

function sectionIsValid(section: DocSectionDraft) {
  return (
    section.title.trim() &&
    codePoints(section.title) <= 500 &&
    inIntRange(section.targetWords, 1, 20000) &&
    inIntRange(section.minWords, 1, 20000) &&
    inIntRange(section.maxWords, 1, 20000) &&
    section.minWords <= section.targetWords &&
    section.targetWords <= section.maxWords &&
    inIntRange(section.targetBlocks, 1, 64) &&
    inIntRange(section.minBlocks, 1, 64) &&
    inIntRange(section.maxBlocks, 1, 64) &&
    section.minBlocks <= section.targetBlocks &&
    section.targetBlocks <= section.maxBlocks
  );
}

function parseColumns(columnText: string): WorkbookOutputColumn[] {
  return columnText
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      const parts = line.split('|').map((part) => part.trim());
      if (parts.length !== 3) throw new Error(`Each workbook column needs exactly key|label|type; ${COLUMN_HELP}`);
      const [key, label, value_type] = parts;
      if (!key || !label || !COLUMN_TYPES.includes(value_type))
        throw new Error(`Each workbook column needs key|label|type; ${COLUMN_HELP}`);
      return { key, label, value_type: value_type as WorkbookOutputColumn['value_type'] };
    });
}

function sheetIsValid(sheet: SheetDraft, columns: WorkbookOutputColumn[]) {
  return (
    sheet.name.trim() &&
    codePoints(sheet.name) <= 31 &&
    !/[\[\]:*?/\\]/.test(sheet.name) &&
    !sheet.name.startsWith("'") &&
    !sheet.name.endsWith("'") &&
    columns.length &&
    columns.length <= 128 &&
    new Set(columns.map((column) => column.key)).size === columns.length &&
    !columns.some(
      (column) =>
        !/^[A-Za-z][A-Za-z0-9_]*$/.test(column.key) || column.key.length > 64 || codePoints(column.label) > 128,
    ) &&
    inIntRange(sheet.minRows, 1, 100000) &&
    inIntRange(sheet.targetRows, 1, 100000) &&
    inIntRange(sheet.maxRows, 1, 100000) &&
    sheet.minRows <= sheet.targetRows &&
    sheet.targetRows <= sheet.maxRows
  );
}

function checkedTitle(title: string, fallback: string) {
  const planTitle = title.trim() || fallback;
  if (codePoints(planTitle) > 500) throw new Error('Generated artifact title must be 500 characters or fewer.');
  return planTitle;
}

function buildDocumentPlan(draft: PlanDraft): DocumentOutputPlan {
  const sections = draft.sections.map((section, index) => {
    if (!sectionIsValid(section))
      throw new Error(`Check section ${index + 1}: targets must sit between valid minimum and maximum bounds.`);
    return {
      key: `section-${index + 1}`,
      title: section.title.trim(),
      target_words: section.targetWords,
      min_words: section.minWords,
      max_words: section.maxWords,
      target_blocks: section.targetBlocks,
      min_blocks: section.minBlocks,
      max_blocks: section.maxBlocks,
    };
  });
  const minimumBlocks = sections.reduce((sum, section) => sum + section.max_blocks + 1, 0);
  if (
    !sections.length ||
    sections.length > MAX_UNITS ||
    !inIntRange(draft.documentBlockQuota, 1, 1000) ||
    draft.documentBlockQuota < minimumBlocks
  )
    throw new Error(
      'Total document blocks must include each section heading and its maximum blocks (up to 1,000 blocks).',
    );
  return {
    kind: 'document',
    title: checkedTitle(draft.title, 'Generated document'),
    sections,
    max_total_blocks: draft.documentBlockQuota,
    max_output_tokens_per_unit: draft.unitTokens,
    max_repairs_per_unit: draft.unitRepairs,
    max_checkpoint_bytes: draft.checkpointBytes,
  };
}

function buildWorkbookPlan(draft: PlanDraft): WorkbookOutputPlan {
  const sheets = draft.sheets.map((sheet, index) => {
    const columns = parseColumns(sheet.columnText);
    if (!sheetIsValid(sheet, columns))
      throw new Error(
        `Check worksheet ${index + 1}: use a valid name and unique column keys; row target must be within 1–100,000 bounds.`,
      );
    return {
      key: `sheet-${index + 1}`,
      name: sheet.name,
      columns,
      min_rows: sheet.minRows,
      target_rows: sheet.targetRows,
      max_rows: sheet.maxRows,
    };
  });
  const maxCells = sheets.reduce((sum, sheet) => sum + sheet.columns.length * (sheet.max_rows + 1), 0);
  if (
    !sheets.length ||
    sheets.length > MAX_UNITS ||
    new Set(sheets.map((sheet) => sheet.name.toLowerCase())).size !== sheets.length ||
    !inIntRange(draft.cellQuota, 1, 100000) ||
    maxCells > draft.cellQuota
  )
    throw new Error(
      'Worksheet names must be unique, and total workbook cells must cover every header and maximum row across all sheets (up to 100,000 cells).',
    );
  return {
    kind: 'workbook',
    title: checkedTitle(draft.title, 'Generated workbook'),
    sheets,
    max_total_cells: draft.cellQuota,
    max_output_tokens_per_unit: draft.unitTokens,
    max_repairs_per_unit: draft.unitRepairs,
    max_checkpoint_bytes: draft.checkpointBytes,
  };
}

function assertWithinAllowance(
  plan: DocumentOutputPlan | WorkbookOutputPlan,
  draft: PlanDraft,
  limits: ArtifactRunLimits | null,
  limitsError: string | null,
) {
  if (!limits)
    throw new Error(
      limitsError
        ? `Generation limits are unavailable: ${limitsError}`
        : 'Loading generation limits. Try again when they are shown.',
    );
  const units = plan.kind === 'document' ? plan.sections.length : plan.sheets.length;
  const calls = units * (1 + draft.unitRepairs);
  const tokenAllowance = calls * draft.unitTokens;
  if (
    !inIntRange(limits.maxCalls, 1, 100) ||
    !inIntRange(limits.totalTokenBudget, 1, 100000000) ||
    !inIntRange(limits.perCallOutputCap, 1, 1000000) ||
    draft.unitTokens > limits.perCallOutputCap ||
    calls > limits.maxCalls ||
    tokenAllowance > limits.totalTokenBudget
  )
    throw new Error(
      `This plan exceeds the current generation allowance (${calls} calls and up to ${tokenAllowance} output tokens). Reduce the plan bounds or repair allowance; no targets were changed.`,
    );
}

/**
 * Validate the plan form against the server's rules and build the request's output plan.
 * Throws an `Error` with a user-facing message for the first problem found, in the order
 * the form is laid out: units, then per-unit limits, then the server's current allowance.
 */
export function buildOutputPlan(
  draft: PlanDraft,
  limits: ArtifactRunLimits | null,
  limitsError: string | null,
): DocumentOutputPlan | WorkbookOutputPlan {
  const plan = draft.kind === 'document' ? buildDocumentPlan(draft) : buildWorkbookPlan(draft);
  if (
    !inIntRange(draft.unitTokens, 64, 8192) ||
    !inIntRange(draft.unitRepairs, 0, 2) ||
    !inIntRange(draft.checkpointBytes, 1024, 16000000)
  )
    throw new Error('Output limits: tokens 64–8,192 per unit, repairs 0–2, checkpoint 1,024–16,000,000 bytes.');
  assertWithinAllowance(plan, draft, limits, limitsError);
  return plan;
}
