import { useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type { SourceDoc } from '../../api/types';
import { errorMessage } from '../../lib/errors';
import { useMountedRef } from '../../lib/useMountedRef';
import { statusLabel } from './statusLabel';

/** Per-source reindex state: which are running, and the last note or error for each. */
export function useReindex(refreshSources: () => Promise<SourceDoc[]>) {
  const [reindexing, setReindexing] = useState<Set<string>>(() => new Set());
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  // A ref, not state, so a double click cannot start a second request before a render.
  const active = useRef(new Set<string>());
  const mounted = useMountedRef();

  const reindex = async (id: string, expectedIndexRevision?: number) => {
    if (expectedIndexRevision === undefined || active.current.has(id)) return;
    active.current.add(id);
    setReindexing((current) => new Set(current).add(id));
    setNotes((current) => ({ ...current, [id]: '' }));
    setErrors((current) => ({ ...current, [id]: '' }));
    try {
      const result = await getClient().reindexSource(id, expectedIndexRevision);
      const refreshed = await refreshSources();
      if (!mounted.current) return;
      const latest = refreshed.find((item) => item.id === id) ?? result;
      const revision = latest.indexRevision ?? result.indexRevision;
      const note =
        latest.status === 'indexed' && revision !== undefined
          ? `Reindex complete. Current index revision: ${revision}.`
          : `Reindex request complete. Current source status: ${statusLabel(latest.status)}.`;
      setNotes((current) => ({ ...current, [id]: note }));
    } catch (error) {
      let latest: SourceDoc | undefined;
      try {
        latest = (await refreshSources()).find((item) => item.id === id);
      } catch {
        /* retain the reindex error */
      }
      if (!mounted.current) return;
      const refreshedStatus = latest
        ? ` Refreshed status: ${statusLabel(latest.status)}${latest.indexRevision === undefined ? '' : `, index revision ${latest.indexRevision}`}.`
        : '';
      const recovery =
        latest?.status === 'indexed'
          ? ' The previously ready index remains available.'
          : ' Review the refreshed source status and diagnostics before retrying.';
      setErrors((current) => ({ ...current, [id]: `${errorMessage(error)}.${refreshedStatus}${recovery}` }));
    } finally {
      active.current.delete(id);
      if (mounted.current)
        setReindexing((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
    }
  };

  return { reindexing, notes, errors, reindex };
}
