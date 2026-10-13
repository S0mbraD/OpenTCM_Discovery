# Changelog

本项目变更记录。版本遵循语义化版本（SemVer），日期为实际开发日。完整演进脉络见 `references/iteration_history.md`。

## [Unreleased]

### Added

- 进度说明 `docs/PROJECT_PROGRESS.md`：写明当前检索如何收成分析网、证据口径，以及尚未接入的部分
- `tcm_tcmsp_search`：请求 TCMSP 旧站 `old.tcmsp-e.com`。2026-10-13 探测里，只有这里还能返回带 `ob`、`dl` 的成分表。`ob_dl_filter` 只筛这些字段。失败不补记录
- 分析网络拓扑：小图改为圆角卡片（名称、详情、状态色、有向动效边）；实体节点满 300 后改为按类别分组的球面，并同时写出 `.topology.svg` 与自包含 `.topology.html`
- 分析网络改为浅暖宣纸底和木色边框。SVG、自包含 HTML，以及 `tcm_analysis_topology` 和 `tcm_smart_run` 的拓扑摘要增加图例、检索通路、下一步建议和数据源状态
- 数据整理 6 个本地工具：`tcm_clean_table`、`tcm_normalize_ids`、`tcm_normalize_structure`、`tcm_normalize_genes`、`tcm_normalize_disease`、`tcm_convert_dose`。它们整理表格、标识、SMILES、基因符号、疾病名和剂量，不访问网络
- `tcm_analysis_topology`：检索过程中维护疾病–通路–基因–成分–药材–方剂分析网络，并把数据库、文献库和本地方剂索引画进同一张可重放的拓扑图
- `tcm_self_iterate`：每次运行结束后，连接的智能体复核本地遥测和洞察。只有用户点名的 id 会追加到 `references/runtime_lessons.md`，不改工具代码、证据门槛或数据源路由
- 主页改为完整的中文项目介绍，并补充数据来源说明 `docs/DATA_SOURCES.md` 与技术路线 `docs/ROADMAP.md`
- `tcm_core`：超几何尾概率、Benjamini–Hochberg 校正、成分名提取、口袋残基解析和索引路径
- 不访问网络的单元测试 `tests/`，并加入持续集成
- 开源文档：`docs/README.md`、`docs/SCIENTIFIC_SCOPE.md`、`docs/WORKFLOWS.md`、`docs/TOOLS.md`、`NOTICE`、`bin/README.md`
- GitHub Issue / PR 模板、CI 自检 `scripts/check_repo.py`
- 本地 DAG 的额外依赖文件 `requirements-workflow.txt`

### Changed

- 说明文档写得更具体：补上调用步骤、返回里要看的字段，以及失败时怎么判断。科学口径、工具行为和数据源路由没有改

### Fixed

- 疾病筛选脚本在超几何检验遇到不可能的命中数时，不再把概率算成 1。MCP 工具与脚本改为调用同一实现。扑克牌算例 P(至少 3 张红心) = 241098/2598960
- 文档与入口注释中的工具数、默认端口和执行链与代码对齐：统一服务器 54 个工具，StreamableHTTP 默认 `127.0.0.1:8002`
- `tcm_smart_run` 在 `depth=full` 时把四步药材工作流登记为延后步骤 D3，并给出可执行命令
- `tcm_formula_analysis` 读取 `<仓库>/data/herb_mol_index.json`（可用 `TCM_HERB_INDEX` 覆盖），不再查找本机绝对路径
- 部署模板改为 `unified_mcp.py`。检索族文件单独运行仍是子集服务器

## [3.1.0] - 2026-10-12

### Added

- 第 54 个工具 `tcm_dependency_status`：10 项依赖健康检查、缺失影响面矩阵、可选自动安装
- `requirements.txt` 固化全量依赖；`tcm_smart_run` 自动链新增 5.1 ADMET / 5.2 文献共现 / 5.3 临床试验三步（浮点步号断点续跑）
- goal 文本自动提取 27 个 TCM 成分白名单；`tcm_smart_plan` 词表升级为七组关键词路由

### Changed

- AutoDock Vina 1.2.5 官方 Windows 二进制内置至 `bin/`，解析顺序统一为参数 > `VINA_EXE` > 内置 > PATH，五处调用点同步改造
- networkx 依赖移除：网络图布局改为纯 numpy+matplotlib 自研 FR 力导向实现
- 项目更名 `open-tcm-discovery`：技能目录、MCP 实现模块、SKILL.md、部署配置、README BibTeX key 共 8 处变更；工具名前缀 `tcm_`/`dock_` 保持不变

### Fixed

- 综合测试 19/19（修复重写残留的 NameError 真 bug）

### Verified

- 冒烟测试 7/7（53 秒，退出码 0）；自动链在线检索全部 200 OK，无假数据
- 改名后残留扫描 0、quick_validate 通过、注册成功、E2E 新路径加载 54 工具
- 评估基准 32 → 36 题

## [3.0.0] - 2026-10-10 至 2026-10-12

### Added

