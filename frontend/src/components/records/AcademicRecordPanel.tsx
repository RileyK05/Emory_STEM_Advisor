import { useCallback, useEffect, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type { AcademicRecordAttemptItem, AcademicRecordImportBatchResponse } from '../../api/types';
import { errorMessage } from '../../lib/errors';

export function AcademicRecordPanel() {
  const [attempts, setAttempts] = useState<AcademicRecordAttemptItem[]>([]);
  const [revision, setRevision] = useState(1);
  const [course, setCourse] = useState('');
  const [term, setTerm] = useState('');
  const [grade, setGrade] = useState('');
  const [csv, setCsv] = useState('');
  const [batch, setBatch] = useState<AcademicRecordImportBatchResponse | null>(null);
  const [rowGrade, setRowGrade] = useState('');
  const [historical, setHistorical] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const requestSeq = useRef(0);
  const importDirty = batch !== null && rowGrade !== String(batch.parsedRows?.[0]?.grade ?? '');

  const load = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    try {
      const row = await getClient().getAcademicRecord();
      if (seq !== requestSeq.current) return;
      setAttempts(row.attempts ?? []);
      setRevision(row.record.recordRevision);
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

  const saveAttempt = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await getClient().upsertAcademicAttempt({
        submittedCourseCode: course.trim(),
        term: term.trim(),
        grade: grade.trim(),
        provenance: 'student_manual',
      });
      setNotice('Course attempt saved.');
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const preview = async () => {
    setBusy(true);
    setError(null);
    try {
      const row = await getClient().previewAcademicImport({
        filename: 'courses.csv',
        rawContent: csv,
        expectedRecordRevision: revision,
      });
      setBatch(row);
      setRowGrade(String(row.parsedRows?.[0]?.grade ?? ''));
      setNotice('Import preview ready.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const saveRows = async () => {
    if (!batch) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await getClient().updateAcademicImport(batch.batchId, {
        expectedRecordRevision: revision,
        corrections: [{ rowIndex: 0, grade: rowGrade }],
      });
      setBatch(updated);
      setNotice('Import rows saved.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const commit = async () => {
    if (!batch?.previewDigest || importDirty) return;
    setBusy(true);
    setError(null);
    try {
      const result = await getClient().commitAcademicImport(batch.batchId, {
        expectedRecordRevision: revision,
        previewDigest: batch.previewDigest,
      });
      setNotice(`Import committed ${result.attemptsImported} attempt.`);
      await load();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const showPrior = async () => {
    setBusy(true);
    setError(null);
    try {
      const prior = await getClient().getAcademicRecordRevision(Math.max(1, revision - 1));
      const grades = (prior.attempts ?? []).map((row) => `${row.submittedCourseCode} ${row.grade ?? ''}`).join(', ');
      setHistorical(grades || 'No attempts in that revision.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="stack" aria-label="Academic record">
      <h3>Academic record</h3>
      {loading && <p>Loading academic record...</p>}
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={() => void load()}>
            Retry record
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      {!loading && !error && attempts.length === 0 && <p>No course attempts.</p>}
      <ul>
        {attempts.map((attempt) => (
          <li key={attempt.attemptId}>
            {attempt.submittedCourseCode} {attempt.term} {attempt.grade} {attempt.provenance}
          </li>
        ))}
      </ul>
      <label>
        Course code
        <input value={course} onChange={(event) => setCourse(event.target.value)} />
      </label>
      <label>
        Term
        <input value={term} onChange={(event) => setTerm(event.target.value)} />
      </label>
      <label>
        Grade
        <input value={grade} onChange={(event) => setGrade(event.target.value)} />
      </label>
      <button
        type="button"
        disabled={busy || loading || !course.trim() || !term.trim()}
        onClick={() => void saveAttempt()}
      >
        Save attempt
      </button>
      <label>
        CSV import
        <textarea value={csv} onChange={(event) => setCsv(event.target.value)} />
      </label>
      <button type="button" disabled={busy || !csv.trim()} onClick={() => void preview()}>
        Preview import
      </button>
      {batch && (
        <>
          <label>
            Reviewed grade
            <input value={rowGrade} onChange={(event) => setRowGrade(event.target.value)} />
          </label>
          <button type="button" disabled={busy} onClick={() => void saveRows()}>
            Save import rows
          </button>
          {importDirty && <p>Save the reviewed grade before committing the import.</p>}
          <button type="button" disabled={busy || importDirty || !batch.previewDigest} onClick={() => void commit()}>
            Commit import
          </button>
        </>
      )}
      <button type="button" disabled={busy} onClick={() => void showPrior()}>
        Show prior revision
      </button>
      {historical && <p>{historical}</p>}
    </section>
  );
}
