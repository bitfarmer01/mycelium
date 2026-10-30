"""learning router: corrections, confirmations, and decay ticks."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.common import _run
from core.contracts import ROLES

router = APIRouter()


class CorrectIn(BaseModel):
    question_id: str
    to_role: str


class ConfirmIn(BaseModel):
    question_id: str


@router.post("/correct")
async def correct(body: CorrectIn):
    if body.to_role not in ROLES:
        raise HTTPException(status_code=422, detail=f"to_role must be one of {list(ROLES)}")
    return await _run("learn", "correct", body.question_id, body.to_role)


@router.post("/confirm")
async def confirm(body: ConfirmIn):
    return await _run("learn", "confirm", body.question_id)


@router.post("/tick")
async def tick():
    return {"decayed": await _run("learn", "decay_tick")}


@router.get("/learning/priors")
async def priors():
    """Per-shape prior values with ledger edit history (feature 31)."""
    try:
        return await _run("learn", "priors_snapshot")
    except Exception as e:  # db down: the dashboard degrades, the rest of the API lives on
        raise HTTPException(status_code=503, detail=f"db unavailable: {type(e).__name__}") from e
