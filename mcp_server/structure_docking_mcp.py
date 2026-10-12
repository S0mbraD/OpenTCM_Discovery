#!/usr/bin/env python3
"""structure_docking_mcp: MCP server for the structure-docking-analysis pipeline.

Exposes the 6 battle-tested pipeline scripts (../scripts/) as AI-agent-callable
tools so agents can drive the full retrieval -> docking -> analysis -> evidence
loop without leaving the MCP protocol:

  dock_check_environment  - vina / meeko / pymol / rdkit availability check
  dock_fetch_structure    - PDB fetch+clean / PubChem 3D SDF / PDBe / SWISS-MODEL
  dock_vina_docking       - AutoDock Vina small-molecule docking (pocket-centered)
  dock_analyze_contacts   - contact residues + H-bond analysis on a complex PDB
  dock_geometric_ppi      - literature-constrained geometric protein-protein docking

Design: thin typed wrapper over subprocess calls to the scripts (they own all
validated logic); this server adds JSON parsing, timeouts and provenance stamps.
Every output carries `source` (script + database) and `queried_at`.

Chaining with the tcm-database-query skill (data circulation):
  tcm_cross_database_screen (compound) -> PubChem CID
  -> dock_fetch_structure (pubchem, 3D SDF)
  -> dock_vina_docking (vs. cleaned receptor)
  -> dock_analyze_contacts -> key residues -> UniProt/literature evidence.
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

from pydantic import BaseModel, Field, ConfigDict

mcp = _MCPBase("structure_docking_mcp")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
SKILL_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_SCRIPTS = ("fetch_structure.py", "run_vina.py", "analyze_contacts.py",
                    "geometric_docking.py", "make_figure_pml.py", "compose_panel.py")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt(resp_data: Dict, title: str) -> str:
    lines = [f"# {title}", ""]
    for k, v in resp_data.items():
        if isinstance(v, list):
            lines.append(f"## {k} ({len(v)})")
            for item in v:
                lines.append(f"- {json.dumps(item, ensure_ascii=False, default=str)[:300]}")
        elif isinstance(v, dict):
            lines.append(f"## {k}")
            lines.append(json.dumps(v, ensure_ascii=False, indent=1, default=str)[:1500])
        else:
            lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


async def _run_script_core(script_name: str, args: List[str],
                           timeout: float = 900.0) -> Dict[str, Any]:
    '''Run a pipeline script via subprocess; return {rc, stdout}.

    PYTHONUTF8=1 is forced so Chinese/Unicode output never crashes on Windows.
    '''
    script = SCRIPTS_DIR / script_name
    if not script.exists():
        raise FileNotFoundError(
            f"pipeline script not found: {script} (skill install incomplete?)")
    cmd = [sys.executable, "-u", str(script)] + [str(a) for a in args]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, env=env)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise TimeoutError(f"{script_name} exceeded {timeout:.0f}s "
                           f"(raise timeout_seconds for long dockings)")
    text = out.decode("utf-8", errors="replace")
    return {"rc": proc.returncode, "stdout": text}


async def _run_script(script_name: str, args: List[str],
                      timeout: float = 900.0) -> Dict[str, Any]:
    """Telemetry wrapper around _run_script_core: records script, rc,
    duration into the flight recorder for self-iteration analysis."""
    t0 = time.monotonic()
    try:
        res = await _run_script_core(script_name, args, timeout=timeout)
        _log_event({"ts": _now_iso(), "type": "script", "script": script_name,
                    "rc": res["rc"], "ok": res["rc"] == 0,
                    "ms": round((time.monotonic() - t0) * 1000)})
        return res
    except Exception as e:
        _log_event({"ts": _now_iso(), "type": "script", "script": script_name,
                    "error": type(e).__name__, "ok": False,
                    "ms": round((time.monotonic() - t0) * 1000)})
        raise


def _log_experiment(rec: Dict):
    """Append one experiment record to the ledger (docking runs, PPI models)."""
    rec = dict(rec)
    rec.setdefault("ts", _now_iso())
    _append_jsonl(EXPERIMENTS_PATH, rec)


def _json_from_text(text: str) -> Optional[Any]:
    '''Extract the first JSON document printed by a script (object or array).'''
    text = text.strip()
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except Exception:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start >= 0:
            depth, i = 0, start
            while i < len(text):
                if text[i] == opener:
                    depth += 1
                elif text[i] == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except Exception:
                            break
                i += 1
    return None


# ================= 运行时自迭代（2026-10-12） =================
# 飞行记录仪 + 实验台账 + 分层洞察库（与 tcm-database-query 机制对齐）。
# 状态目录默认 ~/.dock_mcp_state（可用 DOCK_MCP_STATE_DIR 覆盖），仅本地。

STATE_DIR = Path(os.environ.get("DOCK_MCP_STATE_DIR",
                                 str(Path.home() / ".dock_mcp_state")))
CALL_LOG_PATH = STATE_DIR / "call_log.jsonl"
EXPERIMENTS_PATH = STATE_DIR / "experiments.jsonl"
INSIGHTS_PATH = STATE_DIR / "insights.json"
_INSIGHT_CATS = ("environment", "method", "quirk", "performance", "repro")
_MAX_LOG_BYTES = 2_000_000
_insights_lock = asyncio.Lock()


def _state_init():
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _append_jsonl(path: Path, rec: Dict):
    """Append one record; rotate at 2MB; never raise."""
    try:
        _state_init()
        if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
            rot = path.with_suffix(path.suffix + ".1")
            try:
                rot.unlink()
            except Exception:
                pass
            path.replace(rot)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _log_event(rec: Dict):
    _append_jsonl(CALL_LOG_PATH, rec)


def _load_insights() -> Dict:
    try:
        if INSIGHTS_PATH.exists():
            with open(INSIGHTS_PATH, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {"version": 1, "insights": []}


def _save_insights(data: Dict):
    tmp = str(INSIGHTS_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, INSIGHTS_PATH)


async def _py_check(code: str, timeout: float = 60.0) -> Tuple[int, str]:
    '''Run a one-line python check in an isolated subprocess.'''
    cmd = [sys.executable, "-u", "-c", code]
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, env=env)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timeout"
    return proc.returncode, out.decode("utf-8", errors="replace")


# ================= Tool 1: environment check =================

@mcp.tool(
    name="dock_check_environment",
    annotations={"title": "Docking Environment Check (vina/meeko/pymol/rdkit)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def dock_check_environment() -> str:
    '''Check local dependencies for the docking pipeline before running it.

    Reports availability + path of: AutoDock Vina, Meeko receptor/ligand CLIs
    (mk_prepare_receptor / mk_prepare_ligand), PyMOL (figure rendering),
    RDKit (2D structures), and the 6 pipeline scripts shipped with this skill.
    Run this FIRST in any docking session to know which steps will work.

    Returns:
        str: per-dependency status with install hints for missing ones.
    '''
    def which(name: str) -> Dict[str, Any]:
        p = shutil.which(name)
        return {"available": bool(p), "path": p}

    # bundled binary fallback: <skill>/bin/vina.exe (self-contained)
    bundled_vina = SKILL_ROOT / "bin" / "vina.exe"
    vina_path = (shutil.which("vina") or
                 (str(bundled_vina) if bundled_vina.exists() else None))
    checks: Dict[str, Any] = {
        "vina": {"available": bool(vina_path), "path": vina_path,
                 "source": ("PATH" if shutil.which("vina") else
                            "skill-bundled bin/vina.exe" if vina_path
                            else None)},
        "meeko_mk_prepare_receptor": which("mk_prepare_receptor"),
        "meeko_mk_prepare_ligand": which("mk_prepare_ligand"),
        "pymol_cli": which("pymol"),
    }
    try:
        rc, out = await _py_check("import rdkit; print(rdkit.__version__)")
        checks["rdkit"] = {"available": rc == 0,
                           "version": out.strip().splitlines()[-1] if rc == 0 else None}
    except Exception as e:
        checks["rdkit"] = {"available": False, "error": str(e)[:100]}
    scripts = {s: (SCRIPTS_DIR / s).exists() for s in PIPELINE_SCRIPTS}
    missing = [k for k, v in checks.items() if not v.get("available")]
    ready = {
        "vina_docking": checks["vina"]["available"] and
        checks["meeko_mk_prepare_ligand"]["available"],
        "contact_analysis": True,           # pure stdlib
        "geometric_ppi": True,              # pure stdlib
        "figure_rendering": checks["pymol_cli"]["available"],
    }
    return _fmt({
        "dependencies": checks,
        "pipeline_scripts": scripts,
        "step_readiness": ready,
        "missing": missing,
        "install_hints": {
            "vina": "conda install -c conda-forge autodock-vina (or download "
                    "release binary and add to PATH)",
            "meeko": f"{sys.executable} -m pip install meeko gemmi",
            "pymol": "conda install -c conda-forge pymol-open-source",
            "rdkit": "conda install -c conda-forge rdkit"},
        "queried_at": _now_iso()},
        "Docking environment check")


# ================= Tool 2: structure fetch =================

class FetchStructureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(..., description="pdb | pdbinfo | pubchem | pdbe | swissmodel")
    query: str = Field(..., description="PDB id (e.g. 2Z64) | compound name/CID "
                                        "(e.g. berberine) | UniProt AC (e.g. P05109)")
    chains: Optional[str] = Field(
        None, description="[pdb] comma-separated chains to keep, e.g. A,C")
    drop_hetatm: bool = Field(False, description="[pdb] remove waters/glycans/ligands")
    out: Optional[str] = Field(None, description="output file (PDB for pdb / SDF for "
                                                 "pubchem); stdout if omitted")
    props: Optional[str] = Field(None, description="[pubchem] save compound props JSON")
    section: str = Field("summary", description="[pdbe] summary | ligands")
    limit: int = Field(10, ge=1, le=50, description="[swissmodel] max models")
    timeout_seconds: int = Field(300, ge=30, le=900)


@mcp.tool(
    name="dock_fetch_structure",
    annotations={"title": "Fetch/Clean Structures (PDB/PubChem/PDBe/SWISS-MODEL)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def dock_fetch_structure(params: FetchStructureInput) -> str:
    '''Fetch and clean structural data for docking.

    source=pdb: download a PDB entry, keep only chosen chains, optionally drop
    HETATM (waters/glycans) -> cleaned receptor file (out= path recommended).
    source=pdbinfo: entry metadata from the RCSB data API (resolution, method).
    source=pubchem: compound properties + 3D SDF (correct stereochemistry) - the
    ligand input for Vina. Chains the tcm-database-query skill's screening
    output (PubChem CID/name) into docking.
    source=pdbe: PDBe (EBI) entry summary or ligand inventory.
    source=swissmodel: homology models for a UniProt accession (no PDB?).

    Args:
        params (FetchStructureInput): source + query + per-source options.

    Returns:
        str: parsed JSON from the fetch script + saved file paths.

    Examples:
        - "Get cleaned TLR4/MD-2 complex" -> source=pdb, query=2Z64,
          chains="A,C", out="2Z64_clean.pdb"
        - "Get berberine 3D structure" -> source=pubchem, query=berberine,
          out="berberine.sdf"
    '''
    try:
        args = [params.source, params.query]
        if params.source == "pdb":
            if params.chains:
                args += ["--chains", params.chains]
            if params.drop_hetatm:
                args.append("--drop-hetatm")
            if params.out:
                args += ["--out", params.out]
        elif params.source == "pdbinfo":
            if params.out:
                args += ["--out", params.out]
        elif params.source == "pubchem":
            if params.out:
                args += ["--out", params.out]
            if params.props:
                args += ["--props", params.props]
        elif params.source == "pdbe":
            args += ["--section", params.section]
            if params.out:
                args += ["--out", params.out]
        elif params.source == "swissmodel":
            args += ["--limit", str(params.limit)]
            if params.out:
                args += ["--out", params.out]
        else:
            return (f"Error: source must be one of "
                    f"pdb|pdbinfo|pubchem|pdbe|swissmodel (got {params.source}).")
        res = await _run_script("fetch_structure.py", args,
                                timeout=params.timeout_seconds)
        payload = _json_from_text(res["stdout"])
        saved = [p for p in (params.out, params.props) if p]
        return _fmt({
            "source": params.source, "query": params.query,
            "exit_code": res["rc"],
            "data": payload if payload is not None else
            res["stdout"][-2000:],   # raw tail when not JSON
            "saved_files": saved,
            "script": "fetch_structure.py (RCSB/PubChem/PDBe/SWISS-MODEL live)",
            "queried_at": _now_iso()},
            f"Structure fetch: {params.source}:{params.query}")
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:250]}"


# ================= Tool 3: Vina docking =================

class VinaDockingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receptor: str = Field(..., description="cleaned receptor PDB (no waters); "
                                           "produce via dock_fetch_structure source=pdb")
    ligand: str = Field(..., description="ligand 3D SDF; produce via "
                                         "dock_fetch_structure source=pubchem")
    pocket_chain: str = Field(..., description="chain ID of pocket residues, e.g. C")
    pocket_residues: str = Field(..., description="comma-separated residue numbers "
                                                  "defining the pocket, e.g. 61,63,92,105")
    box_size: float = Field(30.0, ge=10, le=40, description="cubic box edge (A)")
    box_center: Optional[str] = Field(None, description="explicit 'x,y,z' "
                                                        "(overrides pocket centroid)")
    exhaustiveness: int = Field(16, ge=1, le=64)
    num_modes: int = Field(9, ge=1, le=20)
    seed: int = Field(42)
    workdir: str = Field(..., description="absolute output directory (created)")
    vina_path: Optional[str] = Field(None, description="explicit vina executable")
    timeout_seconds: int = Field(1800, ge=60, le=7200)


@mcp.tool(
    name="dock_vina_docking",
    annotations={"title": "AutoDock Vina Small-Molecule Docking",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def dock_vina_docking(params: VinaDockingInput) -> str:
    '''Run AutoDock Vina docking of a ligand into a receptor pocket.

    Full chain: meeko receptor/ligand PDBQT preparation -> pocket centroid from
    CA atoms of the given residues (or explicit box center) -> Vina search
    (exhaustiveness default 16) -> best pose converted to a strictly-formatted
    ligand PDB (chain L, resname LIG) ready for contact analysis and PyMOL.

    Scoring: AutoDock Vina 1.x/2.x empirical score (sterics + H-bond +
    hydrophobic + torsion penalties), kcal/mol - LOWER is better.

    Args:
        params (VinaDockingInput): receptor + ligand + pocket + workdir + params.

    Returns:
        str: per-mode affinity table, best affinity, and output file paths
        (docked.pdbqt / best_pose.pdb / vina_log.txt under workdir).

    Examples:
        - "Dock berberine into MD-2 pocket" -> receptor=2Z64_clean.pdb,
          ligand=berberine.sdf, pocket_chain=C,
          pocket_residues="61,63,92,105,118,124", workdir="E:/docking/MD2_berberine"
    '''
    try:
        os.makedirs(params.workdir, exist_ok=True)
        args = ["--receptor", params.receptor, "--ligand", params.ligand,
                "--pocket-chain", params.pocket_chain,
                "--pocket-residues", params.pocket_residues,
                "--box-size", str(params.box_size),
                "--exhaustiveness", str(params.exhaustiveness),
                "--num-modes", str(params.num_modes),
                "--seed", str(params.seed), "--workdir", params.workdir]
        if params.box_center:
            args += ["--box-center", params.box_center]
        if params.vina_path:
            args += ["--vina", params.vina_path]
        res = await _run_script("run_vina.py", args, timeout=params.timeout_seconds)
        if res["rc"] != 0:
            return (f"Error: run_vina.py exited with {res['rc']}.\n"
                    f"Output tail:\n{res['stdout'][-800:]}")
        modes = []
        best = None
        for line in res["stdout"].splitlines():
            m = re.match(r"\s*mode\s+(\d+):\s+(-?[\d.]+)", line)
            if m:
                modes.append({"mode": int(m.group(1)),
                              "affinity_kcal_mol": float(m.group(2))})
            mb = re.search(r"Best affinity:\s*(-?[\d.]+)\s*kcal/mol", line)
            if mb:
                best = float(mb.group(1))
        spread = (abs(modes[0]["affinity_kcal_mol"] -
                      modes[-1]["affinity_kcal_mol"])
                  if len(modes) > 1 else None)
        out_files = {name: os.path.join(params.workdir, name)
                     for name in ("docked.pdbqt", "best_pose.pdb", "vina_log.txt")}
        # experiment ledger (self-iteration: benchmarking & reproducibility)
        _log_experiment({
            "type": "vina_docking",
            "receptor": os.path.basename(params.receptor),
            "ligand": os.path.basename(params.ligand),
            "pocket_chain": params.pocket_chain,
            "pocket_residues": params.pocket_residues,
            "box_size": params.box_size,
            "exhaustiveness": params.exhaustiveness,
            "seed": params.seed,
            "best_affinity_kcal_mol": best,
            "mode_spread_kcal_mol": round(spread, 2) if spread is not None else None,
            "n_modes": len(modes),
            "workdir": params.workdir})
        return _fmt({
            "receptor": params.receptor, "ligand": params.ligand,
            "pocket": f"chain {params.pocket_chain}: {params.pocket_residues}",
            "best_affinity_kcal_mol": best,
            "modes": modes,
            "mode_spread_kcal_mol": round(spread, 2) if spread is not None else None,
            "output_files": out_files,
            "next_step": "dock_analyze_contacts on the merged complex "
                         "(receptor + best_pose.pdb) for contact residues; "
                         "label results as 'proposed docking model'",
            "method": "AutoDock Vina (meeko PDBQT prep, pocket-centroid box)",
            "queried_at": _now_iso()},
            f"Vina docking: {os.path.basename(params.ligand)} -> "
            f"{os.path.basename(params.receptor)}")
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:250]}"


# ================= Tool 4: contact analysis =================

class ContactsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complex_pdb: str = Field(..., description="complex PDB containing the ligand chain "
                                              "(merge receptor + pose, see merge_ligand)")
    ligand_chain: str = Field(..., description="chain ID of the small-molecule ligand, "
                                               "e.g. L")
    receptor_chains: str = Field(..., description="comma-separated receptor chains, "
                                                  "e.g. A,C")
    cutoff: float = Field(4.5, ge=3.0, le=8.0, description="contact distance (A)")
    hbond_cutoff: float = Field(3.5, ge=2.5, le=4.0, description="H-bond distance (A)")
    merge_ligand: Optional[str] = Field(
        None, description="optional ligand pose PDB to merge INTO complex_pdb first "
                          "(analyze_contacts.py merge subcommand), e.g. best_pose.pdb")
    workdir: str = Field(..., description="output directory for JSON results")
    timeout_seconds: int = Field(300, ge=30, le=1200)


@mcp.tool(
    name="dock_analyze_contacts",
    annotations={"title": "Contact Residue + H-bond Analysis",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def dock_analyze_contacts(params: ContactsInput) -> str:
    '''Analyze protein-ligand contacts and hydrogen bonds in a complex.

    Detects receptor residues within the contact cutoff of the ligand (per-atom
    minimum distances) and candidate H-bonds (donor-acceptor N/O pairs within
    hbond_cutoff). Optionally first merges a docking pose PDB (best_pose.pdb)
    into the receptor complex via the merge subcommand.

    Output feeds the evidence loop: predicted contacts should be marked with *
    and dashed lines in figures, and cross-checked against literature (see the
    tcm-database-query skill's UniProt/literature channels).

    Args:
        params (ContactsInput): complex + chains + cutoffs + workdir.

    Returns:
        str: contact residues with distances, H-bond candidates, JSON path.

    Examples:
        - "Analyze berberine pose contacts" -> complex_pdb=2Z64_clean.pdb,
          ligand_chain=L, receptor_chains="A,C",
          merge_ligand=best_pose.pdb, workdir="E:/docking/MD2_berberine"
    '''
    try:
        os.makedirs(params.workdir, exist_ok=True)
        complex_pdb = params.complex_pdb
        if params.merge_ligand:
            merged = os.path.join(params.workdir, "merged_complex.pdb")
            res = await _run_script(
                "analyze_contacts.py",
                ["merge", "--complex", params.complex_pdb,
                 "--ligand", params.merge_ligand, "--out", merged],
                timeout=params.timeout_seconds)
            if res["rc"] != 0:
                return (f"Error: merge step failed ({res['rc']}):\n"
                        f"{res['stdout'][-500:]}")
            complex_pdb = merged
        out_json = os.path.join(params.workdir, "contacts.json")
        res = await _run_script(
            "analyze_contacts.py",
            ["analyze", "--pdb", complex_pdb,
             "--ligand-chain", params.ligand_chain,
             "--receptor-chains", params.receptor_chains,
             "--cutoff", str(params.cutoff),
             "--hbond", str(params.hbond_cutoff),
             "--out", out_json],
            timeout=params.timeout_seconds)
        if res["rc"] != 0:
            return (f"Error: analyze step failed ({res['rc']}):\n"
                    f"{res['stdout'][-500:]}")
        payload = None
        if os.path.exists(out_json):
            try:
                with open(out_json, encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                payload = None
        return _fmt({
            "complex": complex_pdb,
            "ligand_chain": params.ligand_chain,
            "receptor_chains": params.receptor_chains,
            "cutoffs": {"contact": params.cutoff, "hbond": params.hbond_cutoff},
            "results": payload if payload is not None else res["stdout"][-2000:],
            "json_path": out_json,
            "evidence_note": "computational predictions - label as 'proposed "
                             "docking model' and cross-check key residues with "
                             "UniProt/literature before publication",
            "script": "analyze_contacts.py (local geometric analysis)",
            "queried_at": _now_iso()},
            f"Contact analysis: {os.path.basename(complex_pdb)}")
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:250]}"


# ================= Tool 5: geometric PPI docking =================

class GeometricPPIInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receptor: str = Field(..., description="cleaned receptor PDB (single or "
                                           "multi-chain protein)")
    ligand: str = Field(..., description="ligand PROTEIN PDB (single chain)")
    ligand_chain: Optional[str] = Field(None, description="chain to use from "
                                                          "ligand PDB")
    epitope: str = Field(..., description="binding epitope residue range on the "
                                          "ligand, e.g. 68-77 (faces receptor)")
    key_residues: str = Field(..., description="comma-separated ligand residues that "
                                               "must stay near the receptor, e.g. 73,74,75,77")
    surface_region: Optional[str] = Field(
        None, description="ligand region expected at the interface, e.g. 60-89 "
                          "(scoring bonus)")
    center: Optional[str] = Field(None, description="explicit interface center 'x,y,z'")
    search_radius: float = Field(28.0, ge=10, le=60)
    min_dist: float = Field(2.5, ge=1.5, le=5.0, description="hard clash threshold (A)")
    spins: int = Field(12, ge=4, le=48)
    gaps: str = Field("1.5,3.0,4.5,6.0,7.5")
    out: str = Field(..., description="output complex PDB path")
    workdir: str = Field(..., description="directory for the JSON report")
    timeout_seconds: int = Field(1800, ge=60, le=7200)


@mcp.tool(
    name="dock_geometric_ppi",
    annotations={"title": "Geometric Protein-Protein Docking (literature-constrained)",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def dock_geometric_ppi(params: GeometricPPIInput) -> str:
    '''Run literature-constrained geometric docking of two proteins.

    Surface-point sampling + spatial-grid scoring (NOT energy minimization):
    hard constraint = key residues within min_dist of the receptor; solutions
    ranked by key-residue distances. Use when no experimental complex exists
    but literature defines the interface (e.g. S100A8 onto TLR4/MD-2 with
    residues I73/L74/V75/K77).

    IMPORTANT: results are geometric hypotheses - always label as 'proposed
    docking model' and distinguish predicted residues (*, dashed lines) from
    experimental evidence in figures and text.

    Args:
        params (GeometricPPIInput): receptor + ligand + epitope + key residues
        + output paths.

    Returns:
        str: best transform, key-residue distances, output complex + report JSON.

    Examples:
        - "Dock S100A8 onto MD-2" -> receptor=TLR4_MD2_clean.pdb,
          ligand=S100A8.pdb, epitope="68-77",
          key_residues="73,74,75,77", out="complex.pdb", workdir="E:/ppi"
    '''
    try:
        os.makedirs(params.workdir, exist_ok=True)
        report = os.path.join(params.workdir, "geometric_report.json")
        args = ["--receptor", params.receptor, "--ligand", params.ligand,
                "--epitope", params.epitope,
                "--key-residues", params.key_residues,
                "--search-radius", str(params.search_radius),
                "--min-dist", str(params.min_dist),
                "--spins", str(params.spins), "--gaps", params.gaps,
                "--out", params.out, "--report", report]
        if params.ligand_chain:
            args += ["--ligand-chain", params.ligand_chain]
        if params.surface_region:
            args += ["--surface-region", params.surface_region]
        if params.center:
            args += ["--center", params.center]
        res = await _run_script("geometric_docking.py", args,
                                timeout=params.timeout_seconds)
        if res["rc"] != 0:
            return (f"Error: geometric_docking.py exited with {res['rc']}:\n"
                    f"{res['stdout'][-800:]}")
        payload = None
        if os.path.exists(report):
            try:
                with open(report, encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception:
                payload = None
        # experiment ledger (self-iteration)
        _log_experiment({
            "type": "geometric_ppi",
            "receptor": os.path.basename(params.receptor),
            "ligand": os.path.basename(params.ligand),
            "epitope": params.epitope,
            "key_residues": params.key_residues,
            "search_radius": params.search_radius,
            "spins": params.spins,
            "out": params.out,
            "report": payload if isinstance(payload, dict) else
                      {"report_file": report}})
        return _fmt({
            "receptor": params.receptor, "ligand": params.ligand,
            "epitope": params.epitope, "key_residues": params.key_residues,
            "results": payload if payload is not None else res["stdout"][-2000:],
            "complex_pdb": params.out,
            "report_json": report,
            "evidence_note": "geometric hypothesis, NOT energy-based; label "
                             "'proposed docking model', mark predicted residues "
                             "with * and dashed lines",
            "script": "geometric_docking.py (surface sampling + grid scoring)",
            "queried_at": _now_iso()},
            f"Geometric PPI docking: {os.path.basename(params.ligand)} -> "
            f"{os.path.basename(params.receptor)}")
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:250]}"


# ================= 自迭代工具（2026-10-12 新增） =================

class DockReportInsightInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = Field(...,
                         description="environment | method | quirk | performance | repro")
    title: str = Field(..., min_length=3, max_length=120)
    detail: str = Field(..., min_length=3, max_length=2000)
    evidence: str = Field("", max_length=2000)


@mcp.tool(
    name="dock_report_insight",
    annotations={"title": "Record a Docking/Environment Insight (self-iteration)",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": False})
async def dock_report_insight(params: DockReportInsightInput) -> str:
    '''Persist a lesson learned while docking into the local insight store.

    Categories: environment (vina/meeko/pymol setup quirks), method
    (parameter/scoring learnings), quirk (structure/PDB oddities), performance
    (timing), repro (reproducibility findings, e.g. seed sensitivity).
    Insights feed dock_analyze_runtime's proposed updates and are weighted by
    repeat hits. Local file store (~/.dock_mcp_state), survives restarts,
    works on any agent host.

    Examples:
        - "Vina exhaustiveness 16 vs 8 gave identical best pose for LA" ->
          category="repro", title="Vina exh 16 vs 8 same pose LA/MD-2"
    '''
    try:
        cat = params.category.strip().lower()
        if cat not in _INSIGHT_CATS:
            return f"Error: category must be one of {_INSIGHT_CATS}."
        async with _insights_lock:
            data = _load_insights()
            ins = data.setdefault("insights", [])
            for e in ins:
                if e.get("category") == cat and e.get("title") == params.title.strip():
                    e["hits"] = int(e.get("hits", 0)) + 1
                    e["last_hit"] = _now_iso()
                    if params.evidence:
                        e["evidence"] = params.evidence
                    _save_insights(data)
                    return _fmt({"action": "insight_merged", "id": e["id"],
                                 "hit_count": e["hits"], "store": str(INSIGHTS_PATH),
                                 "queried_at": _now_iso()},
                                f"Insight reinforced: {e['title'][:60]}")
            iid = "DIN-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + \
                "-" + os.urandom(3).hex()
            entry = {"id": iid, "category": cat, "title": params.title.strip(),
                     "detail": params.detail.strip(),
                     "evidence": params.evidence.strip(),
                     "created_at": _now_iso(), "hits": 1, "last_hit": _now_iso()}
            ins.insert(0, entry)
            _save_insights(data)
        return _fmt({"action": "insight_recorded", "id": iid, "category": cat,
                     "title": entry["title"], "store": str(INSIGHTS_PATH),
                     "queried_at": _now_iso()},
                    f"Insight recorded: {entry['title'][:60]}")
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:200]}"


class DockAnalyzeRuntimeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_events: int = Field(5000, ge=100, le=50000)


@mcp.tool(
    name="dock_analyze_runtime",
    annotations={"title": "Analyze Docking Runtime (telemetry + ledger + insights)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def dock_analyze_runtime(params: DockAnalyzeRuntimeInput) -> str:
    '''Analyze accumulated docking runs: script health, experiment stats,
    reproducibility flags, recorded insights -> proposed skill updates.

    Aggregates the flight recorder per pipeline script (success rate, avg
    duration) and the experiment ledger per ligand+receptor pair (best
    affinity range across seeds/runs - wide spread on same seed flags
    reproducibility problems). Merges dock_report_insight entries and emits
    proposed_updates for the platform's Skill-evolution flow.

    Examples:
        - "How are our docking runs doing?" -> defaults
    '''
    try:
        events = []
        if CALL_LOG_PATH.exists():
            with open(CALL_LOG_PATH, encoding="utf-8") as f:
                lines = f.readlines()
            for ln in lines[-params.max_events:]:
                try:
                    events.append(json.loads(ln))
                except Exception:
                    continue
        per_script: Dict[str, Dict] = {}
        for ev in events:
            if ev.get("type") != "script":
                continue
            s = per_script.setdefault(ev.get("script") or "?",
                                      {"runs": 0, "ok": 0, "ms_total": 0.0,
                                       "last": None})
            s["runs"] += 1
            s["ms_total"] += float(ev.get("ms") or 0)
            s["last"] = ev.get("ts")
            if ev.get("ok"):
                s["ok"] += 1
        script_rows = [{"script": k, "runs": v["runs"],
                        "ok_rate": round(v["ok"] / v["runs"], 3) if v["runs"] else 0,
                        "avg_ms": round(v["ms_total"] / v["runs"]) if v["runs"] else 0,
                        "last_seen": v["last"]}
                       for k, v in sorted(per_script.items(),
                                          key=lambda kv: -kv[1]["runs"])]
        # experiment ledger stats
        experiments = []
        if EXPERIMENTS_PATH.exists():
            with open(EXPERIMENTS_PATH, encoding="utf-8") as f:
                for ln in f:
                    try:
                        experiments.append(json.loads(ln))
                    except Exception:
                        continue
        pair_stats: Dict[str, List[float]] = {}
        for ex in experiments:
            if ex.get("type") != "vina_docking" or ex.get("best_affinity_kcal_mol") is None:
                continue
            key = f"{ex.get('ligand')} -> {ex.get('receptor')}"
            pair_stats.setdefault(key, []).append(float(ex["best_affinity_kcal_mol"]))
        vina_rows = []
        repro_flags = []
        for key, vals in pair_stats.items():
            vina_rows.append({"pair": key, "n_runs": len(vals),
                              "best": min(vals), "worst": max(vals),
                              "spread": round(max(vals) - min(vals), 3)})
            same_seed = [ex for ex in experiments
                         if ex.get("type") == "vina_docking" and
                         f"{ex.get('ligand')} -> {ex.get('receptor')}" == key and
                         ex.get("seed") is not None]
            seeds = {}
            for ex in same_seed:
                seeds.setdefault(str(ex.get("seed")), []).append(
                    ex.get("best_affinity_kcal_mol"))
            for seed, vals2 in seeds.items():
                if len(vals2) >= 2 and (max(vals2) - min(vals2)) > 0.1:
                    repro_flags.append({"pair": key, "seed": seed,
                                         "spread": round(max(vals2) - min(vals2), 3),
                                         "note": "same-seed spread >0.1 kcal/mol - "
                                                 "check receptor prep / box "
                                                 "determinism"})
        insights = _load_insights().get("insights", [])[:20]
        proposals = []
        for r in script_rows:
            if r["ok_rate"] < 0.5 and r["runs"] >= 3:
                proposals.append(
                    f"[script health] {r['script']} ok_rate {r['ok_rate']} -> "
                    f"check its dependencies (dock_check_environment) and "
                    f"record findings via dock_report_insight")
        for rf in repro_flags:
            proposals.append(
                f"[reproducibility] {rf['pair']} seed {rf['seed']} spread "
                f"{rf['spread']} -> investigate; if systematic, record via "
                f"dock_report_insight(category=repro)")
        for ins in insights[:10]:
            if ins.get("hits", 1) >= 2:
                proposals.append(
                    f"[insight x{ins['hits']}] {ins['title']} -> fold into "
                    f"references/workflow.md pitfalls section")
        if not proposals:
            proposals.append("no update proposals - pipeline healthy")
        return _fmt({
            "state_dir": str(STATE_DIR),
            "events_analyzed": len(events),
            "script_health": script_rows,
            "vina_experiment_stats": vina_rows[:15],
            "reproducibility_flags": repro_flags,
            "total_experiments": len(experiments),
            "recorded_insights": insights,
            "proposed_updates": proposals,
            "iteration_note": "proposals feed the platform Skill-evolution flow "
                               "(user-confirmed); compare runs via "
                               "dock_compare_experiments",
            "queried_at": _now_iso()},
            f"Docking runtime analysis ({len(events)} events, "
            f"{len(experiments)} experiments)")
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:200]}"


class CompareExperimentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ligand: Optional[str] = Field(None, description="filter by ligand file name")
    receptor: Optional[str] = Field(None, description="filter by receptor file name")
    exp_type: str = Field("vina_docking",
                          description="vina_docking | geometric_ppi | all")
    limit: int = Field(20, ge=1, le=100)


@mcp.tool(
    name="dock_compare_experiments",
    annotations={"title": "Compare Docking Experiments (ledger query)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def dock_compare_experiments(params: CompareExperimentsInput) -> str:
    '''Query the experiment ledger to compare docking runs across ligands,
    receptors, seeds or exhaustiveness - the scientific self-iteration loop.

    Every dock_vina_docking / dock_geometric_ppi run is recorded locally
    (ligand, receptor, pocket, seed, exhaustiveness, best affinity, mode
    spread). This tool groups and ranks them: pick the best binder, spot
    seed sensitivity, or compare candidates before committing to deep
    rendering/figures.

    Examples:
        - "Compare all runs against 2Z64_clean.pdb" -> receptor="2Z64_clean.pdb"
        - "Show every LA docking run" -> ligand="LA.sdf"
    '''
    try:
        experiments = []
        if EXPERIMENTS_PATH.exists():
            with open(EXPERIMENTS_PATH, encoding="utf-8") as f:
                for ln in f:
                    try:
                        experiments.append(json.loads(ln))
                    except Exception:
                        continue
        if params.exp_type != "all":
            experiments = [e for e in experiments
                           if e.get("type") == params.exp_type]
        if params.ligand:
            experiments = [e for e in experiments
                           if params.ligand.lower() in str(e.get("ligand", "")).lower()]
        if params.receptor:
            experiments = [e for e in experiments
                           if params.receptor.lower() in str(e.get("receptor", "")).lower()]
        vina = [e for e in experiments if e.get("type") == "vina_docking"
                and e.get("best_affinity_kcal_mol") is not None]
        vina_sorted = sorted(vina, key=lambda e: float(e["best_affinity_kcal_mol"]))
        ranked = [{"ligand": e.get("ligand"), "receptor": e.get("receptor"),
                   "best_affinity_kcal_mol": e["best_affinity_kcal_mol"],
                   "seed": e.get("seed"),
                   "exhaustiveness": e.get("exhaustiveness"),
                   "mode_spread": e.get("mode_spread_kcal_mol"),
                   "ts": e.get("ts")} for e in vina_sorted[:params.limit]]
        ppi = [e for e in experiments if e.get("type") == "geometric_ppi"][-params.limit:]
        return _fmt({
            "filters": {"ligand": params.ligand, "receptor": params.receptor,
                        "type": params.exp_type},
            "n_matched": len(experiments),
            "vina_ranked_by_affinity": ranked,
            "geometric_ppi_runs": ppi,
            "note": "lower (more negative) affinity = stronger predicted "
                    "binding; compare same-receptor runs only; label all as "
                    "'proposed docking model'",
            "queried_at": _now_iso()},
            f"Experiment comparison ({len(experiments)} runs, "
            f"top {len(ranked)} by affinity)")
    except Exception as e:
        return f"Error: {type(e).__name__}: {str(e)[:200]}"


# ================= 入口 =================

def main():
    ap = argparse.ArgumentParser(description="structure_docking_mcp server")
    ap.add_argument("--port", type=int, default=8001,
                    help="StreamableHTTP port (default 8001)")
    ap.add_argument("--stdio", action="store_true",
                    help="Run with stdio transport instead of HTTP")
    args = ap.parse_args()

    if args.stdio:
        mcp.run()
    else:
        try:
            mcp.run(transport="streamable-http", port=args.port, host="127.0.0.1")
        except ValueError:
            mcp.run(transport="streamable_http", port=args.port, host="127.0.0.1")


if __name__ == "__main__":
    main()
