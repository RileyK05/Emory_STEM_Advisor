import { useState } from 'react';
import {
  approvePromptVersion,
  promotePromptVersion,
  registerPromptVersion,
  savePromptFixture,
} from '../api/backoffice';
import type { PromptPromotionResponse, PromptVersionResponse } from '../api/types';
import { errorMessage } from '../lib/errors';

export function PromptPromotionPanel() {
  const [name, setName] = useState('');
  const [templateText, setTemplateText] = useState('');
  const [suiteName, setSuiteName] = useState('');
  const [fixtureText, setFixtureText] = useState('');
  const [version, setVersion] = useState<PromptVersionResponse | null>(null);
  const [promotion, setPromotion] = useState<PromptPromotionResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const row = await registerPromptVersion({ name: name.trim(), templateText });
      setVersion(row);
      setNotice('Prompt version saved.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!version) return;
    setBusy(true);
    setError(null);
    try {
      const row = await approvePromptVersion(version.versionId);
      setVersion(row);
      setNotice('Prompt review approved.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const fixture = async () => {
    setBusy(true);
    setError(null);
    try {
      await savePromptFixture({ suiteName: suiteName.trim(), fixtureText });
      setNotice('Eval fixture saved.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  const promote = async () => {
    if (!version) return;
    setBusy(true);
    setError(null);
    try {
      const row = await promotePromptVersion(version.versionId, { suiteName: suiteName.trim() });
      setPromotion(row);
      setNotice('Prompt promotion published.');
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="stack">
      {!version && <p>No prompt version is saved.</p>}
      <p>
        Promoting a version named <code>grounded-answer</code> replaces the built-in system prompt for grounded answers.
        Other names are stored for review only.
      </p>
      {error && (
        <p role="alert">
          {error}{' '}
          <button type="button" onClick={() => void promote()}>
            Retry prompt promotion
          </button>
        </p>
      )}
      {notice && <p>{notice}</p>}
      <label>
        Prompt name
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <label>
        Prompt text
        <textarea value={templateText} onChange={(event) => setTemplateText(event.target.value)} />
      </label>
      <button type="button" disabled={busy || !name.trim() || !templateText.trim()} onClick={() => void save()}>
        Save prompt version
      </button>
      {version && (
        <p>
          Version {version.versionId} is {version.reviewState}.
        </p>
      )}
      <button type="button" disabled={busy || !version} onClick={() => void approve()}>
        Approve prompt review
      </button>
      <label>
        Suite name
        <input value={suiteName} onChange={(event) => setSuiteName(event.target.value)} />
      </label>
      <label>
        Fixture text
        <textarea value={fixtureText} onChange={(event) => setFixtureText(event.target.value)} />
      </label>
      <button type="button" disabled={busy || !suiteName.trim() || !fixtureText.trim()} onClick={() => void fixture()}>
        Save eval fixture
      </button>
      <button type="button" disabled={busy || !version || !suiteName.trim()} onClick={() => void promote()}>
        Promote prompt
      </button>
      {promotion && <p>Promotion status {promotion.status}.</p>}
    </section>
  );
}
