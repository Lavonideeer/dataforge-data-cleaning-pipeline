# DataForge

From messy spreadsheets to validated, analysis-ready data.

DataForge is a bounded portfolio project for consolidating and validating messy
Excel/CSV e-commerce data while preserving source provenance, rejected records,
reconciliation, and a machine-readable audit trail.

## Current status

**DF-006 - end-to-end pipeline and client deliverables: implemented**

The authoritative v1.0 PDF has been recovered and recorded in
[`docs/SPECIFICATION.md`](docs/SPECIFICATION.md). The Owner-adopted deterministic
clarification is [`docs/NORMATIVE_APPENDIX_v1.0.1.md`](docs/NORMATIVE_APPENDIX_v1.0.1.md).
Together they provide the frozen authority for future implementation.

DF-000 is closed. DF-001 supplies the deterministic synthetic demo corpus,
DF-002 provides bounded CSV/XLSX ingestion plus structural schema checks, and
DF-003 converts recoverable representations to canonical values. DF-004 applies
the frozen hard business rules, duplicate resolution, and reference validation.
DF-005 serializes that evidence into the client-facing audit, summary, and
quality artifacts. DF-006 composes those stages into the public CLI and publishes
the complete six-file client delivery. No DF-007 or DF-008 work has been
performed.

## Authority

- Source: `DataForge_Project_Workflow_v1.0.pdf`
- Specification: v1.0 plus adopted v1.0.1 normative clarification
- SHA-256: `ea7f9af75c73d84a5f08a89e8d9eecc043f66d8f9fb9d4fa9c3251777c038dea`
- Bootstrap date: 2026-10-07

The PDF defines the original v1 scope. The adopted appendix governs the five
deterministic details it clarifies. Neither authorizes scope expansion.

## Run the complete demo

```bash
python -m dataforge.cli --input data/demo_raw --output examples/output
```

The command exits nonzero for unsafe paths or structural input failures. A
successful run reports reconciliation and writes exactly these DataForge
artifacts while leaving unrelated output-directory files untouched:

- `cleaned_sales.xlsx` - one `cleaned_sales` worksheet with typed dates, numeric
  money, a frozen header row, and an autofilter;
- `cleaned_sales.csv` - the same accepted transactions in deterministic source
  order;
- `rejected_rows.csv` - non-accepted transaction and reference evidence;
- `audit_log.csv` - material transformations, failures, deduplication, and flags;
- `cleaning_summary.json` - frozen metadata, counts, reconciliation, and outputs;
- `data_quality_report.html` - client-readable quality and safeguard report.

For the frozen corpus, the pipeline derives 164 input transactions, 146 accepted,
16 quarantined, and 2 deduplicated. The cleaned files contain the 146 accepted
rows plus their header.

## Synthetic demo data

DF-001 generates 164 synthetic transaction rows across four monthly files (two
CSV and two XLSX), plus customer and product references. Every dirty record is a
controlled transformation of canonical ground truth.

```bash
python scripts/generate_demo_data.py
```

- Fixed seed: `1007`
- Frozen clock: `2026-10-07`, `Europe/Paris`
- `data/demo_raw/`: publishable messy inputs
- `data/demo_expected/ground_truth_*.csv`: canonical source-of-truth records
- `data/demo_expected/expected_transactions.csv`: one expected terminal outcome
  per transaction source row
- `data/demo_expected/expected_outcomes.json`: cases, coverage, relationships,
  counts, and reproducibility metadata

CSV and JSON artifacts are byte-deterministic. XLSX ZIP containers are verified
by semantic workbook content because container bytes may vary while records do
not. All identities, emails, orders, and products are synthetic.

## Ingestion and schema boundary

DF-002 discovers only immediate `.csv` and `.xlsx` inputs in deterministic
UTF-8 relative-path order. It classifies the bounded sales, customer, and product
roles; reads only the authorized workbook sheets; maps the exhaustive documented
header aliases; and attaches `_source_file`, `_source_sheet`, and `_source_row`
to every ingested record.

Unexpected inputs, columns, and sheets are reported with stable schema
diagnostics. Missing required headers and alias collisions fail ingestion without
exposing partial row tables. Nullable business cells remain null, while all other
cell values—including whitespace, casing, dates, and currency text—remain raw.
Those raw values form the input boundary for DF-003.

## Value normalization boundary

DF-003 normalizes identifiers, descriptive text, bounded dates, exact EUR
currency, countries, product categories, email hygiene, and integer
representations. Canonical dates use `YYYY-MM-DD`; canonical money uses exact
decimal parsing and two-decimal text. The frozen clock remains `2026-10-07` in
`Europe/Paris`, but future-date policy is deliberately deferred to DF-004.

Material recoveries produce stable normalization events containing provenance,
field, original value, cleaned value, and action code. Malformed, ambiguous,
missing-critical, and unknown values produce separate non-terminal issues. No
rows are removed, and normalization is semantically idempotent. Hard validation,
quarantine, deduplication, and reference validation begin only in DF-004.

