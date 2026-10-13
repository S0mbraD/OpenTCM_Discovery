# HERB / HERB 2.0 通路嗅探

探测开始于 2026-10-13 17:35:15 +08。证书校验保持开启。单次超时 15 秒，每个 URL 最多两次。进程没有 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`，因此未使用环境代理。没有关闭证书校验，没有写入代理地址或密钥，没有把全库文件写入仓库。

`references/runtime_lessons.md` 没有已确认条目，没有一条旧观察能对上这次失败。

`docs/DATA_SOURCES.md` 在 2026-10-13 早些时候记下的结果是：`http://herb.ac.cn/` 为 HTML，`/v2` 的 HTTP 与 HTTPS 都没有状态码，`http://47.92.70.12/` 为 HTML，`POST http://47.92.70.12/chedi/api/` 对 `HERB000281` 返回 JSON 正文，`Content-Type` 却是 `text/html`。这次按文档里已经写出的主机再测 HTTP 与 HTTPS。

## 已知主机

只使用仓库文档里已经写出的地址，没有另编域名。

| 主机 | 出处 | 这次实际请求的路径 |
|---|---|---|
| `herb.ac.cn` | `docs/DATA_SOURCES.md` | `/`、`/v2` |
| `47.92.70.12` | `docs/DATA_SOURCES.md`、`references/tcm_chem_db.md` | `/`、`/chedi/api/`、`/static/download_data/V2/HERB_herb_info_v2.txt`、`/static/download_data/V2/HERB_ingredient_info_v2.txt`、`/Detail/?v=HERB002540&label=Herb` |
| `herb.netlab-aied.org` | `references/database_availability_2026-09.md` | `/` |

详情只请求了已记录的一条：`func_name=detail_api`，`key_id=HERB000281`。请求体还有文档里已写的 `v` 和 `label`：

```json
{"v": "HERB000281", "label": "Herb", "key_id": "HERB000281", "func_name": "detail_api"}
```

两个 txt 只读了开头 4096 字节。响应给出的是整文件的 `Content-Length`（状态码 200，不是 206）。表头在这一段里各有完整的一行。数据行没有保存。

## 逐条结果

结论分三档：环境失败、方法失败、跑通。超时、重置、TLS unexpected eof、证书主机名不匹配，算环境失败。404、空正文、门户、`Content-Type` 为 `text/html` 且正文不是可解析表，算方法失败。`Content-Type` 标成 `text/html`、但正文能解析成 JSON 的，算跑通，并写明类型标错。

