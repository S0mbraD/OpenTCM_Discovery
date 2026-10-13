"""Read a caller-supplied compound–gene table.

Rows stay in the bucket the caller labeled. A recorded_source_count is the
number of distinct, specific sources marked recorded for one pair. Predicted
rows are listed separately and are not added to that count. This module does
not open a socket and does not ship a target database.
"""
from __future__ import annotations

import json
import re
from typing import Any

from tcm_core.prep import PrepError, clean_table

_MAX_CHARS = 100_000
_MAX_ROWS = 2000
_REQUIRED = ("compound_id", "gene", "support", "source")
_SUPPORTS = {"recorded", "predicted"}

_VAGUE = {
    "unknown", "unknown source", "na", "n/a", "none", "null", "nil",
    "?", "-", "--", "—", "database", "db", "databases",
    "数据库", "未知", "不详", "未明", "不明确",
    "multiple", "mixed", "several", "多个", "多个库", "多库", "多个数据库",
    "混合", "若干", "various",
    "predicted", "recorded", "prediction", "预测", "已记录", "已知",
    "literature", "文献", "paper", "论文", "见文献", "文献报道",
    "source", "来源", "其他", "其它", "etc",
    "yes", "no", "true", "false", "high", "low",
    "tcm", "中药",
}

_WORD_SPLIT = re.compile(r";|；|\||、| and | or | 和 | 或 ", re.I)
_GLUE_SPLIT = re.compile(r"\S(?:和|或|\+)\S")

NOTE = (
    "这张表由调用方给出，没有访问任何靶点库，也没有内置靶点库。"
    "support 只认 recorded 和 predicted，不会把别的写法当成已记录。"
    "recorded_source_count 只统计同一成分、同一基因上，来源写明一个名称且 support 为 recorded 的不同来源。"
    "predicted 行在 predicted_pairs 里单独列出，不计入 recorded_source_count。"
    "来源为空、只写笼统词，或一格里写了多个来源时，该行放进 unclassified，不拆开猜测。"
    "缺少成分标识或基因符号的行也不进入上述计数。"
    "这个计数不是 tcm_cross_database_screen 的在线共识，也不是疗效。"
    "预测靶点不是已记录证据。"
    "基因符号按表中原文保留，不对照别名表。"
    "表上多出来的列不读取。"
)


class TargetTableError(ValueError):
    """The local table cannot be read. The message is safe to show."""


class _Field:
    __slots__ = ("text", "ok")

    def __init__(self, text: str, ok: bool) -> None:
        self.text = text
        self.ok = ok


def read_target_table(text: str) -> dict[str, Any]:
    """Split a caller-supplied target table into recorded and predicted rows.

    ``text`` is the table the caller already has. This function does not open
    a socket, does not read a target database, and does not treat a predicted
    row as recorded evidence.
    """
    raw = _require(text)
    if raw[0] in "{[":
        records, ignored = _json_records(raw)
    else:
        records, ignored = _table_records(raw)
    if not records:
        raise TargetTableError("空表。没有数据行。")
    if len(records) > _MAX_ROWS:
        raise TargetTableError(f"数据行超过 {_MAX_ROWS} 行。")
    return _classify(records, ignored)


