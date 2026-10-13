"""Candidate names for one herb or formula query.

The shipped index stores ``chinese_name``, ``pinyin`` and ``latin_name``
on each SMHB id. Molecule ids in that file have no ingredient names, so
this module does not read them.

``broad_name_candidates`` returns a list. An index hit, a sourced alias
and a near string stay on separate rows. 黄连 is not collapsed into
胡黄连, and 生甘草 is not collapsed into 甘草. A near string is not the
same herb and not an efficacy claim. The index category is not either.

Sentence scanning in ``herbs_in_text`` still prefers the longer name.
This function does not change that scanner.

``tcm_broad_names`` only calls this function. ``tcm_smart_run`` does not.
No network.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from tcm_core.paths import herb_index_candidates

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ALIAS_PATH = _REPO_ROOT / "data" / "name_aliases.json"

_NOTE = (
    "候选列表没有并成同一条记录。"
    "索引命中、有出处的别名和字符串相近分开列出。"
    "相近不是同一药材，也不是疗效。"
    "索引里的分类名不是疗效。"
)

_AFFIXES = (
    "生", "炙", "制", "煅", "焦", "炒",
    "法", "姜", "清", "酒", "醋", "盐", "蜜", "麸",
)
_FORMULA_SUFFIXES = ("汤", "散", "丸", "饮", "丹", "膏", "方", "剂")

_RANK = {
    "index_record": 0,
    "cited_label": 1,
    "processing_prefix": 2,
    "formula_name_contains_herb": 3,
    "contained_name": 4,
    "different_record": 5,
    "latin_near": 6,
    "pinyin_near": 7,
    "one_character": 8,
}

_LEVEL_NOTE = {
    "index_record": "索引字段全等。",
    "cited_label": "别名表里的叫法，没有并成索引主键。",
    "processing_prefix": "多出来的前缀按炮制层级单列，没有并进索引里的那一条。",
    "formula_name_contains_herb": "查询带方剂后缀，其中有药材名。这不是方剂记录，也不是同一条目。",
    "contained_name": "中文名互相包含。这是另一条索引，不是同一味药。",
    "different_record": "别名表写明这是另一条索引记录。",
    "latin_near": "拉丁名整段互相包含。不是同一条索引。",
    "pinyin_near": "拼音字符串相近，和索引里的拼音字段并不全等。",
    "one_character": "中文名只差一个字。相近不是同一味药。",
}

_PIN_LEVELS = frozenset({
    "index_record",
    "cited_label",
    "processing_prefix",
    "formula_name_contains_herb",
    "contained_name",
    "different_record",
    "latin_near",
    "pinyin_near",
})


class NameSpectrumError(ValueError):
    """The local index or alias table could not be read."""


def _norm_space(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _edit1(left: str, right: str) -> bool:
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    if len(left) > len(right):
        left, right = right, left
    i = j = skipped = 0
    while i < len(left) and j < len(right):
        if left[i] == right[j]:
            i += 1
            j += 1
        else:
            skipped += 1
            if skipped > 1:
                return False
            j += 1
    return True


def _contains_name(left: str, right: str) -> bool:
    return (
        len(left) >= 2
        and len(right) >= 2
        and left != right
        and (left in right or right in left)
    )


def load_herb_rows(path: Path) -> tuple[dict, str]:
    """Return the herbs object and its source string.

    Molecule maps in the same file are discarded. They are identifiers
    without ingredient names.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NameSpectrumError("药材索引无法解析。") from exc
    if not isinstance(data, dict) or not isinstance(data.get("herbs"), dict):
        raise NameSpectrumError("索引里没有药材字典。")
    source = data.get("source") if isinstance(data.get("source"), str) else ""
    return data["herbs"], source


