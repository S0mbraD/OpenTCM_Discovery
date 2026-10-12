# 端到端工作流与实战陷阱

> 按场景组织。每个工作流给出脚本链、参数与验证点。环境陷阱在文末——务必先读。

## 场景 A：小分子-受体对接（AutoDock Vina）

**适用**：把化合物对接到已知口袋（如紫草酸 → MD-2 LPS 口袋）。

```
1. 结构获取与清理
   python scripts/fetch_structure.py pdb 2Z64 --chains A,C --drop-hetatm --out receptor.pdb
   python scripts/fetch_structure.py pubchem 6441498 --out ligand.sdf --props ligand.json

2. 口袋定义（文献口袋残基列表，受体链）
   python scripts/run_vina.py --receptor receptor.pdb --ligand ligand.sdf \
       --pocket-chain C --pocket-residues 46,52,53,87,92,94,102,113,119,124,126,131,147 \
       --box-size 30 --exhaustiveness 16 --seed 42 --workdir dock_out

3. 合并 pose 进复合物
   python scripts/analyze_contacts.py merge --complex receptor.pdb \
       --ligand dock_out/best_pose.pdb --out complex.pdb

4. 接触分析
   python scripts/analyze_contacts.py analyze --pdb complex.pdb \
       --ligand-chain L --receptor-chains A,C --out contacts.json
```

**参数经验**：
- 口袋残基取文献值（如 MD-2 LPS 位点 17 残基）；中心由 CA 质心自动算
- box-size 30 Å 覆盖典型口袋；exhaustiveness 16 收敛充分（8 也可，结果差 <0.1 kcal/mol）
- 9 模式全部集中在同一口袋（spread <1.2 kcal/mol）= 收敛好；分散 = 位点不确定，慎用
- 结合能参考：-8.8 kcal/mol ≈ 已知 TLR4 拮抗剂 eritoran 类对接水平

**验证点**：接触残基是否全为该口袋经典残基（疏水墙 Phe/Ile/Leu）；配体是否与蛋白链外其他成分冲突（analyze_contacts 换 --receptor-chains 检查）。

## 场景 B：蛋白-蛋白几何对接（文献约束）

**适用**：无实验复合物结构、但有文献定位结合区域的蛋白对（如 S100A8 → TLR4/MD-2）。

```
python scripts/geometric_docking.py \
    --receptor receptor.pdb --ligand ligand_prot.pdb --ligand-chain A \
    --epitope 68-77 --key-residues 73,74,75,77 --surface-region 60-89 \
    --search-radius 28 --ligand-out-chain S --out model.pdb
```

**方法本质**：文献约束的几何搜索（表面点采样 × 自旋 × 间隙），**不是能量最小化**。输出必须标注 "proposed docking model"。

**打分解读**：mean_key_dist（关键残基到受体均距，越小越好）<6 Å 可用；mind（全原子最近距）≥2.5 Å 无穿插；region_contacts（界面区 <8 Å 残基数）>10 界面贴合好。

**单体 vs 二聚体决策**：先跑 dimer；若所有放置 mind <3 Å（原子穿插），改用单体并在文档记录该排除证据（S100A8 案例即如此）。

## 场景 C：渲染出图（PyMOL）

```
1. 写 JSON 配置（链、配体、图类型：overall/detail/pocket）
2. python scripts/make_figure_pml.py --config fig.json --out figure.pml
3. pymol -c -r figure.pml   # 3200x2200 全参数约 2-4 分钟/张
```

**图型选择**：
- overall：全景 + 链标签（配体标签必须单原子：`and id 2`）
- detail：界面特写 + 关键残基彩色 sticks + 接触虚线
- pocket：配体口袋特写 + 口袋残基金色 + 黄虚线接触

**渲染时长**：3200×2200 含表面 + sticks 约 100-240 秒/张；6 张图后台跑约 15 分钟。低分辨率快速预览（800×600，<30 秒）验证构图后再出大图。

## 场景 D：参考图风格组合图

```
python scripts/compose_panel.py \
    --main overall.png --zoom detail.png \
    --box 930,400,1740,1200 --box-src 3200x2200 \
    --panel A --name "2Z64-TLR4/MD2_S100A8" \
    --red "Interface metrics|I73-MD-2  3.8 A@258CD9|K77-MD-2  4.1 A@666666" \
    --out panel.png
```

**框选定位法**：--box 坐标可用像素颜色分析定位（橙-青绿交界 = 蛋白界面；绿色区 = 配体口袋）。命名格式固定 `PDBID-蛋白_配体`。红框参数一行一条，支持每行独立颜色（@RRGGBB）。

## 场景 E：结构模板替换（human 化）

```
python scripts/fetch_structure.py swissmodel P05109 --out models.json
```
SWISS-MODEL Repository 按 UniProt AC 检索同源模型（如 mouse 受体 → human 模板）。用 PDBe ligand_monomers 端点检查结构里的配体/糖基修饰。

---

## 环境陷阱（全部实测踩坑，务必遵守）

1. **双 Python 环境**：Windows 上 `pip` 可能指向 `C:\Python312`，而运行时 Python 是宿主环境。安装包时用当前 Python 解释器：
   `& python -m pip install <pkg>`
   （否则装到另一个环境里 import 不到）
2. **依赖链**：meeko 0.8 需要 gemmi；Vina 用官方二进制 vina_1.2.5_win.exe（GitHub releases），pip 装不上（无 Windows wheel）。
3. **PDB 列对齐**：手工拼 PDB 行时 chainID 必须在**列 22**（0-indexed 21）。f-string 少一个空格 → PyMOL 里 `chain L` 选择器失效、配体"消失"。run_vina.py 的 pose_to_pdb 已内置列自检 assert。
4. **PyMOL 标签重复陷阱**：`label chain L and resn LA and elem C` 会给**所有**碳原子打标签（如 27 个重叠文字）。必须限定单原子：`and id 2`。
5. **PyMOL 工作目录**：`pymol -c -r script.pml` 的工作目录是 shell 当前目录而非 pml 所在目录。pml 内 load/png 必须用绝对路径（make_figure_pml.py 已处理），否则文件散落/读取失败。
6. **PYTHONUTF8=1**：所有 Python 调用前设置（Windows 中文环境 stdout 编码）。
7. **后台渲染**：长渲染用后台任务（注册表+日志），3200×2200 单张 100-240 秒。轮询间隔 ≥100 秒避免 shell 超时。
8. **PowerShell JSON BOM**：`Set-Content -Encoding UTF8` 会写 BOM 导致 `json.load` 失败；Python 内部改 JSON 配置或用 `-Encoding utf8NoBOM`（PS6+）/读时用 `utf-8-sig`。
9. **PubChem 限速**：PUG-REST 400/504 时等待重试（脚本已内置 4 次退避）；名称查询失败先试 CID。
10. **Vina CPU 参数**：默认 `CPU: 0` 表示用全部核，多任务并行时每个 vina 限制 `--cpu 4` 防抢占。
