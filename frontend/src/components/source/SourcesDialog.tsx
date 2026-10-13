import { useRef, useState } from 'react';
import type { DragEvent } from 'react';
import type { SourceDoc } from '../../api/types';
import { useApp } from '../../state/AppContext';
import { Dialog } from '../shell/Dialog';
import { AlertIcon, BookIcon, RefreshIcon, UploadIcon } from '../icons';
import { statusLabel } from './statusLabel';
import { useReindex } from './useReindex';
import { useSourceUpload } from './useSourceUpload';

interface RowProps {
  source: SourceDoc;
  reindexing: boolean;
  reindexDisabled: boolean;
  reindexNote: string | undefined;
  reindexError: string | undefined;
  onReindex: () => void;
}

function SourceRow({ source, reindexing, reindexDisabled, reindexNote, reindexError, onReindex }: RowProps) {
  const problem = Boolean(source.errorMessage || source.warnings?.length || reindexError);
  return (
    <li className={'source-row' + (source.status === 'failed' ? ' source-row-failed' : '')}>
      <div className="source-row-main">
        <span className="source-row-icon" aria-hidden>
          <BookIcon size={16} />
        </span>
        <span className="source-row-name">{source.name}</span>
        <span className={'badge badge-' + source.status}>{statusLabel(source.status)}</span>
      </div>
      {source.errorMessage && <p className="form-error">{source.errorMessage}</p>}
      {source.warnings?.map((warning, index) => (
        <p className="source-row-warning" key={index}>
          <AlertIcon size={14} /> {warning}
        </p>
      ))}
      {reindexNote && (
        <p className="muted" role="status">
          {reindexNote}
        </p>
      )}
      {reindexError && (
        <p className="form-error" role="alert">
          Reindex failed: {reindexError}
        </p>
      )}
      <details className="source-row-details" open={problem || undefined}>
        <summary>Details</summary>
        <dl>
          <div>
            <dt>Cited by</dt>
            <dd>{source.locatorType}</dd>
          </div>
          <div>
            <dt>Passages</dt>
            <dd>{source.chunkCount}</dd>
          </div>
          {source.indexRevision !== undefined && (
            <div>
              <dt>Index</dt>
              <dd>Index revision {source.indexRevision}</dd>
            </div>
          )}
        </dl>
        {source.canReindex === true && source.indexRevision !== undefined && (
          <button
            type="button"
            className="secondary-button source-reindex-button"
            aria-label={'Reindex ' + source.name}
            disabled={reindexing || reindexDisabled}
            onClick={onReindex}
          >
            <RefreshIcon size={14} />
            {reindexing ? 'Reindexing…' : 'Reindex'}
          </button>
        )}
        <p className="muted small">
          Reindexing re-reads the stored upload. If it fails, the previous index keeps working.
        </p>
      </details>
    </li>
  );
}

function SourcesBody() {
  const { sources, sourcesError, refreshSources, uploadSource } = useApp();
  const fileInput = useRef<HTMLInputElement>(null);
  const upload = useSourceUpload(uploadSource, refreshSources);
  const reindex = useReindex(refreshSources);
  const [dragging, setDragging] = useState(false);

  const handleUpload = async (file?: File) => {
    if (!file) return;
    await upload.upload(file);
    if (fileInput.current) fileInput.current.value = '';
  };
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    void handleUpload(event.dataTransfer.files?.[0]);
  };

  return (
    <div className="stack">
      <button
        type="button"
        className={'dropzone' + (dragging ? ' dropzone-over' : '')}
        onClick={() => fileInput.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        disabled={upload.uploading}
      >
        <UploadIcon size={22} />
        <strong>{upload.uploading ? 'Uploading…' : 'Add a source'}</strong>
        <small>Drop a PDF, text, or Markdown file here, or click to choose one.</small>
      </button>
      <input
        ref={fileInput}
        className="visually-hidden"
        type="file"
        accept=".pdf,.txt,.md,.markdown,text/plain,text/markdown,application/pdf"
        onChange={(event) => void handleUpload(event.currentTarget.files?.[0])}
        aria-label="Choose a PDF, text, or Markdown source"
      />
      {upload.note && (
        <p className="callout callout-info" role="status">
          {upload.note}
        </p>
      )}
      {upload.error && (
        <div className="callout callout-error" role="alert">
          <strong>Upload failed.</strong> {upload.error}
          <p>PDF, plain text, and Markdown are supported. Check that the file opens correctly, then try again.</p>
        </div>
      )}
      {sourcesError && (
        <p className="callout callout-error" role="alert">
          Couldn’t load your sources. {sourcesError}
        </p>
      )}
      <div className="row-between">
        <h3 className="section-title">
          {sources.length} source{sources.length === 1 ? '' : 's'}
        </h3>
        <button
          type="button"
          className="ghost-button"
          onClick={() => void refreshSources().catch(() => undefined)}
          aria-label="Refresh sources"
        >
          <RefreshIcon size={16} /> Refresh
        </button>
      </div>
      {sources.length === 0 && !sourcesError && <p className="muted">No sources yet. Add one above.</p>}
      <ul className="source-list">
        {sources.map((source) => (
          <SourceRow
            key={source.id}
            source={source}
            reindexing={reindex.reindexing.has(source.id)}
            reindexDisabled={upload.uploading}
            reindexNote={reindex.notes[source.id] || undefined}
            reindexError={reindex.errors[source.id] || undefined}
            onReindex={() => void reindex.reindex(source.id, source.indexRevision)}
          />
        ))}
      </ul>
    </div>
  );
}

export function SourcesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog
      open={open}
      title="Your sources"
      description="The documents the advisor answers from. Choose which ones a question uses with “Search in” under the message box."
      onClose={onClose}
    >
      <SourcesBody />
    </Dialog>
  );
}
