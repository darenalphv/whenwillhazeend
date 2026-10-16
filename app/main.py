"""FastAPI application for whenwillhazeend.com.

    uvicorn app.main:app --reload --port 8000

The app imports and serves with no network access and no API keys. Live weather
is an optimisation, never a requirement: every model falls back to bundled
climatology and says so in its response.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api.routes import router as api_router
from app.config import settings

if TYPE_CHECKING:  # pragma: no cover
    from fastapi.templating import Jinja2Templates

log = logging.getLogger("whenwillhazeend")

DESCRIPTION = """
A Monte-Carlo estimate of two things:

* **how many days** until the current Kalimantan fire season stops burning, and
* **how many years** until there is no peat or forest left in Kalimantan to burn.

Both are *models*, not observations. Every response carries its own `caveats`.
Nothing here is a fallback or a filler value: if a model cannot run, the
endpoint returns 503 and says which component failed.
"""

# Every module the API reaches through deps.call(). Imported once, single-
# threaded, before the first request: route handlers run in a thread pool, and
# concurrent first imports of the same package tripped Python's import-lock
# deadlock detection (_DeadlockError) after every reload.
_WARM_MODULES = (
    "app.data.loader", "app.data.loader_season",
    "app.model.meteo", "app.model.kbdi", "app.model.drought_code",
    "app.model.burn", "app.model.forecast", "app.model.history",
    "app.model.stock", "app.model.emissions", "app.model.transport",
    "app.model.dispersion", "app.model.haze3d", "app.model.health",
    "app.live",
)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    for name in _WARM_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:  # the module exists; one of its imports doesn't
                log.warning("warm import of %s failed: %s", name, exc)
        except Exception as exc:  # noqa: BLE001
            # One broken model must cost only its own endpoints (they 503 with
            # the component named), never the whole app's startup.
            log.warning("warm import of %s failed (%s); its endpoints will 503", name, exc)
    yield


app = FastAPI(
    title="whenwillhazeend",
    description=DESCRIPTION,
    version=__version__,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
    lifespan=_lifespan,
)

app.include_router(api_router)

# Live "this week" data (NEA, FIRMS, CAMS). Wired here so it switches on as
# soon as the router exists; a broken router is logged, never silently hidden.
try:
    from app.api.routes_live import router as _live_router
except ModuleNotFoundError as exc:
    if exc.name not in ("app.api.routes_live",):
        log.warning("live-data router failed to import: %s", exc)
    _live_router = None
except Exception as exc:  # noqa: BLE001
    log.warning("live-data router failed to import: %s", exc)
    _live_router = None
if _live_router is not None:
    app.include_router(_live_router)

# The haze3d payloads run to several hundred KB of JSON; gzip cuts that ~6x.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Liveness for the platform's health checks. Deliberately does no I/O:
    /api/health probes Open-Meteo, and a slow upstream must never get a healthy
    container restarted."""
    return {"status": "ok", "version": __version__}


# --------------------------------------------------------------------------- #
# Production: serve the content-hashed build in public/.
#
# `scripts/build_static.py` writes public/ with every asset URL carrying
# ?v=<content hash>, so /static can be cached for a year and a deploy is still
# picked up on the next page load. Opt-in via SERVE_BUILT=1 (set in the
# Dockerfile) rather than "if public/ exists", because a stale public/ left on a
# dev machine would otherwise silently shadow the live templates.
# --------------------------------------------------------------------------- #

PUBLIC_DIR = Path(__file__).resolve().parents[1] / "public"
SERVE_BUILT = os.environ.get("SERVE_BUILT") == "1" and (PUBLIC_DIR / "index.html").is_file()

_BUILT_HEADERS: dict = {}
if SERVE_BUILT:
    try:
        _BUILT_HEADERS = json.loads((PUBLIC_DIR / "_headers.json").read_text("utf-8"))
    except (OSError, ValueError):
        log.warning("public/_headers.json missing or unreadable; serving without it")


