import zipfile

from wdautofill.coverletter import _fill_xml, leftover_placeholders, make_cover_letter
from wdautofill.jobs import detect_country, read_job_lines
from wdautofill.search import Site, parse_picks, parse_site, posted_days, search


# ------------------------------------------------------------ cover letter
DOC = ('<w:document xmlns:w="w"><w:body>'
       '<w:p><w:r><w:t>Dear {{company}} team,</w:t></w:r></w:p>'
       '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Re: {{job_</w:t></w:r><w:r><w:t>title}} role</w:t></w:r></w:p>'
       '<w:p><w:r><w:t>No placeholder here</w:t></w:r></w:p>'
       '</w:body></w:document>')


def test_placeholders_filled_even_when_word_splits_them():
    out = _fill_xml(DOC, {"company": "AT&T", "job_title": "SDET"})
    assert "Dear AT&amp;T team," in out
    assert "Re: SDET role" in out and "{{" not in out
    assert "<w:b/>" in out  # formatting of the first run kept
    assert "No placeholder here" in out


def test_make_cover_letter_docx(tmp_path):
    tpl = tmp_path / "t.docx"
    with zipfile.ZipFile(tpl, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", DOC.replace("{{company}}", "{{company}} {{compnay}}"))
    out = make_cover_letter(tpl, tmp_path / "out", "Jane Doe", "Acme Corp", "SDET", "Pune")
    assert out.name == "Jane_Doe_Cover_Letter_Acme_Corp.docx"
    assert leftover_placeholders(out) == ["compnay"]  # typo is reported


def test_make_cover_letter_txt_and_pdf(tmp_path):
    t = tmp_path / "t.txt"
    t.write_text("Hello {{ company }}, re {{job_title}} in {{job_location}}", encoding="utf-8")
    out = make_cover_letter(t, tmp_path, "J", "Acme", "QA", "Pune")
    assert out.read_text(encoding="utf-8") == "Hello Acme, re QA in Pune"
    pdf = tmp_path / "fixed.pdf"
    pdf.write_bytes(b"%PDF")
    assert make_cover_letter(pdf, tmp_path, "J", "Acme", "QA", "Pune") == pdf


# ------------------------------------------------------------ jobs
def test_job_lines_and_country():
    rows = read_job_lines("# comment\nhttps://a.wd1.myworkdayjobs.com/x/job/1 US  # note\n\nhttps://b/x#frag\n")
    assert rows == [("https://a.wd1.myworkdayjobs.com/x/job/1", "US"), ("https://b/x#frag", None)]
    assert detect_country("HINJEWADI-PUNE") == "IN"
    assert detect_country("Pune, India") == "IN" and detect_country("Remote") is None


# ------------------------------------------------------------ search
def test_parse_site():
    s = parse_site("https://qualys.wd5.myworkdayjobs.com/en-US/Careers/job/Pune/Lead-QA_R1/apply")
    assert (s.host, s.tenant, s.site) == ("qualys.wd5.myworkdayjobs.com", "qualys", "Careers")
    assert s.api == "https://qualys.wd5.myworkdayjobs.com/wday/cxs/qualys/Careers"
    assert parse_site("https://prodege.wd108.myworkdayjobs.com/Prodege_Careers").site == "Prodege_Careers"
    assert parse_site("https://www.linkedin.com/jobs/view/1") is None


def test_posted_days_and_picks():
    assert [posted_days(t) for t in ("Posted Today", "Posted Yesterday", "Posted 3 Days Ago", "Posted 30+ Days Ago")] == [0, 1, 3, 30]
    assert parse_picks("1,3, 5-6", 6) == [0, 2, 4, 5]
    assert parse_picks("all", 3) == [0, 1, 2] and parse_picks("", 3) == [] and parse_picks("9", 3) == []


def test_search_filters_scores_and_ranks():
    site = Site("acme.wd5.myworkdayjobs.com", "acme", "Ext")
    listing = [
        {"title": "Senior SDET", "externalPath": "/job/Pune/Senior-SDET_R1", "locationsText": "Pune, India", "postedOn": "Posted 2 Days Ago"},
        {"title": "QA Engineer", "externalPath": "/job/X/QA_R2", "locationsText": "2 Locations", "postedOn": "Posted Today"},
        {"title": "QA Intern", "externalPath": "/job/Pune/Intern_R3", "locationsText": "Pune", "postedOn": "Posted Today"},
        {"title": "SDET", "externalPath": "/job/Austin/SDET_R4", "locationsText": "Austin, TX", "postedOn": "Posted Today"},
        {"title": "SDET old", "externalPath": "/job/Pune/Old_R5", "locationsText": "Pune", "postedOn": "Posted 30+ Days Ago"},
    ]
    details = {
        "/job/Pune/Senior-SDET_R1": {"jobPostingInfo": {"location": "Pune, India", "jobDescription":
            "<p>Playwright and C# with Azure DevOps. 8+ years</p>", "externalUrl": "https://acme.wd5.myworkdayjobs.com/Ext/job/Pune/Senior-SDET_R1"},
            "hiringOrganization": {"name": "Acme Payments"}},
        "/job/X/QA_R2": {"jobPostingInfo": {"location": "Bangalore", "additionalLocations": ["Pune"],
            "jobDescription": "Selenium. 15+ years"}},
    }
    calls = []

    def http(url, body=None):
        calls.append(url)
        if body is not None:
            return {"total": len(listing), "jobPostings": listing} if body["offset"] == 0 else {"jobPostings": []}
        return details.get(url.split("/Ext", 1)[1], {})

    posts = search([site], ["SDET", "QA"], "Pune", ["Playwright", "C#", "Azure DevOps", "Selenium"], years=9,
                   max_days=14, exclude=["Intern"], http=http, log=lambda *a: None)
    assert [p.title for p in posts] == ["Senior SDET", "QA Engineer"]
    top = posts[0]
    assert top.company == "Acme" and top.url.endswith("/Ext/job/Pune/Senior-SDET_R1")
    assert top.matched == ["Playwright", "C#", "Azure DevOps"] and top.score > posts[1].score
    assert "asks 15+ yrs" in posts[1].notes
