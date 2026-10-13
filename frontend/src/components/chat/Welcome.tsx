import { CompassIcon, DocumentIcon } from '../icons';

const SAVED_SUGGESTIONS = [
  'What do I need before taking Organic Chemistry?',
  'What are the requirements for the Biology major?',
  'Which math courses lead to linear algebra?',
  'Compare the Computer Science BA and BS.',
];

// The demo corpus is a small sample (an onboarding guide, a linear algebra primer, a lab manual).
const DEMO_SUGGESTIONS = [
  'What is a vector basis?',
  'What do I need before I can use the lab?',
  'When is the advisor matching form due?',
  'How does quantum entanglement work?',
];

interface Props {
  demo: boolean;
  onSuggest: (text: string) => void;
  onExplore: () => void;
  onCreate?: () => void;
}

export function Welcome({ demo, onSuggest, onExplore, onCreate }: Props) {
  return (
    <section className="welcome" aria-labelledby="welcome-title">
      <p className="welcome-kicker">Emory STEM Advisor</p>
      <h2 id="welcome-title">What are you planning?</h2>
      <p className="welcome-lede">
        Ask about courses, prerequisites, and program requirements. Every answer shows the exact passage it came from —
        and you can turn any answer into a document, spreadsheet, or web page.
      </p>
      <div className="suggestions">
        {(demo ? DEMO_SUGGESTIONS : SAVED_SUGGESTIONS).map((text) => (
          <button key={text} type="button" className="suggestion" onClick={() => onSuggest(text)}>
            {text}
          </button>
        ))}
      </div>
      <div className="welcome-actions">
        <button type="button" className="ghost-button" onClick={onExplore}>
          <CompassIcon size={16} /> Explore courses by interest
        </button>
        {onCreate && (
          <button type="button" className="ghost-button" onClick={onCreate}>
            <DocumentIcon size={16} /> Start a document
          </button>
        )}
      </div>
      {demo && (
        <p className="demo-note">
          Demo mode: answers come from a small sample library and chats aren’t saved. Connect the backend to save chats
          and build documents.
        </p>
      )}
    </section>
  );
}
