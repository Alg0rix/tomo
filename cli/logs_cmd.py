"""Inspect local application logs using the same reader as the web inspector."""
from __future__ import annotations

import json
import time

from app.core.logging import LEVELS
from app.core.log_reader import LogReader


def add_parser(sub) -> None:
    parser = sub.add_parser("logs", help="Inspect application logs (rotation-aware live tail)")
    parser.add_argument("-f", "--follow", action="store_true")
    parser.add_argument("-n", "--tail", type=int, default=100)
    parser.add_argument("--level", type=str.upper, choices=LEVELS, help="Minimum severity")
    parser.add_argument("--type", dest="log_type", help="Exact type: http, chat, agent, llm, tool, scheduler, telegram, session_title, etc.")
    parser.add_argument("--session", help="Session ID")
    parser.add_argument("--request", help="HTTP request ID")
    parser.add_argument("--event", help="Lifecycle event, e.g. failed or started")
    parser.add_argument("--json", action="store_true", help="Output JSONL")


def run(args) -> int:
    if args.tail < 0:
        raise ValueError("--tail must be non-negative")
    reader = LogReader(tail=args.tail, level=args.level, log_type=args.log_type,
                       session=args.session, request_id=args.request, event=args.event)

    def emit(item):
        record = item["record"]
        if args.json:
            print(json.dumps(record, ensure_ascii=False), flush=True)
        else:
            context = f" session={record['session_id']}" if record.get("session_id") else ""
            print(f"{record['timestamp']} {record['level']:7} [{record['type']}]{context} {record['message']}", flush=True)
            if record.get("exception"):
                print(record["exception"], flush=True)

    try:
        for item in reader.history():
            emit(item)
        if args.follow:
            while True:
                for item in reader.poll():
                    emit(item)
                time.sleep(0.2)
        return 0
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    finally:
        reader.close()
