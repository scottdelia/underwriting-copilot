import type { CSSProperties } from 'react';
import { CANONICAL_ORDER, type CanonicalClass } from './api/types';

/**
 * The bridge between a rate class and its colour.
 *
 * The tier hues live in index.css as CSS variables, and components never name a
 * colour directly. A component sets `--tier` on an element and the `.tier-chip`,
 * `.tier-dot`, and `.tier-rail` rules in index.css mix their background, text,
 * and border out of it. That is what keeps a tier one variable to change rather
 * than a hunt through three files for shades that were meant to match.
 */

const TIER_VARIABLE: Record<CanonicalClass, string> = {
  preferred_plus: 'var(--tier-preferred-plus)',
  preferred: 'var(--tier-preferred)',
  standard_plus: 'var(--tier-standard-plus)',
  standard: 'var(--tier-standard)',
  table_rated: 'var(--tier-table-rated)',
  decline: 'var(--tier-decline)',
};

/**
 * Inline style setting `--tier` for a verdict.
 *
 * An abstention resolves to the neutral hue rather than a warning colour. It is
 * a correct and expected outcome, and colouring it as a failure would teach an
 * agent to read "I do not know" as "something broke" -- exactly the wrong
 * lesson in a regulated setting.
 */
export function tierStyle(tier: CanonicalClass | null): CSSProperties {
  return {
    '--tier': tier ? TIER_VARIABLE[tier] : 'var(--tier-none)',
  } as CSSProperties;
}

/** Ladder position, best first. Unclassified sorts last. */
export function tierRank(tier: CanonicalClass | null): number {
  return tier ? CANONICAL_ORDER.indexOf(tier) : CANONICAL_ORDER.length;
}
