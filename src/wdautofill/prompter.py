"""Asks YOU questions - one at a time, even when many windows are filling at once.

* All windows share one prompter, so questions never interleave in the terminal.
* If two windows hit the same new question, you are asked once and both get it.
* Before asking, the answer bank is checked again: another window may have just
  taught it the answer.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from .answer_bank import match_option, normalize


@dataclass
class Reply:
    answer: str | None
    remember: bool = True


class TerminalPrompter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[tuple, asyncio.Future] = {}

    async def ask(self, label, kind, options, job, scope, suggestion=None, why="", recheck=None) -> Reply:
        key = (normalize(label), scope, tuple(options or ()))
        if key in self._pending:  # same question already on screen for another window
            return await asyncio.shield(self._pending[key])
        fut = asyncio.get_running_loop().create_future()
        self._pending[key] = fut
        try:
            async with self._lock:
                known = recheck() if recheck else None
                if known is not None:
                    reply = Reply(known, remember=False)
                else:
                    if job is not None and job.page is not None:
                        try:
                            await job.page.bring_to_front()
                        except Exception:
                            pass
                    reply = await asyncio.to_thread(
                        self._blocking_ask, label, kind, options, job, scope, suggestion, why
                    )
            fut.set_result(reply)
            return reply
        except BaseException as exc:
            if not fut.done():
                fut.set_exception(exc)
                fut.exception()  # mark retrieved
            raise
        finally:
            self._pending.pop(key, None)

    async def ask_country(self, job) -> str | None:
        async with self._lock:
            return await asyncio.to_thread(self._blocking_country, job)

    # ---------------------------------------------------------------- terminal
    @staticmethod
    def _blocking_country(job) -> str | None:
        where = f" (posting says: {job.location})" if job.location else ""
        raw = input(f"\n{job.tag} Which COUNTRY is this job in{where}? Code like US, UK, IN: ").strip()
        return raw.upper() or None

    @staticmethod
    def _blocking_ask(label, kind, options, job, scope, suggestion, why) -> Reply:
        tag = job.tag + " " if job is not None else ""
        extra = f"  [country {scope}]" if scope != "*" else ""
        print(f"\n{tag}? {label}{extra}")
        if options:
            for i, opt in enumerate(options, 1):
                print(f"   {i}. {opt}")
        if suggestion:
            print(f"   Suggested: {suggestion}" + (f"   ({why})" if why else ""))
            hint = "Enter = accept suggestion"
        else:
            hint = "Enter = skip"
        multi = ", several: 1,3" if kind == "checkboxes" else ""
        while True:
            raw = input(f"   Answer ({'number' + multi if options else 'text'}; {hint}; start with ! = don't remember): ").strip()
            remember = True
            if raw.startswith("!"):
                remember, raw = False, raw[1:].strip()
            if not raw:
                return Reply(suggestion or None, remember)
            if options:
                picks = []
                for part in (raw.split(",") if kind == "checkboxes" else [raw]):
                    part = part.strip()
                    if part.isdigit() and 1 <= int(part) <= len(options):
                        picks.append(options[int(part) - 1])
                    elif part:
                        picks.append(part)
                if all(match_option(p, options) is not None for p in picks):
                    return Reply("; ".join(picks), remember)
                print("   That is not one of the options - type its number.")
                continue
            if kind == "date" and raw.lower() != "today" and not re.search(r"\d", raw):
                print("   Dates: MM/YYYY, MM/DD/YYYY, YYYY or 'today'.")
                continue
            return Reply(raw, remember)
