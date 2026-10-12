#!/usr/bin/env python3
"""Minimal example: connect to the open-tcm-discovery MCP server and run one tool.

Usage:
    python examples/basic_usage.py

Expects the repository root as CWD (or adjust SERVER path below).
"""
import asyncio
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PY = "python"
SERVER = Path(__file__).resolve().parent.parent / "mcp_server" / "unified_mcp.py"


async def main() -> None:
    p = StdioServerParameters(
        command=PY,
        args=[str(SERVER), "--stdio"],
        env={"PYTHONUTF8": "1"},
    )
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await asyncio.wait_for(s.initialize(), 120)
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            print(f"tools registered: {len(tools)}")

            # call one lightweight tool
            res = await asyncio.wait_for(
                s.call_tool(
                    "tcm_check_database_status",
                    {"params": {"refresh": False}},
                ),
                timeout=120,
            )
            text = "\n".join(
                x.text for x in res.content if hasattr(x, "text")
            )
            print(text[:500])


if __name__ == "__main__":
    asyncio.run(main())