"""匹配服务：求解、落库、查询。"""
from __future__ import annotations

from decimal import Decimal

from ...errors import DomainError
from ..m8_clearing import clearing as clearing_mod
from . import solve


def payload(store, run: dict | None) -> dict:
    if not run:
        return {"run_id": None, "count": 0, "tiers": {}, "groups": [],
                "fee_alloc_total": "0.00", "fee_actual_total": "0.00",
                "clearing": clearing_mod.review([])}
    groups = store.matches(run["run_id"])
    tiers: dict[str, int] = {}
    for group in groups:
        key = str(group["tier"])
        tiers[key] = tiers.get(key, 0) + 1
    fee_alloc = sum((Decimal(group["fee_alloc"]) for group in groups), Decimal("0.00"))
    fee_actual = sum((Decimal(group["fee_actual"]) for group in groups), Decimal("0.00"))
    return {
        "run_id": run["run_id"],
        "count": len(groups),
        "tiers": tiers,
        "groups": groups,
        "fee_alloc_total": format(fee_alloc, "f"),
        "fee_actual_total": format(fee_actual, "f"),
        "clearing": clearing_mod.review(store.clearing_entries(run["run_id"])),
    }


def run_matching(store, run: dict | None, settings) -> dict:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 404)
    candidates = store.candidates(run["run_id"])
    records = store.records(run["run_id"])
    if not candidates and not _has_clearing(run, store, settings):
        raise DomainError("E_NO_CANDIDATES", "还没有候选", 409)
    groups, entries = solve.solve_all(candidates, records, settings, store.confirmations())
    store.replace_matches(run["run_id"], groups, entries)
    # 单边批次重跑后仍是单边，不能因为求解过一次就标成「已匹配」。
    if run.get("bank_sha256") and run.get("ledger_sha256"):
        status = "matched"
    elif run.get("status", "").startswith("partial_"):
        status = run["status"]
    else:
        status = run.get("status", "ready")
    store.set_status(run["run_id"], status)
    return payload(store, store.get_run(run["run_id"]))


def _has_clearing(run: dict, store, settings) -> bool:
    """待清算凭证不生成普通候选，但仍应允许直接清理。"""
    return any(
        record["side"] == "ledger" and record["account"] in settings.clearing_accounts
        for record in store.records(run["run_id"], side="ledger")
    )
