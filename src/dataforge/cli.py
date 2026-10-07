"""Bounded public command-line interface for DataForge v1."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Sequence
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from .ingest import SchemaIngestionError
from .pipeline import PipelineError, run_pipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dataforge.cli",
        description="Clean and validate the bounded DataForge CSV/XLSX input set.",
    )
    parser.add_argument("--input", required=True, type=Path, help="Raw input directory")
    parser.add_argument("--output", required=True, type=Path, help="Delivery output directory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_pipeline(args.input, args.output)
    except (
        PipelineError,
        SchemaIngestionError,
        BadZipFile,
        InvalidFileException,
        UnicodeError,
        csv.Error,
        OSError,
    ) as exc:
        print(f"DataForge failed: {exc}", file=sys.stderr)
        return 2

    counts = result.validation.reconciliation
    print(f"DataForge completed: {args.input} -> {args.output}")
    print(
        "Transactions: "
        f"{counts.input_transaction_rows} input = {counts.accepted_rows} accepted + "
        f"{counts.quarantined_rows} quarantined + {counts.deduplicated_rows} deduplicated"
    )
    print("Artifacts: " + ", ".join(path.name for path in result.paths()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
