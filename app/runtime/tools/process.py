"""process tool — list / status / kill background bash jobs."""

from __future__ import annotations

from typing import Any

from app.runtime.tools import process_registry


def run(arguments: dict[str, Any]) -> str:
    """Manage background jobs; always returns a string."""
    if not isinstance(arguments, dict):
        return "Error: process expects a dict of arguments"
    action = arguments.get("action")
    if not isinstance(action, str) or not action.strip():
        return "Error: 'action' must be one of: list, status, kill"
    action = action.strip().lower()
    job_id = arguments.get("id")

    if action == "list":
        jobs = process_registry.list_jobs()
        if not jobs:
            return "No background jobs"
        lines = []
        for job in jobs:
            summary = process_registry.job_summary(job)
            lines.append(
                f"{summary['id']}: {summary['status']} rc={summary['returncode']} "
                f"cmd={summary['command']!r}"
            )
        return "\n".join(lines)

    if action in {"status", "kill"}:
        if not isinstance(job_id, str) or not job_id.strip():
            return "Error: 'id' is required for status/kill"
        job_id = job_id.strip()
        if action == "kill":
            job = process_registry.kill(job_id)
        else:
            job = process_registry.get(job_id)
        if job is None:
            return f"Error: unknown job id {job_id!r}"
        summary = process_registry.job_summary(job)
        parts = [
            f"id: {summary['id']}",
            f"status: {summary['status']}",
            f"returncode: {summary['returncode']}",
            f"command: {summary['command']}",
        ]
        if job.stdout.strip():
            parts.append(f"stdout:\n{job.stdout.rstrip()}")
        if job.stderr.strip():
            parts.append(f"stderr:\n{job.stderr.rstrip()}")
        return "\n".join(parts)

    return "Error: 'action' must be one of: list, status, kill"


__all__ = ["run"]
