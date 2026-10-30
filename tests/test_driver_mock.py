"""Integration test: drives fake Workday-like wizards in headless Chromium,
two windows at once.

Skipped automatically when no Playwright browser is installed
(`playwright install chromium`).
"""
import asyncio
import time

import pytest

from wdautofill.answer_bank import AnswerBank
from wdautofill.jobs import Job
from wdautofill.prompter import Reply, TerminalPrompter
from wdautofill.resolver import Resolver

HTML = """
<html><head><title>Mock Workday</title>
<script type="application/ld+json">{"@context":"http://schema.org","@type":"JobPosting","title":"Senior SDET",
 "hiringOrganization":{"@type":"Organization","name":"Acme Payments"},
 "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Pune","addressCountry":"IND"}}}</script>
</head><body>
<h2 data-automation-id="jobPostingHeader">Senior SDET</h2>
<div data-automation-id="progressBarActiveStep" id="stp">current step 1 of 2 My Information</div>
<div data-automation-id="locations">Pune, India</div>
<div id="start">
  <div data-automation-id="adventureButton" role="button" onclick="
    this.style.display='none';
    document.getElementById('signin').style.display='block';
    setTimeout(() => { document.getElementById('signin').remove();
                       document.getElementById('manual').style.display='block'; }, 2500)">Apply</div>
  <div id="signin" style="display:none" data-automation-id="signInContent">Sign in</div>
  <div id="manual" style="display:none">
    <div data-automation-id="applyManually" role="button" onclick="
      document.getElementById('start').style.display='none';
      document.getElementById('p1').style.display='block'">Apply Manually</div>
  </div>
</div>

<div id="p1" style="display:none">
  <div data-automation-id="formField-firstName"><label>First Name*</label><input type="text"></div>
  <div data-automation-id="formField-country"><label>Country*</label>
    <button aria-haspopup="listbox" onclick="openList(this, ['India','United States of America','Canada'])">Select One</button></div>
  <div data-automation-id="formField-phoneCode"><label>Country Phone Code*</label>
    <div data-automation-id="multiselectInputContainer">
      <input data-automation-id="searchBox" type="text" onkeydown="if(event.key==='Enter') search(this)"></div></div>
  <div data-automation-id="formField-source"><label>How Did You Hear About Us?*</label>
    <button aria-haspopup="listbox" onclick="openList(this, ['Referral','LinkedIn','Job Board'])">Select One</button></div>
  <fieldset data-automation-id="formField-auth">
    <legend>Are you legally authorized to work in the country of this job?*</legend>
    <input type="radio" id="r1" name="auth"><label for="r1">Yes</label>
    <input type="radio" id="r2" name="auth"><label for="r2">No</label></fieldset>
  <div data-automation-id="pageFooterNextButton" role="button" onclick="
    document.getElementById('p1').style.display='none';
    document.getElementById('stp').textContent='current step 2 of 2 Application Questions';
    document.getElementById('p2').style.display='block'">Next</div>
</div>

<div id="p2" style="display:none">
  <div data-automation-id="formField-docs"><h3>Resume/CV</h3><input type="file" id="resumeFile"></div>
  <div><h3>Cover Letter</h3><input type="file" id="coverFile"></div>
  <div data-automation-id="formField-notice"><label>Notice period</label><input type="text"></div>
  <div data-automation-id="formField-start"><label>Earliest start date*</label>
    <input data-automation-id="dateSectionMonth-input" type="text">
    <input data-automation-id="dateSectionYear-input" type="text"></div>
  <fieldset data-automation-id="formField-shifts"><legend>Which shifts can you work?</legend>
    <input type="checkbox" id="c1"><label for="c1">Day</label>
    <input type="checkbox" id="c2"><label for="c2">Night</label>
    <input type="checkbox" id="c3"><label for="c3">Weekend</label></fieldset>
  <div data-automation-id="pageFooterNextButton" role="button" onclick="window.submitted = true">Submit</div>
</div>

<script>
window.submitted = false;
function openList(btn, items) {
  const ul = document.createElement('ul'); ul.setAttribute('role', 'listbox');
  items.forEach(t => { const li = document.createElement('li'); li.setAttribute('role', 'option');
    li.textContent = t; li.onclick = () => { btn.textContent = t; ul.remove(); }; ul.appendChild(li); });
  document.body.appendChild(ul);
}
function search(input) {
  const all = ['India (+91)', 'Indonesia (+62)', 'United States of America (+1)'];
  const q = input.value.toLowerCase().split(' ')[0];
  const ul = document.createElement('ul');
  all.filter(t => t.toLowerCase().includes(q)).forEach(t => {
    const li = document.createElement('li'); li.setAttribute('data-automation-id', 'promptOption');
    li.textContent = t; li.onclick = () => {
      const pill = document.createElement('div'); pill.setAttribute('data-automation-id', 'selectedItem');
      pill.textContent = t; input.parentElement.appendChild(pill); input.value = ''; ul.remove(); };
    ul.appendChild(li); });
  document.body.appendChild(ul);
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('ul[role=listbox]').forEach(u => u.remove()); });
</script></body></html>
"""


