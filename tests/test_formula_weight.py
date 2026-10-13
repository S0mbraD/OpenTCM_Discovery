import ast
import unittest
from pathlib import Path
from unittest.mock import patch

from tcm_core.formula_weight import formula_weight
from tcm_core.prep import PrepError, convert_doses


class FormulaWeightTests(unittest.TestCase):
    def test_missing_convention(self):
        row = {"herb": "桂枝", "dose": "1两"}
        for convention in (None, "", "ancient"):
            with self.subTest(convention=convention):
                with patch("tcm_core.formula_weight.convert_doses") as mocked:
                    with self.assertRaises(PrepError):
                        formula_weight([row], convention)
                mocked.assert_not_called()
        with patch("tcm_core.formula_weight.convert_doses") as mocked:
            with self.assertRaises(PrepError):
                formula_weight([row])
        mocked.assert_not_called()

    def test_han_tang_one_liang_is_13_8_grams(self):
        out = formula_weight([{"herb": "桂枝", "dose": "1两"}], "han_tang")
        direct = convert_doses("1两", "han_tang")
        self.assertEqual(direct["rows"][0]["grams"], "13.8")
        self.assertEqual(out["rows"][0]["grams"], "13.8")
        self.assertEqual(out["total_grams"], "13.8")
        self.assertEqual(out["rows"][0]["weight_ratio"], "1")
        self.assertEqual(out["convention"], "han_tang")
        self.assertEqual(out["label"], direct["label"])
        self.assertEqual(out["citation"], direct["citation"])
        self.assertIsNone(out["rows"][0]["processing"])

    def test_two_rows_total_and_share_of_total(self):
        out = formula_weight(
            [
                {"herb": "桂枝", "dose": "1两"},
                {"herb": "芍药", "dose": "3两"},
            ],
            "han_tang",
        )
        self.assertEqual([row["grams"] for row in out["rows"]], ["13.8", "41.4"])
        self.assertEqual(out["total_grams"], "55.2")
        self.assertEqual(
            [row["weight_ratio"] for row in out["rows"]],
            ["0.25", "0.75"],
        )
        self.assertEqual(out["rows"][0]["herb"], "桂枝")
        self.assertEqual(out["rows"][1]["herb"], "芍药")

    def test_processing_kept_and_missing_stays_missing(self):
        out = formula_weight(
            [
                {"herb": "麻黄", "dose": "1两", "processing": "生"},
                {"药材": "甘草", "剂量": "1两", "炮制": "炙"},
                {"herb": "桂枝", "dose": "1两", "processing": "酒炙"},
                {"药材名": "芍药", "剂量原文": "1两"},
            ],
            "han_tang",
        )
        self.assertEqual(
            [row["processing"] for row in out["rows"]],
            ["生", "炙", "酒炙", None],
        )
        self.assertEqual([row["grams"] for row in out["rows"]], ["13.8"] * 4)
        self.assertEqual(
            out["rows"][1]["grams"],
            convert_doses("1两", "han_tang")["rows"][0]["grams"],
        )
        missing_line = [
            line for line in out["text"].splitlines() if line.startswith("芍药")
        ][0]
        self.assertNotIn("生", missing_line)
        self.assertNotIn("炮制", missing_line)
        for row in out["rows"]:
            self.assertNotIn("efficacy", row)
            self.assertNotIn("role", row)

    def test_note_says_weight_ratio_is_not_efficacy(self):
        out = formula_weight(
            [{"herb": "桂枝", "dose": "1两", "processing": "生"}],
            "han_tang",
        )
        sentence = "重量比不是疗效，不是暴露量，不是君臣佐使。"
        self.assertIn(sentence, out["note"])
        self.assertIn(sentence, out["text"])
        self.assertIn("重量比不是疗效", out["text"])
        self.assertEqual(out["rows"][0]["processing"], "生")

    def test_empty_formula(self):
        with patch("tcm_core.formula_weight.convert_doses") as mocked:
            with self.assertRaises(PrepError) as ctx:
                formula_weight([], "han_tang")
        self.assertIn("空", str(ctx.exception))
        mocked.assert_not_called()
        with self.assertRaises(PrepError):
            formula_weight(None, "metric")

    def test_unparseable_row(self):
        with self.assertRaises(PrepError):
            formula_weight([{"herb": "桂枝", "dose": "半两"}], "han_tang")
        with self.assertRaises(PrepError):
            formula_weight(
                [
                    {"herb": "桂枝", "dose": "1两"},
                    {"herb": "甘草", "dose": "不清楚"},
                ],
                "han_tang",
            )
        with self.assertRaises(PrepError) as ctx:
            formula_weight(
                [{"herb": "桂枝", "dose": "1两；2钱"}],
                "song_yuan_2025",
            )
        self.assertIn("一个剂量", str(ctx.exception))

    def test_mixed_unit_systems_do_not_guess_grams(self):
        samples = (
            [
                {"herb": "桂枝", "dose": "1两"},
                {"herb": "甘草", "dose": "10g"},
            ],
            [{"herb": "桂枝", "dose": "1两10g"}],
        )
        for rows in samples:
            with self.subTest(rows=rows):
                with patch("tcm_core.formula_weight.convert_doses") as mocked:
                    with self.assertRaises(PrepError) as ctx:
                        formula_weight(rows, "han_tang")
                self.assertIn("混用", str(ctx.exception))
                mocked.assert_not_called()

    def test_conventions_are_not_one_liang(self):
        cases = (
            ("han_tang", "1两", "13.8"),
            ("market_1959", "1两", "50"),
            ("song_jin_yuan_2024", "1钱", "4.13"),
            ("ming_qing_2024", "1钱", "3.73"),
            ("song_yuan_2025", "1钱", "4"),
            ("ming_qing_2025", "1钱", "3.7"),
            ("metric", "1g", "1"),
        )
        grams = []
        for convention, dose, expected in cases:
            with self.subTest(convention=convention):
                out = formula_weight([{"herb": "甘草", "dose": dose}], convention)
                direct = convert_doses(dose, convention)
                self.assertEqual(out["rows"][0]["grams"], expected)
                self.assertEqual(out["rows"][0]["grams"], direct["rows"][0]["grams"])
                self.assertEqual(out["citation"], direct["citation"])
                self.assertEqual(out["convention"], convention)
                grams.append(out["rows"][0]["grams"])
        self.assertEqual(len(set(grams)), len(cases))

    def test_amount_plus_unit_and_compound_dose(self):
        by_amount = formula_weight(
            [{"herb": "桂枝", "amount": 1, "unit": "两"}],
            "han_tang",
        )
        by_number = formula_weight(
            [{"herb": "桂枝", "dose": 1, "unit": "两"}],
            "han_tang",
        )
        self.assertEqual(by_amount["rows"][0]["grams"], "13.8")
        self.assertEqual(by_number["rows"][0]["dose"], "1两")
        self.assertEqual(by_number["rows"][0]["grams"], "13.8")
        compound = formula_weight(
            [{"herb": "甘草", "dose": "1两2钱"}],
            "song_jin_yuan_2024",
        )
        direct = convert_doses("1两2钱", "song_jin_yuan_2024")
        self.assertEqual(len(compound["rows"]), 1)
        self.assertEqual(compound["rows"][0]["grams"], direct["rows"][0]["grams"])
        self.assertEqual(compound["rows"][0]["grams"], "49.56")

    def test_grams_come_from_convert_doses(self):
        calls = []

        def fake(text, convention):
            calls.append((text, convention))
            return {
                "convention": convention,
                "label": "桩",
                "citation": "桩出处",
                "rows": [{"input": text, "grams": "7", "ratio_to_min": "99"}],
                "note": "桩",
            }

        with patch("tcm_core.formula_weight.convert_doses", fake):
            out = formula_weight(
                [
                    {"herb": "桂枝", "dose": "1两"},
                    {"herb": "芍药", "dose": "1两"},
                ],
                "han_tang",
            )
        self.assertEqual(calls, [("1两", "han_tang"), ("1两", "han_tang")])
        self.assertEqual([row["grams"] for row in out["rows"]], ["7", "7"])
        self.assertEqual(out["total_grams"], "14")
        self.assertEqual(
            [row["weight_ratio"] for row in out["rows"]],
            ["0.5", "0.5"],
        )
        self.assertEqual(out["label"], "桩")
        self.assertEqual(out["citation"], "桩出处")
        self.assertNotEqual(out["rows"][0]["weight_ratio"], "99")

    def test_library_does_not_copy_grams_or_open_network(self):
        import tcm_core.formula_weight as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                roots.add(node.module.split(".")[0])
        self.assertTrue(roots <= {"__future__", "re", "decimal", "tcm_core"})
        for banned in (
            "13.8",
            "4.13",
            "3.73",
            "41.3",
            "37.3",
            "urllib",
            "requests",
            "httpx",
            "socket",
            "herb_index",
            "herb_mol",
            "liang_g",
        ):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
