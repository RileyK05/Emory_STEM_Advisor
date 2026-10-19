import { AppProvider } from './state/AppContext';
import type { AuthSession } from './api/types';
import { Shell } from './components/shell/Shell';
import { SessionGate } from './app/SessionGate';

function MainSurface({ session, onLogout }: { session: AuthSession; onLogout?: () => void }) {
  return (
    <AppProvider key={`${session.mode}:${session.workspaceKey ?? 'development'}`} session={session}>
      <Shell onLogout={onLogout} />
    </AppProvider>
  );
}

export default function App() {
  return <SessionGate render={(session, logout) => <MainSurface session={session} onLogout={logout} />} />;
}