| URL | 次数 | 分类 | 结论 |
|---|---|---|---|
| `http://herb.ac.cn/` | 2 | 方法失败：门户 | 第一次约 189 毫秒，HTTP 200，`Content-Type: text/html; charset=utf-8`，911 字节，标题 HERB，没有表格。第二次没有状态码，`RemoteDisconnected`，对端在没有 HTTP 响应时关掉连接。第一次已经看到无表格的 HTML，所以整条记为门户，不把第二次断线写成整条连不上 |
| `https://herb.ac.cn/` | 2 | 环境失败：TLS eof | 两次都没有状态码。第二次约 5.0 秒，`SSLEOFError`，`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否。握手没有完成 |
| `http://herb.ac.cn/v2` | 2 | 方法失败：门户 | 两次都是 HTTP 301，落到 `http://47.92.70.12/`，最终 HTTP 200。第一次约 249 毫秒。正文 978 字节，`text/html; charset=utf-8`，标题 HERB 2.0。可见文字只有标题，引用 `/static/umi.js`、`/static/umi.css` 和 `/static/HERB2.0.svg`，另有一段百度统计脚本，编号未记录。没有 `<table>`，也没有 JSON。早些时候的记录是这条没连上；这次连上了，落地页仍是壳 |
| `https://herb.ac.cn/v2` | 2 | 环境失败：TLS eof | 两次都没有状态码。第二次约 5.0 秒，`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否 |
| `http://47.92.70.12/` | 2 | 方法失败：门户 | 两次都是 HTTP 200，978 字节，标题 HERB 2.0，与 `/v2` 落地页是同一张 umi 壳。第一次约 64 毫秒。没有表格，没有成分或靶点行 |
| `https://47.92.70.12/` | 2 | 环境失败：TLS eof | 两次都没有状态码。第二次约 5.0 秒，`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否。没有因为校验失败而去掉主机名检查 |
| `POST http://47.92.70.12/chedi/api/` | 2 | 跑通 | 两次都是 HTTP 200，`Content-Type: text/html; charset=utf-8`，正文 49949 字节，可解析为 JSON。第一次约 123 毫秒。类型标成 HTML，正文是 JSON，所以不是「text/html 但不是可解析表」。字段见下一节 |
| `POST https://47.92.70.12/chedi/api/` | 2 | 环境失败：TLS eof | 两次都没有状态码。第二次约 5.0 秒，`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否。同一条详情只在 HTTP 上跑通 |
| `http://herb.netlab-aied.org/` | 2 | 环境失败：重置 | 两次都没有状态码。第二次约 335 毫秒，`RemoteDisconnected`：对端关掉连接且没有响应。九月快照里的这个主机，这次没有打开 |
| `https://herb.netlab-aied.org/` | 2 | 环境失败：TLS eof | 两次都没有状态码。第二次约 520 毫秒，`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否 |
| `http://47.92.70.12/static/download_data/V2/HERB_herb_info_v2.txt` | 1 | 跑通：只读到表头 | 约 62 毫秒，HTTP 200，`Content-Type: text/plain; charset=utf-8`。`Content-Length` 为 1864763。只保留了表头一行，文件没有下完，也没有写入仓库。列名见下一节 |
| `https://47.92.70.12/static/download_data/V2/HERB_herb_info_v2.txt` | 2 | 环境失败：TLS eof | 两次都没有状态码。第二次约 5.0 秒，`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否 |
| `http://47.92.70.12/static/download_data/V2/HERB_ingredient_info_v2.txt` | 1 | 跑通：只读到表头 | 约 59 毫秒，HTTP 200，`text/plain; charset=utf-8`。`Content-Length` 为 22572386。只保留表头，没有读数据行，没有下整库。表头里有 `MolWt`、`Drug_likeness`、`OB_score` |
| `https://47.92.70.12/static/download_data/V2/HERB_ingredient_info_v2.txt` | 2 | 环境失败：TLS eof | 两次都没有状态码。第二次约 5.0 秒，`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否 |
| `http://47.92.70.12/Detail/?v=HERB002540&label=Herb` | 2 | 方法失败：门户 | 两次都是 HTTP 200，978 字节，标题 HERB 2.0，与首页是同一张 umi 壳。第一次约 3.1 秒。查询串没有变成可解析的表。没有成分行，也没有靶点行 |
| `https://47.92.70.12/Detail/?v=HERB002540&label=Herb` | 2 | 环境失败：TLS eof | 两次都没有状态码。第二次约 5.0 秒，`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否 |

## 跑通的详情：字段与一条例子

跑通的数据接口只有 `POST http://47.92.70.12/chedi/api/`。例子只用 `HERB000281`。顶层键是：

`cmap2_data`、`herb_ingredient`、`formula_herb`、`Merge_diff_gene`、`herb_disease`、`summary`、`herb_target`、`drug_paper_target`、`meta_herb`、`drug_paper_disease`、`clinical_herb`。

`cmap2_data` 这次是字符串 `None.`。`meta_herb` 和 `clinical_herb` 各是一行空列表，没有列名，也没有数据。

### 摘要（一条）

`summary` 有表头加 1 行数据。列是：

`Herb ID`、`Herb pinyin name`、`Herb Chinese name`、`Herb alias name`、`Herb English name`、`Herb Latin name`、`Property`、`Meridian`、`Use part`、`Function`、`Indication`、`Toxicity`、`Clinical manifestations`、`Therapeutic class`、`Therapeutic class (Chinese)`、`SymMap`、`TCMID`、`TCMSP`、`TCM-ID`、`URL`。

这一行的短字段：

| 字段 | 值 |
|---|---|
| Herb ID | HERB000281 |
| Herb pinyin name | Bai Tou Weng |
| Herb Chinese name | 白头翁 |
| Herb alias name | 野丈人; 胡王使者; 白头公 |
| Property | Bitter; Cold |
| Meridian | Stomach; Large Intestine |
| Use part | Root |
| Toxicity | NA |
| Therapeutic class | Antipyretic Detoxicate Drugs |
| Therapeutic class (Chinese) | 清热解毒药 |
| TCMID | 6999 |
| TCMSP | 23 |

`TCMSP` 的 23 是摘要里的交叉引用，不是口服生物利用度。`Herb English name` 是字符串。`Herb Latin name` 是对象，键为 `style`、`title`。`SymMap` 和 `TCM-ID` 是对象，键为 `link`、`title`。`Function`、`Indication`、`Clinical manifestations` 是长文本，这里不整段抄。`URL` 以 `http://47.92.70.12/KnowledgeGraph/?query_node_label=Herb&query_node_name` 开头，全长 83。这个 KnowledgeGraph 地址这次没有再请求。

### 成分列表

`herb_ingredient` 是表：1 行表头，108 行数据。列是：

| 位置 | 字段 | 这一条里的形状 |
|---|---|---|
| 0 | Ingredient id | 对象，键只有 `link`、`title` |
| 1 | Ingredient name | 字符串 |
| 2 | Ingredient alias name | 字符串 |
| 3 | Molecular formula | 字符串 |
| 4 | Canonical smiles | 字符串 |

