#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HERB 2.0 批量药材-成分关联获取模板脚本（并发 + 缓存版）

用途：给定药材中文名列表，从 HERB 2.0 批量获取每味药材的成分关联。
输出：JSON 文件，每条记录含 {herb_id, herb_name, ingredient_id, ingredient_name, formula, smiles}

特性：
  - 并发请求（ThreadPoolExecutor），默认 5 线程
  - 本地缓存：已成功获取的药材结果缓存到 JSON，重复运行自动跳过
  - 表头自适应：herb_info.txt 列名按语义模式匹配（兼容 Herb_name / Herb_cn._name 等新旧表头），旧缓存表头失效自动重下
  - 错误重试：单味药材请求失败自动重试 3 次
  - 进度输出：实时显示匹配/未匹配/缓存命中情况

使用方法：
  1. 准备药材中文名列表文件（每行一个药名）
  2. 运行：python fetch_herb_ingredients_template.py herbs.txt output.json
  3. 可选参数：--workers 10 --delay 0.3 --cache herb_cache.json
"""

import json
import os
import sys
import time
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# HERB 2.0 API
HERB_API_URL = "http://47.92.70.12/chedi/api/"
HERB_HERB_INFO_URL = "http://47.92.70.12/static/download_data/V2/HERB_herb_info_v2.txt"

# 请求重试次数
MAX_RETRIES = 3


def download_herb_info(cache_path="herb_info.txt"):
    """下载 HERB 全量药材信息（含中文名 -> HERB ID 映射）

    缓存有效性校验：旧缓存若因上游表头变更而无法解析列，
    自动删除缓存并强制重新下载（最多重试 2 次），避免旧缓存导致永久失败。
    """
    if os.path.exists(cache_path):
        # 校验旧缓存表头是否可用
        try:
            with open(cache_path, encoding="utf-8") as f:
                header = f.readline().rstrip("\r\n").split("\t")
            if (
                _find_col_idx(header, HERB_NAME_COL_PATTERNS) is not None
                and _find_col_idx(header, HERB_ID_COL_PATTERNS) is not None
            ):
                print(f"[skip] {cache_path} 已存在且表头可解析")
                return
            print(
                f"[stale] {cache_path} 表头无法解析（上游表头可能已变更），"
                "自动刷新缓存"
            )
            os.remove(cache_path)
        except (OSError, UnicodeDecodeError) as e:
            print(f"[stale] {cache_path} 读取失败（{e}），自动刷新缓存")
            try:
                os.remove(cache_path)
            except OSError:
                pass

    for attempt in range(2):
        print("[download] HERB herb_info.txt ...")
        try:
            resp = requests.get(HERB_HERB_INFO_URL, timeout=120)
            resp.raise_for_status()
            resp.encoding = "utf-8"
            # 下载后先校验表头再落盘，防止写坏缓存
            first_line = resp.text.splitlines()[0] if resp.text else ""
            header = first_line.split("\t")
            if (
                _find_col_idx(header, HERB_NAME_COL_PATTERNS) is None
                or _find_col_idx(header, HERB_ID_COL_PATTERNS) is None
            ):
                raise ValueError(
                    f"下载内容表头无法解析: {header[:10]} ..."
                )
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write(resp.text)
            print(f"[done] {len(resp.text.splitlines())} 行")
            return
        except Exception as e:
            if attempt == 0:
                print(f"[retry] 下载/校验失败（{e}），5 秒后重试")
                time.sleep(5)
            else:
                raise


def _norm_col(name: str) -> str:
    """列名规范化：去空白、转小写、去掉点号/下划线/连字符，便于跨版本匹配。

    例如 "Herb_cn._name" -> "herbcnname"，"Herb_name" -> "herbname"。
    """
    return "".join(
        ch for ch in str(name).strip().lower() if ch.isalnum()
    )


# 列名候选（规范化后子串匹配，按优先级排序；上游表头随版本变化可自适应）
HERB_NAME_COL_PATTERNS = [
    "herbcnname",   # V2+: Herb_cn._name（新表头）
    "herbname",     # 旧版: Herb_name
    "herbcn",       # 兜底: Herb_cn / herb_cn_name 等
    "cnname",
    "herbchinese",
]
HERB_ID_COL_PATTERNS = [
    "herbid",       # Herb_id / HERB_ID
    "herbhbin",     # 部分版本 ID 列叫 HBIN
    "herbno",
]


def _find_col_idx(header, patterns):
    """在表头列表中按候选模式（规范化子串匹配）查找列索引。

    命中规则：列名规范化后【等于】或【包含】候选模式串。
    多个候选命中同一列时返回第一个匹配的列；
    出现多个列匹配同一模式时优先返回精确相等的那个。
    """
    normed = [(_norm_col(h), i) for i, h in enumerate(header)]
    for pattern in patterns:
        # 先找精确匹配
        for nh, idx in normed:
            if nh == pattern:
                return idx
        # 再找包含匹配
        for nh, idx in normed:
            if pattern in nh:
                return idx
    return None


def load_herb_name_to_id(cache_path="herb_info.txt"):
    """构建 中文名 -> HERB ID 映射（列名自适应，兼容新旧表头版本）"""
    mapping = {}
    with open(cache_path, encoding="utf-8") as f:
        header = f.readline().rstrip("\r\n").split("\t")
        name_idx = _find_col_idx(header, HERB_NAME_COL_PATTERNS)
        id_idx = _find_col_idx(header, HERB_ID_COL_PATTERNS)
        if name_idx is None or id_idx is None:
            # 自适应失败：打印实际表头便于排查，抛出明确错误
            raise ValueError(
                f"[herb_info] 无法识别表头列（中文名列/ID列）。"
                f"实际表头: {header}。"
                f"请检查 {HERB_HERB_INFO_URL} 的最新表头格式。"
            )
        print(
            f"[info] 表头自适应: 中文名列 = '{header[name_idx]}' (col {name_idx}), "
            f"ID列 = '{header[id_idx]}' (col {id_idx})"
        )
        for line in f:
            cols = line.rstrip("\r\n").split("\t")
            if len(cols) > max(name_idx, id_idx):
                name = cols[name_idx].strip()
                herb_id = cols[id_idx].strip()
                if name and herb_id:
                    mapping[name] = herb_id
    return mapping


def load_cache(cache_path):
    """加载本地缓存（已成功获取的药材成分数据）"""
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache_path, cache):
    """保存缓存到文件"""
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)


def fetch_herb_ingredients(herb_id, herb_name=""):
    """调用 HERB 详情 API 获取单味药材的成分列表（带重试）

    注意：herb_ingredient 是 list of lists，每行第一个元素是 dict {'link':..., 'title':'HBINxxxx'}
    """
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                HERB_API_URL,
                json={
                    "v": herb_id,
                    "label": "Herb",
                    "key_id": herb_id,
                    "func_name": "detail_api",
                },
                timeout=30,
            )
            data = resp.json()
            results = []
            for row in data.get("herb_ingredient", []):
                if not row or not isinstance(row[0], dict):
                    continue
                ingredient_id = row[0].get("title", "")
                ingredient_name = row[1] if len(row) > 1 else ""
                formula = row[2] if len(row) > 2 else ""
                smiles = row[3] if len(row) > 3 else ""
                results.append(
                    {
                        "herb_id": herb_id,
                        "herb_name": herb_name,
                        "ingredient_id": ingredient_id,
                        "ingredient_name": ingredient_name,
                        "molecular_formula": formula,
                        "canonical_smiles": smiles,
                    }
                )
            return results
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1 * (attempt + 1))
            else:
                raise


def batch_fetch(herb_names, name_to_id, output_path,
                max_workers=5, delay=0.3, cache_path=None):
    """批量获取多味药材的成分关联（并发 + 缓存）

    Args:
        herb_names: 药材中文名列表
        name_to_id: 中文名 -> HERB ID 映射
        output_path: 输出 JSON 路径
        max_workers: 并发线程数
        delay: 每个请求前的延迟（秒）
        cache_path: 缓存文件路径（None 表示不缓存）
    """
    # 加载缓存
    cache = load_cache(cache_path)
    cache_hits = 0

    all_results = []
    matched = 0
    unmatched = []

    # 需要实际请求的药材（未命中缓存的）
    to_fetch = []
    for name in herb_names:
        name = name.strip()
        herb_id = name_to_id.get(name)
        if not herb_id:
            unmatched.append(name)
            continue
        # 检查缓存
        if cache_path and herb_id in cache:
            all_results.extend(cache[herb_id])
            cache_hits += 1
            matched += 1
        else:
            to_fetch.append((name, herb_id))

    if cache_hits > 0:
        print(f"[cache] 命中缓存: {cache_hits} 味，跳过请求")

    # 并发请求未缓存的药材
    def _fetch_one(name, herb_id):
        time.sleep(delay)
        try:
            ingredients = fetch_herb_ingredients(herb_id, name)
            return name, herb_id, ingredients, None
        except Exception as e:
            return name, herb_id, None, str(e)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_one, name, hid): (name, hid)
                for name, hid in to_fetch
            }

            for i, future in enumerate(as_completed(futures)):
                name, herb_id, ingredients, error = future.result()
                if error or ingredients is None:
                    print(f"[ERROR] {name} ({herb_id}): {error}")
                    unmatched.append(name)
                else:
                    matched += 1
                    all_results.extend(ingredients)
                    # 写入缓存
                    if cache_path:
                        cache[herb_id] = ingredients
                    print(
                        f"[{i+1}/{len(to_fetch)}] {name} ({herb_id}): "
                        f"{len(ingredients)} 个成分"
                    )

        # 保存缓存
        if cache_path:
            save_cache(cache_path, cache)
            print(f"[cache] 缓存已保存: {cache_path} ({len(cache)} 条)")

    # 写入最终输出
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n=== 汇总 ===")
    print(f"输入药材: {len(herb_names)}")
    print(f"匹配成功: {matched} (含缓存命中 {cache_hits})")
    print(f"未匹配/失败: {len(unmatched)}")
    if unmatched:
        print(f"未匹配列表: {unmatched}")
    print(f"成分关联总数: {len(all_results)}")
    print(f"输出文件: {output_path}")

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="HERB 2.0 批量药材-成分关联获取（并发+缓存）"
    )
    parser.add_argument("herbs_file", help="药材中文名列表文件（每行一个）")
    parser.add_argument("output_file", help="输出 JSON 文件路径")
    parser.add_argument(
        "--workers", type=int, default=5, help="并发线程数（默认 5）"
    )
    parser.add_argument(
        "--delay", type=float, default=0.3, help="每个请求前延迟秒数（默认 0.3）"
    )
    parser.add_argument(
        "--cache", default=None, help="缓存文件路径（不指定则不缓存）"
    )
    args = parser.parse_args()

    # 1. 下载/加载 HERB 药材信息
    download_herb_info()

    # 2. 构建名称->ID 映射
    name_to_id = load_herb_name_to_id()
    print(f"[info] 药材映射表: {len(name_to_id)} 条")

    # 3. 读取药材列表
    with open(args.herbs_file, encoding="utf-8") as f:
        herb_names = [line.strip() for line in f if line.strip()]
    print(f"[info] 目标药材: {len(herb_names)} 味")

    # 4. 批量获取（并发 + 缓存）
    batch_fetch(
        herb_names,
        name_to_id,
        args.output_file,
        max_workers=args.workers,
        delay=args.delay,
        cache_path=args.cache,
    )
