"""SQLite persistence for extracted build charts, condition rules, and anomalies.

Two things are worth calling out.

**Every query is parameterized.** There is no string-built SQL anywhere in this
file or in `retrieval/structured.py`. Carrier ids and condition keys come from
extraction output, which originates in a model reading a third-party document --
which is to say, they are untrusted values that happen to look like identifiers.

**Anomalies are stored alongside the data they concern.** A run that kept only
its successes would report a better error rate than it earned. Keeping the
rejections in the same database means the extraction report is computed from
what actually happened rather than from what survived.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.models.extraction import ExtractionAnomaly
from app.models.schemas import BuildChartEntry, ConditionRule

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS build_chart_entries (
    id              INTEGER PRIMARY KEY,
    carrier_id      TEXT    NOT NULL,
    doc_id          TEXT    NOT NULL,
    page            INTEGER NOT NULL,
    height_inches   INTEGER NOT NULL,
    rate_class      TEXT    NOT NULL,
    canonical_class TEXT    NOT NULL,
    max_weight_lbs  INTEGER NOT NULL,
    gender          TEXT    NOT NULL,
    notes           TEXT,
    -- A chart split across pages repeats its header, and a boundary row can be
    -- transcribed twice. The constraint makes that idempotent rather than
    -- doubling the row and skewing any count computed from this table.
    UNIQUE (carrier_id, height_inches, rate_class, gender)
);

-- The lookup shape the retrieval layer uses: given a carrier, a sex, and a
-- height, find the classes whose limit the applicant is inside.
CREATE INDEX IF NOT EXISTS idx_build_lookup
    ON build_chart_entries (carrier_id, gender, height_inches);

CREATE TABLE IF NOT EXISTS condition_rules (
    id                    INTEGER PRIMARY KEY,
    carrier_id            TEXT NOT NULL,
    doc_id                TEXT NOT NULL,
    page                  INTEGER NOT NULL,
    condition             TEXT NOT NULL,
    criteria              TEXT NOT NULL,
    best_available_class  TEXT NOT NULL,
    canonical_best_class  TEXT NOT NULL,
    disqualifiers_json    TEXT NOT NULL,
    source_excerpt        TEXT NOT NULL,
    UNIQUE (carrier_id, condition, page)
);

CREATE INDEX IF NOT EXISTS idx_condition_lookup
    ON condition_rules (carrier_id, condition);

-- Threshold tables are stored as transcribed rather than parsed into columns.
-- Their shape varies per carrier: one guide keys diabetes on A1c alone, another
-- on A1c crossed with BMI, a third on A1c crossed with duration. Forcing those
-- into a shared column layout would require inventing a schema none of them
-- share, and would discard the refinement each table actually encodes. Keeping
-- the transcription intact lets synthesis quote the applicable row.
CREATE TABLE IF NOT EXISTS threshold_tables (
    id            INTEGER PRIMARY KEY,
    carrier_id    TEXT NOT NULL,
    doc_id        TEXT NOT NULL,
    page          INTEGER NOT NULL,
    title         TEXT,
    columns_json  TEXT NOT NULL,
    rows_json     TEXT NOT NULL,
    footnotes_json TEXT NOT NULL,
    UNIQUE (carrier_id, page, title)
);

CREATE INDEX IF NOT EXISTS idx_threshold_lookup
    ON threshold_tables (carrier_id, page);

CREATE TABLE IF NOT EXISTS extraction_anomalies (
    id         INTEGER PRIMARY KEY,
    carrier_id TEXT NOT NULL,
    doc_id     TEXT NOT NULL,
    page       INTEGER NOT NULL,
    severity   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL
);
"""


