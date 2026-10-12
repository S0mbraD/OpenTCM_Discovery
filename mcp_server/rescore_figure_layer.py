#!/usr/bin/env python3
"""rescore_figure_layer: docking re-scoring + publication network figures.

Two P0 tools from the phase-3 plan:

  dock_rescore        - post-screening precision ranking: affinity-spectrum
                        analysis from vina logs (spread/mode-gap as binding
                        -mode stability signal) + literature-pocket
                        anchoring rate (fraction of pocket residues within
                        contact cutoff of the best pose) + composite
                        re-rank of batch screening results
  tcm_network_figure  - publication-grade multi-layer network figure
                        (ingredient-target-disease or plain PPI): built-in
                        Fruchterman-Reingold layout (pure numpy) +
                        matplotlib rendering at 3200x2200 (PNG, optional
                        PDF), hub-tier colouring, evidence-grading
                        caption, fixed seed reproducible

Disciplines: docking outputs stay 'proposed docking model'; figure caption
carries source + queried_at; no CDN / offline rendering only.
"""
import asyncio
import json
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

from pydantic import BaseModel, Field, ConfigDict

mcp = _MCPBase("tcm_rescore_figure_layer")

_FNS: Dict[str, Callable] = {}


def attach_tools(fn_map: Dict[str, Callable]) -> None:
    _FNS.update(fn_map)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ================= tool 3: dock_rescore =================

