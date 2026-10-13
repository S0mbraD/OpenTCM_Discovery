# 工具总表

统一入口 `mcp_server/unified_mcp.py` 注册下面 69 个工具。带参调用使用 `{"params": {...}}`。漏了 `params`，返回的是校验错误。只读检索的响应带 `source` 与 `queried_at`。失败时看 `error`，或正文是否以 `Error:` 开头。空列表表示这次没有行。字段细节以各工具 docstring 为准；模块级决策见 `modules/`。

下面各表只说明工具实际做什么，以及返回里先看哪几个字段。不要把工具名写成结论。单独运行 `open_tcm_discovery_mcp.py` 只有检索族，HTTP 默认端口是 8000。69 个工具要跑统一入口，HTTP 默认 `127.0.0.1:8002`。推荐接法是 stdio。

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
| `tcm_analysis_topology` | 按会话重画分析网络和执行状态。实体节点少于 300 个时是圆角卡片，到 300 个按种类收成球壳。输出 Mermaid、SVG 和自包含 HTML |

`tcm_smart_run` 先看 `status`。`paused` 时读 `needs` 和 `session_id`，不要猜口袋残基。`deferred scripts` 里的命令这次没跑。`tcm_smart_plan` 只有步骤表。`tcm_analysis_topology` 的图例、检索通路、下一步建议和数据源状态是给智能体读的进度，不是疗效。下一步仍最多三条。至少两味药材且文本或节点详情里有剂量时，可以提示 `tcm_formula_weight`，并写明要先点名口径，重量比不是疗效。文献写明口袋时，可以同时提示 `tcm_check_docking_replay`，并写明这只是门槛、不跑对接。调用方文本里有靶点表或 predicted、recorded 时，可以提示 `tcm_read_target_table`。空图不另加这三条。药材少于两味时不提示方剂克重。没有口袋时不提示对接或复现门槛。步骤节点不计入那 300 个实体节点。浅暖宣纸底、木色边框。`tcm_self_iterate` 在用户点名之前不会改 `references/runtime_lessons.md`。

## 检索、网络与自迭代（27）

实现在 `open_tcm_discovery_mcp.py`。单独运行该文件不会带上其余工具，HTTP 默认端口是 8000。`tcm_check_database_status` 在 `refresh` 为 false 时返回保存的探测表，看 `snapshot_date`。这不是每个网站的现场状态。`tcm_cross_database_screen` 的共识分是几个库同时查到了该实体。化合物模式走 PubChem、ChEMBL 和 SymMap。基因模式走 STRING、Open Targets、SymMap 和 Pharos。它不把预测靶点计成已记录的相互作用。某个通道失败时，该通道是 `error`，不要把它算成「这个库收录了」。

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

`dock_vina_docking` 和 `dock_analyze_contacts` 的输出是提议的对接模型。没有文献口袋就不要调用。接触残基若只在计算里出现，报告里加 `*`，接触画虚线。`dock_check_environment` 用来看 Vina、Meeko、PyMOL、RDKit 各自是否可用，以及 Vina 解析到了哪条路径。环境缺一项时，检索工具不受影响。

## 并行层（4）

| 工具 | 作用 |
|---|---|
| `tcm_adme_profile` | 批量 RDKit 描述符与 Lipinski / Veber / Egan / QED。与 TCMSP 的 OB/DL 不是同一口径 |
| `tcm_network_simulation` | 活网络上的敲除、累积敲除和蒙特卡洛对照 |
| `dock_batch_screening` | 单受体、多配体的快速对接分诊，默认 exhaustiveness 4 |
| `tcm_multi_path_run` | 2–8 个候选并行走共识、ADME 和可选对接，再做启发式排名 |

`tcm_network_simulation` 的 z-score 是拓扑扰动，不是基因必需性。`tcm_adme_profile` 的 Lipinski、Veber、Egan 和 QED 不要和 TCMSP 的 OB、DL 写成同一道门。没装 RDKit 时，描述符算不出来，返回里会说明缺依赖，不会填一组看起来完整的数。

## 证据与出图（4）

| 工具 | 作用 |
|---|---|
| `tcm_literature_mine` | PubMed 共现计数。文献计量信号，不是因果证明 |
| `tcm_formula_analysis` | 用本地 SymMap 索引比较药对或复方的成分集合 |
| `dock_rescore` | 用亲和能谱和口袋锚定率对批量对接做启发式精排 |
| `tcm_network_figure` | 3200×2200 网络图，固定 seed 的自研力导向布局 |

`tcm_literature_mine` 至少要有基因或疾病之一，只给成分名会报错。计数字段是命中篇数。`tcm_formula_analysis` 看 `herbs_matched`、`herbs_missing` 和 `pairwise_ingredients` 里的 `jaccard`、`overlap_coeff`。索引找不到，或对上的药材少于两味，正文以 `Error:` 开头。高重叠那一档的标签字面是「功效相近」，仍是集合重叠，不是药效。

