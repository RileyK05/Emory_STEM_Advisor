import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import type { Citation, RetrievalTrace, SourceDoc } from '../api/types';
import { getClient } from '../api/client';

export type MessageStatus = 'sending' | 'done' | 'error';

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant';
  status: MessageStatus;
  /** Present on assistant answer messages. */
  text?: string;
  /** Present on assistant refusal messages (distinct, non-error state). */
  refusalReason?: string;
  citations?: Citation[];
  trace?: RetrievalTrace;
  errorText?: string;
}

interface HighlightTarget {
  sourceId: string;
  chunkId: string;
  nonce: number;
}

interface AppState {
  messages: ChatMessage[];
  sending: boolean;
  debugMode: boolean;
  toggleDebugMode: () => void;
  sendQuery: (text: string) => void;
  sources: SourceDoc[];
  highlight: HighlightTarget | null;
  showCitationSource: (citation: Citation) => void;
}

const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used within AppProvider');
  return ctx;
}

let nextMessageId = 1;

export function AppProvider({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [debugMode, setDebugMode] = useState(false);
  const [sources, setSources] = useState<SourceDoc[]>([]);
  const [highlight, setHighlight] = useState<HighlightTarget | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    getClient()
      .listSources()
      .then((list) => {
        if (mounted.current) setSources(list);
      })
      .catch(() => {
        /* mock client never rejects; http stub does before backend lands */
      });
    return () => {
      mounted.current = false;
    };
  }, []);

  const toggleDebugMode = useCallback(() => setDebugMode((v) => !v), []);

  const showCitationSource = useCallback((citation: Citation) => {
    const source = sources.find((s) => s.name === citation.sourceName);
    if (!source) return;
    setHighlight({ sourceId: source.id, chunkId: citation.chunkId, nonce: Date.now() });
  }, [sources]);

  const sendQuery = useCallback(
    (text: string) => {
      const query = text.trim();
      if (!query || sending) return;
      const userMsg: ChatMessage = { id: nextMessageId++, role: 'user', status: 'done', text: query };
      const assistantId = nextMessageId++;
      const assistantMsg: ChatMessage = { id: assistantId, role: 'assistant', status: 'sending' };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setSending(true);

      getClient()
        .query({ query })
        .then((res) => {
          setMessages((prev) =>
            prev.map((m) => {
              if (m.id !== assistantId) return m;
              if (res.type === 'refusal') {
                return { ...m, status: 'done', refusalReason: res.reason, trace: res.trace };
              }
              return { ...m, status: 'done', text: res.answer, citations: res.citations, trace: res.trace };
            }),
          );
        })
        .catch((err: unknown) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, status: 'error', errorText: err instanceof Error ? err.message : String(err) }
                : m,
            ),
          );
        })
        .finally(() => setSending(false));
    },
    [sending],
  );

  const value = useMemo<AppState>(
    () => ({
      messages,
      sending,
      debugMode,
      toggleDebugMode,
      sendQuery,
      sources,
      highlight,
      showCitationSource,
    }),
    [messages, sending, debugMode, toggleDebugMode, sendQuery, sources, highlight, showCitationSource],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
