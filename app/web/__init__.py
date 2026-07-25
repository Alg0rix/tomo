"""HTML routes and auth forms."""

from fastapi import APIRouter

from .auth import router as auth_router
from .pages import router as pages_router

router = APIRouter()
router.include_router(pages_router)
router.include_router(auth_router)

__all__ = ["router"]
