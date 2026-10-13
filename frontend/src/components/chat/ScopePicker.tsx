import { useCallback, useRef, useState } from 'react';
import type { SourceDoc } from '../../api/types';
import type { SourceScope } from '../../state/AppContext';
import { useDismiss } from '../../lib/useDismiss';
import { BookIcon, ChevronIcon } from '../icons';
import { statusLabel } from '../source/statusLabel';

export function scopeLabel(scope: SourceScope) {
  if (scope === null) return 'All sources';
  if (scope.length === 0) return 'No sources';
  return `${scope.length} source${scope.length === 1 ? '' : 's'}`;
}

interface Props {
  scope: SourceScope;
  sources: SourceDoc[];
  onChange: (scope: SourceScope) => void;
  onManage: () => void;
}

/** "Search in": every source, or an explicit subset (which may be empty — then nothing is searched). */
export function ScopePicker({ scope, sources, onChange, onManage }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  const ready = sources.filter((source) => source.status === 'indexed');
  const included = (id: string) => scope === null || scope.includes(id);

  const toggle = (id: string) => {
    const current = scope ?? ready.map((source) => source.id);
    onChange(current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  };

  return (
    <div className="scope-picker" ref={ref}>
      <button
        type="button"
        className={'pill' + (scope !== null && scope.length === 0 ? ' pill-warning' : '')}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <BookIcon size={14} />
        <span>
          Search in: <strong>{scopeLabel(scope)}</strong>
        </span>
        <ChevronIcon size={14} />
      </button>
      {open && (
        <div className="popover scope-popover" role="group" aria-label="Sources to search">
          <label className="scope-option">
            <input type="radio" name="scope" checked={scope === null} onChange={() => onChange(null)} />
            <span>
              <strong>All sources</strong>
              <small>Search everything in your library.</small>
            </span>
          </label>
          <label className="scope-option">
            <input
              type="radio"
              name="scope"
              checked={scope !== null}
              onChange={() => onChange(ready.map((source) => source.id))}
            />
            <span>
              <strong>Only these sources</strong>
              <small>Pick which documents the advisor may use.</small>
            </span>
          </label>
          <ul className="scope-list">
            {sources.map((source) => (
              <li key={source.id}>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`Include ${source.name} in questions`}
                    checked={source.status === 'indexed' && included(source.id)}
                    disabled={source.status !== 'indexed'}
                    onChange={() => toggle(source.id)}
                  />
                  <span className="scope-source-name">{source.name}</span>
                  {source.status !== 'indexed' && <small className="muted">{statusLabel(source.status)}</small>}
                </label>
              </li>
            ))}
            {sources.length === 0 && <li className="muted">No sources yet.</li>}
          </ul>
          {scope !== null && scope.length === 0 && (
            <p className="scope-warning" role="status">
              No sources selected — the advisor won’t be able to answer.
            </p>
          )}
          <button
            type="button"
            className="link-button"
            onClick={() => {
              setOpen(false);
              onManage();
            }}
          >
            Manage sources…
          </button>
        </div>
      )}
    </div>
  );
}
