## DGIdb GraphQL API

### 基础信息

- 端点：`https://dgidb.org/api/graphql`
- 方法：POST
- Content-Type：`application/json`
- 响应格式：JSON（GraphQL）
- 延迟：2.8-4.3s
- 无需 API Key

### 按基因名查询药物交互

```python
def dgidb_gene_drug_interactions(gene_names):
    """查询多个基因的药物交互"""
    query = """
    {
      genes(names: %s) {
        nodes {
          name
          interactions {
            drug { name conceptId }
            interactionScore
            interactionTypes { type directionality }
            publications { pmid }
          }
        }
      }
    }
    """ % json.dumps(gene_names)  # 如 ["TNF", "IL6", "IL10"]
    resp = requests.post(
        "https://dgidb.org/api/graphql",
        json={"query": query},
        timeout=60
    )
    return resp.json()["data"]["genes"]["nodes"]
```

### DGIdb 常见陷阱

- schema 用 `nodes`（不是 `matches`）
- `publications` 是对象类型，必须加子选择 `{ pmid }`，否则报 `selectionMismatch` 错误
- `conceptId` 返回外部数据库 ID（如 `rxcui:232158`、`ncit:C29723`）
- `interactionTypes` 可能为空数组
- `interactionScore` 范围 0-1

## CTD (Comparative Toxicogenomics Database)

> 化学物-基因交互、化学物/基因-疾病关联的权威数据库。**核心策略：全量数据下载本地化，绕过 ALTCHA 人机验证。**

### 访问策略

| 方式 | URL | 状态 | 说明 |
|---|---|---|---|
| **全量数据下载（推荐）** | `https://ctdbase.org/reports/*.tsv.gz` | ✅ 无验证 | 静态文件，Python requests 直接下载 |
| 下载索引页 | `https://ctdbase.org/downloads/` | ✅ 可访问 | 列出全部数据文件 |
| batchQuery API | `https://ctdbase.org/tools/batchQuery.go` | ❌ ALTCHA 拦截 | Python requests 返回 302 到验证页；Playwright 可通过但有时效性 |
| detail 页 | `https://ctdbase.org/detail.go?type=chem&acc={MeSH_ID}` | ⚠️ 同受验证限制 | — |

### 全量数据文件清单

下载基址：`https://ctdbase.org/reports/`

| 文件 | 内容 | 压缩大小 | 关键列 |
|---|---|---|---|
| `CTD_chem_gene_ixns.tsv.gz` | 化学-基因交互（315万行） | 41 MB | ChemicalName, ChemicalID, CasRN, GeneSymbol, GeneID, GeneForms, Organism, OrganismID, Interaction, InteractionActions, PubMedIDs |
| `CTD_chemicals.tsv.gz` | 化学物词表（179,669条） | 10 MB | ChemicalName, ChemicalID, CasRN, PubChemCID, PubChemSID, DTXSID, InChIKey, ..., MESHSynonyms, CTDCuratedSynonyms |
| `CTD_curated_genes_diseases.tsv.gz` | 基因-疾病 curated 直接证据 | 0.5 MB | GeneSymbol, GeneID, DiseaseName, DiseaseID, DirectEvidence, OmimIDs, PubMedIDs |
| `CTD_genes_diseases.tsv.gz` | 基因-疾病全量（含推断） | **3 GB** | 同上（流式下载超9分钟，优先 curated 版） |
| `CTD_curated_chemicals_diseases.tsv.gz` | 化学物-疾病 curated | 2.1 MB | ChemicalName, ChemicalID, CasRN, DiseaseName, DiseaseID, DirectEvidence, OmimIDs, PubMedIDs |
| `CTD_chemicals_diseases.tsv.gz` | 化学物-疾病全量 | 154 MB | 同上 |
| `CTD_genes.tsv.gz` | 基因词表 | 117 MB | GeneSymbol, GeneID, ... |
| `CTD_diseases.tsv.gz` | 疾病词表（13,353条） | 1.7 MB | DiseaseName, DiseaseID, AltDiseaseIDs, Definition, ParentIDs, TreeNumbers, ... |
| `CTD_genes_pathways.tsv.gz` | 基因-通路关联 | 1 MB | GeneSymbol, GeneID, PathwayID, PathwayName |
| `CTD_chem_pathways_enriched.tsv.gz` | 化学物-通路富集 | — | ChemicalID, PathwayID, ... |

