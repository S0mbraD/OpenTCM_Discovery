"""Analysis network and its live topology.

The graph is the herb–compound–target–pathway–disease form used in
TCM network pharmacology, plus the libraries that supplied each edge:
pathway databases, literature, and the local formula index. Layout and
SVG are pure Python so an agent can redraw the picture after every step.
Fewer than 300 entity nodes are rounded cards. At 300 the picture
switches to one sphere per kind. The SVG and HTML do not load scripts.

The canvas is warm paper with wood-colored frames. The same reading
block — legend, retrieval chain, next-step hints, and source status —
is written into the SVG, the HTML, and the text summary. Hints name
tools already in the server. They are not run automatically, and they
are not efficacy claims.

Edges record where a link was retrieved. They do not promote a
co-occurrence count, a set overlap, or a trial listing into efficacy.
"""

from __future__ import annotations

import html
import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

LIT_NOTE = "共现不是因果"
FORMULA_NOTE = "集合重叠不是药效"
TRIAL_NOTE = "试验登记不是疗效"
SCREEN_NOTE = "收录计数不是疗效"
DOCK_NOTE = "提议的对接模型"

LAYERS = {
    "source": 0,
    "disease": 1,
    "pathway": 2,
    "gene": 3,
    "compound": 4,
    "herb": 5,
    "formula": 6,
    "literature": 7,
    "trial": 8,
    "model": 9,
}

SOURCE_CATALOG = (
    ("kegg", "KEGG", "通路"),
    ("symmap", "SymMap", "药材-成分-基因"),
    ("string", "STRING", "蛋白互作"),
    ("pubchem", "PubChem", "化合物"),
    ("chembl", "ChEMBL", "化合物"),
    ("pharos", "Pharos", "靶点分级"),
    ("opentargets", "Open Targets", "疾病-基因"),
    ("pubmed", "PubMed", "文献"),
    ("ctgov", "ClinicalTrials.gov", "试验登记"),
    ("herb_index", "SymMap药材成分索引", "方剂集合"),
)

_CHANNEL_SOURCE = {
    "pubchem": "pubchem",
    "chembl": "chembl",
    "symmap": "symmap",
    "pharos": "pharos",
    "opentargets": "opentargets",
    "string": "string",
    "kegg": "kegg",
}

_MAX = {
    "pathway": 8,
    "gene": 12,
    "compound": 8,
    "herb": 8,
    "literature": 4,
    "trial": 4,
}
_MAX_EDGES = 160

# Warm paper, cream cards, ink, and wood. Status hues stay distinct.
# Not evidence grades.
PAPER_BG = "#f6f0e6"
CARD_PAPER = "#fffaf3"
INK = "#3d2b1f"
WOOD = "#8b5e3c"
WOOD_DARK = "#6b4423"
SHELL_AMBER = "#e8c99a"
SHELL_AMBER_DEEP = "#c4956a"
STATUS_READY = "#b08968"
STATUS_PENDING = "#c4842a"
STATUS_DONE = "#5f8f62"
STATUS_FAILED = "#b5453a"
STATUS_SKIPPED = "#8a7b6b"
STATUS_DEFERRED = "#7a4e32"
_STATUS_COLOR = {
    "ready": STATUS_READY,
    "pending": STATUS_PENDING,
    "done": STATUS_DONE,
    "failed": STATUS_FAILED,
    "skipped": STATUS_SKIPPED,
    "deferred": STATUS_DEFERRED,
}
_STATUS_PRIORITY = {
    "failed": 5, "pending": 4, "deferred": 3, "ready": 2, "done": 1, "skipped": 0,
}
_KIND_FILL = {
    "source": CARD_PAPER,
    "disease": CARD_PAPER,
    "pathway": CARD_PAPER,
    "gene": CARD_PAPER,
    "compound": CARD_PAPER,
    "herb": CARD_PAPER,
    "formula": CARD_PAPER,
    "literature": CARD_PAPER,
    "trial": CARD_PAPER,
    "model": CARD_PAPER,
    "step": CARD_PAPER,
}
_KIND_NEBULA = {
    "source": ("#f4e2c4", SHELL_AMBER),
    "disease": ("#f3d8c6", "#d2a88a"),
    "pathway": ("#f6e6c8", SHELL_AMBER_DEEP),
    "gene": ("#f3e0c4", SHELL_AMBER),
    "compound": ("#f8e7c6", SHELL_AMBER_DEEP),
    "herb": ("#efe6c8", "#c8b07a"),
    "formula": ("#f6ddc4", "#c49868"),
    "literature": ("#f3e2d2", "#c4a484"),
    "trial": ("#f1e6d6", "#c4b090"),
    "model": ("#f4e4cc", SHELL_AMBER_DEEP),
}
_KIND_LABEL = {
    "source": "来源库",
    "disease": "疾病",
    "pathway": "通路",
    "gene": "基因",
    "compound": "成分",
    "herb": "药材",
    "formula": "方剂集合",
    "literature": "文献",
    "trial": "试验",
    "model": "对接模型",
    "step": "步骤",
}
SPHERE_ENTITY_THRESHOLD = 300
_CARD_W = 176.0
_CARD_H = 42.0
_COL_GAP = 56.0
_ROW_GAP = 22.0
_MARGIN_X = 16.0
_HEADER_H = 92.0
_MIN_WIDTH = 760.0
_BG = PAPER_BG
_CHAIN = (
    ("disease", "疾病"),
    ("pathway", "通路"),
    ("gene", "基因"),
    ("compound", "成分"),
    ("herb", "药材"),
)
_SOURCE_RETRY = {
    "kegg": "tcm_kegg_disease_pathways",
    "symmap": "tcm_symmap_lookup",
    "string": "tcm_network_analysis",
    "pubchem": "tcm_cross_database_screen",
    "chembl": "tcm_cross_database_screen",
    "pharos": "tcm_cross_database_screen",
    "opentargets": "tcm_search_disease_targets",
    "pubmed": "tcm_literature_mine",
    "ctgov": "tcm_trials_match",
    "herb_index": "tcm_formula_analysis",
}
_POCKET_MARKERS = (
    "口袋残基",
    "pocket_residues",
    "pocket residue",
    "pocket residues",
)
_STATUS_WORD = {
    "ready": "就绪",
    "pending": "进行中",
    "done": "完成",
    "failed": "失败",
    "skipped": "跳过",
    "deferred": "延后",
}
_READING_CLASS = {
    "图例": "reading-legend",
    "检索通路": "reading-pathway",
    "下一步建议": "reading-next",
    "数据源状态": "reading-sources",
}


def _slug(text: str) -> str:
    parts = []
    for ch in (text or "").strip().lower():
        if ch.isascii() and (ch.isalnum() or ch in "._-"):
            parts.append(ch)
        else:
            parts.append(f"u{ord(ch):x}")
    slug = "".join(parts)[:56]
    return slug or "x"


def step_id(tool: str, qualifier: str = "") -> str:
    key = tool if not qualifier else f"{tool}:{qualifier}"
    return "step:" + _slug(key)


def herbs_in_text(text: str, rows: Iterable[dict], limit: int = 8) -> List[str]:
    """Return herb names that appear in text, preferring the longer name."""
    if not text:
        return []
    lowered = text.lower()
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field in ("chinese_name", "pinyin", "latin_name"):
            name = (row.get(field) or "").strip()
            if field != "chinese_name" and len(name) < 4:
                continue
            if len(name) < 2:
                continue
            hay = text if field == "chinese_name" else lowered
            needle = name if field == "chinese_name" else name.lower()
            if needle in hay:
                found.append(name if field == "chinese_name" else name)
                break
    found.sort(key=len, reverse=True)
    kept: List[str] = []
    for name in found:
        if any(name != other and name.lower() in other.lower() for other in kept):
            continue
        if name not in kept:
            kept.append(name)
        if len(kept) >= limit:
            break
    return kept


def _split_genes(value, limit: int = 12) -> List[str]:
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = re.split(r"[;,\s]+", value)
    else:
        return []
    out = []
    for item in raw:
        gene = str(item).strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,14}", gene):
            out.append(gene)
        if len(out) >= limit:
            break
    return out


def _channel_ok(payload) -> bool:
    return isinstance(payload, dict) and not payload.get("error")


