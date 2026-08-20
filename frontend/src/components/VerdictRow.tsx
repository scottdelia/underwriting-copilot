import { useState } from 'react';
import {
  CANONICAL_LABELS,
  type CarrierVerdict,
  type Claim,
} from '../api/types';
import { tierStyle } from '../theme';

/**
 * One carrier's verdict as a row in the ranked comparison, evidence one click
 * away.
 *
 * WHY A ROW AND NOT A CARD
 * ------------------------
 * This was four equal cards in a two-column grid. The tool's entire purpose is
 * comparison, and a grid of cards makes the reader hold four answers in their
 * head and rank them from memory. As rows in ladder order, with the offer in
 * its own column, the comparison is done by running an eye down one line.
 *
 * Three presentation decisions carry weight and survive from the card version.
 *
 * The carrier's own label leads and the normalised tier is secondary. An agent
 * has to say "Select NT" to Cardinal; "standard_plus" is our internal
 * comparison key and would be meaningless on a phone call. The carrier label is
 * the largest text in the row; the tier rides beside it as a chip so
 * cross-carrier comparison stays possible without putting our vocabulary in the
 * agent's mouth.
 *
 * An abstention is a neutral outcome, not an error. It gets the neutral hue and
 * a dash where its rank would be -- it is unranked because there is no
 * determination to rank, which is the true position. Colouring it as a failure
 * would train an agent to read "I do not know" as "something broke", exactly
 * the wrong lesson in a regulated setting.
 *
 * Every claim renders its citation. There is no code path that shows a
 * statement without the page and quotation behind it, because `Claim` has no
 * shape that permits one.
 */

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden
      className={`size-4 shrink-0 text-ink-faint transition-transform duration-200 ${
        open ? 'rotate-180' : ''
      }`}
    >
      <path
        d="M4 6.5L8 10.5L12 6.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CitedClaim({ claim, tone }: { claim: Claim; tone: 'plus' | 'minus' }) {
  const qualifying = tone === 'plus';
  return (
    <li className="flex gap-3">
      <span
        aria-hidden
        className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full text-[0.6875rem] font-bold leading-none"
        style={{
          background: qualifying
            ? 'color-mix(in oklab, var(--tier-preferred-plus) 18%, transparent)'
            : 'color-mix(in oklab, var(--tier-table-rated) 20%, transparent)',
          color: qualifying
            ? 'var(--tier-preferred-plus)'
            : 'var(--tier-table-rated)',
        }}
      >
        {qualifying ? '+' : '−'}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm leading-relaxed text-ink-muted">
          {claim.statement}
        </p>
        <figure className="mt-2">
          <blockquote className="excerpt italic">
            &ldquo;{claim.citation.excerpt}&rdquo;
          </blockquote>
          {/* The source is named but not linked. Section 8 of the brief forbids
              serving or redistributing the carrier document, so the citation
              identifies the page precisely enough to check by hand without the
              app handing out the PDF. */}
          <figcaption className="mt-1.5 font-mono text-[0.6875rem] tracking-tight text-ink-faint">
            {claim.citation.doc_id} · p.{claim.citation.page}
          </figcaption>
        </figure>
      </div>
    </li>
  );
}

export function VerdictRow({
  verdict,
  rank,
}: {
  verdict: CarrierVerdict;
  rank: number | null;
}) {
  const [open, setOpen] = useState(false);
  const classified = verdict.determination === 'classified';
  const evidenceCount = verdict.qualifying.length + verdict.disqualifying.length;
  const expandable =
    evidenceCount > 0 ||
    verdict.missing_information.length > 0 ||
    Boolean(verdict.abstention_reason);
  const style = tierStyle(verdict.canonical_class);

  const summary = (
    <>
      {/* Rank. A dash for an abstention: it is unranked because there is no
          determination, not because it came last. */}
      <span
        aria-hidden
        className="tabular hidden w-7 shrink-0 text-center text-sm font-semibold text-ink-faint sm:block"
      >
        {rank ?? '–'}
      </span>

      <span className="min-w-0 flex-1 sm:flex-none sm:basis-[15rem]">
        <span className="block truncate text-[0.8125rem] font-medium text-ink-subtle">
          {verdict.carrier_name}
        </span>
        <span
          className={`mt-0.5 block truncate ${
            classified
              ? 'text-offer font-semibold text-ink'
              : 'text-base font-medium text-ink-subtle'
          }`}
        >
          {classified ? verdict.carrier_label : 'Insufficient information'}
        </span>
      </span>

      <span className="flex flex-1 flex-wrap items-center justify-end gap-x-4 gap-y-1.5">
        <span className="tier-chip" style={style}>
          <span className="tier-dot" style={style} />
          {verdict.canonical_class
            ? CANONICAL_LABELS[verdict.canonical_class]
            : 'No determination'}
        </span>

        {expandable && (
          <span className="flex items-center gap-2 text-xs text-ink-subtle">
            <span className="tabular hidden sm:inline">
              {evidenceCount > 0
                ? `${evidenceCount} cited`
                : 'why not'}
              {verdict.missing_information.length > 0 && (
                <span className="text-ink-faint">
                  {' '}
                  · {verdict.missing_information.length} to confirm
                </span>
              )}
            </span>
            <Chevron open={open} />
          </span>
        )}
      </span>
    </>
  );

  return (
    <li
      className="tier-rail border-b border-line last:border-b-0"
      style={style}
    >
      {expandable ? (
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3.5 text-left transition-colors hover:bg-surface-inset sm:flex-nowrap sm:px-5"
        >
          {summary}
        </button>
      ) : (
        <div className="flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3.5 sm:flex-nowrap sm:px-5">
          {summary}
        </div>
      )}

      {open && (
        <div className="space-y-4 border-t border-line bg-surface-inset px-4 py-4 sm:px-5 sm:pl-16">
          {!classified && verdict.abstention_reason && (
            <p className="text-sm leading-relaxed text-ink-muted">
              {verdict.abstention_reason}
            </p>
          )}

          {verdict.qualifying.length > 0 && (
            <ul className="space-y-4">
              {verdict.qualifying.map((claim, index) => (
                <CitedClaim key={index} claim={claim} tone="plus" />
              ))}
            </ul>
          )}

          {verdict.disqualifying.length > 0 && (
            <ul className="space-y-4">
              {verdict.disqualifying.map((claim, index) => (
                <CitedClaim key={index} claim={claim} tone="minus" />
              ))}
            </ul>
          )}

          {verdict.missing_information.length > 0 && (
            <div className="note note-warn">
              <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.06em]">
                Confirm before relying on this
              </p>
              <ul className="mt-2 space-y-1.5">
                {verdict.missing_information.map((item, index) => (
                  <li key={index} className="flex gap-2 text-sm">
                    <span aria-hidden className="select-none opacity-60">
                      •
                    </span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
