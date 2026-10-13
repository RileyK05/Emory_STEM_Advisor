import { useCallback, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { AppProvider } from './state/AppContext';
import { getClient } from './api/client';
import { errorMessage } from './lib/errors';
import type { AuthSession } from './api/types';
import { Shell } from './components/shell/Shell';
import { TopBar } from './components/shell/TopBar';

function Gate({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="shell">
      <TopBar />
      <main className="session-gate">
        <div className="gate-card">
          <h2>{title}</h2>
          {children}
        </div>
      </main>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState('Checking your session…');

  const refreshSession = useCallback(async () => {
    setSessionLoading(true);
    setSessionError(null);
    setStatusMessage('Checking your session…');
    try {
      setSession(await getClient().getSession());
    } catch (cause) {
      setSession(null);
      setSessionError(errorMessage(cause));
    } finally {
      setSessionLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);
  useEffect(() => {
    const handleExpiredSession = () => {
      setSession(null);
      setSessionLoading(true);
      setSessionError(null);
      setStatusMessage('Your session expired. Clearing this workspace…');
      void refreshSession();
    };
    window.addEventListener('advisor-session-invalid', handleExpiredSession);
    return () => window.removeEventListener('advisor-session-invalid', handleExpiredSession);
  }, [refreshSession]);

  const logout = async () => {
    // Unmount the workspace first so its source, artifact, and job state is discarded.
    setSession(null);
    setSessionError(null);
    setSessionLoading(true);
    setStatusMessage('Signing out and clearing this workspace…');
    try {
      await getClient().logout();
    } catch (cause) {
      setSessionError(errorMessage(cause, 'Sign-out request failed.'));
    }
    await refreshSession();
  };

  if (sessionLoading)
    return (
      <Gate title="One moment">
        <p role="status">{statusMessage}</p>
      </Gate>
    );
  if (sessionError && !session)
    return (
      <Gate title="Could not check your session">
        <p role="alert">{sessionError}</p>
        <button type="button" className="primary-button" onClick={() => void refreshSession()}>
          Retry session check
        </button>
      </Gate>
    );
  if (!session)
    return (
      <Gate title="Session unavailable">
        <button type="button" className="primary-button" onClick={() => void refreshSession()}>
          Retry
        </button>
      </Gate>
    );
  if (session.mode === 'oidc' && !session.authenticated)
    return (
      <Gate title="Sign in required">
        <p>Sign in with your Emory account to see your chats, sources, and documents.</p>
        {sessionError && <p role="alert">{sessionError}</p>}
        <div className="gate-actions">
          <button type="button" className="primary-button" onClick={() => getClient().beginLogin()}>
            Sign in
          </button>
          <button type="button" className="secondary-button" onClick={() => void refreshSession()}>
            Check session again
          </button>
        </div>
      </Gate>
    );

  return (
    <AppProvider key={`${session.mode}:${session.workspaceKey ?? 'development'}`} session={session}>
      <Shell onLogout={session.mode === 'oidc' ? () => void logout() : undefined} />
    </AppProvider>
  );
}
