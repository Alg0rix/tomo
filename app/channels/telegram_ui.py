"""One Telegram turn: quiet activity card, streamed answer, and bound HITL controls."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.channels.telegram_format import render_markdown, split_html
from app.runtime.permissions import hitl
from app.services.store import store

if TYPE_CHECKING:
    from app.channels.telegram import TelegramAPI


@dataclass
class Prompt:
    token: str
    kind: str
    payload: dict[str, Any]
    message_id: int
    awaiting_text: bool = False
    confirming_always: bool = False


class TelegramTurnUI:
    def __init__(
        self,
        api: TelegramAPI,
        chat_id: int,
        session_id: str,
        *,
        actor_id: int | None = None,
        reply_to: int | None = None,
        thread_id: int | None = None,
    ) -> None:
        self.api = api
        self.chat_id = chat_id
        self.session_id = session_id
        self.actor_id = actor_id if actor_id is not None else chat_id
        self.reply_to = reply_to
        self.thread_id = thread_id
        session = store.get_session(session_id) or {}
        self.agent_id = session.get("coordinator_id")
        self.token = secrets.token_urlsafe(12)
        self.started = time.monotonic()
        self.phase = "Thinking"
        self.tools: dict[str, str] = {}
        self.completed = 0
        self.failed = 0
        self.todos: list[dict] = []
        self.finished = False
        self.stop_requested = False
        self.recent: list[str] = []
        self.prompts: dict[str, Prompt] = {}
        self.status_id: int | None = None
        self.answer_id: int | None = None
        self.answer = ""
        self.last_preview = ""
        self.draft_id = secrets.randbits(31) or 1
        self.last_draft_at = 0.0
        self.last_status = ""
        self.outcome = "Done"
        self._ticker: asyncio.Task | None = None
        self.receiving_task: asyncio.Task | None = None
        self.on_stop = None
        self.on_mode = None
        self.steer_receipts: dict[str, dict] = {}
        self.input_mode = "steer"
        self.queue_depth = 0
        self.progress_count = 0
        self.last_progress = ""
        self._io_lock = asyncio.Lock()

    def stop_keyboard(self) -> dict:
        return {
            "inline_keyboard": [
                [{"text": "■ Stop", "callback_data": f"ts:{self.token}"}],
                [
                    {
                        "text": ("✓ " if self.input_mode == mode else "")
                        + mode.capitalize(),
                        "callback_data": f"tm:{self.token}:{mode}",
                    }
                    for mode in ("steer", "queue", "interrupt")
                ],
            ]
        }

    def status_text(self, *, finished: bool = False) -> str:
        elapsed = int(time.monotonic() - self.started)
        if not finished:
            elapsed = (elapsed // 5) * 5
        phase = self.phase
        if self.waiting:
            phase = (
                "Waiting for approval"
                if any(p.kind == "approval" for p in self.prompts.values())
                else "Waiting for your answer"
            )
        lines = [
            f"{'✓' if finished and self.outcome == 'Done' else '◌'} {self.outcome if finished else phase} · {elapsed}s"
        ]
        if self.tools:
            lines.append(
                ("Pending tools: " if self.waiting else "Running: ")
                + ", ".join(list(self.tools.values())[-3:])
            )
        lines.extend(self.recent[-3:])
        if self.todos:
            done = sum(t.get("status") == "completed" for t in self.todos)
            lines.append(f"Plan: {done}/{len(self.todos)} complete")
            current = next(
                (t for t in self.todos if t.get("status") == "in_progress"), None
            )
            if current:
                lines.append("→ " + str(current.get("content") or "")[:100])
        if self.completed:
            lines.append(
                f"{self.completed} tool call(s) completed"
                + (f" · {self.failed} failed" if self.failed else "")
            )
        from app.runtime.permissions.modes import mode_payload

        lines.append("Approvals: " + mode_payload(self.session_id)["label"])
        lines.append(f"New messages: {self.input_mode} · Queued: {self.queue_depth}")
        return "\n".join(lines)

    async def start(self) -> None:
        with contextlib.suppress(Exception):
            result = await self.api.send_message(
                self.chat_id,
                self.status_text(),
                silent=True,
                reply_markup=self.stop_keyboard(),
                reply_to=self.reply_to,
                thread_id=self.thread_id,
            )
            self.status_id = result.get("message_id")
            self.last_status = self.status_text()
        self._ticker = asyncio.create_task(self._tick())

    async def _tick(self) -> None:
        typing_after = 0.0
        while True:
            now = time.monotonic()
            if now >= typing_after and not self.waiting:
                try:
                    await self.api.send_typing(self.chat_id, thread_id=self.thread_id)
                    typing_after = now + 4
                except Exception:
                    typing_after = now + 30
            with contextlib.suppress(Exception):
                await self.refresh()
            await asyncio.sleep(2)

    @property
    def waiting(self) -> bool:
        return bool(self.prompts)

    def pending_payload(self, prompt: Prompt) -> dict | None:
        pending = hitl.list_pending_for_session(self.session_id)
        collection = "approvals" if prompt.kind == "approval" else "clarifies"
        return next(
            (p for p in pending[collection] if p["id"] == prompt.payload["id"]), None
        )

    async def refresh(self) -> None:
        async with self._io_lock:
            for token, prompt in list(self.prompts.items()):
                if self.pending_payload(prompt) is None:
                    self.prompts.pop(token, None)
                    with contextlib.suppress(Exception):
                        await self.api.edit_message(
                            self.chat_id,
                            prompt.message_id,
                            "This request expired or was resolved elsewhere.",
                        )
            if not self.waiting and self.phase.startswith("Waiting"):
                self.phase = "Thinking"
            text = self.status_text()
            if self.status_id is not None and text != self.last_status:
                await self.api.edit_message(
                    self.chat_id,
                    self.status_id,
                    text,
                    reply_markup=self.stop_keyboard(),
                )
                self.last_status = text
            # Never publish private reasoning; only user-facing answer deltas.
            if self.answer and self.api.rich_enabled:
                preview_text = self.answer[:4000] + "\n\n…"
                now = time.monotonic()
                if preview_text != self.last_preview or (
                    self.last_draft_at and now - self.last_draft_at >= 20
                ):
                    if self.answer_id is None and await self.api.send_rich_draft(
                        self.chat_id,
                        self.draft_id,
                        preview_text,
                        thread_id=self.thread_id,
                    ):
                        self.last_draft_at = now
                    elif self.answer_id is None:
                        sent = await self.api.send_answer(
                            self.chat_id,
                            preview_text,
                            reply_to=self.reply_to,
                            thread_id=self.thread_id,
                            preview=True,
                        )
                        self.answer_id = sent.get("message_id")
                    else:
                        await self.api.edit_answer(
                            self.chat_id,
                            self.answer_id,
                            preview_text,
                            thread_id=self.thread_id,
                            preview=True,
                        )
                    self.last_preview = preview_text
                return
            chunks = (
                split_html(render_markdown(self.answer[:4000]), limit=3600)
                if self.answer
                else []
            )
            preview = chunks[0] + "\n\n…" if chunks else ""
            if preview and preview != self.last_preview:
                if self.answer_id is None:
                    sent = await self.api.send_html(
                        self.chat_id,
                        preview,
                        reply_to=self.reply_to,
                        thread_id=self.thread_id,
                    )
                    self.answer_id = sent.get("message_id")
                else:
                    await self.api.edit_html(self.chat_id, self.answer_id, preview)
                self.last_preview = preview

    async def consume(self, chunk: str) -> None:
        for block in chunk.strip().split("\n\n"):
            event = ""
            data_lines: list[str] = []
            for line in block.splitlines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: "):
                    data_lines.append(line[6:])
            try:
                data = json.loads("\n".join(data_lines))
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            if event == "user" and data.get("steered"):
                receipt = self.steer_receipts.pop(data.get("steer_id"), None)
                if receipt is not None:
                    receipt["consumed"] = True
                    if receipt.get("feedback_id"):
                        with contextlib.suppress(Exception):
                            await self.api.edit_message(
                                self.chat_id,
                                receipt["feedback_id"],
                                "✓ Agent read your guidance and will use it in the next model round.",
                            )
                    self.recent.append("↳ Guidance read by agent")
                    self.recent = self.recent[-3:]
            elif event == "assistant_progress" and not data.get("delegate_call_id"):
                content = str(data.get("content") or "").strip()[:1000]
                if (
                    content
                    and content != self.last_progress
                    and self.progress_count < 10
                ):
                    self.last_progress = content
                    self.progress_count += 1
                    with contextlib.suppress(Exception):
                        await self.api.send_message(
                            self.chat_id,
                            "↳ " + content,
                            formatted=True,
                            silent=True,
                            thread_id=self.thread_id,
                        )
            elif event in {"approval_required", "clarify_required"}:
                await self.show_prompt(
                    "approval" if event == "approval_required" else "clarify", data
                )
            elif event == "tool":
                tool = str(data.get("tool") or "tool")[:80]
                key = f"{data.get('agent_id')}:{data.get('delegate_call_id')}:{data.get('call_id') or tool}"
                agent = str(data.get("agent") or "")[:40]
                self.tools[key] = tool + (f" · {agent}" if agent else "")
                self.phase = "Working"
            elif event == "tool_result":
                tool = str(data.get("tool") or "tool")[:80]
                key = f"{data.get('agent_id')}:{data.get('delegate_call_id')}:{data.get('call_id') or tool}"
                self.tools.pop(key, None)
                self.completed += 1
                self.failed += bool(data.get("error"))
                agent = str(data.get("agent") or "")[:40]
                self.recent.append(
                    f"{'✗' if data.get('error') else '✓'} {tool}"
                    + (f" · {agent}" if agent else "")
                )
                self.recent = self.recent[-3:]
                self.phase = "Working" if self.tools else "Thinking"
            elif event == "todos":
                self.todos = [
                    t for t in (data.get("todos") or [])[:20] if isinstance(t, dict)
                ]
            elif event in {"delegate", "subagent_start"}:
                self.phase = "Delegating"
                self.recent.append(
                    "↗ " + str(data.get("agent") or data.get("to") or "Agent")[:80]
                )
                self.recent = self.recent[-3:]
            elif event == "subagent_done" or (
                event == "error" and data.get("delegate_call_id")
            ):
                failed = event == "error" or data.get("status") in {"error", "failed"}
                agent = str(data.get("agent") or data.get("agent_id") or "Agent")[:80]
                self.recent.append(
                    f"{'✗' if failed else '✓'} {agent} {'failed' if failed else 'finished'}"
                )
                self.recent = self.recent[-3:]
            elif event in {"thinking", "thinking_delta", "status"}:
                if not self.waiting and not self.tools:
                    self.phase = "Thinking"
            elif (
                event in {"delta", "done"}
                and not data.get("delegate_call_id")
                and data.get("agent_id") in {None, self.agent_id}
            ):
                content = str(data.get("content") or "")
                self.answer = (
                    content if event == "done" else (self.answer + content)[:8000]
                )
                if not self.waiting:
                    self.phase = "Writing"
            elif (
                event == "error"
                and not data.get("delegate_call_id")
                and data.get("agent_id") in {None, self.agent_id}
            ):
                self.outcome = (
                    "Stopped" if data.get("code") == "cancelled" else "Failed"
                )

    async def show_prompt(self, kind: str, payload: dict) -> None:
        if any(p.payload["id"] == payload.get("id") for p in self.prompts.values()):
            return
        token = secrets.token_urlsafe(12)
        rows: list[list[dict[str, str]]] = []
        if kind == "approval":
            preview = payload.get("args_preview") or {}
            # Match the web preview while keeping environment values off the channel.
            if isinstance(preview, dict):
                preview = {
                    k: (
                        "[redacted]"
                        if any(
                            s in k.lower()
                            for s in ("password", "token", "secret", "api_key")
                        )
                        else v
                    )
                    for k, v in preview.items()
                    if k != "env"
                }
                preview = json.dumps(preview, ensure_ascii=False, indent=2)
            findings = "\n".join(
                str(f.get("description") or "")[:200]
                for f in payload.get("findings", [])[:3]
            )
            text = f"⚠ Approval required · {str(payload.get('tool') or 'tool')[:80]}\n\n{str(payload.get('description') or '')[:500]}"
            if findings:
                text += "\n" + findings
            text += "\n\n" + str(preview)[:1200]
            text += "\n\nAllow once runs this request. Session and Always grant broader permission."
            labels = {
                "once": "Allow once",
                "session": "This session",
                "always": "Always allow",
                "deny": "Deny",
            }
            allowed = list(payload.get("choices") or ["once", "deny"])
            if not payload.get("allow_permanent", True) or payload.get("smart_denied"):
                allowed = [c for c in allowed if c != "always"]
            if not payload.get("allow_session", True) or payload.get("smart_denied"):
                allowed = [c for c in allowed if c != "session"]
            buttons = [
                {"text": labels[c], "callback_data": f"ta:{token}:{c}"}
                for c in allowed
                if c in labels
            ]
            rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
            self.phase = "Waiting for approval"
        else:
            text = (
                "❓ " + str(payload.get("question") or "Your answer is needed")[:1400]
            )
            choices = (payload.get("choices") or [])[:4]
            for index, choice in enumerate(choices):
                text += f"\n{index + 1}. {str(choice)[:300]}"
                rows.append(
                    [
                        {
                            "text": f"{index + 1}. {str(choice)[:45]}",
                            "callback_data": f"tc:{token}:{index}",
                        }
                    ]
                )
            rows.append(
                [{"text": "Type an answer", "callback_data": f"tc:{token}:other"}]
            )
            text += "\n\nTap a choice or reply to this message with your answer."
            self.phase = "Waiting for your answer"
        try:
            result = await self.api.send_message(
                self.chat_id,
                text,
                reply_markup={"inline_keyboard": rows},
                thread_id=self.thread_id,
            )
            message_id = result.get("message_id")
            if not isinstance(message_id, int):
                raise RuntimeError("Telegram prompt has no message ID")
            self.prompts[token] = Prompt(
                token,
                kind,
                payload,
                message_id,
                awaiting_text=kind == "clarify" and not payload.get("choices"),
            )
        except Exception:
            # A missing control must never leave the agent waiting invisibly.
            with contextlib.suppress(KeyError, RuntimeError, ValueError):
                if kind == "approval":
                    hitl.resolve_approval(
                        payload["id"], "deny", reason="telegram_delivery_failed"
                    )
                else:
                    hitl.resolve_clarify(payload["id"], "")
            self.phase = "Control delivery failed"
        with contextlib.suppress(Exception):
            await self.refresh()

    def authorized(self, query: dict, message_id: int) -> bool:
        from app.channels.telegram import chat_is_allowed

        message = query.get("message") or {}
        sender = query.get("from") or {}
        return (
            chat_is_allowed(self.chat_id)
            and (message.get("chat") or {}).get("id") == self.chat_id
            and message.get("message_id") == message_id
            and sender.get("id") == self.actor_id
            and message.get("message_thread_id") == self.thread_id
        )

    def request_stop(self) -> bool:
        if self.finished:
            return False
        from app.services.chat import cancel_session_turn

        self.stop_requested = True
        self.outcome = "Stopped"
        if self.on_stop is not None:
            self.on_stop()
        if self.receiving_task is not None:
            self.receiving_task.cancel()
        cancel_session_turn(self.session_id)
        return True

    async def callback(self, query: dict) -> bool:
        data = str(query.get("data") or "")
        callback_id = str(query.get("id") or "")
        if data == f"ts:{self.token}":
            if not self.authorized(query, self.status_id or -1):
                await self.api.answer_callback(
                    callback_id,
                    "Only the person who started this task can stop it.",
                    alert=True,
                )
            else:
                stopped = self.request_stop()
                await self.api.answer_callback(
                    callback_id,
                    "Stopping…" if stopped else "This task already finished.",
                )
            return True
        parts = data.split(":")
        if (
            len(parts) == 3
            and parts[:2] == ["tm", self.token]
            and parts[2] in {"steer", "queue", "interrupt"}
        ):
            if (
                self.finished
                or self.stop_requested
                or self.status_id is None
                or not self.authorized(query, self.status_id)
            ):
                await self.api.answer_callback(
                    callback_id,
                    "This control belongs to another person or has expired.",
                    alert=True,
                )
                return True
            self.input_mode = parts[2]
            if self.on_mode is not None:
                self.on_mode(self.input_mode)
            await self.api.answer_callback(
                callback_id, f"New messages: {self.input_mode}"
            )
            await self.refresh()
            return True
        if len(parts) != 3 or parts[0] not in {"ta", "tc"}:
            return False
        prompt = self.prompts.get(parts[1])
        if prompt is None:
            return False
        if not self.authorized(query, prompt.message_id):
            await self.api.answer_callback(
                callback_id,
                "This control belongs to another person or chat.",
                alert=True,
            )
            return True
        pending = self.pending_payload(prompt)
        if pending is None:
            self.prompts.pop(prompt.token, None)
            await self.api.answer_callback(
                callback_id, "This request expired or was already resolved."
            )
            with contextlib.suppress(Exception):
                await self.api.remove_keyboard(self.chat_id, prompt.message_id)
            return True
        choice = parts[2]
        try:
            if parts[0] == "ta" and prompt.kind == "approval":
                confirmed = choice == "confirm_always"
                if confirmed:
                    if not prompt.confirming_always:
                        raise ValueError("Confirmation required")
                    choice = "always"
                if (
                    choice not in pending.get("choices", [])
                    or (choice == "always" and not pending.get("allow_permanent"))
                    or (choice == "session" and not pending.get("allow_session"))
                ):
                    raise ValueError("Unavailable choice")
                if choice == "always" and not confirmed:
                    prompt.confirming_always = True
                    await self.api.answer_callback(
                        callback_id, "Confirm the permanent permission on the card."
                    )
                    await self.api.edit_message(
                        self.chat_id,
                        prompt.message_id,
                        "Always allow this permission?\n\n"
                        + str(pending.get("description") or pending.get("tool") or "")[
                            :1000
                        ]
                        + "\n\nThis permission persists beyond this conversation. You can choose Allow once instead.",
                        reply_markup={
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "Confirm always allow",
                                        "callback_data": f"ta:{prompt.token}:confirm_always",
                                    },
                                ],
                                [
                                    {
                                        "text": "Allow once",
                                        "callback_data": f"ta:{prompt.token}:once",
                                    },
                                    {
                                        "text": "Deny",
                                        "callback_data": f"ta:{prompt.token}:deny",
                                    },
                                ],
                            ]
                        },
                    )
                    return True
                hitl.resolve_approval(pending["id"], choice, reason="telegram_button")
                label = {
                    "once": "Allowed once",
                    "session": "Allowed this session",
                    "always": "Always allowed",
                    "deny": "Denied",
                }[choice]
            elif parts[0] == "tc" and prompt.kind == "clarify":
                if choice == "other":
                    prompt.awaiting_text = True
                    await self.api.answer_callback(
                        callback_id, "Reply to the question with your answer."
                    )
                    return True
                options = pending.get("choices", [])
                if not choice.isdigit() or int(choice) >= len(options):
                    raise ValueError("Unavailable choice")
                label = options[int(choice)]
                hitl.resolve_clarify(pending["id"], label)
            else:
                raise ValueError("Wrong control")
        except (ValueError, KeyError, RuntimeError):
            await self.api.answer_callback(
                callback_id, "This choice is unavailable or the request expired."
            )
            return True
        self.prompts.pop(prompt.token, None)
        await self.api.answer_callback(callback_id, label[:150])
        with contextlib.suppress(Exception):
            await self.api.edit_message(self.chat_id, prompt.message_id, "✓ " + label)
        self.phase = "Thinking"
        return True

    async def answer_text(self, message: dict) -> bool:
        from app.channels.telegram import chat_is_allowed

        if (
            not chat_is_allowed(self.chat_id)
            or (message.get("from") or {}).get("id") != self.actor_id
            or message.get("message_thread_id") != self.thread_id
        ):
            return False
        reply_id = (message.get("reply_to_message") or {}).get("message_id")
        candidates = [
            p
            for p in self.prompts.values()
            if p.kind == "clarify" and self.pending_payload(p)
        ]
        target = next((p for p in candidates if p.message_id == reply_id), None)
        if target is None and self.chat_id > 0 and not reply_id:
            waiting = [p for p in candidates if p.awaiting_text]
            if len(waiting) == 1:
                target = waiting[0]
        if target is None:
            return False
        answer = str(message.get("text") or "").strip()
        if not answer:
            return False
        try:
            hitl.resolve_clarify(target.payload["id"], answer)
        except (KeyError, RuntimeError):
            return False
        self.prompts.pop(target.token, None)
        with contextlib.suppress(Exception):
            await self.api.edit_message(
                self.chat_id, target.message_id, "✓ Answer: " + answer[:1500]
            )
        self.phase = "Thinking"
        return True

    async def finish(self, reply: str) -> None:
        self.finished = True
        if self._ticker:
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
        # All controls become inert, even if delivery of the final answer fails.
        for prompt in list(self.prompts.values()):
            with contextlib.suppress(Exception):
                await self.api.edit_message(
                    self.chat_id,
                    prompt.message_id,
                    "This request expired or was resolved elsewhere.",
                )
        self.prompts.clear()
        self.tools.clear()
        with contextlib.suppress(Exception):
            if self.status_id is not None:
                await self.api.edit_message(
                    self.chat_id, self.status_id, self.status_text(finished=True)
                )
        if self.outcome == "Stopped":
            reply = "Stopped. A tool already running may still finish. You can send another message or use /new."
        if self.api.rich_enabled or self.last_draft_at:
            if self.answer_id is None:
                await self.api.send_answer(
                    self.chat_id,
                    reply,
                    thread_id=self.thread_id,
                    reply_to=self.reply_to,
                )
            else:
                await self.api.edit_answer(
                    self.chat_id, self.answer_id, reply, thread_id=self.thread_id
                )
            return
        chunks = split_html(render_markdown(reply))
        if chunks:
            if self.answer_id is not None:
                try:
                    await self.api.edit_html(self.chat_id, self.answer_id, chunks[0])
                except Exception:
                    await self.api.send_html(
                        self.chat_id,
                        chunks[0],
                        thread_id=self.thread_id,
                        reply_to=self.reply_to,
                    )
            else:
                await self.api.send_html(
                    self.chat_id,
                    chunks[0],
                    thread_id=self.thread_id,
                    reply_to=self.reply_to,
                )
            for chunk in chunks[1:]:
                await self.api.send_html(
                    self.chat_id, chunk, silent=True, thread_id=self.thread_id
                )

    async def close(self) -> None:
        if self._ticker and not self._ticker.done():
            self._ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ticker
        for prompt in list(self.prompts.values()):
            with contextlib.suppress(Exception):
                await self.api.remove_keyboard(self.chat_id, prompt.message_id)
        self.prompts.clear()
        if self.status_id is not None:
            with contextlib.suppress(Exception):
                if not self.finished:
                    await self.api.edit_message(
                        self.chat_id, self.status_id, self.status_text(finished=True)
                    )
                else:
                    await self.api.remove_keyboard(self.chat_id, self.status_id)
