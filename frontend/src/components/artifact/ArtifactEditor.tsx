import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type { ArtifactPreview, DocumentSpec, NativeArtifactRecord, WorkbookSpec } from '../../api/types';
import { useAsyncAction } from '../../lib/useAsyncAction';
import { useDismiss } from '../../lib/useDismiss';
import { BackIcon, ChatIcon, CloseIcon, DownloadIcon, KIND_LABEL, KindIcon, SparkIcon } from '../icons';
import { DocumentEditor } from './DocumentEditor';
import { PreviewPane } from './PreviewPane';
import { WorkbookEditor } from './WorkbookEditor';
import { editedDocument, unverifiedBlockIds, withBlockPatch } from './documentDraft';
import { saveBlob } from './download';
import { workbookEditOperations } from './workbook';

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
  /** A new revision was saved or loaded. */
  onRecord: (record: NativeArtifactRecord) => void;
  onBack: () => void;
  onClose: () => void;
  onAskChanges: (record: NativeArtifactRecord) => void;
  /** Attach this version to the next chat message. */
  onDiscuss?: (record: NativeArtifactRecord) => void;
}

export function ArtifactEditor({ record, onRecord, onBack, onClose, onAskChanges, onDiscuss }: Props) {
  const [title, setTitle] = useState(record.title);
  const [documentDraft, setDocumentDraft] = useState<DocumentSpec | null>(() =>
    record.spec.kind === 'document' ? structuredClone(record.spec) : null,
  );
  const [htmlDraft, setHtmlDraft] = useState(record.spec.kind === 'html' ? record.spec.html : '');
  const [cellDrafts, setCellDrafts] = useState<Record<string, string>>({});
  const [sheetId, setSheetId] = useState<string | null>(null);
  const [view, setView] = useState<'edit' | 'preview'>('edit');
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const { busy, error, notice, setNotice, run } = useAsyncAction();
  // A retried save with an unchanged payload reuses its draft id so the server can dedupe it.
  const draftIdRef = useRef<{ fingerprint: string; id: string } | null>(null);

  // Opening, saving, or reloading a record discards the preview and any per-revision editor state.
  useEffect(() => {
    setTitle(record.title);
    setDocumentDraft(record.spec.kind === 'document' ? structuredClone(record.spec) : null);
    setHtmlDraft(record.spec.kind === 'html' ? record.spec.html : '');
    setCellDrafts({});
    setSheetId(null);
    setPreview(null);
    setView('edit');
  }, [record]);

  const edited = useMemo(
    () => (record.spec.kind === 'document' && documentDraft ? editedDocument(record.spec, documentDraft) : null),
    [documentDraft, record],
  );
  const changed = edited?.changed ?? new Set<string>();
  const unverified = record.spec.kind === 'document' ? unverifiedBlockIds(record.spec) : new Set<string>();
  const workbook = record.spec.kind === 'workbook' ? record.spec : null;
  const titleChanged = title !== record.title;
  const dirty =
    titleChanged ||
    (record.spec.kind === 'document' && changed.size > 0) ||
    (record.spec.kind === 'html' && htmlDraft !== record.spec.html) ||
    (workbook !== null && workbookEditOperations(workbook, cellDrafts).length > 0);

  /** Save the open artifact as a new revision; returns the saved record, or null if nothing changed. */
  const saveCurrent = async (current: NativeArtifactRecord): Promise<NativeArtifactRecord | null> => {
    const saveWithId = async (payload: Record<string, unknown>) => {
      const fingerprint = JSON.stringify({ artifactId: current.artifactId, parent: current.revisionId, payload });
      if (!draftIdRef.current || draftIdRef.current.fingerprint !== fingerprint)
        draftIdRef.current = { fingerprint, id: crypto.randomUUID() };
      return getClient().saveArtifactRevision(current.artifactId, {
        expectedParentRevisionId: current.revisionId,
        ...payload,
        draftId: draftIdRef.current.id,
      });
    };
    if (current.spec.kind === 'workbook') {
      const operations = workbookEditOperations(current.spec, cellDrafts);
      if (!operations.length && !titleChanged) return null;
      if (!titleChanged) return saveWithId({ operations });
      const cellsByCoordinate = new Map(
        operations.map((operation) => [`${operation.sheet_id}:${operation.cell_id}`, operation.cell]),
      );
      const spec: WorkbookSpec = {
        ...current.spec,
        title,
        sheets: current.spec.sheets.map((sheet) => ({
          ...sheet,
          cells: sheet.cells.map((cell) => cellsByCoordinate.get(`${sheet.id}:${cell.id}`) ?? cell),
        })),
      };
      return saveWithId({ spec });
    }
    if (current.spec.kind === 'document' && edited) {
      if (!changed.size && !titleChanged) return null;
      return saveWithId({ spec: edited.spec });
    }
    if (current.spec.kind === 'html') {
      if (htmlDraft === current.spec.html && !titleChanged) return null;
      return saveWithId({ spec: { ...current.spec, title, html: htmlDraft } });
    }
    return null;
  };

  const save = () =>
    run(async () => {
      const editedBlocks = edited?.changed.size ?? 0;
      const next = await saveCurrent(record);
      if (!next) return;
      draftIdRef.current = null;
      onRecord(next);
      setNotice(
        editedBlocks
          ? 'Saved as a new version. Parts you edited are marked unverified; everything else keeps its sources.'
          : 'Saved as a new version.',
      );
    });

  const showPreview = () => {
    setView('preview');
    if (!preview)
      void run(async () => setPreview(await getClient().previewArtifact(record.artifactId, record.revisionId)));
  };

  const download = (format: Format) =>
    run(async () => saveBlob(await getClient().downloadArtifact(record.artifactId, record.revisionId, format)));
  const downloadOriginal = () =>
    run(async () => saveBlob(await getClient().downloadOriginalArtifact(record.artifactId)));

  return (
    <section className="canvas" aria-label="Artifact editor">
      <header className="canvas-header">
        <button type="button" className="icon-button" aria-label="Back to artifacts" onClick={onBack}>
          <BackIcon />
        </button>
        <span className="canvas-kind" title={KIND_LABEL[record.kind]}>
          <KindIcon kind={record.kind} />
        </span>
        <input
          className="canvas-title"
          aria-label="Title"
          value={title}
          maxLength={500}
          disabled={busy}
          onChange={(e) => {
            const next = e.target.value;
            setTitle(next);
            setDocumentDraft((d) => (d ? { ...d, title: next } : d));
          }}
        />
        <button type="button" className="icon-button" aria-label="Close artifact" onClick={onClose}>
          <CloseIcon />
        </button>
      </header>
      <div className="canvas-toolbar">
        <div className="segmented segmented-small" role="group" aria-label="View">
          <button type="button" aria-pressed={view === 'edit'} onClick={() => setView('edit')}>
            Edit
          </button>
          <button type="button" aria-pressed={view === 'preview'} onClick={showPreview} disabled={busy}>
            Preview
          </button>
        </div>
        <button type="button" className="ghost-button" onClick={() => onAskChanges(record)} disabled={busy}>
          <SparkIcon size={16} /> Ask for changes
        </button>
        {onDiscuss && (
          <button type="button" className="ghost-button" onClick={() => onDiscuss(record)} disabled={busy}>
            <ChatIcon size={16} /> Discuss in chat
          </button>
        )}
        <span className="toolbar-spacer" />
        <DownloadMenu record={record} disabled={busy} onDownload={download} onOriginal={downloadOriginal} />
        <button type="button" className="primary-button" onClick={save} disabled={busy || !dirty}>
          {busy ? 'Working…' : dirty ? 'Save changes' : 'Saved'}
        </button>
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
        {view === 'preview' ? (
          preview ? (
            <>
              {dirty && <p className="muted">Showing the last saved version — save to preview your edits.</p>}
              <PreviewPane preview={preview} recordTitle={record.title} />
            </>
          ) : (
            <p className="muted" role="status">
              Loading preview…
            </p>
          )
        ) : workbook ? (
          <WorkbookEditor
            workbook={workbook}
            activeSheetId={sheetId ?? undefined}
            cellDrafts={cellDrafts}
            disabled={busy}
            onSheetChange={setSheetId}
            onCellChange={(key, text) => setCellDrafts((current) => ({ ...current, [key]: text }))}
          />
        ) : record.kind === 'document' && documentDraft ? (
          <DocumentEditor
            draft={documentDraft}
            changed={changed}
            unverified={unverified}
            disabled={busy}
            onBlockChange={(blockId, patch) => setDocumentDraft((d) => d && withBlockPatch(d, blockId, patch))}
          />
        ) : record.kind === 'html' ? (
          <label className="field">
            <span>HTML content</span>
            <textarea
              className="code"
              rows={18}
              value={htmlDraft}
              disabled={busy}
              onChange={(e) => setHtmlDraft(e.target.value)}
            />
          </label>
        ) : null}
      </div>
      <footer className="canvas-footer">
        <span className="muted" title={`Artifact ${record.artifactId} · revision ${record.revisionId}`}>
          {KIND_LABEL[record.kind]} · version {record.revisionId.slice(0, 8)}
          {record.original?.preserved ? ` · imported from ${record.original.filename}` : ''}
        </span>
        {dirty && <span className="tag tag-warn">Unsaved changes</span>}
      </footer>
    </section>
  );
}
