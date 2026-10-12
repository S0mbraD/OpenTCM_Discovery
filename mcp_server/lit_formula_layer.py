#!/usr/bin/env python3
"""lit_formula_layer: literature evidence + formula compatibility layer.

Two P0 tools from the phase-3 plan:

  tcm_literature_mine   - PubMed E-utilities co-occurrence mining:
                          compound x gene x disease literature counts
                          (full triple + pairwise edges), yearly trend,
                          bibliometric evidence for the evidence-grading
                          discipline (co-occurrence = literature signal,
                          NEVER causal validation)
  tcm_formula_analysis  - herb-pair / formula compatibility analysis on
                          the local SymMap herb-molecule index (698 herbs,
                          25,975 molecules): ingredient-set overlap /
                          complementarity (Jaccard), per-herb uniqueness,
                          optional target-level layer via SymMap lookup

Both keep the hard disciplines: live retrieval with source + queried_at,
computational/bibliometric tier labelling, atomic writes.
"""
import asyncio
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

from pydantic import BaseModel, Field, ConfigDict

mcp = _MCPBase("tcm_lit_formula_layer")

SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))
from tcm_core.paths import herb_index_candidates  # noqa: E402


def _index_candidates() -> List[Path]:
    """TCM_HERB_INDEX when set, otherwise <repo>/data/herb_mol_index.json."""
    return herb_index_candidates(SKILL_ROOT)

_FNS: Dict[str, Callable] = {}


def attach_tools(fn_map: Dict[str, Callable]) -> None:
    _FNS.update(fn_map)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _load_index() -> Optional[Dict]:
    for p in _index_candidates():
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return None


# ================= tool 1: tcm_literature_mine =================

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
_REQ_INTERVAL = 0.4  # conservative vs 3 req/s policy (no API key)
_last_req = {"t": 0.0}


async def _pubmed_count(term: str) -> Dict[str, Any]:
    """One esearch count call, rate-limited, with retry/backoff."""
    import httpx
    wait = _REQ_INTERVAL - (time.time() - _last_req["t"])
    if wait > 0:
        await asyncio.sleep(wait)
    params = {"db": "pubmed", "retmode": "json", "term": term,
              "retmax": 0}
    for attempt in range(3):
        try:
            _last_req["t"] = time.time()
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(PUBMED_ESEARCH, params=params)
            if r.status_code == 429 or r.status_code >= 500:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            d = r.json().get("esearchresult", {})
            return {"count": int(d.get("count", 0)), "term": term,
                    "query_key": d.get("querykey"),
                    "warning": d.get("warninglist")}
        except (httpx.HTTPError, ValueError, KeyError) as e:
            if attempt == 2:
                return {"error": f"{type(e).__name__}: {e}", "term": term}
            await asyncio.sleep(1.5 * (attempt + 1))
    return {"error": "retries exhausted", "term": term}


def _quoted(s: str) -> str:
    return f'"{s}"[Title/Abstract]'


class LiteratureMineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compound: str = Field(..., min_length=2, max_length=100,
                          description="compound name (e.g. berberine)")
    genes: Optional[List[str]] = Field(
        None, max_length=10,
        description="target gene symbols; multiple genes are OR-ed inside "
                    "one count query")
    disease: Optional[str] = Field(
        None, max_length=100,
        description="disease name (e.g. ulcerative colitis)")
    yearly_trend: bool = Field(
        True, description="count the full-triple query per publication year "
                          "(last 10 years)")
    response_format: str = Field("markdown", description="markdown | json")


@mcp.tool(
    name="tcm_literature_mine",
    annotations={"title": "PubMed Co-occurrence Mining (bibliometric "
                          "evidence layer)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": True})
