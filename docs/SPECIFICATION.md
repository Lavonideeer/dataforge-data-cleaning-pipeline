# DataForge v1.0 / v1.0.1 implementation specification aid

> Authority notice: this file is a concise implementation aid. The original PDF
> and adopted v1.0.1 normative appendix are the governing documents described
> below.

## Provenance and phase

| Field | Value |
| --- | --- |
| PDF filename | `DataForge_Project_Workflow_v1.0.pdf` |
| PDF SHA-256 | `ea7f9af75c73d84a5f08a89e8d9eecc043f66d8f9fb9d4fa9c3251777c038dea` |
| Specification version | v1.0 plus adopted v1.0.1 normative clarification |
| PDF pages reviewed | 6 of 6 |
| Bootstrap date | 2026-10-07 |
| Current authorized phase | DF-005 audit, summary, and quality reporting only |
| DF-000 decision | PASS after adopted clarification and closure review |
| Appendix status | ADOPTED on 2026-10-07 |
| DF-000R source commit | `389c7a3` |
| DF-001 corpus | Seed `1007`; frozen clock `2026-10-07`, `Europe/Paris` |
| DF-002 boundary | Deterministic CSV/XLSX discovery, selected-sheet ingestion, provenance, aliases, and structural diagnostics |
| DF-003 boundary | Canonical recoverable values plus deterministic normalization events/issues; no terminal disposition |
| DF-004 boundary | Hard validation, duplicate resolution, reference validation, and internal terminal-disposition evidence; no client-facing artifact |
| DF-005 boundary | Evidence serialization and reporting only: `rejected_rows.csv`, `audit_log.csv`, `cleaning_summary.json`, `data_quality_report.html`; no cleaned-sales output and no business decision |

## Authority model

1. `DataForge_Project_Workflow_v1.0.pdf` defines the objective, scope, phases,
   gates, deliverables, and original functional requirements.
2. [`NORMATIVE_APPENDIX_v1.0.1.md`](NORMATIVE_APPENDIX_v1.0.1.md) is the adopted
   normative clarification for the five previously underspecified deterministic
   implementation areas.
3. Where the appendix provides a more precise rule in those five areas, the
   appendix governs that implementation detail.
4. The appendix is not permission to expand v1 scope or begin DF-001.

## Objective

Simulate a small e-commerce client engagement in which inconsistent monthly
Excel/CSV inputs are consolidated into one trustworthy dataset. The result must
show the entire transformation from raw input to validated output, including
rejected records and an audit trail. Success means reproducible, tested results
that a non-technical client can understand and whose material transformations are
explainable.

The user story is: consolidate messy monthly sales spreadsheets, clean formatting
and duplicates, identify invalid records, and return trustworthy Excel/CSV data
with a clear change summary.

## V1 non-goals

- No database, warehouse, Airflow, Spark, cloud deployment, API service,
  dashboard platform, LLM, or machine-learning model.
- No universal auto-cleaner for arbitrary datasets.
- No hidden source-file mutation; raw demo inputs remain immutable.
- No research governance framework or multi-week architecture expansion.
- No production claim beyond the bounded demo schema and documented assumptions.

The scope guard also defers streaming/distributed processing, Kafka, dbt,
Kubernetes, authentication/accounts, generic plugin architecture, interactive BI,
enterprise observability, distributed tracing, and complex governance.

## Demo dataset concept

Create deterministic, synthetic, publishable e-commerce data by generating known
clean ground truth and then injecting controlled defects.

| Input | Purpose | Required examples |
| --- | --- | --- |
| Monthly sales files | Transactions | Mixed CSV/XLSX, aliases, duplicate orders, invalid quantity, malformed prices, mixed dates |
| `customers.xlsx` | Customer reference | Casing/spacing, missing IDs, malformed email, near-duplicate names |
| `products.csv` | Product reference | Category aliases, missing SKU, invalid unit price |

Required defect classes:

- schema aliases (`Customer ID`, `customer_id`, `CustomerID`), optional columns,
  and unexpected columns;
- EUR locale values such as `1 299,00 EUR`, `EUR1,299.00`, and `1299`, with
  mixed decimal/thousands separators;
