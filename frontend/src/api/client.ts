/**
 * Typed client for the backend.
 *
 * Two things here are deliberate.
 *
 * The Anthropic key is never in this bundle. Every model call happens
 * server-side; this file talks only to our own API. That is what section 7 of
 * the brief means by keeping the key server-side, and it is the reason the
 * frontend has no provider SDK in its dependency list at all.
 *
 * The query travels in a POST body, never a query string. A real query names a
 * person's medical conditions, and query strings land in access logs, proxy
 * logs, and browser history.
 */

import {
  DemoQueryUnavailableError,
  InputRejectedError,
  UnauthorizedError,
  type ComparisonResponse,
  type HealthResponse,
} from './types';

// Vite proxies /api to the backend in development (see vite.config.ts), so the
// same relative path works in dev and behind a single origin in production.
const BASE = '/api';

/**
 * Whether this build answers from recorded responses instead of a live backend.
 *
 * WHY THIS MODE EXISTS
 * --------------------
 * The deployed build is a static site with no server behind it. That is a
 * deliberate choice, not a limitation worked around: a portfolio link has to
 * answer when a stranger clicks it, and the two live options both fail that.
 * A free-tier backend sleeps and answers the first click a minute later. A
 * always-on one holds a paid API key behind a shared secret the reader does
 * not have.
 *
 * So the deployed build serves recordings. They are not fabricated: each one
 * is a real response from this pipeline, captured by tools/capture_fixtures.py
 * from a live run, carrying that run's own latency and its own
 * dropped-citation count. The live path is what runs locally and what the eval
 * measures.
 *
 * The limitation this cannot escape is that a recording cannot answer a query
 * nobody ran. That case throws DemoQueryUnavailableError rather than
 * pretending, and the UI says so.
 */
export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true';

/** Where the recordings live, relative to the deployed page. */
const FIXTURES = `${import.meta.env.BASE_URL}fixtures`;

interface FixtureIndexEntry {
  key: string;
  query: string;
  query_type: string;
}

let fixtureIndex: FixtureIndexEntry[] | null = null;

/**
 * Hash a query to its recording's filename.
 *
 * Must stay identical to `key_for` in tools/capture_fixtures.py: same
 * normalisation, same digest, same truncation. Whitespace is collapsed and
 * case folded so a reader who retypes an example with a different space still
 * lands on the recording rather than falling off the set.
 */
async function fixtureKey(query: string): Promise<string> {
  const normalized = query.replace(/\s+/g, ' ').trim().toLowerCase();
  const digest = await crypto.subtle.digest(
    'SHA-256',
    new TextEncoder().encode(normalized),
  );
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, 16);
}

/**
 * Whether a response is really the JSON file that was asked for.
 *
 * A 404 check is not enough. Static hosts serve a single-page app by answering
 * unknown paths with index.html and a 200, so a missing recording arrives
 * looking like a success and only fails later, inside JSON.parse, as
 * "Unexpected token '<'". GitHub Pages behaves this way and so does `vite
 * preview`, which is where this was caught.
 */
function isJsonResponse(response: Response): boolean {
  return (
    response.ok &&
    (response.headers.get('content-type') ?? '').includes('json')
  );
}

/** Load and memoise the list of recorded queries. */
async function loadFixtureIndex(): Promise<FixtureIndexEntry[]> {
  if (fixtureIndex !== null) {
    return fixtureIndex;
  }
  const response = await fetch(`${FIXTURES}/index.json`);
  if (!isJsonResponse(response)) {
    throw new Error('The demo data failed to load.');
  }
  const loaded: FixtureIndexEntry[] = await response.json();
  fixtureIndex = loaded;
  return loaded;
}

/** Return the recorded response for a query, or report what is available. */
async function compareFromFixture(query: string): Promise<ComparisonResponse> {
  const index = await loadFixtureIndex();
  const response = await fetch(`${FIXTURES}/${await fixtureKey(query)}.json`);
  if (!isJsonResponse(response)) {
    throw new DemoQueryUnavailableError(index.map((entry) => entry.query));
  }
  return response.json();
}

/**
 * The shared secret for the deployed demo, held in memory only.
 *
 * Deliberately not localStorage: this is a password for a demo behind a single
 * gate, and persisting it across sessions on a shared machine buys convenience
 * at a cost nobody asked for. A refresh asks again.
 */
let sharedSecret: string | null = null;

export function setSharedSecret(secret: string): void {
  sharedSecret = secret;
}

export function hasSharedSecret(): boolean {
  return sharedSecret !== null;
}

function headers(): HeadersInit {
  const base: HeadersInit = { 'Content-Type': 'application/json' };
  return sharedSecret ? { ...base, 'X-App-Secret': sharedSecret } : base;
}

/** Check whether the service is up and the index is loaded. */
export async function getHealth(): Promise<HealthResponse> {
  if (DEMO_MODE) {
    // Reported from the index the recordings were captured against, so the
    // footer shows the corpus the answers actually came from.
    return {
      status: 'recorded',
      index_ready: true,
      chunk_count: 34,
      carriers: ['cardinal', 'granite', 'meridian', 'northstar'],
    };
  }
  const response = await fetch(`${BASE}/health`);
  if (!response.ok) {
    throw new Error(`Health check failed (${response.status})`);
  }
  return response.json();
}

/**
 * Ask the backend to compare carriers for a described prospect.
 *
 * @param query - The agent's natural-language description.
 * @param signal - Abort signal, so a superseded request stops rather than
 *   racing the one that replaced it.
 * @throws UnauthorizedError when the shared-secret gate rejects the request.
 * @throws InputRejectedError when the backend refuses the input, carrying its
 *   explanation so the user sees the real reason rather than a generic failure.
 */
export async function compare(
  query: string,
  signal?: AbortSignal,
): Promise<ComparisonResponse> {
  if (DEMO_MODE) {
    return compareFromFixture(query);
  }

  const response = await fetch(`${BASE}/compare`, {
    method: 'POST',
    headers: headers(),
    body: JSON.stringify({ query }),
    signal,
  });

  if (response.status === 401) {
    throw new UnauthorizedError('Not authorized.');
  }
  if (response.status === 400) {
    const body = await response.json().catch(() => ({ detail: 'Invalid input.' }));
    throw new InputRejectedError(body.detail ?? 'Invalid input.');
  }
  if (response.status === 429) {
    throw new Error('Rate limit reached. Wait a little and try again.');
  }
  if (!response.ok) {
    // The backend deliberately returns terse messages for server-side
    // failures, so there is nothing more specific to surface here.
    throw new Error(`The service could not answer that (${response.status}).`);
  }
  return response.json();
}
