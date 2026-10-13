# 模块索引（MODULE INDEX）—— 依赖关系与速查

> Agent 用法：先 `unified_overview` 定向，`tcm_smart_plan` 出执行网络；**单目标深链**用 `tcm_smart_run`，**多候选横向比较**用 `tcm_multi_path_run`，**多配体对单受体筛库**用 `dock_batch_screening`（后接 `dock_rescore` 精排），**证据链补强**用 `tcm_literature_mine`，**药对/复方分析**用 `tcm_formula_analysis`，**论文网络图**用 `tcm_network_figure`；仅当需要某模块细节时再读对应 mX 文档。
> 数据流：执行引擎自动把每步产物填入下一步 —— 动态分析网络；并行层多臂同时跑同一张网。

## 依赖图（结果驱动的模块咬合）

```
M1 疾病检索 ──通路基因──> M3 网络富集 ──hub基因──> M2 靶点证据
     │                        │                      │
     └──OB成分（full深度）──> M2 化合物共识 ──共识≥2/3──> M5 对接建模
                                                    │
                                  M6 渲染出图 <──接触残基+亲和能
                                                    │
                                      预测残基 ──回查──> M2（UniProt/文献）
（任何时刻）新发现 ─────────> M7 自迭代落库 <── 会话结束分析

并行层（多臂同时跑同一张网）：
  候选A ┐
  候选B ├─ asyncio.gather ──> 各自完整链（共识→ADME→对接） ──> 横向对比+综合排名
  候选C ┘
  多配体×单受体：dock_batch_screening（并行 Vina 进程 + ADME 预过滤 + 亲和能排名）
  靶点模块：tcm_network_simulation（knockout 扫描 + 累积敲除 + 蒙特卡洛 z-score）
```

## 速查表

| 模块 | 入口工具/脚本 | 关键决策点 | 产物 |
|---|---|---|---|
| M1 | tcm_kegg_disease_pathways / disease_pathway_workflow.py | D1 基因数、D3 成分反哺 | 通路表/候选药材/成分/FDR |
| M2 | tcm_cross_database_screen（并发） | D2 基因共识、D4 化合物共识 | 证据记录+共识分+ID |
| M3 | tcm_network_analysis → tcm_multi_enrichment | D1 分支 | hub 排名/共识通路 |
| M4 | molecular_library_builder.py（14步）+ tcm_classify_structure | OB/DL 门/QC 评分 | 分子库四件套+结构分类 |
| M5 | dock_check_environment → dock_vina_docking | D5 亲和能、D6 回查 | pose/接触/台账 |
| M6 | make_figure_pml.py → compose_panel.py | 证据分级 | 论文图/组合图 |
| M7 | tcm_self_iterate；先 *_report_insight / *_analyze_runtime | hits≥2 为 strong；用户点名后才写入 | references/runtime_lessons.md |
| 智能层 | tcm_smart_plan / tcm_smart_run / tcm_report_generate / tcm_cache_status / tcm_classify_structure / tcm_self_iterate / tcm_analysis_topology | 决策点自动分叉；缺文献口袋残基时暂停；拓扑图随步骤重画 | 会话 checkpoint/研究简报/缓存/分类/分析网络 SVG 与 HTML |
| 并行层 | tcm_adme_profile / tcm_network_simulation / dock_batch_screening / tcm_multi_path_run | 综合排名=优先级启发式 | 批量理化/knockout 排名/并行对接排名/多臂对比表 |
| P0 证据配伍 | tcm_literature_mine / tcm_formula_analysis / dock_rescore / tcm_network_figure | 共现=文献计量学信号非因果；重打分=启发式 | 文献计数表/配对互补矩阵/精排表/论文网络图 |
| P1 细胞分辨率与数据驱动 | tcm_graph_embed / tcm_scrna_channel / tcm_admet_predict | 嵌入=拓扑视图非功能验证；GEO 仅发现层；ADMET 全近似 | 靶点优先级排名/数据集表/ADMET 面板 |
| P2 临床转化 | tcm_trials_match | 试验匹配=转化上下文非疗效证明 | 分期计数+NCT 试验表 |
| 自举层 | tcm_dependency_status | install 为显式 opt-in | 依赖影响面矩阵+一键安装+内置 vina 盘点 |
| 数据整理 | tcm_clean_table / tcm_normalize_ids / tcm_normalize_structure / tcm_normalize_genes / tcm_normalize_disease / tcm_convert_dose | 不进入自动链 | 表、标识、SMILES、基因符号、病名、克数 |
| TCMSP 旧站 | tcm_tcmsp_search | 不进入自动链。OB/DL 只滤本页字段 | 药材行和成分行。不是疗效，也不是靶点表 |

