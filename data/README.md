# data/ 数据索引说明

`herb_mol_index.json` 是随仓库发布的药材–成分关系索引。`tcm_formula_analysis` 用它做成分集合的 Jaccard 与互补度。它是关系缓存，不是预置的疾病或靶点答案。智能层 goal 里的成分名白名单写在 `smart_layer.py`，不从本文件生成。

## 文件

- `herb_mol_index.json`（约 4.2 MB）

## 结构

顶层为字典，共 5 个键：

| 键 | 类型 | 说明 |
|----|------|------|
| `herbs` | dict | 药材名 → 药材元信息 |
| `herb_to_mols` | dict | 药材 → 成分 ID 列表 |
| `mol_to_herbs` | dict | 成分 ID → 来源药材列表 |
| `built_at` | str | 构建时间（UTC ISO 8601） |
| `source` | str | 数据来源说明。当前文件为 `SymMap v2.0 related_components` |

## 数据规模

- 药材：698 / 703 味（5 味因 SymMap 服务端 502 超时跳过，断点续跑机制下可补齐）
- 成分：25,975 个（SYMMAP Mol 表全量唯一成分）

## 来源与合规

- 来源是 SymMap v2.0 的 Herb–Mol 关联（Wu et al., Nucleic Acids Research, 2019）。本文件只保留标识映射，不含结构图。
- 使用时遵守 SymMap 的数据条款。索引不作为临床决策依据。
- 代码读取路径是仓库根目录 `data/herb_mol_index.json`。可用环境变量 `TCM_HERB_INDEX` 指向其他文件。
- 重建（需要公网，耗时较长，支持断点）：

```bash
python scripts/disease_pathway_workflow.py build-index --out data
```

## 使用示例

```python
import json

idx = json.load(open("data/herb_mol_index.json", encoding="utf-8"))
# 黄连 → 成分
mols = idx["herb_to_mols"].get("黄连")
# 成分 → 来源药材
herbs = idx["mol_to_herbs"].get("SMIT00104")
```
