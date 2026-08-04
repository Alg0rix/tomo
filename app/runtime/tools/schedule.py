"""schedule tool — agent-callable schedule harness."""

from __future__ import annotations

import json
from typing import Any


def _ok(**payload: Any) -> str:
    body = {"success": True, **payload}
    return json.dumps(body, ensure_ascii=False, indent=2)


def _err(msg: str, **extra: Any) -> str:
    body = {"success": False, "error": msg, **extra}
    return json.dumps(body, ensure_ascii=False, indent=2)


def _fmt(sch: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sch.get("id"),
        "name": sch.get("name"),
        "agent_id": sch.get("agent_id"),
        "schedule": sch.get("schedule_display") or sch.get("cron") or "",
        "kind": sch.get("schedule_kind") or "interval",
        "message": sch.get("message") or "",
        "enabled": bool(sch.get("enabled")),
        "state": sch.get("state") or ("scheduled" if sch.get("enabled") else "paused"),
        "pause_reason": sch.get("pause_reason") or "",
        "next_run": sch.get("next_run"),
        "last_run": sch.get("last_run"),
        "repeat_times": sch.get("repeat_times"),
        "run_count": sch.get("run_count") or 0,
        "interval_seconds": sch.get("interval_seconds") or 0,
    }


def _default_agent_id(explicit: Any) -> str | None:
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    try:
        from app.runtime.tools.sandbox import current_agent_id

        aid = current_agent_id()
        if aid:
            return aid
    except ImportError:
        pass
    return None


def _resolve(store: Any, ref: str) -> dict[str, Any] | str:
    try:
        sch = store.resolve_schedule(ref)
    except ValueError as exc:
        return _err(str(exc))
    if not sch:
        return _err(
            f"Schedule '{ref}' not found. Use action='list' to inspect jobs."
        )
    return sch


def run(arguments: dict[str, Any]) -> str:
    """Dispatch schedule actions; always returns a JSON string."""
    if not isinstance(arguments, dict):
        return _err("schedule expects a dict of arguments")

    action = str(arguments.get("action") or "").strip().lower()
    if not action:
        return _err("action is required")

    from app.services import store

    if action == "create":
        schedule = arguments.get("schedule")
        message = arguments.get("message")
        if not isinstance(schedule, str) or not schedule.strip():
            return _err("schedule is required for create (e.g. 'every 1h', '0 9 * * *')")
        if not isinstance(message, str) or not message.strip():
            return _err("message is required for create (self-contained agent prompt)")
        agent_id = _default_agent_id(arguments.get("agent_id"))
        if not agent_id:
            return _err("agent_id is required (no calling agent bound)")
        data: dict[str, Any] = {
            "name": (arguments.get("name") or "").strip() or None,
            "agent_id": agent_id,
            "schedule": schedule.strip(),
            "message": message.strip(),
            "enabled": True,
        }
        if data["name"] is None:
            data.pop("name")
        repeat = arguments.get("repeat")
        if repeat is not None:
            try:
                data["repeat_times"] = int(repeat)
            except (TypeError, ValueError):
                return _err("repeat must be an integer")
        try:
            sch = store.create_schedule(data)
        except ValueError as exc:
            return _err(str(exc))
        return _ok(
            schedule_id=sch["id"],
            job=_fmt(sch),
            message=f"Schedule '{sch['name']}' created ({sch.get('schedule_display') or sch.get('cron')}).",
        )

    if action == "list":
        include = bool(arguments.get("include_disabled", False))
        jobs = store.list_schedules(include_disabled=True)
        if not include:
            jobs = [
                j
                for j in jobs
                if j.get("enabled") and j.get("state") not in ("paused", "completed")
            ]
        return _ok(count=len(jobs), jobs=[_fmt(j) for j in jobs])

    ref = arguments.get("schedule_id") or arguments.get("job_id") or arguments.get("id")
    if not isinstance(ref, str) or not ref.strip():
        return _err(f"schedule_id is required for action '{action}'")
    resolved = _resolve(store, ref.strip())
    if isinstance(resolved, str):
        return resolved
    sch = resolved
    sid = sch["id"]

    if action == "remove":
        store.delete_schedule(sid)
        return _ok(
            message=f"Schedule '{sch['name']}' removed.",
            removed={"id": sid, "name": sch["name"]},
        )

    if action == "pause":
        reason = str(arguments.get("reason") or "")
        updated = store.pause_schedule(sid, reason=reason)
        return _ok(job=_fmt(updated or sch), message=f"Schedule '{sch['name']}' paused.")

    if action == "resume":
        try:
            updated = store.resume_schedule(sid)
        except ValueError as exc:
            return _err(str(exc))
        return _ok(
            job=_fmt(updated or sch), message=f"Schedule '{sch['name']}' resumed."
        )

    if action == "update":
        updates: dict[str, Any] = {}
        if arguments.get("name") is not None:
            updates["name"] = str(arguments["name"])
        if arguments.get("message") is not None:
            updates["message"] = str(arguments["message"])
        if arguments.get("schedule") is not None:
            updates["schedule"] = str(arguments["schedule"]).strip()
        if arguments.get("agent_id") is not None:
            updates["agent_id"] = str(arguments["agent_id"]).strip()
        if arguments.get("repeat") is not None:
            try:
                updates["repeat_times"] = int(arguments["repeat"])
            except (TypeError, ValueError):
                return _err("repeat must be an integer")
        if not updates:
            return _err("No updates provided.")
        try:
            updated = store.update_schedule(sid, updates)
        except ValueError as exc:
            return _err(str(exc))
        if not updated:
            return _err(f"Schedule '{sid}' not found")
        return _ok(job=_fmt(updated), message=f"Schedule '{updated['name']}' updated.")

    if action in {"run", "run_now", "trigger"}:
        # Synchronous bridge: schedule the coroutine on a fresh loop if needed.
        import asyncio

        from app.scheduler.runner import run_schedule_now

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                import concurrent.futures

                pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = pool.submit(asyncio.run, run_schedule_now(sid))
                try:
                    result = future.result(timeout=600)
                except concurrent.futures.TimeoutError:
                    future.cancel()
                    return _err("schedule run timed out after 600s")
                finally:
                    pool.shutdown(wait=False)
            else:
                result = asyncio.run(run_schedule_now(sid))
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001
            return _err(f"run failed: {exc}")
        fresh = store.get_schedule(sid) or sch
        return _ok(
            job=_fmt(fresh),
            execution=result,
            message=f"Schedule '{sch['name']}' executed (status={result.get('status')}).",
        )

    if action == "runs":
        limit = arguments.get("limit", 20)
        try:
            limit_i = max(1, min(int(limit), 100))
        except (TypeError, ValueError):
            limit_i = 20
        rows = store.list_schedule_runs(sid, limit=limit_i)
        return _ok(schedule_id=sid, count=len(rows), runs=rows)

    return _err(
        f"Unknown action '{action}'. Use create|list|update|pause|resume|remove|run|runs."
    )


__all__ = ["run"]
