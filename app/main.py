from __future__ import annotations

import hmac
import os

import httpx
from fastapi import FastAPI, Query
from fastapi.responses import Response

from .torznab import caps_xml, error_xml, results_xml

PROWLARR_URL = os.environ.get("PROWLARR_URL", "http://prowlarr:9696").rstrip("/")
PROWLARR_API_KEY = os.environ.get("PROWLARR_API_KEY", "")
ADAPTER_API_KEY = os.environ.get("ADAPTER_API_KEY", "")
TIMEOUT = float(os.environ.get("PROWLARR_TIMEOUT_SECONDS", "30"))
MAX_RESULTS = min(max(int(os.environ.get("MAX_RESULTS", "200")), 1), 500)
app = FastAPI(title="Prowlarr Torznab Adapter", docs_url=None, redoc_url=None)


def xml(body: bytes, status_code: int = 200) -> Response:
    return Response(body, status_code=status_code, media_type="application/rss+xml")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/torznab")
async def torznab(
    t: str = Query("search"),
    q: str = Query(""),
    apikey: str = Query(""),
    limit: int | None = Query(None),
) -> Response:
    if not ADAPTER_API_KEY or not hmac.compare_digest(apikey, ADAPTER_API_KEY):
        return xml(error_xml(100, "Incorrect API key"), 401)
    if t == "caps":
        return xml(caps_xml())
    if t != "search":
        return xml(error_xml(200, "Unsupported function"))
    if not q.strip():
        return xml(error_xml(200, "Missing search query"))
    if not PROWLARR_API_KEY:
        return xml(error_xml(900, "Adapter is missing its Prowlarr API key"), 500)

    requested_limit = min(max(limit or MAX_RESULTS, 1), MAX_RESULTS)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{PROWLARR_URL}/api/v1/search",
                params={"query": q, "type": "search", "limit": requested_limit, "offset": 0},
                headers={"X-Api-Key": PROWLARR_API_KEY, "Accept": "application/json"},
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError):
        return xml(error_xml(900, "Prowlarr search failed"), 502)

    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("records") or payload.get("results") or []
    else:
        records = []
    return xml(results_xml(records, requested_limit))
