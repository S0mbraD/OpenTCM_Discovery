---
name: open-tcm-discovery
description: 'Unified TCM drug-discovery skill: disease-first screening (disease to KEGG/SymMap pathways, candidate herbs, OB-filtered ingredients, hypergeometric FDR), cross-database evidence screening (PubChem, ChEMBL, SymMap, STRING, Open Targets, Pharos), PPI network and hub analysis, dual-engine enrichment consensus, Wikidata/UniProt ID bridging, molecular library building with QC and dedup, AutoDock Vina docking, contact and H-bond analysis, geometric PPI modeling, PyMOL publication figures, runtime self-iteration, local cleanup of tables, identifiers, SMILES, gene symbols, disease names, and doses. One 63-tool MCP server: planner, execution engine (auto-chain incl. ADMET/literature/trials), parallel layer, evidence expansion (PubMed, herb-pair, re-score, figures), P1 (node2vec, GEO scRNA, ADMET), P2 (trials matching), bootstrap (dependency check; optional bin/vina.exe), prep layer. Use for TCM, 中药, targets, pathways, PPI networks, enrichment, docking, Vina, pocket, ulcerative colitis or any disease research, network pharmacology, evidence-graded in-silico modeling.'
name_cn: "中药小分子检索、通路筛选与对接"
description_cn: "中药小分子的检索和筛选。从疾病查通路、药材和 OB 成分，再做超几何 FDR。多库交叉核对靶点。PPI 网络、hub 和双引擎富集。Wikidata、UniProt 标识互转。分子库 QC 和去重。Vina 对接、接触残基、几何 PPI、PyMOL 论文图。运行时自迭代。表格、标识、SMILES、基因符号、疾病名和剂量可以在本地整理。63 个工具在同一个 MCP 服务里，包括规划、执行、断点续跑、缓存、结构分类、报告、并行比较、文献出图、P1、P2、依赖检查和数据整理。用中文说明疾病和成分，就能把在线步骤串起来。药材抓取要 30–60 分钟，由脚本另跑。depth=full 只登记命令。"
---

# 中药小分子检索与对接

这个技能做中药小分子的检索、筛选和对接。顺序是：疾病检索，多库核对，网络和富集，分子库，对接，论文图，运行之后的自迭代。

MCP 里一共 63 个工具。检索分析 27 个，对接建模 8 个，智能层 8 个，并行层 4 个，证据与出图 4 个，P1 有 3 个，P2 有 1 个，依赖自举 1 个，数据整理 6 个，TCMSP 旧站检索 1 个。另外有 20 个脚本、11 步本地 DAG、15 份参考文档。工具名录见 `docs/TOOLS.md`。

智能层负责出计划，并执行在线步骤。抓药材成分要 30–60 分钟，用 `scripts/disease_pathway_workflow.py` 另跑。`depth=full` 时，引擎只把这条命令登记为延后步骤 D3，不在同一次请求里抓取。

## 先做这几步

1. **看总览**。调用 `unified_overview`，看模块、工具和依赖是否正常，并读本文件的约定。
2. **出计划**。调用 `tcm_smart_plan`。可以传 goal，也可以传 disease、compounds、genes。计划里带决策点 D1–D6。
3. **按深度执行**。调用 `tcm_smart_run(goal=...)`。在线链是：M1 通路；D1 分支之后做 M3 网络或富集；M2 多库核对；P1 ADMET；P0 文献共现；P2 临床试验。文献口袋和 Vina 都齐了，才进入 M5 对接。`depth=full` 不在这次调用里跑四步药材脚本，只把命令写入延后步骤 D3。缺文献口袋残基或 Vina 时暂停，并写出续跑参数。不要猜口袋。用同一个 `session_id` 续跑时，已完成的步骤不重跑。Vina 按这个顺序找：调用参数，然后 `VINA_EXE`，然后 `bin/vina.exe`，最后 PATH。
4. **比较多个候选**。`tcm_multi_path_run(compounds=[...])` 让每个候选各跑一条链（共识、ADME、可选对接），再出横向对比和综合排名。一个受体对多个配体，用 `dock_batch_screening`：并行 Vina，先做 ADME 预过滤，再排名。看网络里哪个节点脆，用 `tcm_network_simulation`：knockout 扫描、蒙特卡洛对照、z-score。
5. **写报告**。`tcm_report_generate(session_id=...)` 写出 Markdown 研究简报，给导师审阅。简报里有时间线、逐步结果、决策日志、证据分级和每一步的来源。文献共现计数用 `tcm_literature_mine`。网络图用 `tcm_network_figure`。
6. **按结果分叉**。hub 有多少，决定做不做富集。共识分决定进不进对接。亲和能决定还要不要往下看。出现复现红旗，先回去查受体准备。
7. **收尾**。会话结束前跑 `tcm_analyze_runtime` 和 `dock_analyze_runtime`。新的运行观察交给 `tcm_report_insight` 或 `dock_report_insight`。写入 `references/runtime_lessons.md` 必须等用户点名，见文末。

