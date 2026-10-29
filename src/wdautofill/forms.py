"""Filler for application forms that are not Workday: Lever, Greenhouse and other
ordinary web forms.

It finds each question on the page (its wording + its input boxes), answers it with
the same resolver as Workday (profile -> rules -> saved answers -> AI -> you), uploads
your resume, and stops. It never clicks Submit; CAPTCHAs are yours.

Site notes
  Lever       (jobs.lever.co)            one page; questions in li.application-question
  Greenhouse  (boards.greenhouse.io ...)  one page; label[for] / fieldset legends
  Others                                  best effort: labels, fieldsets, aria-labels;
                                          "Next"/"Continue" steps are followed, never Submit
"""
from __future__ import annotations

import re

from playwright.async_api import TimeoutError as PWTimeout

from .jobs import clean_company
from .workday import NEXT_WORDS, WorkdayFiller

MAX_PAGES = 8

# Marks every question block on the page with data-wda-q (index), data-wda-label (its
# wording) and data-wda-req ("1" if required). A question = one text box / select /
# textarea, or one group of radio buttons / checkboxes sharing a name.
MARK_QUESTIONS_JS = r"""() => {
  const norm = t => (t || '').replace(/\s+/g, ' ').trim();
  const skipType = new Set(['hidden', 'submit', 'button', 'file', 'image', 'reset', 'search', 'password']);
  const shown = e => e.offsetParent !== null ||
    ((e.type === 'radio' || e.type === 'checkbox') && e.closest('label') && e.closest('label').offsetParent !== null);
  const ctrls = [...document.querySelectorAll('input, select, textarea')].filter(e =>
    !skipType.has(e.type) && shown(e) &&
    !/captcha|beecatcher|honeypot|g-recaptcha/i.test((e.name || '') + ' ' + (e.id || '') + ' ' + (e.className || '')));
  const key = e => ((e.type === 'radio' || e.type === 'checkbox') && e.name) ? 'n:' + e.name : e;
  const groups = new Map();
  for (const e of ctrls) { const k = key(e); if (!groups.has(k)) groups.set(k, []); groups.get(k).push(e); }
  document.querySelectorAll('[data-wda-q]').forEach(e => { e.removeAttribute('data-wda-q'); });
  const out = [];
  let n = 0;
  for (const [k, els] of groups) {
    // the question block = highest ancestor holding this question's inputs and no other question's
    let box = els[0];
    while (box.parentElement && box.parentElement !== document.body) {
      const p = box.parentElement;
      if (ctrls.some(x => p.contains(x) && key(x) !== k)) break;
      box = p;
    }
    if (box === els[0]) box = els[0].parentElement || els[0];
    const first = els[0];
    const byFor = first.id ? document.querySelector(`label[for="${CSS.escape(first.id)}"]`) : null;
    const lab = (box.querySelector('.application-label') || {}).innerText            // Lever
      || (box.querySelector('legend') || {}).innerText                                // fieldsets
      || ((first.type !== 'radio' && first.type !== 'checkbox') && byFor ? byFor.innerText : '')
      || (first.getAttribute('aria-labelledby') || '').split(' ')
           .map(id => (document.getElementById(id) || {}).innerText || '').join(' ')
      || ((first.type !== 'radio' && first.type !== 'checkbox' && first.closest('label')) ? first.closest('label').innerText : '')
      || first.getAttribute('aria-label')
      || (box.innerText || '').split('\n').map(norm).find(Boolean)
      || first.getAttribute('placeholder') || first.name || '';
    const required = els.some(e => e.required || e.getAttribute('aria-required') === 'true') || /[*✱]/.test(lab);
    box.setAttribute('data-wda-q', String(n));
    box.setAttribute('data-wda-label', norm(lab).slice(0, 300));
    box.setAttribute('data-wda-req', required ? '1' : '0');
    out.push(n++);
  }
  return out;
}"""


