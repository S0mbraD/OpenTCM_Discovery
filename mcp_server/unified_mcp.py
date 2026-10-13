#!/usr/bin/env python3
"""unified_mcp: the ONE MCP server for the open-tcm-discovery unified skill.

Combines the retrieval/analysis tool family (27 tools) and the
docking/modeling tool family (8 tools) with the intelligence, parallel,
evidence, P1, P2, bootstrap, iteration, topology, prep, TCMSP, and local
alignment layers into one 64-tool server:

  unified_overview  - first-contact orientation: modules, tool inventory,
                      state health, quick-start protocol
  tcm_smart_plan    - rule-based DYNAMIC ANALYSIS NETWORK planner: turns a
                      research goal into an execution DAG whose branches fire
                      on intermediate results (result-driven routing), so any
                      agent can autonomously chain modules without being told
                      which one to use

Design: loads the two sub-server modules and the smart-layer extension
from this directory, transfers their registered tools verbatim (same
names/annotations), and wires the intelligence layer:

  - read-only retrieval tools get a transparent TTL response cache
    (smart_layer.make_cached_wrapper; original queried_at preserved)
  - smart_layer.attach_tools exposes the underlying fns to the execution
    engine so tcm_smart_run can drive them directly (no protocol hop)
  - sub-servers keep their own telemetry and insight stores; both are
    surfaced by unified_overview.
"""
import argparse
import asyncio
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase   # mcp < 2
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase  # mcp >= 2

from pydantic import BaseModel, Field, ConfigDict

mcp = _MCPBase("tcm_unified_mcp")

BASE = Path(__file__).resolve().parent
TCM_IMPL = BASE / "open_tcm_discovery_mcp.py"
DOCK_IMPL = BASE / "structure_docking_mcp.py"
SMART_IMPL = BASE / "smart_layer.py"
PARALLEL_IMPL = BASE / "parallel_layer.py"
LIT_IMPL = BASE / "lit_formula_layer.py"
RESCORE_IMPL = BASE / "rescore_figure_layer.py"
P1_IMPL = BASE / "p1_layer.py"
P2_IMPL = BASE / "p2_layer.py"
BOOTSTRAP_IMPL = BASE / "bootstrap_layer.py"
ITERATION_IMPL = BASE / "iteration_layer.py"
TOPOLOGY_IMPL = BASE / "topology_layer.py"
PREP_IMPL = BASE / "prep_layer.py"
TCMSP_IMPL = BASE / "tcmsp_layer.py"
ALIGN_IMPL = BASE / "align_layer.py"

# ================= submodule loading + tool transfer =================

def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _transfer_tools(source_mcp, target=mcp, cache_wrap=None):
    '''Copy every registered tool (name + annotations + fn) onto the unified
    server, optionally wrapping the fn (e.g. transparent response cache).
    Falls back to re-decoration if the internal manager moved.'''
    copied, skipped = [], []
    try:
        tools = source_mcp._tool_manager.list_tools()
    except Exception:
        tools = []
    for t in tools:
        try:
            ann = getattr(t, "annotations", None)
            fn = cache_wrap(t.name, t.fn) if cache_wrap else t.fn
            target.add_tool(fn, name=t.name, annotations=ann)
            copied.append(t.name)
        except Exception as e:
            skipped.append(f"{t.name}: {type(e).__name__}")
    if copied or skipped:
        return copied, skipped
    return [], ["tool manager unavailable on this SDK"]


_tcm_mod = _load_module(TCM_IMPL, "tcm_unified_impl_tcm")
_dock_mod = _load_module(DOCK_IMPL, "tcm_unified_impl_dock")
_smart_mod = _load_module(SMART_IMPL, "tcm_unified_impl_smart")
_parallel_mod = _load_module(PARALLEL_IMPL, "tcm_unified_impl_parallel")
_lit_mod = _load_module(LIT_IMPL, "tcm_unified_impl_lit")
_rescore_mod = _load_module(RESCORE_IMPL, "tcm_unified_impl_rescore")
_p1_mod = _load_module(P1_IMPL, "tcm_unified_impl_p1")
_p2_mod = _load_module(P2_IMPL, "tcm_unified_impl_p2")
_bootstrap_mod = _load_module(BOOTSTRAP_IMPL, "tcm_unified_impl_bootstrap")
_iteration_mod = _load_module(ITERATION_IMPL, "tcm_unified_impl_iteration")
_topology_mod = _load_module(TOPOLOGY_IMPL, "tcm_unified_impl_topology")
_prep_mod = _load_module(PREP_IMPL, "tcm_unified_impl_prep")
_tcmsp_mod = _load_module(TCMSP_IMPL, "tcm_unified_impl_tcmsp")
_align_mod = _load_module(ALIGN_IMPL, "tcm_unified_impl_align")

