"""Regenerate the README screenshots in docs/images/.

Runs the real tool against the demo pages used by the tests (no real company site,
no real person) and captures what it prints and fills in.

    pip install -e ".[dev]" && playwright install chromium
    python docs/make_screenshots.py
"""
from __future__ import annotations

import asyncio
import builtins
import contextlib
import html
import io
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from playwright.async_api import async_playwright  # noqa: E402

import test_driver_mock as wd_mock  # noqa: E402
import test_forms as form_mock  # noqa: E402
from wdautofill import cli  # noqa: E402
from wdautofill import search as search_mod  # noqa: E402
from wdautofill.answer_bank import AnswerBank  # noqa: E402
from wdautofill.forms import FormFiller  # noqa: E402
from wdautofill.jobs import Job  # noqa: E402
from wdautofill.resolver import Resolver  # noqa: E402
from wdautofill.workday import STATUS_PAGE, WorkdayFiller  # noqa: E402

OUT = ROOT / "docs" / "images"

DEMO = {
    "personal": {"first_name": "Asha", "last_name": "Patil", "email": "asha.patil@example.com",
                 "phone": "9000000000", "phone_country_code": "India (+91)", "city": "Pune",
                 "state": "Maharashtra", "country": "India"},
    "links": {"linkedin": "https://www.linkedin.com/in/asha-patil-demo/"},
    "experience": [{"company": "Globex Payments"}],
    "rules": [
        {"question": "how did you hear", "answer": ["LinkedIn", "Company Website"]},
        {"question": "notice period", "answer": ["90 days", "3 months"]},
        {"question": "authori[sz]ed to work", "answer": "Yes", "countries": ["IN"]},
    ],
}

# What "you" type when the terminal asks (only questions nothing else could answer)
TYPED = {"Notice period": "90 days", "Earliest start date": "01/2027",
         "Which shifts can you work?": "1,3", "Do you have consistent internet access?": "1"}

TERMINAL_CSS = """
body { margin: 0; background: #0d1117; font: 14px/1.5 'DejaVu Sans Mono', Consolas, monospace; }
.win { margin: 18px; border-radius: 10px; background: #161b22; box-shadow: 0 8px 30px #0008; overflow: hidden; width: 1060px; }
.bar { background: #21262d; padding: 9px 14px; color: #8b949e; font-size: 13px; }
.bar i { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 7px; }
pre { margin: 0; padding: 14px 18px 18px; color: #c9d1d9; white-space: pre-wrap; }
.tag { color: #79c0ff; } .src { color: #d2a8ff; } .ok { color: #3fb950; font-weight: bold; }
.ask { color: #e3b341; } .cmd { color: #f0f6fc; } .dim { color: #8b949e; }
"""


