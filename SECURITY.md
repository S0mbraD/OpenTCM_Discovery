# Security

open-tcm-discovery 以本地 MCP 服务器运行，不包含任何云端后端，也不收集使用数据。本文件说明安全边界与漏洞报告方式。

## 设计边界

- 所有工具在本机执行；网络请求仅发生在调用检索类工具时，访问公开数据库 API。
- 运行时状态（缓存、检查点、死路记录）写入用户目录下的 `~/.tcm_mcp_state/`，不会随仓库提交（见 .gitignore）。
- 本地服务默认绑定 `127.0.0.1`。如以 StreamableHTTP 对外暴露，请自行处理鉴权与防火墙。

## 报告漏洞

发现安全问题时，请**不要**在公开 issue 中披露细节，优先采用以下渠道：

1. 使用 GitHub Security Advisories（仓库 Security → Report a vulnerability），不要开公开 issue。
2. 仓库尚未启用 Advisories 时，私下联系维护者，并在公开渠道只写「存在安全问题、请走私下渠道」。

请在报告中包含：受影响版本、可复现步骤、影响范围与建议修复方案（如已有）。

## 已知注意点

- 第三方数据库 API 可能随上游变更（端点迁移、schema 变化），代码中的可用性快照（`references/database_availability_2026-09.md`）为某一时间点的实测结果，不保证长期有效。
- AutoDock Vina 官方二进制为 Apache-2.0。本仓库默认不提交 `bin/vina.exe`，使用者自行放置，见 `bin/README.md` 与 `NOTICE`。
- 数据处理脚本遵循原子写与串行约束；发现并发写风险请按 [CONTRIBUTING.md](CONTRIBUTING.md) 质量要求处理。

## 致谢

感谢所有为项目安全与严谨性提出建议的贡献者。
