"""Local cleanup for tables, identifiers, structures, genes, diseases, and doses.

These functions do not call the network. A hit in a local alias table is a
spelling crosswalk, not a recorded target or a clinical effect.
"""
from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP

class PrepError(ValueError):
    """The input cannot be cleaned. The message is safe to show the user."""


_MAX_TABLE_CHARS = 100_000
_MAX_DATA_ROWS = 2000
_MAX_COLUMNS = 40
_MAX_CELL = 2000
_MAX_ID_CHARS = 20_000
_MAX_GENE_TOKENS = 300
_MAX_DISEASE_NAMES = 100
_MAX_DOSES = 80
_MAX_SMILES = 4000
_MAX_GRAMS = Decimal("5000")
_CID_MAX = 160_000_000

_SMILES_CHARS = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "@+-=#$:\\/().%[]*"
)

_INCHIKEY_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z]{14}-[A-Za-z]{10}-[A-Za-z])(?![A-Za-z0-9])"
)
_CAS_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d{2,7}-\d{2}-\d)(?![A-Za-z0-9])"
)
_CID_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:pubchem\s*)?cid\s*[:#]?\s*(\d{1,12})(?!\d)"
)
_GENEID_RE = re.compile(
    r"(?i)(?<![A-Za-z])(?:ncbi\s*)?gene\s*id\s*[:#]?\s*(\d{1,9})(?!\d)"
)
_ENSG_RE = re.compile(r"(?<![A-Za-z0-9])(ENSG\d{11})(?!\d)", re.I)
_UNIPROT_RE = re.compile(
    r"(?<![A-Za-z0-9])("
    r"[OPQ][0-9][A-Z0-9]{3}[0-9]"
    r"|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2}"
    r")(?![A-Za-z0-9])"
)

_DELIM_NAMES = {
    "tab": "\t",
    "\\t": "\t",
    "\t": "\t",
    "comma": ",",
    ",": ",",
    "semicolon": ";",
    ";": ";",
    "pipe": "|",
    "|": "|",
}
_DELIM_LABEL = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}

# NCBI Gene "Also known as" / orthographic variants used in network-pharmacology
# tables. Keys are lowercase, with hyphens and spaces removed and Greek letters
# spelled out. This is not the HGNC download.
_GENE_ALIAS = {
    "tnf": "TNF",
    "tnfa": "TNF",
    "tnfalpha": "TNF",
    "tnfsf2": "TNF",
    "dif": "TNF",
    "ptgs2": "PTGS2",
    "cox2": "PTGS2",
    "pghs2": "PTGS2",
    "phs2": "PTGS2",
    "hcox2": "PTGS2",
    "ptgs1": "PTGS1",
    "cox1": "PTGS1",
    "pghs1": "PTGS1",
    "il1b": "IL1B",
    "il1beta": "IL1B",
    "cxcl8": "CXCL8",
    "il8": "CXCL8",
    "nfe2l2": "NFE2L2",
    "nrf2": "NFE2L2",
    "hmox1": "HMOX1",
    "ho1": "HMOX1",
    "hsp32": "HMOX1",
    "rela": "RELA",
    "p65": "RELA",
    "nfkb3": "RELA",
    "tp53": "TP53",
    "p53": "TP53",
    "nos2": "NOS2",
    "inos": "NOS2",
    "nos2a": "NOS2",
    "nfkb1": "NFKB1",
    "p50": "NFKB1",
    "p105": "NFKB1",
    "mapk1": "MAPK1",
    "erk2": "MAPK1",
    "mapk3": "MAPK3",
    "erk1": "MAPK3",
    "mtor": "MTOR",
    "il6": "IL6",
    "il10": "IL10",
    "stat3": "STAT3",
    "jun": "JUN",
    "cjun": "JUN",
    "fos": "FOS",
    "cfos": "FOS",
    "casp3": "CASP3",
    "caspase3": "CASP3",
    "bcl2": "BCL2",
    "mmp9": "MMP9",
    "icam1": "ICAM1",
    "vcam1": "VCAM1",
    "tgfb1": "TGFB1",
    "tgfbeta1": "TGFB1",
    "vegfa": "VEGFA",
}

