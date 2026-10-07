"""DF-005 audit and reconciliation evidence for the bounded DataForge v1 corpus.

DF-005 reports decisions; it never makes them. This module serializes structured
evidence already produced by DF-002/DF-003/DF-004 into the adopted appendix
section 8 artifacts:

* ``rejected_rows.csv`` (8.1) -- every non-accepted record;
* ``audit_log.csv`` (8.2) -- every material normalization and action;
* ``cleaning_summary.json`` (8.3) -- frozen reconciliation and counts.

The client-readable ``data_quality_report.html`` (8.6) lives in
:mod:`dataforge.report`, which also owns the DF-005 ``write_evidence_outputs``
facade.

DF-005 never re-parses currency, dates, identifiers, quantities, or prices, never
resolves duplicates, never validates foreign keys, and never chooses a terminal
disposition. Every audit row traces back to exactly one upstream evidence object:

* a DF-003 ``NormalizationEvent`` (material normalization, ``NORMALIZE_*``);
* a DF-003 ``NormalizationIssue`` whose action type is ``FLAG`` (retained, never
  terminal);
* a DF-004 ``ValidationEvent`` (failure, deduplication, reference-failure, or
  fuzzy flag).

DF-003 ``VALIDATION_FAILURE`` issues are deliberately not copied: DF-004 already
emits one failure event per detected failure, and appendix section 8.2 requires
one event per applicable failure rather than one per detection layer.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Mapping, Sequence

from .dedupe import SourceRef
from .ingest import IngestionResult
from .normalize import DEMO_DATE, TIMEZONE, NormalizationResult
from .schema import SCHEMAS, RecordType
from .validate import (
    DEDUPLICATED,
    QUARANTINED,
    REFERENCE_DEDUPLICATED,
    REFERENCE_REJECTED,
    ValidationResult,
)

# --------------------------------------------------------------------------- #
# Frozen metadata (appendix section 8.3)
# --------------------------------------------------------------------------- #

SPECIFICATION_VERSION = "1.0.1"
PIPELINE_VERSION = "1.0.0"
DEMO_SEED = 1007
SOURCE_ORDERING = "nfc_relative_path_then_sheet_then_row"

#: Appendix 8.3 freezes the v1 deliverable list. It names the two cleaned-sales
#: artifacts that only DF-006 produces; DF-005 must not claim to have written them.
V1_OUTPUT_NAMES: tuple[str, ...] = (
    "cleaned_sales.xlsx",
    "cleaned_sales.csv",
    "rejected_rows.csv",
    "audit_log.csv",
    "cleaning_summary.json",
    "data_quality_report.html",
)

#: The four artifacts this phase is authorized to produce.
DF005_ARTIFACT_NAMES: tuple[str, ...] = (
    "rejected_rows.csv",
    "audit_log.csv",
    "cleaning_summary.json",
    "data_quality_report.html",
)

# --------------------------------------------------------------------------- #
# Frozen code catalogue order (appendix section 7)
# --------------------------------------------------------------------------- #

FROZEN_CODE_ORDER: tuple[str, ...] = (
    "UNEXPECTED_INPUT",
    "UNEXPECTED_SHEET",
    "UNEXPECTED_COLUMN",
    "MISSING_REQUIRED_COLUMN",
    "ALIAS_COLLISION",
    "NORMALIZE_WHITESPACE",
    "NORMALIZE_IDENTIFIER",
    "NORMALIZE_COUNTRY",
    "NORMALIZE_CURRENCY",
    "NORMALIZE_DATE",
    "NORMALIZE_EMAIL",
    "NORMALIZE_CATEGORY",
    "MISSING_ORDER_ID",
    "MISSING_CUSTOMER_ID",
    "MISSING_PRODUCT_ID",
    "INVALID_IDENTIFIER",
    "INVALID_ORDER_DATE",
    "FUTURE_ORDER_DATE",
    "INVALID_QUANTITY",
    "INVALID_UNIT_PRICE",
    "DUPLICATE_EXACT",
    "DUPLICATE_KEY_CONFLICT",
    "REFERENCE_DUPLICATE_EXACT",
    "REFERENCE_KEY_CONFLICT",
    "UNKNOWN_CUSTOMER_REFERENCE",
    "UNKNOWN_PRODUCT_REFERENCE",
    "INVALID_EMAIL_FORMAT",
    "UNKNOWN_COUNTRY",
    "UNKNOWN_CATEGORY",
    "FUZZY_CUSTOMER_CANDIDATE",
)
_CODE_RANK = {code: index for index, code in enumerate(FROZEN_CODE_ORDER)}

#: Appendix 8.2: ``field_name`` is empty for row/group events.
GROUP_EVENT_CODES = frozenset(
    {
        "DUPLICATE_EXACT",
        "DUPLICATE_KEY_CONFLICT",
        "REFERENCE_DUPLICATE_EXACT",
        "REFERENCE_KEY_CONFLICT",
    }
)

# Frozen processing stages used only to order audit evidence (appendix section 6).
_STAGE_NORMALIZATION = 0
_STAGE_HARD_VALIDATION = 1
_STAGE_DUPLICATE_RESOLUTION = 2
_STAGE_REFERENCE_VALIDATION = 3

_REFERENCE_STAGE_CODES = frozenset(
    {
        "REFERENCE_DUPLICATE_EXACT",
        "REFERENCE_KEY_CONFLICT",
        "UNKNOWN_CUSTOMER_REFERENCE",
        "UNKNOWN_PRODUCT_REFERENCE",
        "FUZZY_CUSTOMER_CANDIDATE",
    }
)

# --------------------------------------------------------------------------- #
# Column contracts (appendix sections 8.1 and 8.2)
# --------------------------------------------------------------------------- #

REJECTED_COLUMNS: tuple[str, ...] = (
    "record_type",
    "source_file",
    "source_sheet",
    "source_row",
    "terminal_disposition",
    "primary_rule_code",
    "rule_codes",
    "reason",
    "order_id",
    "customer_id",
    "product_id",
    "order_date",
    "quantity",
    "unit_price_eur",
    "country",
    "customer_name",
    "email",
    "product_name",
    "category",
)

#: Business columns follow the eight leading evidence columns.
REJECTED_BUSINESS_COLUMNS: tuple[str, ...] = REJECTED_COLUMNS[8:]

AUDIT_COLUMNS: tuple[str, ...] = (
    "event_index",
    "record_type",
    "source_file",
    "source_sheet",
    "source_row",
    "field_name",
    "original_value",
    "cleaned_value",
    "code",
    "action_type",
    "related_source_file",
    "related_source_sheet",
    "related_source_row",
    "message",
)


def serialize_value(value: object) -> str:
    """Deterministic evidence text for one upstream value; null is empty.

    A midnight ``datetime`` is rendered as its calendar date so an Excel date cell
    keeps date semantics; a ``datetime`` carrying a time component is rendered in
    full, because DF-003 treats it as a bounded-date violation rather than a date.
    """

    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.time() == time.min and value.tzinfo is None:
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


# --------------------------------------------------------------------------- #
# Audit log (appendix section 8.2)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuditEvent:
    """One row of ``audit_log.csv`` in the exact appendix 8.2 column order."""

    event_index: int
    record_type: RecordType
    source_file: str
    source_sheet: str
    source_row: int
    field_name: str
    original_value: str
    cleaned_value: str
    code: str
    action_type: str
    related_source_file: str = ""
    related_source_sheet: str = ""
    related_source_row: int = 0
    message: str = ""

    @property
    def ref(self) -> SourceRef:
        return SourceRef(self.source_file, self.source_sheet, self.source_row)

    @property
    def related(self) -> SourceRef | None:
        if not self.related_source_file and not self.related_source_sheet and not self.related_source_row:
            return None
        return SourceRef(self.related_source_file, self.related_source_sheet, self.related_source_row)

    def as_row(self) -> tuple[str, ...]:
        return (
            str(self.event_index),
            self.record_type,
            self.source_file,
            self.source_sheet,
            str(self.source_row),
            self.field_name,
            self.original_value,
            self.cleaned_value,
            self.code,
            self.action_type,
            self.related_source_file,
            self.related_source_sheet,
            str(self.related_source_row) if self.related_source_row else "",
            self.message,
        )

    @property
    def fingerprint(self) -> tuple[object, ...]:
        """Identity used to prove audit completeness and the absence of fabrication."""

        return (
            self.record_type,
            self.source_file,
            self.source_sheet,
            self.source_row,
            self.field_name,
            self.original_value,
            self.cleaned_value,
            self.code,
            self.action_type,
            self.related_source_file,
            self.related_source_sheet,
            self.related_source_row,
            self.message,
        )


def _field_rank(record_type: RecordType, field_name: str) -> int:
    if not field_name:
        return -1
    fields = SCHEMAS[record_type].fields
    return fields.index(field_name) if field_name in fields else len(fields)


def _event_stage(record_type: RecordType, code: str) -> int:
    """Reporting-level stage classification; it never changes a disposition."""

    if code in {"DUPLICATE_EXACT", "DUPLICATE_KEY_CONFLICT"}:
        return _STAGE_DUPLICATE_RESOLUTION
    if code in _REFERENCE_STAGE_CODES:
        return _STAGE_REFERENCE_VALIDATION
    return _STAGE_HARD_VALIDATION if record_type == "sales" else _STAGE_REFERENCE_VALIDATION


def _order_key(record_type: RecordType, ref: SourceRef, stage: int, code: str, field_name: str) -> tuple:
    """Appendix 8.2 ordering: source order, then stage, then catalogue priority."""

    return (
        ref.sort_key,
        stage,
        _CODE_RANK.get(code, len(FROZEN_CODE_ORDER)),
        _field_rank(record_type, field_name),
        field_name,
        code,
    )


def assemble_audit_events(
    normalization: NormalizationResult,
    validation: ValidationResult,
) -> tuple[AuditEvent, ...]:
    """Merge DF-003 and DF-004 evidence into one deterministically ordered log.

    No evidence is synthesized and no decision is recomputed. Every returned row
    is a faithful serialization of exactly one upstream evidence object.
    """

    keyed: list[tuple[tuple, AuditEvent]] = []

    for event in normalization.events:
        ref = SourceRef(event.source_file, event.source_sheet, event.source_row)
        keyed.append(
            (
                _order_key(event.record_type, ref, _STAGE_NORMALIZATION, event.code, event.field_name),
                AuditEvent(
                    event_index=0,
                    record_type=event.record_type,
                    source_file=event.source_file,
                    source_sheet=event.source_sheet,
                    source_row=event.source_row,
                    field_name=event.field_name,
                    original_value=serialize_value(event.original_value),
                    cleaned_value=serialize_value(event.cleaned_value),
                    code=event.code,
                    action_type=event.action_type,
                    message=event.message,
                ),
            )
        )

    for issue in normalization.issues:
        if issue.action_type != "FLAG":
            continue
        ref = SourceRef(issue.source_file, issue.source_sheet, issue.source_row)
        keyed.append(
            (
                _order_key(issue.record_type, ref, _STAGE_NORMALIZATION, issue.code, issue.field_name),
                AuditEvent(
                    event_index=0,
                    record_type=issue.record_type,
                    source_file=issue.source_file,
                    source_sheet=issue.source_sheet,
                    source_row=issue.source_row,
                    field_name=issue.field_name,
                    original_value=serialize_value(issue.original_value),
                    cleaned_value=serialize_value(issue.normalized_value),
                    code=issue.code,
                    action_type=issue.action_type,
                    message=issue.message,
                ),
            )
        )

    for event in validation.validation_events:
        field_name = "" if event.code in GROUP_EVENT_CODES else event.field_name
        related = event.related
        ref = event.ref
        keyed.append(
            (
                _order_key(
                    event.record_type,
                    ref,
                    _event_stage(event.record_type, event.code),
                    event.code,
                    field_name,
                ),
                AuditEvent(
                    event_index=0,
                    record_type=event.record_type,
                    source_file=event.source_file,
                    source_sheet=event.source_sheet,
                    source_row=event.source_row,
                    field_name=field_name,
                    original_value=serialize_value(event.original_value),
                    cleaned_value=serialize_value(event.cleaned_value),
                    code=event.code,
                    action_type=event.action_type,
                    related_source_file=related.source_file if related else "",
                    related_source_sheet=related.source_sheet if related else "",
                    related_source_row=related.source_row if related else 0,
                    message=event.message,
                ),
            )
        )

    keyed.sort(key=lambda item: item[0])
    return tuple(
        replace(event, event_index=index)
        for index, (_, event) in enumerate(keyed, start=1)
    )


def write_audit_log(path: str | Path, events: Sequence[AuditEvent]) -> Path:
    """Write ``audit_log.csv`` with LF endings and RFC 4180 quoting."""

    target = Path(path)
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(AUDIT_COLUMNS)
        for event in events:
            writer.writerow(event.as_row())
    return target


# --------------------------------------------------------------------------- #
# Rejected rows (appendix section 8.1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RejectedRow:
    """One row of ``rejected_rows.csv`` in the exact appendix 8.1 column order."""

    record_type: RecordType
    ref: SourceRef
    terminal_disposition: str
    primary_rule_code: str
    rule_codes: tuple[str, ...]
    reason: str
    business: Mapping[str, str]

    def as_row(self) -> tuple[str, ...]:
        return (
            self.record_type,
            self.ref.source_file,
            self.ref.source_sheet,
            str(self.ref.source_row),
            self.terminal_disposition,
            self.primary_rule_code,
            "|".join(self.rule_codes),
            self.reason,
            *(self.business.get(column, "") for column in REJECTED_BUSINESS_COLUMNS),
        )


def _raw_row_index(ingestion: IngestionResult) -> dict[tuple[str, str, str, int], Mapping[str, object]]:
    index: dict[tuple[str, str, str, int], Mapping[str, object]] = {}
    for table in ingestion.tables:
        for row in table.rows:
            key = (
                table.record_type,
                str(row["_source_file"]),
                str(row["_source_sheet"]),
                int(row["_source_row"]),
            )
            index[key] = row
    return index


def _business_values(
    record_type: RecordType,
    ref: SourceRef,
    raw_rows: Mapping[tuple[str, str, str, int], Mapping[str, object]],
) -> dict[str, str]:
    """Appendix 8.1: the source row's original values, non-applicable fields empty."""

    key = (record_type, ref.source_file, ref.source_sheet, ref.source_row)
    if key not in raw_rows:
        raise KeyError(f"no ingested source row for {record_type} {ref.as_tuple()!r}")
    raw = raw_rows[key]
    fields = SCHEMAS[record_type].fields
    return {
        column: (serialize_value(raw.get(column)) if column in fields else "")
        for column in REJECTED_BUSINESS_COLUMNS
    }