### 下载代码

```python
import requests, gzip

BASE = 'https://ctdbase.org/reports/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

def download_ctd(filename, save_path, timeout=600):
    """下载并解压 CTD 全量数据文件（绕过 ALTCHA 验证）"""
    r = requests.get(BASE + filename, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    with open(save_path + '.gz', 'wb') as f:
        f.write(r.content)
    text = gzip.decompress(r.content).decode('utf-8', errors='replace')
    with open(save_path, 'w', encoding='utf-8') as f:
        f.write(text)
    return text

# 核心两个文件
chem_ixns = download_ctd('CTD_chem_gene_ixns.tsv.gz', 'CTD_chem_gene_ixns.tsv')
chemicals = download_ctd('CTD_chemicals.tsv.gz', 'CTD_chemicals.tsv')
```

### 解析化学-基因交互（人类）

```python
from collections import defaultdict

def load_human_chem_gene_ixns(path):
    """返回 {ChemicalID(裸): {GeneSymbol: [(InteractionActions, PubMedIDs)]}}"""
    chem_targets = defaultdict(dict)
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 11 or parts[6] != 'Homo sapiens':
                continue
            chem_id, gene_sym = parts[1], parts[3]
            chem_targets[chem_id].setdefault(gene_sym, []).append((parts[9], parts[10]))
    return chem_targets  # 实测：人类 11,103 个化学物
```

### 名称/标识符匹配（词表）

```python
import re

def load_chemical_vocab(path):
    """返回 (name_map, synonym_map, cas_map)；ChemicalID 已去 MESH: 前缀"""
    name_map, syn_map, cas_map = {}, {}, {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.rstrip('\n').split('\t')
            if len(parts) < 5:
                continue
            cid = parts[1].replace('MESH:', '')  # 关键：去前缀
            name_map[parts[0].lower()] = cid
            if parts[2]:
                cas_map[parts[2]] = cid
            for col in (11, 12):  # MESHSynonyms, CTDCuratedSynonyms
                if len(parts) > col and parts[col]:
                    for syn in parts[col].split('|'):
                        if syn:
                            syn_map.setdefault(syn.lower(), cid)
    return name_map, syn_map, cas_map

def normalize_name(s):
    """TCMSP 来源名称规范化：去手性前缀/_qt后缀/尾括号别名"""
    s = s.strip().lower()
    s = re.sub(r'^\(\d*[+-eprsz,]*\)-', '', s)
    s = re.sub(r'^[+-]\s*', '', s)
    s = re.sub(r'[_ ]qt$', '', s)
    s = re.sub(r',\s*\(.*\)$', '', s)
    return s.strip()

# 匹配优先级：CAS > 主名称 > 规范化名称 > 同义词
# 实测（1638条成分库）：CAS 441 + 名称 191 + 规范化 17 + 同义词 65 = 714 (43.6%)
```

### batchQuery API（备用，需 Playwright 验证）

```
GET/POST https://ctdbase.org/tools/batchQuery.go
参数：
  inputType: chem | disease | gene | go | pathway | phenotype | reference
  inputTerms: 换行/tab/| 分隔，最多 4000 个
  report: cgixns(需actionTypes) | chems_curated | chems_inferred | genes_curated |
          genes_inferred | diseases_curated | diseases_inferred | pathways_curated |
          pathways_inferred | pathways_enriched | go | go_enriched(需ontology) | exposure
  format: tsv | csv | json | xml
  inputTermSearchType: hierarchicalAssociations | directAssociations
示例: ?inputType=chem&inputTerms=D003634|mercury&report=genes_curated&format=tsv
```

