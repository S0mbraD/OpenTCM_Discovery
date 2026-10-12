## HERB 2.0

### 批量数据文件

| 文件 | URL | 内容 |
|---|---|---|
| 药材信息 | `http://47.92.70.12/static/download_data/V2/HERB_herb_info_v2.txt` | 6,892 味药，TSV 格式 |
| 成分信息 | `http://47.92.70.12/static/download_data/V2/HERB_ingredient_info_v2.txt` | 44,595 个成分，TSV 格式 |

#### herb_info.txt 列序

```
Herb_id | Herb_name | Herb_alias_name | Latin_name | Species_name | Family | Genus | Species | TCMSP_id | TCMID_id | TCM_ID_id | SymMap_id | PubChem_id | DrugBank_id | NPASS_id | HIT_id | HIT_ingredient_num | HIT_ingredient_id | TCMSP_ingredient_num | TCMSP_ingredient_id
```

#### ingredient_info.txt 列序

```
Ingredient_id | Ingredient_name | Ingredient_alias_name(含CAS) | Molecular_formula | Canonical_smiles | Isomeric_smiles | InChI | InChIKey | MolWt | NumHAcceptors | NumHDonors | MolLogP | NumRotatableBonds | Drug_likeness(DL) | OB_score(OB) | CAS_id | SymMap_id | TCMID_id | TCMSP_id | TCM_ID_id | PubChem_id | DrugBank_id | NPASS_id | HIT_id
```

### 详情 API

**端点**：`POST http://47.92.70.12/chedi/api/`

**请求体**：

```json
{
  "v": "HERB000281",
  "label": "Herb",
  "key_id": "HERB000281",
  "func_name": "detail_api"
}
```

**响应结构**：

- `herb_ingredient`：**list of lists**（非普通数组），每行结构为：
  - `[0]` = dict `{'link': '/chedi/ingredient/detail/...', 'title': 'HBINxxxx'}`（title 即成分 ID）
  - `[1]` = 成分英文名
  - `[2]` = 分子式
  - `[3]` = Canonical SMILES
  - 后续元素可能包含其他字段
- 其他字段含药材摘要信息（药名、拉丁名、功效等）

**解析代码**：

```python
import requests

resp = requests.post(
    "http://47.92.70.12/chedi/api/",
    json={"v": herb_id, "label": "Herb", "key_id": herb_id, "func_name": "detail_api"},
    timeout=30
)
data = resp.json()
for row in data.get("herb_ingredient", []):
    if not row or not isinstance(row[0], dict):
        continue
    ingredient_id = row[0]["title"]  # HBINxxxx
    ingredient_name = row[1] if len(row) > 1 else ""
    formula = row[2] if len(row) > 2 else ""
    smiles = row[3] if len(row) > 3 else ""
```

### HERB 常见陷阱

- `herb_ingredient` 不是普通 dict 数组，而是 **list of lists**，第一个元素是 dict
- 部分行可能缺少后续元素，需用 `len(row)` 判断
- HERB 无药材-成分关联表下载，只能通过 API 逐味获取
- `Ingredient_alias_name` 列可能包含 CAS 号，用正则 `\d+-\d+-\d+` 提取
- `TCMSP_id` 列可能有多个 ID（分号分隔），取第一个
- **⚠️ HERB 的 `Drug_likeness` 字段与 TCMSP 网站的 DL 值计算方法不同，数值完全不一致**（OB 值基本一致，DL 差异可达 ±0.5）。ADME 筛选时必须直接从 TCMSP 获取 DL 值，不能用 HERB 的 Drug_likeness 替代
- 数据文件为 UTF-8 编码的 TSV，**用 Python 读取**（PowerShell 处理中文会乱码）

## TCMSP

### 搜索 API

**Token**：从 `tcmsp.php` 首页的 `<input type="hidden" name="token" value="...">` 提取。⚠️ Token 会过期，搜索返回 "Error querying database.." 时须重新 GET 首页提取新 token。

#### 第一步：中文名搜索

```
GET https://www.tcmsp-e.com/tcmspsearch.php?qs=herb_all_name&q={中文名}&token={token}
```

返回 HTML，数据以 KendoGrid JSON 嵌入：

```javascript
dataSource: { data: [{herb_cn_name, herb_en_name, herb_pinyin, ...}] }
```

#### 第二步：拉丁名搜索

```
GET https://www.tcmsp-e.com/tcmspsearch.php?qr={TCMSP拉丁名}&qsr=herb_en_name&token={token}
Referer: {第一步的搜索URL}
```

**必须带 Referer 头**，否则返回空数据。

### 数据解析

TCMSP 用 KendoGrid 渲染，数据以 JSON 嵌入 HTML：

```python
import json, re

# 从 HTML 中提取 KendoGrid dataSource
match = re.search(r'dataSource:\s*\{', html)
if match:
    decoder = json.JSONDecoder()
    obj, end = decoder.raw_decode(html, match.end() - 1)  # 从 '{' 开始
    data = obj.get("data", [])
```

### 成分字段

`MOL_ID`、`molecule_name`、`ob`、`mw`、`dl`、`alogp`、`caco2`、`bbb`、`hdon`、`hacc`、`FASA`

### 分子详情页

`https://www.tcmsp-e.com/molecule.php?qn={MOL数字}`

含 PubChem CID、CAS、InChIKey，但不含 SMILES。

### TCMSP 常见陷阱

- TCMSP 的拉丁名与药典/HERB 的拉丁名可能不同（如甘草在 TCMSP 中叫 `licorice`，不是 `Glycyrrhizae Radix`）
- 同名药材可能有多个条目（如"黄连"同时匹配胡黄连 Picrorhizae Rhizoma 和黄连 Coptidis Rhizoma），需选正确条目
- 直接用 HERB 的拉丁名搜 TCMSP 大概率失败，必须走两步法获取 TCMSP 自身命名
- 使用 `requests.Session()` 保持 Cookie
- ⚠️ **Token 过期**：Token 使用一段时间后会失效，搜索返回 "Error querying database.." 错误。须重新 GET `https://www.tcmsp-e.com/tcmsp.php` 提取新 token 后重试。Token 不是永久固定的
- ⚠️ **部分药材未被 TCMSP 收录**：矿物药（如石膏、滑石）、动物药（如全蝎、蜈蚣）等在 TCMSP 中无收录。实测 383 味 UC 药材中仅 282 味成功获取（73.6%），101 味未收录
- ⚠️ **OB/DL 匹配率**：用 HERB 的 `TCMSP_id` 列作为匹配键，实测 13,037 条化合物中 7,419 条 DL 值被 TCMSP 数据替换（匹配率 59.7%），OB 值仅 49 条需替换（HERB 的 OB 值与 TCMSP 基本一致）

## 跨库匹配键

