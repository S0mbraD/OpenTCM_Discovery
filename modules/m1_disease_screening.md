# M1 疾病优先检索（Disease-First Screening）

**触发意图**：疾病相关通路 / 候选药材 / 有效成分 / 症状链 / 成分反向链（关键词：disease, colitis, UC, 通路, 疾病, 药材, 成分）

## 工具链

```
① tcm_kegg_disease_pathways {disease_name, use_enrichment=true}
   疾病 → KEGG 通路（KEGG DISEASE 直连 + SymMap 疾病基因富集，双通道合并）
② scripts/disease_pathway_workflow.py run-all --disease "<病名>" --out <workdir>
   四步全流程：通路 → 候选药材（症状链+成分反向链）→ OB≥30 成分 → 超几何 FDR 交叉验证
③ tcm_symmap_lookup {action=related, rrid=<SMGE/SMDE id>, table=...}
   单点验证 SymMap 关联（大结果集 hub 基因 Gene→Mol 会 502，重试后跳过属设计行为）
```

## 决策规则（动态点）

- **D1**：首通路基因数 ≥15 → 基因集进 M3 做网络+富集；<15 → 直接富集
- **D3**：四步工作流产出 OB 筛选成分 → top 成分进 M2 化合物交叉筛选
- 药材名检索容错：拼音无空格或拉丁名（如苦参用 Radix Sophorae Flavescentis），不确定先 typeahead

## 质量门

- 每步产物 JSON 带 source/queried_at；herb_mol_index.json 是关系缓存非预置答案
- 超几何检验背景 N=20000（人蛋白编码基因）；FDR 用 BH
- 索引构建 30-60 分钟，后台跑（每 25 药材 checkpoint，断点续跑）

## 输出产物

`step1_disease_pathways.json / step2_pathway_herbs.json / step3_herb_ingredients.json / step4_cross_validation.json / herb_mol_index.json`

UC 基线（2026-10-11 实测）：151 通路（IBD hsa05321 p=4.87e-18）；619 候选药材（症状链 574+成分反向链 355）；黄芩 411 成分/105 OB≥30；药材级 P≈1e-14。
通道细节/502 陷阱：references/symmap_kegg_disease.md
