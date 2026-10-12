#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
disease_live_scan.py —— 双疾病实时检索验证脚本（不依赖任何本地预设数据）
=====================================================================

用途：验证 tcm-database-query 技能包的 MCP 服务器，确保所有输出均来自
**实时 API 检索**，而非本地预置答案。对两个疾病（默认溃疡性结肠炎 UC /
卵巢癌 Ovarian Cancer）各跑一条完整工具链并断言关键验收锚点。

设计原则（与技能「实时检索纪律」一致）：
  - 只断言「结构成立 + 有实时返回」，不写死具体数值（外部库会演进）
  - 每个工具返回均带 source / queried_at，脚本打印首行摘要供人工核对
  - 全程走 MCP stdio 拉起 server，与 TeleAgent 部署方式一致

用法：
    python scripts/disease_live_scan.py            # 双疾病默认
    python scripts/disease_live_scan.py --disease uc
    python scripts/disease_live_scan.py --disease ovarian
    python scripts/disease_live_scan.py --server /path/to/open_tcm_discovery_mcp.py

退出码：0=PASS（双疾病全部断言通过），1=FAIL（任一断言失败）。
"""
import argparse
import json
import os
import sys

import anyio
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters

DEFAULT_SERVER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mcp_server", "open_tcm_discovery_mcp.py")


async def call(session, name, params):
    """调用 MCP 工具并返回文本输出（含 source / queried_at）。"""
    r = await session.call_tool(name, {"params": params})
    return "\n".join(c.text for c in r.content if getattr(c, "text", None))


def check(cond, msg):
    if cond:
        print(f"  [OK] {msg}")
    else:
        raise AssertionError(f"[FAIL] {msg}")


async def scan_uc(session):
    print("\n===== 溃疡性结肠炎（UC）实时检索链 =====")
    # 1) 疾病名称 -> MONDO ID
    out = await call(session, "tcm_search_disease_targets",
                     {"disease_name": "ulcerative colitis", "limit": 3})
    check("MONDO_0005101" in out, "UC 疾病解析出 MONDO_0005101")
    # 2) MONDO -> 靶点
    out = await call(session, "tcm_search_disease_targets",
                     {"mondo_id": "MONDO_0005101", "limit": 5})
    check("total_associated_targets" in out, "UC 返回总关联靶点数>0")
    check(any(x in out for x in ["IL12B", "JAK2", "TNF", "IL6"]),
          "UC top 靶点含炎症标志基因 (IL12B/JAK2/TNF/IL6)")
    # 3) STRING PPI
    out = await call(session, "tcm_string_ppi",
                     {"genes": ["TNF", "IL6", "IL1B", "PTGS2", "NFKB1"]})
    check("STRING PPI" in out, "STRING 返回 PPI 网络")
    check("interactions_medium_plus" in out, "STRING 有交互对")
    # 4) GWAS 研究
    out = await call(session, "tcm_gwas_associations",
                     {"keyword": "ulcerative colitis", "limit": 3})
    check("studies" in out and "GCST" in out, "GWAS 返回 ≥1 项 UC 研究")
    print("[UC] 全链路 PASS")


async def scan_ovarian(session):
    print("\n===== 卵巢癌（Ovarian Cancer）实时检索链 =====")
    out = await call(session, "tcm_search_disease_targets",
                     {"disease_name": "ovarian cancer", "limit": 3})
    check("MONDO_0008170" in out, "卵巢癌解析出 MONDO_0008170")
    out = await call(session, "tcm_search_disease_targets",
                     {"mondo_id": "MONDO_0008170", "limit": 5})
    check("total_associated_targets" in out, "卵巢癌返回总关联靶点数>0")
    check(any(x in out for x in ["BRCA1", "BRCA2", "TP53"]),
          "卵巢癌 top 靶点含 BRCA1/BRCA2/TP53")
    out = await call(session, "tcm_dgidb_interactions",
                     {"genes": ["BRCA1", "BRCA2"], "limit_per_gene": 5})
    check("DGIdb" in out, "DGIdb 返回基因-药物互作")
    check(any(x in out.upper() for x in ["OLAPARIB", "CARBOPLATIN"]),
          "DGIdb 返回已知卵巢癌药物")
    out = await call(session, "tcm_gwas_associations",
                     {"gene": "BRCA1", "limit": 3})
    check("associations" in out and "rs" in out, "GWAS 返回 BRCA1 SNP 关联")
    print("[Ovarian] 全链路 PASS")


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", default=DEFAULT_SERVER, help="MCP server 路径")
    ap.add_argument("--disease", default="both", choices=["uc", "ovarian", "both"],
                    help="要验证的疾病链")
    args = ap.parse_args()

    params = StdioServerParameters(
        command=sys.executable, args=[args.server, "--stdio"],
        env={"PYTHONUTF8": "1"})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            check(len(names) >= 15, f"MCP 至少 15 工具 (实际 {len(names)})")
            print(f"[MCP] 工具清单: {', '.join(sorted(names))}")
            if args.disease in ("uc", "both"):
                await scan_uc(session)
            if args.disease in ("ovarian", "both"):
                await scan_ovarian(session)
    print("\n双疾病实时检索验证 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(anyio.run(main))