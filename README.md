# DataForge

From messy spreadsheets to validated, analysis-ready data.

DataForge is a bounded portfolio project for consolidating and validating messy
Excel/CSV e-commerce data while preserving source provenance, rejected records,
reconciliation, and a machine-readable audit trail.

## Current status

**DF-000 - specification freeze / repository bootstrap: PASS**

The authoritative v1.0 PDF has been recovered and recorded in
[`docs/SPECIFICATION.md`](docs/SPECIFICATION.md). The Owner-adopted deterministic
clarification is [`docs/NORMATIVE_APPENDIX_v1.0.1.md`](docs/NORMATIVE_APPENDIX_v1.0.1.md).
Together they provide the frozen authority for future implementation.

DF-000 is closed. DF-001 supplies the deterministic synthetic demo corpus. No
DF-002 through DF-008 implementation has been performed or authorized.

## Authority

- Source: `DataForge_Project_Workflow_v1.0.pdf`
- Specification: v1.0 plus adopted v1.0.1 normative clarification
- SHA-256: `ea7f9af75c73d84a5f08a89e8d9eecc043f66d8f9fb9d4fa9c3251777c038dea`
- Bootstrap date: 2026-10-07

The PDF defines the original v1 scope. The adopted appendix governs the five
deterministic details it clarifies. Neither authorizes scope expansion.

## Intended public command

The frozen specification reserves this interface for DF-006:

```bash
python -m dataforge.cli --input data/demo_raw --output examples/output
```

It remains reserved for the later CLI phase and is not implemented by DF-001.

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

DF-000 installs no runtime data stack. The planned minimum is:

- `pandas` for tabular ingestion and transformation;
- `openpyxl` for XLSX input/output;
- one schema library, selected only after the schema is frozen (`pandera` is the
  leading candidate; do not add both Pandera and Pydantic without justification);
- `rapidfuzz` only when DF-004 implements flag-only near-duplicate detection;
- `pytest` as the development test runner.

Dependencies enter the project only in the phase that needs them.

## Scope guard

V1 excludes databases/warehouses, cloud infrastructure, hosted APIs, web apps,
authentication, streaming/distributed systems, Spark, Airflow, Kafka, dbt,
Kubernetes, LLM cleaning, ML anomaly detection, predictive models, interactive
BI dashboards, and generic arbitrary-schema plugin frameworks.

See [`docs/SPECIFICATION.md`](docs/SPECIFICATION.md) for the complete recovered
requirements, acceptance gates, phase boundaries, testing strategy, and the
owner decisions required before DF-001.
