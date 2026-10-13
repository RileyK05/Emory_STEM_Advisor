import type { WorkbookSpec } from '../../api/types';
import { makeId } from './ids';

/** Spreadsheet address (`A1`, `AB12`) for a 1-based row and column. */
export function address(row: number, column: number) {
  let n = column;
  let letters = '';
  while (n > 0) {
    const x = (n - 1) % 26;
    letters = String.fromCharCode(65 + x) + letters;
    n = Math.floor((n - 1) / 26);
  }
  return letters + row;
}

export const emptyWorkbook = (title: string): WorkbookSpec => ({
  kind: 'workbook',
  title,
  sheets: [
    { id: makeId('sheet'), name: 'Sheet1', cells: [{ id: makeId('cell'), row: 1, column: 1, value: null, style: {} }] },
  ],
  provenance: {},
});

type CellValue = string | number | boolean | null;

/** Interpret edited text for a cell: a leading `=` is a formula, otherwise keep the cell's original type. */
function interpretCellEdit(text: string, original: CellValue | undefined) {
  const trimmed = text.trim();
  if (trimmed.startsWith('=')) return { formula: trimmed, value: null as CellValue };
  let value: CellValue = text;
  if (typeof original === 'number' && trimmed !== '' && Number.isFinite(Number(text))) value = Number(text);
  else if (typeof original === 'boolean' && /^(true|false)$/i.test(trimmed)) value = trimmed.toLowerCase() === 'true';
  else if (text === '') value = null;
  return { formula: null, value };
}

/**
 * Revision operations for the cells whose edited text differs from the saved cell.
 * `drafts` is keyed `sheetId:cellId`; an untouched or unchanged cell produces nothing.
 */
export function workbookEditOperations(spec: WorkbookSpec, drafts: Record<string, string>) {
  return spec.sheets.flatMap((sheet) =>
    sheet.cells.flatMap((cell) => {
      const draft = drafts[`${sheet.id}:${cell.id}`];
      if (draft === undefined || draft === String(cell.formula ?? cell.value ?? '')) return [];
      const { formula, value } = interpretCellEdit(draft, cell.value);
      return [{ type: 'set_cell', sheet_id: sheet.id, cell_id: cell.id, cell: { ...cell, value, formula } }];
    }),
  );
}
