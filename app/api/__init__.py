"""HTTP layer: pydantic request models, dependency guards and routers.

``router`` is exposed lazily so that importing ``app.api.schemas`` does not drag
in the route module (and through it the model layer).
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import APIRouter

__all__ = ["router"]


def __getattr__(name: str) -> Any:
    if name == "router":
        from app.api.routes import router

        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
