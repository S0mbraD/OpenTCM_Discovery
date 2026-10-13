import json
import unittest
from pathlib import Path

from tcm_core.name_spectrum import broad_name_candidates, load_herb_rows

ROOT = Path(__file__).resolve().parent.parent
INDEX_PATH = ROOT / "data" / "herb_mol_index.json"
ALIAS_PATH = ROOT / "data" / "name_aliases.json"

HERBS = {
    "SMHB00186": {
        "chinese_name": "黄连",
        "pinyin": "Huanglian",
        "latin_name": "Coptidis Rhizoma",
        "class": "清热燥湿药",
    },
    "SMHB00172": {
        "chinese_name": "胡黄连",
        "pinyin": "Huhuangl1an",
        "latin_name": "Picrorhizae Rhizoma",
        "class": "清虚热药",
    },
    "SMHB00188": {
        "chinese_name": "黄芩",
        "pinyin": "Huangqin",
        "latin_name": "Scutellariae Radix",
        "class": "清热燥湿药",
    },
    "SMHB00133": {
        "chinese_name": "甘草",
        "pinyin": "Gancao",
        "latin_name": "Glycyrrhizae Radix Et Rhizoma",
        "class": "补气药",
    },
    "SMHB00537": {
        "chinese_name": "炙甘草",
        "pinyin": "Zhigancao",
        "latin_name": "Glycyrrhizae Radix Et Rhizoma\nPraeparata Cum Melle",
        "class": "补虚药",
    },
    "SMHB00134": {
        "chinese_name": "甘松",
        "pinyin": "Gansong",
        "latin_name": "Nardostachyos Radix Et Rhizoma",
        "class": "理气药",
    },
    "SMHB00155": {
        "chinese_name": "桂枝",
        "pinyin": "Guizi",
        "latin_name": "Cinnamomi Ramulus",
        "class": "解表药",
    },
    "SMHB00131": {
        "chinese_name": "附子",
        "pinyin": "Fuzi",
        "latin_name": "Aconiti Lateralis Radix Praeparata",
        "class": "温里药",
    },
    "SMHB00010": {
        "chinese_name": "白附子",
        "pinyin": "Baifuzi",
        "latin_name": "Typhonii Rhizoma",
        "class": "化痰药",
    },
    "SMHB00515": {
        "chinese_name": "山楂",
        "pinyin": "Shanzha",
        "latin_name": "Crataegi Fructus",
        "class": "消食药",
    },
    "SMHB00360": {
        "chinese_name": "山楂叶",
        "pinyin": "Shanzha",
        "latin_name": "Crataegi Folium",
        "class": "活血化瘀药",
    },
}


def _aliases():
    return json.loads(ALIAS_PATH.read_text(encoding="utf-8"))


def _run(query, **kwargs):
    kwargs.setdefault("herbs", HERBS)
    kwargs.setdefault("aliases", _aliases())
    kwargs.setdefault("index_source", "fixture")
    return broad_name_candidates(query, **kwargs)


def _by_name(out):
    return {row["chinese_name"]: row for row in out["candidates"]}


