"""learning router: corrections, confirmations, and decay ticks."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.routers.common import _run
from core.contracts import ROLES, SHAPES

router = APIRouter()


class CorrectIn(BaseModel):
    question_id: str
    to_role: str
    confidence: float = 1.0
    actor: str = ""


class ConfirmIn(BaseModel):
    question_id: str


@router.post("/learning/preview")
async def preview(body: CorrectIn):
    """Prior-delta preview before submit: identical math to /correct, zero writes (feature 35)."""
    try:
        return await _run("learn", "preview_correction", body.question_id, body.to_role, body.confidence)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/correct")
async def correct(body: CorrectIn):
    if body.to_role not in ROLES:
        raise HTTPException(status_code=422, detail=f"to_role must be one of {list(ROLES)}")
    return await _run("learn", "correct", body.question_id, body.to_role, body.confidence, body.actor)


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


@router.get("/learning/attribution/{shape}")
async def attribution(shape: str):
    """Cross-session attribution badge: ledger correction events for a shape (feature 36)."""
    if shape not in SHAPES:
        raise HTTPException(status_code=422, detail=f"shape must be one of {list(SHAPES)}")
    count = await _run("learn", "correction_count", shape)
    return {"shape": shape, "corrections": count}


class UndoIn(BaseModel):
    correction_id: str
    actor: str = ""


@router.post("/learning/undo")
async def undo(body: UndoIn):
    """Revert a correction's exact prior delta; only its actor may (feature 38)."""
    try:
        return await _run("learn", "undo", body.correction_id, body.actor)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
