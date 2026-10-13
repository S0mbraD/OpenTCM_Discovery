"""Offline gate for literature-pocket docking replay entries.

The caller supplies a complex or target name, an optional PDB id, a PMID or
DOI, residue numbers written in the literature, and a ligand id. This module
does not download structures, does not run docking, and does not invent
residues. A row that passes is only marked ready to enter a replay. Any later
pose would be a proposed docking model, not an experimental structure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

READY_STATUS = "可以进入对接复现"
REJECT_STATUS = "拒绝"
PROPOSED_MODEL = "提议的对接模型"

_NAME_KEYS = ("complex_name", "target_name", "name", "复合物", "靶点", "名称")
_PDB_KEYS = ("pdb_id", "pdb", "receptor_pdb", "PDB编号")
_LIGAND_KEYS = ("ligand_id", "ligand", "配体标识", "配体")
_POCKET_KEYS = ("pocket_residues", "口袋残基", "残基")
_PRIMARY_LIT_KEYS = ("literature_id", "文献标识", "文献")
_PMID_KEYS = ("pmid", "PMID")
_DOI_KEYS = ("doi", "DOI")

_POCKET_SPLIT = re.compile(r"[\s,;、，]+")
_RESIDUE = re.compile(r"^(?:[A-Za-z])?([1-9]\d{0,4})$")
_PMID = re.compile(r"(?i)^pmid\s*[:：]?\s*(\d{1,8})$")
_DOI = re.compile(r"(?i)^(?:doi\s*[:：]\s*)?(10\.\d{4,9}/\S+)$")

__all__ = [
    "PROPOSED_MODEL",
    "READY_STATUS",
    "check_docking_replay",
    "load_docking_replay_fixture",
]


def load_docking_replay_fixture(path: str | Path) -> dict[str, Any]:
    """Read a local JSON fixture and check its entries. No network."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("夹具必须是包含 entries 的 JSON 对象")
    return check_docking_replay(payload)


def check_docking_replay(entries: Any) -> dict[str, Any]:
    """Check caller-supplied replay rows. Does not run docking.

    ``entries`` may be a list of rows, or a dict with an ``entries`` list.
    A dict may set ``verified_public_complexes`` to false and include a note.
    This function never reports that it checked a paper or reproduced an
    experimental affinity.
    """
    note = ""
    verified_public_complexes: bool | None = None
    raw = entries
    if isinstance(entries, dict):
        if "entries" not in entries:
            raise ValueError("复现记录要放在 entries 里")
        raw = entries["entries"]
        note = str(entries.get("note") or "").strip()
        flag = entries.get("verified_public_complexes", None)
        if flag is True:
            verified_public_complexes = False
            extra = "本函数没有核对原文，不能写成已核对的公开复合物。"
            note = f"{note}{extra}" if note else extra
        elif flag is False:
            verified_public_complexes = False
    if not isinstance(raw, list):
        raise ValueError("entries 必须是列表")

    checked = [_check_one(index, item) for index, item in enumerate(raw)]
    n_ready = sum(1 for row in checked if row["eligible"])
    parts = [
        "通过检查的条目可以进入对接复现。结果将是提议的对接模型。",
        "本函数不运行对接，不补残基，不做全蛋白盲对接，也不把预测复合物写成实验结构。",
        "没有复现实验亲和力。",
    ]
    if verified_public_complexes is False:
        parts.append("还没有放入已核对的公开复合物。")
    result: dict[str, Any] = {
        "docking_executed": False,
        "blind_docking": False,
        "poses_generated": False,
        "experimental_structure": False,
        "experimental_affinity_reproduced": False,
        "literature_text_checked": False,
        "output_kind": PROPOSED_MODEL,
        "n_entries": len(checked),
        "n_ready": n_ready,
        "n_rejected": len(checked) - n_ready,
        "note": note,
        "statement": "".join(parts),
        "entries": checked,
    }
    if verified_public_complexes is not None:
        result["verified_public_complexes"] = verified_public_complexes
    return result


