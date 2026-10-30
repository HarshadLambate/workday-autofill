# workday-autofill – how it works

The whole flow, how fields are found and filled, and what AI adds.

---

## 1. The big picture

```
 companies.txt ──► wdautofill search ──► scored shortlist ──► you pick ──► jobs.txt
                                                                              │
 profile.yaml  ─┐                                                             ▼
 answers.yaml  ─┼──► wdautofill apply ──► one browser window per job ──► filled form
 instructions  ─┘     (5 filling at once,      (Workday / Lever /         stops before
 resume, cover        queue for the rest)       Greenhouse / forms)        Submit ─► YOU
 letter template
```

Two commands, one private folder (`personal/`), and you always click Submit yourself.

---

## 2. `wdautofill search` – finding jobs

1. **Reads** `search:` in `profile.yaml` (keywords, location, max_days, skills,
   years_experience, exclude) and the career-site list in `companies.txt`. If that
   list is empty, the 16 built-in sites are used.
2. **Asks each company's Workday site directly.** Every Workday career page has a
   public JSON endpoint that its own search box uses:
   `POST https://<company>.wdN.myworkdayjobs.com/wday/cxs/<company>/<site>/jobs`
   with `{"searchText": "SDET", "limit": 20, "offset": 0}`.
   It runs once per keyword, with up to 60 results per keyword per company and a short
   pause between pages so the sites aren't hammered.
3. **Filters:**
   - drops postings older than `max_days`, and titles containing an `exclude` word;
   - opens each remaining posting (a second endpoint) for its full location and
     description;
   - keeps jobs in your city. Only the first part of `location` is used, so
     "Pune, India" matches "Pune" and "IND Pune".
4. **Scores each job from 0 to 100:**
   - 35 points if the title contains one of your keywords;
   - up to 65 points for how many of your `skills` appear in the description;
   - −20 and a flag if it asks for far more years than you have ("asks 15+ yrs");
     −15 and a flag if it's clearly junior.
   - Jobs with no keyword in the title and fewer than 2 of your skills are dropped. That
     removes things like "Recruiter – QA hiring".
5. **Shows the list, best first.** You type `1,3,5-8` or `all`; those links are added to
   `jobs.txt`, and it offers to start `apply`. Jobs already queued or applied to don't
   appear again.

LinkedIn is **not** searched: its terms forbid automated searching and accounts that do
it get restricted. Links you find on LinkedIn that lead to Workday, Lever or Greenhouse
can be pasted into `jobs.txt`.

---

## 3. `wdautofill apply` – start-up

1. Loads `profile.yaml`. A formatting mistake gives a clear "line X" message, and wrong
   dates such as `04/08/2025` instead of `08/2025` are flagged.
2. Loads your **rules** (from `profile.yaml`), your **answer bank** (`answers.yaml`), and
   your **instructions** (`instructions.md`, only used when AI is on).
3. Collects job links from `jobs.txt`, the command line, or pasted into the terminal.
   Jobs already filled in an earlier run (listed in `applications.jsonl`) are skipped
   unless you add `--again`.
4. Asks **once** for the password to use on every company site (hidden as you type), or
   reads it from the `WDA_PASSWORD` environment variable. The tool never saves it.
5. Makes sure the browser is installed (it installs Chromium automatically the first
   time), then opens **one Chromium browser** with a saved profile in `~/.wdautofill/browser`, so
   sign-ins and cookies survive between runs. The first window is the **status page**.
6. Starts the **queue**:
   - **5 jobs fill at the same time** (`--parallel`). A job that finishes filling frees
     its slot immediately, even while its window waits for your review.
   - At most **15 job windows** are open in total (`--max-open`).
   - `jobs.txt` is re-read every 5 seconds, so links you add while it runs join the queue.

---

## 4. One Workday job, step by step

1. **Opens the job posting.** Links copied from LinkedIn often go straight into
   `/apply/...`; the tool opens the posting first instead.