| 匹配场景 | 键 |
|---|---|
| HERB → TCMSP | HERB 的 `TCMSP_id` 列 ↔ TCMSP 的 `MOL_ID` |
| HERB → PubChem | HERB 的 `PubChem_id` 列 |
| TCMSP → PubChem | TCMSP 分子详情页的 PubChem CID |

## ETCM 2.0 API

### 基础信息

- 前端 SPA：`http://www.tcmip.cn/ETCM2/front/#/`（Vue + Webpack）
- **API 服务器**：`http://www.tcmip.cn:18124`（Django REST 后端）
- 从前端 JS 中提取的配置：`{apiPath: "http://www.tcmip.cn:18124", project: "ETCM"}`

### detail API（药材/成分详情）

**端点**：`GET http://www.tcmip.cn:18124/home/detail/?id={名称}&type={类型}`

| 参数 | 值 | 说明 |
|---|---|---|
| id | 药材中文名（如 `黄连`）或成分英文名（如 `berberine`） | 不支持数字 ID，不支持拼音 |
| type | `herb` 或 `ingredient` | 药材或成分 |

**响应结构**：

```json
{
  "code": 1,
  "msg": "success!!",
  "data": [
    {
      "key": "Basic Information",
      "id": "base_information",
      "value": [
        {"key": "Herb Name in Pinyin", "value": "HuangLian"},
        {"key": "Herb Name in Latin", "value": "<i>Coptis chinensis Franch.; ...</i>"},
        {"key": "Family", "value": "Ranunculaceae"},
        {"key": "Habitat", "value": "SiChuan, YunNan, HuBei"},
        {"key": "Medicinal Part", "value": "Rhizome"},
        {"key": "Database Cross References", "value": ["HIT2.0", "HERB2.0", "SymMap"],
         "linkformat": {"value": {"url": "{param}", "param": ["http://...", "http://47.92.70.12/Detail/?v=HERB002540&label=Herb", "http://www.symmap.org/detail/SMHB00186"]}}
        }
      ]
    },
    {"key": "Related Tables", "id": "related_table", "value": [...]},
    {"key": "Habitat of this Chinese medicinal materials", "id": "habitat", "value": [...]},
    {"key": "Quantitative Analysis Of Index Components", "id": "Quantitative", "value": [...]},
    {"key": "Component Target", "id": "ingredient_target", "value": [...]}
  ],
  "name": "HuangLian",
  "page_title": "Herb HuangLian Details page"
}
```

**药材 detail 返回的 11 个 section**：

| Section key | Section id | 内容 |
|---|---|---|
| Basic Information | base_information | 药名、拉丁名、科属、产地、药用部位、性味、归经、功效、PubMed、跨库引用 |
| Related Tables | related_table | 含子表：中成药、方剂、成分、靶点、疾病 |
| Habitat | habitat | 产地信息 |
| Quantitative Analysis | Quantitative | 指标成分定量分析 |
| Component Target | ingredient_target | 成分-靶点网络 |

**Related Tables 中的成分子表**（⚠️ 分页限制：仅返回前 20 行，`count` 字段报告总数）：

```json
{
  "label": "Ingredients",
  "type": "ingredient",
  "count": 53,
  "value": [
    {
      "linkformat": {"Ingredient Name": {"url": "http://www.tcmip.cn/ETCM2/front/#/Detail/ingredient/{param}", "show": true}},
      "Ingredient Name": ["berberine"],
      "Molecular Formula": "C21H18NO4+",
      "Molecular Weight": "336.120",
      "Quantitative Estimate of Drug-likeness（QED）": "0.728",
      "FDA Maximum Daily Dose (FDAMDD)": "0.061",
      "Standards": "Yes"
    }
  ]
}
```

> **重要限制**：`related_table` 的成分子表**仅返回前 20 行**（无论 count 多大），无法通过 page/page_size/limit 等参数获取更多。获取全量成分须使用 `herb_chem_phy` 端点（见下文）。

**成分 detail 返回的 4 个 section**：

| Section | 内容 |
|---|---|
| Basic Information | 成分名、分子式、分子量、SMILES、CAS 等 |
| Standards | 质量标准 |
| Related Tables | 含相关药材、靶点 |
| Component Target | 成分-靶点网络 |

### herb_chem_phy API（全量成分 + 理化属性，推荐）

**端点**：`POST http://www.tcmip.cn:18124/home/herb_chem_phy/`

**请求体**（JSON）：

```json
{
  "id": "黄连",
  "type": "herb",
  "Component_Phy_Chem": ["Molecular Weight"]
}
```

**可用 Component_Phy_Chem 值**：`Molecular Weight`、`logP`、`logS`、`Quantitative Estimate of Drug-likeness`、`F20%(20% Oral Bioavailability)`、`T1/2`

**响应结构**：

```json
{
  "code": 1,
  "msg": "success",
  "data": {
    "key": "Feature Distribution of Ingredients",
    "id": "component_chem_phy",
    "value": {
      "div": ["TACROLIMUS MONOHYDRATE", "tacrolimus", "L-Tetrandrine", ...],
      "script": ["821.490", "803.480", "622.300", ...],
      "chart_type": "histogram"
    }
  }
}
```

- `div`：成分名列表（**全量，无分页限制**）
- `script`：对应属性值列表（与 div 等长，按值排序而非按 div 排序）
- 每次请求一个 `Component_Phy_Chem` 字段，需多次请求获取多属性
- 不同字段返回的成分排序不同，需按成分名 join

**解析代码**：

```python
import requests

CHEM_PHY_FIELDS = [
    "Molecular Weight", "logP", "logS",
    "Quantitative Estimate of Drug-likeness",
    "F20%(20% Oral Bioavailability)", "T1/2",
]

result = {}  # {ingredient_name: {field: value}}
for field in CHEM_PHY_FIELDS:
    resp = requests.post(
        "http://www.tcmip.cn:18124/home/herb_chem_phy/",
        json={"id": herb_name, "type": "herb", "Component_Phy_Chem": [field]},
        timeout=30,
    )
    val = resp.json().get("data", {}).get("value", {})
    div = val.get("div", [])
    script = val.get("script", [])
    for name, value in zip(div, script):
        if name not in result:
            result[name] = {}
        result[name][field] = value
```

**实测数据**（2026-08 验证，20 味 UC 药材）：

| 指标 | 结果 |
|---||
| 药材成功率 | 20/20 (100%) |
| 成分关联总数 | 3,495 条 |
| 平均每味药 | 174.8 个成分 |
| 6 个属性覆盖率 | 100%（MW, logP, logS, QED, F20%, T1/2） |
| related_table 分子式覆盖 | 仅前 20 条/味（~11%） |

### browse API（浏览分类数据）

**端点**：`POST http://www.tcmip.cn:18124/home/browse/`

**请求体**（JSON）：

