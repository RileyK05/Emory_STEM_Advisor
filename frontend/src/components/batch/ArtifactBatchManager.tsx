import { useEffect, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import { errorMessage } from '../../lib/errors';
import type { ArtifactBatchRecord, ArtifactBatchSummary, NativeArtifactKind } from '../../api/types';
import { useApp } from '../../state/AppContext';
import { useMountedRef } from '../../lib/useMountedRef';
import { BatchDetail } from './BatchDetail';
import type { BatchAction } from './BatchDetail';
import { MAX_PROMPTS, NewBatchForm } from './NewBatchForm';
import type { BatchMode } from './NewBatchForm';
import { SavedBatches } from './SavedBatches';

export function ArtifactBatchManager({
  onOpenRevision,
}: {
  onOpenRevision: (artifactId: string, revisionId: string) => Promise<void>;
}) {
  const { scope } = useApp();
  const [summaries, setSummaries] = useState<ArtifactBatchSummary[]>([]);
  const [batch, setBatch] = useState<ArtifactBatchRecord | null>(null);
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);
  const [kind, setKind] = useState<NativeArtifactKind>('document');
  const [mode, setMode] = useState<BatchMode>('grounded');
  const [prompts, setPrompts] = useState(['']);
  const [batchId, setBatchId] = useState<string>(() => crypto.randomUUID());
  const [itemIds, setItemIds] = useState<string[]>(() => [crypto.randomUUID()]);
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const alive = useMountedRef();
  const selectionEpoch = useRef(0);
  /** False once the panel unmounted or the selection changed since `epoch`. */
  const isCurrent = (epoch: number) => alive.current && selectionEpoch.current === epoch;

  useEffect(() => {
    const epoch = ++selectionEpoch.current;
    void getClient()
      .listArtifactBatches()
      .then(async (items) => {
        if (!isCurrent(epoch)) return;
        setSummaries(items);
        const latest = items[0];
        if (!latest) return;
        setActiveBatchId(latest.batchId);
        const detail = await getClient().getArtifactBatch(latest.batchId);
        if (isCurrent(epoch)) setBatch(detail);
      })
      .catch((cause: unknown) => {
        if (isCurrent(epoch)) setListError(errorMessage(cause));
      });
    return () => {
      selectionEpoch.current += 1;
    };
  }, []);

  const refreshList = async () => {
    const epoch = selectionEpoch.current;
    setListError(null);
    try {
      const items = await getClient().listArtifactBatches();
      if (isCurrent(epoch)) setSummaries(items);
    } catch (cause) {
      if (isCurrent(epoch)) setListError(errorMessage(cause));
    }
  };

  const inspectBatch = async (id: string) => {
    const epoch = ++selectionEpoch.current;
    setActiveBatchId(id);
    setBatch(null);
    setError(null);
    try {
      const result = await getClient().getArtifactBatch(id);
      if (isCurrent(epoch)) setBatch(result);
    } catch (cause) {
      if (isCurrent(epoch)) setError(errorMessage(cause));
    }
  };

  /** The request changed, so it is a new batch intent: fresh batch and item IDs, nothing attempted yet. */
  const startNewIntent = (nextPrompts: string[]) => {
    setPrompts(nextPrompts);
    setItemIds(nextPrompts.map(() => crypto.randomUUID()));
    setBatchId(crypto.randomUUID());
    setAttempted(false);
  };
  const reviseDraft = (nextPrompts: string[]) => {
    startNewIntent(nextPrompts);
    setNotice(null);
  };

  const submitBatch = async () => {
    const normalized = prompts.map((prompt) => prompt.trim());
    if (!normalized.length || normalized.length > MAX_PROMPTS || normalized.some((prompt) => !prompt)) {
      setError('Enter between 1 and 50 non-empty prompt entries.');
      return;
    }
    if (attempted || busy) return;
    const id = batchId;
    const ids = itemIds;
    const request = {
      batchId: id,
      kind,
      items: normalized.map((prompt, index) => ({
        itemId: ids[index],
        inputs: {
          prompt,
          mode,
          sourceIds: mode === 'authoring' ? [] : scope ? [...scope] : null,
        },
      })),
    } as const;
    const epoch = selectionEpoch.current;
    setAttempted(true);
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await getClient().createArtifactBatch(request);
      if (!isCurrent(epoch)) return;
      setBatch(result);
      setActiveBatchId(result.batchId);
      setNotice('Batch intent was saved. Status changes only when you explicitly refresh or poll.');
      await refreshList();
    } catch (cause) {
      if (!isCurrent(epoch)) return;
      setError(errorMessage(cause));
      setNotice(
        'Submission outcome may be unknown. The exact request and IDs are retained; it will not be resubmitted. Refresh the batch list or inspect this batch ID.',
      );
      // Inspect durable intent using read-only GETs. Never retry the submit.
      try {
        const items = await getClient().listArtifactBatches();
        if (!isCurrent(epoch)) return;
        setSummaries(items);
        if (items.some((item) => item.batchId === id)) {
          const detail = await getClient().getArtifactBatch(id);
          if (isCurrent(epoch)) {
            setBatch(detail);
            setActiveBatchId(id);
          }
        }
      } catch {
        /* Keep original failure and retained request visible. */
      }
    } finally {
      if (isCurrent(epoch)) setBusy(false);
    }
  };

  const explicitAction = async (action: BatchAction) => {
    if (!activeBatchId || busy) return;
    const id = activeBatchId;
    const epoch = selectionEpoch.current;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result =
        action === 'poll'
          ? await getClient().pollArtifactBatch(id)
          : action === 'cancel'
            ? await getClient().cancelArtifactBatch(id)
            : await getClient().getArtifactBatch(id);
      if (!isCurrent(epoch)) return;
      setBatch(result);
      if (action === 'cancel')
        setNotice(
          'Cancellation was requested. Remote provider usage may still incur charges; already published revisions are retained.',
        );
      else if (action === 'poll') setNotice('Provider status was explicitly refreshed.');
      await refreshList();
    } catch (cause) {
      if (isCurrent(epoch)) setError(errorMessage(cause));
    } finally {
      if (isCurrent(epoch)) setBusy(false);
    }
  };

  return (
    <section className="artifact-batches" aria-label="Native artifact batches">
      <p className="muted">
        Submit 1–50 independent drafts in one provider batch. Submitting, checking status, and cancelling are separate,
        explicit actions — nothing is resubmitted automatically.
      </p>
      <NewBatchForm
        busy={busy}
        attempted={attempted}
        kind={kind}
        onKindChange={(next) => {
          setKind(next);
          startNewIntent(prompts);
        }}
        mode={mode}
        onModeChange={(next) => {
          setMode(next);
          startNewIntent(prompts);
        }}
        sourceScope={{ enabled: scope !== null, selectedCount: scope?.length ?? 0 }}
        prompts={prompts}
        onPromptsChange={reviseDraft}
        onSubmit={() => void submitBatch()}
      />
      <SavedBatches
        summaries={summaries}
        activeBatchId={activeBatchId}
        busy={busy}
        listError={listError}
        onRefresh={() => void refreshList()}
        onInspect={(id) => void inspectBatch(id)}
      />
      {activeBatchId && (
        <BatchDetail
          batchId={activeBatchId}
          batch={batch}
          busy={busy}
          onAction={(action) => void explicitAction(action)}
          onOpenRevision={(artifactId, revisionId) => void onOpenRevision(artifactId, revisionId)}
        />
      )}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
