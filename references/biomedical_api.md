## Open Targets Platform GraphQL API

### 基础信息

- 端点：`https://api.platform.opentargets.org/api/v4/graphql`
- 方法：POST
- Content-Type：`application/json`
- 响应格式：JSON（GraphQL）
- 延迟：0.7-1.4s
- 无需 API Key

### Schema 关键类型

通过 GraphQL introspection 确认的类型结构：

| 类型 | 关键字段 | 说明 |
|---|---|---|
| **Disease** | `associatedTargets`, `drugAndClinicalCandidates`, `name`, `id` | 疾病→靶点、疾病→临床候选药 |
| **Target** | `associatedDiseases`, `interactions`, `drugAndClinicalCandidates`, `approvedSymbol`, `id` | 靶点→疾病、靶点→PPI、靶点→临床候选药 |
| **Drug** | `mechanismsOfAction`, `indications`, `name`, `drugType`, `id` | 药物→机制、药物→适应症 |
| MechanismOfActionRow | `mechanismOfAction`, `actionType`, `targetName`, `targets{id approvedSymbol}` | 药物机制详情 |
| ClinicalIndicationFromDisease | `id`, `maxClinicalStage`, `drug{id name}`, `clinicalReports` | 疾病关联药物详情 |
| ClinicalTargetFromTarget | `id`, `maxClinicalStage`, `drug{id name drugType}`, `clinicalReports`, `diseases` | 靶点关联药物详情 |
| Interaction | `score`, `targetA`, `targetB`, `sourceDatabase`, `count` | PPI 互作详情 |

### 搜索疾病（确认 MONDO ID）

```python
import requests

def ot_search_disease(keyword):
    """搜索疾病获取 MONDO ID"""
    query = """
    {
      search(queryString: "%s", entityNames: ["disease"], page: {index: 0, size: 10}) {
        total
        hits { id name entity }
      }
    }
    """ % keyword
    resp = requests.post(
        "https://api.platform.opentargets.org/api/v4/graphql",
        json={"query": query},
        timeout=30
    )
    return resp.json()["data"]["search"]
```

> ⚠️ 搜索参数名是 `queryString`（不是 `query`）。
> ⚠️ **MONDO_0004989 = 乳腺癌，不是 UC！** UC 正确 ID = `MONDO_0005101`。

### 疾病→靶点关联

```python
def ot_disease_to_targets(mondo_id, page_size=100):
    """获取疾病关联靶点（分页获取全部）"""
    query = """
    {
      disease(efoId: "%s") {
        id name
        associatedTargets(page: {index: %d, size: %d}) {
          count
          rows {
            target { id approvedSymbol }
            score
            datatypeScores { id score }
          }
        }
      }
    }
    """ % (mondo_id, 0, page_size)
    resp = requests.post(
        "https://api.platform.opentargets.org/api/v4/graphql",
        json={"query": query},
        timeout=45
    )
    data = resp.json()["data"]["disease"]
    targets = data["associatedTargets"]
    # 分页获取剩余靶点
    total = targets["count"]
    all_rows = list(targets["rows"])
    page = 1
    while len(all_rows) < total:
        query_page = """
        {
          disease(efoId: "%s") {
            associatedTargets(page: {index: %d, size: %d}) { rows { target { id approvedSymbol } score } }
          }
        }
        """ % (mondo_id, page, page_size)
        r = requests.post(
            "https://api.platform.opentargets.org/api/v4/graphql",
            json={"query": query_page},
            timeout=45
        )
        rows = r.json()["data"]["disease"]["associatedTargets"]["rows"]
        if not rows:
            break
        all_rows.extend(rows)
        page += 1
    return all_rows
```

### 靶点→疾病关联

```python
def ot_target_to_diseases(ensembl_id, page_size=100):
    """获取靶点关联疾病"""
    query = """
    {
      target(ensemblId: "%s") {
        id approvedSymbol
        associatedDiseases(page: {index: 0, size: %d}) {
          count
          rows { disease { id name } score }
        }
      }
    }
    """ % (ensembl_id, page_size)
    resp = requests.post(
        "https://api.platform.opentargets.org/api/v4/graphql",
        json={"query": query},
        timeout=45
    )
    return resp.json()["data"]["target"]
```

### 靶点→PPI 互作

```python
def ot_target_interactions(ensembl_id, page_size=100):
    """获取靶点 PPI 互作"""
    query = """
    {
      target(ensemblId: "%s") {
        id approvedSymbol
        interactions(page: {index: 0, size: %d}) {
          count
          rows {
            score
            targetA { id approvedSymbol }
            targetB { id approvedSymbol }
            sourceDatabase
          }
        }
      }
    }
    """ % (ensembl_id, page_size)
    resp = requests.post(
        "https://api.platform.opentargets.org/api/v4/graphql",
        json={"query": query},
        timeout=45
    )
    return resp.json()["data"]["target"]["interactions"]
```

### 疾病→临床候选药物

```python
def ot_disease_drugs(mondo_id):
    """获取疾病关联的临床候选药物"""
    query = """
    {
      disease(efoId: "%s") {
        id name
        drugAndClinicalCandidates {
          count
          rows {
            id maxClinicalStage
            drug { id name drugType }
          }
        }
      }
    }
    """ % mondo_id
    resp = requests.post(
        "https://api.platform.opentargets.org/api/v4/graphql",
        json={"query": query},
        timeout=45
    )
    return resp.json()["data"]["disease"]["drugAndClinicalCandidates"]
```

> ⚠️ `drugAndClinicalCandidates` **不接受 page 参数**，直接返回 count + rows。

### 药物→作用机制

```python
def ot_drug_mechanisms(chembl_id):
    """获取药物作用机制和适应症"""
    query = """
    {
      drug(chemblId: "%s") {
        id name drugType
        mechanismsOfAction {
          rows {
            mechanismOfAction
            actionType
            targetName
            targets { id approvedSymbol }
          }
        }
        indications {
          count
          rows { id maxClinicalStage disease { id name } }
        }
      }
    }
    """ % chembl_id
    resp = requests.post(
        "https://api.platform.opentargets.org/api/v4/graphql",
        json={"query": query},
        timeout=45
    )
    return resp.json()["data"]["drug"]
```

> ⚠️ 字段名是 `mechanismOfAction`（不是 `mechanism`）。
> ⚠️ mesalazine 的 ChEMBL ID 是 `CHEMBL704`（搜索名 MESALAMINE），不是 CHEMBL649。

### Open Targets 常见陷阱

- 搜索参数名是 `queryString`（不是 `query`）
- `drugAndClinicalCandidates` 不接受 `page` 参数
- 没有 `linkedTargets` / `knownDrugs` 字段（schema 中不存在）
- 正确字段：Drug 用 `mechanismsOfAction` + `indications`；Target 用 `associatedDiseases` + `interactions` + `drugAndClinicalCandidates`
- `efoId` 参数实际接受 MONDO ID 格式（如 `MONDO_0005101`）
- 可用 `__type(name: "TypeName") { fields { name } }` 进行 schema introspection
- 分页用 `page: {index: 0, size: N}`（index 从 0 开始，size 最大 100+）

