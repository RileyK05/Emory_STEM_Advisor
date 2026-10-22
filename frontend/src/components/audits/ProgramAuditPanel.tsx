import { useCallback, useEffect, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type { AuditableProgramItem, DegreeAuditDetailResponse, DegreeAuditRecordItem } from '../../api/types';
import { errorMessage } from '../../lib/errors';

export function ProgramAuditPanel() {
  const [programs, setPrograms] = useState<AuditableProgramItem[]>([]);
  const [audits, setAudits] = useState<DegreeAuditRecordItem[]>([]);
  const [programId, setProgramId] = useState('');
  const [detail, setDetail] = useState<DegreeAuditDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestSeq = useRef(0);

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const [programRows, auditRows] = await Promise.all([
        getClient().listAuditablePrograms(),
        getClient().listProgramAudits(),
      ]);
      if (seq !== requestSeq.current) return;
      setPrograms(programRows);
      setAudits(auditRows);
      setDetail((current) => {
        if (!current) return current;
        const match = auditRows.find((row) => row.auditId === current.audit.auditId);
        if (!match) return current;
        return {
          ...current,
          audit: { ...current.audit, isStale: match.isStale, overallStatus: match.overallStatus },
        };
      });
    } catch (cause) {
      if (seq === requestSeq.current) setError(errorMessage(cause));
    } finally {
      if (seq === requestSeq.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      requestSeq.current += 1;
    };
  }, [load]);

  const run = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await getClient().runProgramAudit({ programId });
      setDetail(saved);
      setNotice('Program audit saved.');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const exportAudit = async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    try {
      const exported = await getClient().exportProgramAudit(detail.audit.auditId);
      setDetail((current) =>
        current && current.audit.auditId === exported.audit.auditId
          ? { ...current, audit: { ...current.audit, isStale: exported.audit.isStale } }
          : current,
      );
      setNotice(
        exported.audit.isStale
          ? 'Exported program audit is stale.'
          : 'Exported program audit matches the saved result.',
      );
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="stack">
      <p>Coverage is a program audit, not a degree audit.</p>
      {loading && <p>Loading program audits...</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={() => void load()}>
            Retry program audits
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      {!loading && !error && audits.length === 0 && <p>No saved program audits.</p>}
      {!loading && !error && programs.length === 0 && <p>No published programs are ready for a program audit.</p>}
      <ul>
        {audits.map((audit) => (
          <li key={audit.auditId}>
            <button type="button" onClick={() => void getClient().getProgramAudit(audit.auditId).then(setDetail)}>
              {audit.programTitle ?? 'Program'} {audit.overallStatus}
              {audit.isStale ? ' stale' : ''}
            </button>
          </li>
        ))}
      </ul>
      <label>
        Program
        <select value={programId} onChange={(event) => setProgramId(event.target.value)}>
          <option value="">Select a program</option>
          {programs.map((program) => (
            <option key={program.programId} value={program.programId}>
              {program.title}
            </option>
          ))}
        </select>
      </label>
      <button type="button" disabled={busy || loading || !programId} onClick={() => void run()}>
        Run program audit
      </button>
      <button type="button" disabled={busy || loading} onClick={() => void load()}>
        Refresh program audits
      </button>
      {detail && (
        <>
          <p>Status {detail.audit.overallStatus}</p>
          {detail.audit.isStale && <p>This program audit is stale.</p>}
          <ul>
            {(detail.evaluations ?? []).map((evaluation) => (
              <li key={evaluation.evaluationId}>
                {evaluation.title} {evaluation.status}
              </li>
            ))}
          </ul>
          {detail.audit.isStale && (
            <button type="button" disabled={busy || !programId} onClick={() => void run()}>
              Recompute program audit
            </button>
          )}
          <button type="button" disabled={busy} onClick={() => void exportAudit()}>
            Export program audit
          </button>
        </>
      )}
    </section>
  );
}
