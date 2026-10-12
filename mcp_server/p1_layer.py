#!/usr/bin/env python3
"""p1_layer: cell-resolution discovery + data-driven ranking + local ADMET.

Three P1 tools (post-probe adjustments, dead ends recorded in insights):

  tcm_graph_embed   - node2vec-style graph embedding for target
                      prioritization: biased random walks -> PMI ->
                      truncated-SVD embeddings (pure numpy, no deps),
                      cosine similarity to disease seed genes ranks
                      candidate targets; compared against the
                      degree-based centrality view (dual-perspective
                      consensus)
  tcm_scrna_channel - GEO dataset discovery for single-cell evidence:
                      esearch db=gds + esummary (ALIVE, verified); returns
                      GSE accessions, titles, designs, organisms and linked
                      PubMed ids. HPA/GTEx expression APIs are DEAD (404 /
                      SSL) - this tool is discovery-only by design
  tcm_admet_predict - LOCAL approximation ADMET panel (ADMETlab REST dead):
                      ESOL aqueous solubility (Delaney linear model),
                      Crippen LogS, BBB permeability and plasma-protein
                      -binding RULE estimates - every value labelled with
                      its model type and limitations

Disciplines: all outputs carry model-type labels (approximation /
rule / embedding), source + queried_at where live; embedding ranking is
a computational view, never validated essentiality.
"""
import asyncio
import json
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

from pydantic import BaseModel, Field, ConfigDict

mcp = _MCPBase("tcm_p1_layer")

_FNS: Dict[str, Callable] = {}


def attach_tools(fn_map: Dict[str, Callable]) -> None:
    _FNS.update(fn_map)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ================= tool 1: tcm_graph_embed =================
# node2vec-style embedding, pure numpy: biased walks -> PMI -> tSVD.

def _biased_walks(adj: Dict[str, set], n_walks: int, walk_len: int,
                  p: float, q: float, seed: int) -> List[List[str]]:
    """Second-order random walks (node2vec p/q semantics)."""
    rng = random.Random(seed)
    nodes = list(adj)
    walks = []
    for _ in range(n_walks):
        rng.shuffle(nodes)
        for start in nodes:
            walk = [start]
            prev = None
            cur = start
            for _step in range(walk_len - 1):
                nbrs = list(adj.get(cur, ()))
                if not nbrs:
                    break
                if prev is None:
                    nxt = rng.choice(nbrs)
                else:
                    prev_nbrs = adj.get(prev, set())
                    weights = []
                    for nb in nbrs:
                        if nb == prev:
                            weights.append(1.0 / p)
                        elif nb in prev_nbrs:
                            weights.append(1.0)
                        else:
                            weights.append(1.0 / q)
                    tot = sum(weights)
                    r = rng.random() * tot
                    acc = 0.0
                    nxt = nbrs[-1]
                    for nb, w in zip(nbrs, weights):
                        acc += w
                        if acc >= r:
                            nxt = nb
                            break
                walk.append(nxt)
                prev, cur = cur, nxt
            walks.append(walk)
    return walks


def _pmi_embeddings(walks: List[List[str]], window: int, dim: int
                    ) -> Dict[str, Any]:
    import numpy as np
    counts = defaultdict(lambda: defaultdict(int))
    node_freq = defaultdict(int)
    for walk in walks:
        for i, v in enumerate(walk):
            node_freq[v] += 1
            lo, hi = max(0, i - window), min(len(walk), i + window + 1)
            for j in range(lo, hi):
                if j != i:
                    counts[v][walk[j]] += 1
    vocab = sorted(node_freq)
    idx = {v: i for i, v in enumerate(vocab)}
    n = len(vocab)
    if n == 0:
        return {"error": "empty vocabulary"}
    total = sum(node_freq.values())
    m = np.zeros((n, n), dtype=np.float64)
    for v, nbrs in counts.items():
        for u, c in nbrs.items():
            pmi = max(0.0, (c / total) / ((node_freq[v] / total)
                                          * (node_freq[u] / total)))
            m[idx[v], idx[u]] = pmi
    dim = min(dim, n)
    u, s, _vt = np.linalg.svd(m, full_matrices=False)
    emb = u[:, :dim] * np.sqrt(s[:dim])
    return {"vocab": vocab, "idx": idx, "emb": emb}