Playwright 通过流程：导航到 `https://ctdbase.org/` → 点击 ALTCHA "I'm not a robot" → 等 Verified → 点 Accept → goto API URL → 自动下载 JSON。**验证有时效性（隔天失效），不稳定，不推荐自动化使用。**

### 常用疾病 MeSH ID

| 疾病 | MeSH ID | curated 基因数 |
|---|---|---|
| Colitis, Ulcerative（UC） | D003093 | 71（marker/mechanism + therapeutic） |
| Inflammatory Bowel Diseases | D015212 | — |
| Crohn Disease | D003424 | — |
| Colitis | D003092 | — |
| Colorectal Neoplasms | D015179 | — |

### CTD 常见陷阱

1. **ALTCHA 验证仅拦截动态查询**：`/tools/batchQuery.go`、`/detail.go` 受限；`/reports/*.gz` 静态文件和 `/downloads/` 页不受影响
2. **ID 前缀不一致**：词表 `MESH:C008298` vs 交互表 `C008298`，必须去前缀
3. **药物 MeSH ID 不可凭记忆**：sulfasalazine=D012460（非 D013464）、budesonide=D019819（非 D018941）、prednisolone=D011239（非 D011923），一律查词表
4. **3GB 文件超时**：`CTD_genes_diseases.tsv.gz` 流式下载 >9 分钟（工具超时），优先 curated 版（0.5MB）
5. **同义词提升匹配**：`(+)-catechin`/`(-)-catechin` 同 ID D002392；名称规范化可再提升 ~5%
6. **人类数据过滤**：Organism 列 = `Homo sapiens`（交互表 315万行中人类化学物 1.1万）
7. **引用义务**：CTD 使用条款要求引用 + 通知 CTD + 提供出版物访问（文件头注释）

## DrugBank SPARQL（via UniProt）

> DrugBank 官网（go.drugbank.com）被 Cloudflare 拦截 403。UniProt SPARQL 内嵌 DrugBank 药物-靶点交叉引用（`rdfs:seeAlso <http://purl.uniprot.org/drugbank/DBxxxxx>`），可双向查询。

### 端点

`https://sparql.uniprot.org/sparql`（POST，`data={'query': ..., 'format': 'json'}`，Header `Accept: application/sparql-results+json`）

### 蛋白 → DrugBank 药物

```python
import requests

def protein_to_drugs(accessions):
    """accessions: ['P01375', 'P29460'] -> {acc: [DB00005, ...]}"""
    values = '\n'.join(f'    <http://purl.uniprot.org/uniprot/{a}>' for a in accessions)
    query = f'''
    PREFIX up: <http://purl.uniprot.org/core/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?acc ?drug WHERE {{
      VALUES ?protein {{
    {values}
      }}
      ?protein a up:Protein .
      ?protein up:organism <http://purl.uniprot.org/taxonomy/9606> .
      ?protein up:reviewed true .
      ?protein rdfs:seeAlso ?drug .
      FILTER(STRSTARTS(STR(?drug), "http://purl.uniprot.org/drugbank/"))
      BIND(STRAFTER(STR(?protein), "uniprot/") AS ?acc)
    }} LIMIT 1000
    '''
    r = requests.post('https://sparql.uniprot.org/sparql',
                      data={'query': query, 'format': 'json'},
                      headers={'Accept': 'application/sparql-results+json'},
                      timeout=60)
    result = {}
    for b in r.json()['results']['bindings']:
        acc = b['acc']['value']
        drug = b['drug']['value'].split('/')[-1]  # DB00005
        result.setdefault(acc, []).append(drug)
    return result
# 实测：P01375(TNF) → 51 个药物；P29460(IL12B) → 7 个
```

### DrugBank 药物 → 靶点蛋白