class FormFiller(WorkdayFiller):
    """Same answers and safety rules as WorkdayFiller, for ordinary one-page (or few-step) forms."""

    async def run(self, url: str | None = None) -> str:
        url = url or self.job.url
        self.job.url = url
        host = url.split("//")[-1].split("/")[0].lower()
        if "lever.co" in host and "/apply" not in url:
            url = url.split("?")[0].rstrip("/") + "/apply"  # Lever's form lives at .../apply
        await self.page.goto(url, wait_until="domcontentloaded")
        await self._settle()
        await self._read_page_details()
        self.status = "stopped_early"
        for _ in range(MAX_PAGES):
            if not await self._wait_for_form():
                self.say(">> No application form found on this page. Apply in this window yourself.")
                self.stopped_at = "no form"
                break
            await self._upload_files()
            await self._fill_questions()
            await self._dump("form")
            nxt = await self._next_step_button()
            if nxt is None:
                self.status = "ready_for_review"
                self.say(">> READY: form filled. Review it, complete any CAPTCHA, and click Submit yourself.")
                break
            before = await self._questions_signature()
            await self._press(nxt)
            await self._settle(1500)
            for _ in range(20):  # wait for the next step to replace the questions
                if await self._questions_signature() != before:
                    break
                await self.page.wait_for_timeout(500)
            await self._wait_out_errors()
        await self._report()
        return self.status

    async def _read_page_details(self) -> None:
        await self._read_posting(announce=False)  # JSON-LD JobPosting when the site has it (Greenhouse etc.)
        title = await self.page.title()
        if "lever.co" in self.job.url:
            # Lever page title: "<Company> - <Job title>"; location under the headline
            parts = [p.strip() for p in title.split(" - ", 1)]
            if len(parts) == 2:
                self.job.company = self.job.company or clean_company(parts[0], self.job.tenant)
                self.job.title = parts[1]
        if not self.job.location:  # Lever shows it under the job headline
            self.job.location = await self._text_of(".posting-categories .location, .posting-category.location")
        if not self.job.company:
            self.job.company = clean_company(self.job.tenant.replace("-", " ").title(), self.job.tenant)
        if not self.job.country and self.job.location:
            from .jobs import detect_country
            self.job.country = detect_country(self.job.location)
        self.say(f"opened. {self.job.location or 'location unknown'} -> country {self.job.country or '? (will ask if needed)'}")

    async def _wait_for_form(self, timeout_ms: int = 20000) -> bool:
        try:
            await self.page.locator(
                "form input:not([type=hidden]), form select, form textarea, input[type=file], "
                "[data-qa] input, .application-question input"
            ).first.wait_for(state="attached", timeout=timeout_ms)
            await self.page.wait_for_timeout(800)
            return True
        except PWTimeout:
            return False

    async def _questions_signature(self) -> str:
        try:
            return await self.page.evaluate(
                "() => [...document.querySelectorAll('input,select,textarea')]"
                ".filter(e => e.offsetParent !== null).map(e => e.name || e.id).join(',')")
        except Exception:
            return ""

    async def _fill_questions(self) -> None:
        await self.page.evaluate(MARK_QUESTIONS_JS)
        boxes = self.page.locator("[data-wda-q]")
        for i in range(await boxes.count()):
            c = boxes.nth(i)
            label = (await c.get_attribute("data-wda-label") or "").strip()
            required = (await c.get_attribute("data-wda-req")) == "1"
            label, star = self._clean(label)
            if not label:
                continue
            try:
                # optional questions nothing is known about are left empty instead of asking you
                await self._fill_one(c, label=label, required=required or star, ask_optional=False)
            except Exception as exc:
                self.manual.append(label)
                self.say(f"! could not handle '{label[:60]}': {type(exc).__name__}")

    async def _next_step_button(self):
        """A 'Next' / 'Continue' button for multi-step forms. Never a Submit button."""
        buttons = self.page.locator('button, [role="button"], input[type="submit"], input[type="button"]')
        found = None
        for i in range(await buttons.count()):
            b = buttons.nth(i)
            try:
                if not await b.is_visible():
                    continue
                t = " ".join(((await b.inner_text()) or (await b.get_attribute("value")) or "").split()).lower()
            except Exception:
                continue
            if re.search(r"\bsubmit\b|\bsend application\b", t):
                return None  # final step: yours
            if t in NEXT_WORDS:
                found = b
        return found
