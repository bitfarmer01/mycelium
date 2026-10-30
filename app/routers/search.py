"""search router: GET /search debug endpoint (feature 20).

Returns the ranked retrieval path's output — chunk id, cosine, fitness, rank — for any
query without the answer-path side effects (no context pack, no retrieved_count bump).
Auth-gated when auth is on: with MYCELIUM_AUTH_TOKEN set, requests without the matching
bearer token get 401; unset, the endpoint is open, matching the feature 8 env contract.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Query, Request

from app.routers.common import _mod

router = APIRouter()


def _require_bearer(request: Request) -> None:
    """Feature 20 / feature 8 env contract: gate only when MYCELIUM_AUTH_TOKEN is set.

    Read per request (not at import) so tests and runtime config changes apply live.
    """
    token = os.environ.get("MYCELIUM_AUTH_TOKEN")
    if not token:
        return
    if request.headers.get("authorization") != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="bearer token required")


@router.get("/search")
async def search(request: Request, q: str = Query(..., min_length=1), k: int = Query(8, ge=1, le=50)):
    """Debug view of the ranking path: chunk id, cosine, fitness, and rank per result."""
    _require_bearer(request)
    retrieve = _mod("retrieve")
    learn = _mod("learn")  # canonical alpha/(alpha+beta) fitness; single source of truth

    rows = await retrieve.ranked_chunks(q, k=k)
    return {
        "query": q,
        "results": [
            {
                "rank": i,
                "chunk_id": row.get("id"),
                "cosine": row.get("cosine"),
                "fitness": learn.fitness(float(row.get("alpha") or 1.0), float(row.get("beta") or 1.0)),
                "score": row.get("score"),
            }
            for i, row in enumerate(rows, start=1)
        ],
    }
