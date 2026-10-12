# M2 成分/靶点交叉证据（Cross-Database Evidence）

**触发意图**：化合物/靶点验证 / 多库共识 / TDL / ID 互转 / 文献靶点（compound, target, 靶点, 交叉验证, 共识）

## 工具链

```
① tcm_cross_database_screen {mode=compound|gene, entities=[...], concurrency=3}
   并发批量：化合物（PubChem CID/InChIKey + ChEMBL + SymMap，共识 0-3）
           基因（STRING + OpenTargets + SymMap + Pharos TDL，共识 0-4）
② 细节深挖：tcm_pubchem_lookup / tcm_chembl_lookup / tcm_ctd_gene_interactions
           / tcm_dgidb_interactions / tcm_get_gene_info
③ ID 桥接：tcm_uniprot_idmap {from_db, to_db, ids}（AC↔ChEMBL↔Ensembl↔PDB）
           tcm_wikidata_lookup {action=resolve, query=<QID>}（CAS/InChIKey/CID/DrugBank 矩阵）
④ 靶点-疾病：tcm_search_disease_targets {disease_name}（Open Targets）
           tcm_gwas_associations / tcm_tcmbank_search / tcm_npass_search_compound
```

## 决策规则

- **D4**：化合物共识 ≥2/3 → 可进 M5 对接；<2/3 → 先 wikidata resolve 核名，禁止对接未验证实体
- **D2**：基因共识 ≥3/4 为核心靶点；Pharos TDL=Tclin/Tchem → 药物开发优先级高
- ID 链式迁移：CID→tcm_pubchem_lookup；UniProt→tcm_uniprot_idmap；SymMap Mol_id→tcm_symmap_lookup related

## 质量门

- 每实体输出 summary 串（如 `berberine: PubChem(CID=2353,InChIKey=...), ChEMBL(CHEMBL295124), SymMap(SMIT00104) -> 3/3`）
- Wikidata 通道波动（连接重置时内置 3 次重试，失败报明确错误，稍后重试）
- 实测锚点：berberine 3/3；TNF 4/4（Tclin, UniProt P01375）；P04637→CHEMBL4096

通道快照与死路清单：references/cross_database_analysis.md；数据库可用性：references/database_availability_2026-09.md