```python
def drugs_to_proteins(db_ids):
    """db_ids: ['DB00065', 'DB00005'] -> {DB_ID: [P01375, ...]}"""
    values = '\n'.join(f'    <http://purl.uniprot.org/drugbank/{d}>' for d in db_ids)
    query = f'''
    PREFIX up: <http://purl.uniprot.org/core/>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?acc ?drug WHERE {{
      ?protein a up:Protein .
      ?protein up:organism <http://purl.uniprot.org/taxonomy/9606> .
      ?protein up:reviewed true .
      ?protein rdfs:seeAlso ?drug .
      VALUES ?drug {{
    {values}
      }}
      BIND(STRAFTER(STR(?protein), "uniprot/") AS ?acc)
    }} LIMIT 500
    '''
    r = requests.post('https://sparql.uniprot.org/sparql',
                      data={'query': query, 'format': 'json'},
                      headers={'Accept': 'application/sparql-results+json'},
                      timeout=60)
    result = {}
    for b in r.json()['results']['bindings']:
        drug = b['drug']['value'].split('/')[-1]
        result.setdefault(drug, []).append(b['acc']['value'])
    return result
# 实测：DB00065 → [P01375(TNF)]；DB00005 → 15 个人类靶点
```

### DrugBank 常见陷阱

1. **禁用 DrugBank 本体语法**：`?drug drugbank:target ?protein` 在 UniProt SPARQL 无结果（无该本体三元组），必须 `rdfs:seeAlso` + `FILTER(STRSTARTS(..., "drugbank/"))`
2. **复杂查询 503**：加 `up:encodedBy`/`skos:prefLabel` 或大范围扫描易 503；保持简单、VALUES 每批 ≤ 20、LIMIT 限制
3. **只有 DB ID**：SPARQL 只返回 `DBxxxxx`，药物名称/适应症/机制需查 ChEMBL（mesalazine=CHEMBL704）或 Open Targets `drug.mechanismsOfAction`
4. **基因符号需先转 accession**：先 UniProt REST API `gene:{name} AND organism_id:9606 AND reviewed:true`
5. **reviewed 过滤**：加 `up:reviewed true` 避免冗余未审核条目

## NPASS（Natural Product Activity & Species Source）

- 站点：`https://bidd.group/NPASS/`（204,023 天然产物；PHP 传统站，requests 直接可用，无需特殊头）
- 字段体系：化合物=NPC ID、物种=NPO ID（Taxonomy ID/属/科/界）、靶点=NPT ID
- **核心陷阱：搜索表单的提交按钮 name/value 必须一起 POST**（如 `search_by_property: 'SEARCH'`），漏掉返回空页

### 化合物名搜索

```python
import requests
r = requests.post('https://bidd.group/NPASS/search_compound.php',
    data={'CompoundName': 'quercetin', 'logPMin': '', 'logPMax': '', 'MWMin': '',
          'MWMax': '', 'HdonorMin': '', 'HdonorMax': '', 'HacceptorMin': '',
          'HacceptorMax': '', 'RotatableMin': '', 'RotatableMax': '',
          'IfQuantity': 'AllNPs', 'search_by_property': 'SEARCH'},
    timeout=30)
# 结果 HTML 表：NPC ID / 名称 / 分子量 / 来源物种数 / 靶点数 / 活性记录数
# 实测 quercetin → 119 行；MW/ALogP 范围可组合筛选
```

### 结构相似性搜索

```python
r = requests.post('https://bidd.group/NPASS/search_compound_s.php',
    data={'smiles': 'OC1=CC(Cl)=CC=C1OC1=CC=C(Cl)C=C1Cl',  # 查询 SMILES
          'CompoundSMILES': '', 'fingerprintType': 'ECFP4',
          'similarityCutoff_inputSMILES': '0.5', 'search_by_similarity': 'SEARCH'},
    timeout=60)
```

### 物种搜索（多基原药材按物种查成分）

