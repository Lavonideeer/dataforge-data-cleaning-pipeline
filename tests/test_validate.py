"""DF-004 hard validation, duplicate precedence, reference validation, and oracle tests.

The corpus tests deliberately run the production pipeline first and only then read
the preregistered DF-001 fixtures, so the fixtures act as an oracle instead of an
implementation input.
"""

from __future__ import annotations

import builtins
import csv
import functools
import io
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import Workbook

from dataforge.dedupe import DUPLICATE_EXACT, DUPLICATE_KEY_CONFLICT, SourceRef
from dataforge.ingest import ingest_directory
from dataforge.normalize import DEMO_DATE, TIMEZONE, NormalizationResult, normalize
from dataforge.validate import (
    ACCEPTED,
    ACTION_TYPES,
    DEDUPLICATED,
    FUZZY_CUSTOMER_CANDIDATE,
    FUZZY_THRESHOLD,
    QUARANTINED,
    REFERENCE_DEDUPLICATED,
    REFERENCE_KEY_CONFLICT,
    REFERENCE_REJECTED,
    REFERENCE_VALID,
    TERMINAL_DISPOSITIONS,
    UNKNOWN_CUSTOMER_REFERENCE,
    UNKNOWN_PRODUCT_REFERENCE,
    Reconciliation,
    ValidationInvariantError,
    ValidationResult,
    hard_failures,
    validate,
)


ROOT = Path(__file__).resolve().parents[1]
DEMO_RAW = ROOT / "data" / "demo_raw"
EXPECTED_DIR = ROOT / "data" / "demo_expected"
SRC_DIR = ROOT / "src" / "dataforge"

SALES_HEADER = (
    "order_id",
    "customer_id",
    "product_id",
    "order_date",
    "quantity",
    "unit_price_eur",
    "country",
)

DEFAULT_CUSTOMERS = (("C001", "Aline Martin", "aline.martin@example.test", "FR"),)
DEFAULT_PRODUCTS = (("P001", "Laptop Stand", "ELECTRONICS", "49.90"),)

#: Production modules DF-004 owns or consumes; they must never read the oracle.
PRODUCTION_MODULES = ("ingest.py", "schema.py", "normalize.py", "dedupe.py", "validate.py")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _valid_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "order_id": "ORD-1",
        "customer_id": "C001",
        "product_id": "P001",
        "order_date": "2026-07-01",
        "quantity": 1,
        "unit_price_eur": "49.90",
        "country": "FR",
    }
    row.update(overrides)
    return row


def _sale(**overrides: object) -> dict[str, object]:
    return _valid_row(**overrides)


