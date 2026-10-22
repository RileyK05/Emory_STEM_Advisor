import { useCallback, useEffect, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type { AuditableProgramItem, ProgramComparisonResponse, ProgramComparisonSummary } from '../../api/types';
import { errorMessage } from '../../lib/errors';

export function ProgramComparisonPanel() {
  const [programs, setPrograms] = useState<AuditableProgramItem[]>([]);
  const [saved, setSaved] = useState<ProgramComparisonSummary[]>([]);
  const [firstId, setFirstId] = useState('');
  const [secondId, setSecondId] = useState('');
  const [overlayCourse, setOverlayCourse] = useState('');
  const [detail, setDetail] = useState<ProgramComparisonResponse | null>(null);
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
      const [programRows, comparisons] = await Promise.all([
        getClient().listAuditablePrograms(),
        getClient().listProgramComparisons(),
      ]);
      if (seq !== requestSeq.current) return;
      setPrograms(programRows);
      setSaved(comparisons);
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

  const compare = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const row = await getClient().createProgramComparison({
        programIds: [firstId, secondId],
        addCourseCodes: overlayCourse.trim() ? [overlayCourse.trim()] : [],
      });
      setDetail(row);
      setNotice('Program comparison saved.');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="stack">
      <p>Mention overlap is not saved credit.</p>
      {loading && <p>Loading program comparisons...</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={() => void load()}>
            Retry program comparisons
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      {!loading && !error && saved.length === 0 && <p>No saved program comparisons.</p>}
      <ul>
        {saved.map((row) => (
          <li key={row.comparisonId}>
            {row.mentionKind} {row.sharingPolicy}
            {row.creditSavings == null ? ' no credit savings' : ''}
          </li>
        ))}
      </ul>
      <label>
        First program
        <select value={firstId} onChange={(event) => setFirstId(event.target.value)}>
          <option value="">Select a program</option>
          {programs.map((program) => (
            <option key={program.programId} value={program.programId}>
              {program.title}
            </option>
          ))}
        </select>
      </label>
      <label>
        Second program
        <select value={secondId} onChange={(event) => setSecondId(event.target.value)}>
          <option value="">Select a program</option>
          {programs.map((program) => (
            <option key={program.programId} value={program.programId}>
              {program.title}
            </option>
          ))}
        </select>
      </label>
      <label>
        Overlay course
        <input value={overlayCourse} onChange={(event) => setOverlayCourse(event.target.value)} />
      </label>
      <button
        type="button"
        disabled={busy || loading || !firstId || !secondId || firstId === secondId}
        onClick={() => void compare()}
      >
        Compare programs
      </button>
      {detail && (
        <>
          <p>Sharing policy is {detail.sharingPolicy}.</p>
          {detail.creditSavings == null && <p>No credit savings are claimed.</p>}
          {(detail.sharedMentions ?? []).length > 0 && (
            <p>Shared mentions: {(detail.sharedMentions ?? []).join(', ')}</p>
          )}
          {detail.rankingBlockedReason && <p>{detail.rankingBlockedReason}</p>}
        </>
      )}
    </section>
  );
}