## Validation, deduplication, and quarantine boundary

DF-004 is the first phase allowed to assign a terminal disposition. It preserves
the frozen precedence `NORMALIZE -> HARD VALIDATION -> DUPLICATE RESOLUTION ->
REFERENCE VALIDATION -> ACCEPT` and gives every ingested transaction row exactly
one of `ACCEPTED`, `QUARANTINED`, or `DEDUPLICATED`.

Hard validation consumes the DF-003 issues instead of reparsing raw text, so a
normalization failure stays distinct from a successfully normalized value that
violates a business rule (for example a parsed `-12.50` price). Every applicable
failure is retained in frozen priority order, while the row still receives a
single terminal disposition. Hard-invalid rows are resolved before duplicate
accounting, so an invalid row can never become a duplicate.

Duplicate resolution groups only hard-valid rows by `order_id`. Identical groups
keep the first row in frozen provenance order and mark later copies
`DUPLICATE_EXACT`; groups that disagree on any canonical business field are
entirely quarantined with `DUPLICATE_KEY_CONFLICT`, with no winner selected.
Surviving rows are then checked against usable customer and product reference
sets. Near-duplicate customer names produce evidence-only
`FUZZY_CUSTOMER_CANDIDATE` flags and never merge identities or rewrite keys.

DF-004 asserts the G2 invariant `input = accepted + quarantined + deduplicated`
and fails closed on any violation. On the demo corpus the rules independently
derive `164 = 146 + 16 + 2`. DF-004 emits internal structured evidence only; the
client-facing artifacts belong to DF-005.

## Audit, summary, and quality report boundary

DF-005 reports decisions; it does not make them. It consumes DF-002/DF-003/DF-004
evidence without re-parsing values, re-resolving duplicates, or re-deciding any
disposition, and writes exactly four artifacts:

- `rejected_rows.csv` - every non-accepted record per appendix section 8.1. On the
  demo corpus that is 23 data rows: 16 quarantined transactions, 2 deduplicated
  transactions, 3 rejected customer/product references, and 2 deduplicated
  references. Reference rows are evidence only and stay outside transaction
  reconciliation, so 23 evidence rows is not a 23-row rejection count.
- `audit_log.csv` - every material normalization, failure, deduplication,
  reference failure, and flag, ordered by source order, then processing stage,
  then appendix catalogue priority, with original and cleaned values.
- `cleaning_summary.json` - the frozen v1 summary contract: run metadata, input
  file digests, transaction and reference counts, rule counts, normalization
  counts, and the v1 deliverable list.
- `data_quality_report.html` - a self-contained, dependency-free report covering
  processed inputs, dispositions, the G2 equation with an explicit pass/fail,
  defect counts, reference quality, safeguards, and bounded-demo limitations.

Every audit row traces back to exactly one upstream evidence object, and the
reporting modules expose no parser, resolver, or rule engine. All four artifacts
are byte-deterministic and contain no timestamp, hostname, or absolute path.
DF-006 invokes this evidence layer with completed-pipeline context so the final
HTML truthfully links all six generated artifacts without changing evidence
semantics.

## End-to-end delivery boundary

DF-006 calls DF-002 ingestion, DF-003 normalization, DF-004 validation, and
DF-005 evidence writing through their committed interfaces. It does not duplicate
their rules. Only DF-004 `ACCEPTED` outcomes enter `cleaned_sales.*`; the pipeline
asserts accepted-row identity and G2 reconciliation before publishing.

The six artifacts are generated in a staging directory, reopened and checked,
then published by known filename. Existing known outputs are restored if
publication fails, and foreign files are never deleted. CSV and evidence outputs
are byte-deterministic. XLSX is verified semantically because ZIP container bytes
may vary while sheet names, headers, types, values, and ordering remain identical.

## Repository layout

```text
.
|-- DataForge_Project_Workflow_v1.0.pdf
|-- docs/SPECIFICATION.md
|-- docs/NORMATIVE_APPENDIX_v1.0.1.md
|-- src/dataforge/
|-- tests/
|-- data/demo_raw/
|-- data/demo_expected/
|-- examples/output/
|-- scripts/generate_demo_data.py
|-- .github/workflows/tests.yml
|-- pyproject.toml
`-- LICENSE
```

## Dependency plan

The runtime currently uses:

- `openpyxl` for XLSX fixture generation and bounded ingestion;
- `rapidfuzz` for the frozen section 5.7 evidence-only customer-name comparison.

The development dependency is `pytest`. DF-006 adds no runtime dependency.

## Scope guard

V1 excludes databases/warehouses, cloud infrastructure, hosted APIs, web apps,
authentication, streaming/distributed systems, Spark, Airflow, Kafka, dbt,
Kubernetes, LLM cleaning, ML anomaly detection, predictive models, interactive
BI dashboards, and generic arbitrary-schema plugin frameworks.

See [`docs/SPECIFICATION.md`](docs/SPECIFICATION.md) for the complete recovered
requirements, acceptance gates, phase boundaries, testing strategy, and the
owner decisions and phase boundaries.
