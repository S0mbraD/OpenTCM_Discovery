import unittest

from tcm_core.prep import (
    PrepError,
    clean_table,
    convert_doses,
    normalize_disease,
    normalize_genes,
    normalize_ids,
    normalize_structure,
)


class CleanTableTests(unittest.TestCase):
    def test_fullwidth_comma_and_blank_row(self):
        text = "名称，靶点\n小檗碱，TNF\n\n黄芩苷，PTGS2\n"
        out = clean_table(text)
        self.assertEqual(out["delimiter"], "comma")
        self.assertEqual(out["columns"], ["名称", "靶点"])
        self.assertEqual(out["rows"], [["小檗碱", "TNF"], ["黄芩苷", "PTGS2"]])
        self.assertEqual(out["dropped_empty_rows"], 1)

    def test_quoted_comma_stays_in_the_cell(self):
        text = 'name,note\nberberine,"cid 2353, alkaloid"\n'
        out = clean_table(text, delimiter="comma")
        self.assertEqual(out["rows"], [["berberine", "cid 2353, alkaloid"]])

    def test_header_only_is_an_empty_table(self):
        out = clean_table("name\tgene")
        self.assertEqual(out["delimiter"], "tab")
        self.assertEqual(out["columns"], ["name", "gene"])
        self.assertEqual(out["n_rows"], 0)

    def test_single_column_list(self):
        out = clean_table("symbol\nTNF\nIL6\n")
        self.assertEqual(out["delimiter"], "none")
        self.assertEqual(out["rows"], [["TNF"], ["IL6"]])

    def test_empty_input_raises(self):
        with self.assertRaises(PrepError):
            clean_table("  \n\t")
        with self.assertRaises(PrepError):
            clean_table(None)

    def test_inconsistent_columns_raise(self):
        with self.assertRaises(PrepError) as caught:
            clean_table("a,b\n1,2,3\n")
        self.assertIn("列数", str(caught.exception))

    def test_duplicate_header_raises(self):
        with self.assertRaises(PrepError):
            clean_table("gene,gene\nTNF,IL6\n")

    def test_unknown_delimiter_raises(self):
        with self.assertRaises(PrepError):
            clean_table("a,b\n1,2\n", delimiter="excel")

    def test_unclosed_quote_raises(self):
        with self.assertRaises(PrepError):
            clean_table('a,b\n1,"2\n')

    def test_column_limit(self):
        header = ",".join(f"c{i}" for i in range(40))
        row = ",".join("1" for _ in range(40))
        out = clean_table(header + "\n" + row)
        self.assertEqual(out["n_columns"], 40)
        too_wide = ",".join(f"c{i}" for i in range(41))
        with self.assertRaises(PrepError):
            clean_table(too_wide + "\n" + ",".join("1" for _ in range(41)))

    def test_row_limit(self):
        body = "\n".join("x" for _ in range(2000))
        out = clean_table("h\n" + body)
        self.assertEqual(out["n_rows"], 2000)
        with self.assertRaises(PrepError):
            clean_table("h\n" + "\n".join("x" for _ in range(2001)))


class NormalizeIdTests(unittest.TestCase):
    def test_known_shapes(self):
        text = (
            "berberine CID:2353 CAS:2086-83-1 "
            "ybhilyktiriute-uhfffaoysa-n P01375 ENSG00000232810"
        )
        out = normalize_ids(text)
        kinds = {item["kind"]: item for item in out["items"]}
        self.assertEqual(kinds["pubchem_cid"]["value"], "2353")
        self.assertTrue(kinds["pubchem_cid"]["valid"])
        self.assertTrue(kinds["cas"]["valid"])
        self.assertEqual(kinds["cas"]["value"], "2086-83-1")
        self.assertEqual(kinds["inchikey"]["value"], "YBHILYKTIRIUTE-UHFFFAOYSA-N")
        self.assertTrue(kinds["inchikey"]["standard"])
        self.assertEqual(kinds["uniprot"]["value"], "P01375")
        self.assertEqual(kinds["ensembl_gene"]["value"], "ENSG00000232810")
        self.assertIn("berberine", out["unrecognized"])

    def test_cas_checksum_and_plain_integer(self):
        bad = normalize_ids("2086-83-2")
        self.assertFalse(bad["items"][0]["valid"])
        self.assertEqual(bad["items"][0]["reason"], "CAS 校验位不符。")
        good = normalize_ids("50-00-0")
        self.assertTrue(good["items"][0]["valid"])
        bare = normalize_ids("2353")
        self.assertEqual(bare["items"], [])
        self.assertEqual(bare["ambiguous"][0]["value"], "2353")

    def test_cid_bounds_and_gene_id(self):
        low = normalize_ids("CID:1")
        high = normalize_ids("CID:160000000")
        over = normalize_ids("CID:160000001")
        self.assertTrue(low["items"][0]["valid"])
        self.assertTrue(high["items"][0]["valid"])
        self.assertFalse(over["items"][0]["valid"])
        gene = normalize_ids("GeneID:7124")
        self.assertEqual(gene["items"][0]["kind"], "ncbi_gene_id")
        self.assertEqual(gene["items"][0]["value"], "7124")

    def test_bad_inchikey_flag(self):
        out = normalize_ids("YBHILYKTIRIUTE-UHFFFAOYXA-N")
        self.assertFalse(out["items"][0]["valid"])
        nonstandard = normalize_ids("YBHILYKTIRIUTE-UHFFFAOYSA-N".replace(
            "SA-N", "NA-N"))
        self.assertTrue(nonstandard["items"][0]["valid"])
        self.assertFalse(nonstandard["items"][0]["standard"])

    def test_empty_and_type_raise(self):
        with self.assertRaises(PrepError):
            normalize_ids("   ")
        with self.assertRaises(PrepError):
            normalize_ids(None)

    def test_no_identifier_is_a_result(self):
        out = normalize_ids("hello")
        self.assertEqual(out["items"], [])
        self.assertEqual(out["unrecognized"], ["hello"])

    def test_length_limit(self):
        with self.assertRaises(PrepError):
            normalize_ids("A" * 20001)


