import { useState, type DependencyList, type ReactNode } from 'react';
import { useAsyncAction } from '../lib/useAsyncAction';
import { useLoad } from '../lib/useLoad';

/** Rows per page in the paged admin lists. */
export const PAGE = 50;

/** A count with thousands separators, or a dash when the backend did not report it. */
export function formatCount(value?: number | null): string {
  return value != null ? value.toLocaleString() : '—';
}

/**
 * One admin panel as a card: its heading, a line on what it is for, and its own actions. A panel
 * that loads a listing passes `onRefresh`, so every Refresh sits beside the list it reloads.
 */
export function AdminSection({
  title,
  description,
  actions,
  busy = false,
  onRefresh,
  label,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  busy?: boolean;
  onRefresh?: () => void;
  /** Accessible name when it differs from the visible title. */
  label?: string;
  children: ReactNode;
}) {
  return (
    <section className="admin-section" aria-label={label ?? title}>
      <header className="admin-section-head">
        <div className="admin-section-title">
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {(actions || onRefresh) && (
          <div className="admin-section-actions">
            {actions}
            {onRefresh && (
              <button type="button" className="secondary-button" onClick={onRefresh} disabled={busy}>
                Refresh
              </button>
            )}
          </div>
        )}
      </header>
      {children}
    </section>
  );
}

/** The one row an empty table shows: loading, or what an empty list means. */
export function EmptyRow({ columns, loading, children }: { columns: number; loading?: boolean; children: ReactNode }) {
  return (
    <tr className="admin-empty-row">
      <td colSpan={columns}>{loading ? 'Loading…' : children}</td>
    </tr>
  );
}

export function FieldLine({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="admin-field-line">
      <span>{label}</span>
      <div>{children}</div>
    </div>
  );
}

export function AdminError({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p role="alert" className="admin-error">
      {error}
    </p>
  );
}

/** A listing beside an optional detail panel for the selected row. */
export function AdminWorkspace({ detail, children }: { detail?: ReactNode; children: ReactNode }) {
  return (
    <div className="admin-workspace">
      <div className="admin-list">{children}</div>
      {detail && (
        <aside className="admin-detail-panel" aria-label="Admin detail">
          {detail}
        </aside>
      )}
    </div>
  );
}

export function DetailHead({ title, onClose }: { title: ReactNode; onClose: () => void }) {
  return (
    <div className="admin-detail-head">
      <h2>{title}</h2>
      <button type="button" className="secondary-button" onClick={onClose}>
        Close
      </button>
    </div>
  );
}

export function Pager({
  offset,
  count,
  busy,
  onOffset,
}: {
  offset: number;
  count: number;
  busy: boolean;
  onOffset: (offset: number) => void;
}) {
  // One page of rows has nothing to page through.
  if (offset === 0 && count < PAGE) return null;
  return (
    <div className="admin-pagination">
      <span>{count ? `${offset + 1}–${offset + count}` : 'No rows'}</span>
      <button
        type="button"
        className="secondary-button"
        disabled={busy || offset === 0}
        onClick={() => onOffset(Math.max(0, offset - PAGE))}
      >
        Previous
      </button>
      <button
        type="button"
        className="secondary-button"
        disabled={busy || count !== PAGE}
        onClick={() => onOffset(offset + PAGE)}
      >
        Next
      </button>
    </div>
  );
}

/** One offset-paged admin list plus the busy/error state of the actions taken on it.
 *  `working` covers both loading and an action in flight. */
export function usePagedList<T>(fetchPage: (offset: number) => Promise<T[]>, deps: DependencyList = []) {
  const [rows, setRows] = useState<T[]>([]);
  const [offset, setOffset] = useState(0);
  const action = useAsyncAction();
  const { loading, reload } = useLoad(() => fetchPage(offset), [offset, ...deps], {
    onLoad: setRows,
    setError: action.setError,
  });
  return { ...action, rows, offset, setOffset, reload, working: action.busy || loading };
}

/** The search box above the Users and Chats lists; submits the typed text. */
export function AdminSearch({
  placeholder,
  busy,
  onSearch,
}: {
  placeholder: string;
  busy: boolean;
  onSearch: (query: string) => void;
}) {
  const [query, setQuery] = useState('');
  return (
    <form
      className="admin-search"
      onSubmit={(event) => {
        event.preventDefault();
        onSearch(query);
      }}
    >
      <input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={placeholder}
        aria-label="Search"
      />
      <button type="submit" className="primary-button" disabled={busy}>
        Search
      </button>
    </form>
  );
}