```json
{"type": "herb"}
```

type 可选值：`herb`、`ingredient`、`chinese_patent_drug`、`traditional_chinese_medicine_formula`

**响应**：返回分类树（tree_data）和过滤器（filter）。

### 其他可用端点

| 端点 | 方法 | 请求体 | 功能 |
|---|---|---|---|
| `/home/home_data/` | GET | — | 数据库统计（319 证候、48442 方剂等） |
| `/home/get_type/` | GET | — | 类型列表 |
| `/home/filter_ingredient/` | GET | — | 成分过滤器（分子量范围等） |
| `/home/logo/` | GET | — | Logo 数据 |
| `/home/bottom/` | GET | — | 底部信息 |
| `/home/herb_get_level/` | POST | `{"level": [1]}` | 药材分类层级（level 必须是 list） |
| `/home/ingredient_target/` | POST | — | 成分-靶点数据 |
| `/home/SyndromeGetLevel/` | POST | — | 证候层级 |
| `/home/SyndromeGetNum/` | POST | — | 证候数量 |
| `/home/mineral_get_level/` | POST | — | 矿物药分类 |
| `/home/finally_network/` | POST | — | 网络分析 |
| `/home/homeEsSearch/` | POST | JSON | ES 搜索（**当前返回 Search failed**，ES 可能宕机） |

### ETCM 常见陷阱

- detail API **仅支持 GET**，POST 返回 405
- browse 等 POST 端点**必须用 JSON body**，form-urlencoded 返回 500（JSONDecodeError）
- `herb_get_level` 的 `level` 参数必须是 **list 类型**（如 `[1]`），传整数返回 500（TypeError）
- detail API 不支持数字 ID（如 `id=1`），只支持中文名（药材）或英文名（成分）
- `homeEsSearch` 端口虽然响应但总是返回 `"Search failed!"`，ES 服务可能已停止
- 成分中文名（如"小檗碱"）不可查询，必须用英文名（如"berberine"）
- **related_table 成分子表有 20 行硬限制**：无论 count 多少，只返回前 20 行，无法通过分页参数获取更多。获取全量成分必须使用 `herb_chem_phy` 端点
- `herb_chem_phy` 中 `Component_Phy_Chem` 的可选值需从 detail 响应的 `component_chem_phy` section 的 `data > data > Component_Phy_Chem > front > value` 字段获取（共 6 个）
- `herb_chem_phy` 不同字段的响应中成分排序不同，须按成分名做 join
- `Molecular Formula` 不是 `Component_Phy_Chem` 的可用字段（请求返回空响应），分子式只能从 `related_table` 前 20 条或成分 detail 获取

## BATMAN-TCM 2.0

### 基础信息

- 地址：`http://bionet.ncpsb.org.cn/batman-tcm/`（**必须 HTTP**）
- HTTPS 证书损坏：`SSLError: UNEXPECTED_EOF_WHILE_READING`
- 前端：Vue SPA（webpack 打包）
- **无 REST API**：前端 JS 中未发现任何 API 调用端点

### 全局变量

从首页 HTML 提取：

```javascript
window.downloadApi = "http://batman2.cloudna.cn/downloadApi"
window.spaceIPRecords = "http://111.198.139.89/spaceIPRecords"
```

- `downloadApi`：指向另一个 Vue SPA（非 API 端点）
- `spaceIPRecords`：返回 404

### Vue 路由（非 API 路由）

`/home`、`/search`、`/browse`、`/download`、`/statistics`、`/Api`、`/document`、`/communication`、`/geneFilter`、`/geneRetrieve`、`/tcmRetrieve`、`/ingredientView`、`/resultView`、`/heatMapView`、`/GSLabelView`、`/net`

### BATMAN-TCM 结论

- **无法通过 API 程序化获取数据**
- 如需 BATMAN-TCM 数据，需通过浏览器手动操作或爬取渲染后的页面
- downloadApi 提供文件下载功能（需浏览器访问）

## PubChem REST API

### 基础信息

- 基础 URL：`https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/`
- 延迟：800-1500ms
- 无速率限制头（X-Throttle-Global 未返回）

### 按 CID 查询（支持批量）

**端点**：

```
GET https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{CID1,CID2,...}/property/MolecularFormula,MolecularWeight,CanonicalSMILES,InChIKey/JSON
```

- 批量查询：CID 用逗号分隔
- 实测 50 个 CID 批量查询 ~1s
- 返回格式：

```json
{
  "PropertyTable": {
    "Properties": [
      {"CID": 2244, "MolecularFormula": "C9H8O4", "MolecularWeight": "180.16", "CanonicalSMILES": "CC(=O)Oc1ccccc1C(=O)O", "InChIKey": "BSYNRYMUTXBXLQ-UHFFFAOYSA-N"}
    ]
  }
}
```

### 按名称查询（仅单个）

**端点**：

```
GET https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/property/MolecularFormula,MolecularWeight,CanonicalSMILES,InChIKey/JSON
```

- **不能请求 CAS 字段**（返回 400 Bad Request）
- **不支持批量名称查询**：URL 逗号分隔返回 404，POST JSON 返回 400
- **⚠️ 实测（2026-08）**：返回的 SMILES 字段名是 `ConnectivitySMILES` 而非文档写的 `CanonicalSMILES`，解析需两者兼容：

```python
smiles = p.get('CanonicalSMILES', '') or p.get('ConnectivitySMILES', '')
```

### 按名称转 CID（可并发，推荐）

**端点**：

```
GET https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{name}/cids/JSON
```

- 返回 `{"IdentifierList": {"CID": [2244, ...]}}`，取第一个 CID 即可
- **支持并发**：实测 8 线程 200 个名称 ~65s（单线程 ~2s/个）
- 命中率约 63%（200 个样本命中 126）；部分成分名含盐、水合物、构型描述导致无匹配

**推荐流程（名称→CID→批量属性）**：
1. 并发 `name/{name}/cids/JSON` 获取 CID（8 线程）
2. 对命中 CID 批量查询属性：`cid/{CID1,CID2,...}/property/MolecularFormula,MolecularWeight,CanonicalSMILES,InChIKey/JSON`（50 个/批，~1s/批）
3. 实测 20,275 个 ETCM 成分名：7,605 个命中 CID（37.5%），其中 7,419 个含 SMILES（36.6%）
4. 大批量必须断点续跑：每批保存 JSON 缓存，自动跳过已完成名称，防止超时中断丢失进度

### 按 SMILES 查询

**端点**：

```
GET https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/{SMILES}/property/MolecularFormula,MolecularWeight,CanonicalSMILES,InChIKey/JSON
```

- SMILES 需 URL 编码

### PubChem 批量策略

| 场景 | 方法 | 效率 |
|---|---|---|
| 有 CID | 按 50 个一批查询 | 最高 |
| 有名称无 CID | 逐个查询 | 低（无法批量） |
| 有 SMILES | 逐个查询 | 低 |