# transparent response cache on read-only retrieval tools (schema unchanged;
# original queried_at preserved inside cached responses)
CACHEABLE_TOOLS = _smart_mod.CACHEABLE_TOOLS
make_cached_wrapper = _smart_mod.make_cached_wrapper


def _maybe_cache(name: str, fn):
    if name in CACHEABLE_TOOLS:
        return make_cached_wrapper(name, fn)
    return fn


_TCM_TOOLS, _tcm_skip = _transfer_tools(_tcm_mod.mcp, cache_wrap=_maybe_cache)
_DOCK_TOOLS, _dock_skip = _transfer_tools(_dock_mod.mcp)
_SMART_TOOLS, _smart_skip = _transfer_tools(_smart_mod.mcp)
_PARALLEL_TOOLS, _parallel_skip = _transfer_tools(_parallel_mod.mcp)
_LIT_TOOLS, _lit_skip = _transfer_tools(_lit_mod.mcp)
_RESCORE_TOOLS, _rescore_skip = _transfer_tools(_rescore_mod.mcp)
_P1_TOOLS, _p1_skip = _transfer_tools(_p1_mod.mcp)
_P2_TOOLS, _p2_skip = _transfer_tools(_p2_mod.mcp)
_BOOTSTRAP_TOOLS, _bootstrap_skip = _transfer_tools(_bootstrap_mod.mcp)
_ITERATION_TOOLS, _iteration_skip = _transfer_tools(_iteration_mod.mcp)
_TOPOLOGY_TOOLS, _topology_skip = _transfer_tools(_topology_mod.mcp)
_PREP_TOOLS, _prep_skip = _transfer_tools(_prep_mod.mcp)
_TCMSP_TOOLS, _tcmsp_skip = _transfer_tools(_tcmsp_mod.mcp, cache_wrap=_maybe_cache)
_ALIGN_TOOLS, _align_skip = _transfer_tools(_align_mod.mcp)

# expose underlying fns to the execution engines (smart_layer.tcm_smart_run,
# parallel_layer, lit_formula_layer call them directly; includes the
# parallel/p0/p1/p2 layers so the auto-chain can drive them)
_fn_map: Dict[str, Any] = {}
for _src in (_tcm_mod.mcp, _dock_mod.mcp, _parallel_mod.mcp,
             _lit_mod.mcp, _rescore_mod.mcp, _p1_mod.mcp, _p2_mod.mcp,
             _tcmsp_mod.mcp):
    try:
        for _t in _src._tool_manager.list_tools():
            _fn_map[_t.name] = _t.fn
    except Exception:
        pass
_smart_mod.attach_tools(_fn_map)
_parallel_mod.attach_tools(_fn_map)
_lit_mod.attach_tools(_fn_map)


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


def _state_snapshot(state_dir) -> Dict[str, Any]:
    p = Path(state_dir)
    snap: Dict[str, Any] = {"dir": str(p), "exists": p.exists()}
    if not p.exists():
        return snap
    for f in p.iterdir():
        if f.is_file():
            try:
                if f.suffix == ".jsonl":
                    n = sum(1 for _ in open(f, encoding="utf-8"))
                    snap[f.name] = f"{n} records, {f.stat().st_size}B"
                else:
                    d = json.load(open(f, encoding="utf-8"))
                    if isinstance(d, dict) and "insights" in d:
                        snap[f.name] = f"{len(d['insights'])} insights"
                    else:
                        snap[f.name] = f"{f.stat().st_size}B"
            except Exception:
                snap[f.name] = "unreadable"
    return snap


# ================= tool 1: unified_overview =================

