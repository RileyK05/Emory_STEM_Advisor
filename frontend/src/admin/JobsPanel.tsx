import { useState } from 'react';
import type { AdminJob } from '../api/types';
import { cancelAdminJob, listAdminJobs } from '../api/backoffice';
import { formatTimestamp } from '../lib/format';
import {
  AdminError,
  AdminSection,
  AdminWorkspace,
  DetailHead,
  EmptyRow,
  FieldLine,
  PAGE,
  Pager,
  usePagedList,
} from './adminUi';

export function JobsPanel() {
  const list = usePagedList((offset) => listAdminJobs('', PAGE, offset));
  const [selected, setSelected] = useState<AdminJob | null>(null);

  const cancelJob = async (job: AdminJob) => {
    if (!window.confirm(`Cancel job ${job.jobId}?`)) return;
    await list.run(async () => {
      const updated = await cancelAdminJob(job.jobId);
      setSelected((current) => (current?.jobId === job.jobId ? updated : current));
      await list.reload();
    });
  };

  return (
    <AdminSection
      title="Background jobs"
      description="Saved conversation turns and other work the job worker runs. Select a job to see its detail."
      busy={list.working}
      onRefresh={() => void list.reload()}
    >
      <AdminError error={list.error} />
      <AdminWorkspace
        detail={
          selected && (
            <>
              <DetailHead title="Job" onClose={() => setSelected(null)} />
              <FieldLine label="Job ID">
                <span className="mono">{selected.jobId}</span>
              </FieldLine>
              <FieldLine label="Kind">{selected.kind}</FieldLine>
              <FieldLine label="Status">
                {selected.status}
                {selected.cancelRequested ? ' (cancel requested)' : ''}
              </FieldLine>
              <FieldLine label="Scope">
                <span className="mono">{selected.scopeKey}</span>
              </FieldLine>
              <FieldLine label="Attempts">
                {selected.attemptCount}/{selected.maxAttempts}
              </FieldLine>
              <FieldLine label="Created">{formatTimestamp(selected.createdAt)}</FieldLine>
              <FieldLine label="Finished">{formatTimestamp(selected.finishedAt)}</FieldLine>
              {selected.error && <pre className="admin-json">{JSON.stringify(selected.error, null, 2)}</pre>}
              {['queued', 'running'].includes(selected.status) && (
                <button
                  type="button"
                  className="danger-button"
                  disabled={list.working || selected.cancelRequested}
                  onClick={() => void cancelJob(selected)}
                >
                  {selected.cancelRequested ? 'Cancel requested' : 'Cancel job'}
                </button>
              )}
            </>
          )
        }
      >
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Status</th>
                <th>Scope</th>
                <th>Attempts</th>
                <th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.rows.length === 0 && (
                <EmptyRow columns={6} loading={list.working}>
                  No jobs yet.
                </EmptyRow>
              )}
              {list.rows.map((job) => (
                <tr key={job.jobId} className="admin-clickable" onClick={() => setSelected(job)}>
                  <td>{job.kind}</td>
                  <td>
                    {job.status}
                    {job.cancelRequested && job.status === 'running' ? ' (cancel requested)' : ''}
                  </td>
                  <td className="mono">{job.scopeKey}</td>
                  <td>
                    {job.attemptCount}/{job.maxAttempts}
                  </td>
                  <td>{formatTimestamp(job.createdAt)}</td>
                  <td>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelected(job);
                      }}
                    >
                      View
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager offset={list.offset} count={list.rows.length} busy={list.working} onOffset={list.setOffset} />
      </AdminWorkspace>
    </AdminSection>
  );
}