def build_rejected_rows(
    validation: ValidationResult,
    ingestion: IngestionResult,
) -> tuple[RejectedRow, ...]:
    """Build the appendix 8.1 non-accepted-record artifact.

    Contains every ``QUARANTINED`` and ``DEDUPLICATED`` transaction plus every
    ``REFERENCE_REJECTED`` and ``REFERENCE_DEDUPLICATED`` reference row. Reference
    rows never enter the transaction G2 equation.
    """

    raw_rows = _raw_row_index(ingestion)
    rows: list[RejectedRow] = []

    def _reason(ref: SourceRef, rule_codes: Sequence[str]) -> str:
        wanted = set(rule_codes)
        ordered: list[str] = []
        for event in validation.validation_events:
            if event.ref == ref and event.code in wanted:
                ordered.append(event.message)
        return "; ".join(text for text in ordered if text)

    def _rejected_row(
        record_type: RecordType,
        ref: SourceRef,
        disposition: str,
        rule_codes: Sequence[str],
        primary: str | None,
    ) -> RejectedRow:
        return RejectedRow(
            record_type=record_type,
            ref=ref,
            terminal_disposition=disposition,
            primary_rule_code=primary or "",
            rule_codes=tuple(rule_codes),
            reason=_reason(ref, rule_codes),
            business=_business_values(record_type, ref, raw_rows),
        )

    for outcome in validation.quarantined:
        rows.append(_rejected_row("sales", outcome.ref, QUARANTINED, outcome.rule_codes, outcome.primary_rule_code))

    for outcome in validation.deduplicated:
        rows.append(_rejected_row("sales", outcome.ref, DEDUPLICATED, outcome.rule_codes, outcome.primary_rule_code))

    for outcome in validation.reference_results:
        if outcome.disposition not in {REFERENCE_REJECTED, REFERENCE_DEDUPLICATED}:
            continue
        rows.append(
            _rejected_row(
                outcome.record_type,
                outcome.ref,
                outcome.disposition,
                outcome.rule_codes,
                outcome.primary_rule_code,
            )
        )

    # Appendix section 8: rows use section 2 source order.
    rows.sort(key=lambda row: row.ref.sort_key)
    return tuple(rows)


