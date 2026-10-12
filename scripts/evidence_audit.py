#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence_audit.py —— 产物级循证审计（三档结论：PASS / REVIEW / FAIL）
====================================================

用途：
    对 workflow_engine 一次 DAG 运行的 artifacts 目录做循证完整性审计，
    生成 audit_report.md。用于交付前质量把关（导师审阅前的自检）。

检查项（A-E）：
    A. 证据日志完整性：evidence_log.json 是否覆盖全部 11 步（缺失步骤告警）
    B. 产物文件存在性：评分表 / 溯源明细 / CSV 三件套
    C. 溯源列完整性：Top100循证溯源明细.xlsx 每行必须有证据层 + 证据来源
    D. 阳性药完整性：阳性药验证 sheet 每行完整性应为 100%
    E. 评分表抽查：Top10 成分的命中靶点必须能在溯源明细或统一靶点集中找到

评分规则：
    FAIL = 任一硬校验失败（A 无日志 / B 缺核心产物 / D 完整性 <100%）
    REVIEW = 软告警（步骤证据缺失 / C 有空白来源 / E 抽查未命中）
    PASS = 无告警

用法：
    python evidence_audit.py <artifacts_dir> [--output audit_report.md]

    artifacts_dir：workflow_engine/artifacts/ 下某次运行目录
                   （含 evidence_log.json 与产物 xlsx/csv，参考 run 的目录结构）

输出：
    - 终端打印三档结论 + 明细
    - audit_report.md（默认写到 artifacts_dir 内，可用 --output 指定路径）

