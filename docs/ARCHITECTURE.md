# 架构说明

本仓库以统一 MCP 服务器承载 56 个工具，按职责分层。核心模块归属、执行链路与数据流如下。

## 1. 模块分层

```
mcp_server/
├── unified_mcp.py               # 统一入口。注册 unified_overview、tcm_smart_plan，并转交其余 52 个工具
├── open_tcm_discovery_mcp.py    # 检索 / 网络 / 富集 / 自迭代，27 个工具
├── structure_docking_mcp.py     # 对接建模，8 个工具
├── smart_layer.py               # 缓存、结构分类、tcm_smart_run、研究报告
├── parallel_layer.py            # 批量 ADME、敲除模拟、批量对接、多臂比较
├── lit_formula_layer.py         # PubMed 共现、药对集合分析
├── rescore_figure_layer.py      # 对接精排、网络图（自研 FR 布局）
├── p1_layer.py                  # 图嵌入、GEO 发现、本地 ADMET
├── p2_layer.py                  # ClinicalTrials.gov v2
└── bootstrap_layer.py           # 依赖检查与 Vina 解析
```

各层文件各自用 `@mcp.tool` 注册，彼此不 import。`unified_mcp.py` 加载它们，把工具拷到同一个服务器，并把函数表交给执行引擎，进程内直接调用。19 个只读检索工具在转交时包一层 SQLite 缓存。

单独运行 `open_tcm_discovery_mcp.py` 只得到检索族，HTTP 端口默认 8000。56 工具的入口是 `unified_mcp.py`，HTTP 端口默认 8002。

## 2. 两条运行时

| | MCP 在线深链 | 本地 DAG |
|---|---|---|
| 入口 | `tcm_smart_plan` / `tcm_smart_run` | `workflow_engine/run_server.py` |
| 数据 | 当次 API | 用户提供的机制库、临床药、DrugBank、CTD、分子库 |
| 产物 | 会话 JSON、可选研究报告 | 评分表、循证日志、看板 |

`tcm_smart_plan` 只产出步骤表。`tcm_smart_run` 执行下面这条在线链，不调用批量对接，也不调用四步药材脚本。

```
目标文本
  │  关键词识别；27 个常见成分名可从 goal 抽出
  ▼
1    M1  tcm_kegg_disease_pathways
  │  D1  通路基因 ≥15 → 网络再富集；<15 → 直接富集
2–3  M3  tcm_network_analysis（可跳过）→ tcm_multi_enrichment
4    M2  基因 tcm_cross_database_screen；出现 Tclin/Tchem 记下 D2
5    M2  化合物 tcm_cross_database_screen；D4 规则写入决策日志
5.1  P1  tcm_admet_predict          （本地近似，不是 tcm_adme_profile）
5.2  P0  tcm_literature_mine
5.3  P2  tcm_trials_match
  │  缺受体 PDB、文献口袋残基或 Vina → paused
6–10 M5  环境检查 → 拉结构 → dock_vina_docking → dock_analyze_contacts
```

`depth=full` 时，引擎把 `python scripts/disease_pathway_workflow.py run-all ...` 写入 `deferred_scripts` 和决策点 D3，然后继续在线链。该脚本约 30–60 分钟，带 checkpoint，由操作者或 Agent 的 shell 执行。OB 成分回到会话时，作为 `compounds` 传入续跑。

浮点步号让 5.1–5.3 可以插进旧会话。续跑时 `first_step` 取暂停步号，已完成的更小步号不再执行。对接默认 exhaustiveness 8、seed 42。批量筛选的 exhaustiveness 4 和 `dock_rescore` 属于并行层，不在这条深链里。

多候选比较走 `tcm_multi_path_run`。单受体多配体走 `dock_batch_screening`，再用 `dock_rescore`。报告用 `tcm_report_generate`。

## 3. 数据流与缓存

- 在线检索结果带 `source` / `queried_at`。19 个只读工具的响应进入 `~/.tcm_mcp_state/` 下的 SQLite 缓存，命中时保留原始 `queried_at`，错误不缓存。
- 同一进程内，上游连续失败会触发短时熔断（例如 `tcmbank.cn` 连续 3 次失败后冷却 60 秒）。熔断状态不写入磁盘，进程重启后重新计数。
- 持久化的 `insights.json` 在下次同类错误里附加提示，不会自动停用整个数据源。是否停用由人阅读 `tcm_analyze_runtime` 后决定。
- 离线索引：`data/herb_mol_index.json` 提供药材与成分的标识映射，供 `tcm_formula_analysis` 使用。见 [data/README.md](../data/README.md)。

## 4. 证据分级

输出严格区分三类证据，措辞与标注不同：

| 级别 | 含义 | 标注方式 |
|------|------|----------|
| 实验直接证据 | 结构解析、SPR、临床数据 | 正常引用（PDB 编号 / 试验 NCT） |
| 结构模板 | 实验坐标重利用 | 注明 PDB 模板与来源 |
| 计算模型 | 对接位姿、预测残基 | `proposed docking model`、残基带 `*`、虚线 |

报告类工具（`tcm_report_*`）按此规则生成措辞，计算预测不得混入实验结论表述。

## 5. 质量与自迭代

- 提交前运行 `python scripts/check_repo.py`。它检查语法、56 个工具名和文档口径，不访问网络。
- 在线行为的历史记录在 `mcp_server/evaluation.xml`。新增对外行为时补可复核的观察，不把推断值写进基准。
- 运行时遥测和人工确认的洞察写入 `~/.tcm_mcp_state/`。`tcm_self_iterate` 只在用户点名后把观察追加到 `references/runtime_lessons.md`，不改工具行为。
- `tcm_smart_run` 每一步重画分析网络。`tcm_analysis_topology` 按会话输出 Mermaid 和 SVG，标出数据库、文献库、方剂索引和步骤状态。

## 6. 演进路线

- v1.x：数据与工作流（分子库 → DAG 引擎）
- v2.x：MCP 化与平台化（双传输、LiveScreening、17 工具）
- v3.x：统一技能（56 工具）与开源化

版本细节见 [CHANGELOG.md](../CHANGELOG.md)，工具索引见 [modules/MODULE_INDEX.md](../modules/MODULE_INDEX.md)。
