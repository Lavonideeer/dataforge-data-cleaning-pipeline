# DataForge v1.0 implementation specification aid

> Authority notice: this file is a concise implementation aid. The original PDF
> `DataForge_Project_Workflow_v1.0.pdf` remains authoritative for v1.0.

## Provenance and phase

| Field | Value |
| --- | --- |
| PDF filename | `DataForge_Project_Workflow_v1.0.pdf` |
| PDF SHA-256 | `ea7f9af75c73d84a5f08a89e8d9eecc043f66d8f9fb9d4fa9c3251777c038dea` |
| Specification version | v1.0 |
| PDF pages reviewed | 6 of 6 |
| Bootstrap date | 2026-10-07 |
| Current authorized phase | DF-000R specification repair only |
| DF-000 decision | REWORK REQUIRED; Owner accepted the finding |
| Repair status | v1.0.1 normative appendix proposed, not adopted |

The bounded repair proposal is
[`NORMATIVE_APPENDIX_v1.0.1_PROPOSAL.md`](NORMATIVE_APPENDIX_v1.0.1_PROPOSAL.md).
It supplies deterministic candidate decisions for Owner review but does not modify
the authority or status of the original PDF.

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

## Material specification defects under Owner review

The Owner accepted the DF-000 rework finding and authorized DF-000R to propose,
but not adopt, deterministic repairs. These defects control record acceptance,
output values, or G2/G4/G5 verification.

1. **Canonical schema is absent.** DF-000 requires it at exit and F02/F04/F06
   depend on it, but no table defines sales/customer/product fields, types,
   requiredness, business keys, output columns, or reference-join behavior.
2. **Normalization rules are not frozen.** F03 names domains but supplies no
   canonical country/category values, complete alias maps, locale disambiguation
   policy, case policy, or email normalization/validation outcome.
3. **Date boundary is absent.** The dataset section requires future dates under a
   "frozen demo date," but does not state that date or timezone.
4. **Duplicate precedence/accounting is absent.** F05/F06/G2 do not define which
   duplicate survives, whether duplicate `order_id` rows are deduplicated or
   quarantined when their values conflict, or event priority when a row is both
   invalid and duplicate. This changes accepted/quarantined/deduplicated counts.
5. **Stable rules/evidence schemas are absent.** F07/F08 require rule/action codes
   and evidence fields, but the code catalogue, rejected-row schema, audit schema,
   summary schema, and XLSX sheet contract are not defined.

The PDF also has text overflow in the page 3-4 workflow/requirements tables. The
surrounding text makes their intended prose recoverable; this is a publication
quality defect, not by itself a semantic blocker.

### Proposed repair and remaining Owner action

The proposed appendix now defines:

- canonical tables/columns, types, requiredness, keys, output order, and joins;
- exhaustive aliases and normalization targets for the bounded demo;
- frozen date and timezone;
- validation/deduplication precedence plus deterministic survivor and G2 rules;
- stable rule/action codes and minimum output/evidence schemas.

The Owner must now accept, reject, or amend the proposal. After explicit adoption,
update the specification status and re-run DF-000 acceptance. DF-001 remains
unauthorized unless the Owner separately authorizes it.
