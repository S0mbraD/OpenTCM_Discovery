---
name: open-tcm-discovery
description: 'Unified TCM drug-discovery skill: disease-first screening (disease to KEGG/SymMap pathways, candidate herbs, OB-filtered ingredients, hypergeometric FDR), cross-database evidence screening (PubChem, ChEMBL, SymMap, STRING, Open Targets, Pharos), PPI network and hub analysis, dual-engine enrichment consensus, Wikidata/UniProt ID bridging, molecular library building with QC and dedup, AutoDock Vina docking, contact and H-bond analysis, geometric PPI modeling, PyMOL publication figures, runtime self-iteration, local cleanup of tables, identifiers, SMILES, gene symbols, disease names, and doses, plus local alignment of TCMSP ingredient names and InChIKeys. One 64-tool MCP server: planner, execution engine (auto-chain incl. ADMET/literature/trials), parallel layer, evidence expansion (PubMed, herb-pair, re-score, figures), P1 (node2vec, GEO scRNA, ADMET), P2 (trials matching), bootstrap (dependency check; optional bin/vina.exe), prep layer. Use for TCM, 中药, targets, pathways, PPI networks, enrichment, docking, Vina, pocket, ulcerative colitis or any disease research, network pharmacology, evidence-graded in-silico modeling.'
name_cn: "中药小分子检索、通路筛选与对接"
description_cn: "中药小分子的检索和筛选。从疾病查通路、药材和 OB 成分，再做超几何 FDR。多库交叉核对靶点。PPI 网络、hub 和双引擎富集。Wikidata、UniProt 标识互转。分子库 QC 和去重。Vina 对接、接触残基、几何 PPI、PyMOL 论文图。运行时自迭代。表格、标识、SMILES、基因符号、疾病名和剂量可以在本地整理。TCMSP 成分名可以和调用方给出的 InChIKey 在本地对齐。64 个工具在同一个 MCP 服务里，包括规划、执行、断点续跑、缓存、结构分类、报告、并行比较、文献出图、P1、P2、依赖检查、数据整理和成分名对齐。用中文说明疾病和成分，就能把在线步骤串起来。药材抓取要 30–60 分钟，由脚本另跑。depth=full 只登记命令。参数放在 params 里。会话先看 session_id、status、source、queried_at，失败看 error。对接输出写提议的对接模型，口袋残基必须来自文献。OB≥30、DL≥0.18 只用于 TCMSP 本页的 ob、dl，不和 HERB 的 DL 或 RDKit 的 Lipinski、QED 混用。文献共现、方剂集合重叠、试验登记、嵌入、敲除、ADMET 近似和剂量换算都不是疗效。预测靶点不是已记录证据。tcm_align_tcmsp 只比字符串，不访问网站。剂量必须点名 metric、market_1959、han_tang、song_yuan_2025、ming_qing_2025、song_jin_yuan_2024 或 ming_qing_2024，不能并成一个通用的两。"
---

# 中药小分子检索与对接

这个技能做中药小分子的检索、筛选和对接。顺序是：疾病检索，多库核对，网络和富集，分子库，对接，论文图，运行之后的自迭代。

MCP 里一共 64 个工具。检索分析 27 个，对接建模 8 个，智能层 8 个，并行层 4 个，证据与出图 4 个，P1 有 3 个，P2 有 1 个，依赖自举 1 个，数据整理 6 个，TCMSP 旧站检索 1 个，成分名对齐 1 个。另外有 20 个脚本、11 步本地 DAG、15 份参考文档。工具名录见 `docs/TOOLS.md`。

智能层负责出计划，并执行在线步骤。抓药材成分要 30–60 分钟，用 `scripts/disease_pathway_workflow.py` 另跑。`depth=full` 时，引擎只把这条命令登记为延后步骤 D3，不在同一次请求里抓取。

## 先做这几步

