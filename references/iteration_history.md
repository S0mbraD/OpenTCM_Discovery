# 迭代演进史（Iteration History）

> 本技能的方法论与脚本并非一次性写成，而是经过 2026-08-28 至 2026-09-12 共 14 个工作日、941 个实验脚本、6 个交付版本的迭代沉淀。
> 本文档记录完整演进脉络、关键决策、死路与教训，供后续复用者避坑。
> 全部 941 个实验脚本的结构化索引见同目录 `iteration_scripts_index.tsv`；代表性里程碑脚本见 `scripts/archive/`。

## 一、项目主线

**研究背景**：UC（溃疡性结肠炎）中药有效成分分子筛选 + AI 药物靶点筛选（网络药理学）。

**最终交付**（v5.5，2026-09-12，QC 35 PASS / 0 FAIL）：
- Compound_Master：51,399 行 × 43 列
- 结构组 32,768；AF3 库 20,594（UC 子集 12,622）；P0 复核 2,836 行
- 覆盖率终态：SMILES 76.0% / InChIKey 76.1% / Formula 91.3% / MW 86.0% / CAS 50.3% / Name_CN 14.1%

## 二、七个阶段演进

### 阶段 1（8/28-8/29）：TCMSP 数据破冰
- 起点：TCMSP 无公开 API，网页是 KendoGrid 动态渲染 → 从 HTML 提取 dataSource（`analyze_tcmsp_html.py`，5 版迭代）
- 建立「两步搜索法」：中文名 → 拉丁名 → 成分列表
- 首版 Excel 构建与成分-Excel 匹配验证
- 里程碑：`s01_tcmsp_html_parse_origin.py`、`s01_dedup_classify_v0.py`、`s01_build_excel_v0.py`

### 阶段 2（8/29-8/30）：多库扩展与多基原处理
- HERB/ETCM/PubChem 三库接入；383 味药材「药材名→拉丁学名」权威映射（`build_species_mapping.py`）
- OB≥30% / DL≥0.18 筛选口径确立（TCMSP 官方口径，与 HERB 的 DL 算法不可混用）
- 首次跨库去重：InChIKey > SMILES > CAS > 分子式+分子量
- 里程碑：`s02_species_mapping.py`、`s02_multi_source_herbs.py`

### 阶段 3（9/1-9/2）：靶点侧基建
- CTD 全量数据下载策略确立（`.tsv.gz` 静态文件绕开网页 ALTCHA 人机验证；UC MeSH=D003093）
- DrugBank SPARQL via UniProt（官网 Cloudflare 拦截，走 UniProt 端点）
- 文献参考靶点集构建（build_ref_targets 3 版）
- 里程碑：`s03_ctd_download.py`、`s03_drugbank_sparql.py`

### 阶段 4（9/4-9/9）：外部通道补全攻坚
- 逐一试探外部数据源：TCMBank（11 版 probe 才打通 API 闭环）、Cactus（8 版）、OPSIN、NPASS/COCONUT
- 大量「死路」记录：NPASS 404、KNApSAcK 超时、ChEMBL 500、PubChem POST 503 等
- 通道命中数据：全库 TCMBank 1,650 / UC TCMBank 213 / OPSIN 23 / Cactus 668（后弃用）/ NPASS+COCONUT 220（后弃用）
- 里程碑：`s04_tcmbank_full_fill.py`、`s04_cactus_fetch_v2.py`

### 阶段 5（9/9-9/10）：三 Agent 流水线与实体去重
- 导师规范的 A/B/C 三 Agent 流水线：A=Name_CN 补全、B=桥接（CID↔IK↔名称）、C=QC 五维评分
- 母体化学实体原则确立（盐型/手性前缀不拆分为新实体）
- 五层去重键体系落地：InChIKey > SMILES > CID > CAS > 规范化名称
- AIGC 水印过滤三件套：`~str.startswith('AIGC')` + `dropna(how='all')` + `Compound_ID.notna()`
- 里程碑：`s05_agent_a_namecn.py`、`s05_agent_c_qc_v2.py`、`s05_entity_dedup.py`

### 阶段 6（9/10-9/12）：标准化交付链 phaseA-E
- phaseA：增量合并 → phaseB：QC 与写回 → phaseC：来源映射（Source_Map）→ phaseD：Excel 交付 → phaseE：35 项验证
- 版本链 v5 → v5_1 → v5_2 → v5_3 → v5_4 → v5_5（每版 PASS/FAIL 全量验证）
- **事故教训**：两进程同写一个 xlsx 导致 Bad CRC-32 损坏（phaseD 严禁重复/并行执行）；phaseB QC 中间 pkl 与 p0_writeback 同秒互覆（修复靠幂等重跑）
- 启动交付链前必须 `Get-Process python` 确认无残留进程
- 里程碑：`s06_phaseD_deliver_final.py`、`s06_phaseE_verify_final.py`、`s06_p0_writeback.py`

