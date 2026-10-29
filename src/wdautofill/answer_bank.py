"""Persistent bank of confirmed answers ("ask once, remember forever")."""
from __future__ import annotations

import difflib
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml


def normalize(text: str) -> str:
    """Lower-case, drop required-markers and punctuation, collapse whitespace."""
    text = text.lower().replace("*", " ")
    text = re.sub(r"[^a-z0-9/\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Entry:
    question: str
    answer: str
    scope: str = "*"  # "*" = everywhere, otherwise a country code such as "US"
    sensitive: bool = False


class AnswerBank:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: list[Entry] = []
        self.load()

    # ---------- persistence ----------
    def load(self) -> None:
        self.entries = []
        if not self.path.exists():
            return
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        for raw in data.get("answers") or []:
            self.entries.append(
                Entry(
                    question=str(raw["question"]),
                    answer=str(raw["answer"]),
                    scope=str(raw.get("scope", "*")),
                    sensitive=bool(raw.get("sensitive", False)),
                )
            )

    def save(self) -> None:
        data = {"answers": [asdict(e) for e in self.entries]}
        self.path.write_text(
            yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

    # ---------- lookup ----------
    def find(
        self,
        question: str,
        scope: str = "*",
        fuzzy: bool = True,
        threshold: float = 0.9,
    ) -> Entry | None:
        """Find a saved answer.

        fuzzy=False means exact (normalized) match only. Sensitive questions use
        exact matching because near-identical wording can mean a different legal
        question ("authorized to work in the US" vs "... in the UK").
        """
        nq = normalize(question)
        best: Entry | None = None
        best_score = 0.0
        for e in self.entries:
            if e.scope not in ("*", scope):
                continue
            ne = normalize(e.question)
            if ne == nq:
                score = 1.0
            elif fuzzy:
                score = difflib.SequenceMatcher(None, nq, ne).ratio()
            else:
                continue
            if scope != "*" and e.scope == scope:
                score += 0.001  # prefer the country-specific answer
            if score > best_score:
                best, best_score = e, score
        if best is not None and best_score >= threshold:
            return best
        return None

    def add(self, question: str, answer: str, scope: str = "*", sensitive: bool = False) -> None:
        nq = normalize(question)
        self.entries = [
            e for e in self.entries if not (normalize(e.question) == nq and e.scope == scope)
        ]
        self.entries.append(Entry(question.strip(), answer, scope, sensitive))
        self.save()


def match_option(answer: str, options: list[str]) -> int | None:
    """Return the index of the option that best matches `answer`, or None."""
    na = normalize(answer)
    norm = [normalize(o) for o in options]
    if not na:
        return None
    for i, o in enumerate(norm):
        if o == na:
            return i
    for i, o in enumerate(norm):
        if o and (o.startswith(na + " ") or na.startswith(o + " ")):
            return i
    for i, o in enumerate(norm):
        if len(na) >= 4 and na in o:
            return i
    close = difflib.get_close_matches(na, norm, n=1, cutoff=0.8)
    if close:
        return norm.index(close[0])
    return None
