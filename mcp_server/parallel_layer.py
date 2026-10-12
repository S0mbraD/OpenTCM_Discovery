#!/usr/bin/env python3
"""parallel_layer: parallel & multi-path analysis extension for the unified skill.

Four tools complementing the sequential smart_layer engine:

  tcm_adme_profile       - batch physicochemical property profiling
                           (MW/TPSA/LogP/HBD/HBA/RotB/aromatic rings +
                           Lipinski/Veber/Egan rules + QED), RDKit-based,
                           live PubChem SMILES resolution by name
  tcm_network_simulation - network perturbation simulation: single-gene
                           knockout sweep, cumulative knockout of top-N hubs,
                           Monte-Carlo random-removal controls with z-scores,
                           global-efficiency/robustness metrics
  dock_batch_screening   - PARALLEL multi-ligand virtual screening against
                           one receptor (asyncio.gather + worker semaphore;
                           each Vina runs as its own subprocess), ranked
                           output, top-N shortlist for contact analysis
  tcm_multi_path_run     - multi-arm parallel analysis network: every
                           candidate compound runs its own chain
                           (consensus -> ADME -> optional docking) in
                           parallel, then a horizontal comparison table and
                           rule-based composite ranking

Disciplines carried over: live retrieval with provenance, rule-based
ranking is explicitly NOT a validated scoring function, docking outputs
remain 'proposed docking model', atomic writes only.
"""
import asyncio
import json
import os
import random
import re
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

from pydantic import BaseModel, Field, ConfigDict

mcp = _MCPBase("tcm_parallel_layer")

STATE_ROOT = Path(os.environ.get(
    "TCM_MCP_STATE_DIR", str(Path.home() / ".tcm_mcp_state")))
SESSIONS_DIR = STATE_ROOT / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

_FNS: Dict[str, Callable] = {}


def attach_tools(fn_map: Dict[str, Callable]) -> None:
    _FNS.update(fn_map)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _atomic_write_json(path: Path, data: Dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1,
                              default=str), encoding="utf-8")
    os.replace(tmp, path)


async def _invoke(tool: str, timeout: float, **kwargs) -> Dict[str, Any]:
    """Call an attached tool fn (params wrapper, JSON mode preferred)."""
    fn = _FNS.get(tool)
    if fn is None:
        return {"status": "error", "error": f"tool {tool} not available"}
    import inspect
    try:
        ann = inspect.signature(fn).parameters["params"].annotation
    except (KeyError, TypeError):
        ann = None
    call_kwargs = dict(kwargs)
    try:
        if ann is not None and "response_format" in ann.model_fields:
            call_kwargs.setdefault("response_format", "json")
    except AttributeError:
        pass
    try:
        obj = ann(**call_kwargs) if ann is not None else None
        if obj is not None:
            raw = await asyncio.wait_for(fn(params=obj), timeout=timeout)
        else:
            raw = await asyncio.wait_for(fn(**call_kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "timeout",
                "error": f"{tool} exceeded {timeout:.0f}s"}
    except Exception as e:
        return {"status": "error", "error": f"{tool}: {type(e).__name__}: {e}"}
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data.setdefault("status", "ok")
                return data
        except json.JSONDecodeError:
            pass
        if raw.startswith("Error"):
            return {"status": "error", "error": raw[:400]}
        return {"status": "ok", "markdown": raw[:3000]}
    return {"status": "ok", "raw": str(raw)[:3000]}


# ================= tool 1: tcm_adme_profile =================

try:
    from rdkit import Chem
    from rdkit.Chem import Crippen, Lipinski, QED, rdMolDescriptors
    _HAS_RDKIT = True
except ImportError:
    _HAS_RDKIT = False


async def _resolve_smiles(name: str) -> Dict[str, Any]:
    """Live PubChem lookup -> canonical/connectivity SMILES."""
    import httpx
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
           f"{name}/property/CanonicalSMILES,ConnectivitySMILES,SMILES/JSON")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
        if r.status_code == 404:
            return {"error": f"no PubChem compound for '{name}'"}
        r.raise_for_status()
        props = (r.json().get("PropertyTable", {})
                 .get("Properties", [{}])[0])
        for field in ("CanonicalSMILES", "ConnectivitySMILES", "SMILES"):
            if props.get(field):
                return {"smiles": props[field], "field": field,
                        "queried_at": _now_iso()}
        return {"error": "PubChem returned no SMILES field"}
    except Exception as e:
        return {"error": f"PubChem lookup failed: {type(e).__name__}: {e}"}


def _adme_from_mol(mol) -> Optional[Dict[str, Any]]:
    """Descriptor panel for an RDKit mol object."""
    if mol is None:
        return None
    mw = rdMolDescriptors.CalcExactMolWt(mol)
    tpsa = rdMolDescriptors.CalcTPSA(mol)
    logp = Crippen.MolLogP(mol)
    hbd = Lipinski.NumHDonors(mol)
    hba = Lipinski.NumHAcceptors(mol)
    rotb = Lipinski.NumRotatableBonds(mol)
    lipinski_viol = sum([mw > 500, logp > 5, hbd > 5, hba > 10])
    return {"mw": round(mw, 2), "tpsa": round(tpsa, 2),
            "logp": round(logp, 2), "hbd": hbd, "hba": hba,
            "rotatable_bonds": rotb,
            "lipinski_violations": lipinski_viol,
            "lipinski_pass": lipinski_viol == 0}


