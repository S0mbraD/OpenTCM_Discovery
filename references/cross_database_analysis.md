# 交叉分析与数据循环工具套件（2026-10-12 新增，MCP 五工具）

> 目标：多数据库交叉分析筛选 + 网络 + 数据循环利用。所有工具为 tcm-database-query MCP 服务器的组成部分（`mcp_server/open_tcm_discovery_mcp.py`），遵守实时检索纪律（无预置答案，输出带 source/queried_at）。
> 参数传递约定：与其余工具一致，客户端用 `{"params": {...}}` 嵌套。

## 一、五工具速览

| 工具 | 一句话 | 输入 | 输出核心 |
|---|---|---|---|
| `tcm_cross_database_screen` | 多库证据合并筛选 | mode=compound（名称列表）或 gene（符号列表） | 每实体跨库记录 + 共识分（compound 0-3 / gene 0-4） |
| `tcm_network_analysis` | STRING PPI 网络 + hub 排名 | 基因列表（2-200） | 度/中介/紧密中心性、k-core、连通分量、hub 表 |
| `tcm_multi_enrichment` | 双引擎富集对比 | 基因列表（3-300） | 共识通路（双引擎同项）+ 各引擎 FDR 表 |
| `tcm_wikidata_lookup` | 跨库 ID 解析 | search（名称）或 resolve（QID） | QID 命中 / 标识符矩阵（CAS/InChIKey/CID/ChEMBL/KEGG/DrugBank/UniProt/PDB） |
| `tcm_uniprot_idmap` | UniProt ID 映射 | ids + from_db + to_db | from→to 映射表（异步 submit+poll） |

## 二、各工具要点与陷阱

### tcm_cross_database_screen
- **compound 模式**（PubChem + ChEMBL + SymMap）：PubChem 取 CID/分子式/SMILES/InChIKey（1s 限流）；ChEMBL 用 search.json 过滤 doc_type 含 COMPOUND；SymMap 用 /search/ Mol。共识分 = 独立命中库数。
- **gene 模式**（STRING + Open Targets + SymMap + Pharos）：STRING interaction_partners 取互作伙伴；OT GraphQL target 搜索（返回 Ensembl ID）；SymMap Gene 搜索；Pharos `target(q:{sym:...})` 返回 **TDL 开发级别**（Tclin/Tchem/Tdark）——药物开发价值分层的独有证据。
- 通道间错误互不影响（单库失败只记 error 不中断）。

### tcm_network_analysis
- STRING network 端点，identifiers 用 `\r` 分隔（httpx params 自动编码）；score 阈值默认 0.4（medium）。
- 中介中心性用 Brandes 算法（O(VE)，≤300 节点才计算，超过置 0 防超时）。
- hub 综合排名 = 度排名 + 中介排名 + 紧密排名之和（rank-sum，越小越靠前）。
- 陷阱：STRING 会把输入名标准化（preferredName）；未入网的基因度数为 0 仍出现在结果里。

### tcm_multi_enrichment
- g:Profiler gost：POST biit.cs.ut.ee（organism=hsapiens，sources=[KEGG]），**本地计算 BH-FDR**（gost 响应无 fdr 字段时兜底）。
- SymMap /related_enrichment/：行格式为位置数组 [pathway_id, name, odd_ratio, p, fdr, n_genes, genes]。
- 共识匹配：通路名分词 Jaccard ≥ 0.34 视为同项；双引擎 FDR 均 < 0.05 标记 "significant both"（最强证据）。

### tcm_wikidata_lookup
- resolve 的 QID 必须形如 `Q\d+`；先 search 拿 QID。
- 标识符属性表：P231 CAS / P235 InChIKey / P234 InChI / P662 PubChem CID / P233 SMILES / P592 ChEMBL / P665 KEGG / P715 DrugBank / P352 UniProt / P351 NCBI Gene / P638 PDB。
- **通道波动**（2026-10-12 实测当晚多次连接重置）：内置 3 次重试+退避；仍失败会返回明确错误（历史可靠，隔时段重试）。名称查询注入防护：双引号替换为单引号。
- VALUES 批量 ≤ 200（历史实测约束）。

