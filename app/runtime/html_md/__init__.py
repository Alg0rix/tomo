"""HTML → Markdown converter (CommonMark).

Used by web_fetch and any other callers that need HTML conversion.
"""

from __future__ import annotations

from app.runtime.html_md.service import HtmlToMarkdown

__all__ = ["HtmlToMarkdown"]
