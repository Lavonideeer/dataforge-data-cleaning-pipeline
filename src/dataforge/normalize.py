"""Deterministic value normalization for the bounded DataForge v1 corpus.

This module converts recoverable representations and records evidence. It does
not assign terminal dispositions, quarantine rows, resolve duplicates, or check
business/reference rules owned by DF-004.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from .ingest import IngestedTable, IngestionResult
from .schema import PROVENANCE_FIELDS, RawValue, RecordType, SCHEMAS, SchemaDiagnostic


DEMO_DATE = date(2026, 10, 7)
TIMEZONE = "Europe/Paris"

IDENTIFIER_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9_-]{0,63}\Z")
SIGNED_INTEGER_PATTERN = re.compile(r"[+-]?\d+\Z")
EMAIL_LOCAL_PATTERN = re.compile(r"[^@\s]+\Z")
EMAIL_LABEL_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?\Z")

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

COUNTRY_ALIASES = {"france": "FR", "fr": "FR", "fra": "FR"}
CATEGORY_ALIASES = {
    "electronics": "ELECTRONICS",
    "tech": "ELECTRONICS",
    "home": "HOME",
    "home & living": "HOME",
    "home and living": "HOME",
    "office": "OFFICE",
    "office supplies": "OFFICE",
    "apparel": "APPAREL",
    "clothing": "APPAREL",
}

ISSUE_ACTION_TYPES = {
    "UNKNOWN_COUNTRY": "FLAG",
    "UNKNOWN_CATEGORY": "FLAG",
    "INVALID_EMAIL_FORMAT": "FLAG",
}


@dataclass(frozen=True)
class ScalarNormalization:
    """Pure result for one field value before provenance is attached."""

    value: RawValue | Decimal
    event_code: str | None = None
    issue_code: str | None = None
    issue_reason: str = ""


@dataclass(frozen=True)
class NormalizationEvent:
    record_type: RecordType
    source_file: str
    source_sheet: str
    source_row: int
    field_name: str
    original_value: RawValue | Decimal
    cleaned_value: RawValue | Decimal
    code: str
    action_type: str = "NORMALIZATION"
    message: str = ""


@dataclass(frozen=True)
class NormalizationIssue:
    record_type: RecordType
    source_file: str
    source_sheet: str
    source_row: int
    field_name: str
    original_value: RawValue | Decimal
    normalized_value: RawValue | Decimal
    code: str
    action_type: str
    message: str


@dataclass(frozen=True)
class NormalizedTable:
    record_type: RecordType
    source_file: str
    source_sheet: str
    columns: tuple[str, ...]
    rows: tuple[Mapping[str, RawValue | Decimal], ...]
    events: tuple[NormalizationEvent, ...]
    issues: tuple[NormalizationIssue, ...]


@dataclass(frozen=True)
class NormalizationResult:
    tables: tuple[NormalizedTable, ...]
    events: tuple[NormalizationEvent, ...]
    issues: tuple[NormalizationIssue, ...]
    schema_diagnostics: tuple[SchemaDiagnostic, ...] = ()

    def rows_for(self, record_type: RecordType) -> tuple[Mapping[str, RawValue | Decimal], ...]:
        return tuple(
            row
            for table in self.tables
            if table.record_type == record_type
            for row in table.rows
        )

    def summary(self) -> dict[str, object]:
        return {
            "sales_rows": len(self.rows_for("sales")),
            "customer_rows": len(self.rows_for("customers")),
            "product_rows": len(self.rows_for("products")),
            "normalization_events": len(self.events),
            "normalization_issues": len(self.issues),
            "event_counts": dict(sorted(Counter(item.code for item in self.events).items())),
            "issue_counts": dict(sorted(Counter(item.code for item in self.issues).items())),
        }


def _nfc(value: object) -> str:
    return unicodedata.normalize("NFC", str(value))


def _text_value(value: object) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", _nfc(value).strip())
    return cleaned or None


def normalize_text(value: object) -> ScalarNormalization:
    """Normalize descriptive text while preserving presentation casing."""

    cleaned = _text_value(value)
    original = None if value is None else str(value)
    event = "NORMALIZE_WHITESPACE" if cleaned is not None and cleaned != original else None
    return ScalarNormalization(cleaned, event_code=event)


def normalize_identifier(value: object, *, missing_code: str) -> ScalarNormalization:
    """Normalize one identifier without guessing malformed values."""

    if value is None:
        return ScalarNormalization(None, issue_code=missing_code, issue_reason="identifier is missing")
    if isinstance(value, bool):
        return ScalarNormalization(value, issue_code="INVALID_IDENTIFIER", issue_reason="boolean is not an identifier")
    if isinstance(value, int):
        cleaned = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return ScalarNormalization(value, issue_code="INVALID_IDENTIFIER", issue_reason="numeric identifier is not an integer")
        cleaned = str(int(value))
    elif isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            return ScalarNormalization(value, issue_code="INVALID_IDENTIFIER", issue_reason="numeric identifier is not an integer")
        cleaned = str(value.to_integral_value())
    else:
        cleaned = _nfc(value).strip().upper()
        if not cleaned:
            return ScalarNormalization(None, issue_code=missing_code, issue_reason="identifier is missing")

    original = str(value)
    event = "NORMALIZE_IDENTIFIER" if cleaned != original else None
    issue = None if IDENTIFIER_PATTERN.fullmatch(cleaned) else "INVALID_IDENTIFIER"
    reason = "identifier violates the bounded canonical pattern" if issue else ""
    return ScalarNormalization(cleaned, event_code=event, issue_code=issue, issue_reason=reason)


def normalize_integer(value: object) -> ScalarNormalization:
    """Parse the exact integer representation; positivity is a later rule."""

    if value is None:
        return ScalarNormalization(None, issue_code="INVALID_QUANTITY", issue_reason="quantity is missing")
    if isinstance(value, bool):
        return ScalarNormalization(value, issue_code="INVALID_QUANTITY", issue_reason="boolean is not an integer")
    if isinstance(value, int):
        return ScalarNormalization(value)
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer():
            return ScalarNormalization(int(value))
        return ScalarNormalization(value, issue_code="INVALID_QUANTITY", issue_reason="quantity is fractional or non-finite")
    if isinstance(value, Decimal):
        if value.is_finite() and value == value.to_integral_value():
            return ScalarNormalization(int(value))
        return ScalarNormalization(value, issue_code="INVALID_QUANTITY", issue_reason="quantity is fractional or non-finite")

    cleaned = _nfc(value).strip()
    if not cleaned:
        return ScalarNormalization(None, issue_code="INVALID_QUANTITY", issue_reason="quantity is missing")
    if not SIGNED_INTEGER_PATTERN.fullmatch(cleaned):
        return ScalarNormalization(cleaned, issue_code="INVALID_QUANTITY", issue_reason="quantity is not an exact integer")
    return ScalarNormalization(int(cleaned))


def _decimal_from_currency_text(text: str) -> Decimal | None:
    sign = ""
    unsigned = text
    if unsigned[:1] in {"+", "-"}:
        sign, unsigned = unsigned[0], unsigned[1:]
    if not unsigned:
        return None

    canonical: str | None = None
    if re.fullmatch(r"\d+", unsigned):
        canonical = unsigned
    elif re.fullmatch(r"\d+\.\d{1,2}", unsigned):
        canonical = unsigned
    elif re.fullmatch(r"\d+,\d{2}", unsigned):
        canonical = unsigned.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:[ \u00a0]\d{3})+,\d{2}", unsigned):
        canonical = unsigned.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", unsigned):
        canonical = unsigned.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+\.\d{1,2}", unsigned):
        canonical = unsigned.replace(",", "")
    if canonical is None:
        return None
    try:
        return Decimal(sign + canonical)
    except InvalidOperation:
        return None


def normalize_currency(value: object) -> ScalarNormalization:
    """Parse the exhaustive EUR forms with exact decimal semantics."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return ScalarNormalization(None, issue_code="INVALID_UNIT_PRICE", issue_reason="unit price is missing")
    if isinstance(value, bool):
        return ScalarNormalization(value, issue_code="INVALID_UNIT_PRICE", issue_reason="boolean is not money")

    parsed: Decimal | None
    retained: RawValue | Decimal
    if isinstance(value, Decimal):
        parsed = value
        retained = value
    elif isinstance(value, int):
        parsed = Decimal(value)
        retained = value
    elif isinstance(value, float):
        parsed = Decimal(str(value)) if math.isfinite(value) else None
        retained = value
    else:
        retained = _nfc(value).strip()
        text = retained
        prefix = re.match(r"(?i)^EUR", text)
        suffix = re.search(r"(?i)EUR$", text)
        if prefix and suffix:
            parsed = None
        else:
            if prefix:
                text = text[prefix.end():].strip()
            elif suffix:
                text = text[:suffix.start()].strip()
            parsed = _decimal_from_currency_text(text)

    if parsed is None or not parsed.is_finite():
        return ScalarNormalization(retained, issue_code="INVALID_UNIT_PRICE", issue_reason="unit price is malformed or ambiguous")
    fractional_digits = max(-parsed.as_tuple().exponent, 0)
    if fractional_digits > 2:
        return ScalarNormalization(retained, issue_code="INVALID_UNIT_PRICE", issue_reason="unit price has more than two decimal places")
    if parsed == 0:
        parsed = abs(parsed)
    canonical = format(parsed, ".2f")
    event = "NORMALIZE_CURRENCY" if str(value) != canonical else None
    return ScalarNormalization(canonical, event_code=event)


