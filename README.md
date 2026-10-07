# DataForge

From messy spreadsheets to validated, analysis-ready data.

DataForge is a bounded portfolio project for consolidating and validating messy
Excel/CSV e-commerce data while preserving source provenance, rejected records,
reconciliation, and a machine-readable audit trail.

## Current status

**DF-000 - specification freeze / repository bootstrap: REWORK REQUIRED**

The authoritative v1.0 PDF has been recovered and recorded in
[`docs/SPECIFICATION.md`](docs/SPECIFICATION.md). The repository skeleton is in
place, but implementation must not begin until the owner resolves the material
freeze gaps listed in that document. In particular, the PDF requires a canonical
schema and frozen rules at DF-000 exit but does not define them.

No DF-001 through DF-008 work has been performed.

## Authority

- Source: `DataForge_Project_Workflow_v1.0.pdf`
- Version: v1.0
- SHA-256: `ea7f9af75c73d84a5f08a89e8d9eecc043f66d8f9fb9d4fa9c3251777c038dea`
- Bootstrap date: 2026-10-07

The PDF is authoritative. The Markdown specification is an implementation aid.

## Intended public command

The frozen specification reserves this interface for DF-006:

```bash
python -m dataforge.cli --input data/demo_raw --output examples/output
```

It is intentionally not implemented during DF-000.

## Repository layout

```text
.
|-- DataForge_Project_Workflow_v1.0.pdf
|-- docs/SPECIFICATION.md
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
