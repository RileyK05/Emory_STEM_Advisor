import type { Citation } from '../api/types';
import { useApp } from '../state/AppContext';

export function CitationCard({ citation }: { citation: Citation }) {
  const { showCitationSource } = useApp();
  return (
    <button type="button" className="citation-card" onClick={() => showCitationSource(citation)}>
      <span className="citation-source">{citation.sourceName}</span>
      <span className="citation-locator">{citation.locatorLabel}</span>
    </button>
  );
}