def _string_adjacency(genes: List[str], required_score: int
                      ) -> Dict[str, Any]:
    import httpx
    url = ("https://string-db.org/api/tsv/network"
           f"?identifiers={'%0d'.join(genes)}"
           f"&species=9606&required_score={required_score}")
    r = httpx.get(url, timeout=60)
    r.raise_for_status()
    adj: Dict[str, set] = {g: set() for g in genes}
    edges = 0
    for line in r.text.splitlines()[1:]:
        if "\t" not in line:
            continue
        f = line.split("\t")
        a, b = (f[2] if len(f) > 2 else None, f[3] if len(f) > 3 else None)
        if a and b and a in adj and b in adj:
            adj[a].add(b)
            adj[b].add(a)
            edges += 1
    return {"adj": adj, "edges": edges}


class GraphEmbedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    genes: List[str] = Field(
        ..., min_length=8, max_length=100,
        description="network nodes (live STRING PPI, score>=required_score)")
    seed_genes: List[str] = Field(
        ..., min_length=1, max_length=20,
        description="disease seed genes (known disease genes to rank "
                    "candidates against; must be within genes)")
    required_score: int = Field(700, ge=150, le=1000)
    n_walks: int = Field(10, ge=2, le=50)
    walk_len: int = Field(40, ge=10, le=120)
    window: int = Field(5, ge=2, le=10)
    dim: int = Field(32, ge=8, le=64)
    p: float = Field(1.0, ge=0.25, le=4.0,
                     description="return parameter (1=DeepWalk-like)")
    q: float = Field(1.0, ge=0.25, le=4.0,
                     description="in-out parameter (0.5 emphasizes BFS "
                                 "structural equivalence)")
    random_seed: int = Field(42)
    response_format: str = Field("markdown", description="markdown | json")