class FakeLLM:
    def answer(self, question, kind, options, context, suggest=False):
        return ("LinkedIn", 0.95) if "hear about" in question.lower() else (None, 0.0)


def test_two_windows_fill_in_parallel_ask_once_never_submit(tmp_path, monkeypatch):
    from playwright.async_api import async_playwright

    from wdautofill.workday import WorkdayFiller

    page_file = tmp_path / "mock.html"
    page_file.write_text(HTML, encoding="utf-8")

    bank = AnswerBank(tmp_path / "answers.yaml")
    bank.add("Are you legally authorized to work in the country of this job?", "Yes", scope="IN", sensitive=True)

    asked = []
    answers = {"Notice period": "30 days", "Earliest start date": "01/2027",
               "Which shifts can you work?": "Day; Weekend"}

    def fake_blocking(label, kind, options, job, scope, suggestion, why):
        asked.append(label)
        time.sleep(0.3)  # you, typing - the other window must keep going meanwhile
        return Reply(answers.get(label))

    prompter = TerminalPrompter()
    monkeypatch.setattr(prompter, "_blocking_ask", fake_blocking)
    tpl = tmp_path / "tpl.txt"
    tpl.write_text("Dear {{company}}, I want the {{job_title}} job.", encoding="utf-8")
    resume = tmp_path / "Jane_CV.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    resolver = Resolver(
        {"personal": {"first_name": "Jane", "last_name": "Doe", "country": "India", "phone_country_code": "India (+91)"}},
        bank, llm=FakeLLM(), prompter=prompter,
    )

    async def main():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception:
                pytest.skip("Playwright browser not installed")
            ctx = await browser.new_context()
            jobs = [Job(page_file.as_uri(), None, i, 2) for i in (1, 2)]
            fillers = [WorkdayFiller(await ctx.new_page(), resolver, j, log_path=tmp_path / "log.jsonl",
                                     wait_for_you_s=20, resume_path=resume, cover_letter_template=tpl,
                                     cover_letter_dir=tmp_path / "letters") for j in jobs]
            t0 = time.monotonic()
            statuses = await asyncio.gather(*(f.run() for f in fillers))
            elapsed = time.monotonic() - t0
            pages = [f.page for f in fillers]
            for page in pages:
                assert await page.evaluate("window.submitted") is False  # never clicks Submit
                assert await page.locator("#p2").is_visible()
                assert await page.locator('[data-automation-id="formField-notice"] input').input_value() == "30 days"
                assert await page.locator('[data-automation-id="dateSectionMonth-input"]').input_value() == "01"
                assert await page.locator('[data-automation-id="dateSectionYear-input"]').input_value() == "2027"
                assert await page.locator("#c1").is_checked() and not await page.locator("#c2").is_checked()
                assert await page.locator("#c3").is_checked()
                assert await page.evaluate("document.getElementById('resumeFile').files[0].name") == "Jane_CV.pdf"
                assert await page.evaluate("document.getElementById('coverFile').files[0].name") == \
                    "Jane_Doe_Cover_Letter_Acme_Payments.txt"
                await page.evaluate("document.getElementById('p1').style.display='block'")
                assert await page.locator('[data-automation-id="formField-firstName"] input').input_value() == "Jane"
                assert await page.locator('[data-automation-id="formField-country"] button').inner_text() == "India"
                assert await page.locator('[data-automation-id="selectedItem"]').inner_text() == "India (+91)"
                assert await page.locator('[data-automation-id="formField-source"] button').inner_text() == "LinkedIn"
                assert await page.locator("#r1").is_checked()
            await browser.close()
            return statuses, elapsed, jobs

    statuses, elapsed, jobs = asyncio.run(main())
    assert statuses == ["ready_for_review", "ready_for_review"]
    assert all(j.country == "IN" for j in jobs)  # read from the posting's JSON-LD (IND)
    assert all(j.company == "Acme Payments" and j.title == "Senior SDET" for j in jobs)
    letter = (tmp_path / "letters" / "Jane_Doe_Cover_Letter_Acme_Payments.txt").read_text(encoding="utf-8")
    assert letter == "Dear Acme Payments, I want the Senior SDET job."
    # each new question reached the human ONCE although two windows needed it
    company_q = [a for a in asked if a.startswith("Company name to use in cover letters")]
    assert len(company_q) == 1  # confirmed once for both windows (same company site)
    assert sorted(a for a in asked if a not in company_q) == sorted(answers)
    assert len((tmp_path / "log.jsonl").read_text().strip().splitlines()) == 2