详情里的成分行没有分子量，也没有类药性，没有名为 `ob` 或 `dl` 的列。108 行没有逐行抄下，SMILES 也没有写入这份记录。

### 同一次响应里的其他表

`formula_herb`：表头加 14 行。列是 `Formula id`（对象，键 `link`、`title`）、`Formula pinyin name`、`Formula cn name`、`Formula en name`。方剂行没有逐条列出。

`herb_disease`：只有表头，没有数据行。列是 `Disease id`、`Disease name`、`Disease alias name`、`UMLS disease type`、`P value`、`FDR BH`。

`drug_paper_disease`：只有表头。列是 `Disease id`、`Disease name`、`UMLS disease type`、`Reference id`、`PubMed id`、`Paper title`。

`Merge_diff_gene` 的键有：`all_GO_csv#mouse`、`CMAP_CP_txt#mouse`、`up_kegg_csv#mouse`、`down_kegg_csv#human`、`diff_sig_gene#mouse`、`all_GO_csv#human`、`drug_experiment`、`down_GO_csv#mouse`、`all_kegg_csv#human`、`up_GO_csv#mouse`、`CMAP_PCL_txt#mouse`、`all_kegg_csv#mouse`、`down_GO_csv#human`、`CMAP_OE_txt#mouse`、`CMAP_KD_txt#mouse`、`CMAP_draw#human`、`down_kegg_csv#mouse`、`CMAP_KD_txt#human`、`CMAP_OE_txt#human`、`up_GO_csv#human`、`CMAP_draw#mouse`、`CMAP_CP_txt#human`、`diff_sig_gene#human`、`up_kegg_csv#human`、`CMAP_PCL_txt#human`。

除 `drug_experiment` 外，这些值都是不超过 80 个字符的字符串。抽到的内容是占位句：`None.`、`NONE.No enriched GO terms`、`NONE.No enriched KEGG terms`、`NONE.No significantly different genes`。`drug_experiment` 只有表头，没有数据行。列是 `EXP id`、`GSE id`、`Organism`、`Experiment type`、`Experiment detail`、`Control condition`、`Control samples`、`Experiment subject detail`、`Treatment samples`。`Experiment type` 是实验说明的列名；这一味没有实验行。

## 全库文件的表头

这两份是 HTTP 上跑通的表头，不是整库。

药材信息 `HERB_herb_info_v2.txt` 的列：

`Herb_id`、`Herb_pinyin_name`、`Herb_cn_name`、`Herb_alias_name`、`Herb_en_name`、`Herb_latin_name`、`Properties`、`Meridians`、`UsePart`、`Function`、`Indication`、`Toxicity`、`Clinical_manifestations`、`Therapeutic_en_class`、`Therapeutic_cn_class`、`SymMap_id`、`TCMID_id`、`TCMSP_id`、`TCM_ID_id`。

成分信息 `HERB_ingredient_info_v2.txt` 的列：

`Ingredient_id`、`Ingredient_name`、`Ingredient_alias_name`、`Molecular_formula`、`Canonical_smiles`、`Isomeric_smiles`、`InChI`、`InChIKey`、`MolWt`、`NumHAcceptors`、`NumHDonors`、`MolLogP`、`NumRotatableBonds`、`Drug_likeness`、`OB_score`、`CAS_id`、`SymMap_id`、`TCMID_id`、`TCMSP_id`、`TCM_ID_id`、`PubChem_id`、`DrugBank_id`、`NPASS_id`、`HIT_id`。

`MolWt` 是分子量。`Drug_likeness` 是类药性。`OB_score` 是 HERB 这张表自己的一列分数。这三列都不能填进 TCMSP 的 `ob`、`dl`，也不能拿去和 0.18 比较。这次没有读到任何一行数值。

## 靶点

`herb_target`：表头加 14 行。列是 `Target id`（对象，键 `link`、`title`）、`Gene symbol`、`Gene alias name`、`Protein name`、`Type of gene`、`P value`、`FDR BH`。这 14 行的 `Type of gene` 都是 `protein-coding`。第一条数据行的 `P value` 是 3.16895e-05，`FDR BH` 是 0.00592134。基因符号没有逐个写入。`Type of gene` 是基因类型。`P value` 和 `FDR BH` 是统计量。这里没有 source、recorded 或 predicted 列。

`drug_paper_target`：只有表头，这一味没有数据行。列是 `Target id`、`Gene symbol`、`Protein name`、`Reference`。`Reference` 是参考文献槽位，不是已记录和预测的开关。

这一条详情里，没有来源字段能把靶点分成已记录和预测。`detail_api` 不能当靶点证据工具。
