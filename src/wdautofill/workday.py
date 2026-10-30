"""Playwright driver for Workday application wizards (async: many jobs at once).

SAFETY: this code never clicks a "Submit" button. Each window stops on the final
step so that YOU review the whole application and submit it yourself.

Workday tenants differ. Selectors below use Workday's `data-automation-id`
attributes, which are the most stable hooks available, but expect to tune them.
"""
from __future__ import annotations

import asyncio
import html
import json
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

from playwright.async_api import Locator, Page, async_playwright
from playwright.async_api import TimeoutError as PWTimeout

from .answer_bank import match_option
from .coverletter import leftover_placeholders, make_cover_letter
from .jobs import Job, clean_company, country_from_code, detect_country, posting_url, read_job_lines
from . import profile_map
from .resolver import Field, Resolution, Resolver

MAX_STEPS = 25
WAIT_FOR_YOU_S = 30 * 60  # how long a window waits for you (sign-in, fixing errors)
FIELD = '[data-automation-id^="formField-"]'
NEXT_BTN = (
    '[data-automation-id="pageFooterNextButton"], '
    '[data-automation-id="bottom-navigation-next-button"]'
)
ERRORS = '[data-automation-id="errorMessage"], [data-automation-id="errorHeading"]'
AUTH = (
    '[data-automation-id="signInContent"], [data-automation-id="signInSubmitButton"], '
    '[data-automation-id="createAccountSubmitButton"]'
)
APPLY = '[data-automation-id="adventureButton"]'
JOB_TITLE = '[data-automation-id="jobPostingHeader"], [data-automation-id="jobTitleHeading"]'
JOB_LOCATION = '[data-automation-id="locations"]'
SEARCH_BOX = '[data-automation-id="searchBox"]'
SEARCH_OPTION = '[data-automation-id="promptOption"], [role="option"]'
SELECTED_PILL = '[data-automation-id="selectedItem"]'
DATE_PARTS = ("dateSectionMonth-input", "dateSectionDay-input", "dateSectionYear-input")
# Workday pages contain a hidden "beecatcher" box that only bots fill in. Never touch it.
ANY_INPUT = (
    'input:not([type="hidden"]):not([type="file"]):not([type="radio"])'
    ':not([type="checkbox"]):not([type="button"]):not([data-automation-id="beecatcher"])'
)
SIGN_IN_WITH_EMAIL = '[data-automation-id="SignInWithEmailButton"]'
STEP = '[data-automation-id="progressBarActiveStep"]'
LOADING = '[data-automation-id="applyFlowLoadingPage"], [data-automation-id="loadingSpinner"], [data-automation-id="wd-LoadingPanel"]'
READY_TIMEOUT_S = 45
NEXT_WORDS = {"next", "continue", "save and continue", "save & continue", "save and next"}
ROW_SECTIONS = {"work experience": "experience", "education": "education"}

# Marks every "Work Experience N" / "Education N" block on the page with data-wda-row="<section>|<n>",
# each section's Add button with data-wda-add="<section>" and each row's Delete button with
# data-wda-del="<section>|<n>". Found from the visible headings, not from tenant-specific ids.
MARK_ROWS_JS = r"""() => {
  const norm = t => (t || '').replace(/\s+/g, ' ').trim();
  const rowRe = /^(work experience|education)\s+(\d+)$/i;
  const headings = [...document.querySelectorAll('h2,h3,h4,h5,h6,[role=heading],legend')]
    .filter(h => h.offsetParent !== null);
  const rows = [];
  for (const h of headings) {
    const m = norm(h.innerText).match(rowRe);
    if (!m) continue;
    // the row block = highest ancestor that contains only this one row heading
    let box = h, up = h.parentElement;
    while (up && up !== document.body) {
      const inside = [...up.querySelectorAll('h2,h3,h4,h5,h6,[role=heading],legend')]
        .filter(x => rowRe.test(norm(x.innerText)));
      if (inside.length > 1) break;
      box = up; up = up.parentElement;
    }
    const key = m[1].toLowerCase() + '|' + m[2];
    box.setAttribute('data-wda-row', key);
    rows.push(key);
    const del = [...box.querySelectorAll('button,[role=button]')]
      .find(b => /^(delete|remove)\b/i.test(norm(b.innerText) || b.getAttribute('aria-label') || ''));
    if (del) del.setAttribute('data-wda-del', key);
  }
  // Add buttons: label says Add / Add Another; section = aria-label, else nearest heading above it
  const sectionHeads = headings.filter(h => /^(work experience|education)$/i.test(norm(h.innerText)));
  for (const b of document.querySelectorAll('button,[role=button]')) {
    if (b.offsetParent === null) continue;
    const t = norm(b.innerText), a = norm(b.getAttribute('aria-label'));
    if (!/^add( another)?\b/i.test(t) && !/^add( another)?\b/i.test(a)) continue;
    let sec = (a.match(/work experience|education/i) || [])[0];
    if (!sec) {
      const above = sectionHeads.filter(h => h.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (above.length) sec = norm(above[above.length - 1].innerText);
    }
    if (sec) b.setAttribute('data-wda-add', sec.toLowerCase());
  }
  return {rows, sections: sectionHeads.map(h => norm(h.innerText).toLowerCase())};
}"""

