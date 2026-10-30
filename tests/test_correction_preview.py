"""Feature 35 — the preview shown before submitting a correction matches the
post-submit prior EXACTLY: preview endpoint and client calc agree to float precision."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from core import learn

JS_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _learn_router(state):
    """Fake SurrealDB covering preview() then correct() against one shared store."""

    def route(sql, vars):
        if "->cites->chunk" in sql:
            return [{"c": []}]
        if sql.startswith("SELECT * FROM question:"):
            return [{"id": "question:q1", "shape": "who-owns", "taken": "literal"}]
        if sql.startswith("SELECT prior FROM question_shape"):
            return [{"prior": list(state["prior"])}]
        if "UPSERT question_shape" in sql:
            state["prior"] = list(vars["prior"])
            state["writes"] += 1
            return [{"id": "question_shape:who-owns"}]
        if sql.startswith(("CREATE ledger", "CREATE")):
            state["writes"] += 1
            return [{"id": "ledger:x"}]
        return []

    return route


@pytest.fixture
def learn_state(fake_db):
    state = {"prior": [0.5, 0.3, 0.2], "writes": 0}
    fake_db.rows[""] = _learn_router(state)
    return state


# ---------- preview matches the post-submit prior exactly ----------

def test_preview_matches_post_submit_prior_exactly(learn_state):
    pv = asyncio.run(learn.preview_correction("question:q1", "intent", confidence=0.6))
    post = asyncio.run(learn.correct("question:q1", "intent", confidence=0.6))

    assert pv["prior_before"] == post["prior_before"]
    # bit-exact: the preview runs the same prior_update on the same inputs
    assert pv["prior_after"] == post["prior_after"]
    assert pv["prior_after"] == learn_state["prior"]
    assert pv["shape"] == "who-owns" and pv["from_role"] == "literal"


def test_preview_writes_nothing(learn_state, fake_db):
    asyncio.run(learn.preview_correction("question:q1", "intent", confidence=1.0))

    assert learn_state["writes"] == 0
    assert all(
        not s.startswith(("UPSERT", "CREATE", "UPDATE"))
        for s in fake_db.sql()
    )
    assert learn_state["prior"] == [0.5, 0.3, 0.2]


def test_preview_confidence_zero_moves_nothing(learn_state):
    pv = asyncio.run(learn.preview_correction("question:q1", "intent", confidence=0.0))

    assert pv["rate"] == 0.0
    assert pv["prior_after"] == pv["prior_before"]


def test_preview_rejects_unknown_role(learn_state):
    with pytest.raises(ValueError):
        asyncio.run(learn.preview_correction("question:q1", "vibes", confidence=1.0))


# ---------- API surface ----------

@pytest.fixture
def client():
    from app.server import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_preview_route_then_correct_route_agree_exactly(learn_state, client):
    async with client as c:
        pv = (await c.post("/learning/preview", json={
            "question_id": "question:q1", "to_role": "intent", "confidence": 0.6})).json()
        post = (await c.post("/correct", json={
            "question_id": "question:q1", "to_role": "intent", "confidence": 0.6})).json()

    assert pv["prior_after"] == post["prior_after"]
    assert pv["prior_before"] == post["prior_before"]
    assert pv["delta"] == post["prior_after"][1] - post["prior_before"][1]


async def test_preview_route_rejects_unknown_role(learn_state, client):
    async with client as c:
        res = await c.post("/learning/preview", json={
            "question_id": "question:q1", "to_role": "vibes"})
    assert res.status_code == 422


# ---------- client calc (JS mirror) agrees with the endpoint to float precision ----------

def test_client_js_calc_matches_endpoint_to_float_precision(learn_state, client):
    if not shutil.which("node"):
        pytest.skip("node is not installed; the JS mirror cannot be evaluated")
    pv = asyncio.run(learn.preview_correction("question:q1", "intent", confidence=0.6))
    idx = ["literal", "intent", "scope"].index(pv["to_role"])

    script = "import { predictPrior } from 'file://" + str(JS_DIR / "prior_math.js") + "';\n" \
        + "var pv = JSON.parse(process.env.PV);" \
        + "console.log(JSON.stringify(predictPrior(pv.prior_before, pv.idx, pv.rate)));"
    env = {**os.environ, "PV": json.dumps({"prior_before": pv["prior_before"], "idx": idx, "rate": pv["rate"]})}
    proc = subprocess.run(["node", "--input-type=module", "-e", script],
                          check=False, capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, proc.stderr

    client_calc = json.loads(proc.stdout)
    # exact equality, not approx: the console's predicted prior is the server's, bit for bit
    assert client_calc == pv["prior_after"]
