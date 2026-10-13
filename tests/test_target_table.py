import json
import socket
import unittest
from pathlib import Path

from tcm_core.target_table import TargetTableError, read_target_table

ROOT = Path(__file__).resolve().parent.parent

TABLE = """\
compound_id,gene,support,source,ob
berberine,TNF,recorded,HERB,0.123456
berberine,TNF,recorded,HIT,0.123456
berberine,TNF,predicted,BATMAN-TCM,0.123456
berberine,TNF,recorded,unknown,0.123456
berberine,TNF,recorded,HERB/ETCM,0.123456
berberine,IL6,known,HERB,0.123456
berberine,IL6,predicted,BATMAN-TCM,0.123456
,TNF,recorded,HERB,0.123456
"""


class TargetTableTests(unittest.TestCase):
    def test_recorded_and_predicted_stay_apart(self):
        out = read_target_table(TABLE)
        self.assertEqual(out["mode"], "local")
        self.assertNotIn("queried_at", out)
        self.assertEqual(out["n_recorded"], 2)
        self.assertEqual(out["n_predicted"], 2)
        self.assertGreater(out["n_unclassified"], 0)
        recorded = out["recorded_pairs"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["compound_id"], "berberine")
        self.assertEqual(recorded[0]["gene"], "TNF")
        self.assertEqual(recorded[0]["recorded_sources"], ["HERB", "HIT"])
        self.assertEqual(recorded[0]["recorded_source_count"], 2)
        self.assertNotIn("BATMAN-TCM", recorded[0]["recorded_sources"])
        predicted = {(item["gene"], tuple(item["predicted_sources"])) for item in out["predicted_pairs"]}
        self.assertEqual(predicted, {("TNF", ("BATMAN-TCM",)), ("IL6", ("BATMAN-TCM",))})
        dumped = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("0.123456", dumped)
        self.assertEqual(out["ignored_columns"], ["ob"])
        for row in out["rows"]:
            self.assertNotIn("ob", row)
        self.assertIn("不是疗效", out["note"])
        self.assertIn("预测靶点不是已记录证据", out["note"])
        reasons = {row["reason"] for row in out["rows"] if row["bucket"] == "unclassified"}
        self.assertIn("来源含糊", reasons)
        self.assertIn("support 不是 recorded 或 predicted", reasons)
        self.assertIn("缺少成分标识", reasons)

    def test_same_source_spelling_counts_once(self):
        text = (
            "compound_id,gene,support,source\n"
            "berberine,TNF,recorded,HERB\n"
            "berberine,TNF,Recorded,herb\n"
            "berberine,TNF,predicted,BATMAN-TCM\n"
        )
        out = read_target_table(text)
        pair = out["recorded_pairs"][0]
        self.assertEqual(pair["recorded_sources"], ["HERB"])
        self.assertEqual(pair["recorded_source_count"], 1)
        self.assertEqual(out["predicted_pairs"][0]["predicted_source_count"], 1)
        self.assertEqual(out["rows"][1]["support"], "recorded")
        self.assertEqual(out["rows"][2]["bucket"], "predicted")

    def test_ambiguous_source_is_not_counted_as_recorded(self):
        text = (
            "compound_id\tgene\tsupport\tsource\n"
            "berberine\tTNF\trecorded\t\n"
            "berberine\tTNF\trecorded\t文献\n"
            "berberine\tTNF\trecorded\tHERB;HIT\n"
            "berberine\tTNF\trecorded\tHERB和ETCM\n"
            "berberine\tIL6\t预测\tHERB\n"
            "berberine\tSTAT3\texperimental\tHIT\n"
            "quercetin\tTNF\trecorded\tdoi:10.1093/nar/gkad926\n"
        )
        out = read_target_table(text)
        self.assertEqual(out["recorded_pairs"], [{
            "compound_id": "quercetin",
            "gene": "TNF",
            "recorded_sources": ["doi:10.1093/nar/gkad926"],
            "recorded_source_count": 1,
        }])
        self.assertEqual(out["predicted_pairs"], [])
        self.assertEqual(out["n_recorded"], 1)
        self.assertEqual(out["n_unclassified"], 6)
        genes = {row["gene"] for row in out["rows"] if row["bucket"] == "recorded"}
        self.assertEqual(genes, {"TNF"})
        self.assertNotIn("HERB", json.dumps(out["recorded_pairs"]))

    def test_empty_table_and_missing_columns_raise(self):
        with self.assertRaises(TargetTableError) as blank:
            read_target_table("  \n")
        self.assertIn("空", str(blank.exception))
        with self.assertRaises(TargetTableError) as header:
            read_target_table("compound_id,gene,support,source\n")
        self.assertIn("空表", str(header.exception))
        with self.assertRaises(TargetTableError) as missing:
            read_target_table("compound_id,gene,source\nberberine,TNF,HERB\n")
        self.assertIn("support", str(missing.exception))
        self.assertIn("不会把缺的 support 当成 recorded", str(missing.exception))
        with self.assertRaises(TargetTableError):
            read_target_table("[]")
        with self.assertRaises(TargetTableError):
            read_target_table('{"rows": []}')
        with self.assertRaises(TargetTableError) as too_many:
            body = "compound_id,gene,support,source\n" + "\n".join(
                f"c,g,recorded,HERB" for _ in range(2001)
            )
            read_target_table(body)
        self.assertIn("2000", str(too_many.exception))

    def test_json_rows_ignore_extra_fields_and_keep_gene_text(self):
        payload = {
            "targets": [
                {"compound_id": "berberine", "gene": "TNF", "support": "recorded", "source": "NPASS"},
                {"compound_id": "berberine", "gene": "tnf", "support": "predicted", "source": "BATMAN-TCM", "score": 0.99},
            ]
        }
        out = read_target_table(json.dumps(payload))
        self.assertEqual(out["recorded_pairs"][0]["recorded_source_count"], 1)
        self.assertEqual(out["recorded_pairs"][0]["gene"], "TNF")
        self.assertEqual(out["predicted_pairs"][0]["gene"], "tnf")
        self.assertEqual(out["ignored_columns"], ["score"])
        self.assertNotIn("0.99", json.dumps(out))

    def test_does_not_touch_the_network(self):
        core = (ROOT / "tcm_core" / "target_table.py").read_text(encoding="utf-8")
        layer = (ROOT / "mcp_server" / "target_layer.py").read_text(encoding="utf-8")
        smart = (ROOT / "mcp_server" / "smart_layer.py").read_text(encoding="utf-8")
        unified = (ROOT / "mcp_server" / "unified_mcp.py").read_text(encoding="utf-8")
        self.assertNotIn("urlopen", core + layer)
        self.assertNotIn("httpx", core + layer)
        self.assertNotIn("open(", core)
        self.assertNotIn("herb_mol_index", core + layer)
        self.assertNotIn("tcm_read_target_table", smart)
        self.assertIn("target_layer.py", unified)
        fn_map = unified.split("_fn_map", 1)[1].split("attach_tools", 1)[0]
        self.assertNotIn("_target_mod", fn_map)

        def boom(*args, **kwargs):
            raise AssertionError("network")

        originals = (socket.socket, socket.create_connection)
        socket.socket = boom
        socket.create_connection = boom
        try:
            out = read_target_table(
                "compound_id,gene,support,source\nberberine,TNF,recorded,HERB\n"
            )
        finally:
            socket.socket, socket.create_connection = originals
        self.assertEqual(out["recorded_pairs"][0]["recorded_source_count"], 1)


if __name__ == "__main__":
    unittest.main()