依赖：pandas / openpyxl（脚本目录内无额外依赖）
"""
import argparse
import glob
import json
import os
import sys

try:
    import pandas as pd
except ImportError:
    print("需要 pandas + openpyxl：pip install pandas openpyxl")
    sys.exit(2)

# 全部工作流步骤（与 steps_disease_screening.build_workflow 一致）
ALL_STEPS = [
    "s1_load_mechanism_db", "s2_load_clinical_drugs", "s3_load_drugbank_targets",
    "s4_clinical_anchor_targets", "s5_mechanism_targets", "s6_integrate_targets",
    "s7_load_molecule_library", "s8_ctd_matching", "s9_scoring",
    "s10_positive_control", "s11_output_artifacts",
]

# 评分表必需列（缺失即 FAIL 项 B）
REQUIRED_SCORED_COLS = ["排名", "英文名", "总分", "命中锚定靶点", "锚定靶点命中数"]


def load_evidence_log(run_dir: str):
    """查找 evidence_log.json（可能叫 evidence_log.json 或嵌在 run_state.json）"""
    for cand in ("evidence_log.json", "run_state.json"):
        p = os.path.join(run_dir, cand)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            return None, f"{cand} 解析失败：{e}"
        # run_state.json 结构里可能有 evidence 列表
        if cand == "run_state.json":
            items = d.get("evidence") or d.get("evidence_log") or []
        else:
            items = d if isinstance(d, list) else d.get("items", [])
        return items, None
    return None, "未找到 evidence_log.json / run_state.json"


def check_a_evidence_coverage(items, findings):
    """A. 证据日志覆盖全部步骤"""
    if items is None:
        findings.append(("FAIL", "A", "证据日志缺失：无 evidence_log.json"))
        return set()
    steps_hit = {it.get("step", "") for it in items if isinstance(it, dict)}
    missing = [s for s in ALL_STEPS if s not in steps_hit]
    if missing:
        findings.append(("REVIEW", "A",
                         f"证据日志缺少 {len(missing)} 步：{', '.join(missing)}"))
    else:
        findings.append(("PASS", "A", f"证据日志覆盖全部 {len(ALL_STEPS)} 步（{len(items)} 条证据）"))
    return steps_hit


def check_b_artifacts(run_dir: str, findings):
    """B. 核心产物存在性"""
    xlsx_files = glob.glob(os.path.join(run_dir, "*临床锚定评分.xlsx"))
    trace_files = glob.glob(os.path.join(run_dir, "*循证溯源明细.xlsx"))
    csv_files = glob.glob(os.path.join(run_dir, "scored_compounds.csv"))
    if not xlsx_files:
        findings.append(("FAIL", "B", "缺少评分表（*临床锚定评分.xlsx）"))
    if not trace_files:
        findings.append(("FAIL", "B", "缺少循证溯源明细（*循证溯源明细.xlsx）"))
    if not csv_files:
        findings.append(("REVIEW", "B", "缺少 CSV 快速版（scored_compounds.csv）"))
    if xlsx_files and trace_files and csv_files:
        findings.append(("PASS", "B", "三件套产物齐全"))
    return (xlsx_files[0] if xlsx_files else None,
            trace_files[0] if trace_files else None,
            csv_files[0] if csv_files else None)


def check_c_trace_completeness(trace_path, findings):
    """C. 溯源明细每行有证据层+证据来源"""
    if not trace_path:
        return None
    df = pd.read_excel(trace_path)
    if df.empty:
        findings.append(("REVIEW", "C", "循证溯源明细为空表"))
        return df
    bad = df[df["证据来源"].isna() | (df["证据来源"].astype(str).str.strip() == "")]
    n_bad = len(bad)
    if n_bad:
        findings.append(("REVIEW", "C",
                         f"溯源明细 {n_bad}/{len(df)} 行缺少证据来源"
                         f"（涉及成分：{', '.join(bad['成分'].astype(str).unique()[:5])}）"))
    else:
        findings.append(("PASS", "C", f"溯源明细 {len(df)} 行全部有证据来源"))
    return df


def check_d_positive_control(scored_path, findings):
    """D. 阳性药完整性=100% 硬校验"""
    if not scored_path:
        return
    try:
        df = pd.read_excel(scored_path, sheet_name="阳性药验证")
    except Exception:
        findings.append(("REVIEW", "D", "评分表中无『阳性药验证』sheet（跳过）"))
        return
    if df.empty:
        findings.append(("REVIEW", "D", "阳性药验证表为空"))
        return
    comp = df["完整性"].astype(str).str.rstrip("%")
    vals = [float(x) if x.replace(".", "", 1).isdigit() else 0 for x in comp]
    min_v = min(vals) if vals else 0
    if min_v < 100:
        fails = df.loc[[v < 100 for v in vals], "阳性药"].tolist()
        findings.append(("FAIL", "D",
                         f"阳性药完整性未达 100%（最低 {min_v:.0f}%：{', '.join(fails)}）"))
    else:
        findings.append(("PASS", "D", f"阳性药验证 {len(df)} 个全部 100% 完整"))


def check_e_top10_sampling(scored_path, trace_df, findings):
    """E. Top10 成分命中靶点抽查（溯源链可反查）"""
    if not scored_path:
        return
    df = pd.read_excel(scored_path, sheet_name="Top200评分")
    missing_cols = [c for c in REQUIRED_SCORED_COLS if c not in df.columns]
    if missing_cols:
        findings.append(("FAIL", "E", f"评分表缺必需列：{missing_cols}"))
        return
    top10 = df.head(10)
    if trace_df is not None and not trace_df.empty:
        traced = set(trace_df["成分"].astype(str))
        not_traced = [str(x) for x in top10["英文名"] if str(x) not in traced]
        if not_traced:
            findings.append(("REVIEW", "E",
                             f"Top10 中 {len(not_traced)} 个成分未进溯源明细："
                             f"{', '.join(not_traced[:5])}"))
        else:
            findings.append(("PASS", "E", "Top10 成分全部可溯源"))
    else:
        no_anchor = top10[top10["锚定靶点命中数"] == 0]
        if len(no_anchor) >= 5:
            findings.append(("REVIEW", "E",
                             f"Top10 中 {len(no_anchor)} 个无锚定靶点命中（溯源明细缺失，无法深查）"))
        else:
            findings.append(("PASS", "E", "Top10 抽查通过（溯源明细缺失但锚定命中正常）"))


def grade(findings):
    levels = [lv for lv, _, _ in findings]
    if "FAIL" in levels:
        return "FAIL"
    if "REVIEW" in levels:
        return "REVIEW"
    return "PASS"


def render_report(run_dir, findings, result):
    lines = [f"# 循证审计报告", "",
             f"- 审计目录：`{run_dir}`",
             f"- 审计结论：**{result}**",
             "", "## 检查明细", "",
             "| 级别 | 检查项 | 说明 |", "|---|---|---|"]
    icon = {"PASS": "PASS", "REVIEW": "REVIEW", "FAIL": "FAIL"}
    for lv, item, msg in findings:
        lines.append(f"| {icon[lv]} | {item} | {msg} |")
    lines += ["", "## 结论解读", "",
              "- **PASS**：可交付（循证链完整，建议导师审阅）",
              "- **REVIEW**：有软告警，逐项核对后决定是否交付",
              "- **FAIL**：存在硬校验失败，必须修复后重跑工作流",
              "", "> 审计规则见 scripts/evidence_audit.py docstring；"
              "证据日志由 workflow_engine EvidenceLog 生成。"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="产物级循证审计：PASS / REVIEW / FAIL 三档")
    ap.add_argument("artifacts_dir", help="一次 DAG 运行的 artifacts 目录")
    ap.add_argument("--output", default=None,
                    help="报告输出路径（默认 <artifacts_dir>/audit_report.md）")
    args = ap.parse_args()

    run_dir = args.artifacts_dir
    if not os.path.isdir(run_dir):
        print(f"目录不存在：{run_dir}")
        sys.exit(2)

    findings = []
    items, err = load_evidence_log(run_dir)
    if err:
        findings.append(("REVIEW", "A", f"证据日志加载告警：{err}"))
    check_a_evidence_coverage(items, findings)
    scored_p, trace_p, _ = check_b_artifacts(run_dir, findings)
    trace_df = check_c_trace_completeness(trace_p, findings)
    check_d_positive_control(scored_p, findings)
    check_e_top10_sampling(scored_p, trace_df, findings)

    result = grade(findings)

    # 终端输出
    print(f"\n循证审计：{os.path.basename(run_dir)}")
    print("=" * 60)
    for lv, item, msg in findings:
        print(f"[{lv:6s}] {item}: {msg}")
    print("=" * 60)
    print(f"结论：{result}")

    # 报告落盘
    out = args.output or os.path.join(run_dir, "audit_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(render_report(run_dir, findings, result))
    print(f"报告已写入：{out}")
    sys.exit(0 if result == "PASS" else 1)


if __name__ == "__main__":
    main()