1. **看总览**。调用 `unified_overview`，看模块、工具和依赖是否正常，并读本文件的约定。
2. **出计划**。调用 `tcm_smart_plan`。可以传 goal，也可以传 disease、compounds、genes。计划里带决策点 D1–D6。
3. **按深度执行**。调用 `tcm_smart_run(goal=...)`。在线链是：M1 通路；D1 分支之后做 M3 网络或富集；M2 多库核对；P1 ADMET；P0 文献共现；P2 临床试验。文献口袋和 Vina 都齐了，才进入 M5 对接。`depth=full` 不在这次调用里跑四步药材脚本，只把命令写入延后步骤 D3。缺文献口袋残基或 Vina 时暂停，并写出续跑参数。不要猜口袋。用同一个 `session_id` 续跑时，已完成的步骤不重跑。Vina 按这个顺序找：调用参数，然后 `VINA_EXE`，然后 `bin/vina.exe`，最后 PATH。
4. **比较多个候选**。`tcm_multi_path_run(compounds=[...])` 让每个候选各跑一条链（共识、ADME、可选对接），再出横向对比和综合排名。一个受体对多个配体，用 `dock_batch_screening`：并行 Vina，先做 ADME 预过滤，再排名。看网络里哪个节点脆，用 `tcm_network_simulation`：knockout 扫描、蒙特卡洛对照、z-score。
5. **写报告**。`tcm_report_generate(session_id=...)` 写出 Markdown 研究简报，给导师审阅。简报里有时间线、逐步结果、决策日志、证据分级和每一步的来源。文献共现计数用 `tcm_literature_mine`。网络图用 `tcm_network_figure`。
6. **按结果分叉**。hub 有多少，决定做不做富集。共识分决定进不进对接。亲和能决定还要不要往下看。出现复现红旗，先回去查受体准备。
7. **收尾**。会话结束前跑 `tcm_analyze_runtime` 和 `dock_analyze_runtime`。新的运行观察交给 `tcm_report_insight` 或 `dock_report_insight`。写入 `references/runtime_lessons.md` 必须等用户点名，见文末。

**参数**：带参数的工具把参数放在 `{"params": {...}}` 里。漏了这一层，返回的是校验错误，不是空的科学结果。

## 返回里先看这些

`tcm_smart_run` 的正文按这个顺序读。`session_id` 用来续跑。`status` 为 `done` 是这次在线链结束，为 `paused` 是在等文献口袋、Vina、结构文件，或更长的 `max_runtime`。`executed steps` 里每一行有步号、工具、状态和摘要。在线步骤还会带 `source` 和 `queried_at`。`decision log` 是 D1–D6 实际走了哪条分支。通路基因少于 15 个时，第 2 步的工具名写成 `(skipped: D1 direct branch)`，状态仍是 ok，表示直接去做富集。`deferred scripts` 里的命令没有在这次请求里执行。分析拓扑一节的图例、检索通路、下一步建议和数据源状态是给智能体看进度的。下一步最多三条，不会自动调用。iteration packet 的 `open` 为空就不要改技能文件。

单次工具失败时，正文以 `Error:` 开头，或 JSON 里有 `error`。空的 `ingredients`、空的命中列表，配合错误说明，表示这次没取到。缓存命中时，`queried_at` 保持原来那次查询的时间，不是这次读缓存的时间。报错不进缓存。

TCMSP 旧站：`herbs` 多于一行且没有 `latin_name` 时，成分表不会自动选一行。`ob_dl_filter` 只作用于本页 `ob`、`dl`。对齐工具看 `n_matches`、`n_ambiguous`、`matches` 里的 `link`，以及两边的 unmatched。`link` 为 `name` 只是名称字符串相同。`link` 为 `inchikey` 只是 27 位字符串相同，没有向 PubChem 核对。

剂量必须带 `convention`。可选值是 `metric`、`market_1959`、`han_tang`、`song_yuan_2025`、`ming_qing_2025`、`song_jin_yuan_2024`、`ming_qing_2024`。不写口径就会报错，不会默认成某一个「两」。毫升和升不会换成克。返回里的 `grams` 是克，`ratio_to_min` 是这次输入里相对最小剂量的重量比，`citation` 写明这套数字出自哪里。

