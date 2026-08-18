import {
  CANONICAL_ORDER,
  type CanonicalClass,
  type ComparisonResponse,
  type DirectAnswer,
} from '../api/types';
import { ProfileCard } from './ProfileCard';
import { VerdictCard } from './VerdictCard';

/**
 * Renders whichever shape the backend returned.
 *
 * The router picks one of four paths and each produces a different answer, so
 * the UI branches the same way rather than forcing everything into a comparison
 * table. An out-of-scope question gets a plain statement that the guides cannot
 * answer it; a build lookup gets the published figures; a policy question gets
 * the guide's own words. Only a described prospect gets carrier verdicts.
 */

/** Sorts verdicts best-offer-first so the comparison reads at a glance. */
function byLadder(a: CanonicalClass | null, b: CanonicalClass | null): number {
  const rank = (value: CanonicalClass | null) =>
    value ? CANONICAL_ORDER.indexOf(value) : CANONICAL_ORDER.length;
  return rank(a) - rank(b);
}

function DirectAnswerView({ answer }: { answer: DirectAnswer }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-sm font-semibold text-slate-900">
        {answer.kind === 'build_lookup'
          ? 'Published build limits'
          : 'What the guides say'}
      </h2>
      {answer.note && (
        <p className="mt-1 text-xs text-slate-500">{answer.note}</p>
      )}
      <ul className="mt-3 space-y-3">
        {answer.claims.map((claim, index) => (
          <li key={index} className="border-l-2 border-slate-200 pl-3">
            <p className="text-sm text-slate-800">{claim.statement}</p>
            <blockquote className="mt-1 border-l-2 border-slate-300 bg-slate-50 py-1.5 pl-2.5 text-xs italic leading-relaxed text-slate-600">
              &ldquo;{claim.citation.excerpt}&rdquo;
            </blockquote>
            <p className="mt-1 text-xs text-slate-400">
              {claim.citation.doc_id} &middot; page {claim.citation.page}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ResultView({ result }: { result: ComparisonResponse }) {
  const outOfScope = result.query_type === 'out_of_scope';
  const verdicts = [...result.verdicts].sort((a, b) =>
    byLadder(a.canonical_class, b.canonical_class),
  );

  return (
    <div className="space-y-4">
      {/* Surfacing that a citation was discarded, rather than hiding it. A
          non-zero count means the tool caught itself quoting something it could
          not find in the source, and a reader is entitled to know that
          happened on their query. */}
      {result.unverified_claims_dropped > 0 && (
        <div className="rounded-lg border border-orange-200 bg-orange-50 px-4 py-3 text-sm text-orange-900">
          <span className="font-semibold">
            {result.unverified_claims_dropped} unverifiable{' '}
            {result.unverified_claims_dropped === 1 ? 'claim was' : 'claims were'}{' '}
            discarded.
          </span>{' '}
          Their quoted text could not be found in the cited guide, so they are
          not shown.
        </div>
      )}

      {outOfScope ? (
        <section className="rounded-lg border border-slate-200 bg-white p-5">
          <h2 className="text-base font-semibold text-slate-900">
            The indexed guides cannot answer this
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-slate-600">
            {result.routing_reason}
          </p>
          <p className="mt-3 text-xs text-slate-500">
            Answering anyway would mean inventing a guideline. Nothing was sent
            to a model beyond classifying the question.
          </p>
        </section>
      ) : (
        <>
          {Object.keys(result.profile).length > 0 && (
            <ProfileCard profile={result.profile} />
          )}

          {result.answer && <DirectAnswerView answer={result.answer} />}

          {verdicts.length > 0 && (
            <div className="grid gap-3 md:grid-cols-2">
              {verdicts.map((verdict) => (
                <VerdictCard key={verdict.carrier_id} verdict={verdict} />
              ))}
            </div>
          )}
        </>
      )}

      <p className="text-xs text-slate-400">
        Answered in {(result.latency_ms / 1000).toFixed(1)}s
        {result.model !== 'none (answered without a model)' && (
          <> &middot; {result.model}</>
        )}
        {result.model === 'none (answered without a model)' && (
          <> &middot; read directly from the indexed documents</>
        )}
      </p>
    </div>
  );
}
