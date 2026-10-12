# -*- coding: utf-8 -*-
"""
PubChem 批量查询：名称 → CID → 属性（分子式/SMILES/分子量/InChIKey）
支持断点续跑（每批保存一次缓存）

策略：
1. 并发 8 线程：name → cids/JSON 获取 CID
2. 对成功命名的 CID，批量（50个/批）查询属性
3. 结果保存到缓存 JSON，自动跳过已完成名称

用法：
  python fetch_pubchem_batch.py --names names.json --cache cache.json [--batch 200] [--workers 8] [--start 0] [--max-batches 9999]
"""
import argparse
import json
import os
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# 并行度（8 线程 ~0.3s/个 → 200 个约 65s）
WORKERS = 8

# 路径全局变量（由 argparse 入口赋值；声明在此以便函数引用）
NAMES_FILE = ""
CACHE_FILE = ""


def load_names():
    with open(NAMES_FILE, encoding='utf-8') as f:
        return json.load(f)


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False)


def get_cid(name):
    """名称 → CID 列表（取第一个）"""
    try:
        r = requests.get(
            f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{requests.utils.quote(name)}/cids/JSON',
            timeout=15
        )
        if r.status_code == 200:
            cids = r.json().get('IdentifierList', {}).get('CID', [])
            return name, (cids[0] if cids else None)
        return name, None
    except Exception:
        return name, None


def get_props_batch(cids):
    """批量 CID → 属性（50个/批）"""
    results = {}
    for i in range(0, len(cids), 50):
        batch = cids[i:i + 50]
        try:
            r = requests.get(
                f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{",".join(map(str, batch))}/property/MolecularFormula,MolecularWeight,CanonicalSMILES,InChIKey/JSON',
                timeout=30
            )
            if r.status_code == 200:
                for p in r.json().get('PropertyTable', {}).get('Properties', []):
                    results[p['CID']] = {
                        'cid': p['CID'],
                        'formula': p.get('MolecularFormula', ''),
                        'mw': p.get('MolecularWeight', ''),
                        'smiles': p.get('CanonicalSMILES', '') or p.get('ConnectivitySMILES', ''),
                        'inchikey': p.get('InChIKey', ''),
                    }
        except Exception:
            pass
        time.sleep(0.2)
    return results


def main():
    parser = argparse.ArgumentParser(description="PubChem 名称→CID→属性批量查询（断点续跑）")
    parser.add_argument("--names", required=True, help="名称列表 JSON 文件（list[str]）")
    parser.add_argument("--cache", required=True, help="缓存 JSON 输出路径（续跑时读取已有缓存）")
    parser.add_argument("--batch", type=int, default=200, help="每批名称数（默认 200，~65s）")
    parser.add_argument("--workers", type=int, default=WORKERS, help="并发线程数（默认 8）")
    parser.add_argument("--start", type=int, default=0, help="从第几个批次开始（断点续跑）")
    parser.add_argument("--max-batches", type=int, default=9999, help="最多处理批次上限")
    args = parser.parse_args()

    global NAMES_FILE, CACHE_FILE, BATCH_SIZE
    NAMES_FILE = args.names
    CACHE_FILE = args.cache
    BATCH_SIZE = args.batch
    start_batch = args.start
    max_batches = args.max_batches

    names = load_names()
    cache = load_cache()
    print(f'总名称数: {len(names)}, 已缓存: {len(cache)}')

    # 找出未处理的名称 + 已处理但缺 SMILES 的名称（需补属性）
    todo = []
    for n in names:
        if n not in cache:
            todo.append(n)
        else:
            v = cache[n]
            if v.get('cid') and not v.get('smiles'):
                todo.append(n)
    print(f'待处理: {len(todo)}')

    # 分批处理
    total_batches = (len(todo) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f'总批次数: {total_batches}')

    batch_idx = 0
    for b in range(total_batches):
        if b < start_batch:
            continue
        if batch_idx >= max_batches:
            break
        batch = todo[b * BATCH_SIZE:(b + 1) * BATCH_SIZE]
        t0 = time.time()

        # 1. 并发获取 CID
        cid_map = {}
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(get_cid, n): n for n in batch}
            for f in as_completed(futs):
                name, cid = f.result()
                cid_map[name] = cid

        # 2. 批量查询属性
        found_cids = [c for c in cid_map.values() if c]
        props = get_props_batch(found_cids) if found_cids else {}

        # 3. 更新缓存
        for name in batch:
            cid = cid_map.get(name)
            if cid and cid in props:
                cache[name] = props[cid]
            elif cid:
                cache[name] = {'cid': cid, 'formula': '', 'mw': '', 'smiles': '', 'inchikey': ''}
            else:
                cache[name] = {'cid': None, 'formula': '', 'mw': '', 'smiles': '', 'inchikey': ''}

        # 每批保存
        save_cache(cache)
        found = sum(1 for n in batch if cache.get(n, {}).get('cid'))
        print(f'批次 {b+1}/{total_batches}: 处理 {len(batch)}, 命中 {found}, 耗时 {time.time()-t0:.0f}s, 累计缓存 {len(cache)}')
        batch_idx += 1

    # 最终统计
    cache = load_cache()
    with_cid = sum(1 for v in cache.values() if v.get('cid'))
    with_smiles = sum(1 for v in cache.values() if v.get('smiles'))
    with_formula = sum(1 for v in cache.values() if v.get('formula'))
    print(f'\n完成: 总缓存 {len(cache)}, CID {with_cid}, SMILES {with_smiles}, 分子式 {with_formula}')


if __name__ == '__main__':
    main()