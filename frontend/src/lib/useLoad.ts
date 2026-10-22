import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react';
import { errorMessage } from './errors';

interface LoadOptions<T> {
  /** Applies a result; never called for a superseded request or after unmount. */
  onLoad: (value: T) => void;
  /** Error state the load clears on start and sets on failure (often shared with useAsyncAction). */
  setError: (message: string | null) => void;
  /** While false, loads are skipped entirely (no loading state, no error reset). Defaults to true. */
  enabled?: boolean;
}

/** Loads on mount and whenever `deps` change. Each call supersedes the previous one,
 *  so a slow earlier response can never overwrite a newer one. `reload` re-runs it. */
export function useLoad<T>(load: () => Promise<T>, deps: DependencyList, options: LoadOptions<T>) {
  const [loading, setLoading] = useState(options.enabled !== false);
  const seq = useRef(0);
  const latest = useRef({ load, ...options });
  latest.current = { load, ...options };

  const reload = useCallback(async () => {
    const current = latest.current;
    if (current.enabled === false) return;
    const id = ++seq.current;
    setLoading(true);
    current.setError(null);
    try {
      const value = await current.load();
      if (id === seq.current) latest.current.onLoad(value);
    } catch (cause) {
      if (id === seq.current) latest.current.setError(errorMessage(cause));
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    return () => {
      seq.current += 1;
    };
  }, [reload, options.enabled, ...deps]);

  return { loading, reload };
}
