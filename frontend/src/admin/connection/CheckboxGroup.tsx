import { toggle } from './formState';

interface Props<T extends string> {
  legend: string;
  options: T[];
  selected: T[];
  onChange: (next: T[]) => void;
}

export function CheckboxGroup<T extends string>({ legend, options, selected, onChange }: Props<T>) {
  return (
    <fieldset>
      <legend>{legend}</legend>
      {options.map((option) => (
        <label key={option}>
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