def test_queue_runs_all_jobs_and_picks_up_new_links(tmp_path, monkeypatch):
    """Unlimited queue: 1 window at a time, next job starts when you close the window,
    links added to jobs.txt while running are picked up."""
    from wdautofill import workday

    started = []
    jobs_file = tmp_path / "jobs.txt"
    jobs_file.write_text("https://a.wd1.myworkdayjobs.com/x/job/1\nhttps://b.wd1.myworkdayjobs.com/x/job/2\n")

    async def fake_run(self, url=None):
        started.append(self.job.url)
        if len(started) == 1:  # while job 1 is open, you add another link
            with jobs_file.open("a") as fh:
                fh.write("https://c.wd1.myworkdayjobs.com/x/job/3  # added later\n")
        await self.page.set_content("<p>filled</p>")
        await asyncio.sleep(0.3)
        await self.page.close()  # you submitted and closed the window
        return "ready_for_review"

    monkeypatch.setattr(workday.WorkdayFiller, "run", fake_run)
    from wdautofill.jobs import parse_jobs

    resolver = Resolver({}, AnswerBank(tmp_path / "a.yaml"))
    try:
        status = asyncio.run(workday.run_jobs(
            parse_jobs([], str(jobs_file), None), resolver, browser_dir=tmp_path / "browser",
            parallel=1, watch_file=jobs_file, watch_interval=0.5, headless=True,
            skip_urls={"https://b.wd1.myworkdayjobs.com/x/job/2"},  # filled in an earlier run
        ))
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Playwright browser not installed")
        raise
    assert started == ["https://a.wd1.myworkdayjobs.com/x/job/1", "https://c.wd1.myworkdayjobs.com/x/job/3"]
    assert set(status.values()) == {"closed"}


SIGNIN_HTML = """
<html><head><script type="application/ld+json">{"@type":"JobPosting","title":"Lead QA Engineer",
 "hiringOrganization":{"name":"QLYS_IN Qualys Security TechServices Private Ltd."},
 "jobLocation":{"@type":"Place","address":{"addressLocality":"Pune","addressCountry":"India"}}}</script></head><body>
<div data-automation-id="signInContent" id="auth">Sign In
  <button data-automation-id="GoogleSignInButton">Sign in with Google</button>
  <button data-automation-id="SignInWithEmailButton" onclick="document.getElementById('form').style.display='block'">Sign in with email</button>
  <div id="form" style="display:none">
    <div data-automation-id="formField-email"><label>Email Address*</label><input data-automation-id="email" type="text"></div>
    <div data-automation-id="formField-password"><label>Password*</label><input data-automation-id="password" type="password"></div>
    <div data-automation-id="noCaptchaWrapper" style="position:relative">
      <button data-automation-id="signInSubmitButton" style="width:96px;height:40px">Sign In</button>
      <div data-automation-id="click_filter" aria-label="Submit" style="position:absolute;left:0;top:0;width:96px;height:40px"
           onclick="window.signedIn = [document.querySelector('[data-automation-id=email]').value,
                    document.querySelector('[data-automation-id=password]').value];
                    document.getElementById('auth').remove(); document.getElementById('p1').style.display='block'"></div>
    </div>
    <input data-automation-id="beecatcher" name="website" type="text" style="width:1px;height:1px">
  </div>
</div>
<div id="p1" style="display:none">
  <div data-automation-id="formField-website"><label>Website</label><input type="text" id="site"></div>
  <div data-automation-id="pageFooterNextButton" role="button">Submit</div>
</div></body></html>
"""


