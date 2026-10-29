"""Decides the answer for each form field.

Order of precedence:
  1. profile  - obvious fields (name, email, address ...)
  2. bank     - answers you confirmed before (exact match for sensitive questions)
  3. AI       - ordinary questions: filled if confident.
                Sensitive questions: NEVER filled by AI. At most it SUGGESTS an
                answer from your instructions, which you confirm with Enter.
  4. you      - asked once (with any suggestion), then remembered
"""
from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from . import profile_map
from .answer_bank import AnswerBank, match_option
from .prompter import Reply, TerminalPrompter
from .sensitive import is_country_dependent, is_sensitive

CHOICE_KINDS = {"dropdown", "select", "radio", "checkbox", "checkboxes"}
PROFILE_KINDS = {"text", "textarea", "dropdown", "select", "search"}


@dataclass
class Field:
    label: str
    kind: str  # text | textarea | dropdown | radio | checkbox | checkboxes | search | date
    options_loader: Callable[[], Any] | None = None
    required: bool = False
    _options: list[str] | None = field(default=None, repr=False)

    async def options(self) -> list[str] | None:
        if self.kind == "checkbox":
            return ["Yes", "No"]
        if self._options is None and self.options_loader is not None:
            got = self.options_loader()
            self._options = await got if inspect.isawaitable(got) else got
        return self._options


@dataclass
class Resolution:
    answer: str
    source: str  # profile | rule | bank | llm | user
    alternatives: list[str] = field(default_factory=list)  # tried in order if `answer` doesn't fit


def _as_list(v) -> list[str]:
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, (list, tuple)) else [str(v)]


@dataclass
class Rule:
    """Your fixed answer for every question matching `pattern` (regex, case-insensitive).
    `answer` may be a list: the first one that is a real option on the form is used."""
    pattern: re.Pattern
    answers: list[str]
    countries: set[str] = field(default_factory=set)         # only for jobs in these countries
    except_countries: set[str] = field(default_factory=set)  # never for jobs in these countries

    def applies(self, question: str, country: str | None) -> bool:
        if not self.pattern.search(question):
            return False
        c = (country or "").upper()
        if self.countries and c not in self.countries:
            return False
        if self.except_countries and (not c or c in self.except_countries):
            return False
        return True


def load_rules(profile: dict) -> list[Rule]:
    rules = []
    for raw in profile.get("rules") or []:
        try:
            rules.append(Rule(
                re.compile(str(raw["question"]), re.I),
                _as_list(raw.get("answer")),
                {c.upper() for c in _as_list(raw.get("countries"))},
                {c.upper() for c in _as_list(raw.get("except_countries"))},
            ))
        except (KeyError, re.error) as exc:
            print(f"Ignoring bad rule {raw!r}: {exc}")
    return rules