## 说什么，用哪个工具

下表按你说的话选入口。括号里是中英文关键词。

| 用户目标（中/英关键词） | 模块 | 入口 |
|---|---|---|
| 疾病相关通路/候选药材/有效成分（disease, colitis, 通路, 疾病） | M1 | `tcm_kegg_disease_pathways` → 四步脚本 |
| 化合物或靶点的多库验证/共识/TDL（compound, target, 靶点, 交叉验证） | M2 | `tcm_cross_database_screen`（并发批量） |
| 网络图/hub基因/富集/通路共识（network, hub, 富集, 网络） | M3 | `tcm_network_analysis` → `tcm_multi_enrichment` |
| 网络脆弱性/敲除模拟/鲁棒性（knockout, 扰动, 脆弱, simulation） | M3+ | `tcm_network_simulation`（knockout+蒙特卡洛+z-score） |
| 理化性质/类药性/Lipinski/ADME 描述符（drug-likeness, Lipinski, TPSA, 理化） | M2+ | `tcm_adme_profile`（批量，RDKit） |
| 分子库/成分整理/去重/结构分类/QC（library, 去重, QC, 分子库） | M4 | `molecular_library_builder.py`（14 步） |
| 对接/结合模式/口袋/亲和能/复合物（docking, Vina, 对接, 口袋） | M5 | `dock_check_environment` → `dock_vina_docking` |
| 虚拟筛选/多化合物批量对接排名（screening, 筛库, 批量对接, virtual screening） | M5+ | `dock_batch_screening`（并行+ADME预过滤+排名） |
| 多候选横向比较/并行比较（compare, 比较, 多化合物, 哪个更好） | 并行层 | `tcm_multi_path_run`（多臂并行+综合排名） |
| 论文图/组合图/渲染/证据声明（figure, 渲染, 论文图） | M6 | `make_figure_pml.py` → `compose_panel.py` |
| 会话收尾/发现新情况/长期运行（runtime, 自迭代, 死路） | M7 | `*_analyze_runtime` → `*_report_insight` |
| 表格、标识、基因符号、疾病名、剂量或 SMILES（table, SMILES, InChIKey, 基因符号, 剂量, MeSH） | 数据整理 | `tcm_clean_table`、`tcm_normalize_ids`、`tcm_normalize_genes`、`tcm_normalize_disease`、`tcm_convert_dose`、`tcm_normalize_structure` |
| 已有 TCMSP 成分行要对上名称或 InChIKey（align, MOL_ID, InChIKey, 成分名） | 本地对齐 | `tcm_align_tcmsp` |
| 不确定/组合目标 | 全部 | `tcm_smart_plan`（自动识别并组链） |

## 写结果时要守的规矩

1. **当次检索**。仓库里不预置疾病、基因或成分的答案。结论来自这次的 API 查询。输出带 `source` 和 `queried_at`。响应缓存只保存过去真实查到的结果，有 TTL。原来的 `queried_at` 留在结果里。报错不缓存。
2. **证据分三层**。实验证据高于结构模板，结构模板高于计算预测。对接输出一律写 "proposed docking model"。预测残基加 `*`，接触用虚线。`tcm_classify_structure` 的结构分类、`tcm_network_simulation` 的拓扑结论、`tcm_adme_profile` 的 ADME 描述符，都算计算预测。发表前要另行核对。多臂比较和批量筛选的综合排名是优先级启发式，不是验证过的打分函数。
3. **ADME 门槛**。OB≥30，DL≥0.18。只用 TCMSP 的口径。HERB 的 DL 不能混进来。`tcm_adme_profile` 给出的 MW、TPSA、LogP、Lipinski 是另一套描述符。不要和 TCMSP 这道门写成同一个结论。
4. **口袋**。口袋残基必须来自文献，不能猜。执行引擎缺口袋就暂停。批量筛选默认 exhaustiveness=4，只做快速分诊。要重点看的化合物，用 exhaustiveness 16 再对接一遍。
5. **写文件**。共享文件先写临时文件，再用 `os.replace` 换上。不要并发写同一个文件。
6. **自迭代**。死路、端点变更、接口怪癖和复现问题，用 `*_report_insight` 记下来。会话结束时跑分析。追加到 `references/runtime_lessons.md` 时，必须用户点名那个 id。
7. **整理不是结论**。`tcm_clean_table`、`tcm_normalize_ids`、`tcm_normalize_structure`、`tcm_normalize_genes`、`tcm_normalize_disease` 和 `tcm_convert_dose` 只整理用户已经给出的文本。基因别名、MeSH 对照和剂量比例不能写成靶点收录或疗效。
8. **名称对齐不是结构鉴定**。`tcm_align_tcmsp` 只比较调用方已经给出的成分名、MOL_ID 和 InChIKey。名称相同不是结构相同。全键相同只说明字符串一致，没有向 PubChem 核对。它不访问 TCMSP，也不是靶点或疗效。