**参数**：带参数的工具把参数放在 `{"params": {...}}` 里。

## 说什么，用哪个工具

下表按你说的话选入口。括号里是中英文关键词。

| 用户目标（中/英关键词） | 模块 | 入口 |
|---|---|---|
| 疾病相关通路/候选药材/有效成分（disease, colitis, 通路, 疾病） | M1 | `tcm_kegg_disease_pathways` → 四步脚本 |
| 化合物或靶点的多库验证/共识/TDL（compound, target, 靶点, 交叉验证） | M2 | `tcm_cross_database_screen`（并发批量） |
| 网络图/hub基因/富集/通路共识（network, hub, 富集, 网络） | M3 | `tcm_network_analysis` → `tcm_multi_enrichment` |
| 网络脆弱性/敲除模拟/鲁棒性（knockout, 扰动, 脆弱, simulation） | M3+ | `tcm_network_simulation`（knockout+蒙特卡洛+z-score） |
| 理化性质/类药性/Lipinski/ADME 描述符（drug-likeness, Lipinski, TPSA, 理化） | M2+ | `tcm_adme_profile`（批量，RDKit） |
| 分子库/成分整理/去重/结构分类/QC（library, 去重, QC, 分子库） | M4 | `molecular_library_builder.py`（14 步） |
| 对接/结合模式/口袋/亲和能/复合物（docking, Vina, 对接, 口袋） | M5 | `dock_check_environment` → `dock_vina_docking` |
| 虚拟筛选/多化合物批量对接排名（screening, 筛库, 批量对接, virtual screening） | M5+ | `dock_batch_screening`（并行+ADME预过滤+排名） |
| 多候选横向比较/并行比较（compare, 比较, 多化合物, 哪个更好） | 并行层 | `tcm_multi_path_run`（多臂并行+综合排名） |
| 论文图/组合图/渲染/证据声明（figure, 渲染, 论文图） | M6 | `make_figure_pml.py` → `compose_panel.py` |
| 会话收尾/发现新情况/长期运行（runtime, 自迭代, 死路） | M7 | `*_analyze_runtime` → `*_report_insight` |
| 表格、标识、基因符号、疾病名、剂量或 SMILES（table, SMILES, InChIKey, 基因符号, 剂量, MeSH） | 数据整理 | `tcm_clean_table`、`tcm_normalize_ids`、`tcm_normalize_genes`、`tcm_normalize_disease`、`tcm_convert_dose`、`tcm_normalize_structure` |
| 不确定/组合目标 | 全部 | `tcm_smart_plan`（自动识别并组链） |

## 写结果时要守的规矩

