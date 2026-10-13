# 数据来源

这张表区分三件事：数据库现在公开提供什么，本仓库是否在工具里直接请求它，以及结果在证据上属于哪一层。探测日期写在每一节里。2026 年 9 月 22 日的端点快照仍保留在 `references/database_availability_2026-09.md`，那里记录的是当时的 HTTP 结果，不是数据库论文里的宣称，也不是现在该走哪个 TCMSP 地址。当前成分表接法以下面 2026-10-13 的探测为准。不要去改那份九月快照来追当前状态。

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
| TCMSP 2.3 旧站 | `tcm_tcmsp_search` 读 `https://old.tcmsp-e.com/` 的药材表和成分表 | 数据库记录 | OB≥30、DL≥0.18 只过滤本页的 `ob`、`dl`。不是疗效。这一页没有靶点 |
| GWAS Catalog、DGIdb、CTD、Ensembl | 基因、化学物和疾病关联 | 数据库记录 | CTD 本地压缩包只在本地打分流程里使用 |

## 论文里重要、但当前没有接成实时工具的来源

这些库没有被漏记。它们或者没有稳定的公开程序接口，或者接口在上次探测中失败，或者返回的预测靶点不能和已知靶点混计。下一阶段是否接入，以 [ROADMAP.md](ROADMAP.md) 里的探针结果为准，不在参数未公开时猜测 URL。

| 来源 | 公开入口 | 2026 年 10 月的判断 |
|---|---|---|
| ETCM 2.0 | `http://www.tcmip.cn/ETCM2/front/`，详情在 `:18124` | 2026-10-13 前端是 HTML。`/ETCM/api/` 仍是 404。`/home/detail/` 和 `/home/herb_chem_phy/` 返回了 JSON，字段不是 TCMSP 的 OB、DL，所以没有另做工具 |
| BATMAN-TCM 2.0 | `http://bionet.ncpsb.org.cn/batman-tcm/` | 2026-10-13 首页超时，`index.php` 连接被关掉，HTTPS 握手失败。论文写了 API，这次没有打开可复现的 JSON 参数，不做工具 |
| HERB 2.0 详情 | `http://herb.ac.cn/`，`POST http://47.92.70.12/chedi/api/` | 2026-10-13 首页是 HTML。`/v2` 没有连上。`detail_api` 对 `HERB000281` 返回了 JSON 正文，Content-Type 却是 `text/html`。类药性不能代替 0.18，这次不另做 HERB 工具 |
| DisGeNET | 需要使用许可 | 不把公开页面解析当作接口 |

## 2026-10-13 探测：为什么现在走 TCMSP 旧站

探测时间是 2026-10-13 04:38 UTC 起，单次超时 20 秒。下面的状态是这次请求的结果，不是论文里的宣称。仓库没有下载任何全库压缩包。GitHub 上的 TCMSP-Spider 已归档，TCMSP-Exporter 是浏览器脚本，仍然指向 `www.tcmsp-e.com/tcmspsearch.php`。那个地址现在是入口页，不是旧表格。

| URL | 状态 | 最终 URL | 正文 |
|---|---|---|---|
| `https://www.tcmsp-e.com/` | 200 | 同左 | HTML。入口页，链到旧站和 `next.tcmsp-e.com` |
| `https://www.tcmsp-e.com/tcmsp.php` | 200 | 同左 | HTML。同一张入口页，不是旧的 PHP 搜索 |
| `https://www.tcmsp-e.com/tcmspsearch.php` | 200 | 同左 | HTML。同一张入口页 |
| `https://tcmsp-e.com/browse.php?qc=herbs` | 200 | 同左 | HTML。同一张入口页 |
| `https://old.tcmsp-e.com/` | 200 | 同左 | HTML。旧版 XHTML 站点 |
| `https://old.tcmsp-e.com/tcmsp.php` | 200 | 同左 | HTML。隐藏字段里有 token |
| `https://old.tcmsp-e.com/tcmspsearch.php?qs=herb_all_name&q=甘草` 加当次 token | 200 | 同左 | HTML。表格里嵌着 JSON，一行药材：甘草 / licorice / Gancao |
| 同上，`qr=licorice&qsr=herb_en_name` | 200 | 同左 | HTML。表格里嵌着 JSON，280 行成分，字段含 `MOL_ID`、`ob`、`dl` |
| `https://tcmspw.com/` 与 `/tcmsp.php`、`/tcmspsearch.php` | 无状态码 | 无 | TLS 握手被对端提前结束 |
| `http://tcmspw.com/` | 无状态码 | 无 | 对端关掉连接 |
| `http://www.tcmsp-e.com/tcmsp.php` | 无状态码 | 无 | TLS 握手失败 |
| `https://next.tcmsp-e.com/` | 200 | 同左 | HTML。产品介绍页，这次没有可复现的查询参数 |
| `http://www.tcmip.cn/ETCM2/front/` | 200 | 同左 | HTML |
| `https://www.tcmip.cn/ETCM2/front/` | 无状态码 | 无 | TLS 握手失败 |
| `http://www.tcmip.cn/ETCM/api/` | 404 | 同左 | HTML |
| `http://www.tcmip.cn:18124/home/detail/?id=黄连&type=herb` | 200 | 同左 | JSON。`code` 为 1，药名 HuangLian |
| `POST http://www.tcmip.cn:18124/home/herb_chem_phy/`，正文 `{"id":"黄连","type":"herb","Component_Phy_Chem":["Molecular Weight"]}` | 200 | 同左 | JSON。成分名和分子量，不是 OB、DL |
| `http://herb.ac.cn/` | 200 | 同左 | HTML |
| `http://herb.ac.cn/v2` 与 `https://herb.ac.cn/v2` | 无状态码 | 无 | 连接被关掉，或 TLS 握手失败 |
| `http://47.92.70.12/` | 200 | 同左 | HTML。页面标题资源是 HERB 2.0 |
| `POST http://47.92.70.12/chedi/api/`，`func_name=detail_api`，`key_id=HERB000281` | 200 | 同左 | 正文是 JSON，`Content-Type` 为 `text/html`。含 `herb_ingredient` |
| `http://bionet.ncpsb.org.cn/batman-tcm/` | 无状态码 | 无 | 20 秒超时 |
| `http://bionet.ncpsb.org.cn/batman-tcm/index.php` | 无状态码 | 无 | 对端关掉连接 |
| `https://bionet.ncpsb.org.cn/batman-tcm/` | 无状态码 | 无 | TLS 握手失败 |

