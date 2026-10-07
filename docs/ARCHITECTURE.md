# DataForge architecture

DataForge is a linear, deterministic batch pipeline for the repository's frozen
sales, customer, and product schemas. Each phase has one responsibility and hands
structured records or evidence to the next phase.

```text
CSV/XLSX
   |
   v
ingest + schema -> normalize -> hard validation -> deduplicate
                                              -> reference validation
                                              -> accepted rows
                                                        |
                                                        v
                                     cleaned CSV/XLSX + evidence outputs
```

## Module responsibilities

| Module | Responsibility |
| --- | --- |
| `ingest.py` | Discovers immediate CSV/XLSX inputs in stable order, selects authorized sheets, reads raw values, and attaches provenance. |
| `schema.py` | Defines canonical fields, exhaustive header aliases, schemas, stable codes, and shared contracts. |
| `normalize.py` | Converts only documented representations for identifiers, text, dates, EUR values, countries, categories, email hygiene, and integers. It emits events and issues without assigning terminal dispositions. |
| `validate.py` | Applies hard business rules, prepares customer/product reference sets, validates foreign keys, and asserts terminal reconciliation. |
| `dedupe.py` | Resolves valid same-key transactions and references: exact copies keep the first source-order row; conflicts keep no winner. |
| `audit.py` | Serializes upstream normalization and validation evidence into deterministic audit and rejected-row records. |
| `report.py` | Writes the evidence CSVs, summary JSON, and self-contained HTML quality report without re-deciding business outcomes. |
| `pipeline.py` | Orchestrates the phases, verifies accepted-row identity and output semantics, stages all artifacts, and publishes the six-file delivery safely. |
| `cli.py` | Validates public paths, invokes the pipeline, prints reconciliation, and maps failures to stable exit codes. |

## Phase boundaries

The processing order is fixed:

```text
INGEST -> SCHEMA -> NORMALIZE -> HARD VALIDATION
       -> DUPLICATE RESOLUTION -> REFERENCE VALIDATION -> ACCEPT
```

Normalization may repair a documented representation, but it cannot remove a
row. Hard validation runs before duplicate handling, so invalid rows cannot alter
survivor selection. Duplicate conflicts quarantine every member rather than
selecting an unsupported fact. Reference validation occurs only after a
transaction survives those checks.

Every transaction ends in exactly one terminal set:

```text
input rows = accepted rows + quarantined rows + deduplicated rows
```

Reference rows use their own valid, rejected, and deduplicated counts and never
enter that transaction equation.

## Evidence and publication

Normalization and validation create structured evidence with source provenance.
The reporting layer renders that evidence; it does not parse values or repeat
rules. The pipeline writes all outputs to a staging directory, reopens and checks
them, then replaces only the six known deliverables. A failed run does not publish
a partial delivery, and unrelated output-directory files remain untouched.

Tests compare the pipeline with the deterministic corpus and preregistered oracle
in `data/demo_expected/`. Production modules never read that oracle.
