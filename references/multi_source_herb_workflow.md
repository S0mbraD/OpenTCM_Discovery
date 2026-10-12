# 多基原中药化合物整理工作流

## 背景

部分中药为多基原，即同一药材名对应多个物种来源。例如：

| 药材名 | 拉丁名 | 基原物种 |
|---|---|---|
| 黄连 | Coptidis Rhizoma | *Coptis chinensis* Franch.（味连）、*Coptis deltoidea* C.Y.Cheng et Hsiao（雅连）、*Coptis teeta* Wall.（云连） |
| 甘草 | Glycyrrhizae Radix | *Glycyrrhiza uralensis* Fisch.、*Glycyrrhiza inflata* Bat.、*Glycyrrhiza glabra* L. |
| 大黄 | Rhei Radix et Rhizoma | *Rheum palmatum* L.、*Rheum tanguticum* Maxim. ex Balf.、*Rheum officinale* Baill. |

一个品种可含 5000+ 已知化合物，需按物种逐一检索、合并去重、按结构分类。

## 第一步：确定基原物种

### 方法 A：蒲标网查询（免费，推荐）

1. 访问 `https://db2.ouryao.com`
2. 在检索栏选择"药典"，输入药材中文名（如"黄连"）
3. 进入药典条目，查看"基原"段落
4. 记录格式：`本品为[科名]植物[物种1][拉丁名1]、[物种2][拉丁名2]或[物种3][拉丁名3]的干燥[药用部位]`

### 方法 B：直接查阅《中国药典》纸质版或 PDF

## 第二步：按物种逐一检索化合物

### Reaxys 检索流程

1. **登录**：通过机构 VPN + 统一身份认证登录 Reaxys（`https://www.reaxys.com`）
2. **Quick Search**：在搜索框输入物种拉丁名（如 `Coptis chinensis`）
3. **查看结果**：检索结果分为 Documents（文献）和 Substances（物质）两类
   - Documents：显示命中文献数（如 14,024 Documents）
   - Substances：显示命中物质数（如 5,433 Substances）
4. **切换到 Substances**：点击面包屑导航中的 Substances 链接
5. **选中物质**：勾选需要导出的物质（可全选或筛选）
6. **导出**：点击 Export → 选择导出格式（Excel）→ 选择导出字段
7. **下载文件**：文件名格式 `Reaxys_Exp_YYYYMMDD_HHMMSS.xlsx`

### Reaxys 导出字段说明

| 列名 | 含义 |
|---|---|
| Structure: Image | 化合物结构图 |
| SMILES | 简化分子线性输入规范 |
| Substance Identification: Reaxys Registry Number | Reaxys 物质编号 |
| Links to Reaxys | Reaxys 物质详情页链接 |
| Data Count | 关联数据条数 |
| CAS Registry Number | CAS 注册号 |
| Chemical Name | 化学名称 |
| Linear Structure Formula | 线性结构式 |
| Molecular Formula | 分子式 |
| Molecular Weight | 分子量 |
| Type of Substance | 物质类型 |
| Type and Modification | 类型与修饰 |
| InChI Key | InChI 密钥（国际化学标识符） |
| Composition: Comp. Name | 组成成分名称 |
| Composition: Comp. Conc. | 组成成分浓度 |
| Composition: Comp. Attrib. | 组成成分属性 |
| Field Availability | 字段可用性 |
| Number of Reactions | 关联反应数 |
| Number of References | 关联文献数 |

### HERB 2.0 检索流程

- HERB 以药材中文名为单位，可能不区分基原物种
- 用药材中文名匹配 HERB ID → 调用详情 API 获取成分关联
- 详见 SKILL.md 中 HERB 2.0 检索方法

### TCMSP 检索流程

- 用两步法搜索（中文名 → 拉丁名）
- 注意同名药材需选正确条目

## 第三步：合并与去重

### 合并策略

1. 将各基原物种的化合物列表合并为一张总表
2. 新增"来源物种"列，记录每个化合物来自哪些基原物种
3. 同一化合物在多个物种中出现时，来源物种列记录多个值（分号分隔）

### 去重键优先级

| 优先级 | 去重键 | 说明 |
|---|---|---|
| 1 | InChI Key | 最可靠，国际标准化学标识符，含立体化学信息 |
| 2 | Canonical SMILES | 次选，不含立体化学信息时可用 |
| 3 | CAS Registry Number | 备选，部分化合物可能无 CAS 号 |
| 4 | 分子式 + 分子量 | 最后手段，可能误并不同异构体 |

### 去重代码示例

