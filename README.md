# 采购三方勾稽报告

面向真实数据复核的中型数据工程工具，包含六个可独立调用的业务模块。规则、失败原因与证据字段见 docs/DATA-DICTIONARY.md，陷阱和测试映射见 docs/DEPTH.md。

## 启动

docker compose up --build

浏览器访问 http://localhost:18218。测试：docker compose exec -T app pytest -q

## 模块

- M1 单据采信：页面 /documents，接口 /api/documents/import
- M2 主数据映射：页面 /master，接口 /api/master/resolve、/api/master/versions、/api/master/publish
- M3 数量勾稽：页面 /quantity，接口 /api/quantity/match
- M4 税额核验：页面 /tax，接口 /api/tax/check
- M5 多对多匹配：页面 /matching，接口 /api/matches/solve
- M6 差异批次：页面 /batches，接口 /api/reconcile、/api/batches、/api/batches/compare

## 运行规则

同一输入内容与同一映射版本生成相同运行标识，重复执行复用已有批次。映射纠错以发布新版本的方式生效：已建批次绑定的版本不变，历史勾稽结果仍按当时映射解释；同一原始输入可指定新版另建批次，并通过 `/api/batches/compare` 比较归属差异。隔离记录包含原因码、原始行号与原值；业务导出只保留一份证据清单。

