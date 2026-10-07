"""DF-005 evidence-artifact and quality-report tests.

The corpus fixtures run the real DF-002 -> DF-003 -> DF-004 -> DF-005 composition
into a temporary directory. DF-005 is a reporting boundary: these tests assert
that it serializes upstream evidence faithfully, completely, deterministically,
and without recomputing any business decision.
"""

from __future__ import annotations

import csv
import functools
import hashlib
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from openpyxl import Workbook

import dataforge.audit as audit_module
import dataforge.report as report_module
from dataforge.audit import (
    AUDIT_COLUMNS,
    DF005_ARTIFACT_NAMES,
    FROZEN_CODE_ORDER,
    GROUP_EVENT_CODES,
    REJECTED_BUSINESS_COLUMNS,
    REJECTED_COLUMNS,
    V1_OUTPUT_NAMES,
    assemble_audit_events,
    build_cleaning_summary,
    build_input_file_entries,
    build_rejected_rows,
    rejected_disposition_counts,
    serialize_value,
)
from dataforge.ingest import ingest_directory
from dataforge.normalize import normalize
from dataforge.report import build_report_html, write_evidence_outputs
from dataforge.validate import (
    ACCEPTED,
    DEDUPLICATED,
    QUARANTINED,
    REFERENCE_DEDUPLICATED,
    REFERENCE_REJECTED,
    REFERENCE_VALID,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]
DEMO_RAW = ROOT / "data" / "demo_raw"
DEMO_EXPECTED = ROOT / "data" / "demo_expected"

REJECTED_HEADER = (
    "record_type,source_file,source_sheet,source_row,terminal_disposition,"
    "primary_rule_code,rule_codes,reason,order_id,customer_id,product_id,order_date,"
    "quantity,unit_price_eur,country,customer_name,email,product_name,category"
)
AUDIT_HEADER = (
    "event_index,record_type,source_file,source_sheet,source_row,field_name,"
    "original_value,cleaned_value,code,action_type,related_source_file,"
    "related_source_sheet,related_source_row,message"
)

FROZEN_SUMMARY_KEYS = {
    "specification_version",
    "run_metadata",
    "input_files",
    "transaction_counts",
    "reference_counts",
    "rule_counts",
    "normalization_counts",
    "outputs",
}
FROZEN_RUN_METADATA_KEYS = {
    "pipeline_version",
    "demo_seed",
    "demo_date",
    "timezone",
    "source_ordering",
}

