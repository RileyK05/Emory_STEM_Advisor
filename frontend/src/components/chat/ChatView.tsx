import { useEffect, useRef } from 'react';
import { CloseIcon } from '../icons';
import { useTheme } from '../../state/ThemeContext';
import { Composer } from './Composer';
import { MessageView, PendingAnswer } from './MessageView';
import { Welcome } from './Welcome';
import type { AdvisorPersona } from '../../api/types';
import type { Attachment, ChatController } from './model';

interface Props {
  chat: ChatController;
  attachments: Attachment[];
  onRemoveAttachment: (attachment: Attachment) => void;
}

export function ChatView({ chat, attachments, onRemoveAttachment }: Props) {
  const bottom = useRef<HTMLDivElement>(null);
  // The voice belongs to the theme at send time, so a later theme switch
  // leaves earlier replies as they were.
  const { theme } = useTheme();
  const persona: AdvisorPersona = theme === 'dark' ? 'dooley' : 'swoop';
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [chat.entries, chat.pending !== null]);

  const suggest = (text: string) => {
    chat.setDraft(text);
    document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus();
  };
  const empty = chat.entries.length === 0 && !chat.pending && !chat.loadingConversation;

  return (
    <main className="chat" aria-label="Chat">
      <header className="chat-header">
        <h2 className="chat-title">{chat.title ?? 'New chat'}</h2>
      </header>
      <div className="chat-scroll">
        <div className="chat-column" aria-busy={chat.loadingConversation}>
          {chat.hasEarlier && (
            <button type="button" className="link-button load-earlier" onClick={chat.loadEarlier}>
              Load earlier messages
            </button>
          )}
          {chat.loadingConversation && chat.entries.length === 0 && (
            <p className="muted" role="status">
              Loading chat…
            </p>
          )}
          {empty ? (
            <Welcome demo={!chat.saved} onSuggest={suggest} />
          ) : (
            chat.entries.map((entry) => (
              <MessageView key={entry.key} entry={entry} onRetry={(text) => chat.retry?.(text, persona)} />
            ))
          )}
          {chat.pending && (
            <PendingAnswer
              requestId={chat.pending.requestId}
              preview={chat.pending.preview}
              abandoning={chat.pending.abandoning}
              onStop={chat.abandon}
              onRetry={() => chat.retryPending?.(persona)}
            />
          )}
          <div ref={bottom} />
        </div>
      </div>
      <div className="chat-footer">
        <div className="chat-column">
          {(chat.error || chat.notice) && (
            <div className={'banner' + (chat.error ? ' banner-error' : '')}>
              <div>
                {chat.error && <p role="alert">{chat.error}</p>}
                {chat.notice && <p role="status">{chat.notice}</p>}
              </div>
              <button type="button" className="icon-button" aria-label="Dismiss" onClick={chat.dismiss}>
                <CloseIcon size={16} />
              </button>
            </div>
          )}
          {!chat.advisingModelConfigured && (
            <div className="banner banner-error">
              <p role="alert">The advisor isn&apos;t set up yet.</p>
            </div>
          )}
          <Composer chat={chat} attachments={attachments} onRemoveAttachment={onRemoveAttachment} persona={persona} />
          <p className="composer-hint">
            Answers use only Emory sources and can be incomplete. Confirm important decisions with your advisor.
          </p>
        </div>
      </div>
    </main>
  );
}
