# DataForge v1.0.1 normative appendix - proposal

**Status: PROPOSED FOR OWNER REVIEW - NOT ADOPTED**

**Task:** DF-000R

**Proposal date:** 2026-10-07

**Base authority:** `DataForge_Project_Workflow_v1.0.pdf`
**Base SHA-256:** `ea7f9af75c73d84a5f08a89e8d9eecc043f66d8f9fb9d4fa9c3251777c038dea`

This appendix proposes only the rules needed to resolve the five material gaps
found in DF-000. Until the Owner adopts it, the PDF remains the sole authoritative
v1.0 specification, DF-000 remains in rework, and DF-001 is not authorized.

Normative keywords (`MUST`, `MUST NOT`, `SHOULD`) in this proposal describe the
behavior that would become binding if the Owner adopts this appendix.

## Repair traceability

| DF-000 defect | Proposed resolution | Functional requirements | Acceptance gates |
| --- | --- | --- | --- |
| Missing canonical schemas | Sections 1-4 freeze tables, fields, types, keys, aliases, provenance, joins, and final order | F01, F02, F04-F06, F10 | G1-G4, G6, G7 |
| Incomplete normalization | Section 5 freezes bounded text, country, currency, date, email, category, and fuzzy rules | F03, F04, F06, F08 | G1, G4-G6 |
| Missing demo clock | Section 5.4 fixes `2026-10-07`, `Europe/Paris`, and strict future-date behavior | F03, F06 | G1, G4, G6 |
| Undefined precedence/accounting | Sections 2 and 6 freeze order, survivor/conflict behavior, dispositions, failure priority, and G2 math | F05-F08, F10 | G2-G6 |
| Missing codes/evidence schemas | Sections 7-8 freeze codes and exact CSV/JSON/XLSX/report contracts | F07-F10 | G2, G3, G5-G7 |

## 1. Bounded input contract

The input root contains synthetic demo files only:

- exact basename `customers.xlsx` is the customer reference workbook; its selected
  worksheet is exactly `customers`;
- exact basename `products.csv` is the product reference file;
- every other `.csv` or `.xlsx` file is a monthly sales file; the selected sheet
  in each sales workbook is exactly `sales`;
- CSV files are comma-delimited UTF-8; an optional UTF-8 BOM is accepted;
- the first selected row is the header and data begins on row 2;
- other file extensions and unselected worksheets are ignored and reported with
  `UNEXPECTED_INPUT` or `UNEXPECTED_SHEET` evidence.

A selected table missing any required canonical header, or containing two headers
that map to the same canonical field, is a file-level schema error. The run MUST
fail without publishing partial client deliverables. Unexpected columns are
reported, ignored by transformations, and preserved in the immutable raw source.

"Required column" means that the header MUST exist. Field nullability is governed
separately by the schemas below.

## 2. Provenance and deterministic source order

Every source row receives these provenance fields:

| Field | Type | Contract |
| --- | --- | --- |
| `_source_file` | string | Unicode-NFC relative path from the input root, with `/` separators |
| `_source_sheet` | string | Selected worksheet name; empty string for CSV |
| `_source_row` | positive integer | One-based table row number, including the header as row 1; first data row is 2 |

Source order MUST NOT depend on filesystem enumeration. It is the ascending tuple:

1. UTF-8 byte ordering of `_source_file` after Unicode NFC and `/` normalization;
2. UTF-8 byte ordering of `_source_sheet` (empty CSV value sorts normally);
3. numeric `_source_row`.

Two paths or sheet names that collide after normalization are a schema error. The
same inputs therefore always select the same duplicate survivor.

## 3. Canonical schemas

### 3.1 Shared logical types

- `identifier`: Unicode-NFC string, surrounding whitespace removed, then
  uppercased. It MUST match `[A-Z0-9][A-Z0-9_-]{0,63}` after normalization.
  Integer-valued spreadsheet cells are converted to base-10 text without a
  decimal suffix. Other numeric cells are invalid identifiers.
- `text`: Unicode-NFC string with surrounding whitespace removed and every run of
  internal Unicode whitespace replaced by one ASCII space. Empty becomes null.
- `date`: calendar date serialized as `YYYY-MM-DD`.
- `integer`: exact base-10 integer; booleans and fractional values are invalid.
- `money_eur`: decimal EUR value serialized in CSV/JSON with exactly two decimal
  places. Parsing and validation MUST use decimal arithmetic, never binary float.