class NormalizeStructureTests(unittest.TestCase):
    def test_ethanol_cleanup_does_not_require_rdkit(self):
        out = normalize_structure("  C(C)O  ")
        self.assertEqual(out["cleaned"], "C(C)O")
        self.assertEqual(out["component_count"], 1)
        if out["engine"] == "rdkit":
            self.assertEqual(out["canonical_smiles"], "CCO")
            if out["inchikey"]:
                self.assertEqual(out["inchikey"], "LFQSCWFLJHTTHZ-UHFFFAOYSA-N")
        else:
            self.assertIsNone(out["canonical_smiles"])
            self.assertIsNone(out["inchikey"])
            self.assertIn("RDKit", out["note"])

    def test_dot_keeps_both_components(self):
        out = normalize_structure("CCO.[Na+]")
        self.assertEqual(out["component_count"], 2)
        self.assertIn("不删除", out["note"])

    def test_empty_and_illegal_characters(self):
        with self.assertRaises(PrepError):
            normalize_structure("   ")
        with self.assertRaises(PrepError):
            normalize_structure(None)
        with self.assertRaises(PrepError):
            normalize_structure("C C")
        with self.assertRaises(PrepError) as caught:
            normalize_structure("CCO!")
        self.assertIn("字符", str(caught.exception))

    def test_unbalanced_brackets(self):
        with self.assertRaises(PrepError):
            normalize_structure("C(C")

    def test_length_limit(self):
        with self.assertRaises(PrepError):
            normalize_structure("C" * 4001)

    def test_unparseable_when_rdkit_present(self):
        try:
            out = normalize_structure("1")
        except PrepError as exc:
            self.assertIn("RDKit", str(exc))
            return
        self.assertEqual(out["engine"], "none")
        self.assertIn("RDKit", out["note"])


class NormalizeGeneTests(unittest.TestCase):
    def test_aliases_collapse(self):
        out = normalize_genes("TNF-α, TNFA, TNF, COX-2, PTGS2, IL-8, HO-1")
        by_symbol = {row["symbol"]: row for row in out["records"]}
        self.assertEqual(by_symbol["TNF"]["n_inputs"], 3)
        self.assertEqual(by_symbol["PTGS2"]["status"], "alias")
        self.assertEqual(by_symbol["CXCL8"]["symbol"], "CXCL8")
        self.assertEqual(by_symbol["HMOX1"]["symbol"], "HMOX1")

    def test_space_separated_official_symbols(self):
        out = normalize_genes("IL6 IL-6")
        self.assertEqual(len(out["records"]), 1)
        self.assertEqual(out["records"][0]["symbol"], "IL6")
        self.assertEqual(out["records"][0]["n_inputs"], 2)

    def test_ambiguous_and_unlisted(self):
        out = normalize_genes("NF-κB, VEGF, BRCA1")
        self.assertEqual(out["records"], [])
        ambiguous = {row["input"]: row["candidates"] for row in out["ambiguous"]}
        self.assertIn("NFKB1", ambiguous["NF-κB"])
        self.assertIn("RELA", ambiguous["NF-κB"])
        self.assertIn("VEGFA", ambiguous["VEGF"])
        self.assertEqual(out["unlisted"][0]["symbol"], "BRCA1")

    def test_other_identifier_is_invalid(self):
        out = normalize_genes("P01375")
        self.assertEqual(out["records"], [])
        self.assertIn("UniProt", out["invalid"][0]["reason"])

    def test_empty_and_garbage(self):
        with self.assertRaises(PrepError):
            normalize_genes(" , ")
        with self.assertRaises(PrepError):
            normalize_genes(None)
        out = normalize_genes("###")
        self.assertEqual(out["invalid"][0]["input"], "###")

    def test_token_limit(self):
        normalize_genes(",".join(["BRCA1"] * 300))
        with self.assertRaises(PrepError):
            normalize_genes(",".join(["BRCA1"] * 301))


