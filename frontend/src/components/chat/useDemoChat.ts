import { useRef, useState } from 'react';
import { getClient } from '../../api/client';
import { errorMessage } from '../../lib/errors';
import { useApp } from '../../state/AppContext';
import type { AssistantEntry, ChatController, ChatEntry } from './model';

let nextKey = 1;

/** One unsaved chat over the stateless query API (mock mode). Nothing is persisted. */
export function useDemoChat(): ChatController {
  const { scope, setScope } = useApp();
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  // Answers that arrive after "New chat" belong to a cleared transcript.
  const generation = useRef(0);

  const send = () => {
    const query = draft.trim();
    if (!query || sending) return;
    const epoch = generation.current;
    const assistantKey = `demo-${nextKey++}`;
    const base: AssistantEntry = {
      key: assistantKey,
      role: 'assistant',
      kind: 'thinking',
      text: '',
      citations: [],
      context: [],
      question: query,
      scope,
    };
    setEntries((current) => [
      ...current,
      { key: `demo-${nextKey++}`, role: 'user', text: query, attachments: [] },
      base,
    ]);
    setDraft('');
    setSending(true);
    const settle = (entry: AssistantEntry) => {
      if (generation.current !== epoch) return;
      setEntries((current) => current.map((item) => (item.key === assistantKey ? entry : item)));
    };
    getClient()
      .query({ query, ...(scope !== null ? { sourceIds: scope } : {}) })
      .then((res) => {
        if (res.type === 'refusal')
          settle({
            ...base,
            kind: 'refusal',
            text: res.reason,
            context: res.trace?.contextBlock ?? [],
            trace: res.trace,
          });
        else
          settle({
            ...base,
            kind: 'answer',
            text: res.answer,
            citations: res.citations,
            context: res.trace.contextBlock,
            research: res.research,
            applicability: res.applicability,
            trace: res.trace,
          });
      })
      .catch((cause: unknown) => settle({ ...base, kind: 'error', text: errorMessage(cause) }))
      .finally(() => {
        if (generation.current === epoch) setSending(false);
      });
  };

  return {
    saved: false,
    conversations: [],
    activeId: null,
    title: null,
    loadingList: false,
    loadingConversation: false,
    entries,
    pending: null,
    abandon: () => undefined,
    hasEarlier: false,
    loadEarlier: () => undefined,
    draft,
    setDraft,
    busy: sending,
    send: (_attachments, onAccepted) => {
      send();
      onAccepted?.();
    },
    changeScope: setScope,
    open: () => undefined,
    startNew: () => {
      generation.current += 1;
      setEntries([]);
      setSending(false);
    },
    ensureSaved: async () => null,
    refreshList: () => undefined,
    error: null,
    notice: null,
    dismiss: () => undefined,
  };
}
