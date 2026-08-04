"""Schedule string parsing and next-run computation.

Accepted forms
--------------
* Duration one-shot: ``30m``, ``2h``, ``1d`` → fire once after that delay
* Interval: ``every 30m``, ``every 2h``, ``every 1d``
* 5-field cron: ``0 9 * * *`` (minute hour dom month dow)
* ISO timestamp one-shot: ``2026-06-01T09:00:00`` / ``...Z``
* Bare seconds (legacy UI): ``3600`` → interval every 3600s
"""

from __future__ import annotations

import calendar
import re
import time
from datetime import datetime, timedelta
from typing import Any

ONESHOT_GRACE_SECONDS = 120
MIN_INTERVAL_SECONDS = 5
MAX_INTERVAL_SECONDS = 86400 * 90  # 90 days
_CLAIM_TTL_DEFAULT = 300

_DURATION_RE = re.compile(
    r"^(?:(?P<days>\d+)\s*d)?\s*(?:(?P<hours>\d+)\s*h)?\s*"
    r"(?:(?P<minutes>\d+)\s*m)?\s*(?:(?P<seconds>\d+)\s*s)?$",
    re.IGNORECASE,
)
_BARE_SECONDS_RE = re.compile(r"^\d+$")
_CRON_FIELD_RE = re.compile(r"^[\d\*\-,/]+$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _now_ts() -> float:
    return time.time()


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_local_tz())
    return dt


def parse_duration(text: str) -> int:
    """Parse ``30m`` / ``2h`` / ``1d`` / ``1h30m`` / ``90s`` → seconds."""
    raw = (text or "").strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("empty duration")
    if _BARE_SECONDS_RE.match(raw):
        secs = int(raw)
        if secs <= 0:
            raise ValueError("duration must be > 0")
        return secs
    # Single unit shorthand without unit on multi-digit alone already handled
    m = _DURATION_RE.match(raw)
    if not m or not any(m.groupdict().values()):
        raise ValueError(f"invalid duration '{text}'")
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    if total <= 0:
        raise ValueError(f"invalid duration '{text}'")
    return total


def _format_duration(seconds: int) -> str:
    if seconds % 86400 == 0 and seconds >= 86400:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0 and seconds >= 3600:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0 and seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def parse_schedule(schedule: str, *, now: float | None = None) -> dict[str, Any]:
    """Parse a schedule string into a structured dict.

    Returns
    -------
    dict with keys:
      kind: ``once`` | ``interval`` | ``cron``
      display: human label
      interval_seconds: int (interval only; 0 otherwise)
      expr: cron expression or ISO run_at (once/cron)
      run_at: float unix timestamp (once only)
    """
    original = (schedule or "").strip()
    if not original:
        raise ValueError("schedule is required")
    ts = now if now is not None else _now_ts()
    lower = original.lower()

    # "every X" → recurring interval
    if lower.startswith("every "):
        duration_str = original[6:].strip()
        try:
            seconds = parse_duration(duration_str)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported interval phrase '{original}'. "
                "Use 'every 30m', 'every 2h', or a 5-field cron."
            ) from exc
        if seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(f"interval must be >= {MIN_INTERVAL_SECONDS}s")
        if seconds > MAX_INTERVAL_SECONDS:
            raise ValueError(f"interval must be <= {MAX_INTERVAL_SECONDS}s")
        return {
            "kind": "interval",
            "interval_seconds": seconds,
            "expr": "",
            "run_at": None,
            "display": f"every {_format_duration(seconds)}",
        }

    # 5-field cron
    parts = original.split()
    if len(parts) == 5 and all(_CRON_FIELD_RE.match(p) for p in parts):
        # Validate by attempting a next-run computation
        try:
            _cron_next_after(original, ts)
        except ValueError as exc:
            raise ValueError(f"Invalid cron expression '{original}': {exc}") from exc
        return {
            "kind": "cron",
            "interval_seconds": 0,
            "expr": original,
            "run_at": None,
            "display": original,
        }

    # ISO timestamp one-shot
    if "T" in original or _ISO_DATE_RE.match(original):
        try:
            dt = datetime.fromisoformat(original.replace("Z", "+00:00"))
            dt = _aware(dt)
            run_at = dt.timestamp()
        except ValueError as exc:
            raise ValueError(f"Invalid timestamp '{original}': {exc}") from exc
        return {
            "kind": "once",
            "interval_seconds": 0,
            "expr": datetime.fromtimestamp(run_at, tz=_local_tz()).isoformat(
                timespec="seconds"
            ),
            "run_at": run_at,
            "display": f"once at {datetime.fromtimestamp(run_at, tz=_local_tz()).strftime('%Y-%m-%d %H:%M')}",
        }

    # Bare integer seconds → interval (UI / REST legacy)
    if _BARE_SECONDS_RE.match(original):
        seconds = int(original)
        if seconds < MIN_INTERVAL_SECONDS:
            raise ValueError(f"interval must be >= {MIN_INTERVAL_SECONDS}s")
        if seconds > MAX_INTERVAL_SECONDS:
            raise ValueError(f"interval must be <= {MAX_INTERVAL_SECONDS}s")
        return {
            "kind": "interval",
            "interval_seconds": seconds,
            "expr": "",
            "run_at": None,
            "display": f"every {_format_duration(seconds)}",
        }

    # Duration one-shot: "30m", "2h"
    try:
        seconds = parse_duration(original)
    except ValueError:
        raise ValueError(
            f"Invalid schedule '{original}'. Use:\n"
            "  - Duration: '30m', '2h', '1d' (one-shot)\n"
            "  - Interval: 'every 30m', 'every 2h' (recurring)\n"
            "  - Cron: '0 9 * * *' (5-field)\n"
            "  - Timestamp: '2026-06-01T09:00:00' (one-shot at time)\n"
            "  - Seconds: '3600' (every 3600s)"
        ) from None
    run_at = ts + seconds
    return {
        "kind": "once",
        "interval_seconds": 0,
        "expr": datetime.fromtimestamp(run_at, tz=_local_tz()).isoformat(
            timespec="seconds"
        ),
        "run_at": run_at,
        "display": f"once in {original}",
    }


