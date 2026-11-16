"""M8 待清算：分户核对边界、账户归属、剩余未清、确认与重跑幂等。"""
from decimal import Decimal


def import_clearing(client):
    bank = client.post("/api/statements/import",
                       data={"side": "bank", "sample": "clearing_bank.csv"})
    assert bank.status_code == 200, bank.text
    ledger = client.post("/api/statements/import",
                         data={"side": "ledger", "sample": "clearing_ledger.csv"})
    assert ledger.status_code == 200, ledger.text
    return ledger.json()


def entries(client):
    body = client.get("/api/clearing").json()
    return {item["ledger_txn_no"]: item for item in body["entries"]}, body


def groups(client):
    return client.get("/api/matches").json()["groups"]


def test_clearing_entries_show_account_combo_and_remaining(client):
    import_clearing(client)
    rows, body = entries(client)
    assert rows["V901"]["status"] == "CLEARED"
    assert rows["V901"]["bank_account"] == "6222-0012"
    assert rows["V901"]["bank_ids"] == ["CB001"]
    assert rows["V901"]["remaining"] == "0.00"
    assert body["totals"]["remaining"] == "1934.57"


def test_unique_account_is_cleared_without_guessing(client):
    """只有 6222-0012 能凑出 500.00，凭证清理到这个账户。"""
    import_clearing(client)
    rows, _ = entries(client)
    assert rows["V901"]["candidate_accounts"] == ["6222-0012"]
    assert rows["V901"]["confirmed"] is False


def test_same_amount_in_two_accounts_stays_ambiguous(client):
    """6222-0012 与 6222-0034 都凑得出 700.00，不按文件顺序猜账户。"""
    import_clearing(client)
    rows, _ = entries(client)
    assert rows["V905"]["status"] == "AMBIGUOUS"
    assert rows["V905"]["bank_account"] == ""
    assert rows["V905"]["candidate_accounts"] == ["6222-0012", "6222-0034"]
    assert rows["V905"]["remaining"] == "700.00"
    assert rows["V905"]["match_id"] == ""


def test_partial_clearing_keeps_cent_outstanding(client):
    # 999.99 清理 1000.00，尾差 0.01 留在待清算
    import_clearing(client)
    rows, _ = entries(client)
    assert rows["V903"]["status"] == "PARTIAL"
    assert rows["V903"]["cleared"] == "999.99"
    assert rows["V903"]["remaining"] == "0.01"


def test_multi_bank_rows_merge_into_one_clearing_group(client):
    import_clearing(client)
    rows, _ = entries(client)
    assert rows["V908"]["bank_ids"] == ["CB008", "CB009"]
    assert rows["V908"]["tier"] == 3
    assert rows["V908"]["bank_account"] == "6222-0056"
    assert rows["V908"]["cleared"] == "200.00"


def test_outstanding_without_bank_flow_keeps_full_amount(client):
    import_clearing(client)
    rows, _ = entries(client)
    assert rows["V904"]["status"] == "OUTSTANDING"
    assert rows["V904"]["remaining"] == "1234.56"


def test_quarantined_clearing_voucher_is_listed_not_matched(client):
    import_clearing(client)
    rows, body = entries(client)
    assert rows["V912"]["status"] == "ISOLATED"
    assert rows["V912"]["note"].startswith("AMOUNT_SENTINEL")
    assert "N/A" in rows["V912"]["note"]
    assert body["totals"]["records"] == 7


def test_normal_candidates_never_cross_accounts(client):
    """分户核对：CB003 在 6222-0034，V911 在 6222-0012，等额也不生成候选。"""
    import_clearing(client)
    items = client.get("/api/candidates").json()["candidates"]
    assert not any(
        "CB003" in item["bank_ids"] and "V911" in item["ledger_ids"]
        for item in items
    )
    assert all(item["account"] == "6222-0034"
               for item in items if "CB004" in item["bank_ids"])


def test_same_account_cross_amount_not_matched(client):
    """同账户但金额不等也不生成候选，避免把两笔不同业务硬配成一对。"""
    import_clearing(client)
    items = client.get("/api/candidates").json()["candidates"]
    assert not any(
        "CB002" in item["bank_ids"] and "V910" in item["ledger_ids"]
        for item in items
    )


def test_clearing_voucher_not_in_normal_candidates(client):
    import_clearing(client)
    items = client.get("/api/candidates").json()["candidates"]
    assert not any("V901" in item["ledger_ids"] for item in items)
    assert not any("V905" in item["ledger_ids"] for item in items)


