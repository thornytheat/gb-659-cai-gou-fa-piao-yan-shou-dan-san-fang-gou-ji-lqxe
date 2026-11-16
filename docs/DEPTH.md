# 深度登记 采购三方勾稽报告

- 规模：中型
- 核心规则：采购、分批验收和合并发票按货号与税率分组，数量和金额使用 Decimal 守恒。
- 使用者与工作流：应付会计导入三类单据，映射主数据，核对数量税额，多对多匹配并导出差异。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 单据采信 | 导入三类单据并隔离冲突 | POST /api/documents/import | `app/modules/m1_documents/parser.py` `app/modules/m1_documents/money.py` | `tests/test_m1_documents.py` |
| M2 主数据映射 | 归一货号、发布映射版本 | POST /api/master/resolve<br>POST /api/master/publish | `app/modules/m2_master/aliases.py` `app/modules/m2_master/versions.py` | `tests/test_m2_master.py` `tests/test_mapping_versions.py` |
| M3 数量勾稽 | 统一包装单位并核对未到量 | POST /api/quantity/match | `app/modules/m3_quantity/units.py` `app/modules/m3_quantity/match.py` | `tests/test_m3_quantity.py` |
| M4 税额核验 | 重算净额、税额与含税额 | POST /api/tax/check | `app/modules/m4_tax/calculate.py` `app/modules/m4_tax/verify.py` | `tests/test_m4_tax.py` |
| M5 多对多匹配 | 在相同货号税率内组合匹配 | POST /api/matches/solve | `app/modules/m5_matching/candidates.py` `app/modules/m5_matching/solver.py` | `tests/test_m5_matching.py` |
| M6 差异批次 | 按映射版本建批、比较归属差异并导出 | POST /api/reconcile<br>GET /api/batches/compare | `app/modules/m6_batches/service.py` `app/modules/m6_batches/exporter.py` | `tests/test_m6_batches.py` `tests/test_version_api.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-05 | 单据缺少字段 | 静默补空进入匹配 | 隔离并保留行号 | `app/modules/m1_documents/parser.py` | `tests/test_m1_documents.py::test_missing_field_quarantined` |
| TRAP-06 | 金额带千分位 | 直接转换失败 | 清理分隔符后使用 Decimal | `app/modules/m1_documents/money.py` | `tests/test_m1_documents.py::test_thousands_amount_uses_decimal` |
| TRAP-07 | 空值哨兵 | 被当作零金额 | 空值集合统一拒绝 | `app/modules/m1_documents/money.py` | `tests/test_m1_documents.py::test_missing_field_quarantined` |
| TRAP-08 | 供应商编码前导零 | 转整数后主数据失配 | 编码始终按字符串保形 | `app/modules/m2_master/aliases.py` | `tests/test_m2_master.py::test_supplier_code_preserves_leading_zero` |
| TRAP-10 | 税额浮点尾差 | 分项和总额差一分 | Decimal 半入到分 | `app/modules/m4_tax/calculate.py` | `tests/test_m4_tax.py::test_half_up_to_cent` |
| TRAP-14 | 重复单号值冲突 | 后写覆盖金额 | 单独隔离冲突版本 | `app/modules/m1_documents/parser.py` | `tests/test_m1_documents.py::test_conflicting_duplicate_detected` |
| TRAP-17 | 旧列名仍在使用 | 新版读取报错 | 受控字段迁移并报告冲突 | `app/modules/m2_master/evolve.py` | `tests/test_m2_master.py::test_old_columns_migrate` |
| TRAP-19 | 组合候选顺序变化 | 匹配结果随输入顺序变化 | 按差额、数量与编号稳定择一 | `app/modules/m5_matching/solver.py` | `tests/test_m5_matching.py::test_choice_is_order_independent` |
| TRAP-23 | 相邻税率边界 | 不同税率金额互相抵消 | 货号与税率共同分组 | `app/modules/m4_tax/verify.py` | `tests/test_m4_tax.py::test_different_tax_rates_never_offset` |
| TRAP-26 | 同批重复执行 | 重复新增对账结果 | 输入摘要作为运行唯一键 | `app/store.py` | `tests/test_m6_batches.py::test_idempotent_record` |
| TRAP-31 | 别名纠错覆盖旧映射 | 历史批次被按新映射改写，审计结果变化 | 映射版本只追加，批次绑定建批版本并保留别名快照 | `app/modules/m2_master/versions.py` `app/modules/m6_batches/service.py` | `tests/test_mapping_versions.py::test_existing_batch_keeps_original_mapping_after_publish` |
| TRAP-32 | 别名成环 | 解析死循环或静默返回原值 | 发布前逐链检测并拒绝 | `app/modules/m2_master/versions.py` | `tests/test_mapping_versions.py::test_alias_cycle_rejected_and_nothing_published` |
| TRAP-33 | 并发发布 | 两个编辑者各自基于旧版本发布，后写覆盖先写 | 基准版本校验加写事务，过期版本返回 409 | `app/modules/m2_master/versions.py` | `tests/test_mapping_versions.py::test_concurrent_publishes_only_one_wins` |
| TRAP-34 | 发布中途中断 | 半写入的版本或重试重复建版 | 单事务提交，幂等键复用已有版本 | `app/modules/m2_master/versions.py` | `tests/test_mapping_versions.py::test_retry_after_interrupted_write_creates_single_version` |

## 对账规则

- 采购数量 = 净验收数量 + 未到数量。
- 净验收含税额 = 已开票含税额 + 未开票金额 + 已解释差异。
- 不同税率的行不可相互抵消。
- 映射版本只追加：发布纠错产生新版本，已建批次始终按建批时的版本解释。
- 同一原始输入可在不同映射版本下各建一个批次，并比较归属差异。

## 已知不覆盖

- 不处理外币汇兑，样例与输出均为人民币。
- 不提供付款与撤销；映射版本与批次只追加，不删除、不回滚已发布版本。