@contextmanager
def connect(db_path: Path, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with row access by column name.

    Args:
        db_path: Path to the database file.
        read_only: Open without creating the file or its schema. Used by the
            query path so a missing database surfaces as a clear error rather
            than as an empty database that answers every question with silence.

    Yields:
        An open connection, committed and closed on exit.

    Raises:
        FileNotFoundError: If read_only is set and the database does not exist.
    """
    if read_only and not db_path.exists():
        raise FileNotFoundError(
            f"no structured store at {db_path}. Build it with:\n"
            f"    python -m app.ingest.build_tables"
        )

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialize(db_path: Path, *, reset: bool = False) -> None:
    """Create the schema, optionally clearing existing rows first.

    Args:
        db_path: Path to the database file.
        reset: Delete all rows before creating the schema. On by default in the
            ingestion path, because a partial re-extraction layered over a
            previous run produces a store matching no single version of the
            corpus.
    """
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)
        if reset:
            for table in (
                "build_chart_entries",
                "condition_rules",
                "threshold_tables",
                "extraction_anomalies",
            ):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed literal
            logger.info("cleared existing structured store at %s", db_path)


def insert_build_entries(db_path: Path, entries: list[BuildChartEntry]) -> int:
    """Insert build chart rows, ignoring duplicates.

    Args:
        db_path: Path to the database file.
        entries: Validated entries.

    Returns:
        The number of rows actually inserted.
    """
    if not entries:
        return 0
    with connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM build_chart_entries").fetchone()[0]
        conn.executemany(
            """
            INSERT OR IGNORE INTO build_chart_entries
                (carrier_id, doc_id, page, height_inches, rate_class,
                 canonical_class, max_weight_lbs, gender, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    e.carrier_id,
                    e.doc_id,
                    e.page,
                    e.height_inches,
                    e.rate_class,
                    e.canonical_class,
                    e.max_weight_lbs,
                    e.gender,
                    e.notes,
                )
                for e in entries
            ],
        )
        after = conn.execute("SELECT COUNT(*) FROM build_chart_entries").fetchone()[0]
    return after - before


def insert_condition_rules(db_path: Path, rules: list[ConditionRule]) -> int:
    """Insert condition rules, ignoring duplicates.

    Args:
        db_path: Path to the database file.
        rules: Validated rules.

    Returns:
        The number of rows actually inserted.
    """
    if not rules:
        return 0
    with connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM condition_rules").fetchone()[0]
        conn.executemany(
            """
            INSERT OR IGNORE INTO condition_rules
                (carrier_id, doc_id, page, condition, criteria,
                 best_available_class, canonical_best_class,
                 disqualifiers_json, source_excerpt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.carrier_id,
                    r.doc_id,
                    r.page,
                    r.condition,
                    r.criteria,
                    r.best_available_class,
                    r.canonical_best_class,
                    json.dumps(r.disqualifiers),
                    r.source_excerpt,
                )
                for r in rules
            ],
        )
        after = conn.execute("SELECT COUNT(*) FROM condition_rules").fetchone()[0]
    return after - before


def insert_threshold_tables(
    db_path: Path,
    carrier_id: str,
    doc_id: str,
    tables: list[tuple[int, dict]],
) -> int:
    """Store transcribed threshold tables.

    Args:
        db_path: Path to the database file.
        carrier_id: Owning carrier.
        doc_id: Source document filename.
        tables: (page, table dict) pairs as returned by extraction.

    Returns:
        The number of rows actually inserted.
    """
    if not tables:
        return 0
    with connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) FROM threshold_tables").fetchone()[0]
        conn.executemany(
            """
            INSERT OR IGNORE INTO threshold_tables
                (carrier_id, doc_id, page, title, columns_json, rows_json,
                 footnotes_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    carrier_id,
                    doc_id,
                    page,
                    table.get("title"),
                    json.dumps(table.get("columns", [])),
                    json.dumps(table.get("rows", [])),
                    json.dumps(table.get("footnotes", [])),
                )
                for page, table in tables
            ],
        )
        after = conn.execute("SELECT COUNT(*) FROM threshold_tables").fetchone()[0]
    return after - before


def insert_anomalies(db_path: Path, anomalies: list[ExtractionAnomaly]) -> int:
    """Record extraction anomalies.

    Args:
        db_path: Path to the database file.
        anomalies: Anomalies from this run.

    Returns:
        The number of rows inserted.
    """
    if not anomalies:
        return 0
    with connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO extraction_anomalies
                (carrier_id, doc_id, page, severity, kind, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (a.carrier_id, a.doc_id, a.page, a.severity, a.kind, a.detail)
                for a in anomalies
            ],
        )
    return len(anomalies)


def counts(db_path: Path) -> dict[str, int]:
    """Return row counts per table, for reporting.

    Args:
        db_path: Path to the database file.

    Returns:
        A mapping of table name to row count.
    """
    with connect(db_path, read_only=True) as conn:
        return {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - fixed literal
            ).fetchone()[0]
            for table in (
                "build_chart_entries",
                "condition_rules",
                "threshold_tables",
                "extraction_anomalies",
            )
        }
