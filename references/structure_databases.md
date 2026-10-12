# 结构数据库扩展（2026-10 实测）

> 本 skill 的结构数据通道实测快照。与本技能检索工具族（tcm_*）互补：
> 该 skill 覆盖成分/靶点/通路/疾病检索，本 skill 覆盖**结构、模型、亲和力验证**。

## 实测可用通道（fetch_structure.py 已封装）

| 通道 | 端点 | 用途 | 状态 |
|---|---|---|---|
| RCSB 文件 | files.rcsb.org/download/{id}.pdb | 下载 PDB 坐标 | OK |
| RCSB 数据 API | data.rcsb.org/rest/v1/core/entry/{id} | 元数据（方法/分辨率/实体） | OK |
| PubChem PUG-REST | pubchem.ncbi.nlm.nih.gov/rest/pug/... | 化合物属性 + 3D SDF | OK（限速，4 次退避重试） |
| PDBe API | ebi.ac.uk/pdbe/api/pdb/entry/{summary,ligand_monomers}/{id} | 入口摘要 / 每链配体单体（糖基检查） | OK |
| SWISS-MODEL Repository | swissmodel.expasy.org/repository/uniprot/{ac}.json | 按 UniProt AC 检索同源模型（human 化） | OK |

## 实测可用但未封装（按需直接调用）

| 通道 | 端点 | 用途 |
|---|---|---|
| UniProt REST | rest.uniprot.org/uniprotkb/{ac}.json | 序列、功能注释、PDB 交叉引用 |
| EBI Proteins API | ebi.ac.uk/proteins/api/proteins?accession={ac} | 蛋白特征（XML） |
| PDBe 模体/口袋 | ebi.ac.uk/pdbe/api/pdb/entry/... 多子端点 | 结合位点注释 |
| RCSB 序列搜索 | search.rcsb.org/rcsbsearch/v2/query (POST) | 按序列找同源结构（BLAST 型） |
| RCSB 化学搜索 | data.rcsb.org/rest/v1/chemsearch/... | 子结构/相似性找 PDB 配体 |
| ExpPASy ProtParam | web.expasy.org/protparam/ (POST sequence) | 分子量/pI/疏水性 |

## 死路清单（2026-10 实测，勿重复尝试）

| 通道 | 结果 | 替代 |
|---|---|---|
| BindingDB REST 全部子端点（global_search / getAffinityBySmiles / getTopResults 等） | 404（服务下线） | ChEMBL（tcm-database-query 的 tcm_chembl_lookup）；PDBbind 需官网注册下载索引包 |
| PubChem 名称 property 查询（name/property/...） | 400 | 先 name→cids，再 cid→property |
| PDBe-KB summarise 聚合端点 | 404 | PDBe 基础 API 可用 |
| PubChem 中文名直接查询 | 0 命中（CJK 不支持） | 英文名/别名/CID |
| SMILES→CID fastidentity | 400 | 用 InChIKey 或名称 |

## 亲和力数据获取策略（对接结果验证）

1. **ChEMBL**（首选，在线）：`tcm-database-query` 技能的 `tcm_chembl_lookup`，
   化合物→机制靶点与 IC50/Ki
2. **PDBbind-CN**（批量，离线）：www.pdbbind-plus.org.cn 注册下载索引（PDB ID→实测 Kd/Ki/IC50），
   与对接 pose 对照验证评分合理性
3. **文献数字**：导师提供的综述文档数字优先（人工校验过）

## 与 tcm-database-query 的闭环

```
tcm-database-query（检索筛选）          structure-docking-analysis（分析模拟）
  成分/靶点/通路/疾病检索  ──候选分子──→  结构获取 → Vina 对接 → 接触分析
  ChEMBL 亲和力查询      ←─验证对照──  预测模型 → PyMOL 渲染 → 组合图
  PDB/AlphaFold 结构检索  ──PDB ID──→  几何对接（蛋白-蛋白）→ 证据分级文档
```

检索阶段拿到的靶点 UniProt/基因名 → 本 skill swissmodel 子命令取同源模型；
检索阶段的化合物 CID → pubchem 子命令取 3D 结构对接；
对接预测的关键残基 → 反馈 tcm-database-query 查 UniProt/文献证据链支撑。
