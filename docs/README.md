# 文档

阅读顺序按你要做的事，而不是按文件名字母。

| 文档 | 何时读 |
|---|---|
| [科学边界](SCIENTIFIC_SCOPE.md) | 写报告、做图、判断一个分数能不能进论文之前 |
| [数据来源](DATA_SOURCES.md) | 查看某个数据库是直接调用、本地表，还是目前不接入 |
| [技术路线](ROADMAP.md) | 查看已经守住的边界，以及接下来四阶段做什么、不做什么 |
| [工作流](WORKFLOWS.md) | 选择 MCP 深链、并行工具，还是本地 DAG |
| [架构](ARCHITECTURE.md) | 改服务器分层、缓存或步号 |
| [工具总表](TOOLS.md) | 查 56 个工具各自做什么 |
| [部署](DEPLOYMENT.md) | 把服务器接到 Cursor 或其他 MCP 客户端 |
| [模块索引](../modules/MODULE_INDEX.md) | 看 D1–D6 和每一层的实现要点 |
| [证据分级](../references/evidence_grading.md) | 写图注和残基标注 |
| [数据索引](../data/README.md) | 使用或重建 `herb_mol_index.json` |
| [Vina 二进制](../bin/README.md) | 准备对接 |
| [第三方声明](../NOTICE) | 发布、再分发或写致谢 |

`modules/m1` 到 `m7` 是七个模块的操作说明。`references/` 按数据库和主题记录端点、死路和渲染风格；用库名搜索，不必通读。

技能协议本身在仓库根目录的 `SKILL.md`。给 Agent 的短指令以那份文件为准，给维护者的说明以 `docs/` 为准。两边的工具数、端口和深链顺序必须一致，`python scripts/check_repo.py` 会检查这一点。
