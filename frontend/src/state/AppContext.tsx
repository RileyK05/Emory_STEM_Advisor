import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import type { AuthSession, SourceDoc } from '../api/types';
import { getClient } from '../api/client';
import { errorMessage } from '../lib/errors';
import { useMountedRef } from '../lib/useMountedRef';

/**
 * Which sources ground the next question or draft. `null` searches every source;
 * an array (including an explicitly empty one) limits the search to those ids.
 */
export type SourceScope = string[] | null;

interface AppState {
  session: AuthSession;
  /** Show retrieval traces under answers (developer detail). */
  showDetails: boolean;
  toggleShowDetails: () => void;
  sources: SourceDoc[];
  sourcesError: string | null;
  refreshSources: () => Promise<SourceDoc[]>;
  uploadSource: (file: File) => Promise<SourceDoc>;
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
  const [showDetails, setShowDetails] = useState(false);
  const [sources, setSources] = useState<SourceDoc[]>([]);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [scope, setScope] = useState<SourceScope>(null);
  const mounted = useMountedRef();

  const refreshSources = useCallback(async () => {
    setSourcesError(null);
    try {
      const list = await getClient().listSources();
      if (mounted.current) {
        setSources(list);
        setScope((previous) => previous && previous.filter((id) => list.some((source) => source.id === id)));
      }
      return list;
    } catch (error) {
      if (mounted.current) setSourcesError(errorMessage(error));
      throw error;
    }
  }, []);

  const uploadSource = useCallback(
    async (file: File) => {
      const source = await getClient().uploadSource(file);
      const list = await refreshSources();
      if (mounted.current) {
        setSources((current) => (current.some((item) => item.id === source.id) ? current : [...list, source]));
        // A narrowed scope includes what the user just added; "all sources" already does.
        setScope((current) => current && [...new Set([...current, source.id])]);
      }
      return source;
    },
    [refreshSources],
  );

  useEffect(() => {
    void refreshSources().catch(() => {
      /* The library shows the error and offers a refresh. */
    });
  }, [refreshSources]);

  const toggleShowDetails = useCallback(() => setShowDetails((v) => !v), []);

  const value = useMemo<AppState>(
    () => ({
      session,
      showDetails,
      toggleShowDetails,
      sources,
      sourcesError,
      refreshSources,
      uploadSource,
      scope,
      setScope,
    }),
    [session, showDetails, toggleShowDetails, sources, sourcesError, refreshSources, uploadSource, scope],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
