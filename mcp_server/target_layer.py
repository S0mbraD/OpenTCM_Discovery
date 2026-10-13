#!/usr/bin/env python3
"""target_layer: local read of a caller-supplied compound–gene table.

One tool. It does not call the network, does not ship a target database,
and does not enter the tcm_smart_run chain. Predicted rows stay out of the
recorded source count.
"""
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tcm_core.target_table import TargetTableError, read_target_table

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase

from pydantic import BaseModel, ConfigDict, Field

mcp = _MCPBase("tcm_target_layer")

_READ = {
    "title": "Read a local target table into recorded and predicted rows",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


class ReadTargetTableInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    table_text: str = Field(
        ..., min_length=1, max_length=100000,
        description="调用方自备的靶点表。列要有 compound_id、gene、support、source",
    )


@mcp.tool(name="tcm_read_target_table", annotations=_READ)
async def tcm_read_target_table(params: ReadTargetTableInput) -> str:
    """按行读入调用方已经给出的靶点表，把已记录和预测分开。

    不请求靶点库，也不内置整份靶点库。support 只认 recorded 和 predicted。
    recorded_source_count 只数来源写明一个名称的 recorded 行。预测行单独列出，
    不并进这个计数。缺列或空表会报错。含糊来源放进 unclassified，不猜。
    结果不是在线多库共识，也不是疗效。

    Args:
        params (ReadTargetTableInput): 调用方粘贴的表或 JSON。

    Returns:
        str: JSON。含 recorded_pairs、predicted_pairs，以及一句限制说明。
    """
    try:
        data = read_target_table(params.table_text)
    except TargetTableError as exc:
        return f"Error: {exc}"
    return json.dumps(data, ensure_ascii=False, indent=2)