def write_rejected_rows(path: str | Path, rows: Sequence[RejectedRow]) -> Path:
    """Write ``rejected_rows.csv`` with LF endings and RFC 4180 quoting."""

    target = Path(path)
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(REJECTED_COLUMNS)
        for row in rows:
            writer.writerow(row.as_row())
    return target


def rejected_disposition_counts(rows: Sequence[RejectedRow]) -> dict[str, int]:
    """Deterministic counts of a rejected-row set by terminal disposition."""

    counter = Counter(row.terminal_disposition for row in rows)
    return {name: counter.get(name, 0) for name in sorted(counter)}


# --------------------------------------------------------------------------- #
# Cleaning summary (appendix section 8.3)
# --------------------------------------------------------------------------- #


def build_input_file_entries(
    ingestion: IngestionResult,
    input_dir: str | Path,
) -> list[dict[str, object]]:
    """Deterministic appendix 8.3 input-file array in source-file order."""

    root = Path(input_dir)
    entries: list[dict[str, object]] = []
    for metadata in sorted(ingestion.file_metadata, key=lambda item: item.source_file.encode("utf-8")):
        digest = hashlib.sha256((root / metadata.source_file).read_bytes()).hexdigest()
        entries.append(
            {
                "path": metadata.source_file,
                "sha256": digest,
                "record_type": metadata.record_type,
                "selected_sheets": [metadata.selected_sheet] if metadata.file_format == "xlsx" else [],
                "row_count": metadata.row_count,
            }
        )
    return entries


