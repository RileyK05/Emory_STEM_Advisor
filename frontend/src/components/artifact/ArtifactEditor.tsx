import { useCallback, useEffect, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type { ArtifactPreview, ArtifactRevisionSummary, NativeArtifactRecord } from '../../api/types';
import { useAsyncAction } from '../../lib/useAsyncAction';
import { useDismiss } from '../../lib/useDismiss';
import { CloseIcon, DownloadIcon, KIND_LABEL, KindIcon } from '../icons';
import { PreviewPane } from './PreviewPane';
import { saveBlob } from './download';

type Format = 'md' | 'docx' | 'xlsx' | 'html';

const FORMATS: Record<NativeArtifactRecord['kind'], { format: Format; label: string }[]> = {
  document: [
    { format: 'docx', label: 'Word document' },
    { format: 'md', label: 'Markdown' },
  ],
  workbook: [{ format: 'xlsx', label: 'Excel workbook' }],
  html: [{ format: 'html', label: 'HTML page' }],
};

function DownloadMenu({
  record,
  disabled,
  onDownload,
  onOriginal,
}: {
  record: NativeArtifactRecord;
  disabled: boolean;
  onDownload: (format: Format) => void;
  onOriginal: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  return (
    <div className="menu-anchor" ref={ref}>
      <button
        type="button"
        className="secondary-button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        <DownloadIcon size={16} /> Download
      </button>
      {open && (
        <div className="popover menu menu-right" role="menu">
          {FORMATS[record.kind].map(({ format, label }) => (
            <button
              key={format}
              type="button"
              role="menuitem"
              aria-label={`Download .${format}`}
              onClick={() => {
                setOpen(false);
                onDownload(format);
              }}
            >
              {label} <span className="muted">.{format}</span>
            </button>
          ))}
          {record.original?.preserved && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onOriginal();
              }}
            >
              Original file <span className="muted">{record.original.filename}</span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}

interface Props {
  record: NativeArtifactRecord;
  onOpenVersion: (artifactId: string, revisionId: string) => Promise<void>;
  onClose: () => void;
}

/** Read-only artifact preview with version history and download. Editing happens in chat. */
export function ArtifactEditor({ record, onOpenVersion, onClose }: Props) {
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [revisions, setRevisions] = useState<ArtifactRevisionSummary[]>([]);
  const [version, setVersion] = useState(record.revisionId);
  const { busy, error, notice, run } = useAsyncAction();

  useEffect(() => {
    setPreview(null);
    setVersion(record.revisionId);
    void run(async () => setPreview(await getClient().previewArtifact(record.artifactId, record.revisionId)));
    void getClient()
      .listArtifactRevisions(record.artifactId)
      .then(setRevisions)
      .catch(() => setRevisions([]));
  }, [record.artifactId, record.revisionId]);

  const download = (format: Format) =>
    run(async () => saveBlob(await getClient().downloadArtifact(record.artifactId, record.revisionId, format)));
  const downloadOriginal = () =>
    run(async () => saveBlob(await getClient().downloadOriginalArtifact(record.artifactId)));

  return (
    <section className="canvas" aria-label="Artifact preview">
      <header className="canvas-header">
        <span className="canvas-kind" title={KIND_LABEL[record.kind]}>
          <KindIcon kind={record.kind} />
        </span>
        <h2 className="canvas-title">{record.title}</h2>
        <button type="button" className="icon-button" aria-label="Close artifact" onClick={onClose}>
          <CloseIcon />
        </button>
      </header>
      <div className="canvas-toolbar">
        <label className="artifact-version">
          <span className="muted">Version</span>
          <select
            value={version}
            disabled={busy || revisions.length === 0}
            onChange={(event) => {
              const next = event.target.value;
              setVersion(next);
              if (next !== record.revisionId) void run(async () => onOpenVersion(record.artifactId, next));
            }}
          >
            {revisions.length === 0 && <option value={record.revisionId}>Current</option>}
            {revisions.map((item) => (
              <option key={item.revisionId} value={item.revisionId}>
                {item.revisionId.slice(0, 8)} · {new Date(item.createdAt).toLocaleDateString()}
              </option>
            ))}
          </select>
        </label>
        <span className="toolbar-spacer" />
        <DownloadMenu record={record} disabled={busy} onDownload={download} onOriginal={downloadOriginal} />
      </div>
      {(error || notice || record.status === 'needs_recalculation') && (
        <div className="canvas-messages">
          {record.status === 'needs_recalculation' && (
            <p className="callout callout-info" role="status">
              Formula results need recalculation{record.message ? `: ${record.message}` : '.'}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
          {error && <p role="alert">{error}</p>}
        </div>
      )}
      <div className="canvas-body">
        {preview ? (
          <PreviewPane preview={preview} recordTitle={record.title} />
        ) : (
          <p className="muted" role="status">
            Loading preview…
          </p>
        )}
      </div>
      <footer className="canvas-footer">
        <span className="muted" title={`Artifact ${record.artifactId} · revision ${record.revisionId}`}>
          {KIND_LABEL[record.kind]} · version {record.revisionId.slice(0, 8)}
          {record.original?.preserved ? ` · imported from ${record.original.filename}` : ''}
        </span>
      </footer>
    </section>
  );
}