class AnalysisGraph:
    def __init__(self):
        self.nodes: Dict[str, dict] = {}
        self.edges: List[dict] = []
        self.disease = ""
        self.compounds: List[str] = []
        self.genes_seed: List[str] = []
        self.herbs: List[str] = []
        self.n_genes = 0
        self._edge_ids = set()

    def seed_sources(self) -> None:
        for key, label, role in SOURCE_CATALOG:
            self.upsert("source", key, "ready", label=label, detail=role)

    def note_intent(self, disease: Optional[str] = None,
                    compounds: Optional[List[str]] = None,
                    genes: Optional[List[str]] = None,
                    herbs: Optional[List[str]] = None) -> None:
        self.disease = (disease or "").strip()
        self.compounds = [c for c in (compounds or []) if c][:_MAX["compound"]]
        self.genes_seed = [g for g in (genes or []) if g][:_MAX["gene"]]
        self.herbs = [h for h in (herbs or []) if h][:_MAX["herb"]]
        if self.disease:
            self.upsert("disease", self.disease, "pending")
        for name in self.compounds:
            self.upsert("compound", name, "pending")
        for name in self.genes_seed:
            self.upsert("gene", name, "pending")
            if self.disease:
                self.link("disease", self.disease, "gene", name,
                          "queried_with", "pending")
        for name in self.herbs:
            self.upsert("herb", name, "pending")
            self.link("source", "herb_index", "herb", name,
                      "indexed_in", "ready")
        if len(self.herbs) >= 2:
            self.upsert("formula", "成分集合", "pending",
                        detail=FORMULA_NOTE)
            for name in self.herbs:
                self.link("formula", "成分集合", "herb", name,
                          "contains", "pending", detail=FORMULA_NOTE)

    def node_id(self, kind: str, key: str) -> str:
        return f"{kind}:{_slug(key)}"

    def upsert(self, kind: str, key: str, status: str, label: str = "",
               detail: str = "", source: str = "", queried_at: str = "") -> Optional[str]:
        if not key:
            return None
        nid = self.node_id(kind, key)
        cap = _MAX.get(kind)
        if nid not in self.nodes and cap is not None:
            existing = sum(1 for n in self.nodes.values() if n["kind"] == kind)
            if existing >= cap:
                return None
        node = self.nodes.get(nid)
        if node is None:
            self.nodes[nid] = {
                "id": nid, "kind": kind, "key": key,
                "label": label or key, "status": status,
                "detail": detail, "source": source,
                "queried_at": queried_at,
            }
            return nid
        if label:
            node["label"] = label
        if detail:
            node["detail"] = detail
        if source:
            node["source"] = source
        if queried_at:
            node["queried_at"] = queried_at
        if _status_rank(status) >= _status_rank(node["status"]):
            node["status"] = status
        return nid

    def link(self, skind: str, skey: str, dkind: str, dkey: str,
             relation: str, status: str = "done", detail: str = "") -> None:
        src = self.node_id(skind, skey)
        dst = self.node_id(dkind, dkey)
        if src not in self.nodes or dst not in self.nodes:
            return
        eid = f"{src}|{relation}|{dst}"
        if eid in self._edge_ids or len(self.edges) >= _MAX_EDGES:
            return
        self._edge_ids.add(eid)
        self.edges.append({
            "id": eid, "src": src, "dst": dst,
            "relation": relation, "status": status, "detail": detail,
        })

    def _mark_step(self, tool: str, qualifier: str, status: str, record: dict) -> None:
        nid = step_id(tool, qualifier)
        label = tool if not qualifier else f"{tool} ({qualifier})"
        self.nodes[nid] = {
            "id": nid, "kind": "step", "key": label, "label": label,
            "status": status,
            "detail": str(record.get("summary") or "")[:240],
            "source": str(record.get("source") or ""),
            "queried_at": str(record.get("queried_at") or ""),
        }

    def step_status(self, tool: str, qualifier: str = "") -> str:
        node = self.nodes.get(step_id(tool, qualifier))
        return node["status"] if node else ""

    def observe(self, record: dict) -> None:
        tool = str(record.get("tool") or "")
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        status = _record_status(tool, record.get("status"))
        qualifier = ""
        if tool == "tcm_cross_database_screen":
            qualifier = str(result.get("mode") or "")
        if "skipped" in tool and "D1" in tool:
            tool = "tcm_network_analysis"
            status = "skipped"
        self._mark_step(tool, qualifier, status, record)
        if status != "done":
            return
        handler = {
            "tcm_kegg_disease_pathways": self._from_pathways,
            "tcm_network_analysis": self._from_ppi,
            "tcm_multi_enrichment": self._from_enrichment,
            "tcm_cross_database_screen": self._from_screen,
            "tcm_literature_mine": self._from_literature,
            "tcm_formula_analysis": self._from_formula,
            "tcm_trials_match": self._from_trials,
            "tcm_admet_predict": self._from_admet,
            "dock_vina_docking": self._from_docking,
        }.get(tool)
        if handler:
            handler(result, record)

    def _from_pathways(self, result: dict, record: dict) -> None:
        disease = result.get("disease") or self.disease
        if disease:
            self.upsert("disease", disease, "done",
                        source=str(result.get("source") or record.get("source") or ""),
                        queried_at=str(result.get("queried_at") or ""))
        self.upsert("source", "kegg", "done")
        self.upsert("source", "symmap", "done")
        for row in (result.get("pathways") or [])[:_MAX["pathway"]]:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("pathway_id") or row.get("name") or "")
            label = str(row.get("pathway_name") or row.get("name") or pid)
            if not pid:
                continue
            self.upsert("pathway", pid, "done", label=label,
                        source=str(row.get("source") or ""))
            if disease:
                self.link("disease", disease, "pathway", pid, "associated_with")
            self.link("source", "kegg", "pathway", pid, "retrieved_from")
            genes = _split_genes(row.get("related_genes"), limit=40)
            self.n_genes = max(self.n_genes, len(genes))
            for gene in genes[:_MAX["gene"]]:
                self.upsert("gene", gene, "done")
                self.link("pathway", pid, "gene", gene, "member_of")
                if disease:
                    self.link("disease", disease, "gene", gene, "associated_with")

    def _from_ppi(self, result: dict, record: dict) -> None:
        self.upsert("source", "string", "done",
                    queried_at=str(result.get("queried_at") or ""))
        hubs = []
        for row in result.get("top_hubs") or []:
            if isinstance(row, dict) and row.get("gene"):
                hubs.append(str(row["gene"]))
        for gene in hubs[:_MAX["gene"]]:
            self.upsert("gene", gene, "done", detail="hub")
            self.link("source", "string", "gene", gene, "retrieved_from")
        for edge in (result.get("edge_sample") or [])[:20]:
            if not isinstance(edge, dict):
                continue
            a, b = edge.get("a"), edge.get("b")
            if a and b:
                self.upsert("gene", str(a), "done")
                self.upsert("gene", str(b), "done")
                self.link("gene", str(a), "gene", str(b), "interacts_with",
                          detail="STRING 拓扑，不是功能验证")

    def _from_enrichment(self, result: dict, record: dict) -> None:
        for row in (result.get("consensus_terms") or [])[:_MAX["pathway"]]:
            if not isinstance(row, dict) or not row.get("term"):
                continue
            term = str(row["term"])
            self.upsert("pathway", term, "done", label=term,
                        detail="双引擎名称重叠")
            for gene in [n["key"] for n in self.nodes.values() if n["kind"] == "gene"][:6]:
                self.link("gene", gene, "pathway", term, "enriched_in")

    def _from_screen(self, result: dict, record: dict) -> None:
        mode = result.get("mode") or "entity"
        kind = "gene" if mode == "gene" else "compound"
        for row in (result.get("consensus_ranking") or [])[:_MAX[kind]]:
            if not isinstance(row, dict):
                continue
            name = str(row.get("entity") or "")
            if not name:
                continue
            hits = row.get("consensus_hits")
            self.upsert(kind, name, "done",
                        detail=f"consensus_hits={hits}; {SCREEN_NOTE}")
            channels = row.get("channels") or {}
            if isinstance(channels, dict):
                for channel, payload in channels.items():
                    source_key = _CHANNEL_SOURCE.get(str(channel))
                    if not source_key:
                        continue
                    ok = _channel_ok(payload)
                    self.upsert("source", source_key, "done" if ok else "failed")
                    self.link("source", source_key, kind, name, "recorded_in",
                              "done" if ok else "failed",
                              detail=SCREEN_NOTE if ok else str(
                                  (payload or {}).get("error") or "failed")[:120])

    def _from_literature(self, result: dict, record: dict) -> None:
        compound = str(result.get("compound") or "")
        disease = str(result.get("disease") or self.disease or "")
        counts = result.get("counts") if isinstance(result.get("counts"), dict) else {}
        detail = f"full_triple={counts.get('full_triple')}; {LIT_NOTE}"
        self.upsert("literature", "PubMed", "done", label="PubMed",
                    detail=detail, source="NCBI E-utilities",
                    queried_at=str(result.get("queried_at") or ""))
        self.upsert("source", "pubmed", "done")
        if compound:
            self.upsert("compound", compound, "done")
            self.link("compound", compound, "literature", "PubMed",
                      "co_mentioned", detail=LIT_NOTE)
        if disease:
            self.upsert("disease", disease, "done")
            self.link("disease", disease, "literature", "PubMed",
                      "co_mentioned", detail=LIT_NOTE)
        for gene in (result.get("genes") or [])[:6]:
            if gene:
                self.upsert("gene", str(gene), "done")
                self.link("gene", str(gene), "literature", "PubMed",
                          "co_mentioned", detail=LIT_NOTE)

    def _from_formula(self, result: dict, record: dict) -> None:
        matched = result.get("herbs_matched") or {}
        self.upsert("formula", "成分集合", "done", detail=FORMULA_NOTE,
                    source=str(result.get("index_source") or "SymMap herb index"))
        self.upsert("source", "herb_index", "done")
        names = []
        if isinstance(matched, dict):
            for name in list(matched)[:_MAX["herb"]]:
                self.upsert("herb", str(name), "done")
                self.link("formula", "成分集合", "herb", str(name),
                          "contains", detail=FORMULA_NOTE)
                self.link("source", "herb_index", "herb", str(name),
                          "indexed_in")
                names.append(str(name))
        for row in result.get("pairwise_ingredients") or []:
            if not isinstance(row, dict) or " + " not in str(row.get("pair") or ""):
                continue
            left, right = str(row["pair"]).split(" + ", 1)
            self.upsert("herb", left, "done")
            self.upsert("herb", right, "done")
            self.link("herb", left, "herb", right, "set_overlap",
                      detail=f"jaccard={row.get('jaccard')}; {FORMULA_NOTE}")

    def _from_trials(self, result: dict, record: dict) -> None:
        condition = str(result.get("condition") or self.disease or "trials")
        total = result.get("total_trials")
        self.upsert("trial", condition, "done",
                    label=f"试验 {total}",
                    detail=f"total_trials={total}; {TRIAL_NOTE}",
                    source="ClinicalTrials.gov API v2",
                    queried_at=str(result.get("queried_at") or ""))
        self.upsert("source", "ctgov", "done")
        if self.disease or result.get("condition"):
            disease = str(result.get("condition") or self.disease)
            self.upsert("disease", disease, "done")
            self.link("disease", disease, "trial", condition,
                      "trial_context", detail=TRIAL_NOTE)

    def _from_admet(self, result: dict, record: dict) -> None:
        for row in (result.get("panel") or [])[:_MAX["compound"]]:
            if not isinstance(row, dict) or not row.get("compound"):
                continue
            self.upsert("compound", str(row["compound"]), "done",
                        detail="ADMET 为本地近似，不是实测")

    def _from_docking(self, result: dict, record: dict) -> None:
        self.upsert("model", "vina", "done", label=DOCK_NOTE, detail=DOCK_NOTE)

    def pending_tasks(self) -> List[dict]:
        tasks = []
        if len(self.herbs) >= 2 and self.step_status("tcm_formula_analysis") not in {"done", "skipped"}:
            tasks.append({
                "tool": "tcm_formula_analysis",
                "reason": "至少两味药材已入网，用本地成分索引计算集合重叠",
                "inputs": {"herbs": list(self.herbs)},
                "local": True,
            })
        if self.disease and self.step_status("tcm_kegg_disease_pathways") not in {"done", "skipped"}:
            tasks.append({
                "tool": "tcm_kegg_disease_pathways",
                "reason": "疾病节点尚未连接通路库",
                "inputs": {"disease": self.disease},
                "local": False,
            })
        genes = [n for n in self.nodes.values() if n["kind"] == "gene"]
        gene_n = max(self.n_genes, len(genes))
        network = self.step_status("tcm_network_analysis")
        if gene_n >= 15 and network not in {"done", "skipped"}:
            tasks.append({
                "tool": "tcm_network_analysis",
                "reason": "基因不少于 15，先建 PPI 再取 hub",
                "inputs": {"genes": [n["key"] for n in genes[:40]]},
                "local": False,
            })
        elif genes and self.step_status("tcm_multi_enrichment") not in {"done", "skipped"}:
            if gene_n < 15 or network in {"done", "skipped"}:
                tasks.append({
                    "tool": "tcm_multi_enrichment",
                    "reason": "基因已入网，做双引擎富集",
                    "inputs": {"genes": [n["key"] for n in genes[:20]]},
                    "local": False,
                })
        if genes and self.step_status("tcm_cross_database_screen", "gene") not in {"done", "skipped"}:
            tasks.append({
                "tool": "tcm_cross_database_screen",
                "reason": "基因尚未核对多个数据库是否同时收录",
                "inputs": {"mode": "gene"},
                "local": False,
            })
        compounds = [n for n in self.nodes.values() if n["kind"] == "compound"]
        if compounds and self.step_status("tcm_cross_database_screen", "compound") not in {"done", "skipped"}:
            tasks.append({
                "tool": "tcm_cross_database_screen",
                "reason": "成分尚未核对多个数据库是否同时收录",
                "inputs": {"mode": "compound"},
                "local": False,
            })
        if compounds and self.step_status("tcm_admet_predict") not in {"done", "skipped"}:
            tasks.append({
                "tool": "tcm_admet_predict",
                "reason": "成分为本地 ADMET 近似留出节点注释",
                "inputs": {},
                "local": True,
            })
        if self.disease and (compounds or genes) and self.step_status("tcm_literature_mine") not in {"done", "skipped"}:
            tasks.append({
                "tool": "tcm_literature_mine",
                "reason": "文献库边尚未写入；计数只表示被研究过",
                "inputs": {},
                "local": False,
            })
        if self.disease and genes and self.step_status("tcm_trials_match") not in {"done", "skipped"}:
            tasks.append({
                "tool": "tcm_trials_match",
                "reason": "试验登记尚未写入；登记不是疗效",
                "inputs": {},
                "local": False,
            })
        return tasks

    def entity_nodes(self) -> List[dict]:
        return [n for n in self.nodes.values() if n["kind"] != "step"]

    def step_nodes(self) -> List[dict]:
        return [n for n in self.nodes.values() if n["kind"] == "step"]

    def to_dict(self) -> dict:
        nodes = sorted(self.nodes.values(), key=lambda n: (n["kind"], n["id"]))
        return {
            "disease": self.disease,
            "compounds": list(self.compounds),
            "genes": list(self.genes_seed),
            "herbs": list(self.herbs),
            "nodes": nodes,
            "edges": list(self.edges),
            "pending_tasks": self.pending_tasks(),
            "disclaimer": "拓扑图表示检索网络如何长成，以及每一步的状态。"
                          "它不是疗效、靶点或配伍结论。",
        }

    def to_mermaid(self, limit: int = 40) -> str:
        lines = ["flowchart LR"]
        shown = []
        for node in self.entity_nodes():
            if len(shown) >= limit:
                break
            shown.append(node["id"])
            label = f"{node['label']} [{node['status']}]".replace('"', "'")
            lines.append(f'  { _mermaid_id(node["id"]) }["{label}"]')
        shown_set = set(shown)
        for edge in self.edges:
            if edge["src"] in shown_set and edge["dst"] in shown_set:
                lines.append(
                    f'  {_mermaid_id(edge["src"])} -->|{edge["relation"]}| '
                    f'{_mermaid_id(edge["dst"])}')
        if len(lines) == 1:
            lines.append('  empty["分析网络尚未写入节点"]')
        return "\n".join(lines)

    def view_mode(self) -> str:
        """卡片 below 300 entity nodes, 球面 at that count. Steps do not count."""
        if len(self.entity_nodes()) >= SPHERE_ENTITY_THRESHOLD:
            return "球面"
        return "卡片"

    def to_svg(self) -> str:
        if len(self.entity_nodes()) >= SPHERE_ENTITY_THRESHOLD:
            return _svg_sphere(self)
        return _svg_cards(self)

    def to_html(self, svg: Optional[str] = None) -> str:
        """Self-contained page. The SVG is inlined; nothing is loaded from the network."""
        picture = self.to_svg() if svg is None else svg
        mode = html.escape(self.view_mode())
        note = html.escape("这张图记录检索过程里节点和库是怎样连起来的。它不是疗效结论。")
        reading = _reading_html(self.reading_lines())
        style = (
            "body{margin:0;background:" + PAPER_BG + ";color:" + INK + ";"
            'font-family:"Microsoft YaHei",sans-serif;}\n'
            ".note{margin:12px 16px 4px;font-size:13px;color:" + WOOD_DARK + ";}\n"
            ".reading{margin:8px 16px 16px;padding:12px 14px;background:" + CARD_PAPER
            + ";border:1px solid " + WOOD + ";border-radius:8px;}\n"
            ".reading h2{font-size:15px;margin:14px 0 6px;color:" + WOOD_DARK
            + ";font-weight:600;}\n"
            ".reading h2:first-child{margin-top:0;}\n"
            ".reading li{margin:2px 0;font-size:13px;line-height:1.5;}\n"
        )
        return "".join([
            "<!DOCTYPE html>\n",
            '<html lang="zh-CN">\n',
            "<head>\n",
            '<meta charset="utf-8"/>\n',
            "<title>分析网络拓扑</title>\n",
            "<style>\n",
            style,
            "</style>\n",
            "</head>\n",
            "<body>\n",
            f'<p class="note">{note}当前视图：{mode}。</p>\n',
            reading,
            "\n",
            picture,
            "\n</body>\n</html>\n",
        ])

    def summary_lines(self) -> List[str]:
        counts: Dict[str, int] = {}
        for node in self.entity_nodes():
            counts[node["kind"]] = counts.get(node["kind"], 0) + 1
        lines = [
            f"- 节点：{', '.join(f'{k} {v}' for k, v in sorted(counts.items())) or '尚无'}",
            f"- 边：{len(self.edges)}",
            f"- 视图：{self.view_mode()}",
        ]
        tasks = self.pending_tasks()
        if not tasks:
            lines.append("- 当前意图下没有待执行的分析边。")
        for task in tasks[:6]:
            lines.append(f"- 待执行 `{task['tool']}`：{task['reason']}")
        lines.append(f"- {self.to_dict()['disclaimer']}")
        lines.extend(self.reading_lines())
        return lines

    def reading_lines(self) -> List[str]:
        """Legend, chain, hints, and sources. Same words for SVG, HTML, and text."""
        lines: List[str] = []
        lines.extend(self.legend_lines())
        lines.extend(self.pathway_lines())
        lines.extend(self.next_step_lines())
        lines.extend(self.source_status_lines())
        return lines

    def legend_lines(self) -> List[str]:
        order = (
            "source", "disease", "pathway", "gene", "compound", "herb",
            "formula", "literature", "trial", "model", "step",
        )
        kinds = "、".join(_KIND_LABEL[kind] for kind in order)
        return [
            "图例",
            f"- 节点种类：{kinds}",
            "- 运行状态：ready 为长虚线；pending 为等长虚线；done 为实线并带轻流动；"
            "failed 为实线；skipped 为灰褐实线；deferred 为点线。状态不只靠颜色。",
            f"- 关系边界：{LIT_NOTE}；{FORMULA_NOTE}；{TRIAL_NOTE}；"
            f"{SCREEN_NOTE}；{DOCK_NOTE}。",
        ]

    def pathway_lines(self) -> List[str]:
        parts = []
        prev_kind = ""
        prev_label = ""
        for kind, label in _CHAIN:
            nodes = _nodes_kind(self, kind)
            if not nodes:
                parts.append(f"{label}（缺）")
            elif not prev_kind:
                status = _dominant_status([node.get("status") or "ready" for node in nodes])
                parts.append(f"{label}（图上已有，{_status_word(status)}）")
            elif not _edges_between(self, prev_kind, kind):
                parts.append(f"{label}（有节点，缺与{prev_label}的边）")
            else:
                edges = _edges_between(self, prev_kind, kind)
                status = _dominant_status([edge.get("status") or "done" for edge in edges])
                parts.append(f"{label}（已连接，{_status_word(status)}）")
            prev_kind, prev_label = kind, label
        return [
            "检索通路",
            "- " + " → ".join(parts),
            _literature_line(self),
            _formula_line(self),
            _dock_line(self),
        ]

    def next_step_lines(self) -> List[str]:
        lines = [
            "下一步建议",
            "- 这些是提示，不会自动执行，也不是疗效结论。",
        ]
        items = _suggest_next(self)
        if not items:
            lines.append("- 当前图上没有额外的下一步。")
            return lines
        for index, item in enumerate(items, 1):
            lines.append(f"- {index}. `{item['tool']}`：{item['reason']}")
        return lines

    def source_status_lines(self) -> List[str]:
        present = {node["key"]: node for node in _nodes_kind(self, "source")}
        appeared: List[str] = []
        failed: List[str] = []
        missing: List[str] = []
        seen = set()
        for key, label, _role in SOURCE_CATALOG:
            seen.add(key)
            node = present.get(key)
            if node is None:
                missing.append(label)
                continue
            shown = str(node.get("label") or label)
            status = node.get("status") if node.get("status") in _STATUS_COLOR else "ready"
            appeared.append(f"{shown}（{_status_word(status)}）")
            if node.get("status") == "failed":
                failed.append(shown)
        for key in sorted(present):
            if key in seen:
                continue
            node = present[key]
            shown = str(node.get("label") or key)
            status = node.get("status") if node.get("status") in _STATUS_COLOR else "ready"
            appeared.append(f"{shown}（{_status_word(status)}）")
            if node.get("status") == "failed":
                failed.append(shown)
        return [
            "数据源状态",
            "- 已出现：" + ("、".join(appeared) if appeared else "无"),
            "- 失败：" + ("、".join(failed) if failed else "无"),
            "- 缺失：" + ("、".join(missing) if missing else "无"),
        ]


