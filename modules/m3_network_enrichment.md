# M3 网络与富集（Network & Enrichment）

**触发意图**：hub 基因 / PPI 网络 / 中心性 / 富集 / 通路共识（network, hub, ppi, 富集, 网络, 通路）

## 工具链

```
① tcm_network_analysis {genes=[...], required_score=0.4, hub_top=10}
   STRING v12 网络 → 度/中介（Brandes）/紧密中心性 + k-core + 连通分量
   → hub 按三中心性 rank-sum 综合排名
② tcm_multi_enrichment {genes=[...], sources=[gprofiler, symmap]}
   双引擎 KEGG 富集对比：g:Profiler（g_SCS）vs SymMap（OR/P/FDR）
   → 通路名 Jaccard≥0.34 匹配共识项；双引擎 FDR<0.05 = 最强证据
③ tcm_hypergeometric_test {hit_genes, term_genes, query_genes, population_size=20000}
   成分靶点 vs 疾病通路基因集的单尾超几何过表征检验（交叉验证步）
④ 辅助：tcm_string_ppi（单基因互作）/ tcm_enrichment_analysis（单引擎富集）
       scripts/fetch_string_network.py（网络导出）
```

## 决策规则

- **D1**：基因集 ≥15 → 先网络后富集（hubs top10-20 进 M2 基因筛选）；<15 → 直接富集
- 完全连通小网络（如 7 基因 21 边）中介中心性全 0 → 以度排名为准，正常现象
- hub 综合排名 = 度排名+中介排名+紧密排名之和（越小越核心）

## 质量门

- 中介中心性 >300 节点自动置 0（O(VE) 保护，结果中会注明）
- g:Profiler 用 gost/profile 端点 + g_SCS 校正（勿本地再 BH，避免双重校正）
- SymMap 富集行是位置数组 [pathway_id, name, odd_ratio, p, fdr, n_genes, genes]

实测基线：7 UC 基因 → 21 边全连通，hub TNF/IL10/IL12B（度 6）；双引擎共识含 IBD/TNF 通路。
