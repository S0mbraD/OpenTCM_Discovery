# 运行时自迭代机制（2026-10-12 新增：飞行记录仪 + 分层洞察库 + 实验台账）

> 目标：skills 在**任何 Agent 宿主**上运行时，能根据**运行结果**（成功/失败/延迟/实验数据）与**分析过程中总结的信息**（新发现的死路、端点变更、参数怪癖）持续自我迭代。
> 机制融合：AgentLens（飞行记录仪——全调用捕获）/ Self-Improving-Agent（错误-知识分层记忆）/ Hermes（经验→技能资产化）/ TeleAgent 技能自进化（建议更新→用户确认的 staged update）。
> 隐私：全部数据**仅存本地**状态目录，绝不上传；写入失败静默跳过，绝不影响工具本身。

## 一、三层架构

```
                    ┌────────────── 写路径 ──────────────┐
Agent 工具调用 ──> │ call_log.jsonl   遥测（每请求自动记录） │
Agent 分析总结 ──> │ insights.json    洞察库（Agent 主动写入）│
对接/建模运行 ──> │ experiments.jsonl 实验台账（自动记录）    │
                    └──────────────────────────────────┘
                                        │
                    ┌────────────── 读路径 ──────────────┐
                    │ tcm_analyze_runtime / dock_analyze_runtime            │
                    │  通道健康 → 死路候选 → 延迟回归 → 洞察合并 → 建议更新     │
                    │ dock_compare_experiments（台账查询：亲和能排名/复现性）   │
                    └──────────────────────────────────┘
                                        │
                    ┌────────────── 迭代出口 ────────────┐
                    │ proposed_updates → 平台技能自进化确认流（用户确认后生效） │
                    │ 或人工编辑 references 文档沉淀                       │
                    └──────────────────────────────────┘
```

## 二、状态目录与文件格式

| 文件 | 生成方 | 内容 |
|---|---|---|
| `~/.tcm_mcp_state/call_log.jsonl` | 遥测包装器（`_make_api_request`） | 每条：`{ts, type:"db_call", host, method, status/error, ok, ms}`；>2MB 自动轮转 `.1` |
| `~/.tcm_mcp_state/insights.json` | `tcm_report_insight` | `{version, insights:[{id, category, title, detail, evidence, source_ref, created_at, hits, last_hit}]}`；原子写 |
| `~/.dock_mcp_state/call_log.jsonl` | 脚本遥测包装器（`_run_script`） | `{ts, type:"script", script, rc, ok, ms}` |
| `~/.dock_mcp_state/experiments.jsonl` | `dock_vina_docking`/`dock_geometric_ppi` 自动追加 | 对接参数+亲和能+模式谱（基准与复现性数据） |
| `~/.dock_mcp_state/insights.json` | `dock_report_insight` | 同上格式（category: environment/method/quirk/performance/repro） |

环境变量可覆盖（跨 Agent 迁移/隔离多实例）：`TCM_MCP_STATE_DIR`、`DOCK_MCP_STATE_DIR`。

## 三、新工具

### tcm-database-query（20→25→27）

- **`tcm_report_insight`**：把分析中发现的新事实写入洞察库。类别：`dead_end`（死路）/ `endpoint_change`（端点迁移）/ `quirk`（参数怪癖）/ `performance`（延迟模式）/ `new_channel`（新通道）/ `methodology`（方法学）。同 title 重复写入 → hits+1（权重上升），不重复建条目。
- **`tcm_analyze_runtime`**：聚合遥测 → 每库健康表（调用量/成功率/平均延迟）、死路候选（≥5 次调用且成功率≤50%）、慢通道（平均>15s）、top 错误、洞察合并展示，输出 `proposed_updates`（对 SKILL/references 的具体修改建议，供平台自进化流确认）。

### structure-docking-analysis（5→8）

- **`dock_report_insight`**：环境/方法/复现类经验写入（如 "Vina exh 16 与 8 对 LA 同 pose"）。
- **`dock_analyze_runtime`**：脚本健康表、实验台账统计（配体×受体亲和能分布）、**复现性红旗**（同 seed 多次运行亲和能差>0.1 kcal/mol 时提示检查受体准备确定性）、洞察合并、建议更新。
- **`dock_compare_experiments`**：台账查询——按配体/受体过滤、按亲和能排名（挑最优结合物）、看 seed 敏感性；科学层自迭代（本轮结果指导下轮实验）。

## 四、运行时适应（已内置）

- 遥测包装器在**请求失败时自动检索洞察库**：若该 host 有已记录的 dead_end/endpoint_change 洞察，错误遥测附带 `| insight INS-xxx [...]` 提示。
- 错误消息统一追加指引：`tcm_analyze_runtime` 可查通道健康与已记录洞察。
- 洞察**不自动改写请求路由**（防止过拟合——参考 RRSI 正则化思路）：命中≥2 次的洞察才进入 proposed_updates 强建议区。

## 五、与平台技能自进化的衔接

`proposed_updates` 是建议文本（dead_end 候选/延迟回归/复现红旗/高频洞察），设计为可被宿主 Agent 直接转述进 TeleAgent 的技能自进化流程：

1. Agent 长会话结束前调用 `*_analyze_runtime`
2. 将 proposed_updates 摘要给用户，说明"技能运行发现了可优化点"
3. 用户确认 → 平台 staged update（带回退凭证）→ 技能文档真正迭代
4. 未确认的建议保留在洞察库中，随 hits 增长持续可见

这实现了两级闭环：**MCP 运行时级**（数据驱动、即时、本地）与**平台技能级**（用户确认、可回退、全局）。

## 六、其他 Agent 宿主接入要点

- 状态目录默认在用户 home 下，与技能安装目录解耦（技能目录可能只读）
- 多进程并发：同目录多实例时 insights.json 为原子替换+进程内锁，跨进程写入可能后写覆盖（v1 已知限制，典型场景单实例无影响）
- 遥测/台账/洞察均为**纯本地文件**，符合"数据不出机"纪律；清理状态目录即完全重置学习记忆

## 七、实测基线（2026-10-12）

- 遥测：每次 DB 调用落盘一条（PubChem/SymMap/STRING/Pharos/ChEMBL/UniProt/g:Profiler 全覆盖，含失败与延迟）
- 洞察：写入/去重（hits 递增）/错误提示联动验证通过
- 分析：通道健康表、死路候选规则、proposed_updates 结构验证通过
- 台账：Vina 对接运行自动入账（LA/MD-2 -8.809 kcal/mol 基准在册），compare 排名与过滤正常
