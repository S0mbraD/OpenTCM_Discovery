"""Analysis network and its live topology.

The graph is the herb–compound–target–pathway–disease form used in
TCM network pharmacology, plus the libraries that supplied each edge:
pathway databases, literature, and the local formula index. Layout and
SVG are pure Python so an agent can redraw the picture after every step.

Edges record where a link was retrieved. They do not promote a
co-occurrence count, a set overlap, or a trial listing into efficacy.
"""

from __future__ import annotations

import html
import re
from typing import Dict, Iterable, List, Optional

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

_STATUS_COLOR = {
    "ready": "#3d6f99",
    "pending": "#c47b00",
    "done": "#1b7f4e",
    "failed": "#a33b32",
    "skipped": "#777777",
    "deferred": "#6b5b95",
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

    def to_svg(self) -> str:
        entities = self.entity_nodes()
        positions = layered_positions(entities)
        steps = self.step_nodes()
        width = 980
        entity_height = 80 + 28 * max((1, _column_height(entities)))
        step_top = entity_height + 24
        height = step_top + 70
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#f7f4ee"/>',
            '<text x="16" y="22" font-family="Microsoft YaHei, sans-serif" '
            'font-size="14">分析网络拓扑：数据库、文献库与方剂索引如何连到疾病、通路、基因、成分和药材</text>',
        ]
        for edge in self.edges:
            if edge["src"] not in positions or edge["dst"] not in positions:
                continue
            x1, y1 = positions[edge["src"]]
            x2, y2 = positions[edge["dst"]]
            color = "#a33b32" if edge["status"] == "failed" else "#8a8175"
            parts.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{color}" stroke-width="1.2"/>')
        for node in entities:
            x, y = positions[node["id"]]
            color = _STATUS_COLOR.get(node["status"], "#555")
            parts.append(
                f'<circle cx="{x}" cy="{y}" r="7" fill="{color}"/>')
            parts.append(
                f'<text x="{x + 10}" y="{y + 4}" font-family="Microsoft YaHei, sans-serif" '
                f'font-size="11">{html.escape(_short(node["label"]))}</text>')
        parts.append(
            f'<text x="16" y="{step_top}" font-family="Microsoft YaHei, sans-serif" '
            f'font-size="13">执行状态</text>')
        for index, node in enumerate(steps[:12]):
            x = 16 + (index % 6) * 155
            y = step_top + 18 + (index // 6) * 22
            color = _STATUS_COLOR.get(node["status"], "#555")
            parts.append(
                f'<rect x="{x}" y="{y}" width="10" height="10" fill="{color}"/>')
            parts.append(
                f'<text x="{x + 14}" y="{y + 10}" font-family="Microsoft YaHei, sans-serif" '
                f'font-size="11">{html.escape(_short(node["label"], 22))}</text>')
        parts.append("</svg>")
        return "\n".join(parts)

    def summary_lines(self) -> List[str]:
        counts: Dict[str, int] = {}
        for node in self.entity_nodes():
            counts[node["kind"]] = counts.get(node["kind"], 0) + 1
        lines = [
            f"- 节点：{', '.join(f'{k} {v}' for k, v in sorted(counts.items())) or '尚无'}",
            f"- 边：{len(self.edges)}",
        ]
        tasks = self.pending_tasks()
        if not tasks:
            lines.append("- 当前意图下没有待执行的分析边。")
        for task in tasks[:6]:
            lines.append(f"- 待执行 `{task['tool']}`：{task['reason']}")
        lines.append(f"- {self.to_dict()['disclaimer']}")
        return lines


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
    columns: Dict[int, List[dict]] = {}
    for node in nodes:
        columns.setdefault(LAYERS.get(node["kind"], 9), []).append(node)
    positions = {}
    for layer, group in columns.items():
        group = sorted(group, key=lambda n: n["id"])
        for index, node in enumerate(group):
            positions[node["id"]] = (36 + layer * 92, 48 + index * 28)
    return positions


def _column_height(nodes: List[dict]) -> int:
    counts: Dict[int, int] = {}
    for node in nodes:
        layer = LAYERS.get(node["kind"], 9)
        counts[layer] = counts.get(layer, 0) + 1
    return max(counts.values()) if counts else 1


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