- 统一技能 `tcm-drug-discovery`：原检索技能与结构分析技能合并为单一 MCP，工具数 37 → 41 → 45 → 49 → 52 → 53 → 54
- 智能编排层：`tcm_smart_plan` / `tcm_smart_run`（目标自动规划、断点续跑）、缓存、结构分类、报告生成
- 并行计算层：多化合物并行对接、批量筛选、多臂比较、ADME 批量
- 证据扩展层：文献三元共现、药对分析、重打分（`dock_rescore`）、网络图渲染
- P1 层：`tcm_graph_embed`（node2vec 式图嵌入）、`tcm_scrna_channel`（GEO 单细胞发现）、`tcm_admet_predict`（本地 ADMET 近似面板）
- P2 转化层：`tcm_trials_match`（ClinicalTrials.gov v2 试验匹配）
- 结构分析技能（10.10）：fetch_structure / run_vina / analyze_contacts / geometric_docking / make_figure_pml / compose_panel 六个实测脚本固化，三层证据分级规范

### Changed

- `herb_mol_index.json` 全量构建：698/703 味药材、25,975 个成分映射（5 味因 SymMap 502 跳过，断点续跑有效）
- 交叉分析套件五件：多库证据合并与共识分、PPI 网络 Hub 排名、双引擎富集共识、Wikidata 解析、UniProt ID 映射
- 数据循环闭环：检索 → 网络 → 富集 → 交叉 → 对接 → 接触残基 → 回查文献，全程 source 溯源

### Verified

- 合并前双技能 e2e 17/17（berberine 三库共识、TNF 四库、Vina 复现 -8.809 对历史 -8.816、接触残基吻合）
- P1 单元 11/11 + 集成 11/11；P2 e2e 7/7（berberine 命中 Phase 4 与 Phase 1 真实试验）
- UC 全链实战 12/12（134 秒）：4 化合物并行对接 53 秒、dock_rescore 精排、文献共现 39 篇、网络图 9/10 论文可用级
- 评估基准 13 → 32 题；质量关（description ≤1024、安全扫描 0 高风险、注册）双技能全过

## [2.3.0] - 2026-09-21 至 2026-09-22

### Added

- LiveScreening 可视化工作台：React19 六视图前端 + 11 步实时工作流 + SSE + AI 助手（14 工具）
- `tcm_chembl_lookup`（化合物 → 机制靶点）、`tcm_ensembl_lookup`（gene symbol → ID/xrefs）

### Changed

- MCP 工具 15 → 17；Open Targets GraphQL v4 schema 适配（name/knownDrugs 移除，改 approvedSymbol 等）
- 数据库可用性快照更新 28/36 up；ChEMBL/Ensembl 三端点恢复、MONDO/OLS 与 BindingDB 新入口接入

### Fixed

- 前端构建多处引用错误（SourcesView state.data、NAV 未定义、旧 dist 空态）；端口按 config.yaml 修正为 8788

### Verified

- 数据库全面测试 24/24 OK（22 真实成功 + 2 业务限制）；MCP 全量回归 28/28
- UC 11/11 全绿（Top1 Quercetin）、卵巢癌 11/11 全绿（Top1 Olaparib，7 靶点），全程无预置数据

## [2.2.0] - 2026-09-15

### Fixed

- `fetch_herb_ingredients.py`：HERB 上游表头变更（`Herb name` → `Herb_cn._name`）导致的崩溃，改为规范化语义匹配
- `fetch_target_screening.py`：裸文件名输出路径 100% 崩，改为 abspath 后取 dirname
- `dedup_classify.py`：结构分类分层修正（含氮骨架先于碳数、黄酮 SMARTS 芳香兼容），建立回归锚点（小檗碱→生物碱、槲皮素→黄酮类）

### Changed

- MCP 层 TCMBank 指数退避 + 熔断；参数统一嵌套；scheduler 守护修复并清理 14 个过时定时任务

### Verified

- 三处修复各带回归测试，锚点全部通过；技能健康复查 28/28、claw_scan 0 风险、双模式加载正常

## [2.1.0] - 2026-09-14

### Added

- 首个 MCP 服务器 `tcm_drug_discovery_mcp.py`：13 个工具，StreamableHTTP（8000 端口）与 stdio 双传输
- 开源工具包 `tcm-drug-discovery-toolkit`（30 文件）：argparse 参数化、路径去硬编码、workflow_engine 脱敏、MIT LICENSE、CITATION.cff
- `tcm_dgidb_interactions`、`tcm_gwas_associations`（13 → 15 工具）
- 评估基准 XML 首版 10 题（答案全部实测锚定）；双疾病端到端扫描脚本 `disease_live_scan.py`

### Fixed

- 六个工具级 bug：PubChem 带电离子 SMILES 回退、STRING 双重编码与 0-1 归一、CTD curated 报告、NPASS HTML 解析、KEGG find 端点
- GWAS 端点勘误（findByDiseaseTrait 为正确入口）

### Verified