这次能同时给出 TCMSP 成分、`ob` 和 `dl` 的，只有旧站。`www.tcmsp-e.com` 上的 `tcmsp.php` / `tcmspsearch.php` 已经不再返回那张表。`tcmspw.com` 连不上。ETCM 和 HERB 的上述请求能复现，但它们的数字不是 TCMSP 的口服生物利用度和类药性，不能拿来当 0.18 这道门。BATMAN-TCM 这次没有返回可解析正文。所以工具只请求旧站。

`tcm_tcmsp_search` 每次重新取 token。超时 20 秒。超时或 HTTP 5xx 再试一次。页面里没有表格、或请求失败时，`ingredients` 为空，不补记录。响应带 `source` 和 `queried_at`。打开 `ob_dl_filter` 时只保留本页 `ob≥30` 且 `dl≥0.18` 的行，丢掉的行数在 `ob_dl_dropped`。这个开关默认关闭。检索结果不是疗效。成分行不是靶点收录。

读这一次的 JSON 时，先看 `error` 是不是空的，再看 `herbs` 有几行。多行且没有 `latin_name` 时，`note` 会说明要你指定 `herb_en_name`，成分表不会替你选一行。只有一行时，工具用那一行的 `herb_en_name` 继续取成分。成分行里用得到的是 `molecule_name`、`MOL_ID`、`ob`、`dl`。`limit` 只截断返回的行数，`ingredients_matched` 仍是筛完之后的总数。HERB 详情里的类药性、ETCM 返回的分子量，都不能填进这几个字段，也不能拿去和 0.18 比较。

`tcm_align_tcmsp` 不请求上面任何一个地址。它只对齐调用方已经拿到的成分行，以及调用方自己的名称和 InChIKey。`data/herb_mol_index.json` 没有这些名称，所以不对着那份索引做。返回里没有 `queried_at`。`link` 为 `name` 或 `inchikey` 都只是字符串相同。对不齐的行在 `tcmsp_unmatched` 或 `catalog_unmatched`，含糊的在 `ambiguous`。不要把对不齐写成「该成分不存在」。

其他在线工具也按同一习惯读。成功时保留 `source` 和 `queried_at`。通道级失败放在该通道的 `error` 里，不要把失败通道算进共识分。`tcm_check_database_status` 在 `refresh` 为 false 时给出保存的探测表，看 `snapshot_date`。那张表不是本节这张 2026-10-13 的 URL 对照，也不能代替正式分析前的再探。`refresh` 为 true 时只重探一小部分关键端点。

文献共现、方剂集合重叠、试验登记、图嵌入、敲除和 ADMET 近似都不占用上面的数据库记录名额，也不构成疗效。预测靶点即使来自某个库的预测列，也不是已记录证据。本仓库还没有一个工具去读调用方标好的 `recorded` / `predicted` 列，那是路线图里还没做的阶段。

## 计算层，不占用数据库名额

RDKit 描述符、ESOL 水溶性近似、血脑屏障与 hERG 的盒式规则、图嵌入、网络敲除和 Vina，都是计算预测。ADMETlab 的在线接口在先前探测中不可用，因此 ADMET 面板全部在本地计算并标明模型名称。HPA 与 GTEx 的相关接口当时失败，单细胞工具只到 GEO 去发现数据集，不下载表达矩阵。数据整理的六个工具和成分名对齐都不请求数据库。疾病名只对照一份写在代码里的常用 MeSH 子集，基因符号也只对照一份常见别名表。