## P1 与 P2（4）

| 工具 | 作用 |
|---|---|
| `tcm_graph_embed` | 带偏随机游走、PMI 和截断 SVD。拓扑排序，不是功能验证 |
| `tcm_scrna_channel` | GEO 单细胞数据集发现。不返回表达矩阵 |
| `tcm_admet_predict` | ESOL、BBB、PPB、Caco-2、hERG 的本地近似 |
| `tcm_trials_match` | ClinicalTrials.gov 试验数、分期和 NCT。转化上下文，不是疗效证明 |

`tcm_admet_predict` 的每一项都带着近似模型的名字，不要写成实测 ADMET。`tcm_trials_match` 先看试验总数和分期，再看 NCT。命中试验不说明该中药成分有效。`tcm_scrna_channel` 只有数据集编号和实验设计，没有表达矩阵。`tcm_graph_embed` 是与种子基因的余弦相似度，不是功能验证。

## 自举（1）

| 工具 | 作用 |
|---|---|
| `tcm_dependency_status` | Python 依赖、命令行工具和 Vina 解析结果。`action=install` 才会安装 |

## 数据整理（6）

这些工具在本地运行，不访问网络，也不进入 `tcm_smart_run` 的自动链。整理结果用来准备下一步检索。别名、MeSH 对照和剂量比例都不是靶点收录，也不是疗效。

`tcm_convert_dose` 必须点名 `convention`：`metric`、`market_1959`、`han_tang`、`song_yuan_2025`、`ming_qing_2025`、`song_jin_yuan_2024`、`ming_qing_2024`。不写就报错，不会默认成某一个「两」。`market_1959` 的一两是 50 g，那是市秤。其余几套是各自文献里的古方折算，克数不一样。毫升和升不会换成克。返回看 `grams`、`ratio_to_min` 和 `citation`。`tcm_normalize_genes` 的家族名留在 `ambiguous`。`tcm_normalize_disease` 对不上时是 unmapped、ambiguous 或 nonspecific，不会编一个 MeSH 号。

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

调用时看 `query`。多行药材要给 `latin_name`，否则 `ingredients` 为空，药材行留在 `herbs`。成功时看 `ingredients`、`ingredients_matched`、`queried_at`。`ob_dl_filter` 默认关闭。打开后丢掉的行数在 `ob_dl_dropped`，门槛只用于本页 `ob`、`dl`。超时 20 秒，超时或 HTTP 5xx 再试一次。请求没完成时有 `error`，不补成分。

## 成分名对齐（1）

这个工具在本地运行，不访问网络，也不读 `data/herb_mol_index.json`，不进入自动链，也不进只读缓存。名称相同不是结构相同。InChIKey 全键相同只说明调用方给出的字符串一致。`ob`、`dl` 原样抄自 TCMSP 行，这里不筛选。结果不是靶点收录，也不是疗效。

返回先看 `n_matches`、`n_ambiguous` 和 `matches` 里的 `link`。`link` 只有 `inchikey`、`inchikey_block`、`mol_id`、`name`。`ambiguous` 里的行不要挑一条当成对上了。`ob_dl_filter_applied` 是 false。没有 `queried_at`。任一侧是空的，或单侧超过 2000 行，会报错。表里没有成分名、MOL_ID 或 InChIKey 列，也会报错。

| 工具 | 作用 |
|---|---|
| `tcm_align_tcmsp` | 把已有 TCMSP 成分行和调用方的名称、InChIKey 表对齐。先看全键，再看不冲突的 MOL_ID，再看连接层，最后看名称 |

## 靶点表读入（1）

这个工具在本地运行，不访问网络，不内置靶点库，不进入自动链，也不进只读缓存。预测行不并进已记录证据。`recorded_source_count` 不是 `tcm_cross_database_screen` 的在线共识，也不是疗效。

返回先看有没有 `Error:`。读成了就看 `n_recorded`、`n_predicted`、`n_unclassified`，再看 `recorded_pairs` 里的 `recorded_source_count`。这个数只统计 `support` 为 recorded、且来源写明一个名称的不同来源。`predicted_pairs` 不并进这个数。`unclassified` 行看 `reason`。没有 `queried_at`。列名是 `compound_id`、`gene`、`support`、`source`。缺列、空表或超过 2000 行会报错。含糊来源不拆开猜测。表上多出来的列不读。

| 工具 | 作用 |
|---|---|
| `tcm_read_target_table` | 按行读入调用方自备的靶点表。已记录行才计入同时证实的来源数，预测行单独列出 |