@mcp.tool(
    name="tcm_graph_embed",
    annotations={"title": "Node2vec Graph-Embedding Target Prioritization",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_graph_embed(params: GraphEmbedInput) -> str:
    '''Rank candidate targets by graph-embedding similarity to seed genes.

    Data-driven complement to degree-based hub ranking: fetches the LIVE
    STRING PPI network, learns node2vec-style embeddings via biased random
    walks (p/q) -> PMI co-occurrence -> truncated SVD (pure numpy, no
    external ML deps), then ranks every non-seed gene by mean cosine
    similarity to the seed genes. The result is cross-referenced against
    the degree-based view: genes ranked high by BOTH perspectives are the
    strongest consensus candidates; embedding-only hits suggest
    structurally-similar but low-degree candidates worth checking.

    Interpretation discipline: embedding proximity captures NETWORK
    TOPOLOGY similarity only (a computational view) - it does not encode
    function, expression or validation. Networks with < 15 nodes are
    rejected (embedding meaningless); consensus with centrality and M2
    database evidence is required before biological claims.

    Args:
        params (GraphEmbedInput): genes + seed_genes + walk/SVD controls.

    Returns:
        str: embedding-similarity ranking, centrality comparison table,
        dual-perspective consensus list, discipline notes.

    Examples:
        - "Prioritize UC candidate targets against known UC genes"
          -> genes=[30 hub genes...], seed_genes=["TNF","IL6","PTGS2"]
    '''
    seeds = [s for s in params.seed_genes if s in params.genes]
    if not seeds:
        return ("Error: no seed gene is inside the gene list; provide "
                "seed_genes that are members of genes.")
    if len(params.genes) < 15:
        return (f"Error: {len(params.genes)} nodes is too few for "
                "meaningful embedding (need >= 15); use "
                "tcm_network_analysis centrality instead.")
    try:
        net = await asyncio.get_running_loop().run_in_executor(
            None, lambda: _string_adjacency(params.genes,
                                            params.required_score))
    except Exception as e:
        return f"Error: STRING fetch failed: {type(e).__name__}: {e}"
    adj = net["adj"]
    if net["edges"] < 5:
        return (f"Error: network too sparse ({net['edges']} edges) - "
                "embedding meaningless; lower required_score.")

    walks = _biased_walks(adj, params.n_walks, params.walk_len,
                          params.p, params.q, params.random_seed)
    res = _pmi_embeddings(walks, params.window, params.dim)
    if "error" in res:
        return f"Error: {res['error']}"

    import numpy as np
    emb, idx = res["emb"], res["idx"]
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    normed = emb / (norms + 1e-12)
    seed_idx = [idx[s] for s in seeds if s in idx]
    seed_vec = normed[seed_idx].mean(axis=0)
    seed_vec /= (np.linalg.norm(seed_vec) + 1e-12)
    sims = normed @ seed_vec

    degree = {g: len(adj[g]) for g in adj}
    rank_emb = sorted(((g, float(sims[idx[g]])) for g in adj
                       if g not in seeds),
                      key=lambda x: -x[1])
    rank_deg = sorted(((g, degree[g]) for g in adj if g not in seeds),
                      key=lambda x: -x[1])
    deg_rank_of = {g: i for i, (g, _) in enumerate(rank_deg)}
    emb_rank_of = {g: i for i, (g, _) in enumerate(rank_emb)}
    consensus = []
    for g, sim in rank_emb:
        dr = deg_rank_of.get(g, 10 ** 6)
        if emb_rank_of[g] < len(rank_emb) * 0.2 and dr < len(rank_deg) * 0.2:
            consensus.append({"gene": g, "sim": round(sim, 4),
                              "degree": degree[g],
                              "emb_rank": emb_rank_of[g] + 1,
                              "deg_rank": dr + 1})

    data = {
        "network": {"nodes": len(adj), "edges": net["edges"],
                    "required_score": params.required_score},
        "embedding": {"method": "biased walks -> PMI -> truncated SVD",
                      "n_walks": params.n_walks,
                      "walk_len": params.walk_len, "dim": params.dim,
                      "p": params.p, "q": params.q},
        "seed_genes": seeds,
        "embedding_ranking": [
            {"rank": i + 1, "gene": g, "cosine_to_seed": round(s, 4),
             "degree": degree[g]}
            for i, (g, s) in enumerate(rank_emb[:15])],
        "degree_view_top": [
            {"rank": i + 1, "gene": g, "degree": d}
            for i, (g, d) in enumerate(rank_deg[:10])],
        "dual_perspective_consensus": consensus[:10],
        "interpretation": [
            "embedding similarity = network-topology view only "
            "(computational, not functional validation)",
            "consensus genes (top 20% in BOTH embedding and degree views) "
            "are the strongest structural candidates",
            "embedding-only hits (high sim, low degree) are structurally "
            "similar niche candidates - verify with M2 database evidence",
            "requires centrality + cross-database consensus before any "
            "biological claim"],
        "source": "STRING v12 network API (live) + local numpy embedding",
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = [f"# Graph-embedding target prioritization "
             f"({len(adj)} nodes / {net['edges']} edges)", ""]
    lines.append(f"- seeds: {', '.join(seeds)}")
    lines.append(f"- method: {data['embedding']['method']} "
                 f"(p={params.p}, q={params.q}, dim={params.dim})")
    lines.append("")
    lines.append("## Embedding similarity ranking (non-seed, top 15)")
    lines.append("| rank | gene | cos-sim to seed | degree |")
    lines.append("|---|---|---|---|")
    for r in data["embedding_ranking"]:
        lines.append(f"| {r['rank']} | {r['gene']} | "
                     f"{r['cosine_to_seed']} | {r['degree']} |")
    lines.append("")
    lines.append("## Dual-perspective consensus (top 20% both views)")
    if consensus:
        lines.append("| gene | cos-sim | degree | emb rank | degree rank |")
        lines.append("|---|---|---|---|---|")
        for c in consensus[:10]:
            lines.append(f"| {c['gene']} | {c['sim']} | {c['degree']} | "
                         f"{c['emb_rank']} | {c['deg_rank']} |")
    else:
        lines.append("- none (views disagree; treat ranking with caution)")
    lines.append("")
    for note in data["interpretation"]:
        lines.append(f"- {note}")
    lines.append(f"- source: {data['source']}; queried_at: {_now_iso()}")
    return "\n".join(lines)


# ================= tool 2: tcm_scrna_channel =================

GEO_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
GEO_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


async def _geo_search(term: str, retmax: int) -> Dict[str, Any]:
    import httpx
    async with httpx.AsyncClient(timeout=40) as client:
        r = await client.get(GEO_ESEARCH, params={
            "db": "gds", "retmode": "json", "retmax": retmax, "term": term})
        r.raise_for_status()
        d = r.json().get("esearchresult", {})
        return {"count": int(d.get("count", 0)),
                "uids": d.get("idlist", [])}


async def _geo_summary(uids: List[str]) -> List[Dict[str, Any]]:
    import httpx
    rows = []
    for chunk_start in range(0, len(uids), 20):
        chunk = uids[chunk_start:chunk_start + 20]
        async with httpx.AsyncClient(timeout=40) as client:
            r = await client.get(GEO_ESUMMARY, params={
                "db": "gds", "retmode": "json", "id": ",".join(chunk)})
            r.raise_for_status()
            res = r.json().get("result", {})
        for uid in res.get("uids", []):
            it = res.get(uid, {})
            rows.append({
                "uid": uid,
                "accession": it.get("accession"),
                "title": it.get("title", "")[:120],
                "summary": it.get("summary", "")[:220],
                "organism": it.get("taxon"),
                "gsm_count": it.get("n_samples"),
                "platform": it.get("gpl"),
                "pubmed_ids": it.get("pubmedids") or [],
                "study_type": it.get("gdstype"),
                "entry_type": it.get("entrytype")})
        await asyncio.sleep(0.4)
    return rows


class ScrnaChannelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    disease: str = Field(..., min_length=2, max_length=100,
                         description="disease of interest (e.g. ulcerative "
                                     "colitis)")
    genes: Optional[List[str]] = Field(
        None, max_length=10,
        description="optional genes to include in the search term "
                    "(datasets mentioning them)")
    extra_term: Optional[str] = Field(
        None, max_length=120,
        description="extra query fragment (e.g. 'colon organoid')")
    retmax: int = Field(8, ge=1, le=20)
    response_format: str = Field("markdown", description="markdown | json")


@mcp.tool(
    name="tcm_scrna_channel",
    annotations={"title": "GEO Single-Cell Dataset Discovery "
                          "(scRNA evidence channel)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_scrna_channel(params: ScrnaChannelInput) -> str:
    '''Discover GEO single-cell datasets for a disease (cell-resolution
    evidence channel).

    Live NCBI esearch/esummary on db=gds: finds scRNA-seq / scATAC-seq
    datasets for the disease (optionally mentioning specific genes),
    returning accessions (GSE), titles, designs, organisms, sample counts,
    platforms and linked PubMed ids - the entry points for cell-type-
    specific target validation.

    SCOPE NOTE (probe-verified 2026-10-12): HPA APIs (all shapes) return
    404 and the GTEx v2 API fails SSL - automated single-cell EXPRESSION
    verification is currently unavailable as external channels. This tool
    is therefore DISCOVERY-ONLY: use the returned accessions to pull raw
    data (GEO download) or read the linked papers; do not expect cell-type
    expression tables from this call.

    Args:
        params (ScrnaChannelInput): disease + optional genes/extra term.

    Returns:
        str: dataset table (accession/design/organism/samples/PubMed),
        scope note, provenance.

    Examples:
        - "Find single-cell datasets for ulcerative colitis"
          -> disease="ulcerative colitis"
    '''
    term = (f'("{params.disease}"[All Fields]) AND '
            '("single cell"[All Fields] OR "single-cell"[All Fields] '
            'OR scRNA[All Fields])')
    if params.genes:
        term += " AND (" + " OR ".join(
            f'"{g}"[All Fields]' for g in params.genes) + ")"
    if params.extra_term:
        term += f" AND {params.extra_term}"
    try:
        search = await _geo_search(term, params.retmax)
        rows = await _geo_summary(search["uids"]) if search["uids"] else []
    except Exception as e:
        return f"Error: GEO query failed: {type(e).__name__}: {e}"
    data = {
        "disease": params.disease, "genes": params.genes,
        "query_term": term,
        "total_datasets_matching": search["count"],
        "datasets": rows,
        "scope_note": [
            "discovery-only channel: HPA APIs dead (404) and GTEx v2 SSL "
            "fails (probe 2026-10-12); cell-type expression tables are "
            "NOT produced by this tool",
            "use returned GSE accessions for raw-data download or follow "
            "the linked PubMed ids for cell-type-specific findings",
            "expression-level claims require reading the primary dataset/"
            "paper - keep the evidence-grading discipline"],
        "source": "NCBI E-utilities (db=gds, live)",
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = [f"# GEO single-cell datasets: {params.disease}", ""]
    lines.append(f"- total matching: {search['count']} "
                 f"(showing {len(rows)})")
    lines.append("")
    lines.append("## Datasets")
    for r in rows:
        lines.append(f"### {r.get('accession') or r.get('uid')} "
                     f"({r.get('organism')})")
        lines.append(f"- title: {r.get('title')}")
        if r.get("summary"):
            lines.append(f"- design: {r.get('summary')}")
        lines.append(f"- samples: {r.get('gsm_count')} | platform: "
                     f"{r.get('platform')} | type: {r.get('study_type')}")
        if r.get("pubmed_ids"):
            lines.append(f"- PubMed: {', '.join(map(str, r['pubmed_ids'][:4]))}")
    lines.append("")
    for note in data["scope_note"]:
        lines.append(f"- {note}")
    lines.append(f"- source: {data['source']}; queried_at: {_now_iso()}")
    return "\n".join(lines)


# ================= tool 3: tcm_admet_predict =================

try:
    from rdkit import Chem
    from rdkit.Chem import Crippen, Lipinski, rdMolDescriptors
    _HAS_RDKIT = True
except ImportError:
    _HAS_RDKIT = False


async def _resolve_smiles_list(names: List[str]) -> List[Dict[str, Any]]:
    import httpx
    out = []
    sem = asyncio.Semaphore(2)

    async def one(name: str) -> Dict[str, Any]:
        url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
               f"{name}/property/CanonicalSMILES,ConnectivitySMILES/JSON")
        async with sem:
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    r = await client.get(url)
                if r.status_code == 404:
                    return {"name": name, "error": "not in PubChem"}
                r.raise_for_status()
                p = (r.json().get("PropertyTable", {})
                     .get("Properties", [{}])[0])
                smi = p.get("CanonicalSMILES") or p.get("ConnectivitySMILES")
                return {"name": name, "smiles": smi,
                        "queried_at": _now_iso()} if smi else \
                    {"name": name, "error": "no SMILES"}
            except Exception as e:
                return {"name": name,
                        "error": f"{type(e).__name__}"}
    return await asyncio.gather(*[one(n) for n in names])


def _admet_local(smiles: str) -> Optional[Dict[str, Any]]:
    """Local approximation ADMET panel (each item labelled by model)."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    import math
    mw = rdMolDescriptors.CalcExactMolWt(mol)
    logp = Crippen.MolLogP(mol)
    tpsa = rdMolDescriptors.CalcTPSA(mol)
    rotb = Lipinski.NumRotatableBonds(mol)
    rings = mol.GetRingInfo().AtomRings()
    n_rings = len(rings)
    n_arom = sum(1 for ring in rings
                 if all(mol.GetAtomWithIdx(a).GetIsAromatic()
                        for a in ring))
    arom_ratio = (n_arom / n_rings) if n_rings else 0.0

    # ESOL (Delaney 2004 linear model) - aqueous solubility log(mol/L)
    esol = (0.16 - 0.63 * logp - 0.0062 * mw
            + 0.066 * rotb - 0.74 * arom_ratio)
    # Crippen-style LogS rough estimate (solubility from LogP/MW heuristic)
    log_s_est = 0.5 - logp - 0.01 * (mw - 20)
    # BBB rule estimate (Clark/Pardridge-inspired box rule)
    bbb_pos = (tpsa <= 70 and 1 <= logp <= 5 and mw <= 450)
    bbb_label = ("BBB+ (rule: TPSA<=70, LogP 1-5, MW<=450)" if bbb_pos
                 else "BBB- (violates box rule)")
    # Plasma protein binding rule estimate
    ppb_label = ("high-binding expected (rule: LogP>4.5)" if logp > 4.5
                 else "moderate (LogP 2-4.5)" if logp > 2
                 else "low-binding (LogP<2)")
    # Caco-2-like rough permeability proxy (TPSA/LogP rule)
    caco = ("high-permeability proxy (TPSA<75, LogP>1)" if
            (tpsa < 75 and logp > 1) else "low/moderate permeability proxy")
    # hERG risk proxy (basic + high LogP rule-of-thumb)
    has_basic_n = any(a.GetAtomicNum() == 7 and not a.IsInRing()
                      and a.GetFormalCharge() >= 0
                      and a.GetTotalNumHs() > 0 for a in mol.GetAtoms())
    herg = ("possible hERG risk (basic center + LogP>3.5 rule)"
            if (has_basic_n and logp > 3.5)
            else "no obvious hERG rule flag")
    return {
        "esol_log_mol_l": round(esol, 3),
        "esol_model": "Delaney 2004 linear regression (approximation)",
        "logs_est": round(log_s_est, 3),
        "logs_model": "LogP/MW heuristic (rough)",
        "bbb": bbb_label,
        "ppb": ppb_label,
        "caco2_proxy": caco,
        "herg_proxy": herg,
        "descriptors": {"mw": round(mw, 2), "logp": round(logp, 2),
                        "tpsa": round(tpsa, 2), "rotb": rotb,
                        "n_rings": n_rings, "n_arom": n_arom}}


class AdmetPredictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compounds: Optional[List[str]] = Field(
        None, max_length=15, description="compound names (live PubChem "
                                         "SMILES resolution)")
    smiles_list: Optional[List[str]] = Field(
        None, max_length=15, description="raw SMILES (skip lookup)")
    response_format: str = Field("markdown", description="markdown | json")


@mcp.tool(
    name="tcm_admet_predict",
    annotations={"title": "Local ADMET Approximation Panel "
                          "(ESOL/BBB/PPB/hERG proxies)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_admet_predict(params: AdmetPredictInput) -> str:
    '''Local approximation ADMET panel for candidate compounds.

    Scope (probe-verified 2026-10-12): ADMETlab 2.0 SSL is expired and
    ADMETlab 3.0 exposes no public REST prediction endpoint - so this tool
    computes LOCAL approximations only:
      - ESOL aqueous solubility (Delaney 2004 linear model on RDKit
        descriptors)
      - rough LogS (LogP/MW heuristic)
      - BBB permeability (box rule: TPSA<=70, LogP 1-5, MW<=450)
      - plasma protein binding tier (LogP rule)
      - Caco-2-like permeability proxy (TPSA/LogP rule)
      - hERG risk proxy (basic center + LogP rule)

    EVERY value is a labelled approximation/rule - suitable for triage and
    rough comparison, NOT for regulatory-grade ADMET conclusions. For
    publication-grade values use dedicated ADMET software on your own.

    Args:
        params (AdmetPredictInput): compounds (names) OR smiles_list.

    Returns:
        str: per-compound ADMET panel with per-item model labels +
        limitations note.

    Examples:
        - "Approximate ADMET for berberine and baicalin"
          -> compounds=["berberine", "baicalin"]
    '''
    if not params.compounds and not params.smiles_list:
        return "Error: provide compounds or smiles_list."
    if params.compounds and params.smiles_list:
        return "Error: provide only ONE of compounds or smiles_list."
    if not _HAS_RDKIT:
        return "Error: RDKit unavailable (pip install rdkit)."
    rows, provenance = [], []
    if params.compounds:
        resolved = await _resolve_smiles_list(params.compounds)
        for r in resolved:
            if r.get("error"):
                rows.append({"compound": r["name"], "status": "error",
                             "error": r["error"]})
                continue
            panel = _admet_local(r["smiles"])
            rows.append({"compound": r["name"], "status": "ok",
                         "smiles": r["smiles"],
                         **(panel or {"error": "RDKit parse failed"})})
            provenance.append({"compound": r["name"],
                               "source": "PubChem PUG-REST",
                               "queried_at": r.get("queried_at")})
    else:
        for smi in params.smiles_list:
            panel = _admet_local(smi)
            rows.append({"compound": smi[:40], "status": "ok",
                         "smiles": smi,
                         **(panel or {"error": "RDKit parse failed"})})
    data = {
        "n": len(rows), "panel": rows,
        "limitations": [
            "ALL values are local approximations or box rules "
            "(ADMETlab REST dead per probe 2026-10-12)",
            "ESOL = Delaney linear model (typical error ~1 log unit)",
            "BBB/PPB/Caco-2/hERG are simple descriptor rules - triage "
            "only, never regulatory-grade",
            "combine with tcm_adme_profile (Lipinski/Veber/QED) and the "
            "TCMSP OB/DL gates for the full picture"],
        "provenance": provenance,
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = [f"# Local ADMET approximation panel ({len(rows)})", ""]
    lines.append("| compound | ESOL logS | LogS est | BBB | PPB | "
                 "Caco-2 proxy | hERG proxy |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in rows:
        if r.get("status") == "ok":
            lines.append(
                f"| {r['compound']} | {r.get('esol_log_mol_l', '-')} | "
                f"{r.get('logs_est', '-')} | "
                f"{(r.get('bbb') or '-')[:30]} | "
                f"{(r.get('ppb') or '-')[:24]} | "
                f"{(r.get('caco2_proxy') or '-')[:26]} | "
                f"{(r.get('herg_proxy') or '-')[:30]} |")
        else:
            lines.append(f"| {r['compound']} | ERROR: "
                         f"{r.get('error', '?')} | | | | | |")
    lines.append("")
    for note in data["limitations"]:
        lines.append(f"- {note}")
    for p in provenance:
        lines.append(f"- provenance: {p['compound']} SMILES from "
                     f"{p['source']} at {p['queried_at']}")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)