- ISO, `DD/MM/YYYY`, textual, invalid, impossible, and future dates;
- country aliases (`France`, `france`, `FR`, `FRA`) and inconsistent categories;
- missing critical identifiers and missing optional descriptions;
- exact rows, duplicate `order_id`, and normalized near-duplicate customer names;
- `quantity <= 0`, `price < 0`, and impossible/future dates under a frozen date;
- leading/trailing whitespace, inconsistent case, and repeated internal spaces.

## Functional requirements

| ID | Frozen requirement |
| --- | --- |
| F01 | Read CSV and XLSX, including selected workbook sheets, from an input directory. Preserve source filename and source-row identifier. |
| F02 | Map documented aliases to canonical `snake_case` columns; report missing required and unexpected columns. |
| F03 | Normalize dates, EUR currency, whitespace, case, country codes, email text, and categorical aliases. |
| F04 | Quarantine critical missing identifiers; retain optional missing values; never use blanket `dropna()`. |
| F05 | Detect exact duplicates and duplicate business keys. Near-duplicate customers may be flagged but ambiguous entities must never be silently merged. |
| F06 | Enforce explicit constraints including `quantity > 0`, `unit_price >= 0`, unique `order_id`, and valid date. |
| F07 | Write invalid records to `rejected_rows.csv` with source, row ID, rule code, and human-readable reason. |
| F08 | Record material changes with source row, field, original value, cleaned value, and rule/action code. |
| F09 | Produce concise HTML with input/accepted/rejected counts, defect counts, and quality metrics. |
| F10 | Produce `cleaned_sales.xlsx`, `cleaned_sales.csv`, `rejected_rows.csv`, `audit_log.csv`, and `cleaning_summary.json`. |

F09 additionally establishes `data_quality_report.html` as a required artifact in
the deliverable contract.

## Data-handling policies

| Condition | Action | Rationale |
| --- | --- | --- |
| Recoverable formatting defect | Normalize and audit | Semantically recoverable |
| Critical ID missing | Quarantine | Entity cannot be safely identified |
| Invalid numeric business value | Quarantine | Do not invent commercial facts |
| Optional field missing | Retain null | Avoid unnecessary row loss |
| Exact duplicate | Deduplicate and audit | Deterministic |
| Ambiguous fuzzy duplicate | Flag only | No unsafe automatic merge |
| Unknown category | Retain/flag per schema | Avoid forced misclassification |

Frozen principle: never silently destroy customer data. Every dropped,
quarantined, deduplicated, normalized, or materially changed record must be
explainable from machine-readable evidence.

## DF-004 validation, deduplication, and quarantine contract

Validation precedence is frozen and must not be reordered for convenience:

```text
INGEST -> SCHEMA -> NORMALIZE -> HARD VALIDATION
       -> DUPLICATE RESOLUTION -> REFERENCE VALIDATION -> ACCEPT
```

**Terminal disposition model.** Every successfully ingested transaction row
receives exactly one terminal disposition: `ACCEPTED`, `QUARANTINED`, or
`DEDUPLICATED`. A row may carry several failure codes but never more than one
terminal disposition, and no row may silently disappear. The production result
contract asserts that the three terminal buckets are pairwise disjoint, that
their union equals every ingested transaction provenance identity, and that
`input_transaction_rows = accepted_rows + quarantined_rows + deduplicated_rows`.
Any violation raises and fails the run closed. Reference rows use the separate
dispositions `REFERENCE_VALID`, `REFERENCE_REJECTED`, and
`REFERENCE_DEDUPLICATED` and never enter the transaction equation.

**Hard validation.** DF-004 consumes the DF-003 issues rather than reparsing raw
strings, keeping a normalization failure distinct from a successfully normalized
value that violates a business rule. It evaluates every applicable rule and keeps
all failures in the frozen primary-code order `MISSING_ORDER_ID`,
`MISSING_CUSTOMER_ID`, `MISSING_PRODUCT_ID`, `INVALID_IDENTIFIER` (field order
`order_id`, `customer_id`, `product_id`), `INVALID_ORDER_DATE`,
`FUTURE_ORDER_DATE`, `INVALID_QUANTITY`, `INVALID_UNIT_PRICE`. Business rules add
`quantity > 0`, `unit_price_eur >= 0.00` in exact decimal arithmetic, and
`order_date <= 2026-10-07` against the frozen clock, never the machine clock.
`UNKNOWN_COUNTRY`, `UNKNOWN_CATEGORY`, and `INVALID_EMAIL_FORMAT` stay flag-only.

