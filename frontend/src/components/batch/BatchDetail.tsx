import type { ArtifactBatchRecord } from '../../api/types';
import { TERMINAL_STATES, publicationLabel, stateLabel } from './labels';

export type BatchAction = 'refresh' | 'poll' | 'cancel';

interface Props {
  batchId: string;
  /** Null while the batch record is loading. */
  batch: ArtifactBatchRecord | null;
  busy: boolean;
  onAction: (action: BatchAction) => void;
  onOpenRevision: (artifactId: string, revisionId: string) => void;
}

export function BatchDetail({ batchId, batch, busy, onAction, onOpenRevision }: Props) {
  const active = batch !== null && !TERMINAL_STATES.has(batch.state);
  const canCancel = active && !batch.cancelRequested;
  return (
    <div className="artifact-batch-detail">
      <p>
        Selected batch · {stateLabel(batch?.state ?? 'pending')} · {batch?.completedCount ?? 0}/
        {batch?.totalCount ?? batch?.itemCount ?? 0} complete · {batch?.failedCount ?? 0} failed
      </p>
      <div className="artifact-actions">
        <button type="button" onClick={() => onAction('refresh')} disabled={busy || !batch}>
          Inspect status
        </button>
        <button type="button" onClick={() => onAction('poll')} disabled={busy || !active}>
          Poll provider status
        </button>
        <button type="button" onClick={() => onAction('cancel')} disabled={busy || !canCancel}>
          Cancel batch
        </button>
      </div>
      {batch ? (
        <ol>
          {batch.items.map((item) => (
            <li key={item.itemId}>
              <span>
                Item {item.itemId.slice(0, 8)} · {stateLabel(item.state)} · {publicationLabel(item.publicationState)}
                {item.errorCode ? ` · ${item.errorCode}` : ''}
              </span>
              {item.publicationState === 'published' && item.artifactId && item.revisionId && (
                <button
                  type="button"
                  onClick={() => onOpenRevision(item.artifactId!, item.revisionId!)}
                  disabled={busy}
                >
                  Open published revision
                </button>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <p role="status">Loading batch {batchId.slice(0, 8)}…</p>
      )}
    </div>
  );
}
