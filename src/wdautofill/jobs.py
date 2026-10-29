"""One application = one Job. Parses job lists and guesses the job's country."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Country code -> words that identify it in a Workday location string.
# Deliberately small and conservative: when nothing matches, the country stays
# unknown and you are asked once per job (never guessed).
COUNTRY_HINTS: dict[str, list[str]] = {
    "IN": ["india", "bengaluru", "bangalore", "pune", "mumbai", "hyderabad", "chennai",
           "gurgaon", "gurugram", "noida", "new delhi", "delhi", "kolkata", "ahmedabad"],
    "US": ["united states", "usa", "u s a", "us remote", "remote us", "remote - us"],
    "UK": ["united kingdom", "england", "scotland", "wales", "london", "gbr"],
    "CA": ["canada", "toronto", "vancouver", "montreal"],
    "DE": ["germany", "deutschland", "berlin", "munich", "frankfurt"],
    "NL": ["netherlands", "amsterdam"],
    "IE": ["ireland", "dublin"],
    "FR": ["france", "paris"],
    "SG": ["singapore"],
    "AE": ["united arab emirates", "dubai", "abu dhabi"],
    "AU": ["australia", "sydney", "melbourne"],
    "PL": ["poland", "warsaw", "krakow"],
    "BE": ["belgium", "brussels"],
    "JP": ["japan", "tokyo"],
}


# Application sites that host many companies under one address (company = first path part)
SHARED_HOSTS = {
    "jobs.lever.co", "jobs.eu.lever.co", "boards.greenhouse.io", "job-boards.greenhouse.io",
    "job-boards.eu.greenhouse.io", "jobs.smartrecruiters.com", "apply.workable.com", "jobs.ashbyhq.com",
}

ISO = {
    "IN": "IN", "IND": "IN", "US": "US", "USA": "US", "GB": "UK", "GBR": "UK", "UK": "UK",
    "CA": "CA", "CAN": "CA", "DE": "DE", "DEU": "DE", "NL": "NL", "NLD": "NL", "IE": "IE",
    "IRL": "IE", "FR": "FR", "FRA": "FR", "SG": "SG", "SGP": "SG", "AE": "AE", "ARE": "AE",
    "AU": "AU", "AUS": "AU", "PL": "PL", "POL": "PL", "BE": "BE", "BEL": "BE", "JP": "JP", "JPN": "JP",
}


def country_from_code(code: str) -> str | None:
    """'IN', 'IND' or a country name such as 'India' -> 'IN'."""
    code = (code or "").strip()
    if not code:
        return None
    return ISO.get(code.upper()) or detect_country(code)


_LEGAL = re.compile(
    r"\b(private|pvt\.?|ltd\.?|limited|llp|llc|inc\.?|incorporated|corp\.?|corporation|gmbh|s\.?a\.?|"
    r"b\.?v\.?|plc|co\.?|company|holdings?|techservices|services|technologies|solutions|india|"
    r"global|international)\b",
    re.I,
)


def clean_company(name: str, tenant: str = "") -> str:
    """'QLYS_IN Qualys Security TechServices Private Ltd.' + tenant 'qualys' -> 'Qualys'."""
    words = [w for w in re.split(r"\s+", name or "") if w and "_" not in w and not re.search(r"\d", w)]
    if tenant:
        for w in words:
            if re.sub(r"[^a-z]", "", w.lower()) == tenant.lower():
                return w.strip(",.")
    cleaned = " ".join(w for w in words if not _LEGAL.fullmatch(w.strip(",.()")))
    return cleaned.strip(" ,.-") or (tenant.title() if tenant else name)


def posting_url(url: str) -> str:
    """A link straight into the apply flow (.../job/X/apply/...) -> the job posting itself."""
    return re.sub(r"/apply(/.*)?(\?.*)?$", "", url.split("#")[0]) if "/apply" in url else url


def detect_country(location: str) -> str | None:
    text = " " + re.sub(r"[^a-z]+", " ", location.lower()) + " "
    found = {code for code, words in COUNTRY_HINTS.items()
             if any(f" {w} " in text for w in words)}
    return found.pop() if len(found) == 1 else None  # ambiguous -> unknown


@dataclass
class Job:
    url: str
    country: str | None = None       # set explicitly, detected from the page, or asked once
    number: int = 1
    total: int = 1
    title: str = ""
    company: str = ""
    location: str = ""
    page: Any = field(default=None, repr=False)

    @property
    def tenant(self) -> str:
        """Company id: 'qualys' for qualys.wd5.myworkdayjobs.com, 'jumpcloud' for
        jobs.lever.co/jumpcloud/..., else the first part of the host name."""
        rest = self.url.split("//")[-1]
        host = rest.split("/")[0].lower()
        path = [p for p in rest.split("?")[0].split("/")[1:] if p]
        if host in SHARED_HOSTS and path:
            return path[0].lower()
        return host.split(".")[0]

    @property
    def tag(self) -> str:
        name = (self.title or self.tenant)[:40]
        if self.company:
            name = f"{self.company[:20]}: {name}"
        return f"[#{self.number} {name}]"


def read_job_lines(text: str) -> list[tuple[str, str | None]]:
    rows = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith("http") else line.split(" #", 1)[0].strip()
        if line.startswith("http"):
            parts = line.split()
            rows.append((parts[0], parts[1].upper() if len(parts) > 1 else None))
    return rows


def parse_jobs(urls: list[str], file: str | None, default_country: str | None) -> list[Job]:
    """URLs from the command line and/or a file with lines: URL [COUNTRY]  (# = comment)."""
    rows: list[tuple[str, str | None]] = [(u, None) for u in urls]
    if file and Path(file).exists():
        rows += read_job_lines(Path(file).read_text(encoding="utf-8"))
    seen, jobs = set(), []
    for url, country in rows:
        if url in seen:
            continue
        seen.add(url)
        c = country or default_country
        jobs.append(Job(url=url, country=c.upper() if c else None))
    for i, j in enumerate(jobs, 1):
        j.number, j.total = i, len(jobs)
    return jobs
