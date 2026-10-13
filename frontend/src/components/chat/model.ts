import type {
  ArtifactRevisionReference,
  Citation,
  ContextItem,
  ConversationSummary,
  ConversationStreamEvent,
  ConversationTurn,
  NativeArtifactKind,
  RetrievalTrace,
  ResearchDecisionInfo,
} from '../../api/types';
import type { SourceScope } from '../../state/AppContext';

/** An artifact revision attached to a message, with what the user needs to recognize it. */
export interface Attachment extends ArtifactRevisionReference {
  title: string;
  kind: NativeArtifactKind;
}

export interface UserEntry {
  key: string;
  role: 'user';
  text: string;
  attachments: Attachment[];
}

export interface AssistantEntry {
  key: string;
  role: 'assistant';
  kind: 'answer' | 'refusal' | 'error' | 'thinking';
  text: string;
  citations: Citation[];
  /** The numbered passages the answer was written from; `[n]` in the text is `context[n - 1]`. */
  context: ContextItem[];
  trace?: RetrievalTrace;
  research?: ResearchDecisionInfo | null;
  /** The question this answers, for turning the answer into an artifact. */
  question?: string;
  /** The source scope that question searched. */
  scope?: SourceScope;
}

export type ChatEntry = UserEntry | AssistantEntry;

/** The unverified text and stage of a turn that is still being generated. */
export interface StreamPreview {
  requestId: string;
  text: string;
  stage: string | null;
}

export const emptyPreview = (requestId: string): StreamPreview => ({ requestId, text: '', stage: null });

/** Fold one event into the preview of `requestId`, starting fresh if the preview belongs to another request. */
export function applyStreamEvent(
  current: StreamPreview | null,
  requestId: string,
  event: ConversationStreamEvent,
): StreamPreview {
  const base = current?.requestId === requestId ? current : emptyPreview(requestId);
  switch (event.type) {
    case 'provisional_delta':
      return { ...base, text: base.text + event.text };
    case 'provisional_reset':
      return { ...base, text: '' };
    case 'stage':
      return { ...base, stage: event.stage };
    default:
      return base;
  }
}

const STAGE_LABELS: Record<string, string> = {
  planning: 'Planning the search…',
  retrieving: 'Searching your sources…',
  routing: 'Getting ready…',
  generating: 'Writing an answer…',
  using_tools: 'Looking things up…',
  validating: 'Checking citations…',
  validated: 'Finishing up…',
  researching: 'Researching your options…',
  comparing: 'Comparing your options…',
  finalizing: 'Preparing a recommendation…',
};

export const streamStageLabel = (stage: string | null) => (stage && STAGE_LABELS[stage]) || 'Thinking…';

/** What the chat UI needs from either the saved (HTTP) or the demo (mock) engine. */
export interface ChatController {
  /** Chats are saved and listed; false in demo mode. */
  saved: boolean;
  conversations: ConversationSummary[];
  activeId: string | null;
  title: string | null;
  loadingList: boolean;
  loadingConversation: boolean;
  entries: ChatEntry[];
  /** A saved turn whose answer has not finished. */
  pending: { preview: StreamPreview | null; abandoning: boolean } | null;
  abandon: () => void;
  hasEarlier: boolean;
  loadEarlier: () => void;
  draft: string;
  setDraft: (text: string) => void;
  /** Sending is not possible right now (a turn is in flight, or the chat is loading). */
  busy: boolean;
  send: (attachments: Attachment[]) => void;
  /** Change the source scope for the next question. */
  changeScope: (scope: SourceScope) => void;
  open: (id: string) => void;
  startNew: () => void;
  /** The open chat's id, creating the chat (titled `title`) if it is new. Null in demo mode or on failure. */
  ensureSaved: (title: string) => Promise<string | null>;
  refreshList: () => void;
  error: string | null;
  notice: string | null;
  dismiss: () => void;
}

/** Transcript entries for saved turns. History and terminal turns render as plain answers or errors. */
export function entriesFromTurns(turns: ConversationTurn[]): ChatEntry[] {
  const questions = new Map<string, ConversationTurn>();
  for (const turn of turns) if (turn.role === 'user') questions.set(turn.requestId, turn);
  return turns.map((turn): ChatEntry => {
    const key = `${turn.sequence}-${turn.requestId}`;
    const evidence = turn.evidence;
    if (turn.role === 'user') {
      const references = 'effective' in evidence ? evidence.effective.artifactReferences : [];
      return {
        key,
        role: 'user',
        text: turn.text,
        attachments: references.map((ref) => ({
          artifactId: ref.artifactId,
          revisionId: ref.revisionId,
          title: ref.spec.title,
          kind: ref.spec.kind,
        })),
      };
    }
    const question = questions.get(turn.requestId);
    const questionEvidence = question && 'effective' in question.evidence ? question.evidence : null;
    const assistantEvidence = 'contextBlock' in evidence ? evidence : null;
    const kind =
      turn.status === 'refusal'
        ? 'refusal'
        : turn.status === 'failed' || turn.status === 'abandoned'
          ? 'error'
          : 'answer';
    return {
      key,
      role: 'assistant',
      kind,
      text: turn.text,
      citations: assistantEvidence?.citations ?? [],
      context: assistantEvidence?.contextBlock ?? [],
      research: assistantEvidence?.research,
      question: question?.text,
      scope: questionEvidence ? questionEvidence.effective.sourceIds : undefined,
    };
  });
}
