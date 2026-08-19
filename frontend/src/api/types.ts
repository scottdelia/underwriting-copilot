/**
 * Types mirroring the backend's Pydantic response models.
 *
 * Hand-written rather than generated, because the surface is small and a
 * generator would be another moving part in a demo. The shapes here must track
 * `backend/app/models/verdict.py`; the one that matters most is `Claim`, which
 * pairs a statement with its citation so that the UI has no way to render an
 * assertion without the evidence behind it.
 */

/** The shared rate class ladder. Lower index is a better offer. */
export const CANONICAL_ORDER = [
  'preferred_plus',
  'preferred',
  'standard_plus',
  'standard',
  'table_rated',
  'decline',
] as const;

export type CanonicalClass = (typeof CANONICAL_ORDER)[number];

/** Human-facing labels for the normalized tiers. */
export const CANONICAL_LABELS: Record<CanonicalClass, string> = {
  preferred_plus: 'Preferred Plus tier',
  preferred: 'Preferred tier',
  standard_plus: 'Standard Plus tier',
  standard: 'Standard tier',
  table_rated: 'Table rated',
  decline: 'Not eligible',
};

export interface Citation {
  carrier_id: string;
  doc_id: string;
  page: number;
  excerpt: string;
}

export interface Claim {
  statement: string;
  citation: Citation;
}

export interface CarrierVerdict {
  carrier_id: string;
  carrier_name: string;
  determination: 'classified' | 'insufficient_information';
  carrier_label: string | null;
  canonical_class: CanonicalClass | null;
  qualifying: Claim[];
  disqualifying: Claim[];
  missing_information: string[];
  abstention_reason: string | null;
}

export interface DirectAnswer {
  kind: 'build_lookup' | 'prose_question';
  claims: Claim[];
  note: string | null;
}

export interface ProspectProfile {
  age?: number;
  gender?: 'male' | 'female';
  height_inches?: number;
  weight_lbs?: number;
  bmi?: number;
  conditions?: string[];
  a1c?: number;
  medications?: string[];
  tobacco?: boolean;
  coverage_amount_usd?: number;
  product_type?: string;
}

export interface ComparisonResponse {
  query: string;
  query_type: string;
  routing_reason: string;
  profile: ProspectProfile;
  verdicts: CarrierVerdict[];
  answer: DirectAnswer | null;
  retrieved_pages: Record<string, number[]>;
  unverified_claims_dropped: number;
  latency_ms: number;
  model: string;
}

export interface HealthResponse {
  status: string;
  index_ready: boolean;
  chunk_count: number;
  carriers: string[];
}

/** Raised when the shared-secret gate rejects a request. */
export class UnauthorizedError extends Error {}

/** Raised when the backend refuses the input, carrying its explanation. */
export class InputRejectedError extends Error {}

/**
 * Raised by the static demo build for a query it has no recording of.
 *
 * Carries the queries it *does* have, so the UI can offer them rather than
 * leaving the reader guessing which inputs the deployed build can answer.
 */
export class DemoQueryUnavailableError extends Error {
  // Declared and assigned separately rather than as a constructor parameter
  // property: tsconfig sets erasableSyntaxOnly, which bars the shorthand
  // because it emits code rather than being erased with the types.
  available: string[];

  constructor(available: string[]) {
    super('That query is not in the recorded demo set.');
    this.available = available;
  }
}