def compute_next_run(
    parsed: dict[str, Any],
    *,
    now: float | None = None,
    last_run: float | None = None,
    from_time: float | None = None,
) -> float | None:
    """Next fire time (unix seconds), or None if no more runs.

    * ``once`` — returns run_at if still within grace and never run; else None
    * ``interval`` — last_run + interval (or now + interval on first schedule)
    * ``cron`` — next matching wall-clock after ``from_time`` or now
    """
    ts = now if now is not None else _now_ts()
    kind = parsed.get("kind")
    if kind == "once":
        run_at = parsed.get("run_at")
        if run_at is None:
            return None
        if last_run is not None:
            return None
        if float(run_at) >= ts - ONESHOT_GRACE_SECONDS:
            return float(run_at)
        return None

    if kind == "interval":
        seconds = int(parsed.get("interval_seconds") or 0)
        if seconds <= 0:
            return None
        base = from_time if from_time is not None else (
            last_run if last_run is not None else ts
        )
        if last_run is None and from_time is None:
            return ts + seconds
        nxt = float(base) + seconds
        # Fast-forward if we're far behind (missed many ticks)
        if nxt < ts - seconds:
            missed = int((ts - nxt) // seconds) + 1
            nxt = nxt + missed * seconds
        return nxt

    if kind == "cron":
        expr = parsed.get("expr") or ""
        if not expr:
            return None
        after = from_time if from_time is not None else ts
        return _cron_next_after(expr, after)

    return None


def catchup_grace_seconds(parsed: dict[str, Any]) -> int:
    """How late a job may still fire before fast-forwarding past the slot."""
    min_g, max_g = 120, 7200
    kind = parsed.get("kind")
    if kind == "interval":
        period = int(parsed.get("interval_seconds") or 60)
        return max(min_g, min(period // 2, max_g))
    if kind == "cron":
        # Estimate period from two successive fires
        try:
            t0 = _now_ts()
            a = _cron_next_after(parsed.get("expr") or "", t0)
            b = _cron_next_after(parsed.get("expr") or "", a)
            period = max(60, int(b - a))
            return max(min_g, min(period // 2, max_g))
        except ValueError:
            return min_g
    return min_g


def parsed_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a parsed schedule dict from a schedule row (DB/API shape)."""
    kind = (row.get("schedule_kind") or "").strip() or "interval"
    if kind == "cron":
        expr = (row.get("schedule_expr") or row.get("cron") or "").strip()
        return {
            "kind": "cron",
            "interval_seconds": 0,
            "expr": expr,
            "run_at": None,
            "display": row.get("schedule_display") or expr,
        }
    if kind == "once":
        run_at = row.get("next_run")
        expr = (row.get("schedule_expr") or "").strip()
        if run_at is None and expr:
            try:
                run_at = datetime.fromisoformat(expr.replace("Z", "+00:00")).timestamp()
            except ValueError:
                run_at = None
        elif isinstance(run_at, str):
            try:
                run_at = datetime.fromisoformat(run_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                try:
                    run_at = float(run_at)
                except ValueError:
                    run_at = None
        elif run_at is not None:
            try:
                run_at = float(run_at)
            except (TypeError, ValueError):
                run_at = None
        return {
            "kind": "once",
            "interval_seconds": 0,
            "expr": expr,
            "run_at": float(run_at) if run_at is not None else None,
            "display": row.get("schedule_display") or "once",
        }
    # interval (default / legacy)
    seconds = int(row.get("interval_seconds") or 0)
    if seconds <= 0 and (row.get("cron") or "").strip():
        # Best-effort legacy cron display → treat as interval heuristic
        try:
            seconds = _legacy_interval_from_cron(str(row.get("cron") or ""))
        except Exception:
            seconds = 3600
    effective_seconds = max(seconds, MIN_INTERVAL_SECONDS) if seconds else 3600
    return {
        "kind": "interval",
        "interval_seconds": effective_seconds,
        "expr": "",
        "run_at": None,
        "display": row.get("schedule_display")
        or f"every {_format_duration(effective_seconds)}",
    }


def _legacy_interval_from_cron(cron: str) -> int:
    parts = (cron or "").strip().split()
    if len(parts) >= 1 and parts[0].startswith("*/"):
        try:
            minutes = int(parts[0][2:])
            if minutes > 0:
                return minutes * 60
        except ValueError:
            pass
    if cron.lower().startswith("every "):
        try:
            return parse_duration(cron[6:])
        except ValueError:
            pass
    if len(parts) >= 5:
        return 86400
    return 3600


# ---------------------------------------------------------------------------
# Minimal 5-field cron next-run (no croniter dependency)
# ---------------------------------------------------------------------------


def _expand_field(field: str, minimum: int, maximum: int) -> set[int]:
    """Expand a cron field into the set of allowed integers."""
    out: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = int(step_s)
            if step <= 0:
                raise ValueError(f"invalid step in '{field}'")
        else:
            base = part
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            if base == "*" or base == "":
                start, end = minimum, maximum
            else:
                val = int(base)
                if step == 1 and "/" not in part:
                    if not (minimum <= val <= maximum):
                        raise ValueError(f"value {val} out of range in '{field}'")
                    out.add(val)
                    continue
                start = val
                end = maximum if "/" in part else val
        if start > end or start < minimum or end > maximum:
            raise ValueError(f"range out of bounds in '{field}'")
        for v in range(start, end + 1, step):
            out.add(v)
    if not out:
        raise ValueError(f"empty field '{field}'")
    return out


def _cron_next_after(expr: str, after_ts: float) -> float:
    """Return the next cron fire strictly after ``after_ts`` (unix seconds)."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron must have 5 fields")
    minutes = _expand_field(parts[0], 0, 59)
    hours = _expand_field(parts[1], 0, 23)
    doms = _expand_field(parts[2], 1, 31)
    months = _expand_field(parts[3], 1, 12)
    dows = _expand_field(parts[4], 0, 6)  # 0=Sunday

    # Start at the next whole minute after after_ts
    dt = datetime.fromtimestamp(after_ts, tz=_local_tz()) + timedelta(minutes=1)
    dt = dt.replace(second=0, microsecond=0)

    dom_star = parts[2] == "*"
    dow_star = parts[4] == "*"
    # Search up to ~2 years; bail if nothing matches
    limit = dt + timedelta(days=366 * 2)
    while dt <= limit:
        if dt.month not in months:
            y, m = dt.year, dt.month
            while True:
                m += 1
                if m > 12:
                    m = 1
                    y += 1
                if m in months:
                    break
                if y > dt.year + 2:
                    raise ValueError(f"no match for cron '{expr}'")
            dt = dt.replace(year=y, month=m, day=1, hour=0, minute=0)
            continue
        dim = calendar.monthrange(dt.year, dt.month)[1]
        if dt.day > dim:
            dt = (dt.replace(day=1) + timedelta(days=32)).replace(
                day=1, hour=0, minute=0
            )
            continue
        # Standard cron: if both DOM and DOW are restricted, match EITHER.
        weekday = (dt.weekday() + 1) % 7  # Mon=1 … Sat=6, Sun=0
        if dom_star and dow_star:
            day_ok = True
        elif dom_star:
            day_ok = weekday in dows
        elif dow_star:
            day_ok = dt.day in doms
        else:
            day_ok = dt.day in doms or weekday in dows
        if not day_ok:
            dt = (dt + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if dt.hour not in hours:
            for h in range(dt.hour + 1, 24):
                if h in hours:
                    dt = dt.replace(hour=h, minute=0)
                    break
            else:
                dt = (dt + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if dt.minute not in minutes:
            for m in range(dt.minute + 1, 60):
                if m in minutes:
                    dt = dt.replace(minute=m)
                    break
            else:
                dt = (dt + timedelta(hours=1)).replace(minute=0)
            continue
        return dt.timestamp()
    raise ValueError(f"no upcoming match for cron '{expr}'")


__all__ = [
    "ONESHOT_GRACE_SECONDS",
    "MIN_INTERVAL_SECONDS",
    "MAX_INTERVAL_SECONDS",
    "parse_duration",
    "parse_schedule",
    "compute_next_run",
    "catchup_grace_seconds",
    "parsed_from_row",
]
