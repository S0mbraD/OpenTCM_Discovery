# M4 分子库构建（Molecular Library Building）

**触发意图**：分子库 / 成分整理 / 跨库去重 / 结构分类 / QC 评分（library, 去重, dedup, QC, 分子库, 结构分类）

## 工具链（脚本，argparse 参数化）

```
① scripts/fetch_herb_ingredients.py herbs.txt out.json --workers 5
   药材-成分抓取（并发+缓存）
② scripts/molecular_library_builder.py --input 分子库.xlsx --out <dir> [--no-pubchem]
   14 步标准流程：ADME 筛选 → 跨库合并 → QC 五维评分（PASS≥90/REVIEW 70-90/REJECT<70）
   → 结构分类 → PubChem 在线验证
③ scripts/dedup_classify.py input.csv output.xlsx
   五层去重（InChIKey > SMILES > CID > CAS > 规范化名称）→ RDKit 结构分类
④ scripts/evidence_audit.py <artifacts_dir>
   循证审计五项：证据日志/三件套/溯源列/阳性药覆盖率/Top10 可溯源 → PASS/REVIEW/FAIL
⑤ 辅助：fetch_etcm_ingredients.py / fetch_pubchem_batch.py（断点续跑）
       tcm_small_molecule_screening.py（文献参考靶点法六步）
```

## 决策规则

- OB≥30 / DL≥0.18（TCMSP 官方口径；HERB 的 DL 算法不同，禁止混用）
- 补全只填缺失行不覆盖已有值；每字段带 `*_Source` 溯源列
- CAS 必须校验位验证；假 CID（>1.6 亿）为脏数据
- 母体化学实体原则：前药/盐/缀合物在 QC 时归母体

## 质量门与产物

交付四件套：Compound_Master / Compound_Source_Map / QC_Report / AF3_Input（下游 AF3/对接用）。
QC→P0 分析→写回→合并→导出→验证 交付链；AIGC 水印过滤三件套（写回时）。
多基原四步（药典定基原→按物种检索→五层去重→结构分类）：references/multi_source_herb_workflow.md。
14 步细节与历史陷阱：references/iteration_history.md（941 脚本演进沉淀）。
