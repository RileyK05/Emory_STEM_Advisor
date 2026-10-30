import type { NativeArtifactSummary } from '../../api/types';
import type { ChatController } from '../chat/model';
import { FolderIcon, PlusIcon, RefreshIcon } from '../icons';

const ADVISING_URL = import.meta.env.VITE_ADVISING_URL ?? 'https://advising.emory.edu';

interface Props {
  chat: ChatController;
  artifacts: NativeArtifactSummary[];
  artifactsLoading?: boolean;
  open: boolean;
  onClose: () => void;
  onOpenArtifact: (artifactId: string, revisionId?: string) => void;
  onRefreshArtifacts: () => void;
}

export function Sidebar({
  chat,
  artifacts,
  artifactsLoading,
  open,
  onClose,
  onOpenArtifact,
  onRefreshArtifacts,
}: Props) {
  // On narrow screens the sidebar is a drawer; any navigation closes it.
  const go = (action: () => void) => () => {
    action();
    onClose();
  };
  return (
    <>
      <div className={'scrim' + (open ? ' scrim-on' : '')} onClick={onClose} aria-hidden />
      <nav className={'sidebar' + (open ? ' sidebar-open' : '')} aria-label="Chats and artifacts">
        <button type="button" className="new-chat" onClick={go(chat.startNew)}>
          <PlusIcon size={16} /> New chat
        </button>
        <div className="sidebar-section">
          <div className="sidebar-heading">
            <span>{chat.showArchived ? 'Archived' : 'Chats'}</span>
            {chat.saved && (
              <>
                <button
                  type="button"
                  className="sidebar-heading-action"
                  onClick={chat.toggleArchived}
                  disabled={chat.loadingList}
                >
                  {chat.showArchived ? 'Active' : 'Archived'}
                </button>
                <button
                  type="button"
                  className="icon-button"
                  aria-label="Refresh chats"
                  onClick={chat.refreshList}
                  disabled={chat.loadingList}
                >
                  <RefreshIcon size={14} />
                </button>
              </>
            )}
          </div>
          {chat.saved ? (
            <ul className="chat-list" aria-label="Recent chats">
              {chat.loadingList && chat.conversations.length === 0 && (
                <li className="muted" role="status">
                  Loading…
                </li>
              )}
              {(chat.showArchived ? (chat.archivedConversations ?? []) : chat.conversations).map((item) => (
                <li key={item.conversationId} className="chat-list-row">
                  <button
                    type="button"
                    aria-current={chat.activeId === item.conversationId ? 'page' : undefined}
                    onClick={go(() => chat.open(item.conversationId))}
                    title={item.title}
                  >
                    <span className="chat-list-title">{item.title}</span>
                    {item.pendingRequestId && <span className="pending-dot" aria-label="answer in progress" />}
                  </button>
                  {chat.archive && (
                    <button
                      type="button"
                      className="chat-archive"
                      aria-label="Archive chat"
                      title="Archive chat"
                      disabled={!!item.pendingRequestId}
                      onClick={() =>
                        chat.showArchived ? chat.restore?.(item.conversationId) : chat.archive?.(item.conversationId)
                      }
                    >
                      {chat.showArchived ? 'Restore' : 'Archive'}
                    </button>
                  )}
                </li>
              ))}
              {!chat.loadingList &&
                (chat.showArchived ? (chat.archivedConversations ?? []) : chat.conversations).length === 0 && (
                  <li className="muted sidebar-empty">
                    {chat.showArchived ? 'No archived chats.' : 'Your chats will appear here.'}
                  </li>
                )}
            </ul>
          ) : (
            <p className="muted sidebar-empty">Demo mode — chats aren’t saved.</p>
          )}
        </div>
        <div className="sidebar-lower">
          <div className="sidebar-heading">
            <span>Artifacts</span>
            <button
              type="button"
              className="icon-button"
              aria-label="Refresh artifacts"
              onClick={onRefreshArtifacts}
              disabled={artifactsLoading}
            >
              <RefreshIcon size={14} />
            </button>
          </div>
          {artifacts.length === 0 ? (
            <p className="muted sidebar-empty">
              {artifactsLoading ? 'Loading artifacts…' : 'Ask the chat to make a plan or checklist.'}
            </p>
          ) : (
            <ul className="artifact-sidebar-list" aria-label="Artifacts">
              {artifacts.map((item) => (
                <li key={item.artifactId}>
                  <button type="button" onClick={go(() => onOpenArtifact(item.artifactId, item.revisionId))}>
                    <FolderIcon size={14} />
                    <span className="chat-list-title">{item.title}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          <a className="sidebar-advising" href={ADVISING_URL} target="_blank" rel="noopener noreferrer">
            Emory advising ↗
          </a>
        </div>
      </nav>
    </>
  );
}
