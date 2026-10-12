# M6 渲染出图与证据分级（Figures & Evidence Documentation）

**触发意图**：论文级结构图 / 组合图 / 图注 / 证据声明（figure, 渲染, 论文图, 组合图, PyMOL, publish）

## 工具链（脚本）

```
① scripts/make_figure_pml.py --config fig.json --out figure.pml
   JSON 配置（chains/ligand/figures[]: overall|detail|pocket）→ 渲染脚本
② pymol -c -r figure.pml          # 后台跑；3200x2200 约 100-240 秒/张
③ scripts/compose_panel.py --main overall.png --zoom detail.png
   --box <x,y,w,h> --box-src 3200x2200 --panel A
   --name "2Z64-TLR4/MD2_S100A8" --red "Interface metrics|I73-MD-2  3.8 A@258CD9|..."
   # 参考期刊版式组合图
```

## 风格规范（导师参考图复刻，9-10/10 验收）

- 整体图：渐变卡通 + 半透明表面 + 深度雾化；细节图：黄虚线接触 + 残基标签
- 组合图：左整体+右放大、**细黑实线框**+**梯形引导线**、左上 Panel 字母、底部 `PDB-蛋白_配体`、右下**红框核心参数**
- PyMOL 标签限定单原子 `and id 2`（否则全碳重复标签）；pml 内路径全绝对

## 证据分级纪律（写图注/文档时强制）

三层证据：**实验直接证据**（SPR/突变/共晶）> **结构模板**（PDB 坐标）> **计算预测**（对接 pose/距离）。
图上：预测残基带 `*`、接触一律 "potential contacts"（虚线）、结合能带引擎版本。
声明模板：`"Predicted binding models. ... Dashed lines indicate potential contacts, not experimentally validated bonds."`
完整规范与模板：references/evidence_grading.md + references/rendering_style.md

## 验收清单

3200x2200 分辨率 / 主图无配体文字标签 / 放大图带细黑外框 / 引导线成梯形 / 红框参数右下 / 底部命名规范 / 证据声明齐全。