if SERVE_BUILT:
    from fastapi.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory=PUBLIC_DIR / "static"), name="static")

    @app.middleware("http")
    async def _built_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = _BUILT_HEADERS.get(
                "static_cache_control", "public, max-age=31536000, immutable")
        elif not path.startswith("/api/"):
            # pages: always revalidate, so a deploy shows on the next load
            response.headers.setdefault("Cache-Control", "no-cache")
            for key, value in _BUILT_HEADERS.get("html_headers", {}).items():
                response.headers.setdefault(key, value)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    def _page(rel: str) -> FileResponse:
        return FileResponse(PUBLIC_DIR / rel, media_type="text/html")

    @app.get("/", include_in_schema=False)
    def index_built() -> FileResponse:
        return _page("index.html")

    @app.get("/simulator", include_in_schema=False)
    def simulator_built() -> FileResponse:
        return _page("simulator/index.html")

    @app.exception_handler(StarletteHTTPException)
    async def _not_found_page(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and not request.url.path.startswith("/api/") \
                and (PUBLIC_DIR / "404.html").is_file():
            return FileResponse(PUBLIC_DIR / "404.html", status_code=404,
                                media_type="text/html")
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))


# --------------------------------------------------------------------------- #
# Development: live templates and /static straight from app/ (mounted
# defensively so a missing directory degrades to a clear message rather than an
# import error).
# --------------------------------------------------------------------------- #

if SERVE_BUILT:
    pass
elif settings.static_dir.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
else:  # pragma: no cover - Worker bundle, or the frontend has not been written yet
    log.info("static directory %s missing; /static not mounted", settings.static_dir)


def _make_templates() -> Jinja2Templates | None:
    if not settings.templates_dir.is_dir():
        return None
    try:
        from fastapi.templating import Jinja2Templates
    except ImportError:  # pragma: no cover - jinja2 not installed
        log.warning("jinja2 not installed; page routes will return 503")
        return None
    return Jinja2Templates(directory=str(settings.templates_dir))


templates: Jinja2Templates | None = _make_templates()

_MISSING_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>whenwillhazeend — frontend unavailable</title></head>
<body style="background:#12100e;color:#e8e2d9;font:16px/1.6 system-ui;padding:3rem">
<h1>The page template is not available</h1>
<p>The API itself is running. Try
<a style="color:#ffb45e" href="/api/docs">/api/docs</a> or
<a style="color:#ffb45e" href="/api/health">/api/health</a>.</p>
</body></html>"""


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index(request: Request) -> HTMLResponse:  # shadowed by index_built in production
    """Serve the single-page frontend."""
    if templates is None or not (settings.templates_dir / "index.html").is_file():
        return HTMLResponse(
            _MISSING_PAGE, status_code=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"version": __version__, "app_name": settings.app_name},
    )


@app.get("/simulator", response_class=HTMLResponse, include_in_schema=False)
def simulator(request: Request) -> HTMLResponse:
    """The 3D haze simulator. Its own page because deck.gl is a heavy load that
    the main page should not pay for."""
    if templates is None or not (settings.templates_dir / "simulator.html").is_file():
        return HTMLResponse(
            _MISSING_PAGE, status_code=status.HTTP_503_SERVICE_UNAVAILABLE
        )
    return templates.TemplateResponse(
        request=request,
        name="simulator.html",
        context={"version": __version__, "app_name": settings.app_name},
    )


# --------------------------------------------------------------------------- #
# Last-resort handler.
#
# Route handlers already translate data/model failures into 503 with a named
# component. This catches anything that slipped through so that a user never
# receives a bare 500 (and never receives a made-up number in its place).
# --------------------------------------------------------------------------- #


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": (
                f"An internal component failed while handling {request.url.path} "
                f"({type(exc).__name__}). No estimate is returned rather than a "
                "fabricated one."
            )
        },
    )
