"""Make a cover letter for each job from your template.

Your template is a normal .docx (or .txt) containing placeholders:

    {{company}}  {{job_title}}  {{job_location}}  {{date}}

Every job gets its own filled-in copy, which is then uploaded. Placeholders may
be styled however you like in Word; the replacement keeps the formatting of the
first run of the paragraph.
"""
from __future__ import annotations

import html
import re
import zipfile
from datetime import date
from pathlib import Path

PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_PARA = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_TEXT = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.S)


def _fill_text(text: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1).lower(), m.group(0)), text)


def _fill_xml(xml: str, values: dict[str, str]) -> str:
    esc = {k: html.escape(v, quote=False) for k, v in values.items()}

    def para(m: re.Match) -> str:
        p = m.group(0)
        texts = _TEXT.findall(p)
        joined = "".join(t[1] for t in texts)
        if "{{" not in joined:
            return p
        # 1) placeholders contained in a single run
        p = _TEXT.sub(lambda t: t.group(1) + _fill_text(t.group(2), esc) + t.group(3), p)
        if "{{" not in "".join(t[1] for t in _TEXT.findall(p)):
            return p
        # 2) placeholders Word split across runs: move the paragraph text into the first run
        full = _fill_text("".join(t[1] for t in _TEXT.findall(p)), esc)
        first = [True]

        def put(t: re.Match) -> str:
            if first[0]:
                first[0] = False
                return '<w:t xml:space="preserve">' + full + "</w:t>"
            return t.group(1) + t.group(3)

        return _TEXT.sub(put, p)

    return _PARA.sub(para, xml)


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:60] or "Company"


def make_cover_letter(template: Path, out_dir: Path, applicant: str, company: str,
                      job_title: str, job_location: str) -> Path:
    values = {
        "company": company,
        "job_title": job_title,
        "job_location": job_location or "your location",
        "date": date.today().strftime("%d %B %Y"),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{safe_name(applicant)}_Cover_Letter_{safe_name(company)}"
    suffix = template.suffix.lower()
    if suffix == ".docx":
        out = out_dir / f"{stem}.docx"
        with zipfile.ZipFile(template) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if re.fullmatch(r"word/(document|header\d*|footer\d*)\.xml", item.filename):
                    data = _fill_xml(data.decode("utf-8"), values).encode("utf-8")
                zout.writestr(item, data)
        return out
    if suffix in {".txt", ".md"}:
        out = out_dir / f"{stem}.txt"
        out.write_text(_fill_text(template.read_text(encoding="utf-8"), values), encoding="utf-8")
        return out
    return template  # e.g. a fixed PDF: uploaded as it is


def leftover_placeholders(path: Path) -> list[str]:
    """Placeholders still present after filling (typos such as {{compnay}})."""
    if path.suffix.lower() == ".docx":
        with zipfile.ZipFile(path) as z:
            text = "".join(t[1] for t in _TEXT.findall(z.read("word/document.xml").decode("utf-8")))
    elif path.suffix.lower() in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8")
    else:
        return []
    return PLACEHOLDER.findall(text)