### 解析代码

```python
import requests

# 批量 CID 查询
cids = ",".join([str(c) for c in cid_list[:50]])
resp = requests.get(
    f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cids}/property/MolecularFormula,MolecularWeight,CanonicalSMILES,InChIKey/JSON",
    timeout=30
)
data = resp.json()
for prop in data.get("PropertyTable", {}).get("Properties", []):
    cid = prop["CID"]
    smiles = prop.get("CanonicalSMILES", "")
    inchikey = prop.get("InChIKey", "")
    formula = prop.get("MolecularFormula", "")
    mw = prop.get("MolecularWeight", "")
```

### POST 批量属性查询（100 CID/批，分子库构建实测）

比 GET 逗号分隔批量（50/批）更大，POST body 传 JSON 数组：

```python
import requests

H = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
resp = requests.post(
    'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/property/'
    'CanonicalSMILES,IsomericSMILES,MolecularFormula,MolecularWeight/JSON',
    json={'ids': [5280343, 5281605, 5280445]},  # 最多 100 个 CID
    headers=H, timeout=60)
props = {}
for prop in resp.json().get('PropertyTable', {}).get('Properties', []):
    props[str(prop['CID'])] = prop  # 注意转 str 键，与库内 CID 文本统一
```

**要点（2026-09 分子库构建实测）**：
- 100 CID/批约 1-2s，比逐条 GET 快 30 倍以上；1638 条库（约 1500 个去重 CID）全量验证仅需几十秒
- **503 限流处理**：连续 503 说明 IP 被限流（通常按小时重置），应跳过该批而非重试或退回逐条——退回逐条会形成重试风暴延长限流
- GET 逗号批量（上表）返回字段为 `CanonicalSMILES`；POST 批量两种 SMILES 字段都可能返回，解析时 `CanonicalSMILES` / `IsomericSMILES` / `ConnectivitySMILES` 需兼容
- CAS 号不在 property 返回中，需走 `/cid/{cid}/synonyms/JSON` 从同义词中用正则 `^\d{2,7}-\d{2}-\d$` 筛选（逐条限速，不适合全量）

## SymMap v2.0

### 基础信息

- 地址：`http://www.symmap.org/`
- 前端技术：jQuery（无 Vue/React），无 REST API 端点
- 数据获取方式：全量 xlsx 文件下载

### 全量数据文件

**下载页面**：`http://www.symmap.org/download/`

**文件目录**：`http://www.symmap.org/static/download/V2.0/`

| 文件名 | 内容 | 记录数 |
|---|---|---|
| `SMHB file.xlsx` | 药材-证候关联（SMHB = SymMap Herb） | 499 味药 |
| `SMHB key.xlsx` | 药材文件字段说明 | — |
| `SMTS file.xlsx` | 中医证候（SMTS = SymMap TCM Syndrome） | 1,717 个证候 |
| `SMMS file.xlsx` | 现代医学症状（SMMS = SymMap Modern Symptoms） | — |
| `SMIT file.xlsx` | 成分-靶点关联（SMIT = SymMap Ingredient-Target） | 19,595 成分 × 4,302 靶点 |
| `SMIT key.xlsx` | 成分-靶点字段说明 | — |
| `SMTT file.xlsx` | 证候-靶点关联 | — |

### 下载代码

```python
import requests

base_url = "http://www.symmap.org/static/download/V2.0/"
files = [
    "SMHB file.xlsx", "SMHB key.xlsx",
    "SMTS file.xlsx", "SMMS file.xlsx",
    "SMIT file.xlsx", "SMIT key.xlsx",
    "SMTT file.xlsx",
]

for fname in files:
    url = base_url + fname
    resp = requests.get(url, timeout=60)
    if resp.status_code == 200:
        with open(f"symmap_{fname.replace(' ', '_')}", "wb") as f:
            f.write(resp.content)
        print(f"Downloaded: {fname} ({len(resp.content)} bytes)")
    else:
        print(f"Failed: {fname} (HTTP {resp.status_code})")
```

### ID 格式

| 实体 | ID 前缀 | 示例 |
|---|---|---|
| 药材 | SMHB | SMHB00186 |
| 成分 | SMIT | SMIT00001 |
| 中医证候 | SMTS | SMTS00001 |
| 现代医学症状 | SMMS | SMMS00001 |
| 靶点 | SMTR | SMTR00001 |

### SymMap 常见陷阱

- 无 API 端点，无法实时查询，只能全量下载后本地处理
- 文件名含空格（如 `SMHB file.xlsx`），URL 中空格保留原样即可（requests 库自动处理）
- 数据覆盖面广但字段不如 HERB/TCMSP 详细（无 OB/DL 等 ADME 参数）
- xlsx 文件需用 `openpyxl` 或 `pandas` 读取

## ChEMBL

### 基础信息

- 地址：`https://www.ebi.ac.uk/chembl/`
- API 根路径：`https://www.ebi.ac.uk/chembl/api/data/`
- 响应格式：JSON（添加 `.json` 后缀或设置 `Accept: application/json`）
- ⚠️ 延迟较高（~30-45s），需设置 `timeout=60`

### 数据库状态

```
GET https://www.ebi.ac.uk/chembl/api/data/status.json
```

返回示例：

```json
{
  "chembl_db_version": "ChEMBL_37",
  "num_targets": 17435,
  "num_activities": 24527044,
  "num_molecules": 2312995,
  "num_assays": 75633,
  "num_documents": 87842
}
```

### 按化合物查生物活性

```
GET https://www.ebi.ac.uk/chembl/api/data/activity.json?molecule_chembl_id={CHEMBL_ID}
```

返回 JSON，`activities` 数组中每条记录含：

| 字段 | 说明 |
|---|---|
| `activity_id` | 活性记录 ID |
| `assay_chembl_id` | 实验批次 ID |
| `molecule_chembl_id` | 化合物 ID |
| `target_chembl_id` | 靶点 ID |
| `standard_type` | 活性类型（IC50, Ki, Kd, EC50 等） |
| `standard_relation` | 关系（`=`, `>`, `<`） |
| `standard_value` | 活性值（字符串，需转 float） |
| `standard_units` | 单位（通常 nM） |
| `pchembl_value` | -log10 标准化值 |

### 按靶点查化合物

```
GET https://www.ebi.ac.uk/chembl/api/data/activity.json?target_chembl_id={TARGET_ID}
```

### 分页

返回 JSON 含 `_meta` 对象：

```json
{
  "_meta": {
    "total_count": 100,
    "limit": 20,
    "offset": 0
  }
}
```

- 默认每页 20 条，通过 `limit` 参数调整（最大 100）
- 使用 `offset` 翻页：`?offset=20&limit=20`

