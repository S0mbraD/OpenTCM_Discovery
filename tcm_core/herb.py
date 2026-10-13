"""HERB detail for one herb id.

The only path that returned a table on 2026-10-13 is
POST http://47.92.70.12/chedi/api/. HTTPS on that host stopped at TLS
eof, so it stays on the list as an environment-failure skip and is not
a data fallback. Portal pages and HTTP 404 are method failures. This
module does not switch hosts, and it does not download the ingredient
file.

Ingredient rows keep the five fields seen on the detail page. MolWt,
Drug_likeness and OB_score are not copied into ob or dl. Target rows
from this page are not recorded evidence.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

HTTP_DETAIL_URL = "http://47.92.70.12/chedi/api/"
HTTPS_DETAIL_URL = "https://47.92.70.12/chedi/api/"
SOURCE = "HERB detail POST http://47.92.70.12/chedi/api/"
TARGET_NOTE = "这一页没有已记录和预测的分列，不能算已记录证据"
NOTE = (
    "成分只保留编号、名称、别名、分子式和 SMILES。"
    "MolWt、Drug_likeness、OB_score 不能填进 TCMSP 的 ob、dl。"
    "类药性不是 ob、dl。"
    "靶点不能并进 tcm_read_target_table 的 recorded 计数。"
    + TARGET_NOTE
)
HTTPS_SKIP_DETAIL = (
    "环境失败：TLS eof。这次 HTTPS 没有跑通，不把它当成会返回数据的备用，"
    "也不把失败响应当成空结果。"
)

PATHS = (
    {"name": "http_detail", "url": HTTP_DETAIL_URL, "mode": "post"},
    {"name": "https_detail", "url": HTTPS_DETAIL_URL, "mode": "skip_environment"},
)

Transport = Callable[[str, Dict[str, Any]], Awaitable[Tuple[Any, str, str]]]

_DETAIL_KEYS = {"herb_ingredient", "herb_target", "summary"}
_INGREDIENT_FIELDS = {
    "ingredient id": "id",
    "ingredient name": "name",
    "ingredient alias name": "alias",
    "molecular formula": "formula",
    "canonical smiles": "smiles",
}
_INGREDIENT_ORDER = ("id", "name", "alias", "formula", "smiles")
_TARGET_FIELDS = {
    "target id": "id",
    "gene symbol": "gene_symbol",
    "gene alias name": "gene_alias",
    "protein name": "protein_name",
    "type of gene": "gene_type",
    "p value": "p_value",
    "fdr bh": "fdr_bh",
}
_TARGET_ORDER = (
    "id", "gene_symbol", "gene_alias", "protein_name", "gene_type", "p_value", "fdr_bh",
)
_SUMMARY_FIELDS = {
    "herb id": "herb_id",
    "herb pinyin name": "pinyin",
    "herb chinese name": "chinese_name",
    "herb alias name": "alias",
    "herb english name": "english_name",
    "herb latin name": "latin_name",
}


class EnvironmentFailure(Exception):
    """TLS eof, connection reset, or timeout. Not an empty data page."""


def clip_text(text: str, limit: int = 300) -> str:
    flat = " ".join(str(text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "..."


def format_herb(payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    if payload.get("error"):
        return "Error: " + body
    return body


def _exception_chain(exc: BaseException):
    pending = [exc]
    seen = set()
    while pending:
        cur = pending.pop()
        if cur is None or id(cur) in seen or not isinstance(cur, BaseException):
            continue
        seen.add(id(cur))
        yield cur
        pending.append(cur.__cause__)
        if cur.__context__ is not cur.__cause__:
            pending.append(cur.__context__)
        reason = getattr(cur, "reason", None)
        if isinstance(reason, BaseException):
            pending.append(reason)


def _is_environment(exc: BaseException) -> bool:
    for cur in _exception_chain(exc):
        if isinstance(cur, EnvironmentFailure):
            return True
        if isinstance(cur, (TimeoutError, ConnectionResetError,
                            ConnectionAbortedError, BrokenPipeError, ssl.SSLError)):
            return True
        name = type(cur).__name__.casefold()
        text = str(cur).casefold()
        if "timeout" in name or "timed out" in text or "timeout" in text:
            return True
        if "unexpected_eof" in text or "ssleoferror" in name or "eof occurred" in text:
            return True
        if "certificate" in text and ("hostname" in text or "match" in text):
            return True
        if ("remotedisconnected" in name or "remote end closed" in text
                or "connection reset" in text
                or "disconnected without sending" in text
                or "server disconnected" in text):
            return True
    return False


def _urllib_post(url: str, body: Dict[str, Any], timeout: float = 15.0):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            status = getattr(resp, "status", 200)
            return status, raw.decode("utf-8", "replace"), ctype
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        if isinstance(raw, str):
            text = raw
        else:
            text = raw.decode("utf-8", "replace")
        ctype = exc.headers.get("Content-Type", "") if exc.headers else ""
        return exc.code, text, ctype
    except Exception as exc:
        if _is_environment(exc):
            raise EnvironmentFailure(clip_text(str(exc))) from exc
        raise


async def urllib_post(url: str, body: Dict[str, Any]):
    """POST one URL. Tests pass their own transport and do not call this."""
    import asyncio
    return await asyncio.to_thread(_urllib_post, url, body, 15.0)


def _cell(value: Any) -> Any:
    if isinstance(value, dict):
        if "title" not in value or value.get("title") is None:
            return ""
        return _cell(value.get("title"))
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def _is_header(row: List[Any], field_map: Dict[str, str]) -> bool:
    if not row or not all(isinstance(cell, str) for cell in row):
        return False
    names = {cell.strip().casefold() for cell in row}
    return bool(names & set(field_map))


def _records(raw: Any, field_map: Dict[str, str], order: Tuple[str, ...]) -> List[dict]:
    if not isinstance(raw, list):
        return []
    rows = [row for row in raw if isinstance(row, list) and row]
    if not rows:
        return []
    if _is_header(rows[0], field_map):
        headers = [str(cell).strip().casefold() for cell in rows[0]]
        index = {}
        for pos, header in enumerate(headers):
            if header in field_map and header not in index:
                index[header] = pos
        data = rows[1:]
        found = []
        for row in data:
            item = {}
            for header, key in field_map.items():
                pos = index.get(header)
                item[key] = _cell(row[pos]) if pos is not None and pos < len(row) else ""
            found.append({key: item[key] for key in order})
        return found
    found = []
    for row in rows:
        if _is_header(row, field_map):
            continue
        item = {}
        for pos, key in enumerate(order):
            item[key] = _cell(row[pos]) if pos < len(row) else ""
        found.append(item)
    return found


def _summary(raw: Any) -> Dict[str, Any]:
    rows = _records(raw, _SUMMARY_FIELDS, tuple(_SUMMARY_FIELDS.values()))
    if not rows:
        return {}
    return rows[0]


def _parse_detail(text: str) -> Tuple[str, Optional[dict], str]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        lowered = (text or "").lstrip().lower()
        if lowered.startswith("<!doctype") or lowered.startswith("<html") or "<table" not in lowered:
            return "method", None, "方法失败：门户或不可解析正文"
        return "method", None, "方法失败：正文不是 JSON"
    if not isinstance(data, dict) or not (_DETAIL_KEYS & set(data)):
        return "method", None, "方法失败：不是详情 JSON"
    return "success", data, ""


def _classify(status: Any, text: str) -> Tuple[str, Optional[dict], str]:
    if status is None:
        return "environment", None, "环境失败：没有状态码"
    try:
        code = int(status)
    except (TypeError, ValueError):
        return "environment", None, "环境失败：没有状态码"
    if code == 404:
        return "method", None, "方法失败：404"
    if code >= 500:
        return "environment", None, f"环境失败：HTTP {code}"
    if code != 200:
        return "method", None, f"方法失败：HTTP {code}"
    return _parse_detail(text or "")


def _blank(herb_id: str, queried_at: str) -> Dict[str, Any]:
    return {
        "source": SOURCE,
        "queried_at": queried_at,
        "herb_id": herb_id,
        "url": None,
        "path": None,
        "attempts": 0,
        "failed_paths": [],
        "content_type": None,
        "summary": {},
        "ingredients": [],
        "targets": [],
        "target_note": TARGET_NOTE,
        "note": NOTE,
        "error": None,
    }


def _fail(payload: Dict[str, Any], message: str, failed: List[dict]) -> Dict[str, Any]:
    payload["error"] = clip_text(message, 400)
    payload["url"] = None
    payload["path"] = None
    payload["summary"] = {}
    payload["ingredients"] = []
    payload["targets"] = []
    payload["failed_paths"] = failed
    payload["content_type"] = None
    return payload


def _normalize_id(herb_id: str) -> Optional[str]:
    text = herb_id.strip().upper() if isinstance(herb_id, str) else ""
    if len(text) < 5 or len(text) > 20 or not text.startswith("HERB") or not text[4:].isdigit():
        return None
    return text


def _request_body(herb_id: str) -> Dict[str, str]:
    return {
        "v": herb_id,
        "label": "Herb",
        "key_id": herb_id,
        "func_name": "detail_api",
    }


async def _post_http(transport: Transport, body: Dict[str, str]) -> dict:
    last = "环境失败"
    attempts = 0
    for _ in (1, 2):
        attempts += 1
        try:
            status, text, ctype = await transport(HTTP_DETAIL_URL, body)
        except Exception as exc:
            if _is_environment(exc):
                last = "环境失败：" + clip_text(str(exc))
                continue
            return {
                "ok": False,
                "kind": "method",
                "attempts": attempts,
                "detail": "方法失败：" + clip_text(str(exc)),
                "content_type": None,
            }
        kind, parsed, detail = _classify(status, text if isinstance(text, str) else str(text or ""))
        if kind == "success":
            return {
                "ok": True,
                "attempts": attempts,
                "data": parsed,
                "content_type": ctype or "",
            }
        if kind == "environment":
            last = detail
            continue
        return {
            "ok": False,
            "kind": "method",
            "attempts": attempts,
            "detail": detail,
            "content_type": ctype or "",
        }
    return {
        "ok": False,
        "kind": "environment",
        "attempts": attempts,
        "detail": last,
        "content_type": None,
    }


def _from_page(payload: Dict[str, Any], data: dict, content_type: str, attempts: int) -> Dict[str, Any]:
    payload["url"] = HTTP_DETAIL_URL
    payload["path"] = "http_detail"
    payload["attempts"] = attempts
    payload["content_type"] = content_type
    payload["failed_paths"] = []
    payload["summary"] = _summary(data.get("summary"))
    payload["ingredients"] = _records(
        data.get("herb_ingredient"), _INGREDIENT_FIELDS, _INGREDIENT_ORDER,
    )
    payload["targets"] = _records(
        data.get("herb_target"), _TARGET_FIELDS, _TARGET_ORDER,
    )
    payload["error"] = None
    return payload


async def fetch_herb_detail(
    herb_id: str,
    *,
    transport: Transport,
    queried_at: str,
) -> Dict[str, Any]:
    """POST the HERB detail API. HTTPS is skipped, not queried."""
    clean = _normalize_id(herb_id)
    payload = _blank(clean or (herb_id.strip() if isinstance(herb_id, str) else ""), queried_at)
    if clean is None:
        return _fail(payload, "HERB 编号要像 HERB000281。", [])
    payload["herb_id"] = clean
    failed: List[dict] = []
    for spec in PATHS:
        if spec["mode"] == "post":
            outcome = await _post_http(transport, _request_body(clean))
            payload["attempts"] = outcome["attempts"]
            if outcome["ok"]:
                return _from_page(
                    payload, outcome["data"], outcome["content_type"], outcome["attempts"],
                )
            failed.append({
                "name": spec["name"],
                "url": spec["url"],
                "kind": outcome["kind"],
                "attempts": outcome["attempts"],
                "detail": outcome["detail"],
            })
            if outcome["kind"] != "environment":
                return _fail(payload, outcome["detail"] + " 不换成别的主机。", failed)
            continue
        failed.append({
            "name": spec["name"],
            "url": spec["url"],
            "kind": "environment",
            "skipped": True,
            "detail": HTTPS_SKIP_DETAIL,
        })
    detail = failed[0]["detail"] if failed else "环境失败"
    return _fail(payload, detail + " " + HTTPS_SKIP_DETAIL, failed)