def build_from_session(state: dict) -> AnalysisGraph:
    graph = AnalysisGraph()
    graph.seed_sources()
    graph.note_intent(
        disease=state.get("disease"),
        compounds=state.get("compounds") or [],
        genes=state.get("genes") or [],
        herbs=state.get("herbs") or [],
    )
    for record in state.get("executed") or []:
        if isinstance(record, dict):
            graph.observe(record)
    return graph


def layered_positions(nodes: List[dict]) -> Dict[str, tuple]:
    """Centers of the rounded cards. Empty layers close up; order follows LAYERS.

    The same nodes always land on the same centers.
    """
    columns = _entity_columns(nodes)
    slot = {layer: index for index, layer in enumerate(sorted(columns))}
    positions: Dict[str, tuple] = {}
    for layer, group in columns.items():
        column = slot[layer]
        for index, node in enumerate(group):
            x = _MARGIN_X + _CARD_W / 2 + column * (_CARD_W + _COL_GAP)
            y = _HEADER_H + _CARD_H / 2 + index * (_CARD_H + _ROW_GAP)
            positions[node["id"]] = (round(x, 2), round(y, 2))
    return positions


def _status_rank(status: str) -> int:
    return {"pending": 0, "ready": 1, "skipped": 2, "deferred": 2,
            "failed": 3, "done": 4}.get(status, 0)


