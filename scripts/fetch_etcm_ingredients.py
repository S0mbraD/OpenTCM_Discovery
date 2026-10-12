#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETCM 2.0 批量药材-成分关联获取模板脚本（v2: herb_chem_phy 全量获取版）

用途：给定药材中文名列表，从 ETCM 2.0 API 批量获取每味药材的**全部**成分及其理化属性。
输出：JSON 文件，每味药材含完整成分列表（成分名、分子量、logP、logS、QED、F20%、T1/2）

核心方法：
  1. POST /home/herb_chem_phy/ 获取全量成分名 + 指定属性值（无分页限制）
  2. GET /home/detail/ 获取 related_table 前20条补充字段（分子式、MW、QED、FDAMDD、Standards）
  3. 按成分名 join 两源数据

特性：
  - 并发请求（ThreadPoolExecutor），默认 5 线程
  - 本地缓存：已成功获取的药材结果缓存到 JSON，重复运行自动跳过
  - 错误重试：单味药材请求失败自动重试 3 次

使用方法：
  python fetch_etcm_ingredients_template.py herbs.txt output.json
  可选参数：--workers 5 --delay 0.3 --cache etcm_cache.json
"""

import json
import sys
import time
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ETCM 2.0 API
ETCM_API_BASE = "http://www.tcmip.cn:18124"

# herb_chem_phy 可用字段
CHEM_PHY_FIELDS = [
    "Molecular Weight",
    "logP",
    "logS",
    "Quantitative Estimate of Drug-likeness",
    "F20%(20% Oral Bioavailability)",
    "T1/2",
]

MAX_RETRIES = 3


def _retry_request(method, url, **kwargs):
    """带重试的请求封装"""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.request(method, url, timeout=30, **kwargs)
            if resp.status_code == 200 and resp.text.strip():
                return resp.json()
            return None
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1 * (attempt + 1))
            else:
                raise


def fetch_herb_chem_phy(herb_name, fields):
    """调用 herb_chem_phy 端点获取全量成分名 + 属性值

    返回: {ingredient_name: {field_name: value, ...}, ...}
    """
    result = {}
    for field in fields:
        data = _retry_request(
            "POST",
            f"{ETCM_API_BASE}/home/herb_chem_phy/",
            json={"id": herb_name, "type": "herb", "Component_Phy_Chem": [field]},
        )
        if not data or data.get("code") != 1:
            continue
        val = data.get("data", {}).get("value", {})
        div = val.get("div", [])
        script = val.get("script", [])
        if not isinstance(div, list) or not isinstance(script, list):
            continue
        for name, value in zip(div, script):
            if name not in result:
                result[name] = {}
            result[name][field] = value
    return result


def fetch_related_table_ingredients(herb_name):
    """从 detail API 的 related_table 获取前20条成分补充字段

    返回: {ingredient_name: {molecular_formula, molecular_weight, qed, fda_max_daily_dose, standards}, ...}
    """
    data = _retry_request(
        "GET",
        f"{ETCM_API_BASE}/home/detail/",
        params={"id": herb_name, "type": "herb"},
    )
    if not data or data.get("code") != 1:
        return {}, 0, []

    details = {}
    total_count = 0
    cross_refs = []

    for section in data.get("data", []):
        if section.get("id") == "related_table":
            for item in section.get("value", []):
                if item.get("type") == "ingredient":
                    total_count = item.get("count", 0)
                    for row in item.get("value", []):
                        if not isinstance(row, dict):
                            continue
                        n = row.get("Ingredient Name", [])
                        if isinstance(n, list):
                            n = n[0] if n else ""
                        if not n:
                            continue
                        details[n] = {
                            "molecular_formula": row.get("Molecular Formula", ""),
                            "molecular_weight_detail": row.get("Molecular Weight", ""),
                            "qed_detail": row.get("Quantitative Estimate of Drug-likeness\uff08QED\uff09", ""),
                            "fda_max_daily_dose": row.get("FDA Maximum Daily Dose (FDAMDD)", ""),
                            "standards": row.get("Standards", ""),
                        }

        # 提取跨库引用
        if section.get("id") == "base_information":
            for field in section.get("value", []):
                if "Cross" in str(field.get("key", "")):
                    cross_refs.append(field)

    return details, total_count, cross_refs


def fetch_herb_ingredients(herb_name):
    """获取单味药材的全部成分及属性（合并 herb_chem_phy + related_table）

    返回:
        {
            "herb_name": str,
            "etcm_pinyin": str,
            "total_count": int,          # API 报告的总数
            "ingredients": [
                {
                    "ingredient_name": str,
                    "molecular_weight": str,   # from herb_chem_phy
                    "logP": str,
                    "logS": str,
                    "qed": str,
                    "f20": str,
                    "t1_2": str,
                    "molecular_formula": str, # from related_table (前20条有)
                    ...
                },
            ],
            "cross_references": list,
        }
    """
    # 1. herb_chem_phy: 全量成分名 + 属性
    chem_phy_data = fetch_herb_chem_phy(herb_name, CHEM_PHY_FIELDS)

    # 2. related_table: 前20条补充字段 + count + cross_refs
    rt_data, total_count, cross_refs = fetch_related_table_ingredients(herb_name)

    # 3. 合并：以 herb_chem_phy 的成分名为基准
    ingredients = []
    for name, props in chem_phy_data.items():
        rt = rt_data.get(name, {})
        ingredients.append({
            "ingredient_name": name,
            "molecular_weight": props.get("Molecular Weight", ""),
            "logP": props.get("logP", ""),
            "logS": props.get("logS", ""),
            "qed": props.get("Quantitative Estimate of Drug-likeness", ""),
            "f20_oral_bioavailability": props.get("F20%(20% Oral Bioavailability)", ""),
            "t1_2": props.get("T1/2", ""),
            "molecular_formula": rt.get("molecular_formula", ""),
            "fda_max_daily_dose": rt.get("fda_max_daily_dose", ""),
            "standards": rt.get("standards", ""),
            "etcm_url": f"http://www.tcmip.cn/ETCM2/front/#/Detail/ingredient/{name}",
        })

    return {
        "herb_name": herb_name,
        "etcm_pinyin": "",
        "total_count": total_count,
        "ingredient_count": len(ingredients),
        "ingredients": ingredients,
        "cross_references": cross_refs,
    }


def load_cache(cache_path):
    if cache_path:
        import os
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
    return {}


def save_cache(cache_path, cache):
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)


def batch_fetch(herb_names, output_path, max_workers=5, delay=0.3, cache_path=None):
    """批量获取多味药材的成分关联（并发 + 缓存）"""
    cache = load_cache(cache_path)
    cache_hits = 0

    all_results = []
    matched = 0
    unmatched = []

    to_fetch = []
    for name in herb_names:
        name = name.strip()
        if cache_path and name in cache:
            all_results.append(cache[name])
            cache_hits += 1
            matched += 1
        else:
            to_fetch.append(name)

    if cache_hits > 0:
        print(f"[cache] 命中缓存: {cache_hits} 味，跳过请求")

    def _fetch_one(name):
        time.sleep(delay)
        try:
            return name, fetch_herb_ingredients(name), None
        except Exception as e:
            return name, None, str(e)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_one, name): name for name in to_fetch}
            for i, future in enumerate(as_completed(futures)):
                name, result, error = future.result()
                if error or result is None:
                    print(f"[ERROR] {name}: {error}")
                    unmatched.append(name)
                else:
                    matched += 1
                    all_results.append(result)
                    if cache_path:
                        cache[name] = result
                        # 增量保存：每完成一味药材立即写入缓存，中断后可续跑
                        save_cache(cache_path, cache)
                    ing_count = result.get("ingredient_count", 0)
                    total = result.get("total_count", 0)
                    print(f"[{i+1}/{len(to_fetch)}] {name}: "
                          f"{ing_count} 个成分 (API count={total})")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    print(f"\n=== 汇总 ===")
    print(f"输入药材: {len(herb_names)}")
    print(f"匹配成功: {matched} (含缓存命中 {cache_hits})")
    print(f"未匹配/失败: {len(unmatched)}")
    if unmatched:
        print(f"未匹配列表: {unmatched}")
    total_ingredients = sum(r.get("ingredient_count", 0) for r in all_results)
    print(f"成分关联总数: {total_ingredients}")
    print(f"输出文件: {output_path}")

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ETCM 2.0 批量药材-成分获取（herb_chem_phy 全量版）"
    )
    parser.add_argument("herbs_file", help="药材中文名列表文件（每行一个）")
    parser.add_argument("output_file", help="输出 JSON 文件路径")
    parser.add_argument("--workers", type=int, default=5, help="并发线程数（默认 5）")
    parser.add_argument("--delay", type=float, default=0.3, help="请求前延迟秒数")
    parser.add_argument("--cache", default=None, help="缓存文件路径")
    args = parser.parse_args()

    with open(args.herbs_file, encoding="utf-8") as f:
        herb_names = [line.strip() for line in f if line.strip()]
    print(f"[info] 目标药材: {len(herb_names)} 味")

    batch_fetch(
        herb_names,
        args.output_file,
        max_workers=args.workers,
        delay=args.delay,
        cache_path=args.cache,
    )
