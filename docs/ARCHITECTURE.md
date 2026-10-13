# 架构说明

69 个工具挂在同一个 MCP 服务器上，按职责分层。下面是各层文件、两条运行链路，以及数据怎么走。

## 1. 模块分层

```
mcp_server/
├── unified_mcp.py               # 统一入口。注册 unified_overview、tcm_smart_plan，并转交其余 67 个工具
├── open_tcm_discovery_mcp.py    # 检索 / 网络 / 富集 / 自迭代，27 个工具
├── structure_docking_mcp.py     # 对接建模，8 个工具
├── smart_layer.py               # 缓存、结构分类、tcm_smart_run、研究报告
├── parallel_layer.py            # 批量 ADME、敲除模拟、批量对接、多臂比较
├── lit_formula_layer.py         # PubMed 共现、药对集合分析
├── rescore_figure_layer.py      # 对接精排、网络图（自研 FR 布局）
├── p1_layer.py                  # 图嵌入、GEO 发现、本地 ADMET
├── p2_layer.py                  # ClinicalTrials.gov v2
├── bootstrap_layer.py           # 依赖检查与 Vina 解析
├── iteration_layer.py           # 运行观察复核，只追加 runtime_lessons.md
├── topology_layer.py            # 分析网络重画
├── prep_layer.py                # 表格、标识、SMILES、基因、疾病名、剂量
├── tcmsp_layer.py               # TCMSP 旧站药材表和成分表
├── align_layer.py               # 本地对齐 TCMSP 成分名和 InChIKey
├── target_layer.py              # 本地读入调用方自备的靶点表
├── formula_weight_layer.py      # 一个口径下的方剂克重合计。重量比不是疗效
├── docking_replay_layer.py      # 对接复现的离线门槛。不运行对接
└── herb_layer.py                # HERB HTTP 详情，以及本地名称候选
```

各层文件用 `@mcp.tool` 自己注册，彼此不 import。`unified_mcp.py` 把它们加载进来，工具拷到同一个服务器，函数表交给执行引擎，在进程里直接调用。20 个只读检索工具在转交时包一层 SQLite 缓存。改某一层的返回字段时，要同时看执行引擎读的是哪些键。引擎只认它已经在用的字段，多出来的键不会自动进决策日志。

单独运行 `open_tcm_discovery_mcp.py` 只得到检索族，HTTP 端口默认 8000。单独运行 `structure_docking_mcp.py` 只得到对接族，HTTP 端口默认 8001。69 个工具的入口是 `unified_mcp.py`，HTTP 端口默认 8002，只绑定 `127.0.0.1`。stdio 是推荐接法，不占这些端口。`list_tools` 不到 69 时，先确认启动文件是不是统一入口。

加载时若某个 layer 文件缺失，统一进程起不来，而不是悄悄少一个工具还能继续。`python scripts/check_repo.py` 会核对 `unified_mcp.py` 是否仍然加载整理层、TCMSP 层、对齐层、靶点表层、方剂克重层、对接复现门槛、自迭代和拓扑。这是仓库一致性检查，不是在线健康检查。

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

`depth=full` 时，引擎把 `python scripts/disease_pathway_workflow.py run-all ...` 写进 `deferred_scripts`，并记在决策点 D3。然后继续跑在线链。这个脚本大约要 30–60 分钟，自带 checkpoint。由人在 shell 里执行，或由 Agent 去调 shell。脚本得到的 OB 成分若要回到会话，放在 `compounds` 里续跑。返回里看到这条命令，只说明它被登记了，不说明药材和成分已经抓完。

浮点步号让 5.1–5.3 可以插进旧会话。续跑时 `first_step` 取暂停步号，已完成的更小步号不再执行。会话文件在 `~/.tcm_mcp_state/sessions/`。对接默认 exhaustiveness 8、seed 42。批量筛选的 exhaustiveness 4 和 `dock_rescore` 属于并行层，不在这条深链里。六个整理工具、`tcm_tcmsp_search`、`tcm_align_tcmsp`、`tcm_read_target_table`、`tcm_formula_weight` 和 `tcm_check_docking_replay` 也不在这条自动链里，要单独调用。

