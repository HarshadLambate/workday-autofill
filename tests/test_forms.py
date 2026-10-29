"""Non-Workday forms. The Lever page below copies the structure of a real Lever
application (jobs.lever.co/jumpcloud/..., October 2026)."""
import asyncio

import pytest

from wdautofill.answer_bank import AnswerBank
from wdautofill.jobs import Job
from wdautofill.prompter import Reply, TerminalPrompter
from wdautofill.resolver import Resolver

LEVER_HTML = """
<html><head><title>JumpCloud - Senior Quality Engineer - India</title></head><body>
<div class="posting-categories"><div class="location">Bangalore, India - Remote</div></div>
<form id="application-form">
<ul>
 <li class="application-question awli-application-row"><div class="application-label">LinkedIn profile</div>
   <button type="button">Apply with LinkedIn</button></li>
 <li class="application-question resume"><label><div class="application-label">Resume/CV <span class="required">✱</span></div>
   <div class="application-field"><input type="file" name="resume" id="resume-upload-input" onchange="parsed()"></div></label></li>
 <li class="application-question"><label><div class="application-label">Full name <span class="required">✱</span></div>
   <div class="application-field"><input type="text" name="name" required></div></label></li>
 <li class="application-question"><label><div class="application-label">Email <span class="required">✱</span></div>
   <div class="application-field"><input type="email" name="email" required></div></label></li>
 <li class="application-question"><label><div class="application-label">Phone <span class="required">✱</span></div>
   <div class="application-field"><input type="text" name="phone" required></div></label></li>
 <li class="application-question" data-qa="structured-contact-location-question"><label><div class="application-label">Current location </div>
   <div class="application-field"><input class="location-input" id="location-input" type="text" name="location" oninput="suggest(this)">
   <input id="selected-location" type="hidden" name="selectedLocation">
   <div class="dropdown-container"><div class="dropdown-results"></div></div></div></label></li>
 <li class="application-question"><label><div class="application-label">Current company</div>
   <div class="application-field"><input type="text" name="org"></div></label></li>
 <li class="application-question"><label><div class="application-label">LinkedIn URL</div>
   <div class="application-field"><input type="text" name="urls[LinkedIn]"></div></label></li>
 <li class="application-question"><label><div class="application-label">Twitter URL</div>
   <div class="application-field"><input type="text" name="urls[Twitter]"></div></label></li>
 <li class="application-question custom-question"><div><div class="application-label full-width multiple-choice">
   <div class="text">Do you have consistent internet access?<span class="required">✱</span></div></div>
   <div class="application-field full-width required-field"><ul data-qa="multiple-choice">
   <li><label><input type="radio" name="cards[a][field0]" value="Yes" required><span>Yes</span></label></li>
   <li><label><input type="radio" name="cards[a][field0]" value="No" required><span>No</span></label></li></ul></div></div></li>
 <li class="application-question custom-question"><div><div class="application-label full-width multiple-choice">
   <div class="text">What is your current notice period? We define notice period as the length of time for you to start your new role upon signing an offer letter.<span class="required">✱</span></div></div>
   <div class="application-field"><ul>
   <li><label><input type="radio" name="cards[b][field0]" value="No Notice Period (able to start new role as soon as possible)" required><span>No Notice Period (able to start new role as soon as possible)</span></label></li>
   <li><label><input type="radio" name="cards[b][field0]" value="30 days (1 Month)" required><span>30 days (1 Month)</span></label></li>
   <li><label><input type="radio" name="cards[b][field0]" value="60 days (2 Months)" required><span>60 days (2 Months)</span></label></li>
   <li><label><input type="radio" name="cards[b][field0]" value="90 days+ (3+ Months)" required><span>90 days+ (3+ Months)</span></label></li></ul></div></div></li>
 <li class="application-question custom-question"><div><div class="application-label full-width">
   <div class="text">I acknowledge that I do not have permission to record or transcribe any part of the interview process<span class="required">✱</span></div></div>
   <div class="application-field"><select name="cards[c][field0]" required><option value="">Select...</option>
   <option>Yes, I acknowledge this.</option><option>No, I do not acknowledge this.</option></select></div></div></li>
 <li class="application-question custom-question"><div><div class="application-label"><div class="text">Are you legally authorized to work in India WITHOUT sponsorship or an employment pass?<span class="required">✱</span></div></div>
   <div class="application-field"><ul>
   <li><label><input type="radio" name="cards[d][field1]" value="Yes" required><span>Yes</span></label></li>
   <li><label><input type="radio" name="cards[d][field1]" value="No" required><span>No</span></label></li></ul></div></div></li>
</ul>
<input type="hidden" name="h-captcha-response">
<button type="button" id="btn-submit" onclick="window.submitted = true">Submit application</button>
</form>
<script>
window.submitted = false;
function parsed() {   // Lever reads the resume and pre-fills (in capitals, like a CV header)
  setTimeout(() => {
    document.querySelector('[name=name]').value = 'ASHA PATIL';
    document.querySelector('[name=email]').value = 'asha.patil@example.com';
  }, 500);
}
function suggest(input) {
  const box = input.parentElement.querySelector('.dropdown-results');
  box.innerHTML = '';
  if (input.value.length < 2) return;
  ['Pune, Maharashtra, India', 'Pune, Pune, India'].forEach(t => {
    const d = document.createElement('div'); d.textContent = t;
    d.onclick = () => { input.value = t; document.getElementById('selected-location').value = t; box.innerHTML = ''; };
    box.appendChild(d); });
}
</script></body></html>
"""

