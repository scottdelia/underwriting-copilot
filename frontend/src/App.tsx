import { useEffect, useRef, useState } from 'react';
import {
  compare,
  DEMO_MODE,
  getHealth,
  hasSharedSecret,
  setSharedSecret,
} from './api/client';
import {
  DemoQueryUnavailableError,
  InputRejectedError,
  UnauthorizedError,
  type ComparisonResponse,
  type HealthResponse,
} from './api/types';
import exampleQueries from './api/exampleQueries.json';
import { Disclaimer } from './components/Disclaimer';
import { ResultView } from './components/ResultView';

/**
 * The whole application: one page, one input, one answer.
 *
 * No router and no chat history, both of which the brief puts out of scope. The
 * absence of history is not only scope discipline. A stored transcript of
 * these queries would be a file of named people's medical details, which is not
 * something a demo should accumulate.
 */

/**
 * The seeded example queries.
 *
 * Imported rather than written here because tools/capture_fixtures.py reads the
 * same file: the published build looks recordings up by hashing the query, so
 * an example button whose text differs from the captured query by one character
 * silently stops working. It differed by one character ("5'6" versus "5'06")
 * until this was made a single source.
 *
 * The last entry is out of scope on purpose. A demo that only seeds queries the
 * tool can answer hides the behaviour that matters most in a regulated context,
 * which is what it does when it cannot answer.
 */
const EXAMPLES: string[] = exampleQueries;

/** The mark from the favicon: four carriers, ranked, in ladder colours. */
function Mark() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className="size-8 shrink-0">
      {/* Brand teal ground with the ranked bars in the action colour and white.
          The bars used to be tier hues on a near-black square, which worked
          while the tiers were bright on a dark theme; against the current
          palette the best tier is a dark teal and it disappeared into the
          square. The mark only has to say "carriers, ranked", so it uses the
          two colours guaranteed to read on the brand ground. */}
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <rect x="7" y="8" width="18" height="3.5" rx="1.75" fill="var(--cta)" />
      <rect
        x="7"
        y="14"
        width="13"
        height="3.5"
        rx="1.75"
        fill="var(--surface)"
        opacity="0.92"
      />
      <rect
        x="7"
        y="20"
        width="15"
        height="3.5"
        rx="1.75"
        fill="var(--surface)"
        opacity="0.55"
      />
    </svg>
  );
}

