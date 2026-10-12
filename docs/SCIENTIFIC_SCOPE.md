# 科学边界

open-tcm-discovery 是中药小分子的计算检索与分析工具。它帮助研究者把公开数据库、网络拓扑、理化描述符和对接姿势放进同一条可追溯的流程。它不产生临床建议，也不把计算排名写成药效。

## 可以支持的工作

- 从疾病名检索通路和基因，再决定要不要做 PPI。
- 把一个成分或基因放到多个公开库里对照，保留每条记录的来源和时间。
- 用本地 SymMap 索引比较几味药的成分集合是否重叠。
- 在文献已经给出口袋残基时，跑 AutoDock Vina，并单独列出接触。
- 查找 PubMed 共现和 ClinicalTrials.gov 试验，作为下一步实验或阅读的索引。
- 对用户自备的机制表和分子库做加权评分，并用阳性药检查这套权重是否把已知药排在前面。

## 不能支持的结论

| 输出 | 它是 | 它不是 |
|---|---|---|
| 多库共识分 | 若干数据库同时收录该实体 | 结合亲和力或临床有效 |
| OB/DL 过滤 | TCMSP 口径下的历史筛选门 | 与 HERB 等库的 DL 可互换的活性证明 |
| RDKit 描述符、ESOL、BBB/hERG 代理 | 分诊用近似 | 实测 ADMET |
| hub、敲除 z-score、图嵌入 | 网络拓扑 | 基因必需性或机制验证 |
| 文献共现 | 检索式命中篇数 | 因果关系 |
| 临床试验匹配 | 该病、该靶点或该成分附近有试验 | 中药候选的疗效 |
| Vina 亲和能与接触 | proposed docking model | 晶体结构、SPR 或已验证氢键 |
| 多臂综合分、`dock_rescore` | 优先级启发式 | 经过基准验证的打分函数 |

对接图上，预测残基带 `*`，接触用虚线。有突变、竞争或直接结合实验的残基才不带星号。措辞规范见 `references/evidence_grading.md`。

## 统计与过滤约定

- 疾病–成分超几何检验的背景 N 取 20000，作为人蛋白编码基因数量的工作近似。多重比较用 Benjamini–Hochberg FDR。
- 成分进入「活性成分」讨论前，TCMSP 口径使用 OB≥30、DL≥0.18。其他数据库的 DL 不混入这道门。
- 深链对接默认 exhaustiveness 8、seed 42。批量筛选默认 exhaustiveness 4，只用于分诊；准备写入论文的姿势用 16 重跑。
- 同一种子重复对接，亲和能差超过 0.1 kcal/mol，记为复现红旗，先查受体准备是否确定，再解释生物学。
- D1 的 15 个基因、D5 的 −7 / −5 kcal/mol，是流程分支阈值。换阈值要在报告里写明。

## 可追溯性

在线工具成功返回时保留 `source` 和 `queried_at`。19 个只读工具有本地 TTL 缓存，默认 3600 秒，命中时仍显示原来的查询时间，错误响应不缓存。缓存可以通过 `tcm_cache_status` 清空。

网络图布局使用固定随机种子。Vina 调用记录种子和 exhaustiveness。本地 DAG 把每条断言的文件或接口来源写入循证日志。

仓库不预置「某病的答案」。`data/herb_mol_index.json` 只是 SymMap 的药材–成分标识映射。

## 上游数据

调用方自行遵守各数据库当时的使用条款。本仓库不转售、不镜像它们的完整库。代码里出现过、并且文档记录过可用性变化的来源包括：SymMap、PubChem、ChEMBL、KEGG、STRING、Open Targets、Pharos、UniProt、Wikidata、DGIdb、CTD、GWAS Catalog、NCBI、g:Profiler、ClinicalTrials.gov、TCMBank、NPASS、HERB。

2026-09 的端点快照在 `references/database_availability_2026-09.md`。快照会过期。正式分析前用 `tcm_check_database_status` 再探一次。已知不可作为本项目数据通道的包括：HPA 与 GTEx 的相关接口、ADMETlab REST。GEO 通道只做数据集发现，不下载表达矩阵。

## 软件引用

若使用对接结果，请同时引用 AutoDock Vina 及其版本（本流程按 1.2.5 的命令行接口编写）。若使用药材–成分索引，请引用 SymMap。本仓库的软件引用见根目录 `CITATION.cff`。
