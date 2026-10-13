import type { ChatController } from '../chat/model';
import { BookIcon, CompassIcon, FolderIcon, PlusIcon, RefreshIcon, ToolsIcon } from '../icons';

interface Props {
  chat: ChatController;
  sourceCount: number;
  open: boolean;
  onClose: () => void;
  onSources: () => void;
  onExplore: () => void;
  onLibrary?: () => void;
  onTools: () => void;
}

export function Sidebar({ chat, sourceCount, open, onClose, onSources, onExplore, onLibrary, onTools }: Props) {
  // On narrow screens the sidebar is a drawer; any navigation closes it.
  const go = (action: () => void) => () => {
    action();
    onClose();
  };
  return (
    <>
      <div className={'scrim' + (open ? ' scrim-on' : '')} onClick={onClose} aria-hidden />
      <nav className={'sidebar' + (open ? ' sidebar-open' : '')} aria-label="Chats and library">
        <button type="button" className="new-chat" onClick={go(chat.startNew)}>
          <PlusIcon size={16} /> New chat
        </button>
        <div className="sidebar-section">
          <div className="sidebar-heading">
            <span>Chats</span>
            {chat.saved && (
              <button
                type="button"
                className="icon-button"
                aria-label="Refresh chats"
                onClick={chat.refreshList}
                disabled={chat.loadingList}
              >
                <RefreshIcon size={14} />
              </button>
            )}
          </div>
          {chat.saved ? (
            <ul className="chat-list" aria-label="Recent chats">
              {chat.loadingList && chat.conversations.length === 0 && (
                <li className="muted" role="status">
                  Loading…
                </li>
              )}
              {chat.conversations.map((item) => (
                <li key={item.conversationId}>
                  <button
                    type="button"
                    aria-current={chat.activeId === item.conversationId ? 'page' : undefined}
                    onClick={go(() => chat.open(item.conversationId))}
                    title={item.title}
                  >
                    <span className="chat-list-title">{item.title}</span>
                    {item.pendingRequestId && <span className="pending-dot" aria-label="answer in progress" />}
                  </button>
                </li>
              ))}
              {!chat.loadingList && chat.conversations.length === 0 && (
                <li className="muted sidebar-empty">Your chats will appear here.</li>
              )}
            </ul>
          ) : (
            <p className="muted sidebar-empty">Demo mode — chats aren’t saved.</p>
          )}
        </div>
        <div className="sidebar-links">
          <button type="button" onClick={go(onSources)}>
            <BookIcon size={16} /> Sources <span className="count">{sourceCount}</span>
          </button>
          <button type="button" onClick={go(onExplore)}>
            <CompassIcon size={16} /> Explore courses
          </button>
          {onLibrary && (
            <button type="button" onClick={go(onLibrary)}>
              <FolderIcon size={16} /> All artifacts
            </button>
          )}
          <button type="button" onClick={go(onTools)}>
            <ToolsIcon size={16} /> Tools
          </button>
        </div>
      </nav>
    </>
  );
}
