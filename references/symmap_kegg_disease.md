# SymMap v2.0 API 通道与疾病优先四步工作流（2026-10-10/11 实测）

> 背景：SymMap v2.0（symmap.org）官方 API 文档不可用（无 /docs、无 swagger），以下端点均由前端 JavaScript 逆向挖掘并逐一经真实请求验证。
> 基础地址：`http://symmap.org`（http 即可，无需 Key）。
> 共享客户端：`scripts/symmap_client.py`（节流 0.4s + 3 次重试 + 502 退避），四步工作流：`scripts/disease_pathway_workflow.py`。
> MCP 工具：`tcm_symmap_lookup`（四动作合一）、`tcm_kegg_disease_pathways`、`tcm_hypergeometric_test`。

## 一、四端点详解

### 1. POST /search_th/ —— 名称联想（typeahead）

```json
{"name": "ulcerative colitis", "table_name": "Disease"}
```

- 返回：字符串数组（名称建议），无 ID。
- 用途：搜索词容错兜底（药材拼音/别名不确定时先跑这个）。

### 2. POST /search/ —— 实体搜索（带 ID）

```json
{"table_name": "Disease", "key": "ulcerative colitis"}
```

- 返回：`{"data": [...]}`，每条记录含实体 ID（如 `Disease_id: "SMDE14075"`）。
- ID 命名规律：表名前缀（SMHB=Herb、SMIT=Mol、SMDE=Disease、SMGE=Gene…）。
- **字段名陷阱**：不同端点返回的 Mol ID 字段名不一致（`Mol_id` / `MOL_id`），客户端提供 `first_id(row)` 助手统一提取。

### 3. POST /related_components/ —— 关联实体（核心关系查询）

```json
{"rrid": "SMDE14075", "table_name": "Gene", "filter": "0"}
```

- `rrid`：源实体 ID（先 /search/ 拿到）。
- `table_name`：**目标表**（要查的方向），不是源表。
- `filter`：`"0"`=All、`"1"`=P<0.05、`"2"`=FDR(BH)<0.05、`"3"`=Bonferroni。
- 返回：`{"data": [...]}`，含目标实体与关联统计（如 Herb→Mol 带模型预测分）。
- **502 陷阱（重要）**：大结果集请求（hub 基因 Gene→Mol、多靶点成分 Mol→Herb）后端超时返回 HTTP 502。客户端重试 3 次（6s/12s/18s 退避）后抛 `SymMapTimeout`，调用方应捕获并跳过该实体，不要无限重试。

### 4. POST /related_enrichment/ —— 服务端 KEGG 富集

```json
{"genes": "{\"gene\": [\"NFKB1\", \"RELA\", \"JUN\"]}"}
```

- 注意 `genes` 是**字符串化的 JSON**（不是数组），内层键为 `gene`。
- 基因符号**不能带分号**（会静默失败）；KEGG `get/{pathway}` 返回的 GENE 段符号自带 `;` 尾缀，必须 strip。
- 返回：通路列表含 odd ratio / P / FDR。

### 5. GET /static/download/V2.0/ —— 全量实体表

- 路径下有 SMHB（药材）、SMIT（成分）等 xlsx 全量表，可离线核对实体清单（约 703 药材）。
- 属静态快照，仍须实时检索后才可作为结论（见实时检索纪律）。

## 二、关系方向表（SymMap 原生支持的方向）

| 源表 | 可查目标表（related_components 的 table_name） |
|---|---|
| Herb | Mol / TCM_symptom / MM_symptom / Syndrome |
| Mol | Herb / Gene |
| Gene | Mol / Disease |
| Disease | MM_symptom / Gene |
| TCM_symptom | Herb / MM_symptom / Syndrome |
| MM_symptom | TCM_symptom / Disease |

不在表中的方向（如 Disease→Herb 直连）SymMap 不支持，需经中间实体串联（这正是四步工作流的 Step2 走 Disease→MM_symptom→TCM_symptom→Herb 症状链的原因）。

## 三、KEGG DISEASE 通道（Step1 直连半边）

| 端点 | 用途 | 陷阱 |
|---|---|---|
| `GET /list/disease` | 全部 H##### 疾病条目（id + 同义名列表） | 名称匹配用 tab 分隔后按 `;` 拆同义名，双向包含匹配 |
| `GET /link/pathway/{Hid}` | 疾病↔通路直连对 | 覆盖以癌症/感染为主，多因子病（如 UC）覆盖差 → 必须 SymMap 富集通道补 |
| `GET /get/{hsa#####}` | 通路详情（GENE 段） | GENE 段符号带 `;` 尾缀须 strip |
| `GET /list/pathway/hsa` | 全通路名表 | **一次性拉取建缓存**，逐个请求会拖垮超时 |

