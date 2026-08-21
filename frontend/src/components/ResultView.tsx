import type { ComparisonResponse, DirectAnswer } from '../api/types';
import { tierRank } from '../theme';
import { ProfileCard } from './ProfileCard';
import { VerdictRow } from './VerdictRow';

/**
 * Renders whichever shape the backend returned.
 *
 * The router picks one of four paths and each produces a different answer, so
 * the UI branches the same way rather than forcing everything into a comparison
 * table. An out-of-scope question gets a plain statement that the guides cannot
 * answer it; a build lookup gets the published figures; a policy question gets
 * the guide's own words. Only a described prospect gets carrier verdicts.
 */

function DirectAnswerView({ answer }: { answer: DirectAnswer }) {
  return (
    <section className="card overflow-hidden">
      <div className="border-b border-line px-5 py-3.5">
        <h2 className="text-sm font-semibold text-ink">
          {answer.kind === 'build_lookup'
            ? 'Published build limits'
            : 'What the guides say'}
        </h2>
        {answer.note && (
          <p className="mt-1 text-xs leading-relaxed text-ink-subtle">
            {answer.note}
          </p>
        )}
      </div>
      <ul className="divide-y divide-line">
        {answer.claims.map((claim, index) => (
          <li key={index} className="px-5 py-4">
            <p className="text-sm leading-relaxed text-ink">
              {claim.statement}
            </p>
            <blockquote className="excerpt mt-2 italic">
              &ldquo;{claim.citation.excerpt}&rdquo;
            </blockquote>
            <p className="mt-1.5 font-mono text-[0.6875rem] tracking-tight text-ink-faint">
              {claim.citation.doc_id} · p.{claim.citation.page}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ResultView({ result }: { result: ComparisonResponse }) {
  const outOfScope = result.query_type === 'out_of_scope';

  // Best offer first, so the comparison reads down the column.
  const verdicts = [...result.verdicts].sort(
    (a, b) => tierRank(a.canonical_class) - tierRank(b.canonical_class),
  );
  const classifiedCount = verdicts.filter(
    (verdict) => verdict.determination === 'classified',
  ).length;

  return (
    <div className="space-y-4">
      {/* Surfacing that a citation was discarded rather than hiding it. A
          non-zero count means the tool caught itself quoting something it could
          not find in the source, and a reader is entitled to know that happened
          on their query. */}
      {result.unverified_claims_dropped > 0 && (
        <div className="note note-warn flex gap-3">
          <svg
            viewBox="0 0 16 16"
            fill="none"
            aria-hidden
            className="mt-0.5 size-4 shrink-0"
          >
            <circle cx="8" cy="8" r="6.4" stroke="currentColor" strokeWidth="1.4" />
            <path
              d="M8 4.9v3.6"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
            <circle cx="8" cy="11" r="0.85" fill="currentColor" />
          </svg>
          <p>
            <span className="font-bold">
              The tool caught itself making{' '}
              {result.unverified_claims_dropped}{' '}
              {result.unverified_claims_dropped === 1 ? 'claim' : 'claims'} up.
            </span>{' '}
            It quoted the guide, then the quote turned out not to be in the
            guide.{' '}
            {result.unverified_claims_dropped === 1
              ? 'That claim was'
              : 'Those claims were'}{' '}
            thrown away instead of being shown to you.
          </p>
        </div>
      )}

      {outOfScope ? (
        <section className="card p-6">
          <h2 className="text-title font-extrabold text-ink-strong">
            None of these four guides covers that
          </h2>
          <p className="mt-3 max-w-prose text-lead text-ink-muted">
            {result.routing_reason}
          </p>
          <p className="mt-4 border-t border-line pt-4 text-sm text-ink-subtle">
            Answering anyway would mean making up a guideline. Beyond working
            out that the question was off-topic, no AI was involved.
          </p>
        </section>
      ) : (
        <>
          {Object.keys(result.profile).length > 0 && (
            <ProfileCard profile={result.profile} />
          )}

          {result.answer && <DirectAnswerView answer={result.answer} />}

          {verdicts.length > 0 && (
            <section className="card overflow-hidden">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line px-5 py-3.5">
                <h2 className="text-sm font-bold text-ink-strong">
                  What each carrier would likely offer
                </h2>
                <p className="text-xs text-ink-subtle">
                  Best offer first.{' '}
                  <span className="tabular">{classifiedCount}</span> of{' '}
                  <span className="tabular">{verdicts.length}</span> could be
                  answered from the guides. Open a row to read the wording it
                  used.
                </p>
              </div>
              <ul>
                {verdicts.map((verdict, index) => (
                  <VerdictRow
                    key={verdict.carrier_id}
                    verdict={verdict}
                    // Rank counts classified verdicts only. An abstention has
                    // no position on the ladder, so it is shown unranked rather
                    // than given a number that implies it placed last.
                    rank={
                      verdict.determination === 'classified' ? index + 1 : null
                    }
                  />
                ))}
              </ul>
            </section>
          )}
        </>
      )}

      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1 text-xs text-ink-faint">
        <span className="tabular">
          Answered in {(result.latency_ms / 1000).toFixed(1)}s
        </span>
        <span aria-hidden>·</span>
        <span className="font-mono tracking-tight">{result.model}</span>
      </p>
    </div>
  );
}
