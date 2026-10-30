"""api router: console serving, config, and the live events feed."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse

from app.routers.common import STATIC_DIR, _jsonable

log = logging.getLogger(__name__)

router = APIRouter()

PLACEHOLDER = """<!doctype html><html><head><meta charset="utf-8"><title>Mycelium</title></head>
<body style="font-family:system-ui;padding:2rem"><h1>Mycelium</h1>
<p>The page is not delivered yet. The API is live: try <a href="/vitals">/vitals</a>.</p></body></html>"""


@router.get("/")
async def index():
    page = STATIC_DIR / "index.html"
    if page.is_file():
        return FileResponse(str(page), media_type="text/html")
    return HTMLResponse(PLACEHOLDER)


@router.get("/config")
async def config():
    try:
        from core import db

        rows = await db.q("SELECT * FROM config:main")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {type(e).__name__}")
    if not rows:
        raise HTTPException(status_code=404, detail="config:main not found")
    return _jsonable(rows[0])


@router.websocket("/events")
async def events(ws: WebSocket):
    await ws.accept()
    send_lock = asyncio.Lock()

    async def send(msg: dict):
        async with send_lock:
            await ws.send_text(json.dumps(_jsonable(msg), default=str))

    async def poll_ledger():
        from core import db

        seen: set[str] = set()
        first = True
        while True:
            try:
                rows = await db.q("SELECT * FROM ledger ORDER BY ts DESC LIMIT 20")
                for row in reversed(rows):
                    key = str(row.get("id")) if isinstance(row, dict) else str(row)
                    if key in seen:
                        continue
                    seen.add(key)
                    if not first:
                        await send({"table": "ledger", "action": "CREATE", "row": row})
                first = False
            except (WebSocketDisconnect, RuntimeError) as e:
                if isinstance(e, WebSocketDisconnect):
                    raise
                log.warning("ledger poll failed: %s", type(e).__name__)
            except Exception as e:
                log.warning("ledger poll failed: %s", type(e).__name__)
            await asyncio.sleep(2)

    async def relay(table: str):
        try:
            from core import db

            async for msg in db.live(table):
                await send(msg)
        except WebSocketDisconnect:
            raise
        except Exception as e:
            log.warning("live(%s) failed: %s", table, type(e).__name__)
            if table == "ledger":
                await poll_ledger()

    tasks = [asyncio.create_task(relay("ledger")), asyncio.create_task(relay("candidate"))]

    async def watch_disconnect():
        try:
            while True:
                await ws.receive_text()
        except Exception:
            return

    watcher = asyncio.create_task(watch_disconnect())
    try:
        await watcher
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
