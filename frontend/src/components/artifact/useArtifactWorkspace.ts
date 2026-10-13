import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import type {
  ArtifactRun,
  ArtifactRunEvent,
  ArtifactRunRequest,
  NativeArtifactKind,
  NativeArtifactRecord,
} from '../../api/types';
import { errorMessage } from '../../lib/errors';
import { useMountedRef } from '../../lib/useMountedRef';
import type { Attachment } from '../chat/model';

const POLL_MS = 1200;
const FINISHED = ['succeeded', 'failed', 'cancelled'];

/** An artifact that belongs to a chat; `revisionId` is the exact version the chat produced or last saved. */
export interface ArtifactLink {
  artifactId: string;
  revisionId?: string;
  kind: NativeArtifactKind;
  title: string;
}

/** A generation job started from a chat. */
export interface RunItem {
  localId: string;
  chatKey: string;
  kind: NativeArtifactKind;
  label: string;
  run: ArtifactRun;
  events: ArtifactRunEvent[];
  pollError: string | null;
  /** The existing artifact this job revises. */
  revises?: { artifactId: string; title: string };
}

export interface StartRunInput extends ArtifactRunRequest {
  chatKey: string;
  label: string;
  revises?: RunItem['revises'];
}

const isActive = (item: RunItem) => !FINISHED.includes(item.run.state);

/** Revisions a run published, de-duplicated across the run record and its events; ids without a revision last. */
export function runOutputs(run: ArtifactRun, events: ArtifactRunEvent[]) {
  const pinned = new Map(
    [...(run.artifactRevisions ?? []), ...events.flatMap((event) => event.artifactRevisions ?? [])].map((item) => [
      `${item.artifactId}:${item.revisionId}`,
      item as { artifactId: string; revisionId?: string },
    ]),
  );
  const pinnedIds = new Set([...pinned.values()].map((item) => item.artifactId));
  const legacy = [...new Set([...(run.artifactIds ?? []), ...events.flatMap((event) => event.artifactIds ?? [])])]
    .filter((id) => !pinnedIds.has(id))
    .map((artifactId) => ({ artifactId }) as { artifactId: string; revisionId?: string });
  return [...pinned.values(), ...legacy];
}

function storageKey(workspaceKey: string) {
  return `advisor-chat-artifacts:${workspaceKey}`;
}

function loadLinks(workspaceKey: string): Record<string, ArtifactLink[]> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(storageKey(workspaceKey)) ?? '{}');
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, ArtifactLink[]>) : {};
  } catch {
    return {};
  }
}

/**
 * The artifacts made in each chat, the generation jobs still running, and the one artifact open
 * in the side canvas. Which artifacts belong to which chat is remembered in this browser; artifacts
 * a chat message referenced are always included.
 */