### 3.2 Sales schema

All seven business headers are required. Provenance fields are system-generated.

| Field | Type | Nullable / role | Accepted raw forms and normalization | Hard constraint | Final output |
| --- | --- | --- | --- | --- | --- |
| `order_id` | identifier | No; critical | String or integer cell to canonical identifier | Present and identifier-valid; transaction business key | Yes |
| `customer_id` | identifier | No; critical | String or integer cell to canonical identifier | Present, identifier-valid, and later found in valid customers | Yes |
| `product_id` | identifier | No; critical | String or integer cell to canonical identifier | Present, identifier-valid, and later found in valid products | Yes |
| `order_date` | date | No; critical | Formats in section 5.4 to ISO date | Valid date and not later than `2026-10-07` | Yes |
| `quantity` | integer | No; critical | Integer cell or signed integer text | `quantity > 0` | Yes |
| `unit_price_eur` | money_eur | No; critical | Formats in section 5.3 to fixed two-decimal EUR | `unit_price_eur >= 0.00` | Yes |
| `country` | string | Yes; optional | Section 5.2 | No hard failure; unknown non-null value is retained and flagged | Yes |
| `_source_file` | string | No; provenance | Section 2 | Generated for every row | Yes |
| `_source_sheet` | string | No; provenance | Section 2 | Generated for every row | Yes |
| `_source_row` | integer | No; provenance | Section 2 | Generated for every row | Yes |

The sales business key is `order_id`. The exact canonical final column order is:

```text
order_id,customer_id,product_id,order_date,quantity,unit_price_eur,country,_source_file,_source_sheet,_source_row
```

### 3.3 Customer reference schema

All four headers are required; optional fields may contain null cells.

| Field | Type | Nullable / role | Accepted raw forms and normalization | Validation | In cleaned sales |
| --- | --- | --- | --- | --- | --- |
| `customer_id` | identifier | No; critical | Shared identifier rule | Missing/invalid excludes reference row | Key only |
| `customer_name` | text | Yes; optional | Whitespace normalization; original casing preserved | No hard failure | No |
| `email` | string | Yes; optional | Section 5.5 | Malformed value is retained and flagged; row remains usable | No |
| `country` | string | Yes; optional | Section 5.2 | Unknown value retained and flagged | No |

The customer business/reference key is `customer_id`.

### 3.4 Product reference schema

All four headers are required; optional fields may contain null cells.

| Field | Type | Nullable / role | Accepted raw forms and normalization | Validation | In cleaned sales |
| --- | --- | --- | --- | --- | --- |
| `product_id` | identifier | No; critical | Shared identifier rule | Missing/invalid excludes reference row | Key only |
| `product_name` | text | Yes; optional | Whitespace normalization; original casing preserved | No hard failure | No |
| `category` | string | Yes; optional | Section 5.6 | Unknown value retained and flagged | No |
| `unit_price_eur` | money_eur | No; critical | Section 5.3 | Missing/unparseable/negative excludes reference row | No |

The product business/reference key is `product_id`.

Reference data validates foreign keys only. The transaction-level cleaned output
MUST NOT be enriched with customer or product descriptive fields, and sales price
MUST NOT be silently replaced by the product reference price.

## 4. Exhaustive demo alias catalogue

Matching occurs after removal of an optional UTF-8 BOM and surrounding header
whitespace. No other heuristic header inference is allowed. The following aliases
are exhaustive for generated v1 demo inputs.

### 4.1 Sales headers

| Canonical | Accepted source headers |
| --- | --- |
| `order_id` | `order_id`, `Order ID`, `OrderID`, `order id` |
| `customer_id` | `customer_id`, `Customer ID`, `CustomerID`, `customer id` |
| `product_id` | `product_id`, `Product ID`, `ProductID`, `product id` |
| `order_date` | `order_date`, `Order Date`, `OrderDate`, `order date`, `Date` |
| `quantity` | `quantity`, `Quantity`, `Qty`, `qty` |
| `unit_price_eur` | `unit_price_eur`, `Unit Price`, `UnitPrice`, `unit price`, `Price EUR`, `price` |
| `country` | `country`, `Country`, `Country Code`, `country code` |

### 4.2 Customer headers

