import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { NativeArtifactKind } from '../../api/types';
import { useApp } from '../../state/AppContext';
import { useDismiss } from '../../lib/useDismiss';
import { CloseIcon, KIND_LABEL, KindIcon, PlusIcon, SendIcon, UploadIcon } from '../icons';
import { ScopePicker } from './ScopePicker';
import type { Attachment, ChatController } from './model';

interface Props {
  chat: ChatController;
  attachments: Attachment[];
  onRemoveAttachment: (attachment: Attachment) => void;
  /** Start a new artifact (saved mode only). */
  onCreate?: (kind: NativeArtifactKind) => void;
  onImport?: () => void;
  onManageSources: () => void;
}

function CreateMenu({ onCreate, onImport }: { onCreate: (kind: NativeArtifactKind) => void; onImport: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  const choose = (action: () => void) => {
    setOpen(false);
    action();
  };
  return (
    <div className="create-menu" ref={ref}>
      <button
        type="button"
        className="icon-button round"
        aria-label="Create or import"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <PlusIcon />
      </button>
      {open && (
        <div className="popover menu" role="menu">
          {(['document', 'workbook', 'html'] as NativeArtifactKind[]).map((kind) => (
            <button key={kind} type="button" role="menuitem" onClick={() => choose(() => onCreate(kind))}>
              <KindIcon kind={kind} />
              New {KIND_LABEL[kind].toLowerCase()}
            </button>
          ))}
          <button type="button" role="menuitem" onClick={() => choose(onImport)}>
            <UploadIcon />
            Import a Word, Excel, or Markdown file
          </button>
        </div>
      )}
    </div>
  );
}

export function Composer({ chat, attachments, onRemoveAttachment, onCreate, onImport, onManageSources }: Props) {
  const { scope, sources } = useApp();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const canSend = !chat.busy && chat.draft.trim().length > 0;

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
        placeholder="Ask about courses, prerequisites, or program requirements…"
      />
      <div className="composer-bar">
        {onCreate && onImport && <CreateMenu onCreate={onCreate} onImport={onImport} />}
        <ScopePicker scope={scope} sources={sources} onChange={chat.changeScope} onManage={onManageSources} />
        <button type="submit" className="send-button" aria-label="Send" disabled={!canSend}>
          <SendIcon />
        </button>
      </div>
    </form>
  );
}