**Hard-validation precedence over deduplication.** A hard-invalid row is
`QUARANTINED` immediately and does not participate in duplicate grouping, so it
can never become `DEDUPLICATED` merely because another row shares its key.

**Duplicate behavior.** Groups form only from hard-valid rows sharing `order_id`.
If every member is identical across the seven normalized business fields, the
first member in frozen section 2 source order survives and each later member is
`DEDUPLICATED` / `DUPLICATE_EXACT` with evidence naming that survivor. If any
member differs, every member is `QUARANTINED` / `DUPLICATE_KEY_CONFLICT`; no
winner is selected and no conflicting row reaches accepted output.

**Reference validation.** Customer rows with a missing or invalid `customer_id`,
and product rows with a missing or invalid `product_id`, a missing/unparseable
price, or a negative price, are rejected and excluded from the lookup; email,
country, and category flags do not exclude them. Identical reference copies keep
the first source-order row and mark the rest `REFERENCE_DUPLICATED` /
`REFERENCE_DUPLICATE_EXACT`; conflicting reference keys reject every member with
`REFERENCE_KEY_CONFLICT` and make the key unavailable. Surviving sales rows then
require both keys to be present in the usable reference sets; either failure
quarantines the row, and `UNKNOWN_CUSTOMER_REFERENCE` precedes
`UNKNOWN_PRODUCT_REFERENCE`. Reference tables validate foreign keys only:
transaction rows are never enriched with reference descriptive fields or
reference prices. Near-duplicate customer names emit evidence-only
`FUZZY_CUSTOMER_CANDIDATE` at the frozen threshold and never merge entities or
rewrite identifiers.

**DF-005 boundary.** DF-004 publishes no client-facing artifact. It produces
internal structured evidence for hard failures, duplicate decisions, conflicts,
reference failures, fuzzy flags, and terminal dispositions so that DF-005 can
render `rejected_rows.csv`, `audit_log.csv`, `cleaning_summary.json`, and the
HTML quality report. `cleaned_sales.csv`/`.xlsx`, the final CLI, and the
end-to-end pipeline remain DF-006 deliverables.

## DF-005 evidence and reporting contract

DF-005 reports decisions; it never makes them. It consumes DF-002/DF-003/DF-004
evidence without parsing currency, dates, identifiers, quantities, or prices,
without resolving duplicates, without validating foreign keys, and without
choosing a terminal disposition. The reporting modules expose no rule engine.

**Artifacts.** Exactly four are authorized: `rejected_rows.csv` (appendix 8.1),
`audit_log.csv` (8.2), `cleaning_summary.json` (8.3), and
`data_quality_report.html` (8.6). All are UTF-8, use LF line endings, and are
byte-deterministic: no wall-clock timestamp, hostname, username, absolute path,
or random run identifier is written.

**`rejected_rows.csv` row set.** Appendix 8.1 governs unchanged: the file holds
every `QUARANTINED` and `DEDUPLICATED` transaction row plus every
`REFERENCE_REJECTED` and `REFERENCE_DEDUPLICATED` reference row. On the frozen
corpus that is 23 data rows, recorded as 16 + 2 transaction rows and 3 + 2
reference rows. The filename is historical and its semantics are not narrowed.

**Evidence artifact versus transaction G2.** The 23-row evidence artifact is not
a transaction rejection count. Transaction reconciliation remains exactly
`164 = 146 + 16 + 2`, and reference rows never enter that equation. A row with
several failures is still one rejected row whose `primary_rule_code`,
`rule_codes`, and `reason` carry every applicable terminal failure. A
deduplicated transaction is classified `DEDUPLICATED`, never `QUARANTINED`, is
excluded from the quarantine count, and stays traceable through the survivor
provenance in the audit log.

**Audit completeness.** `audit_log.csv` is the union of three upstream evidence
sources and nothing else: DF-003 material `NormalizationEvent`s, DF-003
`NormalizationIssue`s whose action type is `FLAG`, and DF-004 `ValidationEvent`s.
DF-003 `VALIDATION_FAILURE` issues are not copied, because DF-004 already emits
one failure event per detected failure. `event_index` starts at 1 and follows
source order, then processing stage, then appendix section 7 catalogue priority.
No-op normalizations emit no event, and `field_name` is empty for row/group
events. Every audit row therefore satisfies `evidence ⊆ upstream evidence` while
also being complete over it.

