import { useEffect, useRef } from 'react';
import type { NativeArtifactKind } from '../../api/types';
import { CloseIcon, FolderIcon } from '../icons';
import { Composer } from './Composer';
import { MessageView, PendingAnswer } from './MessageView';
import { Welcome } from './Welcome';
import type { AssistantEntry, Attachment, ChatController } from './model';

interface Props {
  chat: ChatController;
  attachments: Attachment[];
  onRemoveAttachment: (attachment: Attachment) => void;
  /** Saved mode: artifacts can be made from answers and from the composer. */
  onMake?: (kind: NativeArtifactKind, entry: AssistantEntry) => void;
  onCreate?: (kind: NativeArtifactKind) => void;
  onImport?: () => void;
  artifactCount: number;
  artifactsOpen: boolean;
  onToggleArtifacts?: () => void;
  onManageSources: () => void;
  onExplore: () => void;
}

export function ChatView(props: Props) {
  const { chat } = props;
  const bottom = useRef<HTMLDivElement>(null);
  const textLength = chat.pending?.preview?.text.length ?? 0;

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [chat.entries, chat.pending !== null, textLength]);

  const suggest = (text: string) => {
    chat.setDraft(text);
    document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus();
  };
  const empty = chat.entries.length === 0 && !chat.pending && !chat.loadingConversation;

  return (
    <main className="chat" aria-label="Chat">
      <header className="chat-header">
        <h2 className="chat-title">{chat.title ?? 'New chat'}</h2>
        {props.onToggleArtifacts && (props.artifactCount > 0 || props.artifactsOpen) && (
          <button
            type="button"
            className="ghost-button"
            aria-pressed={props.artifactsOpen}
            onClick={props.onToggleArtifacts}
          >
            <FolderIcon size={16} /> Artifacts{props.artifactCount ? ` (${props.artifactCount})` : ''}
          </button>
        )}
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
            <Welcome
              demo={!chat.saved}
              onSuggest={suggest}
              onExplore={props.onExplore}
              onCreate={props.onCreate && (() => props.onCreate?.('document'))}
            />
          ) : (
            chat.entries.map((entry) => <MessageView key={entry.key} entry={entry} onMake={props.onMake} />)
          )}
          {chat.pending && (
            <PendingAnswer preview={chat.pending.preview} abandoning={chat.pending.abandoning} onStop={chat.abandon} />
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
          <Composer
            chat={chat}
            attachments={props.attachments}
            onRemoveAttachment={props.onRemoveAttachment}
            onCreate={props.onCreate}
            onImport={props.onImport}
            onManageSources={props.onManageSources}
          />
          <p className="composer-hint">
            Answers use only your sources and can be incomplete. Confirm important decisions with your advisor.
          </p>
        </div>
      </div>
    </main>
  );
}