## 执行引擎（tcm_smart_run）行为要点

- 自动链：M1 通路 →（D1 自动分支）M3 网络/富集 → M2 交叉证据 → 5.1 `tcm_admet_predict` → 5.2 `tcm_literature_mine` → 5.3 `tcm_trials_match` →（文献口袋与 Vina 齐全时）M5 对接。`depth=full` 只登记四步药材脚本为延后步骤 D3，不在引擎内执行
- 每步结果落盘 `~/.tcm_mcp_state/sessions/<id>.json`（原子写）；传 `session_id` 续跑，已完成步骤不重跑
- 暂停不臆测：缺 `receptor_pdb` / 文献 `pocket_residues` / vina 可执行文件时明确列出 needs 并给出 resume 指引
- 口袋残基输入兼容 `I46,I52` 与 `46,52` 两种写法（引擎自动归一化为编号）
- 响应缓存：20 个只读检索工具透明缓存（TTL 默认 3600s，`TCM_CACHE_TTL` 可调），命中标注 age 与原始检索时间，Error 不缓存

## 并行层（parallel_layer.py）行为要点

- **tcm_multi_path_run**：2-8 候选各自成臂，`asyncio.gather` 并行跑「共识→ADME→（可选）对接」，综合排名=共识 35%+类药 20%+亲和能 45%（无对接时 50/50）——**优先级启发式，非验证打分函数**；session 存 `sessions/multi-<id>.json`
- **dock_batch_screening**：单受体多配体（2-12），配体并行获取（限速 2）→ ADME 预过滤（Lipinski ≥3 违规跳过省算力）→ `max_workers`（默认 3）并发 Vina 子进程 → 亲和能排名；默认 exhaustiveness=4 **快速分诊**，头部化合物须 16 精对接复验
- **tcm_network_simulation**：STRING 活网络 → 单节点 knockout 扫描（全局效率损失+最大连通分量缩减）→ 头部 K 累积敲除曲线 vs 随机对照 → 蒙特卡洛 z-score（|z|≥2 显著脆弱）；**拓扑结论≠验证的必需性**，须与 M2 证据合判
- **tcm_adme_profile**：名称→活取 PubChem SMILES（并发 2）→ RDKit MW/TPSA/LogP/HBD/HBA/RotB/芳环/Lipinski/Veber/Egan/QED；**结构描述符与 TCMSP OB/DL 门互补不互替**

## P0 证据与配伍扩展行为要点

- **tcm_literature_mine**：NCBI E-utilities esearch 活查询，限速 0.4s/req+退避重试；证据三角（三元/三对边计数）+ 逐年趋势（近 10 年）；**共现=文献计量学信号，非因果验证**，与 CTD 套录计数互补
- **tcm_formula_analysis**：本地索引 `data/herb_mol_index.json`（698 药材/25,975 成分，SymMap v2.0）→ 成分集合 Jaccard/重叠系数/互补度 → 三级标签（协同候选/部分互补/功效相近）；`include_targets=True` 开启靶点层（每药材前 N 成分 SymMap 活查）；**集合计算≠药效验证**
- **dock_rescore**：batch workdir 精排 = 亲和能谱（mode-1/2 gap+谱宽）+ 文献口袋锚定率（best pose 与口袋残基 4.5Å 接触比）+ 复合分（0.45 亲和+0.35 锚定-0.20 gap 罚+0.40 基线）；**启发式非验证打分**，发表前仍须 exhaustiveness 16 复验
- **tcm_network_figure**：自研 Fruchterman–Reingold 布局（纯 numpy，固定 seed 可复现）+ matplotlib 3200x2200 PNG+PDF；hub 分层着色（度前 70%/40%）；支持自定义边表+node_kinds 多层着色（成分/靶点/疾病）；图注内嵌证据分级声明与 queried_at。networkx 不是运行依赖

