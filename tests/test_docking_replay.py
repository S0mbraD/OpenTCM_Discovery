"""Offline checks for literature-pocket docking replay entries."""
import json
import socket
import unittest
from pathlib import Path

from tcm_core.docking_replay import (
    check_docking_replay,
    load_docking_replay_fixture,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "docking_replay" / "entries.json"


class DockingReplayTests(unittest.TestCase):
    def test_fixture_rejects_the_row_without_a_pocket(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["entries"]), 3)
        self.assertIs(raw["verified_public_complexes"], False)
        out = load_docking_replay_fixture(FIXTURE)
        self.assertIs(out["verified_public_complexes"], False)
        self.assertIn("还没有放入已核对的公开复合物", out["note"])
        self.assertIn("还没有放入已核对的公开复合物", out["statement"])
        self.assertFalse(out["docking_executed"])
        self.assertFalse(out["poses_generated"])
        self.assertFalse(out["blind_docking"])
        self.assertFalse(out["experimental_structure"])
        self.assertFalse(out["experimental_affinity_reproduced"])
        self.assertFalse(out["literature_text_checked"])
        self.assertEqual(out["n_ready"], 2)
        self.assertEqual(out["n_rejected"], 1)
        missing = _by_case(out, "test-missing-pocket")
        self.assertFalse(missing["eligible"])
        self.assertEqual(missing["status"], "拒绝")
        self.assertIn("缺口袋残基", missing["reason"])
        self.assertNotIn("可以进入对接复现", missing["status"])
        self.assertNotIn("可以进入对接复现", missing["statement"])
        self.assertIsNone(missing["pocket_residues"])
        self.assertFalse(missing["poses_generated"])
        self.assertNotIn("output_kind", missing)

    def test_missing_literature_id_is_rejected(self):
        out = check_docking_replay([
            {
                "case_id": "test-missing-literature",
                "label": "测试用例",
                "complex_name": "测试用例-缺文献",
                "pocket_residues": "101,102",
                "ligand_id": "测试配体",
            }
        ])
        row = out["entries"][0]
        self.assertFalse(row["eligible"])
        self.assertIn("缺文献标识", row["reason"])
        self.assertNotIn("可以进入对接复现", row["statement"])
        self.assertIsNone(row["pocket_residues"])
        self.assertFalse(row["docking_executed"])
        self.assertNotIn("verified_public_complexes", out)

    def test_title_is_not_a_literature_id(self):
        out = check_docking_replay([
            {
                "complex_name": "测试用例-文章标题",
                "literature_id": "某篇关于口袋的论文",
                "pocket_residues": "101,102",
                "ligand_id": "测试配体",
            }
        ])
        row = out["entries"][0]
        self.assertFalse(row["eligible"])
        self.assertIn("文献标识不是 PMID 或 DOI", row["reason"])
        self.assertIsNone(row["literature_id"])

    def test_ready_rows_are_proposed_models_only(self):
        out = load_docking_replay_fixture(FIXTURE)
        ready = [row for row in out["entries"] if row["eligible"]]
        self.assertEqual([row["case_id"] for row in ready], ["test-ready-a", "test-ready-b"])
        self.assertEqual(ready[0]["pocket_residues"], "101,102")
        self.assertEqual(ready[0]["literature_id"], "PMID:00000000")
        self.assertIsNone(ready[0]["pdb_id"])
        self.assertEqual(ready[1]["pocket_residues"], "201,202,203")
        self.assertEqual(ready[1]["literature_id"], "10.0000/docking-replay-test-case")
        self.assertEqual(ready[1]["name"], "测试用例-靶点乙")
        for row in ready:
            self.assertEqual(row["status"], "可以进入对接复现")
            self.assertIn("提议的对接模型", row["statement"])
            self.assertEqual(row["output_kind"], "提议的对接模型")
            self.assertIn("测试用例", row["name"])
            self.assertFalse(row["poses_generated"])
            self.assertFalse(row["docking_executed"])
            self.assertFalse(row["literature_text_checked"])
        text = json.dumps(out, ensure_ascii=False)
        self.assertNotIn("kcal", text.lower())
        self.assertNotIn("亲和能", text)
        self.assertFalse(out["experimental_affinity_reproduced"])
        self.assertIn("不做全蛋白盲对接", out["statement"])
        self.assertIn("没有复现实验亲和力", out["statement"])

    def test_non_numeric_residues_are_not_filled_in(self):
        out = check_docking_replay([
            {
                "complex_name": "测试用例-文字口袋",
                "literature_id": "PMID:00000000",
                "pocket_residues": "活性口袋",
                "ligand_id": "测试配体",
            },
            {
                "complex_name": "测试用例-残基名",
                "literature_id": "PMID:00000000",
                "pocket_residues": "His101",
                "ligand_id": "测试配体",
            },
            {
                "complex_name": "测试用例-区间",
                "literature_id": "PMID:00000000",
                "pocket_residues": "101-103",
                "ligand_id": "测试配体",
            },
        ])
        self.assertEqual(out["n_ready"], 0)
        for row in out["entries"]:
            self.assertFalse(row["eligible"])
            self.assertIn("残基不是编号", row["reason"])
            self.assertIsNone(row["pocket_residues"])
            self.assertNotIn("可以进入对接复现", row["statement"])
            dumped = json.dumps(row, ensure_ascii=False)
            self.assertNotIn("102", dumped)
            self.assertNotIn("103", dumped)
        self.assertNotIn("101", json.dumps(out["entries"][1], ensure_ascii=False))
        self.assertNotIn("101", json.dumps(out["entries"][2], ensure_ascii=False))

    def test_chain_prefix_keeps_only_the_given_numbers(self):
        out = check_docking_replay([
            {
                "target_name": "测试用例-链前缀",
                "literature_id": "PMID:00000000",
                "pocket_residues": "A101,B102",
                "ligand_id": "测试配体",
            }
        ])
        row = out["entries"][0]
        self.assertTrue(row["eligible"])
        self.assertEqual(row["status"], "可以进入对接复现")
        self.assertEqual(row["pocket_residues"], "101,102")
        self.assertIn("提议的对接模型", row["statement"])
        self.assertNotIn("103", row["pocket_residues"])

    def test_checker_does_not_use_the_network(self):
        source = (ROOT / "tcm_core" / "docking_replay.py").read_text(encoding="utf-8")
        for banned in (
            "urllib",
            "httpx",
            "requests",
            "subprocess",
            "socket",
            "vina",
            "exhaustiveness",
            "urlopen",
        ):
            self.assertNotIn(banned, source.lower())

        def boom(*_args, **_kwargs):
            raise AssertionError("network")

        originals = (socket.socket, socket.create_connection)
        socket.socket = boom
        socket.create_connection = boom
        try:
            out = load_docking_replay_fixture(FIXTURE)
        finally:
            socket.socket, socket.create_connection = originals
        self.assertEqual(out["n_entries"], 3)
        self.assertFalse(out["docking_executed"])


def _by_case(result: dict, case_id: str) -> dict:
    for row in result["entries"]:
        if row.get("case_id") == case_id:
            return row
    raise AssertionError(case_id)


if __name__ == "__main__":
    unittest.main()