# Field-level structure of the current page, for personal/debug (labels and ids only, never values).
DUMP_JS = r"""() => {
  const norm = t => (t || '').replace(/\s+/g, ' ').trim().slice(0, 80);
  const vis = e => e.offsetParent !== null;
  const out = [];
  out.push('URL ' + location.href);
  const h = [...document.querySelectorAll('h1,h2,h3,[data-automation-id=progressBarActiveStep]')].filter(vis);
  out.push('HEADINGS ' + h.map(x => norm(x.innerText)).join(' | '));
  for (const e of document.querySelectorAll('[data-automation-id]')) {
    if (!vis(e)) continue;
    const id = e.getAttribute('data-automation-id');
    if (/^(header|footer|logo|navigation|utility|hammy|progressBar|accessibility|richText)/.test(id)) continue;
    out.push(`id=${id} <${e.tagName.toLowerCase()}${e.type ? ' type=' + e.type : ''}> ${norm(e.getAttribute('aria-label') || (e.children.length < 4 ? e.innerText : ''))}`);
  }
  for (const e of document.querySelectorAll('button,[role=button],input,textarea,select')) {
    if (!vis(e) || e.hasAttribute('data-automation-id')) continue;
    out.push(`plain <${e.tagName.toLowerCase()}${e.type ? ' type=' + e.type : ''}> ${norm(e.getAttribute('aria-label') || e.innerText || e.name)}`);
  }
  out.push('FILE INPUTS ' + document.querySelectorAll('input[type=file]').length);
  return out.join('\n');
}"""


