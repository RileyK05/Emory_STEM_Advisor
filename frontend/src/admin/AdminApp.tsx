import { useState } from 'react';
import type { ReactNode } from 'react';
import type { AuthSession } from '../api/types';
import { isDemoMode } from '../api/client';
import { TopBar } from '../components/shell/TopBar';
import { SessionGate } from '../app/SessionGate';
import { AuditPanel } from './AuditPanel';
import { ConnectionSettings } from './connection/ConnectionSettings';
import { CourseMaintenancePanel } from './CourseMaintenancePanel';
import { FeedbackTriagePanel } from './FeedbackTriagePanel';
import { GapReviewPanel } from './gap/GapReviewPanel';
import { JobsPanel } from './JobsPanel';
import { OverviewPanel } from './OverviewPanel';
import { ProgramRulesPanel } from './ProgramRulesPanel';
import { SourcesPanel } from './SourcesPanel';
import { TelemetryPanel } from './TelemetryPanel';
import { UsersPanel } from './UsersPanel';

type Tab = 'overview' | 'users' | 'sources' | 'courses' | 'questions' | 'usage' | 'connections';

const TABS: Array<[Tab, string]> = [
  ['overview', 'Overview'],
  ['users', 'Users'],
  ['sources', 'Sources'],
  ['courses', 'Courses & programs'],
  ['questions', 'Student questions'],
  ['usage', 'Usage & cost'],
  ['connections', 'Model connections'],
];

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
  const [tab, setTab] = useState<Tab>('overview');
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

  if (session.role !== 'admin') {
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

  const panel = (() => {
    switch (tab) {
      case 'overview':
        return (
          <>
            <OverviewPanel key={`overview-${visit}`} toolbar={toolbar} />
            <JobsPanel key={`jobs-${visit}`} toolbar={toolbar} />
          </>
        );
      case 'users':
        return (
          <>
            <UsersPanel key={`users-${visit}`} toolbar={toolbar} />
            <AuditPanel key={`audit-${visit}`} toolbar={toolbar} />
          </>
        );
      case 'sources':
        return <SourcesPanel key={visit} toolbar={toolbar} canReview />;
      case 'courses':
        return (
          <>
            <CourseMaintenancePanel />
            <ProgramRulesPanel />
          </>
        );
      case 'questions':
        return (
          <>
            <FeedbackTriagePanel />
            <GapReviewPanel />
          </>
        );
      case 'usage':
        return <TelemetryPanel key={visit} toolbar={toolbar} />;
      case 'connections':
        return <ConnectionSettings />;
    }
  })();

  return (
    <div className="shell admin-shell">
      <TopBar session={session} onLogout={onLogout} />
      <div className="surface-nav">
        <strong>Institutional Admin</strong>
        <a href="/">Main</a>
      </div>
      <main className="admin-main">
        <div className="admin-tabs" role="tablist" ref={setToolbar}>
          {TABS.map(([value, label]) => (
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
