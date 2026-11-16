"""待清算户清理：把集团待清算户下的凭证清理到唯一实际银行账户。

边界（与普通分户核对严格区分）：
- 普通账簿凭证必须与银行流水账户完全相同（分户核对），不做跨账户配对；
- 待清算凭证没有物理流水，只能清理到**一个**实际银行账户；
- 同一笔待清算凭证若在多个账户都凑得出等额组合，视为账户归属不明，保持挂账并
  要求人工确认，不按文件顺序猜一个账户；
- 一次清理只允许使用同一账户下的 1~4 笔银行流水，组合金额与凭证金额差 ≤ 容差。
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

from ...config import dec
from ..m2_contract.coerce import parse_amount
from ..m4_candidates import window
from ..m4_candidates.candidate import as_row, bounded_subsets

ZERO = Decimal("0.00")
QUANT = Decimal("0.01")

STATUS_LABELS = {
    "CLEARED": "已清理",
    "PARTIAL": "部分清理",
    "OUTSTANDING": "待清理",
    "AMBIGUOUS": "待确认账户",
    "PINNED": "已确认待清理",
    "ISOLATED": "隔离",
}


def is_clearing_account(account: str, settings) -> bool:
    return account in settings.clearing_accounts


def is_bank_account(account: str, settings) -> bool:
    return account in settings.bank_accounts


def voucher_key(record: dict) -> str:
    """待清算凭证的稳定业务键：账户、凭证号、金额、记账时间。

    不含 run_id，重跑或重新导入同一张凭证仍命中同一条确认记录，既不重复挂账，
    也不会丢失已经确认的账户归属。
    """
    return "|".join(
        [
            str(record.get("account", "")),
            str(record.get("txn_no", "")),
            format(dec(record.get("amount", "0")), "f"),
            str(record.get("posted_at", "")),
        ]
    )


def _combos(rows: list[dict], amount: Decimal, tolerance: Decimal,
            max_items: int) -> list[list[dict]]:
    if not rows:
        return []
    found = [[row] for row in rows if abs(row["amount"] - amount) <= tolerance]
    found.extend(bounded_subsets(rows, amount, tolerance, max_items))
    return found


def _possible(record: dict, rows: list[dict], settings, zone) -> dict[str, list[list[dict]]]:
    amount = dec(record["amount"])
    direction = int(record["direction"])
    anchor = window.parse_instant(record["posted_at"])
    pools: dict[str, list[dict]] = {}
    for row in rows:
        if row["direction"] != direction:
            continue
        if window.day_gap(anchor, row["posted_at"], zone) > settings.window_days:
            continue
        pools.setdefault(row["account"], []).append(row)
    table: dict[str, list[list[dict]]] = {}
    for account, pool in pools.items():
        combos = _combos(pool, amount, settings.tolerance, settings.subset_max)
        if combos:
            table[account] = combos
    return table


def _score(combo: list[dict], amount: Decimal, anchor, zone) -> tuple:
    total = sum((row["amount"] for row in combo), ZERO)
    gap = max(window.day_gap(anchor, row["posted_at"], zone) for row in combo)
    return (abs(total - amount), gap, tuple(sorted(row["txn_no"] for row in combo)))


def _match_id(bank_ids: list[str], ledger_ids: list[str]) -> str:
    key = hashlib.sha1(
        f"{','.join(bank_ids)}|{','.join(ledger_ids)}".encode("utf-8")
    ).hexdigest()[:10]
    return f"m-{key}"


def _new_group(record: dict, combo: list[dict], settings) -> dict:
    amount = dec(record["amount"])
    bank_ids = sorted(row["txn_no"] for row in combo)
    ledger_ids = [record["txn_no"]]
    bank_sum = sum((row["amount"] for row in combo), ZERO)
    bank_signed = sum((row["amount"] * row["direction"] for row in combo), ZERO)
    ledger_signed = amount * int(record["direction"])
    diff = abs(bank_sum - amount).quantize(settings.fee_quantum)
    return {
        "match_id": _match_id(bank_ids, ledger_ids),
        "tier": 1 if len(combo) == 1 else 3,
        "bank_ids": bank_ids,
        "ledger_ids": ledger_ids,
        "bank_sum": bank_sum,
        "ledger_sum": amount,
        "diff": diff,
        "day_gap": max(
            window.day_gap(window.parse_instant(record["posted_at"]), row["posted_at"],
                           window.tzinfo(settings.tz_name))
            for row in combo
        ),
        "bank_signed": bank_signed,
        "ledger_signed": ledger_signed,
        "fee_actual": sum((row["fee"] for row in combo), ZERO),
        "fee_alloc": ZERO,
        "clearing": True,
        "account": combo[0]["account"],
        "bank_account": combo[0]["account"],
    }


def _entry(record: dict, status: str, *, account: str = "", match_id: str = "",
           bank_ids: list[str] | None = None, tier: int | None = None,
           cleared: Decimal = ZERO, remaining: Decimal | None = None,
           candidates: list[str] | None = None, confirmed: bool = False,
           note: str = "") -> dict:
    amount = dec(record["amount"])
    if remaining is None:
        remaining = amount
    return {
        "key": voucher_key(record),
        "ledger_txn_no": record["txn_no"],
        "clearing_account": record["account"],
        "amount": amount,
        "direction": int(record["direction"]),
        "posted_at": record["posted_at"],
        "bank_account": account,
        "match_id": match_id,
        "bank_ids": list(bank_ids or []),
        "tier": tier,
        "cleared": cleared,
        "remaining": remaining,
        "status": status,
        "candidate_accounts": sorted(candidates or []),
        "confirmed": confirmed,
        "note": note,
    }


def plan(records: list[dict], normal_groups: list[dict], settings,
         confirmations: dict | None = None) -> dict:
    """在普通分户匹配之后做待清算清理，返回清理组与每笔凭证的复核结论。

    纯函数：同一份记录、同一批普通匹配、同一组确认记录，结果完全一致。
    """
    confirmations = confirmations or {}
    zone = window.tzinfo(settings.tz_name)
    valid = [record for record in records if record["status"] == "valid"]
    clearing_records = sorted(
        (record for record in valid
         if record["side"] == "ledger" and is_clearing_account(record["account"], settings)),
        key=voucher_key,
    )
    bank_rows = [
        as_row(record) for record in valid
        if record["side"] == "bank" and is_bank_account(record["account"], settings)
    ]
    used = {key for group in normal_groups for key in group["bank_ids"]}
    leftover = [row for row in bank_rows if row["txn_no"] not in used]

    # 账户归属按「全部剩余流水」判定，不因别的凭证先占用了某账户就改写归属：
    # 占用本身也是一次选择，用它反推会把同额款项记到错误门店。
    possible = {
        voucher_key(record): _possible(record, leftover, settings, zone)
        for record in clearing_records
    }

    # 先清「可选账户最少」的凭证：账户唯一或已确认的凭证优先占用流水，
    # 避免多义凭证先抢走流水、把本可清理的凭证拖成挂账。
    def _order(record: dict) -> tuple:
        key = voucher_key(record)
        confirmation = confirmations.get(key) or {}
        choices = 1 if confirmation.get("bank_account") else len(possible[key])
        return (choices, dec(record["amount"]), key)

    selected: set[str] = set()
    groups: list[dict] = []
    entries: list[dict] = []
    for record in sorted(clearing_records, key=_order):
        key = voucher_key(record)
        amount = dec(record["amount"])
        direction = int(record["direction"])
        anchor = window.parse_instant(record["posted_at"])
        table = possible[key]
        confirmation = confirmations.get(key) or {}
        pin = str(confirmation.get("bank_account", ""))
        confirmed = bool(pin)

        if confirmed:
            accounts = [pin]
        elif len(table) == 1:
            accounts = list(table)
        else:
            accounts = []

        if not accounts:
            status = "AMBIGUOUS" if len(table) > 1 else "OUTSTANDING"
            entries.append(_entry(record, status, candidates=list(table),
                                  confirmed=confirmed))
            continue

        account = accounts[0]
        available = [row for row in leftover if row["txn_no"] not in selected]
        pool = [
            row for row in available
            if row["account"] == account
            and row["direction"] == direction
            and window.day_gap(anchor, row["posted_at"], zone) <= settings.window_days
        ]
        combos = _combos(pool, amount, settings.tolerance, settings.subset_max)
        if not combos:
            # 账户归属仍按「全部剩余流水」记录，但本期该账户流水已被别的凭证用完。
            status = "PINNED" if confirmed else "OUTSTANDING"
            note = ("账户已确认，本期无可清理流水" if confirmed
                    else "本期可用流水已被其他凭证占用")
            entries.append(_entry(record, status, account=pin if confirmed else account,
                                  candidates=[], confirmed=confirmed, note=note))
            continue

        combo = min(combos, key=lambda item: _score(item, amount, anchor, zone))
        group = _new_group(record, combo, settings)
        groups.append(group)
        selected.update(group["bank_ids"])
        cleared = amount - max(amount - group["bank_sum"], ZERO)
        cleared = min(cleared, amount).quantize(QUANT)
        remaining = max(amount - cleared, ZERO).quantize(QUANT)
        entries.append(_entry(
            record, "CLEARED" if remaining == 0 else "PARTIAL",
            account=account, match_id=group["match_id"], bank_ids=group["bank_ids"],
            tier=group["tier"], cleared=cleared, remaining=remaining,
            candidates=list(table), confirmed=confirmed,
        ))

    for record in sorted(
        (item for item in records
         if item["status"] == "quarantine" and item["side"] == "ledger"
         and is_clearing_account(item["account"], settings)),
        key=voucher_key,
    ):
        isolated = dict(record)
        raw_amount = str(record.get("raw_amount", "") or "")
        try:
            isolated["amount"] = parse_amount(raw_amount, settings.sentinels) or ZERO
        except ValueError:
            isolated["amount"] = ZERO
        note = record.get("reason", "")
        if raw_amount:
            note = f"{note} 原值 {raw_amount}"
        entries.append(_entry(isolated, "ISOLATED", note=note))

    return {"groups": groups, "entries": entries}


def _totals(entries: list[dict]) -> dict:
    amount = sum((dec(item["amount"]) for item in entries), ZERO)
    cleared = sum((dec(item["cleared"]) for item in entries), ZERO)
    remaining = sum((dec(item["remaining"]) for item in entries), ZERO)
    counts: dict[str, int] = {}
    for item in entries:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {"amount": amount, "cleared": cleared, "remaining": remaining,
            "status_counts": counts}


def _format_entry(entry: dict) -> dict:
    return {
        **entry,
        "amount": format(dec(entry["amount"]), "f"),
        "cleared": format(dec(entry["cleared"]), "f"),
        "remaining": format(dec(entry["remaining"]), "f"),
        "status_label": STATUS_LABELS.get(entry["status"], entry["status"]),
    }


def review(entries: list[dict]) -> dict:
    """复核视图：只读已落库的待清算明细，页面与落库结论永远一致。

    每笔凭证都带实际银行账户、匹配组合与剩余未清金额；未清原因由状态码表达。
    """
    formatted = [_format_entry(entry) for entry in entries]
    formatted.sort(key=lambda item: (item["ledger_txn_no"], item["key"]))
    summary = _totals(entries)
    return {
        "entries": formatted,
        "groups": len({item["match_id"] for item in entries if item["match_id"]}),
        "totals": {
            "records": len(entries),
            "amount": format(summary["amount"], "f"),
            "cleared": format(summary["cleared"], "f"),
            "remaining": format(summary["remaining"], "f"),
            "status_counts": summary["status_counts"],
        },
    }