def _adme_compute(smiles: str) -> Optional[Dict[str, Any]]:
    """Compute the descriptor panel for one SMILES (RDKit required)."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    mw = rdMolDescriptors.CalcExactMolWt(mol)
    tpsa = rdMolDescriptors.CalcTPSA(mol)
    logp = Crippen.MolLogP(mol)
    hbd = Lipinski.NumHDonors(mol)
    hba = Lipinski.NumHAcceptors(mol)
    rotb = Lipinski.NumRotatableBonds(mol)
    n_arom = sum(1 for ring in mol.GetRingInfo().AtomRings()
                 if all(mol.GetAtomWithIdx(a).GetIsAromatic()
                        for a in ring))
    formula = rdMolDescriptors.CalcMolFormula(mol)
    # rule violations
    lipinski_viol = sum([mw > 500, logp > 5, hbd > 5, hba > 10])
    veber_ok = (rotb <= 10) and (tpsa <= 140)
    egan_ok = (logp <= 5.88) and (tpsa <= 131.6)
    try:
        qed = round(QED.qed(mol), 3)
    except Exception:
        qed = None
    return {
        "formula": formula,
        "mw": round(mw, 2),
        "tpsa": round(tpsa, 2),
        "logp": round(logp, 2),
        "hbd": hbd, "hba": hba, "rotatable_bonds": rotb,
        "aromatic_rings": n_arom,
        "lipinski_violations": lipinski_viol,
        "lipinski_pass": lipinski_viol == 0,
        "veber_pass": veber_ok,
        "egan_pass": egan_ok,
        "qed": qed,
    }


class AdmeProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compounds: Optional[List[str]] = Field(
        None, max_length=20,
        description="compound names (live PubChem SMILES resolution; "
                    "parallel fetch with PubChem rate-limit respected)")
    smiles_list: Optional[List[str]] = Field(
        None, max_length=20,
        description="raw SMILES list (skip the lookup; mutually exclusive "
                    "with compounds)")
    response_format: str = Field("markdown", description="markdown | json")


@mcp.tool(
    name="tcm_adme_profile",
    annotations={"title": "Batch ADME Physicochemical Profiling (RDKit)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_adme_profile(params: AdmeProfileInput) -> str:
    '''Batch physicochemical property profiling for candidate compounds.

    Per compound: MW, formula, TPSA, Crippen LogP, H-bond donors/acceptors,
    rotatable bonds, aromatic rings, Lipinski rule-of-5 violations, Veber
    and Egan oral-bioavailability gates, and QED (quantitative estimate of
    drug-likeness). Names are resolved to SMILES via LIVE PubChem lookups
    (provenance stamped); SMILES can be supplied directly to skip lookup.

    IMPORTANT SCOPING: these are STRUCTURAL descriptors (computational
    tier) - they complement, never replace, the TCMSP OB>=30 / DL>=0.18
    ADME gates (different methodology, do not mix conclusions).

    Args:
        params (AdmeProfileInput): compounds (names) OR smiles_list.

    Returns:
        str: per-compound descriptor table + drug-likeness verdicts +
        provenance (source + queried_at per resolved name).

    Examples:
        - "Profile berberine, baicalin and quercetin for drug-likeness"
          -> compounds=["berberine", "baicalin", "quercetin"]
    '''
    if not params.compounds and not params.smiles_list:
        return "Error: provide compounds (names) or smiles_list."
    if params.compounds and params.smiles_list:
        return "Error: provide only ONE of compounds or smiles_list."
    if not _HAS_RDKIT:
        return ("Error: RDKit is not installed in this environment "
                "(pip install rdkit) - descriptors unavailable.")

    rows: List[Dict[str, Any]] = []
    provenance: List[Dict[str, Any]] = []
    if params.compounds:
        # parallel live lookups, PubChem rate-limit aware (2 at a time)
        sem = asyncio.Semaphore(2)

        async def fetch(name: str) -> Dict[str, Any]:
            async with sem:
                return await _resolve_smiles(name)

        results = await asyncio.gather(
            *[fetch(n) for n in params.compounds])
        for name, res in zip(params.compounds, results):
            row = {"compound": name}
            if res.get("error"):
                row.update({"status": "error", "error": res["error"]})
            elif not res.get("smiles"):
                row.update({"status": "error", "error": "no SMILES"})
            else:
                comp = _adme_compute(res["smiles"])
                if comp is None:
                    row.update({"status": "error",
                                "error": "RDKit parse failed",
                                "smiles": res["smiles"]})
                else:
                    row.update({"status": "ok", "smiles": res["smiles"]})
                    row.update(comp)
                provenance.append(
                    {"compound": name, "field": res.get("field"),
                     "source": "PubChem PUG-REST",
                     "queried_at": res.get("queried_at")})
            rows.append(row)
    else:
        for smi in params.smiles_list:
            row = {"compound": smi[:40], "smiles": smi}
            comp = _adme_compute(smi)
            if comp is None:
                row.update({"status": "error",
                            "error": "RDKit parse failed"})
            else:
                row.update({"status": "ok"})
                row.update(comp)
            rows.append(row)

    ok_rows = [r for r in rows if r.get("status") == "ok"]
    data = {
        "n_input": len(rows), "n_profiled": len(ok_rows),
        "profiles": rows,
        "summary": {
            "lipinski_pass": [r["compound"] for r in ok_rows
                              if r.get("lipinski_pass")],
            "veber_pass": [r["compound"] for r in ok_rows
                           if r.get("veber_pass")],
            "egan_pass": [r["compound"] for r in ok_rows
                          if r.get("egan_pass")]},
        "scopes": {
            "evidence_tier": "computational descriptors (RDKit)",
            "relation_to_tcmsp_gates": "structural descriptors complement - "
                                       "never replace - the TCMSP OB>=30 / "
                                       "DL>=0.18 ADME gates"},
        "provenance": provenance,
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = [f"# ADME physicochemical profile ({len(ok_rows)}/{len(rows)} "
             "profiled)", ""]
    lines.append("| compound | MW | TPSA | LogP | HBD | HBA | RotB | "
                 "Lipinski | Veber | Egan | QED |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        if r.get("status") == "ok":
            lip = ("PASS" if r["lipinski_pass"]
                   else f"{r['lipinski_violations']} viol")
            lines.append(
                f"| {r['compound']} | {r['mw']} | {r['tpsa']} | "
                f"{r['logp']} | {r['hbd']} | {r['hba']} | "
                f"{r['rotatable_bonds']} | {lip} | "
                f"{'PASS' if r['veber_pass'] else 'FAIL'} | "
                f"{'PASS' if r['egan_pass'] else 'FAIL'} | "
                f"{r.get('qed', '-')} |")
        else:
            lines.append(f"| {r['compound']} | ERROR: "
                         f"{r.get('error', '?')} | | | | | | | | | |")
    lines.append("")
    lines.append("**Scope**: computational descriptors (RDKit); complements "
                 "- never replaces - the TCMSP OB/DL gates.")
    for p in provenance:
        lines.append(f"- provenance: {p['compound']} {p['field']} from "
                     f"{p['source']} at {p['queried_at']}")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)


# ================= tool 2: tcm_network_simulation =================
# Perturbation simulation on a STRING PPI network: knockout sweeps,
# cumulative hub removal, Monte-Carlo random controls with z-scores.

def _bfs_shortest(adj: Dict[str, set], src: str) -> Dict[str, int]:
    dist = {src: 0}
    q = deque([src])
    while q:
        cur = q.popleft()
        for nxt in adj.get(cur, ()):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    return dist


def _global_efficiency(adj: Dict[str, set]) -> float:
    """Mean inverse-shortest-path over all ordered pairs (0 if trivial)."""
    n = len(adj)
    if n < 2:
        return 0.0
    total = 0.0
    for src in adj:
        dist = _bfs_shortest(adj, src)
        for dst, d in dist.items():
            if dst != src and d > 0:
                total += 1.0 / d
    return total / (n * (n - 1))


def _largest_component_size(adj: Dict[str, set]) -> int:
    seen: set = set()
    best = 0
    for node in adj:
        if node in seen:
            continue
        comp = {node}
        q = deque([node])
        seen.add(node)
        while q:
            cur = q.popleft()
            for nxt in adj.get(cur, ()):
                if nxt not in comp:
                    comp.add(nxt)
                    seen.add(nxt)
                    q.append(nxt)
        best = max(best, len(comp))
    return best


def _remove_nodes(adj: Dict[str, set], removed: set) -> Dict[str, set]:
    return {v: (nbrs - removed) for v, nbrs in adj.items()
            if v not in removed}


class NetworkSimulationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genes: List[str] = Field(
        ..., min_length=5, max_length=100,
        description="network node genes (STRING PPI built live)")
    required_score: int = Field(
        700, ge=150, le=1000,
        description="STRING combined_score threshold (default 700 = high)")
    top_k_knockout: int = Field(
        5, ge=1, le=20,
        description="cumulative knockout of top-K vulnerable nodes")
    n_random_trials: int = Field(
        100, ge=20, le=1000,
        description="Monte-Carlo random-removal trials per comparison")
    random_seed: int = Field(42, description="reproducibility seed")
    response_format: str = Field("markdown", description="markdown | json")


async def _string_adjacency(genes: List[str], required_score: int
                            ) -> Dict[str, Any]:
    """Live STRING network -> adjacency map (direct httpx, no _FNS hop)."""
    import httpx
    url = ("https://string-db.org/api/tsv/network"
           f"?identifiers={'%0d'.join(genes)}"
           f"&species=9606&required_score={required_score}")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(url)
    r.raise_for_status()
    adj: Dict[str, set] = {g: set() for g in genes}
    edges = 0
    for line in r.text.splitlines()[1:]:
        if "\t" not in line:
            continue
        f = line.split("\t")
        a = f[2] if len(f) > 2 else None   # preferredName_A
        b = f[3] if len(f) > 3 else None   # preferredName_B
        if a and b and a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
            edges += 1
    return {"adj": adj, "edges": edges}


@mcp.tool(
    name="tcm_network_simulation",
    annotations={"title": "Network Perturbation Simulation (knockout / "
                          "robustness / Monte-Carlo)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_network_simulation(params: NetworkSimulationInput) -> str:
    '''Simulate perturbations on a STRING PPI network (in-silico knockout).

    Three coordinated analyses on the live STRING network of your genes:
      (a) single-node knockout sweep - remove every node once, measure the
          global-efficiency loss (network communication degradation) and
          largest-connected-component shrinkage
      (b) cumulative knockout - remove the top-K most vulnerable nodes
          together, showing whether damage is additive or super-additive
      (c) Monte-Carlo controls - random node sets of the same size removed
          over n trials; each observed loss gets a z-score against its
          matched random distribution (|z| >= 2 = significant fragility)

    Interpretation discipline: this is a TOPOLOGICAL simulation on current
    STRING edges - it suggests which nodes structurally anchor the module
    (candidate priority), NOT validated essentiality. Combine with
    expression/phenotype evidence (M2 tcm_cross_database_screen) before
    any biological claim.

    Args:
        params (NetworkSimulationInput): genes (5-100) + scoring +
            simulation controls.

    Returns:
        str: baseline metrics, per-node vulnerability ranking with
        z-scores, cumulative knockout curve, interpretation guidance.

    Examples:
        - "Simulate knockout of UC hub genes" -> genes=["TNF","IL6",...]
    '''
    try:
        net = await _string_adjacency(params.genes, params.required_score)
    except Exception as e:
        return (f"Error: STRING network fetch failed: "
                f"{type(e).__name__}: {e}")
    adj = net["adj"]
    n_nodes = len(adj)
    if n_nodes < 5 or net["edges"] < 3:
        return (f"Error: network too sparse ({n_nodes} nodes, "
                f"{net['edges']} edges) - simulation meaningless; "
                "check gene symbols / lower required_score.")
    rng = random.Random(params.random_seed)

    base_eff = _global_efficiency(adj)
    base_lcc = _largest_component_size(adj)

    # (a) single knockout sweep + (c) matched random controls
    sweep: List[Dict[str, Any]] = []
    eff_losses = {}
    for node in adj:
        pert = _remove_nodes(adj, {node})
        eff = _global_efficiency(pert)
        lcc = _largest_component_size(pert)
        loss = base_eff - eff
        eff_losses[node] = loss
        deg = len(adj[node])
        # matched random control: random single node (n trials)
        trials = [base_eff - _global_efficiency(
            _remove_nodes(adj, {rng.choice(list(adj))}))
            for _ in range(params.n_random_trials)]
        mean_r = sum(trials) / len(trials)
        std_r = (sum((t - mean_r) ** 2 for t in trials)
                 / len(trials)) ** 0.5 or 1e-12
        z = (loss - mean_r) / std_r
        sweep.append({
            "node": node, "degree": deg,
            "efficiency_loss": round(loss, 6),
            "lcc_after": lcc,
            "lcc_shrink": base_lcc - lcc,
            "random_mean_loss": round(mean_r, 6),
            "z_score": round(z, 2),
            "significant": abs(z) >= 2.0})
    sweep.sort(key=lambda r: -r["efficiency_loss"])

    # (b) cumulative knockout of top-K most vulnerable
    k = min(params.top_k_knockout, len(adj))
    ordered = [r["node"] for r in sweep[:k]]
    curve = []
    cum: set = set()
    for i, node in enumerate(ordered, 1):
        cum.add(node)
        pert = _remove_nodes(adj, cum)
        curve.append({
            "step": i, "removed": node,
            "efficiency": round(_global_efficiency(pert), 6),
            "efficiency_pct_of_baseline": round(
                100 * _global_efficiency(pert) / base_eff, 1)
            if base_eff > 0 else None,
            "lcc": _largest_component_size(pert)})
    # random cumulative control (same sizes)
    rand_curve = []
    all_nodes = list(adj)
    for i in range(1, k + 1):
        losses = []
        for _ in range(max(20, params.n_random_trials // 5)):
            removed = set(rng.sample(all_nodes, i))
            losses.append(_global_efficiency(_remove_nodes(adj, removed)))
        rand_curve.append({
            "step": i,
            "random_mean_efficiency": round(sum(losses) / len(losses), 6)})

    data = {
        "baseline": {
            "nodes": n_nodes, "edges": net["edges"],
            "global_efficiency": round(base_eff, 6),
            "largest_component": base_lcc,
            "required_score": params.required_score},
        "settings": {
            "top_k_knockout": k,
            "n_random_trials": params.n_random_trials,
            "random_seed": params.random_seed},
        "vulnerability_ranking": sweep,
        "cumulative_knockout_curve": curve,
        "random_cumulative_control": rand_curve,
        "interpretation": [
            "single-knockout efficiency loss ranks structural fragility; "
            "z >= 2 against matched random removals = significant",
            "cumulative curve dropping faster than the random control = "
            "targeted fragility (super-additive damage)",
            "topological simulation on CURRENT STRING edges - suggests "
            "candidate priority, NOT validated essentiality; combine with "
            "M2 cross-database evidence before biological claims"],
        "source": "STRING v12 network API (live) + local graph simulation",
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = [f"# Network perturbation simulation ({n_nodes} nodes / "
             f"{net['edges']} edges)", ""]
    lines.append(f"- baseline global efficiency: {round(base_eff, 6)}")
    lines.append(f"- baseline largest component: {base_lcc} nodes")
    lines.append("")
    lines.append("## Vulnerability ranking (single knockout, efficiency "
                 "loss desc)")
    lines.append("| rank | node | degree | eff loss | LCC shrink | "
                 "z-score | significant |")
    lines.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(sweep[:15], 1):
        lines.append(
            f"| {i} | {r['node']} | {r['degree']} | "
            f"{r['efficiency_loss']:.6f} | {r['lcc_shrink']} | "
            f"{r['z_score']} | {'YES' if r['significant'] else '-'} |")
    lines.append("")
    lines.append("## Cumulative knockout (top-K vulnerable, vs random)")
    lines.append("| step | removed | efficiency | % baseline | random mean |")
    lines.append("|---|---|---|---|---|")
    for c, rc in zip(curve, rand_curve):
        lines.append(
            f"| {c['step']} | {c['removed']} | {c['efficiency']:.6f} | "
            f"{c['efficiency_pct_of_baseline']}% | "
            f"{rc['random_mean_efficiency']:.6f} |")
    lines.append("")
    lines.append("**Discipline**: topological simulation on current STRING "
                 "edges; suggests candidate priority, not validated "
                 "essentiality - combine with M2 evidence.")
    lines.append(f"- source: {data['source']}; queried_at: {_now_iso()}")
    return "\n".join(lines)


# ================= tool 3: dock_batch_screening =================

class BatchScreeningInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receptor_pdb: str = Field(..., description="receptor PDB id, e.g. 2Z64 "
                              "(fetched+cleaned once, shared by all ligands)")
    ligands: List[str] = Field(..., min_length=2, max_length=12,
                               description="compound names/CIDs (PubChem 3D "
                                           "SDF, parallel fetch)")
    pocket_chain: str = Field("C", description="chain ID of pocket residues")
    pocket_residues: str = Field(
        ..., description="comma-separated pocket residue NUMBERS from "
                         "LITERATURE (e.g. 46,52,53; never guessed)")
    receptor_chains: str = Field(
        "A,C", description="receptor chains for later contact analysis")
    exhaustiveness: int = Field(4, ge=1, le=32,
                                description="Vina exhaustiveness (batch "
                                            "screening default 4 = fast "
                                            "triage; refine top hits at 16)")
    max_workers: int = Field(3, ge=1, le=8,
                             description="parallel Vina processes "
                                         "(CPU-bound; leave cores free)")
    seed: int = Field(42)
    vina_path: Optional[str] = Field(
        None, description="vina executable (or env VINA_EXE)")
    adme_prefilter: bool = Field(
        True, description="skip Lipinski-failing ligands (>=3 violations) "
                          "before spending docking compute")
    response_format: str = Field("markdown", description="markdown | json")


def _builtin_vina() -> Optional[str]:
    """Skill-bundled vina: <skill>/bin/vina.exe (self-contained)."""
    p = Path(__file__).resolve().parent.parent / "bin" / "vina.exe"
    return str(p) if p.exists() else None


def _resolve_vina(explicit: Optional[str]) -> Optional[str]:
    """explicit > VINA_EXE env > skill-bundled bin/vina.exe."""
    if explicit and Path(explicit).exists():
        return explicit
    env = os.environ.get("VINA_EXE")
    if env and Path(env).exists():
        return env
    return _builtin_vina()


async def _batch_screen(params: BatchScreeningInput) -> Dict[str, Any]:
    work = SESSIONS_DIR / f"batch-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    work.mkdir(parents=True, exist_ok=True)
    vina_path = _resolve_vina(params.vina_path)
    if not vina_path or not Path(vina_path).exists():
        return {"status": "paused", "reason":
                "vina executable not found (pass vina_path or set VINA_EXE)"}

    t0 = time.time()
    # 1) receptor: fetch once, share across all ligands
    rec = work / "receptor.pdb"
    res = await _invoke("dock_fetch_structure", 120, source="pdb",
                        query=params.receptor_pdb, drop_hetatm=True,
                        out=str(rec))
    if not rec.exists():
        return {"status": "error", "error":
                f"receptor fetch failed: {res.get('error', res)}"}

    # 2) ligands: parallel fetch (PubChem rate-limit aware)
    sem_f = asyncio.Semaphore(2)

    async def fetch_lig(name: str) -> Dict[str, Any]:
        sdf = work / f"lig_{re.sub(r'[^A-Za-z0-9_-]', '_', str(name))}.sdf"
        if sdf.exists():
            return {"ligand": name, "sdf": str(sdf), "status": "ok"}
        async with sem_f:
            r = await _invoke("dock_fetch_structure", 120, source="pubchem",
                              query=str(name), out=str(sdf))
        ok = sdf.exists() and not str(r).startswith("Error")
        return {"ligand": name, "sdf": str(sdf) if sdf.exists() else None,
                "status": "ok" if ok else "error",
                "error": r.get("error") if isinstance(r, dict) else str(r)}

    lig_results = await asyncio.gather(
        *[fetch_lig(n) for n in params.ligands])
    fetched = [r for r in lig_results if r["status"] == "ok"]
    fetch_errors = [r for r in lig_results if r["status"] != "ok"]

    # 3) ADME prefilter (structural triage before compute; RDKit reads
    #    the 3D SDF directly - no SMILES parsing hop)
    dockable = fetched
    adme_skipped = []
    if params.adme_prefilter and _HAS_RDKIT:
        for r in fetched:
            try:
                supp = Chem.SDMolSupplier(r["sdf"], removeHs=False)
                mol = next(iter(supp), None)
            except Exception:
                mol = None
            desc = _adme_from_mol(mol)
            if desc is None:
                continue  # cannot judge -> dock anyway
            r["adme"] = desc
            if desc.get("lipinski_violations", 0) >= 3:
                adme_skipped.append({
                    "ligand": r["ligand"],
                    "reason": f"{desc['lipinski_violations']} Lipinski "
                              "violations (MW/LogP/HBD/HBA far outside "
                              "oral-drug space)",
                    "descriptors": {k: desc[k] for k in
                                    ("mw", "logp", "hbd", "hba")}})
        skipped_names = {s["ligand"] for s in adme_skipped}
        dockable = [r for r in fetched
                    if r["ligand"] not in skipped_names]

    # 4) parallel docking (worker semaphore; each call spawns its own vina)
    sem_d = asyncio.Semaphore(params.max_workers)
    pocket_nums = []
    for tok in str(params.pocket_residues).replace(";", ",").split(","):
        digits = re.findall(r"\d+", tok)
        if digits:
            pocket_nums.append(digits[-1])
    pocket_num = ",".join(pocket_nums)

    async def dock_one(item: Dict[str, Any]) -> Dict[str, Any]:
        out_dir = work / f"dock_{re.sub(r'[^A-Za-z0-9_-]', '_', str(item['ligand']))}"
        async with sem_d:
            r = await _invoke(
                "dock_vina_docking", 1800,
                receptor=str(rec), ligand=item["sdf"],
                pocket_chain=params.pocket_chain,
                pocket_residues=pocket_num,
                exhaustiveness=params.exhaustiveness,
                num_modes=9, seed=params.seed,
                vina_path=vina_path, workdir=str(out_dir))
        vlog = out_dir / "vina_log.txt"
        aff, modes = None, 0
        if vlog.exists():
            text = vlog.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"^\s+1\s+(-?[\d.]+)", text, re.MULTILINE)
            if m:
                aff = float(m.group(1))
            modes = len(re.findall(r"^\s+\d+\s+-?[\d.]+", text,
                                   re.MULTILINE))
        return {"ligand": item["ligand"], "status": "ok",
                "affinity": aff, "n_modes": modes,
                "workdir": str(out_dir),
                "best_pose": str(out_dir / "best_pose.pdb"),
                "raw": r}

    dock_results = await asyncio.gather(
        *[dock_one(it) for it in dockable],
        return_exceptions=True)
    docked = [r for r in dock_results
              if isinstance(r, dict) and r.get("status") == "ok"]
    dock_errors = [{"ligand": it["ligand"], "error": str(r)[:200]}
                   for it, r in zip(dockable, dock_results)
                   if isinstance(r, Exception)]

    # 5) rank by affinity (ascending = best first)
    docked.sort(key=lambda r: (r["affinity"] is None,
                               r["affinity"] if r["affinity"] is not None
                               else 0))
    total = time.time() - t0
    return {
        "status": "done",
        "workdir": str(work),
        "receptor": params.receptor_pdb,
        "n_ligands_input": len(params.ligands),
        "n_fetched": len(fetched),
        "n_adme_skipped": len(adme_skipped),
        "n_docked": len(docked),
        "parallelism": {"max_workers": params.max_workers,
                        "exhaustiveness": params.exhaustiveness},
        "fetch_errors": fetch_errors,
        "adme_skipped": adme_skipped,
        "dock_errors": dock_errors,
        "ranking": [
            {"rank": i + 1, "ligand": r["ligand"],
             "affinity_kcal_mol": r["affinity"],
             "n_modes": r["n_modes"], "workdir": r["workdir"]}
            for i, r in enumerate(docked)],
        "triage_note": (
            f"batch mode exhaustiveness={params.exhaustiveness} is a FAST "
            "TRIAGE; re-dock the top hits at exhaustiveness 16 with "
            "dock_vina_docking before drawing conclusions "
            "(all results remain 'proposed docking model')"),
        "wall_clock_seconds": round(total, 1),
        "queried_at": _now_iso()}


def _read_first_smi(sdf_path) -> str:
    """Deprecated helper kept for compatibility (unused by prefilter)."""
    try:
        with open(sdf_path, encoding="utf-8", errors="ignore") as fh:
            fh.readline()           # header/name
            smi = fh.readline().strip()
        return smi if smi else ""
    except OSError:
        return ""


@mcp.tool(
    name="dock_batch_screening",
    annotations={"title": "Parallel Multi-Ligand Virtual Screening "
                          "(batch Vina)",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": True})
async def dock_batch_screening(params: BatchScreeningInput) -> str:
    '''Screen MANY ligands against ONE receptor in parallel (batch Vina).

    Pipeline (all inside one call): receptor fetched once and shared ->
    ligands fetched in parallel from PubChem 3D -> optional ADME
    pre-filter (skips >=3 Lipinski violations before spending compute) ->
    parallel AutoDock Vina docking (max_workers concurrent processes, each
    ligand its own workdir) -> affinity-ranked table with triage note.

    The pocket residues MUST come from literature (same discipline as
    single docking). Batch mode defaults to exhaustiveness 4 (fast
    triage): re-dock the top hits at 16 before drawing conclusions.

    Args:
        params (BatchScreeningInput): receptor + ligands (2-12) + pocket +
            parallelism controls.

    Returns:
        str: ranked affinity table, per-ligand workdirs (best_pose.pdb
        ready for dock_analyze_contacts), skipped/error lists, wall clock.

    Examples:
        - "Screen berberine, baicalin, quercetin against MD-2 (2Z64)"
          -> receptor_pdb="2Z64", ligands=["berberine","baicalin",
          "quercetin"], pocket_residues="46,52,53,87,..."
    '''
    if not params.vina_path and not os.environ.get("VINA_EXE") \
            and not _builtin_vina():
        return ("Error: no vina executable (pass vina_path, set VINA_EXE "
                "env, or restore the skill-bundled bin/vina.exe); batch "
                "docking cannot run.")
    result = await _batch_screen(params)
    if params.response_format == "json":
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if result.get("status") != "done":
        return f"Error: {result.get('reason') or result.get('error')}"
    lines = [f"# Parallel batch screening ({result['n_docked']} docked / "
             f"{result['n_ligands_input']} input, "
             f"{result['wall_clock_seconds']}s, workers="
             f"{result['parallelism']['max_workers']})", ""]
    lines.append("| rank | ligand | affinity (kcal/mol) | modes |")
    lines.append("|---|---|---|---|")
    for r in result["ranking"]:
        lines.append(f"| {r['rank']} | {r['ligand']} | "
                     f"{r['affinity_kcal_mol']} | {r['n_modes']} |")
    for s in result["adme_skipped"]:
        lines.append(f"- ADME-skipped: {s['ligand']} - {s['reason']}")
    for e in result["fetch_errors"] + result["dock_errors"]:
        lines.append(f"- error: {e.get('ligand')}: "
                     f"{e.get('error', '?')[:120]}")
    lines.append("")
    lines.append(f"- workdir: {result['workdir']}")
    lines.append(f"- wall clock: {result['wall_clock_seconds']}s "
                 f"(workers={result['parallelism']['max_workers']})")
    lines.append(f"- triage: {result['triage_note']}")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)


# ================= tool 4: tcm_multi_path_run =================

class MultiPathRunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: Optional[str] = Field(None, max_length=500,
                                description="natural language goal (for the "
                                            "report header)")
    compounds: List[str] = Field(
        ..., min_length=2, max_length=8,
        description="candidate compounds = the ARMS of the analysis "
                    "network (each runs its own chain in parallel)")
    disease: Optional[str] = Field(
        None, max_length=200,
        description="optional disease context (recorded for the report)")
    docking_inputs: Optional[Dict[str, Any]] = Field(
        None, description="OPTIONAL shared docking context for every arm: "
                          "receptor_pdb, pocket_chain, pocket_residues "
                          "(FROM LITERATURE), receptor_chains, "
                          "exhaustiveness, seed, vina_path. Omit to run the "
                          "retrieval+ADME arms only (no docking).")
    max_runtime: int = Field(2400, ge=120, le=7200)
    response_format: str = Field("markdown", description="markdown | json")


async def _multi_path(params: MultiPathRunInput) -> Dict[str, Any]:
    t0 = time.time()
    sid = "multi-" + datetime.now(timezone.utc).strftime(
        "%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    dock_in = params.docking_inputs or {}
    vina_path = _resolve_vina(dock_in.get("vina_path"))
    want_docking = bool(dock_in.get("receptor_pdb")
                        and dock_in.get("pocket_residues")
                        and vina_path)
    state = {
        "session_id": sid, "goal": params.goal,
        "disease": params.disease, "compounds": params.compounds,
        "mode": "multi-arm parallel analysis network",
        "docking_enabled": want_docking,
        "arms": [], "status": "running",
        "created_at": _now_iso()}
    p = SESSIONS_DIR / f"{sid}.json"
    _atomic_write_json(p, state)

    # pocket normalization once
    pocket_num = None
    if want_docking:
        nums = []
        for tok in str(dock_in["pocket_residues"]).replace(
                ";", ",").split(","):
            digits = re.findall(r"\d+", tok)
            if digits:
                nums.append(digits[-1])
        pocket_num = ",".join(nums)

    work = SESSIONS_DIR / f"{sid}_work"
    work.mkdir(parents=True, exist_ok=True)

    async def one_arm(compound: str) -> Dict[str, Any]:
        """One arm = full mini-chain for one candidate, run in parallel."""
        arm: Dict[str, Any] = {"compound": compound, "steps": []}
        # A) cross-database consensus (M2)
        r = await _invoke("tcm_cross_database_screen", 300,
                          mode="compound", entities=[compound],
                          concurrency=1)
        rank = (r.get("consensus_ranking") or [])
        ch = rank[0].get("consensus_hits") if rank else None
        arm["consensus_hits"] = ch
        arm["steps"].append({
            "step": "A-consensus", "tool": "tcm_cross_database_screen",
            "result": f"{ch if ch is not None else '?'} database hits",
            "queried_at": r.get("queried_at", _now_iso())})
        # B) ADME profile (RDKit on live-resolved SMILES)
        adme_row = None
        if _HAS_RDKIT:
            lookup = await _resolve_smiles(compound)
            if lookup.get("smiles"):
                adme_row = _adme_compute(lookup["smiles"])
                arm["smiles"] = lookup["smiles"]
        arm["adme"] = adme_row
        arm["steps"].append({
            "step": "B-adme", "tool": "tcm_adme_profile(RDKit)",
            "result": (f"Lipinski {'PASS' if adme_row and adme_row.get('lipinski_pass') else 'FAIL'}"
                       f" (MW {adme_row.get('mw') if adme_row else '?'}, "
                       f"TPSA {adme_row.get('tpsa') if adme_row else '?'}, "
                       f"LogP {adme_row.get('logp') if adme_row else '?'})")
            if adme_row else "descriptors unavailable"})
        # C) optional docking arm
        if want_docking:
            sdf = work / f"lig_{re.sub(r'[^A-Za-z0-9_-]', '_', compound)}.sdf"
            fr = await _invoke("dock_fetch_structure", 120,
                               source="pubchem", query=compound,
                               out=str(sdf))
            if sdf.exists():
                rec = work / "receptor.pdb"
                if not rec.exists():
                    await _invoke("dock_fetch_structure", 120,
                                  source="pdb", query=dock_in["receptor_pdb"],
                                  drop_hetatm=True, out=str(rec))
                out_dir = work / f"dock_{re.sub(r'[^A-Za-z0-9_-]', '_', compound)}"
                dr = await _invoke(
                    "dock_vina_docking", 1500, receptor=str(rec),
                    ligand=str(sdf),
                    pocket_chain=dock_in.get("pocket_chain", "C"),
                    pocket_residues=pocket_num,
                    exhaustiveness=dock_in.get("exhaustiveness", 8),
                    seed=dock_in.get("seed", 42),
                    vina_path=vina_path, workdir=str(out_dir))
                vlog = out_dir / "vina_log.txt"
                aff = None
                if vlog.exists():
                    m = re.search(r"^\s+1\s+(-?[\d.]+)",
                                  vlog.read_text(encoding="utf-8",
                                                 errors="ignore"),
                                  re.MULTILINE)
                    if m:
                        aff = float(m.group(1))
                arm["affinity"] = aff
                arm["best_pose"] = str(out_dir / "best_pose.pdb")
                arm["steps"].append({
                    "step": "C-docking", "tool": "dock_vina_docking",
                    "result": f"best affinity {aff} kcal/mol "
                              "(proposed docking model)",
                    "queried_at": dr.get("queried_at", _now_iso())})
            else:
                arm["steps"].append({
                    "step": "C-docking", "tool": "dock_fetch_structure",
                    "result": f"ligand fetch failed: "
                              f"{fr.get('error', '?')}"})
        arm["finished_at"] = _now_iso()
        return arm

    arms = await asyncio.gather(*[one_arm(c) for c in params.compounds],
                                return_exceptions=True)
    state["arms"] = [a if isinstance(a, dict)
                     else {"compound": "?", "error": str(a)[:200]}
                     for a in arms]

    # ---- horizontal comparison + rule-based composite ranking ----
    ok_arms = [a for a in state["arms"] if isinstance(a, dict)]
    n_db = 3 if not want_docking else 3  # consensus databases: 3 for compound

    def _norm(x, lo, hi, invert=False):
        if x is None:
            return 0.0
        v = max(0.0, min(1.0, (x - lo) / (hi - lo))) if hi > lo else 0.0
        return 1 - v if invert else v

    for a in ok_arms:
        ch = a.get("consensus_hits")
        ch_s = _norm(ch, 0, n_db) if ch is not None else 0.0
        lip = 1.0 if (a.get("adme") or {}).get("lipinski_pass") else \
            (0.5 if (a.get("adme") or {}).get("lipinski_violations") == 1
             else 0.0)
        aff = a.get("affinity")
        # stronger binder (more negative) -> higher score; -5..-12 window
        aff_s = _norm(aff, -5, -12) if aff is not None else 0.0
        weights = ([0.5, 0.5] if aff is None else [0.35, 0.2, 0.45])
        parts = [ch_s, lip, aff_s] if aff is not None else [ch_s, lip]
        a["component_scores"] = {
            "consensus": round(ch_s, 3), "drug_likeness": round(lip, 3),
            **({"affinity": round(aff_s, 3)} if aff is not None else {})}
        a["composite_score"] = round(
            sum(w * s for w, s in zip(weights, parts)), 3)
    ranked = sorted(ok_arms, key=lambda a: -a.get("composite_score", 0))
    state["comparison"] = [
        {"rank": i + 1, "compound": a["compound"],
         "consensus_hits": a.get("consensus_hits"),
         "mw": (a.get("adme") or {}).get("mw"),
         "logp": (a.get("adme") or {}).get("logp"),
         "lipinski": ("PASS" if (a.get("adme") or {}).get("lipinski_pass")
                      else f"{(a.get('adme') or {}).get('lipinski_violations')} viol"
                      if (a.get("adme") or {}).get("lipinski_violations")
                      is not None else None),
         "affinity_kcal_mol": a.get("affinity"),
         "composite_score": a.get("composite_score")}
        for i, a in enumerate(ranked)]
    state["ranking_note"] = (
        "rule-based weighted ranking (consensus + drug-likeness + "
        "affinity when docked); NOT a validated scoring function - use for "
        "prioritization only, then verify top candidates with deep runs")
    state["status"] = "done"
    state["finished_at"] = _now_iso()
    state["wall_clock_seconds"] = round(time.time() - t0, 1)
    _atomic_write_json(p, state)
    return state


@mcp.tool(
    name="tcm_multi_path_run",
    annotations={"title": "Multi-Arm Parallel Analysis Network (breadth "
                          "comparison)",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": True})
async def tcm_multi_path_run(params: MultiPathRunInput) -> str:
    '''Run a MULTI-ARM analysis network: every candidate compound gets its
    own chain, all arms execute in parallel, then compare horizontally.

    Complements tcm_smart_run (depth: one goal, one deep chain) - this is
    BREADTH: 2-8 candidates compared on equal footing. Per arm:
    cross-database consensus (PubChem+ChEMBL+SymMap) -> ADME
    physicochemical profile (RDKit on live-resolved SMILES) -> optional
    Vina docking (shared receptor/pocket when docking_inputs given).
    Output: comparison table + rule-based composite ranking + checkpointed
    session (~/.tcm_mcp_state/sessions/multi-<id>.json).

    The composite score (consensus 35% + drug-likeness 20% + affinity 45%
    when docked) is a PRIORITIZATION heuristic, not a validated scoring
    function - verify top candidates with focused deep runs.

    Args:
        params (MultiPathRunInput): compounds (2-8 arms) + optional shared
            docking_inputs (receptor, LITERATURE pocket residues, vina).

    Returns:
        str: per-arm step logs, comparison table, ranking, session id.

    Examples:
        - "Compare berberine vs baicalin vs quercetin for MD-2"
          -> compounds=["berberine","baicalin","quercetin"],
          docking_inputs={"receptor_pdb":"2Z64","pocket_residues":
          "46,52,...","vina_path":"..."}
    '''
    result = await _multi_path(params)
    if params.response_format == "json":
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    lines = ["# Multi-arm parallel analysis network", ""]
    lines.append(f"- **session_id**: {result['session_id']}")
    lines.append(f"- **goal**: {result.get('goal') or '(not given)'}")
    lines.append(f"- **arms**: {len(result['compounds'])} compounds, "
                 f"docking {'enabled' if result.get('docking_enabled') else 'disabled'}")
    lines.append(f"- **wall clock**: {result.get('wall_clock_seconds')}s")
    lines.append("")
    lines.append("## Horizontal comparison (composite-ranked)")
    lines.append("| rank | compound | consensus | MW | LogP | Lipinski | "
                 "affinity | composite |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in result.get("comparison", []):
        lines.append(
            f"| {c['rank']} | {c['compound']} | {c['consensus_hits']} | "
            f"{c['mw'] or '-'} | {c['logp'] or '-'} | {c['lipinski'] or '-'} | "
            f"{c['affinity_kcal_mol'] if c['affinity_kcal_mol'] is not None else '-'} | "
            f"{c['composite_score']} |")
    lines.append("")
    lines.append("## Per-arm step logs")
    for a in result.get("arms", []):
        lines.append(f"### {a.get('compound')}")
        for s in a.get("steps", []):
            lines.append(f"- [{s['step']}] {s['tool']}: {s['result']}")
    lines.append("")
    lines.append(f"**Ranking note**: {result.get('ranking_note')}")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)