def terminal_html(title: str, text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = html.escape(raw)
        line = re.sub(r"^(\[#\d+ [^\]]*\])", r'<span class="tag">\1</span>', line)
        line = re.sub(r"\[(profile|rule|bank|llm|user) *\]", lambda m: f'<span class="src">{m.group(0)}</span>', line)
        if "READY" in raw:
            line = f'<span class="ok">{line}</span>'
        elif raw.lstrip().startswith(("?", "Answer", "1.", "2.", "3.", "Which ones", "Start filling")) or "? " in raw[:60] and "]" in raw[:60] and raw.split("] ", 1)[-1].startswith("?"):
            line = f'<span class="ask">{line}</span>'
        elif raw.startswith("$ "):
            line = f'<span class="cmd">{line}</span>'
        lines.append(line)
    dots = '<i style="background:#ff5f56"></i><i style="background:#ffbd2e"></i><i style="background:#27c93f"></i>'
    return (f"<html><head><style>{TERMINAL_CSS}</style></head><body><div class='win' id='shot'>"
            f"<div class='bar'>{dots}{html.escape(title)}</div><pre>{chr(10).join(lines)}</pre></div></body></html>")


async def shoot_html(page, content: str, path: Path, selector: str = "#shot") -> None:
    await page.set_content(content)
    await page.locator(selector).screenshot(path=str(path))
    print("wrote", path.relative_to(ROOT))


@contextlib.contextmanager
def typing_user():
    """Answer terminal prompts from TYPED, echoing them like a person typing."""
    real = builtins.input
    last_q = [""]

    def fake_input(prompt=""):
        answer = next((v for k, v in TYPED.items() if k in last_q[0]), "")
        print(f"{prompt}{answer}")
        return answer

    real_print = builtins.print

    def spy_print(*a, **k):
        msg = " ".join(str(x) for x in a)
        if "? " in msg:
            last_q[0] = msg
        real_print(*a, **k)

    builtins.input, builtins.print = fake_input, spy_print
    try:
        yield
    finally:
        builtins.input, builtins.print = real, real_print


async def apply_run(browser, tmp: Path) -> tuple[str, object]:
    (tmp / "wd.html").write_text(wd_mock.HTML, encoding="utf-8")
    (tmp / "lever.html").write_text(form_mock.LEVER_HTML, encoding="utf-8")
    resume = tmp / "Asha_Patil_CV.pdf"
    resume.write_bytes(b"%PDF-1.4 demo")
    resolver = Resolver(DEMO, AnswerBank(tmp / "answers.yaml"))
    resolver.bank.add("Are you legally authorized to work in the country of this job?", "Yes", "IN", True)
    ctx = await browser.new_context(viewport={"width": 1100, "height": 1400})
    p1, p2 = await ctx.new_page(), await ctx.new_page()
    j1 = Job((tmp / "wd.html").as_uri(), None, 1)
    j2 = Job((tmp / "lever.html").as_uri(), "IN", 2)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), typing_user():
        print("$ wdautofill apply --dir personal")
        print("Resume:       personal/Asha_Patil_CV.pdf")
        print("2 job(s) queued; up to 5 windows open at a time.\n")
        f1 = WorkdayFiller(p1, resolver, j1, resume_path=resume, log_path=tmp / "log.jsonl")
        f2 = FormFiller(p2, resolver, j2, resume_path=resume, log_path=tmp / "log.jsonl")
        await asyncio.gather(f1.run(), f2.run())
    text = buf.getvalue()
    # demo pages are local files: show them as the job titles the real sites would give
    text = re.sub(r"\[#2 [^\]]*\]", "[#2 Lever: Senior Quality Engineer]", text)
    text = re.sub(r"\[#1 [^\]]*\]", "[#1 Acme Payments: Senior SDET]", text)
    return text, p2


def hero_text(text: str) -> str:
    """Short version for the top of the README: one job (the Lever form) start to finish."""
    keep, in_prompt = [], False
    for line in text.splitlines():
        if line.startswith("$ ") or line.startswith("Resume:"):
            keep.append(line)
        elif line.startswith("[#2"):
            line = line.replace("Senior Quality Engineer", "Sr Quality Engineer")
            if "skipped (no answer given)" in line:
                continue
            keep.append(line[:118] + ("..." if len(line) > 118 else ""))
            in_prompt = "] ? " in line
        elif in_prompt and line.startswith("   "):
            keep.append(line)
        elif not line.strip() and keep and keep[-1].strip():
            keep.append("")
        else:
            in_prompt = False
    return "\n".join(keep).strip()


async def search_run(tmp: Path) -> str:
    folder = tmp / "personal"
    folder.mkdir()
    (folder / "profile.yaml").write_text(
        "search:\n  keywords: [SDET, QA Automation]\n  location: Pune\n  max_days: 14\n"
        "  years_experience: 9\n  skills: [Playwright, Selenium, C#, API testing, Azure DevOps, Python]\n",
        encoding="utf-8")
    (folder / "companies.txt").write_text(
        "https://acme.wd5.myworkdayjobs.com/External\nhttps://globex.wd1.myworkdayjobs.com/Careers\n"
        "https://initech.wd3.myworkdayjobs.com/Jobs\n", encoding="utf-8")
    jobs = {
        "acme": [("Senior SDET - Payments", "/job/Pune/Senior-SDET_R101", "Posted 2 Days Ago",
                  "Playwright and C# automation, API testing, Azure DevOps pipelines. 8+ years.")],
        "globex": [("QA Automation Lead", "/job/Pune/QA-Automation-Lead_R202", "Posted Today",
                    "Selenium, Python, API testing. 10+ years."),
                   ("Recruiting Coordinator", "/job/Pune/Recruiter_R203", "Posted Today", "Hiring for QA teams.")],
        "initech": [("Test Automation Engineer", "/job/Pune/TAE_R303", "Posted 5 Days Ago",
                     "Selenium WebDriver, C#, CI/CD. 3+ years."),
                    ("Principal SDET", "/job/Pune/Principal-SDET_R304", "Posted 9 Days Ago",
                     "Playwright, Python. 15+ years.")],
    }
    names = {"acme": "Acme Payments", "globex": "Globex", "initech": "Initech"}

    def fake_list(site, query, max_results, http=None):
        return [search_mod.Posting(site, t, path, "Pune, India", search_mod.posted_days(posted))
                for t, path, posted, _ in jobs[site.tenant]]

    def fake_details(post, http=None):
        desc = next(d for t, p, _, d in jobs[post.site.tenant] if p == post.path)
        post.company, post.description, post.locations = names[post.site.tenant], desc, "Pune, India"

    answers = iter(["1,2", "n"])
    buf = io.StringIO()
    old = search_mod.list_postings, search_mod.load_details, builtins.input
    search_mod.list_postings, search_mod.load_details = fake_list, fake_details

    def fake_input(prompt=""):
        a = next(answers)
        print(prompt + a)
        return a

    builtins.input = fake_input
    try:
        with contextlib.redirect_stdout(buf):
            print("$ wdautofill search --dir personal")
            cli.main(["search", "--dir", str(folder)])
    finally:
        search_mod.list_postings, search_mod.load_details, builtins.input = old
    return buf.getvalue().replace(str(folder.resolve() / "jobs.txt"), "personal/jobs.txt")