export function useArtifactWorkspace(workspaceKey: string, chatKey: string | null, referenced: Attachment[]) {
  const [links, setLinks] = useState<Record<string, ArtifactLink[]>>(() => loadLinks(workspaceKey));
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [record, setRecord] = useState<NativeArtifactRecord | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const runsRef = useRef(runs);
  runsRef.current = runs;
  const mounted = useMountedRef();

  useEffect(() => {
    try {
      localStorage.setItem(storageKey(workspaceKey), JSON.stringify(links));
    } catch {
      /* the association is a convenience; artifacts stay in the library */
    }
  }, [links, workspaceKey]);

  const link = useCallback((key: string, item: ArtifactLink) => {
    setLinks((current) => ({
      ...current,
      [key]: [item, ...(current[key] ?? []).filter((existing) => existing.artifactId !== item.artifactId)],
    }));
  }, []);

  const updateRun = (localId: string, change: (item: RunItem) => RunItem) =>
    setRuns((current) => current.map((item) => (item.localId === localId ? change(item) : item)));

  /** Link a finished run's artifacts to its chat, then retire the run card if anything was published. */
  const settle = async (item: RunItem) => {
    const outputs = runOutputs(item.run, item.events);
    for (const output of outputs) {
      try {
        const opened = await getClient().getArtifact(output.artifactId, output.revisionId);
        link(item.chatKey, {
          artifactId: output.artifactId,
          revisionId: output.revisionId,
          kind: opened.kind,
          title: opened.title,
        });
      } catch {
        link(item.chatKey, {
          artifactId: output.artifactId,
          revisionId: output.revisionId,
          kind: item.kind,
          title: item.revises?.title ?? item.label,
        });
      }
    }
    if (outputs.length && mounted.current)
      setRuns((current) => current.filter((existing) => existing.localId !== item.localId));
  };

  // Poll every running job until it finishes; a failed poll is shown and retried.
  const hasActive = runs.some(isActive);
  useEffect(() => {
    if (!hasActive) return;
    let stopped = false;
    let timer = 0;
    const pollOne = async (item: RunItem) => {
      try {
        const afterEventId = item.events.reduce((max, event) => Math.max(max, event.eventId), 0);
        const [latest, newEvents] = await Promise.all([
          getClient().getArtifactRun(item.run.runId),
          getClient().listArtifactRunEvents(item.run.runId, afterEventId),
        ]);
        if (stopped) return;
        const current = runsRef.current.find((existing) => existing.localId === item.localId);
        if (!current) return; // dismissed while polling
        const seen = new Set(current.events.map((event) => event.eventId));
        const next: RunItem = {
          ...current,
          run: FINISHED.includes(current.run.state) ? current.run : latest,
          events: [...current.events, ...newEvents.filter((event) => !seen.has(event.eventId))],
          pollError: null,
        };
        updateRun(item.localId, () => next);
        if (next.run.state === 'succeeded') await settle(next);
      } catch (cause) {
        if (!stopped) updateRun(item.localId, (current) => ({ ...current, pollError: errorMessage(cause) }));
      }
    };
    const loop = async () => {
      await Promise.all(runsRef.current.filter(isActive).map(pollOne));
      if (!stopped) timer = window.setTimeout(() => void loop(), POLL_MS);
    };
    timer = window.setTimeout(() => void loop(), POLL_MS);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
    // The loop reads the latest runs through runsRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasActive]);

  /** Queue a generation job. Throws (with the HTTP status) if the server refuses it. */
  const startRun = async ({ chatKey: key, label, revises, ...request }: StartRunInput) => {
    const run = await getClient().createArtifactRun(request);
    const item: RunItem = {
      localId: crypto.randomUUID(),
      chatKey: key,
      kind: request.kind,
      label,
      run,
      events: [],
      pollError: null,
      revises,
    };
    setRuns((current) => [item, ...current]);
    setPanelOpen(true);
    if (run.state === 'succeeded') void settle(item);
  };

  const cancelRun = async (localId: string) => {
    const item = runsRef.current.find((existing) => existing.localId === localId);
    if (!item) return;
    try {
      const run = await getClient().cancelArtifactRun(item.run.runId);
      updateRun(localId, (current) => ({ ...current, run }));
    } catch (cause) {
      updateRun(localId, (current) => ({ ...current, pollError: errorMessage(cause) }));
    }
  };

  const dismissRun = (localId: string) =>
    setRuns((current) => current.filter((existing) => existing.localId !== localId));

  /** Open an artifact (optionally at an exact revision) in the canvas and link it to the open chat. */
  const openArtifact = async (artifactId: string, revisionId?: string) => {
    const result = await getClient().getArtifact(artifactId, revisionId);
    if (revisionId && result.revisionId !== revisionId)
      throw new Error('That exact version could not be opened. Refresh the list and try again.');
    if (!mounted.current) return result;
    setRecord(result);
    setPanelOpen(true);
    if (chatKey)
      link(chatKey, {
        artifactId: result.artifactId,
        revisionId: result.revisionId,
        kind: result.kind,
        title: result.title,
      });
    return result;
  };

  /** Show a record the user just created, imported, or saved. */
  const showRecord = (next: NativeArtifactRecord, key: string | null = chatKey) => {
    setRecord(next);
    setPanelOpen(true);
    if (key)
      link(key, { artifactId: next.artifactId, revisionId: next.revisionId, kind: next.kind, title: next.title });
  };

  const chatLinks = useMemo(() => {
    const own = chatKey ? (links[chatKey] ?? []) : [];
    const known = new Set(own.map((item) => item.artifactId));
    const fromMessages: ArtifactLink[] = [];
    for (const ref of [...referenced].reverse()) {
      if (known.has(ref.artifactId)) continue;
      known.add(ref.artifactId);
      fromMessages.push({ artifactId: ref.artifactId, revisionId: ref.revisionId, kind: ref.kind, title: ref.title });
    }
    return [...own, ...fromMessages];
  }, [links, chatKey, referenced]);

  const chatRuns = runs.filter((item) => item.chatKey === chatKey);

  return {
    links: chatLinks,
    runs: chatRuns,
    record,
    panelOpen,
    setPanelOpen,
    startRun,
    cancelRun,
    dismissRun,
    openArtifact,
    showRecord,
    closeArtifact: () => setRecord(null),
  };
}

export type ArtifactWorkspace = ReturnType<typeof useArtifactWorkspace>;