Step1 双通道合并逻辑：KEGG 直连命中打 `KEGG-direct` 标签，SymMap 富集命中打 `SymMap-enrichment` 标签，按富集 P 值排序输出（两通道都命中是最强证据）。

## 四、疾病优先四步工作流

```
Step 1  disease-pathways   疾病 → KEGG/SymMap 通路（双通道合并，151 条 for UC）
Step 2  pathway-herbs      通路基因集 → 候选药材
          (a) 症状链：Disease→MM_symptom→TCM_symptom→Herb（SymMap 原生方向）
          (b) 反向链：通路基因 → Gene→Mol → 成分池 → 含该成分的药材
              （需 herb_mol_index.json，见 build-index）
Step 3  herb-ingredients   药材 → SymMap Herb→Mol，OB_score 即 TCMSP 口服生物利用度，
                          OB≥30 过滤（黄芩 411 成分 → 105 个 OB≥30）
Step 4  cross-validate     成分靶点（Mol→Gene）对疾病通路基因集做单尾超几何检验
                          （Fisher 精确等价、log-factorial 防溢出），报告
                          P 值 / 富集比 (k/n)/(K/N) / BH-FDR，药材级 + 成分级
```

命令：

```bash
python scripts/disease_pathway_workflow.py run-all --disease "ulcerative colitis" --out workdir_uc
# 或分步：disease-pathways / pathway-herbs / herb-ingredients / cross-validate / build-index
```

### build-index（Step2b 前置）

- 爬取 SymMap 全部药材（~703）的 Herb→Mol 关系，原子写为 `herb_mol_index.json`（含 source/queried_at 戳）。
- 约 700 次请求、30-60 分钟；每 25 个药材存 checkpoint，**断点续跑**。
- 性质是关系对缓存（保留 SymMap 溯源），不是预置答案，符合实时检索纪律。

### 药材名检索容错（实测）

- 拼音需**无空格**（"Huang Qin" 可能不命中，"HuangQin" 或中文"黄芩"更稳）。
- 不确定时先 `typeahead` 兜底，或用拉丁学名（如苦参 `Radix Sophorae Flavescentis`）。

## 五、超几何检验（Step4 数学）

- P(X≥k) = Σ C(K,i)C(N-K,n-i)/C(N,n)，log-factorial（lgamma）实现防溢出。
- k=命中数，K=通路基因数，n=查询集大小，N=背景基因数（默认 20000 人蛋白编码基因）。
- **已验证**：扑克牌教科书值 N=52, K=13, n=5, k≥3 → 0.0928（正确）。
- 历史 bug 教训：log-factorial 项曾误写 `lnfact(N-n+i)` 漏了 K，导致 P 值下溢为 0——改公式后必须用已知分布重验。

## 六、UC 实测基线（2026-10-11，全部实时数据）

| 步骤 | 结果摘要 |
|---|---|
| Step1 | 151 条通路；IBD hsa05321 p=4.87e-18（第 7 位），TNF/IL-17/类风湿等炎症通路在列 |
| Step2 | 症状链 574 候选药材；symptom_hits 最高 24（吴茱萸/川芎/半夏等） |
| Step3 | 黄芩 411 成分/105 OB≥30；黄连 241/54；白头翁 127/24；秦皮 122/17 |
| Step4 | 药材级：黄芩 P=3.21e-14、黄连 P=2.24e-14；成分级：berberine p≈1.93e-20 量级 |

## 七、与 MCP 工具的对应

| MCP 工具 | 覆盖 |
|---|---|
| `tcm_symmap_lookup` | search / typeahead / related / enrichment 四动作（related 需先 search 拿 rrid） |
| `tcm_kegg_disease_pathways` | Step1 双通道合并 |
| `tcm_hypergeometric_test` | Step4 数学检验（三基因列表 + 背景大小） |

Step2/Step3 的串联逻辑较重（含本地索引），经脚本 `disease_pathway_workflow.py` 执行；MCP 工具适合单点查询与快速验证。

## 八、下游衔接

- Step3 产出的成分（含 SymMap ID/名称）可送 PubChem 补 SMILES/CID（`tcm_pubchem_lookup` / `fetch_pubchem_batch.py`）。
- 补全结构后可流入本技能 dock_* 工具族做 Vina 对接与 PyMOL 论文图（检索→分析闭环）。
- Step4 的显著性成分为后续「成分→靶点→疾病」评分排序（`fetch_target_screening.py`）提供先验。
