import type { ProspectProfile } from '../api/types';

/**
 * Shows the agent what the tool understood, before they trust what it concluded.
 *
 * The brief asks for the parsed profile to be "shown back for confirmation",
 * and the reason is worth being explicit about: everything downstream —
 * which build chart row is read, which condition rules are retrieved, which
 * verdict is reached — follows from this parse. If the tool heard "A1c 7.1" as
 * "age 71", every carrier verdict below is confidently wrong and nothing else
 * on the page reveals it.
 *
 * Fields the agent did not state are listed explicitly rather than omitted. An
 * absent field reads as "not applicable"; a field labelled *not stated* reads
 * as "you did not tell me, and it may matter", which is the true position.
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
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">
        What the tool understood
      </h2>
      <p className="mt-1 text-xs text-slate-500">
        Everything below is derived from this. Check it before trusting the
        verdicts.
      </p>

      <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
        {entries.map(([key, value]) => (
          <div key={key} className="flex items-baseline gap-2">
            <dt className="text-xs uppercase tracking-wide text-slate-400">
              {FIELD_LABELS[key] ?? key}
            </dt>
            <dd className="text-sm font-medium text-slate-900">
              {formatValue(key, value)}
            </dd>
          </div>
        ))}
      </dl>

      {missing.length > 0 && (
        <p className="mt-3 border-t border-slate-100 pt-3 text-xs text-slate-500">
          <span className="font-medium text-slate-600">Not stated:</span>{' '}
          {missing.map((key) => FIELD_LABELS[key] ?? key).join(', ')}. These can
          change a classification.
        </p>
      )}
    </section>
  );
}