| Canonical | Accepted source headers |
| --- | --- |
| `customer_id` | `customer_id`, `Customer ID`, `CustomerID`, `customer id` |
| `customer_name` | `customer_name`, `Customer Name`, `CustomerName`, `customer name`, `Name` |
| `email` | `email`, `Email`, `Email Address`, `email address` |
| `country` | `country`, `Country`, `Country Code`, `country code` |

### 4.3 Product headers

| Canonical | Accepted source headers |
| --- | --- |
| `product_id` | `product_id`, `Product ID`, `ProductID`, `product id`, `SKU`, `sku` |
| `product_name` | `product_name`, `Product Name`, `ProductName`, `product name` |
| `category` | `category`, `Category`, `Product Category`, `product category` |
| `unit_price_eur` | `unit_price_eur`, `Unit Price`, `UnitPrice`, `unit price`, `Price EUR`, `price` |

An unlisted header is unexpected, not an alias candidate. `UNEXPECTED_COLUMN`
evidence MUST name the table, source, and original header. Two accepted aliases
for one canonical field in the same table are an `ALIAS_COLLISION` fatal schema
error; the implementation MUST NOT guess which column wins.

## 5. Normalization contract

Normalization runs before validation. A material change emits one audit event;
applying a rule without changing the serialized value emits no event.

### 5.1 Text and identifiers

- Normalize Unicode to NFC.
- Convert null markers/empty strings to null before field-specific validation.
- For descriptive text, trim and collapse whitespace but preserve user-visible
  casing and punctuation.
- For identifiers, apply the shared identifier rule in section 3.1.
- Do not transliterate, remove accents, title-case names, or infer corrections.

Whitespace changes use `NORMALIZE_WHITESPACE`; identifier case changes use
`NORMALIZE_IDENTIFIER` (one event may describe both if both occurred).

### 5.2 Country

For matching only, apply text normalization and Unicode case-folding. This
exhaustive bounded alias map produces canonical `FR`:

| Match key | Canonical value |
| --- | --- |
| `france` | `FR` |
| `fr` | `FR` |
| `fra` | `FR` |

Thus `France`, `france`, `FR`, and `FRA` all become `FR`. A non-null unknown
country is retained after text whitespace normalization, is not guessed, and
emits `UNKNOWN_COUNTRY`. Null is allowed and does not emit the unknown flag.

### 5.3 EUR currency

Surrounding whitespace and one case-insensitive `EUR` prefix or suffix are
removed. Regular spaces and non-breaking spaces may be grouping separators.
The bounded demo accepts:

- ungrouped integers: `1299`;
- ungrouped dot decimals with one or two digits: `1299.0`, `1299.00`;
- ungrouped comma decimals with exactly two digits: `1299,00`;
- European grouped form: `1 299,00`, `1.299,00`;
- US grouped form: `1,299.00`;
- any form above with a separated or adjacent `EUR` prefix/suffix, including
  `EUR1,299.00` and `1 299,00 EUR`.

Grouping MUST be in three-digit groups. A single separator followed by exactly
three digits and no other separator (for example `1,299` or `1.299`) is ambiguous
and MUST fail rather than be guessed. Other currencies, currency symbols, malformed
grouping, exponent notation, `NaN`, and infinity are invalid. A leading `+` or `-`
is syntactically accepted so that negativity is handled explicitly by validation.

After exact decimal parsing, values with more than two fractional digits are
invalid; values are never rounded. Canonical serialization is fixed two-decimal
text such as `1299.00`. A parse failure uses `INVALID_UNIT_PRICE`; a negative
parsed value uses the same code with a different human-readable reason.

### 5.4 Dates and frozen clock

The fixed clock is:

```text
demo_date = 2026-10-07
timezone = Europe/Paris
```

The machine clock MUST NOT affect validation. Accepted, exhaustive formats are:

- ISO: `%Y-%m-%d`;
- day-first numeric: `%d/%m/%Y`;
- English full month: `%d %B %Y`;
- English abbreviated month: `%d %b %Y`.

Parsing is strict (including real calendar validity) and locale-independent.
Timestamps, two-digit years, and unlisted formats are invalid. A valid date
strictly later than `2026-10-07` is a future date. Invalid/impossible dates use
`INVALID_ORDER_DATE`; later dates use `FUTURE_ORDER_DATE`.

### 5.5 Email hygiene and validation

For a non-null customer email:

