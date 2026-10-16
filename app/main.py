"""FastAPI application for whenwillhazeend.com.

    uvicorn app.main:app --reload --port 8000

The app imports and serves with no network access and no API keys. Live weather
is an optimisation, never a requirement: every model falls back to bundled
climatology and says so in its response.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

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

app = FastAPI(
    title="whenwillhazeend",
    description=DESCRIPTION,
    version=__version__,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.include_router(api_router)

# --------------------------------------------------------------------------- #
# Static files and templates (owned by the WEB agent; mounted defensively so a
# missing directory degrades to a clear message rather than an import error).
# --------------------------------------------------------------------------- #

# On Cloudflare (Python Worker) neither directory is shipped -- Workers Static
# Assets serve the pre-built pages and /static/* -- and jinja2 is not installed,
# so both imports are deferred until we know they are needed.

if settings.static_dir.is_dir():
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
def index(request: Request) -> HTMLResponse:
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
