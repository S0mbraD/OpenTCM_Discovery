# 自动化筛选工作流体系（workflow_engine）详细参考

> 落地目录：`workflow_engine/`（本仓库内）。
> 定位：SKILL.md「自动化 DAG 工作流」一节的工程细节参考。通用性设计：换疾病/文献库/分子库/权重只改 config.yaml，引擎与服务层零改动。

## 一、五层架构与文件映射

| 层 | 文件 | 职责与要点 |
|---|---|---|
| L1 数据层 | config.yaml | 输入路径（分子库/文献库/临床药表/CTD 本地数据）、证据权重（临床 1.0 / E1 0.7 / E2 0.4）、评分权重（0.7 覆盖 + 0.3 ADME）、阳性药列表 |
| L2 步骤库 | steps_uc_screening.py | 11 个步骤函数；每步向事件总线汇报状态与证据；疾病特定逻辑集中在 S4/S5/S10 |
| L3 DAG 引擎 | engine.py | 拓扑排序调度、步骤状态机（pending/running/success/failed）、事件总线、循证溯源采集、运行记录持久化 |
| L4 服务层 | run_server.py | REST + SSE；端口 8765 |
| L5 可视化 | dashboard.html | 单文件无外部依赖；深色科技风；DAG 节点状态流转动画、循证卡片流、时间线、结果表、成分循证链弹窗 |

## 二、11 步流水线输入/输出（UC 实例）

| 步骤 | 输入 | 输出 | 实测（run_20260904_130356） |
|---|---|---|---|
| S1 加载分子库 | UC中药有效成分分子库_筛选OB30DL018.xlsx | 成分记录 1638 条 | — |
| S2 加载文献机制库 | UC有效中药机制数据库_标准化数据挖掘版.xlsx | 主表 243×25 + 机制三元组 1173（Target 型 504，HGNC 434） | — |
| S3 加载临床药表 | ACG,AGA,ECCO（27 药）+ drugbank 靶点表 | 91 药物-靶点对，49 基因（Gene Symbol 分号分隔需拆分） | — |
| S7 加载 CTD 本地数据 | CTD_chem_gene_ixns.tsv.gz / CTD_chemicals.tsv.gz | 人类交互索引（流式扫描） | 全程离线 |
| S4 临床锚定靶点集 | S3 | 锚定靶点集（权重 1.0） | 49 基因 |
| S5 文献机制靶点集 | S2 | 文献靶点集（E1 0.7 / E2 0.4） | 66 基因（152/243 条有标准靶点） |
| S6 整合分层 | S4+S5 | 统一加权靶点集 | 115 基因；双证据交集 6 |
| S8 CTD 三键匹配 | S1+S6+S7 | 成分→CTD ID→人类交互靶点 | 188/1638 命中（11.5%），4379 条人类交互；重写后：名称规范化 _norm_name（去盐型/手性前缀）、lib_keys 升级为 key→(row_index, key_type) 五型、交互明细表 ctd_ixn_detail、每 20 万行进度上报，速度大幅提升 |
| S9 评分 | S6+S8 | 加权总分排序 | 评分升级后（2026-09-04 13:04 复跑）Top1=Arachidonic acid（锚定覆盖率 0.9592、支撑增益 0.3）；新列：锚定覆盖率/支撑增益/靶点作用方向/化学结构大类/来源药材 |
| S10 阳性药验证 | S6+阳性药列表 | 靶点完整性 + 留一冗余度 | 4 药完整性 100%，冗余度均值 73% |
| S11 产物生成 | S8+S9 | 评分 xlsx + 循证明细 xlsx + CSV + state/evidence json | 评分表 188 行×17 列；循证明细 1037 条证据行 |

## 三、服务 API 与数据结构

- `GET /api/state`：全图步骤状态（驱动 DAG 动画）
- `GET /api/evidence`：循证条目列表（驱动循证面板；条目含 step/source/record_id/doi/url/weight）
- `GET /api/top?n=30`：Top N 候选评分
- `POST /api/run` / `POST /api/reset`：启动/重置工作流
- `GET /api/events`：SSE 实时事件流（状态流转、进度、循证卡片、step_log 推送）
- `GET /api/step_logs?step={step_id}`：步骤滚动日志缓冲（deque maxlen=300，四级 info/success/warn/error）
- `POST /api/chat`：AI Agent 对话（chat_delta 流式转发、对话历史持久化 chat_history.json、20 个注册工具）
- 运行记录：`artifacts/run_YYYYmmdd_HHMMSS/`（state.json + evidence.json + 产物文件）

## 四、DAG 引擎 6 个实测 bug 与修复（通用教训）

| # | 症状 | 根因 | 修复 |
|---|---|---|---|
| 1 | S4/S5 读不到上游数据 | 每步骤启动时创建独立 WorkflowContext | 全 run 共享 shared_ctx |
| 2 | 并行步骤进度上报张冠李戴 | S1/S2/S3/S7 并行时共享 `_step_name` 相互覆盖 | `threading.local` 存步骤名 |
| 3 | /api/top ERR_EMPTY_RESPONSE | 代码访问不存在的 `app.engine.data` 属性，AttributeError 未捕获 | 引擎加 `data` property + API 层 try/except |
| 4 | CTD 匹配率异常 | 同义词列误用 parts[8]（实为 ParentIDs） | 先读表头确认列序；同义词在 [11]/[12]；补 PubChem CID（词表 [3] 列）作第三匹配键 |
| 5 | Top15 全满分无法区分 | `min(cover/5.0)` 归一饱和 | 按当批实际最大覆盖分归一 |
| 6 | 部分接口空响应 | NaN 进入 JSON 序列化 | 序列化前 sanitize（NaN→None） |
| 7 | worker 线程内日志 step 字段恒为空 | launch() 在调度器主线程调 ctx.set_current_step，而 threading.local 隔离使 worker 线程读不到 step_name | 将 set_current_step 移入 worker 线程内执行（设置与读取必须同线程） |

待加固项：并行步骤读写 `ctx.data` 的线程安全（可加 `threading.Lock`）。

## 五、复用方法（换疾病/换库示例）

1. config.yaml：改疾病 MeSH ID（如 UC=D003093）、4 个输入文件路径、证据权重、阳性药列表
2. steps_*.py：仅需适配 S4/S5 的输入列名与 S10 阳性药名
3. engine.py / run_server.py / dashboard.html：零改动

## 六、运行命令（双平台）

```powershell
$env:PYTHONUTF8="1"
python workflow_engine\run_server.py   # 端口 8765
# 浏览器打开 http://127.0.0.1:8765/ 点「启动工作流」
```

Unix/macOS：

```bash
python workflow_engine/run_server.py   # 端口 8765
```

> 运行前需在 `workflow_engine/config.yaml`（或环境变量）中配置 `paths` 段输入文件路径。