## MCP 服务器

任意支持 MCP 的客户端都可以接。推荐 stdio。需要常驻进程时用 Streamable HTTP，默认端口 8002。Cursor 只是例子之一。

```bash
python -m pip install -r requirements.txt   # 安装 requirements.txt 里的 Python 依赖

python mcp_server/unified_mcp.py --stdio     # 推荐。一次连接 64 个工具
python mcp_server/unified_mcp.py             # HTTP，默认端口 8002
```

配置里的路径写成 `<仓库绝对路径>`，不要写某一台机器上的目录。stdio 由客户端拉起子进程，不占端口。HTTP 只绑定 `127.0.0.1:8002`，路径是 `/mcp`。只跑 `open_tcm_discovery_mcp.py` 时得到检索一族，默认端口是 8000，工具列表会短一截。对接族单独跑 `structure_docking_mcp.py` 时默认端口是 8001。接上之后用 `list_tools` 看数量，期望是 64。不是 64 时，先看启动的是不是 `unified_mcp.py`，不要改成去猜口袋或补记录。

Windows 上先设 `PYTHONUTF8=1`，否则中文摘要可能乱码，看起来像工具坏了。`tcm_dependency_status` 用来看缺哪个 Python 包、Vina 解析到了哪条路径。`action=install` 才会装包。Vina 不随仓库分发，没装时检索仍可跑，对接会在 `tcm_smart_run` 里暂停。

**自举**（`bootstrap_layer.py`）：`tcm_dependency_status` 检查依赖是否齐全，以及缺了会影响什么。`action=install` 可以安装缺失的 Python 包。Vina 不随仓库分发。把 AutoDock Vina 1.2.5 的官方二进制放到 `bin/vina.exe`，或设置 `VINA_EXE`。查找顺序：调用参数，`VINA_EXE`，`bin/vina.exe`，PATH。详见 `bin/README.md`。

**智能层**：`unified_overview` 看总览。`tcm_smart_plan` 按规则识别意图，生成带决策点的步骤。`tcm_smart_run` 是执行引擎，会话可以断点续跑，到决策点自动分叉，缺文献口袋残基就暂停。`tcm_report_generate` 写研究简报。`tcm_cache_status` 查看或清空响应缓存，环境变量 `TCM_CACHE_TTL` 默认 3600 秒。`tcm_classify_structure` 用 SMILES 做规则分类，类别有黄酮、蒽醌、香豆素、生物碱、萜类、甾体等，规则用 RDKit SMARTS 对过。`tcm_self_iterate` 复核运行观察，只有用户点名的 id 才会追加到 `references/runtime_lessons.md`。`tcm_analysis_topology` 按会话重画分析网络，标出数据库、文献库、方剂索引和执行状态。

**并行层**（`parallel_layer.py`）：`tcm_adme_profile` 批量算理化性质，以及 Lipinski、Veber、Egan、QED；给出成分名时当场去取 SMILES。`tcm_network_simulation` 在 STRING 的实时网络上做 knockout、累积敲除和蒙特卡洛对照，并算 z-score。`dock_batch_screening` 让多个配体并行做 Vina，先按 ADME 预过滤，再按亲和能排名，用于快速分诊。`tcm_multi_path_run` 让多个候选并行，顺序是共识、ADME、可选对接，然后出对比表和综合排名。