### 解析代码

```python
import requests

def fetch_chembl_activities(molecule_chembl_id, limit=100, offset=0):
    url = f"https://www.ebi.ac.uk/chembl/api/data/activity.json"
    params = {
        "molecule_chembl_id": molecule_chembl_id,
        "limit": limit,
        "offset": offset,
    }
    resp = requests.get(url, params=params, timeout=60,
                        headers={"Accept": "application/json"})
    data = resp.json()
    activities = data.get("activities", [])
    total = data.get("_meta", {}).get("total_count", 0)
    return activities, total

# 示例：获取 berberine (CHEMBL258447) 的全部活性
activities, total = fetch_chembl_activities("CHEMBL258447")
for act in activities:
    target = act.get("target_chembl_id", "")
    val = float(act.get("standard_value", 0))
    typ = act.get("standard_type", "")
    units = act.get("standard_units", "")
    print(f"{target}: {typ} = {val} {units}")
```

### ChEMBL 常见陷阱

- **不支持按名称搜索**：`molecule_synonyms` 参数已废弃，返回 400 错误
- **延迟高**：每次请求 ~30-45s，需设置 `timeout=60` 以上
- `standard_value` 为字符串，需转为 float
- 同一化合物-靶点对可能有多个活性记录（不同实验来源），需去重或取最优
- ChEMBL ID 格式：化合物 `CHEMBL` + 数字，靶点 `CHEMBL` + 数字
- 批量查询需分页，`total_count` 可能很大

## KEGG

### 基础信息

- REST API：`https://rest.kegg.jp/`
- 响应格式：纯文本（非 JSON），用 `\n` 分隔行，`\t` 分隔字段
- 响应速度快（~1-3s）

### 化合物搜索

```
GET https://rest.kegg.jp/find/compound/{关键词}
```

返回示例：

```
cpd:C00757\tberberine
cpd:C00398\tTryptamine
```

### 化合物详情

```
GET https://rest.kegg.jp/get/{compound_id}
```

如 `GET https://rest.kegg.jp/get/C00398`，返回纯文本：

```
ENTRY       C00398                      Compound
NAME        Tryptamine
FORMULA     C10H12N2
EXACT_MASS 160.1000
MOL_WEIGHT 160.2156
REACTION    R00711  R00712  ...
PATHWAY     map00380  Tryptophan metabolism
            map00970  Aminoacyl-tRNA biosynthesis
ENZYME      1.4.3.4       1.4.3.21      ...
DBLINKS     PubChem: 5497
            ChEBI: 16793
            CAS: 61-54-5
```

### 通路查询

```
GET https://rest.kegg.jp/find/pathway/{关键词}
```

### 疾病查询

```
GET https://rest.kegg.jp/find/disease/{关键词}
```

实测 `find/disease/colitis` 返回：

```
H01466\tUlcerative colitis
```

### 解析代码

```python
import requests

def kegg_find_compound(keyword):
    resp = requests.get(
        f"https://rest.kegg.jp/find/compound/{keyword}",
        timeout=30
    )
    results = []
    for line in resp.text.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            results.append({"id": parts[0], "name": parts[1]})
    return results

def kegg_get_compound(compound_id):
    resp = requests.get(
        f"https://rest.kegg.jp/get/{compound_id}",
        timeout=30
    )
    text = resp.text
    # 解析纯文本
    data = {}
    for line in text.split("\n"):
        if line.startswith("ENTRY") or line.startswith("  "):
            continue
        if "\t" in line:
            parts = line.split("\t", 1)
            key = parts[0].strip()
            val = parts[1].strip() if len(parts) > 1 else ""
            data[key] = val
        elif " " in line:
            parts = line.split(None, 1)
            key = parts[0]
            val = parts[1] if len(parts) > 1 else ""
            data[key] = val
    return data
```

### KEGG 常见陷阱

- 响应为纯文本，非 JSON，需按行解析
- `find/pathway/colitis` 返回空（需用通路 ID 或其他关键词查关联通路）
- 化合物 ID 格式为 `Cxxxxx`，通路 ID 格式为 `path:hsa#####`，疾病 ID 格式为 `Hxxxxx`
- KEGG Compound 与 PubChem CID 可通过详情中的 `DBLINKS PubChem` 字段交叉引用
- 搜索结果中 `cpd:` 前缀需去掉后才是正式 ID

## UniProt

### 基础信息

- API 根路径：`https://rest.uniprot.org/uniprotkb/`
- 响应格式：JSON（默认）
- 搜索语法支持复杂查询（AND, OR, NOT）

### 按基因名搜索蛋白

```
GET https://rest.uniprot.org/uniprotkb/search?query=gene:{基因名}&format=json&size=25
```

实测 `query=gene:TNF&format=json` 返回 TNF 相关蛋白条目。

### 按 UniProt ID 获取详情

```
GET https://rest.uniprot.org/uniprotkb/{UNIPROT_ID}.json
```

如 `GET https://rest.uniprot.org/uniprotkb/P01375.json` 返回 TNF 蛋白完整注释。

### 批量查询

```
POST https://rest.uniprot.org/uniprotkb/accession
Content-Type: application/x-www-form-urlencoded
Body: ids={ID1},{ID2},...&format=json
```

### 搜索语法

| 语法 | 含义 | 示例 |
|---|---|---|
| `gene:{name}` | 按基因名 | `gene:TNF` |
| `organism:{id}` | 按物种 Taxonomy ID | `organism:9606`（人类） |
| `organism:{scientific_name}` | 按物种学名 | `organism:"Homo sapiens"` |
| `accession:{id}` | 按蛋白 ID | `accession:P01375` |
| `reviewed:{yes/no}` | 是否经人工审阅 | `reviewed:yes` |
| `AND` / `OR` / `NOT` | 布尔逻辑 | `gene:TNF AND organism:9606` |

### 分页

- 默认每页 25 条，`size` 参数最大 500
- 使用 `cursor` 参数翻页（返回 JSON 中的 `next` 字段含 cursor 值）

### 解析代码

```python
import requests

def uniprot_search(gene_name, organism_id=9606, reviewed_only=True):
    query = f"gene:{gene_name} AND organism:{organism_id}"
    if reviewed_only:
        query += " AND reviewed:yes"
    resp = requests.get(
        "https://rest.uniprot.org/uniprotkb/search",
        params={"query": query, "format": "json", "size": 500},
        timeout=30,
    )
    data = resp.json()
    results = data.get("results", [])
    cursor = data.get("next", "").split("cursor=")[-1] if data.get("next") else None
    return results, cursor

def uniprot_batch(accessions):
    if isinstance(accessions, list):
        accession_str = ",".join(accessions)
    else:
        accession_str = accessions
    resp = requests.post(
        "https://rest.uniprot.org/uniprotkb/accession",
        data={"ids": accession_str, "format": "json"},
        timeout=30,
    )
    return resp.json().get("results", [])
```

