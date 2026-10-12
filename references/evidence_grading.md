# 证据分级与科研诚信规范

> 结构模拟领域最高频的错误：把计算预测当作实验事实表述。本规范强制区分三层证据。

## 三层证据模型

| 层级 | 定义 | 图上标记 | 表述模板 |
|---|---|---|---|
| **实验直接证据** | SPR/ITC/CETSA/DARTS/共晶/竞争实验/定点突变 | 无标记，实线 | "X binds Y (SPR)" |
| **结构模板支持** | 实验解析的 PDB 坐标（仅证明组分自身结构） | 无标记 | "based on PDB 2Z64" |
| **计算预测** | 对接/几何放置/MD 中的具体方位、距离、接触 | `*` 上标 + 虚线 | "predicted / proposed / in silico" |

## 图内标记约定（严格执行）

- **残基标签带 `*`**：该残基参与接触仅来自对接预测（如 E88*/H62*/K136*）
- **残基标签无 `*`**：有突变/竞争实验支持的关键残基（如 I73/K77）
- **接触虚线**：距离判定（<4.5 Å），一律是 "potential contacts"，**绝不标注为 hydrogen bond**（除非有几何+能量双重判据且配体极性原子明确）
- **结合能数字**：必须带引擎与版本（"BE: -8.8 kcal/mol, AutoDock Vina 1.2.5"）

## 文档声明模板（每份建模说明必须包含）

模型为 **literature-supported proposed docking model / pure in silico docking prediction**，
不是实验解析的复合物结构。

**建议表述**：
- "Proposed binding model of X with Y"
- "Predicted binding mode of X in the Y pocket"
- "In silico docking model (AutoDock Vina)"

**禁止表述**：
- "experimentally determined complex structure"
- "crystal structure of X–Y"
- "X binds Y"（当只有对接证据时）
- "validated binding" / "confirmed pose"

## 典型案例的分级示范（S100A8/紫草酸项目）

**S100A8-TLR4/MD-2 模型**：
- 实验层：SPR 直接结合（Vogl 2007）；peptide #3 竞争实验；K77A 突变降低结合（CGT 2023）
- 模板层：2Z64（mouse 受体晶体）、1MR8（S100A8 NMR）
- 预测层：S100A8 具体方位、I73-K77 到 MD-2 的 3.8/4.1 Å 距离、图中虚线

**紫草酸-TLR4/MD-2 模型**：
- 文献支持链：LA→KLK5 抑制→TLR4/NF-κB 下降（功能层面，非直接结合）
- 预测层：**全部**（LA 在 MD-2 口袋的 pose、-8.8 kcal/mol、21 个接触残基）
- 必须写明：LA 与 TLR4/MD-2 直接结合无任何实验证据，模型价值在于提出可检验假说（LPS 竞争性拮抗，类 eritoran 机制），后续可 SPR/LPS 竞争实验验证

## 可检验假说的写法

预测模型应输出明确实验验证路径，而非停留在"对接得分不错"：
- 结构假说 → SPR/ITC 直接结合验证
- 竞争假说 → 配体竞争实验（如 LA vs LPS）
- 残基假说 → 定点突变（Ala 扫描）
- 功能假说 → 报告基因（NF-κB luciferase）

## 跨模型一致性检查

合并多个预测成分（如四组分复合物）时必须声明各成分的独立性：
> "A 与 B 分别结合 C 的不同位点（相距 X Å 无冲突），仅证明空间兼容性，不代表生理条件下同时结合。"
