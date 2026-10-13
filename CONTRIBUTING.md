# Contributing

欢迎参与 open-tcm-discovery 的开发。这个仓库是给任意 MCP 客户端用的检索工具。Cursor 只是目前用来试的一个客户端，配置示例里的路径用 `<仓库绝对路径>` 或 `<仓库路径>`，不要写成某一台机器上的目录。

这些约定来自实际做课题时卡住的地方。在线结果要能追溯到来源和时间。计算预测不要写成实验结论。对接输出写成提议的对接模型，口袋残基必须来自文献。文献共现、方剂集合重叠、试验登记、嵌入、敲除、ADMET 近似和剂量换算都不是疗效。预测靶点不是已记录证据。

你改的是工具、测试或说明。改完之后，别人应能在一台干净的 Python 3.10 环境里复现检查，并且不需要你的本机路径。提交代码之前先看下面几条。

## 项目边界

- 本项目是科研工具，输出不构成医疗建议，不做临床疗效断言。
- 一切在线检索必须为实时请求，禁止在代码或文档中内置“预置答案”。接口返回统一携带 `source` 与 `queried_at`。
- 计算预测必须与实验证据分级：对接/建模结果标注 `proposed docking model`，预测残基加 `*` 与虚线，不混合两种证据。

## 数据与口径约定

- ADME 筛选：OB/DL 仅采用 TCMSP 官方口径，其他数据库（如 HERB）的 DL 算法不同，不得混用。
- 多基原药材：按《中国药典》定基原，检索按物种拉丁学名进行，去重按 InChIKey > SMILES > CID > CAS > 规范化名称五层顺序。
- 数据补全只填缺失行，不覆盖已有值；每个字段记录来源列，不得编造。
- 假 CID 判别：PubChem CID 超过 1.6 亿为脏数据。

## 开发流程

1. 改动前先读 `modules/MODULE_INDEX.md` 与对应 layer 文件，确认改动点归属。检索族在 `open_tcm_discovery_mcp.py`，对接族在 `structure_docking_mcp.py`，统一入口在 `unified_mcp.py`。新行为若只属于某一层，不要塞进 `tcm_smart_run` 的自动链。数据整理、TCMSP 旧站检索、成分名对齐和靶点表读入现在就不在这条自动链里。
2. 代码改动必须同时更新 `modules/MODULE_INDEX.md`（工具注册）、SKILL.md（行为描述）与 `mcp_server/evaluation.xml`（基准题，如涉及行为变化）。`docs/TOOLS.md` 里的工具名要用反引号包住。`scripts/check_repo.py` 会扫这张表。工具总数要和代码里的注册数一致，目前是 69。只改说明时，也要让这三处和代码对得上，不要把路线图里还没做的阶段写成已经可调用。
3. 新工具遵循既有前缀：检索/分析 `tcm_`，对接/结构 `dock_`。数据整理、TCMSP 和本地对齐也用 `tcm_`。不要另起一套前缀。
4. 提交前在仓库根运行自检。它不访问网络，只做语法解析、69 个工具名与文档口径对齐：

   ```bash
   python scripts/check_repo.py
   ```

   失败时先读它打印的那一行。常见的是工具数对不上，或 `docs/TOOLS.md` 少了某个反引号里的名字。不要为了让检查通过去改 OB/DL 门槛、口袋规则或数据源地址。
5. 行为变化要对照 `mcp_server/evaluation.xml`。新增对外行为时补一条可复核的基准记录，写实测观察，不写推断值。在线回归需要公网，不放进默认 CI。一次超时记成这次请求失败，不要写成该库已经停用。
6. 提交信息用描述性短句，注明修复的问题或新增能力，例如 `fix(chembl): use molecule_structures for SMILES`。一次提交一个逻辑改动。说明文档和对应的行为改动可以放在一起，不要把无关的历史口径改写掉。

## 质量与测试要求

- 修复 bug 必须带回归用例，参照 `references/iteration_history.md` 中的历史事故清单（如 SMILES 大小写、CNaN 并查集、交付链并发写）。默认测试是 `python -m unittest discover -s tests -q`，不访问网络。改统计、口袋、对齐或剂量换算时，先让对应测试能抓住这个行为，再改实现。
- 剂量换算按调用方点名的历史口径分开。`market_1959`、`han_tang`、`song_yuan_2025`、`ming_qing_2025`、`song_jin_yuan_2024` 和 `ming_qing_2024` 里的「两」不是同一个克数，不要并成一个通用单位。
- 交付链脚本（构建/导出）严禁并行执行；启动前确认无残留 python 进程。
- 共享数据文件原子写（临时文件 + `os.replace`），串行访问。

## 隐私与发布

- 不提交：本机绝对路径、用户名/用户目录、API Key、微信等个人文件路径、运行时状态（`~/.tcm_mcp_state/`）。
- 部署配置中的路径使用占位符（参见 `mcp_server/deployment_config.json` 与 `docs/DEPLOYMENT.md`）。
- `references/database_availability_2026-09.md` 是 2026 年 9 月的端点快照。当前接法看 `docs/DATA_SOURCES.md`。不要把那份快照改写成现在的网站状态，也不要在新文档里把某一次停机写成永久结论。
- 新增文档中文为主，命名随既有结构（`references/` 分域、`modules/` 模块说明）。

## 提交格式

- 分支：`fix/<描述>` 或 `feat/<描述>`。
- PR 描述模板：改动动机（关联 issue）、改动内容、测试结果（数字）、遗留事项。
- 保持提交粒度：一次提交一个逻辑改动。

## 行为规范

参与本项目即同意遵循 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。安全问题走 [SECURITY.md](SECURITY.md) 流程。