2. **Reads the posting's hidden JSON-LD** (the data Workday publishes for Google Jobs):
   job title, company and location. From those it gets:
   - **the company name**, cleaned up: "QLYS_IN Qualys Security TechServices Private
     Ltd." becomes "Qualys";
   - **the job's country** ("India" → IN), which decides your sponsorship and
     work-permit answers.
3. **Clicks Apply**, then picks one option:
   - **Use My Last Application**, but only for a company you've applied to before.
     Workday shows this button to everyone.
   - otherwise **Autofill with Resume**;
   - otherwise **Apply Manually**.
4. **Signs in:**
   - clicks "Sign in with email" if the site first shows Google/Apple/LinkedIn buttons;
   - types your email and password and presses Sign In;
   - if that fails, fills the **Create Account** form for you to finish (terms box,
     email code, CAPTCHA are yours). The window waits up to 30 minutes for you.
5. **Then repeats for each page** (up to 25 pages):
   1. **Waits until the page is really ready.** Workday shows a loading screen after
      every Continue, so the tool waits until it's gone, the Next/Continue button is
      there and the fields have stopped changing (up to 45 s). Reading too early was
      the cause of the very first failures.
   2. **Uploads files.** Your resume goes into the Autofill-with-Resume box and any
      Resume/CV box. Your cover letter template becomes
      `cover_letters/<You>_Cover_Letter_<Company>.docx` (company name confirmed once per
      company) and goes into any Cover Letter box.
   3. **Matches the Work Experience / Education blocks to your profile.** It adds
      missing blocks, deletes extra ones (e.g. HSC/SSC added by Workday's resume
      reader), and fills each block from `experience:` / `education:`.
   4. **Fills every field** (sections 5–6).
   5. **Saves the page structure** to `personal/debug/`: field names and buttons only,
      never your answers.
   6. **Finds the Next / Continue / "Save and Continue" button.** If the button says
      **Submit**, it stops: **READY**, your turn. Otherwise it clicks it and waits for
      the next page.
   7. **If Workday shows errors** ("field is required"), it prints them and waits until
      you fix them in that window, then carries on.
6. **Writes a log line** to `applications.jsonl`: status, company, country, the page it
   stopped on if any, and fields left for you.

## 4b. Lever, Greenhouse and other forms

Same answers, same safety, simpler flow:

1. Opens the form (for Lever, `.../apply`) and reads the title, company and location.
2. Uploads the resume, then waits a few seconds because the site reads it and pre-fills
   fields.
3. **Groups the page into questions:** each text box, drop-down or text area is one
   question, and radio buttons or checkboxes sharing a name are one question. Each
   question's wording is found from:
   - Lever's `.application-label`;
   - a `<legend>`;
   - `<label for=…>`;
   - `aria-labelledby`;
   - a wrapping `<label>`;
   - `aria-label`;
   - otherwise the first line of text in the block.
   CAPTCHA and honeypot (bot-trap) boxes are skipped.
4. Fills each question (sections 5–6). An **optional** question it knows nothing about is
   left empty instead of asking you.
5. Follows "Next/Continue" steps; stops when the button is Submit.

---

## 5. How each answer is chosen (the resolver)

For every field, in this order. The first one that gives an answer wins:

| # | Source | Example | Notes |
|---|---|---|---|
| 1 | **Profile** | "Given Name(s)" → John | Label patterns map to profile fields (name, email, phone, phone code, address, city, postal code, country, LinkedIn/GitHub/website, full name, current company, current location). Also **replaces** what a resume reader typed, e.g. JOHN → John. |
| 2 | **Your rules** | "How did you hear…" → LinkedIn | First matching rule wins. A list = first option that exists on that form. `countries` / `except_countries` use the job's country. Rules are *your* answers, so they also cover personal questions. |
| 3 | **Answer bank** (`answers.yaml`) | anything you typed before | Ordinary questions match fuzzily (90 % similar wording). Personal/legal questions match exact wording only. Country-dependent ones are stored per country. |
| 4 | **AI** (optional) | "Years with Selenium?" → 9 | Ordinary questions only, only if it's confident (≥ 0.8) and picks a real option. For personal/legal questions it may only *suggest*. |
| 5 | **You** | asked in the terminal | One question at a time across all windows, the window comes to the front, the same question in 5 windows is asked once. Remembered for next time unless you start your answer with `!`. |