**P0：文献、方剂、精排和出图**（`lit_formula_layer.py`、`rescore_figure_layer.py`）：`tcm_literature_mine` 用 PubMed E-utilities 数三元共现，并看逐年趋势，限速 0.4 秒一次请求。至少要有基因或疾病之一。只给成分名会直接报错，因为那不是一条关系。返回里看三元计数、成对计数和逐年趋势。共现是文献计量，不是因果。`tcm_formula_analysis` 算药对或复方成分集合的 Jaccard、重叠系数和互补比例。本地 SymMap 索引有 698 味药材、25,975 个成分，文件是 `data/herb_mol_index.json`。索引找不到，或对上的药材少于两味，会以 `Error:` 开头并写明缺什么。高重叠那一档的标签字面是「功效相近」，读的时候仍是集合重叠，不是药效。可选的靶点层会再去查 SymMap，那一部分才访问网络。`dock_rescore` 给批量筛选做精排，看亲和能谱的 mode-gap 和文献口袋锚定率，再给一个复合分。这不是验证过的打分函数。`tcm_network_figure` 出 3200×2200 的 PNG 和 PDF，hub 分层着色，图注里写证据分级，随机种子固定，图可以复现。

**P1**（`p1_layer.py`）：`tcm_graph_embed` 做 node2vec 式图嵌入。带偏随机游走，再算 PMI，用 numpy 做截断 SVD。按与种子基因的余弦相似度排靶点，并和中心性对照。这是拓扑上的排序，不是功能验证。`tcm_scrna_channel` 在 GEO 里找单细胞数据集，用 esearch（`db=gds`）和 esummary 当场查询，返回 GSE、实验设计、物种、样本数和相关 PubMed。HPA 和 GTEx 这条通道已经确认走不通，这里只做数据集发现。`tcm_admet_predict` 是本地 ADMET 近似，包括 ESOL 水溶性（Delaney）、BBB 盒式规则、PPB、Caco-2、hERG 代理。ADMETlab 的 REST 接口已经确认不能用，结果都标成近似模型。

**P2**（`p2_layer.py`）：`tcm_trials_match` 查 ClinicalTrials.gov API v2，当场请求。按疾病和靶点或化合物，返回试验总数、分期计数和 NCT 表。先看 `total_trials` 和 `phase_counts`，再看表里的 NCT、标题和状态。试验匹配只说明转化方面有哪些试验，不能当成候选药已经有效。OpenMM 分子动力学试点评估之后先不做，这条不算死路。

**数据整理**（`prep_layer.py`）：六个工具都不访问网络，也不进入 `tcm_smart_run` 的自动链。`tcm_clean_table` 整理粘贴的表。第一行当表头，空行去掉，某一行列数对不上就报错，不补空列。`tcm_normalize_ids` 检查 CID、CAS 校验位、InChIKey、UniProt 和 Ensembl，裸数字不会当成 CID。`tcm_normalize_structure` 检查 SMILES；装了 RDKit 才写规范 SMILES 和 InChIKey，没装就写明，这个 InChIKey 不是 PubChem 当场核对过的。`tcm_normalize_genes` 把常见别名收成一个符号，家族名留在 `ambiguous`，不要挑一个当成已经收录。`tcm_normalize_disease` 只对到本地核对过的 MeSH 标题。缩写、过宽的词和表外病名保持 unmapped、ambiguous 或 nonspecific，不会编一个 MeSH 号。`tcm_convert_dose` 按点名的口径换成克。`market_1959` 的一两是 50 g，那是 1959 年后的市秤。`han_tang` 的一两是 13.8 g。宋元、明清各有 2025 年网络药理学用表和 2024 年经典名方考证两套数字，必须点名 `song_yuan_2025`、`ming_qing_2025`、`song_jin_yuan_2024` 或 `ming_qing_2024` 里的一个。重量比不是药效。