def test_clearing_and_normal_groups_never_share_bank_rows(client):
    import_clearing(client)
    all_groups = groups(client)
    bank_ids = [key for group in all_groups for key in group["bank_ids"]]
    assert len(bank_ids) == len(set(bank_ids))
    clearing_groups = [group for group in all_groups if group["clearing"]]
    assert {group["bank_account"] for group in clearing_groups} <= {
        "6222-0012", "6222-0034", "6222-0056"
    }


def test_confirm_account_clears_ambiguous_voucher(client):
    import_clearing(client)
    rows, _ = entries(client)
    response = client.post("/api/clearing/confirm",
                           json={"key": rows["V905"]["key"], "bank_account": "6222-0012"})
    assert response.status_code == 200, response.text
    rows, body = entries(client)
    assert rows["V905"]["status"] == "CLEARED"
    assert rows["V905"]["bank_account"] == "6222-0012"
    assert rows["V905"]["confirmed"] is True
    assert rows["V905"]["bank_ids"] == ["CB010"]
    assert body["totals"]["remaining"] == "1234.57"


def test_confirm_rejects_unregistered_account(client):
    import_clearing(client)
    rows, _ = entries(client)
    response = client.post("/api/clearing/confirm",
                           json={"key": rows["V905"]["key"], "bank_account": "6222-9999"})
    assert response.status_code == 400
    assert response.json()["error"] == "E_BAD_ACCOUNT"


def test_confirm_then_rerun_keeps_attribution_without_duplicates(client):
    import_clearing(client)
    rows, _ = entries(client)
    client.post("/api/clearing/confirm",
                json={"key": rows["V905"]["key"], "bank_account": "6222-0034"})
    first = entries(client)[1]
    first_groups = groups(client)

    import_clearing(client)  # 中断后整批重跑
    second = entries(client)[1]
    second_groups = groups(client)

    assert second["totals"] == first["totals"]
    assert second["entries"] == first["entries"]
    assert len(second_groups) == len(first_groups)
    assert [group["match_id"] for group in second_groups] == [
        group["match_id"] for group in first_groups
    ]
    confirmed = {item["ledger_txn_no"]: item for item in second["entries"]}["V905"]
    assert confirmed["confirmed"] is True
    assert confirmed["bank_account"] == "6222-0034"
    assert confirmed["bank_ids"] == ["CB011"]


def test_unconfirm_restores_ambiguous_state(client):
    import_clearing(client)
    rows, _ = entries(client)
    client.post("/api/clearing/confirm",
                json={"key": rows["V905"]["key"], "bank_account": "6222-0012"})
    response = client.request("DELETE", "/api/clearing/confirm",
                              params={"key": rows["V905"]["key"]})
    assert response.status_code == 200, response.text
    rows, _ = entries(client)
    assert rows["V905"]["status"] == "AMBIGUOUS"
    assert rows["V905"]["confirmed"] is False


def test_clearing_totals_are_consistent(client):
    import_clearing(client)
    _, body = entries(client)
    totals = body["totals"]
    assert Decimal(totals["cleared"]) + Decimal(totals["remaining"]) == Decimal(totals["amount"])
    assert sum(totals["status_counts"].values()) == totals["records"]


def test_clearing_page_and_export(client):
    import_clearing(client)
    page = client.get("/clearing")
    assert page.status_code == 200
    assert "剩余未清" in page.text
    report = client.get("/api/export/report.csv").text
    assert "待清算" in report
    assert "AMBIGUOUS" in report
    manifest = client.get("/api/export/manifest.json").json()
    assert manifest["clearing"]["remaining"] == "1934.57"


def test_bank_side_clearing_account_is_quarantined(client):
    """待清算户没有物理流水，银行侧出现该账户即为口径错误。"""
    client.post("/api/statements/import",
                data={"side": "bank", "sample": "clearing_bank.csv"})
    rows = client.get("/api/quarantine").json()["rows"]
    assert rows == []

    raw = (
        "流水号,账户,发生时间,借贷标志,金额,手续费,对方户名,摘要\n"
        "X001,9900-0000,2026-04-02,借,500.00,0.00,客户,待清算户流水\n"
    ).encode("utf-8")
    response = client.post("/api/statements/import", data={"side": "bank"},
                           files={"file": ("bad.csv", raw, "text/csv")})
    assert response.status_code == 200
    rows = client.get("/api/quarantine").json()["rows"]
    assert rows[0]["reason"] == "CLEARING_IN_BANK"


