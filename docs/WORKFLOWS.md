# 工作流

本仓库有两条互不调用的计算路径。选错入口，会把在线检索和本地打分混成一条证据。

## 1. MCP 在线深链

适合：疾病名、少量候选成分、需要当场引用数据库的问题。

```bash
python mcp_server/unified_mcp.py --stdio
```

Agent 侧的顺序：

1. `unified_overview`
2. `tcm_smart_plan`，得到步骤表和 D1–D6
3. `tcm_smart_run`，执行下面的在线链
4. 需要横向比较时改用 `tcm_multi_path_run` 或 `dock_batch_screening`
5. `tcm_report_generate`

`tcm_smart_run` 的实际步骤：

| 步号 | 条件 | 工具 |
|---|---|---|
| 1 | 能识别疾病 | `tcm_kegg_disease_pathways` |
| 2 | 通路基因不少于 15 | `tcm_network_analysis`；否则跳过 |
| 3 | 有基因 | `tcm_multi_enrichment` |
| 4 | 有 hub 或基因 | 基因模式的 `tcm_cross_database_screen` |
| 5 | goal 或参数里有成分 | 化合物模式的 `tcm_cross_database_screen` |
| 5.1 | 同上 | `tcm_admet_predict` |
| 5.2 | 有疾病，且有成分或基因 | `tcm_literature_mine` |
| 5.3 | 有疾病和基因 | `tcm_trials_match` |
| 6–10 | 受体 PDB、文献口袋、配体、Vina 都齐全 | 环境检查、拉结构、`dock_vina_docking`、`dock_analyze_contacts` |

缺口袋或 Vina 时状态为 `paused`。用同一个 `session_id` 加上 `docking_inputs` 续跑，已完成步不重跑。口袋残基接受 `46,52` 或 `I46,I52`，引擎只保留数字。残基必须来自文献，示例文件里的占位符不能直接拿去对接。

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

D5 的阈值是分诊约定，不是结合的实验判据。

### 并行，而不是深链

| 目标 | 工具 |
|---|---|
| 2–8 个成分横向比较 | `tcm_multi_path_run` |
| 一个受体对多个配体 | `dock_batch_screening`，重点分子再用 exhaustiveness 16 复跑，然后 `dock_rescore` |
| 网络节点脆弱性 | `tcm_network_simulation` |
| 类药性描述符 | `tcm_adme_profile` |
| 药对成分集合 | `tcm_formula_analysis` |

`tcm_multi_path_run` 的综合分是启发式：有对接时共识 35%、类药性 20%、亲和能 45%；没有对接时前两项各一半。

## 2. 本地 DAG

适合：已经有机制文献表、临床药表、DrugBank 靶点表、分子库和 CTD 本地文件，要做可重复的加权评分。

```bash
python -m pip install -r requirements-workflow.txt
copy workflow_engine\config.example.yaml workflow_engine\config.yaml
python workflow_engine/run_server.py
```

`config.yaml` 已被 `.gitignore` 忽略。疾病段缺省时行为等价于溃疡性结肠炎（MeSH D003093，MONDO:0005101）。

看板默认端口 8765，与 MCP 的 8002 无关。步骤：

| 步骤 | 内容 |
|---|---|
| s1–s3 | 加载机制库、临床药、DrugBank 靶点 |
| s4–s6 | 临床锚定靶点、文献机制靶点、合并分层 |
| s7–s9 | 分子库、CTD 三级键匹配、靶点覆盖与 ADME 加权 |
| s10–s11 | 阳性药核对、写出评分表和循证明细 |

证据权重缺省：临床锚定 1.0，直接实验 E1 0.7，组学或关联 E2 0.4，综述或理论 E3 0.2。OB/DL 归一基准是 30 与 0.18，只解释 TCMSP 口径的列。

这条 DAG 不调用 MCP 工具。MCP 深链也不读取 `config.yaml` 里的 Excel。

## 3. 会话与状态

| 路径 | 内容 |
|---|---|
| `~/.tcm_mcp_state/sessions/` | `tcm_smart_run` 会话，原子替换写入 |
| `~/.tcm_mcp_state/` | 检索遥测、洞察、SQLite 缓存 |
| `~/.dock_mcp_state/` | 对接台账与洞察 |

这些目录不进 git。