class WorkdayFiller:
    def __init__(
        self,
        page: Page,
        resolver: Resolver,
        job: Job | None = None,
        resume_path: Path | None = None,
        cover_letter_template: Path | None = None,
        cover_letter_dir: Path = Path("cover_letters"),
        password: str | None = None,
        overwrite: bool = False,
        resume_autofill: bool = True,
        auto_account: bool = False,
        log_path: Path = Path("applications.jsonl"),
        wait_for_you_s: float = WAIT_FOR_YOU_S,
        debug_dir: Path | None = None,
    ):
        self.page = page
        self.resolver = resolver
        self.job = job or Job(url="")
        self.job.page = page
        self.resume_path = resume_path
        self.cover_letter_template = cover_letter_template
        self.cover_letter_dir = cover_letter_dir
        self.password = password or os.environ.get("WDA_PASSWORD")
        self.overwrite = overwrite
        self.resume_autofill = resume_autofill
        self.auto_account = auto_account
        self.log_path = log_path
        self.wait_for_you_s = wait_for_you_s
        self.debug_dir = debug_dir
        self._dumps = 0
        self.stopped_at = ""
        self.manual: list[str] = []
        self.skipped: list[str] = []
        self.status = "started"
        self._resume_uploaded = False
        self._cover_uploaded = False

    def say(self, msg: str) -> None:
        print(f"{self.job.tag} {msg}")

    # ------------------------------------------------------------------ flow
    async def run(self, url: str | None = None) -> str:
        url = url or self.job.url
        self.job.url = url
        if "myworkdayjobs.com" not in url and not url.startswith("file:"):
            self.say(">> Not a Workday site - this tool can't fill it. Apply in this window yourself.")
            self.status = "not_workday"
            await self.page.goto(url, wait_until="domcontentloaded")
            await self._report()
            return self.status
        # Links straight into the apply flow (e.g. from LinkedIn) skip the posting, which
        # holds the title, company and location - so open the posting first.
        await self.page.goto(posting_url(url), wait_until="domcontentloaded")
        await self._settle()
        await self._read_posting()
        if not await self._visible(APPLY) and posting_url(url) != url:
            await self.page.goto(url, wait_until="domcontentloaded")
            await self._settle()
        await self._start_application()
        self.status = "stopped_early"
        for _ in range(MAX_STEPS):
            await self._settle()
            if await self._visible(APPLY):
                await self._start_application()
                continue
            if await self._handle_auth():
                continue
            await self._wait_ready()
            step = await self._text_of(STEP) or await self._text_of("h2")
            self.say(f"page: {step or '?'}")
            await self._upload_files()
            await self._sync_rows()
            await self._fill_page()
            await self._dump(step)
            btn = await self._next_button()
            if btn is None:
                self.stopped_at = step
                self.say(f">> No Next/Continue button found on '{step}'. Finish this step in the window;"
                         " the page structure was saved to the debug folder.")
                break
            text = ((await btn.inner_text()) or "").strip().lower()
            if "submit" in text:
                self.status = "ready_for_review"
                self.say(">> READY: final step reached. Review this window and click Submit yourself.")
                break
            before = await self._fields_signature()
            marker = await self._text_of(STEP)  # progress-bar text only ("" if the site has none)
            await self._click_next(btn)
            await self._wait_ready(previous_step=marker, previous_fields=before)
            await self._wait_out_errors()
        await self._report()
        return self.status

    async def _read_posting(self, announce: bool = True) -> None:
        """Title, company and location - from the posting's JSON-LD (for search engines), else the page."""
        ld = {}
        try:
            ld = await self.page.evaluate(
                """() => { for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
                     try { const d = JSON.parse(s.textContent); const items = Array.isArray(d) ? d : [d];
                           for (const x of items) if (x && x['@type'] === 'JobPosting') return x; } catch (e) {} }
                   return {}; }"""
            ) or {}
        except Exception:
            pass
        org = ld.get("hiringOrganization") or {}
        locs = ld.get("jobLocation") or []
        locs = locs if isinstance(locs, list) else [locs]
        addr = [(l or {}).get("address") or {} for l in locs]
        self.job.title = (ld.get("title") or await self._text_of(JOB_TITLE) or self.job.title
                          or await self.page.title()).strip()
        raw_org = (org.get("name") if isinstance(org, dict) else "") or ""
        self.job.company = clean_company(raw_org, self.job.tenant) if raw_org else self.job.company
        if not self.job.location:
            parts = [", ".join(x for x in (a.get("addressLocality"), a.get("addressCountry")) if isinstance(x, str) and x)
                     for a in addr]
            self.job.location = "; ".join(p for p in parts if p) or await self._text_of(JOB_LOCATION)
        if not self.job.country:
            codes = {country_from_code(a.get("addressCountry", "")) for a in addr if isinstance(a.get("addressCountry"), str)}
            codes.discard(None)
            self.job.country = codes.pop() if len(codes) == 1 else (
                detect_country(self.job.location) if self.job.location else None)
        where = f"{self.job.location or 'location unknown'} -> country {self.job.country or '? (will ask if needed)'}"
        if announce:
            self.say(f"opened. {where}")

    async def _start_application(self) -> None:
        if await self._visible(AUTH) or await self._visible(FIELD):
            return  # already in the application (sign-in page or a form step)
        await self._click_if_visible(APPLY, 8000)
        use_resume = bool(self.resume_path and self.resume_autofill)
        # "Use My Last Application" (same company, applied before) > "Autofill with Resume" > manual
        # Workday shows "Use My Last Application" even to new applicants, so only use it
        # for a company you have applied to before (per applications.jsonl).
        last = ["useMyLastApplication"] if self._applied_here_before() else []
        choices = last + (["autofillWithResume"] if use_resume else []) + ["applyManually"]
        sel = ", ".join(f'[data-automation-id="{c}"]' for c in choices)
        try:
            await self.page.locator(sel).first.wait_for(state="visible", timeout=6000)
        except PWTimeout:
            return
        for c in choices:
            if await self._click_if_visible(f'[data-automation-id="{c}"]', 300):
                return

    async def _fields_signature(self) -> str:
        try:
            return await self.page.evaluate(
                "() => [...document.querySelectorAll('[data-automation-id^=\"formField-\"]')]"
                ".filter(e => e.offsetParent !== null).map(e => e.getAttribute('data-automation-id')).join(',')")
        except Exception:
            return ""

    async def _wait_ready(self, previous_step: str | None = None, timeout: float = READY_TIMEOUT_S,
                          previous_fields: str | None = None) -> bool:
        """Workday shows a loading screen for a few seconds after every step. Wait until the
        step has changed (after Next), the loading screen is gone, the footer button is there,
        and the number of fields has stopped changing - or until Workday shows errors."""
        deadline = time.monotonic() + timeout
        last, same = None, 0
        while time.monotonic() < deadline:
            try:
                if previous_step is not None and await self._visible(ERRORS):
                    return True  # Next was refused; the errors are handled next
                step = await self._text_of(STEP)
                loading = await self._visible(LOADING)
                footer = await self._next_button() is not None
                fields = await self._fields_signature()
                if previous_step is None:
                    moved = True
                elif previous_step:  # the progress bar names the step
                    moved = bool(step) and step != previous_step
                else:                # no progress bar: the set of fields changed
                    moved = fields != (previous_fields or "")
                if moved and footer and not loading:
                    sig = (step, fields)
                    same = same + 1 if sig == last else 0
                    last = sig
                    if same >= 2:  # unchanged for ~1 second
                        return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False

    def _applied_here_before(self) -> bool:
        if not self.log_path.exists():
            return False
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("status") == "ready_for_review" and Job(url=rec.get("url", "")).tenant == self.job.tenant:
                return True
        return False

    async def _press(self, btn: Locator) -> None:
        """Workday covers buttons with a transparent 'click_filter' div that receives the click."""
        cover = btn.locator('xpath=..').locator('[data-automation-id="click_filter"]')
        target = cover.first if await cover.count() else btn
        try:
            await target.click(timeout=5000)
        except PWTimeout:
            await target.click(force=True)

    # ------------------------------------------------------------------ waiting for you
    async def _wait_until(self, condition, what: str) -> bool:
        """Poll (without blocking other windows) until condition() is true."""
        deadline = time.monotonic() + self.wait_for_you_s
        while time.monotonic() < deadline:
            if await condition():
                return True
            await asyncio.sleep(2)
        self.say(f"gave up waiting for {what}.")
        return False

    async def _handle_auth(self) -> bool:
        if not await self._visible(AUTH):
            return False
        if self.auto_account:
            await self._try_auto_account()
            if not await self._visible(AUTH):
                self.say("signed in.")
                await self._settle()
                return True
        self.say(">> Sign in / create the account in THIS window (terms box, email code, CAPTCHA are yours). "
                 "It continues by itself afterwards.")
        try:
            await self.page.bring_to_front()
        except Exception:
            pass

        async def gone():
            return not await self._visible(AUTH)

        if not await self._wait_until(gone, "sign-in"):
            raise RuntimeError("sign-in not completed")
        await self._settle()
        return True

    async def _wait_out_errors(self) -> None:
        if not await self._visible(ERRORS):
            return
        errs = self.page.locator(ERRORS)
        lines = []
        for i in range(min(await errs.count(), 6)):
            lines.append(" ".join((await errs.nth(i).inner_text()).split()))
        self.say(">> Workday reports problems on this page:\n   - " + "\n   - ".join(lines)
                 + "\n   Fix them in that window (and click Next there, or just fix them); it carries on by itself.")

        async def cleared():
            return not await self._visible(ERRORS)

        await self._wait_until(cleared, "the errors to be fixed")

    async def _try_auto_account(self) -> None:
        email = (self.resolver.profile.get("personal") or {}).get("email")
        password = self.password
        if not (email and password):
            self.say("auto sign-in needs personal.email in the profile and a password (asked at start).")
            return
        # Some sites first show "Sign in with Google / Apple / LinkedIn / email" buttons.
        if await self._visible(SIGN_IN_WITH_EMAIL) and not await self._visible('[data-automation-id="password"]'):
            await (await self._first_visible(SIGN_IN_WITH_EMAIL)).click()
            await self._settle(1500)
        if await self._visible('[data-automation-id="signInSubmitButton"]'):
            await self._fill_credentials(email, password, create=False)
            await self._press(await self._first_visible('[data-automation-id="signInSubmitButton"]'))
            await self._settle(3000)
            if not await self._visible(AUTH):
                return
            self.say("sign-in did not work - switching to the create-account form.")
            await self._click_if_visible('[data-automation-id="createAccountLink"]', 3000)
        if await self._visible('[data-automation-id="createAccountSubmitButton"]'):
            await self._fill_credentials(email, password, create=True)
            self.say("credentials filled. Tick the terms box and click 'Create Account' yourself.")

    async def _fill_credentials(self, email: str, password: str, create: bool) -> None:
        await (await self._first_visible('[data-automation-id="email"]')).fill(email)
        await (await self._first_visible('[data-automation-id="password"]')).fill(password)
        if create and await self._visible('[data-automation-id="verifyPassword"]'):
            await (await self._first_visible('[data-automation-id="verifyPassword"]')).fill(password)

    # ------------------------------------------------------------ resume file
    async def _cover_letter(self) -> Path | None:
        if not self.cover_letter_template:
            return None
        # Postings often carry a legal entity name ("QLYS_IN Qualys Security TechServices
        # Private Ltd."), so the name used in your letter is confirmed once per company site.
        q = f"Company name to use in cover letters for {self.job.tenant}"
        e = self.resolver.bank.find(q, "*", fuzzy=False)
        if e is not None:
            company = e.answer
        else:
            guess = self.job.company or self.job.tenant.replace("-", " ").title()
            reply = await self.resolver.prompter.ask(q, "text", None, self.job, "*", guess, "Enter = use this")
            company = reply.answer or guess
            if reply.remember:
                self.resolver.bank.add(q, company, "*", False)
        self.job.company = company
        name = " ".join(str((self.resolver.profile.get("personal") or {}).get(k, "")) for k in ("first_name", "last_name")).strip()
        out = make_cover_letter(self.cover_letter_template, self.cover_letter_dir, name or "Applicant",
                                company, self.job.title, self.job.location)
        if leftover_placeholders(out):
            self.say(f"! cover letter still has unknown placeholders {leftover_placeholders(out)} - check your template")
        return out

    async def _upload_files(self) -> None:
        """Resume into resume/CV inputs, the job's own cover letter into cover-letter inputs."""
        if (self._resume_uploaded or not self.resume_path) and (self._cover_uploaded or not self.cover_letter_template):
            return
        inputs = self.page.locator('input[type="file"]')
        # "Autofill with Resume" step: one file box, the page itself says it's for the resume
        if self.resume_path and not self._resume_uploaded and await inputs.count() == 1:
            body = (await self.page.locator("body").inner_text()).lower()
            if "autofill with resume" in body or ("resume" in body and "cover letter" not in body):
                await inputs.first.set_input_files(str(self.resume_path))
                self._resume_uploaded = True
                self.say(f"uploaded resume {self.resume_path.name} (waiting for the site to read it)")
                await self._settle(5000)
                return
        for i in range(await inputs.count()):
            node = inputs.nth(i)
            # text of the NEAREST surrounding block that names a document type
            nearby = await node.evaluate(
                "el => { let n = el; for (let i = 0; i < 8 && n.parentElement; i++) { n = n.parentElement;"
                " const t = (n.innerText || '').toLowerCase();"
                " if (/resume|r\u00e9sum\u00e9|\\bcv\\b|curriculum|cover(ing)? letter|motivation letter/.test(t)) return t; }"
                " return ''; }"
            )
            is_cover = bool(re.search(r"cover(ing)? letter|motivation letter", nearby))
            is_resume = bool(re.search(r"resume|r\u00e9sum\u00e9|\bcv\b|curriculum", nearby))
            if is_cover and not is_resume:
                if self._cover_uploaded or not self.cover_letter_template:
                    continue
                letter = await self._cover_letter()
                await node.set_input_files(str(letter))
                self._cover_uploaded = True
                self.say(f"uploaded cover letter {letter.name}")
                await self._settle(1500)
            elif is_resume and not is_cover:
                if self._resume_uploaded or not self.resume_path:
                    continue
                await node.set_input_files(str(self.resume_path))
                self._resume_uploaded = True
                self.say(f"uploaded resume {self.resume_path.name} (waiting for the site to read it)")
                await self._settle(4000)
            elif is_resume and is_cover:  # one box for both, e.g. "Resume/CV and cover letter"
                files = []
                if self.resume_path and not self._resume_uploaded:
                    files.append(self.resume_path)
                if self.cover_letter_template and not self._cover_uploaded and await node.get_attribute("multiple") is not None:
                    files.append(await self._cover_letter())
                if files:
                    await node.set_input_files([str(f) for f in files])
                    self._resume_uploaded = True
                    self._cover_uploaded = self._cover_uploaded or len(files) > 1 or not self.resume_path
                    self.say("uploaded " + ", ".join(f.name for f in files))
                    await self._settle(4000)

    # ----------------------------------------------------------------- fields
    async def _fill_page(self) -> None:
        containers = self.page.locator(FIELD)
        for i in range(await containers.count()):
            c = containers.nth(i)
            try:
                if not await c.is_visible():  # skip hidden / off-step fields
                    continue
                await self._fill_one(c)
            except Exception as exc:  # one bad field must not stop the page
                label = (await self._label_of(c))[0] or f"field #{i}"
                self.manual.append(label)
                self.say(f"! could not handle '{label}': {type(exc).__name__}")

    async def _fill_one(self, c: Locator, label: str | None = None, required: bool | None = None,
                        ask_optional: bool = True) -> None:
        if label is None:
            label, required = await self._label_of(c)
        if not label:
            return
        kind = await self._kind_of(c)
        if kind == "file":
            return
        if kind == "unknown":
            self.manual.append(label)
            return
        row = await c.evaluate("e => (e.closest('[data-wda-row]') || {getAttribute: () => null}).getAttribute('data-wda-row')")
        if row:
            await self._fill_row_field(c, kind, label, row)
            return
        if await self._has_value(c, kind) and not self.overwrite:
            # Your profile wins over what the resume reader typed (e.g. "JOHN" -> "John").
            mine = profile_map.lookup(label, self.resolver.profile) if kind == "text" else None
            if not mine or (await c.locator(ANY_INPUT).first.input_value()).strip() == mine:
                return
            await c.locator(ANY_INPUT).first.fill(mine)
            await self._pick_suggestion(c, mine)
            self.say(f"[profile] {label} -> {mine} (replaced what the resume reader filled in)")
            return
        f = Field(label=label, kind=kind, required=required, options_loader=self._loader(c, kind))
        res = await self.resolver.resolve(f, self.job, ask=bool(required) or ask_optional)
        if res is None:
            if required:
                self.skipped.append(label)
            return
        await self._apply_resolution(c, kind, label, res)

    async def _apply_resolution(self, c: Locator, kind: str, label: str, res: Resolution) -> bool:
        ok, used = False, res.answer
        for candidate in [res.answer] + list(res.alternatives):
            if await self._apply(c, kind, candidate):
                ok, used = True, candidate
                break
        shown = used if len(used) <= 60 else used[:57] + "..."
        self.say(f"[{res.source:<7}] {label} -> {shown}" + ("" if ok else "   (FAILED - do manually)"))
        if not ok:
            self.manual.append(label)
        return ok

    # ------------------------------------------------------------ experience / education rows
    ROW_FIELDS = {
        "experience": [
            (r"job title|position|role title|^title", "title"),
            (r"company|employer|organi[sz]ation", "company"),
            (r"location|city", "location"),
            (r"currently work|current(ly)? (job|employer|role)|present", "current"),
            (r"^from|start", "from"),
            (r"^to\b|^to$|end", "to"),
            (r"description|responsibilit|duties|summary", "description"),
        ],
        "education": [
            (r"school|university|college|institution", "school"),
            (r"degree|qualification", "degree"),
            (r"field of study|major|discipline|specialization|subject", "field"),
            (r"overall result|gpa|grade|percentage|cgpa", "gpa"),
            (r"^from|start|first year", "from"),
            (r"^to\b|^to$|end|last year|graduat|actual or expected", "to"),
        ],
    }

    def _row_entries(self, section: str) -> list[dict] | None:
        entries = self.resolver.profile.get(section)
        if not isinstance(entries, list):
            return None
        out = []
        for e in entries:
            e = dict(e or {})
            if section == "education" and "school" not in e:  # accept college / university / institution
                for alias in ("college", "university", "institution"):
                    if alias in e:
                        e["school"] = e[alias]
                        break
            out.append(e)
        return out

    async def _sync_rows(self) -> None:
        """Make the number of Work Experience / Education blocks match your profile."""
        try:
            found = await self.page.evaluate(MARK_ROWS_JS)
        except Exception:
            return
        rows, sections = found["rows"], found["sections"]
        for heading, section in ROW_SECTIONS.items():
            entries = self._row_entries(section)
            if entries is None or (heading not in sections and not any(r.startswith(heading + "|") for r in rows)):
                continue  # this section is not on this page
            have = sorted(int(r.split("|")[1]) for r in rows if r.startswith(heading + "|"))
            want = len(entries)
            # delete extras from the bottom (e.g. HSC / SSC added by the resume reader)
            for n in reversed([n for n in have if n > want]):
                btn = self.page.locator(f'[data-wda-del="{heading}|{n}"]')
                if await btn.count():
                    await btn.first.click()
                    self.say(f"removed {heading.title()} {n} (not in your profile)")
                    await self.page.wait_for_timeout(800)
                else:
                    self.manual.append(f"{heading.title()} {n}: delete it (not in your profile)")
            missing = want - len([n for n in have if n <= want])
            for _ in range(max(0, missing)):
                add = self.page.locator(f'[data-wda-add="{heading}"]')
                if not await add.count():
                    self.manual.append(f"{heading.title()}: add {missing} more")
                    break
                await add.last.click()
                await self.page.wait_for_timeout(1000)
                await self.page.evaluate(MARK_ROWS_JS)
        await self.page.evaluate(MARK_ROWS_JS)

    async def _fill_row_field(self, c: Locator, kind: str, label: str, row: str) -> None:
        heading, n = row.split("|")
        section = ROW_SECTIONS.get(heading)
        entries = self._row_entries(section) if section else None
        if not entries or int(n) > len(entries):
            if entries is None:
                self.manual.append(f"{heading.title()} {n}: {label}")
            return
        entry = entries[int(n) - 1] or {}
        key = next((k for pat, k in self.ROW_FIELDS[section] if re.search(pat, label.lower())), None)
        if key is None or key not in entry or entry[key] in (None, ""):
            return  # nothing in your profile for this field; leave whatever is there
        value = entry[key]
        if key == "current":
            want = bool(value)
            box = c.locator('input[type="checkbox"]').first
            if await box.count() and want != await box.is_checked():
                await box.set_checked(want, force=True)
            return
        if key == "to" and str(value).lower() in {"current", "present", "now"}:
            return
        answers = [str(v) for v in value] if isinstance(value, list) else [str(value).strip()]
        if kind == "search" and await self._has_value(c, kind):
            current = " ".join((await c.locator(SELECTED_PILL).first.inner_text()).split())
            if match_option(current, answers) is not None or any(match_option(a, [current]) is not None for a in answers):
                return  # already one of yours
            remove = c.locator('[data-automation-id="DELETE_charm"], [aria-label*="Delete" i], [aria-label*="Remove" i]')
            if await remove.count():
                await remove.first.click()
                await self.page.wait_for_timeout(400)
        res = Resolution(answers[0], "profile", answers[1:])
        await self._apply_resolution(c, kind, f"{heading.title()} {n}: {label}", res)

    async def _dump(self, step: str) -> None:
        if self.debug_dir is None:
            return
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            self._dumps += 1
            text = await self.page.evaluate(DUMP_JS)
            tenant = re.sub(r"[^A-Za-z0-9]+", "_", self.job.tenant)[-20:]
            name = f"{self.job.number:02d}-{tenant}-{self._dumps:02d}-{re.sub(r'[^A-Za-z0-9]+', '_', step or 'page')[:30]}.txt"
            (self.debug_dir / name).write_text(text, encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _clean(text: str) -> tuple[str, bool]:
        required = "*" in text or "\u2731" in text
        return re.sub(r"\s+", " ", text.replace("*", " ").replace("\u2731", " ")).strip(), required

    async def _label_of(self, c: Locator) -> tuple[str, bool]:
        if await c.locator("legend").count():
            return self._clean(await c.locator("legend").first.inner_text())
        if await c.locator('input[type="radio"]').count():
            lines = [ln for ln in (await c.inner_text()).splitlines() if ln.strip()]
            return self._clean(lines[0]) if lines else ("", False)
        if await c.locator("label").count():
            return self._clean(await c.locator("label").first.inner_text())
        return "", False

    async def _kind_of(self, c: Locator) -> str:
        if await c.locator('input[type="file"]').count():
            return "file"
        if await c.locator(f'[data-automation-id="{DATE_PARTS[0]}"], [data-automation-id="{DATE_PARTS[2]}"]').count():
            return "date"
        if await c.locator("select").count() == 1:
            return "select"
        if await c.locator('input[type="radio"]').count():
            return "radio"
        if await c.locator(SEARCH_BOX).count():
            return "search"
        if await c.locator('button[aria-haspopup="listbox"]').count():
            return "dropdown"
        if await c.locator("textarea").count():
            return "textarea"
        boxes = await c.locator('input[type="checkbox"]').count()
        if boxes == 1:
            return "checkbox"
        if boxes > 1:
            return "checkboxes"
        inputs = c.locator(ANY_INPUT)
        if await inputs.count() == 1:
            if await inputs.first.get_attribute("readonly") is None:
                return "text"
        return "unknown"

    async def _has_value(self, c: Locator, kind: str) -> bool:
        if kind == "text":
            return bool((await c.locator(ANY_INPUT).first.input_value()).strip())
        if kind == "textarea":
            return bool((await c.locator("textarea").first.input_value()).strip())
        if kind == "dropdown":
            text = (await c.locator('button[aria-haspopup="listbox"]').first.inner_text()).strip().lower()
            return text not in {"", "select one", "select"}
        if kind == "radio":
            return await c.locator('input[type="radio"]:checked').count() > 0
        if kind == "checkbox":
            return await c.locator('input[type="checkbox"]').first.is_checked()
        if kind == "checkboxes":
            return await c.locator('input[type="checkbox"]:checked').count() > 0
        if kind == "search":
            return await c.locator(SELECTED_PILL).count() > 0
        if kind == "select":
            return await c.locator("select").first.evaluate(
                "s => s.selectedIndex > 0 && !!s.value && !/^(select|choose|please)/i.test(s.options[s.selectedIndex].text.trim())")
        if kind == "date":
            for part in DATE_PARTS:
                loc = c.locator(f'[data-automation-id="{part}"]')
                if await loc.count() and re.search(r"\d", await loc.first.input_value()):
                    return True
            return False
        return False

    def _loader(self, c: Locator, kind: str):
        if kind == "dropdown":
            return lambda: self._read_dropdown(c)
        if kind == "select":
            async def select_options():
                texts = await c.locator("select option").all_inner_texts()
                return [t.strip() for t in texts if t.strip() and not re.match(r"^(select|choose|please select)\b", t.strip(), re.I)]
            return select_options
        if kind == "radio":
            async def radios():
                return [t for t, _ in await self._choice_options(c, "radio")]
            return radios
        if kind == "checkboxes":
            async def boxes():
                return [t for t, _ in await self._choice_options(c, "checkbox")]
            return boxes
        return None

    # ----------------------------------------------------------- apply answer
    async def _apply(self, c: Locator, kind: str, answer: str) -> bool:
        if kind == "text":
            await c.locator(ANY_INPUT).first.fill(answer)
            await self._pick_suggestion(c, answer)
            return True
        if kind == "select":
            sel = c.locator("select").first
            texts = [t.strip() for t in await sel.locator("option").all_inner_texts()]
            idx = match_option(answer, texts)
            if idx is None:
                return False
            await sel.select_option(index=idx)
            return True
        if kind == "textarea":
            await c.locator("textarea").first.fill(answer)
            return True
        if kind == "dropdown":
            return await self._pick_dropdown(c, answer)
        if kind == "search":
            return await self._pick_search(c, answer)
        if kind == "date":
            return await self._fill_date(c, answer)
        if kind == "radio":
            options = await self._choice_options(c, "radio")
            idx = match_option(answer, [t for t, _ in options])
            if idx is None:
                return False
            await options[idx][1].check(force=True)
            return True
        if kind == "checkboxes":
            options = await self._choice_options(c, "checkbox")
            texts = [t for t, _ in options]
            idxs = [match_option(p, texts) for p in answer.split(";") if p.strip()]
            if not idxs or None in idxs:
                return False
            for i in idxs:
                await options[i][1].check(force=True)
            return True
        if kind == "checkbox":
            box = c.locator('input[type="checkbox"]').first
            want = answer.strip().lower() in {"yes", "y", "true", "1"}
            if want != await box.is_checked():
                await box.set_checked(want, force=True)
            return True
        return False

    async def _choice_options(self, c: Locator, typ: str) -> list[tuple[str, Locator]]:
        out: list[tuple[str, Locator]] = []
        items = c.locator(f'input[type="{typ}"]')
        for i in range(await items.count()):
            r = items.nth(i)
            text = ""
            rid = await r.get_attribute("id")
            if rid:
                lab = self.page.locator(f'label[for="{rid}"]')
                if await lab.count():
                    text = (await lab.first.inner_text()).strip()
            if not text:  # <label><input type=radio> Yes</label>
                text = " ".join((await r.evaluate("e => e.closest('label') ? e.closest('label').innerText : ''")).split())
            text = text or await r.get_attribute("aria-label") or await r.get_attribute("value") or ""
            out.append((text, r))
        return out

    async def _open_dropdown(self, c: Locator) -> list[str]:
        await c.locator('button[aria-haspopup="listbox"]').first.click()
        opts = self.page.locator('[role="option"]')
        await opts.first.wait_for(state="visible", timeout=5000)
        return [(await opts.nth(i).inner_text()).strip() for i in range(await opts.count())]

    async def _read_dropdown(self, c: Locator) -> list[str]:
        texts = await self._open_dropdown(c)
        await self.page.keyboard.press("Escape")
        return texts

    async def _pick_dropdown(self, c: Locator, answer: str) -> bool:
        texts = await self._open_dropdown(c)
        idx = match_option(answer, texts)
        if idx is None:
            await self.page.keyboard.press("Escape")
            return False
        await self.page.locator('[role="option"]').nth(idx).click()
        return True

    async def _pick_suggestion(self, c: Locator, answer: str) -> None:
        """Type-ahead boxes (e.g. Lever's 'Current location') only accept a value picked from
        their suggestion list. If one appears inside the field after typing, pick the best match."""
        if not await c.locator('[role="listbox"], [role="combobox"], [aria-autocomplete], .dropdown-container, .dropdown-results').count():
            return  # an ordinary text box - nothing to pick
        items = c.locator('[role="option"], .dropdown-results > *, .pac-item, [class*="suggestion"] li')
        try:
            await items.first.wait_for(state="visible", timeout=2500)
        except PWTimeout:
            return
        texts = [" ".join(t.split()) for t in await items.all_inner_texts()]
        idx = match_option(answer, texts)
        if idx is None:
            first = answer.split(",")[0].strip().lower()
            idx = next((i for i, t in enumerate(texts) if first and first in t.lower()), 0)
        await items.nth(idx).click()

    async def _pick_search(self, c: Locator, answer: str) -> bool:
        """Workday 'multiselect' prompt: type, press Enter, click the matching result."""
        box = c.locator(SEARCH_BOX).first
        await box.click()
        await box.fill(answer)
        await box.press("Enter")
        opts = self.page.locator(SEARCH_OPTION)
        try:
            await opts.first.wait_for(state="visible", timeout=6000)
        except PWTimeout:
            return False
        texts = [(await opts.nth(i).inner_text()).strip() for i in range(await opts.count())]
        idx = match_option(answer, texts)
        if idx is None:
            await self.page.keyboard.press("Escape")
            return False
        await opts.nth(idx).click()
        await self.page.wait_for_timeout(500)
        return True

    async def _fill_date(self, c: Locator, answer: str) -> bool:
        """Answer formats: MM/YYYY, MM/DD/YYYY, YYYY, or 'today'."""
        present = [p for p in DATE_PARTS if await c.locator(f'[data-automation-id="{p}"]').count()]
        if answer.strip().lower() == "today":
            t = date.today()
            today = dict(zip(DATE_PARTS, (f"{t.month:02d}", f"{t.day:02d}", str(t.year))))
            nums = [today[p] for p in present]
        else:
            nums = re.findall(r"\d+", answer)
        if len(nums) != len(present):
            return False
        for part, value in zip(present, nums):
            inp = c.locator(f'[data-automation-id="{part}"]').first
            await inp.click()
            await inp.press("Control+A")  # replace whatever the resume reader put there
            await inp.press_sequentially(value)
        return True

    # ------------------------------------------------------------- navigation
    async def _first_visible(self, selector: str) -> Locator | None:
        """First *visible* match (Workday keeps hidden duplicates in the DOM)."""
        loc = self.page.locator(selector)
        try:
            for i in range(await loc.count()):
                if await loc.nth(i).is_visible():
                    return loc.nth(i)
        except PWTimeout:
            pass
        return None

    async def _next_button(self) -> Locator | None:
        btn = await self._first_visible(NEXT_BTN)
        if btn is not None:
            return btn
        # Fallback: the last visible button whose text is Next / Continue / Save and Continue / Submit
        buttons = self.page.locator('button, [role="button"]')
        found = None
        for i in range(await buttons.count()):
            b = buttons.nth(i)
            try:
                if not await b.is_visible():
                    continue
                t = " ".join((await b.inner_text()).split()).lower()
            except Exception:
                continue
            if t in NEXT_WORDS or t == "submit":
                found = b
        return found

    async def _click_next(self, btn: Locator) -> None:
        text = ((await btn.inner_text()) or "").lower()
        if "submit" in text:  # hard guard: this tool never submits
            raise RuntimeError("Refusing to click a Submit button.")
        await self._press(btn)
        await self._settle(1500)

    # ---------------------------------------------------------------- helpers
    async def _visible(self, selector: str) -> bool:
        return await self._first_visible(selector) is not None

    async def _text_of(self, selector: str) -> str:
        loc = await self._first_visible(selector)
        if loc is None:
            return ""
        return " ".join((await loc.inner_text()).split())

    async def _click_if_visible(self, selector: str, timeout: int) -> bool:
        try:
            loc = self.page.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.click()
            return True
        except PWTimeout:
            return False

    async def _settle(self, extra_ms: int = 1000) -> None:
        try:
            await self.page.wait_for_load_state("networkidle", timeout=10000)
        except PWTimeout:
            pass
        await self.page.wait_for_timeout(extra_ms)

    async def _report(self) -> None:
        if self.manual:
            self.say("fill these yourself: " + "; ".join(dict.fromkeys(self.manual)))
        if self.skipped:
            self.say("skipped (no answer given): " + "; ".join(dict.fromkeys(self.skipped)))
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "url": self.job.url,
            "title": self.job.title,
            "company": self.job.company,
            "country": self.job.country,
            "status": self.status,
            "stopped_at": self.stopped_at,
            "manual": list(dict.fromkeys(self.manual)),
            "skipped": list(dict.fromkeys(self.skipped)),
        }
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


