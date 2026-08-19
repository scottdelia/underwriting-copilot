/**
 * The legal banner required by section 8 of the brief.
 *
 * At the top of the page and not dismissible. A disclaimer the user can close
 * is a disclaimer that is absent from every screenshot anyone takes afterwards,
 * and this tool produces output that looks exactly like an underwriting
 * decision.
 *
 * The wording carries all four required points: illustrative only, no carrier
 * affiliation, guidelines change, verify against the current document. The
 * fictional-carrier line is specific to this build and matters more than the
 * rest -- a reader who assumes these carriers are real would take the numbers
 * seriously.
 *
 * Presentation note: this was a full-width slab that pushed the tool below the
 * fold. It is now a dense strip. Nothing was cut and nothing was hidden behind
 * a disclosure -- the top rule and the bold opening clause do the flagging that
 * a large block of colour was doing, in a fraction of the vertical space.
 */
export function Disclaimer() {
  return (
    <div
      role="note"
      className="border-b border-warn-line bg-warn-soft"
      style={{ boxShadow: 'inset 0 2px 0 0 var(--warn)' }}
    >
      <div className="mx-auto max-w-[76rem] px-5 py-2.5">
        <p
          className="text-[0.8125rem] leading-relaxed"
          style={{ color: 'color-mix(in oklab, var(--warn) 78%, var(--ink))' }}
        >
          <span className="font-semibold">Illustrative demonstration only.</span>{' '}
          Every carrier here is <span className="font-semibold">fictional</span>{' '}
          and every guideline is fabricated for this demo. Not affiliated with,
          endorsed by, or connected to any insurance carrier. Underwriting
          guidelines change frequently — always verify against the
          carrier&rsquo;s current official document. Nothing here is an offer, a
          quote, or underwriting advice.
        </p>
      </div>
    </div>
  );
}
