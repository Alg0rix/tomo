"""HTTP JSON + SSE API routes."""

from fastapi import APIRouter

from .approvals import router as approvals_router
from .connector import router as connector_router
from .openai_compat import router as openai_compat_router
from .platform import router as platform_router
from .rest import router as rest_router
from .stream import router as stream_router

router = APIRouter()
router.include_router(rest_router)
router.include_router(platform_router)
router.include_router(stream_router)
router.include_router(connector_router)
router.include_router(approvals_router)
router.include_router(openai_compat_router)

__all__ = ["router"]
