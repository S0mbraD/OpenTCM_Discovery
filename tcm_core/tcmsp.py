"""Parse the TCMSP legacy search pages on old.tcmsp-e.com.

The site embeds a Kendo grid as a JSON array. OB and DL filters apply
only to those two fields. They are not an efficacy claim, and this page
does not list protein targets.
"""
from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

SOURCE = "TCMSP legacy https://old.tcmsp-e.com/tcmspsearch.php"
HOME_URL = "https://old.tcmsp-e.com/tcmsp.php"
OB_MIN = 30.0
DL_MIN = 0.18

_TOKEN_RE = re.compile(
    r'name="token"\s+value="([0-9a-fA-F]{16,64})"',
    re.I,
)
_GRID_RE = re.compile(r"dataSource\s*:\s*\{\s*data\s*:\s*", re.S)

NOTE = (
    "成分行来自 TCMSP 旧站当次返回的表格。OB 和 DL 只在这份表上筛选，"
    "不是疗效。这一页没有靶点，不能把成分写成已知靶点收录。"
)

Transport = Callable[[str, Optional[str]], Awaitable[Tuple[int, str]]]


class TcmspParseError(ValueError):
    """The HTML did not contain the expected TCMSP token or grid."""


def clip_text(text: str, limit: int = 400) -> str:
    """Collapse whitespace and keep a short prefix of an upstream body."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "..."


def extract_token(html: str) -> str:
    match = _TOKEN_RE.search(html or "")
    if not match:
        raise TcmspParseError("首页没有 TCMSP token")
    return match.group(1)


def extract_grid(html: str) -> List[Dict[str, Any]]:
    """Return the JSON array embedded in a Kendo dataSource."""
    match = _GRID_RE.search(html or "")
    if not match:
        raise TcmspParseError("页面里没有 TCMSP 表格")
    try:
        data, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except json.JSONDecodeError as exc:
        raise TcmspParseError("TCMSP 表格不是完整 JSON") from exc
    if not isinstance(data, list):
        raise TcmspParseError("TCMSP 表格不是记录列表")
    return [row for row in data if isinstance(row, dict)]


def herb_search_url(token: str, query: str) -> str:
    return (
        "https://old.tcmsp-e.com/tcmspsearch.php?qs=herb_all_name&q="
        + quote(query)
        + "&token="
        + quote(token)
    )


def ingredient_url(token: str, latin_name: str) -> str:
    return (
        "https://old.tcmsp-e.com/tcmspsearch.php?qr="
        + quote(latin_name)
        + "&qsr=herb_en_name&token="
        + quote(token)
    )


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def apply_ob_dl(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Keep rows whose TCMSP ob and dl meet OB>=30 and DL>=0.18.

    Rows missing either field are dropped. The thresholds are not applied
    to any other database.
    """
    kept: List[Dict[str, Any]] = []
    dropped = 0
    for row in rows:
        ob = _number(row.get("ob"))
        dl = _number(row.get("dl"))
        if ob is None or dl is None or ob < OB_MIN or dl < DL_MIN:
            dropped += 1
            continue
        kept.append(row)
    return kept, dropped


def format_result(payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if payload.get("error"):
        return "Error: " + body
    return body


def _fail(payload: Dict[str, Any], message: str, *, keep_herbs: bool = False) -> Dict[str, Any]:
    payload["error"] = clip_text(message, 400)
    if not keep_herbs:
        payload["herbs"] = []
    payload["ingredients"] = []
    payload["ingredients_parsed"] = 0
    payload["ingredients_matched"] = 0
    payload["ob_dl_dropped"] = 0
    return payload


async def request_with_retry(
    transport: Transport,
    url: str,
    referer: Optional[str],
    attempts: int = 2,
) -> Tuple[Optional[int], str, Optional[str]]:
    """GET once, and retry one time on timeout or HTTP 5xx.

    Returns (status, body, error). A non-5xx response is returned as-is.
    """
    last_error = "request failed"
    for _ in range(max(1, attempts)):
        try:
            status, text = await transport(url, referer)
        except Exception as exc:
            last_error = type(exc).__name__ + ": " + clip_text(str(exc), 300)
            continue
        body = text or ""
        if status is not None and status < 500:
            return status, body, None
        last_error = f"HTTP {status}: " + clip_text(body, 300)
    return None, "", last_error


async def search_tcmsp(
    *,
    query: str,
    latin_name: Optional[str],
    ob_dl_filter: bool,
    limit: int,
    transport: Transport,
    queried_at: str,
) -> Dict[str, Any]:
    """Run the legacy herb search, then the ingredient table when unambiguous."""
    clean_query = (query or "").strip()
    clean_latin = (latin_name or "").strip()
    payload: Dict[str, Any] = {
        "source": SOURCE,
        "queried_at": queried_at,
        "query": clean_query,
        "latin_name": clean_latin or None,
        "ob_dl_filter": bool(ob_dl_filter),
        "retrieved_from": [],
        "herbs": [],
        "ingredients": [],
        "ingredients_parsed": 0,
        "ingredients_matched": 0,
        "ob_dl_dropped": 0,
        "note": NOTE,
        "error": None,
    }
    if not clean_query:
        return _fail(payload, "查询是空的")

    status, home, err = await request_with_retry(transport, HOME_URL, None)
    payload["retrieved_from"].append(HOME_URL)
    if err or status != 200:
        return _fail(payload, err or f"HTTP {status}: " + clip_text(home, 300))
    try:
        token = extract_token(home)
    except TcmspParseError as exc:
        return _fail(payload, str(exc))

    herb_url = herb_search_url(token, clean_query)
    status, herb_html, err = await request_with_retry(transport, herb_url, HOME_URL)
    payload["retrieved_from"].append(herb_url)
    if err or status != 200:
        return _fail(payload, err or f"HTTP {status}: " + clip_text(herb_html, 300))
    try:
        herbs = extract_grid(herb_html)
    except TcmspParseError as exc:
        return _fail(payload, str(exc))
    payload["herbs"] = herbs

    chosen = clean_latin
    if not chosen:
        if len(herbs) == 1:
            chosen = str(herbs[0].get("herb_en_name") or "").strip()
            if not chosen:
                payload["herbs"] = herbs
                return _fail(payload, "药材行没有 herb_en_name", keep_herbs=True)
        elif len(herbs) == 0:
            payload["note"] = NOTE + " 这次药材表是空的。"
            return payload
        else:
            payload["note"] = (
                NOTE + " 药材名对上了多行。把 latin_name 设成其中一行的 "
                "herb_en_name 后再取成分。这次没有编造成分。"
            )
            return payload

    ing_url = ingredient_url(token, chosen)
    status, ing_html, err = await request_with_retry(transport, ing_url, herb_url)
    payload["retrieved_from"].append(ing_url)
    payload["latin_name"] = chosen
    if err or status != 200:
        payload["herbs"] = herbs
        return _fail(
            payload,
            err or f"HTTP {status}: " + clip_text(ing_html, 300),
            keep_herbs=True,
        )
    try:
        rows = extract_grid(ing_html)
    except TcmspParseError as exc:
        payload["herbs"] = herbs
        return _fail(payload, str(exc), keep_herbs=True)

    payload["ingredients_parsed"] = len(rows)
    if ob_dl_filter:
        kept, dropped = apply_ob_dl(rows)
    else:
        kept, dropped = rows, 0
    payload["ob_dl_dropped"] = dropped
    payload["ingredients_matched"] = len(kept)
    payload["ingredients"] = kept[: max(0, limit)]
    payload["herbs"] = herbs
    return payload
