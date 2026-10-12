#!/usr/bin/env python3
"""bootstrap_layer: dependency self-check + skill-bundled runtime assets.

One tool:

  tcm_dependency_status - health check of EVERY runtime dependency with
                          per-missing-item impact analysis (which tools
                          break), exact install commands, and the bundled
                          binary inventory (bin/vina.exe). Optional
                          action="install" pip-installs selected missing
                          python deps into the RUNNING interpreter
                          (explicit opt-in; never implicit).

Dependency resolution design (vina): explicit param > VINA_EXE env >
skill-bundled bin/vina.exe > PATH - the skill is self-contained once
bin/vina.exe is present (bundled: AutoDock Vina 1.2.5 win binary).
"""
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

from pydantic import BaseModel, Field, ConfigDict

mcp = _MCPBase("tcm_bootstrap_layer")

SKILL_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_VINA = SKILL_ROOT / "bin" / "vina.exe"

_FNS: Dict[str, Callable] = {}


def attach_tools(fn_map: Dict[str, Callable]) -> None:
    _FNS.update(fn_map)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# (module, pip name, tier, tools that BREAK when missing)
_PY_DEPS = [
    ("mcp", "mcp", "required",
     ["unified_mcp (whole server)"]),
    ("httpx", "httpx", "required",
     ["all live-retrieval tools (tcm_pubchem_lookup, STRING, "
      "tcm_literature_mine, tcm_trials_match, ...)"]),
    ("pydantic", "pydantic", "required",
     ["all tools (typed params)"]),
    ("rdkit", "rdkit", "required",
     ["tcm_adme_profile", "tcm_classify_structure", "tcm_admet_predict",
      "dock_batch_screening ADME prefilter"]),
    ("numpy", "numpy", "required",
     ["tcm_graph_embed (SVD embedding)", "tcm_admet_predict (math)"]),
    ("matplotlib", "matplotlib", "needed",
     ["tcm_network_figure"]),
    ("PIL", "pillow", "needed",
     ["tcm_network_figure (PNG size report)"]),
    ("networkx", "networkx", "optional",
     ["tcm_network_figure layout (optional since FR fallback exists)"]),
    ("meeko", "meeko", "needed_docking",
     ["dock_vina_docking / dock_batch_screening (PDBQT prep)"]),
    ("gemmi", "gemmi", "optional",
     ["dock_fetch_structure PDB cleaning (script-level)"]),
]


def _py_dep_status() -> List[Dict[str, Any]]:
    rows = []
    for mod, pip_name, tier, breaks in _PY_DEPS:
        spec = importlib.util.find_spec(mod)
        version = None
        if spec:
            try:
                mod_obj = importlib.import_module(mod)
                version = getattr(mod_obj, "__version__", None)
            except Exception:
                version = None
        rows.append({
            "module": mod, "pip": pip_name, "tier": tier,
            "available": spec is not None, "version": version,
            "breaks_when_missing": breaks})
    return rows


def _vina_status() -> Dict[str, Any]:
    candidates = []
    if os.environ.get("VINA_EXE"):
        candidates.append(("VINA_EXE env", os.environ["VINA_EXE"]))
    if BUNDLED_VINA.exists():
        candidates.append(("skill-bundled bin/vina.exe", str(BUNDLED_VINA)))
    w = shutil.which("vina") or shutil.which("vina.exe")
    if w:
        candidates.append(("PATH", w))
    resolved = next(((src, p) for src, p in candidates
                     if Path(p).exists()), None)
    return {
        "resolved": resolved[1] if resolved else None,
        "resolved_source": resolved[0] if resolved else None,
        "bundled_present": BUNDLED_VINA.exists(),
        "bundled_path": str(BUNDLED_VINA),
        "env_var_set": bool(os.environ.get("VINA_EXE")),
        "breaks_when_missing": ["dock_vina_docking", "dock_batch_screening",
                                 "tcm_smart_run docking branch",
                                 "tcm_multi_path_run docking arms",
                                 "dock_rescore (no logs to parse)"]}


def _cli_status() -> List[Dict[str, Any]]:
    rows = []
    for name, label in (("mk_prepare_receptor", "meeko receptor CLI"),
                        ("mk_prepare_ligand", "meeko ligand CLI"),
                        ("pymol", "PyMOL CLI")):
        p = shutil.which(name)
        rows.append({"cli": name, "label": label, "available": bool(p),
                     "path": p})
    return rows


class DependencyStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(
        "status", description="status (default) | install (pip-install the "
                              "SELECTED missing python deps into the "
                              "running interpreter - explicit opt-in)")
    install_modules: Optional[List[str]] = Field(
        None, max_length=10,
        description="with action=install: module names to pip-install "
                    "(default: all missing required+needed tiers)")
    response_format: str = Field("markdown", description="markdown | json")


