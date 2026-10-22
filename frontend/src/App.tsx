import { AppProvider } from './state/AppContext';
import type { AuthSession } from './api/types';
import { Shell } from './components/shell/Shell';
import { SessionGate } from './app/SessionGate';
import { TopBar } from './components/shell/TopBar';

function MainSurface({ session, onLogout }: { session: AuthSession; onLogout?: () => void }) {
  if (session.role === 'auditor') {
    return (
      <div className="shell">
        <TopBar session={session} onLogout={onLogout} />
        <main className="session-gate">
          <div className="gate-card">
            <h2>Auditor workspace</h2>
            <p>Your account can inspect audit records and usage telemetry.</p>
            <a className="primary-button" href="/admin.html">
              Open audit and telemetry
            </a>
          </div>
        </main>
      </div>
    );
  }
  return (
    <AppProvider key={`${session.mode}:${session.workspaceKey ?? 'development'}`} session={session}>
      <Shell onLogout={onLogout} />
    </AppProvider>
  );
}

export default function App() {
  return <SessionGate render={(session, logout) => <MainSurface session={session} onLogout={logout} />} />;
}
