"""DF-000 repository health checks."""

from pathlib import Path

import dataforge


def test_package_is_importable() -> None:
    assert dataforge.__version__ == "0.0.0"


def test_authoritative_pdf_is_present() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    assert (repository_root / "DataForge_Project_Workflow_v1.0.pdf").is_file()