1. **当次检索**。仓库里不预置疾病、基因或成分的答案。结论来自这次的 API 查询。输出带 `source` 和 `queried_at`。响应缓存只保存过去真实查到的结果，有 TTL。原来的 `queried_at` 留在结果里。报错不缓存。
2. **证据分三层**。实验证据高于结构模板，结构模板高于计算预测。对接输出一律写 "proposed docking model"。预测残基加 `*`，接触用虚线。`tcm_classify_structure` 的结构分类、`tcm_network_simulation` 的拓扑结论、`tcm_adme_profile` 的 ADME 描述符，都算计算预测。发表前要另行核对。多臂比较和批量筛选的综合排名是优先级启发式，不是验证过的打分函数。
3. **ADME 门槛**。OB≥30，DL≥0.18。只用 TCMSP 的口径。HERB 的 DL 不能混进来。`tcm_adme_profile` 给出的 MW、TPSA、LogP、Lipinski 是另一套描述符。不要和 TCMSP 这道门写成同一个结论。
4. **口袋**。口袋残基必须来自文献，不能猜。执行引擎缺口袋就暂停。批量筛选默认 exhaustiveness=4，只做快速分诊。要重点看的化合物，用 exhaustiveness 16 再对接一遍。
5. **写文件**。共享文件先写临时文件，再用 `os.replace` 换上。不要并发写同一个文件。
6. **自迭代**。死路、端点变更、接口怪癖和复现问题，用 `*_report_insight` 记下来。会话结束时跑分析。追加到 `references/runtime_lessons.md` 时，必须用户点名那个 id。
7. **整理不是结论**。`tcm_clean_table`、`tcm_normalize_ids`、`tcm_normalize_structure`、`tcm_normalize_genes`、`tcm_normalize_disease` 和 `tcm_convert_dose` 只整理用户已经给出的文本。基因别名、MeSH 对照和剂量比例不能写成靶点收录或疗效。

## MCP 服务器

```bash
python -m pip install -r requirements.txt   # 安装 requirements.txt 里的 Python 依赖

python mcp_server/unified_mcp.py --stdio     # 推荐。一次连接 63 个工具
python mcp_server/unified_mcp.py             # HTTP，默认端口 8002
```

**自举**（`bootstrap_layer.py`）：`tcm_dependency_status` 检查依赖是否齐全，以及缺了会影响什么。`action=install` 可以安装缺失的 Python 包。Vina 不随仓库分发。把 AutoDock Vina 1.2.5 的官方二进制放到 `bin/vina.exe`，或设置 `VINA_EXE`。查找顺序：调用参数，`VINA_EXE`，`bin/vina.exe`，PATH。详见 `bin/README.md`。

**智能层**：`unified_overview` 看总览。`tcm_smart_plan` 按规则识别意图，生成带决策点的步骤。`tcm_smart_run` 是执行引擎，会话可以断点续跑，到决策点自动分叉，缺文献口袋残基就暂停。`tcm_report_generate` 写研究简报。`tcm_cache_status` 查看或清空响应缓存，环境变量 `TCM_CACHE_TTL` 默认 3600 秒。`tcm_classify_structure` 用 SMILES 做规则分类，类别有黄酮、蒽醌、香豆素、生物碱、萜类、甾体等，规则用 RDKit SMARTS 对过。`tcm_self_iterate` 复核运行观察，只有用户点名的 id 才会追加到 `references/runtime_lessons.md`。`tcm_analysis_topology` 按会话重画分析网络，标出数据库、文献库、方剂索引和执行状态。

**并行层**（`parallel_layer.py`）：`tcm_adme_profile` 批量算理化性质，以及 Lipinski、Veber、Egan、QED；给出成分名时当场去取 SMILES。`tcm_network_simulation` 在 STRING 的实时网络上做 knockout、累积敲除和蒙特卡洛对照，并算 z-score。`dock_batch_screening` 让多个配体并行做 Vina，先按 ADME 预过滤，再按亲和能排名，用于快速分诊。`tcm_multi_path_run` 让多个候选并行，顺序是共识、ADME、可选对接，然后出对比表和综合排名。

**P0：文献、方剂、精排和出图**（`lit_formula_layer.py`、`rescore_figure_layer.py`）：`tcm_literature_mine` 用 PubMed E-utilities 数三元共现，并看逐年趋势，限速 0.4 秒一次请求。共现是文献计量，不是因果。`tcm_formula_analysis` 算药对或复方成分集合的 Jaccard。本地 SymMap 索引有 698 味药材、25,975 个成分，文件是 `data/herb_mol_index.json`，也可以看靶点层。`dock_rescore` 给批量筛选做精排，看亲和能谱的 mode-gap 和文献口袋锚定率，再给一个复合分。这不是验证过的打分函数。`tcm_network_figure` 出 3200×2200 的 PNG 和 PDF，hub 分层着色，图注里写证据分级，随机种子固定，图可以复现。

