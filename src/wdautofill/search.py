"""Find matching jobs on companies' own Workday career sites and shortlist them.

Every Workday career site (https://TENANT.wdN.myworkdayjobs.com/SITE) has a public
JSON search endpoint used by its own search page:

    POST https://HOST/wday/cxs/TENANT/SITE/jobs      {"searchText": ..., "limit": 20, "offset": 0}
    GET  https://HOST/wday/cxs/TENANT/SITE/job/...    (one job, with its description)

This module asks each site in your companies list for your keywords, keeps jobs in
your location that were posted recently, scores them against your skills and lets
you pick which ones go into jobs.txt. It reads public listings only, slowly, and
never applies to anything.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

UA = "Mozilla/5.0 (workday-autofill job search; personal use)"
PAGE = 20


@dataclass
class Site:
    host: str
    tenant: str
    site: str

    @property
    def api(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    @property
    def public(self) -> str:
        return f"https://{self.host}/en-US/{self.site}"


def parse_site(url: str) -> Site | None:
    u = urlparse(url.strip())
    if "myworkdayjobs.com" not in u.netloc:
        return None
    parts = [p for p in u.path.split("/") if p]
    if parts and re.fullmatch(r"[a-z]{2}(-[A-Za-z]{2})?", parts[0]):
        parts = parts[1:]  # locale such as en-US
    if not parts:
        return None
    return Site(u.netloc.lower(), u.netloc.split(".")[0].lower(), parts[0])


@dataclass
class Posting:
    site: Site
    title: str
    path: str
    locations: str
    posted_days: int | None
    company: str = ""
    description: str = ""
    score: int = 0
    matched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    external_url: str = ""

    @property
    def url(self) -> str:
        return self.external_url or (self.site.public + self.path)


# ------------------------------------------------------------------ http
def _http(url: str, body: dict | None = None, timeout: int = 25) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if body is not None else "GET",
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def posted_days(text: str) -> int | None:
    t = (text or "").lower()
    if "today" in t:
        return 0
    if "yesterday" in t:
        return 1
    m = re.search(r"(\d+)\+?\s*day", t)
    return int(m.group(1)) if m else None


def strip_html(text: str) -> str:
    text = re.sub(r"<(br|/p|/li|/div)[^>]*>", "\n", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&#39;", "'"), ("&quot;", '"')):
        text = text.replace(a, b)
    return re.sub(r"[ \t]+", " ", text)


# ------------------------------------------------------------------ search
def list_postings(site: Site, query: str, max_results: int, http=_http) -> list[Posting]:
    out: list[Posting] = []
    offset, total = 0, None
    while offset < max_results:
        data = http(site.api + "/jobs", {"appliedFacets": {}, "limit": PAGE, "offset": offset, "searchText": query})
        posts = data.get("jobPostings") or []
        if total is None:
            total = data.get("total") or 0
        for p in posts:
            if not p.get("externalPath"):
                continue
            out.append(Posting(site, p.get("title", ""), p["externalPath"], p.get("locationsText", ""),
                               posted_days(p.get("postedOn", ""))))
        offset += PAGE
        if not posts or offset >= (total or 0):
            break
        time.sleep(0.4)  # be gentle
    return out


def load_details(post: Posting, http=_http) -> None:
    data = http(post.site.api + post.path)
    info = data.get("jobPostingInfo") or {}
    org = data.get("hiringOrganization") or {}
    from .jobs import clean_company

    post.company = clean_company(org.get("name") or "", post.site.tenant) or post.site.tenant.title()
    locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
    country = (info.get("country") or {}).get("descriptor") if isinstance(info.get("country"), dict) else ""
    post.locations = "; ".join(x for x in locs + [country or ""] if x) or post.locations
    post.description = strip_html(info.get("jobDescription", ""))
    if str(info.get("externalUrl", "")).startswith("http"):
        post.external_url = info["externalUrl"]


def _has(text: str, term: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text.lower()) is not None


def parse_years(value) -> float | None:
    """9, "9", "9+", "9 years" -> 9.0"""
    m = re.search(r"\d+(\.\d+)?", str(value)) if value is not None else None
    return float(m.group(0)) if m else None


def score(post: Posting, keywords: list[str], skills: list[str], years: float | None) -> None:
    title_hit = any(_has(post.title, k) for k in keywords)
    post.matched = [s for s in skills if _has(post.description + " " + post.title, s)]
    skill_part = (len(post.matched) / len(skills)) if skills else 0.5
    post.score = round(35 * title_hit + 65 * min(1.0, skill_part * 1.6))
    if years is not None:
        asked = [int(m) for m in re.findall(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?", post.description.lower())]
        asked = [a for a in asked if 1 <= a <= 25]
        if asked and min(asked) > years + 2:
            post.notes.append(f"asks {min(asked)}+ yrs")
            post.score -= 20
        if asked and max(asked) < years - 5:
            post.notes.append(f"junior ({max(asked)} yrs)")
            post.score -= 15
    post.score = max(0, min(100, post.score))


def search(
    sites: list[Site], keywords: list[str], location: str, skills: list[str],
    years: float | None = None, max_days: int | None = 30, per_query: int = 60,
    exclude: list[str] | None = None, http=_http, log=print,
) -> list[Posting]:
    found: dict[str, Posting] = {}  # keyed by site + path (stable before details load)
    years = parse_years(years)
    # "Pune, India" -> match on "pune" (Workday lists jobs as "Pune", "IND Pune", "Pune, MH"...)
    loc = re.split(r"[,/|]", location.lower())[0].strip()
    for site in sites:
        n_before = len(found)
        try:
            for kw in keywords:
                for p in list_postings(site, kw, per_query, http):
                    key = p.site.public + p.path
                    if key in found:
                        continue
                    if max_days is not None and p.posted_days is not None and p.posted_days > max_days:
                        continue
                    if exclude and any(_has(p.title, x) for x in exclude):
                        continue
                    found[key] = p
        except Exception as exc:
            log(f"  ! {site.tenant}/{site.site}: {type(exc).__name__}: {exc}")
            continue
        log(f"  {site.tenant}: {len(found) - n_before} matching titles")

    posts = list(found.values())

    def detail(p: Posting) -> Posting | None:
        try:
            load_details(p, http)
        except Exception:
            pass
        return p

    # location: cheap check on the list text; "2 Locations" etc. need the details
    maybe = [p for p in posts if not loc or loc in p.locations.lower() or re.search(r"\d+ locations?", p.locations.lower())]
    with ThreadPoolExecutor(max_workers=4) as pool:
        posts = [p for p in pool.map(detail, maybe) if p is not None]
    if loc:
        posts = [p for p in posts if loc in p.locations.lower() or loc in p.description.lower()[:400]]
    for p in posts:
        score(p, keywords, skills, years)
    # Workday's search also matches words anywhere in the ad (a recruiter job for "QA"):
    # keep only jobs whose title matches a keyword or that use at least 2 of your skills.
    posts = [p for p in posts if any(_has(p.title, k) for k in keywords) or len(p.matched) >= 2]
    return sorted(posts, key=lambda p: (-p.score, p.posted_days if p.posted_days is not None else 99))


def read_sites(path: Path) -> list[Site]:
    sites, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        s = parse_site(line) if line else None
        if s and (s.host, s.site) not in seen:
            seen.add((s.host, s.site))
            sites.append(s)
    return sites


def parse_picks(raw: str, n: int) -> list[int]:
    raw = raw.strip().lower()
    if raw in {"all", "a"}:
        return list(range(n))
    picks: list[int] = []
    for part in re.split(r"[,\s]+", raw):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", part)
        nums = range(int(m.group(1)), int(m.group(2)) + 1) if m else ([int(part)] if part.isdigit() else [])
        picks += [i - 1 for i in nums if 1 <= i <= n and i - 1 not in picks]
    return picks
