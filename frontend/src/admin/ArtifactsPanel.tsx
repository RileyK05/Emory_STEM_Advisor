import { useState } from 'react';
import type { AdminArtifact } from '../api/types';
import { listAdminArtifacts } from '../api/backoffice';
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

export function ArtifactsPanel({ toolbar }: AdminPanelProps) {
  const list = usePagedList((offset) => listAdminArtifacts(PAGE, offset));
  const [selected, setSelected] = useState<AdminArtifact | null>(null);

  return (
    <>
      <RefreshButton toolbar={toolbar} busy={list.working} onRefresh={() => void list.reload()} />
      <AdminError error={list.error} />
      <AdminWorkspace
        detail={
          selected && (
            <>
              <DetailHead title={selected.kind} onClose={() => setSelected(null)} />
              <FieldLine label="Artifact">
                <span className="mono">{selected.artifactId}</span>
              </FieldLine>
              <FieldLine label="Head revision">
                <span className="mono">{selected.headRevisionId}</span>
              </FieldLine>
              <FieldLine label="Workspace">
                <span className="mono">{selected.workspaceKey}</span>
              </FieldLine>
              <FieldLine label="Revisions">{selected.revisionCount}</FieldLine>
              <FieldLine label="Original file">{selected.originalFilename || '—'}</FieldLine>
              <FieldLine label="Created">{formatTimestamp(selected.createdAt)}</FieldLine>
              <p className="admin-sub">Artifact revision deletion stays in the artifact revision workflow.</p>
            </>
          )
        }
      >
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Workspace</th>
                <th>Revisions</th>
                <th>Original file</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {list.rows.map((artifact) => (
                <tr key={artifact.artifactId} onClick={() => setSelected(artifact)}>
                  <td>{artifact.kind}</td>
                  <td className="mono">{artifact.workspaceKey}</td>
                  <td>{artifact.revisionCount}</td>
                  <td>{artifact.originalFilename || '—'}</td>
                  <td>{formatTimestamp(artifact.createdAt)}</td>
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
