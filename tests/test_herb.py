import json
import unittest
from pathlib import Path

from tcm_core.herb import (
    HTTPS_DETAIL_URL,
    HTTP_DETAIL_URL,
    TARGET_NOTE,
    fetch_herb_detail,
    format_herb,
)

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).resolve().parent / "fixtures" / "herb_detail_min.json"


def _page() -> str:
    return FIX.read_text(encoding="utf-8")


class _Scripted:
    def __init__(self, steps):
        self.steps = list(steps)
        self.urls = []
        self.bodies = []

    async def __call__(self, url, body):
        self.urls.append(url)
        self.bodies.append(body)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class HerbDetailTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_keeps_probe_fields_and_drops_scores(self):
        transport = _Scripted([
            (200, _page(), "text/html; charset=utf-8"),
        ])
        out = await fetch_herb_detail(
            " herb000281 ",
            transport=transport,
            queried_at="2026-10-13 00:00:00 UTC",
        )
        self.assertIsNone(out["error"])
        self.assertEqual(out["url"], HTTP_DETAIL_URL)
        self.assertEqual(out["path"], "http_detail")
        self.assertEqual(out["failed_paths"], [])
        self.assertEqual(out["attempts"], 1)
        self.assertEqual(out["content_type"], "text/html; charset=utf-8")
        self.assertEqual(out["summary"]["chinese_name"], "白头翁")
        self.assertEqual(out["summary"]["latin_name"], "latin-fixture")
        self.assertNotIn("tcmsp", {key.casefold() for key in out["summary"]})
        self.assertEqual(
            [row["id"] for row in out["ingredients"]],
            ["HBIN000001", "HBIN000002"],
        )
        self.assertEqual(
            list(out["ingredients"][0]),
            ["id", "name", "alias", "formula", "smiles"],
        )
        self.assertEqual(out["ingredients"][0]["smiles"], "CCO")
        self.assertEqual(out["ingredients"][1]["alias"], "")
        blob = json.dumps(out["ingredients"], ensure_ascii=False)
        for banned in ("MolWt", "Drug_likeness", "OB_score", "ob", "dl"):
            self.assertNotIn(banned, blob)
        self.assertEqual(out["targets"][0]["gene_symbol"], "GENE1")
        self.assertEqual(out["targets"][0]["p_value"], 3.16895e-05)
        self.assertEqual(out["target_note"], TARGET_NOTE)
        self.assertNotIn("recorded_source_count", out)
        self.assertNotIn("support", out["targets"][0])
        self.assertEqual(transport.urls, [HTTP_DETAIL_URL])
        self.assertEqual(transport.bodies[0]["func_name"], "detail_api")
        self.assertEqual(transport.bodies[0]["key_id"], "HERB000281")
        self.assertNotIn(HTTPS_DETAIL_URL, transport.urls)

    async def test_environment_failure_retries_http_then_skips_https(self):
        transport = _Scripted([
            TimeoutError("timed out"),
            ConnectionResetError("connection reset"),
        ])
        out = await fetch_herb_detail(
            "HERB000281", transport=transport, queried_at="t",
        )
        self.assertEqual(out["ingredients"], [])
        self.assertEqual(out["targets"], [])
        self.assertEqual(out["summary"], {})
        self.assertIsNone(out["url"])
        self.assertIsNotNone(out["error"])
        self.assertEqual(
            [row["name"] for row in out["failed_paths"]],
            ["http_detail", "https_detail"],
        )
        self.assertEqual(out["failed_paths"][0]["kind"], "environment")
        self.assertEqual(out["failed_paths"][0]["attempts"], 2)
        self.assertTrue(out["failed_paths"][1]["skipped"])
        self.assertEqual(out["failed_paths"][1]["kind"], "environment")
        self.assertEqual(transport.urls, [HTTP_DETAIL_URL, HTTP_DETAIL_URL])
        self.assertTrue(format_herb(out).startswith("Error:"))

    async def test_timeout_then_http_success_does_not_call_https(self):
        transport = _Scripted([
            TimeoutError("timed out"),
            (200, _page(), "text/html"),
        ])
        out = await fetch_herb_detail(
            "HERB000281", transport=transport, queried_at="t",
        )
        self.assertIsNone(out["error"])
        self.assertEqual(out["url"], HTTP_DETAIL_URL)
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(out["failed_paths"], [])
        self.assertEqual(len(out["ingredients"]), 2)
        self.assertEqual(transport.urls, [HTTP_DETAIL_URL, HTTP_DETAIL_URL])

    async def test_portal_and_404_stop_without_another_host(self):
        portal = _Scripted([(200, "<html><title>HERB 2.0</title></html>", "text/html")])
        portal_out = await fetch_herb_detail(
            "HERB000281", transport=portal, queried_at="t",
        )
        self.assertEqual(portal_out["ingredients"], [])
        self.assertEqual(portal_out["targets"], [])
        self.assertEqual(portal_out["failed_paths"][0]["kind"], "method")
        self.assertEqual([row["name"] for row in portal_out["failed_paths"]], ["http_detail"])
        self.assertIn("不换成别的主机", portal_out["error"])
        self.assertEqual(portal.urls, [HTTP_DETAIL_URL])

        missing = _Scripted([(404, "Sorry, Page Not Found", "text/html")])
        missing_out = await fetch_herb_detail(
            "HERB000281", transport=missing, queried_at="t",
        )
        self.assertEqual(missing_out["ingredients"], [])
        self.assertIn("404", missing_out["error"])
        self.assertEqual([row["name"] for row in missing_out["failed_paths"]], ["http_detail"])
        self.assertEqual(missing.urls, [HTTP_DETAIL_URL])

    async def test_bad_id_does_not_request(self):
        transport = _Scripted([])
        out = await fetch_herb_detail("黄连", transport=transport, queried_at="t")
        self.assertEqual(transport.urls, [])
        self.assertEqual(out["ingredients"], [])
        self.assertEqual(out["targets"], [])
        self.assertIn("HERB", out["error"])

    async def test_tls_eof_is_environment_not_an_empty_page(self):
        transport = _Scripted([
            OSError("SSL: UNEXPECTED_EOF_WHILE_READING"),
            OSError("SSL: UNEXPECTED_EOF_WHILE_READING"),
        ])
        out = await fetch_herb_detail(
            "HERB000281", transport=transport, queried_at="t",
        )
        self.assertIsNotNone(out["error"])
        self.assertEqual(out["ingredients"], [])
        self.assertNotEqual(out["url"], HTTPS_DETAIL_URL)
        self.assertTrue(out["failed_paths"][1]["skipped"])

    def test_source_does_not_request_the_full_ingredient_file_or_other_hosts(self):
        source = (ROOT / "tcm_core" / "herb.py").read_text(encoding="utf-8")
        for banned in ("ingredient_info", "download_data", "herb.ac.cn", "netlab", "ob_dl"):
            self.assertNotIn(banned, source)
        layer = (ROOT / "mcp_server" / "herb_layer.py").read_text(encoding="utf-8")
        self.assertIn("broad_name_candidates", layer)
        self.assertIn("fetch_herb_detail", layer)


class BroadNameToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_keeps_lookalikes_apart(self):
        from mcp_server.herb_layer import BroadNamesInput, tcm_broad_names

        raw = await tcm_broad_names(BroadNamesInput(query="黄连"))
        data = json.loads(raw)
        by_name = {row["chinese_name"]: row for row in data["candidates"]}
        exact = [row for row in data["candidates"] if row["same_herb"]]
        self.assertEqual([row["chinese_name"] for row in exact], ["黄连"])
        self.assertFalse(by_name["胡黄连"]["same_herb"])
        self.assertNotEqual(by_name["黄连"]["herb_id"], by_name["胡黄连"]["herb_id"])
        self.assertIsNone(data["picked_herb_id"])
        self.assertIn("不是疗效", data["note"])

        prepared = json.loads(await tcm_broad_names(BroadNamesInput(query="炙甘草")))
        exact = [row for row in prepared["candidates"] if row["same_herb"]]
        self.assertEqual([row["chinese_name"] for row in exact], ["炙甘草"])
        gancao = {row["chinese_name"]: row for row in prepared["candidates"]}["甘草"]
        self.assertFalse(gancao["same_herb"])
        self.assertNotEqual(exact[0]["herb_id"], gancao["herb_id"])

    def test_not_on_the_smart_run_map(self):
        unified = (ROOT / "mcp_server" / "unified_mcp.py").read_text(encoding="utf-8")
        fn_map = unified.split("_fn_map", 1)[1].split("attach_tools", 1)[0]
        self.assertNotIn("tcm_herb_detail", fn_map)
        self.assertNotIn("tcm_broad_names", fn_map)
        self.assertNotIn("_herb_mod", fn_map)
        smart = (ROOT / "mcp_server" / "smart_layer.py").read_text(encoding="utf-8")
        graph = (ROOT / "tcm_core" / "analysis_graph.py").read_text(encoding="utf-8")
        for text in (smart, graph):
            self.assertNotIn("tcm_herb_detail", text)
            self.assertNotIn("tcm_broad_names", text)


if __name__ == "__main__":
    unittest.main()