def _record_status(tool: str, status) -> str:
    if "skipped" in tool.lower():
        return "skipped"
    raw = str(status or "ok").lower()
    if raw in {"error", "timeout", "failed"}:
        return "failed"
    return "done"


def _short(text: str, limit: int = 18) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _mermaid_id(node_id: str) -> str:
    return "n_" + re.sub(r"[^0-9A-Za-z_]", "_", node_id)[:48]


def _f(value: float) -> str:
    return f"{float(value):.2f}"


def _kind_name(kind: str) -> str:
    return _KIND_LABEL.get(kind, kind or "节点")


def _kind_token(kind: str) -> str:
    token = re.sub(r"[^0-9A-Za-z_-]", "", kind or "")
    return token or "kind"


def _tip(node: dict) -> str:
    label = str(node.get("label") or node.get("key") or "")
    detail = str(node.get("detail") or "").strip()
    if detail and detail != label:
        return f"{label} — {detail}"
    return label


def _detail_line(node: dict) -> str:
    detail = str(node.get("detail") or "").strip()
    if detail:
        return detail
    return _kind_name(str(node.get("kind") or ""))


def _entity_columns(nodes: Iterable[dict]) -> Dict[int, List[dict]]:
    columns: Dict[int, List[dict]] = {}
    for node in nodes:
        if node.get("kind") == "step":
            continue
        layer = LAYERS.get(node.get("kind"), 9)
        columns.setdefault(layer, []).append(node)
    for layer in columns:
        columns[layer] = sorted(columns[layer], key=lambda item: item["id"])
    return columns


