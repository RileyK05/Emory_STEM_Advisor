import { useEffect, useRef, useState } from 'react';
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
  // Applies to the next message only; it clears once that message is accepted.
  const [makeArtifact, setMakeArtifact] = useState(false);
  const canSend = chat.advisingModelConfigured && !chat.busy && chat.draft.trim().length > 0;

  // Grow with the text, up to a cap where it starts to scroll.
  useEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 220) + 'px';
  }, [chat.draft]);

  const submit = () => {
    if (canSend) chat.send(attachments, () => setMakeArtifact(false), { makeArtifact });
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
        placeholder="Ask about courses, plans or what to take next…"
      />
      <div className="composer-bar">
        <label
          className="file-toggle"
          title="Also make a document, spreadsheet or page from this answer, such as a plan, checklist or course map"
        >
          <input
            type="checkbox"
            checked={makeArtifact}
            onChange={(event) => setMakeArtifact(event.target.checked)}
            disabled={!chat.advisingModelConfigured}
          />
          Make a file
        </label>
        <button type="submit" className="send-button" aria-label="Send" disabled={!canSend}>
          <SendIcon />
        </button>
      </div>
    </form>
  );
}
