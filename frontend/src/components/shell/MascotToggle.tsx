import dooley from '../../assets/dooley.png';
import swoop from '../../assets/swoop.png';
import { useTheme } from '../../state/ThemeContext';

/**
 * The theme switch is the mascot's face: Swoop for light (Emory blue and gold), Dooley for dark
 * (black and white). Clicking flips the coin over to the other mascot.
 */
export function MascotToggle() {
  const { theme, toggleTheme } = useTheme();
  const label = theme === 'light' ? 'Switch to dark mode (Dooley)' : 'Switch to light mode (Swoop)';
  return (
    <button
      type="button"
      className="mascot-toggle"
      data-face={theme}
      onClick={toggleTheme}
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
