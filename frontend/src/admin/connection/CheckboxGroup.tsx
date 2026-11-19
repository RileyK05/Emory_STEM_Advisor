import { toggle } from './formState';

interface Props<T extends string> {
  legend: string;
  options: T[];
  selected: T[];
  onChange: (next: T[]) => void;
}

export function CheckboxGroup<T extends string>({ legend, options, selected, onChange }: Props<T>) {
  return (
    <fieldset className="admin-checks">
      <legend>{legend}</legend>
      {options.map((option) => (
        <label key={option} className="admin-check">
          <input
            type="checkbox"
            checked={selected.includes(option)}
            onChange={() => onChange(toggle(selected, option))}
          />
          {option}
        </label>
      ))}
    </fieldset>
  );
}
