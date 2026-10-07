"""DF-004 deterministic duplicate-resolution tests."""

from __future__ import annotations

import random

import pytest

from dataforge.dedupe import (
    DUPLICATE_EXACT,
    DUPLICATE_KEY_CONFLICT,
    DuplicateEntry,
    SourceRef,
    resolve_duplicates,
)


def _entry(file: str, row: int, key: str, business: tuple, sheet: str = "") -> DuplicateEntry:
    return DuplicateEntry(SourceRef(file, sheet, row), key, business)


def test_single_member_survives_untouched() -> None:
    (outcome,) = resolve_duplicates([_entry("sales.csv", 2, "ORD-1", ("a", 1))])
    assert outcome.status == "SURVIVOR"
    assert outcome.code is None
    assert outcome.survivor is None
    assert not outcome.deduplicated and not outcome.conflicted


def test_exact_duplicate_keeps_first_source_row() -> None:
    entries = [
        _entry("sales.csv", 5, "ORD-1", ("a", 1)),
        _entry("sales.csv", 42, "ORD-1", ("a", 1)),
    ]
    survivor, copy = resolve_duplicates(entries)
    assert survivor.status == "SURVIVOR"
    assert copy.status == "DUPLICATE"
    assert copy.code == DUPLICATE_EXACT
    assert copy.survivor == SourceRef("sales.csv", "", 5)


def test_three_identical_copies_keep_first_and_deduplicate_the_rest() -> None:
    entries = [_entry("sales.csv", row, "ORD-1", ("a", 1)) for row in (3, 9, 40)]
    outcomes = resolve_duplicates(entries)
    assert [item.status for item in outcomes] == ["SURVIVOR", "DUPLICATE", "DUPLICATE"]
    assert all(item.survivor == SourceRef("sales.csv", "", 3) for item in outcomes[1:])


def test_conflicting_key_quarantines_every_member_without_a_winner() -> None:
    entries = [
        _entry("sales.csv", 6, "ORD-5", ("a", 2)),
        _entry("sales.csv", 43, "ORD-5", ("a", 3)),
    ]
    outcomes = resolve_duplicates(entries)
    assert [item.status for item in outcomes] == ["CONFLICT", "CONFLICT"]
    assert all(item.code == DUPLICATE_KEY_CONFLICT for item in outcomes)
    assert all(item.survivor is None for item in outcomes)
    assert all(item.conflicted for item in outcomes)


def test_distinct_group_keys_never_interact() -> None:
    entries = [
        _entry("sales.csv", 2, "ORD-1", ("a", 1)),
        _entry("sales.csv", 3, "ORD-2", ("a", 1)),
    ]
    outcomes = resolve_duplicates(entries)
    assert [item.status for item in outcomes] == ["SURVIVOR", "SURVIVOR"]


def test_source_order_is_file_then_sheet_then_row() -> None:
    entries = [
        _entry("b.csv", 2, "ORD-1", ("a", 1)),
        _entry("a.csv", 9, "ORD-1", ("a", 1)),
        _entry("a.xlsx", 2, "ORD-1", ("a", 1), sheet="sales"),
        _entry("a.xlsx", 2, "ORD-1", ("a", 1), sheet=""),
    ]
    outcomes = resolve_duplicates(entries)
    # Outcome 1 is a.csv/9: file bytes dominate, so it beats every a.xlsx row.
    assert outcomes[1].status == "SURVIVOR"
    assert outcomes[1].ref == SourceRef("a.csv", "", 9)
    assert all(item.status == "DUPLICATE" for index, item in enumerate(outcomes) if index != 1)

    # Within one file, an empty CSV sheet sorts before any named sheet.
    same_file = [
        _entry("a.xlsx", 2, "ORD-1", ("a", 1), sheet="sales"),
        _entry("a.xlsx", 3, "ORD-1", ("a", 1), sheet=""),
    ]
    survivor = next(item for item in resolve_duplicates(same_file) if item.status == "SURVIVOR")
    assert survivor.ref == SourceRef("a.xlsx", "", 3)


def test_survivor_is_independent_of_input_order() -> None:
    entries = [
        _entry("sales_2026_07.csv", 42, "ORD-1", ("a", 1)),
        _entry("sales_2026_07.csv", 5, "ORD-1", ("a", 1)),
        _entry("sales_2026_08.xlsx", 2, "ORD-1", ("a", 1), sheet="sales"),
    ]
    expected = SourceRef("sales_2026_07.csv", "", 5)
    for seed in range(6):
        shuffled = list(entries)
        random.Random(seed).shuffle(shuffled)
        by_ref = {item.ref: item for item in resolve_duplicates(shuffled)}
        assert by_ref[expected].status == "SURVIVOR"
        assert sum(item.status == "SURVIVOR" for item in by_ref.values()) == 1
        assert by_ref[SourceRef("sales_2026_07.csv", "", 42)].survivor == expected


def test_duplicate_provenance_identity_is_rejected() -> None:
    entry = _entry("sales.csv", 2, "ORD-1", ("a", 1))
    with pytest.raises(ValueError, match="same provenance identity twice"):
        resolve_duplicates([entry, entry])


def test_outcomes_are_returned_in_caller_order() -> None:
    entries = [
        _entry("sales.csv", 42, "ORD-1", ("a", 1)),
        _entry("sales.csv", 5, "ORD-1", ("a", 1)),
    ]
    outcomes = resolve_duplicates(entries)
    assert [item.ref.source_row for item in outcomes] == [42, 5]
    assert [item.status for item in outcomes] == ["DUPLICATE", "SURVIVOR"]


def test_publication_codes_are_the_frozen_spellings() -> None:
    assert DUPLICATE_EXACT == "DUPLICATE_EXACT"
    assert DUPLICATE_KEY_CONFLICT == "DUPLICATE_KEY_CONFLICT"
