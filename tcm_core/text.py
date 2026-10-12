"""Small text helpers for goals and literature pocket strings."""
from __future__ import annotations

import re

# Substring match against a free-text goal. Live lookups still have to
# confirm the entity; this list only decides whether a deep run has a compound.
KNOWN_COMPOUNDS = (
    "berberine", "baicalin", "quercetin", "wogonin",
    "emodin", "chrysophanol", "curcumin", "artemisinin",
    "paclitaxel", "ginsenoside", "astragaloside",
    "salidroside", "glycyrrhizin", "paeoniflorin",
    "tanshinone", "ferulic acid", "genistein",
    "kaempferol", "rutin", "luteolin", "apigenin",
    "catechin", "epigallocatechin", "silibinin",
    "triptolide", "celastrol", "baicalein",
)


def extract_known_compounds(goal: str | None, limit: int = 6) -> list[str]:
    text = (goal or "").lower()
    return [name for name in KNOWN_COMPOUNDS if name in text][:limit]


def normalize_pocket_residues(pocket: str) -> str:
    """Keep residue numbers from '46,52' or 'I46,I52'.

    If the string contains no digits, return it unchanged so a placeholder
    is not silently turned into an empty pocket.
    """
    numbers: list[str] = []
    for token in str(pocket).replace(";", ",").split(","):
        digits = re.findall(r"\d+", token)
        if digits:
            numbers.append(digits[-1])
    if numbers:
        return ",".join(numbers)
    return str(pocket)