async def tcm_literature_mine(params: LiteratureMineInput) -> str:
    '''Mine PubMed co-occurrence counts for compound/gene/disease evidence.

    Uses NCBI E-utilities esearch (live, rate-limited). Given a compound
    and at least one of genes/disease, produces:
      - full-triple literature count (compound + genes OR-ed + disease)
      - pairwise edge counts (compound-disease, compound-gene,
        gene-disease) forming a bibliometric evidence triangle
      - yearly trend of the full-triple count (last 10 years) when enabled

    INTERPRETATION DISCIPLINE: co-occurrence is BIBLIOMETRIC evidence -
    it measures how much the literature has studied a relationship, not
    whether the relationship is validated. Pair it with CTD curated
    counts (tcm_ctd_gene_interactions) and mechanistic reads before any
    biological claim. Gene symbols OR-ed in one query keep the request
    count small; per-gene breakdown is available by calling again.

    Args:
        params (LiteratureMineInput): compound + genes and/or disease.

    Returns:
        str: evidence triangle counts, yearly trend, interpretation note,
        per-query provenance (term text + count + timestamp).

    Examples:
        - "How much literature supports berberine for UC via TNF?"
          -> compound="berberine", genes=["TNF"], disease="ulcerative colitis"
    '''
    if not params.genes and not params.disease:
        return ("Error: provide genes and/or disease for co-occurrence "
                "(compound alone is not a relationship).")
    c = _quoted(params.compound)
    g = None
    if params.genes:
        g = "(" + " OR ".join(_quoted(x) for x in params.genes) + ")"
    d = _quoted(params.disease) if params.disease else None

    queries: Dict[str, str] = {}
    triple_parts = [c] + ([g] if g else []) + ([d] if d else [])
    queries["full_triple"] = " AND ".join(triple_parts)
    if d:
        queries["compound_disease"] = f"{c} AND {d}"
    if g:
        queries["compound_gene"] = f"{c} AND {g}"
        if d:
            queries["gene_disease"] = f"{g} AND {d}"

    results: Dict[str, Any] = {}
    for name, term in queries.items():
        results[name] = await _pubmed_count(term)
        results[name]["queried_at"] = _now_iso()

    trend = []
    if params.yearly_trend and params.disease:
        year = datetime.now(timezone.utc).year
        for y in range(year - 9, year + 1):
            term = (f'{queries["full_triple"]} AND {y}[Date - Publication]')
            r = await _pubmed_count(term)
            trend.append({"year": y, "count": r.get("count"),
                          "queried_at": _now_iso()})

    data = {
        "compound": params.compound, "genes": params.genes,
        "disease": params.disease,
        "counts": {k: v.get("count", v.get("error"))
                   for k, v in results.items()},
        "queries": results,
        "yearly_trend": trend,
        "interpretation": [
            "co-occurrence counts = bibliometric signal (how studied), "
            "NOT causal validation",
            "evidence triangle: full_triple vs the three pairwise edges "
            "shows which link dominates the literature",
            "pair with tcm_ctd_gene_interactions (curated counts) for a "
            "complementary view"],
        "source": "NCBI E-utilities esearch (live, rate-limited 0.4s/req)",
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = [f"# PubMed literature evidence: {params.compound}", ""]
    lines.append("| query | count |")
    lines.append("|---|---|")
    for name in ("full_triple", "compound_disease", "compound_gene",
                 "gene_disease"):
        if name in results:
            lines.append(f"| {name} | {results[name].get('count', 'ERR')} |")
    if trend:
        lines.append("")
        lines.append("## Yearly trend (full_triple, last 10 years)")
        lines.append("| " + " | ".join(str(t["year"]) for t in trend)
                     + " |")
        lines.append("|" + "---|" * len(trend))
        lines.append("| " + " | ".join(str(t["count"]) for t in trend)
                     + " |")
    lines.append("")
    for note in data["interpretation"]:
        lines.append(f"- {note}")
    lines.append(f"- source: {data['source']}")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)


# ================= tool 2: tcm_formula_analysis =================

class FormulaAnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    herbs: List[str] = Field(
        ..., min_length=2, max_length=8,
        description="herb names in Chinese (黄连), pinyin (Huanglian) or "
                    "latin (Coptidis Rhizoma) - matched against the local "
                    "SymMap index")
    include_targets: bool = Field(
        False, description="ALSO compute target-level complementarity via "
                           "SymMap lookups on each herb's first N molecules "
                           "(slower: live API calls)")
    top_mols_for_targets: int = Field(
        5, ge=1, le=15, description="molecules per herb used in the "
        "target-level layer")
    response_format: str = Field("markdown", description="markdown | json")


