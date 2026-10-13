#!/usr/bin/env python3
"""Repository consistency check. No network, no third-party imports.

Verifies Python syntax, the 62 MCP tool registrations, and that user-facing
docs do not revive retired counts, ports, or machine-local paths.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MCP = ROOT / "mcp_server"
EXPECTED = 62

TOOL_RE = re.compile(
    r'@mcp\.tool\((?:(?!@mcp\.tool).){0,800}?name\s*=\s*"([^"]+)"',
    re.S,
)

# Historical narrative may mention old ports and old tool counts.
SKIP_PHRASES = {
    ROOT / "CHANGELOG.md",
    ROOT / "references" / "iteration_history.md",
    ROOT / "scripts" / "check_repo.py",
}

BANNED = (
    "sktool",
    "53 工具",
    "37-tool",
    "单MCP 45",
    "networkx spring",
    "默认 127.0.0.1:8000",
    "默认端口 8000）：",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def check_syntax() -> list[str]:
    errors = []
    for path in ROOT.rglob("*.py"):
        if any(part in {".git", "__pycache__", ".venv", "venv", "OpenTCM_Discovery"}
               for part in path.parts):
            continue
        try:
            ast.parse(_read(path), filename=str(path))
        except SyntaxError as exc:
            errors.append(f"syntax {path.relative_to(ROOT)}: {exc}")
    return errors


def tool_names() -> list[str]:
    found: list[str] = []
    for path in sorted(MCP.glob("*.py")):
        found.extend(TOOL_RE.findall(_read(path)))
    return found


def check_tools() -> list[str]:
    names = tool_names()
    errors = []
    if len(names) != EXPECTED:
        errors.append(f"expected {EXPECTED} tools, found {len(names)}")
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        errors.append("duplicate tool names: " + ", ".join(dupes))
    catalog = _read(ROOT / "docs" / "TOOLS.md") if (ROOT / "docs" / "TOOLS.md").exists() else ""
    missing = [n for n in names if f"`{n}`" not in catalog]
    if missing:
        errors.append("docs/TOOLS.md missing: " + ", ".join(missing))
    skill = _read(ROOT / "SKILL.md")
    if "62" not in skill.split("---", 2)[2]:
        errors.append("SKILL.md body does not mention 62 tools")
    if "prep_layer.py" not in _read(MCP / "unified_mcp.py"):
        errors.append("unified_mcp.py does not load prep_layer.py")
    if "tcm_self_iterate" not in _read(MCP / "unified_mcp.py"):
        errors.append("unified_mcp.py does not load tcm_self_iterate")
    if "tcm_analysis_topology" not in _read(MCP / "unified_mcp.py"):
        errors.append("unified_mcp.py does not load tcm_analysis_topology")
    graph_src = _read(ROOT / "tcm_core" / "analysis_graph.py")
    if "共现不是因果" not in graph_src or "集合重叠不是药效" not in graph_src:
        errors.append("analysis graph lost the evidence-limit labels")
    if "build_from_session" not in _read(MCP / "smart_layer.py"):
        errors.append("smart_layer.py does not build the analysis graph")
    if "references/runtime_lessons.md" not in _read(MCP / "iteration_layer.py"):
        errors.append("iteration_layer.py lost the lesson file target")
    formula = _read(MCP / "lit_formula_layer.py")
    paths = _read(ROOT / "tcm_core" / "paths.py")
    if "herb_index_candidates" not in formula:
        errors.append("lit_formula_layer.py does not use herb_index_candidates")
    if 'repo_root) / "data" / "herb_mol_index.json"' not in paths and \
            '"data" / "herb_mol_index.json"' not in paths:
        errors.append("tcm_core.paths does not point at data/herb_mol_index.json")
    if "_defer_full_herb_workflow" not in _read(MCP / "smart_layer.py"):
        errors.append("smart_layer.py lost the depth=full deferral")
    unified = _read(MCP / "unified_mcp.py")
    if "default=8002" not in unified:
        errors.append("unified_mcp.py default port is not 8002")
    return errors


def check_phrases() -> list[str]:
    errors = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".md", ".py", ".json", ".yml", ".yaml"}:
            continue
        if any(part in {".git", "__pycache__", "OpenTCM_Discovery"} for part in path.parts):
            continue
        if path in SKIP_PHRASES:
            continue
        text = _read(path)
        rel = path.relative_to(ROOT).as_posix()
        for phrase in BANNED:
            if phrase in text:
                errors.append(f"{rel} contains retired phrase: {phrase}")
    return errors


def main() -> int:
    errors = check_syntax() + check_tools() + check_phrases()
    if errors:
        print("check_repo: FAIL")
        for item in errors:
            print(" -", item)
        return 1
    print(f"check_repo: OK ({EXPECTED} tools, syntax, docs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
