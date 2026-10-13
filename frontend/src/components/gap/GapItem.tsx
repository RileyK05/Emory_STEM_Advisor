import type { RetrievalGap, SourceDoc } from '../../api/types';
import { MAX_ATTESTED_SOURCES } from './useGapResolution';
import type { GapDraft } from './useGapResolution';

function sourceScopeLabel(sourceIds: string[] | null): string {
  if (sourceIds === null) return 'All sources in the recorded collection';
  if (sourceIds.length === 0) return 'No sources selected';
  return `${sourceIds.length} selected source${sourceIds.length === 1 ? '' : 's'}: ${sourceIds.join(', ')}`;
}

interface Props {
  gap: RetrievalGap;
  draft: GapDraft;
  /** Sources of the gap's collection, if they loaded. */
  sources: SourceDoc[];
  sourcesError: string | undefined;
  saving: boolean;
  error: string | undefined;
  notice: string | undefined;
  onToggleSource: (sourceId: string) => void;
  onNoteChange: (note: string) => void;
  onResolve: () => void;
}

export function GapItem({
  gap,
  draft,
  sources,
  sourcesError,
  saving,
  error,
  notice,
  onToggleSource,
  onNoteChange,
  onResolve,
}: Props) {
  const allowedIds = gap.sourceIds === null ? null : new Set(gap.sourceIds);
  const eligibleSources = sources.filter(
    (source) => source.status === 'indexed' && (allowedIds === null || allowedIds.has(source.id)),
  );
  const canResolve =
    !saving &&
    draft.sourceIds.length >= 1 &&
    draft.sourceIds.length <= MAX_ATTESTED_SOURCES &&
    draft.sourceIds.every((id) => eligibleSources.some((source) => source.id === id));

  return (
    <li className="gap-review-item">
      <details>
        <summary>
          <span className={'gap-state gap-state-' + gap.state}>{gap.state === 'open' ? 'Open' : 'Resolved'}</span>{' '}
          {gap.query}
        </summary>
        <div className="gap-review-details">
          <p>
            <strong>Original query:</strong> {gap.query}
          </p>
          <p>
            <strong>Collection:</strong> {gap.collectionId}
          </p>
          <p>
            <strong>Recorded source scope:</strong> {sourceScopeLabel(gap.sourceIds)}
          </p>
          <p className="gap-review-counts">
            Observed {gap.occurrenceCount} time{gap.occurrenceCount === 1 ? '' : 's'}; recurrence {gap.recurrenceCount};
            structurally successful recurrences {gap.successfulRecurrenceCount}.
          </p>
          {gap.state === 'resolved' && gap.resolution ? (
            <div className="gap-resolution-record">
              <p>
                <strong>Human source attestation</strong>
              </p>
              <p>Sources: {gap.resolution.sourceIds.join(', ')}</p>
              {gap.resolution.note && <p>Reviewer note: {gap.resolution.note}</p>}
              <p>Recorded {new Date(gap.resolution.resolvedAt).toLocaleString()}.</p>
            </div>
          ) : (
            <>
              <fieldset disabled={saving}>
                <legend>Choose existing indexed sources from this collection</legend>
                {sourcesError && (
                  <p className="source-error" role="alert">
                    Could not load sources for this gap's collection. {sourcesError}
                  </p>
                )}
                {eligibleSources.length ? (
                  eligibleSources.map((source) => (
                    <label key={source.id} className="gap-source-option">
                      <input
                        type="checkbox"
                        checked={draft.sourceIds.includes(source.id)}
                        onChange={() => onToggleSource(source.id)}
                      />
                      <span>{source.name}</span>
                    </label>
                  ))
                ) : (
                  <p className="source-empty">No eligible indexed sources are available for this recorded scope.</p>
                )}
              </fieldset>
              <label className="gap-review-note-label" htmlFor={'gap-note-' + gap.gapId}>
                Optional reviewer note
              </label>
              <textarea
                id={'gap-note-' + gap.gapId}
                maxLength={1000}
                value={draft.note}
                onChange={(event) => onNoteChange(event.target.value)}
                disabled={saving}
                rows={2}
              />
              <p className="gap-review-help">
                A resolution records a human claim that these sources are relevant to the gap. It does not promote
                sources, create an answer, or edit retrieval graphs.
              </p>
              {error && (
                <p className="source-error" role="alert">
                  {error}
                </p>
              )}
              {notice && <p role="status">{notice}</p>}
              <button type="button" className="gap-resolve-button" disabled={!canResolve} onClick={onResolve}>
                {saving ? 'Saving attestation…' : 'Record human source attestation'}
              </button>
            </>
          )}
        </div>
      </details>
    </li>
  );
}