def _reference_counts(values: object) -> dict[str, int]:
    as_dict = values.as_dict()  # type: ignore[attr-defined]
    return {
        "input": as_dict["input"],
        "valid": as_dict["valid"],
        "rejected": as_dict["rejected"],
        "deduplicated": as_dict["deduplicated"],
    }


def build_cleaning_summary(
    validation: ValidationResult,
    ingestion: IngestionResult,
    input_dir: str | Path,
    audit_events: Sequence[AuditEvent],
) -> dict[str, object]:
    """Build the frozen appendix 8.3 summary contract.

    ``rule_counts`` and ``normalization_counts`` count the audit events actually
    written to ``audit_log.csv``, so the summary can never disagree with the log.
    """

    normalization_counter = Counter(
        event.code for event in audit_events if event.code.startswith("NORMALIZE_")
    )
    rule_counter = Counter(
        event.code for event in audit_events if not event.code.startswith("NORMALIZE_")
    )

    return {
        "specification_version": SPECIFICATION_VERSION,
        "run_metadata": {
            "pipeline_version": PIPELINE_VERSION,
            "demo_seed": DEMO_SEED,
            "demo_date": DEMO_DATE.isoformat(),
            "timezone": TIMEZONE,
            "source_ordering": SOURCE_ORDERING,
        },
        "input_files": build_input_file_entries(ingestion, input_dir),
        "transaction_counts": validation.reconciliation.as_dict(),
        "reference_counts": {
            "customers": _reference_counts(validation.customer_counts),
            "products": _reference_counts(validation.product_counts),
        },
        "rule_counts": dict(sorted(rule_counter.items())),
        "normalization_counts": dict(sorted(normalization_counter.items())),
        "outputs": list(V1_OUTPUT_NAMES),
    }


def write_cleaning_summary(path: str | Path, summary: Mapping[str, object]) -> Path:
    """Write ``cleaning_summary.json``: UTF-8, two-space indent, sorted keys, final LF."""

    target = Path(path)
    target.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
