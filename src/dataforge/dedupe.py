"""Deterministic duplicate resolution for the bounded DataForge v1 corpus.

DF-004 owns exact-duplicate removal and conflicting-key quarantine. Groups are
formed only from entries that already passed their owning stage validation, so a
hard-invalid transaction row never influences survivor selection, group
membership, or duplicate accounting (appendix v1.0.1 section 6.2).

``SourceRef`` is the frozen provenance identity defined by appendix section 2. It
is declared here so that ``dataforge.dedupe`` stays independent of the validation
layer that imports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

DUPLICATE_EXACT = "DUPLICATE_EXACT"
DUPLICATE_KEY_CONFLICT = "DUPLICATE_KEY_CONFLICT"

#: The seven normalized sales business fields; provenance is excluded.
BUSINESS_FIELDS = (
    "order_id",
    "customer_id",
    "product_id",
    "order_date",
    "quantity",
    "unit_price_eur",
    "country",
)

DuplicateStatus = Literal["SURVIVOR", "DUPLICATE", "CONFLICT"]


@dataclass(frozen=True)
class SourceRef:
    """Frozen, hashable provenance identity for exactly one source table row."""

    source_file: str
    source_sheet: str
    source_row: int

    @property
    def sort_key(self) -> tuple[bytes, bytes, int]:
        """Appendix section 2 deterministic source order.

        Source order MUST NOT depend on filesystem enumeration: ascending UTF-8
        byte order of the NFC-normalized relative path, then of the selected
        sheet (an empty CSV value sorts normally), then the numeric row number.
        """

        return (
            self.source_file.encode("utf-8"),
            self.source_sheet.encode("utf-8"),
            self.source_row,
        )

    def as_tuple(self) -> tuple[str, str, int]:
        return (self.source_file, self.source_sheet, self.source_row)


@dataclass(frozen=True)
class DuplicateEntry:
    """One stage-valid row offered to duplicate resolution.

    ``group_key`` is the business key whose sharing forms a group; for the sales
    contract that is the normalized ``order_id``. ``canonical_key`` is the tuple
    of every canonical business field used for semantic identity comparison. Both
    must already be canonical: the resolver never re-normalizes values.
    """

    ref: SourceRef
    group_key: str
    canonical_key: tuple[object, ...]


@dataclass(frozen=True)
class DuplicateOutcome:
    """Terminal duplicate decision for one entry, keyed by provenance identity."""

    ref: SourceRef
    status: DuplicateStatus
    code: str | None
    survivor: SourceRef | None
    group_key: str

    @property
    def deduplicated(self) -> bool:
        return self.status == "DUPLICATE"

    @property
    def conflicted(self) -> bool:
        return self.status == "CONFLICT"


def resolve_duplicates(entries: Sequence[DuplicateEntry]) -> tuple[DuplicateOutcome, ...]:
    """Resolve duplicates for stage-valid entries and return one outcome each.

    Outcomes are returned in the caller's order. Within one shared business key:

    * every member identical across the canonical business fields keeps the first
      member in section 2 source order; later members are ``DEDUPLICATED`` with
      ``DUPLICATE_EXACT`` evidence naming that survivor;
    * any difference quarantines *every* member with ``DUPLICATE_KEY_CONFLICT``;
      no winner is selected.
    """

    refs = [entry.ref for entry in entries]
    if len(set(refs)) != len(refs):
        raise ValueError("duplicate resolution received the same provenance identity twice")

    groups: dict[str, list[DuplicateEntry]] = {}
    for entry in sorted(entries, key=lambda item: item.ref.sort_key):
        groups.setdefault(entry.group_key, []).append(entry)

    outcomes: dict[SourceRef, DuplicateOutcome] = {}
    for group_key, members in groups.items():
        if len({member.canonical_key for member in members}) == 1:
            survivor = members[0].ref
            for index, member in enumerate(members):
                outcomes[member.ref] = DuplicateOutcome(
                    ref=member.ref,
                    status="SURVIVOR" if index == 0 else "DUPLICATE",
                    code=None if index == 0 else DUPLICATE_EXACT,
                    survivor=None if index == 0 else survivor,
                    group_key=group_key,
                )
        else:
            for member in members:
                outcomes[member.ref] = DuplicateOutcome(
                    ref=member.ref,
                    status="CONFLICT",
                    code=DUPLICATE_KEY_CONFLICT,
                    survivor=None,
                    group_key=group_key,
                )

    return tuple(outcomes[entry.ref] for entry in entries)