def test_real_workday_signin_shape(tmp_path):
    """Mirrors qualys.wd5.myworkdayjobs.com (Oct 2026): provider buttons first, a transparent
    click_filter over the Sign In button, and a 'beecatcher' bot trap that must stay empty."""
    from playwright.async_api import async_playwright

    from wdautofill.workday import WorkdayFiller

    page_file = tmp_path / "signin.html"
    page_file.write_text(SIGNIN_HTML, encoding="utf-8")
    resolver = Resolver({"personal": {"email": "me@example.com"}, "links": {"website": "https://me.dev"}},
                        AnswerBank(tmp_path / "a.yaml"), prompter=TerminalPrompter())

    async def main():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception:
                pytest.skip("Playwright browser not installed")
            page = await browser.new_page()
            f = WorkdayFiller(page, resolver, Job(page_file.as_uri()), password="S3cret!pw",
                              auto_account=True, log_path=tmp_path / "log.jsonl", wait_for_you_s=10)
            status = await f.run()
            out = (status, f.job.company, f.job.country, await page.evaluate("window.signedIn"),
                   await page.evaluate("document.querySelector('[data-automation-id=beecatcher]')?.value ?? 'gone'"),
                   await page.locator("#site").input_value())
            await browser.close()
            return out

    status, company, country, signed_in, bee, site = asyncio.run(main())
    assert status == "ready_for_review"
    assert company.startswith("Qualys") and "QLYS_IN" not in company and country == "IN"
    assert signed_in == ["me@example.com", "S3cret!pw"]  # clicked through the click_filter cover
    assert bee in ("", "gone")  # bot trap never filled
    assert site == "https://me.dev"  # the real Website field is still filled


EXPERIENCE_HTML = """
<html><body>
<div id="p1">
  <div data-automation-id="formField-legalName--firstName"><label>Given Name(s)*</label><input type="text" value="JOHN"></div>
  <div data-automation-id="formField-source"><label>How Did You Hear About Us?*</label>
    <div data-automation-id="multiselectInputContainer">
      <input data-automation-id="searchBox" type="text" onkeydown="if(event.key==='Enter') search(this)"></div></div>
  <fieldset data-automation-id="formField-prev"><legend>Have you ever worked for Mastercard as an employee or provided services as a contingent worker (contractor, consultant)?*</legend>
    <input type="radio" id="y" name="prev"><label for="y">Yes</label>
    <input type="radio" id="n" name="prev"><label for="n">No</label></fieldset>
  <h3>Work Experience</h3>
  <div class="row"><h4>Work Experience 1</h4>
    <div data-automation-id="formField-jobTitle"><label>Job Title*</label><input type="text" value="CONSULTANT TESTING"></div>
    <div data-automation-id="formField-companyName"><label>Company*</label><input type="text"></div>
    <div data-automation-id="formField-currentlyWorkHere"><label>I currently work here</label><input type="checkbox"></div>
    <div data-automation-id="formField-startDate"><label>From*</label>
      <input data-automation-id="dateSectionMonth-input" type="text"><input data-automation-id="dateSectionYear-input" type="text"></div>
    <button>Delete</button></div>
  <button aria-label="Add Another Work Experience" onclick="addRow(this)">Add Another</button>
  <h3>Education</h3>
  <div class="row"><h4>Education 1</h4>
    <div data-automation-id="formField-degree"><label>Degree*</label>
      <button aria-haspopup="listbox" onclick="openList(this, ['High School Diploma','Bachelor\\'s Degree','Master\\'s Degree'])">Select One</button></div>
    <div data-automation-id="formField-lastYearAttended"><label>To (Actual or Expected)</label>
      <input data-automation-id="dateSectionYear-input" type="text" value="2014"></div>
    <button>Delete</button></div>
  <div class="row" id="ssc"><h4>Education 2</h4>
    <div data-automation-id="formField-school"><label>School or University</label><input type="text" value="VIDYA NIKETAN"></div>
    <button onclick="this.parentElement.remove(); document.querySelectorAll('h4').forEach(h => h.textContent = h.textContent)">Delete</button></div>
  <button>Save and Continue</button>
</div>
<script>
function openList(btn, items) {
  const ul = document.createElement('ul'); ul.setAttribute('role', 'listbox');
  items.forEach(t => { const li = document.createElement('li'); li.setAttribute('role', 'option');
    li.textContent = t; li.onclick = () => { btn.textContent = t; ul.remove(); }; ul.appendChild(li); });
  document.body.appendChild(ul);
}
function search(input) {
  const all = ['Social Media > LinkedIn', 'Company Website', 'Job Board > Naukri'];
  const q = input.value.toLowerCase();
  const ul = document.createElement('ul');
  all.filter(t => t.toLowerCase().includes(q)).forEach(t => {
    const li = document.createElement('li'); li.setAttribute('data-automation-id', 'promptOption');
    li.textContent = t; li.onclick = () => {
      const pill = document.createElement('div'); pill.setAttribute('data-automation-id', 'selectedItem');
      pill.textContent = t; input.parentElement.appendChild(pill); ul.remove(); };
    ul.appendChild(li); });
  document.body.appendChild(ul);
}
function addRow(btn) {
  const n = document.querySelectorAll('h4').length ? [...document.querySelectorAll('h4')].filter(h => h.textContent.startsWith('Work')).length + 1 : 1;
  const d = document.createElement('div'); d.className = 'row';
  d.innerHTML = `<h4>Work Experience ${n}</h4>
    <div data-automation-id="formField-jobTitle"><label>Job Title*</label><input type="text" class="wt"></div>
    <div data-automation-id="formField-companyName"><label>Company*</label><input type="text" class="wc"></div>`;
  btn.parentElement.insertBefore(d, btn);
}
</script></body></html>
"""


