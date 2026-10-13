import type { Citation, ContextItem } from '../../api/types';
import { CloseIcon } from '../icons';

interface Props {
  citations: Citation[];
  context: ContextItem[];
  /** The passage number shown expanded, if any. */
  openNumber: number | null;
  onToggle: (passageNumber: number | null) => void;
}

/** The sources an answer cites, as numbered chips that expand to the exact passage. */
export function SourceList({ citations, context, openNumber, onToggle }: Props) {
  const seen = new Set<string>();
  const rows = citations
    .filter((citation) => !seen.has(citation.chunkId) && seen.add(citation.chunkId))
    .map((citation) => ({
      citation,
      number: context.findIndex((item) => item.chunkId === citation.chunkId) + 1,
    }))
    .sort((a, b) => (a.number || Infinity) - (b.number || Infinity));
  const passage = openNumber ? context[openNumber - 1] : undefined;

  if (!rows.length && !passage) return null;
  return (
    <div className="sources">
      {rows.length > 0 && (
        <div className="sources-row" aria-label="Sources">
          <span className="sources-label">Sources</span>
          {rows.map(({ citation, number }) => (
            <button
              type="button"
              key={citation.chunkId}
              className="source-chip"
              aria-expanded={number > 0 && openNumber === number}
              aria-label={`${citation.sourceName} ${citation.locatorLabel}`}
              disabled={number === 0}
              title={number === 0 ? 'This passage was not saved with the answer.' : 'Show the passage'}
              onClick={() => onToggle(openNumber === number ? null : number)}
            >
              {number > 0 && <span className="source-chip-num">{number}</span>}
              <span className="source-chip-name">{citation.sourceName}</span>
              <span className="source-chip-loc">{citation.locatorLabel}</span>
            </button>
          ))}
        </div>
      )}
      {passage && (
        <figure className="passage" aria-label="Cited passage">
          <figcaption>
            <span className="source-chip-num">{openNumber}</span>
            <strong>{passage.sourceName}</strong>
            <span className="muted">{passage.locatorLabel}</span>
            <button type="button" className="icon-button" aria-label="Close passage" onClick={() => onToggle(null)}>
              <CloseIcon size={16} />
            </button>
          </figcaption>
          <blockquote>{passage.text}</blockquote>
        </figure>
      )}
    </div>
  );
}
