# HIT 2.0 与 ITCM 通路嗅探

探测时间是 2026-10-13 17:32–17:36（UTC+8）。单次超时 15 秒，每个 URL 最多两次。HTTPS 使用默认证书校验。没有下载整库压缩包，也没有把压缩包写入仓库。主机和页面只来自 `docs/DATA_SOURCES.md` 与 `docs/ROADMAP.md` 已写明的地址，再对各试 HTTP 与 HTTPS。

两个库都没有跑通：没有拿到可解析的药材或靶点查询结果。

## HIT 2.0

文档入口是 `http://hit2.badd-cao.net/`，框架指向 `http://www.badd-cao.net:2345/`。四条 URL 都没有药材行或靶点行。

| URL | 次数 | 分类 | 结论 |
|---|---|---|---|
| `http://hit2.badd-cao.net/` | 1 | 方法失败：框架页 | 3.29 秒，HTTP 200，`Content-Type` 为 `text/html;charset=utf-8`，正文 179 字节。唯一的 `frame` 指向 `http://www.badd-cao.net:2345`。没有标题、表单、锚点，也没有药材或靶点行。没有跑通 |
| `https://hit2.badd-cao.net/` | 2 | 环境失败：TLS eof | 两次都约 5.0 秒。`UNEXPECTED_EOF_WHILE_READING`，对端在协议中提前结束。没有状态码。没有跑通 |
| `http://www.badd-cao.net:2345/` | 2 | 环境失败：连接关闭 | 两次都约 5.0 秒。`Remote end closed connection without response`。没有状态码，没有正文。没有跑通 |
| `https://www.badd-cao.net:2345/` | 2 | 环境失败：TLS eof | 两次都约 5.0 秒。`UNEXPECTED_EOF_WHILE_READING`。没有状态码。没有跑通 |

## ITCM

文档入口是 `http://itcm.biotcm.net/`，页内写明 `search.html`、`download.html`。`http://www.itcm.biotcm.net/` 写在路线图里。下面每条都单独结论。检索页和下载页没有给出药材或靶点结果行，所以 ITCM 没有跑通。

| URL | 次数 | 分类 | 结论 |
|---|---|---|---|
| `http://itcm.biotcm.net/` | 2 | 第一次环境失败：连接关闭；第二次打开的是入口页 | 第一次约 5.0 秒，连接关闭，没有状态码。第二次 0.19 秒，HTTP 200，`text/html;charset=UTF-8`，正文 23469 字节，标题 ITCM。导航文字有 Home、SMEP (Small Molecular Expression Platform)、tcm-query、CMTP (TCM target )、Download、help。详情链接文字有 Baicalin、TIAN HUA FEN、tp53、periplocoside b、FIBROADENOMA OF THE BREAST，href 分别是 `/detail/profiledetail.html?nId=1`、`/detail/herb.html?nId=21`、`/detail/target.html?nId=11434`、`/detail/ingredient.html?nId=2`、`/detail/disease.html?nId=3522`。源码里有 `search.html` 字符串，抽出的锚点 href 没有这一条。本页没有查询表单，也没有结果行。这些详情路径这次没有单独请求。没有跑通 |
| `https://itcm.biotcm.net/` | 1 | 环境失败：证书主机名不匹配 | 0.22 秒。证书校验失败，证书不适用于 `itcm.biotcm.net`。没有状态码。没有跑通 |
| `http://www.itcm.biotcm.net/` | 2 | 环境失败：连接关闭 | 0.64 秒与 0.33 秒。两次都是对端关掉连接且没有响应。没有状态码。没有跑通 |
| `https://www.itcm.biotcm.net/` | 2 | 环境失败：TLS eof | 0.34 秒与 0.25 秒。两次都是 `UNEXPECTED_EOF_WHILE_READING`。没有状态码。没有跑通 |
| `http://itcm.biotcm.net/search.html` | 2 | 方法失败：框架页 | 两次都是 HTTP 200，`text/html; charset=utf-8`，正文 6677 字节，标题 ITCM。页内没有表单。唯一的查询容器是 `iframe`，src 为 `searchSon.html`。没有药材或靶点结果行。`searchSon.html` 不在文档已写明的页面链接里，这次没有请求。没有跑通 |
| `https://itcm.biotcm.net/search.html` | 1 | 环境失败：证书主机名不匹配 | 0.16 秒。证书不适用于 `itcm.biotcm.net`。没有状态码。没有跑通 |
| `http://itcm.biotcm.net/download.html` | 2 | 方法失败：下载页没有可解析查询 | 两次都是 HTTP 200，`text/html; charset=utf-8`，正文 12062 字节，标题 ITCM。没有查询表单，没有结果行。数据行的链接文字见下表。这些下载地址和 synapse 链接这次没有继续请求，压缩包没有拉进仓库。没有跑通 |
| `https://itcm.biotcm.net/download.html` | 1 | 环境失败：证书主机名不匹配 | 0.13 秒。证书不适用于 `itcm.biotcm.net`。没有状态码。没有跑通 |

`http://itcm.biotcm.net/download.html` 上的链接文字：

| 分区 | 行上的类型 | 链接文字 | href |
|---|---|---|---|
| General Data | Formulas | Download | `/downDetail/formula` |
| General Data | Herbs | Download | `/downDetail/herb` |
| General Data | Ingredients | Download | `/downDetail/ingredient` |
| General Data | Targets | Download | `/downDetail/target` |
| General Data | Diseases | Download | `/downDetail/disease` |
| Manual Curation | Manual Curation of Formula | Download | `/downloaCuration/3` |
| Manual Curation | Manual Curation of Herb, Ingredient and Target | Download | `/downloaCuration/4` |
| Manual Curation | 6878 Global Approved Small Molecules and Their Category, Properties Prediction | Download | `/downloaCuration/1` |
| Manual Curation | Drugbank Drug and Its Targets | Download | `/downloaCuration/2` |
| Manual Curation | Network Relationship | Download | `/downloaCuration/5` |
| High-throughput Data | MCF-7 / 496 TCM small molecules（SMEP） | synapse | `https://www.synapse.org/#!Synapse:syn25843090/files/` |

Manual Curation 一节写明，手工收集的关系在库里的来源显示为 ITCM，具体关系和参考文献放在上述下载里。这仍是下载说明，不是一条可解析的查询。
