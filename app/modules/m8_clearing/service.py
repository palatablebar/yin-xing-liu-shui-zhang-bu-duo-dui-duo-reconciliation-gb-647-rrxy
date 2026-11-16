"""待清算服务：复核视图、账户归属确认与撤销。

确认只写账户归属，不直接改匹配结果；确认后立刻用同一套求解重跑本批次，
保证「已确认的账户」落到匹配组，同时保留其余凭证的挂账状态。
"""
from __future__ import annotations

from ...errors import DomainError
from ...manifest import utc_now_iso
from ..m5_matching import service as matching_service
from ..m8_clearing import clearing


def payload(store, run: dict | None, settings) -> dict:
    entries = store.clearing_entries(run["run_id"]) if run else []
    result = clearing.review(entries)
    result["run_id"] = run["run_id"] if run else None
    result["bank_accounts"] = settings.bank_accounts
    result["clearing_accounts"] = settings.clearing_accounts
    return result


def confirm(store, run: dict | None, settings, key: str, bank_account: str,
            note: str = "") -> dict:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 404)
    if bank_account not in settings.bank_accounts:
        raise DomainError("E_BAD_ACCOUNT", "只能确认到已登记的实际银行账户", 400)
    entries = {entry["key"]: entry for entry in store.clearing_entries(run["run_id"])}
    entry = entries.get(key)
    if not entry:
        raise DomainError("E_NO_CLEARING", "待清算记录不存在", 404)
    store.confirm_clearing(key, entry["ledger_txn_no"], bank_account, note, utc_now_iso())
    matching_service.run_matching(store, run, settings)
    return payload(store, store.get_run(run["run_id"]), settings)


def unconfirm(store, run: dict | None, settings, key: str) -> dict:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 404)
    store.unconfirm_clearing(key)
    matching_service.run_matching(store, run, settings)
    return payload(store, store.get_run(run["run_id"]), settings)