暂停有几种，都写成 `status: paused`，理由在 `paused_at`。缺 `receptor_pdb`、文献 `pocket_residues` 或配体时，停在第 6 步之前，不会猜残基。Vina 解析不到可执行文件时，理由里会写 vina executable not found。受体或配体文件没写出来时，停在第 9 步之前。`max_runtime` 用完时，已完成的步留在会话里，下次用同一个 `session_id` 续跑。通路基因少于 15 个时不暂停，第 2 步记成 D1 的直接分支，然后去做富集。

多候选比较走 `tcm_multi_path_run`。单受体多配体走 `dock_batch_screening`，再用 `dock_rescore`。报告用 `tcm_report_generate`。

## 3. 数据流与缓存

- 在线检索结果带 `source` 和 `queried_at`。20 个只读工具的响应进 `~/.tcm_mcp_state/` 下的 SQLite 缓存。命中时保留原来的 `queried_at`。错误不缓存。所以同一次失败重复出现，不代表缓存把错误钉死了。要清掉过期的成功响应，用 `tcm_cache_status`。
- 同一个进程里，上游连续失败会短暂熔断。例如 `tcmbank.cn` 连续失败 3 次，冷却 60 秒。熔断状态不写磁盘。进程重启后重新计数。冷却期内的失败是这条保护，不是该库的一条新记录。
- 持久化的 `insights.json` 在下次同类错误里附加提示。它不会自动停用整个数据源。要不要停用，由人看过 `tcm_analyze_runtime` 之后决定。`tcm_self_iterate` 只在用户点名后把观察追加到 `references/runtime_lessons.md`，不改路由。
- 离线索引 `data/herb_mol_index.json` 是药材和成分的标识对照，给 `tcm_formula_analysis` 用。见 [data/README.md](../data/README.md)。里面没有成分名和 InChIKey，`tcm_align_tcmsp` 不读它。

## 4. 证据分级

输出分三层，写法和标注不一样：

| 级别 | 含义 | 标注方式 |
|------|------|----------|
| 实验直接证据 | 结构解析、SPR、临床数据 | 正常引用（PDB 编号 / 试验 NCT） |
| 结构模板 | 实验坐标重利用 | 注明 PDB 模板与来源 |
| 计算模型 | 对接位姿、预测残基 | 提议的对接模型（`proposed docking model`）、残基带 `*`、虚线 |

报告工具（`tcm_report_*`）按这三层写。计算预测不要写成实验结论。

## 5. 质量与自迭代

- 提交前运行 `python scripts/check_repo.py`。它检查语法、69 个工具名和文档口径，不访问网络。
- 在线行为的历史记录在 `mcp_server/evaluation.xml`。新增对外行为时补可复核的观察，不把推断值写进基准。
- 运行时遥测和人工确认的洞察写入 `~/.tcm_mcp_state/`。`tcm_self_iterate` 只在用户点名后把观察追加到 `references/runtime_lessons.md`，不改工具行为。
- `tcm_smart_run` 每一步重画分析网络。`tcm_analysis_topology` 按会话输出 Mermaid、SVG 和自包含 HTML，标出数据库、文献库、方剂索引和步骤状态。实体节点少于 300 个时是浅暖宣纸底上的米白圆角卡片，木色边框。到 300 个按种类收成球壳，步骤节点不计入这 300，仍留在底部。图例、检索通路、下一步建议和数据源状态写在图里，也写在返回文字里，是给智能体读的进度，不是疗效结论。SVG 和 HTML 不加载外部脚本。

## 6. 演进路线

- v1.x：数据和本地工作流（分子库，然后是 DAG 引擎）
- v2.x：MCP 服务（stdio 与 HTTP、LiveScreening、17 个工具）
- v3.x：统一成一个技能（56 个工具），仓库公开

上面三行是当时的里程碑。当前统一入口注册的是 69 个工具。版本细节见 [CHANGELOG.md](../CHANGELOG.md)，工具索引见 [modules/MODULE_INDEX.md](../modules/MODULE_INDEX.md)。变更记录里的旧工具数是历史，不要抄回现在的说明。
