"""Align caller-supplied TCMSP ingredient rows with a local name table.

No network. The herb index is not consulted: it has SymMap ids, not
ingredient names or InChIKeys. A name hit is not a structure identity, and
an InChIKey hit is string equality of the keys the caller already provided.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

from tcm_core.prep import PrepError, clean_table
from tcm_core.tcmsp import DL_MIN, OB_MIN

_MAX_CHARS = 100_000
_MAX_ROWS = 2000

_NAME_HEADERS = ("molecule_name", "name", "成分名", "成分", "名称")
_MOL_HEADERS = ("mol_id",)
_KEY_HEADERS = ("inchikey", "inchi_key")
_LOCAL_HEADERS = ("local_id", "symmap_id", "catalog_id")
_OB_HEADERS = ("ob",)
_DL_HEADERS = ("dl",)
_JOIN_HEADERS = _NAME_HEADERS + _MOL_HEADERS + _KEY_HEADERS

_INCHIKEY_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
_MOL_RE = re.compile(r"^MOL\d{6}$")

NOTE = (
    "对齐只比较你给出的成分名、MOL_ID 和 InChIKey 字符串，没有访问 TCMSP 或其他网站。"
    "名称相同不是结构相同。InChIKey 全键相同只说明两边字符串一致，没有向 PubChem 核对。"
    "前 14 位相同、其余不同时记为 inchikey_block，不是同一条立体化学记录。"
    "两边都有 InChIKey 且前 14 位不同时，不会按名称并到一行。"
    "ob、dl 原样抄自 TCMSP 行。tcmsp_ob_dl_pass 只在两个数都有时填写，"
    "表示是否达到 30 和 0.18；这里不筛选，也不用于其他库，不是疗效。"
    "基因列不读。对齐结果不是靶点收录。"
)


class AlignError(ValueError):
    """The two local tables cannot be aligned. The message is safe to show."""


@dataclass
class _Row:
    index: int
    name_raw: str
    name: str
    mol_id: str
    inchikey: str
    local_id: str
    ob_text: Optional[str]
    ob_num: Optional[float]
    dl_text: Optional[str]
    dl_num: Optional[float]
    problems: list[str]


def align_tcmsp(tcmsp_text: str, catalog_text: str) -> dict[str, Any]:
    """Join TCMSP ingredient rows to a caller-supplied name and InChIKey table.

    Both arguments are text the caller already has. This function does not
    open a socket and does not read data/herb_mol_index.json.
    """
    tcmsp_rows = _parse_side(_require(tcmsp_text, "TCMSP 侧"), with_metrics=True)
    catalog_rows = _parse_side(_require(catalog_text, "目录侧"), with_metrics=False)
    matches, ambiguous, tcmsp_unmatched, catalog_unmatched, rejected = _pair(
        tcmsp_rows, catalog_rows
    )
    return {
        "mode": "local",
        "ob_dl_filter_applied": False,
        "tcmsp_rows": len(tcmsp_rows),
        "catalog_rows": len(catalog_rows),
        "n_matches": len(matches),
        "n_ambiguous": len(ambiguous),
        "n_tcmsp_unmatched": len(tcmsp_unmatched),
        "n_catalog_unmatched": len(catalog_unmatched),
        "n_rejected": len(rejected),
        "matches": matches,
        "ambiguous": ambiguous,
        "tcmsp_unmatched": tcmsp_unmatched,
        "catalog_unmatched": catalog_unmatched,
        "rejected": rejected,
        "note": NOTE,
    }


def _require(text: str, label: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise AlignError(f"{label}是空的。")
    if len(text) > _MAX_CHARS:
        raise AlignError(f"{label}超过 {_MAX_CHARS} 个字符。")
    return text.lstrip("\ufeff").strip()


def _parse_side(text: str, *, with_metrics: bool) -> list[_Row]:
    payload = _maybe_json(text)
    if payload is None:
        try:
            table = clean_table(text)
        except PrepError as exc:
            raise AlignError(str(exc)) from exc
        name_at = _find_header(table["columns"], _NAME_HEADERS)
        mol_at = _find_header(table["columns"], _MOL_HEADERS)
        key_at = _find_header(table["columns"], _KEY_HEADERS)
        if name_at is None and mol_at is None and key_at is None:
            raise AlignError("表里没有成分名、MOL_ID 或 InChIKey 列。")
        local_at = _find_header(table["columns"], _LOCAL_HEADERS)
        ob_at = _find_header(table["columns"], _OB_HEADERS) if with_metrics else None
        dl_at = _find_header(table["columns"], _DL_HEADERS) if with_metrics else None
        rows = []
        for index, cells in enumerate(table["rows"], start=1):
            rows.append(_make_row(
                index,
                _cell(cells, name_at),
                _cell(cells, mol_at),
                _cell(cells, key_at),
                _cell(cells, local_at),
                _cell(cells, ob_at),
                _cell(cells, dl_at),
            ))
        return rows
    records = _records(payload)
    return [
        _make_row(
            index,
            _obj_value(obj, _NAME_HEADERS),
            _obj_value(obj, _MOL_HEADERS),
            _obj_value(obj, _KEY_HEADERS),
            _obj_value(obj, _LOCAL_HEADERS),
            _obj_value(obj, _OB_HEADERS) if with_metrics else None,
            _obj_value(obj, _DL_HEADERS) if with_metrics else None,
        )
        for index, obj in enumerate(records, start=1)
    ]


def _maybe_json(text: str) -> Any:
    if text[0] not in "{[":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise AlignError("JSON 不完整。") from exc


def _records(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if "ingredients" in payload:
            rows = payload["ingredients"]
            if not isinstance(rows, list):
                raise AlignError("ingredients 不是列表。")
        elif _obj_has_join(payload):
            rows = [payload]
        else:
            raise AlignError("JSON 里没有 ingredients 列表。")
    else:
        raise AlignError("JSON 不是记录。")
    if len(rows) > _MAX_ROWS:
        raise AlignError(f"数据行超过 {_MAX_ROWS} 行。")
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise AlignError(f"第 {index} 行不是对象。")
    return rows


def _obj_has_join(obj: dict) -> bool:
    folded = {str(key).casefold() for key in obj}
    return any(name.casefold() in folded for name in _JOIN_HEADERS)


def _obj_value(obj: dict, names: tuple[str, ...]) -> Optional[str]:
    folded = {str(key).casefold(): value for key, value in obj.items()}
    for name in names:
        if name.casefold() in folded:
            value = folded[name.casefold()]
            if value is None:
                return None
            return str(value)
    return None


def _find_header(columns: list[str], names: tuple[str, ...]) -> Optional[int]:
    folded = {column.casefold(): index for index, column in enumerate(columns)}
    for name in names:
        hit = folded.get(name.casefold())
        if hit is not None:
            return hit
    return None


def _cell(cells: list[str], index: Optional[int]) -> Optional[str]:
    if index is None:
        return None
    return cells[index]


def _make_row(
    index: int,
    name: Optional[str],
    mol_id: Optional[str],
    inchikey: Optional[str],
    local_id: Optional[str],
    ob: Optional[str],
    dl: Optional[str],
) -> _Row:
    problems: list[str] = []
    raw_name = _plain(name)
    norm_name = _norm_name(raw_name)
    mol, mol_problem = _norm_mol(mol_id)
    key, key_problem = _norm_key(inchikey)
    if mol_problem:
        problems.append(mol_problem)
    if key_problem:
        problems.append(key_problem)
    if not norm_name and not mol and not key and not problems:
        problems.append("没有成分名、MOL_ID 或 InChIKey")
    ob_text, ob_num = _metric(ob)
    dl_text, dl_num = _metric(dl)
    local = _plain(local_id)
    return _Row(
        index=index,
        name_raw=raw_name,
        name=norm_name,
        mol_id=mol,
        inchikey=key,
        local_id=local,
        ob_text=ob_text,
        ob_num=ob_num,
        dl_text=dl_text,
        dl_num=dl_num,
        problems=problems,
    )


def _plain(value: Optional[str]) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip()


def _norm_name(value: str) -> str:
    return value.casefold()


def _norm_mol(value: Optional[str]) -> tuple[str, Optional[str]]:
    text = _plain(value).upper().replace(" ", "")
    if not text:
        return "", None
    if _MOL_RE.fullmatch(text):
        return text, None
    return "", "MOL_ID 应为 MOL 加 6 位数字"


def _norm_key(value: Optional[str]) -> tuple[str, Optional[str]]:
    text = _plain(value).upper().replace(" ", "")
    if not text:
        return "", None
    if not _INCHIKEY_RE.fullmatch(text):
        return "", "InChIKey 格式不对"
    if text[23] not in {"S", "N"}:
        return "", "InChIKey 第二段第 9 位应为 S 或 N"
    return text, None


def _metric(value: Optional[str]) -> tuple[Optional[str], Optional[float]]:
    if value is None:
        return None, None
    text = _plain(value)
    if not text:
        return None, None
    try:
        number = float(text)
    except ValueError:
        return None, None
    if not math.isfinite(number):
        return None, None
    return text, number


def _pair(
    tcmsp_rows: list[_Row],
    catalog_rows: list[_Row],
) -> tuple[list[dict], list[dict], list[dict], list[dict], list[dict]]:
    rejected = [_rejected("tcmsp", row) for row in tcmsp_rows if row.problems]
    rejected.extend(_rejected("catalog", row) for row in catalog_rows if row.problems)
    usable_t = [row for row in tcmsp_rows if not row.problems]
    usable_c = [row for row in catalog_rows if not row.problems]
    by_key: dict[str, list[_Row]] = {}
    by_block: dict[str, list[_Row]] = {}
    by_mol: dict[str, list[_Row]] = {}
    by_name: dict[str, list[_Row]] = {}
    for row in usable_c:
        if row.inchikey:
            by_key.setdefault(row.inchikey, []).append(row)
            by_block.setdefault(row.inchikey[:14], []).append(row)
        if row.mol_id:
            by_mol.setdefault(row.mol_id, []).append(row)
        if row.name:
            by_name.setdefault(row.name, []).append(row)

    matches: list[dict] = []
    ambiguous: list[dict] = []
    unmatched: list[dict] = []
    cited: set[int] = set()
    for row in usable_t:
        kind, chosen, reason = _decide(row, by_key, by_block, by_mol, by_name)
        if kind == "match" and chosen is not None:
            cited.add(chosen.index)
            matches.append(_match_row(row, chosen, reason))
        elif kind == "ambiguous":
            for item in chosen or []:
                cited.add(item.index)
            ambiguous.append(_ambiguous_row(row, chosen or [], reason))
        else:
            unmatched.append(_tcmsp_unmatched(row, reason))
    catalog_unmatched = [
        _catalog_unmatched(row) for row in usable_c if row.index not in cited
    ]
    return matches, ambiguous, unmatched, catalog_unmatched, rejected


def _decide(
    row: _Row,
    by_key: dict[str, list[_Row]],
    by_block: dict[str, list[_Row]],
    by_mol: dict[str, list[_Row]],
    by_name: dict[str, list[_Row]],
) -> tuple[str, Optional[list[_Row] | _Row], str]:
    """Return (match|ambiguous|unmatched, rows, link or reason)."""
    grouped: dict[int, list[tuple[str, _Row]]] = {}

    def add(rank: int, link: str, catalog: _Row) -> None:
        grouped.setdefault(rank, [])
        if any(item.index == catalog.index for _, item in grouped[rank]):
            return
        grouped[rank].append((link, catalog))

    saw_key_conflict = False
    if row.inchikey:
        for catalog in by_key.get(row.inchikey, []):
            add(0, "inchikey", catalog)
        if row.mol_id:
            for catalog in by_mol.get(row.mol_id, []):
                if catalog.inchikey and catalog.inchikey[:14] != row.inchikey[:14]:
                    saw_key_conflict = True
                    continue
                if catalog.inchikey and catalog.inchikey != row.inchikey:
                    add(1, "inchikey_block", catalog)
                else:
                    add(1, "mol_id", catalog)
        for catalog in by_block.get(row.inchikey[:14], []):
            if catalog.inchikey != row.inchikey:
                add(2, "inchikey_block", catalog)
        for catalog in by_name.get(row.name, []) if row.name else []:
            if catalog.inchikey:
                if catalog.inchikey[:14] != row.inchikey[:14]:
                    saw_key_conflict = True
                continue
            add(3, "name", catalog)
    else:
        for catalog in by_mol.get(row.mol_id, []) if row.mol_id else []:
            add(1, "mol_id", catalog)
        for catalog in by_name.get(row.name, []) if row.name else []:
            add(3, "name", catalog)

    if not grouped:
        if saw_key_conflict:
            return "unmatched", None, "InChIKey 与目录不同"
        return "unmatched", None, "目录里没有"

    best = min(grouped)
    hits = grouped[best]
    link = hits[0][0]
    rows = [item for _, item in hits]
    if len(rows) > 1:
        return "ambiguous", rows, _ambiguous_reason(link)
    return "match", rows[0], link


def _ambiguous_reason(link: str) -> str:
    if link == "inchikey":
        return "InChIKey 对应多行"
    if link == "inchikey_block":
        return "InChIKey 连接层对应多行"
    if link == "mol_id":
        return "MOL_ID 对应多行"
    return "名称对应多行"


def _pass_flag(row: _Row) -> Optional[bool]:
    if row.ob_num is None or row.dl_num is None:
        return None
    return row.ob_num >= OB_MIN and row.dl_num >= DL_MIN


def _match_row(tcmsp: _Row, catalog: _Row, link: str) -> dict[str, Any]:
    return {
        "tcmsp_index": tcmsp.index,
        "catalog_index": catalog.index,
        "tcmsp_name": tcmsp.name_raw or None,
        "catalog_name": catalog.name_raw or None,
        "tcmsp_mol_id": tcmsp.mol_id or None,
        "catalog_mol_id": catalog.mol_id or None,
        "tcmsp_inchikey": tcmsp.inchikey or None,
        "catalog_inchikey": catalog.inchikey or None,
        "inchikey_compared": bool(tcmsp.inchikey and catalog.inchikey),
        "link": link,
        "local_id": catalog.local_id or None,
        "ob": tcmsp.ob_text,
        "dl": tcmsp.dl_text,
        "tcmsp_ob_dl_pass": _pass_flag(tcmsp),
    }


def _ambiguous_row(tcmsp: _Row, rows: list[_Row], reason: str) -> dict[str, Any]:
    return {
        "tcmsp_index": tcmsp.index,
        "tcmsp_name": tcmsp.name_raw or None,
        "tcmsp_mol_id": tcmsp.mol_id or None,
        "tcmsp_inchikey": tcmsp.inchikey or None,
        "reason": reason,
        "catalog_indexes": [row.index for row in rows],
    }


def _tcmsp_unmatched(row: _Row, reason: str) -> dict[str, Any]:
    return {
        "tcmsp_index": row.index,
        "tcmsp_name": row.name_raw or None,
        "tcmsp_mol_id": row.mol_id or None,
        "tcmsp_inchikey": row.inchikey or None,
        "reason": reason,
    }


def _catalog_unmatched(row: _Row) -> dict[str, Any]:
    return {
        "catalog_index": row.index,
        "catalog_name": row.name_raw or None,
        "catalog_mol_id": row.mol_id or None,
        "catalog_inchikey": row.inchikey or None,
        "local_id": row.local_id or None,
    }


def _rejected(side: str, row: _Row) -> dict[str, Any]:
    return {"side": side, "index": row.index, "reason": "；".join(row.problems)}
