import type { ArtifactPreview } from '../../api/types';
import { sandboxDocument } from './previewHtml';
import { address } from './workbook';

interface Props {
  preview: ArtifactPreview;
  /** Fallback heading when the preview carries no title. */
  recordTitle?: string;
}

export function PreviewPane({ preview, recordTitle }: Props) {
  const html = preview.kind === 'html' || preview.kind === 'document' ? preview.html : undefined;
  // The canvas header already shows the artifact's title; repeat it only when the preview's differs.
  const heading = preview.title && preview.title !== recordTitle ? preview.title : null;
  return (
    <section className="artifact-preview" aria-label="Artifact preview">
      {heading && <h3>{heading}</h3>}
      {preview.status === 'needs_recalculation' && (
        <p className="preview-note" role="status">
          Some totals haven’t been worked out yet, so the preview shows the formulas instead of guessing.
        </p>
      )}
      {html !== undefined ? (
        <>
          <p className="preview-note" role="status">
            Links and buttons are turned off in this preview. Download the file to use them.
          </p>
          <iframe
            title="Isolated artifact preview"
            sandbox=""
            referrerPolicy="no-referrer"
            srcDoc={sandboxDocument(html)}
          />
        </>
      ) : preview.sheets ? (
        preview.sheets.map((sheet) => (
          <div key={sheet.id}>
            <h4>{sheet.name}</h4>
            <table>
              <tbody>
                {sheet.cells.map((cell) => (
                  <tr key={cell.id}>
                    <th>{address(cell.row, cell.column)}</th>
                    <td>{cell.formula || String(cell.calculatedValue ?? cell.value ?? '')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))
      ) : (
        <pre>{JSON.stringify(preview.formulas ?? preview, null, 2)}</pre>
      )}
    </section>
  );
}
