import json
import socket
import unittest
from pathlib import Path

from tcm_core.align_tcmsp import AlignError, align_tcmsp

ROOT = Path(__file__).resolve().parent.parent
KEY_A = "AAAAAAAAAAAAAA-BBBBBBBBSN-C"
KEY_A_STEREO = "AAAAAAAAAAAAAA-CCCCCCCCSN-D"
KEY_B = "BBBBBBBBBBBBBB-BBBBBBBBSN-C"
KEY_BAD = "AAAAAAAAAAAAAA-BBBBBBBBBN-C"


class AlignTests(unittest.TestCase):
    def test_key_fixtures_match_the_inchikey_rules(self):
        self.assertEqual(len(KEY_A), 27)
        self.assertEqual(KEY_A[23], "S")
        self.assertEqual(KEY_A_STEREO[:14], KEY_A[:14])
        self.assertNotEqual(KEY_A_STEREO, KEY_A)
        self.assertNotEqual(KEY_B[:14], KEY_A[:14])
        self.assertEqual(KEY_BAD[23], "B")

    def test_name_match_copies_catalog_key_and_keeps_low_ob_dl(self):
        tcmsp = (
            "molecule_name,MOL_ID,ob,dl\n"
            "Protocatechuic  Acid,MOL000105,25.3664679574,0.035092\n"
        )
        catalog = f"name,inchikey,local_id,gene\nprotocatechuic acid,{KEY_A},SMIT00001,TNF\n"
        out = align_tcmsp(tcmsp, catalog)
        self.assertEqual(out["n_matches"], 1)
        self.assertFalse(out["ob_dl_filter_applied"])
        self.assertNotIn("queried_at", out)
        hit = out["matches"][0]
        self.assertEqual(hit["link"], "name")
        self.assertFalse(hit["inchikey_compared"])
        self.assertIsNone(hit["tcmsp_inchikey"])
        self.assertEqual(hit["catalog_inchikey"], KEY_A)
        self.assertEqual(hit["local_id"], "SMIT00001")
        self.assertEqual(hit["ob"], "25.3664679574")
        self.assertEqual(hit["dl"], "0.035092")
        self.assertFalse(hit["tcmsp_ob_dl_pass"])
        self.assertNotIn("TNF", json.dumps(out, ensure_ascii=False))
        self.assertIn("不是疗效", out["note"])
        self.assertIn("不是靶点", out["note"])

    def test_boundary_ob_dl_is_reported_and_not_used_as_a_filter(self):
        tcmsp = "molecule_name,ob,dl\nboundary,30,0.18\nlow,29.9,0.20\n"
        catalog = "name\nboundary\nlow\n"
        out = align_tcmsp(tcmsp, catalog)
        self.assertEqual(out["n_matches"], 2)
        flags = {hit["tcmsp_name"]: hit["tcmsp_ob_dl_pass"] for hit in out["matches"]}
        self.assertEqual(flags, {"boundary": True, "low": False})

    def test_full_inchikey_wins_over_a_different_name(self):
        tcmsp = json.dumps({
            "ingredients": [{
                "molecule_name": "other name",
                "MOL_ID": "MOL000105",
                "molecule_ID": "105",
                "inchikey": KEY_A.lower(),
                "ob": "30",
                "dl": "0.18",
            }]
        })
        catalog = f"name,inchikey\nprotocatechuic acid,{KEY_A}\n"
        hit = align_tcmsp(tcmsp, catalog)["matches"][0]
        self.assertEqual(hit["link"], "inchikey")
        self.assertTrue(hit["inchikey_compared"])
        self.assertEqual(hit["catalog_name"], "protocatechuic acid")
        self.assertEqual(hit["tcmsp_mol_id"], "MOL000105")
        self.assertTrue(hit["tcmsp_ob_dl_pass"])

    def test_connectivity_block_is_not_full_identity(self):
        tcmsp = f"molecule_name,inchikey\nalpha,{KEY_A}\n"
        catalog = f"name,inchikey\nbeta,{KEY_A_STEREO}\n"
        hit = align_tcmsp(tcmsp, catalog)["matches"][0]
        self.assertEqual(hit["link"], "inchikey_block")
        self.assertNotEqual(hit["tcmsp_inchikey"], hit["catalog_inchikey"])
        self.assertTrue(hit["inchikey_compared"])

    def test_different_connectivity_is_not_joined_by_name(self):
        tcmsp = f"molecule_name,inchikey\nprotocatechuic acid,{KEY_A}\n"
        catalog = f"name,inchikey\nprotocatechuic acid,{KEY_B}\n"
        out = align_tcmsp(tcmsp, catalog)
        self.assertEqual(out["matches"], [])
        self.assertEqual(out["tcmsp_unmatched"][0]["reason"], "InChIKey 与目录不同")
        self.assertEqual(out["n_catalog_unmatched"], 1)

    def test_duplicate_names_stay_ambiguous(self):
        tcmsp = "molecule_name\nprotocatechuic acid\n"
        catalog = "name,inchikey\nprotocatechuic acid,\nprotocatechuic acid,\n"
        out = align_tcmsp(tcmsp, catalog)
        self.assertEqual(out["matches"], [])
        self.assertEqual(out["ambiguous"][0]["reason"], "名称对应多行")
        self.assertEqual(out["ambiguous"][0]["catalog_indexes"], [1, 2])
        self.assertEqual(out["catalog_unmatched"], [])

    def test_mol_id_wins_over_a_different_name(self):
        tcmsp = "molecule_name,MOL_ID\nalpha,mol000105\n"
        catalog = "name,mol_id\nbeta,MOL000105\nalpha,\n"
        hit = align_tcmsp(tcmsp, catalog)["matches"][0]
        self.assertEqual(hit["link"], "mol_id")
        self.assertEqual(hit["catalog_name"], "beta")
        self.assertEqual(hit["tcmsp_mol_id"], "MOL000105")

    def test_partial_name_and_chinese_alias_do_not_match(self):
        tcmsp = "molecule_name\nprotocatechuic acid\no-xylene\n"
        catalog = "成分名\nacid\noxylene\n原儿茶酸\n"
        out = align_tcmsp(tcmsp, catalog)
        self.assertEqual(out["matches"], [])
        self.assertEqual(out["n_tcmsp_unmatched"], 2)
        self.assertEqual(out["n_catalog_unmatched"], 3)

    def test_missing_ob_dl_pass_is_null_and_catalog_dl_is_ignored(self):
        tcmsp = "molecule_name,dl\nberberine,\n"
        catalog = "name,dl,inchikey\nberberine,0.99,\n"
        hit = align_tcmsp(tcmsp, catalog)["matches"][0]
        self.assertIsNone(hit["tcmsp_ob_dl_pass"])
        self.assertIsNone(hit["dl"])
        self.assertIsNone(hit["ob"])

    def test_invalid_inchikey_does_not_fall_through_to_the_name(self):
        tcmsp = f"molecule_name,inchikey\nprotocatechuic acid,{KEY_BAD}\n"
        catalog = f"name,inchikey\nprotocatechuic acid,{KEY_A}\n"
        out = align_tcmsp(tcmsp, catalog)
        self.assertEqual(out["matches"], [])
        self.assertEqual(out["rejected"][0]["side"], "tcmsp")
        self.assertIn("InChIKey", out["rejected"][0]["reason"])

    def test_search_json_uses_ingredients_not_herbs(self):
        payload = {
            "herbs": [{"herb_cn_name": "甘草"}],
            "ingredients": [{
                "molecule_name": "o-xylene",
                "MOL_ID": "MOL001097",
                "ob": "45.5470233875",
                "dl": "0.014755",
                "mw": "106.180",
            }],
        }
        catalog = "name\no-xylene\n"
        out = align_tcmsp(json.dumps(payload), catalog)
        self.assertEqual(out["tcmsp_rows"], 1)
        self.assertEqual(out["matches"][0]["link"], "name")
        self.assertFalse(out["matches"][0]["tcmsp_ob_dl_pass"])
        self.assertNotIn("甘草", json.dumps(out["matches"], ensure_ascii=False))

    def test_one_catalog_row_can_match_two_tcmsp_rows(self):
        tcmsp = "molecule_name\nprotocatechuic acid\nprotocatechuic acid\n"
        catalog = f"name,inchikey\nprotocatechuic acid,{KEY_A}\n"
        out = align_tcmsp(tcmsp, catalog)
        self.assertEqual(out["n_matches"], 2)
        self.assertEqual(out["catalog_unmatched"], [])

    def test_empty_and_schema_errors(self):
        with self.assertRaises(AlignError):
            align_tcmsp("  ", "name\na\n")
        with self.assertRaises(AlignError):
            align_tcmsp('{"herbs": []}', "name\na\n")
        with self.assertRaises(AlignError):
            align_tcmsp("{", "name\na\n")
        with self.assertRaises(AlignError):
            align_tcmsp("gene\nTNF\n", "name\na\n")
        with self.assertRaises(AlignError) as caught:
            align_tcmsp("name\n" + "\n".join("a" for _ in range(2001)), "name\nb\n")
        self.assertIn("2000", str(caught.exception))

    def test_does_not_touch_the_network_or_the_herb_index(self):
        core = (ROOT / "tcm_core" / "align_tcmsp.py").read_text(encoding="utf-8")
        layer = (ROOT / "mcp_server" / "align_layer.py").read_text(encoding="utf-8")
        self.assertNotIn("herb_index_candidates", core)
        self.assertNotIn("TCM_HERB_INDEX", core)
        self.assertNotIn("open(", core)
        self.assertNotIn("httpx", core + layer)
        self.assertNotIn("search_tcmsp", layer)
        self.assertNotIn("urlopen", core + layer)

        def boom(*args, **kwargs):
            raise AssertionError("network")

        originals = (socket.socket, socket.create_connection)
        socket.socket = boom
        socket.create_connection = boom
        try:
            out = align_tcmsp(
                "molecule_name\nprotocatechuic acid\n",
                f"name,inchikey\nprotocatechuic acid,{KEY_A}\n",
            )
        finally:
            socket.socket, socket.create_connection = originals
        self.assertEqual(out["matches"][0]["link"], "name")


if __name__ == "__main__":
    unittest.main()
