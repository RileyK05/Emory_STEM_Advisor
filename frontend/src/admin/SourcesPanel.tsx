import { useRef, useState } from 'react';
import type { AdminSource } from '../api/types';
import { listAdminSources } from '../api/backoffice';
import { getClient } from '../api/client';
import { formatTimestamp } from '../lib/format';
import { RefreshIcon, UploadIcon } from '../components/icons';
import { useReindex } from './useReindex';
import { useSourceUpload } from './useSourceUpload';
import {
  AdminError,
  AdminWorkspace,
  DetailHead,
  FieldLine,
  PAGE,
  Pager,
  RefreshButton,
  usePagedList,
  type AdminPanelProps,
} from './adminUi';
import { SourceReviewPanel } from './SourceReviewPanel';

export function SourcesPanel({ toolbar, canReview }: AdminPanelProps & { canReview: boolean }) {
  const list = usePagedList((offset) => listAdminSources('', PAGE, offset));
  const [selected, setSelected] = useState<AdminSource | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const refreshSources = async () => {
    const rows = await getClient().listSources();
    await list.reload();
    return rows;
  };
  const upload = useSourceUpload(refreshSources);
  const reindex = useReindex(refreshSources);

  return (
    <>
      <RefreshButton toolbar={toolbar} busy={list.working} onRefresh={() => void list.reload()} />
      <AdminError error={list.error} />
      <div className="admin-upload-row">
        <button
          type="button"
          className="secondary-button"
          disabled={upload.uploading}
          onClick={() => fileInput.current?.click()}
        >
          <UploadIcon size={16} />
          {upload.uploading ? 'Uploading…' : 'Upload a source'}
        </button>
        <input
          ref={fileInput}
          className="visually-hidden"
          type="file"
          accept=".pdf,.txt,.md,.markdown,text/plain,text/markdown,application/pdf"
          aria-label="Choose a PDF, text, or Markdown source"
          onChange={(event) => {
            const file = event.currentTarget.files?.[0];
            if (file) void upload.upload(file);
            event.currentTarget.value = '';
          }}
        />
        {upload.note && (
          <span className="muted" role="status">
            {upload.note}
          </span>
        )}
        {upload.error && (
          <span className="form-error" role="alert">
            {upload.error}
          </span>
        )}
      </div>
      <AdminWorkspace
        detail={
          selected && (
            <>
              <DetailHead title={selected.filename} onClose={() => setSelected(null)} />
              <FieldLine label="Status">{selected.status}</FieldLine>
              <FieldLine label="Workspace">
                <span className="mono">{selected.workspaceKey || 'shared'}</span>
              </FieldLine>
              <FieldLine label="Collection">
                <span className="mono">{selected.collectionId}</span>
              </FieldLine>
              <FieldLine label="Content type">{selected.contentType}</FieldLine>
              <FieldLine label="Index revision">{selected.indexRevision}</FieldLine>
              <FieldLine label="Current">{selected.isCurrent ? 'yes' : 'no'}</FieldLine>
              <FieldLine label="Created">{formatTimestamp(selected.createdAt)}</FieldLine>
              <p className="admin-sub">
                Source deletion stays in ingest tooling because it also owns raw-file cleanup.
              </p>
              {canReview && (
                <button
                  type="button"
                  className="secondary-button"
                  disabled={reindex.reindexing.has(selected.sourceId)}
                  onClick={() => void reindex.reindex(selected.sourceId, selected.indexRevision)}
                >
                  <RefreshIcon size={14} />
                  {reindex.reindexing.has(selected.sourceId) ? 'Reindexing…' : 'Reindex source'}
                </button>
              )}
              {reindex.notes[selected.sourceId] && <p role="status">{reindex.notes[selected.sourceId]}</p>}
              {reindex.errors[selected.sourceId] && (
                <p className="form-error" role="alert">
                  {reindex.errors[selected.sourceId]}
                </p>
              )}
              {canReview && <SourceReviewPanel sourceId={selected.sourceId} indexRevision={selected.indexRevision} />}
            </>
          )
        }
      >
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Filename</th>
                <th>Workspace</th>
                <th>Status</th>
                <th>Current</th>
                <th>Revision</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {list.rows.map((source) => (
                <tr key={source.sourceId} className="admin-clickable" onClick={() => setSelected(source)}>
                  <td>{source.filename}</td>
                  <td className="mono">{source.workspaceKey || 'shared'}</td>
                  <td>{source.status}</td>
                  <td>{source.isCurrent ? 'yes' : 'no'}</td>
                  <td>{source.indexRevision}</td>
                  <td>{formatTimestamp(source.updatedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager offset={list.offset} count={list.rows.length} busy={list.working} onOffset={list.setOffset} />
      </AdminWorkspace>
    </>
  );
}
