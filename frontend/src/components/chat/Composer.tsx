import { useEffect, useRef } from 'react';
import type { KeyboardEvent } from 'react';
import { CloseIcon, KindIcon, SendIcon } from '../icons';
import type { Attachment, ChatController } from './model';

interface Props {
  chat: ChatController;
  attachments: Attachment[];
  onRemoveAttachment: (attachment: Attachment) => void;
}

export function Composer({ chat, attachments, onRemoveAttachment }: Props) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const canSend = chat.advisingModelConfigured && !chat.busy && chat.draft.trim().length > 0;

  // Grow with the text, up to a cap where it starts to scroll.
  useEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 220) + 'px';
  }, [chat.draft]);

  const submit = () => {
    if (canSend) chat.send(attachments);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      {attachments.length > 0 && (
        <div className="attachment-row" aria-label="Attached to your next message">
          {attachments.map((item) => (
            <span className="attachment-chip" key={`${item.artifactId}:${item.revisionId}`}>
              <KindIcon kind={item.kind} size={14} />
              {item.title}
              <button
                type="button"
                className="chip-remove"
                aria-label={`Remove ${item.title} from this message`}
                onClick={() => onRemoveAttachment(item)}
              >
                <CloseIcon size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      <textarea
        ref={textarea}
        aria-label="Message"
        rows={1}
        value={chat.draft}
        onChange={(event) => chat.setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        disabled={!chat.advisingModelConfigured}
        placeholder="Ask about courses, prerequisites, or program requirements…"
      />
      <div className="composer-bar">
        <button type="submit" className="send-button" aria-label="Send" disabled={!canSend}>
          <SendIcon />
        </button>
      </div>
    </form>
  );
}
