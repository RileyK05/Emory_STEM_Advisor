import { useEffect, useRef, useState } from 'react';
import type { Dispatch, SetStateAction } from 'react';
import { getClient } from '../../api/client';
import type { RetrievalGap } from '../../api/types';
import { errorMessage, statusOf } from '../../lib/errors';
import { useMountedRef } from '../../lib/useMountedRef';

export interface GapDraft {
  sourceIds: string[];
  note: string;
}

export const MAX_ATTESTED_SOURCES = 20;

const EMPTY_DRAFT: GapDraft = { sourceIds: [], note: '' };

/** Per-gap reviewer drafts and the explicit "record human source attestation" action. */
export function useGapResolution(setGaps: Dispatch<SetStateAction<RetrievalGap[]>>, refresh: () => Promise<unknown>) {
  const [drafts, setDrafts] = useState<Record<string, GapDraft>>({});
  const [resolving, setResolving] = useState<Set<string>>(() => new Set());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notices, setNotices] = useState<Record<string, string>>({});
  const mounted = useMountedRef();
  // A ref, not state, so a double click cannot start a second request before a render.
  const active = useRef(new Set<string>());

  useEffect(
    () => () => {
      active.current.clear();
    },
    [],
  );

  const draftFor = (gapId: string): GapDraft => drafts[gapId] ?? EMPTY_DRAFT;

  const toggleSource = (gapId: string, sourceId: string) =>
    setDrafts((current) => {
      const draft = current[gapId] ?? EMPTY_DRAFT;
      const sourceIds = draft.sourceIds.includes(sourceId)
        ? draft.sourceIds.filter((id) => id !== sourceId)
        : [...draft.sourceIds, sourceId];
      return { ...current, [gapId]: { ...draft, sourceIds } };
    });

  const updateNote = (gapId: string, note: string) =>
    setDrafts((current) => ({ ...current, [gapId]: { ...(current[gapId] ?? EMPTY_DRAFT), note } }));

  const setFor = (gapId: string, setter: Dispatch<SetStateAction<Record<string, string>>>, text: string) =>
    setter((current) => ({ ...current, [gapId]: text }));

  const resolve = async (gap: RetrievalGap) => {
    const { gapId } = gap;
    const draft = draftFor(gapId);
    if (active.current.has(gapId) || draft.sourceIds.length < 1 || draft.sourceIds.length > MAX_ATTESTED_SOURCES) {
      return;
    }
    active.current.add(gapId);
    setResolving((current) => new Set(current).add(gapId));
    setFor(gapId, setErrors, '');
    setFor(gapId, setNotices, '');
    try {
      const resolved = await getClient().resolveGap(gapId, {
        expectedRevision: gap.revision,
        sourceIds: draft.sourceIds,
        note: draft.note,
      });
      if (!mounted.current) return;
      setGaps((current) => current.map((item) => (item.gapId === gapId ? resolved : item)));
      setFor(gapId, setNotices, 'Human source attestation saved.');
      try {
        await refresh();
      } catch {
        if (mounted.current) {
          setFor(gapId, setNotices, 'Human source attestation saved; refresh the queue to confirm the latest status.');
        }
      }
    } catch (error) {
      if (!mounted.current) return;
      if (statusOf(error) === 409) {
        try {
          await refresh();
        } catch {
          /* keep the conflict visible and preserve the draft */
        }
        if (mounted.current) {
          setFor(
            gapId,
            setErrors,
            'This gap changed. The queue was refreshed; your source selection and note are preserved. Review the latest query and submit again explicitly.',
          );
        }
      } else {
        setFor(gapId, setErrors, errorMessage(error));
      }
    } finally {
      active.current.delete(gapId);
      if (mounted.current)
        setResolving((current) => {
          const next = new Set(current);
          next.delete(gapId);
          return next;
        });
    }
  };

  return { draftFor, toggleSource, updateNote, resolve, resolving, errors, notices };
}
