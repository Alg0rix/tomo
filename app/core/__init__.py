"""Core configuration and shared dependencies."""

from .config import BRAND, BRAND_SUBTITLE, DATA_DIR, HOST, PORT, RELOAD, STATIC_DIR, TEMPLATE_DIR
from .deps import AuthDep, authenticate, templates

__all__ = [
    "AuthDep",
    "BRAND",
    "BRAND_SUBTITLE",
    "DATA_DIR",
    "HOST",
    "PORT",
    "RELOAD",
    "STATIC_DIR",
    "TEMPLATE_DIR",
    "authenticate",
    "templates",
]
