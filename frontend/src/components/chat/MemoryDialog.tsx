import { useEffect, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type { ConversationMemoryInspection, ConversationSummaryCheckpoint } from '../../api/types';
import { AlertIcon, CheckIcon } from '../icons';
import { Dialog } from '../shell/Dialog';

interface Props {
  open: boolean;
  onClose: () => void;
  conversationId: string | null;
  onResetSuccess?: () => void;
}

export function MemoryDialog({ open, onClose, conversationId, onResetSuccess }: Props) {
  const generation = useRef(0);
  const [data, setData] = useState<ConversationMemoryInspection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmingReset, setConfirmingReset] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [resetNotice, setResetNotice] = useState<string | null>(null);

  const loadMemory = async (cid: string, expected = generation.current) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getClient().inspectConversationMemory(cid);
      if (generation.current === expected) setData(resp);
    } catch (err) {
      if (generation.current === expected)
        setError(err instanceof Error ? err.message : 'Failed to load conversation memory.');
    } finally {
      if (generation.current === expected) setLoading(false);
    }
  };

  useEffect(() => {
    ++generation.current;
    setData(null);
    setResetting(false);
    setLoading(false);
    if (open && conversationId) {
      setConfirmingReset(false);
      setResetNotice(null);
      void loadMemory(conversationId);
    } else {
      setData(null);
      setError(null);
      setConfirmingReset(false);
      setResetNotice(null);
    }
    return () => {
      ++generation.current;
    };
  }, [open, conversationId]);

  const handleReset = async () => {
    if (!conversationId) return;
    const expected = generation.current;
    setResetting(true);
    setError(null);
    try {
      const resp = await getClient().resetConversationMemory(conversationId);
      if (generation.current !== expected) return;
      setResetNotice(resp.message || 'Conversation memory boundary has been reset.');
      setConfirmingReset(false);
      await loadMemory(conversationId, expected);
      if (generation.current === expected) onResetSuccess?.();
    } catch (err) {
      if (generation.current === expected)
        setError(err instanceof Error ? err.message : 'Failed to reset conversation memory.');
    } finally {
      if (generation.current === expected) setResetting(false);
    }
  };

  const checkpointBadgeClass = (state: ConversationSummaryCheckpoint['state']) => {
    switch (state) {
      case 'completed':
        return 'badge-status badge-success';
      case 'pending':
        return 'badge-status badge-warning';
      case 'failed':
      case 'refused':
        return 'badge-status badge-danger';
      case 'stale':
      default:
        return 'badge-status badge-muted';
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Conversation Memory & Context"
      description="Inspect active background checkpoints, context boundary sequence, and research state."
      size="wide"
    >
      <div className="stack" style={{ gap: '20px' }}>
        {loading && !data && (
          <p className="muted" role="status">
            Loading conversation memory…
          </p>
        )}

        {error && (
          <div className="banner banner-error" role="alert">
            <div>
              <p>{error}</p>
            </div>
          </div>
        )}

        {resetNotice && (
          <div
            className="banner"
            style={{ background: 'var(--ok-soft, rgba(34, 197, 94, 0.15))', border: '1px solid var(--ok)' }}
            role="status"
          >
            <div>
              <p style={{ color: 'var(--ok)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <CheckIcon size={16} /> {resetNotice}
              </p>
            </div>
          </div>
        )}

        {data && (
          <>
            {/* Context Boundary Status Card */}
            <section
              style={{
                border: '1px solid var(--border)',
                borderRadius: '12px',
                padding: '16px',
                background: 'var(--surface-2, rgba(0, 0, 0, 0.02))',
              }}
            >
              <div
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  flexWrap: 'wrap',
                  gap: '12px',
                  marginBottom: '12px',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <h3 style={{ margin: 0, fontSize: '15px', fontWeight: 600 }}>Active Context Boundary</h3>
                  <span className="badge-status badge-info">Epoch {data.contextEpoch}</span>
                </div>
                {!confirmingReset && (
                  <button
                    type="button"
                    className="secondary-button"
                    style={{ fontSize: '13px', padding: '4px 10px' }}
                    onClick={() => setConfirmingReset(true)}
                  >
                    Reset Context Memory
                  </button>
                )}
              </div>

              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                  gap: '12px',
                  fontSize: '13px',
                }}
              >
                <div>
                  <span className="muted" style={{ display: 'block', marginBottom: '2px' }}>
                    Boundary Sequence
                  </span>
                  <strong>{data.contextBoundarySequence}</strong>
                  <p className="muted" style={{ margin: '2px 0 0', fontSize: '11px' }}>
                    Turns ≤ boundary are excluded from model context
                  </p>
                </div>
                <div>
                  <span className="muted" style={{ display: 'block', marginBottom: '2px' }}>
                    Context Reset At
                  </span>
                  <strong>
                    {data.contextResetAt ? new Date(data.contextResetAt).toLocaleString() : 'Never reset'}
                  </strong>
                </div>
                <div>
                  <span className="muted" style={{ display: 'block', marginBottom: '2px' }}>
                    Total Checkpoints
                  </span>
                  <strong>{data.checkpoints.length}</strong>
                </div>
              </div>

              {confirmingReset && (
                <div
                  style={{
                    marginTop: '16px',
                    padding: '12px 14px',
                    borderRadius: '8px',
                    background: 'var(--warn-soft, rgba(234, 179, 8, 0.1))',
                    border: '1px solid var(--warn, #ca8a04)',
                  }}
                >
                  <p
                    style={{
                      margin: '0 0 8px 0',
                      fontWeight: 600,
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                    }}
                  >
                    <AlertIcon size={16} /> Reset Context Memory to Current Turn?
                  </p>
                  <p className="muted" style={{ margin: '0 0 12px 0', fontSize: '12.5px', lineHeight: 1.4 }}>
                    Resetting context memory advances the boundary to the latest turn. All prior turns remain intact in
                    your visible transcript history, but subsequent turns and background summaries will exclude them
                    from model prompt context.
                  </p>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button
                      type="button"
                      className="primary-button"
                      style={{ fontSize: '13px', padding: '6px 12px' }}
                      disabled={resetting}
                      onClick={handleReset}
                    >
                      {resetting ? 'Resetting…' : 'Confirm Reset'}
                    </button>
                    <button
                      type="button"
                      className="ghost-button"
                      style={{ fontSize: '13px' }}
                      disabled={resetting}
                      onClick={() => setConfirmingReset(false)}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </section>

            {/* Research Notebook State */}
            <section>
              <h3
                style={{
                  fontSize: '14px',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  color: 'var(--muted)',
                  margin: '0 0 10px 0',
                }}
              >
                Advising Research State
              </h3>
              {data.researchNotebook ? (
                <div
                  style={{
                    border: '1px solid var(--border)',
                    borderRadius: '10px',
                    padding: '14px',
                    background: 'var(--surface)',
                    fontSize: '13px',
                  }}
                >
                  <pre
                    style={{
                      margin: 0,
                      maxHeight: '180px',
                      overflowY: 'auto',
                      fontSize: '12px',
                      fontFamily: 'monospace',
                    }}
                  >
                    {JSON.stringify(data.researchNotebook, null, 2)}
                  </pre>
                </div>
              ) : (
                <p className="muted" style={{ fontSize: '13px', margin: 0 }}>
                  No active research notebook attached to this conversation.
                </p>
              )}
            </section>

            {/* Checkpoints & Exact References */}
            <section>
              <h3
                style={{
                  fontSize: '14px',
                  fontWeight: 600,
                  textTransform: 'uppercase',
                  color: 'var(--muted)',
                  margin: '0 0 10px 0',
                }}
              >
                Summary Checkpoints ({data.checkpoints.length})
              </h3>
              {data.checkpoints.length === 0 ? (
                <p className="muted" style={{ fontSize: '13px', margin: 0 }}>
                  No background checkpoints recorded for this conversation yet.
                </p>
              ) : (
                <div className="stack" style={{ gap: '14px' }}>
                  {data.checkpoints.map((cp) => (
                    <article
                      key={cp.summaryId}
                      style={{
                        border: '1px solid var(--border)',
                        borderRadius: '10px',
                        padding: '12px 14px',
                        background: 'var(--surface)',
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          flexWrap: 'wrap',
                          gap: '8px',
                          marginBottom: '8px',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span className={checkpointBadgeClass(cp.state)}>{cp.state}</span>
                          <span className="muted" style={{ fontSize: '12px' }}>
                            Epoch {cp.contextEpoch ?? 0}
                          </span>
                          <span className="muted" style={{ fontSize: '11px' }}>
                            Expected Seq: {cp.expectedSequence}
                          </span>
                        </div>
                        <span className="muted" style={{ fontSize: '11px' }}>
                          Covered through turn: {cp.coveredThrough ?? 'None'} (candidate: {cp.candidateCoveredThrough})
                        </span>
                      </div>

                      <p className="muted">
                        {cp.state === 'completed' &&
                        (cp.contextEpoch ?? 0) === data.contextEpoch &&
                        (cp.coveredThrough ?? 0) > data.contextBoundarySequence
                          ? 'Available to current conversation context'
                          : 'Historical or unpublished checkpoint; excluded from current context'}
                      </p>
                      {cp.text && (
                        <div
                          style={{
                            background: 'var(--surface-2, rgba(0,0,0,0.03))',
                            borderLeft: '3px solid var(--border-strong)',
                            padding: '8px 12px',
                            borderRadius: '4px',
                            margin: '8px 0',
                            fontSize: '13px',
                            lineHeight: 1.4,
                          }}
                        >
                          {cp.text}
                        </div>
                      )}

                      {cp.failureCode && (
                        <p className="form-error" style={{ margin: '4px 0', fontSize: '12px' }}>
                          Failure: {cp.failureCode}
                        </p>
                      )}

                      {cp.referenceMetadata && cp.referenceMetadata.length > 0 && (
                        <details style={{ marginTop: '8px', fontSize: '12.5px' }}>
                          <summary className="muted" style={{ cursor: 'pointer' }}>
                            Exact References ({cp.referenceMetadata.length} turns)
                          </summary>
                          <div style={{ overflowX: 'auto', marginTop: '6px' }}>
                            <table
                              style={{
                                width: '100%',
                                borderCollapse: 'collapse',
                                fontSize: '12px',
                                textAlign: 'left',
                              }}
                            >
                              <thead>
                                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                                  <th style={{ padding: '4px 8px' }}>Seq</th>
                                  <th style={{ padding: '4px 8px' }}>Role</th>
                                  <th style={{ padding: '4px 8px' }}>Status</th>
                                  <th style={{ padding: '4px 8px' }}>Citations</th>
                                  <th style={{ padding: '4px 8px' }}>Context Items</th>
                                  <th style={{ padding: '4px 8px' }}>Selected source and artifact scope</th>
                                </tr>
                              </thead>
                              <tbody>
                                {cp.referenceMetadata.map((ref) => (
                                  <tr
                                    key={ref.sequence}
                                    style={{ borderBottom: '1px solid var(--border-subtle, rgba(0,0,0,0.05))' }}
                                  >
                                    <td style={{ padding: '4px 8px' }}>{ref.sequence}</td>
                                    <td style={{ padding: '4px 8px' }}>{ref.role}</td>
                                    <td style={{ padding: '4px 8px' }}>{ref.status}</td>
                                    <td style={{ padding: '4px 8px' }}>
                                      <MemoryReferences references={ref.citations} label="citations" />
                                    </td>
                                    <td style={{ padding: '4px 8px' }}>
                                      <MemoryReferences references={ref.contextBlock} label="passages" />
                                    </td>
                                    <td style={{ padding: '4px 8px' }}>
                                      {ref.effective ? (
                                        <details>
                                          <summary>Selected scope</summary>
                                          <p>Collection: {ref.effective.collectionId ?? 'Unspecified'}</p>
                                          <p>
                                            Sources:{' '}
                                            {ref.effective.sourceIds === null
                                              ? 'All visible sources'
                                              : ref.effective.sourceIds.join(', ') || 'No sources selected'}
                                          </p>
                                          <ul>
                                            {ref.effective.artifactReferences.map((artifact) => (
                                              <li key={`${artifact.artifactId}:${artifact.revisionId}`}>
                                                {artifact.spec?.title ?? 'Artifact'}
                                                <div>
                                                  Artifact: <code>{artifact.artifactId}</code>
                                                </div>
                                                <div>
                                                  Revision: <code>{artifact.revisionId}</code>
                                                </div>
                                              </li>
                                            ))}
                                          </ul>
                                        </details>
                                      ) : (
                                        'None'
                                      )}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </details>
                      )}
                    </article>
                  ))}
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </Dialog>
  );
}

function MemoryReferences({
  references,
  label,
}: {
  references?: Array<{ chunkId: string; sourceId?: string; sourceName: string; locatorLabel: string }>;
  label: string;
}) {
  if (!references?.length) return <>None</>;
  return (
    <details>
      <summary>
        {references.length} {label}
      </summary>
      <ul>
        {references.map((reference, index) => (
          <li key={String(reference.chunkId ?? index)}>
            {String(reference.sourceName ?? 'Source')} - {String(reference.locatorLabel ?? 'Location unavailable')}
            <div>
              Passage: <code>{String(reference.chunkId ?? 'Unavailable')}</code>
            </div>
            {reference.sourceId ? (
              <div>
                Source: <code>{String(reference.sourceId)}</code>
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </details>
  );
}
