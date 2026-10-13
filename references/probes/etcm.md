# ETCM / ETCM2 通路嗅探

探测时间是 2026-10-13 17:38（UTC+8）。单次超时 15 秒，每个 URL 最多两次。证书校验保持开启。没有下载整库，也没有把响应正文写入仓库。

`references/runtime_lessons.md` 没有已确认条目，没有一条旧观察能对上这次失败。

进程环境里没有 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY` 及对应的小写变量，因此未使用环境代理。

## 文档里的主机

只使用 `docs/DATA_SOURCES.md` 已写明的主机，没有另编域名。文档没有另写备用域名。`:18124` 是同一主机上的详情端口。

| 主机 | 文档里的路径 |
|---|---|
| `www.tcmip.cn` | `/ETCM2/front/`、`/ETCM/api/` |
| `www.tcmip.cn:18124` | `/home/detail/`、`/home/herb_chem_phy/` |

下面把主机根路径也各试了 HTTP 与 HTTPS。黄连详情是文档里已经记过的药材请求。它是 TCMIP 的黄连页，这次不把它当成 ETCM 的靶点接口。

## 逐条结果

### `http://www.tcmip.cn/`

结论：**方法失败**。没有表格的门户。

一次即返回，约 0.31 秒。两次 302 之后落到 `http://www.tcmip.cn/TCMIP/index.php/Home/Login/login.html`，HTTP 200，`Content-Type: text/html; charset=utf-8`，正文 25058 字节。标题是 Login。没有 `<table>`。页头描述文字提到 ETCM，页面本身是登录门户。没有状态码以外的 TLS 错误。证书主机名：无，这是 HTTP。没有跑通。

### `https://www.tcmip.cn/`

结论：**环境失败**。TLS unexpected eof。

两次都约 5.0 秒，没有状态码。`SSLEOFError`，`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否。握手没有完成，没有取到证书主机名。

### `http://www.tcmip.cn/ETCM2/front/`

结论：**方法失败**。没有表格的门户。

一次即返回，约 0.07 秒。HTTP 200，最终 URL 同左，`Content-Type: text/html; charset=UTF-8`，正文 1495 字节。标题是 ETCM2.0。没有 `<table>`，没有 JSON，没有药材或靶点行。证书主机名：无，这是 HTTP。没有可解析查询。

### `https://www.tcmip.cn/ETCM2/front/`

结论：**环境失败**。TLS unexpected eof。

两次都约 5.0 秒，没有状态码。`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否。没有取到证书主机名。

### `http://www.tcmip.cn/ETCM/api/`

结论：**方法失败**。404。

一次即返回，约 1.08 秒。HTTP 404，`Content-Type: text/html; charset=iso-8859-1`，正文 196 字节。标题是 404 Not Found。没有表格，没有 JSON。证书主机名：无，这是 HTTP。

### `https://www.tcmip.cn/ETCM/api/`

结论：**环境失败**。TLS unexpected eof。

两次都约 5.0 秒，没有状态码。`UNEXPECTED_EOF_WHILE_READING`。证书主机名不匹配：否。没有取到证书主机名。

### `http://www.tcmip.cn:18124/`

结论：**方法失败**。404。

一次即返回，约 3.07 秒。HTTP 404，`Content-Type: text/html`，正文 77 字节。可见文字是 Not Found。没有表格，没有 JSON。证书主机名：无，这是 HTTP。

### `https://www.tcmip.cn:18124/`

结论：**环境失败**。

1. 第一次约 5.0 秒，TLS unexpected eof，`UNEXPECTED_EOF_WHILE_READING`，没有状态码。
2. 第二次约 15.0 秒，握手超时，没有状态码。

两次都没有取到证书主机名。证书主机名不匹配：否。

### `http://www.tcmip.cn:18124/home/detail/?id=黄连&type=herb`

结论：**有 JSON，但这是 TCMIP 的黄连药材页，不是 ETCM 的靶点接口。** 没有能分开已记录与预测的列。

两次都是 HTTP 200，`Content-Type: application/json`。第一次约 3.79 秒，正文约 238 KB，只用于看字段，没有写入仓库。最终 URL 是同一路径，`id` 为百分号编码。`code` 为 1，`msg` 为 success!!，`name` 为 HuangLian，`page_title` 为 Herb HuangLian Details page。

第二次约 4.2 秒，只补了节名，并解开一行格子。11 个节的 id 是 `base_information`、`related_table`、`habitat`、`Quantitative`、`ingredient_target`、`component_target`、`component_chem_phy`、`relation_heatmap`、`Enrichment`、`Correlation`、`basic_network`。`ingredient_target` 的节名是 Component Target。这次没有从该节取出一行，所以不能把这一节写成已经跑通的靶点查询。

`related_table` 里有一块标签为 Targets、`type` 为 `target`、`count` 为 122，响应里带出 20 行。只留一行。字段名：`Target Name`、`UniProt Accession Number`、`Gene Symbol`、`Inhibitors`。

一行例子：`5-hydroxytryptamine receptor 2B`，`P41595`，`HTR2B`，`Inhibitors` 为 0。

这些字段里没有已记录、预测或同类分列。同页成分块的字段是 `Ingredient Name`、`Molecular Formula`、`Molecular Weight`、`Quantitative Estimate of Drug-likeness（QED）`、`FDA Maximum Daily Dose (FDAMDD)`、`Standards`。成分名、分子量和 QED 不能填进 TCMSP 的 `ob`、`dl`，这一块也不是靶点表。

证书主机名：无，这是 HTTP。TLS unexpected eof：否。

### `https://www.tcmip.cn:18124/home/detail/?id=黄连&type=herb`

结论：**环境失败**。超时。

两次都约 15.0 秒。握手超时，没有状态码。TLS unexpected eof：否。证书主机名不匹配：否。没有取到证书主机名。

### `POST http://www.tcmip.cn:18124/home/herb_chem_phy/`

结论：**有 JSON，但不能当靶点工具。**

请求体沿用文档已写的 `{"id":"黄连","type":"herb","Component_Phy_Chem":["Molecular Weight"]}`。一次即返回，约 0.09 秒。HTTP 200，`Content-Type: application/json`，正文 2818 字节。`code` 为 1，`msg` 为 success。字段名：`div`、`script`、`chart_type`。`div` 有 53 个成分名，只留一行。

一行例子：`div` 为 TACROLIMUS MONOHYDRATE，`script` 为 821.490，`chart_type` 为 histogram。

`div` 是成分名，`script` 是这次请求的分子量。这不是 TCMSP 的 `ob`、`dl`，也不是靶点表。证书主机名：无，这是 HTTP。

### `POST https://www.tcmip.cn:18124/home/herb_chem_phy/`

结论：**环境失败**。

同一请求体。第一次约 15.0 秒，握手超时。第二次约 5.0 秒，TLS unexpected eof，`UNEXPECTED_EOF_WHILE_READING`。两次都没有状态码，没有取到证书主机名。证书主机名不匹配：否。

## 靶点查询

没有跑通能分出已记录与预测的靶点查询。

文档里能原样重放的，是上面的前端、`/ETCM/api/`、黄连详情和 `herb_chem_phy`。黄连详情是 TCMIP 药材页；页内 Targets 一行没有已记录与预测的分列。`herb_chem_phy` 只有成分名和分子量。HTTPS 都停在握手，没有另给出查询参数。