def _parse_vina_log(path: Path) -> Dict[str, Any]:
    """Parse a vina log: per-mode affinities + best."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    modes = [(int(m.group(1)), float(m.group(2)))
             for m in re.finditer(r"^\s+(\d+)\s+(-?[\d.]+)\s",
                                  text, re.MULTILINE)]
    best = modes[0][1] if modes else None
    return {"modes": modes, "best": best}


def _pose_residues(pdb_path: Path) -> Dict[str, Dict[str, float]]:
    """Ligand-chain atoms from a best-pose PDB: chain -> {resnum: [xyz]}."""
    atoms = []
    for line in pdb_path.read_text(encoding="utf-8",
                                   errors="ignore").splitlines():
        if line.startswith(("ATOM", "HETATM")):
            chain = (line[21] or " ").strip()
            try:
                resnum = int(line[22:26])
            except ValueError:
                continue
            x, y, z = (float(line[30:38]), float(line[38:46]),
                       float(line[46:54]))
            atoms.append((chain, resnum, x, y, z))
    return {"atoms": atoms}


def _min_dist_to_residue(pdb_path: Path, chain: str, resnum: int,
                         lig_atoms) -> Optional[float]:
    best = None
    for line in pdb_path.read_text(encoding="utf-8",
                                   errors="ignore").splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if (line[21] or " ").strip() != chain:
            continue
        try:
            if int(line[22:26]) != resnum:
                continue
        except ValueError:
            continue
        x, y, z = (float(line[30:38]), float(line[38:46]),
                   float(line[46:54]))
        d = math.sqrt(min(
            (x - lx) ** 2 + (y - ly) ** 2 + (z - lz) ** 2
            for lx, ly, lz in lig_atoms))
        best = d if best is None else min(best, d)
    return best


class DockRescoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workdir: str = Field(..., description="batch screening workdir "
                         "(containing dock_<ligand>/ subdirs) or a single "
                         "docking workdir")
    pocket_chain: str = Field("C", description="chain ID of the pocket")
    pocket_residues: str = Field(
        ..., description="comma-separated literature pocket residue NUMBERS "
                         "(e.g. 46,52,53; the receptor PDB must contain "
                         "them - receptor.pdb in the same workdir)")
    receptor: Optional[str] = Field(
        None, description="path to the receptor PDB (default: workdir/"
                          "receptor.pdb)")
    contact_cutoff: float = Field(4.5, ge=3.0, le=8.0)
    response_format: str = Field("markdown", description="markdown | json")


@mcp.tool(
    name="dock_rescore",
    annotations={"title": "Docking Post-Screening Re-Rank (affinity "
                          "spectrum + pocket anchoring)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def dock_rescore(params: DockRescoreInput) -> str:
    '''Precision re-rank of docking results (batch screening follow-up).

    Per ligand, from its own docking workdir:
      - affinity spectrum from vina_log.txt: best affinity, mode-1 vs
        mode-2 gap and mode-1..9 spread (small gap/spread = one dominant
        binding mode = more stable in-silico signal)
      - literature-pocket anchoring rate: fraction of the LITERATURE
        pocket residues within the contact cutoff of the best pose
        (requires receptor.pdb alongside, measured against all receptor
        atoms of each residue)
      - composite re-rank score: normalised affinity + anchoring rate +
        mode-gap penalty - a PRIORITIZATION heuristic, not a validated
        scoring function

    Args:
        params (DockRescoreInput): workdir (batch or single) + literature
            pocket + receptor path.

    Returns:
        str: per-ligand re-score table sorted by composite score, with the
        'proposed docking model' discipline note.

    Examples:
        - "Re-rank the batch screening results for MD-2"
          -> workdir=".../batch-2026.../", pocket_residues="46,52,53,..."
    '''
    work = Path(params.workdir)
    if not work.exists():
        return f"Error: workdir not found: {work}"
    dock_dirs = ([p for p in sorted(work.iterdir())
                  if p.is_dir() and p.name.startswith("dock_")]
                 if any(p.is_dir() and p.name.startswith("dock_")
                        for p in work.iterdir()) else [work])
    receptor = Path(params.receptor) if params.receptor else \
        work / "receptor.pdb"
    pocket = []
    for tok in params.pocket_residues.replace(";", ",").split(","):
        digits = re.findall(r"\d+", tok)
        if digits:
            pocket.append(int(digits[-1]))
    if not pocket:
        return "Error: no numeric pocket residues parsed."

    rows = []
    for ddir in dock_dirs:
        vlog = ddir / "vina_log.txt"
        pose = ddir / "best_pose.pdb"
        lig_name = ddir.name.replace("dock_", "")
        row: Dict[str, Any] = {"ligand": lig_name, "workdir": str(ddir)}
        if not vlog.exists():
            row["status"] = "error"
            row["error"] = "vina_log.txt missing"
            rows.append(row)
            continue
        spec = _parse_vina_log(vlog)
        modes = [a for _, a in spec["modes"]]
        row["best_affinity"] = spec["best"]
        row["n_modes"] = len(modes)
        if len(modes) >= 2:
            row["mode_gap"] = round(abs(modes[0] - modes[1]), 3)
        if modes:
            row["spread"] = round(max(modes) - min(modes), 3)
        if pose.exists() and receptor.exists():
            lig = _pose_residues(pose)["atoms"]
            lig_c = [(x, y, z) for (_c, _r, x, y, z) in lig]
            anchored, dists = 0, []
            for res in pocket:
                dmin = _min_dist_to_residue(receptor, params.pocket_chain,
                                            res, lig_c)
                if dmin is not None:
                    dists.append(dmin)
                    if dmin <= params.contact_cutoff:
                        anchored += 1
            row["anchored_residues"] = f"{anchored}/{len(pocket)}"
            row["anchoring_rate"] = round(anchored / len(pocket), 3)
            row["mean_pocket_dist"] = (round(sum(dists) / len(dists), 2)
                                       if dists else None)
        else:
            row["status"] = "partial"
            row["error"] = ("best_pose.pdb or receptor.pdb missing - "
                            "affinity spectrum only")
        rows.append(row)

    scored = [r for r in rows if r.get("best_affinity") is not None]
    for r in scored:
        aff_n = max(0.0, min(1.0, (-r["best_affinity"] - 5) / 7))
        anch_n = r.get("anchoring_rate", 0.0)
        gap = r.get("mode_gap")
        gap_pen = 0.0 if gap is None else min(1.0, gap / 5.0)
        r["composite"] = round(0.45 * aff_n + 0.35 * anch_n
                               - 0.20 * gap_pen + 0.40, 3)
    scored.sort(key=lambda r: -r["composite"])

    data = {
        "workdir": str(work),
        "n_ligands": len(rows),
        "pocket": {"chain": params.pocket_chain,
                   "residues": pocket, "cutoff": params.contact_cutoff},
        "rerank": scored + [r for r in rows
                            if r.get("best_affinity") is None],
        "discipline": [
            "all docking outputs remain 'proposed docking model'",
            "composite score = prioritization heuristic (affinity 45% + "
            "literature-pocket anchoring 35% + mode-gap penalty), NOT a "
            "validated scoring function",
            "small mode gap + high anchoring = most trustworthy in-silico "
            "signal; still require exhaustiveness-16 refinement for "
            "publication"],
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = [f"# Docking re-score ({len(scored)} scored / {len(rows)} "
             "ligands)", ""]
    lines.append("| ligand | best (kcal/mol) | modes | gap | spread | "
                 "anchored | mean dist | composite |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {r['ligand']} | {r.get('best_affinity', 'ERR')} | "
            f"{r.get('n_modes', '-')} | {r.get('mode_gap', '-')} | "
            f"{r.get('spread', '-')} | {r.get('anchored_residues', '-')} | "
            f"{r.get('mean_pocket_dist', '-')} | "
            f"{r.get('composite', '-')} |")
    lines.append("")
    for note in data["discipline"]:
        lines.append(f"- {note}")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)


# ================= tool 4: tcm_network_figure =================

class NetworkFigureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genes: Optional[List[str]] = Field(
        None, max_length=80,
        description="gene list (live STRING network fetch, required_score "
                    "700)")
    edges: Optional[List[List[str]]] = Field(
        None, max_length=2000,
        description="custom edge list [[u, v], ...] instead of STRING "
                    "(e.g. ingredient-target pairs); nodes auto-collected")
    node_kinds: Optional[Dict[str, str]] = Field(
        None, description="optional node -> kind map (ingredient|target|"
                          "disease) for multi-layer colouring")
    title: str = Field("Network", max_length=120)
    required_score: int = Field(700, ge=150, le=1000)
    out_path: str = Field(..., description="output PNG path "
                          "(3200x2200; PDF also written alongside)")
    layout_seed: int = Field(42)
    response_format: str = Field("markdown", description="markdown | json")


def _fr_layout(nodes: List[str], edges: List[List[str]], seed: int,
               iterations: int = 120) -> Dict[str, tuple]:
    """Fruchterman-Reingold force-directed layout (pure numpy; no
    networkx dependency). Deterministic via seeded rng."""
    import numpy as np
    n = len(nodes)
    idx = {v: i for i, v in enumerate(nodes)}
    rng = np.random.default_rng(seed)
    if n == 0:
        return {}
    pos = rng.uniform(-1, 1, (n, 2)) * max(1.0, n ** 0.5)
    k = 1.2 / max(1, n ** 0.5)          # optimal pair distance
    temp = max(1.0, n ** 0.5 * 0.5)
    cooling = temp / (iterations + 1)
    e_idx = [(idx[u], idx[v]) for u, v in edges
             if u in idx and v in idx and u != v]
    for _ in range(iterations):
        delta = pos[:, None, :] - pos[None, :, :]        # (n, n, 2)
        dist = np.sqrt((delta ** 2).sum(-1)) + 1e-9
        rep_mag = (k * k) / dist
        np.fill_diagonal(rep_mag, 0.0)
        disp = ((delta / dist[:, :, None]) * rep_mag[:, :, None]).sum(axis=1)
        for i, j in e_idx:
            d = dist[i, j]
            att_mag = (d * d) / k
            direction = delta[i, j] / d
            disp[i] -= direction * att_mag
            disp[j] += direction * att_mag
        norm = np.sqrt((disp ** 2).sum(-1)) + 1e-9
        step = np.minimum(norm, temp) / norm
        pos += disp * step[:, None]
        temp -= cooling
    span = np.abs(pos).max() + 1e-9
    pos = pos / span
    return {v: (float(pos[idx[v]][0]), float(pos[idx[v]][1]))
            for v in nodes}


def _run_figure(genes, edges, node_kinds, title, required_score,
                out_path, layout_seed) -> Dict[str, Any]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    import numpy as np

    # ---- graph construction (plain dicts; no networkx needed) ----
    adj: Dict[str, set] = {}
    edge_list: List[List[str]] = []
    if edges:
        for u, v in edges:
            if u == v:
                continue
            adj.setdefault(u, set()).add(v)
            adj.setdefault(v, set()).add(u)
            edge_list.append([u, v])
        fetch_note = "custom edge list (user-supplied)"
    elif genes:
        # synchronous STRING fetch (tool runs in worker thread context)
        import httpx
        url = ("https://string-db.org/api/tsv/network"
               f"?identifiers={'%0d'.join(genes)}"
               f"&species=9606&required_score={required_score}")
        r = httpx.get(url, timeout=60)
        r.raise_for_status()
        n_e = 0
        for line in r.text.splitlines()[1:]:
            if "\t" not in line:
                continue
            f = line.split("\t")
            if len(f) > 3:
                u, v = f[2], f[3]
                adj.setdefault(u, set()).add(v)
                adj.setdefault(v, set()).add(u)
                edge_list.append([u, v])
                n_e += 1
        fetch_note = (f"STRING v12 live network ({n_e} edges, "
                      f"score>={required_score})")
    else:
        return {"error": "provide genes or edges"}

    nodes = list(adj)
    if not nodes:
        return {"error": "network empty after fetch"}
    degrees = {v: len(adj[v]) for v in nodes}
    hubs = sorted(degrees, key=lambda v: -degrees[v])
    n_nodes, n_edges = len(nodes), len(edge_list)
    pos = _fr_layout(nodes, edge_list, seed=layout_seed)

    kind_color = {"ingredient": "#58a6ff", "target": "#3fb950",
                  "disease": "#d29922"}
    max_deg = max(degrees.values()) if degrees else 1
    t1_cut = max_deg * 0.7
    t2_cut = max_deg * 0.4
    node_colors, node_sizes = [], []
    for v in nodes:
        kind = (node_kinds or {}).get(v, "target")
        deg = degrees[v]
        node_sizes.append(300 + 1900 * (deg / max_deg) ** 2)
        # hub tier colouring applies to non-ingredient nodes (ingredients
        # keep their layer colour); tiers match the legend
        if kind != "ingredient" and deg >= t1_cut:
            node_colors.append("#f85149")      # hub tier 1
        elif kind != "ingredient" and deg >= t2_cut:
            node_colors.append("#d29922")      # hub tier 2
        else:
            node_colors.append(kind_color.get(kind, "#3fb950"))

    fig, ax = plt.subplots(figsize=(16, 11), dpi=200)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    # edges
    segs = [[pos[u], pos[v]] for u, v in edge_list]
    ax.add_collection(LineCollection(segs, colors="#6e7681",
                                     alpha=0.35, linewidths=0.8))
    # nodes
    xs = np.array([pos[v][0] for v in nodes])
    ys = np.array([pos[v][1] for v in nodes])
    ax.scatter(xs, ys, c=node_colors, s=node_sizes, alpha=0.9,
               edgecolors="#24292f", linewidths=0.6, zorder=3)
    # labels for top-degree nodes
    label_top = set(hubs[: min(25, len(hubs))])
    for v in nodes:
        if v in label_top:
            ax.text(pos[v][0], pos[v][1] + 0.018, v, fontsize=8,
                    ha="center", va="bottom", zorder=4,
                    fontfamily="Microsoft YaHei")
    # hub tier legend (counts EXCLUDE ingredient-layer nodes, matching
    # the tier colouring which applies to non-ingredient nodes only)
    non_ing = [v for v in nodes
               if (node_kinds or {}).get(v, "target") != "ingredient"]
    t1 = [v for v in non_ing if degrees[v] >= t1_cut]
    t2 = [v for v in non_ing if t2_cut <= degrees[v] < t1_cut]
    handles = [
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#f85149",
                   markersize=10, label=f"hub tier 1 (deg>={round(t1_cut, 1)}): {len(t1)}"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#d29922",
                   markersize=10, label=f"hub tier 2 (deg>={round(t2_cut, 1)}): {len(t2)}"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#3fb950",
                   markersize=10, label="other nodes")]
    if node_kinds:
        for kind, col in kind_color.items():
            handles.append(plt.Line2D([0], [0], marker="o", color="w",
                                      markerfacecolor=col, markersize=10,
                                      label=f"layer: {kind}"))
    ax.legend(handles=handles, loc="upper left", fontsize=8, framealpha=0.9)
    ax.set_title(f"{title}\n", fontsize=15, fontfamily="Microsoft YaHei")
    cap = (f"topological network | {fetch_note} | nodes={n_nodes} "
           f"edges={n_edges} | hub ranking: degree | "
           f"computational evidence tier | queried_at {_now_iso()}")
    ax.text(0.5, -0.02, cap, transform=ax.transAxes, ha="center",
            fontsize=7, color="#57606a", wrap=True)
    ax.set_xlim(-1.12, 1.12)
    ax.set_ylim(-1.12, 1.12)
    ax.axis("off")
    fig.tight_layout()
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=200, bbox_inches="tight", facecolor="white")
    pdf = out.with_suffix(".pdf")
    fig.savefig(pdf, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    from PIL import Image
    with Image.open(out) as im:
        size = im.size
    return {"png": str(out), "pdf": str(pdf), "pixels": list(size),
            "nodes": n_nodes, "edges": n_edges,
            "top_hubs": hubs[:10],
            "hub_degrees": {v: degrees[v] for v in hubs[:10]},
            "source": fetch_note}


@mcp.tool(
    name="tcm_network_figure",
    annotations={"title": "Publication Network Figure (3200x2200, hub-tier)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_network_figure(params: NetworkFigureInput) -> str:
    '''Render a publication-grade network figure (PNG 3200x2200 + PDF).

    Two data modes:
      - genes=[...]: fetches the LIVE STRING PPI network (score>=700)
        and renders it with hub-tier colouring
      - edges=[[u,v],...]: renders a CUSTOM network (e.g. ingredient-target
        pairs from tcm_formula_analysis / cross screening), optionally
        multi-layer via node_kinds (ingredient|target|disease colouring)

    Layout: built-in Fruchterman-Reingold force-directed layout (pure
    numpy, fixed seed - reproducible, no networkx dependency);
    hub nodes sized/coloured by degree tier; top-25 labels drawn; the
    caption carries the data source, node/edge counts, hub ranking rule,
    the computational-evidence-tier statement and queried_at - ready for
    supervisor review.

    Args:
        params (NetworkFigureInput): genes OR edges (+ node_kinds for
            multi-layer), title, out_path, layout_seed.

    Returns:
        str: output paths, pixel size, top hubs, source note.

    Examples:
        - "Draw the UC hub-gene network" -> genes=[...], out_path="...png"
        - "Draw the ingredient-target network of the Huanglian pair"
          -> edges=[["berberine","TNF"],...],
          node_kinds={"berberine":"ingredient","TNF":"target"}
    '''
    if not params.genes and not params.edges:
        return "Error: provide genes or edges."
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            None, lambda: _run_figure(
                params.genes, params.edges, params.node_kinds,
                params.title, params.required_score, params.out_path,
                params.layout_seed))
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"
    if params.response_format == "json":
        return json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if result.get("error"):
        return f"Error: {result['error']}"
    lines = [f"# Network figure: {params.title}", ""]
    lines.append(f"- png: {result['png']}")
    lines.append(f"- pdf: {result['pdf']}")
    lines.append(f"- pixels: {result['pixels'][0]}x{result['pixels'][1]}")
    lines.append(f"- layout: built-in Fruchterman-Reingold (numpy, "
                 f"seed={params.layout_seed})")
    lines.append(f"- network: {result['nodes']} nodes / "
                 f"{result['edges']} edges")
    lines.append(f"- top hubs: {result['top_hubs']}")
    lines.append(f"- hub degrees: {json.dumps(result['hub_degrees'])}")
    lines.append(f"- source: {result['source']}")
    lines.append(f"- discipline: caption embeds the computational-evidence "
                 f"tier statement; hub ranking is degree-based topology")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)