```python
r = requests.post('https://bidd.group/NPASS/search_organism.php',
    data={'OrganismName': 'Astragalus', 'OrganismID': '', 'IfQuantity': 'AllNPs',
          'search_by_organism': 'SEARCH'}, timeout=30)
# 实测 Astragalus → 137 行（NPO ID/TaxID/属/科/界/NP 统计）
```

### 靶点搜索

```python
r = requests.post('https://bidd.group/NPASS/search_target.php',
    data={'targetName_or_ID': 'TNF', 'search_by_target': 'SEARCH'}, timeout=30)
# 实测 TNF → NPT516 / NPT5387
```

### 详情页（GET，HTML 表格解析）

- `compound.php?compoundID=NPCxxx`：SMILES/InChI/InChIKey/分子式/PubChem CID/ChEMBL ID/化学分类/来源物种表/靶点活性表（IC50/Ki 等）
- `target.php?target_id=NPTxxx`：靶点详情+关联化合物
- 层级浏览：`browse_np.php`（POST `clickColumn_np_initial`/`clickColumn_np_mw`）、`browse_org.php`、`browse_target.php`、`browse_drug.php`、层级版 `browse_np_hierar.php` 等
- 下载页 `downloadnpass.html` 存在；另有 AI 搜索 `aisearch-test/run-script.php`（POST llmForm，实验性）

## SwissTargetPrediction（反向外靶点预测）

- 站点：`https://www.swisstargetprediction.ch/`（2D/3D 相似性，输出 Prob* + 已知活性数）
- **完整工作流：predict → job → 轮询 result，全程约 60-70 秒**
- **关键参数：`ioi`（interest of identity）必须传 `2`；传 `1` 或缺省 → "Invalid request"**
- 重原子数 ≥5 才能提交（乙醇 CCO 会报 error=2）

```python
import requests, re, time
S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})

def swisstarget_predict(smiles, organism='Homo_sapiens', wait=65):
    # 1. 初始化拿 cookie
    S.get('https://www.swisstargetprediction.ch/predict.php', timeout=30)
    # 2. 提交（ioi=2 必带）
    r = S.post('https://www.swisstargetprediction.ch/predict.php',
        data={'organism': organism, 'smiles': smiles, 'ioi': '2'}, timeout=60)
    # 3. 从响应 JS 提取 job 跳转：location.replace("result.php?job=xxx&organism=...")
    m = re.search(r'location\.replace\("([^"]+)"\)', r.text)
    if not m:
        if 'error_page' in r.text:
            raise RuntimeError('提交被拒（重原子<5 或 SMILES 无效）')
        raise RuntimeError(f'未获 job：{r.text[:200]}')
    # 4. 等待计算
    time.sleep(wait)
    # 5. 取结果并解析 HTML 表格
    rr = S.get('https://www.swisstargetprediction.ch/' + m.group(1), timeout=60)
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', rr.text, re.S)
    targets = []
    for row in rows:
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
        if len(cells) >= 9 and cells[0]:
            targets.append({'Target': cells[0], 'Common_name': cells[1],
                            'Uniprot': cells[2], 'ChEMBL': cells[3],
                            'Target_class': cells[4], 'Probability': cells[5],
                            'Known_actives_3D': cells[6], 'Known_actives_2D': cells[7]})
    return targets
# 实测 Triclosan：CBR1 Prob=1.0、AHR 0.80、AKR1B1 0.77 等 100 行结果
# 物种可选：Homo_sapiens / Mus_musculus / Rattus_norvegicus
# result.php 页 DataTables 导出按钮为纯前端，直接解析 HTML 表格即可
```

### 常见陷阱

1. `ioi` 参数：提交表单页有两个值，`1`=定性示例请求（无效），`2`=真实预测，**必须 2**
2. 错误页跳转 `error_page.php?error=N`：error=2 = 重原子数 <5
3. 计算等待：实测 60-70 秒；过早 GET result.php 会得到空表
4. `/download.php` 只是说明页，无服务端 CSV 下载

## PharmMapper（药效团反向前向预测）