```python
import pandas as pd

# 合并各物种的化合物表
all_dfs = [df_species1, df_species2, df_species3]
for i, df in enumerate(all_dfs):
    df['来源物种'] = species_names[i]

merged = pd.concat(all_dfs, ignore_index=True)

# 按 InChI Key 去重，保留首次出现的记录
deduped = merged.drop_duplicates(subset=['InChI Key'], keep='first')

# 对于无 InChI Key 的记录，用 Canonical SMILES 去重
no_inchi = merged[merged['InChI Key'].isna() | (merged['InChI Key'] == '')]
no_inchi_deduped = no_inchi.drop_duplicates(subset=['SMILES'], keep='first')

# 合并结果
final = pd.concat([deduped, no_inchi_deduped], ignore_index=True)
```

## 第四步：按结构归属分类

### 常见中药化合物结构大类

| 结构大类 | 代表化合物 | 特征 |
|---|---|---|
| 生物碱类 | 小檗碱、黄连碱 | 含氮碱性化合物 |
| 黄酮类 | 黄芩苷、槲皮素 | C6-C3-C6 骨架 |
| 萜类 | 青蒿素、穿心莲内酯 | 异戊二烯单元 |
| 苯丙素类 | 阿魏酸、桂皮酸 | C6-C3 骨架 |
| 醌类 | 大黄素、丹参醌 | 醌环结构 |
| 甾体类 | 甾体皂苷、强心苷 | 环戊烷多氢菲骨架 |
| 糖苷类 | 人参皂苷、甘草酸 | 糖+苷元 |

### 自动分类方法

- **RDKit（Python）**：通过 SMILES 解析分子骨架，结合官能团识别自动分类
- **手动分类**：根据结构图和化学名称人工归类，适合小批量

## 注意事项

- Reaxys 导出可能有数量限制，大批量需分批导出
- **Reaxys 导出故障排除**：当选中物质较多（如 5,000+）时，"Export → All data"模式会因数据量过大而失败。变通方案：改用"Export → Choose specific data"模式，勾选 Physical Data + Pharmacological Data 等所需字段子集，可成功导出
- Reaxys 导出时如遇报错，尝试减少选中物质数量后重试
- 蒲标网药典条目中的拉丁学名可直接复制用于 Reaxys 检索
- 多基原物种的化合物可能有大量重叠（如黄连三个物种共享大量生物碱），去重后总数通常远小于各物种化合物数之和

## 实际去重策略（InChI Key 覆盖率不足时的降级方案）

上述去重键优先级（InChI Key > SMILES > CAS > 分子式+分子量）在理论上是最佳方案，但实际执行中 HERB 数据的 InChI Key 覆盖率可能不足 100%。此时可采用以下降级策略：

1. **以成分为英文名去重**：同一化合物在不同药材中可能以不同名称出现，但英文名（Ingredient_name）在 HERB 中是规范化的，可作为可靠的去重键
2. **合并来源药材**：去重时将多个来源药材合并到同一行，用分号分隔（如"黄连;黄柏;三颗针"），保留药材-成分关联信息
3. **实际效果参考**：38,697 条药材-成分关联 → 去重后 15,891 条唯一化合物，其中 5,811 条涉及多味药材来源

> 去重与分类的完整脚本模板见 `scripts/dedup_classify_template.py`。

## RDKit 化合物结构分类方案

使用 RDKit 根据 SMILES 自动分类化合物骨架，分类逻辑基于官能团和骨架特征匹配。实际执行中 15,891 个唯一化合物的分类分布如下：

| 结构大类 | 数量 | 分类依据 |
|---|---|---|
| 脂肪族 | ~2,954 | 无芳香环，以链状脂肪族为主 |
| 黄酮类 | ~1,472 | C6-C3-C6 骨架，含色酮环 |
| 芳香族 | ~1,413 | 含苯环但不属于黄酮/苯丙素 |
| 其他生物碱 | ~1,370 | 含氮碱性化合物（非已知子类） |
| 糖及苷类 | ~1,156 | 含糖基或苷键 |
| 单萜类 | ~1,124 | 异戊二烯单元（C10） |
| 甾体类 | ~1,072 | 环戊烷多氢菲骨架 |
| 挥发油 | ~786 | 小分子萜类/芳香族挥发物 |
| 有机酸类 | ~554 | 含羧基 |
| 未分类 | ~2,121 | SMILES 缺失或无法解析 |

**注意事项**：
- "未分类"的主要原因是 SMILES 字段缺失，需补充 SMILES 后重新分类
- 分类优先级：先匹配特征骨架（黄酮、甾体、萜类），再按官能团（有机酸、糖苷），最后按芳香性/脂肪性归类
- RDKit 分类是自动化的初步分类，对于边界情况可能需要人工复核
