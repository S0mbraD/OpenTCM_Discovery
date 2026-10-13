# BATMAN-TCM 通路嗅探

探测开始于 2026-10-13 17:36:04 +08。证书校验保持开启。单次超时 15 秒，每个 URL 最多两次。进程没有 `HTTP_PROXY`、`HTTPS_PROXY` 或 `ALL_PROXY`，因此未使用环境代理。没有关闭证书校验，没有写入代理地址，没有下载全库，也没有进入需要登录的页面。

`references/runtime_lessons.md` 没有已确认条目，没有一条旧观察能对上这次失败。

2026-10-13 早些时候记在 `docs/DATA_SOURCES.md` 里的结果是：首页 20 秒超时，`index.php` 对端关掉连接，HTTPS 握手失败。那三条都没有 HTTP 状态码，属于环境失败。这次再测，用来区分这种连不上和方法本身不存在。

## 已知主机

只使用仓库文档和论文里已经写出的地址，没有另编域名。

| 主机 | 出处 | 这次实际请求的路径 |
|---|---|---|
| `bionet.ncpsb.org.cn` | `docs/DATA_SOURCES.md`、`references/tcm_chem_db.md`，以及 BATMAN-TCM 2.0 开放获取全文（doi:10.1093/nar/gkad926，PMC10767940） | `/batman-tcm/`、`/batman-tcm/index.php` |
| `batman2.cloudna.cn` | `references/tcm_chem_db.md` 记录的 `window.downloadApi` | `/downloadApi` |
| `111.198.139.89` | 同一份文档记录的 `window.spaceIPRecords` | `/spaceIPRecords` |

Europe PMC 上该文全文（HTTP 200，约 125 KB）里的数据库 URL 只有 `http://bionet.ncpsb.org.cn/batman-tcm/`。其余链接是 ORCID、许可证和期刊页面，不是 BATMAN 主机。文中 API 一节只说可以用请求类型、输出格式和输入项拼 URL，没有给出可粘贴的地址或参数表。这次没有编查询参数。

这次 HTTP 首页返回的 871 字节里没有 `window.downloadApi`，也没有新的绝对地址。上面两个备用地址来自仓库里已有的记录，不是这次页面新给出来的。

## 逐条结果

结论只取三档：环境失败、方法失败、跑通。超时、对端关掉连接、TLS unexpected eof、证书主机名不匹配，算环境失败。HTTP 404、没有表格的 HTML 门户，算方法失败。没有任何一条返回可解析的药材、成分或靶点。

### `https://bionet.ncpsb.org.cn/batman-tcm/`

结论：**环境失败**。

两次都是 `URLError` / `SSLEOFError`，没有状态码。耗时约 5.2 秒和 5.0 秒。TLS unexpected eof：是。证书主机名不匹配：否。异常文本是 `SSL: UNEXPECTED_EOF_WHILE_READING`。连接没有完成，谈不上门户或 JSON。

### `http://bionet.ncpsb.org.cn/batman-tcm/`

结论：**方法失败**。

1. 第一次约 5.0 秒，`RemoteDisconnected`：对端在没有 HTTP 响应时关掉连接。这一次本身是环境失败。
2. 第二次约 59 毫秒，HTTP 200，`Content-Type: text/html`，`Content-Length: 871`，服务器 `nginx/1.15.6`。标题是 BATMAN。正文是要求启用 JavaScript 的空壳（batman-tcm2），没有 `<table>`，没有 JSON，没有登录表单，也没有药材、成分或靶点字段。TLS unexpected eof：否。证书主机名不匹配：否。

第二次已经返回没有表格的门户，所以这条 URL 记为方法失败，不把第一次的断线当成整条通路仍然连不上。

### `https://bionet.ncpsb.org.cn/batman-tcm/index.php`

结论：**环境失败**。

两次都是 `SSLEOFError`，没有状态码。耗时约 5.0 秒和 5.0 秒。TLS unexpected eof：是。证书主机名不匹配：否。没有 HTTP 状态，不能写成 404 或方法不存在。

### `http://bionet.ncpsb.org.cn/batman-tcm/index.php`

结论：**环境失败**。

两次都是 `TimeoutError`，各约 15.0 秒，没有状态码。TLS unexpected eof：否。证书主机名不匹配：否。这是超时，不是方法不存在。

### `https://batman2.cloudna.cn/downloadApi`

结论：**环境失败**。

两次都是 `SSLCertVerificationError`。耗时约 251 毫秒和 64 毫秒。证书主机名不匹配：是。证书对 `batman2.cloudna.cn` 无效。TLS unexpected eof：否。没有关闭校验，因此没有读取正文，也没有状态码。

### `http://batman2.cloudna.cn/downloadApi`

结论：**方法失败**。

一次即返回，约 55 毫秒，HTTP 200，`Content-Type: text/html`，871 字节，服务器 `nginx`。标题是 BATMAN，同样是没有表格的 JavaScript 空壳，不是 JSON，也不是成分或靶点表。TLS unexpected eof：否。证书主机名不匹配：否。

### `https://111.198.139.89/spaceIPRecords`

结论：**环境失败**。

两次都是 `SSLEOFError`，没有状态码。耗时约 5.0 秒和 5.0 秒。TLS unexpected eof：是。证书主机名不匹配：否。握手没有完成。

### `http://111.198.139.89/spaceIPRecords`

结论：**方法失败**。

一次即返回，约 3.1 秒，HTTP 404，`Content-Type: text/html`，319 字节。可见文字是 “Sorry, Page Not Found”。没有表格，没有 JSON。TLS unexpected eof：否。证书主机名不匹配：否。这是路径不存在，不是连不上。

## 可复现查询

没有跑通。八条 URL 里没有任何一条给出可解析的药材、成分或靶点，因此没有字段名，也没有一行截断例子。没有可复现的查询。

能完成 HTTP、但不是数据接口的，只有这三条：首页和 `downloadApi` 是无表格的 HTML 门户，`spaceIPRecords` 是 404。`index.php` 的 HTTP 与全部 HTTPS 都停在环境失败，不能改判成方法不存在。
