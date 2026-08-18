"""Rate class normalization across carriers.

WHY THIS IS A HAND-WRITTEN TABLE AND NOT A MODEL CALL
-----------------------------------------------------
Every carrier names its rate classes differently. Northstar's best class is
"Preferred Elite", Cardinal's is "Super Preferred NT", Granite Peak's is
"Elite". Comparing them requires mapping each onto a shared ladder.

Section 4 of the brief is explicit that this mapping must not be improvised by
the model at query time, and the reason is worth stating: the mapping is a
business judgment about which of two carriers' classes are comparable. If the
model decides it per query, the same prospect can be told two different things
on two consecutive runs, and there is no artifact anyone can review or correct.
A table in source control can be reviewed by an underwriter, diffed, and
blamed. That is the whole argument.

WHERE THIS MAPPING IS LOSSY, STATED UP FRONT
--------------------------------------------
Granite Peak publishes five non-rated classes; the canonical ladder has four.
"Preferred Best" and "Preferred" both map to `preferred`, which collapses a
distinction Granite Peak actually makes and prices. The tool shows the carrier's
own label alongside the normalized tier so a reader can see the original, but
any cross-carrier comparison involving those two Granite Peak classes is
coarser than the source document. This is a real limitation of forcing a shared
ladder onto independent taxonomies, not a bug to be fixed by a better table.
"""

from __future__ import annotations

import logging
import re

from app.models.schemas import CANONICAL_ORDER

logger = logging.getLogger(__name__)

# Authoritative per-carrier mapping, transcribed by reading each guide's rate
# class list. Keys are lower-cased and stripped of footnote markers; see
# `_clean_label`. Adding a carrier means adding an entry here deliberately, not
# discovering one at runtime.
CARRIER_RATE_CLASS_MAP: dict[str, dict[str, str]] = {
    "northstar": {
        "preferred elite": "preferred_plus",
        "preferred": "preferred",
        "standard plus": "standard_plus",
        "standard": "standard",
    },
    "cardinal": {
        "super preferred nt": "preferred_plus",
        "preferred nt": "preferred",
        "select nt": "standard_plus",
        "standard nt": "standard",
    },
    "meridian": {
        "preferred plus": "preferred_plus",
        "preferred": "preferred",
        "standard plus": "standard_plus",
        "standard": "standard",
    },
    "granite": {
        "elite": "preferred_plus",
        # Two labels collapse onto one tier. See the module docstring.
        "preferred best": "preferred",
        "preferred": "preferred",
        "standard plus": "standard_plus",
        "standard": "standard",
    },
}

# Values that appear inside condition threshold tables rather than in a build
# chart header. These are carrier-independent conventions: a "Table 2" rating
# means the same kind of thing everywhere, even though the exact debit differs.
_TABLE_RATING_RE = re.compile(r"^table\s+[a-z0-9]+$")
_DECLINE_TERMS = {
    "decline",
    "declined",
    "not eligible",
    "ineligible",
    "not available",
    "no offer",
}
# Phrases that are outcomes but not classes. Mapping these to a tier would
# assert something the guide does not say, so they resolve to None and the
# caller decides what to do with an unclassifiable cell.
_NON_CLASS_TERMS = {
    "individual consideration",
    "individual underwriting",
    "refer to underwriter",
    "n/a",
    "—",
    "-",
    "",
}

# Footnote markers attached to a label in a printed header, e.g. "Standard *".
_FOOTNOTE_MARKERS = "*†‡§¶⁑⁂"


def _clean_label(label: str) -> str:
    """Reduce a printed rate class label to its comparable form.

    Strips footnote markers, collapses whitespace, and lower-cases. The printed
    header reads "Standard *" because the last column carries a footnote; the
    footnote is not part of the class name.

    Args:
        label: The label exactly as printed.

    Returns:
        A normalized lookup key.
    """
    cleaned = label.strip().strip(_FOOTNOTE_MARKERS).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.lower()


def normalize_rate_class(carrier_id: str, label: str) -> str | None:
    """Map a carrier's own rate class label onto the canonical ladder.

    Resolution order is deliberate. The carrier's own table wins, because a
    carrier is free to use a common word like "Select" for an uncommon tier.
    Only labels the carrier table does not cover fall through to the generic
    conventions.

    Args:
        carrier_id: The carrier the label came from.
        label: The label as printed in the guide.

    Returns:
        A canonical class key, or None when the label cannot be mapped
        confidently. None is a real answer: it means "this guide said something
        the ladder does not represent", and inventing a tier for it would put a
        claim on screen that no document supports.
    """
    key = _clean_label(label)

    if not key or key in _NON_CLASS_TERMS:
        return None

    carrier_map = CARRIER_RATE_CLASS_MAP.get(carrier_id, {})
    if key in carrier_map:
        return carrier_map[key]

    if key in _DECLINE_TERMS:
        return "decline"

    if _TABLE_RATING_RE.match(key):
        return "table_rated"

    # A label nobody anticipated. Logged rather than guessed at, so that an
    # unmapped class shows up as a gap in the extraction report instead of
    # silently becoming whichever tier happened to look closest.
    logger.warning(
        "unmapped rate class label for carrier %s: %r", carrier_id, label
    )
    return None


def canonical_rank(canonical_class: str) -> int:
    """Return the ladder position of a canonical class. Lower is better.

    Args:
        canonical_class: A canonical class key.

    Returns:
        The rank, where 1 is the best available offer.

    Raises:
        KeyError: If the class is not on the ladder.
    """
    return CANONICAL_ORDER[canonical_class]


def worst_of(*canonical_classes: str | None) -> str | None:
    """Return the least favourable of several canonical classes.

    Underwriting combines independently: build limits and condition limits are
    each evaluated on their own and the worse of the two governs. This encodes
    that rule in one place so the synthesis layer does not re-derive it.

    Args:
        *canonical_classes: Classes to combine. None values are ignored, since
            an unmappable outcome is an absence of evidence rather than a bad
            outcome.

    Returns:
        The worst class supplied, or None if none were supplied.
    """
    known = [c for c in canonical_classes if c is not None]
    if not known:
        return None
    return max(known, key=canonical_rank)


def known_labels(carrier_id: str) -> set[str]:
    """Return the cleaned rate class labels known for a carrier.

    Used by extraction validation to detect a vision result whose header row
    does not match the carrier's published classes, which is the signature of a
    misread or shifted column.
    """
    return set(CARRIER_RATE_CLASS_MAP.get(carrier_id, {}))
