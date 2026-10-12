# M5 对接建模（Docking & Modeling）

**触发意图**：分子对接 / 结合模式 / 口袋 / 亲和能 / 接触残基 / 蛋白-蛋白复合物（docking, Vina, 对接, 口袋, binding mode, 亲和能, PPI）

## 工具链

```
① dock_check_environment {}          # 先跑：vina/meeko/pymol/rdkit 体检
② dock_fetch_structure {source=pdb, query=<PDBid>, chains, drop_hetatm, out}
   dock_fetch_structure {source=pubchem, query=<CID|name>, out=<ligand.sdf>}   # 配体 3D
   # 备选 source: pdbinfo（条目元数据）/ pdbe / swissmodel（同源模型）
③ dock_vina_docking {receptor, ligand, pocket_chain, pocket_residues,
   exhaustiveness=16, seed=42, vina_path, workdir}
   # meeko PDBQT 准备 → 口袋 CA 质心盒 → Vina → 亲和能表 + best_pose.pdb
④ dock_analyze_contacts {complex_pdb, ligand_chain=L, receptor_chains, merge_ligand=<best_pose.pdb>}
   # 接触残基（<4.5Å）+ 氢键候选（<3.5Å）；可先自动合并 pose
⑤ dock_geometric_ppi {receptor, ligand(蛋白), epitope="68-77", key_residues="73,74,75,77", out, workdir}
   # 文献约束表面采样（非能量最小化）；无实验复合物但文献定义界面时用
⑥ dock_compare_experiments {ligand|receptor 过滤}   # 实验台账：亲和能排名/seed 敏感性
```

## 决策规则（动态点）

- **D5**：best affinity ≤ -7 kcal/mol → 可信结合物，进接触分析；> -5 → 弱，换下一个候选
- **D6**：预测关键残基 → 必须回查 UniProt/文献证据（M2 工具）才可声称重要性
- 同 seed 重跑亲和能差 >0.1 kcal/mol → 复现红旗（查受体准备确定性）
- 口袋残基**必须来自文献**（检索文献/参考文档，绝不臆测）

## 质量门（强制）

- 一切输出声明为 **proposed docking model**；预测残基 `*` 标记 + 虚线接触
- 结合能必须带引擎版本（如 AutoDock Vina 1.2.5）
- 运行自动入实验台账（~/.dock_mcp_state/experiments.jsonl）
- 历史基准：LA/MD-2 -8.809 kcal/mol（exh16）/ -8.718（exh4）；接触 Y102/F119 复现

复现基准与参数细节：references/workflow.md；通道快照：references/structure_databases.md
