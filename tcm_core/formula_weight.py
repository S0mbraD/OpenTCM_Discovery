"""Gram totals for one formula under a single dose convention.

Each row is converted by :func:`tcm_core.prep.convert_doses`. This module does
not copy the gram table, does not read the herb index, and does not use the
network. A weight ratio is a share of the total grams. Processing text is
stored as the caller wrote it.
"""

from __future__ import annotations

import re
from decimal import Decimal

from tcm_core.prep import (
    PrepError,
    _DOSE_CONVENTIONS,
    _DOSE_RE,
    _MAX_DOSES,
    _canon_unit,
    _dec_str,
    _to_halfwidth,
    convert_doses,
)

_MISSING = object()

_METRIC_UNITS = frozenset({"kg", "g", "mg", "ug"})
_TRADITIONAL_UNITS = frozenset({"两", "钱", "分", "厘", "铢", "斤"})

_HERB_KEYS = ("herb", "name", "药材", "药材名", "药名")
_DOSE_KEYS = ("dose", "剂量", "剂量原文")
_AMOUNT_KEYS = ("amount", "value", "数值", "数量")
_UNIT_KEYS = ("unit", "单位")
_PROCESSING_KEYS = ("processing", "炮制", "炮制字段")
_CONVENTION_KEYS = ("convention", "口径")

_MAX_HERB = 200
_MAX_PROCESSING = 80

_NOTE = (
    "各行重量比是该行克数占全方合计克数的比例。"
    "重量比不是疗效，不是暴露量，不是君臣佐使。"
    "炮制只保留调用方写下的原文，缺炮制不会填成生，"
    "也不会换算成药效或成分变化。"
)


def formula_weight(rows, convention=_MISSING) -> dict:
    """Convert caller-supplied formula rows to grams under one convention.

    Each row needs an herb name and a dose. The dose is either original text
    (``dose`` / ``剂量`` / ``剂量原文``) or a number plus a unit
    (``amount`` / ``数值`` with ``unit`` / ``单位``, or a numeric ``dose``
    with a unit). ``processing`` / ``炮制`` is copied when the caller wrote
    it and left as ``None`` when they did not. ``convention`` is required and
    is the only metrology used for every row.

    Returns each row's grams, the formula total, and each row's share of that
    total. The share is not efficacy, exposure, or a classical formula role.
    """
    chosen = _require_convention(convention)
    items = _require_rows(rows)
    specs = [_spec(row, index, chosen) for index, row in enumerate(items, start=1)]
    _reject_mixed_units(specs)
    converted = [_convert_row(spec, chosen) for spec in specs]
    total = sum((item["grams"] for item in converted), Decimal("0"))
    if total <= 0:
        raise PrepError("全方克数合计要大于 0。")
    total_grams = _dec_str(total)
    output_rows = []
    for item in converted:
        output_rows.append({
            "herb": item["herb"],
            "dose": item["dose"],
            "processing": item["processing"],
            "grams": item["grams_text"],
            "weight_ratio": _dec_str(item["grams"] / total),
        })
    passed = converted[0]["passed"]
    result = {
        "convention": chosen,
        "label": passed["label"],
        "citation": passed["citation"],
        "rows": output_rows,
        "n_rows": len(output_rows),
        "total_grams": total_grams,
        "note": _NOTE,
    }
    result["text"] = _render(result)
    return result


def _require_convention(convention) -> str:
    if isinstance(convention, str) and convention in _DOSE_CONVENTIONS:
        return convention
    allowed = "、".join(_DOSE_CONVENTIONS)
    raise PrepError(f"convention 必须是其中之一：{allowed}。")


def _require_rows(rows) -> list:
    if rows is None or isinstance(rows, (str, bytes, dict)):
        raise PrepError("方剂要是药材行的列表。")
    try:
        items = list(rows)
    except TypeError as exc:
        raise PrepError("方剂要是药材行的列表。") from exc
    if not items:
        raise PrepError("方剂是空的。")
    if len(items) > _MAX_DOSES:
        raise PrepError(f"一次最多 {_MAX_DOSES} 行，这次有 {len(items)} 行。")
    return items


def _spec(row, index: int, convention: str) -> dict:
    if not isinstance(row, dict):
        raise PrepError(f"第 {index} 行不是药材记录。")
    _same_convention(row, index, convention)
    herb = _herb_name(row, index)
    return {
        "index": index,
        "herb": herb,
        "dose": _dose_text(row, index),
        "processing": _processing(row, index),
    }


def _one(row: dict, keys: tuple[str, ...], label: str, index: int):
    found = [row[key] for key in keys if key in row]
    if not found:
        return False, None
    first = found[0]
    for value in found[1:]:
        if value != first:
            raise PrepError(f"第 {index} 行的{label}写了两个不同的值。")
    return True, first


def _herb_name(row: dict, index: int) -> str:
    has, value = _one(row, _HERB_KEYS, "药材名", index)
    if not has or not isinstance(value, str) or not value.strip():
        raise PrepError(f"第 {index} 行没有药材名。")
    text = value.strip()
    if len(text) > _MAX_HERB:
        raise PrepError(f"第 {index} 行的药材名超过 {_MAX_HERB} 个字符。")
    return text


def _processing(row: dict, index: int):
    has, value = _one(row, _PROCESSING_KEYS, "炮制", index)
    if not has or value is None:
        return None
    if not isinstance(value, str):
        raise PrepError(f"第 {index} 行的炮制必须是字符串。缺炮制就不要填。")
    text = value.strip()
    if not text:
        return None
    if len(text) > _MAX_PROCESSING:
        raise PrepError(f"第 {index} 行的炮制超过 {_MAX_PROCESSING} 个字符。")
    return text


