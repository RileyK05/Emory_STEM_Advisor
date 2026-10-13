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
  return (
    <section className="artifact-preview" aria-label="Artifact preview">
      <h3>{preview.title ?? recordTitle ?? 'Artifact preview'}</h3>
      {preview.status === 'needs_recalculation' && (
        <p role="status">Formula values need recalculation. Preview shows formulas and does not guess results.</p>
      )}
      {html !== undefined ? (
        <>
          <p role="status">HTML preview is static for safety: scripts and navigation are disabled.</p>
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
