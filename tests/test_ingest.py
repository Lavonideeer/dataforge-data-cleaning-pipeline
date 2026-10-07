"""DF-002 ingestion and structural-schema contract tests."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from openpyxl import Workbook

from dataforge.ingest import (
    SchemaIngestionError,
    classify_input,
    discover_input_files,
    ingest_directory,
)
from dataforge.schema import SCHEMAS, SALES_SCHEMA, map_headers


ROOT = Path(__file__).resolve().parents[1]
DEMO_RAW = ROOT / "data" / "demo_raw"
SALES_HEADERS = [
    "order_id",
    "customer_id",
    "product_id",
    "order_date",
    "quantity",
    "unit_price_eur",
    "country",
]
DOCUMENTED_ALIASES = {
    "sales": {
        "order_id": ("order_id", "Order ID", "OrderID", "order id"),
        "customer_id": ("customer_id", "Customer ID", "CustomerID", "customer id"),
        "product_id": ("product_id", "Product ID", "ProductID", "product id"),
        "order_date": ("order_date", "Order Date", "OrderDate", "order date", "Date"),
        "quantity": ("quantity", "Quantity", "Qty", "qty"),
        "unit_price_eur": ("unit_price_eur", "Unit Price", "UnitPrice", "unit price", "Price EUR", "price"),
        "country": ("country", "Country", "Country Code", "country code"),
    },
    "customers": {
        "customer_id": ("customer_id", "Customer ID", "CustomerID", "customer id"),
        "customer_name": ("customer_name", "Customer Name", "CustomerName", "customer name", "Name"),
        "email": ("email", "Email", "Email Address", "email address"),
        "country": ("country", "Country", "Country Code", "country code"),
    },
    "products": {
        "product_id": ("product_id", "Product ID", "ProductID", "product id", "SKU", "sku"),
        "product_name": ("product_name", "Product Name", "ProductName", "product name"),
        "category": ("category", "Category", "Product Category", "product category"),
        "unit_price_eur": ("unit_price_eur", "Unit Price", "UnitPrice", "unit price", "Price EUR", "price"),
    },
}


def _write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(headers)
        writer.writerows(rows)


def _write_xlsx(
    path: Path,
    sheet_name: str,
    headers: list[str],
    rows: list[list[object]],
    *,
    extra_sheet: str | None = None,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    if extra_sheet:
        workbook.create_sheet(extra_sheet).append(["must not be ingested"])
    workbook.save(path)


def test_discovery_is_deterministic_supported_and_bounded(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    for filename in ("z.xlsx", "a.csv", "middle.CSV", "ignored.txt"):
        (input_root / filename).touch()
    nested = input_root / "nested"
    nested.mkdir()
    (nested / "outside_scan.csv").touch()
    external = tmp_path / "external.csv"
    external.touch()
    (input_root / "linked.csv").symlink_to(external)

    first = discover_input_files(input_root)
    second = discover_input_files(input_root)

    assert [item.source_file for item in first] == ["a.csv", "middle.CSV", "z.xlsx"]
    assert first == second
    assert all(item.path.parent == input_root for item in first)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("customers.xlsx", ("customers", "customers")),
        ("products.csv", ("products", "")),
        ("sales_2026_07.csv", ("sales", "")),
        ("sales_2026_08.xlsx", ("sales", "sales")),
    ],
)
def test_bounded_role_classification(filename: str, expected: tuple[str, str]) -> None:
    assert classify_input(filename) == expected


def test_role_classification_does_not_guess_unsupported_inputs() -> None:
    with pytest.raises(ValueError, match="Unsupported input type"):
        classify_input("sales.txt")


@pytest.mark.parametrize(
    ("record_type", "canonical", "raw_header"),
    [
        (record_type, canonical, raw_header)
        for record_type, canonical_groups in DOCUMENTED_ALIASES.items()
        for canonical, raw_headers in canonical_groups.items()
        for raw_header in raw_headers
    ],
)
def test_every_documented_alias_maps_exactly(
    record_type: str,
    canonical: str,
    raw_header: str,
) -> None:
    mapping = map_headers([raw_header], SCHEMAS[record_type], source_file="fixture.csv")
    assert mapping.canonical_by_index == (canonical,)


def test_alias_catalogue_is_exhaustive_with_no_extra_guesses() -> None:
    for record_type, canonical_groups in DOCUMENTED_ALIASES.items():
        expected = {
            raw_header: canonical
            for canonical, raw_headers in canonical_groups.items()
            for raw_header in raw_headers
        }
        assert dict(SCHEMAS[record_type].aliases) == expected


def test_header_matching_removes_only_bom_and_outer_whitespace() -> None:
    mapping = map_headers(
        ["\ufeff Order ID ", "Customer ID", "Product ID", "Order Date", "Quantity", "Unit Price", "Country"],
        SALES_SCHEMA,
        source_file="fixture.csv",
    )
    assert not mapping.has_errors
    assert mapping.canonical_by_index == tuple(SALES_HEADERS)


def test_actual_df001_corpus_integrates_with_frozen_counts() -> None:
    result = ingest_directory(DEMO_RAW)

    assert result.discovered_files == (
        "customers.xlsx",
        "products.csv",
        "sales_2026_07.csv",
        "sales_2026_08.xlsx",
        "sales_2026_09.csv",
        "sales_2026_10.xlsx",
    )
    assert result.summary() == {
        "files_discovered": 6,
        "files_classified": 6,
        "sheets_inspected": 4,
        "sales_rows": 164,
        "customer_rows": 26,
        "product_rows": 15,
        "aliases_applied": 28,
        "unexpected_columns": 6,
        "unexpected_sheets": 1,
        "unexpected_inputs": 1,
        "structural_errors": 0,
    }


def test_xlsx_selected_sheets_and_unexpected_sheet_policy() -> None:
    result = ingest_directory(DEMO_RAW)
    unexpected = [item for item in result.diagnostics if item.code == "UNEXPECTED_SHEET"]

    assert [(item.source_file, item.source_sheet, item.severity) for item in unexpected] == [
        ("sales_2026_08.xlsx", "notes", "WARNING")
    ]
    assert all(row["_source_sheet"] == "sales" for row in result.rows_for("sales") if row["_source_file"].endswith(".xlsx"))
    assert not any(row["_source_sheet"] == "notes" for row in result.rows_for("sales"))


def test_provenance_and_source_order_are_stable_across_runs() -> None:
    first = ingest_directory(DEMO_RAW)
    second = ingest_directory(DEMO_RAW)
    first_rows = first.rows_for("sales")

    assert first_rows == second.rows_for("sales")
    assert (first_rows[0]["_source_file"], first_rows[0]["_source_sheet"], first_rows[0]["_source_row"]) == (
        "sales_2026_07.csv",
        "",
        2,
    )
    identities = [
        (row["_source_file"], row["_source_sheet"], row["_source_row"])
        for row in first_rows
    ]
    assert identities == sorted(
        identities,
        key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8"), item[2]),
    )
    assert len(set(identities)) == 164


def test_csv_business_values_and_row_order_remain_raw(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_csv(
        input_root / "sales_values.csv",
        SALES_HEADERS,
        [
            [" ord-0001 ", "c001", "p001", "04/08/2026", "2", "1 299,00 EUR", " France "],
            ["ORD-0002", "C002", "P002", "2026-08-05", "3", "EUR1,299.00", "france"],
            ["ORD-0003", "C003", "P003", "2026-08-06", "4", "1299", "FR"],
            ["ORD-0004", "C004", "P004", "2026-08-07", "5", "1299.00", "FRA"],
        ],
    )

    rows = ingest_directory(input_root).rows_for("sales")

    assert [row["order_id"] for row in rows] == [
        " ord-0001 ",
        "ORD-0002",
        "ORD-0003",
        "ORD-0004",
    ]
    assert rows[0]["order_date"] == "04/08/2026"
    assert rows[0]["unit_price_eur"] == "1 299,00 EUR"
    assert rows[0]["country"] == " France "
    assert rows[1]["unit_price_eur"] == "EUR1,299.00"
    assert [row["country"] for row in rows] == [" France ", "france", "FR", "FRA"]


def test_xlsx_cell_types_and_values_are_not_normalized(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_xlsx(
        input_root / "sales_values.xlsx",
        "sales",
        ["OrderID", "CustomerID", "ProductID", "Date", "Qty", "Price EUR", "Country Code"],
        [["ORD-1", "C001", "P001", "04/08/2026", 2, "1 299,00 EUR", "france"]],
        extra_sheet="notes",
    )

    result = ingest_directory(input_root)
    row = result.rows_for("sales")[0]

    assert row["quantity"] == 2
    assert row["order_date"] == "04/08/2026"
    assert row["unit_price_eur"] == "1 299,00 EUR"
    assert row["country"] == "france"
    assert [(item.code, item.source_sheet) for item in result.diagnostics] == [
        ("UNEXPECTED_SHEET", "notes")
    ]


def test_unexpected_columns_and_unsupported_inputs_are_reported(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_csv(
        input_root / "sales.csv",
        SALES_HEADERS + ["mystery"],
        [["ORD-1", "C1", "P1", "2026-01-01", "1", "10", "FR", "raw evidence"]],
    )
    (input_root / "readme.txt").write_text("ignored", encoding="utf-8")

    result = ingest_directory(input_root)
    diagnostics = [(item.code, item.source_file, item.raw_header) for item in result.diagnostics]

    assert diagnostics == [
        ("UNEXPECTED_INPUT", "readme.txt", ""),
        ("UNEXPECTED_COLUMN", "sales.csv", "mystery"),
    ]
    assert "mystery" not in result.rows_for("sales")[0]


def test_missing_required_column_is_an_atomic_schema_failure(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_csv(
        input_root / "sales.csv",
        [header for header in SALES_HEADERS if header != "quantity"],
        [["ORD-1", "C1", "P1", "2026-01-01", "10", "FR"]],
    )

    with pytest.raises(SchemaIngestionError) as caught:
        ingest_directory(input_root)

    assert not hasattr(caught.value, "tables")
    errors = [item for item in caught.value.diagnostics if item.severity == "ERROR"]
    assert [(item.code, item.canonical_field) for item in errors] == [
        ("MISSING_REQUIRED_COLUMN", "quantity")
    ]


def test_alias_collision_fails_without_choosing_a_column(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    headers = ["Order ID", "order_id"] + SALES_HEADERS[1:]
    _write_csv(
        input_root / "sales.csv",
        headers,
        [["ORD-1", "ORD-OTHER", "C1", "P1", "2026-01-01", "1", "10", "FR"]],
    )

    with pytest.raises(SchemaIngestionError) as caught:
        ingest_directory(input_root)

    collisions = [item for item in caught.value.diagnostics if item.code == "ALIAS_COLLISION"]
    assert len(collisions) == 1
    assert collisions[0].canonical_field == "order_id"
    assert collisions[0].raw_header == "Order ID|order_id"


def test_nullable_customer_cells_and_absent_non_schema_column_do_not_fail(tmp_path: Path) -> None:
    input_root = tmp_path / "input"
    input_root.mkdir()
    _write_xlsx(
        input_root / "customers.xlsx",
        "customers",
        ["Customer ID", "Customer Name", "Email", "Country"],
        [["C001", None, None, None]],
    )

    result = ingest_directory(input_root)
    row = result.rows_for("customers")[0]

    assert row["customer_id"] == "C001"
    assert row["customer_name"] is None
    assert row["email"] is None
    assert row["country"] is None
    assert not result.has_errors
