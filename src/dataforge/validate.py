"""DF-004 deterministic hard validation, duplicate resolution, and reference validation.

This module is the first DataForge stage allowed to assign a terminal disposition.
It applies the adopted v1.0.1 rules to the DF-003 normalized corpus; it never
inspects preregistered expected-output fixtures.

Frozen processing precedence (appendix section 6)::

    INGEST -> SCHEMA -> NORMALIZE -> HARD VALIDATION
           -> DUPLICATE RESOLUTION -> REFERENCE VALIDATION -> ACCEPT

Each successfully ingested transaction row receives exactly one terminal
disposition -- ``ACCEPTED``, ``QUARANTINED``, or ``DEDUPLICATED`` -- and the G2
invariant ``input = accepted + quarantined + deduplicated`` is asserted by
:class:`ValidationResult`. Reconciliation failure fails closed with
:class:`ValidationInvariantError`.

Client-facing artifacts (``rejected_rows.csv``, ``audit_log.csv``,
``cleaning_summary.json``, ``data_quality_report.html``) belong to DF-005/DF-006.
DF-004 emits only internal, machine-testable evidence.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Literal, Mapping, Sequence

from rapidfuzz import fuzz

from .dedupe import (
    BUSINESS_FIELDS,
    DUPLICATE_EXACT,
    DUPLICATE_KEY_CONFLICT,
    DuplicateEntry,
    SourceRef,
    resolve_duplicates,
)
from .normalize import DEMO_DATE, TIMEZONE, NormalizationIssue, NormalizationResult
from .schema import RecordType

# --------------------------------------------------------------------------- #
# Frozen dispositions
# --------------------------------------------------------------------------- #

ACCEPTED = "ACCEPTED"
QUARANTINED = "QUARANTINED"
DEDUPLICATED = "DEDUPLICATED"

TerminalDisposition = Literal["ACCEPTED", "QUARANTINED", "DEDUPLICATED"]
TERMINAL_DISPOSITIONS: tuple[TerminalDisposition, ...] = (ACCEPTED, QUARANTINED, DEDUPLICATED)

REFERENCE_VALID = "REFERENCE_VALID"
REFERENCE_REJECTED = "REFERENCE_REJECTED"
REFERENCE_DEDUPLICATED = "REFERENCE_DEDUPLICATED"

# --------------------------------------------------------------------------- #
# Frozen code catalogue (appendix section 7, DF-004 owned subset)
# --------------------------------------------------------------------------- #

HARD_FAILURE_ORDER: tuple[str, ...] = (
    "MISSING_ORDER_ID",
    "MISSING_CUSTOMER_ID",
    "MISSING_PRODUCT_ID",
    "INVALID_IDENTIFIER",
    "INVALID_ORDER_DATE",
    "FUTURE_ORDER_DATE",
    "INVALID_QUANTITY",
    "INVALID_UNIT_PRICE",
)
HARD_FAILURE_RANK = {code: index for index, code in enumerate(HARD_FAILURE_ORDER)}
HARD_FAILURE_CODES = frozenset(HARD_FAILURE_ORDER)

#: Field order used to break ties between two ``INVALID_IDENTIFIER`` failures.
IDENTIFIER_FIELD_ORDER: tuple[str, ...] = ("order_id", "customer_id", "product_id")

REFERENCE_DUPLICATE_EXACT = "REFERENCE_DUPLICATE_EXACT"
REFERENCE_KEY_CONFLICT = "REFERENCE_KEY_CONFLICT"
UNKNOWN_CUSTOMER_REFERENCE = "UNKNOWN_CUSTOMER_REFERENCE"
UNKNOWN_PRODUCT_REFERENCE = "UNKNOWN_PRODUCT_REFERENCE"
FUZZY_CUSTOMER_CANDIDATE = "FUZZY_CUSTOMER_CANDIDATE"

#: DF-003 flag codes. They are retained evidence and never terminal here.
NON_TERMINAL_FLAG_CODES = frozenset(
    {"INVALID_EMAIL_FORMAT", "UNKNOWN_COUNTRY", "UNKNOWN_CATEGORY"}
)

RULE_CATALOGUE: tuple[tuple[str, str], ...] = (
    ("MISSING_ORDER_ID", "VALIDATION_FAILURE"),
    ("MISSING_CUSTOMER_ID", "VALIDATION_FAILURE"),
    ("MISSING_PRODUCT_ID", "VALIDATION_FAILURE"),
    ("INVALID_IDENTIFIER", "VALIDATION_FAILURE"),
    ("INVALID_ORDER_DATE", "VALIDATION_FAILURE"),
    ("FUTURE_ORDER_DATE", "VALIDATION_FAILURE"),
    ("INVALID_QUANTITY", "VALIDATION_FAILURE"),
    ("INVALID_UNIT_PRICE", "VALIDATION_FAILURE"),
    (DUPLICATE_EXACT, "DEDUPLICATION"),
    (DUPLICATE_KEY_CONFLICT, "VALIDATION_FAILURE"),
    (REFERENCE_DUPLICATE_EXACT, "DEDUPLICATION"),
    (REFERENCE_KEY_CONFLICT, "REFERENCE_FAILURE"),
    (UNKNOWN_CUSTOMER_REFERENCE, "REFERENCE_FAILURE"),
    (UNKNOWN_PRODUCT_REFERENCE, "REFERENCE_FAILURE"),
    (FUZZY_CUSTOMER_CANDIDATE, "FLAG"),
)
ACTION_TYPES: Mapping[str, str] = MappingProxyType(dict(RULE_CATALOGUE))
_RULE_RANK = {code: index for index, (code, _) in enumerate(RULE_CATALOGUE)}

# --------------------------------------------------------------------------- #
# Reference policy
# --------------------------------------------------------------------------- #

CUSTOMER_FIELDS: tuple[str, ...] = ("customer_id", "customer_name", "email", "country")
PRODUCT_FIELDS: tuple[str, ...] = ("product_id", "product_name", "category", "unit_price_eur")
REFERENCE_FIELDS: Mapping[RecordType, tuple[str, ...]] = MappingProxyType(
    {"customers": CUSTOMER_FIELDS, "products": PRODUCT_FIELDS}
)
REFERENCE_KEY_FIELD: Mapping[RecordType, str] = MappingProxyType(
    {"customers": "customer_id", "products": "product_id"}
)

#: Appendix section 6.3: which DF-003 key issues exclude a reference row outright.
REFERENCE_KEY_ISSUES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "customer_id": frozenset({"MISSING_CUSTOMER_ID", "INVALID_IDENTIFIER"}),
        "product_id": frozenset({"MISSING_PRODUCT_ID", "INVALID_IDENTIFIER"}),
    }
)

#: Appendix section 5.7 frozen fuzzy threshold.
FUZZY_THRESHOLD = 90.0

# Evidence stage ranks: source order, then processing stage, then code priority.
_STAGE_HARD_VALIDATION = 0
_STAGE_DUPLICATE_RESOLUTION = 1
_STAGE_REFERENCE_VALIDATION = 2
_STAGE_FLAG = 3


class ValidationInvariantError(ValueError):
    """A terminal-disposition or reconciliation invariant was violated."""


# --------------------------------------------------------------------------- #
# Canonical comparison forms
# --------------------------------------------------------------------------- #


def _text(value: object) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _integer(value: object) -> object:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal):
        return int(value) if value.is_finite() and value == value.to_integral_value() else str(value)
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value.is_integer() else str(value)
    text = "" if value is None else str(value)
    return int(text) if re.fullmatch(r"[+-]?\d+", text) else text


def _money(value: object) -> object:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, Decimal):
        return value if value.is_finite() else str(value)
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else str(value)
    text = "" if value is None else str(value)
    try:
        parsed = Decimal(text)
    except (InvalidOperation, ValueError):
        return text
    return parsed if parsed.is_finite() else text


def canonical_business_key(row: Mapping[str, object]) -> tuple[object, ...]:
    """Semantic identity of the seven normalized sales business fields.

    Values are already canonical after DF-003, so equality of this tuple is
    semantic equality. Provenance is excluded by contract.
    """

    key = (
        _text(row.get("order_id")),
        _text(row.get("customer_id")),
        _text(row.get("product_id")),
        _text(row.get("order_date")),
        _integer(row.get("quantity")),
        _money(row.get("unit_price_eur")),
        _text(row.get("country")),
    )
    assert len(key) == len(BUSINESS_FIELDS), "canonical business key field count drifted"
    return key


def canonical_reference_key(record_type: RecordType, row: Mapping[str, object]) -> tuple[object, ...]:
    if record_type == "customers":
        return (
            _text(row.get("customer_id")),
            _text(row.get("customer_name")),
            _text(row.get("email")),
            _text(row.get("country")),
        )
    return (
        _text(row.get("product_id")),
        _text(row.get("product_name")),
        _text(row.get("category")),
        _money(row.get("unit_price_eur")),
    )


def _parse_iso_date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Hard validation (appendix section 6.1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class HardFailure:
    """One detected hard business-rule failure for a transaction row.

    ``origin`` separates a DF-003 normalization failure (``NORMALIZATION_ISSUE``)
    from a successfully normalized value that violates a business rule
    (``BUSINESS_RULE``). The two concepts are never collapsed.
    """

    code: str
    field_name: str
    reason: str
    origin: Literal["NORMALIZATION_ISSUE", "BUSINESS_RULE"]


def _identifier_rank(code: str, field_name: str) -> int:
    if code != "INVALID_IDENTIFIER":
        return 0
    return (
        IDENTIFIER_FIELD_ORDER.index(field_name)
        if field_name in IDENTIFIER_FIELD_ORDER
        else len(IDENTIFIER_FIELD_ORDER)
    )


def _hard_reason(code: str, field_name: str, value: object) -> str:
    if code == "INVALID_UNIT_PRICE":
        parsed = _money(value)
        if isinstance(parsed, Decimal) and parsed < 0:
            return f"unit_price_eur is negative ({value!s})."
        return "unit_price_eur is missing, malformed, ambiguous, or over-precise."
    return {
        "MISSING_ORDER_ID": "order_id is missing.",
        "MISSING_CUSTOMER_ID": "customer_id is missing.",
        "MISSING_PRODUCT_ID": "product_id is missing.",
        "INVALID_IDENTIFIER": f"{field_name} violates the bounded identifier pattern.",
        "INVALID_ORDER_DATE": "order_date is missing, unparseable, or impossible.",
        "FUTURE_ORDER_DATE": f"order_date is later than the frozen demo date {DEMO_DATE.isoformat()}.",
        "INVALID_QUANTITY": "quantity is missing, non-integer, or not greater than zero.",
    }[code]


def hard_failures(
    row: Mapping[str, object],
    issue_codes: Mapping[str, str],
) -> tuple[HardFailure, ...]:
    """Evaluate every applicable hard check and return failures in frozen order.

    ``issue_codes`` maps field name to the DF-003 issue code already emitted for
    that field, so normalization failures are consumed instead of reparsed. All
    checks run; a row violating several rules still yields one terminal
    disposition downstream.
    """

    detected: list[tuple[int, int, HardFailure]] = []

    for field_name, code in issue_codes.items():
        if code not in HARD_FAILURE_CODES:
            continue
        detected.append(
            (
                HARD_FAILURE_RANK[code],
                _identifier_rank(code, field_name),
                HardFailure(code, field_name, _hard_reason(code, field_name, row.get(field_name)), "NORMALIZATION_ISSUE"),
            )
        )

    # Business rules applied to values that normalized successfully.
    if "quantity" not in issue_codes:
        value = row.get("quantity")
        if not (isinstance(value, int) and not isinstance(value, bool) and value > 0):
            detected.append(
                (
                    HARD_FAILURE_RANK["INVALID_QUANTITY"],
                    0,
                    HardFailure("INVALID_QUANTITY", "quantity", _hard_reason("INVALID_QUANTITY", "quantity", value), "BUSINESS_RULE"),
                )
            )

    if "unit_price_eur" not in issue_codes:
        value = _money(row.get("unit_price_eur"))
        if not (isinstance(value, Decimal) and value >= 0):
            detected.append(
                (
                    HARD_FAILURE_RANK["INVALID_UNIT_PRICE"],
                    0,
                    HardFailure("INVALID_UNIT_PRICE", "unit_price_eur", _hard_reason("INVALID_UNIT_PRICE", "unit_price_eur", row.get("unit_price_eur")), "BUSINESS_RULE"),
                )
            )

    if "order_date" not in issue_codes:
        parsed = _parse_iso_date(row.get("order_date"))
        if parsed is not None and parsed > DEMO_DATE:
            detected.append(
                (
                    HARD_FAILURE_RANK["FUTURE_ORDER_DATE"],
                    0,
                    HardFailure("FUTURE_ORDER_DATE", "order_date", _hard_reason("FUTURE_ORDER_DATE", "order_date", row.get("order_date")), "BUSINESS_RULE"),
                )
            )

    detected.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in detected)


# --------------------------------------------------------------------------- #
# Evidence model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ValidationEvent:
    """Internal, machine-testable DF-004 evidence for one material action.

    Field names mirror the appendix ``audit_log.csv`` columns so DF-005 can index
    and render them; DF-004 deliberately assigns no ``event_index`` and writes no
    client-facing artifact.
    """

    code: str
    action_type: str
    record_type: RecordType
    source_file: str
    source_sheet: str
    source_row: int
    field_name: str = ""
    original_value: object = None
    cleaned_value: object = None
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


def _emit(
    code: str,
    record_type: RecordType,
    ref: SourceRef,
    *,
    stage: int,
    field_name: str = "",
    original_value: object = None,
    cleaned_value: object = None,
    related: SourceRef | None = None,
    message: str = "",
) -> tuple[tuple[object, ...], ValidationEvent]:
    event = ValidationEvent(
        code=code,
        action_type=ACTION_TYPES[code],
        record_type=record_type,
        source_file=ref.source_file,
        source_sheet=ref.source_sheet,
        source_row=ref.source_row,
        field_name=field_name,
        original_value=original_value,
        cleaned_value=cleaned_value,
        related_source_file=related.source_file if related else "",
        related_source_sheet=related.source_sheet if related else "",
        related_source_row=related.source_row if related else 0,
        message=message,
    )
    key = (ref.sort_key, stage, _RULE_RANK.get(code, len(RULE_CATALOGUE)), field_name)
    return key, event


# --------------------------------------------------------------------------- #
# Terminal result contract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TransactionOutcome:
    """Exactly one terminal disposition for one transaction provenance identity."""

    ref: SourceRef
    disposition: TerminalDisposition
    rule_codes: tuple[str, ...] = ()
    primary_rule_code: str | None = None
    survivor: SourceRef | None = None
    values: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        if self.disposition not in TERMINAL_DISPOSITIONS:
            raise ValidationInvariantError(f"unknown terminal disposition: {self.disposition!r}")
        if self.rule_codes and self.primary_rule_code != self.rule_codes[0]:
            raise ValidationInvariantError("primary rule code must be the first terminal rule code")


@dataclass(frozen=True)
class ReferenceOutcome:
    """Terminal disposition for one customer/product reference row.

    Reference rows never enter the transaction G2 equation (appendix section 6.3).
    """

    ref: SourceRef
    record_type: RecordType
    disposition: str
    key: str = ""
    rule_codes: tuple[str, ...] = ()
    primary_rule_code: str | None = None
    survivor: SourceRef | None = None


@dataclass(frozen=True)
class FuzzyCandidate:
    """One evidence-only near-duplicate customer candidate pair."""

    left_id: str
    right_id: str
    left: SourceRef
    right: SourceRef
    score: float


@dataclass(frozen=True)
class ReferenceCounts:
    """Separate reference-table accounting; excluded from G2."""

    input_rows: int
    valid_rows: int
    rejected_rows: int
    deduplicated_rows: int

    @property
    def reconciled(self) -> bool:
        return self.input_rows == self.valid_rows + self.rejected_rows + self.deduplicated_rows

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "input": self.input_rows,
            "valid": self.valid_rows,
            "rejected": self.rejected_rows,
            "deduplicated": self.deduplicated_rows,
            "reconciled": self.reconciled,
        }


@dataclass(frozen=True)
class Reconciliation:
    """G2 transaction accounting (appendix section 6.4)."""

    input_transaction_rows: int
    accepted_rows: int
    quarantined_rows: int
    deduplicated_rows: int

    @property
    def reconciled(self) -> bool:
        return self.input_transaction_rows == (
            self.accepted_rows + self.quarantined_rows + self.deduplicated_rows
        )

    def as_dict(self) -> dict[str, int | bool]:
        return {
            "input": self.input_transaction_rows,
            "accepted": self.accepted_rows,
            "quarantined": self.quarantined_rows,
            "deduplicated": self.deduplicated_rows,
            "reconciled": self.reconciled,
        }


def _assert_source_order(disposition: str, outcomes: Sequence[TransactionOutcome]) -> None:
    refs = [outcome.ref for outcome in outcomes]
    if len(set(refs)) != len(refs):
        raise ValidationInvariantError(f"{disposition} contains a duplicated provenance identity")
    if refs != sorted(refs, key=lambda ref: ref.sort_key):
        raise ValidationInvariantError(f"{disposition} rows are not in deterministic source order")


@dataclass(frozen=True)
class ValidationResult:
    """The DF-004 production contract for one validated normalized corpus.

    Construction fails closed (``ValidationInvariantError``) unless every ingested
    transaction identity appears in exactly one terminal bucket, the buckets are
    pairwise disjoint and in source order, and G2 reconciles.
    """

    accepted: tuple[TransactionOutcome, ...]
    quarantined: tuple[TransactionOutcome, ...]
    deduplicated: tuple[TransactionOutcome, ...]
    validation_events: tuple[ValidationEvent, ...]
    reference_results: tuple[ReferenceOutcome, ...]
    fuzzy_candidates: tuple[FuzzyCandidate, ...]
    reconciliation: Reconciliation
    input_transactions: tuple[SourceRef, ...]
    customer_counts: ReferenceCounts
    product_counts: ReferenceCounts

    def __post_init__(self) -> None:
        buckets = (
            (ACCEPTED, self.accepted),
            (QUARANTINED, self.quarantined),
            (DEDUPLICATED, self.deduplicated),
        )
        terminal: set[SourceRef] = set()
        for disposition, outcomes in buckets:
            _assert_source_order(disposition, outcomes)
            for outcome in outcomes:
                if outcome.disposition != disposition:
                    raise ValidationInvariantError(
                        f"{disposition} bucket holds a {outcome.disposition} outcome"
                    )
            refs = {outcome.ref for outcome in outcomes}
            overlap = terminal & refs
            if overlap:
                raise ValidationInvariantError(
                    "provenance identity appears in multiple terminal buckets: "
                    + repr(sorted(item.as_tuple() for item in overlap))
                )
            terminal |= refs

        inputs = list(self.input_transactions)
        if len(set(inputs)) != len(inputs):
            raise ValidationInvariantError("ingested transaction identities are not unique")
        if set(inputs) != terminal:
            missing = [item.as_tuple() for item in inputs if item not in terminal]
            extra = [item.as_tuple() for item in terminal if item not in inputs]
            raise ValidationInvariantError(
                f"terminal union is incomplete: missing={missing!r} unexpected={extra!r}"
            )

        counts = self.reconciliation
        expected = (len(self.accepted), len(self.quarantined), len(self.deduplicated))
        actual = (counts.accepted_rows, counts.quarantined_rows, counts.deduplicated_rows)
        if actual != expected:
            raise ValidationInvariantError(
                f"reconciliation counts {actual!r} disagree with terminal buckets {expected!r}"
            )
        if counts.input_transaction_rows != len(inputs):
            raise ValidationInvariantError("reconciliation input count disagrees with ingested rows")
        if not counts.reconciled:
            raise ValidationInvariantError(
                "G2 reconciliation failed: "
                f"{counts.input_transaction_rows} != {counts.accepted_rows} + "
                f"{counts.quarantined_rows} + {counts.deduplicated_rows}"
            )

        for counts_for_table in (self.customer_counts, self.product_counts):
            if not counts_for_table.reconciled:
                raise ValidationInvariantError("reference reconciliation failed")

        references = [item.ref for item in self.reference_results]
        if len(set(references)) != len(references):
            raise ValidationInvariantError("a reference row received more than one disposition")

        event_keys = [
            (event.ref, event.code, event.field_name, event.related)
            for event in self.validation_events
        ]
        if len(event_keys) != len(set(event_keys)):
            raise ValidationInvariantError("a validation event was emitted twice")

    @property
    def terminal_outcomes(self) -> tuple[TransactionOutcome, ...]:
        """All terminal outcomes in deterministic source order."""

        return tuple(
            sorted(
                self.accepted + self.quarantined + self.deduplicated,
                key=lambda outcome: outcome.ref.sort_key,
            )
        )

    def outcome_for(self, ref: SourceRef) -> TransactionOutcome:
        for outcome in self.terminal_outcomes:
            if outcome.ref == ref:
                return outcome
        raise KeyError(f"no terminal outcome for {ref.as_tuple()!r}")

    def summary(self) -> dict[str, object]:
        return {
            "transaction_counts": self.reconciliation.as_dict(),
            "customer_counts": self.customer_counts.as_dict(),
            "product_counts": self.product_counts.as_dict(),
            "fuzzy_candidates": len(self.fuzzy_candidates),
            "validation_events": len(self.validation_events),
        }


# --------------------------------------------------------------------------- #
# Row helpers
# --------------------------------------------------------------------------- #


def _ordered_rows(
    normalization: NormalizationResult,
    record_type: RecordType,
) -> list[tuple[SourceRef, Mapping[str, object]]]:
    rows: list[tuple[SourceRef, Mapping[str, object]]] = []
    for table in normalization.tables:
        if table.record_type != record_type:
            continue
        for row in table.rows:
            ref = SourceRef(
                str(row["_source_file"]),
                str(row["_source_sheet"]),
                int(row["_source_row"]),
            )
            rows.append((ref, row))
    rows.sort(key=lambda item: item[0].sort_key)
    return rows


def _issues_index(
    normalization: NormalizationResult,
) -> dict[tuple[object, ...], dict[str, NormalizationIssue]]:
    index: dict[tuple[object, ...], dict[str, NormalizationIssue]] = {}
    for issue in normalization.issues:
        key = (issue.record_type, issue.source_file, issue.source_sheet, issue.source_row)
        index.setdefault(key, {})[issue.field_name] = issue
    return index


def _field_issues(
    index: Mapping[tuple[object, ...], Mapping[str, NormalizationIssue]],
    record_type: RecordType,
    ref: SourceRef,
) -> Mapping[str, NormalizationIssue]:
    return index.get((record_type, ref.source_file, ref.source_sheet, ref.source_row), {})


def _outcome(
    ref: SourceRef,
    disposition: TerminalDisposition,
    codes: Sequence[str],
    row: Mapping[str, object],
    survivor: SourceRef | None = None,
) -> TransactionOutcome:
    rule_codes = tuple(codes)
    return TransactionOutcome(
        ref=ref,
        disposition=disposition,
        rule_codes=rule_codes,
        primary_rule_code=rule_codes[0] if rule_codes else None,
        survivor=survivor,
        values={field: row.get(field) for field in BUSINESS_FIELDS},
    )


def _fuzzy_key(value: object) -> str:
    """Appendix section 5.7 comparison key: NFC, collapsed trim, case-folding."""

    text = unicodedata.normalize("NFC", "" if value is None else str(value))
    return re.sub(r"\s+", " ", text).strip().casefold()


def fuzzy_customer_candidates(
    valid_records: Mapping[str, tuple[SourceRef, Mapping[str, object]]],
) -> tuple[FuzzyCandidate, ...]:
    """Bounded, evidence-only near-duplicate customer candidates.

    No score, including ``100.0``, authorizes a merge or rewrites a customer ID.
    """

    ids = sorted(valid_records)
    candidates: list[FuzzyCandidate] = []
    for position, left_id in enumerate(ids):
        left_ref, left_row = valid_records[left_id]
        left_name = left_row.get("customer_name")
        if left_name is None:
            continue
        left_key = _fuzzy_key(left_name)
        for right_id in ids[position + 1 :]:
            right_ref, right_row = valid_records[right_id]
            right_name = right_row.get("customer_name")
            if right_name is None:
                continue
            score = float(fuzz.ratio(left_key, _fuzzy_key(right_name)))
            if score >= FUZZY_THRESHOLD:
                candidates.append(
                    FuzzyCandidate(
                        left_id=left_id,
                        right_id=right_id,
                        left=left_ref,
                        right=right_ref,
                        score=score,
                    )
                )
    candidates.sort(
        key=lambda item: (item.left_id, item.right_id, item.left.sort_key, item.right.sort_key)
    )
    return tuple(candidates)


# --------------------------------------------------------------------------- #
# Reference preparation and validation (appendix section 6.3)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _ReferencePreparation:
    outcomes: tuple[ReferenceOutcome, ...]
    usable: Mapping[str, tuple[SourceRef, Mapping[str, object]]]
    events: tuple[tuple[tuple[object, ...], ValidationEvent], ...]
    input_rows: int


def _prepare_reference(
    normalization: NormalizationResult,
    issues_index: Mapping[tuple[object, ...], Mapping[str, NormalizationIssue]],
    record_type: RecordType,
) -> _ReferencePreparation:
    rows = _ordered_rows(normalization, record_type)
    key_field = REFERENCE_KEY_FIELD[record_type]
    outcomes: dict[SourceRef, ReferenceOutcome] = {}
    events: list[tuple[tuple[object, ...], ValidationEvent]] = []
    eligible: list[tuple[SourceRef, Mapping[str, object]]] = []

    for ref, row in rows:
        field_issues = _field_issues(issues_index, record_type, ref)
        key_issue = field_issues.get(key_field)
        if key_issue is not None and key_issue.code in REFERENCE_KEY_ISSUES[key_field]:
            code = key_issue.code
            outcomes[ref] = ReferenceOutcome(ref, record_type, REFERENCE_REJECTED, "", (code,), code)
            events.append(
                _emit(
                    code,
                    record_type,
                    ref,
                    stage=_STAGE_REFERENCE_VALIDATION,
                    field_name=key_field,
                    original_value=row.get(key_field),
                    message=f"Reference row excluded: {_hard_reason(code, key_field, row.get(key_field))}",
                )
            )
            continue

        if record_type == "products":
            price_issue = field_issues.get("unit_price_eur")
            price = _money(row.get("unit_price_eur"))
            if price_issue is not None or not (isinstance(price, Decimal) and price >= 0):
                code = "INVALID_UNIT_PRICE"
                outcomes[ref] = ReferenceOutcome(ref, record_type, REFERENCE_REJECTED, "", (code,), code)
                events.append(
                    _emit(
                        code,
                        record_type,
                        ref,
                        stage=_STAGE_REFERENCE_VALIDATION,
                        field_name="unit_price_eur",
                        original_value=row.get("unit_price_eur"),
                        message=(
                            "Reference row excluded: "
                            + _hard_reason(code, "unit_price_eur", row.get("unit_price_eur"))
                        ),
                    )
                )
                continue

        eligible.append((ref, row))

    entries = [
        DuplicateEntry(
            ref=ref,
            group_key=str(row.get(key_field)),
            canonical_key=canonical_reference_key(record_type, row),
        )
        for ref, row in eligible
    ]
    decisions = resolve_duplicates(entries)
    usable: dict[str, tuple[SourceRef, Mapping[str, object]]] = {}
    for (ref, row), decision in zip(eligible, decisions, strict=True):
        key = str(row.get(key_field))
        if decision.status == "SURVIVOR":
            outcomes[ref] = ReferenceOutcome(ref, record_type, REFERENCE_VALID, key)
            usable[key] = (ref, row)
        elif decision.status == "DUPLICATE":
            outcomes[ref] = ReferenceOutcome(
                ref,
                record_type,
                REFERENCE_DEDUPLICATED,
                key,
                (REFERENCE_DUPLICATE_EXACT,),
                REFERENCE_DUPLICATE_EXACT,
                decision.survivor,
            )
            events.append(
                _emit(
                    REFERENCE_DUPLICATE_EXACT,
                    record_type,
                    ref,
                    stage=_STAGE_DUPLICATE_RESOLUTION,
                    field_name=key_field,
                    related=decision.survivor,
                    message=f"Identical reference copy of {key!r}; the first source-order row is retained.",
                )
            )
        else:
            outcomes[ref] = ReferenceOutcome(
                ref,
                record_type,
                REFERENCE_REJECTED,
                key,
                (REFERENCE_KEY_CONFLICT,),
                REFERENCE_KEY_CONFLICT,
            )
            events.append(
                _emit(
                    REFERENCE_KEY_CONFLICT,
                    record_type,
                    ref,
                    stage=_STAGE_DUPLICATE_RESOLUTION,
                    field_name=key_field,
                    message=f"Conflicting reference facts for {key!r}; the key is unavailable.",
                )
            )

    ordered = tuple(outcomes[ref] for ref, _ in rows)
    return _ReferencePreparation(
        outcomes=ordered,
        usable=MappingProxyType(usable),
        events=tuple(events),
        input_rows=len(rows),
    )


def _reference_counts(preparation: _ReferencePreparation) -> ReferenceCounts:
    return ReferenceCounts(
        input_rows=preparation.input_rows,
        valid_rows=sum(item.disposition == REFERENCE_VALID for item in preparation.outcomes),
        rejected_rows=sum(item.disposition == REFERENCE_REJECTED for item in preparation.outcomes),
        deduplicated_rows=sum(
            item.disposition == REFERENCE_DEDUPLICATED for item in preparation.outcomes
        ),
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def validate(normalization: NormalizationResult) -> ValidationResult:
    """Apply DF-004 to a DF-003 normalization result in the frozen precedence."""

    issues_index = _issues_index(normalization)
    emitted: list[tuple[tuple[object, ...], ValidationEvent]] = []

    customer_preparation = _prepare_reference(normalization, issues_index, "customers")
    product_preparation = _prepare_reference(normalization, issues_index, "products")
    emitted.extend(customer_preparation.events)
    emitted.extend(product_preparation.events)

    usable_customers = customer_preparation.usable
    usable_products = product_preparation.usable

    sales_rows = _ordered_rows(normalization, "sales")
    input_refs = tuple(ref for ref, _ in sales_rows)

    # Stage 1: hard validation. Terminal before any duplicate accounting.
    quarantined: list[TransactionOutcome] = []
    deduplicated: list[TransactionOutcome] = []
    hard_valid: list[tuple[SourceRef, Mapping[str, object]]] = []
    for ref, row in sales_rows:
        field_issues = _field_issues(issues_index, "sales", ref)
        failures = hard_failures(row, {name: issue.code for name, issue in field_issues.items()})
        if failures:
            codes = tuple(failure.code for failure in failures)
            quarantined.append(_outcome(ref, QUARANTINED, codes, row))
            for failure in failures:
                emitted.append(
                    _emit(
                        failure.code,
                        "sales",
                        ref,
                        stage=_STAGE_HARD_VALIDATION,
                        field_name=failure.field_name,
                        original_value=row.get(failure.field_name),
                        message=failure.reason,
                    )
                )
        else:
            hard_valid.append((ref, row))

    # Stage 2: duplicate resolution over hard-valid rows only.
    rows_by_ref = {ref: row for ref, row in hard_valid}
    decisions = resolve_duplicates(
        [
            DuplicateEntry(
                ref=ref,
                group_key=str(row.get("order_id")),
                canonical_key=canonical_business_key(row),
            )
            for ref, row in hard_valid
        ]
    )
    survivors: list[tuple[SourceRef, Mapping[str, object]]] = []
    for decision in decisions:
        row = rows_by_ref[decision.ref]
        if decision.status == "SURVIVOR":
            survivors.append((decision.ref, row))
        elif decision.status == "DUPLICATE":
            deduplicated.append(
                _outcome(decision.ref, DEDUPLICATED, (DUPLICATE_EXACT,), row, decision.survivor)
            )
            emitted.append(
                _emit(
                    DUPLICATE_EXACT,
                    "sales",
                    decision.ref,
                    stage=_STAGE_DUPLICATE_RESOLUTION,
                    field_name="order_id",
                    related=decision.survivor,
                    message=(
                        f"Identical transaction copy of {decision.group_key!r}; "
                        "the first source-order row is retained."
                    ),
                )
            )
        else:
            quarantined.append(
                _outcome(decision.ref, QUARANTINED, (DUPLICATE_KEY_CONFLICT,), row)
            )
            emitted.append(
                _emit(
                    DUPLICATE_KEY_CONFLICT,
                    "sales",
                    decision.ref,
                    stage=_STAGE_DUPLICATE_RESOLUTION,
                    field_name="order_id",
                    message=(
                        f"Conflicting business facts for {decision.group_key!r}; "
                        "every group member is quarantined."
                    ),
                )
            )

    # Stage 3: reference validation of the surviving rows.
    accepted: list[TransactionOutcome] = []
    for ref, row in survivors:
        codes: list[str] = []
        if str(row.get("customer_id")) not in usable_customers:
            codes.append(UNKNOWN_CUSTOMER_REFERENCE)
        if str(row.get("product_id")) not in usable_products:
            codes.append(UNKNOWN_PRODUCT_REFERENCE)
        if codes:
            quarantined.append(_outcome(ref, QUARANTINED, tuple(codes), row))
            for code in codes:
                field_name = (
                    "customer_id" if code == UNKNOWN_CUSTOMER_REFERENCE else "product_id"
                )
                emitted.append(
                    _emit(
                        code,
                        "sales",
                        ref,
                        stage=_STAGE_REFERENCE_VALIDATION,
                        field_name=field_name,
                        original_value=row.get(field_name),
                        message=(
                            f"{field_name} {row.get(field_name)!r} is absent from the usable "
                            f"{field_name.removesuffix('_id')} reference."
                        ),
                    )
                )
        else:
            accepted.append(_outcome(ref, ACCEPTED, (), row))

    # Evidence-only fuzzy customer candidates.
    candidates = fuzzy_customer_candidates(usable_customers)
    for candidate in candidates:
        emitted.append(
            _emit(
                FUZZY_CUSTOMER_CANDIDATE,
                "customers",
                candidate.left,
                stage=_STAGE_FLAG,
                related=candidate.right,
                message=(
                    f"Customer names {candidate.left_id!r} and {candidate.right_id!r} score "
                    f"{candidate.score:.4f} at or above {FUZZY_THRESHOLD:.1f}; evidence only, no merge."
                ),
            )
        )

    accepted.sort(key=lambda outcome: outcome.ref.sort_key)
    quarantined.sort(key=lambda outcome: outcome.ref.sort_key)
    deduplicated.sort(key=lambda outcome: outcome.ref.sort_key)
    emitted.sort(key=lambda item: item[0])

    reconciliation = Reconciliation(
        input_transaction_rows=len(input_refs),
        accepted_rows=len(accepted),
        quarantined_rows=len(quarantined),
        deduplicated_rows=len(deduplicated),
    )

    return ValidationResult(
        accepted=tuple(accepted),
        quarantined=tuple(quarantined),
        deduplicated=tuple(deduplicated),
        validation_events=tuple(event for _, event in emitted),
        reference_results=tuple(
            customer_preparation.outcomes + product_preparation.outcomes
        ),
        fuzzy_candidates=candidates,
        reconciliation=reconciliation,
        input_transactions=input_refs,
        customer_counts=_reference_counts(customer_preparation),
        product_counts=_reference_counts(product_preparation),
    )


__all__ = [
    "ACTION_TYPES",
    "ACCEPTED",
    "DEDUPLICATED",
    "DEMO_DATE",
    "FUZZY_CUSTOMER_CANDIDATE",
    "FUZZY_THRESHOLD",
    "FuzzyCandidate",
    "HARD_FAILURE_CODES",
    "HARD_FAILURE_ORDER",
    "HardFailure",
    "QUARANTINED",
    "RULE_CATALOGUE",
    "Reconciliation",
    "ReferenceCounts",
    "ReferenceOutcome",
    "SourceRef",
    "TERMINAL_DISPOSITIONS",
    "TIMEZONE",
    "TransactionOutcome",
    "ValidationEvent",
    "ValidationInvariantError",
    "ValidationResult",
    "canonical_business_key",
    "canonical_reference_key",
    "fuzzy_customer_candidates",
    "hard_failures",
    "validate",
]