1. apply Unicode NFC and surrounding-whitespace trimming;
2. require exactly one `@`;
3. preserve local-part casing exactly;
4. lowercase the domain;
5. accept only a bounded shape of non-whitespace local text plus a DNS-like domain
   containing at least one dot; domain labels may contain ASCII letters, digits,
   and internal hyphens but may not begin or end with a hyphen.

This is transparent demo validation, not full RFC validation. A malformed email is
retained after safe hygiene, emits `INVALID_EMAIL_FORMAT`, and does not invalidate
an otherwise usable customer reference row. A changed value emits
`NORMALIZE_EMAIL`.

### 5.6 Product categories

Matching uses text normalization and case-folding. The exhaustive demo map is:

| Accepted aliases | Canonical value |
| --- | --- |
| `Electronics`, `electronics`, `Tech`, `tech` | `ELECTRONICS` |
| `Home`, `home`, `Home & Living`, `home and living` | `HOME` |
| `Office`, `office`, `Office Supplies`, `office supplies` | `OFFICE` |
| `Apparel`, `apparel`, `Clothing`, `clothing` | `APPAREL` |

Unknown non-null categories are retained after whitespace normalization and emit
`UNKNOWN_CATEGORY`; they are never inferred. Null category is permitted.

### 5.7 Fuzzy customer candidates

Fuzzy comparison is evidence-only. For each pair of valid, distinct customer IDs
with non-null names, construct a comparison key using NFC, whitespace collapse,
trim, and Unicode case-folding. Compute `rapidfuzz.fuzz.ratio` on the keys. A score
of at least `90.0` emits `FUZZY_CUSTOMER_CANDIDATE` with both provenances, IDs,
and the score. Candidate pairs are ordered by customer ID and then source order.

No score, including `100.0`, authorizes a merge or changes a sales foreign key.

## 6. Processing and terminal-disposition contract

The operational order is fixed:

```text
INGEST -> SCHEMA -> NORMALIZE -> HARD VALIDATION
       -> DUPLICATE RESOLUTION -> REFERENCE VALIDATION -> ACCEPT
```

Each successfully ingested transaction row MUST receive exactly one terminal
disposition: `ACCEPTED`, `QUARANTINED`, or `DEDUPLICATED`.

### 6.1 Hard validation

All hard checks applicable to a row are evaluated. If one or more fail, the row is
immediately `QUARANTINED` after their evidence is recorded. It does not enter
duplicate or reference processing, even when another row has the same `order_id`.

Primary failure is the first present code in this order:

1. `MISSING_ORDER_ID`
2. `MISSING_CUSTOMER_ID`
3. `MISSING_PRODUCT_ID`
4. `INVALID_IDENTIFIER` (field order: `order_id`, `customer_id`, `product_id`)
5. `INVALID_ORDER_DATE`
6. `FUTURE_ORDER_DATE`
7. `INVALID_QUANTITY`
8. `INVALID_UNIT_PRICE`

Every detected hard failure is retained in audit evidence in this same order.

### 6.2 Duplicate resolution

Duplicate comparison uses the seven normalized sales business fields and excludes
provenance.

- For a group sharing `order_id`, if every member is identical across all seven
  normalized business fields, the first row in section 2 source order continues;
  every later member is `DEDUPLICATED` with `DUPLICATE_EXACT` evidence pointing
  to the survivor.
- If any member sharing `order_id` differs in any other business field, every
  member of that post-hard-validation group is `QUARANTINED` with
  `DUPLICATE_KEY_CONFLICT`. No winner is selected. This fail-safe rule avoids
  accepting one unsupported commercial fact.
- A group is formed only from rows that passed hard validation. Hard-invalid rows
  never affect group membership or duplicate counts.

### 6.3 Reference preparation and validation

Reference rows are normalized, validated, and ordered under section 2.

- A customer row with missing/invalid `customer_id` is `REFERENCE_REJECTED` and
  excluded from the lookup. Email/country flags do not exclude it.
- A product row with missing/invalid `product_id`, missing/unparseable price, or
  negative price is `REFERENCE_REJECTED` and excluded from the lookup. Unknown
  category does not exclude it.
- Identical normalized rows sharing a reference key retain the first source-order
  row; later copies are `REFERENCE_DEDUPLICATED` with
  `REFERENCE_DUPLICATE_EXACT` evidence.