**TCMSP**（`tcmsp_layer.py`）：`tcm_tcmsp_search` 只请求 `https://old.tcmsp-e.com/`。先读首页 token，再读药材表；只命中一行，或给了 `latin_name`，才读成分表。超时 20 秒，超时或 HTTP 5xx 再试一次。成功时看 `herbs`、`ingredients`、`ingredients_matched` 和 `queried_at`。`ob_dl_filter` 默认关闭。打开时只保留本页 OB≥30 且 DL≥0.18 的行，丢掉的行数在 `ob_dl_dropped`。这道门不用于 HERB 的 DL，也不用于 RDKit 的 Lipinski 或 QED。页面没有表、请求失败，或药材名对上多行时，`ingredients` 为空，不补记录。成分表不是疗效，这一页也没有靶点。

**成分名对齐**（`align_layer.py`）：`tcm_align_tcmsp` 不访问网络，也不进只读缓存，不进入 `tcm_smart_run` 的自动链。它把已经拿到的 TCMSP 成分行，和调用方自己的名称、InChIKey 表对在一起。同一行上多种键都有时，先看全键，再看不冲突的 MOL_ID，再看 InChIKey 前 14 位，最后才看名称。两边 InChIKey 都有且前 14 位不同，不用名称硬并。同一键对上多行就放进 `ambiguous`，不挑一行。`ob`、`dl` 原样抄出。`tcmsp_ob_dl_pass` 只在两个数都有时填写，表示是否达到 30 和 0.18，这里不筛选。`ob_dl_filter_applied` 是 false。没有 `queried_at`，因为这不是一次新的网站请求。单侧最多 2000 行。`data/herb_mol_index.json` 没有成分名，这里不读它。

**检索分析族**：tcm_pubchem_lookup / tcm_kegg_compound / tcm_chembl_lookup / tcm_ensembl_lookup / tcm_search_disease_targets / tcm_string_ppi / tcm_tcmbank_search / tcm_ctd_gene_interactions / tcm_get_gene_info / tcm_ncbi_gene_search / tcm_enrichment_analysis / tcm_npass_search_compound / tcm_get_structure / tcm_dgidb_interactions / tcm_gwas_associations / tcm_check_database_status / tcm_audit_evidence / tcm_symmap_lookup / tcm_kegg_disease_pathways / tcm_hypergeometric_test / tcm_cross_database_screen（并发）/ tcm_network_analysis / tcm_multi_enrichment / tcm_wikidata_lookup / tcm_uniprot_idmap / tcm_report_insight / tcm_analyze_runtime
**对接建模族**：dock_check_environment / dock_fetch_structure / dock_vina_docking / dock_analyze_contacts / dock_geometric_ppi / dock_report_insight / dock_analyze_runtime / dock_compare_experiments

评估记录在 `mcp_server/evaluation.xml`，写的是实测，不是推断。代码分层如下。`unified_mcp.py` 里有 `unified_overview` 和 `tcm_smart_plan`。`smart_layer.py` 里有缓存、结构分类、`tcm_smart_run` 和研究报告。其余层在 `parallel_layer.py`、`lit_formula_layer.py`、`rescore_figure_layer.py`、`p1_layer.py`、`p2_layer.py`、`bootstrap_layer.py`、`iteration_layer.py`、`topology_layer.py`、`prep_layer.py`、`tcmsp_layer.py`。本地索引是 `data/herb_mol_index.json`。网络图用自研的 Fruchterman–Reingold 布局。以前文档里的 tcm-database-query 和 structure-docking-analysis，就是现在的 `tcm_*` 和 `dock_*`。

## 模块与资产

| 模块 | 文档 | 一句话 |
|---|---|---|
| M1 疾病优先检索 | modules/m1_disease_screening.md | 疾病→通路→药材→成分→FDR |
| M2 交叉证据 | modules/m2_target_evidence.md | 多库共识+TDL+ID桥接 |
| M3 网络与富集 | modules/m3_network_enrichment.md | hub排名+双引擎共识 |
| M4 分子库构建 | modules/m4_molecular_library.md | 14步+QC五维+五层去重 |
| M5 对接建模 | modules/m5_docking_modeling.md | Vina/接触/几何PPI+台账 |
| M6 渲染出图 | modules/m6_figures_documentation.md | 论文图+组合图+证据分级 |
| M7 自迭代 | modules/m7_self_iteration.md | 遥测→洞察→用户确认后写入教训 |