**Personal / legal questions** are recognised by words like sponsor, visa, authorized to
work, criminal, gender, ethnicity, religion, disability, veteran, age, salary, consent,
certify. For these the AI never fills anything. A similar wording you answered before
(≥ 75 % alike), or the AI, can only offer a **suggestion** that you accept with Enter.

---

## 6. Is it using locators? Yes — Playwright locators

It doesn't use screenshots or "AI vision". It uses normal Playwright locators, in three
layers:

**a) Workday's own test hooks: `data-automation-id`.** Workday puts these attributes on its
UI, and they are the same for every company:

| What | Locator |
|---|---|
| a question block | `[data-automation-id^="formField-"]` (e.g. `formField-legalName--firstName`) |
| Apply / Autofill / Manually | `adventureButton`, `autofillWithResume`, `applyManually`, `useMyLastApplication` |
| sign-in | `SignInWithEmailButton`, `email`, `password`, `signInSubmitButton`, `createAccountSubmitButton` |
| Next / Submit | `pageFooterNextButton` (+ fallback: a button whose text is Next / Continue / Save and Continue) |
| search pickers | `searchBox`, results `promptOption`, chosen value `selectedItem` |
| dates | `dateSectionMonth-input`, `dateSectionDay-input`, `dateSectionYear-input` |
| loading screen, errors | `applyFlowLoadingPage`, `errorMessage` |
| Workday's invisible layer over buttons | `click_filter` (the tool clicks this, since it's what receives the click) |
| bot trap — never filled | `beecatcher` |

**b) Inside a question block, standard HTML:** `legend` or `label` for the question
wording, and `input`, `textarea`, `input[type=radio]`, `input[type=checkbox]`,
`button[aria-haspopup="listbox"]` (Workday drop-downs) or `select` for the field itself.

**c) Other sites:** a small script marks each question block with `data-wda-q`,
`data-wda-label` and `data-wda-req`, and from then on the same Playwright locators are
used.

**The important design choice:** the code has **no locator per question**. It reads each
question's **wording**, decides the answer from that (section 5), then fills whatever
kind of field it is. That's why it copes with questions it has never seen, and why a
company's custom questions don't need code changes.

**How each kind of field is filled:**

| Kind | How |
|---|---|
| text / text area | `fill()`. Type-ahead boxes (Lever location) then pick the best suggestion. |
| Workday drop-down | click the button → read `[role=option]` → pick the best match → click |
| search picker | type the answer + Enter → pick the matching `promptOption` |
| radio / checkbox group | read each option's label → check the best match |
| `select` | `select_option()` |
| date | click each section → Ctrl+A → type month / day / year |

**Best match** tries, in order: exact text, then the option starting with your answer,
then your answer contained in the option, then 80 % similar. So the answer "90 days"
picks "90 days+ (3+ Months)".

---

## 7. What AI adds

### Without AI (`provider: none`)
- Everything in sections 2–6 works. Profile, rules and saved answers usually cover
  **most** fields.
- Each **new** question is asked once in the terminal. Expect more questions during the
  first 10–20 applications, then very few.
- Free-text questions ("Why do you want to join us?") are typed by you, once per
  wording.

### With a free AI (Groq, Gemini free tier, or Ollama on your PC)
The AI only sees what's left after profile, rules and saved answers. It gets the
question, the options, your profile, `facts:`, `instructions.md`, your previous answers
and the job's title/location. It replies with an answer and a confidence.
- **Fewer questions in the terminal:** e.g. "Years of Selenium experience?", "Willing to
  work in shifts?", "Are you comfortable with hybrid work?", "Which of these tools do
  you know?" get answered from your facts.
- **Short written answers** (2–4 sentences, based only on your facts) for open-ended
  questions.
- **Suggestions for personal/legal questions** from your instructions, which you accept
  with one Enter.
