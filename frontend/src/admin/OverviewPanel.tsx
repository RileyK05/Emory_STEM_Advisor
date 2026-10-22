import { useState } from 'react';
import type { AdminOverview } from '../api/types';
import { getAdminOverview } from '../api/backoffice';
import { useLoad } from '../lib/useLoad';
import { AdminError, AdminWorkspace, RefreshButton, type AdminPanelProps } from './adminUi';

export function OverviewPanel({ toolbar }: AdminPanelProps) {
  const [overview, setOverview] = useState<AdminOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { loading, reload } = useLoad(getAdminOverview, [], { onLoad: setOverview, setError });

  const cards: Array<[string, number]> = overview
    ? [
        ['Users', overview.users],
        ['Admins', overview.admins],
        ['Active sessions', overview.activeSessions],
        ['Conversations', overview.conversations],
        ['Turns', overview.turns],
        ['Compaction checkpoints', overview.summaries],
        ['Queued jobs', overview.queuedJobs],
        ['Running jobs', overview.runningJobs],
        ['Failed jobs', overview.failedJobs],
        ['Sources', overview.sources],
        ['Artifacts', overview.artifacts],
        ['Retrieval traces', overview.traces],
      ]
    : [];

  return (
    <>
      <RefreshButton toolbar={toolbar} busy={loading} onRefresh={() => void reload()} />
      <AdminError error={error} />
      <AdminWorkspace>
        {overview && (
          <div className="admin-cards">
            {cards.map(([label, value]) => (
              <div className="admin-card" key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
        )}
      </AdminWorkspace>
    </>
  );
}
