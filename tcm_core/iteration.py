"""Build, accept, and reject skill lessons from local runtime evidence.

Lessons are observations. Accepting one appends a dated note to
references/runtime_lessons.md. It does not change tool code, evidence
thresholds, pocket rules, or which database is called.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DEAD_MIN_CALLS = 5
DEAD_MAX_OK_RATE = 0.5
SLOW_MIN_CALLS = 3
SLOW_AVG_MS = 15000
SCRIPT_MIN_RUNS = 3
SCRIPT_MAX_OK_RATE = 0.5
REPRO_MIN_REPEATS = 2
REPRO_SPREAD = 0.1
STRONG_HITS = 2
LESSON_RELATIVE = Path("references") / "runtime_lessons.md"
LESSON_HEADER = (
    "# 运行中确认的教训\n\n"
    "本文件只收录用户确认后的运行观察。每一条都是本地遥测或洞察的记录，"
    "不改变工具代码、OB/DL 门槛、口袋规则、统计函数或数据源路由，"
    "也不构成疗效、靶点或结合位点结论。\n\n"
    "智能体在任务开始时阅读最近条目。若某次失败与条目相符，向用户说明该观察，"
    "再决定是否改用文档中已有的替代库。\n"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def read_jsonl(path: Path, max_events: int) -> List[dict]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-max_events:]:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip())[:80]
    return cleaned or "unknown"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def channel_rows(events: Iterable[dict]) -> List[dict]:
    per_host: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if ev.get("type") != "db_call":
            continue
        host = str(ev.get("host") or "?")
        row = per_host.setdefault(host, {"calls": 0, "ok": 0, "ms_total": 0.0})
        row["calls"] += 1
        row["ms_total"] += float(ev.get("ms") or 0)
        if ev.get("ok"):
            row["ok"] += 1
    out = []
    for host, row in sorted(per_host.items(), key=lambda kv: -kv[1]["calls"]):
        calls = row["calls"]
        out.append({
            "host": host,
            "calls": calls,
            "ok_rate": round(row["ok"] / calls, 3) if calls else 0.0,
            "avg_ms": round(row["ms_total"] / calls) if calls else 0,
        })
    return out


def script_rows(events: Iterable[dict]) -> List[dict]:
    per_script: Dict[str, Dict[str, Any]] = {}
    for ev in events:
        if ev.get("type") != "script":
            continue
        name = str(ev.get("script") or "?")
        row = per_script.setdefault(name, {"runs": 0, "ok": 0})
        row["runs"] += 1
        if ev.get("ok"):
            row["ok"] += 1
    out = []
    for name, row in sorted(per_script.items(), key=lambda kv: -kv[1]["runs"]):
        runs = row["runs"]
        out.append({
            "script": name,
            "runs": runs,
            "ok_rate": round(row["ok"] / runs, 3) if runs else 0.0,
        })
    return out


def repro_flags(experiments: Iterable[dict]) -> List[dict]:
    grouped: Dict[tuple, List[float]] = {}
    for ex in experiments:
        if ex.get("type") != "vina_docking":
            continue
        if ex.get("best_affinity_kcal_mol") is None or ex.get("seed") is None:
            continue
        key = (str(ex.get("ligand")), str(ex.get("receptor")), str(ex.get("seed")))
        grouped.setdefault(key, []).append(float(ex["best_affinity_kcal_mol"]))
    flags = []
    for (ligand, receptor, seed), values in grouped.items():
        if len(values) < REPRO_MIN_REPEATS:
            continue
        spread = round(max(values) - min(values), 3)
        if spread > REPRO_SPREAD:
            flags.append({
                "ligand": ligand,
                "receptor": receptor,
                "seed": seed,
                "spread": spread,
                "n": len(values),
            })
    return flags


def _proposal(pid: str, kind: str, strength: str, title: str,
              detail: str, evidence: str) -> dict:
    return {
        "id": pid,
        "kind": kind,
        "strength": strength,
        "title": title,
        "detail": detail,
        "evidence": evidence,
        "target_file": LESSON_RELATIVE.as_posix(),
        "effect": "append_lesson",
        "requires_confirmation": True,
        "does_not": (
            "不修改工具代码、OB/DL 门槛、口袋规则、统计函数或数据源路由"
        ),
    }


def build_proposals(channel: List[dict], insights: List[dict],
                    scripts: List[dict], repro: List[dict]) -> List[dict]:
    proposals = []
    for row in channel:
        if row["calls"] >= DEAD_MIN_CALLS and row["ok_rate"] <= DEAD_MAX_OK_RATE:
            host = row["host"]
            proposals.append(_proposal(
                f"telemetry:dead_end:{_slug(host)}",
                "dead_end_candidate",
                "strong",
                f"{host} 最近调用成功率偏低",
                "该主机达到死路候选门槛：至少 5 次调用且成功率不超过 50%。"
                "这只说明通道不稳定，不说明相关靶点或成分不存在。",
                f"calls={row['calls']}, ok_rate={row['ok_rate']}",
            ))
        elif row["calls"] >= SLOW_MIN_CALLS and row["avg_ms"] > SLOW_AVG_MS:
            host = row["host"]
            proposals.append(_proposal(
                f"telemetry:latency:{_slug(host)}",
                "latency",
                "watch",
                f"{host} 平均延迟超过 15 秒",
                "慢通道可以写入教训，提醒后续运行预留时间。不要因此关闭该库。",
                f"calls={row['calls']}, avg_ms={row['avg_ms']}",
            ))
    for ins in insights:
        hits = int(ins.get("hits") or 1)
        category = str(ins.get("category") or "quirk")
        strength = "strong" if hits >= STRONG_HITS else "watch"
        title = str(ins.get("title") or "untitled")
        proposals.append(_proposal(
            str(ins.get("id") or f"insight:{_slug(title)}"),
            "insight",
            strength,
            title,
            str(ins.get("detail") or ""),
            (f"category={category}; hits={hits}; "
             f"evidence={ins.get('evidence') or ''}"),
        ))
    for row in scripts:
        if row["runs"] >= SCRIPT_MIN_RUNS and row["ok_rate"] < SCRIPT_MAX_OK_RATE:
            name = row["script"]
            proposals.append(_proposal(
                f"telemetry:script:{_slug(name)}",
                "script_health",
                "strong",
                f"{name} 脚本成功率偏低",
                "先用 dock_check_environment 核对依赖。不要把脚本失败写成对接科学结论。",
                f"runs={row['runs']}, ok_rate={row['ok_rate']}",
            ))
    for flag in repro:
        proposals.append(_proposal(
            "telemetry:repro:" + _slug(
                f"{flag['ligand']}|{flag['receptor']}|{flag['seed']}"),
            "reproducibility",
            "strong",
            (f"{flag['ligand']} / {flag['receptor']} "
             f"seed {flag['seed']} 亲和能漂移"),
            "同一种子的亲和能差超过 0.1 kcal/mol。先检查受体准备和盒子是否一致，"
            "不要把漂移最大的数值当作更可靠的结合能。",
            f"n={flag['n']}, spread={flag['spread']} kcal/mol",
        ))
    return proposals


def load_ledger(path: Path) -> dict:
    data = read_json(path, {"applied": {}, "rejected": {}})
    data.setdefault("applied", {})
    data.setdefault("rejected", {})
    return data


def open_proposals(proposals: List[dict], ledger: dict) -> List[dict]:
    closed = set(ledger.get("applied", {})) | set(ledger.get("rejected", {}))
    return [item for item in proposals if item["id"] not in closed]


def review_stores(tcm_dir: Path, dock_dir: Path, max_events: int = 5000) -> dict:
    channel = channel_rows(read_jsonl(tcm_dir / "call_log.jsonl", max_events))
    insights = read_json(tcm_dir / "insights.json", {"insights": []}).get("insights") or []
    dock_insights = read_json(dock_dir / "insights.json", {"insights": []}).get("insights") or []
    scripts = script_rows(read_jsonl(dock_dir / "call_log.jsonl", max_events))
    experiments = read_jsonl(dock_dir / "experiments.jsonl", max_events)
    proposals = build_proposals(
        channel, list(insights) + list(dock_insights), scripts,
        repro_flags(experiments))
    ledger = load_ledger(tcm_dir / "iteration_ledger.json")
    opened = open_proposals(proposals, ledger)
    return {
        "open": opened,
        "strong_count": sum(1 for item in opened if item["strength"] == "strong"),
        "applied_count": len(ledger.get("applied", {})),
        "rejected_count": len(ledger.get("rejected", {})),
        "agent_protocol": [
            "向用户展示 strength=strong 的 id、标题和证据",
            "只把用户点名的 id 传给 tcm_self_iterate action=apply",
            "用户否定的 id 传给 action=reject",
            "未确认时不要改任何技能文件",
        ],
    }


def review_from_env(max_events: int = 5000) -> dict:
    home = Path.home()
    tcm = Path(os.environ.get("TCM_MCP_STATE_DIR", str(home / ".tcm_mcp_state")))
    dock = Path(os.environ.get("DOCK_MCP_STATE_DIR", str(home / ".dock_mcp_state")))
    try:
        return review_stores(tcm, dock, max_events=max_events)
    except OSError as exc:
        return {"open": [], "strong_count": 0, "error": type(exc).__name__,
                "agent_protocol": ["状态目录不可读，本次不提出技能修改"]}


def _lesson_block(item: dict, accepted_at: str) -> str:
    detail = str(item.get("detail") or "").replace("\r\n", "\n").strip()
    evidence = str(item.get("evidence") or "").replace("\r\n", "\n").strip()
    return (
        f"\n## {item['id']}\n\n"
        f"- 接受时间：{accepted_at}\n"
        f"- 类别：{item.get('kind')}\n"
        f"- 标题：{item.get('title')}\n"
        f"- 证据：{evidence}\n\n"
        "> 本条是运行观察，不是新的科学结论，也不改变工具行为。\n\n"
        f"{detail}\n"
    )


def _skill_root(repo_root: Path) -> Path:
    root = repo_root.resolve()
    if not (root / "SKILL.md").is_file():
        raise ValueError("repo_root 不是技能根目录：缺少 SKILL.md")
    return root


def apply_lessons(repo_root: Path, tcm_dir: Path, proposals: List[dict],
                  confirm_ids: List[str], accepted_at: Optional[str] = None) -> dict:
    if not confirm_ids:
        return {"action": "refused", "written": [],
                "reason": "没有 confirm_ids，不写入教训文件"}
    root = _skill_root(repo_root)
    target = root / LESSON_RELATIVE
    ledger_path = tcm_dir / "iteration_ledger.json"
    ledger = load_ledger(ledger_path)
    by_id = {item["id"]: item for item in proposals}
    written, skipped, unknown = [], [], []
    stamp = accepted_at or now_iso()
    existing = target.read_text(encoding="utf-8") if target.exists() else LESSON_HEADER
    if not existing.endswith("\n"):
        existing += "\n"
    for raw in confirm_ids:
        pid = str(raw).strip()
        if not pid or pid not in by_id:
            unknown.append(pid)
            continue
        if pid in ledger["applied"]:
            skipped.append(pid)
            continue
        existing += _lesson_block(by_id[pid], stamp)
        ledger["applied"][pid] = {"at": stamp, "title": by_id[pid]["title"]}
        ledger["rejected"].pop(pid, None)
        written.append(pid)
    if written:
        _atomic_write(target, existing)
        _atomic_write(ledger_path, json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    return {"action": "applied" if written else "unchanged",
            "written": written, "skipped": skipped, "unknown": unknown,
            "target_file": LESSON_RELATIVE.as_posix()}


def reject_proposals(tcm_dir: Path, proposals: List[dict],
                     confirm_ids: List[str], rejected_at: Optional[str] = None) -> dict:
    if not confirm_ids:
        return {"action": "refused", "rejected": [],
                "reason": "没有 confirm_ids，不记录否定"}
    by_id = {item["id"]: item for item in proposals}
    ledger_path = tcm_dir / "iteration_ledger.json"
    ledger = load_ledger(ledger_path)
    rejected, unknown = [], []
    stamp = rejected_at or now_iso()
    for raw in confirm_ids:
        pid = str(raw).strip()
        if not pid or pid not in by_id:
            unknown.append(pid)
            continue
        if pid in ledger["applied"]:
            continue
        ledger["rejected"][pid] = {"at": stamp, "title": by_id[pid]["title"]}
        rejected.append(pid)
    if rejected:
        _atomic_write(ledger_path, json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    return {"action": "rejected" if rejected else "unchanged",
            "rejected": rejected, "unknown": unknown}
