import { useState } from 'react';
import type { ModelPriceSchedule, TelemetryUsageResponse } from '../api/types';
import { getAdminTelemetryUsage, listAdminPriceSchedules } from '../api/backoffice';
import { formatTimestamp } from '../lib/format';
import { useLoad } from '../lib/useLoad';
import { AdminError, AdminSection, EmptyRow, formatCount } from './adminUi';

type GroupBy = 'date' | 'model' | 'operation';

/** Unpriced calls make a total unknown, never zero; a partial total is shown only as the known portion. */
function costHeadline(row: {
  totalCost?: number | null;
  costCurrency?: string | null;
  unknownCostCalls: number;
  costCompleteness?: 'complete' | 'partial' | 'unknown';
}): { headline: string; known: string | null } {
  const known = row.totalCost != null ? `${row.costCurrency || ''} ${row.totalCost.toFixed(4)}`.trim() : null;
  if (row.unknownCostCalls > 0 || row.costCompleteness === 'partial') {
    return { headline: 'Unknown', known: known ? `Known portion ${known}` : null };
  }
  if (row.costCompleteness === 'unknown') return { headline: 'Unknown', known: null };
  return { headline: known ?? '—', known: null };
}

function percent(fraction?: number | null): string {
  return fraction != null ? `${(fraction * 100).toFixed(1)}%` : '—';
}

export function TelemetryPanel() {
  const [groupBy, setGroupBy] = useState<GroupBy>('date');
  const [usage, setUsage] = useState<TelemetryUsageResponse | null>(null);
  const [schedules, setSchedules] = useState<ModelPriceSchedule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const { loading, reload } = useLoad(
    () => Promise.all([getAdminTelemetryUsage({ groupBy }), listAdminPriceSchedules()]),
    [groupBy],
    {
      onLoad: ([usageRes, schedulesRes]) => {
        setUsage(usageRes);
        setSchedules(schedulesRes);
      },
      setError,
    },
  );

  return (
    <>
      <AdminSection
        title="Model usage"
        description="Calls, tokens and estimated cost from recorded model calls."
        busy={loading}
        onRefresh={() => void reload()}
        actions={
          <label className="admin-inline-field">
            Group by
            <select value={groupBy} onChange={(e) => setGroupBy(e.target.value as GroupBy)} disabled={loading}>
              <option value="date">Date</option>
              <option value="model">Model</option>
              <option value="operation">Operation</option>
            </select>
          </label>
        }
      >
        <AdminError error={error} />
        {usage && <UsageSection usage={usage} />}
      </AdminSection>
      <AdminSection title="Model price schedules" description="The tariffs cost estimates are computed from.">
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Model</th>
                <th>Billing / currency</th>
                <th>Input / 1M</th>
                <th>Cached Read / 1M</th>
                <th>Output / 1M</th>
                <th>Effective From</th>
                <th>Provenance</th>
              </tr>
            </thead>
            <tbody>
              {schedules.length === 0 ? (
                <EmptyRow columns={8} loading={loading}>
                  No price schedules configured.
                </EmptyRow>
              ) : (
                schedules.map((s) => (
                  <tr key={s.scheduleId}>
                    <td>{s.provider}</td>
                    <td className="mono">{s.modelId}</td>
                    <td>
                      {s.billingMode} / {s.currency}
                    </td>
                    <td>{s.inputRatePerMillion.toFixed(6)}</td>
                    <td>{s.cachedReadRatePerMillion.toFixed(6)}</td>
                    <td>{s.outputRatePerMillion.toFixed(6)}</td>
                    <td>{formatTimestamp(s.effectiveFrom)}</td>
                    <td>{s.provenance}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </AdminSection>
    </>
  );
}

function UsageSection({ usage }: { usage: TelemetryUsageResponse }) {
  const { summary } = usage;
  const cost = costHeadline(summary);
  return (
    <>
      <div className="admin-cards">
        <div className="admin-card">
          <span>
            Total calls ({summary.completedCalls} ok, {summary.failedCalls} failed)
          </span>
          <strong>{formatCount(summary.totalCalls)}</strong>
        </div>
        <div className="admin-card">
          <span>
            Cost
            {cost.known ? ` (${cost.known})` : ''}
          </span>
          <strong>{cost.headline}</strong>
        </div>
        <div className="admin-card">
          <span>Cached Token Share (reporting calls)</span>
          <strong>{percent(summary.cachedTokenShare)}</strong>
        </div>
        <div className="admin-card">
          <span>
            Cache hit rate ({summary.callsWithCacheHits}/{summary.callsReportingCache} reported)
          </span>
          <strong>{percent(summary.cacheHitFraction)}</strong>
        </div>
      </div>

      <p className="admin-note">
        Costs use recorded tariff versions, not reconciled invoices. Unknown cost is not zero. Cache metrics are not
        answer quality. Cache usage is known for {summary.callsReportingCache} of {summary.totalCalls} calls.
        {(summary.costCurrencies?.length || 0) > 1 &&
          ' Cost totals span multiple currencies; filter by model to inspect them separately.'}
      </p>
      <h3 className="admin-subhead">Breakdown</h3>
      <div className="admin-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>Bucket</th>
              <th>Total Calls</th>
              <th>Prompt Tokens</th>
              <th>Cached Tokens</th>
              <th>Completion Tokens</th>
              <th>Cache Share</th>
              <th>Est. Cost</th>
            </tr>
          </thead>
          <tbody>
            {usage.breakdown.length === 0 ? (
              <EmptyRow columns={7}>No usage records found.</EmptyRow>
            ) : (
              usage.breakdown.map((row) => (
                <tr key={`${row.bucket}-${row.provider || ''}-${row.modelId || ''}`}>
                  <td className="mono">{row.bucket}</td>
                  <td>{row.totalCalls}</td>
                  <td>{formatCount(row.totalPromptTokens)}</td>
                  <td>{formatCount(row.totalCachedTokens)}</td>
                  <td>{formatCount(row.totalCompletionTokens)}</td>
                  <td>{percent(row.cachedTokenShare)}</td>
                  <td>{row.unknownCostCalls > 0 ? 'Unknown' : costHeadline(row).headline}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