## 方剂克重（1）

`tcm_formula_weight` 在本地把一张方剂的各行剂量换成克，再合计。不访问网络，不进入 `tcm_smart_run` 的自动链，也不进只读缓存。口径必填，一次只接受一个：`metric`、`market_1959`、`han_tang`、`song_yuan_2025`、`ming_qing_2025`、`song_jin_yuan_2024`、`ming_qing_2024`。不写就报错，不会把各套「两」并成一个。公制和传统单位不能写在同一张方里。

炮制按原文保留，没写就是空，不会填成生。返回先看有没有 `Error:`。算成了就看 `total_grams`、每一行的 `grams`、`weight_ratio` 和 `processing`。`note` 写明重量比不是疗效，不是暴露量，也不是君臣佐使。这不是 `tcm_formula_analysis` 的集合重叠，那个工具仍然不读剂量。

| 工具 | 作用 |
|---|---|
| `tcm_formula_weight` | 按点名的一个口径把各行剂量换成克后合计，并原样保留炮制。重量比不是疗效 |

## 对接复现门槛（1）

`tcm_check_docking_replay` 只做离线检查，不运行对接，不下载 PDB，不进入自动链，也不进缓存。夹具不是已核对的公开复合物。通过检查只表示可以进入复现，结果仍是提议的对接模型。缺文献标识、缺口袋残基，或残基不是编号，会拒绝。不生成姿势，不复现实验亲和力。已核对的公开夹具仍是后续。深链的 exhaustiveness、种子、口袋规则和 Vina 查找顺序不因这个工具而改。

返回先看 `statement`，再看每条的 `status` 和 `eligible`。通过的条目 `status` 是可以进入对接复现，`output_kind` 是提议的对接模型。`docking_executed` 是 false。

| 工具 | 作用 |
|---|---|
| `tcm_check_docking_replay` | 检查文献标识和口袋残基编号。通过只表示可以进入复现，结果仍是提议的对接模型 |

## HERB 详情（1）

`tcm_herb_detail` 按调用方给出的 HERB 编号取详情。不进入 `tcm_smart_run` 的自动链，也不进只读缓存。通路顺序固定：先 `POST http://47.92.70.12/chedi/api/`。TLS eof、连接重置、超时算环境失败，再试一次这条 HTTP。HTTPS 留在列表里，只在环境失败后跳过，不把失败响应当成空结果，也不当成会返回数据的备用。门户页和 404 是方法失败，不换成别的主机。

返回先看 `error`。走通时看 `url`，它应是上面这条 HTTP。失败过的通路在 `failed_paths` 的 `name`。成分在 `ingredients`，只有编号、名称、别名、分子式、SMILES。不请求成分全表。MolWt、Drug_likeness、OB_score 不会出现在 ob、dl 里，类药性也不是 ob、dl。靶点在 `targets`。`target_note` 写明这一页没有已记录和预测的分列，不能算已记录证据。这些行不并进 `tcm_read_target_table` 的 recorded 计数。失败时成分和靶点都是空列表，不补行。

| 工具 | 作用 |
|---|---|
| `tcm_herb_detail` | 按 HERB 编号读 HTTP 详情。靶点不是已记录证据，类药性不是 ob、dl |

## 名称候选（1）

`tcm_broad_names` 只调用本地已有的名称候选函数。不访问网络，不进入自动链，也不进只读缓存。返回 `candidates`。索引命中、有出处的别名和字符串相近分开列。黄连和胡黄连、甘草和炙甘草不能并成一味。相近不是疗效。`picked_herb_id` 保持空。没有命中就不补行。

| 工具 | 作用 |
|---|---|
| `tcm_broad_names` | 列出本地名称候选。相近不是同一味，也不是疗效 |

## 缓存

下列 20 个工具的成功响应进入 TTL 缓存。报错不进缓存。成分名对齐、靶点表读入、六个整理工具、方剂克重、对接复现门槛、HERB 详情和名称候选不在这份名单里。命中缓存时，`queried_at` 仍是当初那次查询的时间。`tcm_pubchem_lookup`、`tcm_kegg_compound`、`tcm_chembl_lookup`、`tcm_ensembl_lookup`、`tcm_search_disease_targets`、`tcm_string_ppi`、`tcm_tcmbank_search`、`tcm_ctd_gene_interactions`、`tcm_get_gene_info`、`tcm_ncbi_gene_search`、`tcm_enrichment_analysis`、`tcm_npass_search_compound`、`tcm_get_structure`、`tcm_dgidb_interactions`、`tcm_gwas_associations`、`tcm_symmap_lookup`、`tcm_kegg_disease_pathways`、`tcm_wikidata_lookup`、`tcm_uniprot_idmap`、`tcm_tcmsp_search`。