### UniProt 常见陷阱

- 搜索结果默认每页 25 条，需设置 `size` 参数
- 按基因名搜索可能返回多个蛋白变体，需用 `organism:9606` 过滤
- `accession` 字段是蛋白唯一标识（如 `P01375`），`id` 字段是易记名（如 `TNFA_HUMAN`）
- `reviewed:yes` 可过滤经 Swiss-Prot 人工审阅的条目（质量更高）
- 批量查询单次最多 1000 个 accession

## STRING

### 基础信息

- API 根路径：`https://string-db.org/api/json/`
- 响应格式：JSON
- 常用物种 ID：人类 `9606`，小鼠 `10090`，大鼠 `10116`

### 蛋白相互作用网络

```
GET https://string-db.org/api/json/network?identifiers={蛋白列表}&species={物种ID}
```

- `identifiers`：蛋白名/基因名，多个用 `%0d`（URL 编码的换行符）分隔
- `species`：物种 NCBI Taxonomy ID
- `required_score`：可选，置信度阈值（0-1000，默认 400）
- 返回 JSON 数组，每条边含：

| 字段 | 说明 |
|---|---|
| `stringId_A` | 蛋白 A 的 STRING ID |
| `stringId_B` | 蛋白 B 的 STRING ID |
| `preferredName_A` | 蛋白 A 的常用名 |
| `preferredName_B` | 蛋白 B 的常用名 |
| `score` | 置信度（0-1） |
| `ncbiTaxonId` | 物种 ID |

### 交互伙伴查询

```
GET https://string-db.org/api/json/interaction_partners?identifiers={基因名}&species={物种ID}
```

返回与指定蛋白相互作用的蛋白列表。

### 功能富集分析

```
GET https://string-db.org/api/json/enrichment?identifiers={基因名列表}&species={物种ID}
```

返回 KEGG 通路、GO 术语等富集结果，每条含 `category`、`term`、`p_value`、`fdr`。

### 网络图可视化

```
GET https://string-db.org/api/image/network?identifiers={基因名列表}&species={物种ID}
```

返回 PNG 图片（非 JSON）。

### 解析代码

```python
import requests

def string_get_network(gene_list, species=9606, required_score=400):
    identifiers = "%0d".join(gene_list)
    resp = requests.get(
        "https://string-db.org/api/json/network",
        params={
            "identifiers": identifiers,
            "species": species,
            "required_score": required_score,
        },
        timeout=60,
    )
    edges = resp.json()
    # 构建 PPI 网络
    network = {}
    for edge in edges:
        a = edge["preferredName_A"]
        b = edge["preferredName_B"]
        score = edge["score"]
        network.setdefault(a, {})[b] = score
        network.setdefault(b, {})[a] = score
    return network

def string_get_interaction_partners(gene_name, species=9606):
    resp = requests.get(
        "https://string-db.org/api/json/interaction_partners",
        params={"identifiers": gene_name, "species": species},
        timeout=60,
    )
    return resp.json()

def string_get_enrichment(gene_list, species=9606):
    identifiers = "%0d".join(gene_list)
    resp = requests.get(
        "https://string-db.org/api/json/enrichment",
        params={"identifiers": identifiers, "species": species},
        timeout=60,
    )
    return resp.json()
```

### STRING 常见陷阱

- `identifiers` 多个蛋白名用 `%0d`（URL 编码的换行符）分隔，**不是逗号**
- `score` 范围 0-1，建议 ≥0.4（中等置信度）或 ≥0.7（高置信度）
- 首次请求可能较慢（~5-10s）
- 返回网络可能很大，可通过 `limit` 参数控制边数
- 基因名需为标准符号（如 `TNF`、`IL6`），非全名
- `required_score` 参数用 0-1000 整数（非 0-1 小数）

## NCBI E-utilities

### 基础信息

- 端点：`https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`
- 统一入口，覆盖 Gene、GEO、SRA、dbSNP、ClinVar 等数据库
- 速率限制：无 API Key 时 3 次/秒，有 API Key 时 10 次/秒

### esearch（搜索）

```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi
    ?db={数据库}&term={搜索词}&retmax={数量}&retmode=json
```

| db 值 | 数据库 | term 示例 |
|---|---|---|
| `gene` | NCBI Gene | `TNF[Gene Name] AND Homo sapiens[Organism]` |
| `gds` | GEO DataSets | `ulcerative colitis[All Fields]` |
| `sra` | SRA | `ulcerative colitis RNA-seq` |
| `snp` | dbSNP | `TNF[Gene Name] AND Homo sapiens[Organism]` |
| `clinvar` | ClinVar | `TNF[Gene] AND pathogenic[Clinical_Significance]` |

**响应**：

```json
{
  "esearchresult": {
    "count": "1",
    "idlist": ["7124"]
  }
}
```

### esummary（摘要）

```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi
    ?db={数据库}&id={ID}&retmode=json
```

> ⚠️ dbSNP 的 esummary 需用 esearch 返回的**数字 UID**（如 `2533253306`），不能直接用 rs ID（如 `rs1800629` 会报 `Invalid uid`）。

### efetch（完整记录）

```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi
    ?db={数据库}&id={ID}&retmode=xml
```

### 解析代码

```python
import requests
import time

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

def esearch(db, term, retmax=20):
    resp = requests.get(f"{EUTILS}/esearch.fcgi", params={
        "db": db, "term": term, "retmax": retmax, "retmode": "json"
    }, timeout=30)
    result = resp.json().get("esearchresult", {})
    return result.get("idlist", []), int(result.get("count", 0))

def esummary(db, uid):
    resp = requests.get(f"{EUTILS}/esummary.fcgi", params={
        "db": db, "id": uid, "retmode": "json"
    }, timeout=30)
    result = resp.json().get("result", {})
    return result.get(uid, {})

# 示例：搜索 TNF 基因
ids, total = esearch("gene", "TNF[Gene Name] AND Homo sapiens[Organism]")
if ids:
    gene_info = esummary("gene", ids[0])
    print(gene_info.get("Name", ""), gene_info.get("Description", ""))

# 示例：搜索 UC 相关 GEO 数据集
ids, total = esearch("gds", "ulcerative colitis[All Fields]")
print(f"Found {total} GEO datasets")

# 示例：搜索 dbSNP 变异
ids, total = esearch("snp", "TNF[Gene Name] AND Homo sapiens[Organism]")
if ids:
    snp_info = esummary("snp", ids[0])  # 用数字 UID，非 rs ID
```

### NCBI eutils 常见陷阱