- If rows sharing a reference key differ in any canonical field, every member is
  `REFERENCE_REJECTED` with `REFERENCE_KEY_CONFLICT`; that key is unavailable.

For each surviving sales row, missing/unavailable `customer_id` and `product_id`
lookups are both evaluated. Either failure makes the sales row `QUARANTINED`.
`UNKNOWN_CUSTOMER_REFERENCE` precedes `UNKNOWN_PRODUCT_REFERENCE` as the primary
code when both occur. Reference rows have separate summary counts and never enter
the transaction G2 equation.

### 6.4 G2 counting semantics

`input_transaction_rows` is the number of data rows in all successfully ingested,
schema-valid sales tables, counted once before normalization. Headers and reference
rows are excluded. On a successful run:

```text
input_transaction_rows
= accepted_rows + quarantined_rows + deduplicated_rows
```

- `accepted_rows`: transaction rows whose terminal disposition is `ACCEPTED`;
- `quarantined_rows`: rows quarantined at hard validation, duplicate conflict, or
  reference validation;
- `deduplicated_rows`: only non-survivor exact/legal duplicates assigned
  `DEDUPLICATED` after hard validation.

Counts are counts of source rows, not unique keys or audit events. Multiple rule
failures do not increment a terminal count more than once. A file-level schema
failure fails the run, publishes no final deliverables, and therefore does not
claim a G2 result for that failed run.

## 7. Stable rule and action catalogue

Codes and spellings are stable. Tests MUST assert them exactly.

| Code | Action type | Meaning / effect |
| --- | --- | --- |
| `UNEXPECTED_INPUT` | `SCHEMA` | Unsupported input entry ignored and reported |
| `UNEXPECTED_SHEET` | `SCHEMA` | Unselected workbook sheet ignored and reported |
| `UNEXPECTED_COLUMN` | `SCHEMA` | Unlisted column ignored and reported |
| `MISSING_REQUIRED_COLUMN` | `SCHEMA` | Required canonical header absent; run fails |
| `ALIAS_COLLISION` | `SCHEMA` | Multiple headers map to one field; run fails |
| `NORMALIZE_WHITESPACE` | `NORMALIZATION` | Material descriptive-text whitespace change |
| `NORMALIZE_IDENTIFIER` | `NORMALIZATION` | Material identifier whitespace/case change |
| `NORMALIZE_COUNTRY` | `NORMALIZATION` | Known country alias changed to `FR` |
| `NORMALIZE_CURRENCY` | `NORMALIZATION` | Accepted EUR text changed to fixed decimal |
| `NORMALIZE_DATE` | `NORMALIZATION` | Accepted date changed to ISO form |
| `NORMALIZE_EMAIL` | `NORMALIZATION` | Safe email trim/domain-case change |
| `NORMALIZE_CATEGORY` | `NORMALIZATION` | Known category alias changed to canonical code |
| `MISSING_ORDER_ID` | `VALIDATION_FAILURE` | Null/empty sales order key; quarantine |
| `MISSING_CUSTOMER_ID` | `VALIDATION_FAILURE` | Null/empty customer key; quarantine sales or reject customer reference |
| `MISSING_PRODUCT_ID` | `VALIDATION_FAILURE` | Null/empty product key; quarantine sales or reject product reference |
| `INVALID_IDENTIFIER` | `VALIDATION_FAILURE` | Non-null critical identifier violates bounded form; quarantine/reject |
| `INVALID_ORDER_DATE` | `VALIDATION_FAILURE` | Missing, unparseable, or impossible date; quarantine |
| `FUTURE_ORDER_DATE` | `VALIDATION_FAILURE` | Date later than frozen demo date; quarantine |
| `INVALID_QUANTITY` | `VALIDATION_FAILURE` | Missing, non-integer, or non-positive quantity; quarantine |
| `INVALID_UNIT_PRICE` | `VALIDATION_FAILURE` | Missing, malformed, ambiguous, over-precision, or negative price; quarantine/reject |
| `DUPLICATE_EXACT` | `DEDUPLICATION` | Valid identical transaction copy removed; deduplicated |
| `DUPLICATE_KEY_CONFLICT` | `VALIDATION_FAILURE` | Same order key has conflicting valid facts; all group members quarantined |
| `REFERENCE_DUPLICATE_EXACT` | `DEDUPLICATION` | Identical reference copy excluded after first |
| `REFERENCE_KEY_CONFLICT` | `REFERENCE_FAILURE` | Conflicting reference key; all members rejected and key unavailable |
| `UNKNOWN_CUSTOMER_REFERENCE` | `REFERENCE_FAILURE` | Sales customer key absent from usable reference; quarantine |
| `UNKNOWN_PRODUCT_REFERENCE` | `REFERENCE_FAILURE` | Sales product key absent from usable reference; quarantine |
| `INVALID_EMAIL_FORMAT` | `FLAG` | Customer email fails bounded syntax; retain and flag |
| `UNKNOWN_COUNTRY` | `FLAG` | Unknown country retained without guessing |
| `UNKNOWN_CATEGORY` | `FLAG` | Unknown category retained without guessing |
| `FUZZY_CUSTOMER_CANDIDATE` | `FLAG` | Similar distinct customer names; evidence only, never merge |