MODULES = [
    {"id": "M1", "name": "疾病优先检索",
     "triggers": "疾病/通路/症状/候选药材/成分反向链",
     "chain": "tcm_kegg_disease_pathways → (script) disease_pathway_workflow.py "
              "run-all（4步：通路→药材→成分→超几何FDR）→ tcm_symmap_lookup 验证",
     "doc": "modules/m1_disease_screening.md"},
    {"id": "M2", "name": "成分/靶点交叉证据",
     "triggers": "化合物/靶点/交叉验证/共识分/TDL/ID互转",
     "chain": "tcm_cross_database_screen（并发批量）→ tcm_pubchem_lookup / "
              "tcm_chembl_lookup 细节 → tcm_wikidata_lookup / tcm_uniprot_idmap "
              "ID桥接 → tcm_ctd_gene_interactions / tcm_dgidb_interactions",
     "doc": "modules/m2_target_evidence.md"},
    {"id": "M3", "name": "网络与富集",
     "triggers": "hub/网络/中心性/富集/通路共识",
     "chain": "tcm_network_analysis（hub排名）→ tcm_multi_enrichment（双引擎共识）"
              "→ tcm_hypergeometric_test（交叉检验）",
     "doc": "modules/m3_network_enrichment.md"},
    {"id": "M4", "name": "分子库构建",
     "triggers": "分子库/成分整理/去重/结构分类/QC五维",
     "chain": "fetch_herb_ingredients.py → molecular_library_builder.py（14步）"
              "→ dedup_classify.py → evidence_audit.py",
     "doc": "modules/m4_molecular_library.md"},
    {"id": "M5", "name": "对接建模",
     "triggers": "对接/结合模式/口袋/亲和能/复合物/PPI",
     "chain": "dock_check_environment → dock_fetch_structure（受体清理+配体3D）"
              "→ dock_vina_docking（口袋残基须来自文献）→ dock_analyze_contacts "
              "→ dock_geometric_ppi → dock_compare_experiments",
     "doc": "modules/m5_docking_modeling.md"},
    {"id": "M6", "name": "渲染出图与证据分级",
     "triggers": "论文图/组合图/渲染/图注/证据声明",
     "chain": "make_figure_pml.py + pymol → compose_panel.py → "
              "references/evidence_grading.md 三层证据纪律",
     "doc": "modules/m6_figures_documentation.md"},
    {"id": "M7", "name": "运行时自迭代",
     "triggers": "会话结束前/发现新情况/长期运行/复现性疑问",
     "chain": "tcm_analyze_runtime + dock_analyze_runtime → "
              "tcm_report_insight / dock_report_insight 落库 → "
              "tcm_self_iterate 向用户确认后追加 references/runtime_lessons.md",
     "doc": "modules/m7_self_iteration.md"},
]


