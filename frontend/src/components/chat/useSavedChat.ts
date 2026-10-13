import { useEffect, useMemo, useRef, useState } from 'react';
import { getClient } from '../../api/client';
import { errorMessage } from '../../lib/errors';
import type { ConversationRecord, ConversationSummary, ConversationTurnRequest } from '../../api/types';
import { useApp } from '../../state/AppContext';
import type { SourceScope } from '../../state/AppContext';
import { useMountedRef } from '../../lib/useMountedRef';
import { applyStreamEvent, emptyPreview, entriesFromTurns } from './model';
import type { Attachment, ChatController, StreamPreview } from './model';

const NEW_DRAFT_KEY = '__new_conversation__';
const CONVERSATION_PAGE_SIZE = 200;

/** The source scope the conversation's latest question used, if a loaded turn shows it. */
function savedScope(record: ConversationRecord): SourceScope | undefined {
  for (let index = record.turns.length - 1; index >= 0; index -= 1) {
    const evidence = record.turns[index].evidence;
    if ('effective' in evidence) return evidence.effective.sourceIds;
  }
  return record.lastSequence === 0 ? null : undefined;
}

/**
 * Saved conversations over the HTTP API. Every chat is persisted; answers stream as
 * jobs whose provisional text is replaced by the saved, cited turn. A request is never
 * replayed by the browser: reconnecting only reads stored events.
 */
