import { useState } from 'react';
import type { ReactNode } from 'react';
import type { AuthSession } from '../api/types';
import { isDemoMode } from '../api/client';
import { TopBar } from '../components/shell/TopBar';
import { SessionGate } from '../app/SessionGate';
import { ArtifactsPanel } from './ArtifactsPanel';
import { AuditPanel } from './AuditPanel';
import { CatalogRefreshPanel } from './CatalogRefreshPanel';
import { ChatsPanel } from './ChatsPanel';
import { CollectionsPanel } from './CollectionsPanel';
import { CourseMaintenancePanel } from './CourseMaintenancePanel';
import { ExperimentPanel } from './ExperimentPanel';
import { FeatureFlagsPanel } from './FeatureFlagsPanel';
import { FeedbackTriagePanel } from './FeedbackTriagePanel';
import { GrantsPanel } from './GrantsPanel';
import { JobsPanel } from './JobsPanel';
import { OverviewPanel } from './OverviewPanel';
import { PromptPromotionPanel } from './PromptPromotionPanel';
import { SourcesPanel } from './SourcesPanel';
import { TelemetryPanel } from './TelemetryPanel';
import { UsersPanel } from './UsersPanel';

type Tab =
  | 'overview'
  | 'users'
  | 'chats'
  | 'jobs'
  | 'sources'
  | 'artifacts'
  | 'collections'
  | 'audit'
  | 'telemetry'
  | 'flags'
  | 'courses'
  | 'grants'
  | 'refresh'
  | 'triage'
  | 'experiments'
  | 'prompts';

const TABS: Array<[Tab, string]> = [
  ['overview', 'Overview'],
  ['users', 'Users'],
  ['chats', 'Chats'],
  ['jobs', 'Jobs'],
  ['sources', 'Sources'],
  ['artifacts', 'Artifacts'],
  ['collections', 'Collections'],
  ['audit', 'Audit'],
  ['telemetry', 'Usage & Cost'],
  ['flags', 'Feature Rollouts'],
  ['courses', 'Courses'],
  ['grants', 'Support grants'],
  ['refresh', 'Catalog refresh'],
  ['triage', 'Feedback triage'],
  ['experiments', 'Experiments'],
  ['prompts', 'Prompt promotion'],
];

/** The read-only tabs an auditor may open. */
const AUDITOR_TABS: Tab[] = ['audit', 'telemetry'];

function GateNotice({
  session,
  onLogout,
  children,
}: {
  session: AuthSession;
  onLogout?: () => void;
  children: ReactNode;
}) {
  return (
    <div className="shell">
      <TopBar session={session} onLogout={onLogout} />
      <main className="session-gate">
        <div className="gate-card">{children}</div>
      </main>
    </div>
  );
}

function AdminSurface({ session, onLogout }: { session: AuthSession; onLogout?: () => void }) {
  const [tab, setTab] = useState<Tab>(session.role === 'auditor' ? 'audit' : 'overview');
  // Bumped on every tab click so a listing tab, even the open one, starts fresh at its first page.
  const [visit, setVisit] = useState(0);
  // Listing tabs put their Refresh button at the end of the tab bar.
  const [toolbar, setToolbar] = useState<HTMLElement | null>(null);

  if (isDemoMode()) {
    return (
      <GateNotice session={session} onLogout={onLogout}>
        <h2>Admin needs the real API</h2>
        <p>Run the admin bundle with `VITE_API_MODE=http`.</p>
      </GateNotice>
    );
  }

  if (session.role !== 'admin' && session.role !== 'auditor') {
    return (
      <GateNotice session={session} onLogout={onLogout}>
        <h2>Administrator access required</h2>
        <p>This account can use the advisor but cannot manage institutional users.</p>
        <a className="primary-button" href="/">
          Return to the advisor
        </a>
      </GateNotice>
    );
  }

  const isAdmin = session.role === 'admin';
  const tabs = isAdmin ? TABS : TABS.filter(([value]) => AUDITOR_TABS.includes(value));

  const panel = (() => {
    switch (tab) {
      case 'overview':
        return <OverviewPanel key={visit} toolbar={toolbar} />;
      case 'users':
        return <UsersPanel key={visit} toolbar={toolbar} />;
      case 'chats':
        return <ChatsPanel key={visit} toolbar={toolbar} />;
      case 'jobs':
        return <JobsPanel key={visit} toolbar={toolbar} />;
      case 'sources':
        return <SourcesPanel key={visit} toolbar={toolbar} canReview={isAdmin} />;
      case 'artifacts':
        return <ArtifactsPanel key={visit} toolbar={toolbar} />;
      case 'collections':
        return <CollectionsPanel key={visit} toolbar={toolbar} />;
      case 'audit':
        return <AuditPanel key={visit} toolbar={toolbar} />;
      case 'telemetry':
        return <TelemetryPanel key={visit} toolbar={toolbar} />;
      case 'flags':
        return <FeatureFlagsPanel />;
      case 'courses':
        return isAdmin && <CourseMaintenancePanel />;
      case 'grants':
        return isAdmin && <GrantsPanel />;
      case 'refresh':
        return isAdmin && <CatalogRefreshPanel />;
      case 'triage':
        return isAdmin && <FeedbackTriagePanel />;
      case 'experiments':
        return isAdmin && <ExperimentPanel />;
      case 'prompts':
        return isAdmin && <PromptPromotionPanel />;
    }
  })();

  return (
    <div className="shell admin-shell">
      <TopBar session={session} onLogout={onLogout} />
      <div className="surface-nav">
        <strong>Institutional Admin</strong>
        <a href="/">Main</a>
        {isAdmin && <a href="/debug.html">Debug</a>}
      </div>
      <main className="admin-main">
        <div className="admin-tabs" role="tablist" ref={setToolbar}>
          {tabs.map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={tab === value}
              className={tab === value ? 'admin-tab active' : 'admin-tab'}
              onClick={() => {
                setTab(value);
                setVisit((count) => count + 1);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {panel}
      </main>
    </div>
  );
}

export function AdminApp() {
  return <SessionGate render={(session, logout) => <AdminSurface session={session} onLogout={logout} />} />;
}