def _date_from_text(text: str) -> date | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        parts = tuple(map(int, match.groups()))
        try:
            return date(parts[0], parts[1], parts[2])
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", text)
    if match:
        day, month, year = map(int, match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    match = re.fullmatch(r"(\d{2}) ([A-Za-z]+) (\d{4})", text)
    if match:
        day_text, month_text, year_text = match.groups()
        month = MONTHS.get(month_text.casefold())
        if month is None:
            return None
        try:
            return date(int(year_text), month, int(day_text))
        except ValueError:
            return None
    return None


def normalize_date(value: object) -> ScalarNormalization:
    """Parse bounded date forms; future-date validation remains DF-004."""

    if value is None or (isinstance(value, str) and not value.strip()):
        return ScalarNormalization(None, issue_code="INVALID_ORDER_DATE", issue_reason="order date is missing")
    parsed: date | None
    retained: RawValue
    if isinstance(value, datetime):
        if value.time() != time.min or value.tzinfo is not None:
            return ScalarNormalization(value, issue_code="INVALID_ORDER_DATE", issue_reason="timestamp is not a bounded date")
        parsed = value.date()
        retained = value
    elif isinstance(value, date):
        parsed = value
        retained = value
    elif isinstance(value, str):
        retained = _nfc(value).strip()
        parsed = _date_from_text(retained)
    else:
        return ScalarNormalization(value, issue_code="INVALID_ORDER_DATE", issue_reason="order date has an unsupported type")
    if parsed is None:
        return ScalarNormalization(retained, issue_code="INVALID_ORDER_DATE", issue_reason="order date is malformed or impossible")
    canonical = parsed.isoformat()
    event = "NORMALIZE_DATE" if str(value) != canonical else None
    return ScalarNormalization(canonical, event_code=event)


def normalize_country(value: object) -> ScalarNormalization:
    """Normalize the exhaustive France alias map; retain unknown values."""

    cleaned = _text_value(value)
    if cleaned is None:
        return ScalarNormalization(None)
    canonical = COUNTRY_ALIASES.get(cleaned.casefold())
    if canonical is not None:
        event = "NORMALIZE_COUNTRY" if str(value) != canonical else None
        return ScalarNormalization(canonical, event_code=event)
    event = "NORMALIZE_WHITESPACE" if cleaned != str(value) else None
    return ScalarNormalization(
        cleaned,
        event_code=event,
        issue_code="UNKNOWN_COUNTRY",
        issue_reason="country is outside the bounded alias map",
    )


def normalize_category(value: object) -> ScalarNormalization:
    """Normalize the exhaustive category map; retain unknown values."""

    cleaned = _text_value(value)
    if cleaned is None:
        return ScalarNormalization(None)
    canonical = CATEGORY_ALIASES.get(cleaned.casefold())
    if canonical is not None:
        event = "NORMALIZE_CATEGORY" if str(value) != canonical else None
        return ScalarNormalization(canonical, event_code=event)
    event = "NORMALIZE_WHITESPACE" if cleaned != str(value) else None
    return ScalarNormalization(
        cleaned,
        event_code=event,
        issue_code="UNKNOWN_CATEGORY",
        issue_reason="category is outside the bounded alias map",
    )


def _valid_email(value: str) -> bool:
    if value.count("@") != 1:
        return False
    local, domain = value.split("@")
    if not EMAIL_LOCAL_PATTERN.fullmatch(local):
        return False
    labels = domain.split(".")
    return len(labels) >= 2 and all(EMAIL_LABEL_PATTERN.fullmatch(label) for label in labels)


def normalize_email(value: object) -> ScalarNormalization:
    """Apply bounded email hygiene separately from bounded validity."""

    if value is None:
        return ScalarNormalization(None)
    original = str(value)
    cleaned = _nfc(value).strip()
    if not cleaned:
        return ScalarNormalization(None)
    if cleaned.count("@") == 1:
        local, domain = cleaned.split("@")
        cleaned = f"{local}@{domain.lower()}"
    event = "NORMALIZE_EMAIL" if cleaned != original else None
    issue = None if _valid_email(cleaned) else "INVALID_EMAIL_FORMAT"
    reason = "email does not match the bounded syntax" if issue else ""
    return ScalarNormalization(cleaned, event_code=event, issue_code=issue, issue_reason=reason)


def _missing_identifier_code(record_type: RecordType, field_name: str) -> str:
    if field_name == "order_id":
        return "MISSING_ORDER_ID"
    if field_name == "customer_id":
        return "MISSING_CUSTOMER_ID"
    if field_name == "product_id":
        return "MISSING_PRODUCT_ID"
    raise ValueError(f"No missing-identifier code for {record_type}.{field_name}")


def _normalize_field(record_type: RecordType, field_name: str, value: object) -> ScalarNormalization:
    if field_name in {"order_id", "customer_id", "product_id"}:
        return normalize_identifier(
            value,
            missing_code=_missing_identifier_code(record_type, field_name),
        )
    if field_name in {"customer_name", "product_name"}:
        return normalize_text(value)
    if field_name == "order_date":
        return normalize_date(value)
    if field_name == "quantity":
        return normalize_integer(value)
    if field_name == "unit_price_eur":
        return normalize_currency(value)
    if field_name == "country":
        return normalize_country(value)
    if field_name == "email":
        return normalize_email(value)
    if field_name == "category":
        return normalize_category(value)
    raise ValueError(f"Unsupported normalization field: {record_type}.{field_name}")


def normalize_table(table: IngestedTable | NormalizedTable) -> NormalizedTable:
    """Normalize one table without changing its row count or provenance."""

    fields = SCHEMAS[table.record_type].fields
    normalized_rows: list[Mapping[str, RawValue | Decimal]] = []
    events: list[NormalizationEvent] = []
    issues: list[NormalizationIssue] = []
    for source_row in table.rows:
        row = dict(source_row)
        provenance = {
            "source_file": str(source_row["_source_file"]),
            "source_sheet": str(source_row["_source_sheet"]),
            "source_row": int(source_row["_source_row"]),
        }
        for field_name in fields:
            original = source_row[field_name]
            outcome = _normalize_field(table.record_type, field_name, original)
            row[field_name] = outcome.value
            if outcome.event_code:
                events.append(
                    NormalizationEvent(
                        record_type=table.record_type,
                        field_name=field_name,
                        original_value=original,
                        cleaned_value=outcome.value,
                        code=outcome.event_code,
                        message=f"Normalized {field_name} using {outcome.event_code}.",
                        **provenance,
                    )
                )
            if outcome.issue_code:
                action_type = ISSUE_ACTION_TYPES.get(outcome.issue_code, "VALIDATION_FAILURE")
                issues.append(
                    NormalizationIssue(
                        record_type=table.record_type,
                        field_name=field_name,
                        original_value=original,
                        normalized_value=outcome.value,
                        code=outcome.issue_code,
                        action_type=action_type,
                        message=f"{outcome.issue_code} for {field_name}: {outcome.issue_reason}.",
                        **provenance,
                    )
                )
        for field_name in PROVENANCE_FIELDS:
            row[field_name] = source_row[field_name]
        normalized_rows.append(row)

    return NormalizedTable(
        record_type=table.record_type,
        source_file=table.source_file,
        source_sheet=table.source_sheet,
        columns=table.columns,
        rows=tuple(normalized_rows),
        events=tuple(events),
        issues=tuple(issues),
    )


def normalize(
    source: IngestionResult | NormalizationResult | Iterable[IngestedTable | NormalizedTable],
) -> NormalizationResult:
    """Normalize an ingestion result or tables in deterministic source order."""

    if isinstance(source, IngestionResult):
        tables = source.tables
        schema_diagnostics = source.diagnostics
    elif isinstance(source, NormalizationResult):
        tables = source.tables
        schema_diagnostics = source.schema_diagnostics
    else:
        tables = tuple(source)
        schema_diagnostics = ()
    normalized_tables = tuple(normalize_table(table) for table in tables)
    return NormalizationResult(
        tables=normalized_tables,
        events=tuple(event for table in normalized_tables for event in table.events),
        issues=tuple(issue for table in normalized_tables for issue in table.issues),
        schema_diagnostics=tuple(schema_diagnostics),
    )
