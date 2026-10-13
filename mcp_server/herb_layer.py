#!/usr/bin/env python3
"""herb_layer: HERB detail by id, and local broad name candidates.

tcm_herb_detail posts one HERB id to the HTTP detail path. HTTPS is an
environment-failure skip, not a second data source. tcm_broad_names only
calls the existing candidate function. Neither tool enters tcm_smart_run.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tcm_core.herb import fetch_herb_detail, format_herb, urllib_post
from tcm_core.name_spectrum import broad_name_candidates

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase

from pydantic import BaseModel, ConfigDict, Field

mcp = _MCPBase("tcm_herb_layer")

_HERB = {
    "title": "Read one HERB detail page by HERB id",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
_NAMES = {
    "title": "List local herb and formula name candidates",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


class HerbDetailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    herb_id: str = Field(
        ..., min_length=5, max_length=20,
        description="HERB 编号，例如 HERB000281",
    )


class BroadNamesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1, max_length=80, description="要列候选的名称")
    kind: str = Field("any", description="herb、formula 或 any")
    limit: int = Field(8, description="只截断差一个字的相近串，1 到 30")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


@mcp.tool(name="tcm_herb_detail", annotations=_HERB)
async def tcm_herb_detail(params: HerbDetailInput) -> str:
    """按 HERB 编号取详情。先 POST HTTP 详情，环境失败再试一次同一条 HTTP。

    HTTPS 留在通路列表里，只在环境失败后跳过，不当成会返回数据的备用。
    门户页和 404 是方法失败，不换成别的主机。成分只返回编号、名称、别名、
    分子式和 SMILES。不请求成分全表。MolWt、Drug_likeness、OB_score 不能
    填进 ob、dl。靶点这一页没有已记录和预测的分列，不能算已记录证据，
    也不并进 tcm_read_target_table 的 recorded 计数。失败时成分和靶点都是
    空列表，并带 error，不补行。

    Args:
        params (HerbDetailInput): HERB 编号。

    Returns:
        str: JSON。含走通的 url、失败过的通路名、成分和靶点。
    """
    data = await fetch_herb_detail(
        params.herb_id,
        transport=urllib_post,
        queried_at=_now_iso(),
    )
    return format_herb(data)


@mcp.tool(name="tcm_broad_names", annotations=_NAMES)
async def tcm_broad_names(params: BroadNamesInput) -> str:
    """列出一味名称的本地候选。只调用已有的名称候选函数。

    不进入 tcm_smart_run。黄连和胡黄连、甘草和炙甘草不能并成一味。
    相近不是疗效。没有候选就不补行。

    Args:
        params (BroadNamesInput): 查询、种类和条数上限。

    Returns:
        str: JSON。含 candidates。picked_herb_id 保持空。
    """
    data = broad_name_candidates(params.query, kind=params.kind, limit=params.limit)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if data.get("error"):
        return "Error: " + text
    return text
