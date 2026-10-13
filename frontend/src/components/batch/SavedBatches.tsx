import type { ArtifactBatchSummary } from '../../api/types';
import { stateLabel } from './labels';

interface Props {
  summaries: ArtifactBatchSummary[];
  activeBatchId: string | null;
  busy: boolean;
  listError: string | null;
  onRefresh: () => void;
  onInspect: (batchId: string) => void;
}

export function SavedBatches({ summaries, activeBatchId, busy, listError, onRefresh, onInspect }: Props) {
  return (
    <section className="artifact-batch-list" aria-label="Saved artifact batches">
      <div>
        <h4>Saved batches</h4>
        <button type="button" onClick={onRefresh} disabled={busy}>
          Refresh batches
        </button>
      </div>
      {summaries.map((item) => (
        <button
          type="button"
          key={item.batchId}
          aria-pressed={activeBatchId === item.batchId}
          onClick={() => onInspect(item.batchId)}
          disabled={busy}
        >
          Batch · {stateLabel(item.state)} · {item.completedCount ?? 0}/{item.totalCount ?? item.itemCount}
        </button>
      ))}
      {summaries.length === 0 && !listError && <p>No saved batches.</p>}
      {listError && <p role="alert">Batch list unavailable: {listError}</p>}
    </section>
  );
}
