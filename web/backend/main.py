"""EcoFlow Ocean LAN web dashboard — FastAPI entrypoint."""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

# Allow importing bundled pyecoflowocean from the HA custom component tree.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "custom_components" / "ecoflow_ocean"))

from backend.config import Settings  # noqa: E402
from backend.history import HistoryStore  # noqa: E402
from backend.hub import MultiSiteManager  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("ecoflow_web")

FRONTEND_DIR = Path(__file__).resolve().parents[1] / "frontend"

settings: Optional[Settings] = None
history: Optional[HistoryStore] = None
manager: Optional[MultiSiteManager] = None


def get_settings() -> Settings:
    if settings is None:
        raise HTTPException(status_code=503, detail="Settings not loaded")
    return settings


def get_manager() -> MultiSiteManager:
    if manager is None or not manager.ready:
        detail = manager.last_error if manager else "Manager not started"
        raise HTTPException(status_code=503, detail=detail or "Manager not ready")
    return manager


def get_history() -> HistoryStore:
    if history is None:
        raise HTTPException(status_code=503, detail="History not ready")
    return history


def require_token(
    authorization: Optional[str] = Header(default=None),
    x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
) -> None:
    cfg = get_settings()
    if not cfg.web_auth_token:
        return
    provided = x_api_token
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if provided != cfg.web_auth_token:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _resolve_site_id(site_id: Optional[str], mgr: MultiSiteManager) -> str:
    sid = (site_id or "").strip().lower() or mgr.default_site_id()
    if sid not in {s.id for s in mgr.sites}:
        raise HTTPException(status_code=404, detail=f"Unknown site '{sid}'")
    return sid


@asynccontextmanager
async def lifespan(app: FastAPI):
    global settings, history, manager
    settings = Settings.from_env()
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)

    history = HistoryStore(settings.db_path)
    await history.open()

    manager = MultiSiteManager(settings, history)
    try:
        await manager.start()
    except Exception as err:
        _LOGGER.error("Failed to start multi-site manager: %s", err)

    yield

    if manager is not None:
        await manager.stop()
    if history is not None:
        await history.close()


app = FastAPI(title="EcoFlow Ocean Web", version="0.2.0", lifespan=lifespan)


@app.get("/api/health")
@app.get("/health")
async def health() -> dict:
    sites = manager.list_sites() if manager else []
    return {
        "ok": manager is not None and manager.ready,
        "error": manager.last_error if manager else "not started",
        "sites": len(sites),
        "devices": sum(s.get("device_count", 0) for s in sites),
        "status": "ok" if manager is not None and manager.ready else "degraded",
    }


@app.get("/api/sites")
async def list_sites(
    _: None = Depends(require_token),
    mgr: MultiSiteManager = Depends(get_manager),
) -> dict:
    return {
        "sites": mgr.list_sites(),
        "default_site_id": mgr.default_site_id(),
    }


@app.get("/api/overview")
async def overview(
    site: Optional[str] = None,
    _: None = Depends(require_token),
    mgr: MultiSiteManager = Depends(get_manager),
) -> dict:
    return mgr.overview(_resolve_site_id(site, mgr))


@app.get("/api/sites/{site_id}/overview")
async def site_overview(
    site_id: str,
    _: None = Depends(require_token),
    mgr: MultiSiteManager = Depends(get_manager),
) -> dict:
    return mgr.overview(_resolve_site_id(site_id, mgr))


@app.get("/api/devices")
async def devices(
    site: Optional[str] = None,
    _: None = Depends(require_token),
    mgr: MultiSiteManager = Depends(get_manager),
) -> dict:
    data = mgr.overview(_resolve_site_id(site, mgr))
    return {"site_id": data["site_id"], "devices": data["devices"]}


@app.get("/api/history/power")
async def history_power(
    hours: float = 24.0,
    serial: Optional[str] = None,
    site: Optional[str] = None,
    bucket_minutes: int = 5,
    _: None = Depends(require_token),
    mgr: MultiSiteManager = Depends(get_manager),
    store: HistoryStore = Depends(get_history),
) -> dict:
    site_id = _resolve_site_id(site, mgr)
    series = await store.power_series(
        hours=hours,
        serial=serial,
        site_id=site_id,
        bucket_minutes=bucket_minutes,
    )
    return {
        "site_id": site_id,
        "hours": hours,
        "bucket_minutes": bucket_minutes,
        "points": series,
    }


