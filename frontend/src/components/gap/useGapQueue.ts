import { useEffect, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type { GapMetrics, RetrievalGap, SourceDoc } from '../../api/types';
import { errorMessage } from '../../lib/errors';
import { useMountedRef } from '../../lib/useMountedRef';

/**
 * The retrieval-gap queue plus the sources of each gap's collection (what a reviewer may attest).
 * `refresh` rejects on failure, after recording the error; overlapping refreshes keep only the latest.
 */
export function useGapQueue() {
  const [gaps, setGaps] = useState<RetrievalGap[]>([]);
  const [collectionSources, setCollectionSources] = useState<Record<string, SourceDoc[]>>({});
  const [collectionErrors, setCollectionErrors] = useState<Record<string, string>>({});
  const [metrics, setMetrics] = useState<GapMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const mounted = useMountedRef();
  const readEpoch = useRef(0);

  const refresh = async () => {
    const epoch = ++readEpoch.current;
    const current = () => mounted.current && epoch === readEpoch.current;
    setLoading(true);
    setLoadError(null);
    try {
      const client = getClient();
      const [result, collections] = await Promise.all([client.listGaps(), client.listCollections()]);
      const collectionIds = [...new Set(result.gaps.map((gap) => gap.collectionId))];
      const collectionNames = new Map(collections.map((collection) => [collection.id, collection.name]));
      const sourceResults = await Promise.allSettled(
        collectionIds.map(async (id) => {
          const name = collectionNames.get(id);
          if (!name) throw new Error('This gap belongs to a collection that is not visible in the current workspace.');
          return [id, await client.listSources(name)] as const;
        }),
      );
      if (!current()) return result;
      setGaps(result.gaps);
      setMetrics(result.metrics);
      const byCollection: Record<string, SourceDoc[]> = {};
      const errorsByCollection: Record<string, string> = {};
      sourceResults.forEach((item, index) => {
        if (item.status === 'fulfilled') byCollection[item.value[0]] = item.value[1];
        else errorsByCollection[collectionIds[index]] = errorMessage(item.reason);
      });
      setCollectionSources(byCollection);
      setCollectionErrors(errorsByCollection);
      return result;
    } catch (error) {
      if (current()) setLoadError(errorMessage(error));
      throw error;
    } finally {
      if (current()) setLoading(false);
    }
  };

  useEffect(() => {
    void refresh().catch(() => {});
    return () => {
      readEpoch.current += 1;
    };
  }, []);

  return { gaps, setGaps, collectionSources, collectionErrors, metrics, loading, loadError, refresh };
}
