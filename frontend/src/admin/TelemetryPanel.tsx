import { useState } from 'react';
import type { ModelPriceSchedule, TelemetryUsageResponse } from '../api/types';
import { getAdminTelemetryUsage, listAdminPriceSchedules } from '../api/backoffice';
import { formatTimestamp } from '../lib/format';
import { useLoad } from '../lib/useLoad';
import { AdminError, AdminWorkspace, RefreshButton, type AdminPanelProps } from './adminUi';

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

function count(value?: number | null): string {
  return value != null ? value.toLocaleString() : '—';
}

export function TelemetryPanel({ toolbar }: AdminPanelProps) {
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
      <RefreshButton toolbar={toolbar} busy={loading} onRefresh={() => void reload()} />
      <AdminError error={error} />
      <AdminWorkspace>
        <div className="admin-audit-panel">
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: '1rem',
            }}
          >
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <label htmlFor="telemetry-group" style={{ fontSize: '0.85rem', fontWeight: 600 }}>
                Group by:
              </label>
              <select
                id="telemetry-group"
                value={groupBy}
                onChange={(e) => setGroupBy(e.target.value as GroupBy)}
                disabled={loading}
              >
                <option value="date">Date</option>
                <option value="model">Model</option>
                <option value="operation">Operation</option>
              </select>
            </div>
          </div>

          {usage && <UsageSection usage={usage} />}

          <h3>Model Price Schedules</h3>
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
                  <tr>
                    <td colSpan={8} style={{ textAlign: 'center', padding: '1.5rem' }}>
                      No price schedules configured.
                    </td>
                  </tr>
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
        </div>
      </AdminWorkspace>
    </>
  );
}

function UsageSection({ usage }: { usage: TelemetryUsageResponse }) {
  const { summary } = usage;
  const cost = costHeadline(summary);
  return (
    <>
      <div className="admin-overview-grid" style={{ marginBottom: '1.5rem' }}>
        <div className="admin-stat-card">
          <div className="admin-stat-number">{summary.totalCalls}</div>
          <div className="admin-stat-label">
            Total Calls ({summary.completedCalls} ok, {summary.failedCalls} failed)
          </div>
        </div>
        <div className="admin-stat-card">
          <div className="admin-stat-number">{cost.headline}</div>
          <div className="admin-stat-label">
            Cost
            {cost.known ? ` (${cost.known})` : ''}
          </div>
        </div>
        <div className="admin-stat-card">
          <div className="admin-stat-number">{percent(summary.cachedTokenShare)}</div>
          <div className="admin-stat-label">Cached Token Share (reporting calls)</div>
        </div>
        <div className="admin-stat-card">
          <div className="admin-stat-number">{percent(summary.cacheHitFraction)}</div>
          <div className="admin-stat-label">
            Cache Hit Rate ({summary.callsWithCacheHits}/{summary.callsReportingCache} reported)
          </div>
        </div>
      </div>

      <p className="muted">
        Costs use recorded tariff versions, not reconciled invoices. Unknown cost is not zero. Cache metrics are not
        answer quality. Cache usage is known for {summary.callsReportingCache} of {summary.totalCalls} calls.
        {(summary.costCurrencies?.length || 0) > 1 &&
          ' Cost totals span multiple currencies; filter by model to inspect them separately.'}
      </p>
      <h3>Usage Breakdown</h3>
      <div className="admin-table-wrap" style={{ marginBottom: '2rem' }}>
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
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', padding: '1.5rem' }}>
                  No usage records found.
                </td>
              </tr>
            ) : (
              usage.breakdown.map((row) => (
                <tr key={`${row.bucket}-${row.provider || ''}-${row.modelId || ''}`}>
                  <td className="mono">{row.bucket}</td>
                  <td>{row.totalCalls}</td>
                  <td>{count(row.totalPromptTokens)}</td>
                  <td>{count(row.totalCachedTokens)}</td>
                  <td>{count(row.totalCompletionTokens)}</td>
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
