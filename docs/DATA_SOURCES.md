# 数据来源

这张表区分三件事：数据库现在公开提供什么，本仓库是否在工具里直接请求它，以及结果在证据上属于哪一层。探测日期写在每一节里。2026 年 9 月 22 日的端点快照仍保留在 `references/database_availability_2026-09.md`，那里记录的是当时的 HTTP 结果，不是数据库论文里的宣称。

文献依据包括：Wu 等对 SymMap 的介绍（Nucleic Acids Research, 2019, 47(D1): D1110–D1117）；HERB 2.0（Nucleic Acids Research, 2025, 53(D1): D1404）；BATMAN-TCM 2.0（Nucleic Acids Research, 2024, doi:10.1093/nar/gkad926）；对中药数据库预测靶点的评估（Frontiers in Pharmacology, 2024, 1303693）；以及对网络药理学中盲对接的批评（Frontiers in Pharmacology, 2025, 1566772）。

## 已经接到工具里的来源

| 来源 | 本仓库怎么用 | 证据层级 | 注意 |
|---|---|---|---|
| SymMap 2.0 | 疾病、症状、药材、成分和富集；本地索引来自药材到成分的关联 | 数据库记录 | 大结果集可能返回 502。索引是关系缓存，见 `data/README.md` |
| KEGG | 疾病与通路 | 数据库记录 | 遵守 KEGG 的使用条款 |
| PubChem PUG-REST | 名称、CID、性质和结构 | 数据库记录 | 需要限速 |
| ChEMBL | 分子与活性相关记录 | 数据库记录 | 2026 年 9 月探测时接口可用 |
| STRING | 蛋白互作，以及敲除模拟所用的边 | 数据库记录；拓扑结论仍是计算 | 置信度由调用参数决定，报告里要写明 |
| Open Targets GraphQL v4 | 疾病相关靶点。2026 年 10 月公开文档仍指向 `https://api.platform.opentargets.org/api/v4/graphql`，数据版本页可见 26.09 | 数据库记录 | 批量全库下载不走这个接口 |
| UniProt、Wikidata | 标识互转 | 数据库记录 | Wikidata 偶发连接重置，工具内有重试 |
| g:Profiler | 与 SymMap 组成双引擎富集 | 数据库记录 | 两边都显著才当作较强的通路共识 |
| NCBI E-utilities | PubMed 共现计数 | 文献计量，不是因果 | 无密钥时保守限速 |
| ClinicalTrials.gov API v2 | 试验数、分期和 NCT | 转化上下文，不是疗效 | 匹配到试验不等于该中药成分有效 |
| Pharos | 靶点开发等级 Tclin / Tchem 等 | 数据库记录 | 只说明靶点是否已有配体或药物，不说明中药成分有效 |
| HERB 2.0、TCMBank、NPASS | 成分或靶点检索 | 数据库记录 | HERB 的类药性不能代入 TCMSP 的 0.18 门槛 |
| GWAS Catalog、DGIdb、CTD、Ensembl | 基因、化学物和疾病关联 | 数据库记录 | CTD 本地压缩包只在本地打分流程里使用 |

## 论文里重要、但当前没有接成实时工具的来源

这些库没有被漏记。它们或者没有稳定的公开程序接口，或者接口在上次探测中失败，或者返回的预测靶点不能和已知靶点混计。下一阶段是否接入，以 [ROADMAP.md](ROADMAP.md) 里的探针结果为准，不在参数未公开时猜测 URL。

| 来源 | 公开入口 | 2026 年 10 月的判断 |
|---|---|---|
| TCMSP | 历史上的 tcmsp-e.com / tcmspw.com | 2026-09-22 两个入口都失败。OB 和 DL 只使用你本地已有的 TCMSP 字段 |
| ETCM 2.0 | `http://www.tcmip.cn/ETCM2/front/#/` | 网页可浏览。2026-09-22 时 `/ETCM/api/` 返回 404，所以没有接成工具 |
| BATMAN-TCM 2.0 | `http://bionet.ncpsb.org.cn/batman-tcm/` | 论文写明有程序接口，并区分人工收录的相互作用和预测相互作用。官网教程页没有给出足够明确的 JSON 参数，当前版本不调用，以免把预测分数当成已知结合 |
| HERB 2.0 临床试验与荟萃分析 | `http://herb.ac.cn/v2` | 适合作为本地证据表，而不是在请求里临时抓取网页。路线图把这一层列为后续的可选数据 |
| DisGeNET | 需要使用许可 | 不把公开页面解析当作接口 |

## 计算层，不占用数据库名额

RDKit 描述符、ESOL 水溶性近似、血脑屏障与 hERG 的盒式规则、图嵌入、网络敲除和 Vina，都是计算预测。ADMETlab 的在线接口在先前探测中不可用，因此 ADMET 面板全部在本地计算并标明模型名称。HPA 与 GTEx 的相关接口当时失败，单细胞工具只到 GEO 去发现数据集，不下载表达矩阵。
