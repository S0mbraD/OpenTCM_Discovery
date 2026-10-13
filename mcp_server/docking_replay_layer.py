#!/usr/bin/env python3
"""docking_replay_layer: offline gate before a literature-pocket replay.

One tool. It calls check_docking_replay only. It does not run docking, does
not download structures, and does not enter the tcm_smart_run chain. A passing
row is only ready to enter a replay. The fixture is not a checked public complex.
"""
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tcm_core.docking_replay import check_docking_replay

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase

from pydantic import BaseModel, ConfigDict, Field

mcp = _MCPBase("tcm_docking_replay_layer")

_READ = {
    "title": "Check literature-pocket replay rows without running docking",
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


class CheckDockingReplayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[dict[str, Any]] = Field(
        ...,
        description="复现条目。每条要有 PMID 或 DOI，以及文献写明的残基编号。",
    )
    note: str | None = Field(
        None,
        max_length=2000,
        description="调用方自己的说明。可省略。",
    )
    verified_public_complexes: bool | None = Field(
        None,
        description="若写成 true，检查仍不会把它当成已核对的公开复合物。",
    )


def _argument(params: CheckDockingReplayInput):
    if params.note is None and params.verified_public_complexes is None:
        return params.entries
    body: dict[str, Any] = {"entries": params.entries}
    if params.note is not None:
        body["note"] = params.note
    if params.verified_public_complexes is not None:
        body["verified_public_complexes"] = params.verified_public_complexes
    return body


def _show(title: str, data: dict) -> str:
    body = json.dumps(data, ensure_ascii=False, indent=2)
    head = "\n\n".join(
        part for part in (data.get("statement"), data.get("note")) if part
    )
    return f"# {title}\n\n{head}\n\n```json\n{body}\n```"


@mcp.tool(name="tcm_check_docking_replay", annotations=_READ)
async def tcm_check_docking_replay(params: CheckDockingReplayInput) -> str:
    """检查调用方给出的对接复现条目。不运行对接，不下载结构。

    只调用 check_docking_replay。缺文献标识、缺口袋残基，或残基不是编号，
    就拒绝。通过的条目标成可以进入对接复现，结果将是提议的对接模型。
    夹具不是已核对的公开复合物。通过检查只表示可以进入复现。不生成姿势，
    不复现实验亲和力，也不做全蛋白盲对接。

    Args:
        params (CheckDockingReplayInput): 条目列表。可选说明，以及是否声称已核对。

    Returns:
        str: 每条是否可以进入复现。通过的条目仍是提议的对接模型。
    """
    try:
        data = check_docking_replay(_argument(params))
    except ValueError as exc:
        return f"Error: {exc}"
    return _show("对接复现门槛", data)
