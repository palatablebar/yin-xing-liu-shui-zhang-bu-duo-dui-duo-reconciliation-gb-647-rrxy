"""待清算入口：复核清单与账户归属确认。"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from . import service

router = APIRouter(prefix="/api/clearing", tags=["待清算"])


class ConfirmBody(BaseModel):
    key: str
    bank_account: str
    note: str = ""


@router.get("")
def get_clearing(request: Request):
    store = request.app.state.store
    return service.payload(store, store.latest_run(), request.app.state.settings)


@router.post("/confirm")
def post_confirm(request: Request, body: ConfirmBody):
    store = request.app.state.store
    return service.confirm(store, store.latest_run(), request.app.state.settings,
                           body.key, body.bank_account, body.note)


@router.delete("/confirm")
def delete_confirm(request: Request, key: str):
    store = request.app.state.store
    return service.unconfirm(store, store.latest_run(), request.app.state.settings, key)
