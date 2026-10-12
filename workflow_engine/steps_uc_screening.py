# -*- coding: utf-8 -*-
"""
L2 步骤库（兼容别名模块）
================================
本模块已重构为疾病可配置泛化版：steps_disease_screening.py
UC（溃疡性结肠炎）成为 disease profile 的缺省值（不传 disease 段时行为与旧版一致）。

保留本文件的目的：
  - run_server.py 等历史入口 `import steps_uc_screening as steps` 不需要改动
  - 第三方调用方（归档脚本/教程文档）的导入路径继续有效

新代码请直接使用：import steps_disease_screening as steps
"""
from steps_disease_screening import *          # noqa: F401,F403
from steps_disease_screening import (          # noqa: F401  (显式导出常用符号)
    DEFAULTS, EVIDENCE_WEIGHTS, DISEASE_DEFAULTS,
    build_workflow, s1_load_mechanism_db, s2_load_clinical_drugs,
    s3_load_drugbank_targets, s4_clinical_anchor_targets, s5_mechanism_targets,
    s6_integrate_targets, s7_load_molecule_library, s8_ctd_matching,
    s9_scoring, s10_positive_control, s11_output_artifacts,
)
