# 工作流

这个仓库有两条计算路径，互相不调用。在线检索和本地打分不要混成一条证据。

## 1. MCP 在线深链

适合：疾病名、少量候选成分、需要当场引用数据库的问题。

```bash
python mcp_server/unified_mcp.py --stdio
```

Agent 按这个顺序调用：

1. `unified_overview`
2. `tcm_smart_plan`，得到步骤表和 D1–D6
3. `tcm_smart_run`，执行下面的在线链
4. 需要横向比较时改用 `tcm_multi_path_run` 或 `dock_batch_screening`
5. `tcm_report_generate`

`tcm_smart_run` 的实际步骤：

| 步号 | 条件 | 工具 |
|---|---|---|
| 1.5 | 目标或参数里至少两味药材 | `tcm_formula_analysis`（本地索引，集合重叠） |
| 1 | 能识别疾病 | `tcm_kegg_disease_pathways` |
| 2 | 通路基因不少于 15 | `tcm_network_analysis`；否则跳过 |
| 3 | 有基因 | `tcm_multi_enrichment` |
| 4 | 有 hub 或基因 | 基因模式的 `tcm_cross_database_screen` |
| 5 | goal 或参数里有成分 | 化合物模式的 `tcm_cross_database_screen` |
| 5.1 | 同上 | `tcm_admet_predict` |
| 5.2 | 有疾病，且有成分或基因 | `tcm_literature_mine` |
| 5.3 | 有疾病和基因 | `tcm_trials_match` |
| 6–10 | 受体 PDB、文献口袋、配体、Vina 都齐全 | 环境检查、拉结构、`dock_vina_docking`、`dock_analyze_contacts` |

缺口袋或 Vina 时，状态是 `paused`。用同一个 `session_id`，加上 `docking_inputs` 续跑。已完成的步骤不重跑。口袋残基可以写 `46,52`，也可以写 `I46,I52`。引擎只保留数字。残基必须来自文献。示例文件里的占位符不能直接拿去对接。输出按提议的对接模型来写。只在计算里出现的残基标 `*`，接触画虚线。`tcm_check_docking_replay` 不在这条链里，也不运行对接，不下载 PDB。夹具不是已核对的公开复合物。通过检查只表示可以进入复现，结果仍是提议的对接模型。缺文献标识、缺口袋残基，或残基不是编号，会拒绝。这里不改深链的 exhaustiveness、种子、口袋规则和 Vina 查找顺序。

读这次返回时，先看 `status`，再看 `executed steps` 里每一步的摘要。`source` 和 `queried_at` 只出现在真正查过上游的步骤上。第 2 步若写成 `(skipped: D1 direct branch)`，状态仍是 ok，表示通路基因少于 15 个，按 D1 直接富集，不是请求失败。`deferred scripts` 里的四步药材命令这次没有执行。六个整理工具、`tcm_tcmsp_search`、`tcm_align_tcmsp`、`tcm_read_target_table`、`tcm_formula_weight` 和 `tcm_check_docking_replay` 不在这条自动链里。要 TCMSP 的 `ob`、`dl`，要把成分名对上 InChIKey，要读一张调用方自备的靶点表，要把一张方剂换成克，或要先检查对接复现条目，都需要另一次调用。

`tcm_literature_mine` 的计数是检索式命中篇数。`tcm_formula_analysis` 的 Jaccard 是成分集合重叠。`tcm_trials_match` 的 `total_trials` 和分期是登记信息。`tcm_admet_predict` 是近似。`tcm_graph_embed` 和 `tcm_network_simulation` 是拓扑。这些都不是疗效。预测靶点不是已记录证据。`tcm_cross_database_screen` 的共识分只表示几个库同时查到了该实体。

`tcm_read_target_table` 另一次调用。先看正文是不是以 `Error:` 开头。表读成了就看 `n_recorded`、`n_predicted`、`n_unclassified`，再看 `recorded_pairs` 里的 `recorded_source_count`。这个数只统计 `support` 为 recorded、且来源是一个写明的名称的行。`predicted_pairs` 不并进这个数。未分类行的 `reason` 说明缺标识、support 认不出，或来源含糊。没有 `queried_at`。它不是在线多库共识，也不是疗效。空表和缺列会报错，不会猜成已记录。

`depth=full` 额外写入延后命令，不在这次调用里执行：

```bash
python scripts/disease_pathway_workflow.py run-all --disease "ulcerative colitis" --out work/uc
```

四步分别是通路、候选药材、OB≥30 的成分、超几何 FDR。单步也可以跑：`disease-pathways`、`pathway-herbs`、`herb-ingredients`、`cross-validate`。全库索引：

```bash
python scripts/disease_pathway_workflow.py build-index --out data
```

### 决策点

| 点 | 规则 |
|---|---|
| D1 | 基因不少于 15 才做 PPI，再用 hub 富集 |
| D2 | 基因共识高、且 Pharos 为 Tclin/Tchem 的靶点优先进入对接讨论 |
| D3 | `depth=full` 的药材脚本延后；脚本产物再作为成分送回 M2 |
| D4 | 化合物共识达到 2/3 再考虑对接；引擎记下规则，不在未验证名称上编造结构 |
| D5 | 亲和能 ≤ −7 kcal/mol 继续看接触；> −5 kcal/mol 视为弱结合 |
| D6 | 预测残基回查 UniProt 或文献之后，才能写成关键残基 |
| D7 | 少于两味药材时不计算方剂集合重叠；索引节点仍留在分析网络上 |

