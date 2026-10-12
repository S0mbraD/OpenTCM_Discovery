"""Locations of local data files."""
from __future__ import annotations

import os
from pathlib import Path


def herb_index_candidates(repo_root: Path, env_value: str | None = None) -> list[Path]:
    """TCM_HERB_INDEX first, then the index shipped in the repository."""
    if env_value is None:
        env_value = os.environ.get("TCM_HERB_INDEX")
    paths: list[Path] = []
    if env_value:
        paths.append(Path(env_value))
    paths.append(Path(repo_root) / "data" / "herb_mol_index.json")
    return paths
