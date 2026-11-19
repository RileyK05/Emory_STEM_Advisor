import { useRef } from 'react';
import { flushSync } from 'react-dom';
import dooley from '../../assets/dooley.png';
import swoop from '../../assets/swoop.png';
import { useTheme } from '../../state/ThemeContext';

type ViewTransitionDocument = Document & {
  startViewTransition?: (update: () => void) => { ready: Promise<void>; finished: Promise<void> };
};

/** Colors swap at once rather than each control fading on its own; the coin keeps its flip. */
function swapInstantly(root: HTMLElement, done: Promise<unknown>) {
  root.classList.add('theme-switching');
  void done.catch(() => undefined).then(() => requestAnimationFrame(() => root.classList.remove('theme-switching')));
}

/**
 * The theme switch is the mascot's face: Swoop for light (Emory blue and gold), Dooley for dark
 * (black and white). Clicking flips the coin over to the other mascot, and where the browser can,
 * the new theme spreads out from the coin across the page.
 */
export function MascotToggle() {
  const { theme, toggleTheme } = useTheme();
  const ref = useRef<HTMLButtonElement>(null);
  const label = theme === 'light' ? 'Switch to dark mode (Dooley)' : 'Switch to light mode (Swoop)';

  const flip = () => {
    const doc = document as ViewTransitionDocument;
    const button = ref.current;
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const root = document.documentElement;
    if (!doc.startViewTransition || !button || still) {
      swapInstantly(root, new Promise((resolve) => requestAnimationFrame(resolve)));
      toggleTheme();
      return;
    }
    const box = button.getBoundingClientRect();
    const x = box.left + box.width / 2;
    const y = box.top + box.height / 2;
    const reach = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
    const next = theme === 'light' ? 'dark' : 'light';
    const transition = doc.startViewTransition(() => {
      // The new snapshot must already carry the new theme, so apply it synchronously here.
      flushSync(toggleTheme);
      root.dataset.theme = next;
    });
    swapInstantly(root, transition.finished);
    void transition.ready
      .then(() => {
        root.animate(
          { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${reach}px at ${x}px ${y}px)`] },
          { duration: 560, easing: 'cubic-bezier(0.4, 0, 0.2, 1)', pseudoElement: '::view-transition-new(root)' },
        );
      })
      .catch(() => undefined);
  };

  return (
    <button
      ref={ref}
      type="button"
      className="mascot-toggle"
      data-face={theme}
      onClick={flip}
      aria-label={label}
      title={label}
    >
      <span className="mascot-coin">
        <img className="mascot-face mascot-swoop" src={swoop} alt="" draggable={false} />
        <img className="mascot-face mascot-dooley" src={dooley} alt="" draggable={false} />
      </span>
    </button>
  );
}
