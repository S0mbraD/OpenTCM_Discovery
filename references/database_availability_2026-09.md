# 数据库可用性实测报告（2026-09-22 深度验证版）

> 探测方式：2026-09-14 并行 subagent 基线 + 2026-09-21/22 两轮扩展探测（42 端点）
> + 深度业务 API 验证（recovered_deep_test 系列，非仅首页存活）。
> 快照日期：2026-09-22（北京时间）。可用性随时间漂移，MCP 工具 `tcm_check_database_status(refresh=True)` 可重探关键端点。

## 总览

**36 库覆盖：28 可用 / 5 受限（partial）/ 2 不可用（down）/ 1 需 Key**。
本次更新将 ChEMBL、Ensembl REST 从 down 提升为 up；新增 MONDO/OLS、BindingDB、MSigDB、SwissTargetPrediction。

## 可用（28）

### 中药/天然产物库（7）

| 数据库 | 状态 | 探测要点 |
|---|---|---|
| HERB 2.0 | up | SPA 前端存活；数据走 XHR detail API（`herb.netlab-aied.org`） |
| SymMap | up | nginx 正常，无需 Key |
| TCMBank | up | **仅 http**（https 握手失败）；JSON API 以 berberine 实测验证 |
| NPASS | up | 2026-09 恢复（此前 404）；搜索端点为 server-rendered HTML 表格，需正则解析 |
| PubChem | up | PUG-REST 稳定；保持 1s 限流 + 4 次重试 + 5xx 退避 5s×attempt |
| Cactus | up | 恢复可用（此前标记弃用）；高延迟约 8s |
| ChEMBL | up | **2026-09-22 恢复**（此前持续 500）：molecule / search / mechanism 三端点全通（1-2s），机制接口直接给出化合物→靶点（action_type、mechanism_of_action），可用 `tcm_chembl_lookup` 调用。注意：部分化合物（如 berberine）在 ChEMBL 无机制记录，属数据本身缺失 |
| MONDO/OLS | up | **2026-09-22 新增**：`ebi.ac.uk/ols/api` 搜索疾病名→MONDO ID，UC 命中 MONDO_0005101（~1.7s），可作 Open Targets disease 检索的独立备用 |
| BindingDB | up | **2026-09-22 新增**：新入口 `/rwd/bind/index.jsp` 活（旧 `/bind/` 404，站点已改版） |
| SwissTargetPrediction | partial | **2026-09-22 新增**：HTTPS 页面 + 表单 POST `/predict.php` 返回 200；异步结果轮询适配待跟进（SMILES 反靶点备用） |

### 靶点/疾病/药物库（8）

| 数据库 | 状态 | 探测要点 |
|---|---|---|
| Open Targets | up | GraphQL v4；**2026-09-21 修复 ensembl 模式**：`name`/`knownDrugs` 字段已被 schema 移除（400），改用 `approvedSymbol`/`approvedName`/`biotype`/`associatedDiseases`（introspection 验证） |
| CTD | up | web + bulk 下载正常；**batchQuery 偶发被 ALTCHA 拦截（302/HTML）**，MCP 工具已加检测并提示改用 DGIdb/ChEMBL；批量用月度 .tsv.gz |
| DGIdb | up | 必须用新端点 `dgidb.org/api/graphql`（old.dgidb.org 已死） |
| UniProt | up | REST 约 4s 偏慢但稳定；SPARQL 可作 DrugBank 替代 |
| KEGG | up | REST 稳定；compound ID 大小写敏感；名称检索用路径式 `find/compound/{name}` |
| STRING v12 | up | `version-12-0.string-db.org`；identifiers 用 `\r` 分隔；score 0-1 |
| Ensembl REST | up | **2026-09-22 恢复**（此前 3x 超时 0 字节）：lookup/symbol ~5.4s、xrefs/id ~18s（偶发超时需重试，`tcm_ensembl_lookup` 已内置重试）；sequence/id 对过时转录本返回 400 属正常 |
| MSigDB | up | **2026-09-22 新增**：index 1.2s 可达（基因集下载需登录，富集分析优先 g:Profiler） |

### 生信分析/结构库（11）

| 数据库 | 状态 | 探测要点 |
|---|---|---|
| NCBI EUtils | up | 全场最快（约 0.7s） |
| g:Profiler | up | gost API POST 正常 |
| Enrichr | up | 正常 |
| Reactome | up | 去掉 URL 中 .json 后缀；用内部稳定 ID |
| PDB (RCSB) | up | data API 正常 |
| AlphaFold DB | up | prediction API 正常 |
| InterPro | up | 正常 |
| JASPAR | up | 慢（约 7s） |
| dbSNP | up | 正常 |
| GWAS Catalog | up | 正常 |
| ClinVar | up | 正常 |

