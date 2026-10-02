"""HTTP JSON + SSE API routes."""

from fastapi import APIRouter

from .approvals import router as approvals_router
from .connector import router as connector_router
from .connections import router as connections_router
from .openai_compat import router as openai_compat_router
from .platform import router as platform_router
from .processes import router as processes_router
from .rest import router as rest_router
from .self_update import router as self_update_router
from .stream import router as stream_router
from .terminals import router as terminals_router

router = APIRouter()
router.include_router(rest_router)
router.include_router(platform_router)
router.include_router(self_update_router)
router.include_router(stream_router)
router.include_router(connector_router)
router.include_router(connections_router)
router.include_router(approvals_router)
router.include_router(openai_compat_router)
router.include_router(terminals_router)
router.include_router(processes_router)

__all__ = ["router"]
