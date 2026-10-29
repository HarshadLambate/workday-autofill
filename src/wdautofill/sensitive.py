"""Questions that must never be guessed by an AI.

These are legal declarations, attestations, or personal-identity questions.
They are only answered from your saved, confirmed answers - or by asking you.
"""
from __future__ import annotations

import re

_SENSITIVE = [
    r"criminal", r"convict", r"felony", r"misdemeano", r"\barrest", r"offen[cs]e",
    r"background (check|screen|investigation)", r"drug (test|screen)",
    r"authori[sz]ed to work", r"legally (authori[sz]ed|eligible|permitted|entitled)",
    r"eligible to work", r"right to work", r"work permit", r"work authori[sz]ation",
    r"sponsor", r"\bvisa\b", r"immigration", r"citizen", r"security clearance",
    r"disabilit", r"handicap", r"veteran", r"military", r"\bgender\b", r"\brace\b",
    r"ethnic", r"hispanic", r"latino", r"sexual orientation", r"transgender",
    r"pronoun", r"lgbt", r"date of birth", r"\bbirth\b",
    r"terminated", r"discharged", r"dismissed", r"\bfired\b", r"resign(ed)? in lieu",
    r"non[- ]?compete", r"restrictive covenant", r"garden leave",
    r"conflict of interest", r"government (official|employee)", r"public official",
    r"previously (worked|employed)", r"former(ly)? (employee|employed)",
    r"ever (worked|been employed)", r"relative", r"related to", r"family member",
    r"certify", r"attest", r"declare", r"acknowledge", r"i agree", r"i accept",
    r"terms (and|&) conditions", r"privacy (notice|policy|statement)", r"consent",
    r"salary", r"compensation", r"remuneration", r"expected pay", r"\bctc\b",
    r"religio", r"\bfaith\b", r"\bcaste\b", r"\btribe\b", r"national origin", r"nationality",
    r"marital", r"pregnan", r"\bage\b", r"older than", r"at least 18", r"18 years",
    r"union member", r"political", r"medical condition", r"health condition",
    r"self[- ]identif", r"\beeo\b", r"equal (employment )?opportunit", r"signature", r"e-?sign",
]

_COUNTRY_DEPENDENT = [
    r"authori[sz]ed to work", r"legally (authori[sz]ed|eligible|permitted|entitled)",
    r"eligible to work", r"right to work", r"work permit", r"work authori[sz]ation",
    r"sponsor", r"\bvisa\b", r"immigration", r"citizen", r"security clearance",
    r"relocat", r"nationality",
]

_sensitive_re = re.compile("|".join(_SENSITIVE), re.IGNORECASE)
_country_re = re.compile("|".join(_COUNTRY_DEPENDENT), re.IGNORECASE)


def is_sensitive(question: str) -> bool:
    return bool(_sensitive_re.search(question))


def is_country_dependent(question: str) -> bool:
    return bool(_country_re.search(question))
