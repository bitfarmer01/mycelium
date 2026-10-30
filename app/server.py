"""FastAPI composition root: the static console plus one router per segment.

Core modules are imported lazily per request (app/routers/common.py) so a
missing one only 503s its own route.
"""
from __future__ import annotations

from app.routers import api, exam, learning, llm, retrieval, vitals, voice
from app.routers.common import STATIC_DIR
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI(title="Mycelium")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), check_dir=False), name="static")
app.include_router(api.router)
app.include_router(retrieval.router)
app.include_router(llm.router)
app.include_router(learning.router)
app.include_router(exam.router)
app.include_router(voice.router)
app.include_router(vitals.router)