**Summary and metrics.** `cleaning_summary.json` carries exactly the frozen
top-level keys `specification_version`, `run_metadata`, `input_files`,
`transaction_counts`, `reference_counts`, `rule_counts`, `normalization_counts`,
and `outputs`. `rule_counts` and `normalization_counts` count the audit events
actually written to `audit_log.csv`, so the summary can never disagree with the
log. The `outputs` array is the frozen six-name v1 deliverable contract and
includes `cleaned_sales.xlsx`/`.csv`, which this phase does not generate and does
not claim to have generated. No quality score beyond these frozen counts is
defined; the report shows percentages only as labelled derivations of those
counts over the input row count.

**Report.** `data_quality_report.html` is self-contained: no CDN, no script, no
framework, and all data-derived text is HTML-escaped. It presents, in the frozen
order, the specification version and demo clock, processed inputs and input
counts, transaction dispositions, the G2 equation with an explicit pass/fail,
defect and normalization counts, reference-data validity and fuzzy candidates,
safeguards (provenance, quarantine, audit trail, no silent deletion), the output
artifact names with `cleaned_sales.*` identified as DF-006 deliverables, and the
bounded-demo limitations and v1 non-goals.

## Repository architecture

```text
README.md
pyproject.toml
src/dataforge/
  cli.py ingest.py schema.py normalize.py dedupe.py validate.py
  audit.py report.py pipeline.py
data/demo_raw/
data/demo_expected/
examples/output/
tests/
  test_ingest.py test_normalize.py test_dedupe.py test_validate.py test_pipeline.py
scripts/generate_demo_data.py
.github/workflows/tests.yml
LICENSE
```

Reserved demo interface:

```bash
python -m dataforge.cli --input data/demo_raw --output examples/output
```

## Development workflow

| Phase | Work | Exit criterion |
| --- | --- | --- |
| DF-000 Freeze | Commit specification, canonical schema, rules, outputs, and non-goals. | No unresolved core requirement. |
| DF-001 Demo data | Deterministic ground truth and defect injector. | Every required defect class is represented and reproducible. |
| DF-002 Ingestion/schema | CSV/XLSX, provenance, aliases, schema checks. | Ingestion tests pass; malformed schema fails clearly. |
| DF-003 Normalization | Dates, currency, country/category, text, email hygiene. | Known recoverable defects reach expected values. |
| DF-004 Validation/dedupe | Business rules, exact/key duplicates, fuzzy flags, quarantine. | No frozen-rule-invalid row reaches accepted output. |
| DF-005 Audit/report | Audit CSV, summary JSON, HTML QA report. | Every material action is traceable and counts reconcile. |
| DF-006 End-to-end | CLI, Excel/CSV, integration tests, deterministic demo run. | One command reproduces all expected deliverables. |
| DF-007 Portfolio polish | README, before/after, diagram, screenshots, GitHub Actions. | Non-technical reviewer understands value in 10 seconds or less. |
| DF-008 Release gate | Acceptance suite, scope review, v1.0 tag. | PASS or explicit BLOCKED; no scope-creep workaround. |

## Acceptance gates

| Gate | Requirement |
| --- | --- |
| G1 Reproducibility | Fresh environment, documented install, and one command produce expected demo outputs. |
| G2 Reconciliation | `input_rows = accepted_rows + quarantined_rows + explicitly_deduplicated_rows`, with documented accounting semantics. |
| G3 No silent loss | Every excluded row has a deterministic reason and source provenance. |
| G4 Validation integrity | Rows violating frozen hard business rules cannot enter cleaned output. |
| G5 Audit integrity | Every material normalization, deduplication, or quarantine event has an audit record. |
| G6 Test quality | Unit and integration tests pass in GitHub Actions; fixtures cover all frozen defect classes. |
| G7 Client usability | Clean XLSX/CSV opens; report is readable; filenames and outputs are self-explanatory. |
| G8 Portfolio clarity | README opening shows problem, before/after, metrics, command, outputs, and limitations without code inspection. |

