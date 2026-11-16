# 样例数据

- `bank_statement_dirty.csv`：带 BOM、重复表头、千分位、空值、符号错、日期错、未知账户、重复流水号、多余列与超 2^53 编号。
- `ledger_book_dirty.csv`：GB18030 编码、中文列名、日期与时间混用、重复凭证号冲突。
- `bank_truncated_tail.csv`：末行没有换行且列数不足。
- `empty_bank.csv`、`header_only_ledger.csv`、`all_blank_bank.csv`：三种空态。
- `clearing_bank.csv`、`clearing_ledger.csv`：待清算场景。包含跨账户等额、同账户等额、
  两账户都能凑额（挂账待确认）、尾差 0.01（部分清理）、无流水（全额挂账）、
  合并支付（两笔流水清一笔凭证）、待清算户空值隔离，以及一笔「账户不同不得配对」的分户反例。