- quick_validate 通过、安全扫描 0 高风险、干净环境冒烟（13 工具、db_status 23/32、berberine→CID 2353）
- UC 与卵巢癌双疾病实时验证全 PASS（OT 7,161 / 15,776 靶点、DGIdb 命中 olaparib 等）

## [2.0.0] - 2026-09-09 至 2026-09-13

### Added

- 多通道并行补全体系：TCMBank（1,863 命中）、OPSIN（2,461）、COCONUT/NPASS（230）、Wikidata 中文映射库（cid/cas/en→zh 共 9 万条）、PubChem PUG-View 独立通道
- 定时通道进度检查（每 30 分钟）与断点续跑机制
- 分层 QC v2/v3：Structure_Status / Identity_Status / Stereo_Status / Formula_Match / Parent_SMILES / AF3_Ready 等 12 项字段
- 交付三表结构：Compound_Master（51,399 行）、Compound_Source_Map（243,409 对）、UC_AF3_Ready_Library（20,615 条）
- 手性 QC 四分类（confirmed / unspecified / not-applicable / pending）；Isomeric 与 Canonical 双去重口径

### Fixed

- “CNaN” 并查集连锁（缺失 CID 字面量导致 3.7 万条超大组）；SMILES 小写化破坏芳香碳语义；交付链并行写坏 xlsx（Bad CRC-32）两起事故
- 假 CID（>1.6 亿）判别与过滤；Name_CN 字典结构字符串污染清理（3,402 行）

### Verified

- v5.3 phaseE 34 PASS；v5.4 / v5.5 各 35 PASS / 0 FAIL；覆盖率终态 SMILES 76.0% / IK 76.1% / Formula 91.3%
- 交付说明 29 项关键数字与 Excel 权威数字一致

## [1.3.0] - 2026-09-05 至 2026-09-08

### Added

- 三库全量抓取：HERB（44,596 成分 + 6,893 药材）、SymMap（698 药 / 88,848 对）、ETCM（2,075 味 / 67,342 条）
- 三库合并 v2：108,261 原始记录 → 70,530 唯一分子，InChIKey>CID>CAS 强键 + 名称弱键
- 14 步分子库流水线（QC 分子级先行）与四件套交付（Master 25,121 / Source_Map 205,110 / QC_Report / AF3_Input）
- 新旧版本六层匹配合并（97.3% 匹配、零丢弃）；TCMSP-only OB/DL 口径确认

### Fixed

- ETCM keep-alive 静默断连导致 Session 永久挂起，改为新建连接 + 超时元组
- DL 口径：HERB 与 TCMSP 算法不同，OB/DL 只取 TCMSP 官方

### Verified

- 合并抽查 ETCM 未命中 95/264,432（0.09%）；OB/DL 补全 3,574 条全部带 TCMSP MOL_ID 溯源且抽查一致

## [1.2.0] - 2026-09-04

### Added

- 11 步 DAG 工作流引擎 + HTTP/SSE + 可视化页面（五层架构、断点续跑、事件总线）
- 反向外靶点预测五平台打通：SwissTargetPrediction（65 秒出 100 靶点）、PharmMapper、NPASS、TCMBank（四实体闭环）、DCABM-TCM
- 评分体系升级：锚定覆盖率加成、支撑增益（封顶 0.3）、靶点作用方向
- AI 助手面板：流式输出、工具调用卡片、LLM 设置（多服务商）、日志控制台

### Fixed

- shared_ctx 缺失（下游读不到上游数据）、并行步骤 step_name 覆盖（线程本地存储）、current_step 线程归属错误

### Verified

- 工作流重跑 11/11 全绿（约 30 秒）；5 新工具服务器内验证通过；UniProt 新 API 适配后 TNF→P01375 正常

## [1.1.0] - 2026-09-02

### Added

- CTD 全量数据本地化（41 MB / 315 万行化学-基因交互），绕过 ALTCHA 浏览器验证
- DrugBank SPARQL 经 UniProt 双向查询通道
- 中药小分子筛选工作流（文献库 → 参考靶点 → 评分排序）模板脚本 547 行

### Verified

- 全流程冒烟测试通过（约 8 秒）：4 个阳性药靶点重叠率 92–100%，15 个关键治疗靶点全命中，Top30 与文献高度一致

## [1.0.0] - 2026-08-29 至 2026-08-31

### Added

- UC 中药有效成分分子库首版（38,697 行 × 16 列）：TCMSP / HERB / ETCM 三源检索
- 多基原中药四步工作流：药典定物种 → 按物种检索 → 跨物种去重 → 结构分类
- 383 味药材「药材名 → 物种拉丁学名」映射表（《中国药典》2020 一部）
- ADME 筛选标准 OB≥30% 且 DL≥0.18（仅 TCMSP 官方口径）
- tcm-database-query 技能首次自进化：18 个生信库可用性实测、靶点筛选数据库四轮探测定级

### Fixed

- TCMSP Token 过期导致全量 OB/DL 拉取失败（重新获取后修复）

### Verified

- 白及 29 成分 DL 与 TCMSP 网站逐条一致；基原映射抽验 6 味多基原药全部正确