export default function App() {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<ComparisonResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Set when the static build has no recording for a query. Holds the queries
  // it does have, so the reader is offered them rather than told "no".
  const [demoMiss, setDemoMiss] = useState<string[] | null>(null);
  const [needsSecret, setNeedsSecret] = useState(false);
  const [secret, setSecret] = useState('');
  const [health, setHealth] = useState<HealthResponse | null>(null);

  // Holds the in-flight request so a new submission cancels the previous one.
  // Without this a slow first query can land after a fast second one and
  // overwrite it, showing an answer to a question the user has moved on from.
  const inFlight = useRef<AbortController | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  async function run(text: string) {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;

    setLoading(true);
    setError(null);
    setDemoMiss(null);
    setResult(null);

    try {
      setResult(await compare(trimmed, controller.signal));
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return;
      if (caught instanceof UnauthorizedError) {
        setNeedsSecret(true);
      } else if (caught instanceof DemoQueryUnavailableError) {
        setDemoMiss(caught.available);
      } else if (caught instanceof InputRejectedError) {
        // The backend's own explanation, shown verbatim. It knows why it
        // refused; paraphrasing it into "something went wrong" would throw away
        // the only actionable part of the failure.
        setError(caught.message);
      } else {
        setError(
          caught instanceof Error ? caught.message : 'Something went wrong.',
        );
      }
    } finally {
      if (inFlight.current === controller) {
        setLoading(false);
        inFlight.current = null;
      }
    }
  }

  const indexDown = health !== null && !health.index_ready;
  const idle = !result && !loading && !error && !demoMiss;

  return (
    <div className="min-h-screen bg-surface-sunken">
      <Disclaimer />

      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-[76rem] items-center justify-between gap-4 px-5 py-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <Mark />
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold tracking-tight text-ink">
                Underwriting Copilot
              </p>
              <p className="truncate text-xs text-ink-subtle">
                Cross-carrier rate class comparison
              </p>
            </div>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[76rem] px-5 pb-20 pt-8">
        {/* The hero only appears before an answer. Once there is a result the
            reader is here to read it, and a restated pitch above it is just
            distance between them and the thing they asked for. */}
        {idle && (
          <div className="mb-7 max-w-2xl">
            <h1 className="text-display font-semibold text-ink">
              Which carrier writes this case?
            </h1>
            <p className="mt-3 text-lead text-ink-muted">
              Describe a prospect in plain language. Get the likely rate class
              from every carrier, ranked, each one carrying the guideline text
              it rests on and the page it came from.
            </p>
          </div>
        )}

        {DEMO_MODE && (
          <div className="note note-info mb-4">
            <p>
              <span className="font-semibold">Recorded responses.</span> This
              published build has no server behind it. Every answer is a real
              response from the pipeline captured from a live run. The rate
              classes, the quoted guideline text, the page citations, and the
              count of claims dropped in verification are all that run&rsquo;s
              own output, and the timing shown is what that run actually took.
            </p>
            <p className="mt-1.5 opacity-90">
              What a recording cannot do is answer a query nobody ran. Use an
              example below, or clone the repository and run it against the live
              backend to type your own.
            </p>
          </div>
        )}

        {indexDown && (
          <div className="note note-danger mb-4">
            The search index is not built, so no query can be answered. Run{' '}
            <code className="rounded bg-surface-inset px-1.5 py-0.5 font-mono text-[0.8125rem]">
              python -m app.ingest.build_index
            </code>
            .
          </div>
        )}

        {needsSecret && (
          <form
            className="card mb-4 p-5"
            onSubmit={(event) => {
              event.preventDefault();
              if (!secret.trim()) return;
              setSharedSecret(secret.trim());
              setNeedsSecret(false);
              setSecret('');
              void run(query);
            }}
          >
            <label
              htmlFor="secret"
              className="block text-sm font-semibold text-ink"
            >
              This demo is password protected
            </label>
            <p className="mt-1 text-xs text-ink-subtle">
              It calls a paid API, so it is not left open. The password is held
              in memory only and is not stored.
            </p>
            <div className="mt-3 flex gap-2">
              <input
                id="secret"
                type="password"
                value={secret}
                onChange={(event) => setSecret(event.target.value)}
                className="flex-1 rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink outline-none transition-colors focus:border-accent"
                autoComplete="current-password"
              />
              <button
                type="submit"
                className="rounded-lg bg-ink px-4 py-2 text-sm font-medium text-surface transition-opacity hover:opacity-90"
              >
                Unlock
              </button>
            </div>
          </form>
        )}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run(query);
          }}
          className="card overflow-hidden focus-within:border-accent"
        >
          <label htmlFor="query" className="sr-only">
            Describe a prospect
          </label>
          <textarea
            id="query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              // Enter submits; Shift+Enter adds a line. A description is
              // usually one line, so requiring a click would add a step to
              // every single query.
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void run(query);
              }
            }}
            rows={2}
            placeholder="55 year old male, A1c 7.1 on metformin, BMI 31, non-smoker, $500K 20-year term"
            className="w-full resize-y bg-transparent px-5 pb-3 pt-4 text-[0.9375rem] leading-relaxed text-ink outline-none placeholder:text-ink-faint"
          />
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line bg-surface-inset px-5 py-2.5">
            <p className="text-xs text-ink-faint">
              <kbd className="font-sans font-medium text-ink-subtle">Enter</kbd>{' '}
              to submit ·{' '}
              <kbd className="font-sans font-medium text-ink-subtle">
                Shift+Enter
              </kbd>{' '}
              for a new line
            </p>
            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="rounded-lg bg-cta px-5 py-2 text-sm font-semibold text-cta-ink transition-all hover:bg-cta-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {loading ? 'Reading guides…' : 'Compare carriers'}
            </button>
          </div>
        </form>

        {idle && (
          <div className="mt-5">
            <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-ink-faint">
              Try one
            </p>
            <div className="mt-2.5 flex flex-col gap-2">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => {
                    setQuery(example);
                    void run(example);
                  }}
                  className="group flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-2.5 text-left text-sm text-ink-muted transition-colors hover:border-line-strong hover:bg-surface-inset"
                >
                  <span
                    aria-hidden
                    className="text-ink-faint transition-colors group-hover:text-accent"
                  >
                    →
                  </span>
                  <span className="min-w-0 flex-1">{example}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {loading && (
          <div className="mt-5" aria-live="polite">
            <p className="text-sm text-ink-subtle">
              Reading each carrier&rsquo;s guide…
            </p>
            {/* A skeleton rather than a spinner: four carriers are queried in
                parallel and the shape of the answer is known in advance, so
                showing that shape sets the right expectation for a wait that
                runs to ten seconds or more. It mirrors the ranked rows exactly
                so nothing jumps when the answer lands. */}
            <div className="card mt-3 overflow-hidden">
              {[0, 1, 2, 3].map((index) => (
                <div
                  key={index}
                  className="flex animate-pulse items-center gap-4 border-b border-line px-5 py-4 last:border-b-0"
                  style={{ animationDelay: `${index * 90}ms` }}
                >
                  <div className="size-4 rounded bg-line" />
                  <div className="flex-1 space-y-2">
                    <div className="h-2.5 w-28 rounded bg-line" />
                    <div className="h-4 w-40 rounded bg-line" />
                  </div>
                  <div className="h-6 w-28 rounded-full bg-line" />
                </div>
              ))}
            </div>
          </div>
        )}

        {demoMiss && (
          <div role="status" className="note note-warn mt-5">
            <p className="font-semibold">No recording for that query.</p>
            <p className="mt-1">
              This published build answers from responses captured in advance,
              so it can only answer the queries that were run. These are the
              ones it has:
            </p>
            <ul className="mt-3 space-y-1.5">
              {demoMiss.map((available) => (
                <li key={available}>
                  <button
                    type="button"
                    onClick={() => {
                      setQuery(available);
                      void run(available);
                    }}
                    className="flex gap-2 text-left underline decoration-current/40 underline-offset-[3px] transition-colors hover:decoration-current"
                  >
                    <span aria-hidden className="opacity-60">
                      →
                    </span>
                    <span>{available}</span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {error && (
          <div role="alert" className="note note-danger mt-5">
            {error}
          </div>
        )}

        {result && !loading && (
          <div className="mt-5">
            <ResultView result={result} />
          </div>
        )}

        <footer className="mt-16 flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-line pt-5 text-xs text-ink-faint">
          <p>
            {health && (
              <>
                <span className="tabular">{health.chunk_count}</span> indexed
                passages across{' '}
                <span className="tabular">{health.carriers.length}</span>{' '}
                fictional carriers.
                {hasSharedSecret() && ' Session unlocked.'}
              </>
            )}
          </p>
          <p>
            A portfolio demonstration. Not for underwriting, sales, or advisory
            use.
          </p>
        </footer>
      </main>
    </div>
  );
}