def _check_one(index: int, item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return _row(
            index,
            name="",
            pdb_id=None,
            literature_id=None,
            ligand_id=None,
            pocket_residues=None,
            eligible=False,
            reason="条目不是记录",
            statement="条目不是记录。不生成姿势。",
            case_id=None,
        )
    literature_id, literature_reason = _literature_id(item)
    pocket_kind, numbers = _classify_pocket(_first(item, _POCKET_KEYS))
    reasons: list[str] = []
    if literature_reason:
        reasons.append(literature_reason)
    if pocket_kind == "missing":
        reasons.append("缺口袋残基")
    elif pocket_kind == "not_numbers":
        reasons.append("残基不是编号")
    eligible = not reasons
    pocket_text = ",".join(numbers) if eligible else None
    if eligible:
        statement = (
            "可以进入对接复现。结果将是提议的对接模型。"
            "文献标识和残基编号按调用方所写接受，这里没有核对原文，也没有运行对接。"
        )
        reason = ""
    else:
        reason = "；".join(reasons)
        statement = _reject_statement(reasons)
    case_id = item.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        case_id = None
    return _row(
        index,
        name=_short_text(_first(item, _NAME_KEYS), 300),
        pdb_id=_short_text(_first(item, _PDB_KEYS), 64) or None,
        literature_id=literature_id,
        ligand_id=_short_text(_first(item, _LIGAND_KEYS), 300) or None,
        pocket_residues=pocket_text,
        eligible=eligible,
        reason=reason,
        statement=statement,
        case_id=case_id.strip() if case_id else None,
    )


def _row(
    index: int,
    *,
    name: str,
    pdb_id: str | None,
    literature_id: str | None,
    ligand_id: str | None,
    pocket_residues: str | None,
    eligible: bool,
    reason: str,
    statement: str,
    case_id: str | None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "index": index,
        "name": name,
        "pdb_id": pdb_id,
        "literature_id": literature_id,
        "ligand_id": ligand_id,
        "pocket_residues": pocket_residues,
        "eligible": eligible,
        "status": READY_STATUS if eligible else REJECT_STATUS,
        "reason": reason,
        "statement": statement,
        "poses_generated": False,
        "literature_text_checked": False,
        "docking_executed": False,
    }
    if eligible:
        row["output_kind"] = PROPOSED_MODEL
    if case_id:
        row["case_id"] = case_id
    return row


def _reject_statement(reasons: list[str]) -> str:
    bits: list[str] = []
    if "缺文献标识" in reasons or "文献标识不是 PMID 或 DOI" in reasons:
        bits.append("没有可用的 PMID 或 DOI")
    if "缺口袋残基" in reasons:
        bits.append("没有文献写明的口袋残基，不生成姿势")
    if "残基不是编号" in reasons:
        bits.append("残基不是编号，不补残基，不生成姿势")
    if "条目不是记录" in reasons:
        bits.append("条目不是记录，不生成姿势")
    bits.append("不能进入对接复现")
    return "；".join(bits) + "。"


def _literature_id(entry: dict[str, Any]) -> tuple[str | None, str | None]:
    primary = _first(entry, _PRIMARY_LIT_KEYS)
    if primary is not None and str(primary).strip() != "":
        if not isinstance(primary, str):
            return None, "文献标识不是 PMID 或 DOI"
        parsed = _parse_literature(primary.strip(), bare_digits_ok=False)
        if not parsed:
            return None, "文献标识不是 PMID 或 DOI"
        return parsed, None
    for keys, bare in ((_PMID_KEYS, True), (_DOI_KEYS, False)):
        value = _first(entry, keys)
        if value is None or str(value).strip() == "":
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None, "文献标识不是 PMID 或 DOI"
        parsed = _parse_literature(str(value).strip(), bare_digits_ok=bare)
        if not parsed:
            return None, "文献标识不是 PMID 或 DOI"
        return parsed, None
    return None, "缺文献标识"


def _parse_literature(text: str, *, bare_digits_ok: bool) -> str | None:
    if len(text) > 200:
        return None
    pmid = _PMID.fullmatch(text)
    if pmid:
        return f"PMID:{pmid.group(1)}"
    doi = _DOI.fullmatch(text)
    if doi:
        return doi.group(1)
    if bare_digits_ok and re.fullmatch(r"\d{1,8}", text):
        return f"PMID:{text}"
    return None


def _classify_pocket(value: Any) -> tuple[str, list[str]]:
    if value is None or value == "" or value == []:
        return "missing", []
    if isinstance(value, bool) or isinstance(value, float):
        return "not_numbers", []
    if isinstance(value, int):
        tokens = [str(value)]
    elif isinstance(value, (list, tuple)):
        tokens = []
        if len(value) == 0:
            return "missing", []
        for item in value:
            if isinstance(item, bool) or isinstance(item, float) or item is None:
                return "not_numbers", []
            if isinstance(item, int):
                tokens.append(str(item))
            elif isinstance(item, str) and item.strip():
                tokens.append(item.strip())
            else:
                return "not_numbers", []
    elif isinstance(value, str):
        tokens = [part for part in _POCKET_SPLIT.split(value.strip()) if part]
        if not tokens:
            return "missing", []
    else:
        return "not_numbers", []
    numbers: list[str] = []
    for token in tokens:
        match = _RESIDUE.fullmatch(token)
        if not match:
            return "not_numbers", []
        numbers.append(match.group(1))
    return "ok", numbers


def _first(entry: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in entry:
            return entry[key]
    return None


def _short_text(value: Any, limit: int) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return ""
    text = str(value).strip()
    return text[:limit]
