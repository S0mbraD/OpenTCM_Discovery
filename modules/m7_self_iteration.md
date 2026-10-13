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
  tcm_self_iterate review → 用户点名 → apply 追加 runtime_lessons.md
```

## 工具链

```
① tcm_analyze_runtime {}    # 检索族：每库成功率/延迟/死路候选（≥5 调用且成功率≤50%）
② dock_analyze_runtime {}   # 对接族：脚本健康/实验统计/同seed复现红旗
③ tcm_report_insight {category, title, detail, evidence}
   category: dead_end | endpoint_change | quirk | performance | new_channel | methodology
④ dock_report_insight {category: environment|method|quirk|performance|repro, ...}
⑤ dock_compare_experiments {ligand, receptor, exp_type}   # 台账查询/排名
⑥ tcm_self_iterate {action: review|apply|reject, confirm_ids}  # 用户点名后才写教训
```

## 决策规则

- 失败请求自动附带相关洞察提示（error hint）；错误消息指引查 analyze_runtime
- 洞察 hits≥2 才进入 proposed_updates 强建议区（防自进化过拟合，RRSI 思路）
- 洞察**不自动改写请求路由**——只做建议与提示

## 会话收尾协议（Agent 必做）

1. 阅读 `tcm_smart_run` 返回的 iteration packet，或调用 `tcm_self_iterate`（`action=review`）
2. 有新发现时，先用 `*_report_insight` 落库，再复核
3. 向用户展示 `strength=strong` 的 id、标题和证据
4. 用户点名的 id 用 `action=apply` 追加到 `references/runtime_lessons.md`；用户否定的 id 用 `action=reject`
5. 未确认时不改技能文件。已确认的条目不改变工具代码、OB/DL、口袋规则或请求路由

状态目录：`~/.tcm_mcp_state`（TCM_MCP_STATE_DIR 可覆盖）、`~/.dock_mcp_state`（DOCK_MCP_STATE_DIR）。确认账本在检索状态目录的 `iteration_ledger.json`。
机制细节/格式/开源依据（AgentLens/Self-Improving/Hermes）：references/self_iteration.md