### 阶段 7（9/11-9/12）：v6/v7 五通道引擎化
- v6 五通道体系（`v6_common.py` 共享限流/重试/缓存基建）：
  - A=PubChem property 批量（100 CID/次）：MW_DB +2,315 / Formula +644
  - B=PubChem synonyms 单查提取 CAS+校验位：+1,299
  - C=Wikidata SPARQL 三路（P662/P235/P231）：Name_CN +124
  - D=RDKit 离线 SMILES→MW/Formula/IK/SMARTS 十类 Class：+22,369（32 秒零成本，收益最大）
  - E=CAS 反查 CID：+387
- Structure_Class 覆盖率 31.2% → 74.7%
- v7 增量补全：engine_A/engine_B 引擎化 + CID 缺口补全（RDKit canonical +50 / PubChem +19）
- 里程碑：`s07_v6_common.py`、`s07_v7_engine_A.py`、`s07_v7_rdkit_backfill.py`

## 三、已确认死路清单（不要再花时间）

| 通道 | 结论 | 证据 |
|---|---|---|
| PubChem 中文名（Title/同义词） | 0 命中 | 26,300 次 Title 查询 + 600 次同义词查询均 0 |
| SMILES→CID fastidentity | 400 错误 | 接口不支持该方向 |
| Name_EN→CID（半系统命名） | 命中率 1.3% | 非官方俗名格式 |
| 本地资产 ETCM/SMIT/HERB/SymMap 反查 | 0 命中 | 已耗尽 |
| NPASS 官网 | 404 | 2026-09 实测 |
| KNApSAcK | 超时 | 多次重试 |
| ChEMBL 直接 API | 500 | 间歇性 |
| TCMBank 直接访问 | 500/SSL | 仅 API 端点存活 |
| LOTUS | 连接失败 | — |
| Cactus（SMILES 解析） | 命中质量差 | 8,240/31,405 后弃用 |
| 假 CID（>1.6 亿超长数字） | 脏数据 | 直接过滤 |

## 四、关键方法论沉淀

1. **补全三原则**：只填缺失行不覆盖已有值；每字段带 `*_Source` 溯源列；必须真实数据不编造
2. **CAS 校验位验证**：提取后必须跑校验位算法过滤脏数据
3. **原子写**：临时文件 + `os.replace`，严禁同一文件并发写（Excel/pkl 会损坏）
4. **限流熔断**：PubChem 1s 间隔 + 4 次重试 + 503/502/500 退避 5s×attempt + 连续 10 次网络失败熔断保存退出（404 属业务响应不熔断）
5. **断点续传**：所有长任务通道必须带缓存文件，中断后从缓存续跑
6. **QC 五维评分**：PASS≥90 / REVIEW 70-90 / REJECT<70；`Smiles_Parse` 旧值 `no_smiles` 需视为缺失重新 QC
7. **交付四件套**：Compound_Master / Compound_Source_Map / QC_Report / AF3_Input + P0_Review

## 五、DAG 工作流引擎的 7 个实测 bug（通用工程教训）

详见 `workflow_system.md` 第四节。核心教训：threading.local 的设置与读取必须同线程；API 层必须 try/except 防止单接口 500 拖垮整个服务；NaN 序列化前必须 sanitize。

## 六、脚本索引使用说明

`iteration_scripts_index.tsv`（941 行）字段：`relative_path` / `size_bytes` / `last_modified` / `topic_docstring`。

按需检索示例：
- 找所有 TCMBank 相关实验：过滤 `relative_path` 含 `tcmbank`（16 个）
- 找交付链全部版本：过滤 `phase[ABCDE]`（40+ 个）
- 找通道引擎：`v6/channel_*`（6 个）、`v7/engine_*`（2 个）

`scripts/archive/` 的 47 个里程碑脚本已按阶段前缀命名（s01-s07），每个阶段的核心决策都有对应可读代码。注意：归档脚本是**历史快照**（路径已脱敏为 `<WORKSPACE>` 占位符），用于理解演进逻辑，不建议直接运行；生产可用版本是 `scripts/` 下的 10 个 argparse 化脚本。
