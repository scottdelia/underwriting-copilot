import { useState, type ReactElement } from 'react';
import {
  applyColorScheme,
  readColorScheme,
  type ColorScheme,
} from '../theme';

/**
 * A three-state colour-scheme control.
 *
 * Three rather than two, because "follow the system" is a real preference and
 * the one most readers have without knowing it. A two-state toggle silently
 * converts that into a frozen choice the first time it is touched.
 *
 * Icons are inline SVG. The project ships no icon library, and three glyphs do
 * not justify one.
 */

const OPTIONS: {
  value: ColorScheme;
  label: string;
  icon: ReactElement;
}[] = [
  {
    value: 'system',
    label: 'Match system',
    icon: (
      <svg viewBox="0 0 16 16" fill="none" aria-hidden className="size-3.5">
        <rect
          x="1.75"
          y="2.75"
          width="12.5"
          height="8.5"
          rx="1.5"
          stroke="currentColor"
          strokeWidth="1.4"
        />
        <path
          d="M5.5 13.75h5"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    value: 'light',
    label: 'Light',
    icon: (
      <svg viewBox="0 0 16 16" fill="none" aria-hidden className="size-3.5">
        <circle cx="8" cy="8" r="3.1" stroke="currentColor" strokeWidth="1.4" />
        <path
          d="M8 1.4v1.5M8 13.1v1.5M14.6 8h-1.5M2.9 8H1.4M12.67 3.33l-1.06 1.06M4.39 11.61l-1.06 1.06M12.67 12.67l-1.06-1.06M4.39 4.39L3.33 3.33"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
        />
      </svg>
    ),
  },
  {
    value: 'dark',
    label: 'Dark',
    icon: (
      <svg viewBox="0 0 16 16" fill="none" aria-hidden className="size-3.5">
        <path
          d="M13.5 9.6A5.8 5.8 0 016.4 2.5a5.8 5.8 0 107.1 7.1z"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
      </svg>
    ),
  },
];

export function ThemeToggle() {
  // Read once, lazily, during the first render. An effect would be wrong here:
  // it would render the control in the "system" position and then correct it,
  // which is a visible flicker on every load for anyone who picked a scheme.
  // The stored value is already on <html> by this point -- index.html applies
  // it before first paint -- so this only has to agree with what is on screen.
  const [scheme, setScheme] = useState<ColorScheme>(readColorScheme);

  function choose(next: ColorScheme) {
    setScheme(next);
    applyColorScheme(next);
  }

  return (
    <div
      role="radiogroup"
      aria-label="Colour scheme"
      className="inline-flex items-center gap-0.5 rounded-full border border-line bg-surface p-0.5"
    >
      {OPTIONS.map((option) => {
        const active = scheme === option.value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-label={option.label}
            title={option.label}
            onClick={() => choose(option.value)}
            className={`flex size-7 items-center justify-center rounded-full transition-colors ${
              active
                ? 'bg-ink text-surface'
                : 'text-ink-faint hover:bg-surface-inset hover:text-ink-subtle'
            }`}
          >
            {option.icon}
          </button>
        );
      })}
    </div>
  );
}
