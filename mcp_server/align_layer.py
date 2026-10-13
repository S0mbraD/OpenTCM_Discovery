#!/usr/bin/env python3
"""align_layer: local join of TCMSP ingredient rows and a name/InChIKey table.

One tool. It does not call the network, does not read the herb index, and
does not treat a name hit as a target or an efficacy claim.
"""
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tcm_core.align_tcmsp import AlignError, align_tcmsp

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase

from pydantic import BaseModel, ConfigDict, Field

mcp = _MCPBase("tcm_align_layer")

_READ = {
    "title": "Align TCMSP ingredient names with a local InChIKey table",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


class AlignTcmspInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tcmsp_text: str = Field(
        ..., min_length=1, max_length=100000,
        description="tcm_tcmsp_search 的 JSON，或含 molecule_name、MOL_ID、ob、dl 的表",
    )
    catalog_text: str = Field(
        ..., min_length=1, max_length=100000,
        description="调用方自己的成分名表，可带 InChIKey、MOL_ID 和 local_id",
    )


@mcp.tool(name="tcm_align_tcmsp", annotations=_READ)
async def tcm_align_tcmsp(params: AlignTcmspInput) -> str:
    """把已有的 TCMSP 成分行和调用方的名称、InChIKey 表在本地对齐。

    不请求 old.tcmsp-e.com，也不读 data/herb_mol_index.json。那个索引只有
    SymMap 标识，没有成分名和 InChIKey。全键相同只说明两边字符串一致。
    名称相同不是结构相同。ob、dl 只从 TCMSP 行抄出，不在这里筛选。
    结果不是靶点收录，也不是疗效。

    Args:
        params (AlignTcmspInput): TCMSP 侧文本和目录侧文本。

    Returns:
        str: JSON。含 link、未对上的行，以及一句限制说明。
    """
    try:
        data = align_tcmsp(params.tcmsp_text, params.catalog_text)
    except AlignError as exc:
        return f"Error: {exc}"
    return json.dumps(data, ensure_ascii=False, indent=2)
