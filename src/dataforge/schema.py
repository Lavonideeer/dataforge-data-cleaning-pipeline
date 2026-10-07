"""Bounded DataForge schemas and deterministic header mapping.

DF-002 normalizes table structure only. Business cell values are deliberately
left untouched for DF-003.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import Literal, Mapping, Sequence


RecordType = Literal["sales", "customers", "products"]
DiagnosticSeverity = Literal["WARNING", "ERROR"]
RawValue = str | int | float | bool | date | datetime | time | timedelta | None

PROVENANCE_FIELDS = ("_source_file", "_source_sheet", "_source_row")


@dataclass(frozen=True)
class TableSchema:
    """The exhaustive header contract for one bounded input role."""

    record_type: RecordType
    fields: tuple[str, ...]
    required_fields: frozenset[str]
    aliases: Mapping[str, str]


@dataclass(frozen=True)
class SchemaDiagnostic:
    """Stable machine-testable evidence for one structural condition."""

    code: str
    severity: DiagnosticSeverity
    record_type: RecordType | None
    source_file: str
    source_sheet: str = ""
    raw_header: str = ""
    canonical_field: str = ""
    message: str = ""
    action_type: str = "SCHEMA"

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "action_type": self.action_type,
            "severity": self.severity,
            "record_type": self.record_type,
            "source_file": self.source_file,
            "source_sheet": self.source_sheet,
            "raw_header": self.raw_header,
            "canonical_field": self.canonical_field,
            "message": self.message,
        }


@dataclass(frozen=True)
class HeaderMapping:
    """Result of mapping raw headers without touching row values."""

    canonical_by_index: tuple[str | None, ...]
    raw_headers: tuple[str, ...]
    aliases_applied: int
    diagnostics: tuple[SchemaDiagnostic, ...]

    @property
    def has_errors(self) -> bool:
        return any(item.severity == "ERROR" for item in self.diagnostics)


def _alias_map(values: Mapping[str, Sequence[str]]) -> Mapping[str, str]:
    aliases: dict[str, str] = {}
    for canonical, raw_headers in values.items():
        for raw_header in raw_headers:
            if raw_header in aliases:
                raise ValueError(f"duplicate schema alias: {raw_header}")
            aliases[raw_header] = canonical
    return MappingProxyType(aliases)


SALES_SCHEMA = TableSchema(
    record_type="sales",
    fields=(
        "order_id",
        "customer_id",
        "product_id",
        "order_date",
        "quantity",
        "unit_price_eur",
        "country",
    ),
    required_fields=frozenset(
        {
            "order_id",
            "customer_id",
            "product_id",
            "order_date",
            "quantity",
            "unit_price_eur",
            "country",
        }
    ),
    aliases=_alias_map(
        {
            "order_id": ("order_id", "Order ID", "OrderID", "order id"),
            "customer_id": ("customer_id", "Customer ID", "CustomerID", "customer id"),
            "product_id": ("product_id", "Product ID", "ProductID", "product id"),
            "order_date": ("order_date", "Order Date", "OrderDate", "order date", "Date"),
            "quantity": ("quantity", "Quantity", "Qty", "qty"),
            "unit_price_eur": (
                "unit_price_eur",
                "Unit Price",
                "UnitPrice",
                "unit price",
                "Price EUR",
                "price",
            ),
            "country": ("country", "Country", "Country Code", "country code"),
        }
    ),
)

CUSTOMER_SCHEMA = TableSchema(
    record_type="customers",
    fields=("customer_id", "customer_name", "email", "country"),
    required_fields=frozenset({"customer_id", "customer_name", "email", "country"}),
    aliases=_alias_map(
        {
            "customer_id": ("customer_id", "Customer ID", "CustomerID", "customer id"),
            "customer_name": (
                "customer_name",
                "Customer Name",
                "CustomerName",
                "customer name",
                "Name",
            ),
            "email": ("email", "Email", "Email Address", "email address"),
            "country": ("country", "Country", "Country Code", "country code"),
        }
    ),
)

PRODUCT_SCHEMA = TableSchema(
    record_type="products",
    fields=("product_id", "product_name", "category", "unit_price_eur"),
    required_fields=frozenset({"product_id", "product_name", "category", "unit_price_eur"}),
    aliases=_alias_map(
        {
            "product_id": ("product_id", "Product ID", "ProductID", "product id", "SKU", "sku"),
            "product_name": ("product_name", "Product Name", "ProductName", "product name"),
            "category": ("category", "Category", "Product Category", "product category"),
            "unit_price_eur": (
                "unit_price_eur",
                "Unit Price",
                "UnitPrice",
                "unit price",
                "Price EUR",
                "price",
            ),
        }
    ),
)

SCHEMAS: Mapping[RecordType, TableSchema] = MappingProxyType(
    {
        "sales": SALES_SCHEMA,
        "customers": CUSTOMER_SCHEMA,
        "products": PRODUCT_SCHEMA,
    }
)


def normalized_header(raw_header: object) -> str:
    """Apply only the appendix-authorized BOM removal and outer trim."""

    text = "" if raw_header is None else str(raw_header)
    return text.removeprefix("\ufeff").strip()


def map_headers(
    raw_headers: Sequence[object],
    schema: TableSchema,
    *,
    source_file: str,
    source_sheet: str = "",
) -> HeaderMapping:
    """Map exhaustive aliases and return all deterministic schema evidence."""

    display_headers = tuple("" if item is None else str(item) for item in raw_headers)
    mapped: list[str | None] = []
    positions: dict[str, list[str]] = {}
    diagnostics: list[SchemaDiagnostic] = []
    aliases_applied = 0

    for raw_display, raw_value in zip(display_headers, raw_headers, strict=True):
        candidate = normalized_header(raw_value)
        canonical = schema.aliases.get(candidate)
        mapped.append(canonical)
        if canonical is None:
            diagnostics.append(
                SchemaDiagnostic(
                    code="UNEXPECTED_COLUMN",
                    severity="WARNING",
                    record_type=schema.record_type,
                    source_file=source_file,
                    source_sheet=source_sheet,
                    raw_header=raw_display,
                    message=f"Unexpected column {raw_display!r} ignored for {schema.record_type}.",
                )
            )
            continue
        positions.setdefault(canonical, []).append(raw_display)
        if candidate != canonical:
            aliases_applied += 1

    for canonical in schema.fields:
        raw_matches = positions.get(canonical, [])
        if len(raw_matches) > 1:
            diagnostics.append(
                SchemaDiagnostic(
                    code="ALIAS_COLLISION",
                    severity="ERROR",
                    record_type=schema.record_type,
                    source_file=source_file,
                    source_sheet=source_sheet,
                    raw_header="|".join(raw_matches),
                    canonical_field=canonical,
                    message=f"Multiple headers map to required field {canonical!r}: {raw_matches!r}.",
                )
            )
        elif not raw_matches and canonical in schema.required_fields:
            diagnostics.append(
                SchemaDiagnostic(
                    code="MISSING_REQUIRED_COLUMN",
                    severity="ERROR",
                    record_type=schema.record_type,
                    source_file=source_file,
                    source_sheet=source_sheet,
                    canonical_field=canonical,
                    message=f"Required column {canonical!r} is missing for {schema.record_type}.",
                )
            )

    return HeaderMapping(
        canonical_by_index=tuple(mapped),
        raw_headers=display_headers,
        aliases_applied=aliases_applied,
        diagnostics=tuple(diagnostics),
    )