def test_my_experience_rows_rules_and_name_case(tmp_path):
    from playwright.async_api import async_playwright

    from wdautofill.workday import WorkdayFiller

    page_file = tmp_path / "exp.html"
    page_file.write_text(EXPERIENCE_HTML, encoding="utf-8")
    profile = {
        "personal": {"first_name": "John"},
        "rules": [
            {"question": "how did you hear", "answer": ["Company Website", "LinkedIn"]},
            {"question": r"(ever|previously|formerly)\b.*\b(worked for|work for|employed (by|with|at)|been employed|employee of|worked at|contingent worker|contractor for)", "answer": "No"},
        ],
        "experience": [
            {"title": "Consultant - Testing", "company": "Globex Payments", "from": "08/2025", "to": "current", "current": True},
            {"title": "Associate Technical Specialist", "company": "Initech"},
        ],
        "education": [{"degree": ["Bachelor of Engineering", "Bachelor's Degree"], "to": "2014"}],
    }
    asked = []
    prompter = TerminalPrompter()
    prompter._blocking_ask = lambda label, *a: asked.append(label) or Reply(None)
    resolver = Resolver(profile, AnswerBank(tmp_path / "a.yaml"), prompter=prompter)

    async def main():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception:
                pytest.skip("Playwright browser not installed")
            page = await browser.new_page()
            await page.goto(page_file.as_uri())
            f = WorkdayFiller(page, resolver, Job(page_file.as_uri(), "IN"), log_path=tmp_path / "log.jsonl",
                              debug_dir=tmp_path / "debug")
            await f._sync_rows()
            await f._fill_page()
            await f._dump("My Experience")
            btn = await f._next_button()
            q = lambda sel: page.locator(sel)
            out = {
                "name": await q('[data-automation-id="formField-legalName--firstName"] input').input_value(),
                "source": await q('[data-automation-id="selectedItem"]').all_inner_texts(),
                "prev_no": await q("#n").is_checked(),
                "titles": [await x.input_value() for x in await q('[data-automation-id="formField-jobTitle"] input').all()],
                "companies": [await x.input_value() for x in await q('[data-automation-id="formField-companyName"] input').all()],
                "current": await q('[data-automation-id="formField-currentlyWorkHere"] input').is_checked(),
                "month": await q('[data-automation-id="formField-startDate"] [data-automation-id="dateSectionMonth-input"]').input_value(),
                "degree": await q('[data-automation-id="formField-degree"] button').inner_text(),
                "ssc_gone": await q("#ssc").count() == 0,
                "next": (await btn.inner_text()) if btn else None,
                "dump": "".join(p.read_text() for p in (tmp_path / "debug").glob("*.txt")),
            }
            await browser.close()
            return out

    o = asyncio.run(main())
    assert o["name"] == "John"                       # profile beats the resume reader's "JOHN"
    assert o["source"] == ["Company Website"]           # rule, first answer that exists
    assert o["prev_no"]                                  # "ever worked for <company>" -> No
    assert o["titles"] == ["Consultant - Testing", "Associate Technical Specialist"]  # row added
    assert o["companies"] == ["Globex Payments", "Initech"]
    assert o["current"] and o["month"] == "08"
    assert o["degree"] == "Bachelor's Degree"            # first alternative that is a real option
    assert o["ssc_gone"]                                 # extra education row removed
    assert o["next"] == "Save and Continue"              # found by text, no automation id
    assert asked == []                                   # nothing needed asking
    assert "formField-jobTitle" in o["dump"] and "JOHN" not in o["dump"]  # structure only, no values


