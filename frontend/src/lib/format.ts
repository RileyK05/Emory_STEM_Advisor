/** A stored ISO timestamp in the viewer's locale, or `empty` when there is none. */
export function formatTimestamp(value?: string | null, empty = '—'): string {
  return value ? new Date(value).toLocaleString() : empty;
}