async def _pip_install(modules: List[str]) -> List[Dict[str, Any]]:
    """pip install selected modules into the RUNNING interpreter."""
    results = []
    pip = [sys.executable, "-m", "pip", "install", "--quiet"]
    for mod in modules:
        pip_name = next((d[1] for d in _PY_DEPS if d[0] == mod), mod)
        try:
            proc = await asyncio.create_subprocess_exec(
                *pip, pip_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            out, _ = await asyncio.wait_for(proc.communicate(), 300)
            results.append({
                "module": mod, "pip": pip_name, "rc": proc.returncode,
                "ok": proc.returncode == 0,
                "output_tail": out.decode(errors="ignore")[-200:]})
        except Exception as e:
            results.append({"module": mod, "pip": pip_name,
                            "ok": False, "error": f"{type(e).__name__}: {e}"})
    return results


@mcp.tool(
    name="tcm_dependency_status",
    annotations={"title": "Dependency Health Check (+opt-in installer)",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_dependency_status(params: DependencyStatusInput) -> str:
    '''Health-check every runtime dependency with impact analysis.

    Reports:
      - python deps (mcp/httpx/pydantic/rdkit/numpy/matplotlib/pillow/
        networkx/meeko/gemmi) with version + tier + WHICH TOOLS BREAK
        when each is missing + exact pip install command
      - vina executable resolution (param > VINA_EXE env > skill-bundled
        bin/vina.exe > PATH) with the bundled-binary inventory
      - meeko/PyMOL CLI availability
      - overall readiness verdict per functional area

    Optional action="install": pip-installs selected missing python
    modules INTO THE RUNNING INTERPRETTER (system modification - only
    runs when you explicitly pass action="install"). Bundled vina.exe
    needs no installation at all.

    Args:
        params (DependencyStatusInput): action=status | install, with
            install_modules selection for install.

    Returns:
        str: dependency table, impact matrix, install commands, verdict.

    Examples:
        - "Are all dependencies ready for docking?" -> action=status
        - "Install the missing python deps" -> action=install
    '''
    py_rows = _py_dep_status()
    vina = _vina_status()
    clis = _cli_status()
    install_results = None

    if params.action == "install":
        targets = params.install_modules or [
            r["module"] for r in py_rows
            if not r["available"] and r["tier"] in ("required", "needed")]
        install_results = (await _pip_install(targets) if targets
                           else [])
        py_rows = _py_dep_status()      # re-check after install

    missing_required = [r for r in py_rows
                        if not r["available"] and r["tier"] == "required"]
    missing_needed = [r for r in py_rows
                      if not r["available"] and r["tier"] == "needed"]

    verdict = {
        "retrieval_layer": not missing_required,
        "docking": (vina["resolved"] is not None
                    and any(c["available"] for c in clis
                            if c["cli"] == "mk_prepare_ligand")),
        "figures": all(r["available"] for r in py_rows
                       if r["module"] in ("matplotlib", "PIL")),
        "adme_layers": all(r["available"] for r in py_rows
                           if r["module"] in ("rdkit", "numpy")),
        "self_contained_vina": vina["bundled_present"]}

    data = {
        "python_deps": py_rows,
        "vina": vina,
        "clis": clis,
        "verdict": verdict,
        "install_results": install_results,
        "install_commands": {
            r["module"]: f"{sys.executable} -m pip install {r['pip']}"
            for r in py_rows if not r["available"]},
        "note": "run tcm_dependency_status(action='install') to "
                "auto-install missing python deps; bin/vina.exe is "
                "bundled and needs no installation",
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = ["# Dependency health check", ""]
    lines.append("## Python dependencies")
    lines.append("| module | tier | status | version |")
    lines.append("|---|---|---|---|")
    for r in py_rows:
        lines.append(f"| {r['module']} | {r['tier']} | "
                     f"{'OK' if r['available'] else 'MISSING'} | "
                     f"{r['version'] or '-'} |")
    lines.append("")
    lines.append("## vina executable")
    lines.append(f"- resolved: {vina['resolved'] or 'NOT FOUND'} "
                 f"({vina['resolved_source'] or 'no source'})")
    lines.append(f"- skill-bundled bin/vina.exe: "
                 f"{'present' if vina['bundled_present'] else 'MISSING'}")
    lines.append("")
    lines.append("## CLIs")
    for c in clis:
        lines.append(f"- {c['label']}: "
                     f"{'OK' if c['available'] else 'MISSING'}")
    if data["install_commands"]:
        lines.append("")
        lines.append("## Install commands (missing only)")
        for mod, cmd in data["install_commands"].items():
            lines.append(f"- {mod}: `{cmd}`")
        lines.append(f"- or: `{sys.executable} -m pip install -r "
                     f"{SKILL_ROOT / 'requirements.txt'}`")
    if install_results:
        lines.append("")
        lines.append("## Install results")
        for r in install_results:
            lines.append(f"- {r['module']}: "
                         f"{'installed' if r.get('ok') else 'FAILED'}")
    lines.append("")
    lines.append(f"## Verdict: {json.dumps(verdict)}")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)
