"""Direct label -> profile mappings for the obvious fields (no AI needed)."""
from __future__ import annotations

import re

from .answer_bank import normalize

RULES: list[tuple[str, str]] = [
    (r"^(your )?full (legal )?name$|^(your )?name$|^legal name$", "@full_name"),
    (r"^current (company|employer|organi[sz]ation)( name)?$", "@current_company"),
    (r"^(current )?location$|^where are you (based|located)$|^current city$", "@location"),
    (r"^(legal )?(first|given) names?( s)?$", "personal.first_name"),
    (r"^(legal )?(last|family) name$|^surname$", "personal.last_name"),
    (r"^e ?mail( address)?$", "personal.email"),
    (r"^country phone code", "personal.phone_country_code"),
    (r"^(phone|mobile|telephone)( number| no)?$", "personal.phone"),
    (r"^address line 1$|^street( address)?$", "personal.address_line1"),
    (r"^address line 2$", "personal.address_line2"),
    (r"^city( town)?$|^town$", "personal.city"),
    (r"^(state|province|region|state province)$", "personal.state"),
    (r"^(postal|zip)( code)?$|^postcode$", "personal.postal_code"),
    (r"^country(/region| region| territory| or territory)?$", "personal.country"),
    (r"linkedin", "links.linkedin"),
    (r"github", "links.github"),
    (r"^(personal )?(web ?site|portfolio)", "links.website"),
]

_COMPILED = [(re.compile(p), path) for p, path in RULES]


def _computed(profile: dict, key: str):
    per = profile.get("personal") or {}
    if key == "@full_name":
        return " ".join(str(per.get(k, "")).strip() for k in ("first_name", "last_name")).strip() or None
    if key == "@current_company":
        exp = profile.get("experience") or []
        return (exp[0] or {}).get("company") if exp and isinstance(exp[0], dict) else None
    if key == "@location":
        parts = [str(per.get(k, "")).strip() for k in ("city", "state", "country")]
        return ", ".join(p for p in parts if p) or None
    return None


def _get(profile: dict, dotted: str):
    if dotted.startswith("@"):
        return _computed(profile, dotted)
    cur = profile
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def lookup(label: str, profile: dict) -> str | None:
    nl = normalize(label)
    for regex, path in _COMPILED:
        if regex.search(nl):
            value = _get(profile, path)
            if value is not None and str(value).strip():
                return str(value).strip()
    return None