Human-readable reasons MUST be deterministic templates keyed by code, with field
or value details appended in a stable form. Wording is client-facing evidence but
MUST NOT be parsed in place of the code.

## 8. Evidence and output schemas

All CSV files are UTF-8 with header row, comma delimiter, RFC 4180 quoting, and
LF line endings. Null is an empty field. Rows use section 2 source order unless a
more specific ordering is stated.

### 8.1 `rejected_rows.csv`

This file contains every non-accepted transaction row (`QUARANTINED` and
`DEDUPLICATED`) and every `REFERENCE_REJECTED` or `REFERENCE_DEDUPLICATED` row.
Its exact columns are:

```text
record_type,source_file,source_sheet,source_row,terminal_disposition,primary_rule_code,rule_codes,reason,order_id,customer_id,product_id,order_date,quantity,unit_price_eur,country,customer_name,email,product_name,category
```

- `record_type` is `sales`, `customers`, or `products`.
- Transaction `terminal_disposition` is `QUARANTINED` or `DEDUPLICATED`.
- Reference disposition is `REFERENCE_REJECTED` or
  `REFERENCE_DEDUPLICATED` and is excluded from G2.
- `rule_codes` is every applicable terminal-stage code in deterministic priority
  order, joined with `|`; `primary_rule_code` is its first value.
- Business fields contain the source row's original values serialized as text;
  non-applicable fields are empty. The audit log carries cleaned values.
- A multi-failure row appears once here, not once per failure.

### 8.2 `audit_log.csv`

Exact columns:

```text
event_index,record_type,source_file,source_sheet,source_row,field_name,original_value,cleaned_value,code,action_type,related_source_file,related_source_sheet,related_source_row,message
```

- `event_index` starts at 1 and follows source order, then processing stage, then
  catalogue/primary priority; it is deterministic.
- `field_name` is empty for row/group events.
- Related provenance identifies a duplicate survivor or fuzzy-match partner.
- One event is emitted per material normalization and per applicable failure,
  deduplication, reference failure, or flag.
- No event is emitted for a no-op normalization.
- Quarantine evidence includes all failures detected at the terminal stage; later
  stages are not run after a terminal disposition.

### 8.3 `cleaning_summary.json`

The stable top-level contract is:

```json
{
  "specification_version": "1.0.1",
  "run_metadata": {
    "pipeline_version": "1.0.0",
    "demo_seed": 1007,
    "demo_date": "2026-10-07",
    "timezone": "Europe/Paris",
    "source_ordering": "nfc_relative_path_then_sheet_then_row"
  },
  "input_files": [
    {
      "path": "relative/path.csv",
      "sha256": "hex-digest",
      "record_type": "sales",
      "selected_sheets": [],
      "row_count": 0
    }
  ],
  "transaction_counts": {
    "input": 0,
    "accepted": 0,
    "quarantined": 0,
    "deduplicated": 0,
    "reconciled": true
  },
  "reference_counts": {
    "customers": {"input": 0, "valid": 0, "rejected": 0, "deduplicated": 0},
    "products": {"input": 0, "valid": 0, "rejected": 0, "deduplicated": 0}
  },
  "rule_counts": {},
  "normalization_counts": {},
  "outputs": []
}
```

