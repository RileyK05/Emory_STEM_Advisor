import { useEffect, useMemo, useRef, useState } from 'react';
import { getClient, isDemoMode } from '../../api/client';
import type { NativeArtifactKind, NativeArtifactRecord } from '../../api/types';
import { useApp } from '../../state/AppContext';
import { ArtifactLibraryDialog } from '../artifact/ArtifactLibraryDialog';
import { ArtifactPanel } from '../artifact/ArtifactPanel';
import { NewArtifactDialog } from '../artifact/NewArtifactDialog';
import type { NewArtifactRequest } from '../artifact/NewArtifactDialog';
import { useArtifactWorkspace } from '../artifact/useArtifactWorkspace';
import type { StartRunInput } from '../artifact/useArtifactWorkspace';
import { ChatView } from '../chat/ChatView';
import type { AssistantEntry, Attachment, ChatController } from '../chat/model';
import { useDemoChat } from '../chat/useDemoChat';
import { useSavedChat } from '../chat/useSavedChat';
import { useLocalConnectionsAvailable } from '../connection/ConnectionSettings';
import { MemoryDialog } from '../chat/MemoryDialog';
import { ExploreDialog } from '../source/ExploreDialog';
import { SourcesDialog } from '../source/SourcesDialog';
import { Sidebar } from './Sidebar';
import { ToolsDialog } from './ToolsDialog';
import { TopBar } from './TopBar';

// The API mode is fixed for the page's lifetime, so this is the same hook on every render.
const useChat: () => ChatController = isDemoMode() ? useDemoChat : useSavedChat;

const MAKE_PROMPTS: Record<NativeArtifactKind, (question: string) => string> = {
  document: (q) => `Write a clear, well-organized document that answers: ${q}`,
  workbook: (q) => `Build a spreadsheet that organizes the information needed for: ${q}`,
  html: (q) => `Make a simple web page that explains: ${q}`,
};

type DialogName = 'sources' | 'explore' | 'library' | 'tools' | 'memory';