def _require(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise TargetTableError("表是空的。")
    if len(text) > _MAX_CHARS:
        raise TargetTableError(f"表超过 {_MAX_CHARS} 个字符。")
    return text.lstrip("\ufeff").strip()


def _table_records(text: str) -> tuple[list[dict[str, _Field]], list[str]]:
    try:
        table = clean_table(text)
    except PrepError as exc:
        raise TargetTableError(str(exc)) from exc
    columns = table["columns"]
    indexes = _column_indexes(columns)
    ignored = [name for name in columns if name.casefold() not in _REQUIRED]
    records = []
    for cells in table["rows"]:
        records.append({
            name: _Field(cells[indexes[name]], True)
            for name in _REQUIRED
        })
    return records, ignored


def _json_records(text: str) -> tuple[list[dict[str, _Field]], list[str]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TargetTableError("JSON 不完整。") from exc
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if "rows" in payload:
            rows = payload["rows"]
        elif "targets" in payload:
            rows = payload["targets"]
        else:
            raise TargetTableError("JSON 里没有 rows 或 targets。")
        if not isinstance(rows, list):
            raise TargetTableError("rows 或 targets 不是列表。")
    else:
        raise TargetTableError("JSON 不是记录。")
    if not rows:
        return [], []
    if len(rows) > _MAX_ROWS:
        raise TargetTableError(f"数据行超过 {_MAX_ROWS} 行。")
    folded_rows = []
    key_union: set[str] = set()
    ignored: list[str] = []
    ignored_seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise TargetTableError(f"第 {index} 行不是对象。")
        folded: dict[str, Any] = {}
        for key, value in row.items():
            token = str(key).strip().casefold()
            folded[token] = value
            if token not in _REQUIRED and token not in ignored_seen:
                ignored_seen.add(token)
                ignored.append(str(key).strip())
        key_union.update(folded)
        folded_rows.append(folded)
    _missing_columns(key_union)
    records = []
    for folded in folded_rows:
        records.append({
            name: _from_value(folded[name]) if name in folded else _Field("", True)
            for name in _REQUIRED
        })
    return records, ignored


def _column_indexes(columns: list[str]) -> dict[str, int]:
    folded = {name.casefold(): index for index, name in enumerate(columns)}
    _missing_columns(set(folded))
    return {name: folded[name] for name in _REQUIRED}


def _missing_columns(present: set[str]) -> None:
    missing = [name for name in _REQUIRED if name not in present]
    if not missing:
        return
    listed = "、".join(missing)
    raise TargetTableError(
        f"缺少 {listed} 列。需要 compound_id、gene、support、source。"
        "不会把缺的 support 当成 recorded。"
    )


def _from_value(value: Any) -> _Field:
    if value is None:
        return _Field("", True)
    if isinstance(value, bool):
        return _Field("", False)
    if isinstance(value, int):
        return _Field(str(value), True)
    if isinstance(value, str):
        return _Field(re.sub(r"[ \t]+", " ", value).strip(), True)
    return _Field("", False)


def _classify(records: list[dict[str, _Field]], ignored: list[str]) -> dict[str, Any]:
    rows = []
    recorded: dict[tuple[str, str], list[str]] = {}
    predicted: dict[tuple[str, str], list[str]] = {}
    n_recorded = n_predicted = n_unclassified = 0
    for index, record in enumerate(records, start=1):
        bucket, reason, support_text = _bucket(record)
        row = {
            "index": index,
            "compound_id": record["compound_id"].text if record["compound_id"].ok else "",
            "gene": record["gene"].text if record["gene"].ok else "",
            "support": support_text,
            "source": record["source"].text if record["source"].ok else "",
            "bucket": bucket,
        }
        if reason:
            row["reason"] = reason
        rows.append(row)
        if bucket == "recorded":
            n_recorded += 1
            _remember(recorded, row["compound_id"], row["gene"], row["source"])
        elif bucket == "predicted":
            n_predicted += 1
            _remember(predicted, row["compound_id"], row["gene"], row["source"])
        else:
            n_unclassified += 1
    return {
        "mode": "local",
        "rows_read": len(rows),
        "n_recorded": n_recorded,
        "n_predicted": n_predicted,
        "n_unclassified": n_unclassified,
        "ignored_columns": ignored,
        "rows": rows,
        "recorded_pairs": _pairs(recorded, "recorded_sources", "recorded_source_count"),
        "predicted_pairs": _pairs(predicted, "predicted_sources", "predicted_source_count"),
        "note": NOTE,
    }


def _bucket(record: dict[str, _Field]) -> tuple[str, str, str]:
    compound = record["compound_id"]
    gene = record["gene"]
    support = record["support"]
    source = record["source"]
    raw_support = support.text if support.ok else ""
    if not compound.ok or not compound.text:
        return "unclassified", "缺少成分标识", raw_support
    if not gene.ok or not gene.text:
        return "unclassified", "缺少基因符号", raw_support
    if not support.ok:
        return "unclassified", "support 不是 recorded 或 predicted", raw_support
    token = support.text.casefold()
    if token not in _SUPPORTS:
        return "unclassified", "support 不是 recorded 或 predicted", raw_support
    if not source.ok or _source_ambiguous(source.text):
        return "unclassified", "来源含糊", token
    return token, "", token


def _source_ambiguous(source: str) -> bool:
    folded = re.sub(r"\s+", " ", source).strip().casefold()
    if len(folded) < 2 or folded in _VAGUE:
        return True
    if _WORD_SPLIT.search(source) or _GLUE_SPLIT.search(source):
        return True
    parts = [part.strip() for part in re.split(r"[/,，]", source) if part.strip()]
    if len(parts) >= 2 and not _single_locator(folded):
        return True
    return False


def _single_locator(folded: str) -> bool:
    if folded.startswith(("doi:", "http://", "https://")):
        return True
    return re.fullmatch(r"pmid[:\s]*\d+", folded) is not None


def _remember(store: dict[tuple[str, str], list[str]], compound: str, gene: str, source: str) -> None:
    slot = store.setdefault((compound, gene), [])
    seen = {item.casefold() for item in slot}
    if source.casefold() not in seen:
        slot.append(source)


def _pairs(store: dict[tuple[str, str], list[str]], names_key: str, count_key: str) -> list[dict[str, Any]]:
    pairs = []
    for compound, gene in sorted(store):
        sources = store[(compound, gene)]
        pairs.append({
            "compound_id": compound,
            "gene": gene,
            names_key: sources,
            count_key: len(sources),
        })
    return pairs