def card_frame(nodes: Sequence[dict]) -> dict:
    positions = layered_positions(list(nodes))
    if not positions:
        return {"positions": positions, "width": _MIN_WIDTH, "bottom": _HEADER_H + 28}
    max_x = max(x for x, _ in positions.values())
    max_y = max(y for _, y in positions.values())
    width = max(_MIN_WIDTH, max_x + _CARD_W / 2 + _MARGIN_X)
    bottom = max_y + _CARD_H / 2
    return {"positions": positions, "width": width, "bottom": bottom}


def fibonacci_sphere(count: int) -> List[tuple]:
    """Deterministic points on the unit sphere. Same count, same coordinates."""
    count = int(count)
    if count <= 0:
        return []
    if count == 1:
        return [(0.0, 0.0, 1.0)]
    golden = math.pi * (3.0 - math.sqrt(5.0))
    points = []
    for index in range(count):
        y = 1.0 - (2.0 * index + 1.0) / count
        ring = math.sqrt(max(0.0, 1.0 - y * y))
        theta = golden * index
        x = math.cos(theta) * ring
        z = math.sin(theta) * ring
        points.append((round(x, 6), round(y, 6), round(z, 6)))
    return points


def _sphere_radius(count: int) -> float:
    raw = 18.0 + 8.0 * math.sqrt(max(int(count), 1))
    return round(min(120.0, max(68.0, raw)), 2)


def _place_spheres(counts: Dict[str, int]) -> Tuple[Dict[str, tuple], float, float]:
    if not counts:
        return {}, _MIN_WIDTH, _HEADER_H + 28
    kinds = sorted(counts, key=lambda kind: (LAYERS.get(kind, 50), kind))
    radii = {kind: _sphere_radius(counts[kind]) for kind in kinds}
    cols = 4 if len(kinds) > 4 else len(kinds)
    gap_x, gap_y = 72.0, 56.0
    rows = [kinds[start:start + cols] for start in range(0, len(kinds), cols)]
    row_widths = []
    for row in rows:
        row_widths.append(
            sum(radii[kind] * 2 for kind in row) + gap_x * (len(row) - 1))
    width = max(_MIN_WIDTH, max(row_widths) + 48.0)
    positions: Dict[str, tuple] = {}
    y_cursor = _HEADER_H + 10.0
    for row, row_width in zip(rows, row_widths):
        row_r = max(radii[kind] for kind in row)
        x_cursor = (width - row_width) / 2.0
        for kind in row:
            radius = radii[kind]
            cx = round(x_cursor + radius, 2)
            cy = round(y_cursor + row_r, 2)
            positions[kind] = (cx, cy, radius)
            x_cursor += radius * 2 + gap_x
        y_cursor += row_r * 2 + gap_y
    return positions, width, y_cursor


def sphere_scene(nodes: Sequence[dict]) -> dict:
    """Orthographic layout. One sphere per kind; points are deterministic."""
    grouped: Dict[str, List[dict]] = {}
    for node in nodes:
        if node.get("kind") == "step":
            continue
        grouped.setdefault(str(node.get("kind") or "node"), []).append(node)
    for kind in grouped:
        grouped[kind] = sorted(grouped[kind], key=lambda item: item["id"])
    counts = {kind: len(members) for kind, members in grouped.items()}
    centers, width, bottom = _place_spheres(counts)
    order = sorted(grouped, key=lambda kind: (LAYERS.get(kind, 50), kind))
    points: Dict[str, dict] = {}
    for kind in order:
        cx, cy, radius = centers[kind]
        inset = radius * 0.90
        for node, (ux, uy, uz) in zip(grouped[kind], fibonacci_sphere(len(grouped[kind]))):
            points[node["id"]] = {
                "x": round(cx + ux * inset, 2),
                "y": round(cy - uy * inset, 2),
                "z": round(uz, 4),
                "kind": kind,
                "cx": cx,
                "cy": cy,
                "r": radius,
            }
    return {
        "centers": centers,
        "points": points,
        "order": order,
        "groups": grouped,
        "width": width,
        "bottom": bottom,
    }


def _dominant_status(statuses: Sequence[str]) -> str:
    counts: Dict[str, int] = {}
    for status in statuses:
        key = status if status in _STATUS_COLOR else "ready"
        counts[key] = counts.get(key, 0) + 1
    return max(counts, key=lambda item: (counts[item], _STATUS_PRIORITY.get(item, 0)))


def _status_word(status: str) -> str:
    return _STATUS_WORD.get(status, status or "未知")


def _nodes_kind(graph: "AnalysisGraph", kind: str) -> List[dict]:
    return [node for node in graph.nodes.values() if node.get("kind") == kind]


def _edges_between(graph: "AnalysisGraph", kind_a: str, kind_b: str) -> List[dict]:
    found = []
    for edge in graph.edges:
        src = graph.nodes.get(edge.get("src"))
        dst = graph.nodes.get(edge.get("dst"))
        if not src or not dst:
            continue
        if {src.get("kind"), dst.get("kind")} == {kind_a, kind_b}:
            found.append(edge)
    return found


def _literature_names_pocket(graph: "AnalysisGraph") -> bool:
    lit_ids = {node["id"] for node in graph.nodes.values() if node.get("kind") == "literature"}
    if not lit_ids:
        return False
    blobs = []
    for node in graph.nodes.values():
        if node.get("kind") != "literature":
            continue
        blobs.append(str(node.get("label") or ""))
        blobs.append(str(node.get("detail") or ""))
    for edge in graph.edges:
        if edge.get("src") in lit_ids or edge.get("dst") in lit_ids:
            blobs.append(str(edge.get("detail") or ""))
    text = "\n".join(blobs).lower()
    return any(marker.lower() in text for marker in _POCKET_MARKERS)


def _literature_line(graph: "AnalysisGraph") -> str:
    rows = _nodes_kind(graph, "literature")
    if not rows:
        return "- 文献：缺失"
    status = _dominant_status([node.get("status") or "ready" for node in rows])
    return f"- 文献：已出现，{_status_word(status)}。{LIT_NOTE}"


def _formula_line(graph: "AnalysisGraph") -> str:
    herbs = _nodes_kind(graph, "herb")
    formula = _nodes_kind(graph, "formula")
    overlaps = [edge for edge in graph.edges if edge.get("relation") == "set_overlap"]
    if len(herbs) < 2:
        return "- 方剂集合重叠：药材少于 2 个，不算集合重叠"
    if overlaps or any(node.get("status") == "done" for node in formula):
        statuses = [edge.get("status") or "done" for edge in overlaps] or [
            node.get("status") or "done" for node in formula
        ]
        status = _dominant_status(statuses)
        return f"- 方剂集合重叠：已写入，{_status_word(status)}。{FORMULA_NOTE}"
    if formula:
        status = _dominant_status([node.get("status") or "ready" for node in formula])
        return (
            f"- 方剂集合重叠：节点已在，尚未算出重叠，{_status_word(status)}。"
            f"{FORMULA_NOTE}"
        )
    return f"- 方剂集合重叠：至少两味药材已在图上，集合节点还没有。{FORMULA_NOTE}"


def _dock_line(graph: "AnalysisGraph") -> str:
    models = _nodes_kind(graph, "model")
    if models:
        status = _dominant_status([node.get("status") or "ready" for node in models])
        return f"- 对接：模型节点已出现，{_status_word(status)}。{DOCK_NOTE}"
    if _literature_names_pocket(graph):
        return f"- 对接：文献写明口袋，模型节点还没有。{DOCK_NOTE}"
    return "- 对接：缺失。文献未写明口袋"


