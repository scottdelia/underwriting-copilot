/**
 * The legal banner required by section 8 of the brief.
 *
 * Deliberately at the top of the page and not dismissible. A disclaimer the
 * user can close is a disclaimer that is absent for every screenshot anyone
 * takes afterwards, and this tool produces output that looks exactly like an
 * underwriting decision.
 *
 * The wording carries all four required points: illustrative only, no carrier
 * affiliation, guidelines change, verify against the current document. The
 * fictional-carrier line is specific to this build and matters more than the
 * rest: a reader who assumes these are real carriers would take the numbers
 * seriously.
 */
export function Disclaimer() {
  return (
    <div
      role="note"
      className="border-b border-amber-300 bg-amber-50 px-4 py-3 text-amber-950"
    >
      <div className="mx-auto max-w-6xl text-sm leading-relaxed">
        <span className="font-semibold">
          Illustrative demonstration only.
        </span>{' '}
        Every carrier shown here is <span className="font-semibold">fictional</span>{' '}
        and every guideline is fabricated for this demo. This tool is not
        affiliated with, endorsed by, or connected to any insurance carrier.
        Underwriting guidelines change frequently — always verify against the
        carrier&rsquo;s current official document. Nothing here is an offer,
        a quote, or underwriting advice.
      </div>
    </div>
  );
}