def _csv_text(header: tuple[str, ...], rows: list[tuple[object, ...]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def _write_corpus(
    root: Path,
    sales: list[dict[str, object]],
    customers: tuple[tuple[object, ...], ...] = DEFAULT_CUSTOMERS,
    products: tuple[tuple[object, ...], ...] = DEFAULT_PRODUCTS,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    sales_rows = [
        tuple("" if row.get(field) is None else row.get(field) for field in SALES_HEADER)
        for row in sales
    ]
    (root / "sales_case.csv").write_text(_csv_text(SALES_HEADER, sales_rows), encoding="utf-8")
    (root / "products.csv").write_text(
        _csv_text(("product_id", "product_name", "category", "unit_price_eur"), list(products)),
        encoding="utf-8",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "customers"
    sheet.append(["customer_id", "customer_name", "email", "country"])
    for row in customers:
        sheet.append(list(row))
    workbook.save(root / "customers.xlsx")
    workbook.close()
    return root


def _run(root: Path) -> ValidationResult:
    return validate(normalize(ingest_directory(root)))


def _case_result(tmp_path: Path, name: str, sales: list[dict[str, object]], **kwargs: object) -> ValidationResult:
    return _run(_write_corpus(tmp_path / name, sales, **kwargs))


@functools.lru_cache(maxsize=1)
def _corpus() -> tuple[NormalizationResult, ValidationResult]:
    normalization = normalize(ingest_directory(DEMO_RAW))
    return normalization, validate(normalization)


def _fixture_map(rows: list[dict[str, str]]) -> dict[str, tuple[str, str, int]]:
    return {
        row["fixture_id"]: (row["source_file"], row["source_sheet"], int(row["source_row"]))
        for row in rows
    }


def _oracle_rows() -> list[dict[str, str]]:
    with (EXPECTED_DIR / "expected_transactions.csv").open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _oracle_outcomes() -> dict:
    return json.loads((EXPECTED_DIR / "expected_outcomes.json").read_text(encoding="utf-8"))


def _evidence_codes(
    normalization: NormalizationResult, result: ValidationResult, ref: SourceRef
) -> list[str]:
    """DF-003 normalization/flag evidence for a row, then DF-004 terminal codes."""

    identity = ref.as_tuple()
    codes: list[str] = []
    for event in normalization.events:
        if (event.source_file, event.source_sheet, event.source_row) == identity:
            if event.code not in codes:
                codes.append(event.code)
    for issue in normalization.issues:
        if (issue.source_file, issue.source_sheet, issue.source_row) == identity:
            if issue.action_type == "FLAG" and issue.code not in codes:
                codes.append(issue.code)
    codes.extend(result.outcome_for(ref).rule_codes)
    return codes


def _by_identity(result: ValidationResult) -> dict[tuple[str, str, int], object]:
    return {outcome.ref.as_tuple(): outcome for outcome in result.terminal_outcomes}


def _reference_by_identity(result: ValidationResult) -> dict[tuple[str, str, str, int], object]:
    return {
        (item.record_type, item.ref.source_file, item.ref.source_sheet, item.ref.source_row): item
        for item in result.reference_results
    }


# --------------------------------------------------------------------------- #
# Hard validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("order_id", "MISSING_ORDER_ID"),
        ("customer_id", "MISSING_CUSTOMER_ID"),
        ("product_id", "MISSING_PRODUCT_ID"),
        ("order_id", "INVALID_IDENTIFIER"),
        ("customer_id", "INVALID_IDENTIFIER"),
        ("product_id", "INVALID_IDENTIFIER"),
        ("order_date", "INVALID_ORDER_DATE"),
        ("quantity", "INVALID_QUANTITY"),
        ("unit_price_eur", "INVALID_UNIT_PRICE"),
    ],
)
def test_normalization_issue_maps_to_its_hard_failure(field: str, code: str) -> None:
    failures = hard_failures(_valid_row(), {field: code})
    assert [failure.code for failure in failures] == [code]
    assert failures[0].field_name == field
    assert failures[0].origin == "NORMALIZATION_ISSUE"


@pytest.mark.parametrize("quantity", [0, -2, -1])
def test_non_positive_quantity_is_a_business_rule_failure(quantity: int) -> None:
    failures = hard_failures(_valid_row(quantity=quantity), {})
    assert [failure.code for failure in failures] == ["INVALID_QUANTITY"]
    assert failures[0].origin == "BUSINESS_RULE"


def test_non_integer_quantity_issue_is_a_normalization_failure() -> None:
    failures = hard_failures(_valid_row(quantity=2.5), {"quantity": "INVALID_QUANTITY"})
    assert failures[0].origin == "NORMALIZATION_ISSUE"


def test_negative_price_is_a_business_rule_failure_with_the_frozen_code() -> None:
    failures = hard_failures(_valid_row(unit_price_eur="-12.50"), {})
    assert [failure.code for failure in failures] == ["INVALID_UNIT_PRICE"]
    assert failures[0].origin == "BUSINESS_RULE"
    assert "negative" in failures[0].reason


def test_zero_price_is_accepted_by_the_nonnegative_rule() -> None:
    assert hard_failures(_valid_row(unit_price_eur="0.00"), {}) == ()


def test_parse_failure_and_negative_price_stay_distinct_concepts() -> None:
    malformed = hard_failures(_valid_row(), {"unit_price_eur": "INVALID_UNIT_PRICE"})[0]
    negative = hard_failures(_valid_row(unit_price_eur="-12.50"), {})[0]
    assert malformed.code == negative.code == "INVALID_UNIT_PRICE"
    assert malformed.origin == "NORMALIZATION_ISSUE"
    assert negative.origin == "BUSINESS_RULE"
    assert "negative" not in malformed.reason


def test_future_date_uses_the_frozen_clock_and_not_the_machine_clock() -> None:
    assert DEMO_DATE.isoformat() == "2026-10-07"
    assert TIMEZONE == "Europe/Paris"
    on_clock = hard_failures(_valid_row(order_date="2026-10-07"), {})
    future = hard_failures(_valid_row(order_date="2026-10-08"), {})
    assert on_clock == ()
    assert [failure.code for failure in future] == ["FUTURE_ORDER_DATE"]
    assert future[0].origin == "BUSINESS_RULE"


@pytest.mark.parametrize("code", ["UNKNOWN_COUNTRY", "UNKNOWN_CATEGORY", "INVALID_EMAIL_FORMAT"])
def test_flag_only_issues_are_never_terminal(code: str) -> None:
    assert hard_failures(_valid_row(), {"country": code}) == ()


def test_all_failures_are_retained_in_frozen_priority_order() -> None:
    row = _valid_row(order_date="2026-10-08", quantity=0)
    failures = hard_failures(row, {"customer_id": "MISSING_CUSTOMER_ID"})
    assert [failure.code for failure in failures] == [
        "MISSING_CUSTOMER_ID",
        "FUTURE_ORDER_DATE",
        "INVALID_QUANTITY",
    ]


def test_missing_key_precedes_invalid_identifier() -> None:
    failures = hard_failures(
        _valid_row(), {"order_id": "INVALID_IDENTIFIER", "customer_id": "MISSING_CUSTOMER_ID"}
    )
    assert [failure.code for failure in failures] == ["MISSING_CUSTOMER_ID", "INVALID_IDENTIFIER"]


def test_identifier_failures_tie_break_by_frozen_field_order() -> None:
    failures = hard_failures(
        _valid_row(),
        {
            "product_id": "INVALID_IDENTIFIER",
            "order_id": "INVALID_IDENTIFIER",
            "customer_id": "INVALID_IDENTIFIER",
        },
    )
    assert [failure.field_name for failure in failures] == ["order_id", "customer_id", "product_id"]
    assert all(failure.code == "INVALID_IDENTIFIER" for failure in failures)


# --------------------------------------------------------------------------- #
# Cases A-J on purpose-built corpora (rules, not fixture rows)
# --------------------------------------------------------------------------- #


def test_case_a_valid_row_is_accepted(tmp_path: Path) -> None:
    result = _case_result(tmp_path, "a", [_sale()])
    assert result.reconciliation.as_dict() == {
        "input": 1,
        "accepted": 1,
        "quarantined": 0,
        "deduplicated": 0,
        "reconciled": True,
    }
    (outcome,) = result.accepted
    assert outcome.disposition == ACCEPTED
    assert outcome.rule_codes == ()
    assert outcome.primary_rule_code is None


def test_case_b_formatting_only_is_accepted(tmp_path: Path) -> None:
    result = _case_result(
        tmp_path,
        "b",
        [_sale(order_id=" ord-0002 ", order_date="02/07/2026", unit_price_eur="1 299,00 EUR", country="france")],
    )
    (outcome,) = result.accepted
    assert outcome.disposition == ACCEPTED
    assert outcome.rule_codes == ()
    assert outcome.values["order_id"] == "ORD-0002"
    assert outcome.values["unit_price_eur"] == "1299.00"
    assert result.validation_events == ()


def test_case_c_invalid_quantity_is_quarantined(tmp_path: Path) -> None:
    result = _case_result(tmp_path, "c", [_sale(quantity=0)])
    (outcome,) = result.quarantined
    assert outcome.disposition == QUARANTINED
    assert outcome.rule_codes == ("INVALID_QUANTITY",)
    assert result.reconciliation.as_dict()["input"] == 1
    assert result.reconciliation.accepted_rows == 0


def test_case_d_exact_duplicate_keeps_first_and_deduplicates_later(tmp_path: Path) -> None:
    result = _case_result(tmp_path, "d", [_sale(), _sale()])
    (survivor,) = result.accepted
    (copy,) = result.deduplicated
    assert survivor.ref.source_row == 2
    assert copy.ref.source_row == 3
    assert copy.rule_codes == (DUPLICATE_EXACT,)
    assert copy.survivor == survivor.ref
    assert result.reconciliation.as_dict() == {
        "input": 2,
        "accepted": 1,
        "quarantined": 0,
        "deduplicated": 1,
        "reconciled": True,
    }


def test_case_e_conflicting_key_quarantines_every_member(tmp_path: Path) -> None:
    result = _case_result(tmp_path, "e", [_sale(quantity=2), _sale(quantity=3)])
    assert result.accepted == ()
    assert result.deduplicated == ()
    assert [outcome.rule_codes for outcome in result.quarantined] == [
        (DUPLICATE_KEY_CONFLICT,),
        (DUPLICATE_KEY_CONFLICT,),
    ]
    assert result.reconciliation.as_dict()["quarantined"] == 2


def test_case_f_hard_invalid_row_never_joins_duplicate_accounting(tmp_path: Path) -> None:
    result = _case_result(tmp_path, "f", [_sale(quantity=-2), _sale(quantity=2)])
    assert [(outcome.ref.source_row, outcome.rule_codes) for outcome in result.quarantined] == [
        (2, ("INVALID_QUANTITY",))
    ]
    assert [outcome.ref.source_row for outcome in result.accepted] == [3]
    assert result.deduplicated == ()
    assert result.reconciliation.as_dict() == {
        "input": 2,
        "accepted": 1,
        "quarantined": 1,
        "deduplicated": 0,
        "reconciled": True,
    }


def test_case_g_fuzzy_customer_is_evidence_only(tmp_path: Path) -> None:
    customers = (
        ("C001", "Mila Durant", "mila.durant@example.test", "FR"),
        ("C002", "Mila Durand", "mila.durand@example.test", "FR"),
    )
    result = _case_result(tmp_path, "g", [_sale(customer_id="C001")], customers=customers)
    (outcome,) = result.accepted
    assert outcome.disposition == ACCEPTED
    assert outcome.values["customer_id"] == "C001"
    assert [
        (item.key, item.disposition)
        for item in result.reference_results
        if item.record_type == "customers"
    ] == [("C001", REFERENCE_VALID), ("C002", REFERENCE_VALID)]
    (candidate,) = result.fuzzy_candidates
    assert candidate.left_id == "C001" and candidate.right_id == "C002"
    assert candidate.score >= FUZZY_THRESHOLD
    assert {event.code for event in result.validation_events} == {FUZZY_CUSTOMER_CANDIDATE}
    assert result.reconciliation.as_dict()["accepted"] == 1


def test_case_h_unknown_references_are_quarantined_with_frozen_precedence(tmp_path: Path) -> None:
    customer = _case_result(tmp_path, "h1", [_sale(customer_id="C999")])
    (outcome,) = customer.quarantined
    assert outcome.rule_codes == (UNKNOWN_CUSTOMER_REFERENCE,)

    product = _case_result(tmp_path, "h2", [_sale(product_id="P999")])
    (outcome,) = product.quarantined
    assert outcome.rule_codes == (UNKNOWN_PRODUCT_REFERENCE,)

    both = _case_result(tmp_path, "h3", [_sale(customer_id="C999", product_id="P999")])
    (outcome,) = both.quarantined
    assert outcome.rule_codes == (UNKNOWN_CUSTOMER_REFERENCE, UNKNOWN_PRODUCT_REFERENCE)
    assert outcome.primary_rule_code == UNKNOWN_CUSTOMER_REFERENCE
    assert both.reconciliation.as_dict()["accepted"] == 0


def test_case_i_future_date_is_quarantined(tmp_path: Path) -> None:
    result = _case_result(tmp_path, "i", [_sale(order_date="2026-10-08")])
    (outcome,) = result.quarantined
    assert outcome.rule_codes == ("FUTURE_ORDER_DATE",)
    assert result.reconciliation.as_dict()["quarantined"] == 1


def test_case_j_multiple_hard_failures_yield_one_terminal_disposition(tmp_path: Path) -> None:
    result = _case_result(
        tmp_path,
        "j",
        [_sale(customer_id="", order_date="2026-10-08", quantity=0)],
    )
    assert len(result.terminal_outcomes) == 1
    (outcome,) = result.quarantined
    assert outcome.rule_codes == ("MISSING_CUSTOMER_ID", "FUTURE_ORDER_DATE", "INVALID_QUANTITY")
    assert outcome.primary_rule_code == "MISSING_CUSTOMER_ID"
    assert result.reconciliation.as_dict() == {
        "input": 1,
        "accepted": 0,
        "quarantined": 1,
        "deduplicated": 0,
        "reconciled": True,
    }
    assert [event.code for event in result.validation_events] == [
        "MISSING_CUSTOMER_ID",
        "FUTURE_ORDER_DATE",
        "INVALID_QUANTITY",
    ]


def test_both_duplicate_like_rows_hard_invalid_leaves_nothing_deduplicated(tmp_path: Path) -> None:
    result = _case_result(tmp_path, "ff", [_sale(quantity=-2), _sale(quantity=-3)])
    assert [outcome.rule_codes for outcome in result.quarantined] == [
        ("INVALID_QUANTITY",),
        ("INVALID_QUANTITY",),
    ]
    assert result.deduplicated == ()
    assert result.accepted == ()


def test_missing_order_id_is_quarantined_before_deduplication(tmp_path: Path) -> None:
    result = _case_result(tmp_path, "missing", [_sale(order_id=""), _sale(order_id="")])
    assert [outcome.rule_codes for outcome in result.quarantined] == [
        ("MISSING_ORDER_ID",),
        ("MISSING_ORDER_ID",),
    ]
    assert result.deduplicated == ()


# --------------------------------------------------------------------------- #
# Reference validation
# --------------------------------------------------------------------------- #


def test_reference_rows_missing_key_or_invalid_price_are_rejected(tmp_path: Path) -> None:
    products = (
        ("P001", "Laptop Stand", "ELECTRONICS", "49.90"),
        ("", "Missing SKU", "OFFICE", "10.00"),
        ("P013", "Invalid Price", "OFFICE", "-3.00"),
    )
    result = _run(_write_corpus(tmp_path / "ref", [_sale()], products=products))
    by_row = {(item.record_type, item.ref.source_row): item for item in result.reference_results}
    assert by_row[("products", 3)].disposition == REFERENCE_REJECTED
    assert by_row[("products", 3)].rule_codes == ("MISSING_PRODUCT_ID",)
    assert by_row[("products", 4)].disposition == REFERENCE_REJECTED
    assert by_row[("products", 4)].rule_codes == ("INVALID_UNIT_PRICE",)
    assert result.product_counts.as_dict() == {
        "input": 3,
        "valid": 1,
        "rejected": 2,
        "deduplicated": 0,
        "reconciled": True,
    }


def test_unknown_country_and_category_do_not_reject_reference_rows(tmp_path: Path) -> None:
    customers = (("C001", "Aline Martin", "not-an-email", "Atlantis"),)
    products = (("P001", "Laptop Stand", "Gadgets", "49.90"),)
    result = _run(
        _write_corpus(tmp_path / "flags", [_sale()], customers=customers, products=products)
    )
    assert {item.disposition for item in result.reference_results} == {REFERENCE_VALID}
    assert result.reconciliation.as_dict()["accepted"] == 1
    assert not any(
        event.code in {"UNKNOWN_COUNTRY", "UNKNOWN_CATEGORY", "INVALID_EMAIL_FORMAT"}
        for event in result.validation_events
    )


def test_reference_exact_duplicate_and_conflict(tmp_path: Path) -> None:
    products = (
        ("P001", "Laptop Stand", "ELECTRONICS", "49.90"),
        ("P002", "Desk Lamp", "HOME", "39.50"),
    )
    duplicate = _run(
        _write_corpus(tmp_path / "refdup", [_sale()], products=products + (products[1],))
    )
    by_row = {item.ref.source_row: item for item in duplicate.reference_results if item.record_type == "products"}
    assert by_row[3].disposition == REFERENCE_VALID
    assert by_row[4].disposition == REFERENCE_DEDUPLICATED
    assert by_row[4].rule_codes == ("REFERENCE_DUPLICATE_EXACT",)
    assert by_row[4].survivor == by_row[3].ref

    conflicting = _run(
        _write_corpus(
            tmp_path / "refconflict",
            [_sale(product_id="P002")],
            products=products + (("P002", "Desk Lamp", "HOME", "41.00"),),
        )
    )
    by_row = {
        item.ref.source_row: item for item in conflicting.reference_results if item.record_type == "products"
    }
    assert [by_row[row].disposition for row in (3, 4)] == [REFERENCE_REJECTED, REFERENCE_REJECTED]
    assert all(by_row[row].rule_codes == (REFERENCE_KEY_CONFLICT,) for row in (3, 4))
    # The conflicted key is unavailable, so the sales row cannot be accepted.
    assert [outcome.rule_codes for outcome in conflicting.quarantined] == [
        (UNKNOWN_PRODUCT_REFERENCE,)
    ]


def test_accepted_rows_are_not_enriched_with_reference_fields(tmp_path: Path) -> None:
    result = _case_result(tmp_path, "enrich", [_sale()])
    (outcome,) = result.accepted
    assert tuple(outcome.values) == (
        "order_id",
        "customer_id",
        "product_id",
        "order_date",
        "quantity",
        "unit_price_eur",
        "country",
    )


# --------------------------------------------------------------------------- #
# Model invariants
# --------------------------------------------------------------------------- #


def test_frozen_terminal_disposition_catalogue() -> None:
    assert TERMINAL_DISPOSITIONS == ("ACCEPTED", "QUARANTINED", "DEDUPLICATED")


def test_terminal_buckets_are_disjoint_and_complete() -> None:
    _, result = _corpus()
    accepted = {outcome.ref for outcome in result.accepted}
    quarantined = {outcome.ref for outcome in result.quarantined}
    deduplicated = {outcome.ref for outcome in result.deduplicated}
    assert not accepted & quarantined
    assert not accepted & deduplicated
    assert not quarantined & deduplicated
    assert accepted | quarantined | deduplicated == set(result.input_transactions)
    assert len(result.input_transactions) == 164


def test_buckets_are_ordered_by_deterministic_source_order() -> None:
    _, result = _corpus()
    for bucket in (result.accepted, result.quarantined, result.deduplicated):
        keys = [outcome.ref.sort_key for outcome in bucket]
        assert keys == sorted(keys)


def test_result_fails_closed_on_invariant_violation() -> None:
    _, result = _corpus()
    with pytest.raises(ValidationInvariantError):
        replace(result, reconciliation=Reconciliation(164, 145, 16, 2))
    with pytest.raises(ValidationInvariantError):
        replace(result, accepted=result.accepted + (result.quarantined[0],))
    with pytest.raises(ValidationInvariantError):
        replace(result, accepted=result.accepted[:-1])
    with pytest.raises(ValidationInvariantError):
        replace(result, input_transactions=result.input_transactions[:-1])


def test_repeated_execution_is_identical() -> None:
    first_normalization, first = _corpus()
    second_normalization = normalize(ingest_directory(DEMO_RAW))
    second = validate(second_normalization)
    assert first == second
    assert first.reconciliation == second.reconciliation
    assert [
        (item.ref, item.disposition, item.rule_codes, item.primary_rule_code, item.survivor)
        for item in first.terminal_outcomes
    ] == [
        (item.ref, item.disposition, item.rule_codes, item.primary_rule_code, item.survivor)
        for item in second.terminal_outcomes
    ]
    assert first.validation_events == second.validation_events
    assert first.fuzzy_candidates == second.fuzzy_candidates
    assert first.reference_results == second.reference_results
    assert first_normalization.issues == second_normalization.issues


# --------------------------------------------------------------------------- #
# Blind oracle: production first, preregistered fixtures second
# --------------------------------------------------------------------------- #


FORBIDDEN_TOKENS = (
    "demo_expected",
    "expected_transactions",
    "expected_outcomes",
    "ground_truth",
)


def test_production_code_never_reads_preregistered_expected_outputs(monkeypatch: pytest.MonkeyPatch) -> None:
    # Path.open, zipfile, and csv all route through io.open, so both entry points
    # are guarded to prove DF-004 derives its result without the oracle.
    real_open = io.open
    opened: list[str] = []

    def guarded_open(file: object, *args: object, **kwargs: object):
        name = os.fspath(file) if isinstance(file, (str, bytes, os.PathLike)) else repr(file)
        text = name if isinstance(name, str) else name.decode("utf-8", "replace")
        opened.append(text)
        if "demo_expected" in text.replace("\\", "/"):
            raise AssertionError(f"production code attempted to read the oracle: {text}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(io, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_open)
    try:
        result = _run(DEMO_RAW)
    finally:
        monkeypatch.undo()

    assert opened, "the guarded run did not read any file"
    assert result.reconciliation.reconciled
    assert result.reconciliation.input_transaction_rows == 164


def test_production_sources_contain_no_expected_fixture_references() -> None:
    for name in PRODUCTION_MODULES:
        source = (SRC_DIR / name).read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            assert token not in source, f"{name} references {token!r}"


def test_full_corpus_aggregate_reconciliation() -> None:
    _, result = _corpus()
    assert result.reconciliation.as_dict() == {
        "input": 164,
        "accepted": 146,
        "quarantined": 16,
        "deduplicated": 2,
        "reconciled": True,
    }
    assert result.reconciliation.input_transaction_rows == (
        result.reconciliation.accepted_rows
        + result.reconciliation.quarantined_rows
        + result.reconciliation.deduplicated_rows
    )
    assert len(result.accepted) == 146
    assert len(result.quarantined) == 16
    assert len(result.deduplicated) == 2
    assert (result.customer_counts.as_dict(), result.product_counts.as_dict()) == (
        {"input": 26, "valid": 24, "rejected": 1, "deduplicated": 1, "reconciled": True},
        {"input": 15, "valid": 12, "rejected": 2, "deduplicated": 1, "reconciled": True},
    )


def test_full_corpus_matches_the_oracle_row_by_row() -> None:
    normalization, result = _corpus()
    oracle = _oracle_rows()
    fixtures = _fixture_map(oracle)
    computed = _by_identity(result)

    assert len(oracle) == len(result.input_transactions) == 164
    assert set(computed) == {
        (row["source_file"], row["source_sheet"], int(row["source_row"])) for row in oracle
    }

    mismatches: list[str] = []
    non_terminal_divergence: list[tuple[tuple[str, str, int], list[str], list[str]]] = []
    for row in oracle:
        identity = (row["source_file"], row["source_sheet"], int(row["source_row"]))
        outcome = computed[identity]
        expected_disposition = row["expected_terminal_disposition"]
        if outcome.disposition != expected_disposition:
            mismatches.append(
                f"{row['fixture_id']} disposition computed={outcome.disposition} expected={expected_disposition}"
            )

        expected_codes = [code for code in row["expected_codes"].split("|") if code]
        expected_terminal = tuple(code for code in expected_codes if code in ACTION_TYPES)
        expected_non_terminal = [code for code in expected_codes if code not in ACTION_TYPES]
        actual_non_terminal = [
            code for code in _evidence_codes(normalization, result, outcome.ref) if code not in ACTION_TYPES
        ]
        if actual_non_terminal != expected_non_terminal:
            non_terminal_divergence.append((identity, actual_non_terminal, expected_non_terminal))

        if outcome.rule_codes != expected_terminal:
            mismatches.append(
                f"{row['fixture_id']} terminal computed={outcome.rule_codes} expected={expected_terminal}"
            )
        if outcome.primary_rule_code != (expected_terminal[0] if expected_terminal else None):
            mismatches.append(
                f"{row['fixture_id']} primary computed={outcome.primary_rule_code} expected={expected_terminal[:1]}"
            )

        if expected_disposition == DEDUPLICATED:
            expected_survivor = fixtures[row["related_fixture_id"]]
            actual_survivor = outcome.survivor.as_tuple() if outcome.survivor else None
            if actual_survivor != expected_survivor:
                mismatches.append(
                    f"{row['fixture_id']} survivor computed={actual_survivor} expected={expected_survivor}"
                )

    assert not mismatches, "\n".join(mismatches)

    # Every row's terminal disposition, terminal codes, primary code, and duplicate
    # survivor matched exactly. Exactly one row differs in the case-scoped
    # non-terminal code list; see test_oracle_expected_codes_are_case_scoped.
    assert [item[0] for item in non_terminal_divergence] == [("sales_2026_10.xlsx", "sales", 3)]
    identity, actual, expected = non_terminal_divergence[0]
    assert actual == ["NORMALIZE_DATE"]
    assert expected == []


def test_oracle_expected_codes_are_case_scoped_not_an_event_dump() -> None:
    """TX-0122 pins the semantics of the oracle ``expected_codes`` column.

    Fixture case J declares only the three terminal hard-failure codes for
    ORD-0122. DF-003 independently emits its frozen ``NORMALIZE_DATE`` event for
    ``09/10/2026`` -> ``2026-10-09``, which the DF-003 suite already pins
    (``test_boundary_keeps_negative_future_missing_and_duplicate_rows``). The
    terminal disposition and the terminal rule codes agree exactly with the
    oracle, and appendix section 8.1 defines rejected-row ``rule_codes`` as every
    applicable *terminal-stage* code, so this is fixture metadata scope rather
    than a rule inconsistency.
    """

    normalization, result = _corpus()
    outcome = result.outcome_for(SourceRef("sales_2026_10.xlsx", "sales", 3))
    assert outcome.disposition == QUARANTINED
    assert outcome.rule_codes == ("MISSING_CUSTOMER_ID", "FUTURE_ORDER_DATE", "INVALID_QUANTITY")
    assert outcome.primary_rule_code == "MISSING_CUSTOMER_ID"

    events = [
        event
        for event in normalization.events
        if (event.source_file, event.source_sheet, event.source_row) == ("sales_2026_10.xlsx", "sales", 3)
    ]
    assert [(event.code, event.field_name, event.cleaned_value) for event in events] == [
        ("NORMALIZE_DATE", "order_date", "2026-10-09")
    ]


def test_full_corpus_reference_rows_match_the_oracle_cases() -> None:
    _, result = _corpus()
    references = _reference_by_identity(result)
    events_by_ref: dict[tuple[str, str, int], set[str]] = {}
    for event in result.validation_events:
        events_by_ref.setdefault(event.ref.as_tuple(), set()).add(event.code)

    checked = 0
    mismatches: list[str] = []
    for case in _oracle_outcomes()["cases"]:
        records = case["source_records"]
        dispositions = case["expected_terminal_dispositions"]
        if not dispositions or len(dispositions) != len(records):
            continue
        case_refs: list[tuple[str, str, str, int]] = []
        for record, expected in zip(records, dispositions, strict=True):
            if record.get("record_type") not in {"customers", "products"}:
                continue
            if not str(expected).startswith("REFERENCE_"):
                continue
            identity = (
                record["record_type"],
                record["source_file"],
                record.get("source_sheet", ""),
                int(record["source_row"]),
            )
            case_refs.append(identity)
            actual = references.get(identity)
            checked += 1
            if actual is None or actual.disposition != expected:
                mismatches.append(
                    f"{case['case_id']} {identity} computed={getattr(actual, 'disposition', None)} expected={expected}"
                )
        if not case_refs:
            continue
        actual_codes: set[str] = set()
        for identity in case_refs:
            actual_codes |= set(references[identity].rule_codes)
            actual_codes |= events_by_ref.get(identity[1:], set())
        expected_codes = {code for code in case["expected_codes"] if code in ACTION_TYPES}
        if actual_codes != expected_codes:
            mismatches.append(
                f"{case['case_id']} reference codes computed={sorted(actual_codes)} expected={sorted(expected_codes)}"
            )

    assert checked >= 15, f"only {checked} reference expectations were compared"
    assert not mismatches, "\n".join(mismatches)


def test_full_corpus_case_f_independence_and_duplicate_conflict_rows() -> None:
    _, result = _corpus()
    by_identity = _by_identity(result)
    july = "sales_2026_07.csv"

    # Case F: the hard-invalid ORD-0006 row is quarantined; its valid companion
    # is accepted alone rather than being deduplicated.
    assert by_identity[(july, "", 7)].rule_codes == ("INVALID_QUANTITY",)
    assert by_identity[(july, "", 44)].disposition == ACCEPTED

    # Case E: both conflicting ORD-0005 members are quarantined.
    assert by_identity[(july, "", 6)].rule_codes == (DUPLICATE_KEY_CONFLICT,)
    assert by_identity[(july, "", 43)].rule_codes == (DUPLICATE_KEY_CONFLICT,)

    # Case D and the normalized duplicate keep the first source-order survivor.
    assert by_identity[(july, "", 42)].survivor == SourceRef(july, "", 5)
    assert by_identity[(july, "", 45)].survivor == SourceRef(july, "", 31)

    # Case H reference failures.
    assert by_identity[(july, "", 9)].rule_codes == (UNKNOWN_CUSTOMER_REFERENCE,)
    assert by_identity[(july, "", 10)].rule_codes == (UNKNOWN_PRODUCT_REFERENCE,)

    # Case J evidence multiplicity does not multiply the terminal count.
    scope = by_identity[("sales_2026_10.xlsx", "sales", 3)]
    assert scope.rule_codes == ("MISSING_CUSTOMER_ID", "FUTURE_ORDER_DATE", "INVALID_QUANTITY")
    assert len(result.terminal_outcomes) == 164


def test_full_corpus_fuzzy_candidates_are_flagged_without_merging() -> None:
    _, result = _corpus()
    (candidate,) = result.fuzzy_candidates
    assert (candidate.left_id, candidate.right_id) == ("C020", "C021")
    assert candidate.score >= FUZZY_THRESHOLD
    assert (candidate.left.source_row, candidate.right.source_row) == (21, 22)

    references = _reference_by_identity(result)
    assert references[("customers", "customers.xlsx", "customers", 21)].disposition == REFERENCE_VALID
    assert references[("customers", "customers.xlsx", "customers", 22)].disposition == REFERENCE_VALID
    # Case G keeps two distinct identities and never rewrites a sales foreign key.
    assert len([item for item in result.reference_results if item.record_type == "customers"]) == 26
    sales = {item.values["customer_id"] for item in result.accepted}
    assert {"C020", "C021"} <= sales