- **Limits:**
  - if it isn't sure (confidence < 0.8), or picks an option that doesn't exist, you're
    asked as before;
  - small local models (Ollama 8B) say "don't know" more often;
  - free tiers have daily limits;
  - online providers receive your profile and the question (Ollama keeps everything on
    your PC).

**Rough effect:** with good rules, AI turns maybe 5–15 first-time prompts per batch into
1–5. Its biggest visible gain is the free-text answers.

### With Claude, a strong paid model
- **Better at the hard leftovers:** tricky or negated wording ("Do you NOT require…"),
  choosing between near-identical options, and well-written, job-specific free-text
  answers.
- **Compared with a good free model, the difference is modest** for this tool. AI only
  handles the leftover questions, so you'd see a few fewer prompts and nicer essay
  answers. It won't make filling faster or fix site-specific problems, which are
  locator/timing issues.
- **Plans and billing:**
  - The tool talks to an AI through an **API key**.
  - A **Claude Pro** subscription does **not** include API access or API credits; API
    use is billed separately through the Claude Console.
  - **Max** and **Team** plans include monthly API credits that work with an API key.
  - Using Claude Code itself (the coding assistant) is a different product. It doesn't
    plug into this tool.
- **To use Claude:** get an API key from the Console, run `setx ANTHROPIC_API_KEY "..."`,
  then set

  ```yaml
  llm:
    provider: anthropic
    model: claude-haiku-5-5      # fast and cheap; claude-sonnet-5-5 for better writing
  ```

  Each AI call is one short question (max 400 tokens of answer). For typical use the
  cost is small, but check current API pricing.

AI support was tested with stand-in models in the automated tests, not yet against live
providers. Try it on one or two jobs first.

---

## 8. Safety rules built into the code

- Never clicks **Submit**. There's a hard check: the code refuses to press a button
  whose text contains "submit".
- Never solves **CAPTCHAs**, never ticks **terms / consent** boxes for you, and never
  fills Workday's **bot-trap** field.
- Never lets AI **fill** personal/legal questions; it can only suggest.
- Never fills an answer that depends on the job's country under the wrong country (an
  India answer isn't reused for a US job).
- Never saves your **password**.

---

## 9. Your files (all in `personal/`, never pushed to GitHub)

| File | What it is |
|---|---|
| `profile.yaml` | you: contact details, links, rules, experience, education, search settings, AI settings |
| `answers.yaml` | every answer you typed, grows automatically (`wdautofill answers --dir personal` lists it) |
| `instructions.md` | plain sentences about you, read only by AI |
| `jobs.txt` | the queue: one link per line, optional country (`URL IN`) |
| `companies.txt` | career sites for `search` |
| `applications.jsonl` | log: one line per job filled |
| `cover_letters/` | the generated per-company cover letters |
| `debug/` | page structures for fixing sites (no answers) |

---

## 10. Commands cheat-sheet

```bash
wdautofill setup                                 # once: install the browser + create personal/
wdautofill init   --dir personal                 # (re)create missing files
wdautofill search --dir personal                 # find + shortlist jobs
wdautofill apply  --dir personal                 # fill everything in jobs.txt
wdautofill apply  --dir personal --parallel 8    # 8 filling at once
wdautofill apply  --dir personal --again         # redo jobs filled before
wdautofill apply  --dir personal --country US    # all these jobs are in the US
wdautofill answers --dir personal                # show saved answers
```

## 11. When something goes wrong

| Symptom | Likely cause / fix |
|---|---|
| "formatting mistake at line X" | YAML: use `|` for multi-line text, quotes for text with `: `, spaces not tabs |
| search finds 0 sites / 0 jobs | `companies.txt` empty, `max_days` too small, or the city spelled differently |
| a window stops: "No Next/Continue button" | send the files from `personal/debug/` for that job, so the locators can be fixed |
| same question asked at every company | add a `rules:` entry with a regex for its wording |
| wrong option picked | put the exact option text first in that rule's answer list |
| a job you skipped never comes back | it's in `applications.jsonl`; run with `--again` |
