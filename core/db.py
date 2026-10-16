"""SurrealDB helper (surrealdb 2.0.0, AsyncSurreal over ws). The only database access path."""
from __future__ import annotations

import asyncio
import datetime as _dt
import decimal
import os
import re
import uuid
from typing import Any, AsyncIterator

from surrealdb import AsyncSurreal

SURREAL_URL = os.environ.get("SURREAL_URL", "ws://localhost:8000/rpc")
NS = "mycelium"
DB = "main"

_conns: dict[int, Any] = {}


async def connect():
    """Signed-in connection using NS/DB, cached per running event loop."""
    loop = asyncio.get_running_loop()
    key = id(loop)
    conn = _conns.get(key)
    if conn is not None:
        return conn
    conn = AsyncSurreal(os.environ.get("SURREAL_URL", SURREAL_URL))
    await conn.connect()
    await conn.signin(
        {
            "username": os.environ.get("SURREAL_USER", "root"),
            "password": os.environ.get("SURREAL_PASS", "root"),
        }
    )
    await conn.use(NS, DB)
    _conns[key] = conn
    return conn


def plain(v: Any) -> Any:
    """Convert SDK values (RecordID, Datetime, Decimal, UUID…) into JSON-friendly Python."""
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, _dt.datetime):
        return v.isoformat()
    dt = getattr(v, "dt", None)
    if isinstance(dt, _dt.datetime):
        return dt.isoformat()
    return str(v)


async def q(sql: str, vars: dict | None = None) -> list:
    """Run SurrealQL; return the rows of the LAST statement as plain dicts ([] on none). Raises RuntimeError on a statement error."""
    conn = await connect()
    resp = await conn.query_raw(sql, vars or {})
    if isinstance(resp, dict) and resp.get("error"):
        raise RuntimeError(f"surreal error: {resp['error']}")
    results = resp.get("result") if isinstance(resp, dict) else resp
    if not results:
        return []
    for st in results:
        if isinstance(st, dict) and str(st.get("status", "OK")).upper() == "ERR":
            raise RuntimeError(f"surreal error: {st.get('result')}")
    last = results[-1]
    out = last.get("result") if isinstance(last, dict) and "status" in last else last
    if out is None:
        return []
    if not isinstance(out, list):
        out = [out]
    return plain(out)


async def live(table: str) -> AsyncIterator[dict]:
    """Yield {"table", "action": CREATE|UPDATE|DELETE, "row"} for every change on `table`."""
    conn = await connect()
    qid = await conn.live(table)
    gen = await conn.subscribe_live(qid)
    try:
        async for msg in gen:
            msg = plain(msg)
            if isinstance(msg, dict) and "action" in msg:
                row = msg.get("result", msg.get("record", {}))
                action = str(msg["action"]).upper()
            else:
                row, action = msg, "UPDATE"
            yield {"table": table, "action": action, "row": row}
    finally:
        try:
            await conn.kill(qid)
        except Exception:
            pass


def now() -> str:
    """UTC ISO 8601 timestamp."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def rid(table: str, key: str) -> str:
    """Safe record id string: plain keys stay bare, anything else is wrapped in ⟨…⟩."""
    key = str(key)
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        return f"{table}:{key}"
    return f"{table}:⟨{key.replace('⟩', '')}⟩"