- 无 API Key 时限 3 次/秒，建议加 `api_key` 参数提升至 10 次/秒
- dbSNP esummary 必须用数字 UID，不能用 rs ID
- GEO 搜索用 `gds` 数据库（GDS = GEO DataSets）
- term 语法支持字段限定符（`[Gene Name]`、`[Organism]`、`[All Fields]`）
- 批量查询建议每批 ≤100 个 ID

## Ensembl REST API

### 基础信息

- 根路径：`https://rest.ensembl.org/`
- 响应格式：JSON（设置 `Accept: application/json`）
- ⚠️ 延迟较高（6-25s），需设置 `timeout=30` 以上

### 基因查询

```
GET https://rest.ensembl.org/lookup/symbol/homo_sapiens/{基因符号}
Accept: application/json
```

返回：

```json
{
  "id": "ENSG00000232810",
  "display_name": "TNF",
  "description": "tumor necrosis factor",
  "biotype": "protein_coding",
  "seq_region_name": "6",
  "start": 31575558,
  "end": 31578336,
  "strand": 1,
  "assembly_name": "GRCh38"
}
```

### 变异查询

```
GET https://rest.ensembl.org/variation/human/{rsID}?content-type=application/json
```

返回变异位置、等位基因、临床意义、最严重后果等。

### Ensembl 常见陷阱

- 延迟较高（6-25s），不适合大批量实时查询
- 基因符号需标准 HGNC 符号（如 `TNF`，非全名）
- 变异查询用 `variation/human/{rsID}` 路径

## GDC/TCGA REST API

### 基础信息

- 根路径：`https://api.gdc.cancer.gov/`
- 响应格式：JSON
- 覆盖 TCGA、TARGET、CGCI 等癌症基因组项目

### 查询项目

```
GET https://api.gdc.cancer.gov/projects?size={N}
```

### 查询案例

```
GET https://api.gdc.cancer.gov/cases?size={N}&fields=case_id,submitter_id,disease_type
```

### 查询文件（GET + filters）

```
GET https://api.gdc.cancer.gov/files?filters={JSON字符串}&fields={字段}&format=JSON&size={N}
```

filters 格式：

```json
{
  "op": "and",
  "content": [
    {"op": "in", "content": {"field": "access", "value": ["open"]}},
    {"op": "in", "content": {"field": "data_category", "value": ["Transcriptome Profiling"]}}
  ]
}
```

> ⚠️ **POST /files 返回 500**，必须用 GET + `filters` URL 参数（JSON 字符串 URL 编码）。

### 解析代码

```python
import requests
import json

def gdc_search_files(data_category="Transcriptome Profiling", project_id=None, size=10):
    filters = {
        "op": "and",
        "content": [
            {"op": "in", "content": {"field": "access", "value": ["open"]}},
            {"op": "in", "content": {"field": "data_category", "value": [data_category]}}
        ]
    }
    if project_id:
        filters["content"].append(
            {"op": "in", "content": {"field": "cases.project.project_id", "value": [project_id]}}
        )
    params = {
        "filters": json.dumps(filters),
        "fields": "file_id,file_name,data_category,data_type",
        "format": "JSON",
        "size": size,
    }
    resp = requests.get("https://api.gdc.cancer.gov/files", params=params, timeout=30)
    return resp.json().get("data", {}).get("hits", [])
```

### GDC 常见陷阱

- POST /files 返回 500，必须用 GET + filters URL 参数
- `filters` 参数值是 JSON 字符串，需 `json.dumps()` + URL 编码
- `fields` 用逗号分隔（如 `file_id,file_name,data_category`）
- 开放数据用 `access=open` 过滤

## PDB (RCSB) REST API v2

### 基础信息

- 搜索端点：`https://search.rcsb.org/rcsbsearch/v2/query`
- 响应格式：JSON
- 方法：POST

### 全文搜索

```python
import requests

query = {
    "query": {
        "type": "group",
        "logical_operator": "and",
        "nodes": [
            {
                "type": "terminal",
                "service": "full_text",
                "parameters": {"value": "tumor necrosis factor"}
            }
        ]
    },
    "return_type": "entry",
    "request_options": {"paginate": {"start": 0, "rows": 10}}
}

resp = requests.post(
    "https://search.rcsb.org/rcsbsearch/v2/query",
    json=query,
    headers={"Content-Type": "application/json"},
    timeout=30
)
data = resp.json()
# data["result_set"] = [{"identifier": "1TNR", "score": 1.0}, ...]
```

### 结构详情

```
GET https://data.rcsb.org/rest/v1/core/entry/{PDB_ID}
```

### PDB 常见陷阱

- v2 搜索语法要求 `type: "group"` + `nodes` 数组结构
- `service` 可选 `full_text`、`sequence`、`structure`
- `return_type` 可选 `entry`、`polymer_entity`、`nonpolymer_entity`

## AlphaFold DB

### 基础信息

- 端点：`https://alphafold.ebi.ac.uk/api/prediction/{UniProt_ID}`
- 响应格式：JSON
- 延迟：~1.2s

### 查询蛋白预测结构

```python
import requests

resp = requests.get("https://alphafold.ebi.ac.uk/api/prediction/P01375", timeout=30)
data = resp.json()[0]
# data["globalMetricValue"] = 84.56 (pLDDT 均值)
# data["modelEntityId"] = "AF-P01375-F1"
# data["sequence"] = "MSTESMIRDVELAEEALPKKTGGPQ..."
```

### AlphaFold 常见陷阱

- 返回 JSON 数组（非单个对象），取 `[0]`
- `globalMetricValue` 是 pLDDT 均分（0-100），>70 为可信
- 需用 UniProt accession（如 `P01375`），非基因名

## InterPro / Pfam

### 基础信息

- 根路径：`https://www.ebi.ac.uk/interpro/api/`
- 响应格式：JSON
- Pfam 是 InterPro 的子数据库

### 按蛋白查结构域

```
GET https://www.ebi.ac.uk/interpro/api/protein/uniprot/{UniProt_ID}
Accept: application/json
```

### 按 Pfam ID 查蛋白家族

```
GET https://www.ebi.ac.uk/interpro/api/entry/pfam/{Pfam_ID}
Accept: application/json
```

### InterPro 常见陷阱

- 需设置 `Accept: application/json` 头
- Pfam ID 格式为 `PFxxxxx`（如 `PF00001`）
- 响应可能较大，可通过 `page_size` 参数分页

## Reactome REST API

### 基础信息

- 内容服务：`https://reactome.org/ContentService/`
- 数据服务：`https://reactome.org/DataService/`
- 响应格式：JSON

### 通路搜索

```
GET https://reactome.org/ContentService/search/query?query={关键词}&species=Homo+sapiens&types=Pathway
Accept: application/json
```

### 通路详情

```
GET https://reactome.org/ContentService/data/query/{Pathway_ID}
Accept: application/json
```

Pathway ID 格式为 `R-HSA-xxxxx`（如 `R-HSA-75893` TNF signaling）。

