import unittest
from pathlib import Path

from tcm_core.paths import herb_index_candidates
from tcm_core.text import extract_known_compounds, normalize_pocket_residues


class TextTests(unittest.TestCase):
    def test_extracts_compound_from_chinese_goal(self):
        goal = "研究溃疡性结肠炎的候选成分 berberine 与黄芩苷 baicalin"
        self.assertEqual(
            extract_known_compounds(goal),
            ["berberine", "baicalin"],
        )

    def test_longer_name_does_not_drop_shorter_overlap(self):
        found = extract_known_compounds("epigallocatechin and catechin")
        self.assertIn("catechin", found)
        self.assertIn("epigallocatechin", found)

    def test_pocket_accepts_chain_prefix_and_plain_numbers(self):
        self.assertEqual(normalize_pocket_residues("I46,I52;53"), "46,52,53")
        self.assertEqual(normalize_pocket_residues("46,52"), "46,52")

    def test_placeholder_without_digits_is_preserved(self):
        raw = "REPLACE_WITH_LITERATURE_RESIDUE_NUMBERS"
        self.assertEqual(normalize_pocket_residues(raw), raw)


class PathTests(unittest.TestCase):
    def test_env_overrides_shipped_index(self):
        paths = herb_index_candidates(Path("/repo"), env_value="D:/custom.json")
        self.assertEqual(paths[0], Path("D:/custom.json"))
        self.assertEqual(paths[1], Path("/repo/data/herb_mol_index.json"))

    def test_default_is_repository_data(self):
        paths = herb_index_candidates(Path("/repo"), env_value="")
        self.assertEqual(paths, [Path("/repo/data/herb_mol_index.json")])


if __name__ == "__main__":
    unittest.main()
