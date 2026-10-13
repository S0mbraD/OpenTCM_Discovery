# TCMSP 备用主机与靶点页

探测时间是 2026-10-13 17:44:49 至 17:46:58（UTC+8）。分子页、门户链接和一条靶点链接在这次主探测之后又看过，仍是同一天。单次超时 15 秒，每个 URL 最多两次。证书校验保持打开，请求不跟随跳转，以便把 HTTP 和 HTTPS 分开。记录里没有代理地址，也没有 token。当时进程环境里没有 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`。

这里只记录这一次请求的结果。ob 和 dl 只属于旧站成分表这一页上的字段。没有下载整库，成分表只留一行例子。

## 现有工具

`tcm_core/tcmsp.py` 和 `mcp_server/tcmsp_layer.py` 只请求 `https://old.tcmsp-e.com/tcmsp.php`，以及同主机上 `tcmspsearch.php` 的药材表（`qs=herb_all_name`）和成分表（`qsr=herb_en_name`）。`docs/DATA_SOURCES.md` 写的也是这两张表。客户端不请求靶点页。

## 主机结论

| 主机 | HTTPS | HTTP | 结论 |
|---|---|---|---|
| `old.tcmsp-e.com` | 200。入口有 token，甘草的药材表和成分表都返回了 | 301，Location 为 `https://old.tcmsp-e.com` | 跑通 |
| `www.tcmsp-e.com` | 200。门户页，不是成分表 | 301 到同路径的 HTTPS | 方法失败 |
| `tcmsp-e.com` | 200。与 www 同一张门户页 | 301 到同路径的 HTTPS | 方法失败 |
| `tcmspw.com` | 无状态码。握手时连接被提前结束，两次都一样 | 无状态码。对端没有返回响应就断开，两次都一样 | 环境失败 |
| `next.tcmsp-e.com` | 200。前端空壳，没有成分表或靶点表 | 301 到 `https://next.tcmsp-e.com/` | 方法失败 |

没有找到可以抄出靶点名称的靶点表。甘草成分页的数据行仍然没有靶点字段。

## old.tcmsp-e.com

HTTPS 首页 200，约 20 KB，标题是计算系统生物学实验室的旧站，这一页没有 ob、dl 表。`/tcmsp.php` 200，隐藏字段里的 token 能被现有客户端的正则取到。

用这个 token 查询甘草：药材表 1 行，`herb_cn_name` 甘草，`herb_en_name` licorice，`herb_pinyin` Gancao。再用 licorice 取成分表：200，约 1.7 MB，280 行。数据行的键是 `molecule_ID`、`MOL_ID`、`file_ID`、`molecule_name`、`ob`、`mw`、`alogp`、`caco2`、`bbb`、`halflife`、`hdon`、`hacc`、`dl`、`FASA`。其中没有靶点字段。

只留第一行：

| molecule_name | MOL_ID | molecule_ID | ob | dl |
|---|---|---|---|---|
| protocatechuic acid | MOL000105 | 105 | 25.3664679574 | 0.035092 |

这两个数只描述这一页这一行，不是疗效，也不是靶点。

HTTP 的 `/` 和 `/tcmsp.php` 都是 301，Location 指向 `https://old.tcmsp-e.com`。这是 HTTP 有响应，不是握手失败。

## www.tcmsp-e.com 和 tcmsp-e.com

`https://www.tcmsp-e.com/`、`/tcmsp.php`、`/tcmspsearch.php`，以及 `https://tcmsp-e.com/` 和 `https://tcmsp-e.com/browse.php?qc=herbs`，都是 200、约 36 KB 的同一张门户页。没有 token，也没有药材表或成分表。

页面上的链接是 `https://old.tcmsp-e.com` 和 `https://next.tcmsp-e.com`。正文里把一组功能标成 No，其中有 `Target Prediction No`，旁边还有 ADMET、分子对接和文献核验。这是介绍文字，不是靶点表。

对应的 HTTP 地址都返回 301，跳到同路径的 HTTPS。`http://www.tcmsp-e.com/tcmsp.php` 这次没有再出现上次记录的握手失败。

## tcmspw.com

`https://tcmspw.com/`、`/tcmsp.php`、`/tcmspsearch.php` 各试了两次。两次都是 `SSLEOFError: [SSL: UNEXPECTED_EOF_WHILE_READING]`，大约 10 秒，没有 HTTP 状态码。证书校验没有关闭；失败发生在握手读取被提前结束。

`http://tcmspw.com/`、`/tcmsp.php`、`/tcmspsearch.php` 也各试了两次。两次都是 `RemoteProtocolError: Server disconnected without sending a response.`，大约 10 秒。

同一次探测里，old、www 和 next 的 HTTPS 都能完成握手。tcmspw 两次都停在握手或连接被关掉，所以它仍是环境失败，不是路径写错。这次不能由此断定原因是 VPN：本机没有代理环境变量，而且其他主机的 TLS 是通的。

## next.tcmsp-e.com

HTTPS 首页 200，1289 字节，标题 `tcmsp`。正文是一个空的前端挂载点，并引用页面上的脚本和样式。HTML 里没有查询参数，没有成分表，也没有靶点表。HTTP 是 301 到 HTTPS 首页。主机有响应，但现有的旧站查表方法在这里没有表可取。

## 靶点页

成分页和分子页的链接里已经出现这些地址，文档 `references/tcm_chem_db.md` 里也有 `molecule.php?qn=` 这种分子详情。没有另猜别的接口。

成分页脚本里有列名 `target_name`，也有未替换的链接 `target.php?qt=${target_ID}`，文字是 `${target_name}`。280 行数据里没有这些键。用字面量 `${target_ID}` 请求 `https://old.tcmsp-e.com/target.php?qt=${target_ID}` 得到 200，页面写着 `"Target" not exist`，没有表格。

`molecule.php?qn=${molecule_ID}` 出现在成分页模板里。用上面那一行的 `molecule_ID` 打开 `https://old.tcmsp-e.com/molecule.php?qn=105`：200，约 33 KB。页面有 “Related targets” 的占位，Kendo 的 `data` 写成变量名，不是内嵌 JSON。源码里能看到 13 个数字形式的 `target_ID`，这次没有从中解析出靶点名称。

从这 13 个编号里取页面上出现过的 `1172`，打开 `https://old.tcmsp-e.com/target.php?qt=1172`。第一次是和 tcmspw 同一类的握手中断；第二次 200，约 86 KB。内嵌列表的开头一行是 `molecule_ID` 41、`molecule_name` PHA、`SVM_score` 0.90533、`RF_score` 0.7。同一列表里也有 `molecule_ID` 105。这是靶点页上的分子行，字段不是成分页的 ob、dl，可见标题区也没有靶点名称。没有把这张分子列表存下来。

因此：成分页仍然没有靶点。沿页面已经写出的靶点链接，没有得到一张带靶点名称的靶点表。