_GENE_AMBIGUOUS = {
    "nfkb": ("NFKB1", "RELA"),
    "nfkappab": ("NFKB1", "RELA"),
    "cox": ("PTGS1", "PTGS2"),
    "cyclooxygenase": ("PTGS1", "PTGS2"),
    "nos": ("NOS1", "NOS2", "NOS3"),
    "erk": ("MAPK1", "MAPK3"),
    "akt": ("AKT1", "AKT2", "AKT3"),
    "pi3k": ("PIK3CA", "PIK3CB", "PIK3CD"),
    "vegf": ("VEGFA", "VEGFB", "VEGFC", "VEGFD"),
    "jnk": ("MAPK8", "MAPK9", "MAPK10"),
    "p38": ("MAPK11", "MAPK12", "MAPK13", "MAPK14"),
    "ampk": ("PRKAA1", "PRKAA2"),
    "tgfbeta": ("TGFB1", "TGFB2", "TGFB3"),
    "tgfb": ("TGFB1", "TGFB2", "TGFB3"),
    "il1": ("IL1A", "IL1B"),
}

_GENE_GLUE = {"alpha", "beta", "gamma", "kappa", "mu"}

# MeSH headings below were checked against the public MeSH browser.
# Unlisted names stay unmapped. This is not a MeSH distribution.
_DISEASE_ROWS = (
    {
        "mesh_id": "D003093",
        "heading": "Colitis, Ulcerative",
        "keys": ("溃疡性结肠炎", "ulcerative colitis", "colitis, ulcerative",
                 "colitis gravis"),
    },
    {
        "mesh_id": "D001172",
        "heading": "Arthritis, Rheumatoid",
        "keys": ("类风湿关节炎", "类风湿性关节炎", "rheumatoid arthritis"),
    },
    {
        "mesh_id": "D003924",
        "heading": "Diabetes Mellitus, Type 2",
        "keys": ("2型糖尿病", "二型糖尿病", "II型糖尿病", "type 2 diabetes",
                 "type 2 diabetes mellitus", "type ii diabetes", "t2dm",
                 "niddm"),
    },
    {
        "mesh_id": "D000544",
        "heading": "Alzheimer Disease",
        "keys": ("阿尔茨海默病", "阿尔兹海默病", "阿尔茨海默氏病",
                 "alzheimer disease", "alzheimer's disease"),
    },
    {
        "mesh_id": "D001943",
        "heading": "Breast Neoplasms",
        "keys": ("乳腺癌", "breast cancer", "breast carcinoma",
                 "breast neoplasms"),
    },
    {
        "mesh_id": "D001249",
        "heading": "Asthma",
        "keys": ("哮喘", "支气管哮喘", "asthma", "bronchial asthma"),
    },
    {
        "mesh_id": "D010300",
        "heading": "Parkinson Disease",
        "keys": ("帕金森病", "帕金森氏病", "parkinson disease",
                 "parkinson's disease"),
    },
)

_DISEASE_AMBIGUOUS = {
    "uc": "UC 可能是溃疡性结肠炎，也可能是别的缩写。请写全称。",
    "ra": "RA 可能是类风湿关节炎，也可能是别的缩写。请写全称。",
    "diabetes": "没有写明 1 型还是 2 型。",
    "糖尿病": "没有写明 1 型还是 2 型。",
    "depression": "MeSH 里 Depression 和 Depressive Disorder 不是同一条。",
    "抑郁": "没有写明是抑郁症状还是抑郁障碍。",
    "抑郁症": "没有对照到单一 MeSH 条目。请写 Major depressive disorder 或保留原名。",
}

_DISEASE_NONSPECIFIC = {
    "cancer": "cancer 在 MeSH 里对应多个肿瘤条目。",
    "肿瘤": "肿瘤 对应多个 MeSH 条目。",
    "癌症": "癌症 对应多个 MeSH 条目。",
    "tumor": "tumor 对应多个 MeSH 条目。",
    "tumour": "tumour 对应多个 MeSH 条目。",
    "inflammation": "inflammation 不是一个具体疾病。",
    "炎症": "炎症 不是一个具体疾病。",
    "disease": "disease 不是一个具体疾病。",
    "疾病": "疾病 不是一个具体疾病。",
}

_DOSE_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>千克|公斤|毫克|微克|毫升|升|kg|mg|ug|μg|µg|ml|"
    r"克|两|钱|分|厘|铢|斤|g)",
    re.IGNORECASE,
)