class Resolver:
    def __init__(
        self,
        profile: dict,
        bank: AnswerBank,
        llm=None,
        min_confidence: float = 0.8,
        instructions: str = "",
        suggest_sensitive: bool = True,
        prompter: TerminalPrompter | None = None,
    ):
        self.profile = profile
        self.bank = bank
        self.llm = llm
        self.min_confidence = min_confidence
        self.instructions = instructions.strip()
        self.suggest_sensitive = suggest_sensitive
        self.prompter = prompter or TerminalPrompter()
        self.rules = load_rules(profile)

    def rule_for(self, question: str, job) -> Rule | None:
        country = getattr(job, "country", None)
        for r in self.rules:
            if r.applies(question, country):
                return r
        return None

    # ------------------------------------------------------------------ context
    def _llm_context(self, job, sensitive: bool) -> dict:
        bank = [
            {"question": e.question, "answer": e.answer, "country": e.scope}
            for e in self.bank.entries
            if sensitive or not e.sensitive
        ]
        return {
            "job": {"title": getattr(job, "title", ""), "location": getattr(job, "location", ""),
                    "country": getattr(job, "country", None)},
            "applicant_instructions": self.instructions,
            "profile": {
                "personal": self.profile.get("personal", {}),
                "links": self.profile.get("links", {}),
                "facts": self.profile.get("facts", []),
            },
            "previous_answers": bank,
        }

    async def _call_llm(self, question, kind, opts, job, suggest):
        return await asyncio.to_thread(
            self.llm.answer, question, kind, opts, self._llm_context(job, suggest), suggest
        )

    # ------------------------------------------------------------------ resolve
    async def resolve(self, f: Field, job=None, ask: bool = True) -> Resolution | None:
        """ask=False: never prompt (used for optional fields nothing is known about)."""
        question = f.label
        sensitive = is_sensitive(question) or f.kind in {"checkbox", "checkboxes"}
        scope = "*"
        if is_country_dependent(question):
            if job is not None and not job.country:
                job.country = await self.prompter.ask_country(job)
            scope = (getattr(job, "country", None) or "?").upper()

        # 1. profile
        if f.kind in PROFILE_KINDS:
            value = profile_map.lookup(question, self.profile)
            if value is not None:
                return Resolution(value, "profile")

        # 2. your rules (profile.yaml `rules:`) - your own fixed answers, so they also
        #    cover sensitive questions. Country-limited rules need the job's country.
        rule = self.rule_for(question, job)
        if rule is not None and (rule.countries or rule.except_countries) and job is not None and not job.country:
            job.country = await self.prompter.ask_country(job)
            rule = self.rule_for(question, job)
        if rule is not None and rule.answers:
            answers = rule.answers
            if f.kind in CHOICE_KINDS:
                opts = await f.options()
                fitting = [a for a in answers if opts and _all_match(a, opts, f.kind)]
                if fitting:
                    return Resolution(fitting[0], "rule", fitting[1:])
            else:
                return Resolution(answers[0], "rule", answers[1:])

        # 3. bank (exact match only for sensitive questions)
        entry = self.bank.find(question, scope, fuzzy=not sensitive)
        if entry is not None:
            return Resolution(entry.answer, "bank")

        opts = await f.options() if f.kind in CHOICE_KINDS else None
        suggestion, why = None, ""

        # Sensitive: a saved answer to a similar wording becomes a suggestion only.
        if sensitive:
            near = self.bank.find(question, scope, fuzzy=True, threshold=0.75)
            if near is not None and (opts is None or _all_match(near.answer, opts, f.kind)):
                suggestion, why = near.answer, f'your saved answer to "{near.question}"'

        # 3. AI
        if self.llm is not None and (not sensitive or (self.suggest_sensitive and suggestion is None)):
            try:
                answer, conf = await self._call_llm(question, f.kind, opts, job, suggest=sensitive)
                usable = bool(answer) and (opts is None or _all_match(answer, opts, f.kind))
                if usable and conf >= self.min_confidence:
                    if not sensitive:
                        return Resolution(answer, "llm")
                    suggestion, why = answer, "AI, from your instructions - check it"
            except Exception as exc:  # network / quota / parsing - fall back to asking
                print(f"   (AI unavailable: {type(exc).__name__}: {exc}) - asking you instead")

        if not ask:
            return None

        # 4. ask the human (once - across all windows), then remember
        reply: Reply = await self.prompter.ask(
            question, f.kind, opts, job, scope, suggestion, why,
            recheck=lambda: (e.answer if (e := self.bank.find(question, scope, fuzzy=not sensitive)) else None),
        )
        if not reply.answer:
            return None
        if reply.remember and scope != "?":  # never file an answer under an unknown country
            self.bank.add(question, reply.answer, scope, sensitive)
        return Resolution(reply.answer, "user")


def _all_match(answer: str, options: list[str], kind: str) -> bool:
    parts = [p for p in answer.split(";")] if kind == "checkboxes" else [answer]
    return all(match_option(p, options) is not None for p in parts if p.strip())