PROFILE = {
    "personal": {"first_name": "Asha", "last_name": "Patil", "email": "asha.patil@example.com",
                 "phone": "9000000000", "city": "Pune", "state": "Maharashtra", "country": "India"},
    "links": {"linkedin": "https://www.linkedin.com/in/asha-patil-demo/"},
    "experience": [{"company": "Globex Payments"}],
    "rules": [
        {"question": "notice period", "answer": ["90 days", "3 months"]},
        {"question": "authori[sz]ed to work", "answer": "Yes", "countries": ["IN"]},
    ],
}


def test_lever_form(tmp_path):
    from playwright.async_api import async_playwright

    from wdautofill.forms import FormFiller

    page_file = tmp_path / "lever.html"
    page_file.write_text(LEVER_HTML, encoding="utf-8")
    resume = tmp_path / "Asha_Patil_CV.pdf"
    resume.write_bytes(b"%PDF-1.4")
    asked = []

    def blocking(label, kind, options, job, scope, suggestion, why):
        asked.append(label)
        return Reply({"Do you have consistent internet access?": "Yes"}.get(label))  # acknowledgement: skipped

    prompter = TerminalPrompter()
    prompter._blocking_ask = blocking
    resolver = Resolver(PROFILE, AnswerBank(tmp_path / "a.yaml"), prompter=prompter)

    async def main():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception:
                pytest.skip("Playwright browser not installed")
            page = await browser.new_page()
            job = Job(page_file.as_uri(), "IN")
            f = FormFiller(page, resolver, job, resume_path=resume, log_path=tmp_path / "log.jsonl")
            status = await f.run()
            v = lambda n: page.locator(f'[name="{n}"]').input_value()
            out = {
                "status": status,
                "resume": await page.evaluate("document.getElementById('resume-upload-input').files[0].name"),
                "name": await v("name"), "email": await v("email"), "phone": await v("phone"),
                "location": await v("location"), "selected": await page.locator("#selected-location").input_value(),
                "org": await v("org"), "linkedin": await v("urls[LinkedIn]"), "twitter": await v("urls[Twitter]"),
                "internet": await page.locator('[name="cards[a][field0]"][value="Yes"]').is_checked(),
                "notice": await page.locator('[name="cards[b][field0]"][value="90 days+ (3+ Months)"]').is_checked(),
                "authorized": await page.locator('[name="cards[d][field1]"][value="Yes"]').is_checked(),
                "ack": await page.locator('[name="cards[c][field0]"]').input_value(),
                "submitted": await page.evaluate("window.submitted"),
                "skipped": f.skipped,
            }
            await browser.close()
            return out

    o = asyncio.run(main())
    assert o["status"] == "ready_for_review" and o["submitted"] is False   # never submits
    assert o["resume"] == "Asha_Patil_CV.pdf"
    assert o["name"] == "Asha Patil"                # "ASHA PATIL" from the resume reader replaced
    assert (o["email"], o["phone"]) == ("asha.patil@example.com", "9000000000")
    assert o["selected"] == "Pune, Maharashtra, India"   # picked from the location suggestions
    assert o["org"] == "Globex Payments"       # current company = newest job in profile
    assert o["linkedin"].startswith("https://www.linkedin.com/in/asha-patil-demo")
    assert o["twitter"] == ""                            # optional + unknown: left empty, not asked
    assert o["internet"] and o["notice"] and o["authorized"]
    assert o["ack"] == ""                                # attestation: yours (skipped when asked)
    assert "Do you have consistent internet access?" in asked
    assert not any("Twitter" in a for a in asked)
    assert any("acknowledge" in s for s in o["skipped"])