## Release semantics and hard stop

- **PASS:** tag v1.0, pin the GitHub repository, and use it externally.
- **BLOCKED:** fix only the failed frozen requirement; add no unrelated features.
- **REWORK:** only when a core assumption is proven invalid; document why before
  changing scope.

Once G1-G8 pass and README is portfolio-ready, v1 is complete. Further ideas go
to `BACKLOG.md` and do not delay publication.

## Deliverable contract

Required: `cleaned_sales.xlsx`, `cleaned_sales.csv`, `rejected_rows.csv`,
`audit_log.csv`, `cleaning_summary.json`, `data_quality_report.html`, synthetic
demo raw files, tests and CI, and README screenshots/before-after material.

## Definition of Done

- Fresh clone/install/one-command demo works.
- Raw synthetic inputs visibly contain realistic spreadsheet defects.
- Clean output is deterministic and satisfies all frozen hard rules.
- Rejected and deduplicated records reconcile and are explainable.
- Audit data preserves provenance and material changes.
- QA report is useful to a non-technical client without reading code.
- GitHub Actions passes.
- README communicates value in its first screen with a strong before/after.
- No v1 non-goal was added for sophistication.
- Repository is tagged v1.0 and ready to pin beside Kronos; then development stops.

## Testing strategy by future phase

Tests must prove gates, not merely raise coverage. Planned ownership:

| Area | Phase | Proof obligation |
| --- | --- | --- |
| Generator | DF-001 | Same seed/config produces byte- or record-equivalent ground truth and all frozen defects. |
| Ingestion and aliases | DF-002 | CSV/XLSX and selected sheets preserve source/row; aliases map; missing/unexpected columns report deterministically. |
| Locale/date/currency/text | DF-003 | Every documented recoverable fixture maps to an exact expected value; invalid/ambiguous values do not become invented facts. |
| Missingness | DF-003/004 | Critical missing IDs quarantine; optional nulls remain. |
| Exact/key duplicates | DF-004 | Detection and survivor/accounting follow owner-approved precedence. |
| Fuzzy candidates | DF-004 | Candidates are flagged and ambiguous entities never merge. |
| Validation/quarantine | DF-004 | Every hard violation is excluded with stable rule code, reason, and provenance. |
| Audit and reconciliation | DF-005 | All material events have evidence and G2 balances exactly. |
| End-to-end outputs | DF-006 | One command produces deterministic, readable contracted artifacts. |
| CI/release | DF-007/008 | Fresh install passes unit/integration suite and all G1-G8 assertions. |

## Resolved DF-000 specification defects

The Owner adopted the v1.0.1 appendix. Each prior material defect now has
deterministic authority:

1. **Canonical schemas - RESOLVED.** Appendix sections 1-4 define fields, types,
   nullability, criticality, keys, aliases, provenance, joins, and final order.
2. **Normalization rules - RESOLVED.** Section 5 defines bounded text, country,
   currency, date, email, category, and fuzzy-candidate behavior.
3. **Frozen demo clock - RESOLVED.** Section 5.4 fixes `2026-10-07` and
   `Europe/Paris` independently of the machine clock.
4. **Precedence and accounting - RESOLVED.** Sections 2 and 6 define processing
   order, deterministic duplicate behavior, exactly one terminal disposition,
   multiple-failure ordering, and the G2 equation.
5. **Stable codes and evidence/output contracts - RESOLVED.** Sections 7-8 define
   stable codes and exact CSV, JSON, XLSX, and report contracts.

The PDF also has text overflow in the page 3-4 workflow/requirements tables. The
surrounding text makes their intended prose recoverable; this is a publication
quality defect, not by itself a semantic blocker.

### DF-000 closure

The combined authority now defines:

- canonical tables/columns, types, requiredness, keys, output order, and joins;
- exhaustive aliases and normalization targets for the bounded demo;
- frozen date and timezone;
- validation/deduplication precedence plus deterministic survivor and G2 rules;
- stable rule/action codes and minimum output/evidence schemas.

The DF-000 exit review found sufficient deterministic authority to implement
F01-F10 and later prove G1-G8. DF-000 is closed. DF-001 remains unauthorized
unless the Owner separately authorizes it.
