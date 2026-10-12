---
name: open-tcm-discovery
description: 'Unified TCM drug-discovery skill: disease-first screening (disease to KEGG/SymMap pathways, candidate herbs, OB-filtered ingredients, hypergeometric FDR), cross-database evidence screening (PubChem, ChEMBL, SymMap, STRING, Open Targets, Pharos), PPI network and hub analysis, dual-engine enrichment consensus, Wikidata/UniProt ID bridging, molecular library building with QC and dedup, AutoDock Vina docking, contact and H-bond analysis, geometric PPI modeling, PyMOL publication figures, runtime self-iteration. One 54-tool MCP server: planner, execution engine (auto-chain incl. ADMET/literature/trials), parallel layer, evidence expansion (PubMed, herb-pair, re-score, figures), P1 (node2vec, GEO scRNA, ADMET), P2 (trials matching), bootstrap (dependency check; optional bin/vina.exe). Use for TCM, 中药, targets, pathways, PPI networks, enrichment, docking, Vina, pocket, ulcerative colitis or any disease research, network pharmacology, evidence-graded in-silico modeling.'
name_cn: 中药药物发现统一技能（检索-分析-对接-自迭代-自主执行-并行多路）
description_cn: 疾病优先检索（疾病→通路→药材→OB成分→超几何FDR）、多库交叉证据筛选、PPI网络hub分析、双引擎富集共识、Wikidata/UniProt ID桥接、分子库构建（QC+去重）、Vina对接、接触残基分析、几何PPI建模、PyMOL论文图、运行时自迭代；单MCP 54工具：意图路由+动态规划+自主执行引擎（断点会话/响应缓存/结构分类器/报告生成）+并行层+证据与出图+P1/P2+依赖自举，Agent 凭自然语言目标即可自主串联在线深链；30–60分钟药材抓取由脚本另行执行
---

# 中药药物发现统一技能（TCM Drug Discovery · Unified）

> 一个技能覆盖「疾病检索 → 交叉证据 → 网络富集 → 分子库 → 对接建模 → 论文图 → 自迭代」。
> 54 个 MCP 工具（检索分析 27 + 对接建模 8 + 智能层 6 + 并行层 4 + 证据与出图 4 + P1 3 + P2 1 + 自举 1），另有 20 个脚本、11 步本地 DAG 和 14 份参考文档。工具名录见 `docs/TOOLS.md`。
> 智能层规划分析网络，并自动执行在线深链。30–60 分钟的药材成分抓取由 `scripts/disease_pathway_workflow.py` 另行运行，`depth=full` 时引擎只登记为延后步骤 D3。

## 自主使用协议（Agent 第一动作）

1. **定向**：`unified_overview` —— 一览模块、工具、状态健康、本协议
2. **规划**：`tcm_smart_plan`（goal 或 disease/compounds/genes 字段）—— 得到带决策点（D1-D6）的动态分析网络
3. **执行（深度）**：`tcm_smart_run(goal=...)` —— 单目标在线深链（M1 通路 → D1 分支后的 M3 网络/富集 → M2 交叉证据 → P1 ADMET → P0 文献共现 → P2 临床试验 → 前提齐全才进入 M5 对接）。`depth=full` 不在本工具内跑四步药材脚本，只把命令写入延后步骤 D3。缺文献口袋残基或 Vina 时暂停并给出续跑参数，不臆测。`session_id` 续跑时已完成步骤不重跑。Vina 解析顺序：参数 > `VINA_EXE` > `bin/vina.exe` > PATH
4. **执行（广度/并行）**：`tcm_multi_path_run(compounds=[...])` —— 多候选各自成臂并行跑完整链（共识→ADME→可选对接），横向对比表+综合排名；多配体对单一受体用 `dock_batch_screening`（并行批量 Vina+ADME 预过滤+排名）；靶点模块脆弱性用 `tcm_network_simulation`（knockout 扫描+蒙特卡洛对照+z-score）
5. **报告**：`tcm_report_generate(session_id=...)` —— 生成导师可审阅的 Markdown 研究简报（时间线/逐步结果/决策日志/证据分级声明/全量溯源）；论文证据链补 `tcm_literature_mine`（文献共现计数）与 `tcm_network_figure`（网络图）
6. **适应**：在每个决策点按结果分叉（hub 数量定富集、共识分定对接、亲和能定后续、复现红旗回查）
7. **迭代**：会话结束前 `tcm_analyze_runtime` + `dock_analyze_runtime`；新发现 → `tcm_report_insight` / `dock_report_insight`

