"""Starter files written by `wdautofill init`."""

PROFILE = '''\
# wdautofill profile - private to you (git-ignored by default).
personal:
  first_name: Jane
  last_name: Doe
  email: jane.doe@example.com
  phone: "5551234567"
  phone_country_code: "United States of America (+1)"
  address_line1: 1 Main Street
  address_line2: ""
  city: Springfield
  state: Illinois
  postal_code: "62701"
  country: United States of America

links:
  linkedin: https://www.linkedin.com/in/janedoe
  github: ""
  website: ""

resume_path: ./resume.pdf

# Optional. A .docx (or .txt) with {{company}} {{job_title}} {{job_location}} {{date}};
# every job gets its own filled-in copy in cover_letters/, uploaded where asked.
# A .pdf is uploaded unchanged.
cover_letter_path: ./cover_letter_template.docx

# Free-text rules in your own words (sponsorship, education, preferences ...).
instructions_path: ./instructions.md

# Your fixed answers, by question wording (regular expression, case-insensitive).
# They work for every company, without AI, and also cover personal questions -
# they are YOUR answers. A list = first one that exists on the form is used.
# countries / except_countries limit a rule to jobs in (or outside) those countries.
rules:
  - question: "how did you hear"
    answer: [LinkedIn, Company Website, Career Site, Website]
  - question: '(ever|previously|formerly)\\b.*\\b(worked for|work for|employed (by|with|at)|been employed|employee of|worked at|contingent worker|contractor for)'
    answer: "No"
  # - question: "gender"
  #   answer: [Male, Man]
  # - question: "sponsor"
  #   answer: "No"
  #   countries: [US]

# Work history and education, newest first. Filled into Workday's "Work Experience N"
# and "Education N" blocks (missing blocks are added, extra ones removed).
# A list = alternatives, for pickers whose wording differs between companies.
experience:
  - title: QA Engineer
    company: Example Corp
    location: Springfield, USA
    from: "01/2022"        # MM/YYYY
    to: current            # or "MM/YYYY"
    current: true
    description: Built and ran automated tests.
education:
  - school: [Example State University, Example University]
    degree: [Bachelor of Science, Bachelor's Degree, Bachelors]
    field: [Computer Science]
    to: "2016"

# Short facts the AI may use for ordinary questions.
facts:
  - "Total professional experience: 6 years"
  - "Notice period: 30 days"
  - "Preferred work arrangement: hybrid or remote"

# `wdautofill search` - finds jobs on the Workday sites listed in companies.txt
search:
  keywords: [QA Automation, SDET, Test Automation, Quality Engineer]
  location: Pune            # "" = anywhere
  max_days: 14              # only jobs posted in the last N days
  years_experience: 6       # flags jobs asking far more / far fewer years
  skills: [Selenium, Playwright, Python, API testing, CI/CD]   # used to score matches
  exclude: [Intern]         # skip titles containing these
  companies_file: ./companies.txt

# The job's country (for work-authorization / sponsorship answers) is read from
# each posting, or set per line in jobs.txt. If unknown you are asked once per job.

llm:
  provider: none        # none | anthropic | openai  ("openai" = any OpenAI-compatible server)
  model: ""
  base_url: ""
  api_key_env: ""       # name of the env var holding the key, e.g. GROQ_API_KEY
  min_confidence: 0.8
  # For legal/identity questions the AI never fills anything; it only SUGGESTS an
  # answer from your instructions, which you accept with Enter. Set false to keep
  # those questions (and your instructions about them) away from the AI provider.
  suggest_sensitive: true

# Free options (pick one block, copy its values above):
#   Local & private (Ollama, needs ~8 GB RAM):  provider: openai, model: qwen3:8b,
#       base_url: http://localhost:11434/v1
#   Groq free tier:  provider: openai, model: openai/gpt-oss-20b,
#       base_url: https://api.groq.com/openai/v1, api_key_env: GROQ_API_KEY
#   Gemini free tier (Google may use free-tier prompts to improve its products):
#       provider: openai, model: gemini-2.5-flash,
#       base_url: https://generativelanguage.googleapis.com/v1beta/openai/, api_key_env: GEMINI_API_KEY
'''

ANSWERS = '''\
# Your confirmed answers. wdautofill appends here whenever you answer a new question.
#
#   scope: "*" = any employer, or a country code (US, UK, IN ...) for the job's country
#   sensitive: true = legal / identity questions; these are NEVER guessed by the AI
#              and only matched exactly (a similar wording is offered as a suggestion).
#
# Example (replace with YOUR real answer before using):
#
# answers:
#   - question: Will you now or in the future require sponsorship?
#     answer: "<your real answer>"
#     scope: US
#     sensitive: true
answers: []
'''

INSTRUCTIONS = '''\
# My instructions for job applications
# Write in plain sentences. The AI reads this for every new question.
# For legal / identity questions it only SUGGESTS an answer; you confirm once.

- I am a citizen of <country>. I do not need sponsorship for jobs in <country>;
  for jobs in any other country I would need visa sponsorship.
- I have no criminal record.
- Notice period: <n> days. Earliest start date: <date or "after notice period">.
- Willing to relocate: <yes/no/where>.
- How did you hear about us: <LinkedIn / Company website / ...>.
'''

JOBS = '''\
# One job per line:  URL  [COUNTRY]   (Workday, Lever, Greenhouse or other forms)
# COUNTRY is optional (US, UK, IN ...). Without it, it is read from the posting.
# https://acme.wd5.myworkdayjobs.com/en-US/careers/job/Pune/QA-Engineer_R12345
'''

COMPANIES = """\
# Workday career sites to search, one per line (wdautofill search).
# Find more: open a company's careers page; if the address contains
# myworkdayjobs.com, paste it here (any job link from that site also works).
# Starter list: companies that had QA/SDET jobs in India on Workday (October 2026).
# Delete the ones you don't want, add your own.

https://mastercard.wd1.myworkdayjobs.com/CorporateCareers
https://worldpay.wd5.myworkdayjobs.com/Worldpay_External_Careers_Site
https://fiserv.wd5.myworkdayjobs.com/EXT
https://tsys.wd1.myworkdayjobs.com/en-US/TSYS
https://statestreet.wd1.myworkdayjobs.com/en-US/Global
https://lplfinancial.wd1.myworkdayjobs.com/en-US/India_Ext
https://finastra.wd3.myworkdayjobs.com/en-US/FINC
https://autodesk.wd1.myworkdayjobs.com/en-US/Ext
https://qualys.wd5.myworkdayjobs.com/en-US/Careers
https://rockwellautomation.wd1.myworkdayjobs.com/External_Rockwell_Automation
https://wk.wd3.myworkdayjobs.com/en-US/External
https://automationanywhere.wd5.myworkdayjobs.com/en-US/AutomationAnywhereJobs
https://prodege.wd108.myworkdayjobs.com/Prodege_Careers
https://accenture.wd103.myworkdayjobs.com/en-US/AccentureCareers
https://jadeglobal.wd5.myworkdayjobs.com/Jade_Careers
https://dentsuaegis.wd3.myworkdayjobs.com/en-US/DAN_GLOBAL
"""
