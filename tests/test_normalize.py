"""DF-003 deterministic normalization tests."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from dataforge.ingest import ingest_directory
from dataforge.normalize import (
    DEMO_DATE,
    TIMEZONE,
    NormalizationResult,
    normalize,
    normalize_category,
    normalize_country,
    normalize_currency,
    normalize_date,
    normalize_email,
    normalize_identifier,
    normalize_integer,
    normalize_text,
)


ROOT = Path(__file__).resolve().parents[1]
DEMO_RAW = ROOT / "data" / "demo_raw"
EXPECTED = ROOT / "data" / "demo_expected" / "expected_outcomes.json"


def _corpus() -> NormalizationResult:
    return normalize(ingest_directory(DEMO_RAW))


def _row(result: NormalizationResult, record_type: str, source_file: str, source_row: int):
    return next(
        row
        for row in result.rows_for(record_type)
        if row["_source_file"] == source_file and row["_source_row"] == source_row
    )


@pytest.mark.parametrize(
    ("raw", "expected", "event", "issue"),
    [
        ("ORD-0001", "ORD-0001", None, None),
        (" ord-0001 ", "ORD-0001", "NORMALIZE_IDENTIFIER", None),
        (123, "123", None, None),
        (123.0, "123", "NORMALIZE_IDENTIFIER", None),
        ("bad id!", "BAD ID!", "NORMALIZE_IDENTIFIER", "INVALID_IDENTIFIER"),
        (1.5, 1.5, None, "INVALID_IDENTIFIER"),
        ("   ", None, None, "MISSING_ORDER_ID"),
    ],
)
def test_identifier_normalization(raw, expected, event, issue) -> None:
    result = normalize_identifier(raw, missing_code="MISSING_ORDER_ID")
    assert (result.value, result.event_code, result.issue_code) == (expected, event, issue)


@pytest.mark.parametrize(
    ("raw", "expected", "event"),
    [
        ("  Bruno   Leroy  ", "Bruno Leroy", "NORMALIZE_WHITESPACE"),
        ("Bruno Leroy", "Bruno Leroy", None),
        ("Élodie\u00a0 Martin", "Élodie Martin", "NORMALIZE_WHITESPACE"),
        ("   ", None, None),
        (None, None, None),
    ],
)
def test_descriptive_text_hygiene_preserves_casing(raw, expected, event) -> None:
    result = normalize_text(raw)
    assert (result.value, result.event_code) == (expected, event)


@pytest.mark.parametrize("raw", ["France", "france", "FRA", " fr "])
def test_country_aliases_normalize_to_fr(raw: str) -> None:
    result = normalize_country(raw)
    assert result.value == "FR"
    assert result.event_code == "NORMALIZE_COUNTRY"
    assert result.issue_code is None


def test_canonical_and_unknown_country_behavior() -> None:
    canonical = normalize_country("FR")
    unknown = normalize_country("  Atlantis  ")
    missing = normalize_country("")

    assert (canonical.value, canonical.event_code, canonical.issue_code) == ("FR", None, None)
    assert (unknown.value, unknown.event_code, unknown.issue_code) == (
        "Atlantis",
        "NORMALIZE_WHITESPACE",
        "UNKNOWN_COUNTRY",
    )
    assert (missing.value, missing.event_code, missing.issue_code) == (None, None, None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 299,00 EUR", "1299.00"),
        ("1\u00a0299,00 EUR", "1299.00"),
        ("EUR1,299.00", "1299.00"),
        ("1.299,00", "1299.00"),
        ("1299", "1299.00"),
        ("1299.0", "1299.00"),
        ("1299,00", "1299.00"),
        (1299, "1299.00"),
        (1299.5, "1299.50"),
        (Decimal("1299.50"), "1299.50"),
        ("-12.50", "-12.50"),
        ("+12.50", "12.50"),
    ],
)
def test_currency_recoverable_forms_use_exact_decimal_semantics(raw, expected: str) -> None:
    result = normalize_currency(raw)
    assert result.value == expected
    assert result.issue_code is None


@pytest.mark.parametrize(
    "raw",
    ["1,299", "1.299", "12.345", "12.345 EUR", "USD 12.00", "€12", "NaN", "1e3", "12,3", True],
)
def test_currency_malformed_or_ambiguous_is_not_invented(raw) -> None:
    result = normalize_currency(raw)
    assert result.issue_code == "INVALID_UNIT_PRICE"
    assert result.value not in {"1299.00", "12.35", "1000.00"}


def test_currency_missing_and_overprecision_are_explicit_issues() -> None:
    missing = normalize_currency("")
    precise = normalize_currency(Decimal("12.345"))

    assert (missing.value, missing.issue_code) == (None, "INVALID_UNIT_PRICE")
    assert precise.value == Decimal("12.345")
    assert precise.issue_code == "INVALID_UNIT_PRICE"


@pytest.mark.parametrize(
    ("raw", "expected", "event"),
    [
        ("2026-07-22", "2026-07-22", None),
        ("23/07/2026", "2026-07-23", "NORMALIZE_DATE"),
        ("24 July 2026", "2026-07-24", "NORMALIZE_DATE"),
        ("24 Jul 2026", "2026-07-24", "NORMALIZE_DATE"),
        (date(2026, 7, 24), "2026-07-24", None),
        (datetime(2026, 7, 24), "2026-07-24", "NORMALIZE_DATE"),
        ("2026-10-08", "2026-10-08", None),
    ],
)
def test_date_normalization_is_bounded_and_future_agnostic(raw, expected: str, event) -> None:
    result = normalize_date(raw)
    assert (result.value, result.event_code, result.issue_code) == (expected, event, None)


@pytest.mark.parametrize("raw", ["31/02/2026", "07/24/2026", "2026/07/24", "24 Foo 2026", "", None])
def test_date_invalid_or_missing_is_an_issue(raw) -> None:
    result = normalize_date(raw)
    assert result.issue_code == "INVALID_ORDER_DATE"


def test_frozen_clock_is_declared_but_not_used_for_terminal_validation() -> None:
    assert DEMO_DATE == date(2026, 10, 7)
    assert TIMEZONE == "Europe/Paris"
    assert normalize_date("2026-10-08").value == "2026-10-08"
    assert normalize_date("2026-10-08").issue_code is None


def test_email_hygiene_and_bounded_validity() -> None:
    canonical = normalize_email("Local.Part@example.test")
    recoverable = normalize_email(" Local.Part@EXAMPLE.TEST ")
    malformed = normalize_email("local.example.test")

    assert (canonical.value, canonical.event_code, canonical.issue_code) == (
        "Local.Part@example.test",
        None,
        None,
    )
    assert (recoverable.value, recoverable.event_code, recoverable.issue_code) == (
        "Local.Part@example.test",
        "NORMALIZE_EMAIL",
        None,
    )
    assert malformed.value == "local.example.test"
    assert malformed.issue_code == "INVALID_EMAIL_FORMAT"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Electronics", "ELECTRONICS"),
        ("electronics", "ELECTRONICS"),
        ("Tech", "ELECTRONICS"),
        ("tech", "ELECTRONICS"),
        ("Home", "HOME"),
        ("home", "HOME"),
        ("Home & Living", "HOME"),
        ("home and living", "HOME"),
        ("Office", "OFFICE"),
        ("office", "OFFICE"),
        ("Office Supplies", "OFFICE"),
        ("office supplies", "OFFICE"),
        ("Apparel", "APPAREL"),
        ("apparel", "APPAREL"),
        ("Clothing", "APPAREL"),
        ("clothing", "APPAREL"),
    ],
)
def test_every_bounded_category_alias(raw: str, expected: str) -> None:
    result = normalize_category(raw)
    assert result.value == expected
    assert result.issue_code is None


@pytest.mark.parametrize("canonical", ["ELECTRONICS", "HOME", "OFFICE", "APPAREL"])
def test_canonical_categories_are_no_ops(canonical: str) -> None:
    result = normalize_category(canonical)
    assert (result.value, result.event_code, result.issue_code) == (canonical, None, None)


def test_unknown_category_is_retained_and_flagged() -> None:
    result = normalize_category(" Gadgets ")
    assert (result.value, result.event_code, result.issue_code) == (
        "Gadgets",
        "NORMALIZE_WHITESPACE",
        "UNKNOWN_CATEGORY",
    )


def test_integer_parsing_does_not_apply_positive_business_rule() -> None:
    assert normalize_integer("2").value == 2
    assert normalize_integer(0).issue_code is None
    assert normalize_integer(-3).issue_code is None
    assert normalize_integer("2.5").issue_code == "INVALID_QUANTITY"


def test_actual_corpus_counts_events_and_issues() -> None:
    result = _corpus()
    assert result.summary() == {
        "sales_rows": 164,
        "customer_rows": 26,
        "product_rows": 15,
        "normalization_events": 33,
        "normalization_issues": 14,
        "event_counts": {
            "NORMALIZE_CATEGORY": 4,
            "NORMALIZE_COUNTRY": 7,
            "NORMALIZE_CURRENCY": 6,
            "NORMALIZE_DATE": 5,
            "NORMALIZE_EMAIL": 1,
            "NORMALIZE_IDENTIFIER": 9,
            "NORMALIZE_WHITESPACE": 1,
        },
        "issue_counts": {
            "INVALID_EMAIL_FORMAT": 1,
            "INVALID_IDENTIFIER": 1,
            "INVALID_ORDER_DATE": 1,
            "INVALID_QUANTITY": 1,
            "INVALID_UNIT_PRICE": 2,
            "MISSING_CUSTOMER_ID": 2,
            "MISSING_ORDER_ID": 1,
            "MISSING_PRODUCT_ID": 2,
            "UNKNOWN_CATEGORY": 1,
            "UNKNOWN_COUNTRY": 2,
        },
    }


def test_expected_fixture_normalizations_match_actual_corpus() -> None:
    manifest = json.loads(EXPECTED.read_text(encoding="utf-8"))
    result = _corpus()
    source_by_fixture = {
        source["fixture_id"]: source
        for case in manifest["cases"]
        for source in case["source_records"]
        if source.get("fixture_id")
    }

    for case in manifest["cases"]:
        for expectation in case["expected_normalization"]:
            source = source_by_fixture[expectation["fixture_id"]]
            row = _row(
                result,
                source["record_type"],
                source["source_file"],
                source["source_row"],
            )
            for field_name, expected_value in expectation["values"].items():
                assert row[field_name] == expected_value, (case["case_id"], expectation["fixture_id"])

        normalization_codes = {code for code in case["expected_codes"] if code.startswith("NORMALIZE_")}
        if normalization_codes:
            provenances = {
                (source["source_file"], source["source_sheet"], source["source_row"])
                for source in case["source_records"]
                if source.get("fixture_id")
            }
            actual_codes = {
                event.code
                for event in result.events
                if (event.source_file, event.source_sheet, event.source_row) in provenances
            }
            assert normalization_codes <= actual_codes, case["case_id"]


def test_provenance_and_row_counts_are_preserved_exactly() -> None:
    ingested = ingest_directory(DEMO_RAW)
    normalized = normalize(ingested)
    for record_type in ("sales", "customers", "products"):
        before = ingested.rows_for(record_type)
        after = normalized.rows_for(record_type)
        assert len(before) == len(after)
        assert [tuple(row[field] for field in ("_source_file", "_source_sheet", "_source_row")) for row in before] == [
            tuple(row[field] for field in ("_source_file", "_source_sheet", "_source_row")) for row in after
        ]


def test_event_and_issue_evidence_is_exact_and_stable() -> None:
    result = _corpus()
    event = next(
        item
        for item in result.events
        if item.source_file == "sales_2026_07.csv" and item.source_row == 3 and item.field_name == "order_id"
    )
    issue = next(
        item
        for item in result.issues
        if item.source_file == "sales_2026_07.csv" and item.source_row == 15
    )

    assert (event.original_value, event.cleaned_value, event.code, event.action_type) == (
        " ord-0002 ",
        "ORD-0002",
        "NORMALIZE_IDENTIFIER",
        "NORMALIZATION",
    )
    assert (issue.field_name, issue.original_value, issue.normalized_value, issue.code, issue.action_type) == (
        "unit_price_eur",
        "1,299",
        "1,299",
        "INVALID_UNIT_PRICE",
        "VALIDATION_FAILURE",
    )


def test_boundary_keeps_negative_future_missing_and_duplicate_rows() -> None:
    result = _corpus()
    negative = _row(result, "sales", "sales_2026_07.csv", 14)
    future = _row(result, "sales", "sales_2026_10.xlsx", 2)
    multiple = _row(result, "sales", "sales_2026_10.xlsx", 3)

    assert negative["unit_price_eur"] == "-5.00"
    assert future["order_date"] == "2026-10-08"
    assert multiple["customer_id"] is None
    assert multiple["order_date"] == "2026-10-09"
    assert multiple["quantity"] == -3
    assert len(result.rows_for("sales")) == 164
    assert not any(hasattr(result, name) for name in ("accepted", "quarantined", "deduplicated"))


def test_normalization_is_idempotent_at_values_without_new_events() -> None:
    first = _corpus()
    second = normalize(first)

    assert first.rows_for("sales") == second.rows_for("sales")
    assert first.rows_for("customers") == second.rows_for("customers")
    assert first.rows_for("products") == second.rows_for("products")
    assert second.events == ()
    assert [(item.code, item.field_name, item.source_file, item.source_row) for item in first.issues] == [
        (item.code, item.field_name, item.source_file, item.source_row) for item in second.issues
    ]


def test_repeated_normalization_is_deterministic_and_does_not_mutate_ingestion() -> None:
    ingested = ingest_directory(DEMO_RAW)
    before = tuple(dict(row) for table in ingested.tables for row in table.rows)

    first = normalize(ingested)
    second = normalize(ingested)

    assert first == second
    assert tuple(dict(row) for table in ingested.tables for row in table.rows) == before
