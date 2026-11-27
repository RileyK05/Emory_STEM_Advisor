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

type CellValue = string | number | boolean | null | undefined;

export interface GridCell {
  row: number;
  column: number;
  value?: CellValue;
  formula?: string | null;
  calculatedValue?: CellValue;
}

function columnNumber(letters: string): number {
  return [...letters.toUpperCase()].reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0);
}

function parseAddress(text: string): { row: number; column: number } | null {
  const match = /^\$?([A-Za-z]{1,3})\$?(\d{1,6})$/.exec(text.trim());
  return match ? { column: columnNumber(match[1]), row: Number(match[2]) } : null;
}

/**
 * The value a preview can show for a formula without guessing: SUM over cells and
 * ranges, counting only numbers (as a spreadsheet's SUM does). Anything else, or a
 * formula that loops, is null, and the preview shows the formula itself.
 */
export function evaluateFormula(
  formula: string,
  cellAt: (row: number, column: number) => GridCell | undefined,
  depth = 0,
): number | null {
  const match = /^=\s*SUM\((.*)\)\s*$/i.exec(formula);
  if (!match || depth > 8) return null;
  let total = 0;
  for (const part of match[1].split(',')) {
    const arg = part.trim();
    if (!arg) continue;
    if (/^-?\d+(?:\.\d+)?$/.test(arg)) {
      total += Number(arg);
      continue;
    }
    const [start, end = start] = arg.split(':');
    const from = parseAddress(start);
    const to = parseAddress(end);
    if (!from || !to) return null;
    for (let row = Math.min(from.row, to.row); row <= Math.max(from.row, to.row); row++) {
      for (let column = Math.min(from.column, to.column); column <= Math.max(from.column, to.column); column++) {
        const cell = cellAt(row, column);
        if (!cell) continue;
        const value = cell.formula
          ? (cell.calculatedValue ?? evaluateFormula(cell.formula, cellAt, depth + 1))
          : cell.value;
        if (cell.formula && value === null) return null;
        if (typeof value === 'number') total += value;
        else if (typeof value === 'string' && /^-?\d+(?:\.\d+)?$/.test(value.trim())) total += Number(value);
      }
    }
  }
  return Math.round(total * 1e6) / 1e6;
}

/** A sheet's cells as rows and columns, with what each cell shows in a preview. */
export function sheetGrid(cells: GridCell[]): {
  columns: number;
  rows: { row: number; shown: string[] }[];
  unresolved: number;
} {
  const byAddress = new Map(cells.map((cell) => [`${cell.row}:${cell.column}`, cell]));
  const cellAt = (row: number, column: number) => byAddress.get(`${row}:${column}`);
  const columns = cells.reduce((n, cell) => Math.max(n, cell.column), 0);
  const lastRow = cells.reduce((n, cell) => Math.max(n, cell.row), 0);
  let unresolved = 0;
  const rows = [];
  for (let row = 1; row <= lastRow; row++) {
    const shown = [];
    for (let column = 1; column <= columns; column++) {
      const cell = cellAt(row, column);
      if (!cell) {
        shown.push('');
      } else if (cell.formula) {
        const value = cell.calculatedValue ?? evaluateFormula(cell.formula, cellAt);
        if (value === null || value === undefined) unresolved++;
        shown.push(value === null || value === undefined ? cell.formula : String(value));
      } else {
        shown.push(cell.value === null || cell.value === undefined ? '' : String(cell.value));
      }
    }
    rows.push({ row, shown });
  }
  return { columns, rows, unresolved };
}