export function Shell({ onLogout }: { onLogout?: () => void }) {
  const { session, sources, scope, flags, flagsError, refreshFlags } = useApp();
  const chat = useChat();
  const demo = !chat.saved;
  const referenced = useMemo(
    () => chat.entries.flatMap((entry) => (entry.role === 'user' ? entry.attachments : [])),
    [chat.entries],
  );
  const workspace = useArtifactWorkspace(session.workspaceKey ?? 'development', chat.activeId, referenced);
  const connectionsAvailable = useLocalConnectionsAvailable();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [dialog, setDialog] = useState<DialogName | null>(null);
  const [newRequest, setNewRequest] = useState<NewArtifactRequest | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [exportingSummary, setExportingSummary] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const nonce = useRef(0);
  const previousChat = useRef<string | null>(chat.activeId);
  const autoOpenedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!flags?.memory_inspector && dialog === 'memory') setDialog(null);
  }, [flags, dialog]);

  // Moving to a different chat leaves the previous chat's attachments and open artifact behind.
  // A new chat getting its id (null → id) is the same chat, so nothing is cleared then.
  useEffect(() => {
    const previous = previousChat.current;
    previousChat.current = chat.activeId;
    if (previous === null || previous === chat.activeId) return;
    setAttachments([]);
    workspace.closeArtifact();
    workspace.setPanelOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chat.activeId]);

  // On wide screens, a chat that has artifacts shows them alongside.
  useEffect(() => {
    if (demo || !chat.activeId || autoOpenedFor.current === chat.activeId || workspace.links.length === 0) return;
    autoOpenedFor.current = chat.activeId;
    if (window.matchMedia('(min-width: 1200px)').matches) workspace.setPanelOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demo, chat.activeId, workspace.links.length]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const openNew = (request: Omit<NewArtifactRequest, 'nonce'>) => setNewRequest({ ...request, nonce: ++nonce.current });

  const makeFromAnswer = (kind: NativeArtifactKind, entry: AssistantEntry) =>
    openNew({
      tab: 'draft',
      kind,
      prompt: MAKE_PROMPTS[kind](entry.question ?? ''),
      scope: entry.scope !== undefined ? entry.scope : scope,
    });

  const startRun = async (input: Omit<StartRunInput, 'chatKey'>) => {
    const key = await chat.ensureSaved(input.label || 'New chat');
    if (!key) throw new Error('This chat couldn’t be saved, so the draft wasn’t started. Try again.');
    await workspace.startRun({ ...input, chatKey: key });
    // Show the job's progress card instead of the version it will replace.
    if (input.revises) workspace.closeArtifact();
  };

  const created = async (record: NativeArtifactRecord, notice?: string) => {
    const key = await chat.ensureSaved(record.title);
    workspace.showRecord(record, key);
    if (notice) setToast(notice);
  };

  const discuss = (record: NativeArtifactRecord) => {
    setAttachments((current) => [
      ...current.filter((item) => item.artifactId !== record.artifactId),
      { artifactId: record.artifactId, revisionId: record.revisionId, title: record.title, kind: record.kind },
    ]);
    if (!window.matchMedia('(min-width: 1200px)').matches) workspace.setPanelOpen(false);
    document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus();
  };

  const exportSummary = async () => {
    if (!chat.activeId || exportingSummary) return;
    setExportingSummary(true);
    try {
      const record = await getClient().exportSummary(chat.activeId, {
        artifactReferences: attachments.map(({ artifactId, revisionId }) => ({ artifactId, revisionId })),
      });
      await created(record, 'Exported advising summary as an artifact.');
    } catch (err) {
      setToast(err instanceof Error ? err.message : 'Failed to export advising summary.');
    } finally {
      setExportingSummary(false);
    }
  };

  const chatForView: ChatController = {
    ...chat,
    send: (items) => {
      chat.send(items, () => setAttachments([]));
    },
  };
  const showPanel = !demo && workspace.panelOpen;
  const artifactCount = workspace.links.length + workspace.runs.length;

  return (
    <div className={'shell' + (showPanel ? ' with-panel' : '') + (showPanel && workspace.record ? ' with-canvas' : '')}>
      <TopBar session={session} onLogout={onLogout} onMenu={() => setDrawerOpen(true)} />
      {!demo && flagsError && (
        <div className="error-banner" role="alert">
          Optional features could not be checked: {flagsError}{' '}
          <button type="button" className="secondary-button" onClick={() => void refreshFlags()}>
            Retry feature check
          </button>
        </div>
      )}
      <div className="shell-body">
        <Sidebar
          chat={chat}
          sourceCount={sources.length}
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          onSources={() => setDialog('sources')}
          onExplore={() => setDialog('explore')}
          onLibrary={demo ? undefined : () => setDialog('library')}
          onTools={() => setDialog('tools')}
        />
        <ChatView
          chat={chatForView}
          attachments={attachments}
          onRemoveAttachment={(item) =>
            setAttachments((current) => current.filter((existing) => existing.artifactId !== item.artifactId))
          }
          onMake={demo ? undefined : makeFromAnswer}
          onCreate={demo ? undefined : (kind) => openNew({ tab: 'draft', kind })}
          onImport={demo ? undefined : () => openNew({ tab: 'import', kind: 'document' })}
          artifactCount={artifactCount}
          artifactsOpen={showPanel}
          onToggleArtifacts={demo ? undefined : () => workspace.setPanelOpen(!workspace.panelOpen)}
          onManageSources={() => setDialog('sources')}
          onExplore={() => setDialog('explore')}
          onExportSummary={demo || !flags?.advising_summary_export ? undefined : exportSummary}
          exportingSummary={exportingSummary}
          onOpenMemory={demo || !flags?.memory_inspector ? undefined : () => setDialog('memory')}
        />
        {showPanel && (
          <ArtifactPanel
            workspace={workspace}
            onNew={() => openNew({ tab: 'draft', kind: 'document' })}
            onLibrary={() => setDialog('library')}
            onAskChanges={(record) => openNew({ tab: 'draft', kind: record.kind, revise: record })}
            onDiscuss={discuss}
          />
        )}
      </div>
      <SourcesDialog open={dialog === 'sources'} onClose={() => setDialog(null)} />
      <ExploreDialog
        open={dialog === 'explore'}
        onClose={() => setDialog(null)}
        onAsk={(question) => {
          chat.setDraft(question);
          window.setTimeout(() => document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus(), 0);
        }}
      />
      <ToolsDialog
        open={dialog === 'tools'}
        onClose={() => setDialog(null)}
        connectionsAvailable={connectionsAvailable}
        onOpenRevision={async (artifactId, revisionId) => {
          await workspace.openArtifact(artifactId, revisionId);
        }}
      />
      <MemoryDialog
        open={dialog === 'memory'}
        onClose={() => setDialog(null)}
        conversationId={chat.activeId}
        onResetSuccess={() => setToast('Conversation context memory reset.')}
      />
      {!demo && (
        <>
          <ArtifactLibraryDialog
            open={dialog === 'library'}
            onClose={() => setDialog(null)}
            onOpen={async (artifactId) => {
              await workspace.openArtifact(artifactId);
            }}
          />
          <NewArtifactDialog
            request={newRequest}
            onClose={() => setNewRequest(null)}
            onStartRun={startRun}
            onCreated={created}
            onReloadRevise={async (artifactId) => {
              const latest = await getClient().getArtifact(artifactId);
              workspace.showRecord(latest);
              return latest;
            }}
          />
        </>
      )}
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
    </div>
  );
}
