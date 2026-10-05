"""Image-only path helpers. The kernel's container mounts are the boundary.

Installed as app.runtime.tools.sandbox ONLY in Dockerfile.sandbox, never imported
by the coordinator. All path resolution and filesystem I/O occurs in-container;
symlink races cannot reinterpret a path against the coordinator filesystem.
"""
from contextlib import nullcontext
from pathlib import Path


def resolve_work_root(agent_id=None):
    return Path.cwd()


def current_agent_id():
    return "container"


def jail_path(root, relative):
    if not isinstance(relative, str) or not relative.strip() or "\x00" in relative:
        return "Error: invalid container path"
    path = Path(relative)
    return path if path.is_absolute() else root / path


def file_execution_guard():
    # Policy admission and mount enforcement belong to the outer broker. All
    # local I/O here remains inside the container even through path aliases.
    return nullcontext()


def dispatch_execution(tool, arguments):
    # Already inside the enforced boundary. No transport/host fallback exists
    # in this stripped image; the coordinator never imports this module.
    return None