STATUS_PAGE = """<html><head><title>wdautofill - keep this window open</title></head>
<body style="font-family:system-ui,sans-serif;margin:24px;color:#222">
<h2>wdautofill is running</h2>
<p>Keep <b>this</b> window open. Each job opens in its own window and stops before Submit.<br>
Several jobs fill at once; finished ones wait for you. <b>Review, click Submit, then close that window.</b><br>
You can keep adding links to your jobs file; they join the queue.</p>
<table cellpadding="6" style="border-collapse:collapse">{rows}</table></body></html>"""


async def run_jobs(
    jobs: list[Job],
    resolver: Resolver,
    browser_dir: Path,
    channel: str | None = None,
    parallel: int = 5,
    max_open: int = 15,
    watch_file: Path | None = None,
    skip_urls: set[str] | None = None,
    watch_interval: float = 5.0,
    headless: bool = False,
    **kwargs,
) -> dict[str, str]:
    """Unlimited queue. `parallel` applications are being FILLED at any moment; a
    window that is finished and waiting for you does not hold up the next job.
    `max_open` caps how many job windows can be open in total (filled ones wait for
    you to submit and close them). New lines added to `watch_file` are queued too."""
    max_open = max(max_open, parallel)
    filling = asyncio.Semaphore(max(1, parallel))
    browser_dir.mkdir(parents=True, exist_ok=True)
    skip_urls = set(skip_urls or ())
    queue: asyncio.Queue = asyncio.Queue()
    seen: set[str] = set()
    status: dict[str, str] = {}
    counter = [0]

    def enqueue(job: Job) -> None:
        if job.url in seen or job.url in skip_urls:
            return
        seen.add(job.url)
        counter[0] += 1
        job.number = counter[0]
        status[job.url] = "queued"
        queue.put_nowait(job)

    for j in jobs:
        enqueue(j)

    async with async_playwright() as pw:
        launch = {"user_data_dir": str(browser_dir), "headless": headless, "viewport": {"width": 1280, "height": 900}}
        if channel:
            launch["channel"] = channel
        ctx = await pw.chromium.launch_persistent_context(**launch)
        control = ctx.pages[0] if ctx.pages else await ctx.new_page()
        titles: dict[str, str] = {}

        async def refresh() -> None:
            rows = "".join(
                f"<tr><td>{html.escape(u_title)}</td><td><b>{html.escape(st)}</b></td></tr>"
                for u_title, st in ((titles.get(u) or u, st) for u, st in status.items())
            )
            try:
                await control.set_content(STATUS_PAGE.format(rows=rows))
            except Exception:
                pass

        async def worker() -> None:
            while True:
                job = await queue.get()
                if job is None:
                    queue.task_done()
                    return
                page = None
                slot_held = False
                try:
                    await filling.acquire()  # at most `parallel` jobs filling at once
                    slot_held = True
                    page = await ctx.new_page()
                    status[job.url] = "filling"
                    await refresh()
                    if "myworkdayjobs.com" in job.url:
                        filler = WorkdayFiller(page, resolver, job, **kwargs)
                    else:  # Lever, Greenhouse and other ordinary forms
                        from .forms import FormFiller
                        filler = FormFiller(page, resolver, job, **kwargs)
                    try:
                        result = await filler.run()
                    except Exception as exc:
                        print(f"{job.tag} stopped: {type(exc).__name__}: {exc}. Finish it by hand in its window.")
                        result = "error - finish by hand"
                    titles[job.url] = f"{job.company + ': ' if job.company else ''}{job.title or job.url}"
                    status[job.url] = f"{result} - waiting for you to submit and close the window"
                    filling.release()  # done filling: the next job can start now
                    slot_held = False
                    await refresh()
                    if not page.is_closed():  # you close it after submitting
                        await page.wait_for_event("close", timeout=0)
                    status[job.url] = "closed"
                    print(f"{job.tag} window closed.")
                except Exception as exc:  # browser closed, etc.
                    status[job.url] = f"error: {type(exc).__name__}"
                finally:
                    if slot_held:
                        filling.release()
                    await refresh()
                    queue.task_done()

        async def watcher() -> None:
            last = None
            while watch_file is not None:
                try:
                    mtime = watch_file.stat().st_mtime
                    if mtime != last:
                        last = mtime
                        before = len(seen)
                        for url, country in read_job_lines(watch_file.read_text(encoding="utf-8")):
                            enqueue(Job(url=url, country=country))
                        if len(seen) > before:
                            print(f">> {len(seen) - before} new job(s) from {watch_file.name} added to the queue.")
                            await refresh()
                except FileNotFoundError:
                    pass
                await asyncio.sleep(watch_interval)

        await refresh()
        # one worker per window that may be open; the semaphore limits how many fill at once
        workers = [asyncio.create_task(worker()) for _ in range(max(1, max_open))]
        watch_task = asyncio.create_task(watcher())
        try:
            while True:
                await queue.join()
                if watch_file is None:
                    break
                await asyncio.sleep(watch_interval + 1)  # let the watcher look once more
                if queue.empty() and queue._unfinished_tasks == 0:  # noqa: SLF001
                    break
            print("\n=== All jobs done ===")
            for url, st in status.items():
                print(f"- {st}: {titles.get(url) or url}")
            return status
        finally:
            watch_task.cancel()
            for _ in workers:
                queue.put_nowait(None)
            await asyncio.gather(*workers, return_exceptions=True)
            await ctx.close()