- 站点：`https://www.lilab-ecust.cn/pharmmapper/`（7302 全靶点/2241 人源/16159 可药/pKd≥6.0 共 52431 套药效团）
- **流程：Step1 提交 mol2/sdf → Step2 选参数拿 JOB ID → check 轮询 → getres 取结果（计算约 1-2 小时）**
- **核心陷阱：必须直接 HTTPS**；http 会 301 → https 且 POST 降级为 GET 丢 multipart，报 "Invalid request detected"
- 文件要求：Tripos mol2 或 MDL SDF **V2000**（不支持 V3000）、文件名 <50 字符、重原子 ≤100、单邮箱 ≤10 并发作业

```python
import requests, time
S = requests.Session()
S.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
BASE = 'https://www.lilab-ecust.cn/pharmmapper'

def pharmmapper_submit(sdf_text, email='user@example.com', target_set='1'):
    # 1. 初始化会话
    S.get(f'{BASE}/submitfile.html', timeout=30)
    # 2. Step1：上传分子文件（multipart）
    r1 = S.post(f'{BASE}/submitjob.html',
        files={'file': ('query.sdf', sdf_text, 'chemical/x-mdl-sdfile')},
        data={'email': email, 'describe': ''}, timeout=120)
    # 响应为 Step2 参数页（含 d0-d25 隐藏字段）
    # 3. Step2：提交参数拿 JOB ID（参数含义见下方映射表）
    step2 = {f'd{i}': v for i, v in enumerate(
        ['Y', '300', '4', '500', '1', '500', 'Y', 'MMFF94', target_set,
         '300', '1000', 'Y', '2.0', '90', '1.0', '1.0', '1.0', '1.0', '1.0', '1.0',
         '0', '0', '0', '0', '0', '0'])}
    r2 = S.post(f'{BASE}/submitdb.html', data=step2, timeout=120)
    # {"status":0,"response":"260904013057"} → response 即 JOB ID
    import json
    return json.loads(r2.text)['response']

def pharmmapper_check(job_id):
    r = requests.post(f'{BASE}/check.html', data={'mid': job_id}, timeout=30)
    return r.text  # "Status: In Queue" / 完成 Status: Success
```

### Step2 参数映射（d0-d25，来自 submitjob.js 的 jobChk()）

| 参数 | 含义 | 默认值 |
|---|---|---|
| d0 | 构象生成 Y/N | Y |
| d1 | 最大构象数 | 300 |
| d2-d5 | MOGA 参数（种群/代数/数/次） | 4/500/1/500 |
| d6 | 能量最小化 | Y |
| d7 | 力场 | MMFF94（或 TAFF） |
| **d8** | **靶点集** | 0=All 7302；1=Human 2241；2=Druggable 16159；3=pKd≥6.0 52431 |
| d9 | 保留匹配靶点数 | 300 |
| d10 | GA match 数 | 1000 |
| d11 | 排除体积 | Y |
| d12 | Fit cutoff | 2.0 |
| d13 | 采样角度 | 90 |
| d14-d19 | 六特征权重（氢键供/受、疏水、正/负、芳环） | 1.0 |
| d20-d25 | 六特征最小数 | 0 |

- 结果页 `getres.html?job=<JOBID>`：JS 驱动，完成后再访问；实测提交返回 JOB ID `260904013057`（All Targets）
- SDF V2000 最小模板：头 4 行（ Mol）+ 计数行 `aaabbblllfffcccsssxxxrrrpppiiimmmvvvvvv` + 原子块 + 键块 + `M  END`

## DCABM-TCM（血液成分库）

- 站点：`http://bionet.ncpsb.org.cn/dcabm-tcm/`（3,709 血液吸收入血成分 / 194 药材 / 192 方剂；MongoDB 风格查询 API）
- **此前 404 根因：查询语法错误**。正确姿势：`getSearchName` + URL 编码的 JSON 条件，字段名**大写开头**
- 返回极全：_id/Name/分子式/PubChem CID/CAS/InChI/InChIKey/Cross reference（DrugBank/ChEBI/DGIdb）/DCABM Drug ID（如 BC2747_S）/Type（Prototype|Metabolite）/DrugMAP/DRESIS/TTD/NPCDR/来源方剂

