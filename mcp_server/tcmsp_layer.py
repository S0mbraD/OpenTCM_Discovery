#!/usr/bin/env python3
"""tcmsp_layer: one live query against the TCMSP legacy host.

2026-10-13 probe: https://old.tcmsp-e.com/tcmsp.php still returns a token,
and tcmspsearch.php still embeds a JSON ingredient grid with ob and dl.
www.tcmsp-e.com is a portal. tcmspw.com did not complete TLS.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase

from pydantic import BaseModel, ConfigDict, Field

from tcm_core.tcmsp import format_result, search_tcmsp

mcp = _MCPBase("tcm_tcmsp_layer")

_READ = {
    "title": "TCMSP legacy herb and ingredient search",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


class TcmspSearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(
        ..., min_length=1, max_length=80,
        description="药材中文名、拼音或 TCMSP 用的拉丁名，例如甘草",
    )
    latin_name: Optional[str] = Field(
        None, max_length=80,
        description="多行药材时指定 herb_en_name，例如 licorice。留空且只命中一行时自动用那一行",
    )
    ob_dl_filter: bool = Field(
        False,
        description="为真时只保留本页 ob≥30 且 dl≥0.18 的行。不要用这道门去筛其他库",
    )
    limit: int = Field(30, ge=1, le=100, description="返回的成分行上限。总数仍写在 ingredients_matched")


@mcp.tool(name="tcm_tcmsp_search", annotations=_READ)
async def tcm_tcmsp_search(params: TcmspSearchInput) -> str:
    """从 TCMSP 旧站读取一味药的成分表。

    先打开 https://old.tcmsp-e.com/tcmsp.php 取 token，再用
    tcmspsearch.php 的 herb_all_name 找药材。只命中一行，或调用方
    给了 latin_name 时，才继续请求 qsr=herb_en_name 的成分表。
    每次请求超时 20 秒。超时或 HTTP 5xx 再试一次。失败时不补记录。

    OB≥30、DL≥0.18 只在 ob_dl_filter 打开时作用于本页的 ob、dl。
    成分表不是疗效，也不是靶点收录。

    Args:
        params (TcmspSearchInput): 药材名，可选拉丁名和 OB/DL 开关。

    Returns:
        str: JSON。成功时含 source、queried_at、药材行和成分行。
    """
    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": "open-tcm-discovery"},
        ) as client:
            async def transport(url: str, referer: Optional[str]):
                headers = {"Referer": referer} if referer else {}
                response = await client.get(url, headers=headers)
                return response.status_code, response.text[:2_000_000]

            payload = await search_tcmsp(
                query=params.query,
                latin_name=params.latin_name,
                ob_dl_filter=params.ob_dl_filter,
                limit=params.limit,
                transport=transport,
                queried_at=_now_iso(),
            )
    except Exception as exc:
        payload = {
            "source": "TCMSP legacy https://old.tcmsp-e.com/tcmspsearch.php",
            "queried_at": _now_iso(),
            "query": params.query,
            "latin_name": params.latin_name,
            "ob_dl_filter": params.ob_dl_filter,
            "retrieved_from": [],
            "herbs": [],
            "ingredients": [],
            "ingredients_parsed": 0,
            "ingredients_matched": 0,
            "ob_dl_dropped": 0,
            "note": "请求没有完成，下面没有补出来的成分。",
            "error": type(exc).__name__ + ": " + str(exc)[:400],
        }
    return format_result(payload)
