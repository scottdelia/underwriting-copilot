import { useState } from 'react';
import {
  CANONICAL_LABELS,
  type CarrierVerdict,
  type Claim,
} from '../api/types';

/**
 * One carrier's verdict, with its evidence one click away.
 *
 * Three presentation decisions carry weight here.
 *
 * The carrier's own label leads and the normalized tier is secondary. An agent
 * has to quote "Select NT" to Cardinal; "standard_plus tier" is our internal
 * comparison key and would be meaningless on a phone call. Showing the carrier
 * label first and the tier as a subtitle keeps the comparison possible without
 * putting our vocabulary in the agent's mouth.
 *
 * An abstention is styled as a neutral outcome, not an error. It is a correct
 * and expected result, and colouring it red would train an agent to read "I do
 * not know" as "something broke" — which is exactly the wrong lesson in a
 * regulated setting.
 *
 * Every claim renders its citation. There is no code path that shows a
 * statement without the page and quotation behind it, because `Claim` has no
 * shape that permits one.
 */

/** Ladder tier to a colour, best to worst. Neutral for an abstention. */
const TIER_STYLES: Record<string, string> = {
  preferred_plus: 'bg-emerald-100 text-emerald-900 border-emerald-200',
  preferred: 'bg-emerald-50 text-emerald-900 border-emerald-200',
  standard_plus: 'bg-sky-50 text-sky-900 border-sky-200',
  standard: 'bg-slate-100 text-slate-900 border-slate-200',
  table_rated: 'bg-orange-50 text-orange-900 border-orange-200',
  decline: 'bg-rose-50 text-rose-900 border-rose-200',
};

function CitedClaim({ claim, tone }: { claim: Claim; tone: 'plus' | 'minus' }) {
  return (
    <li className="border-l-2 border-slate-200 py-1 pl-3">
      <p className="text-sm text-slate-800">
        <span
          aria-hidden
          className={
            tone === 'plus'
              ? 'mr-1 font-semibold text-emerald-600'
              : 'mr-1 font-semibold text-orange-600'
          }
        >
          {tone === 'plus' ? '+' : '−'}
        </span>
        {claim.statement}
      </p>
      <figure className="mt-1.5">
        <blockquote className="border-l-2 border-slate-300 bg-slate-50 py-1.5 pl-2.5 pr-2 text-xs italic leading-relaxed text-slate-600">
          &ldquo;{claim.citation.excerpt}&rdquo;
        </blockquote>
        {/* The source is named but not linked. Section 8 of the brief forbids
            serving or redistributing the carrier document, so the citation
            identifies the page precisely enough to check by hand without the
            app handing out the PDF. */}
        <figcaption className="mt-1 text-xs text-slate-400">
          {claim.citation.doc_id} &middot; page {claim.citation.page}
        </figcaption>
      </figure>
    </li>
  );
}

export function VerdictCard({ verdict }: { verdict: CarrierVerdict }) {
  const [open, setOpen] = useState(false);
  const classified = verdict.determination === 'classified';
  const evidenceCount = verdict.qualifying.length + verdict.disqualifying.length;

  const tierStyle = verdict.canonical_class
    ? TIER_STYLES[verdict.canonical_class]
    : 'bg-slate-50 text-slate-700 border-slate-200';

  return (
    <article className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 p-4">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-slate-500">
            {verdict.carrier_name}
          </h3>
          {classified ? (
            <>
              <p className="mt-0.5 text-xl font-semibold text-slate-900">
                {verdict.carrier_label}
              </p>
              {verdict.canonical_class && (
                <p className="text-xs text-slate-500">
                  {CANONICAL_LABELS[verdict.canonical_class]}
                </p>
              )}
            </>
          ) : (
            <p className="mt-0.5 text-lg font-semibold text-slate-600">
              Insufficient information
            </p>
          )}
        </div>

        <span
          className={`shrink-0 rounded-full border px-3 py-1 text-xs font-medium ${tierStyle}`}
        >
          {verdict.canonical_class
            ? CANONICAL_LABELS[verdict.canonical_class]
            : 'No determination'}
        </span>
      </div>

      {!classified && verdict.abstention_reason && (
        <p className="border-t border-slate-100 bg-slate-50 px-4 py-3 text-sm leading-relaxed text-slate-700">
          {verdict.abstention_reason}
        </p>
      )}

      {(evidenceCount > 0 || verdict.missing_information.length > 0) && (
        <div className="border-t border-slate-100">
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm text-slate-600 hover:bg-slate-50"
          >
            <span>
              {evidenceCount > 0
                ? `${evidenceCount} cited ${evidenceCount === 1 ? 'finding' : 'findings'}`
                : 'What is still needed'}
              {verdict.missing_information.length > 0 && evidenceCount > 0 && (
                <span className="text-slate-400">
                  {' '}
                  &middot; {verdict.missing_information.length} to confirm
                </span>
              )}
            </span>
            <span aria-hidden className="text-slate-400">
              {open ? '−' : '+'}
            </span>
          </button>

          {open && (
            <div className="space-y-4 px-4 pb-4">
              {verdict.qualifying.length > 0 && (
                <ul className="space-y-3">
                  {verdict.qualifying.map((claim, index) => (
                    <CitedClaim key={index} claim={claim} tone="plus" />
                  ))}
                </ul>
              )}
              {verdict.disqualifying.length > 0 && (
                <ul className="space-y-3">
                  {verdict.disqualifying.map((claim, index) => (
                    <CitedClaim key={index} claim={claim} tone="minus" />
                  ))}
                </ul>
              )}
              {verdict.missing_information.length > 0 && (
                <div className="rounded-md bg-amber-50 p-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-amber-900">
                    Confirm before relying on this
                  </p>
                  <ul className="mt-1.5 list-disc space-y-1 pl-4 text-sm text-amber-950">
                    {verdict.missing_information.map((item, index) => (
                      <li key={index}>{item}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </article>
  );
}
