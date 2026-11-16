"""导出服务：报告 CSV 与运行清单 JSON。"""
from __future__ import annotations

from ...errors import DomainError
from ..m6_diff import differences
from ..m8_clearing import clearing as clearing_mod
from . import csv_out, manifest_out


def _reconciliation(store, run: dict, settings) -> dict:
    return differences.reconcile(store.records(run["run_id"]), store.matches(run["run_id"]),
                                 settings, store.clearing_entries(run["run_id"]))


def report(store, run: dict | None, settings) -> str:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 404)
    result = _reconciliation(store, run, settings)
    clearing = clearing_mod.review(store.clearing_entries(run["run_id"]))
    return csv_out.report_text(store.records(run["run_id"]), store.matches(run["run_id"]),
                               result, clearing)


def manifest(store, run: dict | None, settings) -> str:
    if not run:
        raise DomainError("E_NO_RUN", "还没有批次", 404)
    result = _reconciliation(store, run, settings)
    clearing = clearing_mod.review(store.clearing_entries(run["run_id"]))
    return manifest_out.manifest_text(run, result, clearing)


def summary(store, run: dict | None, settings) -> dict:
    if not run:
        return {"run_id": None, "counts": {}, "totals": {}, "clearing": {}}
    result = _reconciliation(store, run, settings)
    clearing = clearing_mod.review(store.clearing_entries(run["run_id"]))
    return {
        "run_id": run["run_id"],
        "counts": result["counts"],
        "totals": {key: format(value, "f") for key, value in result["totals"].items()},
        "clearing": clearing["totals"],
    }