def test_reexport_twice_no_duplicate_clearing_rows(client):
    """中断重跑：同一份输入跑两次，待清算明细不翻倍、挂账金额不变化。"""
    import_clearing(client)
    first = entries(client)[1]
    again = import_clearing(client)
    stored = client.app.state.store
    assert len(stored.clearing_entries(again["run_id"])) == first["totals"]["records"]
    second = entries(client)[1]
    assert second["totals"]["remaining"] == first["totals"]["remaining"]
    assert [item["key"] for item in second["entries"]] == [
        item["key"] for item in first["entries"]
    ]


def test_ledger_identity_balanced_with_partial_clearing(client):
    """部分清理不能破坏账簿守恒：匹配组按凭证全额计，尾差回到账上。"""
    import_clearing(client)
    identities = {item["key"]: item for item in
                  client.get("/api/differences").json()["identities"]}
    assert all(item["balanced"] for item in identities.values())
    assert identities["ledger_amount"]["balanced"] is True


def test_confirm_without_flow_keeps_attribution_pinned(client):
    """已确认账户但本期没有可用流水：保留归属，金额继续挂账。"""
    import_clearing(client)
    rows, _ = entries(client)
    client.post("/api/clearing/confirm",
                json={"key": rows["V904"]["key"], "bank_account": "6222-0012"})
    rows, body = entries(client)
    assert rows["V904"]["status"] == "PINNED"
    assert rows["V904"]["bank_account"] == "6222-0012"
    assert rows["V904"]["confirmed"] is True
    assert rows["V904"]["remaining"] == "1234.56"
    # V905 仍为多义挂账 700.00，总计不变：1234.56 + 700.00
    assert body["totals"]["remaining"] == "1934.57"


def test_competing_clearing_vouchers_do_not_double_use_a_flow(client):
    """两笔待清算凭证都只有同一账户能凑额，但只有一笔流水：先到先清，后到挂账。"""
    bank = (
        "流水号,账户,发生时间,借贷标志,金额,手续费,对方户名,摘要\n"
        "R001,6222-0012,2026-04-02,借,600.00,0.00,客户,唯一流水\n"
    ).encode("utf-8")
    ledger = (
        "凭证号,账户,记账日期,方向,金额,摘要\n"
        "C1,9900-0000,2026-04-02,借,600.00,第一笔\n"
        "C2,9900-0000,2026-04-03,借,600.00,第二笔\n"
    ).encode("utf-8")
    client.post("/api/statements/import", data={"side": "bank"},
                files={"file": ("b.csv", bank, "text/csv")})
    client.post("/api/statements/import", data={"side": "ledger"},
                files={"file": ("l.csv", ledger, "text/csv")})
    rows, _ = entries(client)
    cleared = [row for row in rows.values() if row["status"] == "CLEARED"]
    outstanding = [row for row in rows.values() if row["status"] == "OUTSTANDING"]
    assert len(cleared) == 1
    assert len(outstanding) == 1
    assert cleared[0]["bank_ids"] == ["R001"]
    assert outstanding[0]["bank_ids"] == []
    assert outstanding[0]["remaining"] == "600.00"


def test_ledger_only_import_lists_outstanding_then_bank_completes(client):
    """先导入账簿：待清算全部挂账且可确认归属；银行到达后按确认账户完成清理。"""
    ledger = client.post("/api/statements/import",
                         data={"side": "ledger", "sample": "clearing_ledger.csv"}).json()
    assert ledger["status"] == "partial_ledger"
    rows, body = entries(client)
    assert body["totals"]["remaining"] == "4434.56"
    assert rows["V905"]["status"] == "OUTSTANDING"

    client.post("/api/clearing/confirm",
                json={"key": rows["V905"]["key"], "bank_account": "6222-0034"})
    rows, _ = entries(client)
    assert rows["V905"]["status"] == "PINNED"
    assert rows["V905"]["bank_account"] == "6222-0034"
    assert rows["V905"]["confirmed"] is True
    assert client.get("/api/health").json()["status_of_run"] == "partial_ledger"

    client.post("/api/statements/import",
                data={"side": "bank", "sample": "clearing_bank.csv"})
    rows, body = entries(client)
    assert rows["V905"]["status"] == "CLEARED"
    assert rows["V905"]["bank_account"] == "6222-0034"
    assert rows["V905"]["bank_ids"] == ["CB011"]
    assert client.get("/api/health").json()["status_of_run"] == "matched"


def test_dirty_sample_groups_are_account_consistent(client, demo):
    """原脏样例重新生成后，所有匹配组两侧账户完全相同。"""
    store = client.app.state.store
    records = store.records(demo["run_id"])
    accounts = {row["txn_no"]: row["account"]
                for row in records if row["status"] == "valid"}
    for group in store.matches(demo["run_id"]):
        assert {accounts[key] for key in group["bank_ids"]} == \
               {accounts[key] for key in group["ledger_ids"]}