**参数约定**：所有带参工具用 `{"params": {...}}` 嵌套传递。

## 意图路由表（说什么 → 走哪条链）

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
| 不确定/组合目标 | 全部 | `tcm_smart_plan`（自动识别并组链） |

## 强制纪律（任何模块都适用）

1. **实时检索**：技能内不预置疾病/基因/成分答案；一切结论来自当次 API 检索，输出带 `source` + `queried_at`；响应缓存仅存过去真实检索（TTL 限界，原始 queried_at 保留在体内，Error 不缓存）
2. **证据三级**：实验证据 > 结构模板 > 计算预测；对接输出一律 "proposed docking model"；预测残基 `*` + 虚线；结构分类（`tcm_classify_structure`）与网络模拟（`tcm_network_simulation` 的拓扑结论）与 ADME 描述符（`tcm_adme_profile`）均属计算预测层，发表前须交叉验证；多臂/批量的综合排名是**优先级启发式**，不是经验证的打分函数
3. **ADME 门**：OB≥30 / DL≥0.18，仅用 TCMSP 官方口径（HERB 的 DL 不可混用）；`tcm_adme_profile` 的结构描述符（MW/TPSA/LogP/Lipinski）是互补维度，不可与 TCMSP 门混谈结论
4. **对接口袋**：口袋残基必须来自文献，禁止臆测；执行引擎缺此输入时宁可暂停；批量筛选默认 exhaustiveness=4 为**快速分诊**，重点化合物须用 16 精对接复验
5. **原子写**：共享文件临时文件+os.replace，严禁并发写同一文件
6. **自迭代落库**：新发现（死路/端点变更/怪癖/复现问题）必须 `*_report_insight` 记录，会话结束跑分析

## MCP 服务器（AI Agent 接入）

```bash
python -m pip install -r requirements.txt   # 一键装全部 python 依赖

python mcp_server/unified_mcp.py --stdio     # 推荐：单连接 54 工具
python mcp_server/unified_mcp.py             # HTTP 模式（默认 8002）
```

**自举层**（bootstrap_layer.py）：`tcm_dependency_status`（依赖健康检查、缺失影响面、`action=install` 可选安装）。Vina 不强制随仓库分发；把 AutoDock Vina 1.2.5 官方二进制放到 `bin/vina.exe`，或设置 `VINA_EXE`。解析顺序：参数 > `VINA_EXE` > `bin/vina.exe` > PATH。见 `bin/README.md`

**智能层**：`unified_overview`（定向）/ `tcm_smart_plan`（动态规划器：规则式意图识别→带决策点的执行 DAG）/ `tcm_smart_run`（自主执行引擎：checkpoint 会话+断点续跑+决策点自动分叉，缺文献口袋残基时暂停）/ `tcm_report_generate`（研究简报）/ `tcm_cache_status`（响应缓存查看/清空，env TCM_CACHE_TTL 默认 3600s）/ `tcm_classify_structure`（SMILES 规则式结构分类：黄酮/蒽醌/香豆素/生物碱/萜类/甾体等，RDKit SMARTS 实测锚定）
**并行层**（parallel_layer.py）：`tcm_adme_profile`（批量理化+Lipinski/Veber/Egan/QED，名称活取 SMILES）/ `tcm_network_simulation`（STRING 活网络 knockout 扫描+累积敲除+蒙特卡洛对照+z-score）/ `dock_batch_screening`（多配体并行 Vina+ADME 预过滤+亲和能排名，快速分诊）/ `tcm_multi_path_run`（多臂并行分析网络：共识→ADME→可选对接，横向对比+综合排名）
**P0 证据与配伍扩展**（lit_formula_layer.py + rescore_figure_layer.py）：`tcm_literature_mine`（PubMed E-utilities 三元共现计数+逐年趋势，限速 0.4s/req；**共现=文献计量学信号非因果验证**）/ `tcm_formula_analysis`（药对/复方成分集合 Jaccard 互补度，本地 SymMap 索引 698 药材 25,975 成分，`data/herb_mol_index.json`；可选靶点层）/ `dock_rescore`（批量筛选精排：亲和能谱 mode-gap+文献口袋锚定率+复合分，非验证打分函数）/ `tcm_network_figure`（论文级网络图 3200x2200 PNG+PDF，hub 分层着色，图注含证据分级声明，固定 seed 可复现）
**P1 细胞分辨率与数据驱动层**（p1_layer.py）：`tcm_graph_embed`（node2vec 式图嵌入：带偏随机游走→PMI→截断SVD 纯 numpy，与种子基因余弦相似度排靶点+与中心性双视角共识；**拓扑视图非功能验证**）/ `tcm_scrna_channel`（GEO 单细胞数据集发现：esearch db=gds+esummary 活查，返回 GSE/设计/物种/样本数/关联PubMed；**HPA/GTEx 通道死路已探明，仅发现层**）/ `tcm_admet_predict`（本地 ADMET 近似面板：ESOL 水溶性(Delaney)/BBB盒式规则/PPB/Caco-2/hERG 代理；**ADMETlab REST 死路，全部标注近似模型**）
**P2 临床转化层**（p2_layer.py）：`tcm_trials_match`（ClinicalTrials.gov API v2 活查：疾病×靶点/化合物的试验总数+分期计数+NCT 表；**试验匹配=转化上下文，非候选疗效证明**；OpenMM MD 试点经评估延后非死路）
**检索分析族**：tcm_pubchem_lookup / tcm_kegg_compound / tcm_chembl_lookup / tcm_ensembl_lookup / tcm_search_disease_targets / tcm_string_ppi / tcm_tcmbank_search / tcm_ctd_gene_interactions / tcm_get_gene_info / tcm_ncbi_gene_search / tcm_enrichment_analysis / tcm_npass_search_compound / tcm_get_structure / tcm_dgidb_interactions / tcm_gwas_associations / tcm_check_database_status / tcm_audit_evidence / tcm_symmap_lookup / tcm_kegg_disease_pathways / tcm_hypergeometric_test / tcm_cross_database_screen（并发）/ tcm_network_analysis / tcm_multi_enrichment / tcm_wikidata_lookup / tcm_uniprot_idmap / tcm_report_insight / tcm_analyze_runtime
**对接建模族**：dock_check_environment / dock_fetch_structure / dock_vina_docking / dock_analyze_contacts / dock_geometric_ppi / dock_report_insight / dock_analyze_runtime / dock_compare_experiments

