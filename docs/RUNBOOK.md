# 运行手册 ledgerbank

## 启动与停止

```bash
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:18206/api/health
docker compose down -v --remove-orphans
```

## 重建数据库

```bash
docker compose down -v
docker compose up -d --build
```

命名卷 `ledgerbank-data` 保存批次、流水行、候选与匹配。重建后从「载入样例」重新开始。

## 主流程

1. `POST /api/demo/run`：读取两份脏样例，采信、质量、候选、匹配一次跑完。
2. `GET /api/quarantine`：查看隔离行与原因分布。
3. `GET /api/candidates`：查看已生成候选。
4. `GET /api/matches`：查看匹配组与手续费分摊。
5. `GET /api/clearing`：查看待清算逐笔复核（实际账户、匹配组合、剩余未清）。
6. `GET /api/differences`：查看两侧守恒与差异归因。
7. `GET /api/export/report.csv` 与 `GET /api/export/manifest.json`：导出。

## 待清算清理

```bash
# 查看复核清单
curl -s http://127.0.0.1:18206/api/clearing
# 确认某笔多义凭证的账户归属（key 从 GET /api/clearing 的 entries[].key 取）
curl -s -X POST http://127.0.0.1:18206/api/clearing/confirm \
  -H 'Content-Type: application/json' \
  -d '{"key":"9900-0000|V905|700.00|2026-04-02T16:00:00+00:00","bank_account":"6222-0012"}'
# 撤销确认
curl -s -X DELETE 'http://127.0.0.1:18206/api/clearing/confirm?key=...'
```

- 只有已登记的实际银行账户可以确认；待清算户本身不能作为归属目标。
- 确认后立即用同一套求解重跑本批次；确认记录按凭证键跨批次保留。
- `AMBIGUOUS` 表示多个账户都能凑出等额组合，不确认就一直挂账，不会自动猜。

## 单步重跑

```bash
curl -s -X POST http://127.0.0.1:18206/api/candidates
curl -s -X POST http://127.0.0.1:18206/api/matches
```

同一输入重跑命中同一 `run_id`，先删后插，行数与合计不变。

## 导入自有文件

```bash
curl -s -X POST http://127.0.0.1:18206/api/statements/import \
  -F side=bank -F file=@sample_data/bank_statement_dirty.csv
```

支持 UTF-8（含 BOM）、UTF-8 无 BOM、GB18030；列名按 `GET /api/contract` 的别名表映射。

## 失败处理

- 400：参数或文件不可用，页面顶部提示原因，保留已填内容。
- 404：批次或样例不存在，先执行 `POST /api/demo/run`。
- 409：还没有候选就求解匹配，先执行 `POST /api/candidates`。
- 隔离行不是失败：按原因码修正源文件后重新导入。

## 日志

```bash
docker compose logs -f app
```