@app.get("/api/history/energy")
async def history_energy(
    hours: float = 24.0,
    serial: Optional[str] = None,
    site: Optional[str] = None,
    _: None = Depends(require_token),
    mgr: MultiSiteManager = Depends(get_manager),
    store: HistoryStore = Depends(get_history),
) -> dict:
    site_id = _resolve_site_id(site, mgr)
    totals = await store.energy_totals(hours=hours, serial=serial, site_id=site_id)
    return {"site_id": site_id, "hours": hours, "totals": totals}


@app.get("/api/history/circuits")
async def history_circuits(
    hours: float = 24.0,
    serial: Optional[str] = None,
    site: Optional[str] = None,
    _: None = Depends(require_token),
    mgr: MultiSiteManager = Depends(get_manager),
    store: HistoryStore = Depends(get_history),
) -> dict:
    """Per-circuit kWh integrated from sampled panel channel watts."""
    site_id = _resolve_site_id(site, mgr)
    hours = max(1.0, min(float(hours), 90 * 24.0))
    if not serial:
        overview = mgr.overview(site_id)
        panel = overview.get("panel") or {}
        serial = panel.get("serial")
    data = await store.circuit_energy_totals(
        hours=hours,
        serial=serial,
        site_id=site_id,
    )
    return {"site_id": site_id, "serial": serial, **data}


@app.get("/api/history/overhead")
async def history_overhead(
    hours: float = 18.0,
    site: Optional[str] = None,
    bucket_minutes: int = 5,
    night_only: bool = False,
    _: None = Depends(require_token),
    mgr: MultiSiteManager = Depends(get_manager),
    store: HistoryStore = Depends(get_history),
) -> dict:
    """Panel vs inverter overhead samples.

    Panel aux (hall_total−channel_sum) and inverter aux (solar−battery−feed)
    are both available continuously now, not just overnight — `night_only`
    is kept as an opt-in filter for anyone specifically after the
    quiet-night-split fallback samples, but the default (and the `stats`
    summary below) covers every bucket with a reading.
    """
    site_id = _resolve_site_id(site, mgr)
    hours = max(1.0, min(float(hours), 90 * 24.0))
    series = await store.overhead_series(
        hours=hours,
        site_id=site_id,
        bucket_minutes=max(1, bucket_minutes),
        night_only=night_only,
    )
    stats = await store.overhead_stats(hours=hours, site_id=site_id)
    return {
        "site_id": site_id,
        "hours": hours,
        "bucket_minutes": bucket_minutes,
        "points": series,
        "overhead_stats": stats,
    }


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    cfg = get_settings()
    token = websocket.query_params.get("token")
    if cfg.web_auth_token and token != cfg.web_auth_token:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    mgr = manager
    if mgr is None:
        await websocket.send_json({"type": "error", "detail": "Manager not ready"})
        await websocket.close()
        return

    site_id = (websocket.query_params.get("site") or mgr.default_site_id()).strip().lower()
    queue = mgr.subscribe()
    try:
        await websocket.send_json({"type": "sites", "sites": mgr.list_sites(), "default_site_id": mgr.default_site_id()})
        await websocket.send_json({"type": "overview", "site_id": site_id, "data": mgr.overview(site_id)})
        while True:
            event = await queue.get()
            if event.get("type") == "site":
                if event.get("site_id") == site_id and event.get("data"):
                    await websocket.send_json(
                        {"type": "overview", "site_id": site_id, "data": event["data"]}
                    )
                await websocket.send_json({"type": "sites", "sites": mgr.list_sites()})
            else:
                await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception as err:
        _LOGGER.debug("WebSocket closed: %s", err)
    finally:
        mgr.unsubscribe(queue)


_NO_CACHE_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}


@app.get("/")
async def index() -> FileResponse:
    # The HTML itself isn't version-stamped (only the CSS/JS/img URLs it
    # references are), so without this header browsers can serve a stale
    # cached copy of index.html that still points at old asset versions —
    # the classic "I deployed a fix but the site still looks old" bug.
    return FileResponse(FRONTEND_DIR / "index.html", headers=_NO_CACHE_HEADERS)


@app.get("/house")
async def house_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "house.html", headers=_NO_CACHE_HEADERS)


@app.get("/flow")
async def flow_redirect() -> RedirectResponse:
    """Flow tab retired in favor of House photo view."""
    return RedirectResponse(url="/house", status_code=302)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