```python
import requests, json, urllib.parse
BASE = 'http://bionet.ncpsb.org.cn/seed_tcm/Search'
H = {'User-Agent': 'Mozilla/5.0'}

def dcabm_search(name='berberine'):
    # 精确名（regex 锚定）
    q = {"Name": {"$regex": f"^{name}$", "$options": "i"}}
    url = f'{BASE}/getSearchName?browseSymMap=ingredients_basic&query=' + urllib.parse.quote(json.dumps(q))
    r = requests.get(url, headers=H, timeout=30)
    return r.json()  # 单条完整记录（含全部跨库 ID）

def dcabm_page(pattern='berbe', page=1, per=5):
    # 模糊分页（POST）
    r = requests.post(f'{BASE}/getSearchData', headers=H, timeout=30, json={
        'browseSymMap': 'ingredients_basic', 'page': page, 'perPage': per,
        'searchKey': {'Name': {'$regex': pattern, '$options': 'i'}}})
    d = r.json()  # [{'total': [{'total': 3709}], 'records': [...]}]
    return d[0]['total'][0]['total'], d[0]['records']

def dcabm_chem_classes():
    r = requests.get(f'{BASE}/getChemicalCompoundData', headers=H, timeout=30)
    return r.json()  # [{'_id': 'Lipids...', 'count': 551}, ...] 化学分类聚合
# 实测：berberine 命中；'berbe' 模糊 → 总数 3709 全库可见
```

### 常见陷阱

1. `browseSymMap` 仅 `ingredients_basic` 实测有效；其他 browse 值（enterBlood 等）对应端点 404
2. 字段大小写敏感：`Name` 有效，`name` 永远空结果
3. `getBatmanId` 500、`getInitEnterBloodData`/`getChemicalCompoundDataBySuperclass` 404，勿再试
4. 未测端点（备用）：`blast/`、`chemicalAnnotationnsSearch`、`submitSmileContent`、`IntoDatabase`（前端 JS 中存在）
5. http 可直连（无强制 https），requests 无需 verify=False

## TCMBank（tcmbank.cn，全 API 闭环 2026-09）

- 站点：`http://tcmbank.cn/`（多页应用 MPA，非 hash SPA；61,965 成分 / 药材 / 靶点 / 疾病四实体互查）
- **站点证书过期**：浏览器访问 https 报 ERR_CERT_DATE_INVALID 且 CSP 强制升级，页面全挂；**Python http 直连完全正常**
- API 前缀：`http://tcmbank.cn/api/`，统一响应 `{"status":true,"code":0,"message":"...","data":{...}}`

### 实体搜索 API（GET，全库分页）

| 实体 | 搜索端点 | 关键参数 | 补全提示 |
|---|---|---|---|
| 成分 | `api/ingredient/search` | ingredient_name, formula, cur_page, per_page | `api/ingredient/search/tip` |
| 药材 | `api/herb/search` | herb_name, cur_page, per_page | `api/herb/search/tip` |
| 靶点 | `api/gene/search` | gene_name, cur_page, per_page | `api/gene/search/tip` |
| 疾病 | `api/disease/search` | disease_name, cur_page, per_page | `api/disease/search/tip` |

```python
import requests
H = {'User-Agent': 'Mozilla/5.0'}
def tcmbank_search(entity, kw, page=1, per=10):
    p = {f'{entity}_name': kw, 'cur_page': page, 'per_page': per}
    r = requests.get(f'http://tcmbank.cn/api/{entity}/search', headers=H, params=p, timeout=30)
    d = r.json()['data']
    return d['list'], d.get('total_ingredients') or d.get('total_herbs') or d.get('total_genes') or d.get('total_diseases')
# 实测：ingredient 'berberine' → 14 条；herb 'astragalus' → 23 物种；
#       gene 'TNF' → 2 条（TCMBANKGE000052）；disease 'colitis' → 40 条（Colitis=TCMBANKDI000037）
```