def _suggest_next(graph: "AnalysisGraph") -> List[dict]:
    """Up to three hints. Does not call tools or change scientific thresholds."""
    out: List[dict] = []
    seen = set()

    def add(tool: str, reason: str) -> None:
        if len(out) >= 3 or not tool or tool in seen:
            return
        if tool == "tcm_formula_analysis" and len(_nodes_kind(graph, "herb")) < 2:
            return
        if tool.startswith("dock_") and not _literature_names_pocket(graph):
            return
        seen.add(tool)
        out.append({"tool": tool, "reason": reason})

    for step in sorted(graph.step_nodes(), key=lambda node: node["id"]):
        if step.get("status") != "failed":
            continue
        tool = str(step.get("label") or step.get("key") or "").split(" (", 1)[0].strip()
        add(tool, "该步骤在图上是失败，可再调用一次核对。失败记录仍留在图上。")

    for key, _label, _role in SOURCE_CATALOG:
        node = graph.nodes.get(graph.node_id("source", key))
        if not node or node.get("status") != "failed":
            continue
        label = str(node.get("label") or key)
        add(
            _SOURCE_RETRY.get(key, ""),
            f"来源「{label}」失败，可再查这一来源。失败记录仍留在图上。",
        )

    herbs = _nodes_kind(graph, "herb")
    compounds = _nodes_kind(graph, "compound")
    genes = _nodes_kind(graph, "gene")
    diseases = _nodes_kind(graph, "disease")
    pathways = _nodes_kind(graph, "pathway")

    if diseases and not _edges_between(graph, "disease", "pathway"):
        if graph.step_status("tcm_kegg_disease_pathways") not in {"done", "skipped"}:
            add("tcm_kegg_disease_pathways", "疾病节点还没有连到通路。")

    if herbs and not compounds:
        add("tcm_tcmsp_search", "图上已有药材，还没有成分。可查该药材的成分表。")

    if (len(herbs) >= 2
            and graph.step_status("tcm_formula_analysis") not in {"done", "skipped"}):
        add(
            "tcm_formula_analysis",
            "至少两味药材已在图上，可计算成分集合重叠。集合重叠不是药效。",
        )

    if (diseases and (genes or compounds)
            and not _nodes_kind(graph, "literature")
            and graph.step_status("tcm_literature_mine") not in {"done", "skipped"}):
        add("tcm_literature_mine", "疾病已与基因或成分同图，文献节点还没有。共现不是因果。")

    if genes and compounds and not _edges_between(graph, "gene", "compound"):
        add("tcm_symmap_lookup", "基因和成分都在图上，中间还没有边。可核对药材-成分-基因记录。")

    if pathways and genes and not compounds and not herbs:
        if (graph.step_status("tcm_network_analysis") not in {"done", "skipped"}
                and (graph.n_genes >= 15 or len(genes) >= 15)):
            add(
                "tcm_network_analysis",
                "通路和基因已连接，成分和药材还没有。基因不少于 15 时，可先做网络分析。",
            )
        else:
            add(
                "tcm_cross_database_screen",
                "通路和基因已连接，成分还没有。可核对基因是否被多个库同时收录。收录计数不是疗效。",
            )

    if (_literature_names_pocket(graph)
            and not _nodes_kind(graph, "model")
            and graph.step_status("dock_vina_docking") not in {"done", "skipped"}):
        add(
            "dock_vina_docking",
            "文献写明了口袋残基。输出仍是提议的对接模型，不是已记录结合证据。",
        )

    if not out and not graph.entity_nodes():
        add("tcm_kegg_disease_pathways", "图上还没有实体节点。若从疾病入手，可先查通路。")
        add(
            "tcm_tcmsp_search",
            "若从一味药材入手，可先查成分表。药材少于 2 个时不要做方剂集合重叠。",
        )
    return out


def _reading_html(lines: Sequence[str]) -> str:
    sections: List[Tuple[str, List[str]]] = []
    title = ""
    items: List[str] = []
    for line in lines:
        if line in _READING_CLASS:
            if title:
                sections.append((title, items))
            title = line
            items = []
            continue
        items.append(line[2:] if line.startswith("- ") else line)
    if title:
        sections.append((title, items))
    parts = ['<section class="reading">']
    for heading, rows in sections:
        css = _READING_CLASS[heading]
        parts.append(f'<h2 class="{css}">{html.escape(heading)}</h2>')
        parts.append("<ul>")
        for row in rows:
            parts.append(f"<li>{html.escape(row)}</li>")
        parts.append("</ul>")
    parts.append("</section>")
    return "\n".join(parts)


def _wrap_text(text: str, limit_px: float, size: float) -> List[str]:
    text = str(text or "")
    if not text:
        return [""]
    lines: List[str] = []
    current = ""
    for ch in text:
        trial = current + ch
        if current and _text_px(trial, size) > limit_px:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _reading_svg(lines: Sequence[str], top: float, width: float) -> Tuple[List[str], float]:
    size = 12.0
    inner = max(240.0, width - 56.0)
    wrapped: List[str] = []
    classes: List[str] = []
    for line in lines:
        pieces = _wrap_text(line, inner, size)
        css = _READING_CLASS.get(line, "reading-line")
        for index, piece in enumerate(pieces):
            wrapped.append(piece)
            classes.append(css if index == 0 else "reading-line")
    line_h = 16.0
    pad = 14.0
    height = pad * 2 + line_h * max(1, len(wrapped))
    parts = [
        '<g class="reading-panel">',
        (
            f'<rect x="16" y="{_f(top)}" width="{_f(max(32.0, width - 32))}" '
            f'height="{_f(height)}" rx="6" fill="{CARD_PAPER}" stroke="{WOOD}" '
            f'stroke-width="1.25"/>'
        ),
    ]
    y = top + pad + 12
    for css, line in zip(classes, wrapped):
        weight = ' font-weight="600"' if css != "reading-line" else ""
        parts.append(
            f'<text class="{css}" x="28" y="{_f(y)}" font-size="12" fill="{INK}"{weight}>'
            f"{html.escape(line)}</text>"
        )
        y += line_h
    parts.append("</g>")
    return parts, top + height


def cross_kind_arcs(nodes: Sequence[dict], edges: Sequence[dict]) -> List[dict]:
    """One directed arc per source kind, destination kind, and relation."""
    by_id = {node["id"]: node for node in nodes if node.get("kind") != "step"}
    buckets: Dict[tuple, List[str]] = {}
    for edge in edges:
        src = by_id.get(edge.get("src"))
        dst = by_id.get(edge.get("dst"))
        if not src or not dst or src.get("kind") == dst.get("kind"):
            continue
        relation = str(edge.get("relation") or "")
        key = (str(src.get("kind")), str(dst.get("kind")), relation)
        buckets.setdefault(key, []).append(str(edge.get("status") or "done"))
    arcs = []
    order = sorted(
        buckets,
        key=lambda item: (
            LAYERS.get(item[0], 50), item[0],
            LAYERS.get(item[1], 50), item[1], item[2],
        ),
    )
    for src_kind, dst_kind, relation in order:
        statuses = buckets[(src_kind, dst_kind, relation)]
        arcs.append({
            "src_kind": src_kind,
            "dst_kind": dst_kind,
            "relation": relation,
            "count": len(statuses),
            "status": _dominant_status(statuses),
        })
    return arcs


def _svg_document(mode: str, width: float, height: float, body: List[str]) -> str:
    w = int(math.ceil(width))
    h = int(math.ceil(height))
    label = "卡片" if mode == "card" else "球面"
    aria = html.escape(f"分析网络拓扑，{label}", quote=True)
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" data-mode="{mode}" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'font-family="Microsoft YaHei, sans-serif" aria-label="{aria}">'
    )
    return "\n".join([head, *body, "</svg>"])


def _defs_block(extra: str = "") -> str:
    lines = ["<defs>"]
    for status, color in _STATUS_COLOR.items():
        lines.append(
            f'<marker id="arrow-{status}" viewBox="0 0 10 10" refX="8.2" refY="5" '
            f'markerWidth="7" markerHeight="7" orient="auto">'
            f'<path d="M 0 1.3 L 8.2 5 L 0 8.7 z" fill="{color}"/></marker>'
        )
    if extra:
        lines.append(extra)
    lines.append("</defs>")
    return "\n".join(lines)


