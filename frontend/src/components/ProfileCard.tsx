import type { ProspectProfile } from '../api/types';

/**
 * Shows the agent what the tool understood, before they trust what it concluded.
 *
 * The brief asks for the parsed profile to be "shown back for confirmation",
 * and the reason is worth being explicit about. Everything downstream, which
 * build chart row is read, which condition rules are retrieved, which verdict
 * is reached, follows from this parse. If the tool heard "A1c 7.1" as "age
 * 71", every carrier verdict below is confidently wrong and nothing else on the
 * page reveals it.
 *
 * Fields the agent did not state are listed explicitly rather than omitted. An
 * absent field reads as "not applicable"; a field labelled *not stated* reads
 * as "you did not tell me, and it may matter", which is the true position.
 *
 * Presentation note: the fields were a wrapped inline run in which a long value
 * ran into the next field's label. Each field is now its own cell with the
 * label above the value, so the parse can be checked by scanning rather than by
 * parsing the parse.
 */

const FIELD_LABELS: Record<string, string> = {
  age: 'Age',
  gender: 'Sex',
  height_inches: 'Height',
  weight_lbs: 'Weight',
  bmi: 'BMI',
  a1c: 'A1c',
  conditions: 'Conditions',
  medications: 'Medications',
  tobacco: 'Tobacco',
  coverage_amount_usd: 'Coverage',
  product_type: 'Product',
};

/** Fields whose absence changes a verdict, so their absence is worth showing. */
const LOAD_BEARING = ['age', 'gender', 'height_inches', 'weight_lbs', 'tobacco'];

function formatValue(key: string, value: unknown): string {
  if (key === 'height_inches' && typeof value === 'number') {
    return `${Math.floor(value / 12)}'${value % 12}"`;
  }
  if (key === 'weight_lbs') return `${value} lb`;
  if (key === 'coverage_amount_usd' && typeof value === 'number') {
    return `$${value.toLocaleString()}`;
  }
  if (key === 'tobacco') return value ? 'Tobacco user' : 'Non-tobacco';
  if (key === 'conditions' && Array.isArray(value)) {
    return value.map((c) => String(c).replace(/_/g, ' ')).join(', ');
  }
  if (Array.isArray(value)) return value.join(', ');
  return String(value);
}

/** Values that are figures get tabular digits so columns line up. */
function isNumeric(key: string): boolean {
  return ['age', 'bmi', 'a1c', 'weight_lbs', 'coverage_amount_usd'].includes(
    key,
  );
}

export function ProfileCard({ profile }: { profile: ProspectProfile }) {
  const entries = Object.entries(profile).filter(
    ([, value]) =>
      value !== null &&
      value !== undefined &&
      !(Array.isArray(value) && value.length === 0),
  );

  const missing = LOAD_BEARING.filter(
    (key) => profile[key as keyof ProspectProfile] === undefined,
  );

  if (entries.length === 0 && missing.length === 0) return null;

  return (
    <section className="card overflow-hidden">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line px-5 py-3.5">
        <h2 className="text-sm font-semibold text-ink">
          What the tool understood
        </h2>
        <p className="text-xs text-ink-subtle">
          Everything below is derived from this. Check it before trusting the
          verdicts.
        </p>
      </div>

      {/* Flex rather than a fixed grid. A grid with a spanning cell leaves an
          empty slot wherever the span does not divide evenly, and an empty cell
          renders as a stray block of the gap colour. Here the last row simply
          grows to fill. */}
      <dl className="flex flex-wrap">
        {entries.map(([key, value]) => (
          <div
            key={key}
            className="grow basis-[9.5rem] border-b border-r border-line px-5 py-3 last:border-r-0"
          >
            <dt className="text-[0.6875rem] font-medium uppercase tracking-[0.06em] text-ink-faint">
              {FIELD_LABELS[key] ?? key}
            </dt>
            <dd
              className={`mt-1 text-sm font-medium text-ink ${
                isNumeric(key) ? 'tabular' : ''
              }`}
            >
              {formatValue(key, value)}
            </dd>
          </div>
        ))}
      </dl>

      {missing.length > 0 && (
        <p className="border-t border-line bg-surface-inset px-5 py-3 text-xs text-ink-subtle">
          <span className="font-semibold text-ink-muted">Not stated:</span>{' '}
          {missing.map((key) => FIELD_LABELS[key] ?? key).join(', ')}. These can
          change a classification.
        </p>
      )}
    </section>
  );
}