def _match_herbs(index: Dict, names: List[str]) -> Dict[str, Any]:
    """Match herb names (Chinese/pinyin/latin/SMHB id) to index entries."""
    herbs = index["herbs"]
    hm = index["herb_to_mols"]
    matched, missing = {}, []
    for name in names:
        key = name.strip()
        hit = None
        if key in herbs:                       # SMHB id
            hit = key
        for hid, h in herbs.items():
            if (h.get("chinese_name") == key
                    or (h.get("pinyin") or "").lower() == key.lower()
                    or (h.get("latin_name") or "").lower() == key.lower()):
                hit = hid
                break
        if hit:
            matched[key] = {"herb_id": hit,
                            "meta": herbs[hit],
                            "n_mols": len(hm.get(hit, [])),
                            "mols": set(hm.get(hit, []))}
        else:
            missing.append(name)
    return {"matched": matched, "missing": missing}


def _pairwise_overlap(sets: Dict[str, set]) -> List[Dict[str, Any]]:
    """Jaccard + overlap coefficient for every herb pair."""
    keys = list(sets)
    rows = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = sets[keys[i]], sets[keys[j]]
            inter = a & b
            union = a | b
            jacc = len(inter) / len(union) if union else 0.0
            overlap = len(inter) / min(len(a), len(b)) if a and b else 0.0
            # complementarity: fraction of each set unique to one side
            uniq = 1.0 - overlap
            label = ("high-complementarity 协同候选"
                     if overlap < 0.15 else
                     "partial-overlap 部分互补"
                     if overlap < 0.4 else
                     "high-overlap 功效相近")
            rows.append({
                "pair": f"{keys[i]} + {keys[j]}",
                "shared_mols": len(inter), "mols_a": len(a),
                "mols_b": len(b), "jaccard": round(jacc, 4),
                "overlap_coeff": round(overlap, 4),
                "complementarity": round(uniq, 4),
                "label": label})
    rows.sort(key=lambda r: -r["complementarity"])
    return rows