export function useSavedChat(): ChatController {
  const { scope, setScope } = useApp();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversation, setConversation] = useState<ConversationRecord | null>(null);
  const [turnsAfter, setTurnsAfter] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loadingList, setLoadingList] = useState(true);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [busyIds, setBusyIds] = useState<Set<string>>(() => new Set());
  const [abandoningIds, setAbandoningIds] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [streamPreview, setStreamPreview] = useState<StreamPreview | null>(null);
  const alive = useMountedRef();
  const activeConversation = useRef<string | null>(null);
  const viewEpoch = useRef(0);
  // The conversation-list load is its own generation: opening or starting a chat
  // must not cancel an in-flight list read and leave `loadingList` stuck true.
  const listEpoch = useRef(0);
  const busyRef = useRef(new Map<string, symbol>());
  const abandoningRef = useRef(new Set<string>());
  const creatingRef = useRef(false);
  const eventCursor = useRef<Record<string, number>>({});
  const pollingRequests = useRef(new Map<string, { epoch: number; promise: Promise<void> }>());
  const settledUnknownRequests = useRef(new Set<string>());
  const loadConversationRef = useRef<((id: string, epoch: number) => Promise<void>) | null>(null);
  // Whether the user changed the scope since it was last saved with a turn. When unchanged,
  // a follow-up omits sourceIds so the server inherits the conversation's saved scope.
  const scopeChanged = useRef(true);

  /** False once the chat unmounted or the user moved to another conversation since `epoch`. */
  const isCurrent = (id: string | null, epoch: number) =>
    alive.current && activeConversation.current === id && viewEpoch.current === epoch;

  const markBusy = (id: string) => {
    const owner = Symbol(id);
    busyRef.current.set(id, owner);
    setBusyIds((current) => new Set(current).add(id));
    return owner;
  };
  const releaseBusy = (id: string, owner: symbol | undefined) => {
    if (!owner || busyRef.current.get(id) !== owner) return;
    busyRef.current.delete(id);
    if (alive.current)
      setBusyIds((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
  };

  const draftKey = conversationId ?? NEW_DRAFT_KEY;
  const draft = drafts[draftKey] ?? '';

  const pollStream = (id: string, requestId: string, epoch: number): Promise<void> => {
    const key = `${id}:${requestId}`;
    const existing = pollingRequests.current.get(key);
    if (existing?.epoch === epoch) return existing.promise;
    if (settledUnknownRequests.current.has(key)) return Promise.resolve();
    let promise!: Promise<void>;
    promise = (async () => {
      await Promise.resolve();
      const ownsPoll = () => pollingRequests.current.get(key)?.promise === promise;
      setStreamPreview((current) => {
        if (current?.requestId === requestId) return current;
        eventCursor.current[key] = 0;
        return emptyPreview(requestId);
      });
      try {
        while (isCurrent(id, epoch) && ownsPoll()) {
          try {
            const afterEventId = eventCursor.current[key] ?? 0;
            const page = await getClient().getConversationTurnEvents(id, requestId, afterEventId);
            if (!isCurrent(id, epoch) || !ownsPoll()) return;
            eventCursor.current[key] = Math.max(afterEventId, page.nextEventId);
            for (const event of page.events) {
              setStreamPreview((current) => applyStreamEvent(current, requestId, event));
            }
            if (page.done) {
              if (page.requestState === 'pending') {
                settledUnknownRequests.current.add(key);
                setNotice(
                  'This answer stopped before it finished, and it will not be retried automatically. Stop it, then ask again.',
                );
              }
              setStreamPreview((current) => (current?.requestId === requestId ? null : current));
              await loadConversationRef.current?.(id, epoch);
              void getClient()
                .listConversations()
                .then((items) => {
                  if (isCurrent(id, epoch)) setConversations(items);
                })
                .catch(() => undefined);
              return;
            }
          } catch {
            if (!isCurrent(id, epoch) || !ownsPoll()) return;
            setNotice('Reconnecting to the answer in progress… Your question will not be sent twice.');
          }
          await new Promise((resolve) => window.setTimeout(resolve, 750));
        }
      } finally {
        if (ownsPoll()) pollingRequests.current.delete(key);
      }
    })();
    pollingRequests.current.set(key, { epoch, promise });
    return promise;
  };

  useEffect(() => {
    const epoch = ++listEpoch.current;
    void getClient()
      .listConversations()
      .then((items) => {
        if (!alive.current || listEpoch.current !== epoch) return;
        setConversations(items);
        setLoadingList(false);
        if (items.length) void open(items[0].conversationId);
      })
      .catch((cause: unknown) => {
        if (!alive.current || listEpoch.current !== epoch) return;
        setError(errorMessage(cause));
        setLoadingList(false);
      });
    return () => {
      viewEpoch.current += 1;
      listEpoch.current += 1;
    };
    // The initial read is intentionally once per mounted workspace.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadConversation = async (id: string, epoch: number) => {
    setLoadingConversation(true);
    try {
      let record = await getClient().getConversation(id, 0, CONVERSATION_PAGE_SIZE);
      let after = Math.max(0, record.lastSequence - CONVERSATION_PAGE_SIZE);
      if (after > 0) {
        record = await getClient().getConversation(id, after, CONVERSATION_PAGE_SIZE);
        // A conversation can advance while pages are being fetched. Make one
        // bounded correction so reopen exposes the latest turn/pending user turn.
        if (record.turns[record.turns.length - 1]?.sequence !== record.lastSequence) {
          after = Math.max(0, record.lastSequence - CONVERSATION_PAGE_SIZE);
          record = await getClient().getConversation(id, after, CONVERSATION_PAGE_SIZE);
        }
      }
      if (isCurrent(id, epoch)) {
        setConversation(record);
        setTurnsAfter(after);
        if (!scopeChanged.current) {
          const saved = savedScope(record);
          if (saved !== undefined) setScope(saved);
        }
        const pendingRequestId = record.pendingRequestId;
        if (pendingRequestId) void pollStream(id, pendingRequestId, epoch);
        else setStreamPreview(null);
      }
    } catch (cause) {
      if (isCurrent(id, epoch)) setError(errorMessage(cause));
    } finally {
      if (isCurrent(id, epoch)) setLoadingConversation(false);
    }
  };
  loadConversationRef.current = loadConversation;

  const loadEarlier = async () => {
    if (!conversationId || !conversation || turnsAfter <= 0 || loadingConversation) return;
    const id = conversationId;
    const epoch = viewEpoch.current;
    const after = Math.max(0, turnsAfter - CONVERSATION_PAGE_SIZE);
    setLoadingConversation(true);
    setError(null);
    try {
      const page = await getClient().getConversation(id, after, CONVERSATION_PAGE_SIZE);
      if (!isCurrent(id, epoch)) return;
      const existingBySequence = new Map(conversation.turns.map((turn) => [turn.sequence, turn]));
      for (const turn of page.turns) existingBySequence.set(turn.sequence, turn);
      setConversation({ ...page, turns: [...existingBySequence.values()].sort((a, b) => a.sequence - b.sequence) });
      setTurnsAfter(after);
    } catch (cause) {
      if (isCurrent(id, epoch)) setError(errorMessage(cause));
    } finally {
      if (isCurrent(id, epoch)) setLoadingConversation(false);
    }
  };

  const open = async (id: string) => {
    const epoch = ++viewEpoch.current;
    activeConversation.current = id;
    scopeChanged.current = false;
    setStreamPreview(null);
    setConversationId(id);
    setConversation(null);
    setTurnsAfter(0);
    setError(null);
    setNotice(null);
    await loadConversation(id, epoch);
  };

  const refreshList = async () => {
    const epoch = ++listEpoch.current;
    setLoadingList(true);
    setError(null);
    try {
      const items = await getClient().listConversations();
      if (alive.current && listEpoch.current === epoch) setConversations(items);
    } catch (cause) {
      if (alive.current && listEpoch.current === epoch) setError(errorMessage(cause));
    } finally {
      if (alive.current && listEpoch.current === epoch) setLoadingList(false);
    }
  };

  const startNew = () => {
    ++viewEpoch.current;
    activeConversation.current = null;
    // A new chat keeps the visible scope and sends it explicitly with its first question.
    scopeChanged.current = true;
    setStreamPreview(null);
    setConversationId(null);
    setConversation(null);
    setTurnsAfter(0);
    setError(null);
    setNotice(null);
    // An in-flight conversation load was cancelled by the epoch bump above and
    // will not clear its flags; do it here so the panel never sticks busy.
    setLoadingConversation(false);
  };

  /** The open conversation, creating it first if this is a new chat. Null if it changed underneath us. */
  const ensureConversation = async (
    titleText: string,
  ): Promise<{ id: string; record: ConversationRecord | null; epoch: number } | null> => {
    if (conversationId) return { id: conversationId, record: conversation, epoch: viewEpoch.current };
    if (creatingRef.current) return null;
    creatingRef.current = true;
    setCreatingConversation(true);
    let epoch = viewEpoch.current;
    try {
      const title = titleText.replace(/\s+/g, ' ').trim().slice(0, 120) || 'New chat';
      const created = await getClient().createConversation({ title });
      if (!alive.current || viewEpoch.current !== epoch || activeConversation.current !== null) return null;
      const id = created.conversationId;
      activeConversation.current = id;
      scopeChanged.current = true;
      setConversationId(id);
      setConversations((items) => [created, ...items.filter((item) => item.conversationId !== id)]);
      setDrafts((current) => {
        const latestDraft = current[NEW_DRAFT_KEY] ?? '';
        return { ...current, [id]: latestDraft, [NEW_DRAFT_KEY]: '' };
      });
      epoch = ++viewEpoch.current;
      const record = await getClient().getConversation(id, 0, CONVERSATION_PAGE_SIZE);
      if (!alive.current || viewEpoch.current !== epoch || activeConversation.current !== id) return null;
      setConversation(record);
      return { id, record, epoch };
    } catch (cause) {
      if (alive.current && viewEpoch.current === epoch) setError(errorMessage(cause));
      return null;
    } finally {
      creatingRef.current = false;
      if (alive.current) setCreatingConversation(false);
    }
  };

  const send = async (attachments: Attachment[]) => {
    const query = draft.trim();
    if (!query) return;
    setError(null);
    setNotice(null);
    const target = await ensureConversation(query);
    if (!target) return;
    const { id, record, epoch } = target;
    // A new chat's draft moved to its id when the chat was created.
    const keyForDraft = id;
    if (busyRef.current.has(id) || abandoningRef.current.has(id) || record?.pendingRequestId) return;
    if (!record) {
      setError('This chat is still loading. Try again in a moment.');
      return;
    }

    const requestId = crypto.randomUUID();
    const request: ConversationTurnRequest = {
      query,
      requestId,
      expectedSequence: record.lastSequence,
      mode: 'advising',
      // A first question over all sources needs no explicit scope; a follow-up with an unchanged
      // scope omits it so the server inherits the conversation's saved scope.
      ...(scopeChanged.current && !(record.lastSequence === 0 && scope === null) ? { sourceIds: scope } : {}),
      ...(attachments.length
        ? { artifactReferences: attachments.map(({ artifactId, revisionId }) => ({ artifactId, revisionId })) }
        : {}),
    };
    const busyOwner = markBusy(id);
    const sentText = draft;
    try {
      await getClient().startConversationTurnStream(id, request);
      scopeChanged.current = false;
      if (!isCurrent(id, epoch)) return;
      await loadConversation(id, epoch);
      await pollStream(id, requestId, epoch);
      if (isCurrent(id, epoch)) {
        const latest = await getClient().getConversation(id, request.expectedSequence, CONVERSATION_PAGE_SIZE);
        if (!isCurrent(id, epoch)) return;
        const terminal = latest.turns.find((turn) => turn.requestId === requestId && turn.role === 'assistant');
        if (terminal?.status === 'answer' || terminal?.status === 'refusal') {
          setDrafts((current) => (current[keyForDraft] === sentText ? { ...current, [keyForDraft]: '' } : current));
        } else if (terminal?.status === 'failed' || terminal?.status === 'abandoned') {
          setNotice(
            terminal.status === 'abandoned'
              ? 'Your turn was stopped; your draft is kept.'
              : 'The answer could not be produced; your draft is kept.',
          );
        }
      }
    } catch (cause) {
      if (!isCurrent(id, epoch)) return;
      setError(errorMessage(cause));
      setNotice(
        'Your message was not sent again. The chat was refreshed so you can see where it stands; your draft is kept.',
      );
      await loadConversation(id, epoch);
      await refreshList();
    } finally {
      releaseBusy(id, busyOwner);
    }
  };

  const abandon = async () => {
    if (!conversationId || !conversation?.pendingRequestId || abandoningRef.current.has(conversationId)) return;
    const id = conversationId;
    const requestId = conversation.pendingRequestId;
    const epoch = viewEpoch.current;
    const pendingOwner = busyRef.current.get(id);
    abandoningRef.current.add(id);
    setAbandoningIds((current) => new Set(current).add(id));
    setError(null);
    try {
      await getClient().abandonConversationTurn(id, requestId);
      if (!isCurrent(id, epoch)) return;
      // Stop the old poll from publishing a late event after the terminal read.
      pollingRequests.current.delete(`${id}:${requestId}`);
      const terminalEpoch = ++viewEpoch.current;
      releaseBusy(id, pendingOwner);
      setStreamPreview(null);
      setNotice('Stopped. If that answer finishes later, it will be discarded.');
      await loadConversation(id, terminalEpoch);
      await refreshList();
    } catch (cause) {
      if (!isCurrent(id, epoch)) return;
      setError(errorMessage(cause));
      await loadConversation(id, epoch);
    } finally {
      abandoningRef.current.delete(id);
      if (alive.current)
        setAbandoningIds((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
    }
  };

  const entries = useMemo(() => entriesFromTurns(conversation?.turns ?? []), [conversation]);
  const currentBusy = creatingConversation || (conversationId ? busyIds.has(conversationId) : false);
  const abandoning = conversationId ? abandoningIds.has(conversationId) : false;
  const title = conversation?.title ?? conversations.find((item) => item.conversationId === conversationId)?.title;

  return {
    saved: true,
    conversations,
    activeId: conversationId,
    title: title ?? null,
    loadingList,
    loadingConversation,
    entries,
    pending: conversation?.pendingRequestId ? { preview: streamPreview, abandoning } : null,
    abandon: () => void abandon(),
    hasEarlier: turnsAfter > 0,
    loadEarlier: () => void loadEarlier(),
    draft,
    setDraft: (text) => setDrafts((current) => ({ ...current, [draftKey]: text })),
    busy: currentBusy || abandoning || loadingConversation || !!conversation?.pendingRequestId,
    send: (attachments) => void send(attachments),
    changeScope: (next) => {
      scopeChanged.current = true;
      setScope(next);
    },
    open: (id) => void open(id),
    startNew,
    ensureSaved: async (titleText) => (await ensureConversation(titleText))?.id ?? null,
    refreshList: () => void refreshList(),
    error,
    notice,
    dismiss: () => {
      setError(null);
      setNotice(null);
    },
  };
}