### Reactome 常见陷阱

- 需设置 `Accept: application/json` 头
- `species` 参数用 `+` 替代空格（如 `Homo+sapiens`）
- `types` 参数限定返回类型（`Pathway`、`Reaction`、`Protein`）

## g:Profiler

### 基础信息

- 端点：`https://biit.cs.ut.ee/gprofiler/api/gost/profile/`
- 方法：POST
- 响应格式：JSON
- 延迟：~4s

### 功能富集分析

```python
import requests

resp = requests.post(
    "https://biit.cs.ut.ee/gprofiler/api/gost/profile/",
    json={
        "organism": "hsapiens",
        "query": ["TNF", "IL6", "IL10", "IL1B", "VEGFA", "NFKB1"]
    },
    timeout=60
)
results = resp.json().get("result", [])
for r in results:
    print(f"{r['description']} (p={r['p_value']:.2e}, FDR={r.get('fdr', 'N/A')})")
```

### g:Profiler 常见陷阱

- `organism` 用物种缩写（`hsapiens`、`mmusculus`）
- `query` 是基因名列表（非字符串，是 JSON 数组）
- 返回的 `native` 字段是通路 ID（如 `WP:WP5353`、`GO:0006954`）

## Enrichr

### 基础信息

- 端点：`https://maayanlab.cloud/Enrichr/`
- 响应格式：JSON
- 延迟：~1s

### 第一步：上传基因列表

> ⚠️ 必须用 **multipart form data** 方式上传

```python
import requests

gene_list = "TNF\nIL6\nIL10\nIL1B\nVEGFA\nNFKB1"
resp = requests.post(
    "https://maayanlab.cloud/Enrichr/addList",
    files={"list": ("gene_list.txt", gene_list), "description": (None, "test gene list")}
)
data = resp.json()
user_list_id = data["userListId"]  # 136670888
```

### 第二步：查询富集结果

```
GET https://maayanlab.cloud/Enrichr/enrich?userListId={ID}&backgroundType={库类型}
```

常用 `backgroundType`：

| 值 | 内容 |
|---|---|
| `KEGG_2021_Human` | KEGG 通路 |
| `GO_Biological_Process_2023` | GO 生物过程 |
| `Reactome_2022` | Reactome 通路 |
| `WikiPathway_2023_Human` | WikiPathways |

### Enrichr 常见陷阱

- **必须用 multipart form data**，不能用 JSON body 或 form-urlencoded
- `files` 参数中文件名必须有（如 `"gene_list.txt"`）
- 基因列表用 `\n` 分隔（每行一个基因名）

## JASPAR

### 基础信息

- 端点：`https://jaspar.elixir.no/api/v1/`
- 响应格式：JSON
- 延迟：7-13s（较慢）

### 搜索转录因子矩阵

```
GET https://jaspar.elixir.no/api/v1/matrix/?search={关键词}&page_size={N}
Accept: application/json
```

返回：

```json
{
  "count": 9,
  "results": [
    {
      "matrix_id": "MA0105.1",
      "name": "NFKB1",
      "collection": "CORE",
      "sequence_logo": "https://jaspar.elixir.no/static/logos/svg/MA0105.1.svg"
    }
  ]
}
```

### JASPAR 常见陷阱

- 延迟较高（7-13s），不适合大批量实时查询
- `matrix_id` 格式为 `MAxxxx.x`（如 `MA0105.1`）
- `search` 参数匹配 TF 名称，不区分大小写

## dbSNP (NCBI eutils)

### 搜索变异

```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=snp&term={搜索词}&retmode=json
```

### 获取变异详情

```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=snp&id={数字UID}&retmode=json
```

> ⚠️ 必须用 esearch 返回的**数字 UID**，不能用 rs ID。

### 解析代码

```python
# 搜索 TNF 基因的 SNP
ids, total = esearch("snp", "TNF[Gene Name] AND Homo sapiens[Organism]")
if ids:
    snp = esummary("snp", ids[0])
    # snp 含 global_population, global_mafs, clinical_significance 等
```

## gnomAD

### 基础信息

- 端点：`https://gnomad.broadinstitute.org/api`
- 方法：POST（GraphQL）
- 响应格式：JSON
- 延迟：1-2s

### 变异频率查询

```python
import requests

query = """
{
  variant(dataset: gnomad_r4, variantId: "1-55042662-G-A") {
    genome { ac ac_hom }
    exome { ac ac_hom }
  }
}
"""
resp = requests.post(
    "https://gnomad.broadinstitute.org/api",
    json={"query": query},
    timeout=30
)
data = resp.json()["data"]["variant"]
```

### gnomAD 常见陷阱

- `variantId` 格式为 `染色体-位置-参考-变异`（如 `1-55042662-G-A`），不是 rs ID
- `dataset` 可选 `gnomad_r4`（v4）或 `gnomad_r3`（v3）
- 变异不存在时返回 `"variant": null`，不是报错
- 支持按基因查询：`gene(gene_symbol: "TNF")`

## GWAS Catalog REST API

### 基础信息

- 根路径：`https://www.ebi.ac.uk/gwas/rest/api/`
- 响应格式：JSON
- 延迟：1.7-6s

### 按关键词搜索研究

```
GET https://www.ebi.ac.uk/gwas/rest/api/studies/search?query={关键词}
Accept: application/json
```

### 按基因搜索关联

```
GET https://www.ebi.ac.uk/gwas/rest/api/associations?gene={基因名}&size={N}
Accept: application/json
```

### GWAS Catalog 常见陷阱

- 需设置 `Accept: application/json` 头
- 搜索可能较慢（~6s）
- 返回 Spring Data REST 格式（含 `_embedded` 和 `_links`）

## ClinVar (NCBI eutils)

### 搜索临床变异

```
GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=clinvar&term={搜索词}&retmode=json
```

term 示例：`TNF[Gene] AND pathogenic[Clinical_Significance]`

### 解析代码

```python
ids, total = esearch("clinvar", "TNF[Gene] AND pathogenic[Clinical_Significance]")
for uid in ids[:5]:
    cv = esummary("clinvar", uid)
    # cv 含 clinical_significance, variation_set, gene 等
```

## ENCODE REST API

### 基础信息

- 根路径：`https://www.encodeproject.org/`
- 响应格式：JSON
- 延迟：~3s

### 实验搜索

```
GET https://www.encodeproject.org/search/?type=Experiment&assay_title={实验类型}&format=json&limit={N}
Accept: application/json
```

常用 `assay_title`：`ATAC-seq`、`ChIP-seq`、`RNA-seq`、`Hi-C`、`eCLIP`。

### ENCODE 常见陷阱

- 需设置 `Accept: application/json` 头
- `format=json` 参数是必需的
- 返回 `@graph` 数组（非直接列表）
- `limit` 控制返回条数

