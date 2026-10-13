#!/usr/bin/env python3
"""topology_layer: redraw the live analysis network for the connected agent.

The picture is built from the session checkpoint. It shows which library,
literature source, or formula index has been linked, and which steps are
still pending. It does not start new database calls.
"""
import json
import os
import re
from pathlib import Path

import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tcm_core.analysis_graph import build_from_session  # noqa: E402

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase

from pydantic import BaseModel, ConfigDict, Field

mcp = _MCPBase("tcm_topology_layer")


def _sessions_dir() -> Path:
    root = Path(os.environ.get(
        "TCM_MCP_STATE_DIR", str(Path.home() / ".tcm_mcp_state")))
    return root / "sessions"


class AnalysisTopologyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(..., min_length=4, max_length=80,
                            description="tcm_smart_run session id")
    write_svg: bool = Field(
        True, description="rewrite the session SVG from the checkpoint")


@mcp.tool(
    name="tcm_analysis_topology",
    annotations={"title": "Draw the live analysis-network topology",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False})
async def tcm_analysis_topology(params: AnalysisTopologyInput) -> str:
    '''Redraw the analysis network for one smart-run session.

    Nodes are the disease, pathways, genes, compounds, herbs, formula
    set, literature, trials, and the libraries that supplied them. A
    second strip shows whether each tool step is done, failed, or
    skipped. Pending tasks are the frontier: local formula overlap runs
    when at least two herbs are already on the graph.

    The SVG and Mermaid views are a map of how the network was built.
    Co-occurrence, set overlap, trial listings, and consensus counts stay
    labelled as retrieval observations.

    Args:
        params (AnalysisTopologyInput): session_id and whether to write
        the SVG beside the session file.

    Returns:
        str: counts, pending tasks, a Mermaid graph, and the SVG path.
    '''
    if not re.fullmatch(r"[A-Za-z0-9_-]+", params.session_id):
        return "Error: session_id 只能包含字母、数字、下划线和连字符。"
    path = _sessions_dir() / f"{params.session_id}.json"
    if not path.is_file():
        return (f"Error: session '{params.session_id}' 不存在。"
                "先运行 tcm_smart_run。")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"Error: {type(exc).__name__}: {exc}"
    graph = build_from_session(state)
    svg_path = path.with_name(f"{params.session_id}.topology.svg")
    if params.write_svg:
        try:
            svg_path.write_text(graph.to_svg(), encoding="utf-8")
            state["analysis_graph"] = graph.to_dict()
            state["topology_svg"] = str(svg_path)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1,
                                      default=str), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            return f"Error: {type(exc).__name__}: {exc}"
    lines = ["# 分析网络拓扑", ""]
    lines.extend(graph.summary_lines())
    lines.append(f"- svg: `{svg_path}`")
    lines.append("")
    lines.append("```mermaid")
    lines.append(graph.to_mermaid())
    lines.append("```")
    return "\n".join(lines)