class NameSpectrumTests(unittest.TestCase):
    def test_module_does_not_use_the_network(self):
        source = (ROOT / "tcm_core" / "name_spectrum.py").read_text(encoding="utf-8")
        for word in ("urllib", "httpx", "requests", "socket"):
            self.assertNotIn(word, source)

    def test_not_on_the_smart_run_chain_or_topology_hints(self):
        smart = (ROOT / "mcp_server" / "smart_layer.py").read_text(encoding="utf-8")
        unified = (ROOT / "mcp_server" / "unified_mcp.py").read_text(encoding="utf-8")
        graph = (ROOT / "tcm_core" / "analysis_graph.py").read_text(encoding="utf-8")
        for text in (smart, unified, graph):
            self.assertNotIn("broad_name_candidates", text)
            self.assertNotIn("name_spectrum", text)

    def test_shipped_alias_table_has_one_sourced_label_and_no_formulas(self):
        data = _aliases()
        self.assertEqual(data["formulas"], [])
        self.assertEqual([row["alias"] for row in data["labels"]], ["licorice"])
        self.assertFalse(data["labels"][0]["same_herb"])
        self.assertEqual(data["labels"][0]["herb_id"], "SMHB00133")
        self.assertIn("DATA_SOURCES.md", data["labels"][0]["source"])
        pairs = {tuple(row["herb_ids"]) for row in data["distinctions"]}
        self.assertEqual(pairs, {
            ("SMHB00186", "SMHB00172"),
            ("SMHB00133", "SMHB00537"),
        })
        blob = json.dumps(data, ensure_ascii=False)
        self.assertNotIn("生甘草", blob)
        self.assertNotIn("goldthread", blob.casefold())

    def test_empty_query_adds_no_rows(self):
        out = _run("  ")
        self.assertEqual(out["error"], "查询是空的。")
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["formula_candidates"], [])
        self.assertIsNone(out["picked_herb_id"])
        self.assertNotIn("queried_at", out)
        self.assertIn("不是疗效", out["note"])

    def test_bad_kind_and_limit_add_no_rows(self):
        kind = broad_name_candidates("黄连", kind="药材", herbs=HERBS, aliases=_aliases())
        self.assertIn("herb", kind["error"])
        self.assertEqual(kind["candidates"], [])
        limit = _run("黄连", limit=0)
        self.assertIn("limit", limit["error"])
        self.assertEqual(limit["candidates"], [])
        flag = _run("黄连", limit=True)
        self.assertIn("limit", flag["error"])

    def test_huanglian_and_huhuanglian_stay_apart(self):
        out = _run("黄连")
        found = _by_name(out)
        exact = [row for row in out["candidates"] if row["same_herb"]]
        self.assertEqual([row["chinese_name"] for row in exact], ["黄连"])
        self.assertEqual(exact[0]["herb_id"], "SMHB00186")
        self.assertEqual(exact[0]["pinyin"], "Huanglian")
        self.assertEqual(exact[0]["latin_name"], "Coptidis Rhizoma")
        self.assertTrue(exact[0]["field_hit"])
        self.assertFalse(found["胡黄连"]["same_herb"])
        self.assertFalse(found["胡黄连"]["merged"])
        self.assertEqual(found["胡黄连"]["herb_id"], "SMHB00172")
        self.assertEqual(found["胡黄连"]["pinyin"], "Huhuangl1an")
        self.assertEqual(found["胡黄连"]["level"], "contained_name")
        self.assertNotEqual(found["胡黄连"]["herb_id"], exact[0]["herb_id"])
        self.assertTrue(found["胡黄连"]["distinction_sources"])
        self.assertIn("Picrorhizae", found["胡黄连"]["distinction_sources"][0])
        self.assertEqual(found["黄芩"]["level"], "one_character")
        self.assertFalse(found["黄芩"]["same_herb"])
        self.assertIsNone(out["picked_herb_id"])
        self.assertIn("不是同一药材", out["note"])
        self.assertTrue(all(row["merged"] is False for row in out["candidates"]))

    def test_limit_keeps_the_lookalike_and_drops_the_one_character_neighbor(self):
        out = _run("黄连", limit=1)
        names = {row["chinese_name"] for row in out["candidates"]}
        self.assertEqual(names, {"黄连", "胡黄连"})
        self.assertTrue(out["truncated"])
        self.assertGreater(out["n_dropped"], 0)
        huhuang = _by_name(out)["胡黄连"]
        self.assertFalse(huhuang["same_herb"])

    def test_shenggancao_is_not_the_index_row(self):
        out = _run("生甘草")
        self.assertEqual(out["n_index_exact"], 0)
        self.assertIsNone(out["error"])
        found = _by_name(out)
        self.assertEqual(found["甘草"]["level"], "processing_prefix")
        self.assertFalse(found["甘草"]["same_herb"])
        self.assertEqual(found["甘草"]["herb_id"], "SMHB00133")
        self.assertIn("炙甘草", found)
        self.assertFalse(found["炙甘草"]["same_herb"])
        self.assertNotEqual(found["甘草"]["herb_id"], found["炙甘草"]["herb_id"])
        self.assertTrue(all(not row["same_herb"] for row in out["candidates"]))
        self.assertIn("炮制前缀", out["note"])
        self.assertNotIn("queried_at", out)

    def test_zhigancao_keeps_its_own_latin_and_does_not_absorb_gancao(self):
        out = _run("炙甘草")
        exact = [row for row in out["candidates"] if row["same_herb"]]
        self.assertEqual(len(exact), 1)
        self.assertEqual(exact[0]["herb_id"], "SMHB00537")
        self.assertIn("\n", exact[0]["latin_name"])
        self.assertIn("Praeparata", exact[0]["latin_name"])
        other = _by_name(out)["甘草"]
        self.assertFalse(other["same_herb"])
        self.assertEqual(other["herb_id"], "SMHB00133")

    def test_licorice_is_a_cited_label_not_an_index_hit(self):
        out = _run("Licorice")
        self.assertEqual(out["n_index_exact"], 0)
        labels = [row for row in out["candidates"] if row["match"] == "alias_table"]
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0]["alias"], "licorice")
        self.assertEqual(labels[0]["herb_id"], "SMHB00133")
        self.assertEqual(labels[0]["pinyin"], "Gancao")
        self.assertFalse(labels[0]["same_herb"])
        self.assertIn("DATA_SOURCES.md", labels[0]["source"])
        self.assertFalse(_by_name(out)["炙甘草"]["same_herb"])
        self.assertTrue(all(not row["same_herb"] for row in out["candidates"]))

    def test_unsourced_english_name_is_not_invented(self):
        out = _run("goldthread")
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["formula_candidates"], [])
        self.assertIn("没有命中", out["note"])
        self.assertIn("没有补行", out["note"])

    def test_stored_pinyin_typo_is_not_rewritten_as_an_index_hit(self):
        out = _run("Huhuanglian")
        self.assertEqual(out["n_index_exact"], 0)
        found = _by_name(out)
        self.assertEqual(found["胡黄连"]["match"], "string_similar")
        self.assertEqual(found["胡黄连"]["pinyin"], "Huhuangl1an")
        self.assertEqual(found["胡黄连"]["level"], "pinyin_near")
        self.assertFalse(found["胡黄连"]["same_herb"])
        self.assertFalse(found["黄连"]["same_herb"])
        self.assertEqual(found["黄连"]["level"], "pinyin_near")

    def test_gancao_pinyin_lists_the_english_label_beside_the_index_row(self):
        out = _run("Gancao")
        exact = [row for row in out["candidates"] if row["same_herb"]]
        self.assertEqual([row["herb_id"] for row in exact], ["SMHB00133"])
        self.assertEqual(exact[0]["matched_field"], "pinyin")
        aliases = [row["alias"] for row in out["candidates"] if row["match"] == "alias_table"]
        self.assertEqual(aliases, ["licorice"])
        self.assertFalse(_by_name(out)["炙甘草"]["same_herb"])

    def test_prepared_latin_does_not_swallow_the_unprepared_row(self):
        query = "Glycyrrhizae Radix Et Rhizoma Praeparata Cum Melle"
        out = _run(query)
        exact = [row for row in out["candidates"] if row["same_herb"]]
        self.assertEqual([row["chinese_name"] for row in exact], ["炙甘草"])
        self.assertEqual(exact[0]["matched_field"], "latin_name")
        self.assertFalse(_by_name(out)["甘草"]["same_herb"])

    def test_fuzi_and_baifuzi_stay_apart(self):
        out = _run("附子")
        exact = [row for row in out["candidates"] if row["same_herb"]]
        self.assertEqual([row["chinese_name"] for row in exact], ["附子"])
        other = _by_name(out)["白附子"]
        self.assertFalse(other["same_herb"])
        self.assertEqual(other["level"], "contained_name")
        self.assertNotEqual(other["herb_id"], exact[0]["herb_id"])

    def test_shanzha_pinyin_hits_two_rows_and_picks_neither(self):
        out = _run("Shanzha")
        exact = [row for row in out["candidates"] if row["field_hit"]]
        self.assertEqual(
            {row["chinese_name"] for row in exact},
            {"山楂", "山楂叶"},
        )
        self.assertEqual(len({row["herb_id"] for row in exact}), 2)
        self.assertTrue(out["ambiguous_exact"])
        self.assertIsNone(out["picked_herb_id"])
        self.assertTrue(all(not row["same_herb"] for row in out["candidates"]))
        self.assertIn("没有挑一条", out["note"])

    def test_shanzhaye_does_not_swallow_shanzha(self):
        out = _run("山楂叶")
        exact = [row for row in out["candidates"] if row["same_herb"]]
        self.assertEqual([row["chinese_name"] for row in exact], ["山楂叶"])
        other = _by_name(out)["山楂"]
        self.assertFalse(other["same_herb"])
        self.assertEqual(other["level"], "contained_name")

    def test_formula_suffix_is_not_a_formula_record(self):
        out = _run("桂枝汤")
        self.assertEqual(out["formula_candidates"], [])
        self.assertEqual(out["n_index_exact"], 0)
        twig = _by_name(out)["桂枝"]
        self.assertEqual(twig["level"], "formula_name_contains_herb")
        self.assertFalse(twig["same_herb"])
        self.assertIn("不是方剂索引", out["note"])

    def test_formula_kind_does_not_return_the_contained_herb(self):
        out = _run("桂枝汤", kind="formula")
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["formula_candidates"], [])
        self.assertIsNone(out["error"])
        self.assertIn("没有把药材名当成方剂", out["note"])

    def test_injected_formula_alias_stays_beside_the_herb(self):
        aliases = {
            "labels": [],
            "distinctions": [],
            "formulas": [{
                "alias": "桂枝汤",
                "also": ["Guizhi Tang"],
                "source": "测试夹具里的叫法，用来核对候选没有并成药材。",
                "same_formula": False,
            }],
        }
        both = _run("桂枝汤", aliases=aliases)
        self.assertEqual(len(both["formula_candidates"]), 1)
        self.assertFalse(both["formula_candidates"][0]["same_formula"])
        self.assertFalse(both["formula_candidates"][0]["merged"])
        self.assertEqual(both["formula_candidates"][0]["alias"], "桂枝汤")
        self.assertFalse(_by_name(both)["桂枝"]["same_herb"])
        herb_only = _run("桂枝汤", kind="herb", aliases=aliases)
        self.assertEqual(herb_only["formula_candidates"], [])
        self.assertIn("桂枝", _by_name(herb_only))
        formula_only = _run("Guizhi Tang", kind="formula", aliases=aliases)
        self.assertEqual(formula_only["candidates"], [])
        self.assertEqual(formula_only["formula_candidates"][0]["alias"], "桂枝汤")
        self.assertFalse(formula_only["formula_candidates"][0]["same_formula"])

    def test_alias_that_claims_same_herb_is_ignored(self):
        aliases = {
            "labels": [{
                "alias": "goldthread",
                "herb_id": "SMHB00186",
                "chinese_name": "黄连",
                "same_herb": True,
                "source": "这条不该被收进来。",
            }],
            "distinctions": [],
            "formulas": [{
                "alias": "某方",
                "same_formula": True,
                "source": "这条也不该被收进来。",
            }],
        }
        out = _run("goldthread", aliases=aliases)
        self.assertEqual(out["candidates"], [])
        self.assertEqual(out["formula_candidates"], [])
        reasons = {item["reason"] for item in out["ignored_aliases"]}
        self.assertIn("same_herb", reasons)
        self.assertIn("same_formula", reasons)

    def test_missing_index_does_not_invent_rows(self):
        aliases = {
            "labels": [],
            "distinctions": [],
            "formulas": [{
                "alias": "桂枝汤",
                "source": "测试夹具里的叫法，用来核对缺索引时不补药材。",
            }],
        }
        out = broad_name_candidates(
            "桂枝汤",
            index_path=ROOT / "data" / "missing-herb-index.json",
            aliases=aliases,
        )
        self.assertEqual(out["error"], "没有读到药材索引。")
        self.assertEqual(out["candidates"], [])
        self.assertEqual(len(out["formula_candidates"]), 1)
        self.assertFalse(out["formula_candidates"][0]["same_formula"])

    def test_missing_alias_file_does_not_invent_a_translation(self):
        out = broad_name_candidates(
            "黄连",
            herbs=HERBS,
            alias_path=ROOT / "data" / "missing-aliases.json",
        )
        self.assertIsNone(out["error"])
        self.assertIn("没有编造译名", out["note"])
        self.assertEqual(
            [row for row in out["candidates"] if row["match"] == "alias_table"],
            [],
        )
        exact = [row for row in out["candidates"] if row["same_herb"]]
        self.assertEqual([row["chinese_name"] for row in exact], ["黄连"])


class ShippedIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.herbs, cls.source = load_herb_rows(INDEX_PATH)
        cls.aliases = _aliases()

    def _shipped(self, query, **kwargs):
        kwargs.setdefault("herbs", self.herbs)
        kwargs.setdefault("aliases", self.aliases)
        kwargs.setdefault("index_source", self.source)
        return broad_name_candidates(query, **kwargs)

    def test_path_loader_reads_the_shipped_index(self):
        self.assertEqual(len(self.herbs), 698)
        self.assertEqual(self.herbs["SMHB00172"]["pinyin"], "Huhuangl1an")
        names = {row.get("chinese_name") for row in self.herbs.values()}
        self.assertNotIn("生甘草", names)
        self.assertIn("SymMap", self.source)
        out = broad_name_candidates(
            "黄连",
            index_path=INDEX_PATH,
            alias_path=ALIAS_PATH,
        )
        exact = [row for row in out["candidates"] if row["same_herb"]]
        self.assertEqual([row["herb_id"] for row in exact], ["SMHB00186"])
        self.assertIn("SymMap", exact[0]["source"])
        huhuang = _by_name(out)["胡黄连"]
        self.assertEqual(huhuang["pinyin"], "Huhuangl1an")
        self.assertFalse(huhuang["same_herb"])
        self.assertNotIn("queried_at", out)

    def test_shipped_index_keeps_processing_forms_apart(self):
        gancao = self._shipped("甘草")
        exact = [row for row in gancao["candidates"] if row["same_herb"]]
        self.assertEqual([row["chinese_name"] for row in exact], ["甘草"])
        found = _by_name(gancao)
        self.assertFalse(found["炙甘草"]["same_herb"])
        self.assertNotEqual(found["甘草"]["herb_id"], found["炙甘草"]["herb_id"])
        self.assertIn("licorice", [row["alias"] for row in gancao["candidates"]])
        self.assertFalse(found["甘松"]["same_herb"])
        self.assertTrue(gancao["truncated"])
        raw = self._shipped("生甘草")
        self.assertEqual(raw["n_index_exact"], 0)
        raw_found = _by_name(raw)
        self.assertEqual(raw_found["甘草"]["level"], "processing_prefix")
        self.assertFalse(raw_found["甘草"]["same_herb"])
        self.assertFalse(raw_found["炙甘草"]["same_herb"])
        banxia = self._shipped("半夏")
        exact_banxia = [row for row in banxia["candidates"] if row["same_herb"]]
        self.assertEqual([row["chinese_name"] for row in exact_banxia], ["半夏"])
        for name in ("法半夏", "姜半夏", "清半夏"):
            row = _by_name(banxia)[name]
            self.assertFalse(row["same_herb"])
            self.assertNotEqual(row["herb_id"], exact_banxia[0]["herb_id"])