**P1**（`p1_layer.py`）：`tcm_graph_embed` 做 node2vec 式图嵌入。带偏随机游走，再算 PMI，用 numpy 做截断 SVD。按与种子基因的余弦相似度排靶点，并和中心性对照。这是拓扑上的排序，不是功能验证。`tcm_scrna_channel` 在 GEO 里找单细胞数据集，用 esearch（`db=gds`）和 esummary 当场查询，返回 GSE、实验设计、物种、样本数和相关 PubMed。HPA 和 GTEx 这条通道已经确认走不通，这里只做数据集发现。`tcm_admet_predict` 是本地 ADMET 近似，包括 ESOL 水溶性（Delaney）、BBB 盒式规则、PPB、Caco-2、hERG 代理。ADMETlab 的 REST 接口已经确认不能用，结果都标成近似模型。

**P2**（`p2_layer.py`）：`tcm_trials_match` 查 ClinicalTrials.gov API v2，当场请求。按疾病和靶点或化合物，返回试验总数、分期计数和 NCT 表。试验匹配只说明转化方面有哪些试验，不能当成候选药已经有效。OpenMM 分子动力学试点评估之后先不做，这条不算死路。

**数据整理**（`prep_layer.py`）：六个工具都不访问网络。`tcm_clean_table` 整理粘贴的表。`tcm_normalize_ids` 检查 CID、CAS 校验位、InChIKey、UniProt 和 Ensembl，裸数字不会当成 CID。`tcm_normalize_structure` 检查 SMILES；装了 RDKit 才写规范 SMILES 和 InChIKey，没装就写明。`tcm_normalize_genes` 把常见别名收成一个符号，家族名保持歧义。`tcm_normalize_disease` 只对到本地核对过的 MeSH 标题，没有的病名保持 unmapped。`tcm_convert_dose` 按点名的度量衡换成克。重量比不是药效。这些工具不进入 `tcm_smart_run` 的自动链。

**TCMSP**（`tcmsp_layer.py`）：`tcm_tcmsp_search` 只请求 `https://old.tcmsp-e.com/`。先读首页 token，再读药材表；只命中一行，或给了 `latin_name`，才读成分表。超时 20 秒，超时或 HTTP 5xx 再试一次。`ob_dl_filter` 打开时只保留本页 OB≥30 且 DL≥0.18 的行。这道门不用于其他库。成分表不是疗效，这一页也没有靶点。

**检索分析族**：tcm_pubchem_lookup / tcm_kegg_compound / tcm_chembl_lookup / tcm_ensembl_lookup / tcm_search_disease_targets / tcm_string_ppi / tcm_tcmbank_search / tcm_ctd_gene_interactions / tcm_get_gene_info / tcm_ncbi_gene_search / tcm_enrichment_analysis / tcm_npass_search_compound / tcm_get_structure / tcm_dgidb_interactions / tcm_gwas_associations / tcm_check_database_status / tcm_audit_evidence / tcm_symmap_lookup / tcm_kegg_disease_pathways / tcm_hypergeometric_test / tcm_cross_database_screen（并发）/ tcm_network_analysis / tcm_multi_enrichment / tcm_wikidata_lookup / tcm_uniprot_idmap / tcm_report_insight / tcm_analyze_runtime
**对接建模族**：dock_check_environment / dock_fetch_structure / dock_vina_docking / dock_analyze_contacts / dock_geometric_ppi / dock_report_insight / dock_analyze_runtime / dock_compare_experiments

评估记录在 `mcp_server/evaluation.xml`，写的是实测，不是推断。代码分层如下。`unified_mcp.py` 里有 `unified_overview` 和 `tcm_smart_plan`。`smart_layer.py` 里有缓存、结构分类、`tcm_smart_run` 和研究报告。其余层在 `parallel_layer.py`、`lit_formula_layer.py`、`rescore_figure_layer.py`、`p1_layer.py`、`p2_layer.py`、`bootstrap_layer.py`、`iteration_layer.py`、`topology_layer.py`、`prep_layer.py`、`tcmsp_layer.py`。本地索引是 `data/herb_mol_index.json`。网络图用自研的 Fruchterman–Reingold 布局。以前文档里的 tcm-database-query 和 structure-docking-analysis，就是现在的 `tcm_*` 和 `dock_*`。

