import { useState } from 'react';
import type { ApplicabilityNotice, SourceApplicabilityItem } from '../../api/types';

interface ApplicabilityBannerProps {
  notice: ApplicabilityNotice;
}

export function ApplicabilityBanner({ notice }: ApplicabilityBannerProps) {
  const [detailsOpen, setDetailsOpen] = useState(false);

  const levelClass =
    notice.level === 'conflict'
      ? 'callout callout-error applicability-banner-conflict'
      : notice.level === 'warning'
        ? 'callout callout-warning applicability-banner-warning'
        : 'callout callout-info applicability-banner-info';

  const badgeClassForCompatibility = (compat: SourceApplicabilityItem['cohortCompatibility']) => {
    switch (compat) {
      case 'compatible':
        return 'badge-status badge-success';
      case 'mismatch':
        return 'badge-status badge-danger';
      default:
        return 'badge-status badge-muted';
    }
  };

  const badgeClassForFreshness = (freshness: SourceApplicabilityItem['freshness']) => {
    switch (freshness) {
      case 'current':
        return 'badge-status badge-success';
      case 'aging':
        return 'badge-status badge-warning';
      case 'stale':
        return 'badge-status badge-danger';
      default:
        return 'badge-status badge-muted';
    }
  };

  return (
    <div
      className={`applicability-banner ${levelClass}`}
      role={notice.level === 'conflict' ? 'alert' : 'region'}
      aria-label="Source applicability and freshness notice"
    >
      <div className="applicability-header">
        <div className="applicability-headline-row">
          <span className="applicability-level-tag" aria-hidden="true">
            {notice.level === 'conflict' ? '⚠ Conflict' : notice.level === 'warning' ? '▲ Notice' : 'ℹ Scope'}
          </span>
          <strong className="applicability-headline">{notice.headline}</strong>
          {notice.declaredCohort && (
            <span className="cohort-tag" title="Declared student cohort">
              Cohort: {notice.declaredCohort}
            </span>
          )}
        </div>

        {notice.hasConflicts && notice.conflicts.length > 0 && (
          <ul className="applicability-conflicts">
            {notice.conflicts.map((conflict, index) => (
              <li key={index}>{conflict}</li>
            ))}
          </ul>
        )}
      </div>

      {notice.sources.length > 0 && (
        <div className="applicability-sources-section">
          <button
            type="button"
            className="ghost-button applicability-toggle"
            onClick={() => setDetailsOpen((prev) => !prev)}
            aria-expanded={detailsOpen}
          >
            {detailsOpen ? 'Hide source details' : `View ${notice.sources.length} cited source details`}
          </button>

          {detailsOpen && (
            <ul className="applicability-sources-list">
              {notice.sources.map((source) => (
                <li key={source.sourceId} className="applicability-source-item">
                  <div className="source-item-header">
                    <span className="source-name font-medium">{source.sourceName}</span>
                    <div className="source-badges">
                      {source.edition && <span className="badge-status badge-info">{source.edition}</span>}
                      {source.isSuperseded ? (
                        <span className="badge-status badge-warning">Superseded</span>
                      ) : (
                        <span className="badge-status badge-success">Current</span>
                      )}
                      <span className={badgeClassForCompatibility(source.cohortCompatibility)}>
                        {source.cohortCompatibility === 'compatible'
                          ? 'Cohort Match'
                          : source.cohortCompatibility === 'mismatch'
                            ? 'Cohort Mismatch'
                            : 'Cohort Unknown'}
                      </span>
                      {source.freshness !== 'unknown' && (
                        <span className={badgeClassForFreshness(source.freshness)}>{source.freshness}</span>
                      )}
                    </div>
                  </div>
                  <p className="source-summary text-sm muted">{source.summary}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