Arrays follow deterministic source/output order. Map keys are lexicographically
sorted. `rule_counts` counts audit events for all non-normalization codes;
`normalization_counts` counts audit events for `NORMALIZE_*` codes. A reference
table obeys `input = valid + rejected + deduplicated`, where `valid` means the
source rows retained as usable lookup records. JSON uses UTF-8, two-space
indentation, sorted keys, and one final newline. `outputs` is the deterministic
list `cleaned_sales.xlsx`, `cleaned_sales.csv`, `rejected_rows.csv`,
`audit_log.csv`, `cleaning_summary.json`, `data_quality_report.html`. No wall-clock
timestamp, absolute path, hostname, username, or runtime-specific value is allowed
in deterministic expected output.

### 8.4 `cleaned_sales.csv`

It contains only `ACCEPTED` rows in source order and uses the exact sales column
order in section 3.2. Dates are ISO, money has two decimals, integers have no
decimal suffix, and null is empty.

### 8.5 `cleaned_sales.xlsx`

The workbook has exactly one worksheet named `cleaned_sales`, with the same rows
and column order as the CSV. It has one header row, no dataframe index, dates as
spreadsheet dates displayed `yyyy-mm-dd`, money as numeric cells displayed `0.00`,
and `_source_row`/`quantity` as integers. Freeze the header row and enable an
autofilter. It contains no dashboard, hidden sheet, formula, or macro.

### 8.6 `data_quality_report.html`

The report MUST show, in this order:

1. title, specification version, frozen demo date/timezone;
2. processed input files and transaction/reference input counts;
3. accepted, quarantined, and deduplicated transaction counts;
4. the G2 equation and explicit pass/fail result;
5. defect/rule counts and normalization counts;
6. reference-data validity counts and fuzzy candidate count;
7. safeguards: provenance, quarantine, audit trail, no silent deletion;
8. links/names for `cleaned_sales.xlsx`, `cleaned_sales.csv`,
   `rejected_rows.csv`, `audit_log.csv`, and `cleaning_summary.json`;
9. bounded-demo limitations and v1 non-goals.

The report MUST use client-readable labels, deterministic section/table ordering,
and no volatile timestamp. Visual styling is deferred to its owning phase.

## 9. Conceptual consistency cases

Each case below is an independent successful run or isolated fixture. `I`, `A`,
`Q`, and `D` denote input, accepted, quarantined, and deduplicated transaction
row counts.

| Case | Proposed result | G2 check |
| --- | --- | --- |
| A - valid row | `ACCEPTED`; no terminal error evidence | `1 = 1 + 0 + 0` |
| B - formatting only | Normalize, audit changed fields, then `ACCEPTED` | `1 = 1 + 0 + 0` |
| C - invalid quantity | `QUARANTINED` / `INVALID_QUANTITY` | `1 = 0 + 1 + 0` |
| D - exact valid duplicate | First source row `ACCEPTED`; later copy `DEDUPLICATED` / `DUPLICATE_EXACT` | `2 = 1 + 0 + 1` |
| E - conflicting order key | Every member `QUARANTINED` / `DUPLICATE_KEY_CONFLICT` | `2 = 0 + 2 + 0` |
| F - invalid row also duplicate | Invalid row quarantined before grouping; otherwise-valid same-key row proceeds alone | `2 = 1 + 1 + 0` |
| G - fuzzy customer similarity | `FUZZY_CUSTOMER_CANDIDATE` evidence only; no merge; valid sale remains accepted | `1 = 1 + 0 + 0` |
| H - unknown reference | Sale `QUARANTINED` with customer/product reference code(s) | `1 = 0 + 1 + 0` |
| I - future date | Date after 2026-10-07 is `QUARANTINED` / `FUTURE_ORDER_DATE` | `1 = 0 + 1 + 0` |
| J - several hard failures | One `QUARANTINED` disposition and rejected row; all hard-failure audit events ordered; primary code fixed | `1 = 0 + 1 + 0` |

These cases confirm that evidence multiplicity never changes terminal row counts.

## 10. Scope and adoption

This proposal adds no runtime implementation and no product feature. It adds no
database, cloud service, web/API/authentication surface, distributed technology,
LLM/ML system, dashboard, schema engine, or universal cleaner. It only freezes
bounded demo behavior needed to test F01-F10 and G1-G8.

Owner adoption SHOULD be recorded explicitly. If adopted, supporting documents
must change the specification status to v1.0.1, close the five DF-000 defects, and
re-run DF-000 acceptance. Adoption itself does not authorize DF-001 unless the
Owner separately does so.
