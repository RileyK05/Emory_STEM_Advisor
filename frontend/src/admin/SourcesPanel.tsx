import { useState } from 'react';
import type { AdminSource } from '../api/types';
import { listAdminSources } from '../api/backoffice';
import { formatTimestamp } from '../lib/format';
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

  return (
    <>
      <RefreshButton toolbar={toolbar} busy={list.working} onRefresh={() => void list.reload()} />
      <AdminError error={list.error} />
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