每一步做完，会话里的分析网络会更新。`tcm_analysis_topology` 按 `session_id` 重画 Mermaid、SVG 和自包含 HTML，用来看数据库、文献库、方剂索引和步骤状态。这张图是检索过程的拓扑。它不是疗效结论。文件在会话目录里，名字带 `topology.svg` 和 `topology.html`。打不开图时，先看返回里有没有写路径，以及这一步是不是在画图之前就 `paused` 了。

实体节点少于 300 个时画成圆角卡片，里面是名称和一行详情。数到 300 个以后，按类别收成球壳，同一类在同一个球上。步骤节点不计入这 300，仍留在底部。HTML 不依赖外网，也不加载外部脚本。

配色是浅暖宣纸底和木色边框，卡片是米白。HTML 里除了图，还有图例、检索通路、下一步建议和数据源状态。同一段文字写在 `tcm_analysis_topology` 和 `tcm_smart_run` 的拓扑摘要里。通路按图上已经有的边说明疾病、通路、基因、成分、药材走到哪一环，没有的环写明缺什么。下一步最多三条，指向已有工具，是提示，不会自动执行，也不是疗效结论。图上至少两味药材且文本或节点详情里有剂量时，可以提示方剂克重，并写明要先点名口径，重量比不是疗效。文献写明口袋时，可以同时提示对接复现门槛，并写明这只是门槛、不跑对接。调用方文本里有靶点表或 predicted、recorded 时，可以提示读入靶点表。空图不另加这三条。药材少于两味时不提示方剂克重。没有口袋时不提示复现门槛。

D5 的阈值是分诊约定，不是结合的实验判据。

### 并行工具

| 目标 | 工具 |
|---|---|
| 2–8 个成分横向比较 | `tcm_multi_path_run` |
| 一个受体对多个配体 | `dock_batch_screening`，重点分子再用 exhaustiveness 16 复跑，然后 `dock_rescore` |
| 网络节点脆弱性 | `tcm_network_simulation` |
| 类药性描述符 | `tcm_adme_profile` |
| 药对成分集合 | `tcm_formula_analysis` |

`tcm_multi_path_run` 的综合分是启发式：有对接时共识 35%、类药性 20%、亲和能 45%；没有对接时前两项各一半。这个百分比是排序用的权重，不是疗效贡献。缺 Vina 或文献口袋时，对接那一支不会猜一个亲和能来凑分。

`tcm_formula_analysis` 至少需要两味能在本地索引里对上的药材。索引缺失或对上的不足两味，正文以 `Error:` 开头。高重叠那一档的标签字面是「功效相近」，它按重叠系数划分，不是药效。剂量不参与这个集合运算。要把单条剂量换成克，另调 `tcm_convert_dose`，并点名口径。整张方剂的合计用 `tcm_formula_weight`，同样必须点名一个口径。它把每一行换成克后相加，炮制按原文保留。重量比不是疗效，也不是君臣佐使。不会把各套「两」并成一个数。集合重叠仍然不读剂量。

## 2. 本地 DAG

适合：已经有机制文献表、临床药表、DrugBank 靶点表、分子库和 CTD 本地文件，要做可重复的加权评分。

```bash
python -m pip install -r requirements-workflow.txt
copy workflow_engine\config.example.yaml workflow_engine\config.yaml
python workflow_engine/run_server.py
```

`config.yaml` 在 `.gitignore` 里，不会提交。疾病段不填时，行为与溃疡性结肠炎相同（MeSH D003093，MONDO:0005101）。

看板默认端口 8765，和 MCP 的 8002 不是一回事。步骤：

| 步骤 | 内容 |
|---|---|
| s1–s3 | 加载机制库、临床药、DrugBank 靶点 |
| s4–s6 | 临床锚定靶点、文献机制靶点、合并分层 |
| s7–s9 | 分子库、CTD 三级键匹配、靶点覆盖与 ADME 加权 |
| s10–s11 | 阳性药核对、写出评分表和循证明细 |

证据权重缺省：临床锚定 1.0，直接实验 E1 0.7，组学或关联 E2 0.4，综述或理论 E3 0.2。OB/DL 归一基准是 30 与 0.18，只解释 TCMSP 口径的列。表里如果是 HERB 的类药性，或 RDKit 的 Lipinski、QED，不要塞进这两列。

这条 DAG 不调用 MCP 工具。MCP 深链也不读取 `config.yaml` 里的 Excel。看板地址打不开时，先看端口是不是被改过，默认 8765。数据文件路径错了，失败出现在对应步骤的日志里，不会变成 MCP 会话里的 `paused`。

方剂克重要按历史口径分开。`tcm_convert_dose` 换单条，`tcm_formula_weight` 在同一个口径下合计。DAG 本身不读这套换算，加权评分也不会把重量比当成药效或君臣佐使。

## 3. 会话与状态

| 路径 | 内容 |
|---|---|
| `~/.tcm_mcp_state/sessions/` | `tcm_smart_run` 会话，原子替换写入 |
| `~/.tcm_mcp_state/` | 检索遥测、洞察、SQLite 缓存 |
| `~/.dock_mcp_state/` | 对接台账与洞察 |

这些目录不进 git。换机器或清掉用户目录之后，旧的 `session_id` 就续不上了。会话 JSON 用临时文件写完再替换，正常情况下不会留下半截文件。若进程被强行杀掉，先看对应的 `session_id` 能否再读出来，再决定要不要用新会话重跑。不要把会话目录里的对接姿势写成晶体结构。
