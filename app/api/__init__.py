"""HTTP JSON + SSE API routes."""

from fastapi import APIRouter

from .platform import router as platform_router
from .rest import router as rest_router
from .stream import router as stream_router

router = APIRouter()
router.include_router(rest_router)
router.include_router(platform_router)
router.include_router(stream_router)

__all__ = ["router"]
