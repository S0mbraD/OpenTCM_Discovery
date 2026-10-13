"""The analysis network grows from retrieval results and redraws itself."""

import math
import re
import unittest

from tcm_core.analysis_graph import (
    CARD_PAPER,
    DOCK_NOTE,
    FORMULA_NOTE,
    INK,
    LIT_NOTE,
    PAPER_BG,
    SCREEN_NOTE,
    SHELL_AMBER,
    SPHERE_ENTITY_THRESHOLD,
    STATUS_DEFERRED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_READY,
    STATUS_SKIPPED,
    TRIAL_NOTE,
    WOOD,
    WOOD_DARK,
    AnalysisGraph,
    build_from_session,
    cross_kind_arcs,
    fibonacci_sphere,
    herbs_in_text,
    layered_positions,
    sphere_scene,
)


def _genes(n):
    return " ".join(f"G{i}" for i in range(n))


class AnalysisGraphTests(unittest.TestCase):
    def test_pathway_result_links_disease_library_and_genes(self):
        graph = AnalysisGraph()
        graph.seed_sources()
        graph.note_intent(disease="ulcerative colitis")
        graph.observe({
            "tool": "tcm_kegg_disease_pathways",
            "status": "ok",
            "result": {
                "disease": "ulcerative colitis",
                "pathways": [{
                    "pathway_id": "hsa04621",
                    "pathway_name": "NOD-like receptor",
                    "related_genes": _genes(16),
                }],
            },
        })
        self.assertEqual(graph.step_status("tcm_kegg_disease_pathways"), "done")
        self.assertGreaterEqual(graph.n_genes, 15)
        kinds = {n["kind"] for n in graph.entity_nodes()}
        self.assertIn("pathway", kinds)
        self.assertIn("gene", kinds)
        tasks = [t["tool"] for t in graph.pending_tasks()]
        self.assertIn("tcm_network_analysis", tasks)
        self.assertNotIn("tcm_kegg_disease_pathways", tasks)

    def test_failed_channel_stays_visible_and_is_not_a_hit(self):
        graph = AnalysisGraph()
        graph.seed_sources()
        graph.observe({
            "tool": "tcm_cross_database_screen",
            "status": "ok",
            "result": {
                "mode": "compound",
                "consensus_ranking": [{
                    "entity": "berberine",
                    "consensus_hits": 1,
                    "channels": {
                        "pubchem": {"cid": "2353"},
                        "chembl": {"error": "no compound hit"},
                    },
                }],
            },
        })
        failed = [e for e in graph.edges if e["status"] == "failed"]
        done = [e for e in graph.edges if e["relation"] == "recorded_in" and e["status"] == "done"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(len(done), 1)
        self.assertIn(SCREEN_NOTE, done[0]["detail"])
        self.assertEqual(graph.step_status("tcm_cross_database_screen", "compound"), "done")
        self.assertEqual(graph.step_status("tcm_cross_database_screen", "gene"), "")

    def test_literature_and_formula_keep_their_limits(self):
        graph = AnalysisGraph()
        graph.seed_sources()
        graph.note_intent(disease="colitis", compounds=["berberine"], herbs=["黄连", "黄芩"])
        graph.observe({
            "tool": "tcm_literature_mine",
            "status": "ok",
            "result": {
                "compound": "berberine",
                "disease": "colitis",
                "genes": ["TNF"],
                "counts": {"full_triple": 3},
            },
        })
        graph.observe({
            "tool": "tcm_formula_analysis",
            "status": "ok",
            "result": {
                "herbs_matched": {"黄连": {}, "黄芩": {}},
                "pairwise_ingredients": [{
                    "pair": "黄连 + 黄芩", "jaccard": 0.02,
                }],
                "index_source": "SymMap v2.0",
            },
        })
        details = " ".join(e["detail"] for e in graph.edges)
        self.assertIn(LIT_NOTE, details)
        self.assertIn(FORMULA_NOTE, details)
        self.assertNotIn("tcm_formula_analysis",
                         [t["tool"] for t in graph.pending_tasks()])

    def test_one_herb_does_not_schedule_formula_overlap(self):
        graph = AnalysisGraph()
        graph.seed_sources()
        graph.note_intent(herbs=["黄连"])
        self.assertNotIn("tcm_formula_analysis",
                         [t["tool"] for t in graph.pending_tasks()])

    def test_d1_skip_is_a_skipped_network_step(self):
        graph = AnalysisGraph()
        graph.observe({
            "tool": "(skipped: D1 direct branch)",
            "status": "ok",
            "result": {},
        })
        self.assertEqual(graph.step_status("tcm_network_analysis"), "skipped")

    def test_layout_and_svg_are_stable_and_escaped(self):
        graph = AnalysisGraph()
        graph.seed_sources()
        graph.note_intent(disease="A<B")
        first = layered_positions(graph.entity_nodes())
        second = layered_positions(graph.entity_nodes())
        self.assertEqual(first, second)
        svg = graph.to_svg()
        self.assertIn("<svg", svg)
        self.assertIn('rx="6"', svg)
        self.assertNotIn("<circle", svg)
        self.assertIn("Microsoft YaHei, sans-serif", svg)
        self.assertIn("A&lt;B", svg)
        self.assertNotIn("A<B", svg.split(">", 1)[-1])  # text escaped after the root tag
        mermaid = graph.to_mermaid()
        self.assertIn("flowchart LR", mermaid)

    def test_session_replay_matches_a_second_build(self):
        state = {
            "disease": "colitis",
            "compounds": ["berberine"],
            "genes": [],
            "herbs": ["黄连", "黄柏"],
            "executed": [{
                "tool": "tcm_kegg_disease_pathways",
                "status": "error",
                "result": {},
            }],
        }
        a = build_from_session(state)
        b = build_from_session(state)
        self.assertEqual(a.to_mermaid(), b.to_mermaid())
        self.assertEqual(a.step_status("tcm_kegg_disease_pathways"), "failed")
        self.assertIn("tcm_formula_analysis", [t["tool"] for t in a.pending_tasks()])

    def test_longer_herb_name_wins(self):
        rows = [
            {"chinese_name": "黄连"},
            {"chinese_name": "黄连花"},
            {"pinyin": "Huangqin"},
        ]
        self.assertEqual(herbs_in_text("看看黄连花", rows), ["黄连花"])
        self.assertEqual(set(herbs_in_text("黄连和huangqin", rows)),
                         {"黄连", "Huangqin"})

    def test_shipped_index_recognizes_huanglian_and_huangqin(self):
        from pathlib import Path
        import json
        path = Path(__file__).resolve().parents[1] / "data" / "herb_mol_index.json"
        if not path.is_file():
            self.skipTest("herb index is not in the tree")
        rows = list(json.loads(path.read_text(encoding="utf-8"))["herbs"].values())
        found = herbs_in_text("比较黄连与黄芩", rows)
        self.assertIn("黄连", found)
        self.assertIn("黄芩", found)

    def test_cards_have_detail_arrows_motion_and_status_styles(self):
        graph = AnalysisGraph()
        graph.upsert("disease", "colitis", "ready", detail="queued")
        graph.upsert("gene", "TNF", "done", detail="hub marker")
        graph.upsert("compound", "berberine", "failed", detail="channel down")
        graph.upsert("herb", "黄连", "pending", detail="waiting")
        graph.upsert("pathway", "NOD", "skipped", detail="not run")
        graph.upsert("formula", "成分集合", "deferred", detail="later")
        graph.link("disease", "colitis", "gene", "TNF", "associated_with", "done")
        graph.link("gene", "TNF", "compound", "berberine", "recorded_in", "failed")
        graph.link("herb", "黄连", "formula", "成分集合", "contains", "pending")
        graph.link("pathway", "NOD", "gene", "TNF", "member_of", "skipped")
        graph.link("disease", "colitis", "pathway", "NOD", "associated_with", "ready")
        graph.link("formula", "成分集合", "herb", "黄连", "contains", "deferred")
        svg = graph.to_svg()
        self.assertEqual(graph.view_mode(), "卡片")
        self.assertIn('data-mode="card"', svg)
        self.assertIn('rx="6"', svg)
        self.assertIn("hub marker", svg)
        self.assertIn("channel down", svg)
        self.assertIn("<marker", svg)
        self.assertIn("marker-end", svg)
        self.assertIn("<animate", svg)
        self.assertIn("stroke-dasharray", svg)
        self.assertNotIn("<circle", svg)
        colors = {}
        for status in ("ready", "pending", "done", "failed", "skipped", "deferred"):
            self.assertIn(f"status-{status}", svg)
            matched = re.search(
                rf'class="edge edge-{status}"[^>]*stroke="(#[0-9A-Fa-f]{{6}})"',
                svg,
            )
            self.assertIsNotNone(matched, status)
            colors[status] = matched.group(1).lower()
        self.assertEqual(len(set(colors.values())), 6)
        failed = re.search(r'<(?:line|path) class="edge edge-failed"[^>]*>', svg)
        pending = re.search(r'<(?:line|path) class="edge edge-pending"[^>]*>', svg)
        done = re.search(r'<(?:line|path) class="edge edge-done"[^>]*>', svg)
        skipped = re.search(r'<(?:line|path) class="edge edge-skipped"[^>]*>', svg)
        self.assertIsNotNone(failed)
        self.assertIsNotNone(pending)
        self.assertIsNotNone(done)
        self.assertIsNotNone(skipped)
        self.assertNotIn("stroke-dasharray", failed.group(0))
        self.assertIn("stroke-dasharray", pending.group(0))
        self.assertNotIn("stroke-dasharray", done.group(0))
        self.assertNotIn("stroke-dasharray", skipped.group(0))
        self.assertIn("edge-flow", svg)
        page = graph.to_html(svg)
        self.assertIn("<svg", page)
        self.assertIn("卡片", page)
        self.assertIn("hub marker", page)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("cdn", page.lower())
        self.assertNotIn("three.js", page.lower())

    def test_two_hundred_ninety_nine_entities_stay_cards(self):
        self.assertEqual(SPHERE_ENTITY_THRESHOLD, 300)
        graph = AnalysisGraph()
        _put_nodes(graph, "gene", 200)
        _put_nodes(graph, "compound", 99)
        _put_nodes(graph, "step", 5, label="STEP-KEEP")
        self.assertEqual(len(graph.entity_nodes()), 299)
        self.assertEqual(graph.view_mode(), "卡片")
        svg = graph.to_svg()
        self.assertIn('data-mode="card"', svg)
        self.assertIn("卡片", svg)
        self.assertNotIn("球面", svg)
        self.assertNotIn("sphere-group", svg)
        self.assertIn('rx="6"', svg)
        self.assertIn("STEP-KEEP", svg)

    def test_three_hundred_entities_group_onto_spheres(self):
        graph = AnalysisGraph()
        _put_nodes(graph, "gene", 200)
        _put_nodes(graph, "compound", 100)
        graph.nodes["gene:0000"]["label"] = "A<B"
        graph.nodes["step:kept"] = {
            "id": "step:kept", "kind": "step", "key": "kept",
            "label": "STEP-KEEP", "status": "done", "detail": "bar",
            "source": "", "queried_at": "",
        }
        graph.edges.extend([
            {"id": "g2c", "src": "gene:0001", "dst": "compound:0001",
             "relation": "targets", "status": "done", "detail": ""},
            {"id": "c2g", "src": "compound:0002", "dst": "gene:0002",
             "relation": "found_in", "status": "pending", "detail": ""},
            {"id": "same", "src": "gene:0003", "dst": "gene:0004",
             "relation": "interacts_with", "status": "done", "detail": ""},
        ])
        self.assertEqual(len(graph.entity_nodes()), 300)
        self.assertEqual(graph.view_mode(), "球面")
        arcs = cross_kind_arcs(graph.entity_nodes(), graph.edges)
        self.assertEqual(
            [(item["src_kind"], item["dst_kind"], item["relation"]) for item in arcs],
            [("gene", "compound", "targets"), ("compound", "gene", "found_in")],
        )
        scene = sphere_scene(graph.entity_nodes())
        again = sphere_scene(list(reversed(graph.entity_nodes())))
        self.assertEqual(scene["points"], again["points"])
        self.assertEqual(fibonacci_sphere(300), fibonacci_sphere(300))
        sample = fibonacci_sphere(30)[10]
        self.assertAlmostEqual(sample[0] ** 2 + sample[1] ** 2 + sample[2] ** 2, 1.0, places=5)
        gene_centers = {(item["cx"], item["cy"]) for item in scene["points"].values()
                        if item["kind"] == "gene"}
        compound_centers = {(item["cx"], item["cy"]) for item in scene["points"].values()
                            if item["kind"] == "compound"}
        self.assertEqual(len(gene_centers), 1)
        self.assertEqual(len(compound_centers), 1)
        self.assertNotEqual(gene_centers, compound_centers)
        svg = graph.to_svg()
        self.assertEqual(svg, graph.to_svg())
        self.assertIn('data-mode="sphere"', svg)
        self.assertIn("<animate", svg)
        self.assertIn("marker-end", svg)
        self.assertIn("A&lt;B", svg)
        self.assertNotIn("A<B", svg.split(">", 1)[-1])
        groups = dict(re.findall(
            r'<g class="sphere-group" data-kind="([^"]+)"[^>]*>(.*?)</g>',
            svg, flags=re.S))
        self.assertEqual(set(groups), {"gene", "compound"})
        gene_titles = re.findall(r"<title>(.*?)</title>", groups["gene"])
        compound_titles = re.findall(r"<title>(.*?)</title>", groups["compound"])
        self.assertTrue(any("A&lt;B" in title for title in gene_titles))
        self.assertTrue(all("compound-" not in title for title in gene_titles))
        self.assertTrue(all(title.startswith("compound-") for title in compound_titles))
        self.assertNotIn("STEP-KEEP", groups["gene"])
        self.assertNotIn("STEP-KEEP", groups["compound"])
        self.assertIn("STEP-KEEP", svg)
        self.assertIn("执行状态", svg)
        self._assert_arc_runs_from_shell(svg, scene, "gene", "compound")
        self._assert_arc_runs_from_shell(svg, scene, "compound", "gene")
        page = graph.to_html(svg)
        self.assertIn("球面", page)
        self.assertIn("<svg", page)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("cdn", page.lower())

    def _assert_arc_runs_from_shell(self, svg, scene, src_kind, dst_kind):
        matched = re.search(
            rf'data-from="{src_kind}" data-to="{dst_kind}"[^>]* '
            rf'd="M ([0-9.]+) ([0-9.]+) Q [0-9.\-]+ [0-9.\-]+ ([0-9.]+) ([0-9.]+)"',
            svg,
        )
        self.assertIsNotNone(matched, f"{src_kind}->{dst_kind}")
        tag = re.search(
            rf'<path class="edge edge-[a-z]+" data-from="{src_kind}" '
            rf'data-to="{dst_kind}"[^>]*>',
            svg,
        )
        self.assertIsNotNone(tag)
        self.assertIn("marker-end", tag.group(0))
        x1, y1, x2, y2 = (float(item) for item in matched.groups())
        src = scene["centers"][src_kind]
        dst = scene["centers"][dst_kind]
        self.assertAlmostEqual(math.hypot(x1 - src[0], y1 - src[1]), src[2] + 2, delta=1.5)
        self.assertAlmostEqual(math.hypot(x2 - dst[0], y2 - dst[1]), dst[2] + 2, delta=1.5)

    def test_warm_palette_locked_in_svg_and_html(self):
        graph = AnalysisGraph()
        graph.upsert("disease", "colitis", "ready", detail="queued")
        graph.upsert("gene", "TNF", "done", detail="hub marker")
        graph.upsert("compound", "berberine", "failed", detail="channel down")
        graph.upsert("herb", "黄连", "pending", detail="waiting")
        graph.upsert("pathway", "NOD", "skipped", detail="not run")
        graph.upsert("formula", "成分集合", "deferred", detail="later")
        graph.link("disease", "colitis", "gene", "TNF", "associated_with", "done")
        graph.link("gene", "TNF", "compound", "berberine", "recorded_in", "failed")
        graph.link("herb", "黄连", "formula", "成分集合", "contains", "pending")
        graph.link("pathway", "NOD", "gene", "TNF", "member_of", "skipped")
        graph.link("disease", "colitis", "pathway", "NOD", "associated_with", "ready")
        graph.link("formula", "成分集合", "herb", "黄连", "contains", "deferred")
        svg = graph.to_svg()
        page = graph.to_html(svg)
        for color in (
            PAPER_BG, CARD_PAPER, INK, WOOD, WOOD_DARK,
            STATUS_READY, STATUS_PENDING, STATUS_DONE,
            STATUS_FAILED, STATUS_SKIPPED, STATUS_DEFERRED,
        ):
            self.assertIn(color, svg)
            self.assertIn(color, page)
        self.assertNotIn("#121418", svg)
        self.assertNotIn("#8eb4d0", svg)
        for phrase in (LIT_NOTE, FORMULA_NOTE, TRIAL_NOTE, SCREEN_NOTE, DOCK_NOTE):
            self.assertIn(phrase, svg)
            self.assertIn(phrase, page)
        self.assertIn('class="reading-legend"', svg)
        self.assertIn('class="reading-pathway"', page)
        self.assertIn("状态不只靠颜色", page)
        self.assertIn("Microsoft YaHei, sans-serif", svg)
        self.assertIn("Microsoft YaHei", page)
        self.assertIn("hub marker", svg)
        self.assertNotIn("<script", page.lower())
        self.assertNotIn("cdn", page.lower())
        self.assertNotIn("three.js", page.lower())
        ready = re.search(r'<path class="edge edge-ready"[^>]*>', svg)
        deferred = re.search(r'<path class="edge edge-deferred"[^>]*>', svg)
        failed = re.search(r'<path class="edge edge-failed"[^>]*>', svg)
        pending = re.search(r'<path class="edge edge-pending"[^>]*>', svg)
        done = re.search(r'<path class="edge edge-done"[^>]*>', svg)
        skipped = re.search(r'<path class="edge edge-skipped"[^>]*>', svg)
        self.assertIsNotNone(ready)
        self.assertIsNotNone(deferred)
        self.assertIn('stroke-dasharray="9 5"', ready.group(0))
        self.assertIn('stroke-dasharray="2 7"', deferred.group(0))
        self.assertNotIn("stroke-dasharray", failed.group(0))
        self.assertIn("stroke-dasharray", pending.group(0))
        self.assertNotIn("stroke-dasharray", done.group(0))
        self.assertNotIn("stroke-dasharray", skipped.group(0))
        self.assertIn("edge-flow", svg)
        self.assertIn("marker-end", svg)

    def test_reading_block_tracks_chain_and_gaps(self):
        empty = AnalysisGraph()
        empty_path = "\n".join(empty.pathway_lines())
        empty_next = "\n".join(empty.next_step_lines())
        chain = "疾病（缺） → 通路（缺） → 基因（缺） → 成分（缺） → 药材（缺）"
        self.assertIn(chain, empty_path)
        self.assertIn(chain, empty.to_svg())
        self.assertIn(chain, empty.to_html())
        self.assertIn("文献：缺失", empty_path)
        self.assertIn("对接：缺失。文献未写明口袋", empty_path)
        self.assertIn("药材少于 2 个，不算集合重叠", empty_path)
        self.assertIn("tcm_kegg_disease_pathways", empty_next)
        self.assertIn("tcm_tcmsp_search", empty_next)
        self.assertNotIn("tcm_formula_analysis", empty_next)
        self.assertNotIn("dock_", empty_next)
        self.assertIn("不会自动执行", empty_next)
        self.assertIn("图例", "\n".join(empty.summary_lines()))

        one = AnalysisGraph()
        one.note_intent(herbs=["黄连"])
        one.seed_sources()
        one.upsert("source", "herb_index", "failed")
        one_text = "\n".join(one.reading_lines())
        self.assertIn("药材（有节点，缺与成分的边）", one_text)
        self.assertIn("tcm_tcmsp_search", one_text)
        self.assertNotIn("tcm_formula_analysis", one_text)
        self.assertIn("药材少于 2 个，不算集合重叠", one_text)
        self.assertIn("失败", one_text)

        two = AnalysisGraph()
        two.note_intent(herbs=["黄连", "黄芩"])
        self.assertIn("尚未算出重叠", "\n".join(two.pathway_lines()))
        self.assertIn("tcm_formula_analysis", "\n".join(two.next_step_lines()))
        two.observe({
            "tool": "tcm_formula_analysis",
            "status": "ok",
            "result": {
                "herbs_matched": {"黄连": {}, "黄芩": {}},
                "pairwise_ingredients": [{
                    "pair": "黄连 + 黄芩", "jaccard": 0.02,
                }],
            },
        })
        self.assertIn("已写入", "\n".join(two.pathway_lines()))
        self.assertNotIn("尚未算出重叠", "\n".join(two.pathway_lines()))
        self.assertNotIn("tcm_formula_analysis", "\n".join(two.next_step_lines()))

        linked = AnalysisGraph()
        linked.note_intent(disease="colitis")
        linked.upsert("pathway", "hsa04621", "done", label="NOD")
        linked.upsert("gene", "TNF", "done")
        linked.link("disease", "colitis", "pathway", "hsa04621",
                    "associated_with", "done")
        linked.link("pathway", "hsa04621", "gene", "TNF", "member_of", "done")
        linked_path = "\n".join(linked.pathway_lines())
        self.assertIn("通路（已连接，完成）", linked_path)
        self.assertIn("基因（已连接，完成）", linked_path)
        self.assertIn("成分（缺）", linked_path)
        self.assertNotIn("疾病（缺）", linked_path)
        self.assertNotEqual(empty_path, linked_path)

    def test_failed_source_stays_in_the_reading_block(self):
        graph = AnalysisGraph()
        graph.seed_sources()
        graph.upsert("source", "chembl", "failed")
        text = "\n".join(graph.source_status_lines())
        self.assertIn("失败：ChEMBL", text)
        self.assertIn("ChEMBL（失败）", text)
        self.assertIn("缺失：无", text)
        self.assertIn("失败：ChEMBL", "\n".join(graph.summary_lines()))
        svg = graph.to_svg()
        page = graph.to_html(svg)
        self.assertIn("失败：ChEMBL", svg)
        self.assertIn("失败：ChEMBL", page)
        self.assertIn("tcm_cross_database_screen", "\n".join(graph.next_step_lines()))

    def test_docking_hint_requires_a_literature_pocket(self):
        graph = AnalysisGraph()
        graph.note_intent(disease="colitis", compounds=["berberine"])
        graph.upsert("literature", "PubMed", "done",
                     detail=f"full_triple=1; {LIT_NOTE}")
        self.assertNotIn("dock_", "\n".join(graph.next_step_lines()))
        self.assertIn("文献未写明口袋", "\n".join(graph.pathway_lines()))
        graph.upsert("literature", "PubMed", "done", detail="文献口袋残基 I46,I52")
        hinted = "\n".join(graph.reading_lines())
        self.assertIn("dock_vina_docking", hinted)
        self.assertIn(DOCK_NOTE, hinted)
        self.assertIn("文献写明口袋", hinted)

    def test_reading_markup_escapes_labels(self):
        graph = AnalysisGraph()
        graph.seed_sources()
        graph.upsert("source", "chembl", "failed", label="ChEMBL<x>")
        graph.note_intent(disease="A<B")
        svg = graph.to_svg()
        page = graph.to_html(svg)
        self.assertIn("ChEMBL&lt;x&gt;", svg)
        self.assertIn("A&lt;B", svg)
        self.assertIn("ChEMBL&lt;x&gt;", page)
        self.assertNotIn("ChEMBL<x>", svg.split(">", 1)[-1])
        self.assertNotIn("ChEMBL<x>", page)
        self.assertNotIn("A<B", svg.split(">", 1)[-1])

    def test_sphere_shell_uses_warm_amber(self):
        self.assertEqual(SPHERE_ENTITY_THRESHOLD, 300)
        graph = AnalysisGraph()
        _put_nodes(graph, "gene", 300)
        self.assertEqual(len(graph.entity_nodes()), 300)
        self.assertEqual(graph.view_mode(), "球面")
        svg = graph.to_svg()
        page = graph.to_html(svg)
        self.assertIn(SHELL_AMBER, svg)
        self.assertIn(PAPER_BG, svg)
        self.assertIn('data-mode="sphere"', svg)
        self.assertNotIn("#121418", svg)
        self.assertNotIn("#8eb4d0", svg)
        self.assertIn("图例", page)
        self.assertIn("检索通路", page)
        self.assertNotIn("<script", page.lower())

    def test_summary_text_is_what_topology_and_smart_run_emit(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        topo = (root / "mcp_server" / "topology_layer.py").read_text(encoding="utf-8")
        smart = (root / "mcp_server" / "smart_layer.py").read_text(encoding="utf-8")
        self.assertIn("summary_lines()", topo)
        self.assertIn("summary_lines()", smart)
        text = "\n".join(AnalysisGraph().summary_lines())
        for heading in ("图例", "检索通路", "下一步建议", "数据源状态"):
            self.assertIn(heading, text)


def _put_nodes(graph, kind, count, label=None, status="done"):
    for index in range(count):
        nid = f"{kind}:{index:04d}"
        graph.nodes[nid] = {
            "id": nid,
            "kind": kind,
            "key": f"{kind}-{index}",
            "label": label or f"{kind}-{index:04d}",
            "status": status,
            "detail": "row",
            "source": "",
            "queried_at": "",
        }


if __name__ == "__main__":
    unittest.main()
