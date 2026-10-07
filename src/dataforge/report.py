"""DF-005 client-facing quality report and writer facade.

This module renders ``data_quality_report.html`` (appendix section 8.6) and owns
the bounded DF-005 ``write_evidence_outputs`` facade, which writes exactly the
four authorized artifacts:

``rejected_rows.csv``, ``audit_log.csv``, ``cleaning_summary.json``,
``data_quality_report.html``.

It reports decisions taken by DF-002/DF-003/DF-004; it never takes one. The HTML
is self-contained (no CDN, no script, no framework) and deterministic: every
value comes from upstream evidence, so repeated runs are byte-identical. No
wall-clock timestamp, hostname, username, absolute path, or random identifier is
emitted. All data-derived text is HTML-escaped.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .audit import (
    AuditEvent,
    RejectedRow,
    assemble_audit_events,
    build_cleaning_summary,
    build_input_file_entries,
    build_rejected_rows,
    rejected_disposition_counts,
    write_audit_log,
    write_cleaning_summary,
    write_rejected_rows,
)
from .ingest import IngestionResult
from .normalize import NormalizationResult
from .validate import (
    QUARANTINED,
    DEDUPLICATED,
    REFERENCE_DEDUPLICATED,
    REFERENCE_REJECTED,
    ValidationResult,
)

_STYLE = """\
body { font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
       margin: 2rem auto; max-width: 60rem; padding: 0 1rem; color: #1c1c1c;
       line-height: 1.5; }
h1 { font-size: 1.6rem; margin-bottom: 0.25rem; }
h2 { font-size: 1.15rem; margin-top: 2rem; border-bottom: 1px solid #d8d8d8;
     padding-bottom: 0.25rem; }
table.data { border-collapse: collapse; margin: 0.75rem 0; width: 100%; }
table.data th, table.data td { border: 1px solid #d8d8d8; padding: 0.35rem 0.6rem;
     text-align: left; vertical-align: top; }
