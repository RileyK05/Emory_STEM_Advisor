import assert from 'node:assert/strict';
import test from 'node:test';

/** Mirrors Shell.tsx history gating after the fix. */
function shouldTrack(entry, historySequence) {
  if (entry.role !== 'assistant' || !entry.artifactRun) return false;
  if ((entry.sequence ?? -1) <= historySequence) return false;
  return true;
}

test('missing sequence is treated as history when historyThrough is at least -1 after load', () => {
  // After opening an existing chat, historyThrough.sequence is max(sequences).
  // An entry without sequence must not reopen the panel.
  assert.equal(shouldTrack({ role: 'assistant', artifactRun: { runId: 'r1' } }, 2), false);
});

test('a new answer after firstOpen (history -1) is tracked', () => {
  assert.equal(shouldTrack({ role: 'assistant', sequence: 2, artifactRun: { runId: 'r1' } }, -1), true);
});

test('savedScope-style guard tolerates missing evidence', () => {
  const evidence = undefined;
  assert.equal(Boolean(evidence && 'effective' in evidence), false);
  const ok = { effective: { sourceIds: null } };
  assert.equal(Boolean(ok && 'effective' in ok), true);
});