`scripts/` 里有 20 个脚本：检索 14 个，对接 6 个。都用 argparse，可以单独跑。

`workflow_engine/` 是本地 DAG。疾病可以配置，一共 11 步，用 `run_server.py` 启动，带看板。

`references/` 有 15 份文档，按数据库和主题记端点、死路、作图风格、证据分级、自迭代和已经确认的教训。用库名搜索，再读那一节。

运行时状态写在 `~/.tcm_mcp_state` 和 `~/.dock_mcp_state`，包括遥测、洞察和实验台账。路径可以用环境变量改。

## 环境

Python 3.10 及以上。Windows 先执行 `$env:PYTHONUTF8="1"`。

```bash
python -m pip install -r requirements.txt
```

本地 DAG 还要装 `requirements-workflow.txt`（pandas、PyYAML）：

```bash
python -m pip install -r requirements-workflow.txt
```

PyMOL 只在画结构图时需要。对接前先跑 `dock_check_environment`。科学边界、两条运行路径，以及 64 个工具的字段说明，写在 `docs/`。

## 每次运行后的自迭代

`tcm_smart_run` 的返回里带有 iteration packet。任务开始时先读 `references/runtime_lessons.md`。一次运行结束后：

1. 若 packet 的 open 为空，停止，不要改技能文件。
2. 调用 `tcm_self_iterate`，`action=review`，向用户展示 `strength=strong` 的 id、标题和证据。
3. 只把用户点名的 id 交给 `action=apply` 或 `action=reject`。
4. `apply` 只追加 `references/runtime_lessons.md`。不要据此修改工具代码、OB/DL 门槛、口袋规则、统计函数或数据源路由。
5. 这些条目是运行观察。下次遇到相符的失败时向用户说明，不要把它们写成疗效或靶点结论。

## 分析网络

`tcm_smart_run` 在每一步之后重画分析网络。图里有两类节点。一类是疾病、通路、基因、成分、药材、方剂集合、文献和试验。另一类是 KEGG、SymMap、STRING、PubChem、ChEMBL、Pharos、Open Targets、PubMed、ClinicalTrials.gov 和本地药材成分索引。目标里至少有两味药材时，引擎先用本地索引算成分集合重叠，再继续在线检索。

返回里的 Mermaid、`sessions/<id>.topology.svg` 和自包含的 `sessions/<id>.topology.html` 表示网络怎样长出来，以及哪一步完成了、哪一步失败了。需要重画时调用 `tcm_analysis_topology`。文献共现、集合重叠、试验登记和收录计数标在边上。这些是检索关系，不是疗效，也不是配伍结论。

小图是浅暖宣纸底上的米白圆角卡片，木色边框，卡片里写名称和一行详情。边用箭头和线型表示方向和状态：失败是实线，进行中是虚线，完成是实线并带轻流动，跳过是灰褐实线，ready 与 deferred 各用不同虚线。实体节点少于 300 个时用这种卡片。数到 300 个以后，改成按类别分组的暖琥珀球壳，同一类在同一个球上，步骤仍在底部，步骤节点不计入这 300。图和返回文字里都有图例、检索通路、下一步建议和数据源状态。这四段是给智能体读的，不是疗效结论。通路按图上已有的边写疾病到药材走到哪一环，缺的环写明缺什么。下一步最多三条，指向已有工具，不会自动执行。少于两味药材不建议方剂集合重叠。文献没有写明口袋残基时不建议对接。摘要里的免责说明是：拓扑图表示检索网络如何长成，以及每一步的状态。边上的文献共现、集合重叠、试验登记和收录计数都保持各自的限制，不能读成因果、药效或疗效。