class NormalizeDiseaseTests(unittest.TestCase):
    def test_chinese_and_english_share_one_mesh(self):
        out = normalize_disease("溃疡性结肠炎；ulcerative colitis")
        self.assertEqual(len(out["records"]), 1)
        self.assertEqual(out["records"][0]["mesh_id"], "D003093")
        self.assertEqual(out["records"][0]["heading"], "Colitis, Ulcerative")
        self.assertEqual(len(out["records"][0]["inputs"]), 2)

    def test_mesh_heading_with_comma(self):
        out = normalize_disease("Colitis, Ulcerative")
        self.assertEqual(out["records"][0]["mesh_id"], "D003093")

    def test_ambiguous_nonspecific_and_unmapped(self):
        out = normalize_disease("UC；癌症；胃溃疡")
        by_input = {row["input"]: row for row in out["records"]}
        self.assertEqual(by_input["UC"]["status"], "ambiguous")
        self.assertIsNone(by_input["UC"]["mesh_id"])
        self.assertEqual(by_input["癌症"]["status"], "nonspecific")
        self.assertEqual(by_input["胃溃疡"]["status"], "unmapped")
        self.assertIsNone(by_input["胃溃疡"]["mesh_id"])

    def test_type2_diabetes_not_bare_diabetes(self):
        mapped = normalize_disease("2 型糖尿病")
        self.assertEqual(mapped["records"][0]["mesh_id"], "D003924")
        bare = normalize_disease("糖尿病")
        self.assertEqual(bare["records"][0]["status"], "ambiguous")

    def test_empty_raises(self):
        with self.assertRaises(PrepError):
            normalize_disease("  ")
        with self.assertRaises(PrepError):
            normalize_disease(None)

    def test_name_limit(self):
        normalize_disease("；".join(f"病名{i}" for i in range(100)))
        with self.assertRaises(PrepError):
            normalize_disease("；".join(f"病名{i}" for i in range(101)))


class ConvertDoseTests(unittest.TestCase):
    def test_metric_and_thousands_separator(self):
        out = convert_doses("500 mg；1 g；1,000 mg", "metric")
        self.assertEqual([row["grams"] for row in out["rows"]], ["0.5", "1", "1"])
        self.assertEqual(out["rows"][0]["ratio_to_min"], "1")
        self.assertEqual(out["rows"][1]["ratio_to_min"], "2")
        self.assertIn("不是药效", out["note"])

    def test_song_yuan_compound_and_ratio(self):
        out = convert_doses("1两2钱；1钱；1分", "song_yuan_2025")
        self.assertEqual([row["grams"] for row in out["rows"]], ["48", "4", "0.4"])
        self.assertEqual(
            [row["ratio_to_min"] for row in out["rows"]],
            ["120", "10", "1"],
        )

    def test_named_conventions(self):
        self.assertEqual(convert_doses("1斤", "market_1959")["rows"][0]["grams"], "500")
        self.assertEqual(convert_doses("1两", "han_tang")["rows"][0]["grams"], "13.8")
        self.assertEqual(convert_doses("1分", "han_tang")["rows"][0]["grams"], "3.45")
        self.assertEqual(convert_doses("1铢", "han_tang")["rows"][0]["grams"], "0.575")
        self.assertEqual(convert_doses("1斤", "han_tang")["rows"][0]["grams"], "220.8")
        self.assertEqual(
            convert_doses("1钱", "song_jin_yuan_2024")["rows"][0]["grams"], "4.13")
        self.assertEqual(
            convert_doses("1钱", "ming_qing_2024")["rows"][0]["grams"], "3.73")
        self.assertEqual(
            convert_doses("1分", "ming_qing_2025")["rows"][0]["grams"], "0.37")

    def test_market_ceiling(self):
        self.assertEqual(convert_doses("100两", "market_1959")["rows"][0]["grams"], "5000")
        with self.assertRaises(PrepError):
            convert_doses("101两", "market_1959")

    def test_rejected_units_and_ranges(self):
        with self.assertRaises(PrepError):
            convert_doses("1两", "metric")
        with self.assertRaises(PrepError):
            convert_doses("1钱", "han_tang")
        with self.assertRaises(PrepError):
            convert_doses("1 mL", "metric")
        with self.assertRaises(PrepError):
            convert_doses("1-2两", "song_yuan_2025")
        with self.assertRaises(PrepError):
            convert_doses("半两", "market_1959")
        with self.assertRaises(PrepError):
            convert_doses("0 g", "metric")

    def test_empty_and_unknown_convention(self):
        with self.assertRaises(PrepError):
            convert_doses("   ", "metric")
        with self.assertRaises(PrepError):
            convert_doses("1 g", "ancient")
        with self.assertRaises(PrepError):
            convert_doses("1 g", None)

    def test_dose_count_limit(self):
        convert_doses(";".join(["1 g"] * 80), "metric")
        with self.assertRaises(PrepError):
            convert_doses(";".join(["1 g"] * 81), "metric")


if __name__ == "__main__":
    unittest.main()
