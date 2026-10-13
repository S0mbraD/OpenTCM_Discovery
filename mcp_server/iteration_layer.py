#!/usr/bin/env python3
"""iteration_layer: one tool that lets the connected agent close a run.

tcm_self_iterate reviews local telemetry and confirmed insights, then
appends a lesson only for ids the user named. Review itself writes nothing.
"""
import os
import sys
from pathlib import Path
from typing import List

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tcm_core.iteration import (  # noqa: E402
    apply_lessons,
    build_proposals,
    channel_rows,
    read_json,
    read_jsonl,
    reject_proposals,
    repro_flags,
    review_stores,
    script_rows,
)

try:
    from mcp.server.fastmcp import FastMCP as _MCPBase
except (ImportError, ModuleNotFoundError):
    from mcp.server.mcpserver import MCPServer as _MCPBase

from pydantic import BaseModel, ConfigDict, Field

mcp = _MCPBase("tcm_iteration_layer")


def _state_dirs():
    home = Path.home()
    tcm = Path(os.environ.get("TCM_MCP_STATE_DIR", str(home / ".tcm_mcp_state")))
    dock = Path(os.environ.get("DOCK_MCP_STATE_DIR", str(home / ".dock_mcp_state")))
    return tcm, dock


def _current_proposals(max_events: int = 5000):
    tcm, dock = _state_dirs()
    channel = channel_rows(read_jsonl(tcm / "call_log.jsonl", max_events))
    insights = read_json(tcm / "insights.json", {"insights": []}).get("insights") or []
    dock_insights = read_json(dock / "insights.json", {"insights": []}).get("insights") or []
    scripts = script_rows(read_jsonl(dock / "call_log.jsonl", max_events))
    experiments = read_jsonl(dock / "experiments.jsonl", max_events)
    proposals = build_proposals(
        channel, list(insights) + list(dock_insights), scripts,
        repro_flags(experiments))
    return tcm, proposals


class SelfIterateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(
        "review",
        description="review | apply | reject. review writes nothing. "
                    "apply and reject act only on confirm_ids.")
    confirm_ids: List[str] = Field(
        default_factory=list,
        max_length=20,
        description="ids the user explicitly named. Empty for review.")
    repo_root: str = Field(
        "",
        description="skill root containing SKILL.md. Empty uses this repository.")


@mcp.tool(
    name="tcm_self_iterate",
    annotations={"title": "Review or record a confirmed runtime lesson",
                 "readOnlyHint": False, "destructiveHint": False,
                 "idempotentHint": False, "openWorldHint": False})
async def tcm_self_iterate(params: SelfIterateInput) -> str:
    '''Close the self-iteration loop with the connected agent.

    action=review reads local telemetry and insight stores and returns open
    proposals. It does not edit the skill. action=apply appends one section
    per user-named id to references/runtime_lessons.md. action=reject records
    those ids so they stop appearing. Neither action changes tool code,
    OB/DL thresholds, pocket rules, statistics, or database routing.

    Args:
        params (SelfIterateInput): action, optional confirm_ids, optional
        repo_root.

    Returns:
        str: the open proposals, or the ids written or rejected.
    '''
    action = params.action.strip().lower()
    if action not in {"review", "apply", "reject"}:
        return "Error: action must be review, apply, or reject."
    try:
        tcm, dock = _state_dirs()
        if action == "review":
            packet = review_stores(tcm, dock)
            lines = ["# 自迭代复核", "",
                     f"- 待确认：{len(packet['open'])}",
                     f"- 其中强建议：{packet['strong_count']}",
                     ""]
            if not packet["open"]:
                lines.append("没有新的待确认教训。不要改技能文件。")
            for item in packet["open"]:
                lines.append(f"## `{item['id']}` [{item['strength']}]")
                lines.append(f"- {item['title']}")
                lines.append(f"- 证据：{item['evidence']}")
                lines.append(f"- 观察：{item['detail']}")
                lines.append(f"- {item['does_not']}")
                lines.append("")
            lines.append("只把用户点名的 id 用于 apply 或 reject。")
            return "\n".join(lines)
        tcm, proposals = _current_proposals()
        root = Path(params.repo_root) if params.repo_root.strip() else _REPO_ROOT
        if action == "apply":
            result = apply_lessons(root, tcm, proposals, params.confirm_ids)
        else:
            result = reject_proposals(tcm, proposals, params.confirm_ids)
        lines = [f"# 自迭代{action}", ""]
        for key in ("action", "written", "rejected", "skipped", "unknown",
                    "reason", "target_file"):
            if key in result:
                lines.append(f"- {key}: {result[key]}")
        lines.append("")
        lines.append("写入的内容只是运行观察，不改变工具行为。")
        return "\n".join(lines)
    except ValueError as exc:
        return f"Error: {exc}"
    except OSError as exc:
        return f"Error: {type(exc).__name__}: {exc}"