## 受限（3）与不可用（2）与需 Key（1）

| 数据库 | 状态 | 详情 |
|---|---|---|
| TCMSP | down | tcmspw.com 502；old.tcmsp-e.com SSL 失败（双通道全死）。历史 OB/DL 数据用本地缓存 |
| ENCODE | down | portal 502 |
| ETCM 2.0 | partial | **2026-09-22 实测**：主站 index 0.7s 活，但 `/ETCM/api/` 仍 404（API 未恢复，网页可浏览） |
| DCABM-TCM | partial | **2026-09-22 实测**：SPA index 0.3s 活，页面内嵌 API key（`var key=3aedf5c0...`），但 API 路径在 chunk JS 内未定位（价值中等，HERB/TCMBank 已覆盖） |
| DrugBank | partial | **2026-09-22 实测**：`go.drugbank.com/releases/latest` 200（~42s，慢但可达）；`www.drugbank.com` 仍 Cloudflare 403；open data 包可下载 |
| DisGeNET | key_required | 2026-09-22 复测：公开端点返回 SPA HTML（非 JSON），需免费注册 API key 才能取 JSON；**key_required 结论不变** |
| gnomAD | partial | REST 2.1 失效（400）；GraphQL 实测返回 RATELIMIT（限速 1.9s 响应），需限速重试 |

## 死路确认（放弃维护）

- **TCMSP 双通道**：tcmspw.com 502 + old.tcmsp-e.com SSL 失败
- **ETCM API**：404（网页存活）
- **ENCODE**：502
- **DCABM API**：SPA 内嵌 key，路径未定位
- **OPSIN / LOTUS / COCONUT / ChemSpider / ChEBI REST / WikiPathways / Reactome ContentService**：NOTFOUND（路径或服务变更）
- **JASPAR SSL**、**TTD SSL**、**TCMID/STITCH TIMEOUT**：仍不可达

## 调用纪律（从既往通道实战沉淀）

1. **PubChem PUG-REST**：1s 最小间隔 + 4 次重试 + 500/502/503 退避 5s×attempt + 连续 10 次网络失败熔断保存退出（404 属业务响应，不熔断）
2. **PubChem 带电离子**：`CanonicalSMILES` 对阳离子化合物（如 berberine C20H18NO4+）返回空，需回退 `SMILES`/`ConnectivitySMILES`
3. **CAS 校验**：从 synonyms 提取后必须做校验位验证
4. **假 CID**：超长数字（>1.6 亿）为脏数据，直接过滤
5. **TCMBank**：仅 http 协议
6. **KEGG**：ID 大写；名称检索用 `find/compound/{name}`
7. **STRING**：identifiers 用 `\r` 分隔；score 0-1；medium confidence 0.4
8. **外网超时熔断**：批量任务单库连续失败即停，换通道
9. **Open Targets**：ensembl 模式必须用 introspection 后字段（approvedSymbol/approvedName/biotype/associatedDiseases/dbXrefs）；`name`/`knownDrugs` 已从 schema 移除
10. **CTD**：batchQuery 可能被 ALTCHA 拦截，自动改用 DGIdb/ChEMBL，或 bulk .tsv.gz
11. **ChEMBL**：按名称检索取第一个 hit，无机制时回退探测前 5 个 hit；`molecule_structures` 字段存 SMILES/InChIKey（非 `molecule_properties`）
12. **Ensembl**：symbol→gene lookup 优先；xrefs 慢（~18s）需长超时；sequence 需有效转录本 ID

## MCP 工具与数据库映射（17 工具）

| MCP 工具 | 数据库 |
|---|---|
| tcm_pubchem_lookup | PubChem |
| tcm_kegg_compound | KEGG |
| tcm_search_disease_targets | Open Targets（含 MONDO 备用） |
| tcm_string_ppi | STRING v12 |
| tcm_tcmbank_search | TCMBank |
| tcm_ctd_gene_interactions | CTD（ALTCHA 拦截提示） |
| tcm_get_gene_info | UniProt |
| tcm_ncbi_gene_search | NCBI EUtils |
| tcm_enrichment_analysis | g:Profiler |
| tcm_npass_search_compound | NPASS |
| tcm_get_structure | PDB / AlphaFold |
| tcm_check_database_status | 本快照 + 实时重探 |
| tcm_audit_evidence | 本地 evidence_audit.py |
| **tcm_chembl_lookup** | **ChEMBL（新）**：化合物→机制靶点 |
| **tcm_ensembl_lookup** | **Ensembl REST（新）**：基因符号规范化 |