## 模块与资产

| 模块 | 文档 | 一句话 |
|---|---|---|
| M1 疾病优先检索 | modules/m1_disease_screening.md | 疾病→通路→药材→成分→FDR |
| M2 交叉证据 | modules/m2_target_evidence.md | 多库共识+TDL+ID桥接 |
| M3 网络与富集 | modules/m3_network_enrichment.md | hub排名+双引擎共识 |
| M4 分子库构建 | modules/m4_molecular_library.md | 14步+QC五维+五层去重 |
| M5 对接建模 | modules/m5_docking_modeling.md | Vina/接触/几何PPI+台账 |
| M6 渲染出图 | modules/m6_figures_documentation.md | 论文图+组合图+证据分级 |
| M7 自迭代 | modules/m7_self_iteration.md | 遥测→洞察→用户确认后写入教训 |

`scripts/` 里有 20 个脚本：检索 14 个，对接 6 个。都用 argparse，可以单独跑。

`workflow_engine/` 是本地 DAG。疾病可以配置，一共 11 步，用 `run_server.py` 启动，带看板。

`references/` 有 15 份文档，按数据库和主题记端点、死路、作图风格、证据分级、自迭代和已经确认的教训。用库名搜索，再读那一节。

运行时状态写在 `~/.tcm_mcp_state` 和 `~/.dock_mcp_state`，包括遥测、洞察和实验台账。路径可以用环境变量改。

## 环境

Python 3.10 及以上。Windows 先执行 `$env:PYTHONUTF8="1"`。

```bash
python -m pip install -r requirements.txt
```

本地 DAG 还要装 `requirements-workflow.txt`（pandas、PyYAML）：

```bash
python -m pip install -r requirements-workflow.txt
```

PyMOL 只在画结构图时需要。对接前先跑 `dock_check_environment`。科学边界、两条运行路径，以及 63 个工具的字段说明，写在 `docs/`。

## 每次运行后的自迭代

`tcm_smart_run` 的返回里带有 iteration packet。任务开始时先读 `references/runtime_lessons.md`。一次运行结束后：

1. 若 packet 的 open 为空，停止，不要改技能文件。
2. 调用 `tcm_self_iterate`，`action=review`，向用户展示 `strength=strong` 的 id、标题和证据。
3. 只把用户点名的 id 交给 `action=apply` 或 `action=reject`。
4. `apply` 只追加 `references/runtime_lessons.md`。不要据此修改工具代码、OB/DL 门槛、口袋规则、统计函数或数据源路由。
5. 这些条目是运行观察。下次遇到相符的失败时向用户说明，不要把它们写成疗效或靶点结论。

## 分析网络

`tcm_smart_run` 在每一步之后重画分析网络。图里有两类节点。一类是疾病、通路、基因、成分、药材、方剂集合、文献和试验。另一类是 KEGG、SymMap、STRING、PubChem、ChEMBL、Pharos、Open Targets、PubMed、ClinicalTrials.gov 和本地药材成分索引。目标里至少有两味药材时，引擎先用本地索引算成分集合重叠，再继续在线检索。

返回里的 Mermaid、`sessions/<id>.topology.svg` 和自包含的 `sessions/<id>.topology.html` 表示网络怎样长出来，以及哪一步完成了、哪一步失败了。需要重画时调用 `tcm_analysis_topology`。文献共现、集合重叠、试验登记和收录计数标在边上。这些是检索关系，不是疗效，也不是配伍结论。

小图是深色底上的圆角卡片，卡片里写名称和一行详情，边用箭头和流动虚线表示方向，颜色区分 ready、pending、done、failed、skipped、deferred。实体节点满 300 个以后，改成按类别分组的球面，同一类在同一个球上，球与球之间仍用带方向的动效弧连接。这是检索过程的图，不是疗效结论。