- 评估基准：mcp_server/evaluation.xml（实测锚定）
- 分层实现：`unified_mcp.py` 持有 `unified_overview` 与 `tcm_smart_plan`；`smart_layer.py` 持有缓存、结构分类、`tcm_smart_run`、研究报告；`parallel_layer.py` / `lit_formula_layer.py` + `rescore_figure_layer.py` / `p1_layer.py` / `p2_layer.py` / `bootstrap_layer.py` 为其余层。本地索引：`data/herb_mol_index.json`。网络图为自研 Fruchterman–Reingold 布局。历史文档中的 tcm-database-query / structure-docking-analysis 即本技能的 `tcm_*` / `dock_*` 工具族

## 模块与资产导航

| 模块 | 文档 | 一句话 |
|---|---|---|
| M1 疾病优先检索 | modules/m1_disease_screening.md | 疾病→通路→药材→成分→FDR |
| M2 交叉证据 | modules/m2_target_evidence.md | 多库共识+TDL+ID桥接 |
| M3 网络与富集 | modules/m3_network_enrichment.md | hub排名+双引擎共识 |
| M4 分子库构建 | modules/m4_molecular_library.md | 14步+QC五维+五层去重 |
| M5 对接建模 | modules/m5_docking_modeling.md | Vina/接触/几何PPI+台账 |
| M6 渲染出图 | modules/m6_figures_documentation.md | 论文图+组合图+证据分级 |
| M7 自迭代 | modules/m7_self_iteration.md | 遥测→洞察→建议更新闭环 |

- `scripts/`：20 个实测脚本（检索 14 + 对接 6，argparse 参数化，可独立运行）
- `workflow_engine/`：DAG 自动化工作流引擎（11 步疾病可配置 + 可视化，`run_server.py`）
- `references/`：14 份按域文档（通道端点/陷阱/风格/证据分级/自迭代机制），**用 grep 定位库名再读该节**
- 状态目录（运行时自动维护）：`~/.tcm_mcp_state`、`~/.dock_mcp_state`（遥测/洞察/实验台账；env 可覆盖）

## 环境要点

Python 3.10+。Windows 先执行 `$env:PYTHONUTF8="1"`。

```bash
python -m pip install -r requirements.txt
```

本地 DAG 工作流另需 `python -m pip install -r requirements-workflow.txt`（pandas、PyYAML）。PyMOL 只在结构出图时需要。对接前运行 `dock_check_environment`。科学边界、两条运行时和 54 个工具的字段级说明见 `docs/`。