@mcp.tool(
    name="unified_overview",
    annotations={"title": "Skill Orientation: Modules, Tools, State, Protocol",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def unified_overview() -> str:
    '''First-contact orientation for this unified skill. Call this at session
    start to understand what you can do WITHOUT reading any documentation:
    the 7 functional modules, which tools belong to which module, the health
    of the local telemetry/insight/experiment state, and the 5-step
    autonomous-use protocol. Then use tcm_smart_plan to turn the user goal
    into a dynamic execution plan.

    Returns:
        str: module map, tool inventory, state snapshot, quick-start protocol.
    '''
    tcm_tools = sorted(_TCM_TOOLS)
    dock_tools = sorted(_DOCK_TOOLS)
    smart_tools = sorted(_SMART_TOOLS)
    parallel_tools = sorted(_PARALLEL_TOOLS)
    lit_tools = sorted(_LIT_TOOLS)
    rescore_tools = sorted(_RESCORE_TOOLS)
    p1_tools = sorted(_P1_TOOLS)
    p2_tools = sorted(_P2_TOOLS)
    bootstrap_tools = sorted(_BOOTSTRAP_TOOLS)
    iteration_tools = sorted(_ITERATION_TOOLS)
    topology_tools = sorted(_TOPOLOGY_TOOLS)
    prep_tools = sorted(_PREP_TOOLS)
    tcmsp_tools = sorted(_TCMSP_TOOLS)
    align_tools = sorted(_ALIGN_TOOLS)
    return _fmt({
        "skill": "open-tcm-discovery (unified: retrieval + analysis + "
                 "docking + execution engine)",
        "protocol": [
            "1. orient: call unified_overview (done)",
            "2. plan: tcm_smart_plan(goal or disease/compounds/genes fields)",
            "3. execute (depth): tcm_smart_run(goal=...) drives one deep "
            "chain with checkpoints, auto-branching, and pause/resume",
            "4. execute (breadth): tcm_multi_path_run(compounds=[...]) runs "
            "every candidate's chain in PARALLEL and ranks them; for "
            "many-ligands-one-receptor use dock_batch_screening (parallel "
            "batch Vina + ADME prefilter); for target-module fragility use "
            "tcm_network_simulation (knockout + Monte-Carlo)",
            "5. report: tcm_report_generate(session_id) -> review-ready "
            "markdown brief with provenance and evidence grading",
            "6. adapt: at each dynamic point re-decide based on results "
            "(hub count, consensus score, affinity, repro flags)",
            "7. iterate: read the iteration packet, call "
            "tcm_self_iterate(action=review), and apply or reject only "
            "ids the user names. Accepted notes append to "
            "references/runtime_lessons.md and do not change tool code",
            "8. topology: read the analysis topology in the smart-run "
            "result, or call tcm_analysis_topology(session_id) to redraw "
            "the SVG and Mermaid map of libraries, literature, and the "
            "formula index"],
        "modules": MODULES,
        "tool_inventory": {
            "retrieval_screening (M1-M4)": tcm_tools,
            "docking_modeling (M5-M6)": dock_tools,
            "intelligence": smart_tools,
            "parallel_multimodule": {
                "tcm_adme_profile": "batch physicochemical profiling "
                                   "(RDKit: MW/TPSA/LogP/Lipinski/Veber/"
                                   "Egan/QED)",
                "tcm_network_simulation": "knockout + robustness + "
                                          "Monte-Carlo simulation on live "
                                          "STRING networks",
                "dock_batch_screening": "parallel multi-ligand batch Vina "
                                        "with ADME prefilter and ranking",
                "tcm_multi_path_run": "multi-arm parallel analysis "
                                      "network with composite ranking",
                "tools": parallel_tools},
            "evidence_visualization": {
                "tcm_literature_mine": "PubMed co-occurrence mining "
                                       "(bibliometric evidence triangle + "
                                       "yearly trend)",
                "tcm_formula_analysis": "herb-pair/formula ingredient & "
                                        "target compatibility on the local "
                                        "SymMap index",
                "dock_rescore": "post-screening re-rank (affinity spectrum "
                                "+ literature-pocket anchoring)",
                "tcm_network_figure": "publication network figure 3200x2200 "
                                      "(PNG+PDF, hub tiers, evidence-tier "
                                      "caption)",
                "tools": sorted(lit_tools + rescore_tools)},
            "p1_cellresolution_datadriven": {
                "tcm_graph_embed": "node2vec embedding target "
                                  "prioritization (dual-perspective "
                                  "vs centrality)",
                "tcm_scrna_channel": "GEO single-cell dataset discovery "
                                    "(HPA/GTEx dead - discovery only)",
                "tcm_admet_predict": "local ADMET approximations (ESOL/"
                                     "BBB/PPB/hERG proxies; ADMETlab "
                                     "REST dead)",
                "tools": p1_tools},
            "p2_translational": {
                "tcm_trials_match": "ClinicalTrials.gov API v2 matching "
                                    "(phase counts + trial table; "
                                    "translational context, not efficacy "
                                    "proof)",
                "tools": p2_tools},
            "self_iteration": {
                "tcm_self_iterate": "review local runtime evidence, then "
                                    "append or dismiss a lesson only for "
                                    "ids the user names",
                "tools": iteration_tools},
            "analysis_topology": {
                "tcm_analysis_topology": "redraw the live herb-compound-"
                                         "target-pathway network and the "
                                         "step status strip from a session",
                "tools": topology_tools},
            "local_prep": {
                "tcm_clean_table": "pasted table to header and rows",
                "tcm_normalize_ids": "CID, CAS checksum, InChIKey, "
                                     "UniProt, Ensembl shapes; no lookup",
                "tcm_normalize_structure": "SMILES check; RDKit canonical "
                                           "SMILES and InChIKey when installed",
                "tcm_normalize_genes": "local HGNC alias collapse; "
                                       "ambiguous family names stay open",
                "tcm_normalize_disease": "small checked MeSH crosswalk; "
                                         "unknown names stay unmapped",
                "tcm_convert_dose": "grams under a named metrology; "
                                    "weight ratio is not efficacy",
                "tools": prep_tools},
            "tcmsp_legacy": {
                "tcm_tcmsp_search": "legacy herb and ingredient tables "
                                    "with ob and dl; not targets",
                "tools": tcmsp_tools},
            "tcmsp_local_align": {
                "tcm_align_tcmsp": "join caller-supplied TCMSP rows to a "
                                   "name and InChIKey table; no network",
                "tools": align_tools},
            "bootstrap": {
                "tcm_dependency_status": "dependency health check with "
                                         "impact analysis + opt-in "
                                         "installer; vina resolves as "
                                         "param > VINA_EXE > bin/vina.exe "
                                         "> PATH (see bin/README.md)",
                "tools": bootstrap_tools},
            "response_cache": {
                "cached_tools": sorted(CACHEABLE_TOOLS),
                "ttl_seconds": "env TCM_CACHE_TTL (default 3600)",
                "inspect": "tcm_cache_status(action=status|clear)",
                "discipline": "cache = past live retrievals, original "
                              "queried_at preserved in-body; Error responses "
                              "never cached"},
            "transfer_notes": (_tcm_skip + _dock_skip + _smart_skip +
                              _parallel_skip + _lit_skip + _rescore_skip +
                              _p1_skip + _p2_skip + _bootstrap_skip +
                              _iteration_skip + _topology_skip + _prep_skip +
                              _tcmsp_skip + _align_skip)
                              or "all tools transferred"},
        "state_health": {
            "retrieval_state": _state_snapshot(_tcm_mod.STATE_DIR),
            "docking_state": _state_snapshot(_dock_mod.STATE_DIR)},
        "hard_disciplines": [
            "real-time retrieval only: every answer from live APIs with "
            "source + queried_at (no preset disease/gene/compound answers); "
            "response cache stores past LIVE retrievals only (TTL-bounded, "
            "original queried_at preserved)",
            "evidence grading: experimental > structure template > "
            "computational prediction; docking output = 'proposed docking "
            "model'; predicted residues get * and dashed lines",
            "ADME gates: OB>=30 / DL>=0.18, TCMSP official values only",
            "atomic writes, no concurrent writes to shared files",
            "docking pocket residues MUST come from literature (never guess); "
            "tcm_smart_run pauses rather than guessing them",
            "structure classification (tcm_classify_structure) is rule-based "
            "prediction - cross-check NPASS/TCMSP/literature"],
        "queried_at": _now_iso()},
        "Unified skill overview")


# ================= tool 2: tcm_smart_plan (dynamic analysis network) =================

class SmartPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goal: Optional[str] = Field(
        None, max_length=1000,
        description="natural language research goal (e.g. 'find UC targets and "
                    "dock berberine into MD-2')")
    disease: Optional[str] = Field(None, max_length=200,
                                   description="disease name if known")
    compounds: Optional[List[str]] = Field(
        None, max_length=20, description="candidate compound names/CIDs")
    genes: Optional[List[str]] = Field(
        None, max_length=50, description="known target gene symbols")
    depth: str = Field("standard", description="fast | standard | full "
                      "(full adds the 4-step herb/ingredient workflow + "
                      "docking reproducibility double-run)")


@mcp.tool(
    name="tcm_smart_plan",
    annotations={"title": "Dynamic Analysis-Network Planner (autonomous routing)",
                 "readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_smart_plan(params: SmartPlanInput) -> str:
    '''Turn a research goal into a dynamic analysis network (execution DAG
    with decision points) mapped to concrete tools. THE autonomy router:
    agents should call this instead of guessing which module to use.

    The plan is not a fixed pipeline - it carries dynamic points where the
    NEXT step depends on results: hub counts route enrichment vs screening,
    consensus scores route rejection vs docking, affinity + reproducibility
    flags route refinement. Execute step by step, feeding each result into
    the next; re-call this tool if the goal evolves.

    Args:
        params (SmartPlanInput): goal (+ optional structured fields).

    Returns:
        str: detected intent, ordered plan steps (module/tool/params/purpose/
        depends_on), dynamic_points (branch rules), execution guidance.

    Examples:
        - "研究溃疡性结肠炎的潜在中药靶点并对接候选成分" ->
          goal="ulcerative colitis targets and dock candidate compounds"
        - Structured: disease="ulcerative colitis", compounds=["berberine"]
    '''
    g = (params.goal or "").lower()
    dis_kw = ("disease", "colitis", "uc", "cancer", "arthritis", "diabetes",
              "pathway", "通路", "疾病", "症状", "ulcerative", "ibd",
              "inflammatory", "靶点筛选", "结肠炎", "溃疡", "癌", "肿瘤",
              "糖尿病", "哮喘", "抑郁", "肝炎", "胃炎", "肾病", "心肌", "神经退行")
    comp_kw = ("compound", "molecule", "ingredient", "berberine", "quercetin",
               "screen", "成分", "化合物", "小分子", "分子库")
    gene_kw = ("gene", "target", "hub", "tnf", "ppi", "network", "enrich",
               "基因", "靶点", "网络", "富集")
    dock_kw = ("dock", "binding mode", "pocket", "affinity", "对接", "口袋",
               "结合", "亲和能", "复合物")
    fig_kw = ("figure", "render", "publish", "paper", "图", "渲染", "组合图",
              "论文")
    lit_kw = ("literature", "pubmed", "co-occurrence", "文献", "共现",
              "证据链", "evidence")
    trials_kw = ("trial", "clinical", "translational", "试验", "临床", "转化")
    formula_kw = ("formula", "herb pair", "配伍", "药对", "协同", "复方",
                  "君臣佐使", "黄连", "黄芩")
    admet_kw = ("admet", "toxicity", "solubility", "bbb", "herg", "毒性",
                "水溶性", "溶解", "通透")
    scna_kw = ("single-cell", "single cell", "scrna", "单细胞")
    embed_kw = ("embedding", "node2vec", "数据驱动", "图嵌入", "嵌入",
                "优先级排序")
    sim_kw = ("knockout", "simulation", "perturbation", "敲除", "模拟",
              "扰动", "脆弱", "鲁棒")
    has_disease = bool(params.disease) or any(k in g for k in dis_kw)
    has_compounds = bool(params.compounds) or any(k in g for k in comp_kw)
    has_genes = bool(params.genes) or any(k in g for k in gene_kw)
    want_docking = any(k in g for k in dock_kw)
    want_figures = any(k in g for k in fig_kw)
    want_lit = any(k in g for k in lit_kw)
    want_trials = any(k in g for k in trials_kw)
    want_formula = any(k in g for k in formula_kw)
    want_admet = any(k in g for k in admet_kw)
    want_scna = any(k in g for k in scna_kw)
    want_embed = any(k in g for k in embed_kw)
    want_sim = any(k in g for k in sim_kw)
    if not any((has_disease, has_compounds, has_genes, want_docking,
                want_figures, want_lit, want_trials, want_formula,
                want_admet, want_scna, want_embed, want_sim)):
        return ("Error: no recognizable intent. Provide goal text and/or "
                "disease/compounds/genes fields (keywords in EN or 中文 both "
                "work).")

    steps: List[Dict[str, Any]] = []
    dyn: List[str] = []

    def add(module, tool, params_dict, purpose, depends):
        steps.append({"step": len(steps) + 1, "module": module, "tool": tool,
                      "params": params_dict, "purpose": purpose,
                      "depends_on": depends})

    disease = params.disease or "<extract disease name from goal; if ambiguous, ask user>"

    # ---- disease branch (M1 -> M3 -> M2) ----
    if has_disease:
        add("M1", "tcm_kegg_disease_pathways",
            {"disease_name": disease, "use_enrichment": True},
            "disease -> ranked KEGG pathways (KEGG direct + SymMap enrichment)",
            [])
        add("M3", "tcm_network_analysis",
            {"genes": "<top pathway's genes from step 1>"},
            "build PPI network, rank hubs by degree+betweenness+closeness",
            [steps[-1]["step"]])
        dyn.append("D1: top pathway gene count >= 15 -> network on top 20 "
                   "genes then M3 enrichment on hubs; < 15 -> skip network, "
                   "enrich the gene list directly")
        add("M3", "tcm_multi_enrichment",
            {"genes": "<hubs or gene list>"},
            "dual-engine (g:Profiler+SymMap) enrichment consensus; "
            "terms significant in BOTH = strongest evidence",
            [steps[-1]["step"]])
        add("M2", "tcm_cross_database_screen",
            {"mode": "gene", "entities": "<top 10 hubs>",
             "concurrency": 3},
            "4-database evidence + Pharos TDL (Tclin/Tchem = druggable "
            "priority)",
            [steps[-1]["step"]])
        dyn.append("D2: gene consensus >= 3/4 -> keep as core targets; "
                   "Tclin/Tchem targets -> prioritize for docking")
        if params.depth == "full":
            add("M1", "script: disease_pathway_workflow.py",
                {"cmd": 'python scripts/disease_pathway_workflow.py run-all '
                        '--disease "<disease>" --out <workdir>',
                 "executed_by": "operator or agent shell, not tcm_smart_run",
                 "note": "4-step: pathways -> herbs (symptom+ingredient "
                         "chains) -> OB-filtered ingredients -> hypergeometric "
                         "FDR; 30-60 min, background + checkpoint. "
                         "tcm_smart_run only records this as deferred step D3"},
                "full TCM-side herb/ingredient screen; not executed inside "
                "the live engine",
                [1])
        # compound candidates emerging from full workflow
        if params.depth == "full":
            dyn.append("D3: if step (workflow) yields OB-filtered ingredients "
                       "-> feed top compounds into M2 compound screen")

    if has_genes and not has_disease:
        add("M3", "tcm_network_analysis", {"genes": params.genes or "<from goal>"},
            "network + hubs on the given targets", [])
        add("M3", "tcm_multi_enrichment", {"genes": "<hubs top 20>"},
            "pathway consensus for the given targets",
            [steps[-1]["step"]] if steps else [])
        add("M2", "tcm_cross_database_screen",
            {"mode": "gene", "entities": "<top 10 hubs or given genes>",
             "concurrency": 3},
            "cross-database evidence profile", [steps[-1]["step"]])

    # ---- compound branch (M2 -> M5) ----
    comps = params.compounds or "<candidate compounds>"
    if has_compounds:
        add("M2", "tcm_cross_database_screen",
            {"mode": "compound", "entities": comps, "concurrency": 3},
            "3-database consensus (PubChem CID/InChIKey + ChEMBL + SymMap); "
            "also runs for compounds produced by the M1 full workflow",
            [steps[-1]["step"]] if steps else [])
        add("M2", "tcm_uniprot_idmap / tcm_wikidata_lookup",
            {"ids_or_qid": "<CID/UniProt from screening>"},
            "bridge IDs across databases for downstream chaining",
            [steps[-1]["step"]])
        dyn.append("D4: compound consensus >= 2/3 -> advance to docking; "
                   "< 2/3 -> verify name/CID first (wikidata resolve), "
                   "do not dock unverified entities")

    # ---- evidence-expansion branches (P0/P1/P2) ----
    if has_compounds and (want_admet or want_lit or has_disease):
        add("P1", "tcm_admet_predict",
            {"compounds": comps},
            "local ADMET approximations (ESOL solubility, BBB/PPB/hERG "
            "proxies; every value labelled as approximation)",
            [s["step"] for s in steps if s["module"] == "M2"][:1] or [])
    if (want_lit or has_disease) and (has_compounds or has_genes):
        add("P0", "tcm_literature_mine",
            {"compound": "<first candidate>",
             "genes": "<top 3 hubs>", "disease": disease},
            "PubMed co-occurrence evidence triangle (bibliometric signal, "
            "NOT causal proof)",
            [s["step"] for s in steps if s["module"] == "M3"][:1] or [])
    if want_trials or (has_disease and has_genes):
        add("P2", "tcm_trials_match",
            {"condition": disease, "target_or_compound": "<top hub>",
             "page_size": 5},
            "ClinicalTrials.gov translational context (phase counts; "
            "trial activity around a target != efficacy proof)",
            [s["step"] for s in steps if s["module"] == "M2"][:1] or [])
    if want_formula:
        add("P0", "tcm_formula_analysis",
            {"herbs": "<2-8 herb names from goal>"},
            "herb-pair/formula ingredient complementarity on the local "
            "SymMap index (set computation, not efficacy validation)", [])
    if want_scna and has_disease:
        add("P1", "tcm_scrna_channel", {"disease": disease},
            "GEO single-cell dataset discovery (HPA/GTEx dead - discovery "
            "only; follow accessions/PubMed for expression evidence)", [])
    if want_embed and has_genes:
        add("P1", "tcm_graph_embed",
            {"genes": "<network genes>", "seed_genes": "<known disease "
             "genes>"},
            "node2vec embedding target prioritization + dual-perspective "
            "consensus with centrality (topology view, not functional "
            "validation)",
            [s["step"] for s in steps if s["module"] == "M3"][:1] or [])
    if want_sim and has_genes:
        add("P3", "tcm_network_simulation",
            {"genes": "<network genes>"},
            "knockout sweep + Monte-Carlo robustness (topological "
            "simulation, not validated essentiality)",
            [s["step"] for s in steps if s["module"] == "M3"][:1] or [])

    # ---- docking branch (M5) ----
    if want_docking or (has_compounds and (has_disease or has_genes)):
        add("M5", "dock_check_environment", {},
            "verify vina/meeko/pymol before any docking", [])
        add("M5", "dock_fetch_structure",
            {"source": "pdb", "query": "<receptor PDB id>", "chains": "<keep>",
             "drop_hetatm": True, "out": "<receptor.pdb>"},
            "cleaned receptor structure",
            [steps[-1]["step"]])
        add("M5", "dock_fetch_structure",
            {"source": "pubchem", "query": "<compound CID>", "out": "<ligand.sdf>"},
            "ligand 3D structure (correct stereochemistry)",
            [steps[-1]["step"]])
        add("M5", "dock_vina_docking",
            {"receptor": "<receptor.pdb>", "ligand": "<ligand.sdf>",
             "pocket_chain": "<C>", "pocket_residues": "<FROM LITERATURE>",
             "exhaustiveness": 16, "seed": 42,
             "vina_path": "<or set VINA_EXE>"},
            "small-molecule docking; pocket residues MUST come from "
            "literature (search references/literature first, never guess)",
            [steps[-1]["step"]])
        dyn.append("D5: best affinity <= -7 kcal/mol -> plausible binder, "
                   "proceed to contacts; > -5 -> weak, try next candidate")
        add("M5", "dock_analyze_contacts",
            {"complex_pdb": "<receptor.pdb>", "ligand_chain": "L",
             "receptor_chains": "<A,C>", "merge_ligand": "<best_pose.pdb>"},
            "contact residues + H-bond candidates (label 'proposed docking "
            "model'; predicted residues get * and dashed lines)",
            [steps[-1]["step"]])
        add("M5", "dock_compare_experiments", {"ligand": "<ligand>"},
            "benchmark against ledger (prior runs); same-seed spread > 0.1 "
            "-> reproducibility flag",
            [steps[-1]["step"]])
        dyn.append("D6: predicted key residues -> cross-check UniProt/"
                   "literature evidence (M2 tcm_get_gene_info / wikidata) "
                   "before claiming importance")

    # ---- figures branch (M6) ----
    if want_figures:
        add("M6", "script: make_figure_pml.py",
            {"cmd": "--config fig.json --out figure.pml; then pymol -c -r",
             "note": "3200x2200, gradient cartoons, transparent surfaces; "
                     "see references/rendering_style.md"},
            "publication-grade structural figures",
            [s["step"] for s in steps if s["module"] == "M5"] or [])
        add("M6", "script: compose_panel.py",
            {"cmd": "--main overall.png --zoom detail.png --panel A "
                    "--name '<PDB-Complex_Ligand>' --red '<key metrics>'"},
            "reference-style composite panel (thin black frame, trapezoid "
            "leader lines, red parameter box)",
            [steps[-1]["step"]])

    # ---- self-iteration (M7, always) ----
    add("M7", "tcm_analyze_runtime + dock_analyze_runtime", {},
        "session-end runtime analysis: channel health, dead ends, experiment "
        "stats, reproducibility flags, proposed_updates",
        ["all"])
    add("M7", "tcm_report_insight / dock_report_insight",
        {"category": "<dead_end|endpoint_change|quirk|performance|repro>",
         "title": "<finding>"},
        "persist NEW findings from this session (they will hint future "
        "errors and surface in proposed updates)",
        [steps[-1]["step"]])

    return _fmt({
        "detected_intent": {
            "disease": has_disease, "compounds": has_compounds,
            "genes_targets": has_genes, "docking": want_docking,
            "figures": want_figures, "depth": params.depth},
        "plan_network": steps,
        "dynamic_points": dyn,
        "execution_guidance": [
            "execute in step order; each step's <placeholder> is filled with "
            "the PREVIOUS step's results (this is the dynamic analysis "
            "network - data flows between modules)",
            "at each dynamic point D1-D6, branch on the observed result",
            "load modules/<module_id doc>.md only when you need the module's "
            "detailed workflow (keeps context lean)",
            "all tool calls use the params wrapper: {'params': {...}}",
            "if the goal changes mid-run, call tcm_smart_plan again"],
        "note": "planner is rule-based (deterministic); it routes, the "
                "databases stay the source of truth (real-time retrieval "
                "discipline)",
        "queried_at": _now_iso()},
        f"Smart plan ({len(steps)} steps, {len(dyn)} dynamic points)")


# ================= 入口 =================

def main():
    ap = argparse.ArgumentParser(description="tcm unified MCP server "
                                             "(retrieval + analysis + docking)")
    ap.add_argument("--port", type=int, default=8002,
                    help="StreamableHTTP port (default 8002)")
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
