import type { WorkbookSpec } from '../../api/types';
import { address } from './workbook';

const MAX_ROWS = 200;
const MAX_COLUMNS = 40;

interface Props {
  workbook: WorkbookSpec;
  activeSheetId: string | undefined;
  /** Edited cell text keyed `sheetId:cellId`; absent means the saved value is shown. */
  cellDrafts: Record<string, string>;
  disabled?: boolean;
  onSheetChange: (sheetId: string) => void;
  onCellChange: (key: string, text: string) => void;
}

function columnLetters(column: number) {
  return address(1, column).replace(/\d+$/, '');
}

/** A spreadsheet grid. Only cells the workbook already has are editable; edits keep their cell ID and style. */
export function WorkbookEditor({ workbook, activeSheetId, cellDrafts, disabled, onSheetChange, onCellChange }: Props) {
  const activeSheet = workbook.sheets.find((sheet) => sheet.id === activeSheetId) ?? workbook.sheets[0];
  const cells = activeSheet?.cells ?? [];
  const byPosition = new Map(cells.map((cell) => [`${cell.row}:${cell.column}`, cell]));
  const rows = Math.min(MAX_ROWS, Math.max(1, ...cells.map((cell) => cell.row)));
  const columns = Math.min(MAX_COLUMNS, Math.max(1, ...cells.map((cell) => cell.column)));
  const hidden = cells.filter((cell) => cell.row > MAX_ROWS || cell.column > MAX_COLUMNS).length;

  return (
    <section className="workbook-editor" aria-label="Workbook editor">
      <div className="grid-scroll">
        <table className="grid">
          <thead>
            <tr>
              <th aria-hidden />
              {Array.from({ length: columns }, (_, index) => (
                <th key={index} scope="col">
                  {columnLetters(index + 1)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: rows }, (_, rowIndex) => (
              <tr key={rowIndex}>
                <th scope="row">{rowIndex + 1}</th>
                {Array.from({ length: columns }, (_, columnIndex) => {
                  const cell = byPosition.get(`${rowIndex + 1}:${columnIndex + 1}`);
                  if (!cell) return <td key={columnIndex} className="grid-empty" />;
                  const key = `${activeSheet.id}:${cell.id}`;
                  const edited = cellDrafts[key] !== undefined;
                  return (
                    <td
                      key={columnIndex}
                      className={(cell.formula ? 'grid-formula ' : '') + (edited ? 'grid-edited' : '')}
                      style={{ fontWeight: cell.style?.bold ? 700 : undefined }}
                    >
                      <input
                        aria-label={`Cell ${address(cell.row, cell.column)}`}
                        value={cellDrafts[key] ?? String(cell.formula ?? cell.value ?? '')}
                        disabled={disabled}
                        title={cell.formula ? 'Formula' : undefined}
                        onChange={(e) => onCellChange(key, e.target.value)}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {cells.length === 0 && <p className="muted">This sheet has no cells that can be edited here.</p>}
      {hidden > 0 && <p className="muted">{hidden} cells beyond the visible area aren’t shown.</p>}
      {workbook.sheets.length > 1 && (
        <div className="sheet-tabs" role="tablist" aria-label="Worksheets">
          {workbook.sheets.map((sheet) => (
            <button
              key={sheet.id}
              type="button"
              role="tab"
              aria-selected={sheet.id === activeSheet?.id}
              disabled={disabled}
              onClick={() => onSheetChange(sheet.id)}
            >
              {sheet.name}
            </button>
          ))}
        </div>
      )}
      {workbook.charts?.length ? (
        <p className="muted">
          Charts ({workbook.charts.map((chart) => chart.title || chart.type).join(', ')}) are kept but can’t be edited
          here.
        </p>
      ) : null}
    </section>
  );
}
