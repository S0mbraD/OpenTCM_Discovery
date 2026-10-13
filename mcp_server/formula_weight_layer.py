#!/usr/bin/env python3
"""formula_weight_layer: gram total for one formula under one dose convention.

One tool. It calls formula_weight only. It does not enter the tcm_smart_run
chain, does not merge metrologies, and does not read the herb index. A weight
ratio is a share of the total grams.
"""
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tcm_core.formula_weight import formula_weight
from tcm_core.prep import PrepError

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase

from pydantic import BaseModel, ConfigDict, Field

mcp = _MCPBase("tcm_formula_weight_layer")

_READ = {
    "title": "Sum one formula in grams under a named dose convention",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


class FormulaWeightInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[dict[str, Any]] = Field(
        ...,
        description="药材行。每行要有药材名和剂量。炮制可省略，省略就是没有，不会填成生。",
    )
    convention: str = Field(
        ...,
        max_length=40,
        description="必填。metric、market_1959、han_tang、song_yuan_2025、"
                    "ming_qing_2025、song_jin_yuan_2024、ming_qing_2024。"
                    "一次只接受一个口径，不会把各套两并成一个。",
    )


def _show(title: str, data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2)
    note = data.get("note") or ""
    return f"# {title}\n\n{note}\n\n```json\n{body}\n```"


@mcp.tool(name="tcm_formula_weight", annotations=_READ)
async def tcm_formula_weight(params: FormulaWeightInput) -> str:
    """按点名的一个口径，把方剂每一行剂量换成克后合计。

    只调用 formula_weight。口径必填。不写就报错，也不会替你选一套「两」。
    同一张方里不能混用公制和传统单位。炮制按调用方写下的原文保留，没写就是
    空，不会填成生。重量比是该行克数占全方合计的比例。重量比不是疗效，不是
    暴露量，也不是君臣佐使。不进入自动链。集合重叠仍不读剂量。

    Args:
        params (FormulaWeightInput): 药材行，以及必填的口径名。

    Returns:
        str: 每行克数、全方合计、重量比和炮制原文。正文保留「重量比不是疗效」。
    """
    try:
        data = formula_weight(params.rows, params.convention)
    except PrepError as exc:
        return f"Error: {exc}"
    return _show("方剂克重", data)