def _nebula_defs(kinds: Sequence[str]) -> str:
    parts = []
    for kind in kinds:
        core, mid = _KIND_NEBULA.get(kind, (SHELL_AMBER, SHELL_AMBER_DEEP))
        token = _kind_token(kind)
        parts.append(
            f'<radialGradient id="nebula-{token}" cx="34%" cy="32%" r="68%">'
            f'<stop offset="0%" stop-color="{core}" stop-opacity="0.55"/>'
            f'<stop offset="42%" stop-color="{mid}" stop-opacity="0.20"/>'
            f'<stop offset="100%" stop-color="{_BG}" stop-opacity="0.02"/>'
            f'</radialGradient>'
        )
    return "\n".join(parts)


def _svg_header(subtitle: str) -> List[str]:
    parts = [
        f'<rect width="100%" height="100%" fill="{_BG}"/>',
        f'<text x="16" y="22" font-size="15" fill="{INK}">分析网络拓扑</text>',
        f'<text x="16" y="40" font-size="12" fill="{WOOD_DARK}">{html.escape(subtitle)}</text>',
    ]
    x = 16
    for status in ("ready", "pending", "done", "failed", "skipped", "deferred"):
        color = _STATUS_COLOR[status]
        parts.append(
            f'<rect x="{x}" y="50" width="10" height="10" rx="2" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{x + 14}" y="60" font-size="11" fill="{WOOD_DARK}">{status}</text>'
        )
        x += 112
    return parts


def _text_px(text: str, size: float) -> float:
    width = 0.0
    for ch in text:
        width += size if ord(ch) > 127 else size * 0.62
    return width


def _fit(text: str, limit_px: float, size: float) -> str:
    text = str(text or "")
    if _text_px(text, size) <= limit_px:
        return text
    kept = ""
    for ch in text:
        if _text_px(kept + ch + "…", size) > limit_px:
            break
        kept += ch
    return (kept or text[:1]) + "…"


def _rounded_node(node: dict, x: float, y: float, w: float, h: float) -> str:
    status = node.get("status") if node.get("status") in _STATUS_COLOR else "ready"
    color = _STATUS_COLOR[status]
    fill = _KIND_FILL.get(node.get("kind"), CARD_PAPER)
    kind = _kind_token(str(node.get("kind") or ""))
    inner = max(24.0, w - 28.0)
    name = html.escape(_fit(node.get("label") or node.get("key") or "", inner, 12))
    detail = html.escape(_fit(_detail_line(node), inner, 10))
    tip = html.escape(_tip(node))
    return (
        f'<g class="node status-{status} kind-{kind}">'
        f"<title>{tip}</title>"
        f'<rect class="card" x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" '
        f'rx="6" fill="{fill}" stroke="{WOOD}" stroke-width="1.25"/>'
        f'<rect class="status-bar" x="{_f(x + 4)}" y="{_f(y + 7)}" width="3" '
        f'height="{_f(h - 14)}" rx="1.5" fill="{color}"/>'
        f'<text class="name" x="{_f(x + 14)}" y="{_f(y + 17)}" font-size="12" '
        f'fill="{INK}">{name}</text>'
        f'<text class="detail" x="{_f(x + 14)}" y="{_f(y + 32)}" font-size="10" '
        f'fill="{WOOD_DARK}">{detail}</text>'
        f"</g>"
    )


def _edge_svg(element: str, geom: str, status: str) -> List[str]:
    status = status if status in _STATUS_COLOR else "ready"
    color = _STATUS_COLOR[status]
    arrow = f'marker-end="url(#arrow-{status})"'
    common = (
        f'<{element} class="edge edge-{status}" {geom} '
        f'stroke="{color}" fill="none" stroke-linecap="round" '
    )
    if status == "failed":
        return [common + f'stroke-width="1.6" {arrow}/>']
    if status == "skipped":
        return [common + f'stroke-width="1.15" stroke-opacity="0.55" {arrow}/>']
    if status == "done":
        solid = common + f'stroke-width="1.45" {arrow}/>'
        flow = (
            f'<{element} class="edge-flow edge-{status}" {geom} '
            f'stroke="{color}" fill="none" stroke-linecap="round" '
            f'stroke-width="1.45" stroke-dasharray="10 18" stroke-opacity="0.45">'
            f'<animate attributeName="stroke-dashoffset" from="0" to="-56" '
            f'dur="2.8s" repeatCount="indefinite"/></{element}>'
        )
        return [solid, flow]
    if status == "pending":
        return [common + (
            f'stroke-width="1.35" stroke-dasharray="5 5" {arrow}>'
            f'<animate attributeName="stroke-dashoffset" from="0" to="-20" '
            f'dur="1.2s" repeatCount="indefinite"/></{element}>'
        )]
    if status == "deferred":
        return [common + (
            f'stroke-width="1.25" stroke-dasharray="2 7" {arrow}>'
            f'<animate attributeName="stroke-dashoffset" from="0" to="-18" '
            f'dur="2.2s" repeatCount="indefinite"/></{element}>'
        )]
    return [common + (
        f'stroke-width="1.3" stroke-dasharray="9 5" {arrow}>'
        f'<animate attributeName="stroke-dashoffset" from="0" to="-28" '
        f'dur="1.8s" repeatCount="indefinite"/></{element}>'
    )]


def _port(cx: float, cy: float, tx: float, ty: float) -> tuple:
    """Leave a card from the side that faces the other card."""
    if abs(tx - cx) >= abs(ty - cy):
        side = 1.0 if tx >= cx else -1.0
        return cx + side * (_CARD_W / 2 - 1.5), cy
    side = 1.0 if ty >= cy else -1.0
    return cx, cy + side * (_CARD_H / 2 - 1.5)