def _same_convention(row: dict, index: int, convention: str) -> None:
    has, value = _one(row, _CONVENTION_KEYS, "口径", index)
    if not has or value is None or value == convention:
        return
    raise PrepError(
        f"第 {index} 行另写了口径 {value}，整方已经是 {convention}。"
        "一次只接受一个口径，不会把不同口径并成一个两。"
    )


def _plain_number(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, Decimal)):
        return True
    if isinstance(value, float):
        return value == value and value not in {float("inf"), float("-inf")}
    if isinstance(value, str):
        return re.fullmatch(r"\d+(?:\.\d+)?", value.strip()) is not None
    return False


def _dose_text(row: dict, index: int) -> str:
    has_dose, dose = _one(row, _DOSE_KEYS, "剂量", index)
    has_amount, amount = _one(row, _AMOUNT_KEYS, "剂量数值", index)
    has_unit, unit = _one(row, _UNIT_KEYS, "单位", index)
    if has_dose and dose is None:
        raise PrepError(f"第 {index} 行没有剂量。")
    if has_amount and has_dose:
        raise PrepError(
            f"第 {index} 行同时写了剂量原文和数值。请只留一种，不要猜克数。"
        )
    if has_dose and isinstance(dose, str) and not (has_unit and _plain_number(dose)):
        if has_unit:
            raise PrepError(
                f"第 {index} 行同时写了剂量原文和单位。请只留一种，不要猜克数。"
            )
        text = dose.strip()
        if not text:
            raise PrepError(f"第 {index} 行没有剂量。")
        return text
    number = amount if has_amount else dose
    if number is None:
        raise PrepError(f"第 {index} 行没有剂量。")
    if not has_unit:
        raise PrepError(f"第 {index} 行的剂量有数值，但没有单位。")
    return _join_amount_unit(number, unit, index)


def _number_text(amount, index: int) -> str:
    if isinstance(amount, bool) or not _plain_number(amount):
        raise PrepError(f"第 {index} 行的剂量数值无法解析。")
    if isinstance(amount, str):
        text = amount.strip()
        if Decimal(text) <= 0:
            raise PrepError(f"第 {index} 行的数量要大于 0。")
        return text
    if isinstance(amount, int):
        if amount <= 0:
            raise PrepError(f"第 {index} 行的数量要大于 0。")
        return str(amount)
    if isinstance(amount, float):
        if not amount > 0:
            raise PrepError(f"第 {index} 行的数量要大于 0。")
        amount = Decimal(str(amount))
    elif not amount.is_finite() or amount <= 0:
        raise PrepError(f"第 {index} 行的数量要大于 0。")
    rendered = format(amount, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if not rendered:
        raise PrepError(f"第 {index} 行的剂量数值无法解析。")
    return rendered


def _join_amount_unit(amount, unit, index: int) -> str:
    if not isinstance(unit, str) or not unit.strip():
        raise PrepError(f"第 {index} 行没有单位。")
    return f"{_number_text(amount, index)}{unit.strip()}"


def _families(dose_text: str) -> set[str]:
    found = set()
    for match in _DOSE_RE.finditer(_to_halfwidth(dose_text)):
        unit = _canon_unit(match.group("unit"))
        if unit in _METRIC_UNITS:
            found.add("metric")
        elif unit in _TRADITIONAL_UNITS:
            found.add("traditional")
    return found


def _reject_mixed_units(specs: list[dict]) -> None:
    metric = None
    traditional = None
    for spec in specs:
        families = _families(spec["dose"])
        if "metric" in families and "traditional" in families:
            raise PrepError(
                f"第 {spec['index']} 行（{spec['herb']}）混用了公制和传统单位。"
                "一次只接受一个口径，不会把两套单位加成克。"
            )
        if "metric" in families and metric is None:
            metric = spec
        if "traditional" in families and traditional is None:
            traditional = spec
    if metric is not None and traditional is not None:
        raise PrepError(
            f"第 {traditional['index']} 行（{traditional['herb']}）用了传统单位，"
            f"第 {metric['index']} 行（{metric['herb']}）用了公制单位。"
            "同一张方剂不能混用两套单位，也不会把它们加成克。"
        )


def _convert_row(spec: dict, convention: str) -> dict:
    try:
        converted = convert_doses(spec["dose"], convention)
    except PrepError as exc:
        raise PrepError(
            f"第 {spec['index']} 行（{spec['herb']}）无法换算：{exc}"
        ) from exc
    rows = converted.get("rows") if isinstance(converted, dict) else None
    if not rows or len(rows) != 1 or "grams" not in rows[0]:
        raise PrepError(
            f"第 {spec['index']} 行（{spec['herb']}）只能有一个剂量。"
            "不会把多段剂量并成一条。"
        )
    gram_text = rows[0]["grams"]
    try:
        grams = Decimal(gram_text)
    except Exception as exc:
        raise PrepError(
            f"第 {spec['index']} 行（{spec['herb']}）的克数无法读取。"
        ) from exc
    if not grams.is_finite() or grams <= 0:
        raise PrepError(
            f"第 {spec['index']} 行（{spec['herb']}）的克数要大于 0。"
        )
    return {
        "herb": spec["herb"],
        "dose": spec["dose"],
        "processing": spec["processing"],
        "grams_text": gram_text,
        "grams": grams,
        "passed": converted,
    }


def _render(result: dict) -> str:
    lines = [
        result["note"],
        f"口径：{result['label']}（{result['convention']}）。"
        f"全方合计：{result['total_grams']} g。",
    ]
    for row in result["rows"]:
        processing = f"，炮制：{row['processing']}" if row["processing"] else ""
        lines.append(
            f"{row['herb']}，剂量：{row['dose']}{processing}，"
            f"{row['grams']} g，重量比 {row['weight_ratio']}。"
        )
    return "\n".join(lines)