#: Rule/parsing engines DF-005 must never reach for: reporting reports decisions.
FORBIDDEN_REPORTING_NAMES = (
    "normalize_currency",
    "normalize_date",
    "normalize_country",
    "normalize_category",
    "normalize_email",
    "normalize_identifier",
    "normalize_integer",
    "hard_failures",
    "resolve_duplicates",
    "fuzzy_customer_candidates",
    "canonical_business_key",
    "canonical_reference_key",
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def corpus():
    ingestion = ingest_directory(DEMO_RAW)
    normalization = normalize(ingestion)
    validation = validate(normalization)
    return ingestion, normalization, validation


@pytest.fixture(scope="module")
def written(corpus, tmp_path_factory):
    ingestion, normalization, validation = corpus
    directory = tmp_path_factory.mktemp("df005")
    return write_evidence_outputs(
        normalization, validation, directory, ingestion=ingestion, input_dir=DEMO_RAW
    )


@pytest.fixture(scope="module")
def rejected_rows(written):
    with written.rejected_rows.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


@pytest.fixture(scope="module")
def audit_rows(written):
    with written.audit_log.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


@pytest.fixture(scope="module")
def summary(written):
    return json.loads(written.cleaning_summary.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report_html(written):
    return written.data_quality_report.read_text(encoding="utf-8")


def _header_line(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as stream:
        return stream.readline().rstrip("\n")


def _tree_digest(root: Path) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _expected_fingerprints(normalization, validation) -> list[tuple]:
    """Independently rebuild the expected audit fingerprints from upstream evidence."""

    expected: list[tuple] = []
    for event in normalization.events:
        expected.append(
            (
                event.record_type,
                event.source_file,
                event.source_sheet,
                event.source_row,
                event.field_name,
                serialize_value(event.original_value),
                serialize_value(event.cleaned_value),
                event.code,
                event.action_type,
                "",
                "",
                0,
                event.message,
            )
        )
    for issue in normalization.issues:
        if issue.action_type != "FLAG":
            continue
        expected.append(
            (
                issue.record_type,
                issue.source_file,
                issue.source_sheet,
                issue.source_row,
                issue.field_name,
                serialize_value(issue.original_value),
                serialize_value(issue.normalized_value),
                issue.code,
                issue.action_type,
                "",
                "",
                0,
                issue.message,
            )
        )
    for event in validation.validation_events:
        related = event.related
        expected.append(
            (
                event.record_type,
                event.source_file,
                event.source_sheet,
                event.source_row,
                "" if event.code in GROUP_EVENT_CODES else event.field_name,
                serialize_value(event.original_value),
                serialize_value(event.cleaned_value),
                event.code,
                event.action_type,
                related.source_file if related else "",
                related.source_sheet if related else "",
                related.source_row if related else 0,
                event.message,
            )
        )
    return sorted(expected)


class _HeadingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.headings: list[str] = []
        self._buffer: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in {"h1", "h2"}:
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._buffer is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2"} and self._buffer is not None:
            self.headings.append("".join(self._buffer).strip())
            self._buffer = None


@functools.lru_cache(maxsize=1)
def build_report_html_from_corpus() -> str:
    """Render the demo report for structural assertions without touching the repo."""

    ingestion = ingest_directory(DEMO_RAW)
    normalization = normalize(ingestion)
    validation = validate(normalization)
    summary = build_cleaning_summary(
        validation, ingestion, DEMO_RAW, assemble_audit_events(normalization, validation)
    )
    return build_report_html(
        validation=validation,
        summary=summary,
        rejected_rows=build_rejected_rows(validation, ingestion),
        input_files=build_input_file_entries(ingestion, DEMO_RAW),
    )


# --------------------------------------------------------------------------- #
# rejected_rows.csv (appendix 8.1; DF-005 Owner ruling OPTION_1_ADOPTED)
# --------------------------------------------------------------------------- #


def test_rejected_rows_schema_is_the_frozen_appendix_schema(written) -> None:
    assert _header_line(written.rejected_rows) == REJECTED_HEADER
    assert len(REJECTED_COLUMNS) == 19
    assert REJECTED_BUSINESS_COLUMNS == REJECTED_COLUMNS[8:]


def test_rejected_rows_regression_cardinality_and_disposition_counts(written) -> None:
    """Owner ruling section 12: the frozen corpus must yield exactly 23 data rows."""

    counts = rejected_disposition_counts(written.rejected_rows_data)
    assert len(written.rejected_rows_data) == 23
    assert counts[QUARANTINED] == 16
    assert counts[DEDUPLICATED] == 2
    assert counts[REFERENCE_REJECTED] == 3
    assert counts[REFERENCE_DEDUPLICATED] == 2
    assert sum(counts.values()) == 23


def test_transaction_g2_reconciliation_is_unchanged_by_the_evidence_artifact(corpus, rejected_rows, summary) -> None:
    """The 23-row evidence artifact must not widen the transaction equation."""

    _, _, validation = corpus
    counts = summary["transaction_counts"]
    assert counts == {
        "input": 164,
        "accepted": 146,
        "quarantined": 16,
        "deduplicated": 2,
        "reconciled": True,
    }
    assert counts["input"] == counts["accepted"] + counts["quarantined"] + counts["deduplicated"]
    assert validation.reconciliation.reconciled

    transaction_rows = [row for row in rejected_rows if row["record_type"] == "sales"]
    reference_rows = [row for row in rejected_rows if row["record_type"] != "sales"]
    assert len(transaction_rows) == 18
    assert len(reference_rows) == 5
    assert len(rejected_rows) == 23 != counts["quarantined"]


def test_rejected_rows_contain_every_df004_non_accepted_record(corpus, rejected_rows) -> None:
    _, _, validation = corpus
    expected = {
        ("sales", outcome.ref.as_tuple(), outcome.disposition)
        for outcome in (*validation.quarantined, *validation.deduplicated)
    } | {
        (outcome.record_type, outcome.ref.as_tuple(), outcome.disposition)
        for outcome in validation.reference_results
        if outcome.disposition in {REFERENCE_REJECTED, REFERENCE_DEDUPLICATED}
    }
    actual = {
        (row["record_type"], (row["source_file"], row["source_sheet"], int(row["source_row"])), row["terminal_disposition"])
        for row in rejected_rows
    }
    assert actual == expected


def test_rejected_rows_never_contain_accepted_or_valid_records(corpus, rejected_rows) -> None:
    _, _, validation = corpus
    dispositions = {row["terminal_disposition"] for row in rejected_rows}
    assert dispositions == {QUARANTINED, DEDUPLICATED, REFERENCE_REJECTED, REFERENCE_DEDUPLICATED}
    assert ACCEPTED not in dispositions
    assert REFERENCE_VALID not in dispositions

    accepted = {outcome.ref.as_tuple() for outcome in validation.accepted}
    written_rows = {(row["source_file"], row["source_sheet"], int(row["source_row"])) for row in rejected_rows}
    assert accepted & written_rows == set()

    # Codes agree with DF-004 exactly.
    by_identity = {
        (outcome.ref.source_file, outcome.ref.source_sheet, outcome.ref.source_row): outcome
        for outcome in (*validation.quarantined, *validation.deduplicated)
    }
    for row in rejected_rows:
        if row["record_type"] != "sales":
            continue
        outcome = by_identity[(row["source_file"], row["source_sheet"], int(row["source_row"]))]
        assert row["terminal_disposition"] == outcome.disposition
        assert row["rule_codes"] == "|".join(outcome.rule_codes)
        assert row["primary_rule_code"] == outcome.primary_rule_code


def test_rejected_rows_case_j_stays_a_single_row_with_ordered_codes(rejected_rows) -> None:
    matching = [
        row for row in rejected_rows
        if row["source_file"] == "sales_2026_10.xlsx" and row["source_row"] == "3"
    ]
    assert len(matching) == 1
    row = matching[0]
    assert row["terminal_disposition"] == QUARANTINED
    assert row["primary_rule_code"] == "MISSING_CUSTOMER_ID"
    assert row["rule_codes"] == "MISSING_CUSTOMER_ID|FUTURE_ORDER_DATE|INVALID_QUANTITY"
    assert "customer_id is missing" in row["reason"]
    assert "quantity" in row["reason"]


def test_rejected_rows_conflicting_key_members_are_both_present(rejected_rows) -> None:
    members = [
        row for row in rejected_rows
        if row["source_file"] == "sales_2026_07.csv" and row["source_row"] in {"6", "43"}
    ]
    assert len(members) == 2
    assert all(row["rule_codes"] == "DUPLICATE_KEY_CONFLICT" for row in members)
    assert all(row["terminal_disposition"] == QUARANTINED for row in members)


def test_rejected_rows_deduplicated_transactions_are_not_quarantined(corpus, rejected_rows) -> None:
    _, _, validation = corpus
    rows = {row["source_row"]: row for row in rejected_rows if row["source_file"] == "sales_2026_07.csv"}
    assert rows["42"]["terminal_disposition"] == DEDUPLICATED
    assert rows["45"]["terminal_disposition"] == DEDUPLICATED
    assert rows["42"]["rule_codes"] == "DUPLICATE_EXACT"
    assert rows["45"]["rule_codes"] == "DUPLICATE_EXACT"
    assert rows["42"]["order_id"] == "ORD-0004"
    assert rows["45"]["order_id"] == " ord-0030 "
    quarantined_provenance = {outcome.ref.as_tuple() for outcome in validation.quarantined}
    assert ("sales_2026_07.csv", "", 42) not in quarantined_provenance
    assert ("sales_2026_07.csv", "", 45) not in quarantined_provenance


def test_rejected_rows_reference_records_use_reference_columns(rejected_rows) -> None:
    customers = [row for row in rejected_rows if row["record_type"] == "customers"]
    products = [row for row in rejected_rows if row["record_type"] == "products"]
    assert len(customers) == 2 and len(products) == 3

    for row in customers:
        assert row["customer_name"] and row["email"]
        assert row["order_id"] == "" and row["product_id"] == ""
        assert row["order_date"] == "" and row["quantity"] == "" and row["unit_price_eur"] == ""
        assert row["product_name"] == "" and row["category"] == ""

    for row in products:
        assert row["product_name"] and row["category"]
        assert row["order_id"] == "" and row["customer_id"] == ""
        assert row["order_date"] == "" and row["quantity"] == "" and row["country"] == ""
        assert row["customer_name"] == "" and row["email"] == ""

    for row in (item for item in rejected_rows if item["record_type"] == "sales"):
        assert row["customer_name"] == "" and row["email"] == ""
        assert row["product_name"] == "" and row["category"] == ""


def test_rejected_rows_business_fields_are_original_source_values(rejected_rows) -> None:
    by_key = {
        (row["source_file"], row["source_row"]): row
        for row in rejected_rows
        if row["record_type"] == "sales"
    }
    # Raw, not normalized: the audit log carries cleaned values instead (8.1).
    assert by_key[("sales_2026_10.xlsx", "3")]["order_date"] == "09/10/2026"
    assert by_key[("sales_2026_07.csv", "32")]["customer_id"] == "BAD ID!"
    assert by_key[("sales_2026_07.csv", "45")]["order_id"] == " ord-0030 "
    assert by_key[("sales_2026_07.csv", "45")]["customer_id"] == " c010 "
    assert by_key[("sales_2026_07.csv", "45")]["country"] == "France"
    assert by_key[("sales_2026_07.csv", "14")]["unit_price_eur"] == "-5.00"
    assert by_key[("sales_2026_07.csv", "30")]["order_id"] == ""


def test_rejected_rows_are_in_frozen_source_order(rejected_rows) -> None:
    keys = [
        (row["source_file"].encode("utf-8"), row["source_sheet"].encode("utf-8"), int(row["source_row"]))
        for row in rejected_rows
    ]
    assert keys == sorted(keys)


def test_rejected_rows_primary_code_is_the_first_rule_code(rejected_rows) -> None:
    for row in rejected_rows:
        codes = row["rule_codes"].split("|") if row["rule_codes"] else []
        assert row["primary_rule_code"] == (codes[0] if codes else "")
        assert row["reason"], f"empty reason for {row['source_file']}:{row['source_row']}"


# --------------------------------------------------------------------------- #
# audit_log.csv (appendix 8.2)
# --------------------------------------------------------------------------- #


def test_audit_log_schema_is_the_frozen_appendix_schema(written) -> None:
    assert _header_line(written.audit_log) == AUDIT_HEADER
    assert len(AUDIT_COLUMNS) == 14


def test_audit_is_complete_and_contains_no_fabricated_events(corpus, written) -> None:
    """Appendix 8.2 completeness in both directions, proven on fingerprints."""

    _, normalization, validation = corpus
    expected = _expected_fingerprints(normalization, validation)
    actual = sorted(event.fingerprint for event in written.audit_events)
    assert actual == expected, "audit evidence must equal the upstream evidence multiset"

    flags = [issue for issue in normalization.issues if issue.action_type == "FLAG"]
    assert len(written.audit_events) == len(normalization.events) + len(flags) + len(validation.validation_events)


def test_audit_excludes_df003_validation_issues_already_covered_by_df004(corpus, written) -> None:
    _, normalization, validation = corpus
    duplicated = [
        issue
        for issue in normalization.issues
        if issue.action_type != "FLAG"
    ]
    assert duplicated, "expected DF-003 validation issues in this corpus"
    covered = {
        (event.source_file, event.source_sheet, event.source_row, event.field_name, event.code)
        for event in validation.validation_events
    }
    for issue in duplicated:
        key = (issue.source_file, issue.source_sheet, issue.source_row, issue.field_name, issue.code)
        assert key in covered, f"DF-003 issue {key} has no DF-004 failure event"
    codes = {event.code for event in written.audit_events}
    assert "MISSING_REQUIRED_COLUMN" not in codes
    # Four DF-003 flag issues plus the DF-004 fuzzy flag.
    assert len([event for event in written.audit_events if event.action_type == "FLAG"]) == 5
    assert len([issue for issue in normalization.issues if issue.action_type == "FLAG"]) == 4


def test_audit_event_index_is_contiguous_from_one(written) -> None:
    indexes = [event.event_index for event in written.audit_events]
    assert indexes == list(range(1, len(indexes) + 1))


def test_audit_preserves_original_and_cleaned_values(audit_rows) -> None:
    def find(row: int, field: str) -> dict:
        return next(
            item
            for item in audit_rows
            if item["source_file"] == "sales_2026_07.csv"
            and item["source_row"] == str(row)
            and item["field_name"] == field
        )

    identifier = find(3, "order_id")
    assert (identifier["original_value"], identifier["cleaned_value"]) == (" ord-0002 ", "ORD-0002")
    price = find(3, "unit_price_eur")
    assert (price["original_value"], price["cleaned_value"]) == ("1 299,00 EUR", "1299.00")
    country = find(3, "country")
    assert (country["original_value"], country["cleaned_value"]) == (" france ", "FR")
    assert identifier["action_type"] == "NORMALIZATION"


def test_audit_has_no_noop_normalization_events(written) -> None:
    normalizations = [event for event in written.audit_events if event.code.startswith("NORMALIZE_")]
    assert normalizations
    for event in normalizations:
        assert event.original_value != event.cleaned_value, f"no-op normalization logged: {event}"


def test_audit_group_events_have_empty_field_name(written) -> None:
    group = [event for event in written.audit_events if event.code in GROUP_EVENT_CODES]
    assert group
    assert all(event.field_name == "" for event in group)


def test_audit_duplicate_evidence_names_the_survivor(written) -> None:
    dedup = {event.ref.as_tuple(): event for event in written.audit_events if event.code == "DUPLICATE_EXACT"}
    assert set(dedup) == {("sales_2026_07.csv", "", 42), ("sales_2026_07.csv", "", 45)}
    assert dedup[("sales_2026_07.csv", "", 42)].related_source_row == 5
    assert dedup[("sales_2026_07.csv", "", 45)].related_source_row == 31
    assert all(event.field_name == "" for event in dedup.values())
    assert all(event.action_type == "DEDUPLICATION" for event in dedup.values())


def test_audit_reference_and_fuzzy_evidence_is_present(written) -> None:
    reference = [
        event
        for event in written.audit_events
        if event.code in {"REFERENCE_DUPLICATE_EXACT", "REFERENCE_KEY_CONFLICT"}
    ]
    assert [event.code for event in reference] == [
        "REFERENCE_DUPLICATE_EXACT",
        "REFERENCE_DUPLICATE_EXACT",
    ]
    assert all(event.field_name == "" for event in reference)
    assert {event.related_source_row for event in reference} == {11}

    fuzzy = [event for event in written.audit_events if event.code == "FUZZY_CUSTOMER_CANDIDATE"]
    assert len(fuzzy) == 1
    assert fuzzy[0].record_type == "customers"
    assert fuzzy[0].ref.source_row == 21
    assert fuzzy[0].related_source_row == 22
    assert fuzzy[0].action_type == "FLAG"

    reference_failures = {event.code for event in written.audit_events if event.action_type == "REFERENCE_FAILURE"}
    assert reference_failures == {"UNKNOWN_CUSTOMER_REFERENCE", "UNKNOWN_PRODUCT_REFERENCE"}


def test_audit_orders_by_source_then_stage_then_priority(written) -> None:
    keys = [
        (event.ref.sort_key, event.event_index) for event in written.audit_events
    ]
    source_keys = [key[0] for key in keys]
    assert source_keys == sorted(source_keys), "audit rows must follow source order first"

    # Within one row, normalization evidence precedes its terminal evidence, and
    # ties break by appendix section 7 catalogue priority then schema field order.
    by_ref: dict[tuple, list[str]] = {}
    for event in written.audit_events:
        by_ref.setdefault(event.ref.as_tuple(), []).append(event.code)
    deduplicated_row = by_ref[("sales_2026_07.csv", "", 45)]
    assert deduplicated_row == [
        "NORMALIZE_IDENTIFIER",
        "NORMALIZE_IDENTIFIER",
        "NORMALIZE_IDENTIFIER",
        "NORMALIZE_COUNTRY",
        "NORMALIZE_CURRENCY",
        "NORMALIZE_DATE",
        "DUPLICATE_EXACT",
    ]
    assert list(dict.fromkeys(deduplicated_row)) == [
        "NORMALIZE_IDENTIFIER",
        "NORMALIZE_COUNTRY",
        "NORMALIZE_CURRENCY",
        "NORMALIZE_DATE",
        "DUPLICATE_EXACT",
    ]
    # Catalogue priority, not the fixture's field-ordered code list.
    ranks = [FROZEN_CODE_ORDER.index(code) for code in deduplicated_row[:-1]]
    assert ranks == sorted(ranks)
    assert deduplicated_row[-1] == "DUPLICATE_EXACT"


def test_audit_quarantine_evidence_includes_every_failure(written) -> None:
    events = [
        event
        for event in written.audit_events
        if event.source_file == "sales_2026_10.xlsx" and event.source_row == 3
    ]
    # DF-003 material normalization first, then every terminal failure in frozen order.
    assert [event.code for event in events] == [
        "NORMALIZE_DATE",
        "MISSING_CUSTOMER_ID",
        "FUTURE_ORDER_DATE",
        "INVALID_QUANTITY",
    ]
    assert (events[0].original_value, events[0].cleaned_value) == ("09/10/2026", "2026-10-09")
    assert all(event.action_type == "VALIDATION_FAILURE" for event in events[1:])


def test_audit_events_all_trace_to_upstream_evidence(corpus, written) -> None:
    _, normalization, validation = corpus
    upstream = (
        {(event.source_file, event.source_sheet, event.source_row, event.code) for event in normalization.events}
        | {(issue.source_file, issue.source_sheet, issue.source_row, issue.code) for issue in normalization.issues if issue.action_type == "FLAG"}
        | {(event.source_file, event.source_sheet, event.source_row, event.code) for event in validation.validation_events}
    )
    for event in written.audit_events:
        assert (event.source_file, event.source_sheet, event.source_row, event.code) in upstream


# --------------------------------------------------------------------------- #
# cleaning_summary.json (appendix 8.3)
# --------------------------------------------------------------------------- #


def test_summary_top_level_contract_keys(summary) -> None:
    assert set(summary) == FROZEN_SUMMARY_KEYS
    assert summary["specification_version"] == "1.0.1"
    assert set(summary["run_metadata"]) == FROZEN_RUN_METADATA_KEYS


def test_summary_run_metadata_is_frozen(summary) -> None:
    assert summary["run_metadata"] == {
        "pipeline_version": "1.0.0",
        "demo_seed": 1007,
        "demo_date": "2026-10-07",
        "timezone": "Europe/Paris",
        "source_ordering": "nfc_relative_path_then_sheet_then_row",
    }


def test_summary_reference_counts(summary) -> None:
    assert summary["reference_counts"] == {
        "customers": {"input": 26, "valid": 24, "rejected": 1, "deduplicated": 1},
        "products": {"input": 15, "valid": 12, "rejected": 2, "deduplicated": 1},
    }
    for counts in summary["reference_counts"].values():
        assert counts["input"] == counts["valid"] + counts["rejected"] + counts["deduplicated"]


def test_summary_counts_match_the_audit_log(written, summary) -> None:
    normalization_counts: dict[str, int] = {}
    rule_counts: dict[str, int] = {}
    for event in written.audit_events:
        target = normalization_counts if event.code.startswith("NORMALIZE_") else rule_counts
        target[event.code] = target.get(event.code, 0) + 1
    assert summary["normalization_counts"] == dict(sorted(normalization_counts.items()))
    assert summary["rule_counts"] == dict(sorted(rule_counts.items()))
    assert sum(summary["normalization_counts"].values()) == 33
    assert summary["normalization_counts"] == {
        "NORMALIZE_CATEGORY": 4,
        "NORMALIZE_COUNTRY": 7,
        "NORMALIZE_CURRENCY": 6,
        "NORMALIZE_DATE": 5,
        "NORMALIZE_EMAIL": 1,
        "NORMALIZE_IDENTIFIER": 9,
        "NORMALIZE_WHITESPACE": 1,
    }
    assert summary["rule_counts"]["DUPLICATE_EXACT"] == 2
    assert summary["rule_counts"]["DUPLICATE_KEY_CONFLICT"] == 2
    assert summary["rule_counts"]["FUZZY_CUSTOMER_CANDIDATE"] == 1
    assert summary["rule_counts"]["MISSING_CUSTOMER_ID"] == 2


def test_summary_input_files_are_deterministic_and_digested(corpus, summary) -> None:
    ingestion, _, _ = corpus
    expected = build_input_file_entries(ingestion, DEMO_RAW)
    assert summary["input_files"] == expected
    assert [entry["path"] for entry in summary["input_files"]] == [
        "customers.xlsx",
        "products.csv",
        "sales_2026_07.csv",
        "sales_2026_08.xlsx",
        "sales_2026_09.csv",
        "sales_2026_10.xlsx",
    ]
    assert [entry["row_count"] for entry in summary["input_files"]] == [26, 15, 44, 40, 40, 40]
    for entry in summary["input_files"]:
        actual = hashlib.sha256((DEMO_RAW / entry["path"]).read_bytes()).hexdigest()
        assert entry["sha256"] == actual
    by_path = {entry["path"]: entry for entry in summary["input_files"]}
    assert by_path["customers.xlsx"]["selected_sheets"] == ["customers"]
    assert by_path["products.csv"]["selected_sheets"] == []
    assert by_path["sales_2026_07.csv"]["selected_sheets"] == []
    assert by_path["sales_2026_08.xlsx"]["selected_sheets"] == ["sales"]
    assert by_path["sales_2026_10.xlsx"]["selected_sheets"] == ["sales"]


def test_summary_outputs_is_the_frozen_v1_list(summary) -> None:
    assert summary["outputs"] == list(V1_OUTPUT_NAMES)
    assert len(summary["outputs"]) == 6
    assert "cleaned_sales.xlsx" in summary["outputs"]


def test_summary_contains_no_volatile_metadata(summary) -> None:
    payload = json.dumps(summary, ensure_ascii=False)
    assert not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", payload)
    for entry in summary["input_files"]:
        assert not entry["path"].startswith("/")
        assert "\\" not in entry["path"]

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from walk(value, f"{path}[{index}]")
        else:
            yield path, node

    banned = ("timestamp", "generated", "created", "hostname", "username", "run_id", "machine")
    for path, value in walk(summary):
        lowered = path.lower()
        assert not any(token in lowered for token in banned), path
        if isinstance(value, str):
            assert not value.startswith("/"), path


def test_summary_json_serialization_is_deterministic(written, summary) -> None:
    text = written.cleaning_summary.read_text(encoding="utf-8")
    assert text == json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert written.cleaning_summary.read_bytes() == text.encode("utf-8")


# --------------------------------------------------------------------------- #
# data_quality_report.html (appendix 8.6)
# --------------------------------------------------------------------------- #


def test_report_is_created_and_is_self_contained_html(report_html) -> None:
    assert report_html.startswith("<!DOCTYPE html>")
    assert report_html.rstrip().endswith("</html>")
    assert '<meta charset="utf-8">' in report_html
    for token in ("http://", "https://", "<script", "@import", "src="):
        assert token not in report_html, f"report must not depend on {token!r}"


def test_report_sections_follow_the_frozen_order(report_html) -> None:
    parser = _HeadingParser()
    parser.feed(report_html)
    assert parser.headings == [
        "DataForge data quality report",
        "1. Processed inputs",
        "2. Transaction dispositions",
        "3. Reconciliation",
        "4. Defect and normalization counts",
        "5. Reference data quality",
        "6. Safeguards",
        "7. Output artifacts",
        "8. Limitations and non-goals",
    ]


def test_report_shows_headline_metrics_and_reconciliation(report_html) -> None:
    assert "164 input rows = 146 accepted + 16 quarantined + 2 deduplicated" in report_html
    assert "Reconciliation: PASS" in report_html
    assert "Specification version 1.0.1" in report_html
    assert "2026-10-07" in report_html and "Europe/Paris" in report_html
    assert "23</strong> data rows" in report_html
    assert "16</strong> quarantined transactions" in report_html
    assert "2</strong> deduplicated transactions" in report_html
    assert "3</strong> rejected customer/product" in report_html
    assert "2</strong> deduplicated references" in report_html


def test_report_shows_defect_and_reference_counts(report_html) -> None:
    for code in ("DUPLICATE_EXACT", "DUPLICATE_KEY_CONFLICT", "INVALID_QUANTITY", "MISSING_CUSTOMER_ID"):
        assert code in report_html
    for code in ("NORMALIZE_IDENTIFIER", "NORMALIZE_CURRENCY", "NORMALIZE_DATE"):
        assert code in report_html
    assert "customers" in report_html and "products" in report_html
    assert "Near-duplicate customer name candidates flagged in evidence" in report_html


def test_report_explains_safeguards(report_html) -> None:
    for phrase in ("Provenance", "Quarantine", "Audit trail", "No silent deletion", "Deterministic"):
        assert phrase in report_html
    assert "never silently corrected" in report_html


def test_report_marks_cleaned_sales_as_df006_only(report_html) -> None:
    assert "DF-006 final cleaned-data" in report_html
    assert "written only by DF-006" in report_html
    assert "They do not exist yet" in report_html


def test_report_has_no_volatile_timestamp(report_html) -> None:
    """The report must not embed a clock reading.

    The demo clock (2026-10-07) intentionally coincides with this machine's date,
    so the proof is structural: no time-of-day or full timestamp may appear, and
    the reporting modules may not consult the machine clock at all.
    """

    assert not re.search(r"\d{2}:\d{2}", report_html)
    assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}", report_html)
    assert "frozen demo date 2026-10-07" in report_html

    for module in (audit_module, report_module):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert ".now(" not in source, f"{module.__name__} consults the machine clock"
        assert ".today(" not in source, f"{module.__name__} consults the machine clock"
        assert "time.time" not in source


def test_report_escapes_data_derived_text(tmp_path: Path) -> None:
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    hostile_name = "<script>alert(1).csv"
    (hostile / hostile_name).write_text(
        "order_id,customer_id,product_id,order_date,quantity,unit_price_eur,country\n"
        "ORD-1,C001,P001,2026-07-01,1,49.90,FR\n",
        encoding="utf-8",
    )
    (hostile / "products.csv").write_text(
        "product_id,product_name,category,unit_price_eur\nP001,Laptop Stand,ELECTRONICS,49.90\n",
        encoding="utf-8",
    )
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "customers"
    sheet.append(["customer_id", "customer_name", "email", "country"])
    sheet.append(["C001", "Aline Martin", "aline.martin@example.test", "FR"])
    workbook.save(hostile / "customers.xlsx")
    workbook.close()

    ingestion = ingest_directory(hostile)
    normalization = normalize(ingestion)
    outputs = write_evidence_outputs(
        normalization, validate(normalization), tmp_path / "out",
        ingestion=ingestion, input_dir=hostile,
    )
    text = outputs.data_quality_report.read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;alert(1).csv" in text
    assert html.escape(hostile_name) in text


# --------------------------------------------------------------------------- #
# Writer boundary (scope, safety, determinism)
# --------------------------------------------------------------------------- #


def test_writer_creates_only_the_four_authorized_artifacts(written) -> None:
    produced = sorted(path.name for path in written.directory.iterdir())
    assert produced == sorted(DF005_ARTIFACT_NAMES)
    assert {path.name for path in written.paths()} == set(DF005_ARTIFACT_NAMES)
    assert not (written.directory / "cleaned_sales.csv").exists()
    assert not (written.directory / "cleaned_sales.xlsx").exists()
    assert sorted(produced) == sorted(V1_OUTPUT_NAMES[2:])


def test_writer_leaves_unrelated_and_foreign_files_untouched(corpus, tmp_path: Path) -> None:
    ingestion, normalization, validation = corpus
    directory = tmp_path / "safe"
    directory.mkdir()
    decoy = directory / "cleaned_sales.csv"
    decoy.write_text("do not touch\n", encoding="utf-8")
    unrelated = directory / "notes.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")

    write_evidence_outputs(normalization, validation, directory, ingestion=ingestion, input_dir=DEMO_RAW)

    assert decoy.read_text(encoding="utf-8") == "do not touch\n"
    assert unrelated.read_text(encoding="utf-8") == "keep me\n"


def test_writer_does_not_modify_the_input_corpus_or_expected_fixtures(corpus, tmp_path: Path) -> None:
    ingestion, normalization, validation = corpus
    raw_before = _tree_digest(DEMO_RAW)
    expected_before = _tree_digest(DEMO_EXPECTED)
    write_evidence_outputs(normalization, validation, tmp_path / "out", ingestion=ingestion, input_dir=DEMO_RAW)
    assert _tree_digest(DEMO_RAW) == raw_before
    assert _tree_digest(DEMO_EXPECTED) == expected_before


def test_reporting_modules_expose_no_rule_engine(corpus, written) -> None:
    """Reporting must not reach for parsers, resolvers, or rule evaluators."""

    for module in (audit_module, report_module):
        for name in FORBIDDEN_REPORTING_NAMES:
            assert not hasattr(module, name), f"{module.__name__} exposes {name}"

    # Dispositions in evidence are exactly the ones DF-004 already decided.
    _, _, validation = corpus
    decided = {outcome.disposition for outcome in (*validation.quarantined, *validation.deduplicated)}
    assert decided == {QUARANTINED, DEDUPLICATED}
    for row in written.rejected_rows_data:
        if row.record_type == "sales":
            assert row.terminal_disposition in decided


def test_all_four_artifacts_are_byte_identical_across_runs(corpus, tmp_path: Path) -> None:
    ingestion, normalization, validation = corpus
    first = write_evidence_outputs(normalization, validation, tmp_path / "run1", ingestion=ingestion, input_dir=DEMO_RAW)
    second = write_evidence_outputs(normalization, validation, tmp_path / "run2", ingestion=ingestion, input_dir=DEMO_RAW)

    for left, right in zip(first.paths(), second.paths(), strict=True):
        assert left.name == right.name
        assert left.read_bytes() == right.read_bytes(), f"{left.name} is not deterministic"

    assert first.summary == second.summary
    assert first.audit_events == second.audit_events
    assert first.rejected_rows_data == second.rejected_rows_data


def test_all_four_artifacts_reopen_and_interpret(written) -> None:
    with written.rejected_rows.open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        rows = list(reader)
    assert header == list(REJECTED_COLUMNS)
    assert len(rows) == 23
    assert all(len(row) == len(REJECTED_COLUMNS) for row in rows)

    with written.audit_log.open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        audit_header = next(reader)
        audit = list(reader)
    assert audit_header == list(AUDIT_COLUMNS)
    assert audit and all(len(row) == len(AUDIT_COLUMNS) for row in audit)

    summary = json.loads(written.cleaning_summary.read_text(encoding="utf-8"))
    assert summary["transaction_counts"]["reconciled"] is True
    assert json.loads(json.dumps(summary)) == summary

    parser = _HeadingParser()
    parser.feed(written.data_quality_report.read_text(encoding="utf-8"))
    assert len(parser.headings) == 9


def test_serialize_value_contract() -> None:
    from datetime import date, datetime
    from decimal import Decimal

    assert serialize_value(None) == ""
    assert serialize_value(0) == "0"
    assert serialize_value(-2) == "-2"
    assert serialize_value(Decimal("1299.00")) == "1299.00"
    assert serialize_value(date(2026, 10, 7)) == "2026-10-07"
    assert serialize_value(datetime(2026, 10, 7, 0, 0)) == "2026-10-07"
    assert serialize_value(datetime(2026, 10, 7, 13, 45, 1)) == "2026-10-07T13:45:01"
    assert serialize_value(" Ord-0002 ") == " Ord-0002 "
