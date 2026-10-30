import { GapItem } from './GapItem';
import { useGapQueue } from './useGapQueue';
import { useGapResolution } from './useGapResolution';

export function GapReviewPanel() {
  const queue = useGapQueue();
  const review = useGapResolution(queue.setGaps, queue.refresh);
  const { gaps, metrics, loading, loadError } = queue;
  const openCount = gaps.filter((gap) => gap.state === 'open').length;

  return (
    <section className="gap-review-panel" aria-label="Retrieval gap review">
      <div className="gap-review-heading">
        <div>
          <h3>Retrieval gaps</h3>
          <p>
            Human source attestation helps diagnose retrieval. It does not prove that a generated answer is factually
            correct or that the sources answer the query.
          </p>
        </div>
        <button
          type="button"
          className="icon-button"
          aria-label="Refresh retrieval gaps"
          onClick={() => void queue.refresh().catch(() => {})}
          disabled={loading}
        >
          ↻
        </button>
      </div>
      {metrics && (
        <p className="gap-review-metrics" role="note">
          {metrics.recurrenceRate === null
            ? 'No repeated observations yet.'
            : `Structural retrieval recurrence: ${Math.round(metrics.recurrenceRate * 100)}% (${metrics.successfulRepeatedObservations} successful repeated observations of ${metrics.repeatedGapObservations}).`}{' '}
          This measures retrieval recovery, not factual quality.
        </p>
      )}
      {loadError && (
        <p className="source-error" role="alert">
          Could not load retrieval gaps. {loadError}
        </p>
      )}
      {loading && <p role="status">Loading retrieval gaps…</p>}
      {!loading && !loadError && gaps.length === 0 && <p className="source-empty">No recorded retrieval gaps.</p>}
      {openCount > 0 && (
        <p className="gap-review-open-count" role="status">
          {openCount} open gap{openCount === 1 ? '' : 's'} awaiting review.
        </p>
      )}
      <ul className="gap-review-list">
        {gaps.map((gap) => (
          <GapItem
            key={gap.gapId}
            gap={gap}
            draft={review.draftFor(gap.gapId)}
            sources={queue.collectionSources[gap.collectionId] ?? []}
            sourcesError={queue.collectionErrors[gap.collectionId]}
            saving={review.resolving.has(gap.gapId)}
            error={review.errors[gap.gapId]}
            notice={review.notices[gap.gapId]}
            onToggleSource={(sourceId) => review.toggleSource(gap.gapId, sourceId)}
            onNoteChange={(note) => review.updateNote(gap.gapId, note)}
            onResolve={() => void review.resolve(gap)}
          />
        ))}
      </ul>
    </section>
  );
}
