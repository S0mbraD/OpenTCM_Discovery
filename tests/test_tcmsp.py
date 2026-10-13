import unittest
from pathlib import Path

from tcm_core.tcmsp import (
    apply_ob_dl,
    extract_grid,
    extract_token,
    format_result,
    request_with_retry,
    search_tcmsp,
)

FIX = Path(__file__).resolve().parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class ParseTests(unittest.TestCase):
    def test_token_and_real_gancao_rows(self):
        token = extract_token(_read("tcmsp_home.html"))
        self.assertEqual(token, "96313e1e5f96a68ac9ab8c8e4d2d64e8")
        herbs = extract_grid(_read("tcmsp_herbs_gancao.html"))
        self.assertEqual(herbs[0]["herb_en_name"], "licorice")
        self.assertEqual(herbs[0]["herb_cn_name"], "甘草")
        rows = extract_grid(_read("tcmsp_ingredients_gancao.html"))
        self.assertEqual([row["MOL_ID"] for row in rows], ["MOL000105", "MOL001097"])
        self.assertEqual(rows[0]["molecule_name"], "protocatechuic acid")

    def test_malformed_pages_raise(self):
        html = _read("tcmsp_malformed.html")
        with self.assertRaises(ValueError):
            extract_token(html)
        with self.assertRaises(ValueError):
            extract_grid(html)
        with self.assertRaises(ValueError):
            extract_grid('dataSource: { data: {"not": "a list"},')
        with self.assertRaises(ValueError):
            extract_grid("dataSource: { data: [1, 2,")

    def test_ob_dl_keeps_only_tcmsp_threshold(self):
        rows = extract_grid(_read("tcmsp_ingredients_gancao.html"))
        kept, dropped = apply_ob_dl(rows)
        self.assertEqual(kept, [])
        self.assertEqual(dropped, 2)
        sample = [
            {"ob": "30", "dl": "0.18", "molecule_name": "boundary"},
            {"ob": "29.9", "dl": "0.20", "molecule_name": "low-ob"},
            {"ob": "", "dl": "0.5", "molecule_name": "missing"},
        ]
        kept, dropped = apply_ob_dl(sample)
        self.assertEqual([row["molecule_name"] for row in kept], ["boundary"])
        self.assertEqual(dropped, 2)


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def _transport(self, pages):
        calls = []

        async def transport(url, referer):
            calls.append((url, referer))
            if url.endswith("/tcmsp.php"):
                return 200, pages["home"]
            if "herb_all_name" in url:
                return 200, pages["herbs"]
            if "qsr=herb_en_name" in url:
                return 200, pages["ingredients"]
            return 404, "missing " + url

        return transport, calls

    async def test_empty_query_does_not_fetch(self):
        called = False

        async def transport(url, referer):
            nonlocal called
            called = True
            return 200, ""

        payload = await search_tcmsp(
            query="  ",
            latin_name=None,
            ob_dl_filter=False,
            limit=10,
            transport=transport,
            queried_at="2026-10-13 04:40:00 UTC",
        )
        self.assertFalse(called)
        self.assertIn("空", payload["error"])
        self.assertEqual(payload["ingredients"], [])
        self.assertEqual(payload["source"], "TCMSP legacy https://old.tcmsp-e.com/tcmspsearch.php")
        self.assertTrue(format_result(payload).startswith("Error:"))

    async def test_malformed_home_returns_no_rows(self):
        async def transport(url, referer):
            return 200, _read("tcmsp_malformed.html")

        payload = await search_tcmsp(
            query="甘草",
            latin_name=None,
            ob_dl_filter=False,
            limit=10,
            transport=transport,
            queried_at="2026-10-13 04:40:00 UTC",
        )
        self.assertIn("token", payload["error"])
        self.assertEqual(payload["herbs"], [])
        self.assertEqual(payload["ingredients"], [])
        self.assertEqual(payload["queried_at"], "2026-10-13 04:40:00 UTC")

    async def test_gancao_fixture_parses_two_ingredients(self):
        transport, calls = self._transport({
            "home": _read("tcmsp_home.html"),
            "herbs": _read("tcmsp_herbs_gancao.html"),
            "ingredients": _read("tcmsp_ingredients_gancao.html"),
        })
        payload = await search_tcmsp(
            query="甘草",
            latin_name=None,
            ob_dl_filter=True,
            limit=10,
            transport=transport,
            queried_at="2026-10-13 04:40:00 UTC",
        )
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["latin_name"], "licorice")
        self.assertEqual(payload["ingredients_parsed"], 2)
        self.assertEqual(payload["ingredients_matched"], 0)
        self.assertEqual(payload["ob_dl_dropped"], 2)
        self.assertEqual(payload["ingredients"], [])
        self.assertEqual(len(calls), 3)
        self.assertIn("licorice", calls[2][0])
        self.assertIn("%E7%94%98%E8%8D%89", calls[1][0])

    async def test_two_herbs_do_not_invent_ingredients(self):
        herbs = (
            'dataSource: { data: ['
            '{"herb_cn_name":"黄连","herb_en_name":"Coptidis Rhizoma","herb_pinyin":"Huanglian"},'
            '{"herb_cn_name":"胡黄连","herb_en_name":"Picrorhizae Rhizoma","herb_pinyin":"Huhuanglian"}'
            '], pageSize: 15, },'
        )
        transport, calls = self._transport({
            "home": _read("tcmsp_home.html"),
            "herbs": herbs,
            "ingredients": _read("tcmsp_ingredients_gancao.html"),
        })
        payload = await search_tcmsp(
            query="黄连",
            latin_name=None,
            ob_dl_filter=False,
            limit=10,
            transport=transport,
            queried_at="2026-10-13 04:40:00 UTC",
        )
        self.assertIsNone(payload["error"])
        self.assertEqual(len(payload["herbs"]), 2)
        self.assertEqual(payload["ingredients"], [])
        self.assertEqual(len(calls), 2)

    async def test_http_error_is_clipped(self):
        async def transport(url, referer):
            if url.endswith("/tcmsp.php"):
                return 200, _read("tcmsp_home.html")
            return 502, "X" * 800 + " upstream exploded"

        payload = await search_tcmsp(
            query="甘草",
            latin_name=None,
            ob_dl_filter=False,
            limit=5,
            transport=transport,
            queried_at="2026-10-13 04:40:00 UTC",
        )
        self.assertTrue(payload["error"].startswith("HTTP 502"))
        self.assertLessEqual(len(payload["error"]), 400)
        self.assertEqual(payload["ingredients"], [])
        self.assertNotIn("upstream exploded", payload["error"])


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_timeout_then_reads_body(self):
        calls = {"n": 0}

        async def transport(url, referer):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("timed out")
            return 200, "ok"

        status, text, err = await request_with_retry(transport, "https://example.test", None)
        self.assertEqual((status, text, err), (200, "ok", None))
        self.assertEqual(calls["n"], 2)

    async def test_does_not_retry_404(self):
        calls = {"n": 0}

        async def transport(url, referer):
            calls["n"] += 1
            return 404, "missing page"

        status, text, err = await request_with_retry(transport, "https://example.test", None)
        self.assertEqual(status, 404)
        self.assertIsNone(err)
        self.assertEqual(calls["n"], 1)

    async def test_two_server_errors_keep_no_body(self):
        async def transport(url, referer):
            return 500, "boom " * 100

        status, text, err = await request_with_retry(transport, "https://example.test", None)
        self.assertIsNone(status)
        self.assertEqual(text, "")
        self.assertIn("HTTP 500", err)
        self.assertLess(len(err), 400)


if __name__ == "__main__":
    unittest.main()