## P1 细胞分辨率与数据驱动层（p1_layer.py）行为要点

- **tcm_graph_embed**：STRING 活网络 → 带偏随机游走（p/q 二阶）→ PMI 共现 → 截断 SVD 嵌入（纯 numpy 无重依赖）→ 与种子基因余弦相似度排靶点 → 与度中心性双视角共识表；<15 节点或 <5 边拒绝（嵌入无意义）；**拓扑视图非功能验证**
- **tcm_scrna_channel**：GEO esearch db=gds + esummary 活查（探测 2026-10-12 存活，UC 单细胞 270 个数据集）；返回 GSE/标题/设计/物种/样本数/平台/关联 PubMed；**HPA 全 API 404、GTEx v2 SSL 失败（死路已记 insights）→ 仅发现层，不产表达表**
- **tcm_admet_predict**：本地近似面板（ADMETlab REST 死路）：ESOL 水溶性（Delaney 2004 线性模型，toluene/caffeine/quercetin 锚定验证）+ LogS 粗估 + BBB 盒式规则 + PPB/Caco-2/hERG 代理；**每项标注模型类型，仅供分诊**

## P2 临床转化层（p2_layer.py）行为要点

- **tcm_trials_match**：ClinicalTrials.gov API v2 活查（探测 2026-10-12 存活，UC+TNF 216 个试验/UC Phase-3 239 个）：疾病×靶点/化合物（intervention 或 term 聚焦）→ 总数+分期计数（Phase 1-4）+NCT 试验表（标题/状态/入组/干预/申办方）；**试验匹配=转化上下文（靶点可药性先例），非 TCM 候选疗效证明**
- OpenMM MD 试点：经评估**延后**（400MB 安装、快速分诊场景性价比低）——非死路，按需启动

## 自举层与依赖内置（bootstrap_layer.py + bin/）行为要点

- **Vina**：解析顺序为 **参数 > VINA_EXE > `bin/vina.exe` > PATH**。官方 1.2.5 Windows 二进制按 `bin/README.md` 自行放置，仓库不强制提交该文件
- **tcm_dependency_status**：10 个 python 依赖（mcp/httpx/pydantic/rdkit/numpy/matplotlib/pillow/networkx可选/meeko/gemmi）健康检查+**缺失影响面矩阵**（每个依赖缺失时哪些工具不可用）+精确 pip 命令；`action=install` 为显式 opt-in 安装（装到当前解释器）；verdict 按功能域（检索/对接/出图/ADME/自包含 vina）汇总
- **requirements.txt**：一键安装全量依赖
- **网络图去 networkx 化**：自研 Fruchterman-Reingold 力导向布局（纯 numpy，固定 seed 可复现），networkx 变为可选依赖
- **tcm_smart_run 自动链扩展**：M1→M3→M2 之后新增 5.1 ADMET / 5.2 文献共现 / 5.3 临床试验三步（float 步号保证 resume 不重跑）；goal 文本中的常见成分名（27 个白名单）自动提取为 compounds
- **tcm_smart_plan 词表升级**：新增文献/试验/配伍/ADMET/单细胞/图嵌入/敲除模拟七组中英关键词，全部新工具可被自然语言路由

## 数据整理（prep_layer.py）

六个工具都不访问网络，也不进入 `tcm_smart_run` 自动链。`tcm_clean_table` 收表。`tcm_normalize_ids` 只做格式和 CAS 校验位，裸数字不记成 CID。`tcm_normalize_structure` 在没有 RDKit 时不生成 InChIKey。`tcm_normalize_genes` 遇到 NF-κB、COX、VEGF、AKT 保持歧义。`tcm_normalize_disease` 只使用代码里核对过的 MeSH 子集。`tcm_convert_dose` 必须点名度量衡。重量比不是药效。

## 超时与后台规则

- 一次 DB 调用超时 → 重试指引（analyze_runtime 查健康）
- 索引构建/四步全流程/长渲染 → 后台任务 + checkpoint，禁前台死等
- 对接 exhaustiveness 16 约 2-5 分钟（工具 timeout 默认 1800s 足够）

## 状态目录健康

`unified_overview` 的 state_health 段实时显示两族遥测/洞察/台账；写入失败静默不影响工具（遥测纪律）。
