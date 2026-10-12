# 示例

在仓库根目录运行。MCP 依赖来自根目录 `requirements.txt`。

| 文件 | 用途 |
|---|---|
| `basic_usage.py` | 拉起 `unified_mcp.py`，打印工具数，并调用 `tcm_check_database_status` |
| `mcp.stdio.json` | 客户端配置。把 `<REPO>` 换成绝对路径 |
| `smart_run_example.json` | 一次不对接的在线深链：溃疡性结肠炎与 berberine |
| `docking_resume.example.json` | 暂停后续跑的字段形状。口袋残基必须换成文献中的编号 |

```bash
python examples/basic_usage.py
```

`docking_resume.example.json` 里的 `REPLACE_*` 是占位符。原样提交给 `dock_vina_docking` 不会得到有意义的姿势。深链在缺口袋时会暂停，这是预期行为。

HTTP 接入见 [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md)。
