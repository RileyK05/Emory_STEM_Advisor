import { useState } from 'react';
import type { AdminOverview } from '../api/types';
import { getAdminOverview } from '../api/backoffice';
import { useLoad } from '../lib/useLoad';
import { AdminError, AdminSection, formatCount } from './adminUi';

export function OverviewPanel() {
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
        ['Queued jobs', overview.queuedJobs],
        ['Running jobs', overview.runningJobs],
        ['Failed jobs', overview.failedJobs],
        ['Sources', overview.sources],
        ['Artifacts', overview.artifacts],
        ['Retrieval traces', overview.traces],
      ]
    : [];

  return (
    <AdminSection
      title="At a glance"
      description="Counts across every workspace, as of the last refresh."
      busy={loading}
      onRefresh={() => void reload()}
    >
      <AdminError error={error} />
      {overview && (
        <div className="admin-cards">
          {cards.map(([label, value]) => (
            <div className="admin-card" key={label}>
              <span>{label}</span>
              <strong>{formatCount(value)}</strong>
            </div>
          ))}
        </div>
      )}
    </AdminSection>
  );
}
