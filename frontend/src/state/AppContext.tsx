import { createContext, useContext, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { AuthSession } from '../api/types';

/**
 * Which sources ground the next question or draft. `null` searches every source;
 * an array (including an explicitly empty one) limits the search to those ids.
 */
export type SourceScope = string[] | null;

interface AppState {
  session: AuthSession;
  scope: SourceScope;
  setScope: (scope: SourceScope) => void;
}

const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}

export function AppProvider({ session, children }: { session: AuthSession; children: ReactNode }) {
  const [scope, setScope] = useState<SourceScope>(null);
  const value = useMemo<AppState>(() => ({ session, scope, setScope }), [session, scope]);

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
