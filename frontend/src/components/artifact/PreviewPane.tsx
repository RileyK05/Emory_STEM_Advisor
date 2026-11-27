import type { ArtifactPreview } from '../../api/types';
import { sandboxDocument } from './previewHtml';
import { address, sheetGrid } from './workbook';

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
      {preview.status === 'needs_recalculation' && !preview.sheets && (
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
        preview.sheets.map((sheet) => {
          const grid = sheetGrid(sheet.cells);
          return (
            <div key={sheet.id} className="sheet-preview">
              <h4>{sheet.name}</h4>
              {grid.unresolved > 0 && (
                <p className="preview-note" role="status">
                  Some cells use formulas the preview can’t work out; they show as formulas here and calculate when you
                  open the downloaded file.
                </p>
              )}
              <div className="sheet-scroll">
                <table>
                  <thead>
                    <tr>
                      <th aria-hidden="true" />
                      {Array.from({ length: grid.columns }, (_, index) => (
                        <th key={index} scope="col">
                          {address(1, index + 1).replace(/\d+$/, '')}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {grid.rows.map(({ row, shown }) => (
                      <tr key={row}>
                        <th scope="row">{row}</th>
                        {shown.map((text, index) => (
                          <td key={index}>{text}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })
      ) : (
        <pre>{JSON.stringify(preview.formulas ?? preview, null, 2)}</pre>
      )}
    </section>
  );
}