# Grams per traditional unit. None means this convention rejects that unit.
# jin is 16 liang except market_1959, which is the 1959 10-liang jin.
_DOSE_CONVENTIONS = {
    "metric": {
        "label": "公制",
        "citation": "只接受 g、mg、μg、kg。传统单位要另选口径。",
        "liang_g": None, "qian_g": None, "fen_g": None, "li_g": None,
        "zhu_g": None, "jin_g": None,
    },
    "market_1959": {
        "label": "1959年市制",
        "citation": "1959年后市制 1 斤 = 10 两 = 500 g，1 两 = 50 g，1 钱 = 5 g，"
                    "1 分 = 0.5 g。这是市秤，不是古方剂量。",
        "liang_g": Decimal("50"),
        "qian_g": Decimal("5"),
        "fen_g": Decimal("0.5"),
        "li_g": Decimal("0.05"),
        "zhu_g": None,
        "jin_g": Decimal("500"),
    },
    "han_tang": {
        "label": "汉唐",
        "citation": "汉唐一两按 13.8 g（PMID 26621440；中国实验方剂学杂志 2024 年"
                    "《古代经典名方中方药剂量折算原则考证》写唐代一分 = 3.45 g，"
                    "即一两的四分之一）。一铢 = 13.8/24 g。斤按十六两。"
                    "不接受「钱」和「厘」。不自动套用 3 g 或 6–9 g 的临床折法。",
        "liang_g": Decimal("13.8"),
        "qian_g": None,
        "fen_g": Decimal("3.45"),
        "li_g": None,
        "zhu_g": Decimal("13.8") / Decimal("24"),
        "jin_g": Decimal("13.8") * Decimal("16"),
    },
    "song_yuan_2025": {
        "label": "宋元（2025年网络药理学用表）",
        "citation": "Frontiers in Pharmacology, 2025, 1534129，表 1（引邱光明，1996）。"
                    "宋元 1 分 = 0.4 g，1 两 = 10 钱 = 100 分。斤按十六两。",
        "liang_g": Decimal("40"),
        "qian_g": Decimal("4"),
        "fen_g": Decimal("0.4"),
        "li_g": Decimal("0.04"),
        "zhu_g": None,
        "jin_g": Decimal("640"),
    },
    "ming_qing_2025": {
        "label": "明清（2025年网络药理学用表）",
        "citation": "Frontiers in Pharmacology, 2025, 1534129，表 1 的明清行。"
                    "1 分 = 0.37 g，1 两 = 37 g。斤按十六两。"
                    "2024 年经典名方考证写明清一钱 = 3.73 g，见 ming_qing_2024。",
        "liang_g": Decimal("37"),
        "qian_g": Decimal("3.7"),
        "fen_g": Decimal("0.37"),
        "li_g": Decimal("0.037"),
        "zhu_g": None,
        "jin_g": Decimal("592"),
    },
    "song_jin_yuan_2024": {
        "label": "宋金元（2024年经典名方考证）",
        "citation": "中国实验方剂学杂志 2024 年《古代经典名方中方药剂量折算原则考证》："
                    "宋金元一钱 = 4.13 g。分按一钱的十分之一，两按十钱，斤按十六两。",
        "liang_g": Decimal("41.3"),
        "qian_g": Decimal("4.13"),
        "fen_g": Decimal("0.413"),
        "li_g": Decimal("0.0413"),
        "zhu_g": None,
        "jin_g": Decimal("660.8"),
    },
    "ming_qing_2024": {
        "label": "明清（2024年经典名方考证）",
        "citation": "中国实验方剂学杂志 2024 年《古代经典名方中方药剂量折算原则考证》："
                    "明清一钱 = 3.73 g。分按一钱的十分之一，两按十钱，斤按十六两。",
        "liang_g": Decimal("37.3"),
        "qian_g": Decimal("3.73"),
        "fen_g": Decimal("0.373"),
        "li_g": Decimal("0.0373"),
        "zhu_g": None,
        "jin_g": Decimal("596.8"),
    },
}

_TRADITIONAL_FIELD = {
    "两": "liang_g",
    "钱": "qian_g",
    "分": "fen_g",
    "厘": "li_g",
    "铢": "zhu_g",
    "斤": "jin_g",
}
_METRIC_GRAMS = {
    "kg": Decimal("1000"),
    "g": Decimal("1"),
    "mg": Decimal("0.001"),
    "ug": Decimal("0.000001"),
}
_VOLUME_UNITS = {"ml", "毫升", "升"}
_CN_DIGITS = set("〇零一二三四五六七八九十百千万半")