def _load_alias_file(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NameSpectrumError("别名表无法解析。") from exc
    if not isinstance(data, dict):
        raise NameSpectrumError("别名表无法解析。")
    return data


def _blank(query: str, kind: str, error: Optional[str], extra_note: str = "") -> dict:
    note = _NOTE if not extra_note else _NOTE + extra_note
    return {
        "query": query,
        "kind": kind,
        "candidates": [],
        "formula_candidates": [],
        "n_index_exact": 0,
        "ambiguous_exact": False,
        "picked_herb_id": None,
        "truncated": False,
        "n_dropped": 0,
        "ignored_aliases": [],
        "error": error,
        "note": note,
    }


def _herb_row(herb_id: str, raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    latin = raw.get("latin_name")
    pinyin = raw.get("pinyin")
    return {
        "herb_id": str(herb_id),
        "chinese_name": (raw.get("chinese_name") or "").strip(),
        "pinyin": pinyin.strip() if isinstance(pinyin, str) else "",
        "latin_name": latin if isinstance(latin, str) else "",
        "index_class": (raw.get("class") or "").strip(),
    }


def _exact_field(query: str, row: dict) -> Optional[str]:
    if row["chinese_name"] and query == row["chinese_name"]:
        return "chinese_name"
    if query.casefold() == row["herb_id"].casefold():
        return "herb_id"
    if row["pinyin"] and query.casefold() == row["pinyin"].casefold():
        return "pinyin"
    if row["latin_name"] and _norm_space(query) == _norm_space(row["latin_name"]):
        return "latin_name"
    return None


def _direct_level(query: str, row: dict) -> Optional[str]:
    name = row["chinese_name"]
    if name and len(name) >= 2 and query != name:
        for affix in sorted(_AFFIXES, key=len, reverse=True):
            if query.startswith(affix) and query[len(affix):] == name:
                return "processing_prefix"
        for suffix in _FORMULA_SUFFIXES:
            if query.endswith(suffix) and query[:-len(suffix)] == name:
                return "formula_name_contains_herb"
        if _contains_name(query, name):
            return "contained_name"
    query_latin = _norm_space(query)
    row_latin = _norm_space(row["latin_name"])
    if query_latin and row_latin and query_latin != row_latin:
        short, long = (
            (query_latin, row_latin)
            if len(query_latin) <= len(row_latin)
            else (row_latin, query_latin)
        )
        if len(short) >= 12 and short in long:
            return "latin_near"
    if query.isascii() and row["pinyin"]:
        query_py = query.casefold()
        row_py = row["pinyin"].casefold()
        if query_py and row_py and query_py != row_py:
            short, long = (
                (query_py, row_py)
                if len(query_py) <= len(row_py)
                else (row_py, query_py)
            )
            if len(short) >= 6 and short in long:
                return "pinyin_near"
            if len(query_py) >= 6 and len(row_py) >= 6 and _edit1(query_py, row_py):
                return "pinyin_near"
    if _has_cjk(query) and name and len(query) >= 2 and len(name) >= 2:
        if _edit1(query, name):
            return "one_character"
    return None


def _index_source_text(index_source: str) -> str:
    base = "data/herb_mol_index.json"
    if index_source:
        return base + "；" + index_source
    return base


def _public_herb(item: dict) -> dict:
    return {
        "match": item["match"],
        "kind": "herb",
        "level": item["level"],
        "same_herb": item["same_herb"],
        "merged": False,
        "field_hit": item["match"] == "index_exact",
        "herb_id": item["herb_id"],
        "chinese_name": item["chinese_name"] or None,
        "pinyin": item["pinyin"] or None,
        "latin_name": item["latin_name"] or None,
        "index_class": item["index_class"] or None,
        "matched_field": item["matched_field"],
        "matched_text": item["matched_text"],
        "alias": item.get("alias"),
        "source": item["source"],
        "distinction_sources": list(item["distinction_sources"]),
        "note": _LEVEL_NOTE[item["level"]],
    }


def _base_item(row: dict, *, match: str, level: str, matched_field: str,
               matched_text: str, source: str, pin: bool) -> dict:
    return {
        "match": match,
        "level": level,
        "herb_id": row["herb_id"],
        "chinese_name": row["chinese_name"],
        "pinyin": row["pinyin"],
        "latin_name": row["latin_name"],
        "index_class": row["index_class"],
        "matched_field": matched_field,
        "matched_text": matched_text,
        "alias": None,
        "source": source,
        "distinction_sources": [],
        "_pin": pin or level in _PIN_LEVELS,
        "same_herb": False,
    }


def _row_key(item: dict) -> tuple:
    if item["match"] == "alias_table":
        return (item["herb_id"], "alias", item.get("alias") or "")
    return (item["herb_id"], item["match"])


def _consider(store: dict, item: dict) -> None:
    if item["match"] == "string_similar" and (item["herb_id"], "index_exact") in store:
        return
    key = _row_key(item)
    current = store.get(key)
    if current is None:
        store[key] = item
        return
    if _RANK[item["level"]] < _RANK[current["level"]]:
        item["_pin"] = bool(item["_pin"] or current["_pin"])
        item["distinction_sources"] = list(current["distinction_sources"])
        store[key] = item
        return
    current["_pin"] = bool(current["_pin"] or item["_pin"])


def _label_names(label: dict) -> list[str]:
    names = []
    alias = label.get("alias")
    if isinstance(alias, str) and alias.strip():
        names.append(alias.strip())
    for extra in label.get("also") or []:
        if isinstance(extra, str) and extra.strip():
            names.append(extra.strip())
    return names


def _accept_table(raw: dict) -> tuple[list, list, list, list]:
    labels = []
    distinctions = []
    formulas = []
    ignored = []
    for row in raw.get("labels") or []:
        if not isinstance(row, dict):
            ignored.append({"alias": "", "reason": "missing_source"})
            continue
        alias = row.get("alias") if isinstance(row.get("alias"), str) else ""
        if row.get("same_herb") is True:
            ignored.append({"alias": alias.strip(), "reason": "same_herb"})
            continue
        source = row.get("source") if isinstance(row.get("source"), str) else ""
        herb_id = row.get("herb_id") if isinstance(row.get("herb_id"), str) else ""
        if not alias.strip() or not source.strip() or not herb_id.strip():
            ignored.append({"alias": alias.strip(), "reason": "missing_source"})
            continue
        labels.append(row)
    for row in raw.get("distinctions") or []:
        if not isinstance(row, dict):
            continue
        source = row.get("source") if isinstance(row.get("source"), str) else ""
        ids = row.get("herb_ids")
        if (
            not source.strip()
            or not isinstance(ids, list)
            or len(ids) != 2
            or not all(isinstance(item, str) and item.strip() for item in ids)
            or ids[0].strip() == ids[1].strip()
        ):
            continue
        distinctions.append({
            "herb_ids": [ids[0].strip(), ids[1].strip()],
            "source": source.strip(),
        })
    for row in raw.get("formulas") or []:
        if not isinstance(row, dict):
            ignored.append({"alias": "", "reason": "missing_source"})
            continue
        alias = row.get("alias") if isinstance(row.get("alias"), str) else ""
        if row.get("same_formula") is True:
            ignored.append({"alias": alias.strip(), "reason": "same_formula"})
            continue
        source = row.get("source") if isinstance(row.get("source"), str) else ""
        if not alias.strip() or not source.strip():
            ignored.append({"alias": alias.strip(), "reason": "missing_source"})
            continue
        formulas.append(row)
    return labels, distinctions, formulas, ignored


def _formula_candidates(query: str, formulas: list) -> list:
    found = []
    folded = _norm_space(query)
    for row in formulas:
        names = _label_names(row)
        if not any(_norm_space(name) == folded for name in names):
            continue
        also = [name for name in names if _norm_space(name) != _norm_space(row["alias"])]
        found.append({
            "match": "alias_table",
            "kind": "formula",
            "level": "cited_label",
            "same_formula": False,
            "merged": False,
            "alias": row["alias"].strip(),
            "also": also,
            "matched_text": query,
            "source": row["source"].strip(),
            "note": "方剂别名来自别名表。没有并成药材，也不是疗效。",
        })
    found.sort(key=lambda item: item["alias"])
    return found


def _alias_item(label: dict, row: Optional[dict], matched_text: str) -> dict:
    herb_id = label["herb_id"].strip()
    if row is None:
        row = {
            "herb_id": herb_id,
            "chinese_name": (label.get("chinese_name") or "").strip()
            if isinstance(label.get("chinese_name"), str) else "",
            "pinyin": "",
            "latin_name": "",
            "index_class": "",
        }
    item = _base_item(
        row,
        match="alias_table",
        level="cited_label",
        matched_field="alias",
        matched_text=matched_text,
        source=label["source"].strip(),
        pin=True,
    )
    item["alias"] = label["alias"].strip()
    return item


def broad_name_candidates(
    query: str,
    *,
    kind: str = "any",
    limit: int = 8,
    herbs: Optional[dict] = None,
    aliases: Optional[dict] = None,
    index_source: str = "",
    index_path: Optional[Path] = None,
    alias_path: Optional[Path] = None,
) -> dict:
    """List herb and formula name candidates for one query.

    ``kind`` is ``herb``, ``formula`` or ``any``. ``limit`` caps near
    strings that are only one character apart. Index hits, sourced
    aliases, and pairs that would be easy to merge are kept even when
    they pass that cap. Nothing in the list is marked as the chosen
    merge. ``picked_herb_id`` stays null.
    """
    kind = kind if isinstance(kind, str) else ""
    text = query.strip() if isinstance(query, str) else ""
    text = " ".join(text.split())
    if kind not in {"herb", "formula", "any"}:
        return _blank(text, kind, "种类只能是 herb、formula 或 any。")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        return _blank(text, kind, "limit 要是 1 到 30 的整数。")
    if not text:
        return _blank(text, kind, "查询是空的。")

    alias_note = ""
    if aliases is None:
        path = Path(alias_path) if alias_path is not None else _ALIAS_PATH
        if path.is_file():
            try:
                aliases = _load_alias_file(path)
            except NameSpectrumError:
                aliases = {}
                alias_note = "别名表无法解析，这次没有使用别名。"
        else:
            aliases = {}
            alias_note = "没有读到别名表，没有编造译名。"
    labels, distinctions, formulas, ignored = _accept_table(aliases)
    formula_rows = _formula_candidates(text, formulas) if kind in {"formula", "any"} else []

    if kind == "formula":
        note = _NOTE
        if alias_note:
            note += alias_note
        if not formula_rows:
            note += "别名表里没有这张方剂。没有把药材名当成方剂。"
        return {
            "query": text,
            "kind": kind,
            "candidates": [],
            "formula_candidates": formula_rows,
            "n_index_exact": 0,
            "ambiguous_exact": False,
            "picked_herb_id": None,
            "truncated": False,
            "n_dropped": 0,
            "ignored_aliases": ignored,
            "error": None,
            "note": note,
        }

    if herbs is None:
        path = Path(index_path) if index_path is not None else None
        if path is None:
            for candidate in herb_index_candidates(_REPO_ROOT):
                if candidate.is_file():
                    path = candidate
                    break
        if path is None or not path.is_file():
            failed = _blank(text, kind, "没有读到药材索引。", alias_note)
            failed["formula_candidates"] = formula_rows
            failed["ignored_aliases"] = ignored
            return failed
        try:
            herbs, loaded_source = load_herb_rows(path)
        except NameSpectrumError as exc:
            failed = _blank(text, kind, str(exc), alias_note)
            failed["formula_candidates"] = formula_rows
            failed["ignored_aliases"] = ignored
            return failed
        if not index_source:
            index_source = loaded_source

    rows = []
    by_id = {}
    for herb_id, raw in (herbs or {}).items():
        row = _herb_row(str(herb_id), raw)
        if row is None:
            continue
        rows.append(row)
        by_id[row["herb_id"]] = row

    source_text = _index_source_text(index_source)
    store: dict = {}
    for row in rows:
        field = _exact_field(text, row)
        if not field:
            continue
        store.pop((row["herb_id"], "string_similar"), None)
        _consider(store, _base_item(
            row,
            match="index_exact",
            level="index_record",
            matched_field=field,
            matched_text=text,
            source=source_text,
            pin=True,
        ))

    for row in rows:
        if (row["herb_id"], "index_exact") in store:
            continue
        level = _direct_level(text, row)
        if not level:
            continue
        _consider(store, _base_item(
            row,
            match="string_similar",
            level=level,
            matched_field="near_string",
            matched_text=text,
            source=source_text,
            pin=level in _PIN_LEVELS,
        ))

    exact_ids = [
        item["herb_id"] for item in store.values() if item["match"] == "index_exact"
    ]
    folded = _norm_space(text)
    for label in labels:
        names = _label_names(label)
        query_hit = any(_norm_space(name) == folded for name in names)
        pointed = label["herb_id"].strip()
        if not query_hit and pointed not in exact_ids:
            continue
        matched = text if query_hit else label["alias"].strip()
        _consider(store, _alias_item(label, by_id.get(pointed), matched))

    for exact in [store[key] for key in list(store) if key[1] == "index_exact"]:
        exact_name = exact["chinese_name"]
        if not exact_name:
            continue
        for row in rows:
            if row["herb_id"] == exact["herb_id"]:
                continue
            if not _contains_name(exact_name, row["chinese_name"]):
                continue
            _consider(store, _base_item(
                row,
                match="string_similar",
                level="contained_name",
                matched_field="near_string",
                matched_text=exact_name,
                source=source_text,
                pin=True,
            ))

    pinned_ids = {
        item["herb_id"]
        for item in store.values()
        if item["match"] in {"index_exact", "alias_table"}
    }
    for herb_id in list(pinned_ids):
        for dist in distinctions:
            if herb_id not in dist["herb_ids"]:
                continue
            for other in dist["herb_ids"]:
                if other == herb_id or other not in by_id:
                    continue
                if (other, "index_exact") in store:
                    continue
                _consider(store, _base_item(
                    by_id[other],
                    match="string_similar",
                    level="different_record",
                    matched_field="near_string",
                    matched_text=text,
                    source=dist["source"],
                    pin=True,
                ))

    ordered = sorted(
        store.values(),
        key=lambda item: (
            _RANK[item["level"]],
            item["chinese_name"] or "",
            item["herb_id"],
            item.get("alias") or "",
        ),
    )
    pinned = [item for item in ordered if item["_pin"]]
    optional = [item for item in ordered if not item["_pin"]]
    room = max(0, limit - len(pinned))
    chosen = pinned + optional[:room]
    n_dropped = len(ordered) - len(chosen)
    chosen_ids = {item["herb_id"] for item in chosen}
    for dist in distinctions:
        if not set(dist["herb_ids"]) <= chosen_ids:
            continue
        for item in chosen:
            if item["match"] == "alias_table":
                continue
            if item["herb_id"] in dist["herb_ids"]:
                if dist["source"] not in item["distinction_sources"]:
                    item["distinction_sources"].append(dist["source"])

    exact_chosen = [item for item in chosen if item["match"] == "index_exact"]
    unique_exact = len(exact_chosen) == 1
    for item in chosen:
        item["same_herb"] = bool(unique_exact and item["match"] == "index_exact")

    candidates = [_public_herb(item) for item in chosen]
    note = _NOTE
    if alias_note:
        note += alias_note
    if len(exact_chosen) > 1:
        note += "同一字符串命中了多条索引，没有挑一条。"
    if any(item["level"] == "formula_name_contains_herb" for item in chosen):
        note += "带方剂后缀的字符串不是方剂索引里的记录。"
    if any(item["level"] == "processing_prefix" for item in chosen):
        note += "多出来的炮制前缀没有并进索引里的药材。"
    if not candidates and not formula_rows:
        note += "这次没有命中。没有补行。"
    return {
        "query": text,
        "kind": kind,
        "candidates": candidates,
        "formula_candidates": formula_rows,
        "n_index_exact": len(exact_chosen),
        "ambiguous_exact": len(exact_chosen) > 1,
        "picked_herb_id": None,
        "truncated": n_dropped > 0,
        "n_dropped": n_dropped,
        "ignored_aliases": ignored,
        "error": None,
        "note": note,
    }
