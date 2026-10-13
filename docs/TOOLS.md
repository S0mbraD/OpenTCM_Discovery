# 工具总表

统一入口 `mcp_server/unified_mcp.py` 注册下面 63 个工具。带参调用使用 `{"params": {...}}`。只读检索的响应带 `source` 与 `queried_at`。字段细节以各工具 docstring 为准；模块级决策见 `modules/`。

## 智能层（8）

| 工具 | 作用 |
|---|---|
| `unified_overview` | 模块、工具清单、状态目录健康、推荐的第一步 |
| `tcm_smart_plan` | 按目标文本生成带 D1–D6 的步骤表。只规划，不执行 |
| `tcm_smart_run` | 执行在线深链并写会话。`depth=full` 只登记药材脚本，不在请求内跑完 |
| `tcm_report_generate` | 把一次会话写成可审阅的 Markdown 简报 |
| `tcm_cache_status` | 查看或清空只读检索缓存 |
| `tcm_classify_structure` | 用 RDKit SMARTS 给 SMILES 做结构大类分类；无 RDKit 时退回字符串启发 |
| `tcm_self_iterate` | 复核本地运行观察。`action=apply` 或 `reject` 只处理用户点名的 id，并只追加 `references/runtime_lessons.md` |
| `tcm_analysis_topology` | 按会话重画分析网络和执行状态。小图是圆角卡片，实体节点满 300 后改为按类别分组的球面。输出 Mermaid、SVG 和自包含 HTML |

## 检索、网络与自迭代（27）

实现在 `open_tcm_discovery_mcp.py`。单独运行该文件不会带上其余 27 个工具。

| 工具 | 作用 |
|---|---|
| `tcm_pubchem_lookup` | PubChem 名称、CID、性质与结构标识 |
| `tcm_kegg_compound` | KEGG 化合物条目 |
| `tcm_chembl_lookup` | ChEMBL 分子与生物活性相关记录 |
| `tcm_ensembl_lookup` | 基因符号到 Ensembl 标识及交叉引用 |
| `tcm_search_disease_targets` | Open Targets 疾病相关靶点 |
| `tcm_string_ppi` | STRING 蛋白互作边 |
| `tcm_tcmbank_search` | TCMBank 成分或靶点检索 |
| `tcm_ctd_gene_interactions` | CTD 基因–化学物相互作用 |
| `tcm_get_gene_info` | UniProt 基因/蛋白信息 |
| `tcm_ncbi_gene_search` | NCBI Gene 检索 |
| `tcm_enrichment_analysis` | 基因集富集 |
| `tcm_npass_search_compound` | NPASS 天然产物检索 |
| `tcm_get_structure` | 结构记录检索（PDB 等） |
| `tcm_dgidb_interactions` | DGIdb 药物–基因相互作用 |
| `tcm_gwas_associations` | GWAS Catalog 关联 |
| `tcm_check_database_status` | 探测一组上游端点是否可回答 |
| `tcm_audit_evidence` | 按证据分级审计一段结论的措辞 |
| `tcm_symmap_lookup` | SymMap 药材、成分、症状、基因关联 |
| `tcm_kegg_disease_pathways` | 疾病到 KEGG / SymMap 通路 |
| `tcm_hypergeometric_test` | 超几何检验与 BH-FDR |
| `tcm_cross_database_screen` | 化合物或基因的多库并发共识 |
| `tcm_network_analysis` | PPI 度、介数、接近中心性与 hub |
| `tcm_multi_enrichment` | g:Profiler 与 SymMap 双引擎富集共识 |
| `tcm_wikidata_lookup` | Wikidata 标识桥（CAS、InChIKey、CID 等） |
| `tcm_uniprot_idmap` | UniProt ID 映射 |
| `tcm_report_insight` | 把死路、端点变化或复现问题写入洞察库 |
| `tcm_analyze_runtime` | 汇总检索遥测并给出后续建议 |

## 对接建模（8）