LOADING_HTML = """
<html><body>
<div data-automation-id="progressBarActiveStep" id="step">current step 1 of 3 Autofill with Resume</div>
<div id="app"><p>Autofill with Resume</p>
  <button data-automation-id="pageFooterNextButton" onclick="go()">Continue</button></div>
<script>
function go() {
  // what real Workday does: a loading screen for a few seconds, then the next step
  document.getElementById('app').innerHTML = '<div data-automation-id="applyFlowLoadingPage">Loading...</div>';
  setTimeout(() => {
    document.getElementById('step').textContent = 'current step 2 of 3 My Information';
    document.getElementById('app').innerHTML = `
      <div data-automation-id="formField-legalName--firstName"><label>Given Name(s)*</label><input type="text" name="legalName--firstName"></div>
      <div data-automation-id="formField-city"><label>City*</label><input type="text" name="city"></div>
      <button data-automation-id="pageFooterNextButton" onclick="window.submitted=true">Submit</button>`;
  }, 3000);
}
</script></body></html>
"""


def test_waits_for_workday_loading_screen(tmp_path):
    """Real failure from 28 Oct: the tool read 'My Information' while Workday still showed
    its loading screen, found no fields and stopped (TSYS) or clicked on (Qualys)."""
    from playwright.async_api import async_playwright

    from wdautofill.workday import WorkdayFiller

    page_file = tmp_path / "loading.html"
    page_file.write_text(LOADING_HTML, encoding="utf-8")
    resolver = Resolver({"personal": {"first_name": "John", "city": "Pune"}}, AnswerBank(tmp_path / "a.yaml"),
                        prompter=TerminalPrompter())

    async def main():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception:
                pytest.skip("Playwright browser not installed")
            page = await browser.new_page()
            f = WorkdayFiller(page, resolver, Job(page_file.as_uri(), "IN"), log_path=tmp_path / "log.jsonl")
            status = await f.run()
            out = (status, await page.locator('input[name="legalName--firstName"]').input_value(),
                   await page.locator('input[name="city"]').input_value(), await page.evaluate("window.submitted === true"))
            await browser.close()
            return out

    status, first, city, submitted = asyncio.run(main())
    assert status == "ready_for_review"
    assert (first, city) == ("John", "Pune")
    assert not submitted


def test_finished_windows_waiting_for_you_do_not_block_new_jobs(tmp_path, monkeypatch):
    """--parallel counts jobs being FILLED; filled windows waiting for review don't hold a slot."""
    from wdautofill import workday

    jobs_file = tmp_path / "jobs.txt"
    jobs_file.write_text("".join(f"https://c{i}.wd1.myworkdayjobs.com/x/job/{i}\n" for i in range(1, 5)))
    pages, started = [], []

    async def fake_run(self, url=None):
        started.append(self.job.url)
        pages.append(self.page)
        await self.page.set_content("<p>filled - waiting for review</p>")
        return "ready_for_review"  # window stays open, like a real finished application

    async def reviewer():
        while len(started) < 4:  # all 4 must start although nobody closed a window yet
            await asyncio.sleep(0.1)
        for p in pages:
            await p.close()

    monkeypatch.setattr(workday.WorkdayFiller, "run", fake_run)
    from wdautofill.jobs import parse_jobs

    async def main():
        rev = asyncio.create_task(reviewer())
        status = await asyncio.wait_for(workday.run_jobs(
            parse_jobs([], str(jobs_file), None), Resolver({}, AnswerBank(tmp_path / "a.yaml")),
            browser_dir=tmp_path / "browser", parallel=2, max_open=15, headless=True), timeout=60)
        await rev
        return status

    try:
        status = asyncio.run(main())
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Playwright browser not installed")
        raise
    assert len(started) == 4 and set(status.values()) == {"closed"}