FORM_CSS = """
body { font: 15px/1.45 -apple-system, 'Segoe UI', Roboto, sans-serif; background: #f6f7f9; color: #222; }
form { background: #fff; max-width: 760px; margin: 0 auto; padding: 10px 28px 24px; border-radius: 10px; box-shadow: 0 2px 12px #0001; }
ul { list-style: none; padding: 0; } li.application-question { margin: 14px 0; }
.application-label { font-weight: 600; margin-bottom: 5px; }
input[type=text], input[type=email], select { width: 100%; box-sizing: border-box; padding: 8px 10px; border: 1px solid #c8ccd2; border-radius: 6px; font: inherit; background: #eef8ef; }
input[type=file] { font: inherit; } li li { display: inline-block; margin-right: 14px; }
.dropdown-results:empty { display: none; }
#btn-submit { background: #5b6ee1; color: #fff; border: 0; border-radius: 6px; padding: 10px 18px; font: inherit; }
.posting-categories { max-width: 760px; margin: 18px auto 8px; color: #555; }
.demo { max-width: 760px; margin: 0 auto 8px; color: #8a5a00; background: #fff4d6; padding: 6px 10px; border-radius: 6px; font-size: 13px; }
"""


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await (await browser.new_context(viewport={"width": 1120, "height": 900})).new_page()

            text, form_page = await apply_run(browser, tmp)
            await shoot_html(page, terminal_html("wdautofill apply", text), OUT / "apply-terminal.png")
            await shoot_html(page, terminal_html("wdautofill apply", hero_text(text)).replace(
                "font: 14px", "font: 16px").replace("width: 1060px", "width: 1000px"), OUT / "hero.png")

            await form_page.add_style_tag(content=FORM_CSS)
            await form_page.evaluate("""() => { const d = document.createElement('div'); d.className = 'demo';
                d.textContent = 'Demo page shaped like a Lever form - filled by wdautofill, Submit left to you';
                document.body.prepend(d); window.scrollTo(0, 0); }""")
            await form_page.set_viewport_size({"width": 860, "height": 1180})
            await form_page.screenshot(path=str(OUT / "filled-form.png"), full_page=True)
            print("wrote docs/images/filled-form.png")

            stext = await search_run(tmp)
            await shoot_html(page, terminal_html("wdautofill search", stext), OUT / "search.png")

            rows = [("Acme Payments: Senior SDET", "ready_for_review - waiting for you to submit and close the window"),
                    ("Globex: QA Automation Lead", "filling"), ("Initech: Test Automation Engineer", "queued"),
                    ("Lever: Senior Quality Engineer", "closed")]
            table = "".join(f"<tr><td>{html.escape(a)}</td><td><b>{html.escape(b)}</b></td></tr>" for a, b in rows)
            await page.set_viewport_size({"width": 900, "height": 300})
            await page.set_content(STATUS_PAGE.format(rows=table))
            await page.screenshot(path=str(OUT / "status-window.png"))
            print("wrote docs/images/status-window.png")
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
