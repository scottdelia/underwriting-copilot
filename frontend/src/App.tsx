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
 * No router and no chat history, both of which the brief puts out of scope.
 * The absence of history is not only scope discipline — a stored transcript of
 * these queries would be a file of named people's medical details, which is not
 * something a demo should accumulate.
 */

/**
 * The seeded example queries.
 *
 * Imported rather than written here because tools/capture_fixtures.py reads the
 * same file: the published build looks recordings up by hashing the query, so
 * an example button whose text differs from the captured query by one
 * character silently stops working. It differed by one character ("5'6" versus
 * "5'06"") until this was made a single source.
 *
 * The last entry is out of scope on purpose. A demo that only seeds queries the
 * tool can answer hides the behaviour that matters most in a regulated context,
 * which is what it does when it cannot answer.
 */
const EXAMPLES: string[] = exampleQueries;

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
        // refused; paraphrasing it into "something went wrong" would throw
        // away the only actionable part of the failure.
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

  return (
    <div className="min-h-screen bg-slate-50">
      <Disclaimer />

      <main className="mx-auto max-w-6xl px-4 py-8">
        <header className="mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Underwriting Copilot
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Describe a prospect in plain language. Get likely rate classes
            across carriers, with the guideline text behind each answer.
          </p>
        </header>

        {DEMO_MODE && (
          <div
            role="note"
            className="mb-6 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900"
          >
            <p>
              <span className="font-medium">Recorded responses.</span> This
              published build has no server behind it. Each answer below is a
              real response from the pipeline, captured from a live run — the
              rate classes, the quoted guideline text, the page citations, and
              the count of claims dropped in verification are all that run&rsquo;s
              own output, and the timing shown is the time that run actually
              took.
            </p>
            <p className="mt-2">
              What a recording cannot do is answer a query nobody ran. Use one
              of the examples below, or clone the repository and run it against
              the live backend to type your own.
            </p>
          </div>
        )}

        {indexDown && (
          <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900">
            The search index is not built, so no query can be answered. Run{' '}
            <code className="rounded bg-rose-100 px-1">
              python -m app.ingest.build_index
            </code>
            .
          </div>
        )}

        {needsSecret && (
          <form
            className="mb-4 rounded-lg border border-slate-300 bg-white p-4"
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
              className="block text-sm font-medium text-slate-900"
            >
              This demo is password protected
            </label>
            <p className="mt-1 text-xs text-slate-500">
              It calls a paid API, so it is not left open. The password is held
              in memory only and is not stored.
            </p>
            <div className="mt-2 flex gap-2">
              <input
                id="secret"
                type="password"
                value={secret}
                onChange={(event) => setSecret(event.target.value)}
                className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
                autoComplete="current-password"
              />
              <button
                type="submit"
                className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
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
              // usually one line, so requiring a click to submit would add a
              // step to every single query.
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                void run(query);
              }
            }}
            rows={3}
            placeholder="55 year old male, A1c 7.1 on metformin, BMI 31, non-smoker, $500K 20-year term"
            className="w-full resize-y rounded-lg border border-slate-300 px-3 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-slate-500 focus:outline-none"
          />
          <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-slate-400">
              Enter to submit &middot; Shift+Enter for a new line
            </p>
            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="rounded-md bg-slate-900 px-5 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {loading ? 'Checking guides…' : 'Compare carriers'}
            </button>
          </div>
        </form>

        {!result && !loading && !error && !demoMiss && (
          <div className="mt-6">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
              Try
            </p>
            <div className="mt-2 flex flex-col gap-2">
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => {
                    setQuery(example);
                    void run(example);
                  }}
                  className="rounded-md border border-slate-200 bg-white px-3 py-2 text-left text-sm text-slate-700 hover:border-slate-400"
                >
                  {example}
                </button>
              ))}
            </div>
          </div>
        )}

        {loading && (
          <div className="mt-6 space-y-3" aria-live="polite">
            <p className="text-sm text-slate-500">
              Reading each carrier&rsquo;s guide…
            </p>
            {/* A skeleton rather than a spinner: four carriers are queried in
                parallel and the shape of the answer is known in advance, so
                showing that shape sets the right expectation for a wait that
                runs to ten seconds or more. */}
            <div className="grid gap-3 md:grid-cols-2">
              {[0, 1, 2, 3].map((index) => (
                <div
                  key={index}
                  className="h-28 animate-pulse rounded-lg border border-slate-200 bg-white"
                />
              ))}
            </div>
          </div>
        )}

        {demoMiss && (
          <div
            role="status"
            className="mt-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
          >
            <p className="font-medium">
              No recording for that query.
            </p>
            <p className="mt-1">
              This published build answers from responses captured in advance,
              so it can only answer the queries that were run. These are the
              ones it has:
            </p>
            <ul className="mt-2 space-y-1">
              {demoMiss.map((available) => (
                <li key={available}>
                  <button
                    type="button"
                    onClick={() => {
                      setQuery(available);
                      void run(available);
                    }}
                    className="text-left underline decoration-amber-400 underline-offset-2 hover:text-amber-950"
                  >
                    {available}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {error && (
          <div
            role="alert"
            className="mt-6 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900"
          >
            {error}
          </div>
        )}

        {result && !loading && (
          <div className="mt-6">
            <ResultView result={result} />
          </div>
        )}

        <footer className="mt-12 border-t border-slate-200 pt-4 text-xs text-slate-400">
          {health && (
            <p>
              {health.chunk_count} indexed passages across{' '}
              {health.carriers.length} fictional carriers.
              {hasSharedSecret() && ' Session unlocked.'}
            </p>
          )}
          <p className="mt-1">
            A portfolio demonstration. Not for underwriting, sales, or advisory
            use.
          </p>
        </footer>
      </main>
    </div>
  );
}