### tcm_uniprot_idmap
- 流程：POST `/idmapping/run`（from/to/ids）→ 轮询 `/idmapping/status/{jobId}`（3s 间隔，默认上限 30s）。
- 常用 from/to 值：UniProtKB_AC-ID、ChEMBL、Ensembl、Ensembl_Gene、HGNC、PDB、RefSeq_Prot、KEGG、UniParc。
- 实测锚点：P04637→CHEMBL4096、P24941→CHEMBL301（2026-10-12 live）。

## 三、数据循环闭环（推荐工作流）

```
[检索] tcm_search_disease_targets(疾病) —— Open Targets 关联靶点
   ↓
[网络] tcm_network_analysis(靶点列表) —— hub 排名（度+中介+紧密）
   ↓
[富集] tcm_multi_enrichment(hub 前 20) —— 双引擎通路共识
   ↓
[筛选] tcm_cross_database_screen(mode=gene, hubs) —— 四库证据 + TDL
   或   tcm_cross_database_screen(mode=compound, 候选成分) —— 三库共识
   ↓
[解析] tcm_wikidata_lookup / tcm_uniprot_idmap —— 跨库 ID 互转
   ↓
[对接] structure-docking-analysis MCP：dock_fetch_structure → dock_vina_docking
   → dock_analyze_contacts —— 预测关键残基
   ↓
[回证] 预测残基回查 UniProt/文献（本 skill 检索通道）→ 假说更新 → 循环
```

脚本级对应（重型任务用脚本而非 MCP）：disease_pathway_workflow.py 四步（含本地 herb-mol 索引）；MCP 适合单点快速验证。

## 四、2026-10-12 新通道探测快照（12 通道实测）

| 通道 | 状态 | 要点 |
|---|---|---|
| UniProt ID mapping | **up** | run+poll 双端点全通，1.4-3.3s |
| Pharos GraphQL | **up** | pharos-api.ncats.io/graphql；introspection 可用；`target(q:{sym})` 返回 TDL/UniProt/名称/家族 |
| Monarch v3 | **up** | api-v3.monarchinitiative.org/v3/api/search + /entity/{id}；疾病搜索与详情（UC=MONDO:0005101） |
| PubChem assay | **up** | /rest/pug/assay/aid/{id}/description/JSON（生物活性数据备用通道） |
| LOTUS 前端 | up（HTML） | lotus.naturalproducts.net 可达，但 API 端点未定位（api.gnps2.org 拒连） |
| Reactome 前端 | up（HTML） | reactome.org 可达，但 AnalysisService identifiers 端点 404/415（迁移） |
| COCONUT 前端 | up（HTML） | coconut.naturalproducts.net 首页可达，/api 路径 404（API 未定位） |
| Wikidata SPARQL | **flaky** | 当晚多次连接重置（WinError 10054）；历史可靠（POST+VALUES≤200），工具内置重试 |
| Open Targets Genetics | **down** | api.genetics.opentargets.org DNS 解析失败（域名疑似退役） |
| SuperNatural | down | supernatural2.dkfz.de / ddpharmfac.net 均域名失败 |
| BATMAN-TCM 2 | down | bionet.ncpsb.org.cn 超时 |
| HPA 旧 API | down | proteinatlas.org/api/search_download_info.php 404（新 API 待定位） |

结论：交叉分析五工具所依赖的通道（PubChem/ChEMBL/SymMap/STRING/OT Platform/Pharos/UniProt/g:Profiler）当日全部实测可用；Wikidata 波动已做容错。

## 五、与既有工具的分工

- `tcm_string_ppi`（旧）：单基因互作查询；`tcm_network_analysis`（新）：多基因**网络+中心性+hub**——分析层升级。
- `tcm_enrichment_analysis`（旧）：单引擎（g:Profiler）；`tcm_multi_enrichment`（新）：**双引擎对比+共识**——证据层升级。
- `tcm_pubchem_lookup` / `tcm_chembl_lookup`（旧）：单库详情；`tcm_cross_database_screen`（新）：**多库并发+共识打分**——筛选层升级。
- `tcm_hypergeometric_test`：Step4 数学检验（与富集互补，一个是富集显著性、一个是集合过表征）。
