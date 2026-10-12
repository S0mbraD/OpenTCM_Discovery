# M7 运行时自迭代（Runtime Self-Iteration）

**触发意图**：会话结束前 / 发现新情况 / 长期运行 / 复现性疑问 / 死路确认（runtime, 自迭代, 死路, dead end, repro）

## 闭环架构

```
写路径（自动/主动）：
  遥测 call_log.jsonl    每次 DB 请求/脚本调用自动落盘（host/status/延迟）
  洞察库 insights.json   Agent 分析中新发现主动写入（去重计数）
  实验台账 experiments.jsonl   每次对接/建模自动入账（参数+亲和能+模式谱）
读路径：
  tcm_analyze_runtime / dock_analyze_runtime
  → 通道健康表 / 死路候选 / 慢通道 / 复现红旗 / 洞察合并 → proposed_updates
出口：
  proposed_updates → 平台技能自进化确认流（用户确认后才改文档）
```

## 工具链

```
① tcm_analyze_runtime {}    # 检索族：每库成功率/延迟/死路候选（≥5 调用且成功率≤50%）
② dock_analyze_runtime {}   # 对接族：脚本健康/实验统计/同seed复现红旗
③ tcm_report_insight {category, title, detail, evidence}
   category: dead_end | endpoint_change | quirk | performance | new_channel | methodology
④ dock_report_insight {category: environment|method|quirk|performance|repro, ...}
⑤ dock_compare_experiments {ligand, receptor, exp_type}   # 台账查询/排名
```

## 决策规则

- 失败请求自动附带相关洞察提示（error hint）；错误消息指引查 analyze_runtime
- 洞察 hits≥2 才进入 proposed_updates 强建议区（防自进化过拟合，RRSI 思路）
- 洞察**不自动改写请求路由**——只做建议与提示

## 会话收尾协议（Agent 必做）

1. `tcm_analyze_runtime` + `dock_analyze_runtime` → 提取 proposed_updates
2. 有新发现（本次会话学到的）→ `*_report_insight` 落库
3. proposed_updates 摘要给用户 → 用户确认 → 平台技能自进化 staged update（带回退凭证）

状态目录：`~/.tcm_mcp_state`（TCM_MCP_STATE_DIR 可覆盖）、`~/.dock_mcp_state`（DOCK_MCP_STATE_DIR）。
机制细节/格式/开源依据（AgentLens/Self-Improving/Hermes）：references/self_iteration.md