| 工具 | 作用 |
|---|---|
| `dock_check_environment` | 检查 Vina、Meeko、PyMOL、RDKit 是否可用 |
| `dock_fetch_structure` | 拉取并清理受体，或准备配体三维结构 |
| `dock_vina_docking` | 以文献口袋做 AutoDock Vina 对接 |
| `dock_analyze_contacts` | 接触残基与氢键候选。输出属于 proposed docking model |
| `dock_geometric_ppi` | 按文献界面做蛋白–蛋白几何放置，不做能量最小化 |
| `dock_report_insight` | 对接侧洞察落库 |
| `dock_analyze_runtime` | 对接实验台账与复现检查 |
| `dock_compare_experiments` | 按配体或受体对照历史台账 |

## 并行层（4）

| 工具 | 作用 |
|---|---|
| `tcm_adme_profile` | 批量 RDKit 描述符与 Lipinski / Veber / Egan / QED。与 TCMSP 的 OB/DL 不是同一口径 |
| `tcm_network_simulation` | 活网络上的敲除、累积敲除和蒙特卡洛对照 |
| `dock_batch_screening` | 单受体、多配体的快速对接分诊，默认 exhaustiveness 4 |
| `tcm_multi_path_run` | 2–8 个候选并行走共识、ADME 和可选对接，再做启发式排名 |

## 证据与出图（4）

| 工具 | 作用 |
|---|---|
| `tcm_literature_mine` | PubMed 共现计数。文献计量信号，不是因果证明 |
| `tcm_formula_analysis` | 用本地 SymMap 索引比较药对或复方的成分集合 |
| `dock_rescore` | 用亲和能谱和口袋锚定率对批量对接做启发式精排 |
| `tcm_network_figure` | 3200×2200 网络图，固定 seed 的自研力导向布局 |

## P1 与 P2（4）

| 工具 | 作用 |
|---|---|
| `tcm_graph_embed` | 带偏随机游走、PMI 和截断 SVD。拓扑排序，不是功能验证 |
| `tcm_scrna_channel` | GEO 单细胞数据集发现。不返回表达矩阵 |
| `tcm_admet_predict` | ESOL、BBB、PPB、Caco-2、hERG 的本地近似 |
| `tcm_trials_match` | ClinicalTrials.gov 试验数、分期和 NCT。转化上下文，不是疗效证明 |

## 自举（1）

| 工具 | 作用 |
|---|---|
| `tcm_dependency_status` | Python 依赖、命令行工具和 Vina 解析结果。`action=install` 才会安装 |

## 数据整理（6）

这些工具在本地运行，不访问网络。整理结果用来准备下一步检索。别名、MeSH 对照和剂量比例都不是靶点收录，也不是疗效。

| 工具 | 作用 |
|---|---|
| `tcm_clean_table` | 把粘贴的表收成表头和行。空行去掉。列数不一致就报错 |
| `tcm_normalize_ids` | 认出 CID、CAS、InChIKey、UniProt、Ensembl。CAS 核对校验位。裸数字不会当成 CID |
| `tcm_normalize_structure` | 检查 SMILES。有 RDKit 时给出规范 SMILES 和 InChIKey；没有则只做字符检查并写明 |
| `tcm_normalize_genes` | 把 TNF-α、COX-2、IL-8 等常见写法收成一个 HGNC 符号。家族名保持歧义 |
| `tcm_normalize_disease` | 把少量常见病名对到已核对的 MeSH 标题。表外病名保持 unmapped |
| `tcm_convert_dose` | 按点名的度量衡把剂量换成克，并给出本次输入的重量比 |

## TCMSP（1）

| 工具 | 作用 |
|---|---|
| `tcm_tcmsp_search` | 读 TCMSP 旧站的药材表和成分表。`ob_dl_filter` 只筛本页的 OB、DL。不是疗效，这一页没有靶点 |

## 缓存

下列 20 个工具的成功响应进入 TTL 缓存：`tcm_pubchem_lookup`、`tcm_kegg_compound`、`tcm_chembl_lookup`、`tcm_ensembl_lookup`、`tcm_search_disease_targets`、`tcm_string_ppi`、`tcm_tcmbank_search`、`tcm_ctd_gene_interactions`、`tcm_get_gene_info`、`tcm_ncbi_gene_search`、`tcm_enrichment_analysis`、`tcm_npass_search_compound`、`tcm_get_structure`、`tcm_dgidb_interactions`、`tcm_gwas_associations`、`tcm_symmap_lookup`、`tcm_kegg_disease_pathways`、`tcm_wikidata_lookup`、`tcm_uniprot_idmap`、`tcm_tcmsp_search`。