def _require_text(value, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise PrepError(f"{label}必须是字符串。")
    if len(value) > limit:
        raise PrepError(f"{label}超过 {limit} 个字符。")
    text = value.replace("\ufeff", "").strip()
    if not text:
        raise PrepError(f"{label}是空的。")
    return text


def _to_halfwidth(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def _dec_str(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _disease_key(text: str) -> str:
    text = _to_halfwidth(text).strip().lower()
    text = text.replace("Ⅱ", "2").replace("ⅱ", "2").replace("Ⅰ", "1").replace("ⅰ", "1")
    text = text.replace("'", "").replace("’", "")
    text = re.sub(r"[\s,，、._-]+", "", text)
    return text


def _disease_index() -> dict:
    index = {}
    for row in _DISEASE_ROWS:
        for key in row["keys"]:
            norm = _disease_key(key)
            previous = index.get(norm)
            if previous is not None and previous["mesh_id"] != row["mesh_id"]:
                raise RuntimeError(f"duplicate disease key: {norm}")
            index[norm] = row
    return index


_DISEASE_INDEX = _disease_index()


def _parse_delimited(text: str, delim: str) -> list[list[str]]:
    rows: list[list[str]] = []
    row: list[str] = []
    cell: list[str] = []
    in_quotes = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_quotes:
            if ch == '"':
                if i + 1 < n and text[i + 1] == '"':
                    cell.append('"')
                    i += 2
                    continue
                in_quotes = False
                i += 1
                continue
            cell.append(ch)
            i += 1
            continue
        if ch == '"':
            in_quotes = True
            i += 1
            continue
        if ch == delim:
            row.append("".join(cell).strip())
            cell = []
            i += 1
            continue
        if ch == "\n":
            row.append("".join(cell).strip())
            rows.append(row)
            row = []
            cell = []
            i += 1
            continue
        if ch == "\r":
            i += 1
            continue
        cell.append(ch)
        i += 1
    if in_quotes:
        raise PrepError("引号没有闭合。")
    if cell or row:
        row.append("".join(cell).strip())
        rows.append(row)
    return [item for item in rows if any(part.strip() for part in item)]


def _column_widths(rows: list[list[str]]) -> set[int]:
    return {len(item) for item in rows}


def clean_table(text: str, delimiter: str | None = None) -> dict:
    """Turn a pasted table into headers and rows.

    The first non-empty row is the header. Empty rows are dropped. Rows with
    a different column count raise PrepError instead of being padded.
    """
    raw = _require_text(text, "表格", _MAX_TABLE_CHARS)
    if delimiter is not None and not isinstance(delimiter, str):
        raise PrepError("delimiter 必须是字符串。")
    folded = _to_halfwidth(raw)
    if delimiter is None or delimiter.strip() == "" or delimiter == "auto":
        chosen = _detect_delimiter(folded)
        if chosen is None:
            rows = _single_column(folded)
            label = "none"
        else:
            rows = _parse_delimited(folded, chosen)
            label = _DELIM_LABEL[chosen]
    else:
        key = delimiter.strip().lower()
        if key not in _DELIM_NAMES and delimiter not in _DELIM_NAMES:
            raise PrepError(
                "delimiter 只能是 tab、comma、semicolon、pipe，或留空自动判断。"
            )
        char = _DELIM_NAMES.get(key, _DELIM_NAMES.get(delimiter))
        rows = _parse_delimited(folded, char)
        label = _DELIM_LABEL[char]
    if not rows:
        raise PrepError("表格里没有内容。")
    dropped = sum(1 for line in folded.splitlines() if not line.strip())
    headers = _clean_header(rows[0])
    data = rows[1:]
    if len(data) > _MAX_DATA_ROWS:
        raise PrepError(f"数据行超过 {_MAX_DATA_ROWS} 行。")
    width = len(headers)
    bad = [str(index + 2) for index, item in enumerate(data) if len(item) != width]
    if bad:
        shown = ", ".join(bad[:8])
        raise PrepError(f"第 {shown} 行的列数不是 {width}。不会自动补空列。")
    cleaned_rows = []
    for item in data:
        cells = []
        for cell in item:
            if len(cell) > _MAX_CELL:
                raise PrepError(f"单元格超过 {_MAX_CELL} 个字符。")
            cells.append(re.sub(r"[ \t]+", " ", cell).strip())
        cleaned_rows.append(cells)
    return {
        "delimiter": label,
        "columns": headers,
        "rows": cleaned_rows,
        "n_rows": len(cleaned_rows),
        "n_columns": width,
        "dropped_empty_rows": dropped,
        "note": "第一行当作表头。列数不一致会报错，不会自动补空列。",
    }


def _detect_delimiter(text: str) -> str | None:
    present = [char for char in ("\t", ",", ";", "|") if char in text]
    best_char = None
    best_score = 0
    for char in present:
        rows = _parse_delimited(text, char)
        if not rows:
            continue
        widths = _column_widths(rows)
        if len(widths) != 1:
            continue
        width = next(iter(widths))
        if width < 2:
            continue
        score = width * 10000 + len(rows)
        if score > best_score:
            best_score = score
            best_char = char
    if best_char is not None:
        return best_char
    if len(present) == 1:
        return present[0]
    if present:
        raise PrepError("不能判断分隔符。请指定 tab、comma、semicolon 或 pipe。")
    return None


def _single_column(text: str) -> list[list[str]]:
    rows = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cell = line.strip()
        if cell:
            rows.append([cell])
    return rows


def _clean_header(cells: list[str]) -> list[str]:
    if len(cells) > _MAX_COLUMNS:
        raise PrepError(f"列数超过 {_MAX_COLUMNS}。")
    headers = []
    seen = set()
    for cell in cells:
        name = re.sub(r"[ \t]+", " ", cell).strip()
        if not name:
            raise PrepError("表头有空列。")
        if len(name) > _MAX_CELL:
            raise PrepError(f"表头超过 {_MAX_CELL} 个字符。")
        token = name.casefold()
        if token in seen:
            raise PrepError(f"表头重复：{name}。")
        seen.add(token)
        headers.append(name)
    return headers


def _cas_ok(token: str) -> bool:
    body, check = token.rsplit("-", 1)
    digits = body.replace("-", "")
    total = 0
    for weight, char in enumerate(reversed(digits), start=1):
        total += weight * int(char)
    return str(total % 10) == check


def _occupied(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(not (end <= left or start >= right) for left, right in spans)


def normalize_ids(text: str) -> dict:
    """Classify CID, CAS, InChIKey, UniProt accession, and Ensembl gene ids.

    Format checks only. A bare integer is ambiguous between a PubChem CID and
    an NCBI Gene ID, so it is not stored as either.
    """
    raw = _require_text(text, "标识", _MAX_ID_CHARS)
    spans: list[tuple[int, int]] = []
    items: list[dict] = []

    def add(start: int, end: int, item: dict) -> None:
        if _occupied(spans, start, end):
            return
        spans.append((start, end))
        items.append(item)

    for match in _INCHIKEY_RE.finditer(raw):
        key = match.group(1).upper()
        flag = key[23]
        item = {
            "kind": "inchikey",
            "value": key,
            "valid": flag in {"S", "N"},
            "standard": flag == "S",
        }
        if flag not in {"S", "N"}:
            item["reason"] = "InChIKey 第二段第 9 位应为 S 或 N。"
        add(match.start(1), match.end(1), item)

    for match in _CAS_RE.finditer(raw):
        token = match.group(1)
        ok = _cas_ok(token)
        item = {"kind": "cas", "value": token, "valid": ok}
        if not ok:
            item["reason"] = "CAS 校验位不符。"
        add(match.start(1), match.end(1), item)

    for match in _CID_RE.finditer(raw):
        number = int(match.group(1))
        ok = 1 <= number <= _CID_MAX
        item = {"kind": "pubchem_cid", "value": str(number), "valid": ok}
        if not ok:
            item["reason"] = f"PubChem CID 需在 1 到 {_CID_MAX} 之间。"
        add(match.start(), match.end(), item)

    for match in _GENEID_RE.finditer(raw):
        number = int(match.group(1))
        ok = number >= 1
        item = {"kind": "ncbi_gene_id", "value": str(number), "valid": ok}
        if not ok:
            item["reason"] = "NCBI Gene ID 需为正整数。"
        add(match.start(), match.end(), item)

    for match in _ENSG_RE.finditer(raw):
        add(match.start(1), match.end(1), {
            "kind": "ensembl_gene",
            "value": match.group(1).upper(),
            "valid": True,
        })

    for match in _UNIPROT_RE.finditer(raw):
        add(match.start(1), match.end(1), {
            "kind": "uniprot",
            "value": match.group(1).upper(),
            "valid": True,
        })

    ambiguous = []
    unrecognized = []
    covered = []
    seen_ambiguous = set()
    seen_unrecognized = set()
    for token in re.findall(r"[A-Za-z0-9_.-]{2,}", raw):
        start = raw.find(token)
        while start != -1 and _occupied(spans + covered, start, start + len(token)):
            start = raw.find(token, start + 1)
        if start == -1:
            continue
        covered.append((start, start + len(token)))
        if token.isdigit():
            if token not in seen_ambiguous:
                seen_ambiguous.add(token)
                ambiguous.append({
                    "value": token,
                    "reason": "裸数字可能是 PubChem CID，也可能是 NCBI Gene ID。"
                              "请写成 CID:123 或 GeneID:123。",
                })
        elif token not in seen_unrecognized:
            if not any(token.upper() == item["value"] or token == item["value"]
                       for item in items):
                if not _INCHIKEY_RE.fullmatch(token) and not _CAS_RE.fullmatch(token):
                    seen_unrecognized.add(token)
                    unrecognized.append(token)

    deduped = []
    seen = set()
    for item in items:
        key = (item["kind"], item["value"], item["valid"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return {
        "items": deduped,
        "ambiguous": ambiguous,
        "unrecognized": unrecognized,
        "note": "这里只检查格式，不查询数据库。裸数字不会记成 PubChem CID。",
    }


def _try_rdkit():
    try:
        from rdkit import Chem
    except ImportError:
        return None
    return Chem


def normalize_structure(smiles: str) -> dict:
    """Check a SMILES string. RDKit, when installed, adds canonical SMILES and InChIKey.

    Without RDKit the function still checks characters and brackets, and says
    that no InChIKey was produced. Counterions after a dot are kept.
    """
    if not isinstance(smiles, str):
        raise PrepError("SMILES 必须是字符串。")
    if len(smiles) > _MAX_SMILES + 20:
        raise PrepError(f"SMILES 超过 {_MAX_SMILES} 个字符。")
    raw = smiles.strip()
    if not raw:
        raise PrepError("SMILES 是空的。")
    if len(raw) > _MAX_SMILES:
        raise PrepError(f"SMILES 超过 {_MAX_SMILES} 个字符。")
    if any(ch.isspace() for ch in raw):
        raise PrepError("SMILES 中间不能有空白。")
    bad = sorted({ch for ch in raw if ch not in _SMILES_CHARS})
    if bad:
        raise PrepError("SMILES 含有无法识别的字符: " + "".join(bad))
    if raw.count("(") != raw.count(")") or raw.count("[") != raw.count("]"):
        raise PrepError("SMILES 的括号没有成对。")
    notes = []
    component_count = raw.count(".") + 1
    if component_count > 1:
        notes.append("字符串里有点号，可能是盐或多组分。本工具不删除抗衡离子。")
    chem = _try_rdkit()
    if chem is None:
        notes.append(
            "当前环境没有 RDKit。只做了字符检查，没有生成规范 SMILES 或 InChIKey。"
            "不同化学工具包的规范 SMILES 不相同，跨库对照要用 InChIKey。"
        )
        return {
            "input": raw,
            "cleaned": raw,
            "component_count": component_count,
            "engine": "none",
            "canonical_smiles": None,
            "inchikey": None,
            "note": "".join(notes),
        }
    mol = chem.MolFromSmiles(raw)
    if mol is None:
        raise PrepError("RDKit 不能解析这串 SMILES。")
    canonical = chem.MolToSmiles(mol, canonical=True)
    inchikey = None
    try:
        from rdkit.Chem import inchi
        inchikey = inchi.MolToInchiKey(mol)
    except Exception as exc:
        notes.append(
            f"RDKit 已给出规范 SMILES，但没有写出 InChIKey（{type(exc).__name__}）。"
        )
    notes.append(
        "canonical_smiles 来自 RDKit。PubChem 的 CanonicalSMILES 用另一套算法，"
        "不要靠字符串相等判断是不是同一化合物。"
    )
    return {
        "input": raw,
        "cleaned": raw,
        "component_count": component_count,
        "engine": "rdkit",
        "canonical_smiles": canonical,
        "inchikey": inchikey,
        "note": "".join(notes),
    }


def _gene_key(token: str) -> str:
    text = _to_halfwidth(token).strip().lower()
    text = (text.replace("α", "alpha").replace("β", "beta")
            .replace("γ", "gamma").replace("κ", "kappa")
            .replace("μ", "mu").replace("µ", "mu"))
    text = re.sub(r"[\s_-]+", "", text)
    return text


def _split_genes(text: str) -> list[str]:
    folded = _to_halfwidth(text)
    folded = folded.replace("，", ",").replace("；", ";").replace("、", ",")
    parts = re.split(r"[,;\n\t|]+", folded)
    tokens = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        if len(bits) > 1 and not any(
            bit.lower() in _GENE_GLUE or bit in "αβγκμµ" for bit in bits
        ):
            tokens.extend(bit for bit in bits if bit)
        else:
            tokens.append(part)
    if not tokens:
        raise PrepError("没有写出基因符号。")
    if len(tokens) > _MAX_GENE_TOKENS:
        raise PrepError(f"一次最多 {_MAX_GENE_TOKENS} 个符号，这次有 {len(tokens)} 个。")
    return tokens


def _looks_like_other_id(token: str) -> str | None:
    if _CAS_RE.fullmatch(token):
        return "这是 CAS 登记号的形状。请用 tcm_normalize_ids。"
    if _INCHIKEY_RE.fullmatch(token):
        return "这是 InChIKey 的形状。请用 tcm_normalize_ids。"
    if _UNIPROT_RE.fullmatch(token):
        return "这是 UniProt 登录号的形状。请用 tcm_normalize_ids。"
    if _ENSG_RE.fullmatch(token):
        return "这是 Ensembl 基因号的形状。请用 tcm_normalize_ids。"
    return None


def normalize_genes(text: str) -> dict:
    """Map frequent aliases to one HGNC symbol and collapse duplicates.

    Unknown but gene-like tokens are kept unchanged and marked unlisted.
    Family names such as NF-κB, COX, VEGF, and AKT stay ambiguous.
    """
    raw = _require_text(text, "基因符号", _MAX_ID_CHARS)
    tokens = _split_genes(raw)
    grouped: dict[str, dict] = {}
    ambiguous = []
    unlisted = []
    invalid = []
    seen_ambiguous = set()
    seen_unlisted = set()
    seen_invalid = set()
    for token in tokens:
        other = _looks_like_other_id(token)
        key = _gene_key(token)
        if other:
            if token not in seen_invalid:
                seen_invalid.add(token)
                invalid.append({"input": token, "reason": other})
            continue
        if key in _GENE_AMBIGUOUS:
            if key not in seen_ambiguous:
                seen_ambiguous.add(key)
                ambiguous.append({
                    "input": token,
                    "candidates": list(_GENE_AMBIGUOUS[key]),
                    "reason": "这个写法对应多个基因，没有改写成其中一个。",
                })
            continue
        if key in _GENE_ALIAS:
            symbol = _GENE_ALIAS[key]
            status = "official" if token.strip().upper() == symbol else "alias"
            row = grouped.get(symbol)
            if row is None:
                grouped[symbol] = {"symbol": symbol, "status": status, "inputs": [token]}
            else:
                if token not in row["inputs"]:
                    row["inputs"].append(token)
                if status == "alias":
                    row["status"] = "alias"
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,19}", token.strip()):
            if token not in seen_invalid:
                seen_invalid.add(token)
                invalid.append({"input": token, "reason": "不像基因符号。"})
            continue
        symbol = token.strip().upper()
        if symbol not in seen_unlisted:
            seen_unlisted.add(symbol)
            unlisted.append({
                "input": token,
                "symbol": symbol,
                "reason": "不在本地别名表里。已按大写保留，没有改成别的基因。",
            })
    records = []
    for symbol, row in grouped.items():
        records.append({
            "symbol": symbol,
            "status": row["status"],
            "inputs": row["inputs"],
            "n_inputs": len(row["inputs"]),
        })
    return {
        "records": records,
        "ambiguous": ambiguous,
        "unlisted": unlisted,
        "invalid": invalid,
        "n_input": len(tokens),
        "note": "本地别名表只覆盖网络药理学表里常见的写法，不是 HGNC 全表。"
                "归一只说明写法对应哪个符号，不说明该基因是疾病靶点。",
    }


def _split_names(text: str, limit: int, empty_message: str, pattern: str) -> list[str]:
    folded = _to_halfwidth(text)
    parts = [part.strip() for part in re.split(pattern, folded)]
    names = [part for part in parts if part]
    if not names:
        raise PrepError(empty_message)
    if len(names) > limit:
        raise PrepError(f"一次最多 {limit} 条，这次有 {len(names)} 条。")
    return names


def normalize_disease(text: str) -> dict:
    """Map a short list of disease names onto checked MeSH headings.

    Names outside the local list are returned as unmapped. No MeSH id is invented.
    """
    raw = _require_text(text, "疾病名", 8_000)
    names = _split_names(
        raw, _MAX_DISEASE_NAMES, "没有写出疾病名。", r"[;\n；、|]+",
    )
    records = []
    seen = set()
    mapped: dict[str, dict] = {}
    for name in names:
        key = _disease_key(name)
        if key in seen:
            continue
        seen.add(key)
        if key in _DISEASE_NONSPECIFIC:
            records.append({
                "input": name,
                "status": "nonspecific",
                "mesh_id": None,
                "heading": None,
                "reason": _DISEASE_NONSPECIFIC[key],
            })
            continue
        if key in _DISEASE_AMBIGUOUS:
            records.append({
                "input": name,
                "status": "ambiguous",
                "mesh_id": None,
                "heading": None,
                "reason": _DISEASE_AMBIGUOUS[key],
            })
            continue
        row = _DISEASE_INDEX.get(key)
        if row is None:
            records.append({
                "input": name,
                "status": "unmapped",
                "mesh_id": None,
                "heading": None,
                "reason": "不在本地病名表里。没有编造 MeSH 号。",
            })
            continue
        bucket = mapped.get(row["mesh_id"])
        if bucket is None:
            bucket = {
                "input": name,
                "inputs": [name],
                "status": "mapped",
                "mesh_id": row["mesh_id"],
                "heading": row["heading"],
            }
            mapped[row["mesh_id"]] = bucket
            records.append(bucket)
        else:
            bucket["inputs"].append(name)
    return {
        "records": records,
        "note": "这是一份核对过的常用病名对照，不是 MeSH 全表。"
                "没有命中时不会编一个 MeSH 号。病名归一不是诊断。",
    }


def _canon_unit(unit: str) -> str:
    if unit in {"μg", "µg"}:
        return "ug"
    if unit.isascii():
        unit = unit.lower()
    mapping = {
        "kg": "kg", "千克": "kg", "公斤": "kg",
        "g": "g", "克": "g",
        "mg": "mg", "毫克": "mg",
        "ug": "ug", "微克": "ug",
        "ml": "ml", "毫升": "ml", "升": "升",
        "两": "两", "钱": "钱", "分": "分", "厘": "厘", "铢": "铢", "斤": "斤",
    }
    if unit not in mapping:
        raise PrepError(f"无法识别的单位：{unit}。")
    return mapping[unit]


def _grams_for_unit(unit: str, amount: Decimal, spec: dict, convention: str) -> Decimal:
    if unit in _VOLUME_UNITS:
        raise PrepError("毫升和升是体积。本工具只换算重量，不把体积写成克。")
    if unit in _METRIC_GRAMS:
        return amount * _METRIC_GRAMS[unit]
    field = _TRADITIONAL_FIELD[unit]
    factor = spec.get(field)
    if factor is None:
        raise PrepError(
            f"口径 {convention} 不使用「{unit}」。{spec['citation']}"
        )
    return amount * factor


def _parse_one_dose(item: str, spec: dict, convention: str) -> Decimal:
    item = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", item)
    if any(ch in item for ch in _CN_DIGITS):
        raise PrepError(
            f"「{item}」含有中文数字。请写成阿拉伯数字，例如 12两。"
        )
    if re.search(r"\d\s*[-~～到至]\s*\d", item):
        raise PrepError(f"「{item}」是区间。请分成两条，不要取中间值。")
    matches = list(_DOSE_RE.finditer(item))
    if not matches:
        raise PrepError(f"「{item}」里没有识别出数字和单位。")
    covered = []
    total = Decimal("0")
    for match in matches:
        amount = Decimal(match.group("num"))
        if amount <= 0:
            raise PrepError(f"「{item}」的数量要大于 0。")
        unit = _canon_unit(match.group("unit"))
        total += _grams_for_unit(unit, amount, spec, convention)
        covered.append(item[match.start():match.end()])
    leftover = item
    for piece in covered:
        leftover = leftover.replace(piece, " ", 1)
    leftover = re.sub(r"[\s,，、+＋]+", "", leftover)
    if leftover:
        hint = "不接受科学计数法。" if "e" in leftover.lower() else ""
        raise PrepError(f"「{item}」里有无法换算的部分：{leftover}。{hint}")
    if total > _MAX_GRAMS:
        raise PrepError(
            f"「{item}」换算后是 {_dec_str(total)} g，超过 {_dec_str(_MAX_GRAMS)} g。"
            "请核对单位。"
        )
    return total


def convert_doses(text: str, convention: str) -> dict:
    """Convert doses to grams under one named metrology convention.

    The ratio uses the smallest positive gram amount in this call as 1.
    That ratio is a weight proportion, not an efficacy weight.
    """
    raw = _require_text(text, "剂量", 8_000)
    if not isinstance(convention, str) or convention not in _DOSE_CONVENTIONS:
        allowed = "、".join(_DOSE_CONVENTIONS)
        raise PrepError(f"convention 必须是其中之一：{allowed}。")
    spec = _DOSE_CONVENTIONS[convention]
    items = _split_names(raw, _MAX_DOSES, "没有写出剂量。", r"[\n;；|]+")
    grams = [_parse_one_dose(item, spec, convention) for item in items]
    base = min(grams)
    rows = []
    for item, gram in zip(items, grams):
        rows.append({
            "input": item,
            "grams": _dec_str(gram),
            "ratio_to_min": _dec_str(gram / base),
        })
    return {
        "convention": convention,
        "label": spec["label"],
        "citation": spec["citation"],
        "rows": rows,
        "note": "输出是克，以及本次输入里的重量比。重量比不是药效，也不是网络因果权重。",
    }