def test_tenant_for_shared_job_sites():
    assert Job("https://jobs.lever.co/jumpcloud/13a4/apply?lever-source=LinkedIn").tenant == "jumpcloud"
    assert Job("https://boards.greenhouse.io/acme/jobs/123").tenant == "acme"
    assert Job("https://qualys.wd5.myworkdayjobs.com/x").tenant == "qualys"


GENERIC_HTML = """
<html><head><title>Job Application for SDET at Acme</title>
<script type="application/ld+json">{"@type":"JobPosting","title":"SDET","hiringOrganization":{"name":"Acme Corp"},
 "jobLocation":{"address":{"addressLocality":"Pune","addressCountry":"IN"}}}</script></head><body>
<form id="application_form">
  <div class="field"><label for="first_name">First Name *</label><input id="first_name" name="first_name" required></div>
  <div class="field"><label for="last_name">Last Name *</label><input id="last_name" name="last_name" required></div>
  <div class="field"><label for="resume">Resume/CV</label><input type="file" id="resume" name="resume"></div>
  <div class="field"><label for="gender">Gender</label>
    <select id="gender" name="gender"><option value="">Please select</option><option>Female</option><option>Male</option><option>Decline to self identify</option></select></div>
  <fieldset><legend>Will you now or in the future require visa sponsorship? *</legend>
    <label><input type="radio" name="spons" value="1"> Yes</label><label><input type="radio" name="spons" value="0"> No</label></fieldset>
  <div class="field"><label for="why">Why do you want to work here?</label><textarea id="why" name="why"></textarea></div>
  <input type="text" name="website" class="honeypot" style="position:absolute;left:-9999px">
  <button type="submit" onclick="window.submitted=true; return false;">Submit Application</button>
</form></body></html>
"""


def test_generic_form(tmp_path):
    from playwright.async_api import async_playwright

    from wdautofill.forms import FormFiller

    page_file = tmp_path / "gh.html"
    page_file.write_text(GENERIC_HTML, encoding="utf-8")
    profile = {"personal": {"first_name": "Asha", "last_name": "Patil"},
               "links": {"website": "https://example.dev"},
               "rules": [{"question": r"\bgender\b", "answer": ["Male"]},
                         {"question": "sponsor", "answer": "No", "countries": ["IN"]}]}
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
            f = FormFiller(page, resolver, Job(page_file.as_uri()), log_path=tmp_path / "log.jsonl")
            status = await f.run()
            out = (status, f.job.company, f.job.country,
                   await page.locator("#first_name").input_value(), await page.locator("#last_name").input_value(),
                   await page.locator("#gender").evaluate("s => s.options[s.selectedIndex].text"),
                   await page.locator('[name=spons][value="0"]').is_checked(),
                   await page.locator('[name=website]').input_value(), await page.evaluate("window.submitted === true"))
            await browser.close()
            return out

    status, company, country, first, last, gender, no_spons, honeypot, submitted = asyncio.run(main())
    assert status == "ready_for_review" and not submitted
    assert (company, country) == ("Acme", "IN")  # JSON-LD "Acme Corp", legal suffix dropped
    assert (first, last, gender, no_spons) == ("Asha", "Patil", "Male", True)
    assert honeypot == ""                                  # bot trap left alone
    assert asked == []                                     # optional essay question not forced on you