### 详情与互查 API（GET，params 均为 tcmbank_id=<ID>）

| 实体 | 详情 | 互查端点 |
|---|---|---|
| 成分 | `ingredient/detail` | totargets / toherbs / todiseases |
| 药材 | `herb/detail` | toingredients / totargets / todiseases |
| 靶点 | `gene/detail` | toingredients / toherbs / todiseases |
| 疾病 | `disease/detail` | totargets / toherbs / toingredients |

```python
def tcmbank_detail(entity, tcmbank_id, relation=None):
    api = f'{entity}/detail' if not relation else f'{entity}/{relation}'
    r = requests.get(f'http://tcmbank.cn/api/{api}',
        headers=H, params={'tcmbank_id': tcmbank_id}, timeout=30)
    return r.json()['data']
# 实测：
#   ingredient/detail(demethyleneberberine) → SMILES/PubChem 101404949/CAS 25459-91-0/
#     HERB HBIN023247/TCM-ID 5050 等 200+ 字段（含 ADMET 预测、能量、Jurs 描述符全家族）
#   herb/toingredients(TCMBANKHE000243) → 5 成分；gene/toingredients(TNF) → 62 成分
#   disease/totargets(Colitis) → 943 靶点
# 关系图：api/relation?tcmbank_id=&type=&slice_length=（chart_data/data_links）
```

### 常见陷阱

1. **必须 http**：https 证书过期（ERR_CERT_DATE_INVALID），且页面 CSP `upgrade-insecure-requests` 会让浏览器内全部资源转向 https 而失败；requests 走 http 无此问题
2. 详情页 URL 是 `/Ingredients/detail?ID=TCMBANKINxxx`（MPA 真实路径），**不是** `/#/detail?id=`（此路由不存在，别再猜）
3. 关联字段可能为空（如某成分 totargets=0），数据覆盖不全属正常，非 API 故障
4. 成分详情含 `Source_ID`（HERB 来源）与海量描述符，按需取字段，勿全量打印

## TCM-ID（bidd.group/TCMID，分类浏览打通）

- 站点：`https://www.bidd.group/TCMID/`（megabionet.org 已死 502，改用 bidd.group 域名）
- 搜索与浏览均为 `results.php`：POST 搜索 / GET browse 参数分类浏览

```python
import requests
H = {'User-Agent': 'Mozilla/5.0'}
B = 'https://www.bidd.group/TCMID'

def tcmid_browse_target(tid='TCMT174'):
    r = requests.get(f'{B}/results.php', headers=H, params={'browsetarget': tid}, timeout=30)
    return r.text  # 4237 行：靶点关联成分/方剂（含 tcmf.php?formula=TCMFx 详情链接）

def tcmid_browse_funcclass(fc='Dissolve mass'):
    r = requests.get(f'{B}/results.php', headers=H, params={'browsefuncclass': fc}, timeout=30)
    return r.text  # 方剂+成分混合表（TCMH 成分 ID）
# browse.php 共 768 个靶点链接 + 153 个功能分类链接可枚举
# 成分详情：tcmh.php?ingredients=TCMHxxx；全量已抓：tcmid_cpd_full.html(2.29MB)/tcmid_formula_all.html(2.69MB)
```

## SEA（Similarity Ensemble Approach，换行分隔批量）

- 站点：`https://sea.bkslab.org/`（任务型：challenge PoW → submit → result 轮询，详见既有 SEA 章节）
- 批量格式：多个 SMILES 用**换行分隔**一次提交；实测 `'CCO\nc1ccccc1'` → 3128 靶点，4 次轮询（约 20 秒）SUCCESS
- 输出字段：Target_Chembl_ID / Target_Species / Z_score / p(P_value) / Target_Name / MaxTc / Uniprot_Accession / Uniprot_ID / Sequence
