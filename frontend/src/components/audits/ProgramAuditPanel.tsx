import { useState } from 'react';
import { getClient } from '../../api/client';
import type { AuditableProgramItem, DegreeAuditDetailResponse, DegreeAuditRecordItem } from '../../api/types';
import { useAsyncAction } from '../../lib/useAsyncAction';
import { useLoad } from '../../lib/useLoad';

export function ProgramAuditPanel() {
  const [programs, setPrograms] = useState<AuditableProgramItem[]>([]);
  const [audits, setAudits] = useState<DegreeAuditRecordItem[]>([]);
  const [programId, setProgramId] = useState('');
  const [detail, setDetail] = useState<DegreeAuditDetailResponse | null>(null);
  const { busy, error, notice, setError, setNotice, run } = useAsyncAction();

  const { loading, reload: load } = useLoad(
    () => Promise.all([getClient().listAuditablePrograms(), getClient().listProgramAudits()]),
    [],
    {
      onLoad: ([programRows, auditRows]) => {
        setPrograms(programRows);
        setAudits(auditRows);
        // Carry the list's fresher staleness and status into the open audit.
        setDetail((current) => {
          if (!current) return current;
          const match = auditRows.find((row) => row.auditId === current.audit.auditId);
          if (!match) return current;
          return {
            ...current,
            audit: { ...current.audit, isStale: match.isStale, overallStatus: match.overallStatus },
          };
        });
      },
      setError,
    },
  );

  const runAudit = () =>
    run(async () => {
      setDetail(await getClient().runProgramAudit({ programId }));
      setNotice('Program audit saved.');
      await load();
    });

  const exportAudit = async () => {
    if (!detail) return;
    await run(
      async () => {
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
      },
      { keepNotice: true },
    );
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
      <button type="button" disabled={busy || loading || !programId} onClick={() => void runAudit()}>
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
            <button type="button" disabled={busy || !programId} onClick={() => void runAudit()}>
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
