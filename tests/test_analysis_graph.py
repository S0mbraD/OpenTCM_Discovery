"""The analysis network grows from retrieval results and redraws itself."""

import unittest

from tcm_core.analysis_graph import (
    FORMULA_NOTE,
    LIT_NOTE,
    SCREEN_NOTE,
    AnalysisGraph,
    build_from_session,
    herbs_in_text,
    layered_positions,
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


if __name__ == "__main__":
    unittest.main()