def _connector(x1: float, y1: float, x2: float, y2: float, slot: int) -> str:
    dx, dy = x2 - x1, y2 - y1
    shift = 0.0 if slot == 0 else 8.0 * ((slot + 1) // 2) * (1 if slot % 2 else -1)
    if abs(dx) >= abs(dy):
        y1 += shift
        y2 += shift
        span = max(32.0, abs(dx) * 0.45)
        sign = 1.0 if dx >= 0 else -1.0
        c1x, c1y = x1 + sign * span, y1
        c2x, c2y = x2 - sign * span, y2
    else:
        x1 += shift
        x2 += shift
        bulge = 32.0 + abs(shift)
        c1x, c1y = x1 + bulge, y1
        c2x, c2y = x2 + bulge, y2
    return (
        f"M {_f(x1)} {_f(y1)} C {_f(c1x)} {_f(c1y)} "
        f"{_f(c2x)} {_f(c2y)} {_f(x2)} {_f(y2)}"
    )


def _column_captions(nodes: Sequence[dict], positions: Dict[str, tuple]) -> List[str]:
    kind_at_x: Dict[float, str] = {}
    for node in nodes:
        pos = positions.get(node["id"])
        if not pos:
            continue
        kind_at_x.setdefault(pos[0], str(node.get("kind") or ""))
    parts = []
    for x, kind in sorted(kind_at_x.items()):
        parts.append(
            f'<text class="column-label" x="{_f(x)}" y="76" text-anchor="middle" '
            f'font-size="11" fill="{WOOD}">{html.escape(_kind_name(kind))}</text>'
        )
    return parts


def _card_edges(edges: Sequence[dict], positions: Dict[str, tuple]) -> List[str]:
    grouped: Dict[tuple, List[dict]] = {}
    for edge in edges:
        src, dst = edge.get("src"), edge.get("dst")
        if src not in positions or dst not in positions or src == dst:
            continue
        grouped.setdefault((src, dst), []).append(edge)
    parts: List[str] = []
    for key in sorted(grouped):
        x1c, y1c = positions[key[0]]
        x2c, y2c = positions[key[1]]
        for index, edge in enumerate(grouped[key]):
            x1, y1 = _port(x1c, y1c, x2c, y2c)
            x2, y2 = _port(x2c, y2c, x1c, y1c)
            if math.hypot(x2 - x1, y2 - y1) < 4:
                continue
            geom = f'd="{_connector(x1, y1, x2, y2, index)}"'
            parts.extend(_edge_svg("path", geom, str(edge.get("status") or "done")))
    return parts


def _step_block(steps: Sequence[dict], top: float, width: float) -> Tuple[List[str], float]:
    parts = [
        f'<text x="16" y="{_f(top)}" font-size="13" fill="{INK}">执行状态</text>'
    ]
    if not steps:
        parts.append(
            f'<text x="16" y="{_f(top + 20)}" font-size="11" fill="{WOOD}">尚无步骤</text>'
        )
        return parts, top + 36
    chip_w, chip_h, gap_x, gap_y = 232.0, 42.0, 8.0, 8.0
    cols = max(1, int((width - 32) // (chip_w + gap_x)))
    for index, node in enumerate(steps):
        col = index % cols
        row = index // cols
        x = 16 + col * (chip_w + gap_x)
        y = top + 12 + row * (chip_h + gap_y)
        parts.append(_rounded_node(node, x, y, chip_w, chip_h))
    rows = (len(steps) + cols - 1) // cols
    bottom = top + 12 + rows * (chip_h + gap_y) + 6
    return parts, bottom


def _svg_cards(graph: "AnalysisGraph") -> str:
    entities = graph.entity_nodes()
    frame = card_frame(entities)
    parts = [_defs_block()]
    parts.extend(_svg_header(
        "当前是卡片。浅暖纸底、木色边框。圆角矩形里是名称和详情，箭头是检索方向。这不是疗效结论。"
    ))
    if not entities:
        parts.append(
            f'<text x="16" y="108" font-size="12" fill="{WOOD}">分析网络尚未写入节点</text>'
        )
    else:
        parts.extend(_column_captions(entities, frame["positions"]))
    parts.extend(_card_edges(graph.edges, frame["positions"]))
    for node in sorted(entities, key=lambda item: item["id"]):
        pos = frame["positions"].get(node["id"])
        if not pos:
            continue
        cx, cy = pos
        parts.append(_rounded_node(
            node, cx - _CARD_W / 2, cy - _CARD_H / 2, _CARD_W, _CARD_H,
        ))
    steps, step_bottom = _step_block(
        sorted(graph.step_nodes(), key=lambda item: item["id"]),
        frame["bottom"] + 28, frame["width"],
    )
    parts.extend(steps)
    reading, bottom = _reading_svg(graph.reading_lines(), step_bottom + 10, frame["width"])
    parts.extend(reading)
    return _svg_document("card", frame["width"], bottom + 16, parts)


def _rim(cx: float, cy: float, radius: float, tx: float, ty: float, gap: float = 2.0):
    dx, dy = tx - cx, ty - cy
    dist = math.hypot(dx, dy) or 1.0
    reach = radius + gap
    return cx + dx / dist * reach, cy + dy / dist * reach


def _curve(x1, y1, x2, y2, bend: float):
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    dx, dy = x2 - x1, y2 - y1
    dist = math.hypot(dx, dy) or 1.0
    px, py = -dy / dist, dx / dist
    cx, cy = mx + px * bend, my + py * bend
    label_x = 0.25 * x1 + 0.5 * cx + 0.25 * x2
    label_y = 0.25 * y1 + 0.5 * cy + 0.25 * y2
    path = f"M {_f(x1)} {_f(y1)} Q {_f(cx)} {_f(cy)} {_f(x2)} {_f(y2)}"
    return path, label_x, label_y


def _arc_label(x: float, y: float, label: str) -> str:
    text = html.escape(label)
    width = _text_px(label, 11) + 14
    return (
        '<g class="arc-label">'
        f'<rect x="{_f(x - width / 2)}" y="{_f(y - 11)}" width="{_f(width)}" height="16" '
        f'rx="3" fill="{CARD_PAPER}" fill-opacity="0.92" stroke="{WOOD}" stroke-width="0.6"/>'
        f'<text x="{_f(x)}" y="{_f(y)}" text-anchor="middle" font-size="11" '
        f'fill="{INK}">{text}</text>'
        "</g>"
    )


def _arc_parts(arcs: Sequence[dict], centers: Dict[str, tuple]) -> List[str]:
    by_pair: Dict[tuple, List[dict]] = {}
    for arc in arcs:
        if arc["src_kind"] not in centers or arc["dst_kind"] not in centers:
            continue
        by_pair.setdefault((arc["src_kind"], arc["dst_kind"]), []).append(arc)
    parts: List[str] = []
    for pair in sorted(by_pair):
        for index, arc in enumerate(by_pair[pair]):
            src = centers[arc["src_kind"]]
            dst = centers[arc["dst_kind"]]
            x1, y1 = _rim(src[0], src[1], src[2], dst[0], dst[1])
            x2, y2 = _rim(dst[0], dst[1], dst[2], src[0], src[1])
            bend = 34 + index * 16
            if index % 2 == 1:
                bend = -bend
            path, label_x, label_y = _curve(x1, y1, x2, y2, bend)
            relation = str(arc["relation"] or "link")
            geom = (
                f'data-from="{html.escape(str(arc["src_kind"]), quote=True)}" '
                f'data-to="{html.escape(str(arc["dst_kind"]), quote=True)}" '
                f'data-relation="{html.escape(relation, quote=True)}" '
                f'data-count="{int(arc["count"])}" d="{path}"'
            )
            parts.extend(_edge_svg("path", geom, str(arc["status"])))
            parts.append(_arc_label(label_x, label_y, f"{_short(relation, 24)} · {arc['count']}"))
    return parts


def _sphere_group(kind: str, scene: dict) -> str:
    members = scene["groups"][kind]
    cx, cy, radius = scene["centers"][kind]
    token = _kind_token(kind)
    bits = [
        f'<g class="sphere-group" data-kind="{html.escape(kind, quote=True)}" '
        f'data-count="{len(members)}">',
        (
            f'<circle class="shell" cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(radius)}" '
            f'fill="url(#nebula-{token})" stroke="{WOOD}" stroke-opacity="0.55" '
            f'stroke-width="1.15"/>'
        ),
        (
            f'<ellipse cx="{_f(cx)}" cy="{_f(cy)}" rx="{_f(radius)}" ry="{_f(radius * 0.18)}" '
            f'fill="none" stroke="{SHELL_AMBER_DEEP}" stroke-opacity="0.45"/>'
        ),
    ]
    ranked = []
    for node in members:
        point = scene["points"][node["id"]]
        ranked.append((point["z"], node["id"], node, point))
    ranked.sort()
    for _z, _nid, node, point in ranked:
        status = node.get("status") if node.get("status") in _STATUS_COLOR else "ready"
        depth = (point["z"] + 1.0) / 2.0
        dot = 2.3 + depth * 1.5
        opacity = 0.42 + depth * 0.58
        bits.append(
            f'<circle class="point status-{status}" cx="{_f(point["x"])}" cy="{_f(point["y"])}" '
            f'r="{_f(dot)}" fill="{_STATUS_COLOR[status]}" fill-opacity="{opacity:.2f}">'
            f"<title>{html.escape(_tip(node))}</title></circle>"
        )
    caption = html.escape(f"{_kind_name(kind)} · {len(members)}")
    bits.append(
        f'<text class="sphere-caption" x="{_f(cx)}" y="{_f(cy + radius + 18)}" '
        f'text-anchor="middle" font-size="12" fill="{INK}">{caption}</text>'
    )
    bits.append("</g>")
    return "\n".join(bits)


def _svg_sphere(graph: "AnalysisGraph") -> str:
    entities = graph.entity_nodes()
    scene = sphere_scene(entities)
    parts = [_defs_block(_nebula_defs(scene["order"]))]
    parts.extend(_svg_header(
        "当前是球面。浅暖纸底。实体节点满 300 后按类别分球，球壳是暖琥珀，弧上是关系和边数。这不是疗效结论。"
    ))
    for kind in scene["order"]:
        parts.append(_sphere_group(kind, scene))
    parts.extend(_arc_parts(cross_kind_arcs(entities, graph.edges), scene["centers"]))
    steps, step_bottom = _step_block(
        sorted(graph.step_nodes(), key=lambda item: item["id"]),
        scene["bottom"] + 8, scene["width"],
    )
    parts.extend(steps)
    reading, bottom = _reading_svg(graph.reading_lines(), step_bottom + 10, scene["width"])
    parts.extend(reading)
    return _svg_document("sphere", scene["width"], bottom + 16, parts)