table.data th { background: #f4f4f4; font-weight: 600; }
table.data td.num { text-align: right; }
p.meta { color: #555; margin-top: 0; }
p.equation { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
     background: #f4f4f4; padding: 0.5rem 0.75rem; }
p.result { font-weight: 700; }
p.result.pass { color: #146c2e; }
p.result.fail { color: #a11212; }
ul { padding-left: 1.25rem; }
code { background: #f4f4f4; padding: 0 0.2rem; }
"""


def _esc(value: object) -> str:
    """HTML-escape every data-derived value before interpolation."""

    return html.escape("" if value is None else str(value), quote=True)


def _int(value: object) -> int:
    return value if isinstance(value, int) else int(str(value))


def _share(count: int, total: int) -> str:
    """Display-only ratio of two appendix-frozen counts, with its denominator stated."""

    if total <= 0:
        return "n/a"
    return f"{100.0 * count / total:.1f}%"


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    parts = ['<table class="data">', "<thead><tr>"]
    parts.extend(f"<th>{_esc(header)}</th>" for header in headers)
    parts.append("</tr></thead>")
    parts.append("<tbody>")
    for row in rows:
        parts.append("<tr>")
        parts.extend(f"<td>{_esc(cell)}</td>" for cell in row)
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def _counts_table(mapping: Mapping[str, object]) -> str:
    if not mapping:
        return "<p>None recorded.</p>"
    return _table(
        ("Rule code", "Audit events"),
        [(code, _int(count)) for code, count in sorted(mapping.items())],
    )


def build_report_html(
    *,
    validation: ValidationResult,
    summary: Mapping[str, object],
    rejected_rows: Sequence[RejectedRow],
    input_files: Sequence[Mapping[str, object]],
    full_pipeline: bool = False,
) -> str:
    """Render the appendix 8.6 report sections in their frozen order."""

    run_metadata = summary["run_metadata"]
    assert isinstance(run_metadata, Mapping)
    transaction_counts = summary["transaction_counts"]
    assert isinstance(transaction_counts, Mapping)
    reference_counts = summary["reference_counts"]
    assert isinstance(reference_counts, Mapping)
    rule_counts = summary["rule_counts"]
    assert isinstance(rule_counts, Mapping)
    normalization_counts = summary["normalization_counts"]
    assert isinstance(normalization_counts, Mapping)
    outputs = summary["outputs"]
    assert isinstance(outputs, Sequence)

    total = _int(transaction_counts["input"])
    accepted = _int(transaction_counts["accepted"])
    quarantined = _int(transaction_counts["quarantined"])
    deduplicated = _int(transaction_counts["deduplicated"])
    reconciles = bool(transaction_counts["reconciled"])

    customers = reference_counts["customers"]
    products = reference_counts["products"]
    assert isinstance(customers, Mapping) and isinstance(products, Mapping)

    disposition_counts = rejected_disposition_counts(rejected_rows)
    reference_input_rows = sum(
        len([row for row in rejected_rows if row.record_type == record_type])
        for record_type in ("customers", "products")
    )

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en">')
    parts.append("<head>")
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>DataForge data quality report</title>")
    parts.append(f"<style>\n{_STYLE}</style>")
    parts.append("</head>")
    parts.append("<body>")

    # 1. Title, specification version, frozen demo date/timezone.
    parts.append("<h1>DataForge data quality report</h1>")
    parts.append(
        '<p class="meta">'
        f"Specification version {_esc(summary['specification_version'])}"
        f" &middot; frozen demo date {_esc(run_metadata['demo_date'])}"
        f" ({_esc(run_metadata['timezone'])})"
        f" &middot; pipeline version {_esc(run_metadata['pipeline_version'])}"
        f" &middot; demo seed {_esc(run_metadata['demo_seed'])}"
        "</p>"
    )
    parts.append(
        "<p>This report explains what DataForge changed, rejected, and "
        "deduplicated for a bounded synthetic demo corpus, and where every record "
        "came from. It reports decisions; it does not make them.</p>"
    )

    # 2. Processed input files and transaction/reference input counts.
    parts.append("<h2>1. Processed inputs</h2>")
    parts.append(
        _table(
            ("Input file", "Record type", "Data rows", "Selected sheets"),
            [
                (
                    entry["path"],
                    entry["record_type"],
                    _int(entry["row_count"]),
                    ", ".join(entry["selected_sheets"]) if entry["selected_sheets"] else "(none)",
                )
                for entry in input_files
            ],
        )
    )
    parts.append(
        f"<p>Transaction input rows: <strong>{total}</strong>. "
        f"Customer reference input rows: <strong>{_int(customers['input'])}</strong>. "
        f"Product reference input rows: <strong>{_int(products['input'])}</strong>. "
        "Reference rows validate foreign keys only and are never merged into "
        "transaction rows.</p>"
    )

    # 3. Accepted, quarantined, and deduplicated transaction counts.
    parts.append("<h2>2. Transaction dispositions</h2>")
    parts.append(
        _table(
            ("Disposition", "Rows", "Share of input"),
            (
                ("Input", total, "100.0%" if total else "n/a"),
                ("Accepted", accepted, _share(accepted, total)),
                ("Quarantined", quarantined, _share(quarantined, total)),
                ("Deduplicated", deduplicated, _share(deduplicated, total)),
            ),
        )
    )
    parts.append(
        "<p>Every ingested transaction row received exactly one of these terminal "
        "dispositions. Shares are display-only ratios of the counts above over the "
        "input row count; no other quality score is defined for this demo.</p>"
    )
    parts.append(
        f"<p>The non-accepted evidence file <code>rejected_rows.csv</code> holds "
        f"<strong>{len(rejected_rows)}</strong> data rows: "
        f"<strong>{disposition_counts.get(QUARANTINED, 0)}</strong> quarantined transactions, "
        f"<strong>{disposition_counts.get(DEDUPLICATED, 0)}</strong> deduplicated transactions, "
        f"<strong>{disposition_counts.get(REFERENCE_REJECTED, 0)}</strong> rejected customer/product "
        f"references, and <strong>{disposition_counts.get(REFERENCE_DEDUPLICATED, 0)}</strong> "
        "deduplicated references. Reference rows are outside the transaction "
        "reconciliation below; the larger evidence count does not widen the "
        "transaction equation.</p>"
    )

    # 4. The G2 equation and explicit pass/fail.
    parts.append("<h2>3. Reconciliation</h2>")
    parts.append(
        '<p class="equation">'
        f"{total} input rows = {accepted} accepted + {quarantined} quarantined "
        f"+ {deduplicated} deduplicated"
        "</p>"
    )
    outcome = "PASS" if reconciles else "FAIL"
    parts.append(f'<p class="result {"pass" if reconciles else "fail"}">Reconciliation: {outcome}</p>')
    parts.append(
        "<p>Transaction reconciliation counts source rows exactly once. Rows with "
        "several failures still count once, and reference rows are excluded "
        f"({reference_input_rows} reference rows appear in "
        "<code>rejected_rows.csv</code> but not in this equation).</p>"
    )

    # 5. Defect/rule counts and normalization counts.
    parts.append("<h2>4. Defect and normalization counts</h2>")
    parts.append("<p>Audit events by defect or action code.</p>")
    parts.append(_counts_table(rule_counts))
    parts.append("<p>Audit events by normalization code.</p>")
    parts.append(_counts_table(normalization_counts))

    # 6. Reference-data validity counts and fuzzy candidate count.
    parts.append("<h2>5. Reference data quality</h2>")
    parts.append(
        _table(
            ("Reference table", "Input", "Usable", "Rejected", "Deduplicated"),
            (
                ("customers", _int(customers["input"]), _int(customers["valid"]),
                 _int(customers["rejected"]), _int(customers["deduplicated"])),
                ("products", _int(products["input"]), _int(products["valid"]),
                 _int(products["rejected"]), _int(products["deduplicated"])),
            ),
        )
    )
    parts.append(
        "<p>Each reference table obeys input = usable + rejected + deduplicated. "
        f"Near-duplicate customer name candidates flagged in evidence: "
        f"<strong>{len(validation.fuzzy_candidates)}</strong>. A fuzzy candidate is "
        "evidence only: customer identities are never merged and sales customer "
        "identifiers are never rewritten.</p>"
    )

    # 7. Safeguards.
    parts.append("<h2>6. Safeguards</h2>")
    parts.append("<ul>")
    parts.append(
        "<li><strong>Provenance</strong>: every record keeps its source file, "
        "source sheet, and source row, so any output row can be traced back to the "
        "exact cell range it came from.</li>"
    )
    parts.append(
        "<li><strong>Quarantine</strong>: rows that break a frozen business rule are "
        "separated, never silently corrected, and always carry a stable rule code "
        "plus a readable reason.</li>"
    )
    parts.append(
        "<li><strong>Audit trail</strong>: every material normalization, rejection, "
        "deduplication, reference failure, and flag produces a machine-readable "
        "<code>audit_log.csv</code> event with original and cleaned values.</li>"
    )
    parts.append(
        "<li><strong>No silent deletion</strong>: every ingested transaction row "
        "reconciles exactly once, and a deduplicated row remains explainable "
        "through the survivor it points to.</li>"
    )
    parts.append(
        "<li><strong>Deterministic</strong>: identical inputs always produce "
        "byte-identical evidence; no timestamps or run identifiers are recorded.</li>"
    )
    parts.append("</ul>")

    # 8. Output artifact names.
    parts.append("<h2>7. Output artifacts</h2>")
    parts.append("<ul>")
    for name in outputs:
        text = str(name)
        if text.startswith("cleaned_sales") and not full_pipeline:
            parts.append(
                f"<li><code>{_esc(text)}</code> &mdash; DF-006 final cleaned-data "
                "deliverable, not produced by this phase.</li>"
            )
        else:
            parts.append(f"<li><a href=\"{_esc(text)}\"><code>{_esc(text)}</code></a></li>")
    parts.append("</ul>")
    if full_pipeline:
        parts.append(
            "<p>All six frozen v1 artifacts were generated and verified by the "
            "completed DataForge pipeline.</p>"
        )
    else:
        parts.append(
            "<p><code>cleaned_sales.csv</code> and <code>cleaned_sales.xlsx</code> are "
            "part of the frozen v1 deliverable contract but are written only by DF-006. "
            "They do not exist yet, and this phase makes no claim that they do.</p>"
        )

    # 9. Bounded-demo limitations and v1 non-goals.
    parts.append("<h2>8. Limitations and non-goals</h2>")
    parts.append(
        "<p>DataForge v1 is a bounded portfolio demonstration over one frozen "
        "synthetic corpus. It is not a production data platform.</p>"
    )
    parts.append("<ul>")
    parts.append(
        "<li>The supported schema is fixed: seven sales columns, four customer "
        "reference columns, and four product reference columns, with an exhaustive "
        "documented alias list. Arbitrary spreadsheets are out of scope.</li>"
    )
    parts.append(
        "<li>Date, currency, country, and category handling is limited to the "
        "documented formats and aliases, and the demo clock is frozen at "
        "2026-10-07 (Europe/Paris) rather than read from the machine.</li>"
    )
    parts.append(
        "<li>Near-duplicate customer names are flagged for human review only. No "
        "entity resolution, machine learning, anomaly detection, or automatic "
        "merging is performed.</li>"
    )
    parts.append(
        "<li>No database, warehouse, cloud deployment, API service, dashboard "
        "platform, streaming system, or LLM is involved.</li>"
    )
    parts.append(
        "<li>Raw inputs are immutable; the pipeline never edits source files, and "
        "it makes no claim of zero data loss beyond this reconciled corpus.</li>"
    )
    parts.append("</ul>")

    parts.append("</body>")
    parts.append("</html>")
    return "\n".join(parts) + "\n"


@dataclass(frozen=True)
class EvidenceOutputs:
    """Paths and evidence produced by one DF-005 write."""

    directory: Path
    rejected_rows: Path
    audit_log: Path
    cleaning_summary: Path
    data_quality_report: Path
    rejected_rows_data: tuple[RejectedRow, ...]
    audit_events: tuple[AuditEvent, ...]
    summary: Mapping[str, object]

    def paths(self) -> tuple[Path, ...]:
        return (
            self.rejected_rows,
            self.audit_log,
            self.cleaning_summary,
            self.data_quality_report,
        )


def write_evidence_outputs(
    normalized: NormalizationResult,
    validation: ValidationResult,
    output_dir: str | Path,
    *,
    ingestion: IngestionResult,
    input_dir: str | Path,
    full_pipeline: bool = False,
) -> EvidenceOutputs:
    """Write the four authorized DF-005 artifacts and nothing else.

    ``ingestion`` and ``input_dir`` are required because appendix 8.3 needs the
    per-file digests, selected sheets, and row counts that only DF-002 produced;
    DF-005 will not guess them.

    The writer creates ``output_dir`` if needed and overwrites only its own four
    known filenames. It never deletes or recurses, and it never writes
    ``cleaned_sales.*``.
    """

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise NotADirectoryError(f"DF-005 output path is not a directory: {directory}")

    audit_events = assemble_audit_events(normalized, validation)
    rejected_rows = build_rejected_rows(validation, ingestion)
    input_files = build_input_file_entries(ingestion, input_dir)
    summary = build_cleaning_summary(validation, ingestion, input_dir, audit_events)
    html_text = build_report_html(
        validation=validation,
        summary=summary,
        rejected_rows=rejected_rows,
        input_files=input_files,
        full_pipeline=full_pipeline,
    )

    rejected_path = write_rejected_rows(directory / "rejected_rows.csv", rejected_rows)
    audit_path = write_audit_log(directory / "audit_log.csv", audit_events)
    summary_path = write_cleaning_summary(directory / "cleaning_summary.json", summary)
    report_path = directory / "data_quality_report.html"
    report_path.write_text(html_text, encoding="utf-8")

    return EvidenceOutputs(
        directory=directory,
        rejected_rows=rejected_path,
        audit_log=audit_path,
        cleaning_summary=summary_path,
        data_quality_report=report_path,
        rejected_rows_data=rejected_rows,
        audit_events=audit_events,
        summary=summary,
    )
