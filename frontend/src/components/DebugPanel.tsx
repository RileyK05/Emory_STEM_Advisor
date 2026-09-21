import { useState } from 'react';
import type { ReactNode } from 'react';
import type { RetrievalTrace, SeamStatus } from '../api/types';
import { useApp } from '../state/AppContext';

const SEAM_STATE_LABEL: Record<SeamStatus['state'], string> = {
  active: 'active',
  dormant: 'dormant',
  disabled: 'disabled',
  failed: 'failed',
};

function Collapsible({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="dbg-section">
      <button type="button" className="dbg-section-header" onClick={() => setOpen((v) => !v)}>
        <span className={`dbg-caret ${open ? 'dbg-caret-open' : ''}`}>▸</span>
        <span>{title}</span>
        {count !== undefined && <span className="dbg-count">{count}</span>}
      </button>
      {open && <div className="dbg-section-body">{children}</div>}
    </div>
  );
}

export function DebugPanel({ trace }: { trace: RetrievalTrace }) {
  const { debugMode } = useApp();
  if (!debugMode) return null;

  const hasProblems = trace.errors.length > 0 || trace.seams.some((s) => s.state === 'failed');

  return (
    <div className="debug-panel">
      <div className="debug-panel-title">
        Retrieval trace <code>{trace.traceId}</code>
      </div>

      {hasProblems && (
        <div className="dbg-banner dbg-banner-error">
          <strong>Pipeline problems detected.</strong>
          {trace.errors.map((e, i) => (
            <div key={i}>ERROR: {e}</div>
          ))}
          {trace.seams
            .filter((s) => s.state === 'failed')
            .map((s) => (
              <div key={s.name}>
                ERROR: seam "{s.name}" failed — {s.reason ?? 'no reason given'}
              </div>
            ))}
        </div>
      )}
      {trace.warnings.length > 0 && (
        <div className="dbg-banner dbg-banner-warning">
          {trace.warnings.map((w, i) => (
            <div key={i}>WARN: {w}</div>
          ))}
        </div>
      )}

      <div className="dbg-query">
        <div>
          <span className="dbg-label">query</span> {trace.query}
        </div>
        <div>
          <span className="dbg-label">normalized</span> <code>{trace.normalizedQuery || '(empty)'}</code>
        </div>
      </div>

      <Collapsible title="Seams" count={trace.seams.length}>
        <table className="dbg-table">
          <thead>
            <tr>
              <th>seam</th>
              <th>state</th>
              <th>candidates</th>
              <th>why</th>
            </tr>
          </thead>
          <tbody>
            {trace.seams.map((s) => (
              <tr key={s.name}>
                <td>{s.name}</td>
                <td>
                  <span className={`seam-state seam-state-${s.state}`}>{SEAM_STATE_LABEL[s.state]}</span>
                </td>
                <td>{s.candidateCount}</td>
                <td className="dbg-dim">{s.reason ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Collapsible>

      <Collapsible title="Contributions" count={trace.contributions.length}>
        {trace.contributions.length === 0 ? (
          <p className="dbg-dim">No chunks survived fusion.</p>
        ) : (
          <table className="dbg-table">
            <thead>
              <tr>
                <th>chunk</th>
                <th>seams</th>
                <th>score</th>
                <th>slot</th>
              </tr>
            </thead>
            <tbody>
              {trace.contributions.map((c) => (
                <tr key={c.chunkId}>
                  <td>
                    <code>{c.chunkId}</code>
                  </td>
                  <td>
                    {c.seams.map((s) => (
                      <span key={s} className="seam-chip">
                        {s}
                      </span>
                    ))}
                  </td>
                  <td>
                    <div className="score-bar">
                      <div className="score-bar-fill" style={{ width: `${Math.round(c.normalizedScore * 100)}%` }} />
                      <span>{c.normalizedScore.toFixed(2)}</span>
                    </div>
                  </td>
                  <td>{c.sourceSlot}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Collapsible>

      <Collapsible title="Context block (sent to model)" count={trace.contextBlock.length}>
        {trace.contextBlock.length === 0 ? (
          <p className="dbg-dim">Empty — the model was never prompted (refusal path).</p>
        ) : (
          trace.contextBlock.map((c, i) => (
            <div key={c.chunkId} className="dbg-chunk">
              <div className="dbg-chunk-header">
                <span>
                  [{i + 1}] {c.sourceName} · {c.locatorLabel}
                </span>
                <code>{c.chunkId}</code>
              </div>
              <pre>{c.text}</pre>
            </div>
          ))
        )}
      </Collapsible>

      <Collapsible title="Prompt">
        <div className="dbg-prompt">
          <div className="dbg-label">system</div>
          <pre>{trace.prompt.system}</pre>
          <div className="dbg-label">user</div>
          <pre>{trace.prompt.user}</pre>
        </div>
      </Collapsible>

      <Collapsible title="Model call">
        <table className="dbg-table">
          <tbody>
            <tr>
              <td className="dbg-label">provider</td>
              <td>{trace.modelCall.provider}</td>
            </tr>
            <tr>
              <td className="dbg-label">model</td>
              <td>
                <code>{trace.modelCall.modelId}</code>
              </td>
            </tr>
            <tr>
              <td className="dbg-label">prompt tokens</td>
              <td>{trace.modelCall.promptTokens}</td>
            </tr>
            <tr>
              <td className="dbg-label">completion tokens</td>
              <td>{trace.modelCall.completionTokens}</td>
            </tr>
            <tr>
              <td className="dbg-label">latency</td>
              <td>{trace.modelCall.latencyMs} ms</td>
            </tr>
          </tbody>
        </table>
      </Collapsible>
    </div>
  );
}