@mcp.tool(
    name="tcm_formula_analysis",
    annotations={"title": "Herb-Pair / Formula Compatibility Analysis "
                          "(SymMap index)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_formula_analysis(params: FormulaAnalysisInput) -> str:
    '''Analyse herb-pair / formula ingredient compatibility (计算配伍分析).

    Matches each herb against the LOCAL SymMap herb-molecule index (698
    herbs / 25,975 molecules, source: SymMap v2.0 related_components),
    then computes per-pair:
      - shared ingredient count, Jaccard index, overlap coefficient
      - complementarity (fraction of molecules unique to one side)
      - a three-level compatibility label (协同候选 / 部分互补 / 功效相近)
    Optional target-level layer: resolves each herb's first N molecules
    against SymMap to compare target sets (slower, live calls).

    INTERPRETATION DISCIPLINE: this is a SET-BASED computational analysis
    of ingredient/target overlap - high complementarity suggests a
    plausible synergistic rationale worth testing, it does NOT prove
    synergy. Cross-check with literature (tcm_literature_mine) and
    validated ADME gates before biological interpretation.

    Args:
        params (FormulaAnalysisInput): herbs (2-8) + optional target layer.

    Returns:
        str: per-herb molecule counts, pairwise overlap table sorted by
        complementarity, optional target-level matrix, interpretation.

    Examples:
        - "Analyze the Huanglian-Huangbai herb pair" -> herbs=["黄连","黄柏"]
        - "Compare Sanhuang formula components" -> herbs=["黄连","黄芩","黄柏"]
    '''
    index = _load_index()
    if index is None:
        return ("Error: herb_mol_index.json not found. Expected "
                "<repo>/data/herb_mol_index.json, or set TCM_HERB_INDEX. "
                "Rebuild with: python scripts/disease_pathway_workflow.py "
                "build-index --out data")
    m = _match_herbs(index, params.herbs)
    if len(m["matched"]) < 2:
        return (f"Error: only {len(m['matched'])} of {len(params.herbs)} "
                f"herbs matched (missing: {m['missing']}); need >= 2.")
    sets = {k: v["mols"] for k, v in m["matched"].items()}
    pairs = _pairwise_overlap(sets)

    target_layer = None
    if params.include_targets and _FNS.get("tcm_symmap_lookup"):
        per_herb_targets: Dict[str, set] = {}
        lookup_errors = []
        for key, v in m["matched"].items():
            mols = sorted(v["mols"])[: params.top_mols_for_targets]
            tg: set = set()
            for mol in mols:
                r = await _invoke("tcm_symmap_lookup", 60,
                                  mode="component", query=mol)
                hits = (r.get("targets") or r.get("related_targets")
                        or []) if isinstance(r, dict) else []
                if isinstance(hits, list):
                    for t in hits:
                        sym = (t.get("Gene_symbol") if isinstance(t, dict)
                               else str(t))
                        if sym:
                            tg.add(sym)
                elif isinstance(r, dict) and r.get("error"):
                    lookup_errors.append(f"{mol}: {r['error'][:80]}")
            per_herb_targets[key] = tg
        target_layer = {
            "per_herb_n_targets": {k: len(v)
                                   for k, v in per_herb_targets.items()},
            "pairwise": _pairwise_overlap(per_herb_targets),
            "lookup_errors": lookup_errors[:10]}

    data = {
        "herbs_matched": {k: {"herb_id": v["herb_id"],
                              "chinese_name": v["meta"].get("chinese_name"),
                              "latin_name": v["meta"].get("latin_name"),
                              "n_molecules": v["n_mols"]}
                          for k, v in m["matched"].items()},
        "herbs_missing": m["missing"],
        "pairwise_ingredients": pairs,
        "target_layer": target_layer,
        "index_source": index.get("source"),
        "interpretation": [
            "set-based computational compatibility on the SymMap "
            "ingredient index - high complementarity is a plausible "
            "synergy rationale, NOT proof of synergy",
            "high-overlap pairs share molecular space (redundant or "
            "potency-enhancing rationale); low-overlap pairs cover "
            "distinct targets (multi-target rationale)",
            "cross-check with tcm_literature_mine and validated ADME "
            "gates before interpretation"],
        "queried_at": _now_iso()}
    if params.response_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2, default=str)
    lines = ["# Herb-pair / formula compatibility analysis", ""]
    lines.append("## Matched herbs (SymMap index)")
    lines.append("| input | herb id | latin name | molecules |")
    lines.append("|---|---|---|---|")
    for k, v in data["herbs_matched"].items():
        lines.append(f"| {k} | {v['herb_id']} | {v['latin_name']} | "
                     f"{v['n_molecules']} |")
    if m["missing"]:
        lines.append(f"- unmatched: {m['missing']}")
    lines.append("")
    lines.append("## Ingredient-set overlap (sorted by complementarity)")
    lines.append("| pair | shared | jaccard | overlap | complementarity | "
                 "label |")
    lines.append("|---|---|---|---|---|---|")
    for p in pairs:
        lines.append(f"| {p['pair']} | {p['shared_mols']} | "
                     f"{p['jaccard']} | {p['overlap_coeff']} | "
                     f"{p['complementarity']} | {p['label']} |")
    if target_layer:
        lines.append("")
        lines.append("## Target-level layer (first N molecules per herb)")
        lines.append(f"- per-herb target counts: "
                     f"{json.dumps(target_layer['per_herb_n_targets'])}")
        for p in target_layer["pairwise"]:
            lines.append(f"- {p['pair']}: overlap {p['overlap_coeff']} "
                         f"({p['label']})")
    lines.append("")
    for note in data["interpretation"]:
        lines.append(f"- {note}")
    lines.append(f"- index source: {data['index_source']}")
    lines.append(f"- queried_at: {_now_iso()}")
    return "\n".join(lines)


async def _invoke(tool: str, timeout: float, **kwargs) -> Dict[str, Any]:
    """Call an attached tool fn (params wrapper)."""
    fn = _FNS.get(tool)
    if fn is None:
        return {"status": "error", "error": f"tool {tool} not available"}
    import inspect
    try:
        ann = inspect.signature(fn).parameters["params"].annotation
    except (KeyError, TypeError):
        ann = None
    try:
        obj = ann(**kwargs) if ann is not None else None
        if obj is not None:
            raw = await asyncio.wait_for(fn(params=obj), timeout=timeout)
        else:
            raw = await asyncio.wait_for(fn(**kwargs), timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "timeout", "error": f"{tool} timeout"}
    except Exception as e:
        return {"status": "error", "error": f"{tool}: {type(e).__name__}"}
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
        return {"status": "ok", "markdown": raw[:3000]}
    return {"status": "ok", "raw": str(raw)[:3000]}
